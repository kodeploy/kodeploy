package session

import (
	"bytes"
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"go.uber.org/goleak"
	"k8s.io/client-go/tools/remotecommand"
	utilexec "k8s.io/client-go/util/exec"

	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/target"
)

func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }

// harness는 Serve를 도는 서버 하나와 거기 붙은 클라이언트다.
type harness struct {
	srv    *httptest.Server
	client *websocket.Conn
	result chan Result
}

func start(t *testing.T, o Options, job Job) *harness {
	t.Helper()
	h := &harness{result: make(chan Result, 1)}
	up := websocket.Upgrader{CheckOrigin: func(*http.Request) bool { return true }}
	h.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := up.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		h.result <- Serve(context.Background(), NewConn(ws), o, job)
	}))
	url := "ws" + strings.TrimPrefix(h.srv.URL, "http")
	c, _, err := websocket.DefaultDialer.Dial(url, nil)
	if err != nil {
		t.Fatal(err)
	}
	h.client = c
	t.Cleanup(func() {
		h.client.Close()
		h.srv.Close()
	})
	return h
}

func (h *harness) send(t *testing.T, ch byte, p []byte) {
	t.Helper()
	if err := h.client.WriteMessage(websocket.BinaryMessage, proto.Frame(ch, p)); err != nil {
		t.Fatal(err)
	}
}

// collect는 close까지 읽어 채널별 내용과 close 코드를 돌려준다.
func (h *harness) collect(t *testing.T) (map[byte][]byte, int) {
	t.Helper()
	out := map[byte][]byte{}
	h.client.SetReadDeadline(time.Now().Add(5 * time.Second))
	for {
		_, msg, err := h.client.ReadMessage()
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

func (h *harness) wait(t *testing.T) Result {
	t.Helper()
	select {
	case r := <-h.result:
		return r
	case <-time.After(5 * time.Second):
		t.Fatal("serve did not return")
	}
	return Result{}
}

// echoJob은 stdin을 끝까지 읽어 stdout으로, 길이를 stderr로 돌려준다.
func echoJob(ctx context.Context, jio JobIO) (Outcome, error) {
	b, err := io.ReadAll(jio.Stdin)
	if err != nil {
		return Outcome{}, err
	}
	jio.Stdout.Write(b)
	jio.Stderr.Write([]byte("err"))
	return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(0)), ExitCode: 0}, nil
}

func TestStdinCloseAndStatus(t *testing.T) {
	h := start(t, Options{Stdin: true, PingInterval: time.Second}, echoJob)
	payload := []byte("한글 \x00\x01\xfe\xff")
	h.send(t, proto.ChStdin, payload[:4])
	h.send(t, proto.ChStdin, payload[4:])
	h.client.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	out, code := h.collect(t)
	if code != proto.CloseNormal {
		t.Fatalf("close code %d", code)
	}
	if !bytes.Equal(out[proto.ChStdout], payload) || string(out[proto.ChStderr]) != "err" {
		t.Fatalf("stdout %q stderr %q", out[proto.ChStdout], out[proto.ChStderr])
	}
	if proto.StatusExitCode(out[proto.ChStatus]) != 0 {
		t.Fatalf("status %s", out[proto.ChStatus])
	}
	r := h.wait(t)
	if r.CloseCode != 1000 || r.BytesIn != int64(len(payload))+1 || // +1: CLOSE 프레임 내용(채널 번호)
		r.BytesOut != int64(len(payload))+3+int64(len(out[proto.ChStatus])) {
		t.Fatalf("result %+v", r)
	}
}

func TestTextFrameRejected(t *testing.T) {
	h := start(t, Options{Stdin: true}, echoJob)
	h.client.WriteMessage(websocket.TextMessage, []byte("ls\n"))
	if _, code := h.collect(t); code != proto.CloseUnsupportedData {
		t.Fatalf("got %d", code)
	}
	h.wait(t)
}

func TestStdinLimit(t *testing.T) {
	h := start(t, Options{Stdin: true, MaxStdin: 5}, echoJob)
	h.send(t, proto.ChStdin, []byte("123"))
	h.send(t, proto.ChStdin, []byte("456"))
	if _, code := h.collect(t); code != proto.CloseBadInput {
		t.Fatalf("got %d", code)
	}
	h.wait(t)
}

