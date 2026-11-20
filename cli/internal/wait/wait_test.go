package wait

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

var t0 = time.Date(2026, 11, 21, 12, 0, 0, 0, time.UTC)

// script는 빌드마다 조회 차례에 줄 상태(또는 오류)다. 다 쓰면 마지막을 반복한다.
type script map[string][]any

func getter(s script, calls map[string]int) Getter {
	return func(_ context.Context, id string) (*api.Build, error) {
		seq := s[id]
		i := calls[id]
		calls[id]++
		if i >= len(seq) {
			i = len(seq) - 1
		}
		switch v := seq[i].(type) {
		case error:
			return nil, v
		case string:
			return &api.Build{BuildID: id, Status: v}, nil
		case *api.Build:
			return v, nil
		}
		panic("bad script")
	}
}

func refs(ids ...string) []api.BuildRef {
	out := make([]api.BuildRef, len(ids))
	for i, id := range ids {
		out[i] = api.BuildRef{BuildID: id, Slot: []string{"server", "static"}[i%2], Status: "queued"}
	}
	return out
}

func opts(clk *clock.Fake, g Getter, settle, timeout time.Duration) Options {
	return Options{Clock: clk, Get: g, Start: clk.Now(), Deadline: clk.Now().Add(timeout), Settle: settle}
}

func secs(ds []time.Duration) []int {
	out := make([]int, len(ds))
	for i, d := range ds {
		out[i] = int(d / time.Second)
	}
	return out
}

func equal(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func TestBackoffGrowsToTenAndResetsOnChange(t *testing.T) {
	clk := clock.NewFake(t0)
	calls := map[string]int{}
	s := script{"a": {"queued", "queued", "queued", "queued", "queued", "queued", "building", "building", "building", "running"}}
	out := Run(context.Background(), opts(clk, getter(s, calls), 0, time.Hour), refs("a"))
	if out.Kind != KindSuccess {
		t.Fatalf("kind %v", out.Kind)
	}
	// queued 6번: 2,4,8,10,10,10 → building으로 바뀜: 2 → 4 → 8 → running
	want := []int{2, 4, 8, 10, 10, 10, 2, 4, 8}
	if got := secs(clk.Sleeps()); !equal(got, want) {
		t.Fatalf("sleeps %v want %v", got, want)
	}
	if h := out.Builds[0].History; len(h) != 3 || h[0] != "queued" || h[1] != "building" || h[2] != "running" {
		t.Fatalf("history %v", h)
	}
	if out.Builds[0].Elapsed != 58*time.Second {
		t.Fatalf("elapsed %v", out.Builds[0].Elapsed)
	}
}

func TestSettleZeroSucceedsAtOnce(t *testing.T) {
	clk := clock.NewFake(t0)
	out := Run(context.Background(), opts(clk, getter(script{"a": {"running"}, "b": {"running"}}, map[string]int{}), 0, time.Hour), refs("a", "b"))
	if out.Kind != KindSuccess || len(clk.Sleeps()) != 0 {
		t.Fatalf("kind %v sleeps %v", out.Kind, clk.Sleeps())
	}
}

func TestSettleKeepsPollingForTheWholeWindow(t *testing.T) {
	clk := clock.NewFake(t0)
	calls := map[string]int{}
	settled := time.Duration(0)
	o := opts(clk, getter(script{"a": {"running"}}, calls), 60*time.Second, time.Hour)
	o.OnSettle = func(d time.Duration) { settled = d }
	out := Run(context.Background(), o, refs("a"))
	if out.Kind != KindSuccess || settled != 60*time.Second {
		t.Fatalf("kind %v settled %v", out.Kind, settled)
	}
	if clk.Now().Sub(t0) != 60*time.Second {
		t.Fatalf("안정화는 정확히 60초: %v", clk.Now().Sub(t0))
	}
	if calls["a"] < 5 {
		t.Fatalf("안정화 동안 계속 조회해야 한다: %d", calls["a"])
	}
}

func TestFailureDuringSettleIsAFailure(t *testing.T) {
	clk := clock.NewFake(t0)
	s := script{"a": {"running", "running", "running", "failed"}, "b": {"running"}}
	out := Run(context.Background(), opts(clk, getter(s, map[string]int{}), 60*time.Second, time.Hour), refs("a", "b"))
	if out.Kind != KindFailed || out.Builds[0].Status != "failed" {
		t.Fatalf("kind %v %v", out.Kind, out.Builds[0].Status)
	}
	if clk.Now().Sub(t0) >= 60*time.Second {
		t.Fatal("실패가 보이면 안정화 시간을 다 기다리지 않는다")
	}
}

func TestCancelledWithoutFailureIsSuperseded(t *testing.T) {
	clk := clock.NewFake(t0)
	s := script{"a": {"building", "cancelled"}, "b": {"building", "building", "running"}}
	out := Run(context.Background(), opts(clk, getter(s, map[string]int{}), 60*time.Second, time.Hour), refs("a", "b"))
	if out.Kind != KindSuperseded {
		t.Fatalf("kind %v", out.Kind)
	}
}

func TestOverallTimeout(t *testing.T) {
	clk := clock.NewFake(t0)
	out := Run(context.Background(), opts(clk, getter(script{"a": {"building"}}, map[string]int{}), 60*time.Second, 25*time.Second), refs("a"))
	if out.Kind != KindTimeout {
		t.Fatalf("kind %v", out.Kind)
	}
	if got := clk.Now().Sub(t0); got != 25*time.Second {
		t.Fatalf("시간 제한을 넘겨 기다리지 않는다: %v", got)
	}
}

func TestTimeoutAlsoCoversTheSettleWindow(t *testing.T) {
	clk := clock.NewFake(t0)
	out := Run(context.Background(), opts(clk, getter(script{"a": {"running"}}, map[string]int{}), 60*time.Second, 30*time.Second), refs("a"))
	if out.Kind != KindTimeout {
		t.Fatalf("kind %v", out.Kind)
	}
}

func TestConsecutiveStatusFailuresStopAtTen(t *testing.T) {
	clk := clock.NewFake(t0)
	boom := &api.Error{Status: 503}
	seq := []any{}
	for i := 0; i < 9; i++ {
		seq = append(seq, boom)
	}
	seq = append(seq, "building")
	for i := 0; i < 10; i++ {
		seq = append(seq, boom)
	}
	calls := map[string]int{}
	out := Run(context.Background(), opts(clk, getter(script{"a": seq}, calls), 0, time.Hour), refs("a"))
	if out.Kind != KindError || calls["a"] != 20 {
		t.Fatalf("kind %v calls %d", out.Kind, calls["a"])
	}
	if !errors.Is(out.Err, boom) {
		t.Fatalf("err %v", out.Err)
	}
}

func TestNonRetryableStatusErrorStops(t *testing.T) {
	clk := clock.NewFake(t0)
	out := Run(context.Background(), opts(clk, getter(script{"a": {&api.Error{Status: 404, Detail: "build not found"}}}, map[string]int{}), 0, time.Hour), refs("a"))
	if out.Kind != KindError {
		t.Fatalf("kind %v", out.Kind)
	}
}

func TestInterruptStopsWaiting(t *testing.T) {
	clk := clock.NewFake(t0)
	ctx, cancel := context.WithCancel(context.Background())
	n := 0
	clk.OnSleep = func(time.Time) {
		if n++; n == 3 {
			cancel()
		}
	}
	out := Run(ctx, opts(clk, getter(script{"a": {"building"}}, map[string]int{}), 0, time.Hour), refs("a"))
	if out.Kind != KindInterrupted {
		t.Fatalf("kind %v", out.Kind)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), -time.Second)
	defer cancel2()
	if out := Run(ctx2, opts(clock.NewFake(t0), getter(script{"a": {"building"}}, map[string]int{}), 0, time.Hour), refs("a")); out.Kind != KindTimeout {
		t.Fatalf("실제 시간 제한(ctx deadline)은 timeout: %v", out.Kind)
	}
}

