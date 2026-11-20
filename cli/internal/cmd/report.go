package cmd

import (
	"context"
	"fmt"
	"strings"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/auth"
	"github.com/kodeploy/kodeploy/cli/internal/output"
	"github.com/kodeploy/kodeploy/cli/internal/wait"
)

const (
	resultSuccess     = "success"
	resultFailed      = "failed"
	resultSuperseded  = "superseded"
	resultTimeout     = "timeout"
	resultError       = "error"
	resultInterrupted = "interrupted"
	resultRequested   = "requested" // --no-wait
)

const (
	defaultServer   = "https://api.kodeploy.com"
	defaultAudience = "kodeploy"
)

// 여러 명령이 같이 쓰는 설정. 플래그가 환경변수보다 우선한다.
type globalFlags struct {
	server, app, audience, output string
}

type resolved struct {
	server, app, audience, output string
	outErr                        string // --output 값이 잘못됐으면 그 이유
	debug                         bool
}

func (g *globalFlags) register(root *cobra.Command) {
	root.PersistentFlags().StringVar(&g.server, "server", "", "KoDeploy API 주소 (KODEPLOY_SERVER, 기본 "+defaultServer+")")
	root.PersistentFlags().StringVar(&g.app, "app", "", "앱 id (KODEPLOY_APP)")
	root.PersistentFlags().StringVar(&g.audience, "audience", "", "GitHub OIDC audience (KODEPLOY_AUDIENCE, 기본 "+defaultAudience+")")
	root.PersistentFlags().StringVar(&g.output, "output", "text", "결과 형식 text|json")
}

func (g *globalFlags) resolve(cmd *cobra.Command, d *Deps) resolved {
	pick := func(flag, value, env, def string) string {
		if cmd.Flags().Changed(flag) {
			return value
		}
		if v := strings.TrimSpace(d.Getenv(env)); v != "" {
			return v
		}
		return def
	}
	r := resolved{
		server:   strings.TrimRight(pick("server", g.server, "KODEPLOY_SERVER", defaultServer), "/"),
		app:      strings.TrimSpace(pick("app", g.app, "KODEPLOY_APP", "")),
		audience: pick("audience", g.audience, "KODEPLOY_AUDIENCE", defaultAudience),
		output:   g.output,
		debug:    d.Getenv("KODEPLOY_DEBUG") != "",
	}
	if r.output != "text" && r.output != "json" {
		r.outErr = "--output은 text나 json이어야 해요"
		r.output = "text"
	}
	return r
}

// reporter는 한 번 실행의 출력과 최종 결과를 모은다.
type reporter struct {
	d   *Deps
	g   resolved
	p   *output.Printer
	res output.Report
}

func newReporter(d *Deps, g resolved) *reporter {
	return &reporter{d: d, g: g, p: output.New(d.Stdout, d.Stderr, d.Getenv, g.output == "json"), res: output.Report{Builds: []output.Build{}}}
}

func (r *reporter) progress(format string, args ...any) { r.p.Progress(format, args...) }

func (r *reporter) buildChanged(b *wait.Build) {
	r.p.BuildLine(b.Ref.Slot, b.Ref.BuildID, b.History, b.Elapsed)
}

// client는 인증을 정하고 API 클라이언트를 만든다. 인증 정보가 없으면 (nil, 125).
func (r *reporter) client(_ context.Context, g resolved) (*api.Client, int) {
	c := &api.Client{BaseURL: g.server, HTTP: r.d.HTTP, UserAgent: "kodeploy-cli/" + version}
	if g.debug {
		c.Debug = r.p.Debug
	}
	src, err := auth.New(auth.Config{
		Getenv: r.d.Getenv, Audience: g.audience, App: g.app, Exchange: c, HTTP: r.d.HTTP, OnSecret: r.p.AddSecret,
	})
	if err != nil {
		ae, _ := auth.IsAuth(err)
		return nil, r.fail(ExitAuth, ae.Msg, ae.Hint)
	}
	c.Auth = src
	return c, 0
}

func (r *reporter) usage(msg string) int {
	return r.fail(ExitUsage, msg, "")
}

// fail은 배포 결과가 아닌 오류(사용법·인증·연결)로 끝낸다.
func (r *reporter) fail(code int, msg, hint string) int {
	r.res.Result, r.res.ExitCode, r.res.Message, r.res.Hint = resultError, code, msg, hint
	r.emit()
	return code
}

func (r *reporter) finish(result string, code int, msg string) int {
	r.res.Result, r.res.ExitCode, r.res.Message = result, code, msg
	r.emit()
	return code
}

func (r *reporter) emit() {
	if r.res.AppID == "" {
		r.res.AppID = r.g.app
	}
	r.p.Result(r.res)
}

func (r *reporter) noWait(refs []api.BuildRef) int {
	for _, b := range refs {
		r.res.Builds = append(r.res.Builds, output.Build{BuildID: b.BuildID, Slot: b.Slot, Status: b.Status})
		r.progress("[%s %s] %s ref=%s", b.Slot, b.BuildID, b.Status, b.Ref)
	}
	return r.finish(resultRequested, ExitOK, "요청함 빌드는 서버에서 진행됩니다 (기다리지 않음)")
}

func (r *reporter) setBuilds(builds []*wait.Build) {
	r.res.Builds = r.res.Builds[:0]
	for _, b := range builds {
		ob := output.Build{BuildID: b.Ref.BuildID, Slot: b.Ref.Slot, Status: b.Status, Elapsed: b.Elapsed}
		if b.Last != nil {
			ob.SourceSHA, ob.Error = b.Last.SourceSHA, b.Last.Error
			if b.Status == api.StatusFailed && b.Last.AIAnalysis != nil {
				r.res.Analysis = append(r.res.Analysis, output.SummarizeAnalysis(*b.Last.AIAnalysis)...)
			}
		}
		r.res.Builds = append(r.res.Builds, ob)
	}
}

// 성공 줄의 커밋 — 슬롯이 모두 같은 커밋이면 source=<7자>, 다르면 슬롯별.
func (r *reporter) sourceSummary(builds []*wait.Build) string {
	shas := map[string]bool{}
	var parts []string
	for _, b := range builds {
		sha := ""
		if b.Last != nil {
			sha = api.Str(b.Last.SourceSHA)
		}
		shas[sha] = true
		parts = append(parts, fmt.Sprintf("%s=%s", b.Ref.Slot, orDash(short(sha))))
	}
	if len(shas) == 1 {
		for sha := range shas {
			return "source=" + orDash(short(sha))
		}
	}
	return strings.Join(parts, " ")
}

func (r *reporter) failureSummary(builds []*wait.Build) string {
	var parts []string
	for _, b := range builds {
		if b.Status == api.StatusFailed {
			parts = append(parts, fmt.Sprintf("[%s %s]", b.Ref.Slot, b.Ref.BuildID))
		}
	}
	return strings.Join(parts, " ")
}

func (r *reporter) cancelSummary(builds []*wait.Build) string {
	var parts []string
	for _, b := range builds {
		if b.Status == api.StatusCancelled {
			parts = append(parts, fmt.Sprintf("[%s %s] cancelled", b.Ref.Slot, b.Ref.BuildID))
		}
	}
	return strings.Join(parts, " ")
}

func orDash(s string) string {
	if s == "" {
		return "-"
	}
	return s
}
