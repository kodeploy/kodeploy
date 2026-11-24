package shell

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const ticket = "TICKET-SECRET-0123456789"

// fakeGate는 kodeploy-exec 공개 문을 흉내 낸다. 받은 요청 헤더와 프레임을 남기고, script대로 답한다.
type fakeGate struct {
	mu     sync.Mutex
	srv    *httptest.Server
	header http.Header
	url    string
	frames [][]byte
	got    chan []byte
	script func(c *websocket.Conn, g *fakeGate)
}

func newGate(t *testing.T, script func(c *websocket.Conn, g *fakeGate)) *fakeGate {
	g := &fakeGate{got: make(chan []byte, 64), script: script}
	up := websocket.Upgrader{Subprotocols: []string{"kodeploy.exec.v1"}}
	g.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		g.mu.Lock()
		g.header, g.url = r.Header.Clone(), r.URL.String()
		g.mu.Unlock()
		if r.Header.Get("Authorization") != "Ticket "+ticket {
			http.Error(w, "no ticket", http.StatusUnauthorized)
			return
		}
		c, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer c.Close()
		go func() {
			for {
				_, msg, err := c.ReadMessage()
				if err != nil {
					close(g.got)
					return
				}
				g.mu.Lock()
				g.frames = append(g.frames, msg)
				g.mu.Unlock()
				g.got <- msg
			}
		}()
		g.script(c, g)
	}))
	t.Cleanup(g.srv.Close)
	return g
}

func (g *fakeGate) wsURL() string {
	return "ws" + strings.TrimPrefix(g.srv.URL, "http") + "/exec/app-1/v1/sessions"
}

// next는 다음 프레임 중 채널이 ch인 것을 기다린다.
func (g *fakeGate) next(t *testing.T, ch byte) []byte {
	t.Helper()
	timeout := time.After(5 * time.Second)
	for {
		select {
		case f, ok := <-g.got:
			if !ok {
				t.Fatalf("채널 %d를 받기 전에 끊겼다", ch)
			}
			if len(f) > 0 && f[0] == ch {
				return f[1:]
			}
		case <-timeout:
			t.Fatalf("채널 %d를 받지 못했다", ch)
		}
	}
}

func frame(ch byte, s string) []byte { return append([]byte{ch}, s...) }

func status(code int) string {
	if code == 0 {
		return `{"status":"Success"}`
	}
	b, _ := json.Marshal(map[string]any{"status": "Failure", "reason": "NonZeroExitCode",
		"details": map[string]any{"causes": []map[string]string{{"reason": "ExitCode", "message": itoa(code)}}}})
	return string(b)
}

func itoa(n int) string { b, _ := json.Marshal(n); return string(b) }

type syncBuf struct {
	mu sync.Mutex
	b  strings.Builder
}

func (s *syncBuf) Write(p []byte) (int, error) { s.mu.Lock(); defer s.mu.Unlock(); return s.b.Write(p) }
func (s *syncBuf) String() string              { s.mu.Lock(); defer s.mu.Unlock(); return s.b.String() }

func TestSessionSendsTicketInTheHeaderOnlyAndReturnsTheExitCode(t *testing.T) {
	g := newGate(t, func(c *websocket.Conn, g *fakeGate) {
		size := g.next(t, 4)
		if string(size) != `{"Height":40,"Width":132}` {
			t.Errorf("처음 창 크기 %s", size)
		}
		in := g.next(t, 0)
		_ = c.WriteMessage(websocket.BinaryMessage, frame(1, "out:"+string(in)))
		_ = c.WriteMessage(websocket.BinaryMessage, frame(2, "err"))
		_ = c.WriteMessage(websocket.BinaryMessage, frame(3, status(3)))
		_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""))
		time.Sleep(50 * time.Millisecond)
	})
	pr, pw := io.Pipe()
	defer pw.Close()
	go func() { _, _ = pw.Write([]byte("안녕 한글\n")) }()
	var out, errb syncBuf
	res, err := Run(context.Background(), Options{URL: g.wsURL(), Ticket: ticket, Protocol: "kodeploy.exec.v1",
		Stdin: pr, Stdout: &out, Stderr: &errb, Size: Size{Cols: 132, Rows: 40}})
	if err != nil || res.ExitCode != 3 || res.CloseCode != 1000 {
		t.Fatalf("%+v %v", res, err)
	}
	if out.String() != "out:안녕 한글\n" || errb.String() != "err" {
		t.Fatalf("stdout %q stderr %q", out.String(), errb.String())
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.header.Get("Origin") != "" {
		t.Fatalf("Origin을 보냈다: %q", g.header.Get("Origin"))
	}
	if strings.Contains(g.url, ticket) || strings.Contains(g.header.Get("Sec-WebSocket-Protocol"), ticket) {
		t.Fatalf("티켓이 URL이나 서브프로토콜에 있다: %s %s", g.url, g.header.Get("Sec-WebSocket-Protocol"))
	}
	if g.header.Get("Sec-WebSocket-Protocol") != "kodeploy.exec.v1" {
		t.Fatalf("서브프로토콜 %q", g.header.Get("Sec-WebSocket-Protocol"))
	}
}

