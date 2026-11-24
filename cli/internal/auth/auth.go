// Package auth는 KoDeploy API에 실을 토큰을 정한다 (계약 9절).
//
// 결정 순서
//  1. KODEPLOY_TOKEN이 있으면 그대로 쓴다. 401이 나도 다시 받지 않는다 (토큰 만료).
//     웹에서 발급한 CLI 토큰(kdcli_)이면 "웹에서 다시 발급"하라고 알린다.
//  2. ACTIONS_ID_TOKEN_REQUEST_URL과 ACTIONS_ID_TOKEN_REQUEST_TOKEN이 있으면 GitHub ID 토큰을 받아
//     POST /apps/{app}/ci/token으로 CI 토큰과 바꾼다. 401이 나면 새 ID 토큰으로 다시 교환한다 (실행 전체에서 최대 5번).
//  3. 둘 다 없으면 인증 오류다.
//
// 토큰 원문(ID 토큰, CI 토큰, ACTIONS_ID_TOKEN_REQUEST_TOKEN)은 오류 메시지에 넣지 않는다.
// 받는 즉시 OnSecret으로 알려서 출력 쪽이 혹시 섞여 들어간 원문을 가린다.
package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/kodeploy/kodeploy/cli/internal/api"
)

const (
	EnvToken           = "KODEPLOY_TOKEN"
	EnvIDTokenURL      = "ACTIONS_ID_TOKEN_REQUEST_URL"
	EnvIDTokenReqToken = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"

	// 실행 전체에서 401 때문에 다시 교환하는 최대 횟수
	MaxRefreshes = 5

	HintIDTokenPermission = "GitHub Actions라면 워크플로에 `permissions: id-token: write`가 있는지 확인하세요"

	// 웹(앱 설정 → CLI 토큰)에서 발급한 토큰의 접두어. 마지막으로 쓴 뒤 15분, 발급 뒤 8시간이면 끝난다.
	CLITokenPrefix     = "kdcli_"
	MsgCLITokenExpired = "토큰이 만료됐어요. 웹에서 다시 발급해 주세요"
)

type Mode int

const (
	ModeStatic Mode = iota // KODEPLOY_TOKEN
	ModeOIDC               // GitHub Actions ID 토큰 교환
)

// Error는 인증 실패다. 교환 API가 거절했으면 Status·Code·Detail이 그 응답이다.
type Error struct {
	Msg    string
	Status int
	Code   string
	Detail string
	Hint   string
	temp   bool // 5xx·네트워크로 실패 — 다시 시도해 볼 만하다
}

func (e *Error) Error() string {
	if e.Detail != "" {
		return e.Msg + ": " + e.Detail
	}
	return e.Msg
}

func (e *Error) Temporary() bool { return e.temp }

// Exchanger는 ID 토큰을 CI 토큰으로 바꾼다 (api.Client.Exchange).
type Exchanger interface {
	Exchange(ctx context.Context, app, idToken string) (*api.ExchangeResponse, error)
}

type Config struct {
	Getenv   func(string) string
	Audience string
	App      string
	Exchange Exchanger
	HTTP     *http.Client
	// OnSecret은 토큰 원문을 받을 때마다 불린다 (출력에서 가리려고).
	OnSecret func(string)
}

type Source struct {
	cfg        Config
	mode       Mode
	static     string
	requestURL string
	requestTok string
	token      string
	refreshes  int
	exchanged  *api.ExchangeResponse
}

// New는 환경변수로 인증 방식을 정한다. 아무것도 없으면 *Error(힌트 포함)다.
func New(cfg Config) (*Source, error) {
	if cfg.OnSecret == nil {
		cfg.OnSecret = func(string) {}
	}
	s := &Source{cfg: cfg}
	if tok := strings.TrimSpace(cfg.Getenv(EnvToken)); tok != "" {
		s.mode, s.static = ModeStatic, tok
		cfg.OnSecret(tok)
		return s, nil
	}
	u, rt := cfg.Getenv(EnvIDTokenURL), cfg.Getenv(EnvIDTokenReqToken)
	if u != "" && rt != "" {
		s.mode, s.requestURL, s.requestTok = ModeOIDC, u, rt
		cfg.OnSecret(rt)
		return s, nil
	}
	return nil, &Error{
		Msg:  "인증 정보가 없어요. GitHub Actions에서 실행하거나 KODEPLOY_TOKEN을 넣어 주세요",
		Hint: HintIDTokenPermission,
	}
}

