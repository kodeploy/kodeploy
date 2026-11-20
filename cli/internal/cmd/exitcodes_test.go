package cmd

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// README·계약 5절의 exit code 표를 명령 단위로 확인한다. 모든 경우에 토큰 원문이 출력에 없는지도 본다 (디버그 출력 포함).
func TestExitCodes(t *testing.T) {
	type scenario struct {
		name  string
		args  []string
		setup func(f *fakeKD, env map[string]string) context.Context
		code  int
		want  string // stdout+stderr 어딘가에
	}
	ok := func(f *fakeKD) {
		f.redeploy = []reply{redeployOK(202, false)}
		happyBuilds(f)
	}
	cases := []scenario{
		{"성공", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context { ok(f); return nil }, ExitOK, "성공 source=3f9a1c2"},
		{"no-wait", []string{"deploy", "--no-wait"}, func(f *fakeKD, _ map[string]string) context.Context { ok(f); return nil }, ExitOK, "[server 3f9a2c1d] queued ref=" + testSHA},
		{"실패", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{redeployOK(202, false)}
			f.builds["3f9a2c1d"] = []any{"building", map[string]any{"status": "failed", "error": "build: exit 1"}}
			f.builds["8e7d6c5b"] = []any{"running"}
			return nil
		}, ExitFailed, "build: exit 1"},
		{"--app 없음", []string{"deploy"}, func(f *fakeKD, env map[string]string) context.Context { delete(env, "KODEPLOY_APP"); return nil }, ExitUsage, "--app"},
		{"짧은 --sha", []string{"deploy", "--sha", "abc1234"}, func(*fakeKD, map[string]string) context.Context { return nil }, ExitUsage, "40자"},
		{"--output 잘못", []string{"deploy", "--output", "yaml"}, func(*fakeKD, map[string]string) context.Context { return nil }, ExitUsage, "text나 json"},
		{"모르는 플래그", []string{"deploy", "--nope"}, func(*fakeKD, map[string]string) context.Context { return nil }, ExitUsage, "unknown flag"},
		{"인자 넘침", []string{"deploy", "extra"}, func(*fakeKD, map[string]string) context.Context { return nil }, ExitUsage, ""},
		{"409 라이브 없음", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{errBody(409, "no_live_deploy", "성공한 배포가 없어요")}
			return nil
		}, ExitUsage, "첫 배포는 웹에서"},
		{"409 슬롯 성공 없음", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{errBody(409, "no_successful_slot", "정적 사이트가 한 번도 성공적으로 배포되지 않아")}
			return nil
		}, ExitUsage, "정적 사이트가"},
		{"409 code 없음", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{{409, `{"detail":"뭔가 충돌"}`}}
			return nil
		}, ExitUsage, "뭔가 충돌"},
		{"400", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{errBody(400, "deploy_rejected", "R2가 설정되지 않았어요")}
			return nil
		}, ExitUsage, "R2가"},
		{"422", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{{422, `{"detail":[{"msg":"extra fields not permitted"}]}`}}
			return nil
		}, ExitUsage, "extra fields"},
		{"밀림", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{redeployOK(202, false)}
			f.builds["3f9a2c1d"] = []any{"building", "cancelled"}
			f.builds["8e7d6c5b"] = []any{"running"}
			return nil
		}, ExitSuperseded, "다른 배포에 밀렸어요 [server 3f9a2c1d] cancelled"},
		{"시간 초과", []string{"deploy", "--timeout", "20s"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{redeployOK(202, false)}
			f.builds["3f9a2c1d"] = []any{"building"}
			f.builds["8e7d6c5b"] = []any{"building"}
			return nil
		}, ExitTimeout, "시간 제한(20s)"},
		{"요청 단계 시간 초과", []string{"deploy", "--timeout", "5s"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{errBody(409, "key_in_progress", "처리 중")}
			return nil
		}, ExitTimeout, "시간 제한(5s)"},
		{"인증 정보 없음", []string{"deploy"}, func(f *fakeKD, env map[string]string) context.Context {
			delete(env, "ACTIONS_ID_TOKEN_REQUEST_TOKEN")
			return nil
		}, ExitAuth, "permissions: id-token: write"},
		{"교환 403 (허용 꺼짐)", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			r := errBody(403, "ci_deploy_disabled", "이 앱은 GitHub Actions 배포가 꺼져 있어요. 앱 설정에서 켜 주세요")
			f.exchangeReply = &r
			return nil
		}, ExitAuth, "GitHub Actions 배포가 꺼져 있어요"},
		{"교환 403 (다른 브랜치)", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			r := errBody(403, "branch_mismatch", "브랜치가 이 앱의 배포 브랜치와 달라요")
			f.exchangeReply = &r
			return nil
		}, ExitAuth, "배포 브랜치와 달라요"},
		{"교환 404", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			r := errBody(404, "app_not_found", "app not found")
			f.exchangeReply = &r
			return nil
		}, ExitAuth, "app not found"},
		{"교환 409 라이브 없음", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			r := errBody(409, "no_live_deploy", "성공한 배포가 없어요")
			f.exchangeReply = &r
			return nil
		}, ExitUsage, "첫 배포는 웹에서"},
		{"재배포 403", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{errBody(403, "commit_mismatch", "CI 토큰으로는 그 워크플로가 돈 커밋만")}
			return nil
		}, ExitAuth, "그 워크플로가 돈 커밋만"},
		{"재배포 404", []string{"deploy"}, func(f *fakeKD, env map[string]string) context.Context {
			env["KODEPLOY_APP"] = "99999999-0000-4000-8000-000000000000"
			return nil
		}, ExitAuth, "app not found"},
		{"5xx 다 씀", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{{503, `{"detail":"down"}`}}
			return nil
		}, ExitAuth, "down"},
		{"연결 안 됨", []string{"deploy"}, func(f *fakeKD, env map[string]string) context.Context {
			env["KODEPLOY_SERVER"] = "http://127.0.0.1:1"
			return nil
		}, ExitAuth, ""},
		{"401 재교환 5번 넘음", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{redeployOK(202, false)}
			f.builds["3f9a2c1d"] = []any{"building"}
			f.builds["8e7d6c5b"] = []any{"building"}
			f.tokenUses = 1 // 토큰마다 요청 하나 — 재교환이 금방 바닥난다
			return nil
		}, ExitAuth, "5번"},
		{"중단", []string{"deploy"}, func(f *fakeKD, _ map[string]string) context.Context {
			f.redeploy = []reply{redeployOK(202, false)}
			f.builds["3f9a2c1d"] = []any{"building"}
			f.builds["8e7d6c5b"] = []any{"building"}
			ctx, cancel := context.WithCancel(context.Background())
			f.onGet = func(string, int) { cancel() }
			return ctx
		}, ExitInterrupted, "빌드는 서버에서 계속 진행됩니다"},
	}
	for _, c := range cases {
		for _, debug := range []bool{false, true} {
			name := c.name
			if debug {
				name += " (debug)"
			}
			t.Run(name, func(t *testing.T) {
				f, o := newFakeKD(t), newFakeOIDC(t)
				env := actionsEnv(f, o)
				if debug {
					env["KODEPLOY_DEBUG"] = "1"
				}
				ctx := c.setup(f, env)
				if ctx == nil {
					ctx = context.Background()
				}
				r := runCLI(t, ctx, env, nil, c.args...)
				if r.code != c.code {
					t.Fatalf("exit %d, want %d\nstdout:\n%s\nstderr:\n%s", r.code, c.code, r.stdout, r.stderr)
				}
				if c.want != "" && !strings.Contains(r.stdout+r.stderr, c.want) {
					t.Fatalf("%q가 없다\nstdout:\n%s\nstderr:\n%s", c.want, r.stdout, r.stderr)
				}
				if debug && c.code != ExitUsage && !strings.Contains(r.stderr, "kodeploy: debug:") && c.name != "인증 정보 없음" {
					t.Fatalf("디버그 줄이 없다\n%s", r.stderr)
				}
				assertNoSecrets(t, r)
			})
		}
	}
}

