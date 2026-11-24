package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

// TokenSource는 Authorization: Bearer에 넣을 토큰을 준다 (internal/auth).
// Refresh는 401을 받았을 때 부른다 — 새 토큰을 받을 수 없으면 그 이유를 오류로 돌려준다.
type TokenSource interface {
	Token(ctx context.Context) (string, error)
	Refresh(ctx context.Context) error
}

// Error는 서버가 2xx가 아닌 상태로 답한 것이다. Code는 오류 본문의 기계용 code(없으면 ""), Detail은 사람용 이유.
type Error struct {
	Status int
	Code   string
	Detail string
}

func (e *Error) Error() string {
	if e.Detail != "" {
		return fmt.Sprintf("HTTP %d: %s", e.Status, e.Detail)
	}
	return fmt.Sprintf("HTTP %d", e.Status)
}

// Temporary는 같은 요청을 다시 보내 볼 만한 오류인가 (5xx).
func (e *Error) Temporary() bool { return e.Status >= 500 }

// NetError는 응답을 받지 못한 것이다 (연결 실패, 요청 시간 초과). 메시지에 주소를 넣지 않는다 —
// GitHub ID 토큰 요청 주소처럼 쿼리에 실행 정보가 든 주소가 출력으로 새지 않게.
type NetError struct {
	Op  string
	Err error
}

func (e *NetError) Error() string   { return e.Op + ": " + e.Err.Error() }
func (e *NetError) Unwrap() error   { return e.Err }
func (e *NetError) Temporary() bool { return true }

// Retryable은 err가 다시 시도할 만한 오류(5xx, 네트워크, 그런 오류로 실패한 토큰 교환)인가.
func Retryable(err error) bool {
	var t interface{ Temporary() bool }
	return errors.As(err, &t) && t.Temporary()
}

// AsError는 err가 서버 응답 오류(*Error) 그 자체면 그것을 준다. 다른 오류에 싸인 것은 보지 않는다
// (토큰 교환이 거절된 것을 재배포 응답으로 착각하지 않게).
func AsError(err error) (*Error, bool) {
	e, ok := err.(*Error)
	return e, ok
}

// NetErr는 요청 오류를 NetError로 바꾼다. url.Error는 주소를 빼고 안쪽 오류만 남긴다.
func NetErr(op string, err error) error {
	var ue *url.Error
	if errors.As(err, &ue) {
		err = ue.Err
	}
	return &NetError{Op: op, Err: err}
}

// ParseError는 2xx가 아닌 응답 본문을 Error로 읽는다. detail이 문자열이 아니면(422의 목록 등) JSON 그대로 짧게 둔다.
func ParseError(status int, body []byte) *Error {
	e := &Error{Status: status}
	var parsed struct {
		Detail json.RawMessage `json:"detail"`
		Code   string          `json:"code"`
	}
	if json.Unmarshal(body, &parsed) == nil {
		e.Code = parsed.Code
		var s string
		if json.Unmarshal(parsed.Detail, &s) == nil {
			e.Detail = s
		} else if len(parsed.Detail) > 0 && string(parsed.Detail) != "null" {
			e.Detail = string(parsed.Detail)
		}
	}
	if len(e.Detail) > 500 {
		e.Detail = e.Detail[:500] + "…"
	}
	return e
}

// Client는 KoDeploy core API 클라이언트다. 재배포·상태 조회는 Auth의 토큰을 Bearer로 싣고,
// 401을 받으면 Auth.Refresh로 한 번 새 토큰을 받아 같은 요청을 다시 보낸다 (계약 9-6).
type Client struct {
	BaseURL   string
	HTTP      *http.Client
	Auth      TokenSource
	UserAgent string
	// Debug가 있으면 요청마다 "METHOD 경로 → 상태" 한 줄을 넘긴다. 헤더·본문은 넘기지 않는다.
	Debug func(format string, args ...any)
}

func (c *Client) debug(format string, args ...any) {
	if c.Debug != nil {
		c.Debug(format, args...)
	}
}

