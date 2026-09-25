package marker

import (
	"regexp"
	"strings"
	"testing"
)

const (
	repo = "ghcr.io/yuntyu01/b1545e9c/dailo"
	tag  = "bench"
	hex  = "4f1c2b3a5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7a8"
)

// kodeploy-bench/logs/edit-1/buildkit.log (2026-07-12)의 실제 줄. 타임스탬프는 logs 패키지가 뗀다.
var realPrefix = []string{
	"#16 86.18 BUILD SUCCESSFUL in 1m 25s",
	"#16 86.18 6 actionable tasks: 5 executed, 1 up-to-date",
	"#20 exporting to image",
	"#20 exporting layers",
	"#20 exporting layers 4.5s done",
	"#20 exporting manifest sha256:" + hex + " 0.0s done",
	"#20 exporting config sha256:" + hex + " done",
	"#20 pushing layers",
	"#22 exporting cache to registry",
	"#22 preparing build cache for export",
	"#20 pushing layers 11.1s done",
	"#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex,
}

const realDone = "#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex + " 0.8s done"

// 원본 pipeline.py의 정규식 (좁히기만 했는지 대조용)
var (
	origExport = regexp.MustCompile(`exporting to image`)
	origPush   = regexp.MustCompile(`pushing manifest .*\bdone\b`)
)

func feedAll(lines []string) (digest string, at int) {
	m := New(repo, tag)
	for i, l := range lines {
		if d, ok := m.Feed(l); ok {
			return d, i
		}
	}
	return "", -1
}

func TestMarker(t *testing.T) {
	cases := []struct {
		name  string
		lines []string
		at    int // 찾아야 하는 줄 번호, -1 = 못 찾아야 함
	}{
		{
			name:  "real buildkit sequence",
			lines: append(append([]string{}, realPrefix...), realDone, "#20 DONE 16.3s"),
			at:    len(realPrefix),
		},
		{
			name: "push before export anchor is ignored",
			lines: []string{
				realDone,
				"#20 exporting to image",
			},
			at: -1,
		},
		{
			name: "buildcache push is ignored",
			lines: []string{
				"#20 exporting to image",
				"#22 pushing manifest for " + repo + ":buildcache@sha256:" + hex + " 0.4s done",
				realDone,
			},
			at: 2,
		},
		{
			name: "user RUN output is ignored",
			lines: []string{
				"#16 12.03 #20 exporting to image",
				"#16 12.04 exporting to image",
				"#20 exporting to image",
				"#16 12.05 #20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex + " 0.8s done",
				"#16 12.06 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex + " done",
			},
			at: -1,
		},
		{
			name: "digest too short",
			lines: []string{
				"#20 exporting to image",
				"#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex[:63] + " 0.8s done",
			},
			at: -1,
		},
		{
			name: "digest too long",
			lines: []string{
				"#20 exporting to image",
				"#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex + "a 0.8s done",
			},
			at: -1,
		},
		{
			name: "other tag of the same repo",
			lines: []string{
				"#20 exporting to image",
				"#20 pushing manifest for " + repo + ":benchx@sha256:" + hex + " 0.8s done",
				"#20 pushing manifest for " + repo + ":ben@sha256:" + hex + " 0.8s done",
			},
			at: -1,
		},
		{
			name: "other repo with same tag",
			lines: []string{
				"#20 exporting to image",
				"#20 pushing manifest for ghcr.io/yuntyu01/b1545e9c/dailo2:" + tag + "@sha256:" + hex + " 0.8s done",
			},
			at: -1,
		},
		{
			name: "push without done",
			lines: []string{
				"#20 exporting to image",
				"#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex,
				"#20 pushing manifest for " + repo + ":" + tag + "@sha256:" + hex + " 0.8s donezo",
			},
			at: -1,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			d, at := feedAll(c.lines)
			if at != c.at {
				t.Fatalf("found at line %d, want %d", at, c.at)
			}
			if at >= 0 && d != "sha256:"+hex {
				t.Fatalf("digest %q", d)
			}
			if at >= 0 {
				// 원본 판정도 같은 줄에서 참이어야 한다 (좁히기만 했다)
				joined := strings.Join(c.lines[:at+1], "\n")
				exp := origExport.FindStringIndex(joined)
				if exp == nil || origPush.FindStringIndex(joined[exp[1]:]) == nil {
					t.Fatal("original regexes would not fire here")
				}
			}
		})
	}
}

func TestMarkerOnce(t *testing.T) {
	m := New(repo, tag)
	m.Feed("#20 exporting to image")
	if _, ok := m.Feed(realDone); !ok {
		t.Fatal("first push not found")
	}
	if _, ok := m.Feed(realDone); ok {
		t.Fatal("digest must be reported only once")
	}
}

// 태그·repo의 정규식 특수문자가 그대로 비교되는지 (. 은 아무 글자가 아니다)
func TestMarkerQuotesImage(t *testing.T) {
	m := New("ghcr.io/u/0badf00d/my.app", "v1.0")
	m.Feed("#9 exporting to image")
	if _, ok := m.Feed("#9 pushing manifest for ghcr.io/u/0badf00d/myXapp:v1x0@sha256:" + hex + " done"); ok {
		t.Fatal("dots must be literal")
	}
	if _, ok := m.Feed("#9 pushing manifest for ghcr.io/u/0badf00d/my.app:v1.0@sha256:" + hex + " 0.1s done"); !ok {
		t.Fatal("exact image not matched")
	}
}
