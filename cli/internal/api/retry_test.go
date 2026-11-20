package api

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

var t0 = time.Date(2026, 11, 21, 12, 0, 0, 0, time.UTC)

type step struct {
	status int
	body   string
}

func scripted(t *testing.T, steps []step) (*httptest.Server, *[]string) {
	keys := []string{}
	i := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		keys = append(keys, r.Header.Get("Idempotency-Key"))
		s := steps[min(i, len(steps)-1)]
		i++
		w.WriteHeader(s.status)
		_, _ = io.WriteString(w, s.body)
	}))
	t.Cleanup(srv.Close)
	return srv, &keys
}

const okBody = `{"app_id":"a","requested_sha":null,"replayed":false,"builds":[{"build_id":"b1","slot":"server","status":"queued","ref":"main"}]}`

func inProgress(code string) step {
	return step{409, fmt.Sprintf(`{"detail":"처리 중","code":"%s"}`, code)}
}

func run(t *testing.T, steps []step, timeout time.Duration) (*RedeployResponse, int, error, []time.Duration, []string) {
	srv, keys := scripted(t, steps)
	clk := clock.NewFake(t0)
	c := &Client{BaseURL: srv.URL, HTTP: srv.Client(), Auth: &fakeTokens{tokens: []string{"kdci_x"}}}
	resp, status, err := c.RedeployWithRetry(context.Background(), clk, t0.Add(timeout), "a", "gha-1-1", RedeployRequest{}, nil)
	return resp, status, err, clk.Sleeps(), *keys
}

func durations(secs ...int) []time.Duration {
	out := make([]time.Duration, len(secs))
	for i, s := range secs {
		out[i] = time.Duration(s) * time.Second
	}
	return out
}

func sameDurations(a, b []time.Duration) bool {
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

func allSame(keys []string) bool {
	for _, k := range keys {
		if k != "gha-1-1" {
			return false
		}
	}
	return true
}

func TestKeyInProgressRetriesWithTheSameKey(t *testing.T) {
	resp, status, err, sleeps, keys := run(t, []step{inProgress(CodeKeyInProgress), inProgress(CodeKeyReleased), {202, okBody}}, time.Hour)
	if err != nil || status != 202 || resp.Builds[0].BuildID != "b1" {
		t.Fatalf("%v %d %v", resp, status, err)
	}
	if !sameDurations(sleeps, durations(2, 4)) || len(keys) != 3 || !allSame(keys) {
		t.Fatalf("sleeps %v keys %v", sleeps, keys)
	}
}

func TestKeyInProgressGivesUpAfterThirtySeconds(t *testing.T) {
	_, status, err, sleeps, keys := run(t, []step{inProgress(CodeKeyInProgress)}, time.Hour)
	e, ok := AsError(err)
	if !ok || e.Code != CodeKeyInProgress || status != 409 {
		t.Fatalf("%v", err)
	}
	if !sameDurations(sleeps, durations(2, 4, 8, 16)) || len(keys) != 5 {
		t.Fatalf("sleeps %v keys %d", sleeps, len(keys))
	}
}

func TestServerErrorsRetryWithTheSameKeyUpToFiveTimes(t *testing.T) {
	resp, status, err, sleeps, keys := run(t, []step{{502, ""}, {504, ""}, {200, okBody}}, time.Hour)
	if err != nil || status != 200 || resp == nil {
		t.Fatalf("%v %d", err, status)
	}
	if !sameDurations(sleeps, durations(1, 2)) || !allSame(keys) {
		t.Fatalf("sleeps %v keys %v", sleeps, keys)
	}
	_, _, err, sleeps, keys = run(t, []step{{503, `{"detail":"down"}`}}, time.Hour)
	if !Retryable(err) || !sameDurations(sleeps, durations(1, 2, 4, 8, 16)) || len(keys) != 6 {
		t.Fatalf("%v sleeps %v requests %d", err, sleeps, len(keys))
	}
}

func TestOtherAnswersAreNotRetried(t *testing.T) {
	for _, s := range []step{
		{400, `{"detail":"bad","code":"invalid_commit_sha"}`},
		{403, `{"detail":"no"}`},
		{404, `{"detail":"app not found"}`},
		{409, `{"detail":"첫 배포는","code":"no_live_deploy"}`},
		{409, `{"detail":"???"}`},
		{422, `{"detail":[{"msg":"extra"}]}`},
	} {
		_, status, err, sleeps, keys := run(t, []step{s}, time.Hour)
		if err == nil || status != s.status || len(sleeps) != 0 || len(keys) != 1 {
			t.Fatalf("%d: err %v sleeps %v", s.status, err, sleeps)
		}
	}
}

func TestRetryStopsAtTheDeadline(t *testing.T) {
	_, _, err, sleeps, _ := run(t, []step{inProgress(CodeKeyInProgress)}, 5*time.Second)
	if !errors.Is(err, ErrDeadline) || !sameDurations(sleeps, durations(2)) {
		t.Fatalf("%v sleeps %v", err, sleeps)
	}
}
