package api

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

// 대상 Pod 조회 시한
const findTimeout = 10 * time.Second

// Public은 8080 핸들러다 (지시서 3-2). 티켓으로 shell·console만 연다.
func (s *Server) Public() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /exec/{ns}/v1/sessions", s.publicSession)
	return mux
}

func (s *Server) publicSession(w http.ResponseWriter, r *http.Request) {
	if !s.enter() {
		http.Error(w, "shutting down", http.StatusServiceUnavailable)
		return
	}
	defer s.leave()
	tk := ticketFrom(r)
	// Origin이 있으면 허용 목록만, 없으면(CLI) 통과. 업그레이드하지 않고 403 — 티켓은 쓰지 않은 채로 남는다
	if origin := r.Header.Get("Origin"); !s.originAllowed(origin) {
		http.Error(w, "origin not allowed", http.StatusForbidden)
		rec := audit.Record{Event: "session", Door: "public", NS: s.o.Namespace, Error: "origin not allowed: " + trim(origin, 100)}
		if tk != "" {
			rec.TicketSHA256 = ticket.Hash(tk)
		}
		now := audit.Time(s.o.Now())
		rec.StartedAt, rec.EndedAt = now, now
		s.o.Audit.Write(rec)
		return
	}
	// kodeploy.exec.v1이 협상되지 않으면(옛 클라이언트) 업그레이드하지 않는다
	if !offers(r, proto.Subprotocol) {
		http.Error(w, "subprotocol "+proto.Subprotocol+" required", http.StatusBadRequest)
		return
	}
	ws, err := s.publicUp.Upgrade(w, r, nil)
	if err != nil {
		return // Upgrade가 이미 응답했다
	}
	c := session.NewConn(ws)
	start := s.o.Now()
	rec := audit.Record{Event: "session", Door: "public", NS: s.o.Namespace}
	if tk != "" {
		rec.TicketSHA256 = ticket.Hash(tk)
	}
	reject := func(code int, reason string) {
		c.Reject(code, reason)
		rec.Error = reason
		s.record(rec, start, session.Result{CloseCode: code, ExitCode: -1})
	}

	if r.PathValue("ns") != s.o.Namespace {
		reject(proto.CloseForbidden, "namespace mismatch")
		return
	}
	info, ok := s.o.Tickets.Consume(tk)
	if !ok {
		reject(proto.CloseTicket, "ticket missing, expired or already used")
		return
	}
	rec.UserID, rec.AppID, rec.Target, rec.Op = info.UserID, info.AppID, info.Target, info.Op
	rec.IssuedAt = audit.Time(info.IssuedAt)

	if !s.acquire(s.publicSem) {
		reject(proto.CloseTooMany, "too many sessions")
		return
	}
	defer s.release(s.publicSem)

	fctx, cancel := context.WithTimeout(s.base, findTimeout)
	t, err := s.o.Finder.Find(fctx, info.Target, info.App)
	cancel()
	if err != nil {
		if errors.Is(err, target.ErrNotFound) {
			reject(proto.CloseNoTarget, "target pod not found")
		} else {
			s.o.Log.Error("find target", "err", err)
			reject(proto.CloseInternal, "target lookup failed")
		}
		return
	}
	cmd, err := publicCmd(info, t)
	if err != nil {
		reject(proto.CloseForbidden, "operation not allowed on this door")
		return
	}

	res := session.Serve(s.base, c, session.Options{
		TTY:          true,
		Stdin:        true,
		Cols:         info.Cols,
		Rows:         info.Rows,
		IdleTimeout:  s.o.TTYIdleTimeout,
		MaxDuration:  s.o.TTYMax,
		PingInterval: s.o.PingInterval,
		Shutdown:     s.shutdown,
	}, session.ExecJob(s.o.Runner, t, cmd))
	s.record(rec, start, res)
}

// publicCmd는 공개 문에서 허용하는 조합(app+shell, db+console, redis+console)만 명령으로 만든다.
func publicCmd(info ticket.Info, t target.Target) (ops.Cmd, error) {
	switch {
	case info.Target == "app" && info.Op == "shell":
		return ops.Shell(info.App), nil
	case info.Target == "db" && info.Op == "console":
		return ops.DBConsole(t.Kind)
	case info.Target == "redis" && info.Op == "console":
		return ops.RedisConsole(), nil
	}
	return ops.Cmd{}, errors.New("not allowed")
}
