// Package apppods는 배포 뒤 앱 Pod이 반복해서 죽는지 보고, 죽은 인스턴스의 로그 끝을 가져온다.
// Argo는 롤아웃이 끝난 뒤 크래시하는 앱을 Degraded가 아니라 Progressing으로 보고, 롤아웃 중이라도
// progressDeadlineSeconds(기본 600초)가 지나야 Degraded가 된다. 그래서 Pod 상태로 먼저 가른다.
// 판정 규칙은 원본 core pipeline._crash_reason과 같다.
//
// 권한: 빌더에는 ClusterRole kodeploy-builder-app-reader(pods get/list, pods/log get)만 있고,
// 바인딩은 kodeploy-charts가 앱 네임스페이스마다 RoleBinding으로 그린다 (Argo가 관리하는 앱만 읽힌다).
package apppods

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
)

const (
	// 의존성(DB·Redis)이 준비된 뒤 이만큼 더 재시작하면 실패로 본다 (원본 CRASH_RESTART_LIMIT).
	// 첫 배포는 DB가 뜨기 전에 앱이 몇 번 죽는 게 정상이라 그동안의 재시작은 세지 않는다.
	RestartLimit = 2
	// 로그 끝 줄 수 (원본 APP_LOG_TAIL_LINES)
	TailLines = 80
	// 차트의 앱 컨테이너 이름 (server·static 둘 다)
	Container = "app"
	// 새 Pod 판정 여유. 빌더와 API 서버 시계 차이를 흡수한다.
	sinceSlack = 5 * time.Second
)

// 의존성 Pod 라벨 (차트 mysql·postgres·redis 템플릿의 component)
var depComponents = map[string]bool{"mysql": true, "postgres": true, "redis": true}

// 기다려도 스스로 풀리지 않는 대기 사유. 보는 즉시 실패 (원본 _FATAL_WAITING).
var fatalWaiting = map[string]string{
	"ImagePullBackOff":           "the image could not be pulled",
	"InvalidImageName":           "the image name is invalid",
	"CreateContainerConfigError": "the container config is invalid",
}

// Source는 네임스페이스의 Pod 목록과 컨테이너 로그다 (KubeSource).
type Source interface {
	List(ctx context.Context, namespace string) ([]corev1.Pod, error)
	Logs(ctx context.Context, namespace, pod, container string, previous bool, tail int64) (string, error)
}

// Target은 이번 배포의 앱 Pod을 고르는 기준이다.
// 앱 컨테이너 이미지가 Image와 같고, Since 뒤에 만들어진 Pod만 본다.
// 시각 조건이 있어야 config(이미지 그대로)로 크래시를 고치는 배포에서 옛 Pod의 크래시를 세지 않는다.
type Target struct {
	Namespace string
	Image     string
	Since     time.Time
}

// Problem은 실패로 본 Pod과 이유다.
type Problem struct {
	Pod    string
	Reason string
}

// Watch는 한 번의 배포 대기 동안 Pod 상태를 본다. 재시작 기준값을 호출 사이에 이어 쓴다.
// 한 고루틴에서만 쓴다.
type Watch struct {
	src  Source
	t    Target
	base map[string]int32 // Pod 이름 → 의존성이 준비됐을 때의 재시작 횟수
}

// NewWatch는 Watch를 만든다.
func NewWatch(src Source, t Target) *Watch {
	return &Watch{src: src, t: t, base: map[string]int32{}}
}

// Check는 Pod 목록을 한 번 읽고 판정한다. 문제가 없으면 nil.
func (w *Watch) Check(ctx context.Context) (*Problem, error) {
	pods, err := w.src.List(ctx, w.t.Namespace)
	if err != nil {
		return nil, err
	}
	return judge(pods, w.t, w.base), nil
}

