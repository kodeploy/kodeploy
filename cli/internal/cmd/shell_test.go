package cmd

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/kodeploy/kodeploy/cli/internal/clock"
	"github.com/kodeploy/kodeploy/cli/internal/shell"
)

// kodeploy shell|db|redis — 가짜 core(티켓 발급)와 가짜 kodeploy-exec 공개 문, 가짜 터미널로 확인한다.

const execTicket = "exec-ticket-SECRET-abcdef"

type fakeTerm struct {
	mu       sync.Mutex
	tty      bool
	raw      int // MakeRaw를 부른 수
	restored int // 되돌린 수
	resize   chan shell.Size
}

func (f *fakeTerm) IsTerminal() bool { return f.tty }
func (f *fakeTerm) Size() shell.Size { return shell.Size{Cols: 120, Rows: 30} }
func (f *fakeTerm) MakeRaw() (func(), error) {
	f.mu.Lock()
	f.raw++
	f.mu.Unlock()
	var once sync.Once
	return func() {
		once.Do(func() {
			f.mu.Lock()
			f.restored++
			f.mu.Unlock()
		})
	}, nil
}
func (f *fakeTerm) Resized(context.Context) <-chan shell.Size { return f.resize }

type execServer struct {
	mu       sync.Mutex
	core     *httptest.Server
	gate     *httptest.Server
	tickets  []map[string]any // 받은 티켓 요청 본문
	coreAuth []string
	gateHdr  http.Header
	frames   [][]byte
	reply    reply                                         // 티켓 응답 (status 0이면 정상)
	session  func(c *websocket.Conn, frames <-chan []byte) // 게이트웨이 쪽 대본
}

func newExecServer(t *testing.T) *execServer {
	s := &execServer{}
	up := websocket.Upgrader{Subprotocols: []string{"kodeploy.exec.v1"}}
	s.gate = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		s.gateHdr = r.Header.Clone()
		s.mu.Unlock()
		if r.Header.Get("Authorization") != "Ticket "+execTicket || r.Header.Get("Origin") != "" {
			http.Error(w, "bad", http.StatusForbidden)
			return
		}
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		frames := make(chan []byte, 64)
		go func() {
			defer close(frames)
			for {
				_, m, err := c.ReadMessage()
				if err != nil {
					return
				}
				s.mu.Lock()
				s.frames = append(s.frames, m)
				s.mu.Unlock()
				frames <- m
			}
		}()
		s.session(c, frames)
		time.Sleep(50 * time.Millisecond)
	}))
	s.core = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		defer s.mu.Unlock()
		s.coreAuth = append(s.coreAuth, r.Header.Get("Authorization"))
		w.Header().Set("Content-Type", "application/json")
		if r.Method != http.MethodPost || r.URL.Path != "/apps/"+testApp+"/exec-tickets" {
			w.WriteHeader(404)
			return
		}
		if r.Header.Get("Authorization") != "Bearer "+cliToken {
			w.WriteHeader(401)
			_, _ = io.WriteString(w, `{"detail":"CLI 토큰이 만료됐어요. 웹에서 다시 발급해 주세요","code":"cli_token_expired"}`)
			return
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		s.tickets = append(s.tickets, body)
		if s.reply.status != 0 {
			w.WriteHeader(s.reply.status)
			_, _ = io.WriteString(w, s.reply.body)
			return
		}
		b, _ := json.Marshal(map[string]any{"ticket": execTicket, "protocol": "kodeploy.exec.v1", "expires_in": 30,
			"url": "ws" + strings.TrimPrefix(s.gate.URL, "http") + "/exec/app-1/v1/sessions"})
		w.WriteHeader(200)
		_, _ = w.Write(b)
	}))
	t.Cleanup(s.core.Close)
	t.Cleanup(s.gate.Close)
	return s
}

// 받은 입력을 그대로 돌려주고 exit code로 끝내는 세션
func echoThenExit(code int) func(c *websocket.Conn, frames <-chan []byte) {
	return func(c *websocket.Conn, frames <-chan []byte) {
		for f := range frames {
			if len(f) > 0 && f[0] == 0 {
				_ = c.WriteMessage(websocket.BinaryMessage, append([]byte{1}, f[1:]...))
				st := `{"status":"Success"}`
				if code != 0 {
					st = `{"status":"Failure","details":{"causes":[{"reason":"ExitCode","message":"` + itoaTest(code) + `"}]}}`
				}
				_ = c.WriteMessage(websocket.BinaryMessage, append([]byte{3}, st...))
				_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""))
				return
			}
		}
	}
}

func itoaTest(n int) string { b, _ := json.Marshal(n); return string(b) }

func runExecCLI(t *testing.T, s *execServer, term *fakeTerm, stdin io.Reader, env map[string]string, args ...string) cliRun {
	t.Helper()
	if env == nil {
		env = map[string]string{"KODEPLOY_TOKEN": cliToken, "KODEPLOY_SERVER": s.core.URL, "KODEPLOY_APP": testApp}
	}
	var out, errb lockedBuffer
	var tm Terminal
	if term != nil {
		tm = term
	}
	code := Run(context.Background(), args, Deps{
		Getenv: func(k string) string { return env[k] }, Stdout: &out, Stderr: &errb, Clock: clock.NewFake(t0),
		HTTP: http.DefaultClient, NewKey: func() string { return "k" }, Stdin: stdin, Terminal: tm,
	})
	r := cliRun{code: code, stdout: out.String(), stderr: errb.String()}
	for _, secret := range []string{cliToken, execTicket} {
		if strings.Contains(r.stdout+r.stderr, secret) {
			t.Fatalf("원문이 출력에 있다\n%s\n%s", r.stdout, r.stderr)
		}
	}
	return r
}

