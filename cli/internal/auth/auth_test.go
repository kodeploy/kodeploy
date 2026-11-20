package auth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/kodeploy/kodeploy/cli/internal/api"
)

const reqToken = "gh-request-token-SECRET"

// 가짜 GitHub OIDC 엔드포인트 — audience 쿼리와 헤더를 기록하고 ID 토큰을 매번 새로 준다.
type fakeOIDC struct {
	mu        sync.Mutex
	audiences []string
	auths     []string
	queries   []string
	status    int
	n         int
	srv       *httptest.Server
}

func newFakeOIDC(t *testing.T) *fakeOIDC {
	f := &fakeOIDC{status: 200}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.audiences = append(f.audiences, r.URL.Query().Get("audience"))
		f.auths = append(f.auths, r.Header.Get("Authorization"))
		f.queries = append(f.queries, r.URL.RawQuery)
		if f.status != 200 {
			w.WriteHeader(f.status)
			return
		}
		f.n++
		_ = json.NewEncoder(w).Encode(map[string]any{"count": 1, "value": fmt.Sprintf("id-token-%d-SECRET", f.n)})
	}))
	t.Cleanup(f.srv.Close)
	return f
}

type fakeExchanger struct {
	idTokens []string
	err      error
}

func (f *fakeExchanger) Exchange(_ context.Context, app, idToken string) (*api.ExchangeResponse, error) {
	f.idTokens = append(f.idTokens, idToken)
	if f.err != nil {
		return nil, f.err
	}
	return &api.ExchangeResponse{Token: fmt.Sprintf("kdci_ci-token-%d-SECRET", len(f.idTokens)), ExpiresIn: 900, AppID: app,
		Repository: "acme/api", Ref: "refs/heads/main", SHA: strings.Repeat("a", 40)}, nil
}

func env(m map[string]string) func(string) string { return func(k string) string { return m[k] } }

func newSource(t *testing.T, vars map[string]string, ex Exchanger, audience string) (*Source, *[]string, error) {
	var secrets []string
	s, err := New(Config{Getenv: env(vars), Audience: audience, App: "app-1", Exchange: ex, HTTP: http.DefaultClient,
		OnSecret: func(v string) { secrets = append(secrets, v) }})
	return s, &secrets, err
}

func oidcEnv(f *fakeOIDC, query string) map[string]string {
	return map[string]string{EnvIDTokenURL: f.srv.URL + "/idtoken" + query, EnvIDTokenReqToken: reqToken}
}

func TestStaticTokenWinsAndIsNeverRefreshed(t *testing.T) {
	f := newFakeOIDC(t)
	vars := oidcEnv(f, "?api-version=2.0")
	vars[EnvToken] = "kdci_static-SECRET"
	s, secrets, err := newSource(t, vars, &fakeExchanger{}, "kodeploy")
	if err != nil {
		t.Fatal(err)
	}
	if s.Mode() != ModeStatic {
		t.Fatal("KODEPLOY_TOKEN이 있으면 그것을 쓴다")
	}
	tok, _ := s.Token(context.Background())
	if tok != "kdci_static-SECRET" || len(*secrets) != 1 {
		t.Fatalf("token %q secrets %v", tok, *secrets)
	}
	err = s.Refresh(context.Background())
	if e, ok := IsAuth(err); !ok || !strings.Contains(e.Msg, "만료") {
		t.Fatalf("static 401 → 만료 오류여야 한다: %v", err)
	}
	if len(f.audiences) != 0 {
		t.Fatal("KODEPLOY_TOKEN이면 GitHub ID 토큰을 받지 않는다")
	}
}

func TestNoCredentialsHasThePermissionHint(t *testing.T) {
	_, _, err := newSource(t, map[string]string{EnvIDTokenURL: "https://x"}, &fakeExchanger{}, "kodeploy")
	e, ok := IsAuth(err)
	if !ok || !strings.Contains(e.Hint, "permissions: id-token: write") {
		t.Fatalf("got %v", err)
	}
}

