package run

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"go.uber.org/goleak"
	batchv1 "k8s.io/api/batch/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	"github.com/kodeploy/kodeploy/builder/internal/argo"
	"github.com/kodeploy/kodeploy/builder/internal/callback"
	"github.com/kodeploy/kodeploy/builder/internal/config"
	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/gitops"
	"github.com/kodeploy/kodeploy/builder/internal/job"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

const (
	ns      = "tenant-d6d8b759"
	userID  = "d6d8b75985524d6f9a9000665e7ca0da"
	repo    = "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring"
	buildID = "3f9a2c1d"
	jobName = "build-d6d8b759-3f9a2c1d"
	digest  = "sha256:4f1c2b3a5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f7a8"
)

var secret = []byte("run-secret")

func buildReq() *contract.DeployRequest {
	uid, name := userID, "kodeploy-test-spring"
	return &contract.DeployRequest{
		BuildID: buildID, Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindBuild,
		Values: &contract.CoreValues{UserID: &uid, Name: &name},
		Unit:   &contract.Unit{Runtime: "java", Port: 8080},
		Build: &contract.BuildSpec{Repo: "https://github.com/yuntyu01/kodeploy-test-spring.git", Ref: "main",
			Mode: "dockerfile", DockerfileName: "Dockerfile", ImageRepo: repo, ImageTag: buildID, CacheRef: repo + ":buildcache"},
	}
}

var (
	preLines  = []string{"=== clone (init) ===", "[1/1] cloning", "", "=== buildkit (main) ===", "#16 12.0 BUILD SUCCESSFUL"}
	pushLines = []string{"#20 exporting to image", "#20 pushing manifest for " + repo + ":" + buildID + "@" + digest + " 0.8s done"}
	postLines = []string{"#22 exporting cache to registry", "#22 DONE 28.1s"}
)

// ---- 가짜 의존성 -------------------------------------------------------------------------------

type fakeLogs struct {
	mu     sync.Mutex
	before []string
	gate   chan struct{} // 닫히면 after를 보내고 끝난다
	after  []string
	calls  int
}

func (f *fakeLogs) Follow(ctx context.Context, _ string, out chan<- string) error {
	f.mu.Lock()
	f.calls++
	before, after, gate := f.before, f.after, f.gate
	f.mu.Unlock()
	send := func(ls []string) bool {
		for _, l := range ls {
			select {
			case out <- l:
			case <-ctx.Done():
				return false
			}
		}
		return true
	}
	if !send(before) {
		return ctx.Err()
	}
	if gate != nil {
		select {
		case <-gate:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	send(after)
	return nil
}

type fakeRegistry struct {
	mu      sync.Mutex
	exists  map[string]bool
	tags    map[string]string
	resolve int
}

func (f *fakeRegistry) Exists(_ context.Context, _, d string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.exists[d] {
		return errors.New("image not found in registry")
	}
	return nil
}

func (f *fakeRegistry) Resolve(_ context.Context, _, tag string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.resolve++
	if d, ok := f.tags[tag]; ok {
		return d, nil
	}
	return "", errors.New("image not found in registry")
}

type fakeGit struct {
	mu       sync.Mutex
	values   map[string]*gitops.Values
	commits  int
	messages []string
}

func (f *fakeGit) Commit(ctx context.Context, ch gitops.Change) (gitops.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	cur := f.values[ch.Namespace]
	if ch.Delete {
		if cur == nil {
			return gitops.Result{Skipped: true}, nil
		}
		delete(f.values, ch.Namespace)
	} else {
		next, err := ch.Apply(cur)
		if err != nil {
			return gitops.Result{}, err
		}
		if cur != nil && cur.Equal(next) {
			return gitops.Result{CommitSHA: "head", Skipped: true, Values: cur}, nil
		}
		f.values[ch.Namespace] = next
	}
	f.commits++
	f.messages = append(f.messages, ch.Message)
	return gitops.Result{CommitSHA: fmt.Sprintf("%040d", f.commits), Values: f.values[ch.Namespace]}, nil
}

func (f *fakeGit) get(ns string) *gitops.Values {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.values[ns]
}

type fakeArgo struct {
	mu      sync.Mutex
	waitErr error
	block   chan struct{}
	waited  []string
}

func (f *fakeArgo) Refresh(context.Context, string) error { return nil }

func (f *fakeArgo) Wait(ctx context.Context, _, sha string, _ time.Time, _ time.Duration) (argo.Synced, error) {
	f.mu.Lock()
	f.waited = append(f.waited, sha)
	block, err := f.block, f.waitErr
	f.mu.Unlock()
	if block != nil {
		select {
		case <-block:
		case <-ctx.Done():
			return argo.Synced{}, context.Cause(ctx)
		}
	}
	if err != nil {
		return argo.Synced{}, err
	}
	now := time.Now()
	return argo.Synced{SyncedAt: now, HealthyAt: now}, nil
}

type mockCore struct {
	mu     sync.Mutex
	events []contract.Event
	badSig int
}

func (m *mockCore) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	if err := sign.VerifyRequest(secret, r, body, time.Now()); err != nil || !strings.HasPrefix(r.URL.Path, "/internal/builds/") {
		m.mu.Lock()
		m.badSig++
		m.mu.Unlock()
		w.WriteHeader(401)
		return
	}
	var ev contract.Event
	_ = json.Unmarshal(body, &ev)
	m.mu.Lock()
	m.events = append(m.events, ev)
	m.mu.Unlock()
}

func (m *mockCore) list() []contract.Event {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]contract.Event(nil), m.events...)
}

