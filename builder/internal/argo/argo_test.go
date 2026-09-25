package argo

import (
	"context"
	"errors"
	"testing"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	dynfake "k8s.io/client-go/dynamic/fake"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

const (
	app       = "tenant-d6d8b759"
	ourSHA    = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	laterSHA  = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	oldSHA    = "0000000000000000000000000000000000000000"
	chartSHA  = "cccccccccccccccccccccccccccccccccccccccc"
	valuesURL = "https://github.com/kodeploy/kodeploy-apps"
)

func application(valuesRev, sync, health string) *unstructured.Unstructured {
	return &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "argoproj.io/v1alpha1",
		"kind":       "Application",
		"metadata":   map[string]any{"name": app, "namespace": "argocd"},
		"spec": map[string]any{"sources": []any{
			map[string]any{"repoURL": "https://github.com/kodeploy/kodeploy-charts", "path": "charts/app"},
			map[string]any{"repoURL": valuesURL, "ref": "values"},
		}},
		"status": map[string]any{
			"sync":   map[string]any{"status": sync, "revisions": []any{chartSHA, valuesRev}},
			"health": map[string]any{"status": health},
		},
	}}
}

func newClient(objs ...runtime.Object) (*Client, *dynfake.FakeDynamicClient) {
	scheme := runtime.NewScheme()
	dyn := dynfake.NewSimpleDynamicClientWithCustomListKinds(scheme,
		map[schema.GroupVersionResource]string{ApplicationGVR: "ApplicationList"}, objs...)
	ancestor := func(_ context.Context, base, head string) (bool, error) {
		return base == ourSHA && head == laterSHA, nil
	}
	c := New(dyn, "argocd", valuesURL+".git/", ancestor)
	c.poll = 5 * time.Millisecond
	return c, dyn
}

func update(t *testing.T, dyn *dynfake.FakeDynamicClient, u *unstructured.Unstructured) {
	t.Helper()
	if _, err := dyn.Resource(ApplicationGVR).Namespace("argocd").Update(context.Background(), u, metav1.UpdateOptions{}); err != nil {
		t.Fatal(err)
	}
}

func TestRefreshPatchesAnnotation(t *testing.T) {
	c, dyn := newClient(application(oldSHA, "Synced", "Healthy"))
	if err := c.Refresh(context.Background(), app); err != nil {
		t.Fatal(err)
	}
	u, _ := dyn.Resource(ApplicationGVR).Namespace("argocd").Get(context.Background(), app, metav1.GetOptions{})
	if u.GetAnnotations()[refreshAnnotation] != "normal" {
		t.Fatalf("annotations: %v", u.GetAnnotations())
	}
}

func TestRefreshWaitsForNewApplication(t *testing.T) {
	c, dyn := newClient()
	go func() {
		time.Sleep(30 * time.Millisecond)
		_, _ = dyn.Resource(ApplicationGVR).Namespace("argocd").Create(context.Background(), application("", "Unknown", "Missing"), metav1.CreateOptions{})
	}()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := c.Refresh(ctx, app); err != nil {
		t.Fatalf("got %v", err)
	}
}

func waitAsync(c *Client, ctx context.Context, timeout time.Duration) chan error {
	ch := make(chan error, 1)
	go func() {
		s, err := c.Wait(ctx, app, ourSHA, time.Now(), timeout)
		if err == nil && (s.SyncedAt.IsZero() || s.HealthyAt.IsZero()) {
			err = errors.New("timestamps missing")
		}
		ch <- err
	}()
	return ch
}

func TestWaitSyncedHealthy(t *testing.T) {
	c, dyn := newClient(application(oldSHA, "Synced", "Healthy")) // 아직 옛 커밋
	out := waitAsync(c, context.Background(), 2*time.Second)
	time.Sleep(20 * time.Millisecond)
	update(t, dyn, application(ourSHA, "Synced", "Progressing"))
	time.Sleep(20 * time.Millisecond)
	update(t, dyn, application(ourSHA, "Synced", "Healthy"))
	if err := <-out; err != nil {
		t.Fatal(err)
	}
}

// 다른 앱 커밋이 뒤따라 들어와 revision이 우리 커밋보다 앞서 있어도, 우리 커밋을 포함하면 된다.
func TestWaitAcceptsDescendantRevision(t *testing.T) {
	c, _ := newClient(application(laterSHA, "Synced", "Healthy"))
	if err := <-waitAsync(c, context.Background(), time.Second); err != nil {
		t.Fatal(err)
	}
}

func TestWaitOldRevisionTimesOut(t *testing.T) {
	c, _ := newClient(application(oldSHA, "Synced", "Healthy"))
	err := <-waitAsync(c, context.Background(), 50*time.Millisecond)
	var f *Failure
	if !errors.As(err, &f) || f.Stage != contract.StageTimeout {
		t.Fatalf("got %v", err)
	}
}

func TestWaitSyncFailure(t *testing.T) {
	u := application(ourSHA, "OutOfSync", "Missing")
	_ = unstructured.SetNestedMap(u.Object, map[string]any{
		"phase": "Failed", "message": "one or more objects failed to apply",
		"startedAt": time.Now().UTC().Format(time.RFC3339),
	}, "status", "operationState")
	c, _ := newClient(u)
	err := <-waitAsync(c, context.Background(), time.Second)
	var f *Failure
	if !errors.As(err, &f) || f.Stage != contract.StageSync || f.Reason != "one or more objects failed to apply" {
		t.Fatalf("got %v", err)
	}
}

// 이번 대기 전에 끝난 옛 실패는 무시한다.
func TestWaitIgnoresOldOperationFailure(t *testing.T) {
	u := application(ourSHA, "Synced", "Healthy")
	_ = unstructured.SetNestedMap(u.Object, map[string]any{
		"phase": "Failed", "message": "old", "startedAt": time.Now().Add(-time.Hour).UTC().Format(time.RFC3339),
	}, "status", "operationState")
	c, _ := newClient(u)
	if err := <-waitAsync(c, context.Background(), time.Second); err != nil {
		t.Fatalf("got %v", err)
	}
}

func TestWaitDegraded(t *testing.T) {
	u := application(ourSHA, "Synced", "Degraded")
	_ = unstructured.SetNestedField(u.Object, "Deployment exceeded its progress deadline", "status", "health", "message")
	c, _ := newClient(u)
	err := <-waitAsync(c, context.Background(), time.Second)
	var f *Failure
	if !errors.As(err, &f) || f.Stage != contract.StageHealth {
		t.Fatalf("got %v", err)
	}
}

func TestWaitCancelledReturnsCause(t *testing.T) {
	c, _ := newClient(application(oldSHA, "Synced", "Healthy"))
	cause := errors.New("user cancelled")
	ctx, cancel := context.WithCancelCause(context.Background())
	out := waitAsync(c, ctx, time.Minute)
	time.Sleep(20 * time.Millisecond)
	cancel(cause)
	if err := <-out; !errors.Is(err, cause) {
		t.Fatalf("got %v", err)
	}
}

func TestSingleSourceRevision(t *testing.T) {
	u := &unstructured.Unstructured{Object: map[string]any{
		"spec":   map[string]any{"source": map[string]any{"repoURL": valuesURL}},
		"status": map[string]any{"sync": map[string]any{"status": "Synced", "revision": ourSHA}},
	}}
	if st := readStatus(u, normRepo(valuesURL)); st.revision != ourSHA {
		t.Fatalf("got %+v", st)
	}
}
