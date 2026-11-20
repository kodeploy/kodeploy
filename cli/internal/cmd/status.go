package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/auth"
)

func newStatusCmd(d *Deps, g *globalFlags) *cobra.Command {
	return &cobra.Command{
		Use:   "status <build_id>",
		Short: "빌드 하나의 상태를 본다",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return exit(runStatus(cmd.Context(), d, g.resolve(cmd, d), args[0]))
		},
	}
}

// statusOut은 status --output json의 객체다.
type statusOut struct {
	BuildID      string     `json:"build_id"`
	Slot         string     `json:"slot"`
	Status       string     `json:"status"`
	SourceSHA    *string    `json:"source_sha"`
	RequestedSHA *string    `json:"requested_sha"`
	Error        *string    `json:"error"`
	AIStatus     *string    `json:"ai_status"`
	CI           *api.CIRun `json:"ci"`
}

func slotOf(b *api.Build) string {
	if b.Runtime == "static" {
		return "static"
	}
	return "server"
}

func runStatus(ctx context.Context, d *Deps, g resolved, buildID string) int {
	r := newReporter(d, g)
	if g.app == "" {
		return r.usage("--app 또는 KODEPLOY_APP이 필요해요")
	}
	if g.outErr != "" {
		return r.usage(g.outErr)
	}
	client, code := r.client(ctx, g)
	if client == nil {
		return code
	}
	b, err := client.GetBuild(ctx, g.app, buildID)
	if err != nil {
		if ctx.Err() != nil {
			return r.finish(resultInterrupted, ExitInterrupted, "중단했어요")
		}
		if ae, ok := auth.IsAuth(err); ok {
			return r.fail(ExitAuth, ae.Error(), ae.Hint)
		}
		if e, ok := api.AsError(err); ok {
			if e.Status == 400 || e.Status == 422 {
				return r.fail(ExitUsage, e.Detail, "")
			}
			return r.fail(ExitAuth, e.Error(), "")
		}
		return r.fail(ExitAuth, "서버에 연결하지 못했어요: "+err.Error(), "")
	}
	out := statusOut{BuildID: b.BuildID, Slot: slotOf(b), Status: b.Status, SourceSHA: b.SourceSHA,
		RequestedSHA: b.RequestedSHA, Error: b.Error, AIStatus: b.AIStatus, CI: b.CI}
	if g.output == "json" {
		j, _ := json.Marshal(out)
		fmt.Fprintln(d.Stdout, r.p.Redact(string(j)))
		return ExitOK
	}
	var sb strings.Builder
	fmt.Fprintf(&sb, "[%s %s] %s", out.Slot, out.BuildID, out.Status)
	if src := api.Str(b.SourceSHA); src != "" {
		fmt.Fprintf(&sb, " source=%s", short(src))
	}
	sb.WriteString("\n")
	if e := api.Str(b.Error); e != "" {
		fmt.Fprintf(&sb, "  %s\n", e)
	}
	if b.CI != nil {
		fmt.Fprintf(&sb, "  GitHub Actions · %s · run %s\n", orDash(b.CI.Actor), orDash(b.CI.RunID))
	}
	fmt.Fprint(d.Stdout, r.p.Redact(sb.String()))
	return ExitOK
}
