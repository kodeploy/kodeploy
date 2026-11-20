package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

type fakeTokens struct {
	tokens    []string
	i         int
	refreshes int
	refErr    error
}

func (f *fakeTokens) Token(context.Context) (string, error) { return f.tokens[f.i], nil }
func (f *fakeTokens) Refresh(context.Context) error {
	f.refreshes++
	if f.refErr != nil {
		return f.refErr
	}
	f.i++
	return nil
}

func TestUnauthorizedRefreshesOnceAndRetriesTheSameRequest(t *testing.T) {
	var auths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		auths = append(auths, r.Header.Get("Authorization"))
		if r.Header.Get("Authorization") != "Bearer kdci_two" {
			w.WriteHeader(401)
			_, _ = io.WriteString(w, `{"detail":"CI 토큰이 만료됐어요","code":"ci_token_expired"}`)
			return
		}
		_ = json.NewEncoder(w).Encode(Build{BuildID: "b1", Status: "running"})
	}))
	defer srv.Close()
	tokens := &fakeTokens{tokens: []string{"kdci_one", "kdci_two"}}
	c := &Client{BaseURL: srv.URL, HTTP: srv.Client(), Auth: tokens}
	b, err := c.GetBuild(context.Background(), "app-1", "b1")
	if err != nil || b.Status != "running" {
		t.Fatalf("%v %v", b, err)
	}
	if tokens.refreshes != 1 || len(auths) != 2 {
		t.Fatalf("refreshes %d requests %d", tokens.refreshes, len(auths))
	}
}

func TestSecondUnauthorizedIsReturned(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(401)
		_, _ = io.WriteString(w, `{"detail":"no","code":"ci_token_invalid"}`)
	}))
	defer srv.Close()
	tokens := &fakeTokens{tokens: []string{"a", "b"}}
	c := &Client{BaseURL: srv.URL, HTTP: srv.Client(), Auth: tokens}
	_, err := c.GetBuild(context.Background(), "app", "b")
	e, ok := AsError(err)
	if !ok || e.Status != 401 || e.Code != "ci_token_invalid" || tokens.refreshes != 1 {
		t.Fatalf("%#v refreshes=%d", err, tokens.refreshes)
	}
	// 다시 받을 수 없으면 그 이유를 돌려준다
	tokens2 := &fakeTokens{tokens: []string{"a"}, refErr: errors.New("expired static token")}
	c.Auth = tokens2
	if _, err := c.GetBuild(context.Background(), "app", "b"); err == nil || err.Error() != "expired static token" {
		t.Fatalf("got %v", err)
	}
}

func TestRedeploySendsKeyBearerAndBody(t *testing.T) {
	var got *http.Request
	var body []byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		got, body = r, mustRead(r)
		w.WriteHeader(200)
		_, _ = io.WriteString(w, `{"app_id":"app-1","requested_sha":null,"replayed":true,"builds":[{"build_id":"b1","slot":"server","status":"running","ref":"main"}]}`)
	}))
	defer srv.Close()
	c := &Client{BaseURL: srv.URL + "/", HTTP: srv.Client(), Auth: &fakeTokens{tokens: []string{"kdci_x"}}, UserAgent: "kodeploy-cli/test"}
	r, status, err := c.Redeploy(context.Background(), "app-1", "gha-1-2", RedeployRequest{})
	if err != nil || status != 200 || !r.Replayed || len(r.Builds) != 1 {
		t.Fatalf("%+v %d %v", r, status, err)
	}
	if got.URL.Path != "/apps/app-1/deploy/redeploy" || got.Header.Get("Idempotency-Key") != "gha-1-2" ||
		got.Header.Get("Authorization") != "Bearer kdci_x" || string(body) != "{}" || got.Header.Get("User-Agent") != "kodeploy-cli/test" {
		t.Fatalf("path %s headers %v body %s", got.URL.Path, got.Header, body)
	}
}

func TestExchangeSendsNoAuthorization(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "" || r.URL.Path != "/apps/app-1/ci/token" {
			w.WriteHeader(400)
			return
		}
		var req ExchangeRequest
		_ = json.Unmarshal(mustRead(r), &req)
		if req.IDToken != "id-1" {
			w.WriteHeader(400)
			return
		}
		w.WriteHeader(201)
		_, _ = io.WriteString(w, `{"token":"kdci_new","expires_in":900,"app_id":"app-1","repository":"o/r","ref":"refs/heads/main","sha":"s"}`)
	}))
	defer srv.Close()
	c := &Client{BaseURL: srv.URL, HTTP: srv.Client()}
	r, err := c.Exchange(context.Background(), "app-1", "id-1")
	if err != nil || r.Token != "kdci_new" {
		t.Fatalf("%v %v", r, err)
	}
}

func TestParseError(t *testing.T) {
	cases := []struct {
		body, code, detail string
	}{
		{`{"detail":"이유","code":"no_live_deploy"}`, "no_live_deploy", "이유"},
		{`{"detail":"이유만"}`, "", "이유만"},
		{`{"detail":[{"loc":["body","env"],"msg":"extra"}]}`, "", `[{"loc":["body","env"],"msg":"extra"}]`},
		{`not json`, "", ""},
		{``, "", ""},
	}
	for _, c := range cases {
		e := ParseError(409, []byte(c.body))
		if e.Code != c.code || e.Detail != c.detail || e.Status != 409 {
			t.Fatalf("%s → %#v", c.body, e)
		}
	}
	if !(&Error{Status: 502}).Temporary() || (&Error{Status: 409}).Temporary() {
		t.Fatal("5xx만 다시 시도")
	}
}

func TestNetworkErrorsHideTheURL(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	base := srv.URL
	srv.Close()
	c := &Client{BaseURL: base, HTTP: http.DefaultClient, Auth: &fakeTokens{tokens: []string{"kdci_x"}}}
	_, err := c.GetBuild(context.Background(), "app?secret=1", "b")
	var ne *NetError
	if !errors.As(err, &ne) || !Retryable(err) {
		t.Fatalf("got %#v", err)
	}
	if strings.Contains(err.Error(), base) || strings.Contains(err.Error(), "http://") {
		t.Fatalf("오류에 주소가 들어갔다: %v", err)
	}
}

func TestDebugLinesCarryNoHeaders(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"build_id":"b","status":"queued"}`)
	}))
	defer srv.Close()
	var lines []string
	c := &Client{BaseURL: srv.URL, HTTP: srv.Client(), Auth: &fakeTokens{tokens: []string{"kdci_SECRET"}},
		Debug: func(f string, a ...any) { lines = append(lines, strings.TrimSpace(sprintf(f, a...))) }}
	if _, err := c.GetBuild(context.Background(), "app", "b"); err != nil {
		t.Fatal(err)
	}
	if len(lines) != 1 || lines[0] != "GET /apps/app/deploy/b → 200" {
		t.Fatalf("%v", lines)
	}
}

func mustRead(r *http.Request) []byte {
	b, _ := io.ReadAll(r.Body)
	return b
}
