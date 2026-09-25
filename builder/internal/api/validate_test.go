package api

import (
	"strings"
	"testing"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

var testValidator = Validator{GHCRUser: "yuntyu01"}

const (
	testNS     = "tenant-d6d8b759"
	testUserID = "d6d8b75985524d6f9a9000665e7ca0da"
	testRepo   = "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring"
	testDigest = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)

func ptr[T any](v T) *T { return &v }

func validBuild() *contract.DeployRequest {
	return &contract.DeployRequest{
		BuildID:   "3f9a2c1d",
		Actor:     "d6d8b759",
		Namespace: testNS,
		Slot:      contract.SlotServer,
		Kind:      contract.KindBuild,
		Values:    &contract.CoreValues{UserID: ptr(testUserID), Name: ptr("kodeploy-test-spring")},
		Unit:      &contract.Unit{Runtime: "java", Port: 8080},
		Build: &contract.BuildSpec{
			Repo:           "https://github.com/yuntyu01/kodeploy-test-spring.git",
			Ref:            "r2-test-v1",
			Mode:           "dockerfile",
			DockerfileName: "Dockerfile",
			ImageRepo:      testRepo,
			ImageTag:       "3f9a2c1d",
			CacheRef:       testRepo + ":buildcache",
		},
	}
}

func validSetImage() *contract.DeployRequest {
	return &contract.DeployRequest{
		BuildID:   "aa11bb22",
		Namespace: testNS,
		Slot:      contract.SlotServer,
		Kind:      contract.KindSetImage,
		Unit:      &contract.Unit{Runtime: "java", Port: 8080},
		Image:     testRepo + ":99483bd9@sha256:" + testDigest,
	}
}

func TestValidate(t *testing.T) {
	cases := []struct {
		name   string
		req    func() *contract.DeployRequest
		errHas string // "" = 통과해야 함
	}{
		{"build ok", validBuild, ""},
		{"build ok without cache", func() *contract.DeployRequest { r := validBuild(); r.Build.CacheRef = ""; return r }, ""},
		{"build ok repo without .git", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://github.com/yuntyu01/kodeploy-test-spring"
			return r
		}, ""},
		{"build ok subdir + tag ref", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.DockerfileDir = "backend/api"
			r.Build.Ref = "release/v1.2.0"
			return r
		}, ""},
		{"set-image ok", validSetImage, ""},
		{"set-image static ok", func() *contract.DeployRequest {
			r := validSetImage()
			r.Slot = contract.SlotStatic
			r.Unit = nil
			return r
		}, ""},
		{"config ok", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "cc33dd44", Namespace: testNS, Slot: "server", Kind: "config",
				Values: &contract.CoreValues{EnvRevision: ptr(2)}}
		}, ""},
		{"delete ok", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "ee55ff66", Namespace: testNS, Kind: "delete"}
		}, ""},
		{"app namespace ok", func() *contract.DeployRequest {
			r := validSetImage()
			r.Namespace = "app-0badf00d"
			r.Image = "ghcr.io/yuntyu01/0badf00d/shop:v1@sha256:" + testDigest
			return r
		}, ""},

		// 공통
		{"build_id format", func() *contract.DeployRequest { r := validBuild(); r.BuildID = "b-3f9a2c1d"; return r }, "build_id"},
		{"namespace pattern", func() *contract.DeployRequest { r := validBuild(); r.Namespace = "kube-system"; return r }, "namespace"},
		{"namespace upper hex", func() *contract.DeployRequest { r := validBuild(); r.Namespace = "tenant-D6D8B759"; return r }, "namespace"},
		{"actor pattern", func() *contract.DeployRequest { r := validBuild(); r.Actor = "root"; return r }, "actor"},
		{"slot missing", func() *contract.DeployRequest { r := validBuild(); r.Slot = ""; return r }, "slot"},
		{"slot unknown", func() *contract.DeployRequest { r := validBuild(); r.Slot = "worker"; return r }, "slot"},
		{"kind unknown", func() *contract.DeployRequest { r := validBuild(); r.Kind = "rollback"; return r }, "kind"},

		// 이미지 경로 (4-1)
		{"hex8 mismatch", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.ImageRepo = "ghcr.io/yuntyu01/deadbeef/kodeploy-test-spring"
			r.Build.CacheRef = r.Build.ImageRepo + ":buildcache"
			return r
		}, "hex8"},
		{"other ghcr user", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.ImageRepo = "ghcr.io/someone/d6d8b759/kodeploy-test-spring"
			r.Build.CacheRef = r.Build.ImageRepo + ":buildcache"
			return r
		}, "ghcr.io/yuntyu01/"},
		{"other registry", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.ImageRepo = "docker.io/yuntyu01/d6d8b759/app"
			return r
		}, "image_repo"},
		{"image_repo extra segment", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.ImageRepo = testRepo + "/x"
			return r
		}, "image_repo"},
		{"image_repo with tag", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.ImageRepo = testRepo + ":latest"
			return r
		}, "image_repo"},
		{"set-image hex8 mismatch", func() *contract.DeployRequest {
			r := validSetImage()
			r.Image = "ghcr.io/yuntyu01/deadbeef/app:v1@sha256:" + testDigest
			return r
		}, "hex8"},
		{"set-image other user", func() *contract.DeployRequest {
			r := validSetImage()
			r.Image = "ghcr.io/someone/d6d8b759/app:v1@sha256:" + testDigest
			return r
		}, "ghcr.io/yuntyu01/"},
		{"set-image no digest", func() *contract.DeployRequest { r := validSetImage(); r.Image = testRepo + ":v1"; return r }, "image must be"},
		{"set-image short digest", func() *contract.DeployRequest {
			r := validSetImage()
			r.Image = testRepo + ":v1@sha256:" + testDigest[:63]
			return r
		}, "image must be"},
		{"set-image no tag", func() *contract.DeployRequest {
			r := validSetImage()
			r.Image = testRepo + "@sha256:" + testDigest
			return r
		}, "image must be"},

		// build 칸
		{"static build", func() *contract.DeployRequest { r := validBuild(); r.Slot = "static"; return r }, "not supported yet"},
		{"mode auto", func() *contract.DeployRequest { r := validBuild(); r.Build.Mode = "auto"; return r }, "not supported yet"},
		{"git_auth_secret", func() *contract.DeployRequest { r := validBuild(); r.Build.GitAuthSecret = "git-auth-x"; return r }, "not supported yet"},
		{"project_path", func() *contract.DeployRequest { r := validBuild(); r.Build.ProjectPath = "backend"; return r }, "not supported yet"},
		{"build missing", func() *contract.DeployRequest { r := validBuild(); r.Build = nil; return r }, "build is required"},
		{"build with image", func() *contract.DeployRequest { r := validBuild(); r.Image = validSetImage().Image; return r }, "image is not allowed"},
		{"unit missing", func() *contract.DeployRequest { r := validBuild(); r.Unit = nil; return r }, "unit"},
		{"unit runtime none", func() *contract.DeployRequest { r := validBuild(); r.Unit.Runtime = "none"; return r }, "unit.runtime"},
		{"unit port 0", func() *contract.DeployRequest { r := validBuild(); r.Unit.Port = 0; return r }, "unit.port"},
		{"unit port big", func() *contract.DeployRequest { r := validBuild(); r.Unit.Port = 70000; return r }, "unit.port"},
		{"userId missing", func() *contract.DeployRequest { r := validBuild(); r.Values.UserID = nil; return r }, "userId"},
		{"cache_ref elsewhere", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.CacheRef = "ghcr.io/yuntyu01/d6d8b759/other:buildcache"
			return r
		}, "cache_ref"},
		{"image_tag bad", func() *contract.DeployRequest { r := validBuild(); r.Build.ImageTag = "-x"; return r }, "image_tag"},

		// 입력 검증 추가분: repo
		{"repo http", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "http://github.com/a/b"
			return r
		}, "build.repo"},
		{"repo other host", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://gitlab.com/a/b"
			return r
		}, "build.repo"},
		{"repo userinfo", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://x:token@github.com/a/b"
			return r
		}, "build.repo"},
		{"repo extra path", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://github.com/a/b/tree/main"
			return r
		}, "build.repo"},
		{"repo dotdot", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://github.com/a/.."
			return r
		}, "build.repo"},
		{"repo owner dash", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://github.com/-a/b"
			return r
		}, "build.repo"},
		{"repo newline", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Repo = "https://github.com/a/b\nx"
			return r
		}, "build.repo"},
		// ref
		{"ref option", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.Ref = "--upload-pack=touch /tmp/x"
			return r
		}, "build.ref"},
		{"ref dash", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = "-b"; return r }, "build.ref"},
		{"ref dotdot", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = "a..b"; return r }, "build.ref"},
		{"ref quote", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = `main"`; return r }, "build.ref"},
		{"ref yaml", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = "main\n  hostPID: true"; return r }, "build.ref"},
		{"ref subst", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = "$(id)"; return r }, "build.ref"},
		{"ref empty", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = ""; return r }, "build.ref"},
		{"ref lock", func() *contract.DeployRequest { r := validBuild(); r.Build.Ref = "main.lock"; return r }, "build.ref"},
		// 경로
		{"dir absolute", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileDir = "/etc"; return r }, "dockerfile_dir"},
		{"dir dotdot", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileDir = "a/../../x"; return r }, "dockerfile_dir"},
		{"dir trailing slash", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileDir = "a/"; return r }, "dockerfile_dir"},
		{"dir space", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileDir = "a b"; return r }, "dockerfile_dir"},
		{"name with slash", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileName = "a/Dockerfile"; return r }, "dockerfile_name"},
		{"name dotdot", func() *contract.DeployRequest { r := validBuild(); r.Build.DockerfileName = ".."; return r }, "dockerfile_name"},
		{"name arg", func() *contract.DeployRequest {
			r := validBuild()
			r.Build.DockerfileName = "Dockerfile --output=type=local,dest=/"
			return r
		}, "dockerfile_name"},

		// kind별 칸 조합
		{"set-image with build", func() *contract.DeployRequest { r := validSetImage(); r.Build = validBuild().Build; return r }, "build is not allowed"},
		{"set-image server no unit", func() *contract.DeployRequest { r := validSetImage(); r.Unit = nil; return r }, "unit"},
		{"set-image static with unit", func() *contract.DeployRequest { r := validSetImage(); r.Slot = "static"; return r }, "unit"},
		{"config with image", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "cc33dd44", Namespace: testNS, Slot: "server", Kind: "config",
				Values: &contract.CoreValues{}, Image: validSetImage().Image}
		}, "config takes only values"},
		{"config no values", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "cc33dd44", Namespace: testNS, Slot: "server", Kind: "config"}
		}, "config requires values"},
		{"delete with values", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "ee55ff66", Namespace: testNS, Kind: "delete", Values: &contract.CoreValues{}}
		}, "delete must not"},
		{"delete with image", func() *contract.DeployRequest {
			return &contract.DeployRequest{BuildID: "ee55ff66", Namespace: testNS, Kind: "delete", Image: "x"}
		}, "delete must not"},

		// values 내용 (차트 스키마와 같은 규칙)
		{"values name", func() *contract.DeployRequest { r := validBuild(); r.Values.Name = ptr("Bad_Name"); return r }, "values.name"},
		{"values userId", func() *contract.DeployRequest { r := validBuild(); r.Values.UserID = ptr("xyz"); return r }, "values.userId"},
		{"values replicas", func() *contract.DeployRequest { r := validBuild(); r.Values.Replicas = ptr(2); return r }, "values.replicas"},
		{"values envRevision", func() *contract.DeployRequest { r := validBuild(); r.Values.EnvRevision = ptr(-1); return r }, "values.envRevision"},
		{"values db", func() *contract.DeployRequest { r := validBuild(); r.Values.DB = ptr("mongo"); return r }, "values.db"},
		{"values mountPath", func() *contract.DeployRequest {
			r := validBuild()
			r.Values.Volume = &contract.CoreVolume{MountPath: ptr("data")}
			return r
		}, "values.volume.mountPath"},
		{"values hostnames", func() *contract.DeployRequest {
			r := validBuild()
			r.Values.Hostnames = &[]string{"ok.kodeploy.com", "Bad Host"}
			return r
		}, "values.hostnames"},
		{"values too many hostnames", func() *contract.DeployRequest {
			r := validBuild()
			hs := make([]string, 11)
			for i := range hs {
				hs[i] = "a.kodeploy.com"
			}
			r.Values.Hostnames = &hs
			return r
		}, "at most"},
		{"values static hostnames", func() *contract.DeployRequest {
			r := validBuild()
			r.Values.Static = &contract.CoreStatic{Hostnames: &[]string{"-x.com"}}
			return r
		}, "values.static.hostnames"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := testValidator.Validate(c.req())
			if c.errHas == "" {
				if err != nil {
					t.Fatalf("unexpected error: %v", err)
				}
				return
			}
			if err == nil {
				t.Fatalf("expected error containing %q", c.errHas)
			}
			if !IsValidation(err) {
				t.Fatalf("not a validation error: %T", err)
			}
			if !strings.Contains(err.Error(), c.errHas) {
				t.Fatalf("error %q does not contain %q", err, c.errHas)
			}
		})
	}
}

