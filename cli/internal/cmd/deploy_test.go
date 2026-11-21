package cmd

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func happyBuilds(f *fakeKD) {
	f.builds["3f9a2c1d"] = []any{"queued", "building", "built", "deploying", "running"}
	f.builds["8e7d6c5b"] = []any{"queued", "building", "deploying", "running"}
}

func TestDeployHappyPathWithOIDC(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitOK {
		t.Fatalf("exit %d\n%s\n%s", r.code, r.stdout, r.stderr)
	}
	if len(f.calls) != 1 || f.calls[0].key != "gha-98765-1" || f.calls[0].body != "{}" ||
		f.calls[0].auth != "Bearer kdci_ci-token-1-SECRET" {
		t.Fatalf("calls %+v", f.calls)
	}
	if len(o.audiences) != 1 || o.audiences[0] != "kodeploy" {
		t.Fatalf("audiences %v", o.audiences)
	}
	if !strings.Contains(r.stdout, "성공 source=3f9a1c2") {
		t.Fatalf("stdout %q", r.stdout)
	}
	for _, want := range []string{
		"kodeploy: 재배포 요청 app=" + testApp + " key=gha-98765-1",
		"[server 3f9a2c1d] queued → building → built → deploying → running (",
		"[static 8e7d6c5b] queued → building → deploying → running (",
		"kodeploy: 안정화 확인 1m0s...",
	} {
		if !strings.Contains(r.stderr, want) {
			t.Fatalf("stderr에 %q가 없다\n%s", want, r.stderr)
		}
	}
	// 안정화 60초를 지켜봤다
	if got := r.clk.Now().Sub(t0); got < 60*time.Second {
		t.Fatalf("elapsed %v", got)
	}
	assertNoSecrets(t, r)
}

