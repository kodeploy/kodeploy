package cmd

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/auth"
)

func newCommitsCmd(d *Deps, g *globalFlags) *cobra.Command {
	return &cobra.Command{
		Use:   "commits",
		Short: "배포할 수 있는 최근 커밋 3개와 지금 떠 있는 커밋을 본다",
		Long: "웹의 배포하기에서 고르는 것과 같은 목록이다 (GET /apps/{app}/deploy/redeploy/commits).\n" +
			"고른 커밋은 kodeploy deploy --sha <40자>로 배포한다. 터미널이면 kodeploy deploy --pick으로 바로 고를 수 있다.",
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return exit(runCommits(cmd.Context(), d, g.resolve(cmd, d)))
		},
	}
}

func runCommits(ctx context.Context, d *Deps, g resolved) int {
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
	res, err := client.Commits(ctx, g.app)
	if err != nil {
		return r.commitsFailed(ctx, err)
	}
	if g.output == "json" {
		j, _ := json.Marshal(res)
		fmt.Fprintln(d.Stdout, r.p.Redact(string(j)))
		return ExitOK
	}
	fmt.Fprint(d.Stdout, r.p.Redact(formatCommits(res, false)))
	return ExitOK
}

// commitsFailed는 커밋 조회 오류를 exit code로 바꾼다. 409(라이브 배포 없음)와 400은 사용법, 나머지는 인증·연결.
func (r *reporter) commitsFailed(ctx context.Context, err error) int {
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
	case e.Status == 409:
		return r.fail(ExitUsage, e.Detail, "첫 배포는 웹에서 해 주세요")
	case e.Status == 400 || e.Status == 422:
		return r.fail(ExitUsage, e.Detail, "")
	case e.Status == 401:
		return r.fail(ExitAuth, "인증이 거절됐어요: "+e.Detail, "")
	case e.Status == 403 || e.Status == 404:
		return r.fail(ExitAuth, e.Detail, "")
	default:
		return r.fail(ExitAuth, "커밋 목록을 받지 못했어요: "+e.Error(), "")
	}
}

var githubRepoRE = regexp.MustCompile(`github\.com[/:]([^/]+/[^/]+?)(?:\.git)?/?$`)

func repoSlug(url string) string {
	if m := githubRepoRE.FindStringSubmatch(url); m != nil {
		return m[1]
	}
	return url
}

// 커밋 한 줄: "d2b166d  11-21 14:03  메시지 첫 줄". 날짜는 이 컴퓨터의 시간대로.
func commitLine(c api.Commit) string {
	when := ""
	if c.Date != nil {
		if t, err := time.Parse(time.RFC3339, *c.Date); err == nil {
			when = t.Local().Format("2006-01-02 15:04")
		}
	}
	msg, _, _ := strings.Cut(strings.TrimSpace(c.Message), "\n")
	if utf8.RuneCountInString(msg) > 60 {
		msg = string([]rune(msg)[:59]) + "…"
	}
	parts := []string{short(c.SHA)}
	if when != "" {
		parts = append(parts, when)
	}
	if msg != "" {
		parts = append(parts, msg)
	}
	return strings.Join(parts, "  ")
}

// formatCommits는 목록을 사람이 읽는 꼴로. numbered면 고르기용 번호를 붙이고 1번이 "브랜치 최신"이다.
func formatCommits(res *api.CommitsResponse, numbered bool) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s · %s\n", repoSlug(res.Repo), res.Branch)
	current := api.Str(res.CurrentSHA)
	if len(res.Commits) == 0 {
		b.WriteString("  최근 커밋을 불러오지 못했어요 (GitHub 조회 실패)\n")
		if numbered {
			b.WriteString("  1) 브랜치 최신\n")
		}
	}
	for i, c := range res.Commits {
		line := commitLine(c)
		var marks []string
		if i == 0 {
			marks = append(marks, "최신")
		}
		if c.SHA == current {
			marks = append(marks, "지금 떠 있음")
		}
		if len(marks) > 0 {
			line += "  (" + strings.Join(marks, ", ") + ")"
		}
		if numbered {
			fmt.Fprintf(&b, "  %d) %s\n", i+1, line)
		} else {
			fmt.Fprintf(&b, "  %s\n", line)
		}
	}
	switch {
	case current == "":
		b.WriteString("지금 떠 있는 커밋: 기록 없음\n")
	case res.Current != nil:
		fmt.Fprintf(&b, "지금 떠 있는 커밋: %s (최근 3개 밖)\n", commitLine(*res.Current))
	}
	return b.String()
}

// errPickAborted는 고르는 중에 입력이 끝났거나(Ctrl+D) 중단된 것이다.
var errPickAborted = errors.New("커밋을 고르지 않고 끝냈어요")

const pickTries = 3

// pickCommit은 최근 커밋 중 하나를 고르게 묻는다. 1번(최신)을 고르면 ""를 돌려준다 — 웹 배포하기처럼 커밋을 지정하지 않고
// 브랜치 최신으로 보낸다 (같은 저장소의 다른 브랜치 정적 칸이 서버 커밋으로 고정되지 않게).
func pickCommit(ctx context.Context, in *bufio.Reader, w io.Writer, res *api.CommitsResponse) (string, error) {
	fmt.Fprint(w, formatCommits(res, true))
	n := max(len(res.Commits), 1)
	for try := 0; try < pickTries; try++ {
		fmt.Fprintf(w, "배포할 커밋 번호 [1-%d, 엔터=1]: ", n)
		line, err := readLine(ctx, in)
		if err != nil {
			fmt.Fprintln(w)
			return "", err
		}
		line = strings.TrimSpace(line)
		choice := 1
		if line != "" {
			v, convErr := strconv.Atoi(line)
			if convErr != nil || v < 1 || v > n {
				fmt.Fprintf(w, "1부터 %d 사이의 번호를 넣어 주세요\n", n)
				continue
			}
			choice = v
		}
		if choice == 1 {
			if len(res.Commits) > 0 {
				fmt.Fprintf(w, "브랜치 최신(%s)으로 배포해요\n", short(res.Commits[0].SHA))
			} else {
				fmt.Fprintln(w, "브랜치 최신으로 배포해요")
			}
			return "", nil
		}
		sha := strings.ToLower(res.Commits[choice-1].SHA)
		fmt.Fprintf(w, "%s로 배포해요\n", short(sha))
		return sha, nil
	}
	return "", fmt.Errorf("번호를 %d번 잘못 넣었어요", pickTries)
}

// readLine은 한 줄을 읽는다. ctx가 끝나면(Ctrl+C) 기다리지 않고 돌아온다 — 읽던 고루틴은 프로세스가 끝날 때 같이 끝난다.
func readLine(ctx context.Context, in *bufio.Reader) (string, error) {
	type result struct {
		line string
		err  error
	}
	ch := make(chan result, 1)
	go func() {
		line, err := in.ReadString('\n')
		ch <- result{line, err}
	}()
	select {
	case <-ctx.Done():
		return "", ctx.Err()
	case res := <-ch:
		if res.err != nil && (res.line == "" || !errors.Is(res.err, io.EOF)) {
			return "", errPickAborted
		}
		return res.line, nil
	}
}
