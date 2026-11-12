package testclient

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"go.uber.org/goleak"
	utilexec "k8s.io/client-go/util/exec"

	"github.com/kodeploy/kodeploy/exec/internal/api"
	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }

const ns = "tenant-0a1b2c3d"

var key = []byte(strings.Repeat("k", 32))

type finder struct{}

func (finder) Find(_ context.Context, t, app string) (target.Target, error) {
	switch t {
	case "app":
		return target.Target{Pod: "web-1", Container: "app", Kind: target.App}, nil
	case "db":
		return target.Target{Pod: "pg-0", Container: "postgres", Kind: target.Postgres}, nil
	}
	return target.Target{}, target.ErrNotFound
}

// runner는 셸 하나와 query·restore를 흉내 낸다.
type runner struct {
	mu   sync.Mutex
	file []byte
}

func (r *runner) Run(ctx context.Context, e session.Exec) error {
	s := e.Command[2]
	if strings.Contains(s, "bash --norc") {
		buf := make([]byte, 256)
		for {
			n, err := e.Stdin.Read(buf)
			if err != nil {
				return err
			}
			line := string(buf[:n])
			if strings.HasPrefix(line, "exit") {
				return utilexec.CodeExitError{Err: errors.New("exit"), Code: 7}
			}
			fmt.Fprintf(e.Stdout, "%s\r\nkd-42\r\n", strings.TrimSpace(line)) // tty echo + 결과
		}
	}
	var in []byte
	if e.Stdin != nil {
		in, _ = io.ReadAll(e.Stdin)
	}
	switch {
	case strings.Contains(s, "df -Pk"):
		fmt.Fprint(e.Stdout, "F 1 U A C M\nfs 1 1 1048576 1% /\n")
	case strings.Contains(s, `cat > "$1/restore.dump"`):
		r.mu.Lock()
		r.file = in
		r.mu.Unlock()
	case strings.Contains(s, "wc -c"):
		r.mu.Lock()
		f := r.file
		r.mu.Unlock()
		sum := sha256.Sum256(f)
		fmt.Fprintf(e.Stdout, "%d\n%s  x\n", len(f), hex.EncodeToString(sum[:]))
	case strings.Contains(s, `-c "$sql"`):
		fmt.Fprintf(e.Stdout, "?column?\n%s\n", in)
	}
	return nil
}

func servers(t *testing.T) (pub, internal *httptest.Server) {
	t.Helper()
	s := api.New(api.Options{
		Namespace: ns, Key: key, Tickets: ticket.New(30*time.Second, 10), Finder: finder{}, Runner: &runner{},
		Audit: audit.New(io.Discard), TTYIdleTimeout: time.Minute, TTYMax: time.Minute, NonTTYMax: time.Minute,
		PingInterval: time.Second, MaxSessions: 3, RestoreMaxBytes: 1 << 20, RestoreWorkdir: "/tmp/kodeploy-restore",
	})
	pub, internal = httptest.NewServer(s.Public()), httptest.NewServer(s.Internal())
	t.Cleanup(func() { pub.Close(); internal.Close(); s.Close() })
	return pub, internal
}

func TestTicketAndPublicShell(t *testing.T) {
	pub, internal := servers(t)
	in := &Internal{Base: internal.URL, Key: key}
	ctx := context.Background()
	tk, err := in.Ticket(ctx, TicketRequest{App: "web", Target: "app", Op: "shell"})
	if err != nil || tk.Ticket == "" {
		t.Fatalf("ticket: %+v %v", tk, err)
	}
	url := "ws" + strings.TrimPrefix(pub.URL, "http") + "/exec/" + ns + "/v1/sessions"
	c, err := DialPublic(ctx, url, tk.Ticket, false)
	if err != nil {
		t.Fatal(err)
	}
	c.Stdin([]byte("echo kd-$((6*7))\n"))
	if out, err := c.ReadUntil([]byte("kd-42"), 5*time.Second); err != nil {
		t.Fatalf("read until: %q %v", out, err)
	}
	c.Stdin([]byte("exit 7\n"))
	res, err := c.Read(5*time.Second, nil)
	if err != nil || res.CloseCode != 1000 || res.ExitCode != 7 {
		t.Fatalf("result %+v %v", res, err)
	}
	c.Close()

	// 재사용 4401, 헤더로 전달
	c, _ = DialPublic(ctx, url, tk.Ticket, true)
	if res, _ := c.Read(5*time.Second, nil); res.CloseCode != 4401 {
		t.Fatalf("reuse via header: %d", res.CloseCode)
	}
	c.Close()

	// 서명이 틀리면 발급이 막힌다
	bad := &Internal{Base: internal.URL, Key: []byte(strings.Repeat("x", 32))}
	if _, err := bad.Ticket(ctx, TicketRequest{App: "web", Target: "app", Op: "shell"}); err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("bad key: %v", err)
	}
}

func TestInternalQueryAndRestore(t *testing.T) {
	_, internal := servers(t)
	in := &Internal{Base: internal.URL, Key: key}
	ctx := context.Background()

	c, err := in.Dial(ctx, "/internal/v1/sessions/query")
	if err != nil {
		t.Fatal(err)
	}
	c.Stdin([]byte("SELECT 1"))
	c.CloseStdin()
	res, err := c.Read(5*time.Second, nil)
	if err != nil || res.ExitCode != 0 || string(res.Stdout) != "?column?\nSELECT 1\n" {
		t.Fatalf("query %+v %v", res, err)
	}
	c.Close()

	data := []byte("CREATE TABLE t (id int);\n")
	sum := sha256.Sum256(data)
	c, err = in.Dial(ctx, RestorePath(int64(len(data)), hex.EncodeToString(sum[:])))
	if err != nil {
		t.Fatal(err)
	}
	c.Stdin(data)
	c.CloseStdin()
	res, err = c.Read(5*time.Second, nil)
	var out struct {
		Status string `json:"status"`
	}
	if err != nil || json.Unmarshal(res.Status, &out) != nil || out.Status != "Success" || res.CloseCode != 1000 {
		t.Fatalf("restore %s close=%d %v", res.Status, res.CloseCode, err)
	}
	c.Close()

	// 서명 없는 세션은 핸드셰이크에서 401
	bad := &Internal{Base: internal.URL, Key: []byte(strings.Repeat("x", 32))}
	if _, err := bad.Dial(ctx, "/internal/v1/sessions/query"); err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("bad key: %v", err)
	}
}
