package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/goleak"
	utilexec "k8s.io/client-go/util/exec"

	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/sign"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }

const ns = "tenant-0a1b2c3d"

var key = []byte(strings.Repeat("k", 32))

type fakeFinder struct {
	targets map[string]target.Target
}

func (f fakeFinder) Find(_ context.Context, tg, app string) (target.Target, error) {
	if tg == "app" && !target.ValidAppName(app) {
		return target.Target{}, target.ErrBadAppName
	}
	t, ok := f.targets[tg]
	if !ok {
		return target.Target{}, target.ErrNotFound
	}
	return t, nil
}

// fakeRunner는 명령 스크립트를 보고 흉내 낸다. 받은 Exec를 기록한다.
type fakeRunner struct {
	mu      sync.Mutex
	seen    []session.Exec
	stdin   [][]byte
	block   chan struct{} // shell 세션을 붙잡아 둘 때
	file    []byte        // 복원 작업 파일
	loadErr error         // 복원 load가 끝나지 못한 것처럼 (연결 끊김)
	pending []string      // 작업 디렉토리에 남은 백업 (이전 복원이 미해결)
}

func (f *fakeRunner) Run(ctx context.Context, e session.Exec) error {
	f.mu.Lock()
	f.seen = append(f.seen, e)
	f.mu.Unlock()
	script := ""
	if len(e.Command) > 2 {
		script = e.Command[2]
	}
	var in []byte
	if e.Stdin != nil && !strings.Contains(script, "bash --norc") {
		in, _ = io.ReadAll(e.Stdin)
		f.mu.Lock()
		f.stdin = append(f.stdin, in)
		f.mu.Unlock()
	}
	switch {
	case strings.Contains(script, "bash --norc"): // shell
		e.Stdout.Write([]byte("한글 출력 \x00\xff"))
		if f.block != nil {
			select {
			case <-f.block:
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		return utilexec.CodeExitError{Err: errors.New("exit"), Code: 2}
	case strings.Contains(script, `gunzip -c "$f"`): // restore.load
		return f.loadErr
	case strings.Contains(script, ops.BackupGlob): // restore.pending
		for _, p := range f.pending {
			fmt.Fprintln(e.Stdout, p)
		}
	case strings.Contains(script, "df -Pk"):
		fmt.Fprint(e.Stdout, "Filesystem 1024-blocks Used Available Capacity Mounted on\noverlay 1 1 1048576 1% /\n")
	case strings.Contains(script, `cat > "$1/restore.dump"`):
		f.mu.Lock()
		f.file = in
		f.mu.Unlock()
	case strings.Contains(script, "wc -c"):
		f.mu.Lock()
		file := f.file
		f.mu.Unlock()
		sum := sha256.Sum256(file)
		fmt.Fprintf(e.Stdout, "%d\n%s  /tmp/kodeploy-restore/restore.dump\n", len(file), hex.EncodeToString(sum[:]))
	case strings.Contains(script, "SHOW TABLES"):
		fmt.Fprint(e.Stdout, "users\n")
	case strings.Contains(script, "-N -B"):
		fmt.Fprint(e.Stdout, "0\t42\n")
	case strings.Contains(script, `-e "$sql"`) || strings.Contains(script, `-c "$sql"`):
		e.Stdout.Write(append([]byte("id\n"), in...))
	}
	return nil
}

func (f *fakeRunner) execs() []session.Exec {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]session.Exec{}, f.seen...)
}

func (f *fakeRunner) uploaded() []byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.file
}

type env struct {
	s        *Server
	pub, int *httptest.Server
	runner   *fakeRunner
	auditBuf *syncBuf
	tickets  *ticket.Store
}

type syncBuf struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuf) Write(p []byte) (int, error) { s.mu.Lock(); defer s.mu.Unlock(); return s.b.Write(p) }
func (s *syncBuf) String() string              { s.mu.Lock(); defer s.mu.Unlock(); return s.b.String() }