func TestWaitAnalysisUntilDoneOrThirtySeconds(t *testing.T) {
	pending, done := "pending", "done"
	analysis := `{"title":"t"}`
	clk := clock.NewFake(t0)
	calls := map[string]int{}
	s := script{"a": {&api.Build{Status: "failed", AIStatus: &pending}, &api.Build{Status: "failed", AIStatus: &pending},
		&api.Build{Status: "failed", AIStatus: &done, AIAnalysis: &analysis}}}
	b := &Build{Ref: api.BuildRef{BuildID: "a"}, Status: "failed", Last: &api.Build{Status: "failed", AIStatus: &pending}}
	WaitAnalysis(context.Background(), opts(clk, getter(s, calls), 0, time.Hour), []*Build{b})
	if api.Str(b.Last.AIAnalysis) != analysis || calls["a"] != 3 {
		t.Fatalf("analysis %q calls %d", api.Str(b.Last.AIAnalysis), calls["a"])
	}

	clk = clock.NewFake(t0)
	b = &Build{Ref: api.BuildRef{BuildID: "a"}, Status: "failed", Last: &api.Build{Status: "failed", AIStatus: &pending}}
	WaitAnalysis(context.Background(), opts(clk, getter(script{"a": {&api.Build{Status: "failed", AIStatus: &pending}}}, map[string]int{}), 0, time.Hour), []*Build{b})
	if got := clk.Now().Sub(t0); got != 30*time.Second {
		t.Fatalf("진단은 최대 30초만 기다린다: %v", got)
	}
}
