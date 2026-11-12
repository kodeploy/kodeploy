package ticket

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"go.uber.org/goleak"
)

func TestMain(m *testing.M) { goleak.VerifyTestMain(m) }

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time      { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) add(d time.Duration) { c.mu.Lock(); c.t = c.t.Add(d); c.mu.Unlock() }

func newStore(max int) (*Store, *clock) {
	c := &clock{t: time.Unix(1_790_000_000, 0)}
	s := New(30*time.Second, max)
	s.now = c.now
	return s, c
}

func TestIssueConsume(t *testing.T) {
	s, c := newStore(10)
	tk, info, err := s.Issue(Info{UserID: "u", AppID: "a", App: "web", Target: "app", Op: "shell", Cols: 120, Rows: 32})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := base64.RawURLEncoding.DecodeString(tk)
	if err != nil || len(raw) != 32 {
		t.Fatalf("ticket must be base64url of 32 bytes: %q %v", tk, err)
	}
	if !info.ExpiresAt.Equal(c.now().Add(30 * time.Second)) {
		t.Fatalf("expires: %v", info.ExpiresAt)
	}

	got, ok := s.Consume(tk)
	if !ok || got.App != "web" || got.Target != "app" || got.Cols != 120 || got.UserID != "u" || got.AppID != "a" {
		t.Fatalf("consume: %+v %v", got, ok)
	}
	// 1회용
	if _, ok := s.Consume(tk); ok {
		t.Fatal("reuse must fail")
	}
	if _, ok := s.Consume(""); ok {
		t.Fatal("empty ticket must fail")
	}
	if _, ok := s.Consume("not-a-ticket"); ok {
		t.Fatal("unknown ticket must fail")
	}
	if i, cns, _ := s.Stats(); i != 1 || cns != 1 {
		t.Fatalf("stats: %d %d", i, cns)
	}
}

func TestExpiry(t *testing.T) {
	s, c := newStore(10)
	tk, _, _ := s.Issue(Info{Target: "db", Op: "console"})
	c.add(30 * time.Second) // 정확히 30초면 만료
	if _, ok := s.Consume(tk); ok {
		t.Fatal("expired ticket must fail")
	}
	if _, _, exp := s.Stats(); exp != 1 {
		t.Fatalf("expired count: %d", exp)
	}

	s.Issue(Info{})
	s.Issue(Info{})
	c.add(31 * time.Second)
	if n := s.Sweep(); n != 2 || s.Len() != 0 {
		t.Fatalf("sweep: %d left %d", n, s.Len())
	}
}

func TestLimit(t *testing.T) {
	s, c := newStore(2)
	s.Issue(Info{})
	s.Issue(Info{})
	if _, _, err := s.Issue(Info{}); err != ErrFull {
		t.Fatalf("want ErrFull, got %v", err)
	}
	// 만료된 것이 있으면 발급 때 먼저 치운다
	c.add(time.Minute)
	if _, _, err := s.Issue(Info{}); err != nil {
		t.Fatalf("after expiry: %v", err)
	}
}

// 원문은 저장소 어디에도 없다: 키는 해시, Info에는 티켓 필드가 없다
func TestRawNotStored(t *testing.T) {
	s, _ := newStore(10)
	tk, _, _ := s.Issue(Info{App: "web"})
	s.mu.Lock()
	defer s.mu.Unlock()
	for k, v := range s.items {
		if bytes.Contains(k[:], []byte(tk)) {
			t.Fatal("raw ticket used as key")
		}
		if strings.Contains(fmt.Sprintf("%+v", v), tk) {
			t.Fatal("raw ticket stored in info")
		}
	}
	if Hash(tk) == tk || len(Hash(tk)) != 64 {
		t.Fatal("hash must be sha256 hex")
	}
}

func TestRunStops(t *testing.T) {
	s, _ := newStore(10)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { s.Run(ctx, time.Millisecond); close(done) }()
	time.Sleep(5 * time.Millisecond)
	cancel()
	<-done
}