// waitAudit는 서버가 감사 기록을 남길 때까지 기다린다. 클라이언트는 close를 받은 뒤 바로 돌아오고,
// 서버는 close 응답을 받은 뒤에 기록하므로 순서가 보장되지 않는다.
func (e *env) waitAudit(t *testing.T, lines int) string {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		s := e.auditBuf.String()
		if strings.Count(s, "\n") >= lines || time.Now().After(deadline) {
			return s
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func newEnv(t *testing.T, maxPublic, maxInternal int, targets map[string]target.Target) *env {
	t.Helper()
	if targets == nil {
		targets = map[string]target.Target{
			"app":   {Pod: "web-1", Container: "app", Kind: target.App},
			"db":    {Pod: "mysql-0", Container: "mysql", Kind: target.MySQL},
			"redis": {Pod: "redis-1", Container: "redis", Kind: target.Redis},
		}
	}
	e := &env{runner: &fakeRunner{}, auditBuf: &syncBuf{}, tickets: ticket.New(30*time.Second, 10)}
	e.s = New(Options{
		Namespace: ns, Key: key, Tickets: e.tickets, Finder: fakeFinder{targets}, Runner: e.runner,
		Audit: audit.New(e.auditBuf), TTYIdleTimeout: time.Minute, TTYMax: time.Minute, NonTTYMax: time.Minute,
		PingInterval: time.Second, MaxSessionsPublic: maxPublic, MaxSessionsInternal: maxInternal,
		AllowedOrigins: []string{"https://app.kodeploy.com", "http://localhost:5173"}, RestoreMaxBytes: 1 << 20, RestoreWorkdir: "/tmp/kodeploy-restore",
	})
	e.pub = httptest.NewServer(e.s.Public())
	e.int = httptest.NewServer(e.s.Internal())
	t.Cleanup(func() {
		e.pub.Close()
		e.int.Close()
		e.s.Close()
	})
	return e
}

func wsURL(base, path string) string { return "ws" + strings.TrimPrefix(base, "http") + path }

func signed(method, path string, body []byte) http.Header {
	h := http.Header{}
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	h.Set(sign.HeaderTimestamp, ts)
	h.Set(sign.HeaderSignature, sign.Compute(key, ts, method, path, body))
	return h
}

func (e *env) issue(t *testing.T, body string, hdr http.Header) (*http.Response, map[string]string) {
	t.Helper()
	req, _ := http.NewRequest("POST", e.int.URL+"/internal/v1/tickets", strings.NewReader(body))
	for k, v := range hdr {
		req.Header[k] = v
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]string
	json.NewDecoder(resp.Body).Decode(&out)
	return resp, out
}

func (e *env) ticketFor(t *testing.T, body string) string {
	t.Helper()
	resp, out := e.issue(t, body, signed("POST", "/internal/v1/tickets", []byte(body)))
	if resp.StatusCode != http.StatusCreated || out["ticket"] == "" {
		t.Fatalf("issue: %d %v", resp.StatusCode, out)
	}
	return out["ticket"]
}

const shellBody = `{"user_id":"u1","app_id":"a1","app":"web","target":"app","op":"shell","cols":120,"rows":32}`

// readAll은 close까지 읽어 채널별 내용과 close 코드를 돌려준다.
func readAll(t *testing.T, c *websocket.Conn) (map[byte][]byte, int) {
	t.Helper()
	out := map[byte][]byte{}
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	for {
		_, msg, err := c.ReadMessage()
		if err != nil {
			var ce *websocket.CloseError
			if errors.As(err, &ce) {
				return out, ce.Code
			}
			t.Fatalf("read: %v", err)
		}
		ch, p, _ := proto.Parse(msg)
		out[ch] = append(out[ch], p...)
	}
}

func dialPublic(t *testing.T, e *env, path string, protocols []string, hdr http.Header) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	d := websocket.Dialer{Subprotocols: protocols}
	c, resp, err := d.Dial(wsURL(e.pub.URL, path), hdr)
	if c != nil {
		t.Cleanup(func() { c.Close() })
	}
	return c, resp, err
}

var sessionPath = "/exec/" + ns + "/v1/sessions"

func TestPublicShell(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	tk := e.ticketFor(t, shellBody)
	c, resp, err := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if got := resp.Header.Get("Sec-WebSocket-Protocol"); got != proto.Subprotocol {
		t.Fatalf("negotiated %q", got)
	}
	out, code := readAll(t, c)
	if code != proto.CloseNormal || string(out[proto.ChStdout]) != "한글 출력 \x00\xff" || proto.StatusExitCode(out[proto.ChStatus]) != 2 {
		t.Fatalf("code %d stdout %q status %s", code, out[proto.ChStdout], out[proto.ChStatus])
	}
	ex := e.runner.execs()[0]
	if ex.Pod != "web-1" || ex.Container != "app" || !ex.TTY || ex.Command[4] != "web" {
		t.Fatalf("exec %+v", ex)
	}

	// 재사용은 4401
	c2, _, err := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, code := readAll(t, c2); code != proto.CloseTicket {
		t.Fatalf("reuse: %d", code)
	}

	// 감사 기록: 티켓 원문은 없고 해시·필드는 있다 (발급 1 + 세션 1 + 재사용 거절 1)
	logs := e.waitAudit(t, 3)
	if strings.Contains(logs, tk) {
		t.Fatal("raw ticket in audit log")
	}
	if !strings.Contains(logs, ticket.Hash(tk)) || !strings.Contains(logs, `"user_id":"u1"`) || !strings.Contains(logs, `"app_id":"a1"`) {
		t.Fatalf("audit: %s", logs)
	}
}

func TestPublicRejects(t *testing.T) {
	e := newEnv(t, 3, 4, nil)

	// 서브프로토콜 없음: 업그레이드하지 않는다
	_, resp, err := dialPublic(t, e, sessionPath, nil, nil)
	if err == nil || resp == nil || resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("no subprotocol: %v %v", err, resp)
	}

	// 티켓 없음 4401
	c, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol}, nil)
	if _, code := readAll(t, c); code != proto.CloseTicket {
		t.Fatalf("no ticket: %d", code)
	}

	// 쿼리로 온 티켓은 보지 않는다
	tk := e.ticketFor(t, shellBody)
	c, _, _ = dialPublic(t, e, sessionPath+"?ticket="+tk, []string{proto.Subprotocol}, nil)
	if _, code := readAll(t, c); code != proto.CloseTicket {
		t.Fatalf("query ticket: %d", code)
	}

	// 다른 ns 경로 4403 (티켓은 쓰지 않는다)
	c, _, _ = dialPublic(t, e, "/exec/tenant-ffffffff/v1/sessions", []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	if _, code := readAll(t, c); code != proto.CloseForbidden {
		t.Fatalf("other ns: %d", code)
	}

	// Authorization: Ticket (CLI용)
	c, _, err = dialPublic(t, e, sessionPath, []string{proto.Subprotocol}, http.Header{"Authorization": {"Ticket " + tk}})
	if err != nil {
		t.Fatal(err)
	}
	if _, code := readAll(t, c); code != proto.CloseNormal {
		t.Fatalf("authorization header: %d", code)
	}
}