func TestResizeGoesToChannel4(t *testing.T) {
	resize := make(chan Size, 1)
	g := newGate(t, func(c *websocket.Conn, g *fakeGate) {
		g.next(t, 4)
		resize <- Size{Cols: 200, Rows: 50}
		if got := string(g.next(t, 4)); got != `{"Height":50,"Width":200}` {
			t.Errorf("바뀐 크기 %s", got)
		}
		_ = c.WriteMessage(websocket.BinaryMessage, frame(3, status(0)))
		_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""))
		time.Sleep(50 * time.Millisecond)
	})
	pr, pw := io.Pipe()
	defer pw.Close()
	res, err := Run(context.Background(), Options{URL: g.wsURL(), Ticket: ticket, Protocol: "kodeploy.exec.v1",
		Stdin: pr, Stdout: io.Discard, Stderr: io.Discard, Size: Size{Cols: 80, Rows: 24}, Resize: resize})
	if err != nil || res.ExitCode != 0 {
		t.Fatalf("%+v %v", res, err)
	}
}

func TestCloseWithoutStatus(t *testing.T) {
	g := newGate(t, func(c *websocket.Conn, g *fakeGate) {
		_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(4404, "target pod not found"))
		time.Sleep(50 * time.Millisecond)
	})
	pr, pw := io.Pipe()
	defer pw.Close()
	res, err := Run(context.Background(), Options{URL: g.wsURL(), Ticket: ticket, Protocol: "kodeploy.exec.v1",
		Stdin: pr, Stdout: io.Discard, Stderr: io.Discard})
	if err != nil || res.ExitCode != -1 || res.CloseCode != 4404 {
		t.Fatalf("%+v %v", res, err)
	}
}

func TestStdinEOFClosesTheStdinChannel(t *testing.T) {
	g := newGate(t, func(c *websocket.Conn, g *fakeGate) {
		if got := g.next(t, 255); string(got) != "\x00" {
			t.Errorf("닫기 프레임 %q", got)
		}
		_ = c.WriteMessage(websocket.BinaryMessage, frame(3, status(0)))
		_ = c.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1000, ""))
		time.Sleep(50 * time.Millisecond)
	})
	res, err := Run(context.Background(), Options{URL: g.wsURL(), Ticket: ticket, Protocol: "kodeploy.exec.v1",
		Stdin: strings.NewReader(""), Stdout: io.Discard, Stderr: io.Discard})
	if err != nil || res.ExitCode != 0 {
		t.Fatalf("%+v %v", res, err)
	}
}

func TestDialRejected(t *testing.T) {
	g := newGate(t, func(*websocket.Conn, *fakeGate) {})
	_, err := Run(context.Background(), Options{URL: g.wsURL(), Ticket: "wrong", Protocol: "kodeploy.exec.v1",
		Stdin: strings.NewReader(""), Stdout: io.Discard, Stderr: io.Discard})
	var de *DialError
	if !errors.As(err, &de) || de.Status != 401 {
		t.Fatalf("%v", err)
	}
}

func TestCancelClosesTheSession(t *testing.T) {
	closed := make(chan int, 1)
	g := newGate(t, func(c *websocket.Conn, g *fakeGate) {
		for range g.got {
		}
		closed <- 1
	})
	ctx, cancel := context.WithCancel(context.Background())
	pr, pw := io.Pipe()
	defer pw.Close()
	go func() { time.Sleep(100 * time.Millisecond); cancel() }()
	_, err := Run(ctx, Options{URL: g.wsURL(), Ticket: ticket, Protocol: "kodeploy.exec.v1",
		Stdin: pr, Stdout: io.Discard, Stderr: io.Discard})
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("%v", err)
	}
	select {
	case <-closed:
	case <-time.After(5 * time.Second):
		t.Fatal("서버 쪽 연결이 닫히지 않았다")
	}
}

func TestExitCode(t *testing.T) {
	for in, want := range map[string]int{status(0): 0, status(7): 7, `{"status":"Failure"}`: 1, `nope`: 1} {
		if got := ExitCode([]byte(in)); got != want {
			t.Fatalf("%s → %d, want %d", in, got, want)
		}
	}
}