func TestReplayedResponseKeepsWaitingOnThoseBuilds(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(200, true)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--settle", "0")
	if r.code != ExitOK || !strings.Contains(r.stderr, "같은 이름표로 이미 처리된 요청") {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
}

func TestKeyInProgressThenSuccess(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{errBody(409, "key_in_progress", "처리 중"), errBody(409, "key_in_progress", "처리 중"), redeployOK(200, true)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--settle", "0")
	if r.code != ExitOK || len(f.calls) != 3 {
		t.Fatalf("exit %d calls %d\n%s", r.code, len(f.calls), r.stderr)
	}
	for _, c := range f.calls {
		if c.key != "gha-98765-1" {
			t.Fatalf("다른 이름표로 보냈다: %+v", f.calls)
		}
	}
	if s := r.clk.Sleeps(); s[0] != 2*time.Second || s[1] != 4*time.Second {
		t.Fatalf("sleeps %v", s)
	}
}

func TestNoLiveDeployIsUsageWithAHint(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{errBody(409, "no_live_deploy", "성공한 배포가 없어요. 첫 배포는 설정 변경(배포 폼)에서 해 주세요")}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitUsage || !strings.Contains(r.stderr, "첫 배포는 웹에서") || !strings.Contains(r.stderr, "성공한 배포가 없어요") {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
	if len(f.calls) != 1 {
		t.Fatal("다시 보내지 않는다")
	}
}

// 토큰과 다른 칸에 떠 있는 커밋 기록이 없다 (계약 9-5) — 다시 보내도 같으니 한 번만 보내고 2로 끝낸다.
func TestSlotCommitUnknownIsUsageAndNotRetried(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{errBody(409, "slot_commit_unknown", "이 칸의 배포 커밋 기록이 없어요. 웹에서 배포하기를 한 번 한 뒤 다시 시도해 주세요")}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--output", "json")
	if r.code != ExitUsage || len(f.calls) != 1 {
		t.Fatalf("exit %d calls %d\n%s\n%s", r.code, len(f.calls), r.stdout, r.stderr)
	}
	var out struct {
		Result   string `json:"result"`
		ExitCode int    `json:"exit_code"`
		Message  string `json:"message"`
	}
	if err := json.Unmarshal([]byte(r.stdout), &out); err != nil || out.ExitCode != ExitUsage || !strings.Contains(out.Message, "웹에서 배포하기") {
		t.Fatalf("json %q (%v)", r.stdout, err)
	}
}

func TestServerErrorRetriesWithTheSameKey(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{{502, "bad gateway"}, {503, `{"detail":"down"}`}, redeployOK(202, false)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--settle", "0")
	if r.code != ExitOK || len(f.calls) != 3 || f.calls[0].key != f.calls[2].key {
		t.Fatalf("exit %d calls %+v\n%s", r.code, f.calls, r.stderr)
	}
}

func TestUnauthorizedReexchangesWithANewIDToken(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.tokenUses = 3 // 토큰 하나로 요청 3번 — 그다음은 만료
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--settle", "0")
	if r.code != ExitOK {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
	if len(f.idTokens) < 2 || f.idTokens[0] == f.idTokens[1] {
		t.Fatalf("새 ID 토큰으로 다시 교환해야 한다: %v", f.idTokens)
	}
	assertNoSecrets(t, r)
}

func TestForbiddenShowsTheDetail(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{errBody(403, "branch_mismatch", "브랜치가 이 앱의 배포 브랜치와 달라요")}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "브랜치가 이 앱의 배포 브랜치와 달라요") {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
}

func TestStatusFailingTenTimesInARowIsAConnectionError(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	f.builds["3f9a2c1d"] = []any{reply{503, `{"detail":"down"}`}}
	f.builds["8e7d6c5b"] = []any{"running"}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "연속 10번") || f.gets["3f9a2c1d"] != 10 {
		t.Fatalf("exit %d gets %d\n%s", r.code, f.gets["3f9a2c1d"], r.stderr)
	}
}

func TestFailureDuringSettleExitsOne(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	f.builds["3f9a2c1d"] = []any{"building", "running", "running", "running",
		map[string]any{"status": "failed", "error": "health: 앱이 재시작됐어요 (OOMKilled)"}}
	f.builds["8e7d6c5b"] = []any{"running"}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitFailed || !strings.Contains(r.stdout, "OOMKilled") {
		t.Fatalf("exit %d\n%s\n%s", r.code, r.stdout, r.stderr)
	}
}

func TestTimeoutDoesNotTouchTheServerBuild(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	f.builds["3f9a2c1d"] = []any{"building"}
	f.builds["8e7d6c5b"] = []any{"building"}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--timeout", "20s")
	if r.code != ExitTimeout || !strings.Contains(r.stdout, "서버에서 계속 진행") {
		t.Fatalf("exit %d\n%s", r.code, r.stdout)
	}
	if got := r.clk.Now().Sub(t0); got != 20*time.Second {
		t.Fatalf("elapsed %v", got)
	}
}

func TestInterruptExits130(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	f.builds["3f9a2c1d"] = []any{"building"}
	f.builds["8e7d6c5b"] = []any{"building"}
	ctx, cancel := context.WithCancel(context.Background())
	f.onGet = func(id string, n int) {
		if n == 2 {
			cancel() // Ctrl+C
		}
	}
	r := runCLI(t, ctx, actionsEnv(f, o), nil, "deploy")
	if r.code != ExitInterrupted || !strings.Contains(r.stdout, "빌드는 서버에서 계속 진행됩니다") {
		t.Fatalf("exit %d\n%s\n%s", r.code, r.stdout, r.stderr)
	}
}

func TestAIAnalysisIsSummarized(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	analysis := `{"title":"gradlew 실행 권한을 확인해 주세요.","cause":"gradlew에 실행 권한이 없어요","evidence":"Permission denied",` +
		`"cause_category":"user_build_error","kodeploy_specific":false,"fix_steps":[{"text":"실행 권한을 더하세요","command":"git update-index --chmod=+x gradlew"}],` +
		`"alternative_label":"","alternative_steps":[]}`
	f.builds["3f9a2c1d"] = []any{
		"building",
		map[string]any{"status": "failed", "error": "build: exit 126", "ai_status": "pending"},
		map[string]any{"status": "failed", "error": "build: exit 126", "ai_status": "pending"},
		map[string]any{"status": "failed", "error": "build: exit 126", "ai_status": "done", "ai_analysis": analysis},
	}
	f.builds["8e7d6c5b"] = []any{"running"}
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy")
	if r.code != ExitFailed {
		t.Fatalf("exit %d", r.code)
	}
	for _, want := range []string{"build: exit 126", "AI 진단: gradlew 실행 권한을 확인해 주세요.", "원인: gradlew에 실행 권한이 없어요",
		"1. 실행 권한을 더하세요", "git update-index --chmod=+x gradlew"} {
		if !strings.Contains(r.stdout, want) {
			t.Fatalf("stdout에 %q가 없다\n%s", want, r.stdout)
		}
	}
}

func TestStaticTokenIsNotReexchanged(t *testing.T) {
	f := newFakeKD(t)
	f.allowStatic = false // 서버가 받지 않는다 = 만료
	env := map[string]string{"KODEPLOY_TOKEN": staticToken, "KODEPLOY_SERVER": f.srv.URL, "KODEPLOY_APP": testApp}
	f.redeploy = []reply{redeployOK(202, false)}
	r := runCLI(t, context.Background(), env, nil, "deploy")
	if r.code != ExitAuth || !strings.Contains(r.stderr, "KODEPLOY_TOKEN이 만료") || len(f.idTokens) != 0 {
		t.Fatalf("exit %d\n%s", r.code, r.stderr)
	}
	if len(f.authSeen) != 1 {
		t.Fatalf("KODEPLOY_TOKEN이면 401 뒤 다시 보내지 않는다: %d", len(f.authSeen))
	}
	assertNoSecrets(t, r)
}

func TestShaFlagIsSentLowercase(t *testing.T) {
	f, o := newFakeKD(t), newFakeOIDC(t)
	f.redeploy = []reply{redeployOK(202, false)}
	happyBuilds(f)
	r := runCLI(t, context.Background(), actionsEnv(f, o), nil, "deploy", "--sha", strings.ToUpper(testSHA), "--settle", "0")
	if r.code != ExitOK || f.calls[0].body != `{"commit_sha":"`+testSHA+`"}` {
		t.Fatalf("exit %d body %s", r.code, f.calls[0].body)
	}
}