// Origin: 없으면 통과(CLI), 있으면 허용 목록만. 막힌 요청은 업그레이드 없이 403이고 티켓은 남는다
func TestPublicOrigin(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	tk := e.ticketFor(t, shellBody)
	protocols := []string{proto.Subprotocol, proto.TicketPrefix + tk}
	for _, bad := range []string{"https://evil.example", "null", "https://app.kodeploy.com.evil.example", "http://app.kodeploy.com"} {
		_, resp, err := dialPublic(t, e, sessionPath, protocols, http.Header{"Origin": {bad}})
		if err == nil || resp == nil || resp.StatusCode != http.StatusForbidden {
			t.Fatalf("origin %q: %v %v", bad, resp, err)
		}
	}
	logs := e.waitAudit(t, 5) // ticket 1 + 거부 4
	if !strings.Contains(logs, `"error":"origin not allowed: https://evil.example"`) || !strings.Contains(logs, ticket.Hash(tk)) {
		t.Fatalf("audit: %s", logs)
	}
	// 거부된 시도로 티켓이 쓰이지 않았다: 허용 Origin(대소문자 무관)으로 그대로 붙는다
	c, _, err := dialPublic(t, e, sessionPath, protocols, http.Header{"Origin": {"HTTPS://App.Kodeploy.com"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, code := readAll(t, c); code != proto.CloseNormal {
		t.Fatalf("allowed origin: %d", code)
	}
	// Origin 없음(CLI)은 통과 — 나머지 공개 문 테스트가 모두 이 경우다. 내부 문은 Origin을 보지 않는다
	p := "/internal/v1/sessions/query"
	hdr := signed("GET", p, nil)
	hdr.Set("Origin", "https://evil.example")
	if _, resp, err := dialInternal(t, e, p, hdr); err != nil {
		t.Fatalf("internal door with origin: %v %v", resp, err)
	}
}

func TestPublicNoTarget(t *testing.T) {
	targets := map[string]target.Target{"app": {Pod: "web-1", Container: "app", Kind: target.App}}
	e := newEnv(t, 3, 4, targets)
	tk := e.ticketFor(t, shellBody)
	delete(targets, "app") // 발급 뒤 Pod가 사라졌다
	c, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	if _, code := readAll(t, c); code != proto.CloseNoTarget {
		t.Fatalf("got %d", code)
	}
}

func TestTooManySessions(t *testing.T) {
	e := newEnv(t, 1, 1, nil)
	e.runner.block = make(chan struct{})
	t1, t2 := e.ticketFor(t, shellBody), e.ticketFor(t, shellBody)
	c1, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + t1}, nil)
	c1.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, _, err := c1.ReadMessage(); err != nil { // 첫 출력까지 와야 세션이 자리를 잡은 것
		t.Fatal(err)
	}
	c2, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + t2}, nil)
	if _, code := readAll(t, c2); code != proto.CloseTooMany {
		t.Fatalf("got %d", code)
	}
	close(e.runner.block)
	readAll(t, c1)
}

