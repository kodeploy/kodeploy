package apppods

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

const (
	ns    = "tenant-d6d8b759"
	image = "ghcr.io/yuntyu01/d6d8b759/app:3f9a2c1d@sha256:aaaa"
)

var since = time.Date(2026, 9, 28, 12, 0, 0, 0, time.UTC)

func target() Target { return Target{Namespace: ns, Image: image, Since: since} }

// appPod은 이번 배포 이미지의 앱 Pod이다 (since 1분 뒤 생성).
func appPod(name string, restarts int32) corev1.Pod {
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns, Labels: map[string]string{"app": "app"},
			CreationTimestamp: metav1.NewTime(since.Add(time.Minute))},
		Spec: corev1.PodSpec{Containers: []corev1.Container{{Name: Container, Image: image}}},
		Status: corev1.PodStatus{ContainerStatuses: []corev1.ContainerStatus{{
			Name: Container, RestartCount: restarts,
			LastTerminationState: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 1}},
		}}},
	}
}

func depPod(component string, isReady bool) corev1.Pod {
	st := corev1.ConditionFalse
	if isReady {
		st = corev1.ConditionTrue
	}
	return corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: component + "-0", Namespace: ns, Labels: map[string]string{"component": component}},
		Status:     corev1.PodStatus{Conditions: []corev1.PodCondition{{Type: corev1.PodReady, Status: st}}},
	}
}

func waiting(p corev1.Pod, reason string) corev1.Pod {
	p.Status.ContainerStatuses[0].State.Waiting = &corev1.ContainerStateWaiting{Reason: reason}
	return p
}

func TestJudgeCountsRestartsAfterDepsReady(t *testing.T) {
	base := map[string]int32{}
	// DB가 뜨기 전 재시작은 세지 않는다
	if p := judge([]corev1.Pod{appPod("a", 3), depPod("mysql", false)}, target(), base); p != nil || len(base) != 0 {
		t.Fatalf("counted before deps ready: %+v base=%v", p, base)
	}
	// 준비된 순간의 재시작 수가 기준
	if p := judge([]corev1.Pod{appPod("a", 3), depPod("mysql", true)}, target(), base); p != nil || base["a"] != 3 {
		t.Fatalf("first ready poll: %+v base=%v", p, base)
	}
	if p := judge([]corev1.Pod{appPod("a", 4), depPod("mysql", true)}, target(), base); p != nil {
		t.Fatalf("one restart is not enough: %+v", p)
	}
	p := judge([]corev1.Pod{appPod("a", 5), depPod("mysql", true)}, target(), base)
	if p == nil || p.Pod != "a" || p.Reason != "the app exited during startup (exit code 1); stopped waiting after 5 restarts" {
		t.Fatalf("problem %+v", p)
	}
}

func TestJudgeFatalWaitingIgnoresDeps(t *testing.T) {
	p := judge([]corev1.Pod{waiting(appPod("a", 0), "ImagePullBackOff"), depPod("postgres", false)}, target(), map[string]int32{})
	if p == nil || p.Reason != "the image could not be pulled (ImagePullBackOff)" {
		t.Fatalf("problem %+v", p)
	}
	// ErrImagePull·CrashLoopBackOff는 그 자체로 실패가 아니다
	for _, r := range []string{"ErrImagePull", "CrashLoopBackOff", "ContainerCreating"} {
		if p := judge([]corev1.Pod{waiting(appPod("a", 0), r)}, target(), map[string]int32{}); p != nil {
			t.Fatalf("%s: %+v", r, p)
		}
	}
}

