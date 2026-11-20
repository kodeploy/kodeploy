package output

import (
	"encoding/json"
	"strconv"
	"strings"
)

// 실패 진단(ai_analysis)의 모양 — core의 diagnose.Diagnosis를 JSON으로 저장한 것이다.
// {"title", "cause", "evidence", "cause_category", "kodeploy_specific", "fix_steps": [{"text", "command"}],
//  "alternative_label", "alternative_steps": [...]}
type diagnosis struct {
	Title    string `json:"title"`
	Cause    string `json:"cause"`
	FixSteps []struct {
		Text    string `json:"text"`
		Command string `json:"command"`
	} `json:"fix_steps"`
}

const rawPreview = 300

// SummarizeAnalysis는 진단 JSON을 몇 줄로 줄인다. 모르는 모양이면 원문 앞부분만.
func SummarizeAnalysis(raw string) []string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	var d diagnosis
	if err := json.Unmarshal([]byte(raw), &d); err != nil || (d.Title == "" && d.Cause == "") {
		r := []rune(raw)
		if len(r) > rawPreview {
			return []string{"AI 진단: " + string(r[:rawPreview]) + "…"}
		}
		return []string{"AI 진단: " + raw}
	}
	lines := []string{"AI 진단: " + firstNonEmpty(d.Title, d.Cause)}
	if d.Title != "" && d.Cause != "" {
		lines = append(lines, "  원인: "+d.Cause)
	}
	for i, s := range d.FixSteps {
		lines = append(lines, "  "+strconv.Itoa(i+1)+". "+s.Text)
		for _, c := range strings.Split(strings.TrimSpace(s.Command), "\n") {
			if c != "" {
				lines = append(lines, "       "+c)
			}
		}
	}
	return lines
}

func firstNonEmpty(a, b string) string {
	if a != "" {
		return a
	}
	return b
}