// 동시 세션은 문마다 따로 센다: 터미널이 공개 문 자리를 다 써도 내부 문(DB 콘솔·덤프)은 열린다. 그 반대도 같다
func TestSessionLimitsPerDoor(t *testing.T) {
	e := newEnv(t, 1, 1, nil)
	e.runner.block = make(chan struct{})
	tk := e.ticketFor(t, shellBody)
	shell, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	shell.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, _, err := shell.ReadMessage(); err != nil { // 공개 문 1/1
		t.Fatal(err)
	}

	// 내부 문 복원 하나가 upload에서 기다린다 (내부 문 1/1). 공개 문이 가득 차 있어도 열린다
	data := []byte("CREATE TABLE users (id int);\n")
	rp := restorePath(data)
	rs, _, err := dialInternal(t, e, rp, signed("GET", rp, nil))
	if err != nil {
		t.Fatal(err)
	}
	rs.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data))
	deadline := time.Now().Add(3 * time.Second)
	for !slices.ContainsFunc(e.runner.execs(), func(x session.Exec) bool { return strings.Contains(x.Command[2], "cat >") }) {
		if time.Now().After(deadline) {
			t.Fatal("restore did not reach upload")
		}
		time.Sleep(10 * time.Millisecond)
	}

	// 지표 sessions_active{door}가 읽는 값: 문마다 1
	if p, i := e.s.ActiveByDoor(); p != 1 || i != 1 || e.s.Active() != 2 {
		t.Fatalf("active by door: public %d internal %d", p, i)
	}

	// 내부 문 두 번째는 4429, 공개 문 두 번째도 4429
	qp := "/internal/v1/sessions/query"
	q, _, err := dialInternal(t, e, qp, signed("GET", qp, nil))
	if err != nil {
		t.Fatal(err)
	}
	if _, code := readAll(t, q); code != proto.CloseTooMany {
		t.Fatalf("internal over limit: %d", code)
	}
	tk2 := e.ticketFor(t, shellBody)
	p2, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk2}, nil)
	if _, code := readAll(t, p2); code != proto.CloseTooMany {
		t.Fatalf("public over limit: %d", code)
	}

	rs.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	if _, code := readAll(t, rs); code != proto.CloseNormal {
		t.Fatalf("restore: %d", code)
	}
	close(e.runner.block)
	readAll(t, shell)
}