func TestJudgeOnlyThisRollout(t *testing.T) {
	old := appPod("old", 0)
	old.CreationTimestamp = metav1.NewTime(since.Add(-time.Minute)) // config 배포 전부터 크래시하던 Pod
	other := appPod("other", 0)
	other.Spec.Containers[0].Image = "ghcr.io/yuntyu01/d6d8b759/app:old@sha256:bbbb"
	gone := appPod("gone", 0)
	now := metav1.Now()
	gone.DeletionTimestamp = &now
	skew := appPod("skew", 0)
	skew.CreationTimestamp = metav1.NewTime(since.Add(-2 * time.Second)) // 시계 차이 여유 안

	base := map[string]int32{}
	pods := []corev1.Pod{old, other, gone, skew}
	judge(pods, target(), base)
	if len(base) != 1 || base["skew"] != 0 {
		t.Fatalf("base %v", base)
	}
	for i := range pods[:3] {
		pods[i].Status.ContainerStatuses[0].RestartCount = 9
	}
	if p := judge(pods, target(), base); p != nil {
		t.Fatalf("judged a pod outside this rollout: %+v", p)
	}
}

func TestCrashMessage(t *testing.T) {
	term := func(reason string, code int32) *corev1.ContainerStatus {
		return &corev1.ContainerStatus{RestartCount: 2, LastTerminationState: corev1.ContainerState{
			Terminated: &corev1.ContainerStateTerminated{Reason: reason, ExitCode: code}}}
	}
	cases := map[string]*corev1.ContainerStatus{
		"the app ran out of memory and was killed (OOMKilled)": term("OOMKilled", 137),
		"(exit code 143); check the port setting":              term("Error", 143),
		"the app exited during startup (exit code 3)":          term("Error", 3),
		"the app keeps restarting":                             {RestartCount: 2},
	}
	for want, cs := range cases {
		if got := crashMessage(cs); !strings.Contains(got, want) || !strings.HasSuffix(got, "after 2 restarts") {
			t.Errorf("%q does not contain %q", got, want)
		}
	}
}

type fakeSource struct {
	pods []corev1.Pod
	logs map[bool]string // previous → 본문
	err  map[bool]error
	got  []bool
}

func (f *fakeSource) List(context.Context, string) ([]corev1.Pod, error) { return f.pods, nil }

func (f *fakeSource) Logs(_ context.Context, _, _, c string, previous bool, tail int64) (string, error) {
	if c != Container || tail != TailLines {
		return "", errors.New("wrong container or tail")
	}
	f.got = append(f.got, previous)
	return f.logs[previous], f.err[previous]
}

func TestTail(t *testing.T) {
	crashed := appPod("a", 2)
	fresh := appPod("b", 0)
	fresh.CreationTimestamp = metav1.NewTime(since.Add(2 * time.Minute))
	src := &fakeSource{pods: []corev1.Pod{crashed, fresh},
		logs: map[bool]string{true: "boot\nCaused by: LazyInitializationException\n", false: "starting\n"}}
	w := NewWatch(src, target())

	got := w.Tail(context.Background(), "a")
	want := []string{"=== app logs (a, last terminated instance) ===", "boot", "Caused by: LazyInitializationException"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("tail %q", got)
	}
	// Pod을 안 정하면 가장 새 Pod, 재시작 없으면 현재 인스턴스
	got = w.Tail(context.Background(), "")
	if len(got) != 2 || got[0] != "=== app logs (b, current instance) ===" || got[1] != "starting" {
		t.Fatalf("tail %q", got)
	}
	// 직전 인스턴스 로그를 못 읽으면 현재 인스턴스로
	src.err = map[bool]error{true: errors.New("previous terminated container not found")}
	src.got = nil
	got = w.Tail(context.Background(), "a")
	if len(got) != 2 || got[0] != "=== app logs (a, current instance) ===" || len(src.got) != 2 {
		t.Fatalf("tail %q calls %v", got, src.got)
	}
	if w.Tail(context.Background(), "missing") != nil {
		t.Fatal("tail of an unknown pod")
	}
}

func TestKubeSource(t *testing.T) {
	p := appPod("a", 0)
	s := NewKubeSource(fake.NewClientset(&p))
	pods, err := s.List(context.Background(), ns)
	if err != nil || len(pods) != 1 || pods[0].Name != "a" {
		t.Fatalf("list %v %v", pods, err)
	}
	text, err := s.Logs(context.Background(), ns, "a", Container, false, TailLines)
	if err != nil || text == "" {
		t.Fatalf("logs %q %v", text, err)
	}
}
