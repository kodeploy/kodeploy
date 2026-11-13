package target

import (
	"context"
	"errors"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

const ns = "tenant-0a1b2c3d"

func pod(ns, name, app string, phase corev1.PodPhase, start time.Time, deleting bool) *corev1.Pod {
	p := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: ns, Labels: map[string]string{"app": app}},
		Status:     corev1.PodStatus{Phase: phase, StartTime: &metav1.Time{Time: start}},
	}
	if deleting {
		now := metav1.Now()
		p.DeletionTimestamp = &now
	}
	return p
}

func TestFind(t *testing.T) {
	t0 := time.Unix(1_790_000_000, 0)
	cs := fake.NewClientset(
		pod(ns, "web-old", "web", corev1.PodRunning, t0, false),
		pod(ns, "web-new", "web", corev1.PodRunning, t0.Add(time.Minute), false),
		pod(ns, "web-pending", "web", corev1.PodPending, t0.Add(2*time.Minute), false),
		pod(ns, "web-dying", "web", corev1.PodRunning, t0.Add(3*time.Minute), true),
		pod(ns, "postgres-0", "postgres", corev1.PodRunning, t0, false),
		pod(ns, "redis-1", "redis", corev1.PodRunning, t0, false),
		// 다른 ns의 Pod는 보이면 안 된다
		pod("tenant-ffffffff", "mysql-0", "mysql", corev1.PodRunning, t0, false),
		pod("tenant-ffffffff", "other", "other", corev1.PodRunning, t0, false),
	)
	f := NewFinder(cs, ns)
	ctx := context.Background()

	got, err := f.Find(ctx, "app", "web")
	if err != nil || got != (Target{Pod: "web-new", Container: "app", Kind: App}) {
		t.Fatalf("app: %+v %v", got, err)
	}
	// mysql은 다른 ns에만 있으니 postgres가 골라져야 한다
	got, err = f.Find(ctx, "db", "")
	if err != nil || got != (Target{Pod: "postgres-0", Container: "postgres", Kind: Postgres}) {
		t.Fatalf("db: %+v %v", got, err)
	}
	got, err = f.Find(ctx, "redis", "")
	if err != nil || got != (Target{Pod: "redis-1", Container: "redis", Kind: Redis}) {
		t.Fatalf("redis: %+v %v", got, err)
	}
	if _, err := f.Find(ctx, "app", "other"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("other ns app: %v", err)
	}
	if _, err := f.Find(ctx, "app", "Bad;name"); !errors.Is(err, ErrBadAppName) {
		t.Fatalf("bad name: %v", err)
	}
	if _, err := f.Find(ctx, "node", ""); !errors.Is(err, ErrBadTarget) {
		t.Fatalf("bad target: %v", err)
	}
}

func TestMySQLFirst(t *testing.T) {
	t0 := time.Unix(1_790_000_000, 0)
	cs := fake.NewClientset(
		pod(ns, "postgres-0", "postgres", corev1.PodRunning, t0.Add(time.Hour), false),
		pod(ns, "mysql-0", "mysql", corev1.PodRunning, t0, false),
	)
	got, err := NewFinder(cs, ns).Find(context.Background(), "db", "")
	if err != nil || got.Kind != MySQL || got.Container != "mysql" {
		t.Fatalf("got %+v %v", got, err)
	}
}

func TestNoDB(t *testing.T) {
	if _, err := NewFinder(fake.NewClientset(), ns).Find(context.Background(), "db", ""); !errors.Is(err, ErrNotFound) {
		t.Fatalf("got %v", err)
	}
}

func TestValidAppName(t *testing.T) {
	for _, ok := range []string{"a", "web", "my-app-1", "a234567890123456789012345678901234567890"[:40]} {
		if !ValidAppName(ok) {
			t.Errorf("%q should be valid", ok)
		}
	}
	for _, bad := range []string{"", "-a", "a-", "A", "a_b", "a b", "$(id)", "a234567890123456789012345678901234567890x"} {
		if ValidAppName(bad) {
			t.Errorf("%q should be invalid", bad)
		}
	}
}
