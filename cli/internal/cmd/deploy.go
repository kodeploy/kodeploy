package cmd

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/auth"
	"github.com/kodeploy/kodeploy/cli/internal/wait"
)

var shaRE = regexp.MustCompile(`^[0-9a-fA-F]{40}$`)

type deployFlags struct {
	sha     string
	timeout time.Duration
	settle  time.Duration
	noWait  bool
}

func newDeployCmd(d *Deps, g *globalFlags) *cobra.Command {
	var f deployFlags
	c := &cobra.Command{
		Use:   "deploy",
		Short: "지금 설정 그대로 재배포하고 끝날 때까지 기다린다",
		Long: "앱의 라이브 설정 그대로 재배포를 요청하고(POST /apps/{app}/deploy/redeploy), 만든 빌드가 끝날 때까지 기다린 뒤\n" +
			"결과를 exit code로 돌려준다. 시간 초과나 Ctrl+C로 멈춰도 서버 빌드는 계속 진행된다.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return exit(runDeploy(cmd.Context(), d, g.resolve(cmd, d), f))
		},
	}
	c.Flags().StringVar(&f.sha, "sha", "", "배포할 커밋 (40자). 없으면 브랜치 최신, CI 토큰이면 워크플로가 돈 커밋")
	c.Flags().DurationVar(&f.timeout, "timeout", 25*time.Minute, "요청부터 안정화까지 전체 시간 제한")
	c.Flags().DurationVar(&f.settle, "settle", 60*time.Second, "모두 running이 된 뒤 더 지켜볼 시간 (0이면 바로 성공)")
	c.Flags().BoolVar(&f.noWait, "no-wait", false, "빌드 목록만 출력하고 기다리지 않는다")
	return c
}

// idempotencyKey는 한 번 실행하는 동안 같은 이름표다. GitHub Actions면 실행과 재실행(attempt)마다 하나.
func idempotencyKey(d *Deps) string {
	if run := d.Getenv("GITHUB_RUN_ID"); run != "" {
		attempt := d.Getenv("GITHUB_RUN_ATTEMPT")
		if attempt == "" {
			attempt = "1"
		}
		return "gha-" + run + "-" + attempt
	}
	return d.NewKey()
}

func runDeploy(ctx context.Context, d *Deps, g resolved, f deployFlags) int {
	r := newReporter(d, g)
	if g.app == "" {
		return r.usage("--app 또는 KODEPLOY_APP이 필요해요")
	}
	if f.sha != "" && !shaRE.MatchString(f.sha) {
		return r.usage("--sha는 40자 커밋 해시여야 해요")
	}
	if f.timeout <= 0 {
		return r.usage("--timeout은 0보다 커야 해요")
	}
	if f.settle < 0 {
		return r.usage("--settle은 0 이상이어야 해요")
	}
	if g.outErr != "" {
		return r.usage(g.outErr)
	}

	start := d.Clock.Now()
	deadline := start.Add(f.timeout)
	ctx, cancel := d.Clock.WithDeadline(ctx, deadline)
	defer cancel()

	client, code := r.client(ctx, g)
	if client == nil {
		return code
	}
	key := idempotencyKey(d)
	r.res.AppID = g.app
	if f.sha != "" {
		sha := strings.ToLower(f.sha)
		r.res.RequestedSHA = &sha
	}
	r.progress("재배포 요청 app=%s key=%s", g.app, key)

	resp, _, err := client.RedeployWithRetry(ctx, d.Clock, deadline, g.app, key,
		api.RedeployRequest{CommitSHA: strings.ToLower(f.sha)},
		func(reason string, delay time.Duration) {
			r.progress("%s — %s 뒤 같은 이름표로 다시 보내요", reason, delay)
		})
	if err != nil {
		return r.requestFailed(ctx, err, f.timeout)
	}
	if resp.RequestedSHA != nil {
		r.res.RequestedSHA = resp.RequestedSHA
	}
	if resp.Replayed {
		r.progress("같은 이름표로 이미 처리된 요청이에요 — 그 빌드를 이어서 기다려요")
	}
	if f.noWait {
		return r.noWait(resp.Builds)
	}

	opts := wait.Options{
		Clock:    d.Clock,
		Get:      func(ctx context.Context, id string) (*api.Build, error) { return client.GetBuild(ctx, g.app, id) },
		Start:    start,
		Deadline: deadline,
		Settle:   f.settle,
		OnChange: r.buildChanged,
		OnSettle: func(s time.Duration) { r.progress("안정화 확인 %s...", s) },
	}
	for _, b := range resp.Builds {
		r.progress("[%s %s] %s", b.Slot, b.BuildID, b.Status)
	}
	out := wait.Run(ctx, opts, resp.Builds)
	if out.Kind == wait.KindFailed {
		wait.WaitAnalysis(ctx, opts, out.Builds)
	}
	return r.outcome(out, f.timeout)
}