// ---- 하네스 ------------------------------------------------------------------------------------

type harness struct {
	t      *testing.T
	cs     *fake.Clientset
	logs   *fakeLogs
	reg    *fakeRegistry
	git    *fakeGit
	argo   *fakeArgo
	core   *mockCore
	srv    *httptest.Server
	cfg    *config.Config
	m      *Manager
	sender *callback.Sender
}

func newHarness(t *testing.T, mutate func(*config.Config)) *harness {
	cfg := &config.Config{
		BuildNamespace: "kodeploy-build", BuildKitImage: config.DefaultBuildKitImage, BuildActiveDeadlineSeconds: 60,
		EarlyTrigger: true, ArgoWaitTimeout: 5 * time.Second, LogBatchInterval: 5 * time.Millisecond, MaxActiveBuilds: 3,
	}
	if mutate != nil {
		mutate(cfg)
	}
	h := &harness{t: t, cs: fake.NewClientset(), cfg: cfg,
		logs: &fakeLogs{before: append(append([]string{}, preLines...), pushLines...), gate: make(chan struct{}), after: postLines},
		reg:  &fakeRegistry{exists: map[string]bool{digest: true}, tags: map[string]string{buildID: digest}},
		git:  &fakeGit{values: map[string]*gitops.Values{}},
		argo: &fakeArgo{},
		core: &mockCore{},
	}
	h.srv = httptest.NewServer(h.core)
	h.sender = callback.NewSender(h.srv.URL, secret, nil)
	h.m = h.newManager()
	return h
}

func (h *harness) newManager() *Manager {
	return NewManager(Deps{Cfg: h.cfg, Jobs: job.NewClient(h.cs, h.cfg.BuildNamespace), Logs: h.logs,
		Registry: h.reg, Git: h.git, Argo: h.argo, Events: h.sender})
}

func (h *harness) close() {
	h.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := h.m.Shutdown(ctx); err != nil {
		h.t.Errorf("shutdown: %v", err)
	}
	h.srv.CloseClientConnections()
	h.srv.Close()
	if h.core.badSig != 0 {
		h.t.Errorf("%d callbacks failed signature check", h.core.badSig)
	}
}