func TestIssueTicketErrors(t *testing.T) {
	e := newEnv(t, 3, 4, map[string]target.Target{"app": {Pod: "web-1", Container: "app", Kind: target.App}})
	cases := []struct {
		name string
		body string
		hdr  http.Header
		want int
	}{
		{"unsigned", shellBody, http.Header{}, 401},
		{"wrong signature", shellBody, signed("POST", "/internal/v1/tickets", []byte(`{}`)), 401},
		{"bad combo", `{"app":"web","target":"app","op":"console"}`, nil, 400},
		{"query op on public door", `{"app":"web","target":"db","op":"query"}`, nil, 400},
		{"bad app name", `{"app":"$(id)","target":"app","op":"shell"}`, nil, 400},
		{"unknown field", `{"app":"web","target":"app","op":"shell","ns":"x"}`, nil, 400},
		{"no db pod", `{"app":"web","target":"db","op":"console"}`, nil, 404},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			hdr := c.hdr
			if hdr == nil {
				hdr = signed("POST", "/internal/v1/tickets", []byte(c.body))
			}
			if resp, _ := e.issue(t, c.body, hdr); resp.StatusCode != c.want {
				t.Fatalf("got %d, want %d", resp.StatusCode, c.want)
			}
		})
	}
}

// 서명 밖 값(X-Kodeploy-Request-Id)과 기록용 값(app_id)은 실행에 영향이 없다
func TestUnsignedValuesRecordOnly(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	var seen []session.Exec
	for _, appID := range []string{"a1", "zzz"} {
		body := strings.Replace(shellBody, `"a1"`, `"`+appID+`"`, 1)
		hdr := signed("POST", "/internal/v1/tickets", []byte(body))
		hdr.Set(HeaderRequestID, "req-"+appID)
		resp, out := e.issue(t, body, hdr)
		if resp.StatusCode != 201 {
			t.Fatalf("issue: %d", resp.StatusCode)
		}
		c, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + out["ticket"]}, nil)
		readAll(t, c)
	}
	seen = e.runner.execs()
	if len(seen) != 2 || seen[0].Pod != seen[1].Pod || strings.Join(seen[0].Command, " ") != strings.Join(seen[1].Command, " ") {
		t.Fatalf("app_id changed execution: %+v", seen)
	}
	if logs := e.waitAudit(t, 4); !strings.Contains(logs, `"request_id":"req-zzz"`) {
		t.Fatalf("request id not recorded: %s", logs)
	}
}

func dialInternal(t *testing.T, e *env, path string, hdr http.Header) (*websocket.Conn, *http.Response, error) {
	t.Helper()
	d := websocket.Dialer{Subprotocols: []string{proto.Subprotocol}}
	c, resp, err := d.Dial(wsURL(e.int.URL, path), hdr)
	if c != nil {
		t.Cleanup(func() { c.Close() })
	}
	return c, resp, err
}

func TestInternalQuery(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	path := "/internal/v1/sessions/query"
	hdr := signed("GET", path, nil)
	hdr.Set(HeaderRequestID, "req-1")
	// 쿼리 문자열은 서명 밖이라 붙어 있어도 검증은 통과하고, 읽지도 않는다
	c, _, err := dialInternal(t, e, path+"?op=dump", hdr)
	if err != nil {
		t.Fatal(err)
	}
	sql := "SELECT '한글', `x` FROM t WHERE a = \"$(id)\""
	c.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, []byte(sql)))
	c.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	out, code := readAll(t, c)
	if code != proto.CloseNormal || string(out[proto.ChStdout]) != "id\n"+sql || proto.StatusExitCode(out[proto.ChStatus]) != 0 {
		t.Fatalf("code %d out %q", code, out[proto.ChStdout])
	}
	if !strings.Contains(e.runner.execs()[0].Command[2], "sql=$(cat)") {
		t.Fatalf("op from query string was used: %q", e.runner.execs()[0].Command)
	}
	sum := sha256.Sum256([]byte(sql))
	logs := e.waitAudit(t, 1)
	if strings.Contains(logs, "한글") || !strings.Contains(logs, hex.EncodeToString(sum[:])) ||
		!strings.Contains(logs, `"request_id":"req-1"`) || !strings.Contains(logs, `"user_id":""`) || !strings.Contains(logs, `"ns":"`+ns+`"`) {
		t.Fatalf("audit: %s", logs)
	}
}

