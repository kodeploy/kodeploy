package api

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

var (
	srvSecret = []byte("test-secret")
	srvNow    = time.Unix(1_790_000_000, 0)
)

type fakeDispatcher struct {
	submitted []*contract.DeployRequest
	submitErr error
	cancelled []string
	cancelErr error
}

func (f *fakeDispatcher) Submit(r *contract.DeployRequest) error {
	if f.submitErr != nil {
		return f.submitErr
	}
	f.submitted = append(f.submitted, r)
	return nil
}

func (f *fakeDispatcher) Cancel(id string) error {
	if f.cancelErr != nil {
		return f.cancelErr
	}
	f.cancelled = append(f.cancelled, id)
	return nil
}

func newTestServer(d Dispatcher) *httptest.Server {
	s := New(Options{Secret: srvSecret, Validator: testValidator, Dispatcher: d, Now: func() time.Time { return srvNow }})
	return httptest.NewServer(s.Handler())
}

func do(t *testing.T, ts *httptest.Server, method, path string, body []byte, signAt *time.Time) (*http.Response, map[string]string) {
	t.Helper()
	req, _ := http.NewRequest(method, ts.URL+path, bytes.NewReader(body))
	if signAt != nil {
		sign.SignRequest(srvSecret, req, body, *signAt)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	out := map[string]string{}
	_ = json.Unmarshal(raw, &out)
	return resp, out
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

func TestSubmitAccepted(t *testing.T) {
	d := &fakeDispatcher{}
	ts := newTestServer(d)
	defer ts.Close()

	resp, out := do(t, ts, "POST", "/internal/deploys", mustJSON(validBuild()), &srvNow)
	if resp.StatusCode != http.StatusAccepted || out["build_id"] != "3f9a2c1d" {
		t.Fatalf("got %d %v", resp.StatusCode, out)
	}
	if len(d.submitted) != 1 || d.submitted[0].Build.Ref != "r2-test-v1" {
		t.Fatalf("dispatcher got %+v", d.submitted)
	}
}

func TestSubmitUnauthorized(t *testing.T) {
	d := &fakeDispatcher{}
	ts := newTestServer(d)
	defer ts.Close()
	body := mustJSON(validBuild())

	old := srvNow.Add(-2 * time.Minute)
	for name, at := range map[string]*time.Time{"unsigned": nil, "expired": &old} {
		resp, _ := do(t, ts, "POST", "/internal/deploys", body, at)
		if resp.StatusCode != http.StatusUnauthorized {
			t.Errorf("%s: got %d", name, resp.StatusCode)
		}
	}

	// 서명 뒤 본문 변조
	req, _ := http.NewRequest("POST", ts.URL+"/internal/deploys", bytes.NewReader(body))
	sign.SignRequest(srvSecret, req, body, srvNow)
	tampered := bytes.Replace(body, []byte("r2-test-v1"), []byte("main"), 1)
	req.Body = io.NopCloser(bytes.NewReader(tampered))
	req.ContentLength = int64(len(tampered))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("tampered: got %d", resp.StatusCode)
	}

	// 서명이 틀리면 본문이 깨져 있어도 401 (파싱 전에 거른다)
	resp, _ = do(t, ts, "POST", "/internal/deploys", []byte("{not json"), nil)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("garbage unsigned: got %d", resp.StatusCode)
	}
	if len(d.submitted) != 0 {
		t.Fatal("dispatcher must not be called")
	}
}

func TestSubmitBadRequest(t *testing.T) {
	d := &fakeDispatcher{}
	ts := newTestServer(d)
	defer ts.Close()

	forbidden := []byte(`{"build_id":"cc33dd44","namespace":"tenant-d6d8b759","slot":"server","kind":"config","values":{"image":"x"}}`)
	bad := validBuild()
	bad.Build.Ref = "-x"
	for name, body := range map[string][]byte{
		"not json":  []byte("{not json"),
		"forbidden": forbidden,
		"invalid":   mustJSON(bad),
	} {
		resp, out := do(t, ts, "POST", "/internal/deploys", body, &srvNow)
		if resp.StatusCode != http.StatusBadRequest || out["error"] == "" {
			t.Errorf("%s: got %d %v", name, resp.StatusCode, out)
		}
	}
	if len(d.submitted) != 0 {
		t.Fatal("dispatcher must not be called")
	}
}

func TestSubmitConflictAndBusy(t *testing.T) {
	d := &fakeDispatcher{submitErr: &contract.ConflictError{BuildID: "11112222", Reason: "namespace+slot busy"}}
	ts := newTestServer(d)
	defer ts.Close()

	resp, out := do(t, ts, "POST", "/internal/deploys", mustJSON(validBuild()), &srvNow)
	if resp.StatusCode != http.StatusConflict || out["build_id"] != "11112222" {
		t.Fatalf("conflict: got %d %v", resp.StatusCode, out)
	}

	d.submitErr = &contract.BusyError{RetryAfter: 1500 * time.Millisecond}
	resp, _ = do(t, ts, "POST", "/internal/deploys", mustJSON(validBuild()), &srvNow)
	if resp.StatusCode != http.StatusTooManyRequests || resp.Header.Get("Retry-After") != "2" {
		t.Fatalf("busy: got %d retry-after=%q", resp.StatusCode, resp.Header.Get("Retry-After"))
	}

	d.submitErr = errors.New("k8s down: token=abc")
	resp, out = do(t, ts, "POST", "/internal/deploys", mustJSON(validBuild()), &srvNow)
	if resp.StatusCode != http.StatusInternalServerError || out["error"] != "internal error" {
		t.Fatalf("internal: got %d %v (내부 오류 문구는 밖으로 내보내지 않는다)", resp.StatusCode, out)
	}
}

func TestCancel(t *testing.T) {
	d := &fakeDispatcher{}
	ts := newTestServer(d)
	defer ts.Close()

	resp, _ := do(t, ts, "DELETE", "/internal/deploys/3f9a2c1d", nil, &srvNow)
	if resp.StatusCode != http.StatusAccepted || len(d.cancelled) != 1 {
		t.Fatalf("cancel: got %d %v", resp.StatusCode, d.cancelled)
	}

	resp, _ = do(t, ts, "DELETE", "/internal/deploys/3f9a2c1d", nil, nil)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unsigned cancel: got %d", resp.StatusCode)
	}

	resp, _ = do(t, ts, "DELETE", "/internal/deploys/not-an-id", nil, &srvNow)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("bad id: got %d", resp.StatusCode)
	}

	d.cancelErr = contract.ErrNotFound
	resp, _ = do(t, ts, "DELETE", "/internal/deploys/3f9a2c1d", nil, &srvNow)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("missing: got %d", resp.StatusCode)
	}
}

func TestHealthzAndRouting(t *testing.T) {
	ts := newTestServer(&fakeDispatcher{})
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("healthz: %d", resp.StatusCode)
	}
	resp, _ = do(t, ts, "GET", "/internal/deploys", nil, &srvNow)
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Fatalf("GET deploys: %d", resp.StatusCode)
	}
}

func TestBodyLimit(t *testing.T) {
	ts := newTestServer(&fakeDispatcher{})
	defer ts.Close()
	big := bytes.Repeat([]byte("a"), maxBodyBytes+1)
	resp, _ := do(t, ts, "POST", "/internal/deploys", big, &srvNow)
	if resp.StatusCode != http.StatusRequestEntityTooLarge {
		t.Fatalf("got %d", resp.StatusCode)
	}
}
