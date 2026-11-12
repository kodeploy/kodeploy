package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"hash"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"

	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/restore"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/sign"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

const maxTicketBody = 8 << 10

var (
	sizePattern   = regexp.MustCompile(`^[1-9][0-9]{0,15}$`)
	sha256Pattern = regexp.MustCompile(`^[0-9a-fA-F]{64}$`)
	// 공개 문 허용 조합 (지시서 3-1)
	allowed = map[string]string{"app": "shell", "db": "console", "redis": "console"}
)

// Internal은 8081 핸들러다 (지시서 3-1). 전부 HMAC 서명이 필요하다.
func (s *Server) Internal() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /internal/v1/tickets", s.issueTicket)
	mux.HandleFunc("GET /internal/v1/sessions/{op}", s.internalSession)
	mux.HandleFunc("GET /internal/v1/sessions/restore/{size}/{sha256}", s.restoreSession)
	return mux
}

// verify는 builder와 같은 규칙으로 서명을 본다. path는 쿼리를 뺀 원문이다.
func (s *Server) verify(w http.ResponseWriter, r *http.Request, body []byte) bool {
	if err := sign.VerifyRequest(s.o.Key, r, body, s.o.Now()); err != nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	return true
}

type ticketRequest struct {
	UserID string `json:"user_id"`
	AppID  string `json:"app_id"`
	App    string `json:"app"`
	Target string `json:"target"`
	Op     string `json:"op"`
	Cols   uint16 `json:"cols"`
	Rows   uint16 `json:"rows"`
}

type ticketResponse struct {
	Ticket    string `json:"ticket"`
	ExpiresAt string `json:"expires_at"`
}

func (s *Server) issueTicket(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxTicketBody))
	if err != nil {
		http.Error(w, "body too large", http.StatusRequestEntityTooLarge)
		return
	}
	if !s.verify(w, r, body) {
		return
	}
	if s.isDraining() {
		http.Error(w, "shutting down", http.StatusServiceUnavailable)
		return
	}
	var req ticketRequest
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		http.Error(w, "bad request body", http.StatusBadRequest)
		return
	}
	if op, ok := allowed[req.Target]; !ok || op != req.Op {
		http.Error(w, "target/op combination not allowed", http.StatusBadRequest)
		return
	}
	if !target.ValidAppName(req.App) || len(req.UserID) > 64 || len(req.AppID) > 64 {
		http.Error(w, "bad app, user_id or app_id", http.StatusBadRequest)
		return
	}
	if req.Cols == 0 || req.Rows == 0 {
		req.Cols, req.Rows = 80, 24
	}
	if req.Cols > 1000 || req.Rows > 1000 {
		http.Error(w, "bad cols/rows", http.StatusBadRequest)
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), findTimeout)
	defer cancel()
	if _, err := s.o.Finder.Find(ctx, req.Target, req.App); err != nil {
		if errors.Is(err, target.ErrNotFound) {
			http.Error(w, "target pod not found", http.StatusNotFound)
			return
		}
		s.o.Log.Error("find target", "err", err)
		http.Error(w, "target lookup failed", http.StatusInternalServerError)
		return
	}

	// user_id·app_id는 기록용이다. 판단(대상 선택·권한)에 쓰지 않는다.
	tk, info, err := s.o.Tickets.Issue(ticket.Info{
		UserID: req.UserID, AppID: req.AppID, App: req.App,
		Target: req.Target, Op: req.Op, Cols: req.Cols, Rows: req.Rows,
	})
	if errors.Is(err, ticket.ErrFull) {
		http.Error(w, "too many pending tickets", http.StatusTooManyRequests)
		return
	}
	if err != nil {
		s.o.Log.Error("issue ticket", "err", err)
		http.Error(w, "ticket issue failed", http.StatusInternalServerError)
		return
	}
	s.o.Audit.Write(audit.Record{
		Event: "ticket", Door: "internal", NS: s.o.Namespace,
		TicketSHA256: ticket.Hash(tk), UserID: info.UserID, AppID: info.AppID,
		Target: info.Target, Op: info.Op, IssuedAt: audit.Time(info.IssuedAt),
		RequestID: requestID(r), CloseCode: 0,
	})
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(ticketResponse{Ticket: tk, ExpiresAt: audit.Time(info.ExpiresAt)})
}

func (s *Server) internalSession(w http.ResponseWriter, r *http.Request) {
	op := r.PathValue("op")
	if op != "query" && op != "browse" && op != "dump" {
		http.NotFound(w, r)
		return
	}
	if !s.verify(w, r, nil) {
		return
	}
	var limit int64
	switch op {
	case "query":
		limit = ops.QueryMaxBytes
	case "browse":
		limit = ops.BrowseMaxBytes
	}
	s.serveInternal(w, r, op, func(t target.Target) (session.Job, session.Options, error) {
		var cmd ops.Cmd
		var err error
		switch op {
		case "query":
			cmd, err = ops.Query(t.Kind)
		case "browse":
			cmd, err = ops.Browse(t.Kind)
		default:
			cmd, err = ops.Dump(t.Kind)
		}
		return session.ExecJob(s.o.Runner, t, cmd), session.Options{Stdin: cmd.Stdin, MaxStdin: limit}, err
	})
}