func TestJSONOutputIsOneObjectOnStdout(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--output", "json", "--settle", "0")
	if r.code != ExitOK {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
	var got struct {
		Result       string `json:"result"`
		ExitCode     int    `json:"exit_code"`
		AppID        string `json:"app_id"`
		RequestedSHA string `json:"requested_sha"`
		Builds       []struct {
			BuildID, Slot, Status string
			SourceSHA             *string `json:"source_sha"`
			Error                 *string `json:"error"`
		} `json:"builds"`
	}
	dec := json.NewDecoder(strings.NewReader(r.stdout))
	if err := dec.Decode(&got); err != nil || dec.More() {
		t.Fatalf("stdout은 JSON 객체 하나여야 한다: %v\n%s", err, r.stdout)
	}
	if got.Result != "success" || got.ExitCode != 0 || got.AppID != testApp || got.RequestedSHA != testSHA ||
		len(got.Builds) != 2 || got.Builds[0].Slot != "server" || got.Builds[0].Status != "running" ||
		got.Builds[0].SourceSHA == nil || got.Builds[0].Error != nil {
		t.Fatalf("%+v", got)
	}
	if !strings.Contains(r.stderr, "[server 3f9a2c1d]") {
		t.Fatal("진행 상황은 stderr에 그대로")
	}
}

func TestActionsModeWritesErrorAndStepSummary(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	f.builds["3f9a2c1d"] = []any{"building", map[string]any{"status": "failed", "error": "health: 포트 8080이 열리지 않았어요"}}
	f.builds["8e7d6c5b"] = []any{"running"}
	env := actionsEnv(f, o)
	summary := filepath.Join(t.TempDir(), "summary.md")
	env["GITHUB_ACTIONS"] = "true"
	env["GITHUB_STEP_SUMMARY"] = summary
	r := runCLI(t, context.Background(), env, nil, "deploy")
	if r.code != ExitFailed {
		t.Fatalf("exit %d", r.code)
	}
	if !strings.Contains(r.stdout, "::error title=kodeploy::배포 실패 [server 3f9a2c1d] — [server 3f9a2c1d] health: 포트 8080이 열리지 않았어요") {
		t.Fatalf("stdout %s", r.stdout)
	}
	md, err := os.ReadFile(summary)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"### KoDeploy 배포 실패", "| `" + testApp + "` | `3f9a1c2` | 1 |", "| server | `3f9a2c1d` | failed |",
		"| static | `8e7d6c5b` | running |", "실패 이유: "} {
		if !strings.Contains(string(md), want) {
			t.Fatalf("summary에 %q가 없다\n%s", want, md)
		}
	}
	assertNoSecrets(t, r)
}

