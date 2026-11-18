package marker

import (
	"strings"
	"testing"
)

const (
	mainHeader = "=== buildkit (main) ==="
	sha        = "0123456789abcdef0123456789abcdef01234567"
)

func feedSource(lines []string) (string, int) {
	s := NewSource(mainHeader)
	got, hits := "", 0
	for _, l := range lines {
		if v, ok := s.Feed(l); ok {
			got, hits = v, hits+1
		}
	}
	return got, hits
}

func TestSourceReadsTheInitLine(t *testing.T) {
	got, hits := feedSource([]string{
		"=== clone (init) ===",
		"[1/1] cloning https://github.com/acme/app (main)...",
		"Cloning into '/workspace/src'...",
		"KODEPLOY_SOURCE_SHA=" + sha,
		"",
		mainHeader,
		"#1 [internal] load build definition from Dockerfile",
	})
	if got != sha || hits != 1 {
		t.Fatalf("got %q hits=%d", got, hits)
	}
}

// main(buildkit) 로그의 같은 줄은 사용자 RUN이 찍을 수 있어 인정하지 않는다.
func TestSourceIgnoresMainLogLines(t *testing.T) {
	for _, fake := range []string{
		"KODEPLOY_SOURCE_SHA=" + sha,
		"#5 0.21 KODEPLOY_SOURCE_SHA=" + sha,
	} {
		got, hits := feedSource([]string{"=== clone (init) ===", "[1/1] cloning", "", mainHeader, fake})
		if hits != 0 || got != "" {
			t.Fatalf("main line %q was accepted", fake)
		}
	}
}

// init에서 처음 맞은 줄만 쓴다 — 뒤에 오는 줄(사용자 저장소에서 나온 출력)은 무시한다.
func TestSourceKeepsTheFirstInitLine(t *testing.T) {
	other := strings.Repeat("f", 40)
	got, hits := feedSource([]string{"KODEPLOY_SOURCE_SHA=" + sha, "KODEPLOY_SOURCE_SHA=" + other})
	if got != sha || hits != 1 {
		t.Fatalf("got %q hits=%d", got, hits)
	}
}

func TestSourceRejectsMalformedValues(t *testing.T) {
	for _, line := range []string{
		"KODEPLOY_SOURCE_SHA=" + sha[:39],
		"KODEPLOY_SOURCE_SHA=" + sha + "0",
		"KODEPLOY_SOURCE_SHA=" + strings.ToUpper(sha),
		"KODEPLOY_SOURCE_SHA=0123456789ABCDEF0123456789abcdef01234567",
		"KODEPLOY_SOURCE_SHA=" + strings.Repeat("g", 40),
		" KODEPLOY_SOURCE_SHA=" + sha,
		"KODEPLOY_SOURCE_SHA=" + sha + " ",
		"KODEPLOY_SOURCE_SHA= " + sha,
		"kodeploy_source_sha=" + sha,
		"KODEPLOY_SOURCE_SHA=",
	} {
		if got, hits := feedSource([]string{line}); hits != 0 {
			t.Fatalf("%q accepted as %q", line, got)
		}
	}
}

// 잘못된 줄을 본 뒤에도 올바른 줄은 받는다.
func TestSourceSkipsBadLinesBeforeTheRealOne(t *testing.T) {
	got, hits := feedSource([]string{"KODEPLOY_SOURCE_SHA=" + sha[:7], "KODEPLOY_SOURCE_SHA=" + sha})
	if got != sha || hits != 1 {
		t.Fatalf("got %q hits=%d", got, hits)
	}
}
