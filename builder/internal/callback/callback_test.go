package callback

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"go.uber.org/goleak"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

var secret = []byte("cb-secret")

// mockCore는 서명을 검증하고 받은 이벤트를 기록한다. fail은 앞으로 몇 번 5xx를 줄지, gate가 있으면 응답 전에 기다린다.
type mockCore struct {
	mu       sync.Mutex
	events   []contract.Event
	attempts map[int64]int
	fail     int
	status   int
	gate     chan struct{}
	badSig   int
}

func (m *mockCore) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	if err := sign.VerifyRequest(secret, r, body, time.Now()); err != nil {
		m.mu.Lock()
		m.badSig++
		m.mu.Unlock()
		w.WriteHeader(401)
		return
	}
	if m.gate != nil {
		<-m.gate
	}
	var ev contract.Event
	_ = json.Unmarshal(body, &ev)
	m.mu.Lock()
	defer m.mu.Unlock()
	m.attempts[ev.Seq]++
	if m.fail > 0 {
		m.fail--
		w.WriteHeader(m.status)
		return
	}
	if !strings.HasSuffix(r.URL.Path, "/internal/builds/3f9a2c1d/events") {
		w.WriteHeader(404)
		return
	}
	m.events = append(m.events, ev)
	w.WriteHeader(200)
}

func (m *mockCore) snapshot() []contract.Event {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]contract.Event(nil), m.events...)
}

func newSender(t *testing.T, core *mockCore) (*Sender, func()) {
	t.Helper()
	core.attempts = map[int64]int{}
	srv := httptest.NewServer(core)
	s := NewSender(srv.URL, secret, nil)
	s.backoffMin, s.backoffMax = time.Millisecond, 4*time.Millisecond
	return s, func() {
		s.hc.CloseIdleConnections()
		srv.Close()
	}
}

func waitDone(t *testing.T, q *Queue) {
	t.Helper()
	select {
	case <-q.Done():
	case <-time.After(5 * time.Second):
		t.Fatal("queue did not drain")
	}
}

func TestOrderAndSeq(t *testing.T) {
	defer goleak.VerifyNone(t)
	core := &mockCore{}
	s, stop := newSender(t, core)
	defer stop()

	var acks []Ack
	q := s.Start(context.Background(), "3f9a2c1d", StartOptions{OnAck: func(a Ack) { acks = append(acks, a) }})
	q.Log([]string{"a", "b"})
	q.Event(contract.Event{Type: contract.EventCommitted, CommitSHA: "c1"})
	q.Log([]string{"c"})
	q.Event(contract.Event{Type: contract.EventFinished})
	q.Close()
	waitDone(t, q)

	got := core.snapshot()
	types := []string{}
	for i, ev := range got {
		if ev.Seq != int64(i+1) {
			t.Fatalf("seq %d at %d", ev.Seq, i)
		}
		types = append(types, ev.Type)
	}
	if strings.Join(types, ",") != "log,committed,log,finished" {
		t.Fatalf("order %v", types)
	}
	if len(acks) != 4 || acks[3].Seq != 4 || acks[3].LogLines != 3 || acks[1].LogLines != 2 {
		t.Fatalf("acks %+v", acks)
	}
	if core.badSig != 0 {
		t.Fatal("signature rejected")
	}
}

// 5xx면 같은 seq로 다시 보낸다.
func TestRetryKeepsSeq(t *testing.T) {
	defer goleak.VerifyNone(t)
	core := &mockCore{fail: 3, status: 503}
	s, stop := newSender(t, core)
	defer stop()

	q := s.Start(context.Background(), "3f9a2c1d", StartOptions{FirstSeq: 42})
	q.Event(contract.Event{Type: contract.EventDeployed})
	q.Close()
	waitDone(t, q)

	got := core.snapshot()
	if len(got) != 1 || got[0].Seq != 42 || core.attempts[42] != 4 {
		t.Fatalf("events %+v attempts %v", got, core.attempts)
	}
}

// core가 막혀 있는 동안 로그가 5000줄을 넘으면 오래된 줄을 버리고 생략 표시를 남긴다. 다른 이벤트는 남는다.
func TestDropsOldestLogLines(t *testing.T) {
	defer goleak.VerifyNone(t)
	core := &mockCore{gate: make(chan struct{})}
	s, stop := newSender(t, core)
	defer stop()

	q := s.Start(context.Background(), "3f9a2c1d", StartOptions{})
	q.Log([]string{"first"}) // 이 항목은 보내는 중이라 버리지 않는다
	time.Sleep(20 * time.Millisecond)

	q.Event(contract.Event{Type: contract.EventCommitted})
	var lines []string
	for i := 0; i < 6000; i++ {
		lines = append(lines, "l")
	}
	q.Log(lines[:3000])
	q.Event(contract.Event{Type: contract.EventDeployed})
	q.Log(lines[3000:])
	q.Close()
	close(core.gate)
	waitDone(t, q)

	got := core.snapshot()
	total, omitted := 0, 0
	var types []string
	for _, ev := range got {
		if ev.Type == contract.EventLog {
			if len(ev.Lines) > MaxLinesPerEvent+1 {
				t.Fatalf("event with %d lines", len(ev.Lines))
			}
			for _, l := range ev.Lines {
				var n int
				if _, err := fmt.Sscanf(l, "... %d줄 생략 ...", &n); err == nil {
					omitted += n
					continue
				}
				total++
			}
		} else {
			types = append(types, ev.Type)
		}
	}
	if strings.Join(types, ",") != "committed,deployed" {
		t.Fatalf("non-log events lost or reordered: %v", types)
	}
	// 보내는 중인 줄까지 합쳐 큐 상한 5000줄만 남고, 나머지는 생략으로 집계된다 (합계 6001줄)
	if total != MaxQueuedLines || total+omitted != 6001 {
		t.Fatalf("delivered %d lines, omitted %d", total, omitted)
	}
}

func TestStopsOnContext(t *testing.T) {
	defer goleak.VerifyNone(t)
	core := &mockCore{fail: 1 << 30, status: 500}
	s, stop := newSender(t, core)
	defer stop()
	ctx, cancel := context.WithCancel(context.Background())
	q := s.Start(ctx, "3f9a2c1d", StartOptions{})
	q.Event(contract.Event{Type: contract.EventFailed})
	time.Sleep(20 * time.Millisecond)
	cancel()
	waitDone(t, q)
}

func TestLogOffsetContinuesCount(t *testing.T) {
	defer goleak.VerifyNone(t)
	core := &mockCore{}
	s, stop := newSender(t, core)
	defer stop()
	var last Ack
	q := s.Start(context.Background(), "3f9a2c1d", StartOptions{FirstSeq: 150, LogOffset: 700, OnAck: func(a Ack) { last = a }})
	q.Log([]string{"x", "y"})
	q.Close()
	waitDone(t, q)
	if last.Seq != 150 || last.LogLines != 702 {
		t.Fatalf("got %+v", last)
	}
}