func (h *harness) waitFor(what string, cond func() bool) {
	h.t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			h.t.Fatalf("timed out waiting for %s; events=%v", what, types(h.core.list()))
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func (h *harness) waitEvent(typ string) contract.Event {
	h.t.Helper()
	var found contract.Event
	h.waitFor("event "+typ, func() bool {
		for _, ev := range h.core.list() {
			if ev.Type == typ {
				found = ev
				return true
			}
		}
		return false
	})
	return found
}

func (h *harness) job() *batchv1.Job {
	j, err := h.cs.BatchV1().Jobs("kodeploy-build").Get(context.Background(), jobName, metav1.GetOptions{})
	if err != nil {
		return nil
	}
	return j
}

func (h *harness) finishJob(ok bool) {
	h.t.Helper()
	h.waitFor("job", func() bool { return h.job() != nil })
	j := h.job()
	if ok {
		j.Status.Succeeded = 1
	} else {
		j.Status.Failed = 1
		j.Status.Conditions = []batchv1.JobCondition{{Type: batchv1.JobFailed, Status: corev1.ConditionTrue}}
	}
	if _, err := h.cs.BatchV1().Jobs("kodeploy-build").UpdateStatus(context.Background(), j, metav1.UpdateOptions{}); err != nil {
		h.t.Fatal(err)
	}
}

func (h *harness) idle() {
	h.t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if !h.m.Wait(ctx) {
		h.t.Fatalf("manager did not become idle; events=%v", types(h.core.list()))
	}
}

func types(evs []contract.Event) []string {
	var out []string
	for _, ev := range evs {
		if ev.Type != contract.EventLog {
			out = append(out, ev.Type)
		}
	}
	return out
}

func logLines(evs []contract.Event) []string {
	var out []string
	for _, ev := range evs {
		out = append(out, ev.Lines...)
	}
	return out
}

func checkSeq(t *testing.T, evs []contract.Event, from int64) {
	t.Helper()
	for i, ev := range evs {
		if ev.Seq != from+int64(i) {
			t.Fatalf("event %d has seq %d, want %d", i, ev.Seq, from+int64(i))
		}
	}
}

// ---- build --------------------------------------------------------------------------------------

func TestBuildEarlyTrigger(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()

	if err := h.m.Submit(buildReq()); err != nil {
		t.Fatal(err)
	}
	committed := h.waitEvent(contract.EventCommitted)
	deployed := h.waitEvent(contract.EventDeployed)
	if h.job().Status.Succeeded != 0 {
		t.Fatal("deploy should happen before the job ends")
	}
	if committed.TriggeredEarly == nil || !*committed.TriggeredEarly || committed.PushDoneAt == nil {
		t.Fatalf("committed: %+v", committed)
	}
	wantImage := repo + ":" + buildID + "@" + digest
	if committed.Image != wantImage || deployed.Image != wantImage || deployed.SyncedAt == nil || deployed.HealthyAt == nil {
		t.Fatalf("images: %q %q", committed.Image, deployed.Image)
	}

	// reap: 배포 뒤 export가 끝나면 finished
	close(h.logs.gate)
	h.finishJob(true)
	fin := h.waitEvent(contract.EventFinished)
	h.idle()
	if !*fin.JobSucceeded || *fin.ExportFailed || fin.JobEndedAt == nil {
		t.Fatalf("finished: %+v", fin)
	}

	evs := h.core.list()
	checkSeq(t, evs, 1)
	if got := strings.Join(types(evs), ","); got != "committed,deployed,finished" {
		t.Fatalf("event order %s", got)
	}
	lines := logLines(evs)
	if lines[0] != "=== clone (init) ===" || lines[len(lines)-1] != "#22 DONE 28.1s" {
		t.Fatalf("log lines %q", lines)
	}

	v := h.git.get(ns)
	if v.Image != wantImage || v.Runtime != "java" || *v.Port != 8080 || v.UserID != userID {
		t.Fatalf("values %+v", v)
	}
	if h.git.messages[0] != "deploy(tenant-d6d8b759): build 3f9a2c1d [3f9a2c1d] by core" {
		t.Fatalf("message %q", h.git.messages[0])
	}
	ann := h.job().Annotations
	for _, k := range []string{job.AnnRequest, job.AnnDigest, job.AnnCommitSHA, job.AnnSynced, job.AnnFinished, job.AnnAckedSeq} {
		if ann[k] == "" {
			t.Errorf("annotation %s missing", k)
		}
	}
	if ann[job.AnnDigest] != digest {
		t.Errorf("digest annotation %q", ann[job.AnnDigest])
	}
	var stored contract.DeployRequest
	if err := json.Unmarshal([]byte(ann[job.AnnRequest]), &stored); err != nil || stored.Build.Ref != "main" {
		t.Errorf("request annotation: %v", err)
	}
}

func TestBuildExportFailsAfterPush(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	_ = h.m.Submit(buildReq())
	h.waitEvent(contract.EventDeployed)
	close(h.logs.gate)
	h.finishJob(false)
	fin := h.waitEvent(contract.EventFinished)
	h.idle()
	if *fin.JobSucceeded || !*fin.ExportFailed {
		t.Fatalf("finished: %+v", fin)
	}
	if got := strings.Join(types(h.core.list()), ","); got != "committed,deployed,finished" {
		t.Fatalf("export failure must not fail the deploy: %s", got)
	}
}

func TestBuildFailure(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.logs.before = []string{"=== clone (init) ===", "fatal: Remote branch nope not found"}
	close(h.logs.gate)
	h.logs.after = nil

	_ = h.m.Submit(buildReq())
	h.finishJob(false)
	failed := h.waitEvent(contract.EventFailed)
	h.idle()
	if failed.Stage != contract.StageBuild || len(failed.LastLines) != 2 || failed.LastLines[1] != "fatal: Remote branch nope not found" {
		t.Fatalf("failed: %+v", failed)
	}
	if h.git.commits != 0 {
		t.Fatal("must not commit")
	}
	if got := strings.Join(types(h.core.list()), ","); got != "failed" {
		t.Fatalf("events %s", got)
	}
	if h.job().Annotations[job.AnnFinished] == "" {
		t.Fatal("finished annotation missing")
	}
}

// 마커를 못 봤는데 Job이 성공하면 태그로 digest를 찾아 같은 배포 경로로 간다.
func TestBuildFallbackWithoutMarker(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.logs.before = preLines
	close(h.logs.gate)

	_ = h.m.Submit(buildReq())
	h.finishJob(true)
	committed := h.waitEvent(contract.EventCommitted)
	h.waitEvent(contract.EventFinished)
	h.idle()
	if h.reg.resolve != 1 || committed.TriggeredEarly == nil || *committed.TriggeredEarly || committed.PushDoneAt != nil {
		t.Fatalf("committed %+v resolve=%d", committed, h.reg.resolve)
	}
	if got := strings.Join(types(h.core.list()), ","); got != "committed,deployed,finished" {
		t.Fatalf("events %s", got)
	}
}

// 원본 EARLY_TRIGGER=false: 마커를 봐도 Job 종료를 기다린 뒤 배포한다.
func TestEarlyTriggerOff(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, func(c *config.Config) { c.EarlyTrigger = false })
	defer h.close()

	_ = h.m.Submit(buildReq())
	h.waitFor("push line consumed", func() bool {
		h.m.mu.Lock()
		r := h.m.active[buildID]
		h.m.mu.Unlock()
		d, _ := r.pushed()
		return d != ""
	})
	time.Sleep(30 * time.Millisecond)
	if h.git.commits != 0 {
		t.Fatal("committed before the job ended")
	}
	close(h.logs.gate)
	h.finishJob(true)
	committed := h.waitEvent(contract.EventCommitted)
	h.waitEvent(contract.EventFinished)
	h.idle()
	if committed.TriggeredEarly == nil || *committed.TriggeredEarly || committed.PushDoneAt == nil || h.reg.resolve != 0 {
		t.Fatalf("committed %+v", committed)
	}
}