func TestOIDCAudienceAndHeader(t *testing.T) {
	for _, c := range []struct{ query, audience, wantPrefix string }{
		{"?api-version=2.0", "kodeploy", "api-version=2.0&audience=kodeploy"},
		{"", "custom aud", "audience=custom+aud"},
	} {
		f := newFakeOIDC(t)
		ex := &fakeExchanger{}
		s, secrets, err := newSource(t, oidcEnv(f, c.query), ex, c.audience)
		if err != nil {
			t.Fatal(err)
		}
		tok, err := s.Token(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		if tok != "kdci_ci-token-1-SECRET" {
			t.Fatalf("token %q", tok)
		}
		if f.queries[0] != c.wantPrefix || f.audiences[0] != c.audience {
			t.Fatalf("query %q audience %q", f.queries[0], f.audiences[0])
		}
		if f.auths[0] != "bearer "+reqToken {
			t.Fatalf("header %q", f.auths[0])
		}
		if ex.idTokens[0] != "id-token-1-SECRET" {
			t.Fatalf("exchanged %v", ex.idTokens)
		}
		// 두 번째는 캐시
		if tok2, _ := s.Token(context.Background()); tok2 != tok || len(ex.idTokens) != 1 {
			t.Fatal("토큰은 401 전까지 다시 교환하지 않는다")
		}
		want := []string{reqToken, "id-token-1-SECRET", "kdci_ci-token-1-SECRET"}
		if strings.Join(*secrets, ",") != strings.Join(want, ",") {
			t.Fatalf("secrets %v", *secrets)
		}
		if s.Exchanged() == nil || s.Exchanged().Token != "" || s.Exchanged().Repository != "acme/api" {
			t.Fatalf("exchanged %+v", s.Exchanged())
		}
	}
}

func TestRefreshUsesANewIDTokenAtMostFiveTimes(t *testing.T) {
	f := newFakeOIDC(t)
	ex := &fakeExchanger{}
	s, _, _ := newSource(t, oidcEnv(f, "?x=1"), ex, "kodeploy")
	ctx := context.Background()
	if _, err := s.Token(ctx); err != nil {
		t.Fatal(err)
	}
	for i := 1; i <= MaxRefreshes; i++ {
		if err := s.Refresh(ctx); err != nil {
			t.Fatalf("refresh %d: %v", i, err)
		}
		tok, _ := s.Token(ctx)
		if tok != fmt.Sprintf("kdci_ci-token-%d-SECRET", i+1) {
			t.Fatalf("refresh %d token %q", i, tok)
		}
	}
	if err := s.Refresh(ctx); err == nil {
		t.Fatal("다섯 번 넘게 다시 교환하지 않는다")
	}
	seen := map[string]bool{}
	for _, id := range ex.idTokens {
		if seen[id] {
			t.Fatalf("같은 ID 토큰을 두 번 교환했다: %s", id)
		}
		seen[id] = true
	}
	if len(ex.idTokens) != 1+MaxRefreshes {
		t.Fatalf("exchanges %d", len(ex.idTokens))
	}
}

func TestExchangeRejectionCarriesTheServerDetail(t *testing.T) {
	f := newFakeOIDC(t)
	ex := &fakeExchanger{err: &api.Error{Status: 403, Code: "ci_deploy_disabled", Detail: "이 앱은 GitHub Actions 배포가 꺼져 있어요"}}
	s, _, _ := newSource(t, oidcEnv(f, "?x=1"), ex, "kodeploy")
	_, err := s.Token(context.Background())
	e, ok := IsAuth(err)
	if !ok || e.Status != 403 || e.Code != "ci_deploy_disabled" || !strings.Contains(err.Error(), "꺼져 있어요") {
		t.Fatalf("got %#v", err)
	}
	if api.Retryable(err) {
		t.Fatal("403은 다시 시도하지 않는다")
	}
	if strings.Contains(err.Error(), "SECRET") {
		t.Fatal("오류에 토큰이 들어갔다")
	}
}

func TestTemporaryExchangeFailuresAreRetryable(t *testing.T) {
	for _, exErr := range []error{
		&api.Error{Status: 503, Code: "jwks_unavailable"},
		&api.NetError{Op: "POST", Err: errors.New("connection refused")},
	} {
		f := newFakeOIDC(t)
		s, _, _ := newSource(t, oidcEnv(f, "?x=1"), &fakeExchanger{err: exErr}, "kodeploy")
		_, err := s.Token(context.Background())
		if !api.Retryable(err) {
			t.Fatalf("%v는 다시 시도할 오류여야 한다", err)
		}
		if _, ok := api.AsError(err); ok {
			t.Fatal("교환 오류가 재배포 응답 오류로 보이면 안 된다")
		}
	}
}

func TestIDTokenEndpointFailures(t *testing.T) {
	for _, c := range []struct {
		status int
		temp   bool
	}{{403, false}, {500, true}} {
		f := newFakeOIDC(t)
		f.status = c.status
		s, _, _ := newSource(t, oidcEnv(f, "?x=1"), &fakeExchanger{}, "kodeploy")
		_, err := s.Token(context.Background())
		e, ok := IsAuth(err)
		if !ok || e.Temporary() != c.temp || !strings.Contains(e.Hint, "id-token: write") {
			t.Fatalf("status %d: %#v", c.status, err)
		}
		if strings.Contains(err.Error(), f.srv.URL) || strings.Contains(err.Error(), reqToken) {
			t.Fatalf("오류에 주소나 토큰이 들어갔다: %v", err)
		}
	}
}
