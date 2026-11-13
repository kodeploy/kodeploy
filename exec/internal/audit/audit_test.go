package audit

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// core 발급 기록(3-6)과 같은 이름이어야 두 기록을 이을 수 있다. 내부 세션처럼 비어 있어도 키는 남는다.
func TestFieldNames(t *testing.T) {
	var buf bytes.Buffer
	l := New(&buf)
	l.Write(Record{Event: "session", Door: "internal", NS: "tenant-0a1b2c3d", Op: "query", Target: "db", RequestID: "r1"})
	var m map[string]any
	if err := json.Unmarshal(buf.Bytes(), &m); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"ticket_sha256", "user_id", "app_id", "target", "op", "issued_at", "ns", "request_id", "exit_code", "close_code", "bytes_in", "bytes_out"} {
		if _, ok := m[k]; !ok {
			t.Errorf("missing field %q in %s", k, buf.String())
		}
	}
	if m["user_id"] != "" || m["app_id"] != "" || m["ticket_sha256"] != "" {
		t.Errorf("internal session must leave ticket fields empty: %s", buf.String())
	}
	if strings.Count(buf.String(), "\n") != 1 {
		t.Errorf("one line per record: %q", buf.String())
	}
}

func TestTime(t *testing.T) {
	if Time(time.Time{}) != "" {
		t.Fatal("zero time must be empty")
	}
	if got := Time(time.Unix(0, 0)); got != "1970-01-01T00:00:00Z" {
		t.Fatal(got)
	}
}

func TestMetrics(t *testing.T) {
	m := NewMetrics(func() (int, int) { return 2, 1 }, func() (int64, int64, int64) { return 5, 3, 1 })
	m.Session("query", true, 500*time.Millisecond)
	m.Session("query", false, time.Second)
	m.Session("shell", true, time.Minute)
	rec := httptest.NewRecorder()
	m.ServeHTTP(rec, nil)
	body := rec.Body.String()
	for _, want := range []string{
		`kodeploy_exec_sessions_total{op="query",result="fail"} 1`,
		`kodeploy_exec_sessions_total{op="query",result="ok"} 1`,
		`kodeploy_exec_session_seconds_sum{op="query"} 1.5`,
		`kodeploy_exec_session_seconds_count{op="shell"} 1`,
		`kodeploy_exec_tickets_total{event="issued"} 5`,
		`kodeploy_exec_tickets_total{event="expired"} 1`,
		`kodeploy_exec_sessions_active{door="public"} 2`,
		`kodeploy_exec_sessions_active{door="internal"} 1`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in\n%s", want, body)
		}
	}
	// 라벨 없는 합계 줄은 없다 (sum by로 더한다)
	if strings.Contains(body, "\nkodeploy_exec_sessions_active ") {
		t.Errorf("unlabeled active line in\n%s", body)
	}
}