func TestBuildImageMissingInRegistry(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.reg.exists = map[string]bool{}
	_ = h.m.Submit(buildReq())
	failed := h.waitEvent(contract.EventFailed)
	if failed.Stage != contract.StageCommit || !strings.Contains(failed.Reason, "registry") {
		t.Fatalf("failed %+v", failed)
	}
	close(h.logs.gate)
	h.finishJob(true)
	h.idle()
	if got := strings.Join(types(h.core.list()), ","); got != "failed" {
		t.Fatalf("events %s", got)
	}
}

func TestArgoSyncFailure(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.argo.waitErr = &argo.Failure{Stage: contract.StageSync, Reason: "one or more objects failed to apply"}
	_ = h.m.Submit(buildReq())
	failed := h.waitEvent(contract.EventFailed)
	close(h.logs.gate)
	h.finishJob(true)
	h.idle()
	if failed.Stage != contract.StageSync || failed.Reason != "one or more objects failed to apply" {
		t.Fatalf("failed %+v", failed)
	}
	if got := strings.Join(types(h.core.list()), ","); got != "committed,failed" {
		t.Fatalf("events %s", got)
	}
}

// ---- 취소·종료·재개 -------------------------------------------------------------------------------

func TestUserCancel(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.logs.before = preLines // 마커 전 상태에서 멈춰 있다

	_ = h.m.Submit(buildReq())
	h.waitFor("job", func() bool { return h.job() != nil })
	if err := h.m.Cancel(buildID); err != nil {
		t.Fatal(err)
	}
	h.waitEvent(contract.EventCancelled)
	h.idle()
	if h.job() != nil {
		t.Fatal("job should be deleted")
	}
	if err := h.m.Cancel(buildID); !errors.Is(err, contract.ErrNotFound) {
		t.Fatalf("second cancel: %v", err)
	}
}