func TestInternalRejects(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	// 서명 없음 401
	if _, resp, err := dialInternal(t, e, "/internal/v1/sessions/dump", nil); err == nil || resp.StatusCode != 401 {
		t.Fatalf("unsigned: %v", resp)
	}
	// 공개 문 작업은 내부 문에 없다
	p := "/internal/v1/sessions/shell"
	if _, resp, _ := dialInternal(t, e, p, signed("GET", p, nil)); resp == nil || resp.StatusCode != 404 {
		t.Fatalf("shell on internal door: %v", resp)
	}
	// restore 경로 형식 오류 400
	for _, p := range []string{
		"/internal/v1/sessions/restore/0/" + strings.Repeat("a", 64),
		"/internal/v1/sessions/restore/12/xyz",
		"/internal/v1/sessions/restore/2097152/" + strings.Repeat("a", 64), // RestoreMaxBytes(1MiB) 초과
	} {
		if _, resp, _ := dialInternal(t, e, p, signed("GET", p, nil)); resp == nil || resp.StatusCode != 400 {
			t.Fatalf("%s: %v", p, resp)
		}
	}
	// query stdin 상한 초과 4400
	p = "/internal/v1/sessions/query"
	c, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	big := bytes.Repeat([]byte("x"), 40000)
	for i := 0; i < 3; i++ {
		c.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, big))
	}
	if _, code := readAll(t, c); code != proto.CloseBadInput {
		t.Fatalf("stdin limit: %d", code)
	}
}

func TestInternalRestore(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	data := []byte("CREATE TABLE users (id int);\n")
	sum := sha256.Sum256(data)
	p := fmt.Sprintf("/internal/v1/sessions/restore/%d/%s", len(data), strings.ToUpper(hex.EncodeToString(sum[:])))
	c, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	c.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data))
	c.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	out, code := readAll(t, c)
	var res struct {
		Status    string `json:"status"`
		DBChanged bool   `json:"db_changed"`
		Tables    []struct {
			Name string `json:"name"`
			Rows int64  `json:"rows"`
		} `json:"tables"`
	}
	if err := json.Unmarshal(out[proto.ChStatus], &res); err != nil {
		t.Fatalf("status: %s", out[proto.ChStatus])
	}
	if code != proto.CloseNormal || res.Status != "Success" || !res.DBChanged || len(res.Tables) != 1 || res.Tables[0].Rows != 42 {
		t.Fatalf("code %d result %s", code, out[proto.ChStatus])
	}
	if !bytes.Equal(e.runner.uploaded(), data) {
		t.Fatalf("uploaded %q", e.runner.uploaded())
	}
}

func restorePath(data []byte) string {
	sum := sha256.Sum256(data)
	return fmt.Sprintf("/internal/v1/sessions/restore/%d/%s", len(data), hex.EncodeToString(sum[:]))
}

// load 도중 끊겨 결과를 모르면 백업을 남기고, 결과 JSON에 Pod·컨테이너·경로를 싣는다
func TestInternalRestoreKeepsBackup(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	e.runner.loadErr = errors.New("stream reset")
	data := []byte("CREATE TABLE users (id int);\n")
	p := restorePath(data)
	c, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	c.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data))
	c.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	out, _ := readAll(t, c)
	var res struct {
		FailedStep string `json:"failed_step"`
		DBChanged  bool   `json:"db_changed"`
		Backup     struct {
			Pod, Container, Path string
		} `json:"backup"`
	}
	if err := json.Unmarshal(out[proto.ChStatus], &res); err != nil {
		t.Fatalf("status: %s", out[proto.ChStatus])
	}
	if res.FailedStep != "load" || !res.DBChanged || res.Backup.Pod != "mysql-0" || res.Backup.Container != "mysql" ||
		!regexp.MustCompile(`^/tmp/kodeploy-restore/backup-\d{8}T\d{6}Z-[0-9a-f]{6}\.sql\.gz$`).MatchString(res.Backup.Path) {
		t.Fatalf("result %s", out[proto.ChStatus])
	}
	execs := e.runner.execs()
	last := execs[len(execs)-1].Command
	if !strings.Contains(last[2], `rm -f "$1/restore.dump"`) || last[5] != "" {
		t.Fatalf("cleanup must keep the backup: %q", last)
	}
}

