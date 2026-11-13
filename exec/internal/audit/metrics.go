package audit

import (
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Metrics는 Prometheus 텍스트 형식으로 내보내는 지표다 (지시서 4-5).
// 의존성을 client-go·gorilla로 묶어 두려고 클라이언트 라이브러리 없이 직접 쓴다.
//
//	kodeploy_exec_sessions_total{op,result}          작업별 세션 수 (result: ok | fail)
//	kodeploy_exec_session_seconds_sum/_count{op}     작업별 실행 시간
//	kodeploy_exec_tickets_total{event}               티켓 issued | consumed | expired
//	kodeploy_exec_sessions_active{door}              지금 열린 세션 수 (door: public | internal, 상한도 문마다 따로다)
type Metrics struct {
	mu       sync.Mutex
	sessions map[[2]string]int64
	sum      map[string]float64
	count    map[string]int64
	active   func() (public, internal int)
	tickets  func() (issued, consumed, expired int64)
}

// NewMetrics는 지표를 만든다. active·tickets는 내보낼 때 읽는다.
func NewMetrics(active func() (public, internal int), tickets func() (int64, int64, int64)) *Metrics {
	return &Metrics{sessions: map[[2]string]int64{}, sum: map[string]float64{}, count: map[string]int64{}, active: active, tickets: tickets}
}

// Session은 세션 하나가 끝났을 때 부른다.
func (m *Metrics) Session(op string, ok bool, d time.Duration) {
	result := "fail"
	if ok {
		result = "ok"
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sessions[[2]string{op, result}]++
	m.sum[op] += d.Seconds()
	m.count[op]++
}

// ServeHTTP는 /metrics다.
func (m *Metrics) ServeHTTP(w http.ResponseWriter, _ *http.Request) {
	var b strings.Builder
	m.mu.Lock()
	b.WriteString("# TYPE kodeploy_exec_sessions_total counter\n")
	keys := make([][2]string, 0, len(m.sessions))
	for k := range m.sessions {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool { return keys[i][0]+keys[i][1] < keys[j][0]+keys[j][1] })
	for _, k := range keys {
		fmt.Fprintf(&b, "kodeploy_exec_sessions_total{op=%q,result=%q} %d\n", k[0], k[1], m.sessions[k])
	}
	b.WriteString("# TYPE kodeploy_exec_session_seconds summary\n")
	ops := make([]string, 0, len(m.count))
	for op := range m.count {
		ops = append(ops, op)
	}
	sort.Strings(ops)
	for _, op := range ops {
		fmt.Fprintf(&b, "kodeploy_exec_session_seconds_sum{op=%q} %g\n", op, m.sum[op])
		fmt.Fprintf(&b, "kodeploy_exec_session_seconds_count{op=%q} %d\n", op, m.count[op])
	}
	m.mu.Unlock()

	if m.tickets != nil {
		i, c, e := m.tickets()
		b.WriteString("# TYPE kodeploy_exec_tickets_total counter\n")
		fmt.Fprintf(&b, "kodeploy_exec_tickets_total{event=\"issued\"} %d\n", i)
		fmt.Fprintf(&b, "kodeploy_exec_tickets_total{event=\"consumed\"} %d\n", c)
		fmt.Fprintf(&b, "kodeploy_exec_tickets_total{event=\"expired\"} %d\n", e)
	}
	if m.active != nil {
		pub, in := m.active()
		b.WriteString("# TYPE kodeploy_exec_sessions_active gauge\n")
		fmt.Fprintf(&b, "kodeploy_exec_sessions_active{door=\"public\"} %d\n", pub)
		fmt.Fprintf(&b, "kodeploy_exec_sessions_active{door=\"internal\"} %d\n", in)
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = w.Write([]byte(b.String()))
}