// requestFailed는 재배포 요청 단계의 오류를 exit code로 바꾼다 (계약 3-4, README의 표).
func (r *reporter) requestFailed(ctx context.Context, err error, timeout time.Duration) int {
	switch {
	case errors.Is(err, context.Canceled) && ctx.Err() != nil:
		return r.finish(resultInterrupted, ExitInterrupted, "중단했어요. 빌드는 서버에서 계속 진행됩니다")
	case errors.Is(err, context.DeadlineExceeded), errors.Is(err, api.ErrDeadline):
		return r.finish(resultTimeout, ExitTimeout, fmt.Sprintf("시간 제한(%s) 안에 재배포 요청을 마치지 못했어요", timeout))
	}
	if ae, ok := auth.IsAuth(err); ok {
		if ae.Status == 409 {
			return r.fail(ExitUsage, "라이브 배포가 없어요: "+ae.Detail, "첫 배포는 웹에서 해 주세요")
		}
		return r.fail(ExitAuth, ae.Error(), ae.Hint)
	}
	e, ok := api.AsError(err)
	if !ok {
		return r.fail(ExitAuth, "서버에 연결하지 못했어요: "+err.Error(), "")
	}
	switch {
	case e.Status == 409:
		switch e.Code {
		case api.CodeNoLiveDeploy:
			return r.fail(ExitUsage, e.Detail, "첫 배포는 웹에서 해 주세요")
		case api.CodeKeyInProgress, api.CodeKeyReleased:
			return r.fail(ExitAuth, "같은 이름표의 요청이 30초가 지나도 끝나지 않았어요: "+e.Detail, "")
		default:
			return r.fail(ExitUsage, e.Detail, "")
		}
	case e.Status == 400 || e.Status == 422:
		return r.fail(ExitUsage, e.Detail, "")
	case e.Status == 401:
		return r.fail(ExitAuth, "인증이 거절됐어요: "+e.Detail, "")
	case e.Status == 403 || e.Status == 404:
		return r.fail(ExitAuth, e.Detail, "")
	default:
		return r.fail(ExitAuth, "재배포 요청이 실패했어요: "+e.Error(), "")
	}
}

func (r *reporter) outcome(out wait.Outcome, timeout time.Duration) int {
	r.setBuilds(out.Builds)
	switch out.Kind {
	case wait.KindSuccess:
		return r.finish(resultSuccess, ExitOK, "성공 "+r.sourceSummary(out.Builds))
	case wait.KindFailed:
		return r.finish(resultFailed, ExitFailed, "배포 실패 "+r.failureSummary(out.Builds))
	case wait.KindSuperseded:
		return r.finish(resultSuperseded, ExitSuperseded, "다른 배포에 밀렸어요 "+r.cancelSummary(out.Builds))
	case wait.KindTimeout:
		return r.finish(resultTimeout, ExitTimeout, fmt.Sprintf("시간 제한(%s)을 넘겼어요. 빌드는 서버에서 계속 진행됩니다", timeout))
	case wait.KindInterrupted:
		return r.finish(resultInterrupted, ExitInterrupted, "중단했어요. 빌드는 서버에서 계속 진행됩니다")
	default:
		if ae, ok := auth.IsAuth(out.Err); ok {
			return r.fail(ExitAuth, ae.Error(), ae.Hint)
		}
		return r.fail(ExitAuth, out.Err.Error(), "")
	}
}

func short(sha string) string {
	if len(sha) > 7 {
		return sha[:7]
	}
	return sha
}