func (s *Source) Mode() Mode { return s.mode }

// IsCLIToken은 KODEPLOY_TOKEN이 웹에서 발급한 CLI 토큰(kdcli_)인가.
func (s *Source) IsCLIToken() bool {
	return s.mode == ModeStatic && strings.HasPrefix(s.static, CLITokenPrefix)
}

// Exchanged는 마지막 교환 응답이다 (OIDC가 아니거나 아직 교환 전이면 nil). 토큰 원문은 빼고 쓴다.
func (s *Source) Exchanged() *api.ExchangeResponse { return s.exchanged }

func (s *Source) Token(ctx context.Context) (string, error) {
	if s.mode == ModeStatic {
		return s.static, nil
	}
	if s.token == "" {
		if err := s.exchange(ctx); err != nil {
			return "", err
		}
	}
	return s.token, nil
}

// Refresh는 401을 받았을 때 부른다. KODEPLOY_TOKEN이면 다시 받을 방법이 없어 오류다.
func (s *Source) Refresh(ctx context.Context) error {
	if s.mode == ModeStatic {
		if s.IsCLIToken() {
			return &Error{Msg: MsgCLITokenExpired}
		}
		return &Error{Msg: "KODEPLOY_TOKEN이 만료됐거나 유효하지 않아요"}
	}
	if s.refreshes >= MaxRefreshes {
		return &Error{Msg: fmt.Sprintf("CI 토큰을 %d번 다시 교환했는데도 거절됐어요", MaxRefreshes)}
	}
	s.refreshes++
	s.token = ""
	return s.exchange(ctx)
}

func (s *Source) exchange(ctx context.Context) error {
	idToken, err := s.idToken(ctx)
	if err != nil {
		return err
	}
	resp, err := s.cfg.Exchange.Exchange(ctx, s.cfg.App, idToken)
	if err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if ae, ok := api.AsError(err); ok {
			return &Error{Msg: "CI 토큰 교환이 거절됐어요", Status: ae.Status, Code: ae.Code, Detail: ae.Detail, temp: ae.Temporary()}
		}
		return &Error{Msg: "CI 토큰을 교환하지 못했어요", Detail: err.Error(), temp: api.Retryable(err)}
	}
	s.cfg.OnSecret(resp.Token)
	s.token = resp.Token
	cp := *resp
	cp.Token = ""
	s.exchanged = &cp
	return nil
}

// idToken은 GitHub Actions 러너에서 ID 토큰을 받는다 (계약 9-2).
// GET $ACTIONS_ID_TOKEN_REQUEST_URL&audience=<audience>, Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN → {"value": ...}
func (s *Source) idToken(ctx context.Context) (string, error) {
	sep := "&"
	if !strings.Contains(s.requestURL, "?") {
		sep = "?"
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, s.requestURL+sep+"audience="+url.QueryEscape(s.cfg.Audience), nil)
	if err != nil {
		return "", &Error{Msg: "GitHub ID 토큰 요청 주소가 잘못됐어요", Hint: HintIDTokenPermission}
	}
	req.Header.Set("Authorization", "bearer "+s.requestTok)
	req.Header.Set("Accept", "application/json")
	resp, err := s.cfg.HTTP.Do(req)
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", &Error{Msg: "GitHub ID 토큰을 받지 못했어요", Detail: api.NetErr("id token", err).Error(), temp: true}
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return "", &Error{
			Msg:  fmt.Sprintf("GitHub ID 토큰을 받지 못했어요 (HTTP %d)", resp.StatusCode),
			Hint: HintIDTokenPermission,
			temp: resp.StatusCode >= 500,
		}
	}
	var body struct {
		Value string `json:"value"`
	}
	if err := json.Unmarshal(data, &body); err != nil || body.Value == "" {
		return "", &Error{Msg: "GitHub ID 토큰 응답에 value가 없어요", Hint: HintIDTokenPermission}
	}
	s.cfg.OnSecret(body.Value)
	return body.Value, nil
}

// IsAuth는 err가 인증 오류(*Error)인가.
func IsAuth(err error) (*Error, bool) {
	var e *Error
	ok := errors.As(err, &e)
	return e, ok
}