// 이전 복원이 남긴 백업이 있으면 precheck에서 거부한다. 남은 백업의 Pod·컨테이너·경로가 결과에 실린다
func TestInternalRestoreRefusesUnresolved(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	old := "/tmp/kodeploy-restore/backup-20261112T000000Z-123456.sql.gz"
	e.runner.pending = []string{old}
	data := []byte("CREATE TABLE users (id int);\n")
	p := restorePath(data)
	c, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	c.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data))
	c.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	out, _ := readAll(t, c)
	var res struct {
		FailedStep string `json:"failed_step"`
		DBChanged  bool   `json:"db_changed"`
		Unresolved []struct {
			Pod, Container, Path string
		} `json:"unresolved_backups"`
	}
	if err := json.Unmarshal(out[proto.ChStatus], &res); err != nil {
		t.Fatalf("status: %s", out[proto.ChStatus])
	}
	if res.FailedStep != "precheck" || res.DBChanged || len(res.Unresolved) != 1 ||
		res.Unresolved[0].Pod != "mysql-0" || res.Unresolved[0].Container != "mysql" || res.Unresolved[0].Path != old {
		t.Fatalf("result %s", out[proto.ChStatus])
	}
	for _, x := range e.runner.execs() {
		if strings.Contains(x.Command[2], "mysqldump") || strings.Contains(x.Command[2], "cat >") {
			t.Fatalf("must not touch the db or upload: %q", x.Command)
		}
	}
}

// 복원은 한 번에 하나: 진행 중이면 두 번째는 4429
func TestInternalRestoreOneAtATime(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	data := []byte("CREATE TABLE users (id int);\n")
	p := restorePath(data)
	c1, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	c1.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data)) // CLOSE 전: upload에서 기다린다
	deadline := time.Now().Add(3 * time.Second)
	for !slices.ContainsFunc(e.runner.execs(), func(x session.Exec) bool { return strings.Contains(x.Command[2], "cat >") }) {
		if time.Now().After(deadline) {
			t.Fatal("first restore did not reach upload")
		}
		time.Sleep(10 * time.Millisecond)
	}
	c2, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	if _, code := readAll(t, c2); code != proto.CloseTooMany {
		t.Fatalf("second restore: %d", code)
	}
	c1.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	if out, code := readAll(t, c1); code != proto.CloseNormal || !strings.Contains(string(out[proto.ChStatus]), `"status":"Success"`) {
		t.Fatalf("first restore: %d %s", code, out[proto.ChStatus])
	}
	// 끝나면 다음 복원을 받는다
	c3, _, err := dialInternal(t, e, p, signed("GET", p, nil))
	if err != nil {
		t.Fatal(err)
	}
	c3.WriteMessage(websocket.BinaryMessage, proto.Frame(proto.ChStdin, data))
	c3.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	if _, code := readAll(t, c3); code != proto.CloseNormal {
		t.Fatalf("third restore: %d", code)
	}
}

func TestShutdown(t *testing.T) {
	e := newEnv(t, 3, 4, nil)
	ops := httptest.NewServer(e.s.Ops())
	defer ops.Close()
	e.runner.block = make(chan struct{})
	defer close(e.runner.block)

	tk := e.ticketFor(t, shellBody)
	c, _, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol, proto.TicketPrefix + tk}, nil)
	c.SetReadDeadline(time.Now().Add(5 * time.Second))
	if _, _, err := c.ReadMessage(); err != nil {
		t.Fatal(err)
	}

	e.s.BeginShutdown()
	if _, code := readAll(t, c); code != proto.CloseShutdown {
		t.Fatalf("tty session: %d", code)
	}
	resp, _ := http.Get(ops.URL + "/healthz")
	resp.Body.Close()
	if resp.StatusCode != 503 {
		t.Fatalf("healthz: %d", resp.StatusCode)
	}
	if _, resp, _ := dialPublic(t, e, sessionPath, []string{proto.Subprotocol}, nil); resp == nil || resp.StatusCode != 503 {
		t.Fatalf("new connection while draining: %v", resp)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := e.s.Wait(ctx); err != nil {
		t.Fatalf("wait: %v", err)
	}
}