func (s *Server) restoreSession(w http.ResponseWriter, r *http.Request) {
	if !s.verify(w, r, nil) {
		return
	}
	sizeStr, sum := r.PathValue("size"), r.PathValue("sha256")
	if !sizePattern.MatchString(sizeStr) || !sha256Pattern.MatchString(sum) {
		http.Error(w, "bad size or sha256", http.StatusBadRequest)
		return
	}
	size, err := strconv.ParseInt(sizeStr, 10, 64)
	if err != nil || size > s.o.RestoreMaxBytes {
		http.Error(w, "size exceeds limit", http.StatusBadRequest)
		return
	}
	p := restore.Params{Workdir: s.o.RestoreWorkdir, Size: size, SHA256: strings.ToLower(sum)}
	s.serveInternal(w, r, "restore", func(t target.Target) (session.Job, session.Options, error) {
		p.Kind = t.Kind
		// 세션 수준 상한은 전역 상한(받는 쪽 방어). 기대 크기 초과는 restore가 upload 실패로 돌려준다
		return restoreJob(s.o.Runner, t, p), session.Options{Stdin: true, MaxStdin: s.o.RestoreMaxBytes}, nil
	})
}

// serveInternal은 내부 세션 공통 흐름이다: 업그레이드 → 동시 세션 → DB Pod 찾기 → 세션 → 기록.
func (s *Server) serveInternal(w http.ResponseWriter, r *http.Request, op string,
	build func(target.Target) (session.Job, session.Options, error)) {
	if !s.enter() {
		http.Error(w, "shutting down", http.StatusServiceUnavailable)
		return
	}
	defer s.leave()
	if !offers(r, proto.Subprotocol) {
		http.Error(w, "subprotocol "+proto.Subprotocol+" required", http.StatusBadRequest)
		return
	}
	ws, err := s.upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	c := session.NewConn(ws)
	start := s.o.Now()
	// 내부 세션은 티켓이 없다: user_id·app_id는 비워 두고, 앱은 ns로, core 로그와는 request_id로 잇는다
	rec := audit.Record{Event: "session", Door: "internal", NS: s.o.Namespace, Target: "db", Op: op, RequestID: requestID(r)}
	reject := func(code int, reason string) {
		c.Reject(code, reason)
		rec.Error = reason
		s.record(rec, start, session.Result{CloseCode: code, ExitCode: -1})
	}
	if !s.acquire() {
		reject(proto.CloseTooMany, "too many sessions")
		return
	}
	defer s.release()

	fctx, cancel := context.WithTimeout(s.base, findTimeout)
	t, err := s.o.Finder.Find(fctx, "db", "")
	cancel()
	if err != nil {
		if errors.Is(err, target.ErrNotFound) {
			reject(proto.CloseNoTarget, "db pod not found")
		} else {
			s.o.Log.Error("find target", "err", err)
			reject(proto.CloseInternal, "target lookup failed")
		}
		return
	}
	job, so, err := build(t)
	if err != nil {
		reject(proto.CloseForbidden, "operation not supported for this db")
		return
	}
	so.MaxDuration = s.o.NonTTYMax
	so.PingInterval = s.o.PingInterval
	var sqlHash hash.Hash
	var sqlBytes countWriter
	if op == "query" || op == "browse" {
		sqlHash = sha256.New()
		so.StdinTap = io.MultiWriter(sqlHash, &sqlBytes)
	}
	res := session.Serve(s.base, c, so, job)
	if sqlHash != nil {
		rec.SQLBytes, rec.SQLSHA256 = int64(sqlBytes), hex.EncodeToString(sqlHash.Sum(nil))
	}
	s.record(rec, start, res)
}

type countWriter int64

func (c *countWriter) Write(p []byte) (int, error) { *c += countWriter(len(p)); return len(p), nil }

// restoreJob은 복원 상태기계를 세션 작업으로 감싼다. 결과(실패 포함)는 채널 3 JSON이다 (지시서 3-5).
func restoreJob(r session.Runner, t target.Target, p restore.Params) session.Job {
	return func(ctx context.Context, jio session.JobIO) (session.Outcome, error) {
		ex := func(ctx context.Context, cmd ops.Cmd, stdin io.Reader, stdout, stderr io.Writer) (int, error) {
			e := session.Exec{Pod: t.Pod, Container: t.Container, Command: cmd.Argv, Stdout: stdout, Stderr: stderr}
			if cmd.Stdin {
				e.Stdin = stdin
			}
			err := r.Run(ctx, e)
			if err == nil {
				return 0, nil
			}
			if code, ok := session.ExitCode(err); ok {
				return code, nil
			}
			return -1, err
		}
		res := restore.Run(ctx, ex, jio.Stdin, p)
		b, _ := json.Marshal(res)
		code := 0
		if res.Status != "Success" {
			code = 1
		}
		return session.Outcome{Status: b, ExitCode: code}, nil
	}
}
