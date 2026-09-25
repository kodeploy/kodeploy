package logs

import (
	"context"
	"errors"
	"fmt"
	"io"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
)

// 스트림 하나 = 본문 + 끝에 낼 오류(nil이면 EOF)
type stream struct {
	body string
	err  error
}

type errReader struct {
	r   io.Reader
	err error
}

func (e *errReader) Read(p []byte) (int, error) {
	n, err := e.r.Read(p)
	if err == io.EOF && e.err != nil {
		return n, e.err
	}
	return n, err
}

type fakeSource struct {
	mu      sync.Mutex
	opens   map[string][]any // 컨테이너별 차례: stream 또는 error
	states  map[string][]State
	sinces  map[string][]time.Time
	podName string
}

func newFakeSource() *fakeSource {
	return &fakeSource{opens: map[string][]any{}, states: map[string][]State{}, sinces: map[string][]time.Time{}, podName: "build-pod"}
}

func (f *fakeSource) WaitPod(context.Context, string) (string, error) { return f.podName, nil }

func (f *fakeSource) Open(_ context.Context, _, c string, since time.Time) (io.ReadCloser, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.sinces[c] = append(f.sinces[c], since)
	q := f.opens[c]
	if len(q) == 0 {
		return nil, errors.New("no more streams")
	}
	next := q[0]
	if len(q) > 1 {
		f.opens[c] = q[1:]
	}
	if err, ok := next.(error); ok {
		return nil, err
	}
	s := next.(stream)
	return io.NopCloser(&errReader{r: strings.NewReader(s.body), err: s.err}), nil
}

func (f *fakeSource) State(_ context.Context, _, c string) (State, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	q := f.states[c]
	s := q[0]
	if len(q) > 1 {
		f.states[c] = q[1:]
	}
	return s, nil
}

func ts(sec int) string {
	return time.Date(2026, 9, 25, 12, 0, sec, 123456789, time.UTC).Format(time.RFC3339Nano)
}

func lines(pairs ...any) string {
	var b strings.Builder
	for i := 0; i < len(pairs); i += 2 {
		fmt.Fprintf(&b, "%s %s\n", ts(pairs[i].(int)), pairs[i+1].(string))
	}
	return b.String()
}

func follow(t *testing.T, src Source) ([]string, error) {
	t.Helper()
	f := NewFollower(src)
	f.backoffMin, f.backoffMax = time.Millisecond, 2*time.Millisecond
	out := make(chan string, 1000)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	err := f.Follow(ctx, "3f9a2c1d", out)
	close(out)
	var got []string
	for l := range out {
		got = append(got, l)
	}
	return got, err
}

var done = State{Started: true, Terminated: true}

func TestFollowFormat(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{stream{body: lines(1, "[1/1] cloning x (main)...", 2, "Cloning into '/workspace/src'...")}}
	src.states["clone"] = []State{done}
	src.opens["buildkit"] = []any{stream{body: lines(3, "#1 [internal] load build definition", 4, "#20 exporting to image")}}
	src.states["buildkit"] = []State{done}

	got, err := follow(t, src)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		InitHeader, "[1/1] cloning x (main)...", "Cloning into '/workspace/src'...",
		"",
		MainHeader, "#1 [internal] load build definition", "#20 exporting to image",
	}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}

