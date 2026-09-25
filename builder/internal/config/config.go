// Package config는 빌더 설정을 환경변수에서 읽는다.
// 원본(core/app/config.py)에 같은 값이 있으면 기본값과 규칙을 그대로 따른다.
package config

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	Listen string

	CoreURL    string
	HMACSecret []byte

	GitHubToken  string
	GitOpsRepo   string // owner/name
	GitOpsBranch string

	GHCRToken string
	GHCRUser  string // 이미지 경로 검증용. GHCR 경로는 소문자라 소문자로 저장

	BuildNamespace  string
	ArgoCDNamespace string

	BuildKitImage              string
	BuildActiveDeadlineSeconds int64
	// 원본 EARLY_TRIGGER 플래그. 끄면 push 마커를 무시하고 Job 종료 후 배포한다.
	EarlyTrigger bool

	ArgoWaitTimeout  time.Duration
	LogBatchInterval time.Duration
	MaxActiveBuilds  int
}

// 원본 config.py의 BUILDKIT_IMAGE (클러스터에서 검증된 버전으로 고정)
const DefaultBuildKitImage = "moby/buildkit:v0.30.0-rootless"

// 원본 BUILD_TIMEOUT_SECONDS 기본값. 빌더는 activeDeadline 기본값을 만들 때만 쓴다.
const defaultBuildTimeoutSeconds = 600

// Load는 getenv로 설정을 읽는다. 테스트에서 os.Getenv 대신 맵을 넘길 수 있게 함수로 받는다.
func Load(getenv func(string) string) (*Config, error) {
	var errs []error
	str := func(key, def string) string {
		if v := strings.TrimSpace(getenv(key)); v != "" {
			return v
		}
		return def
	}
	required := func(key string) string {
		v := strings.TrimSpace(getenv(key))
		if v == "" {
			errs = append(errs, fmt.Errorf("%s is required", key))
		}
		return v
	}
	integer := func(key string, def int64) int64 {
		v := strings.TrimSpace(getenv(key))
		if v == "" {
			return def
		}
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: not an integer", key))
			return def
		}
		return n
	}
	duration := func(key string, def time.Duration) time.Duration {
		v := strings.TrimSpace(getenv(key))
		if v == "" {
			return def
		}
		d, err := time.ParseDuration(v)
		if err != nil || d <= 0 {
			errs = append(errs, fmt.Errorf("%s: not a positive duration", key))
			return def
		}
		return d
	}

	c := &Config{
		Listen:          str("LISTEN", ":8080"),
		CoreURL:         strings.TrimRight(required("CORE_URL"), "/"),
		HMACSecret:      []byte(required("HMAC_SECRET")),
		GitHubToken:     required("GITHUB_TOKEN"),
		GitOpsRepo:      str("GITOPS_REPO", "kodeploy/kodeploy-apps"),
		GitOpsBranch:    str("GITOPS_BRANCH", "main"),
		GHCRToken:       required("GHCR_TOKEN"),
		GHCRUser:        strings.ToLower(required("GHCR_USER")),
		BuildNamespace:  str("BUILD_NAMESPACE", "kodeploy-build"),
		ArgoCDNamespace: str("ARGOCD_NAMESPACE", "argocd"),
		BuildKitImage:   str("BUILDKIT_IMAGE", DefaultBuildKitImage),
		// 원본: EARLY_TRIGGER == "true"일 때만 켠다. 빌더는 라이브 설정(ON)을 기본으로 둔다.
		EarlyTrigger:     strings.ToLower(str("EARLY_TRIGGER", "true")) == "true",
		ArgoWaitTimeout:  duration("ARGO_WAIT_TIMEOUT", 8*time.Minute),
		LogBatchInterval: duration("LOG_BATCH_INTERVAL", time.Second),
		MaxActiveBuilds:  int(integer("MAX_ACTIVE_BUILDS", 3)),
	}
	// 원본 규칙: BUILD_ACTIVE_DEADLINE_SECONDS 기본값 = BUILD_TIMEOUT_SECONDS + 300
	timeout := integer("BUILD_TIMEOUT_SECONDS", defaultBuildTimeoutSeconds)
	c.BuildActiveDeadlineSeconds = integer("BUILD_ACTIVE_DEADLINE_SECONDS", timeout+300)

	if c.BuildActiveDeadlineSeconds <= 0 {
		errs = append(errs, errors.New("BUILD_ACTIVE_DEADLINE_SECONDS must be positive"))
	}
	if c.MaxActiveBuilds < 1 {
		errs = append(errs, errors.New("MAX_ACTIVE_BUILDS must be at least 1"))
	}
	if c.CoreURL != "" && !strings.HasPrefix(c.CoreURL, "http://") && !strings.HasPrefix(c.CoreURL, "https://") {
		errs = append(errs, errors.New("CORE_URL must start with http:// or https://"))
	}
	if strings.Count(c.GitOpsRepo, "/") != 1 {
		errs = append(errs, errors.New("GITOPS_REPO must be owner/name"))
	}
	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	return c, nil
}
