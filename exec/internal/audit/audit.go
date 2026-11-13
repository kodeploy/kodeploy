// Package audit은 세션마다 JSON 한 줄 감사 기록과 지표(9090)를 남긴다.
//
// 남기지 않는 것: SQL 본문, 터미널 입출력, 티켓 원문, HMAC 키. SQL은 길이와 sha256만 남긴다.
// ticket_sha256·user_id·app_id·target·op·issued_at은 core 발급 기록(core app/exec/router.py)과 같은 이름이다.
// 내부 세션은 티켓이 없어 그 넷이 비어 있고, 앱은 ns로(ns 하나 = 앱 하나), core 로그와는 request_id로 잇는다.
//
// TODO: 영구 보관은 아직 없다. 지금은 컨테이너 stdout(로그 보존 기간)까지만 남는다.
package audit

import (
	"encoding/json"
	"io"
	"sync"
	"time"
)

// Record는 감사 기록 한 줄이다. 필드를 빼지 않도록 omitempty를 쓰지 않는다(빈 값도 남긴다).
type Record struct {
	Event        string `json:"event"` // session | ticket
	Door         string `json:"door"`  // public | internal
	NS           string `json:"ns"`
	TicketSHA256 string `json:"ticket_sha256"`
	UserID       string `json:"user_id"`
	AppID        string `json:"app_id"`
	Target       string `json:"target"`
	Op           string `json:"op"`
	IssuedAt     string `json:"issued_at"`
	RequestID    string `json:"request_id"`
	StartedAt    string `json:"started_at"`
	EndedAt      string `json:"ended_at"`
	DurationMS   int64  `json:"duration_ms"`
	BytesIn      int64  `json:"bytes_in"`
	BytesOut     int64  `json:"bytes_out"`
	ExitCode     *int   `json:"exit_code"`
	CloseCode    int    `json:"close_code"`
	SQLBytes     int64  `json:"sql_bytes,omitempty"`
	SQLSHA256    string `json:"sql_sha256,omitempty"`
	Error        string `json:"error,omitempty"` // 원인 요약. 비밀값·입력 내용은 넣지 않는다
}

// Logger는 Record를 한 줄씩 쓴다.
type Logger struct {
	mu  sync.Mutex
	enc *json.Encoder
	now func() time.Time
}

// New는 w(운영은 stdout)에 쓰는 Logger를 만든다.
func New(w io.Writer) *Logger {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return &Logger{enc: enc, now: time.Now}
}

// Time은 기록용 시각 문자열이다. 0이면 빈 문자열.
func Time(t time.Time) string {
	if t.IsZero() {
		return ""
	}
	return t.UTC().Format(time.RFC3339Nano)
}

// Write는 한 줄을 남긴다.
func (l *Logger) Write(r Record) {
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = l.enc.Encode(r)
}
