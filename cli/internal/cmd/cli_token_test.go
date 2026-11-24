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

// 웹에서 발급한 CLI 토큰(kdcli_)으로 쓰는 경우 — 가짜 core는 재배포·상태 조회·최근 커밋 조회만 흉내 낸다.

const cliToken = "kdcli_cli-token-SECRET-0123456789"

type fakeCore struct {
	mu       sync.Mutex
	srv      *httptest.Server
	expired  bool // true면 모든 요청을 401 cli_token_expired로
	commits  reply
	redeploy []string // 재배포 본문
	auth     []string // 받은 Authorization
	paths    []string
}

func newFakeCore(t *testing.T) *fakeCore {
	f := &fakeCore{commits: reply{200, commitsBody(testSHA)}}
	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		defer f.mu.Unlock()
		f.auth = append(f.auth, r.Header.Get("Authorization"))
		f.paths = append(f.paths, r.Method+" "+r.URL.Path)
		write := func(rp reply) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(rp.status)
			_, _ = io.WriteString(w, rp.body)
		}
		if r.Header.Get("Authorization") != "Bearer "+cliToken {
			write(errBody(401, "cli_token_invalid", "CLI 토큰이 없어요. 웹에서 발급해 주세요"))
			return
		}
		if f.expired {
			write(errBody(401, "cli_token_expired", "CLI 토큰이 만료됐어요. 웹에서 다시 발급해 주세요"))
			return
		}
		prefix := "/apps/" + testApp
		switch path := strings.TrimPrefix(r.URL.Path, prefix); {
		case r.Method == http.MethodGet && path == "/deploy/redeploy/commits":
			write(f.commits)
		case r.Method == http.MethodPost && path == "/deploy/redeploy":
			b, _ := io.ReadAll(r.Body)
			f.redeploy = append(f.redeploy, string(b))
			write(reply{202, fmt.Sprintf(`{"app_id":"%s","requested_sha":null,"replayed":false,"builds":[`+
				`{"build_id":"3f9a2c1d","slot":"server","status":"queued","ref":"main"}]}`, testApp)})
		case r.Method == http.MethodGet && path == "/deploy/3f9a2c1d":
			write(reply{200, buildBody("3f9a2c1d", map[string]any{"status": "running"})})
		default:
			write(reply{404, `{"detail":"Not Found"}`})
		}
	}))
	t.Cleanup(f.srv.Close)
	return f
}

var pickSHAs = []string{
	"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
	"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
	"cccccccccccccccccccccccccccccccccccccccc",
}

// 최근 커밋 3개. current가 그중 하나가 아니면 current 칸도 채운다.
func commitsBody(current string) string {
	commits := []map[string]any{}
	for i, sha := range pickSHAs {
		commits = append(commits, map[string]any{"sha": sha, "message": fmt.Sprintf("commit %d\n\nbody", i+1),
			"date": "2026-11-2" + fmt.Sprint(3-i) + "T05:00:00Z", "url": nil})
	}
	body := map[string]any{"repo": "https://github.com/acme/api.git", "branch": "main", "current_sha": current,
		"commits": commits, "current": nil}
	if current != "" && current != pickSHAs[0] && current != pickSHAs[1] && current != pickSHAs[2] {
		body["current"] = map[string]any{"sha": current, "message": "older", "date": nil, "url": nil}
	}
	if current == "" {
		body["current_sha"] = nil
	}
	b, _ := json.Marshal(body)
	return string(b)
}

func cliEnv(f *fakeCore) map[string]string {
	return map[string]string{"KODEPLOY_TOKEN": cliToken, "KODEPLOY_SERVER": f.srv.URL, "KODEPLOY_APP": testApp}
}