// Tail은 로그 끝을 머리줄과 함께 돌려준다. pod가 비면 이번 배포의 가장 새 Pod을 쓴다.
// 재시작한 적이 있으면 마지막으로 종료된 인스턴스 로그, 아니면 현재 인스턴스 로그 (원본 _app_log_tail).
// 못 읽으면 nil (실패 보고를 막지 않는다).
func (w *Watch) Tail(ctx context.Context, pod string) []string {
	pods, err := w.src.List(ctx, w.t.Namespace)
	if err != nil {
		return nil
	}
	var p *corev1.Pod
	for _, c := range targets(pods, w.t) {
		if c.Name == pod || (pod == "" && (p == nil || c.CreationTimestamp.After(p.CreationTimestamp.Time))) {
			p = c
		}
	}
	if p == nil {
		return nil
	}
	restarted := false
	if cs := appStatus(p); cs != nil {
		restarted = cs.RestartCount > 0
	}
	label := "current instance"
	var text string
	if restarted {
		text, err = w.src.Logs(ctx, w.t.Namespace, p.Name, Container, true, TailLines)
		label = "last terminated instance"
	}
	if !restarted || err != nil || text == "" {
		text, err = w.src.Logs(ctx, w.t.Namespace, p.Name, Container, false, TailLines)
		label = "current instance"
	}
	text = strings.TrimRight(text, "\n")
	if err != nil || text == "" {
		return nil
	}
	return append([]string{fmt.Sprintf("=== app logs (%s, %s) ===", p.Name, label)}, strings.Split(text, "\n")...)
}

// judge는 순수 판정이다 (원본 _crash_reason). base는 호출 사이에 이어 쓴다.
func judge(pods []corev1.Pod, t Target, base map[string]int32) *Problem {
	depsReady := true
	for i := range pods {
		if depComponents[pods[i].Labels["component"]] && !ready(&pods[i]) {
			depsReady = false
		}
	}
	for _, p := range targets(pods, t) {
		cs := appStatus(p)
		if cs == nil {
			continue
		}
		if cs.State.Waiting != nil {
			if why, ok := fatalWaiting[cs.State.Waiting.Reason]; ok {
				return &Problem{Pod: p.Name, Reason: fmt.Sprintf("%s (%s)", why, cs.State.Waiting.Reason)}
			}
		}
		if !depsReady {
			continue
		}
		b, ok := base[p.Name]
		if !ok {
			b = cs.RestartCount
			base[p.Name] = b
		}
		if cs.RestartCount-b >= RestartLimit {
			return &Problem{Pod: p.Name, Reason: crashMessage(cs)}
		}
	}
	return nil
}

// targets는 이번 배포의 앱 Pod이다 (지워지는 중인 것 제외). 이름순으로 돌려 판정 순서를 고정한다.
func targets(pods []corev1.Pod, t Target) []*corev1.Pod {
	since := t.Since.Add(-sinceSlack)
	var out []*corev1.Pod
	for i := range pods {
		p := &pods[i]
		if p.DeletionTimestamp != nil || p.CreationTimestamp.Time.Before(since) {
			continue
		}
		for _, c := range p.Spec.Containers {
			if c.Name == Container && c.Image == t.Image {
				out = append(out, p)
				break
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// appStatus는 앱 컨테이너 상태다. 아직 없으면 nil.
func appStatus(p *corev1.Pod) *corev1.ContainerStatus {
	for i := range p.Status.ContainerStatuses {
		if p.Status.ContainerStatuses[i].Name == Container {
			return &p.Status.ContainerStatuses[i]
		}
	}
	return nil
}

// ready는 Pod의 Ready 조건이 True인지다.
func ready(p *corev1.Pod) bool {
	for _, c := range p.Status.Conditions {
		if c.Type == corev1.PodReady {
			return c.Status == corev1.ConditionTrue
		}
	}
	return false
}

// crashMessage는 마지막 종료 사유로 실패 이유를 만든다 (원본 _crash_message).
func crashMessage(cs *corev1.ContainerStatus) string {
	var head string
	switch term := cs.LastTerminationState.Terminated; {
	case term == nil:
		head = "the app keeps restarting"
	case term.Reason == "OOMKilled":
		head = "the app ran out of memory and was killed (OOMKilled)"
	case term.ExitCode == 137 || term.ExitCode == 143:
		head = fmt.Sprintf("the app did not answer the port probe in time and was restarted by the platform (exit code %d); check the port setting", term.ExitCode)
	default:
		head = fmt.Sprintf("the app exited during startup (exit code %d)", term.ExitCode)
	}
	return fmt.Sprintf("%s; stopped waiting after %d restarts", head, cs.RestartCount)
}