// main 스트림이 중간에 끊기면 마지막 시각부터 다시 열고, 겹치는 줄은 다시 보내지 않는다.
func TestFollowReconnectNoDuplicates(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{stream{body: lines(1, "clone")}}
	src.states["clone"] = []State{done}
	src.opens["buildkit"] = []any{
		stream{body: lines(3, "a", 4, "b"), err: errors.New("connection reset")},
		errors.New("apiserver unavailable"),
		stream{body: lines(4, "b", 5, "c")}, // since=4초부터 다시 → b는 겹침
	}
	src.states["buildkit"] = []State{{Started: true}, {Started: true}, done}

	got, err := follow(t, src)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{InitHeader, "clone", "", MainHeader, "a", "b", "c"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
	s := src.sinces["buildkit"]
	if !s[0].IsZero() || s[2].Second() != 4 {
		t.Fatalf("since values: %v", s)
	}
}

// 컨테이너가 아직 안 떴으면(PodInitializing) 열기가 실패한다. 뜰 때까지 다시 연다.
func TestFollowWaitsForContainerStart(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{
		errors.New(`container "clone" in pod "build-pod" is waiting to start: PodInitializing`),
		stream{body: lines(1, "clone")},
	}
	src.states["clone"] = []State{{}, done}
	src.opens["buildkit"] = []any{stream{body: lines(2, "main")}}
	src.states["buildkit"] = []State{done}

	got, err := follow(t, src)
	if err != nil || len(got) != 5 {
		t.Fatalf("got %q %v", got, err)
	}
}

// clone이 실패하면 main은 안 뜬다. main 머리줄 없이 끝나야 한다.
func TestFollowInitFailed(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{stream{body: lines(1, "fatal: Remote branch nope not found in upstream origin")}}
	src.states["clone"] = []State{done}
	src.opens["buildkit"] = []any{errors.New(`container "buildkit" is waiting to start: PodInitializing`)}
	src.states["buildkit"] = []State{{PodDone: true}}

	got, err := follow(t, src)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{InitHeader, "fatal: Remote branch nope not found in upstream origin"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %q", got)
	}
}

func TestFollowPodGone(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{errors.New("pods \"build-pod\" not found")}
	src.states["clone"] = []State{{Gone: true}}
	if _, err := follow(t, src); !errors.Is(err, ErrPodGone) {
		t.Fatalf("got %v", err)
	}
}

func TestFollowStopsOnContext(t *testing.T) {
	src := newFakeSource()
	src.opens["clone"] = []any{errors.New("waiting")}
	src.states["clone"] = []State{{}}
	f := NewFollower(src)
	f.backoffMin, f.backoffMax = time.Millisecond, time.Millisecond
	cause := errors.New("build cancelled")
	ctx, cancel := context.WithCancelCause(context.Background())
	errc := make(chan error, 1)
	go func() { errc <- f.Follow(ctx, "3f9a2c1d", make(chan string)) }()
	time.Sleep(10 * time.Millisecond)
	cancel(cause)
	select {
	case err := <-errc:
		if !errors.Is(err, cause) {
			t.Fatalf("got %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Follow did not stop")
	}
}

func TestReadLineTruncatesLongLines(t *testing.T) {
	long := strings.Repeat("x", maxLineBytes*2)
	var got []string
	_, n, err := readLines(strings.NewReader(ts(1)+" "+long+"\n"+ts(2)+" next\n"), time.Time{}, func(l string) bool {
		got = append(got, l)
		return true
	})
	if err != nil || n != 2 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if !strings.HasSuffix(got[0], " [truncated]") || len(got[0]) > maxLineBytes+20 {
		t.Fatalf("long line not truncated: len=%d", len(got[0]))
	}
	if got[1] != "next" {
		t.Fatalf("line after long one: %q", got[1])
	}
}

func TestReadLinesWithoutTimestampOrNewline(t *testing.T) {
	var got []string
	_, _, err := readLines(strings.NewReader("no timestamp here\n"+ts(1)+" last without newline"), time.Time{}, func(l string) bool {
		got = append(got, l)
		return true
	})
	if err != nil || !reflect.DeepEqual(got, []string{"no timestamp here", "last without newline"}) {
		t.Fatalf("got %q %v", got, err)
	}
}

func TestKubeSourceWaitPodAndState(t *testing.T) {
	cs := fake.NewClientset()
	src := NewKubeSource(cs, "kodeploy-build")
	src.backoff = time.Millisecond

	errc := make(chan error, 1)
	namec := make(chan string, 1)
	go func() {
		n, err := src.WaitPod(context.Background(), "3f9a2c1d")
		namec <- n
		errc <- err
	}()
	time.Sleep(20 * time.Millisecond)
	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "build-d6d8b759-3f9a2c1d-x7k2p", Namespace: "kodeploy-build",
			Labels: map[string]string{"build-id": "3f9a2c1d"}},
		Status: corev1.PodStatus{
			Phase: corev1.PodFailed,
			InitContainerStatuses: []corev1.ContainerStatus{{Name: "clone",
				State: corev1.ContainerState{Terminated: &corev1.ContainerStateTerminated{ExitCode: 128}}}},
			ContainerStatuses: []corev1.ContainerStatus{{Name: "buildkit",
				State: corev1.ContainerState{Waiting: &corev1.ContainerStateWaiting{Reason: "PodInitializing"}}}},
		},
	}
	if _, err := cs.CoreV1().Pods("kodeploy-build").Create(context.Background(), pod, metav1.CreateOptions{}); err != nil {
		t.Fatal(err)
	}
	select {
	case n := <-namec:
		if err := <-errc; err != nil || n != pod.Name {
			t.Fatalf("got %q %v", n, err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("WaitPod did not return")
	}

	st, err := src.State(context.Background(), pod.Name, "clone")
	if err != nil || !st.Terminated || !st.Started || !st.PodDone {
		t.Fatalf("clone state %+v %v", st, err)
	}
	st, _ = src.State(context.Background(), pod.Name, "buildkit")
	if st.Started || st.Terminated || !st.PodDone {
		t.Fatalf("buildkit state %+v", st)
	}
	st, _ = src.State(context.Background(), "missing", "clone")
	if !st.Gone {
		t.Fatalf("missing pod state %+v", st)
	}
}