// 종료(SIGTERM)는 Job을 남긴다. 새 빌더가 Resume으로 이어서 끝낸다. seq는 acked-seq + 여유부터,
// 이미 보낸 로그 줄은 다시 보내지 않는다.
func TestShutdownThenResume(t *testing.T) {
	defer goleak.VerifyNone(t)
	// 줄들이 한 이벤트로 가게 배치 간격을 넉넉히 (acked-log-lines 기록은 2초에 한 번이라)
	h := newHarness(t, func(c *config.Config) { c.LogBatchInterval = 100 * time.Millisecond })
	defer h.close()
	h.logs.before = preLines

	_ = h.m.Submit(buildReq())
	h.waitFor("log lines delivered and acked", func() bool {
		j := h.job()
		return j != nil && j.Annotations[job.AnnAckedLogLines] == strconv.Itoa(len(preLines))
	})
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	if err := h.m.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	cancel()
	if h.job() == nil {
		t.Fatal("shutdown must keep the job")
	}
	before := h.core.list()
	if len(types(before)) != 0 {
		t.Fatalf("shutdown must not send terminal events: %v", types(before))
	}
	acked, _ := strconv.ParseInt(h.job().Annotations[job.AnnAckedSeq], 10, 64)

	// 새 빌더: 로그는 처음부터 다시 흐르고, 이번엔 push 줄까지 있다
	h.logs.mu.Lock()
	h.logs.before = append(append([]string{}, preLines...), pushLines...)
	h.logs.mu.Unlock()
	h.m = h.newManager()
	n, err := h.m.Resume(context.Background())
	if err != nil || n != 1 {
		t.Fatalf("resume: %d %v", n, err)
	}
	h.waitEvent(contract.EventDeployed)
	close(h.logs.gate)
	h.finishJob(true)
	h.waitEvent(contract.EventFinished)
	h.idle()

	after := h.core.list()[len(before):]
	if after[0].Seq != acked+1+seqResumeMargin {
		t.Fatalf("resumed seq %d, want %d", after[0].Seq, acked+1+seqResumeMargin)
	}
	checkSeq(t, after, acked+1+seqResumeMargin)
	resent := logLines(after)
	if resent[0] != pushLines[0] {
		t.Fatalf("already delivered lines were sent again: %q", resent)
	}
	if got := strings.Join(types(after), ","); got != "committed,deployed,finished" {
		t.Fatalf("events after resume %s", got)
	}
	// 끝난 Job은 다음 재개 대상이 아니다
	if n, _ := h.newManager().Resume(context.Background()); n != 0 {
		t.Fatalf("finished job resumed again: %d", n)
	}
}

