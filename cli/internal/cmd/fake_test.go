package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

// 가짜 KoDeploy core — docs/deploy-api-contract.md의 엔드포인트와 응답만 흉내 낸다.
//   POST /apps/{app}/ci/token          ID 토큰(가짜 OIDC가 준 것) → kdci_ 토큰
//   POST /apps/{app}/deploy/redeploy   Bearer + Idempotency-Key, 응답은 redeploy 차례대로
//   GET  /apps/{app}/deploy/{build_id} Bearer, 상태는 builds[id] 차례대로 (다 쓰면 마지막 반복)

const (
	testApp        = "11111111-2222-4333-8444-555555555555"
	ghRequestToken = "gh-request-token-SECRET"
	staticToken    = "kdci_static-token-SECRET"
	testSHA        = "3f9a1c2b0123456789abcdef0123456789abcdef"
)

type reply struct {
	status int
	body   string
}

func errBody(status int, code, detail string) reply {
	b, _ := json.Marshal(map[string]string{"detail": detail, "code": code})
	if code == "" {
		b, _ = json.Marshal(map[string]string{"detail": detail})
	}
	return reply{status, string(b)}
}

type redeployCall struct {
	key, auth, body string
}

type fakeKD struct {
	t   *testing.T
	mu  sync.Mutex
	srv *httptest.Server

	exchangeReply *reply           // 있으면 교환을 이 응답으로 거절
	idTokens      []string         // 교환에 받은 ID 토큰
	tokens        map[string]int   // 발급한 CI 토큰 → 남은 사용 횟수
	tokenUses     int              // 토큰 하나로 받는 요청 수 (0 = 무제한). 넘으면 401 ci_token_expired
	allowStatic   bool             // KODEPLOY_TOKEN(staticToken)을 받는다

	redeploy  []reply // 재배포 응답 차례 (다 쓰면 마지막 반복)
	calls     []redeployCall
	builds    map[string][]any // build id → 조회마다 상태 문자열, reply(오류), map(빌드 본문 덮어쓰기)
	gets      map[string]int
	onGet     func(id string, n int) // 조회마다 (테스트가 끼어들 때)
	authSeen  []string
}

func newFakeKD(t *testing.T) *fakeKD {
	f := &fakeKD{t: t, tokens: map[string]int{}, builds: map[string][]any{}, gets: map[string]int{}}
	f.srv = httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(f.srv.Close)
	return f
}

// 서버 슬롯 하나, 정적 슬롯 하나 — 계약 3-3의 응답 예시
func redeployOK(status int, replayed bool) reply {
	return reply{status, fmt.Sprintf(`{"app_id":"%s","requested_sha":"%s","replayed":%t,"builds":[`+
		`{"build_id":"3f9a2c1d","slot":"server","status":"queued","ref":"%s"},`+
		`{"build_id":"8e7d6c5b","slot":"static","status":"queued","ref":"main"}]}`, testApp, testSHA, replayed, testSHA)}
}

func (f *fakeKD) write(w http.ResponseWriter, r reply) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(r.status)
	_, _ = io.WriteString(w, r.body)
}

// checkAuth는 Bearer를 확인한다. 실패하면 401을 쓰고 false.
func (f *fakeKD) checkAuth(w http.ResponseWriter, r *http.Request) bool {
	h := r.Header.Get("Authorization")
	f.authSeen = append(f.authSeen, h)
	tok, ok := strings.CutPrefix(h, "Bearer ")
	if ok && f.allowStatic && tok == staticToken {
		return true
	}
	left, known := f.tokens[tok]
	if !ok || !known {
		f.write(w, errBody(401, "ci_token_invalid", "CI 토큰이 없거나 폐기됐어요"))
		return false
	}
	if f.tokenUses > 0 {
		if left <= 0 {
			f.write(w, errBody(401, "ci_token_expired", "CI 토큰이 만료됐어요. 새 ID 토큰으로 다시 교환해 주세요"))
			return false
		}
		f.tokens[tok] = left - 1
	}
	return true
}