func TestNoStdinSession(t *testing.T) {
	block := func(ctx context.Context, jio JobIO) (Outcome, error) { <-ctx.Done(); return Outcome{}, ctx.Err() }
	h := start(t, Options{}, block)
	h.send(t, proto.ChStdin, []byte("x"))
	if _, code := h.collect(t); code != proto.CloseBadInput {
		t.Fatalf("got %d", code)
	}
	h.wait(t)
}

func blockJob(ctx context.Context, jio JobIO) (Outcome, error) {
	<-ctx.Done()
	return Outcome{ExitCode: -1}, ctx.Err()
}

func TestIdleTimeoutTTYOnly(t *testing.T) {
	h := start(t, Options{TTY: true, Stdin: true, IdleTimeout: 100 * time.Millisecond}, blockJob)
	if _, code := h.collect(t); code != proto.CloseIdle {
		t.Fatalf("tty idle: got %d", code)
	}
	h.wait(t)

	// 비대화형은 유휴 타임아웃이 없다: 출력 없이 300ms 걸려도 정상 종료
	slow := func(ctx context.Context, jio JobIO) (Outcome, error) {
		time.Sleep(300 * time.Millisecond)
		return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(0))}, nil
	}
	h2 := start(t, Options{IdleTimeout: 100 * time.Millisecond}, slow)
	if _, code := h2.collect(t); code != proto.CloseNormal {
		t.Fatalf("non-tty: got %d", code)
	}
	h2.wait(t)
}

func TestIdleResetByInput(t *testing.T) {
	h := start(t, Options{TTY: true, Stdin: true, IdleTimeout: 200 * time.Millisecond}, func(ctx context.Context, jio JobIO) (Outcome, error) {
		go io.Copy(io.Discard, jio.Stdin)
		<-ctx.Done()
		return Outcome{}, ctx.Err()
	})
	start := time.Now()
	for i := 0; i < 5; i++ {
		time.Sleep(100 * time.Millisecond)
		h.send(t, proto.ChStdin, []byte("k"))
	}
	if _, code := h.collect(t); code != proto.CloseIdle {
		t.Fatalf("got %d", code)
	}
	if time.Since(start) < 600*time.Millisecond {
		t.Fatal("input should have reset the idle timer")
	}
	h.wait(t)
}

func TestMaxDuration(t *testing.T) {
	h := start(t, Options{MaxDuration: 100 * time.Millisecond}, blockJob)
	if _, code := h.collect(t); code != proto.CloseMaxDuration {
		t.Fatalf("got %d", code)
	}
	h.wait(t)
}

func TestShutdown(t *testing.T) {
	stop := make(chan struct{})
	h := start(t, Options{TTY: true, Stdin: true, Shutdown: stop}, blockJob)
	close(stop)
	if _, code := h.collect(t); code != proto.CloseShutdown {
		t.Fatalf("tty: got %d", code)
	}
	h.wait(t)

	// 비대화형은 종료 신호가 와도 끝까지 마친다
	stop2 := make(chan struct{})
	close(stop2)
	done := func(ctx context.Context, jio JobIO) (Outcome, error) {
		time.Sleep(50 * time.Millisecond)
		return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(0))}, ctx.Err()
	}
	h2 := start(t, Options{Shutdown: stop2}, done)
	if _, code := h2.collect(t); code != proto.CloseNormal {
		t.Fatalf("non-tty: got %d", code)
	}
	h2.wait(t)
}

func TestResize(t *testing.T) {
	got := make(chan remotecommand.TerminalSize, 4)
	job := func(ctx context.Context, jio JobIO) (Outcome, error) {
		for i := 0; i < 2; i++ {
			s := jio.Size.Next()
			if s == nil {
				return Outcome{}, ctx.Err()
			}
			got <- *s
		}
		return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(0))}, nil
	}
	h := start(t, Options{TTY: true, Stdin: true, Cols: 120, Rows: 32}, job)
	h.send(t, proto.ChResize, proto.EncodeSize(80, 24))
	if _, code := h.collect(t); code != proto.CloseNormal {
		t.Fatalf("close %d", code)
	}
	h.wait(t)
	if a, b := <-got, <-got; a != (remotecommand.TerminalSize{Width: 120, Height: 32}) || b != (remotecommand.TerminalSize{Width: 80, Height: 24}) {
		t.Fatalf("sizes %v %v", a, b)
	}

	// 잘못된 창 크기는 위반
	h2 := start(t, Options{TTY: true, Stdin: true}, blockJob)
	h2.send(t, proto.ChResize, []byte(`{"Width":0}`))
	if _, code := h2.collect(t); code != proto.CloseBadInput {
		t.Fatalf("bad size: %d", code)
	}
	h2.wait(t)
}

