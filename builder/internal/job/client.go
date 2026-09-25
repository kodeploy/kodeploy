package job

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes"
)

type Client struct {
	cs kubernetes.Interface
	ns string
	// Watch 재연결 백오프. 테스트에서 줄인다.
	backoffMin, backoffMax time.Duration
}

func NewClient(cs kubernetes.Interface, namespace string) *Client {
	return &Client{cs: cs, ns: namespace, backoffMin: time.Second, backoffMax: 30 * time.Second}
}

func (c *Client) Create(ctx context.Context, j *batchv1.Job) error {
	_, err := c.cs.BatchV1().Jobs(c.ns).Create(ctx, j, metav1.CreateOptions{})
	return err
}

func (c *Client) Get(ctx context.Context, name string) (*batchv1.Job, error) {
	return c.cs.BatchV1().Jobs(c.ns).Get(ctx, name, metav1.GetOptions{})
}

// ListManaged는 빌더가 만든 Job 전부다 (재시작 재개용).
func (c *Client) ListManaged(ctx context.Context) ([]batchv1.Job, error) {
	l, err := c.cs.BatchV1().Jobs(c.ns).List(ctx, metav1.ListOptions{LabelSelector: LabelManaged + "=true"})
	if err != nil {
		return nil, err
	}
	return l.Items, nil
}

// Annotate는 어노테이션을 merge patch로 더한다.
func (c *Client) Annotate(ctx context.Context, name string, kv map[string]string) error {
	patch, err := json.Marshal(map[string]any{"metadata": map[string]any{"annotations": kv}})
	if err != nil {
		return err
	}
	_, err = c.cs.BatchV1().Jobs(c.ns).Patch(ctx, name, types.MergePatchType, patch, metav1.PatchOptions{})
	return err
}

// Delete는 원본 _cleanup_build_job과 같다: Background 전파, 이미 없으면 성공.
func (c *Client) Delete(ctx context.Context, name string) error {
	bg := metav1.DeletePropagationBackground
	err := c.cs.BatchV1().Jobs(c.ns).Delete(ctx, name, metav1.DeleteOptions{PropagationPolicy: &bg})
	if apierrors.IsNotFound(err) {
		return nil
	}
	return err
}

// Result는 Job의 결말이다.
type Result struct {
	Succeeded bool
	EndedAt   time.Time // 종료를 감지한 시각 (원본 _wait_for_job과 같은 의미)
	Gone      bool      // Job이 사라짐 = 취소로 본다
}

// ErrStopped는 ctx가 끝나 기다리기를 그만둔 경우다. 원인은 context.Cause로 본다.
var ErrStopped = errors.New("stopped waiting for job")

// Wait는 Job이 성공·실패·삭제될 때까지 기다린다. 현재 상태를 한 번 읽고, 그 resourceVersion부터
// 이름 field selector로 Watch한다. Watch가 끊기거나 API 오류가 나면 백오프 후 다시 읽고 다시 Watch한다.
// 전체 상한은 ctx로 준다 (원본: activeDeadlineSeconds + 30초).
func (c *Client) Wait(ctx context.Context, name string) (Result, error) {
	backoff := c.backoffMin
	for {
		r, done, err := c.watchOnce(ctx, name)
		if done {
			return r, nil
		}
		if ctx.Err() != nil {
			return Result{}, fmt.Errorf("%w: %w", ErrStopped, context.Cause(ctx))
		}
		if err == nil {
			backoff = c.backoffMin // 정상적으로 닫힌 Watch(서버 타임아웃 등)는 짧게 쉬고 다시 붙는다
		}
		if !sleep(ctx, backoff) {
			return Result{}, fmt.Errorf("%w: %w", ErrStopped, context.Cause(ctx))
		}
		if err != nil {
			backoff = min(backoff*2, c.backoffMax)
		}
	}
}

// watchOnce는 한 번 읽고 한 번 Watch한다. Watch가 닫히면 done=false로 돌아온다.
func (c *Client) watchOnce(ctx context.Context, name string) (Result, bool, error) {
	j, err := c.Get(ctx, name)
	if apierrors.IsNotFound(err) {
		return Result{Gone: true, EndedAt: time.Now()}, true, nil
	}
	if err != nil {
		return Result{}, false, err
	}
	if r, done := finished(j); done {
		return r, true, nil
	}
	w, err := c.cs.BatchV1().Jobs(c.ns).Watch(ctx, metav1.ListOptions{
		FieldSelector:   fields.OneTermEqualSelector("metadata.name", name).String(),
		ResourceVersion: j.ResourceVersion,
	})
	if err != nil {
		return Result{}, false, err
	}
	defer w.Stop()
	return consume(ctx, w, name)
}

// consume은 Watch 이벤트를 읽는다. done이면 결말이 났다. 채널이 닫히면 done=false, err=nil.
func consume(ctx context.Context, w watch.Interface, name string) (Result, bool, error) {
	for {
		select {
		case <-ctx.Done():
			return Result{}, false, nil
		case ev, ok := <-w.ResultChan():
			if !ok {
				return Result{}, false, nil
			}
			switch ev.Type {
			case watch.Deleted:
				if j, ok := ev.Object.(*batchv1.Job); ok && j.Name != name {
					continue
				}
				return Result{Gone: true, EndedAt: time.Now()}, true, nil
			case watch.Added, watch.Modified:
				j, ok := ev.Object.(*batchv1.Job)
				if !ok || j.Name != name {
					continue
				}
				if r, done := finished(j); done {
					return r, true, nil
				}
			case watch.Error:
				// 410 Gone(오래된 resourceVersion) 등. 다시 읽고 다시 Watch한다.
				return Result{}, false, apierrors.FromObject(ev.Object)
			}
		}
	}
}

// finished는 원본 판정(status.succeeded / status.failed)에 Complete·Failed 조건을 더한 것이다.
// activeDeadlineSeconds 초과는 Failed 조건(DeadlineExceeded)으로 먼저 나타날 수 있다.
func finished(j *batchv1.Job) (Result, bool) {
	now := time.Now()
	if j.Status.Succeeded > 0 || hasCondition(j, batchv1.JobComplete) {
		return Result{Succeeded: true, EndedAt: now}, true
	}
	if j.Status.Failed > 0 || hasCondition(j, batchv1.JobFailed) {
		return Result{Succeeded: false, EndedAt: now}, true
	}
	return Result{}, false
}

func hasCondition(j *batchv1.Job, t batchv1.JobConditionType) bool {
	for _, c := range j.Status.Conditions {
		if c.Type == t && c.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

// sleep은 ctx를 존중하는 대기다. ctx가 끝나면 false.
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
