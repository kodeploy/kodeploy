package job

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	corev1 "k8s.io/api/core/v1"
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
			cm := c.(map[string]any)
			delete(cm, "resources")
			// 값이 빈 env는 Go가 JSON에서 value를 생략한다 (원본 YAML은 value: ""). 쿠버네티스에선 같은 뜻이다.
			if envs, ok := cm["env"].([]any); ok {
				for _, e := range envs {
					em := e.(map[string]any)
					if _, has := em["value"]; !has && em["valueFrom"] == nil {
						em["value"] = ""
					}
				}
			}
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

// private repo: 토큰은 clone 컨테이너에만 Secret 참조로 주입된다 (main 컨테이너와 Job 어노테이션엔 안 간다).
func TestGitAuthSecretIsInjectedOnlyIntoTheCloneContainer(t *testing.T) {
	p := goldenParams(false)
	p.GitAuthSecret = "git-auth-" + p.BuildID
	j := Build(p)

	var clone *corev1.Container
	for i := range j.Spec.Template.Spec.InitContainers {
		if j.Spec.Template.Spec.InitContainers[i].Name == InitContainer {
			clone = &j.Spec.Template.Spec.InitContainers[i]
		}
	}
	if clone == nil {
		t.Fatal("clone container missing")
	}
	var token *corev1.EnvVar
	for i := range clone.Env {
		if clone.Env[i].Name == "GIT_AUTH_TOKEN" {
			token = &clone.Env[i]
		}
	}
	if token == nil || token.ValueFrom == nil || token.ValueFrom.SecretKeyRef == nil ||
		token.ValueFrom.SecretKeyRef.Name != p.GitAuthSecret || token.ValueFrom.SecretKeyRef.Key != "GIT_AUTH_TOKEN" {
		t.Fatalf("clone token env: %+v", token)
	}
	for _, e := range j.Spec.Template.Spec.Containers[0].Env {
		if e.Name == "GIT_AUTH_TOKEN" {
			t.Fatal("token must not reach the buildkit container")
		}
	}
	if token.Value != "" {
		t.Fatal("token must be a secret reference, never a literal value")
	}
}

func TestNoTokenEnvForPublicRepos(t *testing.T) {
	j := Build(goldenParams(false))
	for _, e := range j.Spec.Template.Spec.InitContainers[0].Env {
		if e.Name == "GIT_AUTH_TOKEN" {
			t.Fatal("public clone must not reference a git-auth secret")
		}
	}
}

// nixpacksParams는 hack/render-original-job.py의 nixpacks-* 모드와 같은 매개변수다.
func nixpacksParams(cache bool, private bool) Params {
	p := goldenParams(false)
	p.Mode = ModeAuto
	if cache {
		p.ProjectPath = "backend"
		p.CacheRef = "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:buildcache"
	}
	if private {
		p.GitAuthSecret = "git-auth-3f9a2c1d"
	}
	return p
}

// TestNixpacksMatchesOriginalTemplate는 자동 빌드(nixpacks) Job이 원본 Jinja 렌더와 같은지 본다.
// init 스크립트는 원본을 글자 그대로 옮겼으니, 이 테스트가 옮기다 생긴 차이를 잡는다.
func TestNixpacksMatchesOriginalTemplate(t *testing.T) {
	for _, tc := range []struct {
		golden  string
		cache   bool
		private bool
	}{
		{"testdata/original-nixpacks-cache.json", true, false},
		{"testdata/original-nixpacks-nocache.json", false, false},
		{"testdata/original-nixpacks-private.json", false, true},
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

			got := toMap(t, Build(nixpacksParams(tc.cache, tc.private)))
			dropZeroArtifacts(got)
			meta := dig(got, "metadata")
			labels := meta["labels"].(map[string]any)
			if labels[LabelManaged] != "true" || labels[LabelBuildMode] != "auto" {
				t.Fatalf("labels: %v", labels)
			}
			delete(labels, LabelManaged)
			delete(meta, "annotations") // kodeploy.io/request는 빌더가 더하는 의도한 차이

			if !reflect.DeepEqual(got, want) {
				g, _ := json.MarshalIndent(got, "", "  ")
				w, _ := json.MarshalIndent(want, "", "  ")
				t.Fatalf("nixpacks Job differs from original template\n--- got\n%s\n--- want\n%s", g, w)
			}
		})
	}
}

