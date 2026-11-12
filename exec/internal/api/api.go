// Package api는 kodeploy-exec의 세 리스너다 (지시서 2, 3절).
//
//	공개 문 8080  GET  /exec/{ns}/v1/sessions                       웹·CLI. 티켓으로 shell·console만
//	내부 문 8081  POST /internal/v1/tickets                         core. HMAC 서명
//	              GET  /internal/v1/sessions/{query|dump|browse}    core. HMAC 서명
//	              GET  /internal/v1/sessions/restore/{size}/{sha256}
//	지표   9090  GET  /metrics, /healthz
//
// 서명은 builder 규칙 그대로라 쿼리 문자열은 서명 밖이다. 그래서 내부 문은 쿼리를 읽지 않는다.
// 서명 밖 값(X-Kodeploy-Request-Id)과 기록용 값(user_id·app_id)은 기록에만 쓰고 실행·권한 판단에는 쓰지 않는다.
package api

import (
	"context"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"

	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

// HeaderRequestID는 core가 넘기는 요청 id다. 서명 밖 값이라 기록에만 쓴다.
const HeaderRequestID = "X-Kodeploy-Request-Id"

var requestIDPattern = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,64}$`)

// Finder는 대상 Pod를 고른다 (target.Finder).
type Finder interface {
	Find(ctx context.Context, target, app string) (target.Target, error)
}

// Options는 서버 조립 재료다.
type Options struct {
	Namespace string
	Key       []byte
	Tickets   *ticket.Store
	Finder    Finder
	Runner    session.Runner
	Audit     *audit.Logger
	Metrics   *audit.Metrics // nil 가능

	TTYIdleTimeout  time.Duration
	TTYMax          time.Duration
	NonTTYMax       time.Duration
	PingInterval    time.Duration
	MaxSessions     int
	RestoreMaxBytes int64
	RestoreWorkdir  string

	Now func() time.Time
	Log *slog.Logger
}

// Server는 세 리스너의 핸들러와 세션 수명을 가진다.
type Server struct {
	o        Options
	sem      chan struct{}
	upgrader websocket.Upgrader

	mu       sync.Mutex
	idle     *sync.Cond
	draining bool
	inFlight int // 업그레이드 뒤 세션까지 포함한 진행 중 요청

	shutdown   chan struct{}
	stopOnce   sync.Once
	base       context.Context
	cancelBase context.CancelFunc
}

// New는 서버를 만든다.
func New(o Options) *Server {
	if o.Now == nil {
		o.Now = time.Now
	}
	if o.Log == nil {
		o.Log = slog.Default()
	}
	s := &Server{
		o:        o,
		sem:      make(chan struct{}, o.MaxSessions),
		shutdown: make(chan struct{}),
		upgrader: websocket.Upgrader{
			Subprotocols:    []string{proto.Subprotocol},
			ReadBufferSize:  32 << 10,
			WriteBufferSize: 32 << 10,
			// 인증은 티켓(공개 문)·HMAC(내부 문)이다. 쿠키 같은 자동 자격증명이 없으니(라우트가 Cookie를 지운다)
			// Origin으로 막을 이유가 없고, CLI는 Origin을 보내지 않는다.
			CheckOrigin: func(*http.Request) bool { return true },
		},
	}
	s.idle = sync.NewCond(&s.mu)
	s.base, s.cancelBase = context.WithCancel(context.Background())
	return s
}

// Active는 지금 열린 세션 수다(동시 세션 제한에 들어간 것).
func (s *Server) Active() int { return len(s.sem) }

// enter는 요청 하나를 시작한다. 종료 중이면 false.
func (s *Server) enter() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.draining {
		return false
	}
	s.inFlight++
	return true
}

func (s *Server) leave() {
	s.mu.Lock()
	s.inFlight--
	if s.inFlight == 0 {
		s.idle.Broadcast()
	}
	s.mu.Unlock()
}

// BeginShutdown은 SIGTERM 처리다: 새 연결을 받지 않고(503, /healthz도 503), tty 세션은 4503으로 닫는다.
// 진행 중인 비대화형 세션(특히 restore)은 끝까지 간다 (지시서 4-7).
func (s *Server) BeginShutdown() {
	s.mu.Lock()
	s.draining = true
	s.mu.Unlock()
	s.stopOnce.Do(func() { close(s.shutdown) })
}

// Wait는 진행 중인 세션이 모두 끝날 때까지 기다린다. ctx가 먼저 끝나면 남은 세션을 끊고 기다린다.
func (s *Server) Wait(ctx context.Context) error {
	done := make(chan struct{})
	go func() {
		s.mu.Lock()
		for s.inFlight > 0 {
			s.idle.Wait()
		}
		s.mu.Unlock()
		close(done)
	}()
	select {
	case <-done:
		return nil
	case <-ctx.Done():
		s.cancelBase()
		<-done
		return ctx.Err()
	}
}

// Close는 남은 자원을 정리한다(테스트·종료용).
func (s *Server) Close() { s.cancelBase() }

func (s *Server) acquire() bool {
	select {
	case s.sem <- struct{}{}:
		return true
	default:
		return false
	}
}

func (s *Server) release() { <-s.sem }

func (s *Server) isDraining() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.draining
}

// Ops는 9090 핸들러다.
func (s *Server) Ops() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if s.isDraining() {
			http.Error(w, "shutting down", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ok"))
	})
	if s.o.Metrics != nil {
		mux.Handle("GET /metrics", s.o.Metrics)
	}
	return mux
}

// offers는 클라이언트가 서브프로토콜 p를 제안했는지 본다.
func offers(r *http.Request, p string) bool {
	for _, v := range websocket.Subprotocols(r) {
		if v == p {
			return true
		}
	}
	return false
}

func requestID(r *http.Request) string {
	if v := r.Header.Get(HeaderRequestID); requestIDPattern.MatchString(v) {
		return v
	}
	return ""
}

// ticketFrom은 공개 문 티켓을 꺼낸다: Sec-WebSocket-Protocol의 ticket.<티켓> 또는 Authorization: Ticket <티켓>.
// URL(쿼리)은 보지 않는다.
func ticketFrom(r *http.Request) string {
	for _, v := range websocket.Subprotocols(r) {
		if t, ok := strings.CutPrefix(v, proto.TicketPrefix); ok && t != "" {
			return t
		}
	}
	if scheme, t, ok := strings.Cut(r.Header.Get("Authorization"), " "); ok && scheme == proto.TicketAuthType {
		return strings.TrimSpace(t)
	}
	return ""
}

func (s *Server) record(rec audit.Record, start time.Time, res session.Result) {
	end := s.o.Now()
	rec.StartedAt, rec.EndedAt = audit.Time(start), audit.Time(end)
	rec.DurationMS = end.Sub(start).Milliseconds()
	rec.BytesIn, rec.BytesOut = res.BytesIn, res.BytesOut
	rec.CloseCode = res.CloseCode
	if res.ExitCode >= 0 && res.CloseCode != 0 && res.Err == nil {
		code := res.ExitCode
		rec.ExitCode = &code
	}
	if res.Err != nil && rec.Error == "" {
		rec.Error = trim(res.Err.Error(), 200)
	}
	s.o.Audit.Write(rec)
	if s.o.Metrics != nil && rec.Op != "" {
		s.o.Metrics.Session(rec.Op, res.CloseCode == proto.CloseNormal && rec.ExitCode != nil && *rec.ExitCode == 0, end.Sub(start))
	}
}

func trim(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