func (f *fakeKD) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	prefix := "/apps/" + testApp
	path, ok := strings.CutPrefix(r.URL.Path, prefix)
	if !ok {
		f.write(w, reply{404, `{"detail":"app not found"}`})
		return
	}
	switch {
	case r.Method == http.MethodPost && path == "/ci/token":
		var body struct {
			IDToken string `json:"id_token"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if r.Header.Get("Authorization") != "" {
			f.t.Errorf("교환 요청에 Authorization이 있다")
		}
		f.idTokens = append(f.idTokens, body.IDToken)
		if f.exchangeReply != nil {
			f.write(w, *f.exchangeReply)
			return
		}
		if !strings.HasPrefix(body.IDToken, "id-token-") {
			f.write(w, errBody(401, "invalid_token", "ID 토큰 서명을 확인할 수 없어요"))
			return
		}
		tok := fmt.Sprintf("kdci_ci-token-%d-SECRET", len(f.idTokens))
		f.tokens[tok] = f.tokenUses
		f.write(w, reply{201, fmt.Sprintf(`{"token":"%s","expires_in":900,"app_id":"%s","repository":"acme/api","ref":"refs/heads/main","sha":"%s"}`,
			tok, testApp, testSHA)})
	case r.Method == http.MethodPost && path == "/deploy/redeploy":
		if !f.checkAuth(w, r) {
			return
		}
		b, _ := io.ReadAll(r.Body)
		f.calls = append(f.calls, redeployCall{key: r.Header.Get("Idempotency-Key"), auth: r.Header.Get("Authorization"), body: string(b)})
		i := min(len(f.calls)-1, len(f.redeploy)-1)
		f.write(w, f.redeploy[i])
	case r.Method == http.MethodGet && strings.HasPrefix(path, "/deploy/"):
		if !f.checkAuth(w, r) {
			return
		}
		id := strings.TrimPrefix(path, "/deploy/")
		seq, ok := f.builds[id]
		if !ok {
			f.write(w, reply{404, `{"detail":"build not found"}`})
			return
		}
		n := f.gets[id]
		f.gets[id]++
		if f.onGet != nil {
			f.onGet(id, n)
		}
		step := seq[min(n, len(seq)-1)]
		switch v := step.(type) {
		case reply:
			f.write(w, v)
		case string:
			f.write(w, reply{200, buildBody(id, map[string]any{"status": v})})
		case map[string]any:
			f.write(w, reply{200, buildBody(id, v)})
		}
	default:
		f.write(w, reply{404, `{"detail":"Not Found"}`})
	}
}

func buildBody(id string, over map[string]any) string {
	b := map[string]any{
		"build_id": id, "status": "queued", "runtime": "java", "branch": "main", "error": nil,
		"source_sha": nil, "requested_sha": testSHA, "ai_status": nil, "ai_analysis": nil, "total_seconds": nil, "ci": nil,
		"logs": "log lines", "created_at": "2026-11-21T00:00:00Z",
	}
	if st, _ := over["status"].(string); st == "running" || st == "failed" {
		b["source_sha"] = testSHA
	}
	for k, v := range over {
		b[k] = v
	}
	out, _ := json.Marshal(b)
	return string(out)
}

// 가짜 GitHub OIDC 엔드포인트 — audience 쿼리와 헤더를 확인하고 ID 토큰을 매번 새로 준다.
type fakeOIDC struct {
	mu        sync.Mutex
	srv       *httptest.Server
	audiences []string
	headers   []string
	n         int
}

func newFakeOIDC(t *testing.T) *fakeOIDC {
	o := &fakeOIDC{}
	o.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		o.mu.Lock()
		defer o.mu.Unlock()
		o.audiences = append(o.audiences, r.URL.Query().Get("audience"))
		o.headers = append(o.headers, r.Header.Get("Authorization"))
		if r.URL.Query().Get("api-version") != "2.0" || r.Header.Get("Authorization") != "bearer "+ghRequestToken {
			w.WriteHeader(403)
			return
		}
		o.n++
		_ = json.NewEncoder(w).Encode(map[string]any{"count": 1, "value": fmt.Sprintf("id-token-%d-SECRET", o.n)})
	}))
	t.Cleanup(o.srv.Close)
	return o
}

type cliRun struct {
	code           int
	stdout, stderr string
	clk            *clock.Fake
}

var t0 = time.Date(2026, 11, 21, 12, 0, 0, 0, time.UTC)

// actionsEnv는 GitHub Actions 러너의 환경변수다 (id-token: write가 있는 워크플로).
func actionsEnv(f *fakeKD, o *fakeOIDC) map[string]string {
	return map[string]string{
		"GITHUB_RUN_ID":                  "98765",
		"GITHUB_RUN_ATTEMPT":             "1",
		"ACTIONS_ID_TOKEN_REQUEST_URL":   o.srv.URL + "/idtoken?api-version=2.0",
		"ACTIONS_ID_TOKEN_REQUEST_TOKEN": ghRequestToken,
		"KODEPLOY_SERVER":                f.srv.URL,
		"KODEPLOY_APP":                   testApp,
	}
}

func runCLI(t *testing.T, ctx context.Context, env map[string]string, clk *clock.Fake, args ...string) cliRun {
	t.Helper()
	if clk == nil {
		clk = clock.NewFake(t0)
	}
	var out, errb bytes.Buffer
	code := Run(ctx, args, Deps{
		Getenv: func(k string) string { return env[k] },
		Stdout: &out,
		Stderr: &errb,
		Clock:  clk,
		HTTP:   http.DefaultClient,
		NewKey: func() string { return "cli-00000000-0000-4000-8000-000000000000" },
	})
	return cliRun{code: code, stdout: out.String(), stderr: errb.String(), clk: clk}
}

// 토큰 원문이 출력 어디에도 없는지 (계약: 토큰은 응답으로만)
func assertNoSecrets(t *testing.T, r cliRun) {
	t.Helper()
	for _, s := range []string{"SECRET", ghRequestToken, staticToken, "kdci_ci-token", "id-token-"} {
		if strings.Contains(r.stdout, s) || strings.Contains(r.stderr, s) {
			t.Fatalf("출력에 비밀 %q가 들어갔다\nstdout:\n%s\nstderr:\n%s", s, r.stdout, r.stderr)
		}
	}
}