// runTTY는 터미널 여부와 stdin을 정해 실행한다.
func runTTY(t *testing.T, ctx context.Context, env map[string]string, tty bool, stdin string, args ...string) cliRun {
	t.Helper()
	clk := clock.NewFake(t0)
	var out, errb bytes.Buffer
	code := Run(ctx, args, Deps{
		Getenv:      func(k string) string { return env[k] },
		Stdout:      &out,
		Stderr:      &errb,
		Clock:       clk,
		HTTP:        http.DefaultClient,
		NewKey:      func() string { return "cli-00000000-0000-4000-8000-000000000000" },
		Stdin:       strings.NewReader(stdin),
		Interactive: func() bool { return tty },
	})
	r := cliRun{code: code, stdout: out.String(), stderr: errb.String(), clk: clk}
	if strings.Contains(r.stdout+r.stderr, cliToken) {
		t.Fatalf("토큰 원문이 출력에 있다\n%s\n%s", r.stdout, r.stderr)
	}
	return r
}

func TestCLITokenDeploysLatestByDefault(t *testing.T) {
	f := newFakeCore(t)
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "deploy", "--settle", "0")
	if r.code != ExitOK || len(f.redeploy) != 1 || f.redeploy[0] != `{}` {
		t.Fatalf("exit %d body %v\n%s", r.code, f.redeploy, r.stderr)
	}
}

func TestCLITokenDeploysTheGivenSha(t *testing.T) {
	f := newFakeCore(t)
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "deploy", "--sha", strings.ToUpper(pickSHAs[1]), "--settle", "0")
	if r.code != ExitOK || f.redeploy[0] != `{"commit_sha":"`+pickSHAs[1]+`"}` {
		t.Fatalf("exit %d body %v\n%s", r.code, f.redeploy, r.stderr)
	}
}

// 만료·폐기된 kdcli_ 토큰은 다시 교환하지 않고 125 — GitHub Actions 환경변수가 있어도 마찬가지다.
func TestExpiredCLITokenAsksToReissueWithoutExchanging(t *testing.T) {
	for _, args := range [][]string{{"deploy"}, {"commits"}, {"status", "3f9a2c1d"}, {"deploy", "--output", "json"}} {
		f := newFakeCore(t)
		f.expired = true
		o := newFakeOIDC(t)
		env := cliEnv(f)
		env["ACTIONS_ID_TOKEN_REQUEST_URL"] = o.srv.URL + "/idtoken?api-version=2.0"
		env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = ghRequestToken
		r := runTTY(t, context.Background(), env, false, "", args...)
		if r.code != ExitAuth || !strings.Contains(r.stderr, "토큰이 만료됐어요. 웹에서 다시 발급해 주세요") {
			t.Fatalf("%v: exit %d\n%s\n%s", args, r.code, r.stdout, r.stderr)
		}
		if len(f.auth) != 1 || o.n != 0 || len(f.redeploy) != 0 {
			t.Fatalf("%v: 401 뒤 다시 보내거나 교환했다: 요청 %d, ID 토큰 %d", args, len(f.auth), o.n)
		}
	}
}

func TestUnknownCLITokenIsAlso125(t *testing.T) {
	f := newFakeCore(t)
	env := cliEnv(f)
	env["KODEPLOY_TOKEN"] = "kdcli_revoked-or-reissued"
	r := runTTY(t, context.Background(), env, false, "", "deploy")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "웹에서 다시 발급해 주세요") || len(f.auth) != 1 {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
}

func TestCommitsText(t *testing.T) {
	f := newFakeCore(t)
	f.commits = reply{200, commitsBody(pickSHAs[1])}
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "commits")
	if r.code != ExitOK {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
	lines := strings.Split(strings.TrimRight(r.stdout, "\n"), "\n")
	if len(lines) != 4 || lines[0] != "acme/api · main" ||
		!strings.HasPrefix(lines[1], "  aaaaaaa  ") || !strings.HasSuffix(lines[1], "commit 1  (최신)") ||
		!strings.HasSuffix(lines[2], "commit 2  (지금 떠 있음)") || !strings.HasSuffix(lines[3], "commit 3") {
		t.Fatalf("%q", r.stdout)
	}
	if f.paths[0] != "GET /apps/"+testApp+"/deploy/redeploy/commits" {
		t.Fatal(f.paths)
	}
}

