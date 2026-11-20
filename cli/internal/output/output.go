// Package output은 진행 상황(stderr)과 최종 결과(stdout)를 쓴다.
// 토큰 원문은 어떤 출력에도 남기지 않는다 — AddSecret으로 받은 값은 쓰기 직전에 ***로 바꾼다.
package output

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"
)

// Build는 최종 결과의 빌드 한 줄이다 (--output json의 builds 항목).
type Build struct {
	BuildID   string        `json:"build_id"`
	Slot      string        `json:"slot"`
	Status    string        `json:"status"`
	SourceSHA *string       `json:"source_sha"`
	Error     *string       `json:"error"`
	Elapsed   time.Duration `json:"-"` // 끝난 시각까지 걸린 시간 (진행 중이면 0)
}

// Report는 최종 결과다. Result는 success|failed|superseded|timeout|error|interrupted|requested(--no-wait).
type Report struct {
	Result       string   `json:"result"`
	ExitCode     int      `json:"exit_code"`
	AppID        string   `json:"app_id"`
	RequestedSHA *string  `json:"requested_sha"`
	Builds       []Build  `json:"builds"`
	Message      string   `json:"message,omitempty"`
	Hint         string   `json:"hint,omitempty"`
	Analysis     []string `json:"-"` // 실패 빌드의 AI 진단 요약 (text 출력에만)
}

type Printer struct {
	Out   io.Writer // 최종 결과
	Err   io.Writer // 진행 상황
	Color bool
	JSON  bool // --output json: stdout에는 결과 객체 하나만
	// GitHub Actions 안이면(GITHUB_ACTIONS=true) 실패에 ::error 한 줄, SummaryPath($GITHUB_STEP_SUMMARY)에 결과 표
	GHA         bool
	SummaryPath string

	mu      sync.Mutex
	secrets []string
}

// New는 터미널이 아니거나 NO_COLOR가 있으면 색을 쓰지 않는다.
func New(out, errw io.Writer, getenv func(string) string, jsonOut bool) *Printer {
	return &Printer{
		Out: out, Err: errw, JSON: jsonOut,
		Color:       getenv("NO_COLOR") == "" && isTerminal(errw),
		GHA:         getenv("GITHUB_ACTIONS") == "true",
		SummaryPath: getenv("GITHUB_STEP_SUMMARY"),
	}
}

func isTerminal(w io.Writer) bool {
	f, ok := w.(*os.File)
	if !ok {
		return false
	}
	st, err := f.Stat()
	return err == nil && st.Mode()&os.ModeCharDevice != 0
}

// AddSecret은 출력에서 가릴 값을 더한다 (토큰 원문).
func (p *Printer) AddSecret(s string) {
	if len(s) < 4 {
		return
	}
	p.mu.Lock()
	p.secrets = append(p.secrets, s)
	p.mu.Unlock()
}

func (p *Printer) Redact(s string) string {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, sec := range p.secrets {
		s = strings.ReplaceAll(s, sec, "***")
	}
	return s
}

func (p *Printer) write(w io.Writer, s string) {
	_, _ = io.WriteString(w, p.Redact(s))
}

func (p *Printer) paint(code, s string) string {
	if !p.Color {
		return s
	}
	return "\x1b[" + code + "m" + s + "\x1b[0m"
}

// Progress는 "kodeploy: ..." 한 줄을 stderr에 쓴다.
func (p *Printer) Progress(format string, args ...any) {
	p.write(p.Err, "kodeploy: "+fmt.Sprintf(format, args...)+"\n")
}

// Debug는 KODEPLOY_DEBUG일 때의 요청 줄이다.
func (p *Printer) Debug(format string, args ...any) {
	p.write(p.Err, "kodeploy: debug: "+fmt.Sprintf(format, args...)+"\n")
}

// BuildLine은 빌드의 상태가 바뀔 때 한 줄: "[server 3f9a2c1d] queued → building → running (6m12s)".
func (p *Printer) BuildLine(slot, id string, history []string, elapsed time.Duration) {
	line := fmt.Sprintf("[%s %s] %s", slot, id, strings.Join(history, " → "))
	if elapsed > 0 {
		line += fmt.Sprintf(" (%s)", Duration(elapsed))
	}
	p.write(p.Err, line+"\n")
}

// Duration은 6m12s처럼 초 단위로 줄인다.
func Duration(d time.Duration) string {
	return d.Round(time.Second).String()
}

// Result는 최종 결과를 쓴다.
//   - text: 배포 결과는 stdout, 오류(사용법·인증·연결)는 stderr
//   - json: stdout에 결과 객체 하나 (오류 문구는 stderr에도)
//   - GitHub Actions: 0이 아니면 ::error 한 줄, step summary에 표
func (p *Printer) Result(r Report) {
	if p.JSON {
		if r.Builds == nil {
			r.Builds = []Build{}
		}
		b, _ := json.Marshal(r)
		p.write(p.Out, string(b)+"\n")
		if r.Result == "error" {
			p.Progress("%s", r.Message)
			if r.Hint != "" {
				p.Progress("%s", r.Hint)
			}
		}
	} else {
		p.text(r)
	}
	if p.GHA {
		if r.ExitCode != 0 {
			w := p.Out
			if p.JSON {
				w = p.Err
			}
			p.write(w, "::error title=kodeploy::"+escapeData(p.Redact(errorLine(r)))+"\n")
		}
		if p.SummaryPath != "" {
			p.appendSummary(r)
		}
	}
}

func (p *Printer) text(r Report) {
	if r.Result == "error" {
		p.write(p.Err, "kodeploy: "+p.paint("31", "오류")+" "+r.Message+"\n")
		if r.Hint != "" {
			p.write(p.Err, "kodeploy: "+r.Hint+"\n")
		}
		return
	}
	var b strings.Builder
	color := map[string]string{"success": "32", "failed": "31", "superseded": "33", "timeout": "33", "interrupted": "33"}[r.Result]
	word, rest, _ := strings.Cut(r.Message, " ")
	fmt.Fprintf(&b, "kodeploy: %s %s\n", p.paint(color, word), rest)
	for _, bl := range r.Builds {
		if bl.Status == "failed" && bl.Error != nil && *bl.Error != "" {
			fmt.Fprintf(&b, "  [%s %s] %s\n", bl.Slot, bl.BuildID, *bl.Error)
		}
	}
	for _, line := range r.Analysis {
		fmt.Fprintf(&b, "  %s\n", line)
	}
	if r.Hint != "" {
		fmt.Fprintf(&b, "kodeploy: %s\n", r.Hint)
	}
	p.write(p.Out, b.String())
}