func TestDecodeForbiddenAndUnknownKeys(t *testing.T) {
	cases := []struct {
		name   string
		values string
		errHas string
	}{
		{"image", `{"image":"ghcr.io/x"}`, "values.image is owned by the builder"},
		{"runtime", `{"runtime":"java"}`, "values.runtime"},
		{"port", `{"port":8080}`, "values.port"},
		{"port null", `{"port":null}`, "values.port"},
		{"static.image", `{"static":{"enabled":true,"image":""}}`, "values.static.image"},
		{"unknown top", `{"nme":"x"}`, "unknown field"},
		{"unknown nested", `{"volume":{"size":"5Gi"}}`, "unknown field"},
		{"wrong type", `{"redis":"yes"}`, "values"},
		{"ok", `{"name":"a","static":null,"hostnames":[]}`, ""},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			body := `{"build_id":"cc33dd44","namespace":"tenant-d6d8b759","slot":"server","kind":"config","values":` + c.values + `}`
			req, err := decodeRequest([]byte(body))
			if c.errHas == "" {
				if err != nil {
					t.Fatalf("unexpected: %v", err)
				}
				if req.Values.Hostnames == nil || len(*req.Values.Hostnames) != 0 {
					t.Fatal("explicit empty hostnames must be kept (clears the field)")
				}
				return
			}
			if err == nil || !strings.Contains(err.Error(), c.errHas) {
				t.Fatalf("got %v, want %q", err, c.errHas)
			}
		})
	}
}

func TestDecodeRejectsUnknownTopLevelAndTrailing(t *testing.T) {
	for name, body := range map[string]string{
		"unknown field": `{"build_id":"cc33dd44","namespace":"tenant-d6d8b759","kind":"delete","force":true}`,
		"trailing":      `{"build_id":"cc33dd44","namespace":"tenant-d6d8b759","kind":"delete"} {}`,
		"not json":      `kind=delete`,
	} {
		if _, err := decodeRequest([]byte(body)); err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
}
