package job

import (
	"context"
	"errors"
	"testing"
	"time"

	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/client-go/kubernetes/fake"
	k8stesting "k8s.io/client-go/testing"
)

const testNS = "kodeploy-build"

func newTestClient(objs ...runtime.Object) (*Client, *fake.Clientset) {
	cs := fake.NewClientset(objs...)
	c := NewClient(cs, testNS)
	c.backoffMin, c.backoffMax = time.Millisecond, 5*time.Millisecond
	return c, cs
}

func runningJob() *batchv1.Job {
	return &batchv1.Job{ObjectMeta: metav1.ObjectMeta{Name: "build-d6d8b759-3f9a2c1d", Namespace: testNS}}
}

type waitOut struct {
	r   Result
	err error
}

func startWait(c *Client, ctx context.Context) chan waitOut {
	ch := make(chan waitOut, 1)
	go func() {
		r, err := c.Wait(ctx, "build-d6d8b759-3f9a2c1d")
		ch <- waitOut{r, err}
	}()
	return ch
}

func await(t *testing.T, ch chan waitOut) waitOut {
	t.Helper()
	select {
	case o := <-ch:
		return o
	case <-time.After(5 * time.Second):
		t.Fatal("Wait did not return")
		return waitOut{}
	}
}

// Watch가 실제로 걸린 뒤에 상태를 바꾸도록, watch 호출을 알려 주는 반응기를 단다.
func watchStarted(cs *fake.Clientset) chan struct{} {
	ch := make(chan struct{}, 10)
	cs.PrependWatchReactor("jobs", func(k8stesting.Action) (bool, watch.Interface, error) {
		ch <- struct{}{}
		return false, nil, nil
	})
	return ch
}

func TestWaitSucceeded(t *testing.T) {
	c, cs := newTestClient(runningJob())
	started := watchStarted(cs)
	out := startWait(c, context.Background())
	<-started

	j := runningJob()
	j.Status.Succeeded = 1
	if _, err := cs.BatchV1().Jobs(testNS).UpdateStatus(context.Background(), j, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	o := await(t, out)
	if o.err != nil || !o.r.Succeeded || o.r.Gone || o.r.EndedAt.IsZero() {
		t.Fatalf("got %+v", o)
	}
}

func TestWaitFailedByDeadlineCondition(t *testing.T) {
	c, cs := newTestClient(runningJob())
	started := watchStarted(cs)
	out := startWait(c, context.Background())
	<-started

	j := runningJob()
	j.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobFailed, Status: corev1.ConditionTrue, Reason: "DeadlineExceeded"}}
	if _, err := cs.BatchV1().Jobs(testNS).UpdateStatus(context.Background(), j, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	o := await(t, out)
	if o.err != nil || o.r.Succeeded || o.r.Gone {
		t.Fatalf("got %+v", o)
	}
}

func TestWaitAlreadyFinished(t *testing.T) {
	j := runningJob()
	j.Status.Failed = 1
	c, _ := newTestClient(j)
	r, err := c.Wait(context.Background(), j.Name)
	if err != nil || r.Succeeded {
		t.Fatalf("got %+v %v", r, err)
	}
}

func TestWaitDeletedIsGone(t *testing.T) {
	c, cs := newTestClient(runningJob())
	started := watchStarted(cs)
	out := startWait(c, context.Background())
	<-started
	if err := c.Delete(context.Background(), "build-d6d8b759-3f9a2c1d"); err != nil {
		t.Fatal(err)
	}
	if o := await(t, out); o.err != nil || !o.r.Gone {
		t.Fatalf("got %+v", o)
	}
}

func TestWaitMissingIsGone(t *testing.T) {
	c, _ := newTestClient()
	r, err := c.Wait(context.Background(), "build-d6d8b759-3f9a2c1d")
	if err != nil || !r.Gone {
		t.Fatalf("got %+v %v", r, err)
	}
}

// Watch가 끊기고, 한 번은 Error 이벤트, 한 번은 API 오류가 나도 다시 붙어서 결말을 받는다.
func TestWaitReconnects(t *testing.T) {
	c, cs := newTestClient(runningJob())
	calls := 0
	getFails := 0
	cs.PrependReactor("get", "jobs", func(k8stesting.Action) (bool, runtime.Object, error) {
		if getFails == 0 {
			getFails++
			return true, nil, apierrors.NewServiceUnavailable("apiserver restarting")
		}
		return false, nil, nil
	})
	fw1, fw2 := watch.NewFake(), watch.NewFake()
	started := make(chan int, 10)
	cs.PrependWatchReactor("jobs", func(k8stesting.Action) (bool, watch.Interface, error) {
		calls++
		started <- calls
		switch calls {
		case 1:
			return true, fw1, nil
		case 2:
			return true, fw2, nil
		}
		return false, nil, nil
	})
	out := startWait(c, context.Background())

	<-started
	fw1.Stop() // 서버가 Watch를 닫음
	<-started
	fw2.Error(&metav1.Status{Status: metav1.StatusFailure, Code: 410, Reason: metav1.StatusReasonGone, Message: "too old"})
	<-started // 3번째는 진짜 fake watch

	j := runningJob()
	j.Status.Succeeded = 1
	if _, err := cs.BatchV1().Jobs(testNS).UpdateStatus(context.Background(), j, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
	if o := await(t, out); o.err != nil || !o.r.Succeeded {
		t.Fatalf("got %+v", o)
	}
	if getFails != 1 || calls != 3 {
		t.Fatalf("get failures=%d watch calls=%d", getFails, calls)
	}
}

func TestWaitStopsOnContext(t *testing.T) {
	c, cs := newTestClient(runningJob())
	started := watchStarted(cs)
	cause := errors.New("user cancelled")
	ctx, cancel := context.WithCancelCause(context.Background())
	out := startWait(c, ctx)
	<-started
	cancel(cause)
	o := await(t, out)
	if !errors.Is(o.err, ErrStopped) || !errors.Is(o.err, cause) {
		t.Fatalf("got %v", o.err)
	}
}

func TestAnnotateDeleteList(t *testing.T) {
	ctx := context.Background()
	c, _ := newTestClient()
	j := Build(goldenParams(false))
	j.Namespace = testNS
	if err := c.Create(ctx, j); err != nil {
		t.Fatal(err)
	}
	if err := c.Annotate(ctx, j.Name, map[string]string{AnnDigest: "sha256:abc", AnnAckedSeq: "3"}); err != nil {
		t.Fatal(err)
	}
	got, err := c.Get(ctx, j.Name)
	if err != nil {
		t.Fatal(err)
	}
	if got.Annotations[AnnDigest] != "sha256:abc" || got.Annotations[AnnAckedSeq] != "3" || got.Annotations[AnnRequest] == "" {
		t.Fatalf("annotations: %v", got.Annotations)
	}

	l, err := c.ListManaged(ctx)
	if err != nil || len(l) != 1 {
		t.Fatalf("list: %v %v", l, err)
	}

	if err := c.Delete(ctx, j.Name); err != nil {
		t.Fatal(err)
	}
	if err := c.Delete(ctx, j.Name); err != nil {
		t.Fatalf("second delete should be ok: %v", err)
	}
}
