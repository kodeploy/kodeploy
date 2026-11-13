// Package target은 exec할 Pod·컨테이너를 고른다. 자기 ns 밖은 조회하지 않는다.
//
// 라벨·컨테이너 규칙은 원본 terminal.py(_find_app_pod)·snapshots.py(_detect_target)를 그대로 옮겼다.
//   - app:   app=<앱 이름>, 컨테이너 app
//   - db:    app=mysql, 없으면 app=postgres (mysql 먼저), 컨테이너는 같은 이름
//   - redis: app=redis, 컨테이너 redis
//
// 고르는 기준은 Running 중 가장 최근 것이다. 지워지는 중(deletionTimestamp)인 Pod는 뺀다.
package target

import (
	"context"
	"errors"
	"regexp"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

// Kind는 대상의 종류다. 명령 조립(ops)이 이걸로 갈린다.
type Kind string

const (
	App      Kind = "app"
	MySQL    Kind = "mysql"
	Postgres Kind = "postgres"
	Redis    Kind = "redis"
)

// Target은 exec할 곳이다.
type Target struct {
	Pod       string
	Container string
	Kind      Kind
}

var (
	ErrNotFound   = errors.New("target pod not found")
	ErrBadTarget  = errors.New("unknown target")
	ErrBadAppName = errors.New("invalid app name")
)

// 차트 values.schema.json의 name 규칙과 같다 (DNS-1123, 최대 40자).
var appName = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]{0,38}[a-z0-9])?$`)

// ValidAppName은 앱 이름 형식을 검사한다. 셸 프롬프트에 들어가므로 형식 밖은 받지 않는다.
func ValidAppName(name string) bool { return appName.MatchString(name) }

// Finder는 자기 ns 하나만 조회한다.
type Finder struct {
	client kubernetes.Interface
	ns     string
}

// NewFinder는 ns 하나에 묶인 Finder를 만든다.
func NewFinder(client kubernetes.Interface, ns string) *Finder {
	return &Finder{client: client, ns: ns}
}

// Find는 target(app|db|redis)에 맞는 Pod를 고른다. app은 target=app일 때만 쓴다.
func (f *Finder) Find(ctx context.Context, target, app string) (Target, error) {
	switch target {
	case "app":
		if !ValidAppName(app) {
			return Target{}, ErrBadAppName
		}
		pod, err := f.newest(ctx, "app="+app)
		if err != nil {
			return Target{}, err
		}
		return Target{Pod: pod, Container: "app", Kind: App}, nil
	case "db":
		for _, k := range []Kind{MySQL, Postgres} {
			pod, err := f.newest(ctx, "app="+string(k))
			if errors.Is(err, ErrNotFound) {
				continue
			}
			if err != nil {
				return Target{}, err
			}
			return Target{Pod: pod, Container: string(k), Kind: k}, nil
		}
		return Target{}, ErrNotFound
	case "redis":
		pod, err := f.newest(ctx, "app=redis")
		if err != nil {
			return Target{}, err
		}
		return Target{Pod: pod, Container: "redis", Kind: Redis}, nil
	}
	return Target{}, ErrBadTarget
}

func (f *Finder) newest(ctx context.Context, selector string) (string, error) {
	list, err := f.client.CoreV1().Pods(f.ns).List(ctx, metav1.ListOptions{LabelSelector: selector})
	if err != nil {
		return "", err
	}
	var best *corev1.Pod
	for i := range list.Items {
		p := &list.Items[i]
		if p.Status.Phase != corev1.PodRunning || p.DeletionTimestamp != nil {
			continue
		}
		if best == nil || started(p).After(started(best).Time) {
			best = p
		}
	}
	if best == nil {
		return "", ErrNotFound
	}
	return best.Name, nil
}

// started는 원본과 같이 status.startTime, 없으면 creationTimestamp다.
func started(p *corev1.Pod) metav1.Time {
	if p.Status.StartTime != nil {
		return *p.Status.StartTime
	}
	return p.CreationTimestamp
}