func (c *Client) send(ctx context.Context, method, path string, body any, headers map[string]string, token string) (int, []byte, error) {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return 0, nil, err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(c.BaseURL, "/")+path, rd)
	if err != nil {
		return 0, nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")
	if c.UserAgent != "" {
		req.Header.Set("User-Agent", c.UserAgent)
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return 0, nil, ctx.Err()
		}
		c.debug("%s %s → %v", method, path, NetErr("request", err))
		return 0, nil, NetErr(method+" "+path, err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if err != nil {
		if ctx.Err() != nil {
			return 0, nil, ctx.Err()
		}
		return 0, nil, NetErr(method+" "+path, err)
	}
	c.debug("%s %s → %d", method, path, resp.StatusCode)
	return resp.StatusCode, data, nil
}

// call은 인증이 필요한 요청이다. 401이면 한 번 새 토큰을 받아 다시 보낸다.
func (c *Client) call(ctx context.Context, method, path string, body any, headers map[string]string, out any) (int, error) {
	token, err := c.Auth.Token(ctx)
	if err != nil {
		return 0, err
	}
	status, data, err := c.send(ctx, method, path, body, headers, token)
	if err != nil {
		return 0, err
	}
	if status == http.StatusUnauthorized {
		if err := c.Auth.Refresh(ctx); err != nil {
			return status, err
		}
		if token, err = c.Auth.Token(ctx); err != nil {
			return status, err
		}
		if status, data, err = c.send(ctx, method, path, body, headers, token); err != nil {
			return 0, err
		}
	}
	return status, decode(status, data, out)
}

func decode(status int, data []byte, out any) error {
	if status < 200 || status > 299 {
		return ParseError(status, data)
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(data, out); err != nil {
		return &Error{Status: status, Detail: "응답을 읽지 못했어요: " + err.Error()}
	}
	return nil
}

// Exchange는 GitHub ID 토큰을 CI 토큰으로 바꾼다 (POST /apps/{app}/ci/token, 계약 9-3). 인증 헤더 없이 보낸다.
func (c *Client) Exchange(ctx context.Context, app, idToken string) (*ExchangeResponse, error) {
	path := "/apps/" + url.PathEscape(app) + "/ci/token"
	status, data, err := c.send(ctx, http.MethodPost, path, ExchangeRequest{IDToken: idToken}, nil, "")
	if err != nil {
		return nil, err
	}
	var out ExchangeResponse
	if err := decode(status, data, &out); err != nil {
		return nil, err
	}
	if out.Token == "" {
		return nil, &Error{Status: status, Detail: "교환 응답에 token이 없어요"}
	}
	return &out, nil
}

// Redeploy는 재배포를 한 번 요청한다 (계약 3절). 상태 코드(202 새 요청, 200 replayed)를 같이 준다.
func (c *Client) Redeploy(ctx context.Context, app, key string, req RedeployRequest) (*RedeployResponse, int, error) {
	path := "/apps/" + url.PathEscape(app) + "/deploy/redeploy"
	var out RedeployResponse
	status, err := c.call(ctx, http.MethodPost, path, req, map[string]string{"Idempotency-Key": key}, &out)
	if err != nil {
		return nil, status, err
	}
	return &out, status, nil
}

// GetBuild는 빌드 하나의 상태다 (계약 4절).
func (c *Client) GetBuild(ctx context.Context, app, buildID string) (*Build, error) {
	path := "/apps/" + url.PathEscape(app) + "/deploy/" + url.PathEscape(buildID)
	var out Build
	if _, err := c.call(ctx, http.MethodGet, path, nil, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// Commits는 배포할 수 있는 최근 커밋과 지금 떠 있는 커밋이다 (웹 배포하기의 커밋 고르기와 같은 API).
func (c *Client) Commits(ctx context.Context, app string) (*CommitsResponse, error) {
	path := "/apps/" + url.PathEscape(app) + "/deploy/redeploy/commits"
	var out CommitsResponse
	if _, err := c.call(ctx, http.MethodGet, path, nil, nil, &out); err != nil {
		return nil, err
	}
	if out.Commits == nil {
		out.Commits = []Commit{}
	}
	return &out, nil
}

// ExecTicket은 셸·DB·Redis 접속 티켓을 받는다. 401이면 다른 요청처럼 한 번 새 토큰을 받아 다시 보낸다(CLI 토큰이면 그대로 실패).
func (c *Client) ExecTicket(ctx context.Context, app string, req ExecTicketRequest) (*ExecTicket, error) {
	path := "/apps/" + url.PathEscape(app) + "/exec-tickets"
	var out ExecTicket
	if _, err := c.call(ctx, http.MethodPost, path, req, nil, &out); err != nil {
		return nil, err
	}
	if out.Ticket == "" || out.URL == "" {
		return nil, &Error{Status: 200, Detail: "티켓 응답에 ticket이나 url이 없어요"}
	}
	return &out, nil
}