func TestCommitsShowsALiveCommitOutsideTheList(t *testing.T) {
	f := newFakeCore(t)
	f.commits = reply{200, commitsBody(testSHA)}
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "commits")
	if r.code != ExitOK || !strings.Contains(r.stdout, "지금 떠 있는 커밋: 3f9a1c2  older (최근 3개 밖)") {
		t.Fatalf("%q", r.stdout)
	}
	f.commits = reply{200, commitsBody("")}
	r = runTTY(t, context.Background(), cliEnv(f), false, "", "commits")
	if !strings.Contains(r.stdout, "지금 떠 있는 커밋: 기록 없음") {
		t.Fatalf("%q", r.stdout)
	}
}

func TestCommitsJSON(t *testing.T) {
	f := newFakeCore(t)
	f.commits = reply{200, commitsBody(pickSHAs[0])}
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "commits", "--output", "json")
	var got struct {
		Repo       string  `json:"repo"`
		Branch     string  `json:"branch"`
		CurrentSHA *string `json:"current_sha"`
		Commits    []struct {
			SHA string `json:"sha"`
		} `json:"commits"`
	}
	if err := json.Unmarshal([]byte(r.stdout), &got); err != nil || r.code != ExitOK {
		t.Fatalf("%v %q", err, r.stdout)
	}
	if got.Branch != "main" || len(got.Commits) != 3 || got.Commits[2].SHA != pickSHAs[2] || *got.CurrentSHA != pickSHAs[0] {
		t.Fatalf("%+v", got)
	}
	if strings.Count(r.stdout, "\n") != 1 {
		t.Fatalf("stdout에는 JSON 한 줄만: %q", r.stdout)
	}
}

func TestCommitsErrors(t *testing.T) {
	f := newFakeCore(t)
	f.commits = errBody(409, "", "성공한 배포가 없어요")
	if r := runTTY(t, context.Background(), cliEnv(f), false, "", "commits"); r.code != ExitUsage {
		t.Fatalf("409 exit %d", r.code)
	}
	f.commits = errBody(403, "cli_token_app_mismatch", "이 CLI 토큰은 다른 앱용이에요")
	r := runTTY(t, context.Background(), cliEnv(f), false, "", "commits")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "다른 앱용") {
		t.Fatalf("403 exit %d %s", r.code, r.stderr)
	}
	if r := runTTY(t, context.Background(), map[string]string{"KODEPLOY_TOKEN": cliToken}, false, "", "commits"); r.code != ExitUsage {
		t.Fatalf("--app 없음 exit %d", r.code)
	}
}

func TestPickOutsideATerminalIsAUsageError(t *testing.T) {
	f := newFakeCore(t)
	r := runTTY(t, context.Background(), cliEnv(f), false, "2\n", "deploy", "--pick")
	if r.code != ExitUsage || !strings.Contains(r.stderr, "터미널") || len(f.auth) != 0 {
		t.Fatalf("exit %d 요청 %d\n%s", r.code, len(f.auth), r.stderr)
	}
	r = runTTY(t, context.Background(), cliEnv(f), true, "2\n", "deploy", "--pick", "--sha", pickSHAs[0])
	if r.code != ExitUsage || len(f.auth) != 0 {
		t.Fatalf("--pick + --sha exit %d", r.code)
	}
}

func TestPickAnOlderCommit(t *testing.T) {
	f := newFakeCore(t)
	f.commits = reply{200, commitsBody(pickSHAs[0])}
	r := runTTY(t, context.Background(), cliEnv(f), true, "3\n", "deploy", "--pick", "--settle", "0")
	if r.code != ExitOK || f.redeploy[0] != `{"commit_sha":"`+pickSHAs[2]+`"}` {
		t.Fatalf("exit %d body %v\n%s", r.code, f.redeploy, r.stderr)
	}
	for _, want := range []string{"1) aaaaaaa", "(최신, 지금 떠 있음)", "3) ccccccc", "배포할 커밋 번호 [1-3, 엔터=1]", "ccccccc로 배포해요"} {
		if !strings.Contains(r.stderr, want) {
			t.Fatalf("stderr에 %q가 없다\n%s", want, r.stderr)
		}
	}
}