// 커밋까지 끝난 뒤 종료되면, 재개 때 committed를 다시 알리고 Argo 대기부터 이어간다.
func TestResumeAfterCommit(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.argo.block = make(chan struct{})

	_ = h.m.Submit(buildReq())
	h.waitEvent(contract.EventCommitted)
	h.waitFor("commit-sha annotation", func() bool { return h.job().Annotations[job.AnnCommitSHA] != "" })
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	_ = h.m.Shutdown(ctx)
	cancel()
	commits := h.git.commits

	h.argo.mu.Lock()
	h.argo.block = nil
	h.argo.mu.Unlock()
	h.m = h.newManager()
	if n, _ := h.m.Resume(context.Background()); n != 1 {
		t.Fatal("not resumed")
	}
	h.waitEvent(contract.EventDeployed)
	close(h.logs.gate)
	h.finishJob(true)
	h.waitEvent(contract.EventFinished)
	h.idle()
	if h.git.commits != commits {
		t.Fatal("resume must not commit again")
	}
	if got := strings.Join(types(h.core.list()), ","); got != "committed,committed,deployed,finished" {
		t.Fatalf("events %s", got)
	}
}

// ---- 동시성 규칙 -----------------------------------------------------------------------------------

func TestSubmitConflictsAndBusy(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, func(c *config.Config) { c.MaxActiveBuilds = 1 })
	defer h.close()
	h.logs.before = preLines

	if err := h.m.Submit(buildReq()); err != nil {
		t.Fatal(err)
	}
	var conflict *contract.ConflictError
	if err := h.m.Submit(buildReq()); !errors.As(err, &conflict) || conflict.BuildID != buildID {
		t.Fatalf("same build_id: %v", err)
	}
	si := &contract.DeployRequest{BuildID: "aa11bb22", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindSetImage,
		Unit: &contract.Unit{Runtime: "java", Port: 8080}, Image: repo + ":v1@" + digest}
	if err := h.m.Submit(si); !errors.As(err, &conflict) || conflict.BuildID != buildID {
		t.Fatalf("same namespace+slot: %v", err)
	}
	del := &contract.DeployRequest{BuildID: "cc33dd44", Namespace: ns, Kind: contract.KindDelete}
	if err := h.m.Submit(del); !errors.As(err, &conflict) {
		t.Fatalf("delete during build: %v", err)
	}
	other := buildReq()
	other.BuildID, other.Namespace = "0badf00d", "tenant-0badf00d"
	var busy *contract.BusyError
	if err := h.m.Submit(other); !errors.As(err, &busy) || busy.RetryAfter <= 0 {
		t.Fatalf("max active builds: %v", err)
	}
	// 다른 slot의 set-image는 막지 않는다 (Job을 쓰지 않아 상한에도 안 걸린다)
	st := &contract.DeployRequest{BuildID: "dd44ee55", Namespace: ns, Slot: contract.SlotStatic, Kind: contract.KindSetImage,
		Image: repo + "-site:v1@" + digest}
	if err := h.m.Submit(st); err != nil {
		t.Fatalf("static set-image: %v", err)
	}
	_ = h.m.Cancel(buildID)
	h.idle()
}

// ---- set-image · config · delete -----------------------------------------------------------------

