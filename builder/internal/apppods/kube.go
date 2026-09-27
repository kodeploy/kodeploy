package apppods

import (
	"context"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
)

// 로그 끝 상한. 줄이 아주 길어도 콜백 본문이 커지지 않게 뒤쪽만 남긴다.
// (API의 limitBytes는 앞쪽을 남겨서, 원인이 찍히는 마지막 줄이 잘린다)
const logLimitBytes = 64 << 10

// KubeSource는 client-go로 앱 네임스페이스의 Pod과 로그를 읽는다.
type KubeSource struct {
	cs kubernetes.Interface
}

// NewKubeSource는 KubeSource를 만든다.
func NewKubeSource(cs kubernetes.Interface) *KubeSource {
	return &KubeSource{cs: cs}
}

// List는 네임스페이스의 Pod 전부다 (앱 Pod과 의존성 Pod을 한 번에 읽는다).
func (s *KubeSource) List(ctx context.Context, namespace string) ([]corev1.Pod, error) {
	l, err := s.cs.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, err
	}
	return l.Items, nil
}

// Logs는 컨테이너 로그 끝 tail줄이다. previous면 마지막으로 종료된 인스턴스.
func (s *KubeSource) Logs(ctx context.Context, namespace, pod, container string, previous bool, tail int64) (string, error) {
	b, err := s.cs.CoreV1().Pods(namespace).GetLogs(pod, &corev1.PodLogOptions{
		Container: container, Previous: previous, TailLines: &tail,
	}).DoRaw(ctx)
	if len(b) > logLimitBytes {
		b = b[len(b)-logLimitBytes:]
	}
	return string(b), err
}
