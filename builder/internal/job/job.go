// Package job은 BuildKit 빌드 Job을 만들고, 종료를 Watch로 기다리고, 어노테이션에 진행 상태를 적는다.
// Job 모양은 원본 core/app/deploy/stack/manifests/templates/buildkit_job.yaml.j2를 그대로 옮겼다
// (testdata/original-*.json이 그 렌더 결과이고 job_test.go가 둘을 비교한다).
package job

import (
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/utils/ptr"
)

// 원본 config.py·템플릿의 고정값
const (
	GHCRAuthSecret    = "ghcr-auth" // config.GHCR_AUTH_SECRET_NAME
	cloneImage        = "alpine/git:latest"
	ttlAfterFinished  = 1200         // 종료 후 20분 뒤 Job/Pod 자동 삭제
	defaultDockerfile = "Dockerfile" // build.py buildkit_job(dockerfile_filename="Dockerfile")
	InitContainer     = "clone"
	MainContainer     = "buildkit"
)

// 라벨: app·build-id·user-id·build-mode는 원본 그대로 (core가 build-id 라벨로 Pod을 찾는다)
const (
	LabelApp       = "app"
	LabelBuildID   = "build-id"
	LabelUserID    = "user-id"
	LabelBuildMode = "build-mode"
	LabelManaged   = "kodeploy.io/managed"
	appLabelValue  = "kodeploy-build"
)

// 진행 상태 어노테이션 (지시서 4-2, 4-5, 3-2)
const (
	AnnRequest   = "kodeploy.io/request"
	AnnDigest    = "kodeploy.io/digest"
	AnnCommitSHA = "kodeploy.io/commit-sha"
	AnnSynced    = "kodeploy.io/synced"
	AnnFinished  = "kodeploy.io/finished"
	AnnAckedSeq  = "kodeploy.io/acked-seq"
	// 재개할 때 이미 core에 보낸 로그 줄을 다시 보내지 않으려고 적는다 (지시서에 없는 추가분)
	AnnAckedLogLines = "kodeploy.io/acked-log-lines"
)

// 원본 init 컨테이너 스크립트 그대로. git_auth(private repo)는 이번 범위 밖이라 GIT_AUTH_TOKEN이
// 주입되지 않지만, 스크립트는 원본과 같게 두어 나중에 Secret만 붙이면 되게 한다.
const cloneScript = `set -eu
echo "[1/1] cloning ${REPO_URL} (${BRANCH})..."
# private repo: 토큰을 URL rewrite(insteadOf)로 주입 — REPO_URL/로그엔 토큰 안 보임(set -x 꺼짐)
if [ -n "${GIT_AUTH_TOKEN:-}" ]; then
  git config --global url."https://x-access-token:${GIT_AUTH_TOKEN}@github.com/".insteadOf "https://github.com/"
fi
git clone --depth 1 -b "$BRANCH" "$REPO_URL" /workspace/src
`

type Params struct {
	Namespace             string
	BuildID               string
	UserID                string // 32 hex
	Image                 string // <image_repo>:<image_tag>
	RepoURL               string
	Branch                string
	DockerfileSubdir      string
	DockerfileFilename    string // 비면 Dockerfile
	CacheRef              string // 비면 캐시 플래그 생략
	BuildKitImage         string
	ActiveDeadlineSeconds int64
	RequestJSON           string // kodeploy.io/request 어노테이션 (재개용)
}

// Name은 원본 _build_job_name: build-<user_id 앞 8자>-<build_id>
func Name(buildID, userID string) string {
	u := userID
	if len(u) > 8 {
		u = u[:8]
	}
	return "build-" + u + "-" + buildID
}

func Build(p Params) *batchv1.Job {
	filename := p.DockerfileFilename
	if filename == "" {
		filename = defaultDockerfile
	}
	src := "/workspace/src"
	if p.DockerfileSubdir != "" {
		src += "/" + p.DockerfileSubdir
	}
	args := []string{
		"build",
		"--frontend=dockerfile.v0",
		"--local=context=" + src,
		"--local=dockerfile=" + src,
		"--opt=filename=" + filename,
		"--output=type=image,name=" + p.Image + ",push=true",
	}
	if p.CacheRef != "" {
		args = append(args,
			"--import-cache=type=registry,ref="+p.CacheRef,
			"--export-cache=type=registry,ref="+p.CacheRef+",mode=max",
		)
	}

	workspace := corev1.VolumeMount{Name: "workspace", MountPath: "/workspace"}
	annotations := map[string]string{}
	if p.RequestJSON != "" {
		annotations[AnnRequest] = p.RequestJSON
	}

	return &batchv1.Job{
		TypeMeta: metav1.TypeMeta{APIVersion: "batch/v1", Kind: "Job"},
		ObjectMeta: metav1.ObjectMeta{
			Name:      Name(p.BuildID, p.UserID),
			Namespace: p.Namespace,
			Labels: map[string]string{
				LabelApp:       appLabelValue,
				LabelBuildID:   p.BuildID,
				LabelUserID:    p.UserID,
				LabelBuildMode: "dockerfile",
				LabelManaged:   "true",
			},
			Annotations: annotations,
		},
		Spec: batchv1.JobSpec{
			TTLSecondsAfterFinished: ptr.To[int32](ttlAfterFinished),
			BackoffLimit:            ptr.To[int32](0),
			ActiveDeadlineSeconds:   ptr.To(p.ActiveDeadlineSeconds),
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: map[string]string{LabelApp: appLabelValue, LabelBuildID: p.BuildID},
				},
				Spec: corev1.PodSpec{
					RestartPolicy: corev1.RestartPolicyNever,
					InitContainers: []corev1.Container{{
						Name:    InitContainer,
						Image:   cloneImage,
						Command: []string{"sh", "-c"},
						Env: []corev1.EnvVar{
							{Name: "REPO_URL", Value: p.RepoURL},
							{Name: "BRANCH", Value: p.Branch},
						},
						Args:         []string{cloneScript},
						VolumeMounts: []corev1.VolumeMount{workspace},
					}},
					Containers: []corev1.Container{{
						Name:  MainContainer,
						Image: p.BuildKitImage,
						SecurityContext: &corev1.SecurityContext{
							SeccompProfile: &corev1.SeccompProfile{Type: corev1.SeccompProfileTypeUnconfined},
							RunAsUser:      ptr.To[int64](1000),
							RunAsGroup:     ptr.To[int64](1000),
						},
						Command: []string{"buildctl-daemonless.sh"},
						Args:    args,
						Env: []corev1.EnvVar{
							{Name: "BUILDKITD_FLAGS", Value: "--oci-worker-no-process-sandbox"},
						},
						VolumeMounts: []corev1.VolumeMount{
							workspace,
							{Name: "docker-config", MountPath: "/home/user/.docker", ReadOnly: true},
						},
					}},
					Volumes: []corev1.Volume{
						{Name: "workspace", VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}}},
						{Name: "docker-config", VolumeSource: corev1.VolumeSource{Secret: &corev1.SecretVolumeSource{
							SecretName: GHCRAuthSecret,
							Items:      []corev1.KeyToPath{{Key: ".dockerconfigjson", Path: "config.json"}},
						}}},
					},
				},
			},
		},
	}
}
