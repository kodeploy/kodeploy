package job

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

// hack/render-original-job.py와 같은 매개변수
func goldenParams(cache bool) Params {
	p := Params{
		Namespace:             "kodeploy-build",
		BuildID:               "3f9a2c1d",
		UserID:                "d6d8b75985524d6f9a9000665e7ca0da",
		Image:                 "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:3f9a2c1d",
		RepoURL:               "https://github.com/yuntyu01/kodeploy-test-spring.git",
		Branch:                "r2-test-v1",
		BuildKitImage:         "moby/buildkit:v0.30.0-rootless",
		ActiveDeadlineSeconds: 900,
		RequestJSON:           `{"build_id":"3f9a2c1d"}`,
	}
	if cache {
		p.DockerfileSubdir = "backend"
		p.DockerfileFilename = "Dockerfile.prod"
		p.CacheRef = "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:buildcache"
	}
	return p
}

func toMap(t *testing.T, v any) map[string]any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func dig(m map[string]any, path ...string) map[string]any {
	for _, p := range path {
		m = m[p].(map[string]any)
	}
	return m
}

// Go 타입을 JSON으로 만들 때만 생기는 빈 칸을 걷어낸다 (의미 차이 없음).
func dropZeroArtifacts(m map[string]any) {
	delete(m, "status")
	delete(dig(m, "metadata"), "creationTimestamp")
	delete(dig(m, "spec", "template", "metadata"), "creationTimestamp")
	pod := dig(m, "spec", "template", "spec")
	for _, key := range []string{"initContainers", "containers"} {
		for _, c := range pod[key].([]any) {
			delete(c.(map[string]any), "resources")
		}
	}
}

// TestMatchesOriginalTemplate는 Go로 옮긴 Job이 원본 Jinja 렌더와 같은지 본다.
// 차이는 의도한 두 가지뿐이어야 한다: kodeploy.io/managed 라벨, kodeploy.io/request 어노테이션.
func TestMatchesOriginalTemplate(t *testing.T) {
	for _, tc := range []struct {
		golden string
		cache  bool
	}{
		{"testdata/original-cache.json", true},
		{"testdata/original-nocache.json", false},
	} {
		t.Run(tc.golden, func(t *testing.T) {
			raw, err := os.ReadFile(tc.golden)
			if err != nil {
				t.Fatal(err)
			}
			var want map[string]any
			if err := json.Unmarshal(raw, &want); err != nil {
				t.Fatal(err)
			}

			got := toMap(t, Build(goldenParams(tc.cache)))
			dropZeroArtifacts(got)

			meta := dig(got, "metadata")
			labels := meta["labels"].(map[string]any)
			if labels[LabelManaged] != "true" {
				t.Fatalf("managed label missing: %v", labels)
			}
			delete(labels, LabelManaged)
			ann := meta["annotations"].(map[string]any)
			if ann[AnnRequest] != `{"build_id":"3f9a2c1d"}` {
				t.Fatalf("request annotation missing: %v", ann)
			}
			delete(meta, "annotations")

			if !reflect.DeepEqual(got, want) {
				g, _ := json.MarshalIndent(got, "", "  ")
				w, _ := json.MarshalIndent(want, "", "  ")
				t.Fatalf("Job differs from original template\n--- got\n%s\n--- want\n%s", g, w)
			}
		})
	}
}

func TestName(t *testing.T) {
	if got := Name("3f9a2c1d", "d6d8b75985524d6f9a9000665e7ca0da"); got != "build-d6d8b759-3f9a2c1d" {
		t.Fatalf("got %s", got)
	}
}

func TestDefaultDockerfileName(t *testing.T) {
	p := goldenParams(false)
	p.DockerfileFilename = ""
	args := Build(p).Spec.Template.Spec.Containers[0].Args
	if args[4] != "--opt=filename=Dockerfile" {
		t.Fatalf("got %q", args[4])
	}
}

func TestNoRequestAnnotationWhenEmpty(t *testing.T) {
	p := goldenParams(false)
	p.RequestJSON = ""
	if _, ok := Build(p).Annotations[AnnRequest]; ok {
		t.Fatal("empty request should not be stored")
	}
}