func TestStatusCommand(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.builds["3f9a2c1d"] = []any{map[string]any{"status": "running",
		"ci": map[string]any{"actor": "octocat", "repository": "acme/api", "ref": "refs/heads/main", "sha": testSHA, "run_id": "98765", "event_name": "push"}}}
	env := actionsEnv(f, o)
	r := runCLI(t, context.Background(), env, nil, "status", "3f9a2c1d")
	if r.code != ExitOK || r.stdout != "[server 3f9a2c1d] running source=3f9a1c2\n  GitHub Actions · octocat · run 98765\n" {
		t.Fatalf("exit %d stdout %q\n%s", r.code, r.stdout, r.stderr)
	}
	r = runCLI(t, context.Background(), env, nil, "status", "3f9a2c1d", "--output", "json")
	var got statusOut
	if err := json.Unmarshal([]byte(r.stdout), &got); err != nil || got.Status != "running" || got.CI == nil || got.CI.RunID != "98765" {
		t.Fatalf("%v %s", err, r.stdout)
	}
	if r := runCLI(t, context.Background(), env, nil, "status", "nope0000"); r.code != ExitAuth {
		t.Fatalf("없는 빌드 exit %d", r.code)
	}
	if r := runCLI(t, context.Background(), env, nil, "status"); r.code != ExitUsage {
		t.Fatalf("build_id 없음 exit %d", r.code)
	}
	assertNoSecrets(t, r)
}

func TestVersionCommand(t *testing.T) {
	old := version
	version = "0.1.0"
	defer func() { version = old }()
	r := runCLI(t, context.Background(), map[string]string{}, nil, "version")
	if r.code != ExitOK || r.stdout != "kodeploy 0.1.0\n" {
		t.Fatalf("%d %q", r.code, r.stdout)
	}
	r = runCLI(t, context.Background(), map[string]string{}, nil, "version", "--output", "json")
	if r.stdout != "{\"version\":\"0.1.0\"}\n" {
		t.Fatalf("%q", r.stdout)
	}
}

func TestFlagsWinOverEnvironment(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	env := actionsEnv(f, o)
	env["KODEPLOY_SERVER"] = "http://127.0.0.1:1"   // 플래그가 이긴다
	env["KODEPLOY_APP"] = "not-this-app"
	env["KODEPLOY_AUDIENCE"] = "from-env"
	r := runCLI(t, context.Background(), env, nil, "deploy", "--server", f.srv.URL, "--app", testApp, "--audience", "from-flag", "--settle", "0")
	if r.code != ExitOK || o.audiences[0] != "from-flag" {
		t.Fatalf("exit %d audiences %v\n%s", r.code, o.audiences, r.stderr)
	}
	// 플래그가 없으면 환경변수
	f2, o2 := newFakeKD(t), newFakeOIDC(t)
	f2.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f2)
	env2 := actionsEnv(f2, o2)
	env2["KODEPLOY_AUDIENCE"] = "from-env"
	if r := runCLI(t, context.Background(), env2, nil, "deploy", "--settle", "0"); r.code != ExitOK || o2.audiences[0] != "from-env" {
		t.Fatalf("exit %d audiences %v", r.code, o2.audiences)
	}
}

func TestKeyWithoutActionsIsARandomCLIKey(t *testing.T) {
	f := newFakeKD(t)
	f.allowStatic = true
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	env := map[string]string{"KODEPLOY_TOKEN": staticToken, "KODEPLOY_SERVER": f.srv.URL, "KODEPLOY_APP": testApp}
	r := runCLI(t, context.Background(), env, nil, "deploy", "--settle", "0")
	if r.code != ExitOK || f.calls[0].key != "cli-00000000-0000-4000-8000-000000000000" {
		t.Fatalf("exit %d key %q", r.code, f.calls[0].key)
	}
	if !strings.HasPrefix(randomKey(), "cli-") || len(randomKey()) != len("cli-")+36 || randomKey() == randomKey() {
		t.Fatal("cli-<UUID>")
	}
	assertNoSecrets(t, r)
}