func TestSetImageConfigDelete(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()

	si := &contract.DeployRequest{BuildID: "aa11bb22", Actor: "d6d8b759", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindSetImage,
		Unit: &contract.Unit{Runtime: "java", Port: 8080}, Image: repo + ":99483bd9@" + digest,
		Values: &contract.CoreValues{UserID: func() *string { s := userID; return &s }()}}
	if err := h.m.Submit(si); err != nil {
		t.Fatal(err)
	}
	h.idle()
	if v := h.git.get(ns); v == nil || v.Image != si.Image || v.UserID != userID {
		t.Fatalf("set-image values %+v", v)
	}
	if h.git.messages[0] != "deploy(tenant-d6d8b759): set-image 99483bd9 [aa11bb22] by d6d8b759" {
		t.Fatalf("message %q", h.git.messages[0])
	}

	rev := 3
	cfgReq := &contract.DeployRequest{BuildID: "bb22cc33", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindConfig,
		Values: &contract.CoreValues{EnvRevision: &rev}}
	_ = h.m.Submit(cfgReq)
	h.idle()
	if v := h.git.get(ns); v.EnvRevision != 3 || v.Image != si.Image {
		t.Fatalf("config values %+v", v)
	}

	_ = h.m.Submit(&contract.DeployRequest{BuildID: "cc33dd44", Namespace: ns, Kind: contract.KindDelete})
	h.idle()
	if h.git.get(ns) != nil {
		t.Fatal("values not deleted")
	}
	_ = h.m.Submit(&contract.DeployRequest{BuildID: "dd44ee55", Namespace: ns, Kind: contract.KindDelete})
	h.idle()

	evs := h.core.list()
	if got := strings.Join(types(evs), ","); got != "committed,deployed,committed,deployed,deleted,deleted" {
		t.Fatalf("events %s", got)
	}
	if evs[4].CommitSHA == "" || evs[5].CommitSHA != "" {
		t.Fatalf("delete commit shas %q %q", evs[4].CommitSHA, evs[5].CommitSHA)
	}
	if evs[2].Image != si.Image {
		t.Fatalf("config committed image %q", evs[2].Image)
	}
}

func TestSetImageMissingDigestFails(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	si := &contract.DeployRequest{BuildID: "aa11bb22", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindSetImage,
		Unit: &contract.Unit{Runtime: "java", Port: 8080}, Image: repo + ":v1@sha256:" + strings.Repeat("0", 64)}
	_ = h.m.Submit(si)
	failed := h.waitEvent(contract.EventFailed)
	h.idle()
	if failed.Stage != contract.StageCommit || h.git.commits != 0 {
		t.Fatalf("failed %+v commits=%d", failed, h.git.commits)
	}
}

func TestConfigWithoutFileFails(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	_ = h.m.Submit(&contract.DeployRequest{BuildID: "bb22cc33", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindConfig,
		Values: &contract.CoreValues{}})
	failed := h.waitEvent(contract.EventFailed)
	h.idle()
	if failed.Stage != contract.StageCommit || !strings.Contains(failed.Reason, "does not exist") {
		t.Fatalf("failed %+v", failed)
	}
}

func TestCancelDuringArgoWait(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	defer h.close()
	h.argo.block = make(chan struct{})
	si := &contract.DeployRequest{BuildID: "aa11bb22", Namespace: ns, Slot: contract.SlotServer, Kind: contract.KindSetImage,
		Unit: &contract.Unit{Runtime: "java", Port: 8080}, Image: repo + ":v1@" + digest}
	_ = h.m.Submit(si)
	h.waitEvent(contract.EventCommitted)
	_ = h.m.Cancel("aa11bb22")
	h.waitEvent(contract.EventCancelled)
	h.idle()
}

func TestShutdownRejectsNewWork(t *testing.T) {
	defer goleak.VerifyNone(t)
	h := newHarness(t, nil)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_ = h.m.Shutdown(ctx)
	if err := h.m.Submit(buildReq()); !errors.Is(err, errClosed) {
		t.Fatalf("got %v", err)
	}
	h.close()
}
