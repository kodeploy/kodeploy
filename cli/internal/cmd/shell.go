package cmd

import (
	"context"
	"errors"
	"fmt"
	"net/url"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/auth"
	"github.com/kodeploy/kodeploy/cli/internal/shell"
)

// 웹 터미널과 같은 세 대상
type execTarget struct {
	use, target, label, short string
}

var execTargets = []execTarget{
	{"shell", "app", "앱 셸", "앱 컨테이너 셸을 연다"},
	{"db", "db", "DB 콘솔", "앱 DB 콘솔(mysql·psql)을 연다"},
	{"redis", "redis", "Redis 콘솔", "앱 Redis 콘솔(redis-cli)을 연다"},
}

func newExecCmds(d *Deps, g *globalFlags) []*cobra.Command {
	var cmds []*cobra.Command
	for _, t := range execTargets {
		cmds = append(cmds, &cobra.Command{
			Use:   t.use,
			Short: t.short + " (터미널에서만)",
			Long: t.short + ". 웹 터미널과 같은 세션이다 — core에서 1회용 티켓을 받아 그 앱의 kodeploy-exec에 붙는다.\n" +
				"웹에서 발급한 CLI 토큰(KODEPLOY_TOKEN=kdcli_…)이 필요하다. 끝나면 원격 명령의 종료 코드로 끝난다.",
			Args: cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				return exit(runExec(cmd.Context(), d, g.resolve(cmd, d), t))
			},
		})
	}
	return cmds
}

func runExec(ctx context.Context, d *Deps, g resolved, t execTarget) int {
	r := newReporter(d, g)
	if g.app == "" {
		return r.usage("--app 또는 KODEPLOY_APP이 필요해요")
	}
	if g.outErr != "" {
		return r.usage(g.outErr)
	}
	if d.Terminal == nil || !d.Terminal.IsTerminal() || d.Stdin == nil {
		return r.usage(fmt.Sprintf("%s은 터미널에서만 열 수 있어요 (stdin과 stdout이 터미널이어야 해요)", t.label))
	}
	client, code := r.client(ctx, g)
	if client == nil {
		return code
	}
	size := d.Terminal.Size()
	tk, err := client.ExecTicket(ctx, g.app, api.ExecTicketRequest{Target: t.target, Cols: size.Cols, Rows: size.Rows})
	if err != nil {
		return r.ticketFailed(ctx, err)
	}
	r.p.AddSecret(tk.Ticket)
	if u, err := url.Parse(tk.URL); err != nil || (u.Scheme != "wss" && u.Scheme != "ws") {
		return r.fail(ExitAuth, "서버가 준 접속 주소가 WebSocket 주소가 아니에요", "")
	}
	r.progress("%s에 연결해요 — 끝내려면 exit", t.label)

	restore, err := d.Terminal.MakeRaw()
	if err != nil {
		return r.fail(ExitUsage, "터미널을 raw 모드로 바꾸지 못했어요: "+err.Error(), "")
	}
	defer restore() // 패닉이 나도 되돌린다 (아래에서 먼저 되돌리면 두 번째는 아무것도 안 한다)
	rctx, cancel := context.WithCancel(ctx)
	defer cancel()
	res, err := shell.Run(rctx, shell.Options{
		URL: tk.URL, Ticket: tk.Ticket, Protocol: tk.Protocol,
		Stdin: d.Stdin, Stdout: d.Stdout, Stderr: d.Stderr,
		Size: size, Resize: d.Terminal.Resized(rctx),
	})
	restore() // 아래 안내 줄은 원래 모드에서 쓴다
	return r.sessionEnded(ctx, res, err)
}

// ticketFailed는 티켓 발급 오류를 exit code로 바꾼다. 400·422는 사용법, 나머지(인증·권한·대상 없음·연결)는 125.
func (r *reporter) ticketFailed(ctx context.Context, err error) int {
	if ctx.Err() != nil {
		return r.finish(resultInterrupted, ExitInterrupted, "중단했어요")
	}
	if ae, ok := auth.IsAuth(err); ok {
		return r.fail(ExitAuth, ae.Error(), ae.Hint)
	}
	e, ok := api.AsError(err)
	if !ok {
		return r.fail(ExitAuth, "서버에 연결하지 못했어요: "+err.Error(), "")
	}
	switch {
	case e.Status == 400 || e.Status == 422:
		return r.fail(ExitUsage, e.Detail, "")
	case e.Status == 401:
		return r.fail(ExitAuth, "인증이 거절됐어요: "+e.Detail, "")
	default:
		return r.fail(ExitAuth, e.Detail, "")
	}
}

// 게이트웨이가 종료 상태 없이 닫은 이유 (web/src/lib/terminalSocket.js의 CLOSE_TEXT와 같은 말)
var closeText = map[int]string{
	1003: "프로토콜 오류",
	1011: "exec를 시작하지 못했어요",
	4400: "입력이 너무 길어요",
	4401: "티켓이 만료됐거나 이미 쓰였어요 — 다시 실행해 주세요",
	4403: "허용되지 않은 접근이에요",
	4404: "대상 Pod가 없어요 — 실행 중인지 확인해 주세요",
	4408: "오래 입력이 없어 연결을 닫았어요",
	4413: "최대 연결 시간이 지났어요",
	4429: "동시에 열 수 있는 터미널 수를 넘었어요",
	4503: "exec 서비스가 재시작 중이에요 — 잠시 뒤 다시 열어 주세요",
}

// 접속·권한 쪽 닫기는 125, 세션이 돌다 끊긴 것은 1
var closeAuth = map[int]bool{4401: true, 4403: true, 4404: true, 4429: true, 4503: true}

// sessionEnded는 원격 명령의 종료 코드를 그대로 돌려준다 (채널 3). 상태 없이 끝났으면 닫기 코드로 정한다.
func (r *reporter) sessionEnded(ctx context.Context, res shell.Result, err error) int {
	if err != nil {
		var de *shell.DialError
		switch {
		case errors.Is(err, context.Canceled) && ctx.Err() != nil:
			return r.finish(resultInterrupted, ExitInterrupted, "중단했어요")
		case errors.As(err, &de):
			return r.fail(ExitAuth, de.Error(), "")
		default:
			return r.fail(ExitFailed, err.Error(), "")
		}
	}
	if res.ExitCode >= 0 {
		return res.ExitCode
	}
	if res.CloseCode == 1000 {
		return ExitOK
	}
	msg := closeText[res.CloseCode]
	if msg == "" {
		msg = fmt.Sprintf("연결이 끊겼어요 (close %d)", res.CloseCode)
	}
	if closeAuth[res.CloseCode] {
		return r.fail(ExitAuth, msg, "")
	}
	return r.fail(ExitFailed, msg, "")
}
