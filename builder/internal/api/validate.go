package api

import (
	"errors"
	"fmt"
	"regexp"
	"strings"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

// Validator는 서명 검증을 통과한 요청의 내용을 검사한다 (지시서 4-1 + 입력 검증 추가분).
type Validator struct {
	GHCRUser string // 소문자
}

var (
	// 원본 build_id = uuid4().hex[:8] (pipeline.py start_deploy)
	buildIDRe   = regexp.MustCompile(`^[a-f0-9]{8}$`)
	namespaceRe = regexp.MustCompile(`^(tenant|app)-([a-f0-9]{8})$`)
	actorRe     = regexp.MustCompile(`^[a-f0-9]{8,32}$`)

	// ghcr.io/<user>/<hex8>/<app> (원본 pipeline.py: f"ghcr.io/{GHCR_USER}/{user.id.hex[:8]}/{app_name}")
	imageRepoRe = regexp.MustCompile(`^ghcr\.io/([^/:@]+)/([^/:@]+)/([^/:@]+)$`)
	appSegRe    = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]*$`)
	ghcrUserRe  = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)
	hex8Re      = regexp.MustCompile(`^[a-f0-9]{8}$`)
	// 도커 태그 규칙
	tagRe = regexp.MustCompile(`^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$`)
	// <repo>:<tag>@sha256:<64hex>
	pinnedImageRe = regexp.MustCompile(`^([^:@]+):([^:@]+)@sha256:([a-f0-9]{64})$`)

	// 입력 검증 추가분: git clone 인자로 들어가는 값
	repoURLRe = regexp.MustCompile(`^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$`)
	refRe     = regexp.MustCompile(`^[A-Za-z0-9._/-]{1,255}$`)
	pathSegRe = regexp.MustCompile(`^[A-Za-z0-9._-]{1,255}$`)

	// 차트 values.schema.json과 같은 규칙 (kodeploy-charts/charts/app/values.schema.json).
	// 틀린 값을 커밋하면 Argo 렌더가 멈추므로 여기서 먼저 400으로 돌려준다.
	valueNameRe      = regexp.MustCompile(`^$|^[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?$`)
	valueUserIDRe    = regexp.MustCompile(`^$|^[a-f0-9]{32}$`)
	valueMountPathRe = regexp.MustCompile(`^$|^/[^\s]*$`)
	valueHostnameRe  = regexp.MustCompile(`^([a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?\.)+[a-z]{2,}$`)
)

var (
	serverRuntimes = map[string]bool{"python": true, "java": true, "php": true, "javascript": true}
	dbTypes        = map[string]bool{"none": true, "mysql": true, "postgres": true}
)

const maxHostnames = 10

// ValidationError → 400. 메시지는 core에 그대로 돌려준다(비밀값 없음).
type ValidationError struct{ Msg string }

func (e *ValidationError) Error() string { return e.Msg }

func invalid(format string, args ...any) error {
	return &ValidationError{Msg: fmt.Sprintf(format, args...)}
}

func notSupported(what string) error {
	return &ValidationError{Msg: what + ": not supported yet"}
}

// NamespaceHex8는 namespace의 hex8 부분이다. 형식이 틀리면 "".
func NamespaceHex8(ns string) string {
	m := namespaceRe.FindStringSubmatch(ns)
	if m == nil {
		return ""
	}
	return m[2]
}

func ValidBuildID(id string) bool { return buildIDRe.MatchString(id) }

func (v Validator) Validate(r *contract.DeployRequest) error {
	if !buildIDRe.MatchString(r.BuildID) {
		return invalid("build_id must match %s", buildIDRe)
	}
	nsHex := NamespaceHex8(r.Namespace)
	if nsHex == "" {
		return invalid("namespace must match %s", namespaceRe)
	}
	if r.Actor != "" && !actorRe.MatchString(r.Actor) {
		return invalid("actor must match %s", actorRe)
	}
	switch r.Slot {
	case contract.SlotServer, contract.SlotStatic:
	case "":
		if r.Kind != contract.KindDelete {
			return invalid("slot is required")
		}
	default:
		return invalid("slot must be server or static")
	}
	if r.Values != nil {
		if err := validateValues(r.Values); err != nil {
			return err
		}
	}

	switch r.Kind {
	case contract.KindBuild:
		return v.validateBuild(r, nsHex)
	case contract.KindSetImage:
		return v.validateSetImage(r, nsHex)
	case contract.KindConfig:
		if r.Build != nil || r.Image != "" || r.Unit != nil {
			return invalid("config takes only values")
		}
		if r.Values == nil {
			return invalid("config requires values")
		}
		return nil
	case contract.KindDelete:
		if r.Values != nil || r.Unit != nil || r.Build != nil || r.Image != "" {
			return invalid("delete must not carry values, unit, build or image")
		}
		return nil
	default:
		return invalid("kind must be build, config, set-image or delete")
	}
}

func (v Validator) validateBuild(r *contract.DeployRequest, nsHex string) error {
	if r.Slot == contract.SlotStatic {
		return notSupported("static build")
	}
	if r.Build == nil {
		return invalid("build is required for kind=build")
	}
	if r.Image != "" {
		return invalid("image is not allowed for kind=build")
	}
	if err := validateUnit(r.Unit); err != nil {
		return err
	}
	// Job 이름·user-id 라벨에 쓴다 (원본 _build_job_name = build-<user_id[:8]>-<build_id>)
	if r.Values == nil || r.Values.UserID == nil || *r.Values.UserID == "" {
		return invalid("values.userId is required for kind=build")
	}

	b := r.Build
	if b.Mode != "dockerfile" {
		return notSupported(fmt.Sprintf("build.mode %q", b.Mode))
	}
	if b.GitAuthSecret != "" {
		return notSupported("build.git_auth_secret")
	}
	if b.ProjectPath != "" {
		return notSupported("build.project_path")
	}
	if err := validateRepoURL(b.Repo); err != nil {
		return err
	}
	if err := validateRef(b.Ref); err != nil {
		return err
	}
	if err := validateRelDir(b.DockerfileDir); err != nil {
		return err
	}
	if b.DockerfileName != "" && !validPathSeg(b.DockerfileName) {
		return invalid("build.dockerfile_name must be a plain file name")
	}
	if err := v.validateImageRepo(b.ImageRepo, nsHex, "build.image_repo"); err != nil {
		return err
	}
	if !tagRe.MatchString(b.ImageTag) {
		return invalid("build.image_tag must match %s", tagRe)
	}
	// 원본 _cache_ref: 이미지와 같은 repo의 :buildcache 태그
	if b.CacheRef != "" && b.CacheRef != b.ImageRepo+":buildcache" {
		return invalid("build.cache_ref must be <image_repo>:buildcache")
	}
	return nil
}

func (v Validator) validateSetImage(r *contract.DeployRequest, nsHex string) error {
	if r.Build != nil {
		return invalid("build is not allowed for kind=set-image")
	}
	m := pinnedImageRe.FindStringSubmatch(r.Image)
	if m == nil {
		return invalid("image must be <repo>:<tag>@sha256:<64hex>")
	}
	if err := v.validateImageRepo(m[1], nsHex, "image"); err != nil {
		return err
	}
	if !tagRe.MatchString(m[2]) {
		return invalid("image tag must match %s", tagRe)
	}
	if r.Slot == contract.SlotStatic {
		if r.Unit != nil {
			return invalid("unit is only for the server slot")
		}
		return nil
	}
	return validateUnit(r.Unit)
}

func (v Validator) validateImageRepo(repo, nsHex, field string) error {
	m := imageRepoRe.FindStringSubmatch(repo)
	if m == nil {
		return invalid("%s must be ghcr.io/<user>/<hex8>/<app>", field)
	}
	if !ghcrUserRe.MatchString(m[1]) || m[1] != v.GHCRUser {
		return invalid("%s must be under ghcr.io/%s/", field, v.GHCRUser)
	}
	if !hex8Re.MatchString(m[2]) || m[2] != nsHex {
		return invalid("%s hex8 segment must match the namespace", field)
	}
	if !appSegRe.MatchString(m[3]) {
		return invalid("%s app segment is invalid", field)
	}
	return nil
}

func validateUnit(u *contract.Unit) error {
	if u == nil {
		return invalid("unit is required for the server slot")
	}
	if !serverRuntimes[u.Runtime] {
		return invalid("unit.runtime must be python, java, php or javascript")
	}
	if u.Port < 1 || u.Port > 65535 {
		return invalid("unit.port must be 1..65535")
	}
	return nil
}

// repo는 https://github.com/<owner>/<repo>(.git 선택)만 받는다.
func validateRepoURL(s string) error {
	m := repoURLRe.FindStringSubmatch(s)
	if m == nil {
		return invalid("build.repo must be https://github.com/<owner>/<repo>")
	}
	if strings.HasPrefix(m[1], "-") {
		return invalid("build.repo owner is invalid")
	}
	name := strings.TrimSuffix(m[2], ".git")
	if name == "" || name == "." || name == ".." {
		return invalid("build.repo name is invalid")
	}
	return nil
}

// ref는 git clone -b 인자로 들어간다. -로 시작하면 옵션으로 해석되므로 거부한다.
func validateRef(s string) error {
	switch {
	case !refRe.MatchString(s):
		return invalid("build.ref has characters outside [A-Za-z0-9._/-]")
	case strings.HasPrefix(s, "-"), strings.HasPrefix(s, "/"):
		return invalid("build.ref must not start with - or /")
	case strings.Contains(s, ".."), strings.Contains(s, "//"):
		return invalid("build.ref must not contain .. or //")
	case strings.HasSuffix(s, "/"), strings.HasSuffix(s, "."), strings.HasSuffix(s, ".lock"):
		return invalid("build.ref has an invalid ending")
	}
	return nil
}

// dockerfile_dir는 repo 안의 상대 경로다. ..와 절대 경로를 거부한다.
func validateRelDir(s string) error {
	if s == "" {
		return nil
	}
	if strings.HasPrefix(s, "/") {
		return invalid("build.dockerfile_dir must be relative")
	}
	for _, seg := range strings.Split(s, "/") {
		if !validPathSeg(seg) {
			return invalid("build.dockerfile_dir must not contain .., empty or unusual segments")
		}
	}
	return nil
}

func validPathSeg(s string) bool {
	return pathSegRe.MatchString(s) && s != ".."
}

func validateValues(v *contract.CoreValues) error {
	if v.Name != nil && !valueNameRe.MatchString(*v.Name) {
		return invalid("values.name must match %s", valueNameRe)
	}
	if v.UserID != nil && !valueUserIDRe.MatchString(*v.UserID) {
		return invalid("values.userId must be 32 hex")
	}
	if v.Replicas != nil && *v.Replicas != 1 {
		return invalid("values.replicas must be 1")
	}
	if v.EnvRevision != nil && *v.EnvRevision < 0 {
		return invalid("values.envRevision must be >= 0")
	}
	if v.DB != nil && !dbTypes[*v.DB] {
		return invalid("values.db must be none, mysql or postgres")
	}
	if v.Volume != nil && v.Volume.MountPath != nil && !valueMountPathRe.MatchString(*v.Volume.MountPath) {
		return invalid("values.volume.mountPath must be an absolute path")
	}
	if err := validateHostnames("values.hostnames", v.Hostnames); err != nil {
		return err
	}
	if v.Static != nil {
		if err := validateHostnames("values.static.hostnames", v.Static.Hostnames); err != nil {
			return err
		}
	}
	return nil
}

func validateHostnames(field string, hs *[]string) error {
	if hs == nil {
		return nil
	}
	if len(*hs) > maxHostnames {
		return invalid("%s allows at most %d entries", field, maxHostnames)
	}
	for _, h := range *hs {
		if !valueHostnameRe.MatchString(h) {
			return invalid("%s has an invalid hostname", field)
		}
	}
	return nil
}

// IsValidation은 err가 400으로 돌려줄 오류인지다.
func IsValidation(err error) bool {
	var ve *ValidationError
	var fe *contract.ForbiddenKeyError
	return errors.As(err, &ve) || errors.As(err, &fe)
}
