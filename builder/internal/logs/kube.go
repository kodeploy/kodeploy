package logs

import (
	"context"
	"io"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"

	"github.com/kodeploy/kodeploy/builder/internal/job"
)

// KubeSource는 client-go로 빌드 Pod 로그를 연다.
type KubeSource struct {
	cs      kubernetes.Interface
	ns      string
	backoff time.Duration
}

func NewKubeSource(cs kubernetes.Interface, namespace string) *KubeSource {
	return &KubeSource{cs: cs, ns: namespace, backoff: time.Second}
}

// WaitPod는 build-id 라벨 Pod을 목록에서 찾고, 없으면 Watch로 생기기를 기다린다.
func (s *KubeSource) WaitPod(ctx context.Context, buildID string) (string, error) {
	sel := job.LabelBuildID + "=" + buildID
	for {
		l, err := s.cs.CoreV1().Pods(s.ns).List(ctx, metav1.ListOptions{LabelSelector: sel})
		if err == nil && len(l.Items) > 0 {
			return l.Items[0].Name, nil
		}
		if err == nil {
			var w watch.Interface
			w, err = s.cs.CoreV1().Pods(s.ns).Watch(ctx, metav1.ListOptions{LabelSelector: sel, ResourceVersion: l.ResourceVersion})
			if err == nil {
				name := firstPod(ctx, w)
				w.Stop()
				if name != "" {
					return name, nil
				}
			}
		}
		if !sleep(ctx, s.backoff) {
			return "", context.Cause(ctx)
		}
	}
}

func firstPod(ctx context.Context, w watch.Interface) string {
	for {
		select {
		case <-ctx.Done():
			return ""
		case ev, ok := <-w.ResultChan():
			if !ok {
				return ""
			}
			if p, ok := ev.Object.(*corev1.Pod); ok && (ev.Type == watch.Added || ev.Type == watch.Modified) {
				return p.Name
			}
		}
	}
}

func (s *KubeSource) Open(ctx context.Context, pod, container string, since time.Time) (io.ReadCloser, error) {
	opts := &corev1.PodLogOptions{Container: container, Follow: true, Timestamps: true}
	if !since.IsZero() {
		t := metav1.NewTime(since)
		opts.SinceTime = &t
	}
	return s.cs.CoreV1().Pods(s.ns).GetLogs(pod, opts).Stream(ctx)
}

func (s *KubeSource) State(ctx context.Context, pod, container string) (State, error) {
	p, err := s.cs.CoreV1().Pods(s.ns).Get(ctx, pod, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return State{Gone: true}, nil
	}
	if err != nil {
		return State{}, err
	}
	st := State{PodDone: p.Status.Phase == corev1.PodSucceeded || p.Status.Phase == corev1.PodFailed}
	for _, list := range [][]corev1.ContainerStatus{p.Status.InitContainerStatuses, p.Status.ContainerStatuses} {
		for _, cs := range list {
			if cs.Name != container {
				continue
			}
			st.Terminated = cs.State.Terminated != nil
			st.Started = st.Terminated || cs.State.Running != nil
		}
	}
	return st, nil
}