// 최신(1번, 엔터)을 고르면 웹 배포하기처럼 커밋을 지정하지 않는다
func TestPickTheLatestSendsNoSha(t *testing.T) {
	for _, in := range []string{"\n", "1\n"} {
		f := newFakeCore(t)
		r := runTTY(t, context.Background(), cliEnv(f), true, in, "deploy", "--pick", "--settle", "0")
		if r.code != ExitOK || f.redeploy[0] != `{}` || !strings.Contains(r.stderr, "브랜치 최신(aaaaaaa)으로 배포해요") {
			t.Fatalf("%q: exit %d body %v\n%s", in, r.code, f.redeploy, r.stderr)
		}
	}
}

func TestPickRetriesABadNumberThenGivesUp(t *testing.T) {
	f := newFakeCore(t)
	r := runTTY(t, context.Background(), cliEnv(f), true, "9\nx\n2\n", "deploy", "--pick", "--settle", "0")
	if r.code != ExitOK || f.redeploy[0] != `{"commit_sha":"`+pickSHAs[1]+`"}` || strings.Count(r.stderr, "1부터 3 사이") != 2 {
		t.Fatalf("exit %d body %v\n%s", r.code, f.redeploy, r.stderr)
	}
	f2 := newFakeCore(t)
	r = runTTY(t, context.Background(), cliEnv(f2), true, "0\n4\n-1\n", "deploy", "--pick")
	if r.code != ExitUsage || len(f2.redeploy) != 0 {
		t.Fatalf("세 번 틀리면 사용법 오류: exit %d", r.code)
	}
}

func TestPickEndOfInputDoesNotDeploy(t *testing.T) {
	f := newFakeCore(t)
	r := runTTY(t, context.Background(), cliEnv(f), true, "", "deploy", "--pick")
	if r.code != ExitInterrupted || len(f.redeploy) != 0 {
		t.Fatalf("exit %d body %v", r.code, f.redeploy)
	}
}

func TestPickWhenGitHubFailedOffersOnlyTheLatest(t *testing.T) {
	f := newFakeCore(t)
	f.commits = reply{200, `{"repo":"https://github.com/acme/api.git","branch":"main","current_sha":null,"commits":[],"current":null}`}
	r := runTTY(t, context.Background(), cliEnv(f), true, "\n", "deploy", "--pick", "--settle", "0")
	if r.code != ExitOK || f.redeploy[0] != `{}` || !strings.Contains(r.stderr, "1) 브랜치 최신") {
		t.Fatalf("exit %d body %v\n%s", r.code, f.redeploy, r.stderr)
	}
}

func TestPickInterruptedByCtrlC(t *testing.T) {
	f := newFakeCore(t)
	ctx, cancel := context.WithCancel(context.Background())
	pr, pw := io.Pipe() // 아무것도 쓰지 않는 stdin — 사람이 아직 고르는 중
	defer pw.Close()
	clk := clock.NewFake(t0)
	var out bytes.Buffer
	var errb lockedBuffer
	done := make(chan int, 1)
	go func() {
		done <- Run(ctx, []string{"deploy", "--pick"}, Deps{
			Getenv: func(k string) string { return cliEnv(f)[k] }, Stdout: &out, Stderr: &errb, Clock: clk,
			HTTP: http.DefaultClient, NewKey: func() string { return "k" }, Stdin: pr, Interactive: func() bool { return true },
		})
	}()
	// 커밋 목록을 받고 번호를 기다리는 중에 Ctrl+C
	deadline := time.Now().Add(5 * time.Second)
	for !strings.Contains(errb.String(), "배포할 커밋 번호") {
		select {
		case code := <-done:
			t.Fatalf("묻기 전에 끝났다: exit %d\n%s", code, errb.String())
		default:
		}
		if time.Now().After(deadline) {
			t.Fatal("번호를 묻지 않았다")
		}
		time.Sleep(10 * time.Millisecond)
	}
	cancel()
	select {
	case code := <-done:
		if code != ExitInterrupted || len(f.redeploy) != 0 {
			t.Fatalf("exit %d", code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Ctrl+C 뒤에도 끝나지 않았다")
	}
}

// lockedBuffer는 -race에서 읽고 쓰기가 겹쳐도 되는 버퍼다.
type lockedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}
