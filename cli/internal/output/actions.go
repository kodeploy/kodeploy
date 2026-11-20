package output

import (
	"fmt"
	"os"
	"strings"
)

var resultLabel = map[string]string{
	"success": "성공", "failed": "실패", "superseded": "다른 배포에 밀림", "timeout": "시간 초과",
	"interrupted": "중단", "error": "오류", "requested": "요청함",
}

// errorLine은 ::error에 실을 이유 한 줄이다. 실패한 빌드가 있으면 그 error를 붙인다.
func errorLine(r Report) string {
	msg := r.Message
	for _, b := range r.Builds {
		if b.Status == "failed" && b.Error != nil && *b.Error != "" {
			msg += fmt.Sprintf(" — [%s %s] %s", b.Slot, b.BuildID, *b.Error)
			break
		}
	}
	return msg
}

// escapeData는 워크플로 명령의 값 이스케이프다 (%, \r, \n).
func escapeData(s string) string {
	s = strings.ReplaceAll(s, "%", "%25")
	s = strings.ReplaceAll(s, "\r", "%0D")
	return strings.ReplaceAll(s, "\n", "%0A")
}

// 표 칸 안의 | 와 줄바꿈을 지운다
func cell(s string) string {
	s = strings.ReplaceAll(s, "|", "\\|")
	return strings.Join(strings.Fields(s), " ")
}

func summaryMarkdown(r Report) string {
	var b strings.Builder
	fmt.Fprintf(&b, "### KoDeploy 배포 %s\n\n", resultLabel[r.Result])
	sha := "-"
	if r.RequestedSHA != nil && *r.RequestedSHA != "" {
		sha = "`" + short(*r.RequestedSHA) + "`"
	}
	fmt.Fprintf(&b, "| 앱 | 커밋 | exit |\n|---|---|---|\n| `%s` | %s | %d |\n\n", cell(r.AppID), sha, r.ExitCode)
	if len(r.Builds) > 0 {
		b.WriteString("| 슬롯 | 빌드 | 상태 | 시간 | 빌드한 커밋 |\n|---|---|---|---|---|\n")
		for _, bl := range r.Builds {
			elapsed := "-"
			if bl.Elapsed > 0 {
				elapsed = Duration(bl.Elapsed)
			}
			src := "-"
			if bl.SourceSHA != nil && *bl.SourceSHA != "" {
				src = "`" + short(*bl.SourceSHA) + "`"
			}
			fmt.Fprintf(&b, "| %s | `%s` | %s | %s | %s |\n", cell(bl.Slot), cell(bl.BuildID), cell(bl.Status), elapsed, src)
		}
		b.WriteString("\n")
	}
	if r.ExitCode != 0 {
		fmt.Fprintf(&b, "실패 이유: %s\n\n", cell(errorLine(r)))
		for _, line := range r.Analysis {
			fmt.Fprintf(&b, "    %s\n", line)
		}
		if len(r.Analysis) > 0 {
			b.WriteString("\n")
		}
	}
	return b.String()
}

func (p *Printer) appendSummary(r Report) {
	f, err := os.OpenFile(p.SummaryPath, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		p.Progress("step summary를 쓰지 못했어요: %v", err)
		return
	}
	defer f.Close()
	_, _ = f.WriteString(p.Redact(summaryMarkdown(r)))
}

func short(sha string) string {
	if len(sha) > 7 {
		return sha[:7]
	}
	return sha
}