func TestNixpacksTokenReachesOnlyTheInitContainer(t *testing.T) {
	j := Build(nixpacksParams(false, true))
	var inInit, inMain bool
	for _, e := range j.Spec.Template.Spec.InitContainers[0].Env {
		inInit = inInit || e.Name == "GIT_AUTH_TOKEN" && e.ValueFrom != nil && e.Value == ""
	}
	for _, e := range j.Spec.Template.Spec.Containers[0].Env {
		inMain = inMain || e.Name == "GIT_AUTH_TOKEN"
	}
	if !inInit || inMain {
		t.Fatalf("token in init=%v main=%v", inInit, inMain)
	}
}

// staticParams는 hack/render-original-job.py의 static-* 모드와 같은 매개변수다.
// Dockerfile 본문은 같은 스크립트가 뽑은 testdata/static-dockerfile.txt (core의 static_dockerfile 렌더 결과).
func staticParams(t *testing.T, cache bool, private bool) Params {
	t.Helper()
	text, err := os.ReadFile("testdata/static-dockerfile.txt")
	if err != nil {
		t.Fatal(err)
	}
	p := goldenParams(false)
	p.Mode = ModeStatic
	p.DockerfileB64 = base64.StdEncoding.EncodeToString(text)
	if cache {
		p.ProjectPath = "site"
		p.CacheRef = "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:buildcache"
	}
	if private {
		p.GitAuthSecret = "git-auth-3f9a2c1d"
	}
	return p
}

// TestStaticMatchesOriginalTemplate는 정적 사이트 Job이 원본 Jinja 렌더와 같은지 본다 (init 스크립트 글자 그대로).
func TestStaticMatchesOriginalTemplate(t *testing.T) {
	for _, tc := range []struct {
		golden  string
		cache   bool
		private bool
	}{
		{"testdata/original-static-cache.json", true, false},
		{"testdata/original-static-nocache.json", false, false},
		{"testdata/original-static-private.json", false, true},
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

			got := toMap(t, Build(staticParams(t, tc.cache, tc.private)))
			dropZeroArtifacts(got)
			meta := dig(got, "metadata")
			labels := meta["labels"].(map[string]any)
			if labels[LabelManaged] != "true" || labels[LabelBuildMode] != "static" {
				t.Fatalf("labels: %v", labels)
			}
			delete(labels, LabelManaged)
			delete(meta, "annotations") // kodeploy.io/request는 빌더가 더하는 의도한 차이

			if !reflect.DeepEqual(got, want) {
				g, _ := json.MarshalIndent(got, "", "  ")
				w, _ := json.MarshalIndent(want, "", "  ")
				t.Fatalf("static Job differs from original template\n--- got\n%s\n--- want\n%s", g, w)
			}
		})
	}
}

func TestStaticTokenReachesOnlyTheInitContainer(t *testing.T) {
	j := Build(staticParams(t, false, true))
	var inInit, inMain bool
	for _, e := range j.Spec.Template.Spec.InitContainers[0].Env {
		inInit = inInit || e.Name == "GIT_AUTH_TOKEN" && e.ValueFrom != nil && e.Value == ""
	}
	for _, e := range j.Spec.Template.Spec.Containers[0].Env {
		inMain = inMain || e.Name == "GIT_AUTH_TOKEN"
	}
	if !inInit || inMain {
		t.Fatalf("token in init=%v main=%v", inInit, inMain)
	}
}
