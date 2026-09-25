// Package api는 core가 부르는 빌더 HTTP 서버다 (지시서 3-1).
// 서명 검증 → 본문 파싱 → 내용 검증 → 디스패처 순서. 본문은 서명이 맞을 때만 파싱한다.
package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

// 요청 본문 상한. values + build 칸이면 몇 KB라 넉넉하다.
const maxBodyBytes = 1 << 20

// Dispatcher는 요청을 실제로 돌리는 쪽(run.Manager)이다.
// Submit은 받아들이면 nil, 아니면 contract.ConflictError / BusyError를 돌려준다.
type Dispatcher interface {
	Submit(req *contract.DeployRequest) error
	Cancel(buildID string) error // 없으면 contract.ErrNotFound
}

// Server는 core 요청을 받는 HTTP 서버다. 서명 키·검증기·디스패처를 들고 있다.
type Server struct {
	secret    []byte
	validator Validator
	disp      Dispatcher
	log       *slog.Logger
	now       func() time.Time
}

// Options는 New에 넘기는 설정이다.
type Options struct {
	Secret     []byte
	Validator  Validator
	Dispatcher Dispatcher
	Logger     *slog.Logger
	Now        func() time.Time // 테스트용. nil이면 time.Now
}

// New는 Server를 만든다. Now·Logger가 비면 time.Now·버리는 로거를 쓴다.
func New(o Options) *Server {
	s := &Server{secret: o.Secret, validator: o.Validator, disp: o.Dispatcher, log: o.Logger, now: o.Now}
	if s.now == nil {
		s.now = time.Now
	}
	if s.log == nil {
		s.log = slog.New(slog.DiscardHandler)
	}
	return s
}

// Handler는 라우팅을 붙인 http.Handler를 돌려준다.
//
//	GET /healthz, POST /internal/deploys, DELETE /internal/deploys/{build_id}
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("POST /internal/deploys", s.handleSubmit)
	mux.HandleFunc("DELETE /internal/deploys/{build_id}", s.handleCancel)
	return mux
}

// readVerified는 본문을 읽고 서명을 검증한다. 실패하면 응답까지 쓰고 false.
func (s *Server) readVerified(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBodyBytes))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "request body too large")
		return nil, false
	}
	if err := sign.VerifyRequest(s.secret, r, body, s.now()); err != nil {
		s.log.Warn("signature rejected", "path", r.URL.Path, "reason", err.Error())
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return nil, false
	}
	return body, true
}

// handleSubmit은 POST /internal/deploys다. 서명 → JSON 파싱 → 검증 → Submit 순서이고,
// 디스패처 오류를 409(충돌)·429(상한)·400(검증)·500(그 밖)으로 바꾼다.
func (s *Server) handleSubmit(w http.ResponseWriter, r *http.Request) {
	body, ok := s.readVerified(w, r)
	if !ok {
		return
	}
	req, err := decodeRequest(body)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := s.validator.Validate(req); err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}

	err = s.disp.Submit(req)
	var conflict *contract.ConflictError
	var busy *contract.BusyError
	switch {
	case err == nil:
		s.log.Info("accepted", "build_id", req.BuildID, "kind", req.Kind, "namespace", req.Namespace, "slot", req.Slot)
		writeJSON(w, http.StatusAccepted, map[string]string{"build_id": req.BuildID})
	case errors.As(err, &conflict):
		writeJSON(w, http.StatusConflict, map[string]string{"error": conflict.Reason, "build_id": conflict.BuildID})
	case errors.As(err, &busy):
		secs := int(math.Ceil(busy.RetryAfter.Seconds()))
		if secs < 1 {
			secs = 1
		}
		w.Header().Set("Retry-After", strconv.Itoa(secs))
		writeError(w, http.StatusTooManyRequests, busy.Error())
	case IsValidation(err):
		writeError(w, http.StatusBadRequest, err.Error())
	default:
		s.log.Error("submit failed", "build_id", req.BuildID, "err", err)
		writeError(w, http.StatusInternalServerError, "internal error")
	}
}

// handleCancel은 DELETE /internal/deploys/{build_id}다. 진행 중이면 취소하고 202, 없으면 404.
func (s *Server) handleCancel(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.readVerified(w, r); !ok {
		return
	}
	id := r.PathValue("build_id")
	if !ValidBuildID(id) {
		writeError(w, http.StatusBadRequest, "invalid build_id")
		return
	}
	switch err := s.disp.Cancel(id); {
	case err == nil:
		s.log.Info("cancel requested", "build_id", id)
		writeJSON(w, http.StatusAccepted, map[string]string{"build_id": id})
	case errors.Is(err, contract.ErrNotFound):
		writeError(w, http.StatusNotFound, "build not found")
	default:
		s.log.Error("cancel failed", "build_id", id, "err", err)
		writeError(w, http.StatusInternalServerError, "internal error")
	}
}

// decodeRequest는 모르는 키와 뒤따르는 잔여 데이터를 거부하며 읽는다.
func decodeRequest(body []byte) (*contract.DeployRequest, error) {
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.DisallowUnknownFields()
	var req contract.DeployRequest
	if err := dec.Decode(&req); err != nil {
		var fe *contract.ForbiddenKeyError
		if errors.As(err, &fe) {
			return nil, fe
		}
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}
	var extra json.RawMessage
	if err := dec.Decode(&extra); err != io.EOF {
		return nil, errors.New("invalid JSON: trailing data")
	}
	return &req, nil
}

// writeJSON은 상태 코드와 JSON 본문을 쓴다.
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

// writeError는 {"error": msg} 본문을 쓴다.
func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