func TestShellRunsAndReturnsTheRemoteExitCode(t *testing.T) {
	for _, tc := range []struct {
		cmd, target string
		code        int
	}{{"shell", "app", 0}, {"db", "db", 7}, {"redis", "redis", 0}} {
		s := newExecServer(t)
		s.session = echoThenExit(tc.code)
		term := &fakeTerm{tty: true}
		r := runExecCLI(t, s, term, strings.NewReader("SELECT '한글';\n"), nil, tc.cmd)
		if r.code != tc.code || r.stdout != "SELECT '한글';\n" {
			t.Fatalf("%s: exit %d stdout %q\n%s", tc.cmd, r.code, r.stdout, r.stderr)
		}
		if term.raw != 1 || term.restored != 1 {
			t.Fatalf("%s: raw %d restored %d", tc.cmd, term.raw, term.restored)
		}
		if got := s.tickets[0]; got["target"] != tc.target || got["cols"] != float64(120) || got["rows"] != float64(30) {
			t.Fatalf("%s: 티켓 요청 %v", tc.cmd, got)
		}
		if s.coreAuth[0] != "Bearer "+cliToken {
			t.Fatal("core에 CLI 토큰을 싣지 않았다")
		}
	}
}

func TestShellOutsideATerminalIsAUsageError(t *testing.T) {
	s := newExecServer(t)
	for _, term := range []*fakeTerm{nil, {tty: false}} {
		r := runExecCLI(t, s, term, strings.NewReader(""), nil, "shell")
		if r.code != ExitUsage || !strings.Contains(r.stderr, "터미널에서만") {
			t.Fatalf("exit %d\n%s", r.code, r.stderr)
		}
	}
	if len(s.coreAuth) != 0 {
		t.Fatal("터미널이 아니면 티켓을 받지 않는다")
	}
}

func TestShellWithAnExpiredTokenIs125AndNeverGoesRaw(t *testing.T) {
	s := newExecServer(t)
	env := map[string]string{"KODEPLOY_TOKEN": "kdcli_revoked", "KODEPLOY_SERVER": s.core.URL, "KODEPLOY_APP": testApp}
	term := &fakeTerm{tty: true}
	r := runExecCLI(t, s, term, strings.NewReader(""), env, "shell")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "토큰이 만료됐어요. 웹에서 다시 발급해 주세요") || term.raw != 0 {
		t.Fatalf("exit %d raw %d\n%s", r.code, term.raw, r.stderr)
	}
	if len(s.coreAuth) != 1 {
		t.Fatalf("kdcli_는 401 뒤 다시 보내지 않는다: %d", len(s.coreAuth))
	}
}

func TestShellTicketErrors(t *testing.T) {
	for _, tc := range []struct {
		r    reply
		code int
	}{{errBody(403, "cli_token_app_mismatch", "이 CLI 토큰은 다른 앱용이에요"), ExitAuth},
		{errBody(404, "", "대상 Pod가 없습니다 — 앱(또는 DB)이 실행 중인지 확인하세요"), ExitAuth},
		{errBody(422, "", "target"), ExitUsage}} {
		s := newExecServer(t)
		s.reply = tc.r
		term := &fakeTerm{tty: true}
		r := runExecCLI(t, s, term, strings.NewReader(""), nil, "db")
		if r.code != tc.code || term.raw != 0 {
			t.Fatalf("%d: exit %d raw %d\n%s", tc.r.status, r.code, term.raw, r.stderr)
		}
	}
}

func TestShellResizeAndCloseCodes(t *testing.T) {
	s := newExecServer(t)
	term := &fakeTerm{tty: true, resize: make(chan shell.Size, 1)}
	s.session = func(c *websocket.Conn, frames <-chan []byte) {
		n := 0
		for f := range frames {
			if len(f) > 0 && f[0] == 4 {
				n++
				if n == 1 {
					term.resize <- shell.Size{Cols: 90, Rows: 20}
					continue
				}
				_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(4408, "idle"))
				return
			}
		}
	}
	pr, pw := io.Pipe()
	defer pw.Close()
	r := runExecCLI(t, s, term, pr, nil, "shell")
	if r.code != ExitFailed || !strings.Contains(r.stderr, "오래 입력이 없어") || term.restored != 1 {
		t.Fatalf("exit %d restored %d\n%s", r.code, term.restored, r.stderr)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var sizes []string
	for _, f := range s.frames {
		if f[0] == 4 {
			sizes = append(sizes, string(f[1:]))
		}
	}
	if len(sizes) != 2 || sizes[0] != `{"Height":30,"Width":120}` || sizes[1] != `{"Height":20,"Width":90}` {
		t.Fatalf("창 크기 프레임 %v", sizes)
	}

	s2 := newExecServer(t)
	s2.session = func(c *websocket.Conn, _ <-chan []byte) {
		_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(4401, "ticket"))
	}
	term2 := &fakeTerm{tty: true}
	if r := runExecCLI(t, s2, term2, pr, nil, "shell"); r.code != ExitAuth || term2.restored != 1 {
		t.Fatalf("4401 exit %d", r.code)
	}
}
