package output

import (
	"bytes"
	"flag"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var update = flag.Bool("update", false, "골든 파일을 새로 쓴다")

func sp(s string) *string { return &s }

func reports() map[string]Report {
	sha := "3f9a1c2b0123456789abcdef0123456789abcdef"
	return map[string]Report{
		"success": {
			Result: "success", ExitCode: 0, AppID: "app-1", RequestedSHA: sp(sha), Message: "성공 source=3f9a1c2",
			Builds: []Build{
				{BuildID: "3f9a2c1d", Slot: "server", Status: "running", SourceSHA: sp(sha), Elapsed: 6*time.Minute + 12*time.Second},
				{BuildID: "8e7d6c5b", Slot: "static", Status: "running", SourceSHA: sp(sha), Elapsed: 2*time.Minute + 40*time.Second},
			},
		},
		"failed": {
			Result: "failed", ExitCode: 1, AppID: "app-1", RequestedSHA: sp(sha), Message: "배포 실패 [server 3f9a2c1d]",
			Builds: []Build{
				{BuildID: "3f9a2c1d", Slot: "server", Status: "failed", Error: sp("build: exit 126 | permission denied"), Elapsed: 95 * time.Second},
				{BuildID: "8e7d6c5b", Slot: "static", Status: "running", SourceSHA: sp(sha), Elapsed: 80 * time.Second},
			},
			Analysis: SummarizeAnalysis(`{"title":"gradlew 실행 권한을 확인해 주세요.","cause":"gradlew에 실행 권한이 없어요",` +
				`"fix_steps":[{"text":"실행 권한을 더하세요","command":"git update-index --chmod=+x gradlew\ngit commit -m fix"}]}`),
		},
		"error": {
			Result: "error", ExitCode: 125, AppID: "app-1", Builds: []Build{},
			Message: "CI 토큰 교환이 거절됐어요: 이 앱은 GitHub Actions 배포가 꺼져 있어요. 앱 설정에서 켜 주세요",
		},
		"timeout": {
			Result: "timeout", ExitCode: 4, AppID: "app-1", Message: "시간 제한(20s)을 넘겼어요. 빌드는 서버에서 계속 진행됩니다",
			Builds: []Build{{BuildID: "3f9a2c1d", Slot: "server", Status: "building"}},
		},
	}
}

func golden(t *testing.T, name, got string) {
	t.Helper()
	path := filepath.Join("testdata", name+".golden")
	if *update {
		if err := os.WriteFile(path, []byte(got), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("%v (go test ./internal/output -update로 만든다)", err)
	}
	if got != string(want) {
		t.Fatalf("%s 골든과 다르다\n--- got\n%s\n--- want\n%s", name, got, want)
	}
}

func render(r Report, jsonOut, gha bool, summary string) (string, string) {
	var out, errb bytes.Buffer
	p := &Printer{Out: &out, Err: &errb, JSON: jsonOut, GHA: gha, SummaryPath: summary}
	p.Result(r)
	return out.String(), errb.String()
}

func TestGoldenText(t *testing.T) {
	for name, r := range reports() {
		out, errb := render(r, false, false, "")
		golden(t, "text_"+name, "--- stdout\n"+out+"--- stderr\n"+errb)
	}
}

func TestGoldenJSON(t *testing.T) {
	for name, r := range reports() {
		out, errb := render(r, true, false, "")
		if strings.Count(out, "\n") != 1 || !strings.HasPrefix(out, "{") {
			t.Fatalf("json은 stdout에 객체 하나: %q", out)
		}
		golden(t, "json_"+name, "--- stdout\n"+out+"--- stderr\n"+errb)
	}
}

func TestGitHubActionsErrorAndSummary(t *testing.T) {
	dir := t.TempDir()
	for name, r := range reports() {
		summary := filepath.Join(dir, name+".md")
		out, errb := render(r, false, true, summary)
		hasError := strings.Contains(out, "::error title=kodeploy::")
		if hasError != (r.ExitCode != 0) {
			t.Fatalf("%s: ::error는 실패일 때만 (stdout %q)", name, out)
		}
		md, err := os.ReadFile(summary)
		if err != nil {
			t.Fatal(err)
		}
		golden(t, "actions_"+name, "--- stdout\n"+out+"--- stderr\n"+errb+"--- summary\n"+string(md))
	}
	// json이면 ::error는 stderr로 (stdout은 객체 하나만)
	out, errb := render(reports()["failed"], true, true, "")
	if strings.Contains(out, "::error") || !strings.Contains(errb, "::error title=kodeploy::배포 실패") {
		t.Fatalf("stdout %q stderr %q", out, errb)
	}
}

func TestErrorCommandEscapesNewlines(t *testing.T) {
	r := reports()["failed"]
	r.Builds[0].Error = sp("line1\nline2 100%")
	out, _ := render(r, false, true, "")
	if !strings.Contains(out, "line1%0Aline2 100%25") {
		t.Fatalf("%q", out)
	}
}

func TestSecretsAreRedactedEverywhere(t *testing.T) {
	dir := t.TempDir()
	var out, errb bytes.Buffer
	p := &Printer{Out: &out, Err: &errb, GHA: true, SummaryPath: filepath.Join(dir, "s.md")}
	p.AddSecret("kdci_SECRETVALUE")
	p.Progress("token kdci_SECRETVALUE")
	p.Debug("auth kdci_SECRETVALUE")
	r := reports()["failed"]
	r.Message = "boom kdci_SECRETVALUE"
	p.Result(r)
	md, _ := os.ReadFile(filepath.Join(dir, "s.md"))
	for _, s := range []string{out.String(), errb.String(), string(md)} {
		if strings.Contains(s, "kdci_SECRETVALUE") {
			t.Fatalf("가리지 못했다: %q", s)
		}
	}
}

func TestBuildLine(t *testing.T) {
	var errb bytes.Buffer
	p := &Printer{Out: &bytes.Buffer{}, Err: &errb}
	p.BuildLine("server", "3f9a2c1d", []string{"queued", "building", "built", "deploying", "running"}, 6*time.Minute+12*time.Second)
	p.BuildLine("static", "8e7d6c5b", []string{"queued", "building"}, 0)
	want := "[server 3f9a2c1d] queued → building → built → deploying → running (6m12s)\n[static 8e7d6c5b] queued → building\n"
	if errb.String() != want {
		t.Fatalf("%q", errb.String())
	}
}

func TestSummarizeUnknownAnalysisShowsTheStart(t *testing.T) {
	long := strings.Repeat("가", 400)
	got := SummarizeAnalysis(long)
	if len(got) != 1 || !strings.HasSuffix(got[0], "…") || len([]rune(got[0])) != len([]rune("AI 진단: "))+rawPreview+1 {
		t.Fatalf("%v", got)
	}
	if got := SummarizeAnalysis(`{"other":"shape"}`); len(got) != 1 || got[0] != `AI 진단: {"other":"shape"}` {
		t.Fatalf("%v", got)
	}
	if SummarizeAnalysis("  ") != nil {
		t.Fatal("빈 진단은 줄이 없다")
	}
}