func TestClientGone(t *testing.T) {
	h := start(t, Options{TTY: true, Stdin: true}, blockJob)
	h.client.Close()
	if r := h.wait(t); r.CloseCode != closeAbnormal {
		t.Fatalf("got %+v", r)
	}
}

// --- ExecJob: 가짜 Runner로 exit code → Status ---

type fakeRunner struct {
	mu   sync.Mutex
	seen []Exec
	run  func(ctx context.Context, e Exec) error
}

func (f *fakeRunner) Run(ctx context.Context, e Exec) error {
	f.mu.Lock()
	f.seen = append(f.seen, e)
	f.mu.Unlock()
	return f.run(ctx, e)
}

func TestExecJobExitCode(t *testing.T) {
	r := &fakeRunner{run: func(ctx context.Context, e Exec) error {
		e.Stdout.Write([]byte("out"))
		return utilexec.CodeExitError{Err: errors.New("exit 3"), Code: 3}
	}}
	tg := target.Target{Pod: "mysql-0", Container: "mysql", Kind: target.MySQL}
	cmd, _ := ops.Dump(target.MySQL)
	h := start(t, Options{}, ExecJob(r, tg, cmd))
	out, code := h.collect(t)
	if code != proto.CloseNormal || string(out[proto.ChStdout]) != "out" || proto.StatusExitCode(out[proto.ChStatus]) != 3 {
		t.Fatalf("code %d out %q status %s", code, out[proto.ChStdout], out[proto.ChStatus])
	}
	if res := h.wait(t); res.ExitCode != 3 {
		t.Fatalf("result %+v", res)
	}
	e := r.seen[0]
	if e.Pod != "mysql-0" || e.Container != "mysql" || e.TTY || e.Stdin != nil || e.Stderr == nil || e.Size != nil {
		t.Fatalf("exec %+v", e)
	}
}

func TestExecJobTTYStreams(t *testing.T) {
	r := &fakeRunner{run: func(ctx context.Context, e Exec) error { return nil }}
	tg := target.Target{Pod: "web-1", Container: "app", Kind: target.App}
	h := start(t, Options{TTY: true, Stdin: true}, ExecJob(r, tg, ops.Shell("web")))
	if _, code := h.collect(t); code != proto.CloseNormal {
		t.Fatalf("close %d", code)
	}
	h.wait(t)
	e := r.seen[0]
	// tty: stderr 없음 (K8s가 stdout으로 합친다), 창 크기 큐 있음
	if !e.TTY || e.Stdin == nil || e.Stderr != nil || e.Size == nil {
		t.Fatalf("exec %+v", e)
	}
}

func TestExecJobInternalError(t *testing.T) {
	r := &fakeRunner{run: func(ctx context.Context, e Exec) error { return errors.New("dial apiserver: refused") }}
	h := start(t, Options{}, ExecJob(r, target.Target{Pod: "p", Container: "c"}, ops.RedisConsole()))
	out, code := h.collect(t)
	if code != proto.CloseInternal || proto.StatusExitCode(out[proto.ChStatus]) != 1 {
		t.Fatalf("code %d status %s", code, out[proto.ChStatus])
	}
	h.wait(t)
}

func TestStdinTap(t *testing.T) {
	var tap bytes.Buffer
	h := start(t, Options{Stdin: true, StdinTap: &tap}, echoJob)
	h.send(t, proto.ChStdin, []byte("SELECT 1"))
	h.client.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
	h.collect(t)
	h.wait(t)
	if tap.String() != "SELECT 1" {
		t.Fatalf("tap %q", tap.String())
	}
}
