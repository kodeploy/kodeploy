// Package run은 요청 하나를 끝까지 돌리는 상태기계와, 진행 중인 요청을 관리하는 Manager다.
// build는 Job → 로그·마커 → (먼저 온 쪽) → registry 확인 → gitops 커밋 → Argo 대기 → reap 순서다 (지시서 4-2).
package run

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"strconv"
	"sync"
	"time"

	batchv1 "k8s.io/api/batch/v1"

	"github.com/kodeploy/kodeploy/builder/internal/argo"
	"github.com/kodeploy/kodeploy/builder/internal/callback"
	"github.com/kodeploy/kodeploy/builder/internal/config"
	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/gitops"
	"github.com/kodeploy/kodeploy/builder/internal/job"
)

// 기능별 의존성. 실제 구현은 같은 이름의 패키지, 테스트는 가짜.
type Jobs interface {
	Create(ctx context.Context, j *batchv1.Job) error
	Wait(ctx context.Context, name string) (job.Result, error)
	Annotate(ctx context.Context, name string, kv map[string]string) error
	Delete(ctx context.Context, name string) error
	ListManaged(ctx context.Context) ([]batchv1.Job, error)
}

type LogFollower interface {
	Follow(ctx context.Context, buildID string, out chan<- string) error
}

type Registry interface {
	Exists(ctx context.Context, repo, digest string) error
	Resolve(ctx context.Context, repo, tag string) (string, error)
}

type Git interface {
	Commit(ctx context.Context, ch gitops.Change) (gitops.Result, error)
}

type Argo interface {
	Refresh(ctx context.Context, app string) error
	Wait(ctx context.Context, app, sha string, since time.Time, timeout time.Duration) (argo.Synced, error)
}

type Deps struct {
	Cfg      *config.Config
	Jobs     Jobs
	Logs     LogFollower
	Registry Registry
	Git      Git
	Argo     Argo
	Events   *callback.Sender
	Log      *slog.Logger
}

var (
	errUserCancelled = errors.New("cancelled by request")
	errShutdown      = errors.New("builder shutting down")
	errClosed        = errors.New("builder is shutting down")
)

const (
	// 429의 Retry-After. 빌드 하나가 보통 몇 분이라 짧게 다시 물어보게 한다.
	busyRetryAfter = 15 * time.Second
	// 재개 시 seq를 acked-seq에서 이만큼 띄운다. acked-seq 기록은 로그 이벤트에서 2초마다만 하므로,
	// core가 받았지만 기록 못 한 seq를 다시 쓰면 core가 중복으로 보고 버린다.
	seqResumeMargin = 100
)

type Manager struct {
	d    Deps
	base context.Context // 빌더 수명. 콜백 큐는 이 ctx로 돈다
	stop context.CancelFunc

	mu     sync.Mutex
	active map[string]*run
	closed bool
	wg     sync.WaitGroup // run 고루틴
	qwg    sync.WaitGroup // 콜백 큐 고루틴 (run이 끝난 뒤에도 남은 이벤트를 보낸다)
}

func NewManager(d Deps) *Manager {
	if d.Log == nil {
		d.Log = slog.New(slog.DiscardHandler)
	}
	base, stop := context.WithCancel(context.Background())
	return &Manager{d: d, base: base, stop: stop, active: map[string]*run{}}
}

// Submit은 api.Dispatcher 구현이다.
func (m *Manager) Submit(req *contract.DeployRequest) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.closed {
		return errClosed
	}
	if _, ok := m.active[req.BuildID]; ok {
		return &contract.ConflictError{BuildID: req.BuildID, Reason: "build_id already in progress"}
	}
	builds := 0
	for _, r := range m.active {
		if r.req.Kind == contract.KindBuild {
			builds++ // reap 중인 Job도 클러스터 자원을 쓰므로 센다
		}
		if r.req.Namespace != req.Namespace || r.reaping() {
			continue
		}
		if req.Kind == contract.KindDelete || r.req.Kind == contract.KindDelete || r.req.Slot == req.Slot {
			return &contract.ConflictError{BuildID: r.req.BuildID, Reason: "another request for this namespace/slot is in progress"}
		}
	}
	if req.Kind == contract.KindBuild && builds >= m.d.Cfg.MaxActiveBuilds {
		return &contract.BusyError{RetryAfter: busyRetryAfter}
	}
	m.startLocked(newRun(m, req, resumeState{}))
	return nil
}

// Cancel은 api.Dispatcher 구현이다.
func (m *Manager) Cancel(buildID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	r, ok := m.active[buildID]
	if !ok {
		return contract.ErrNotFound
	}
	r.cancel(errUserCancelled)
	return nil
}

func (m *Manager) startLocked(r *run) {
	m.active[r.req.BuildID] = r
	m.qwg.Add(1)
	go func() {
		<-r.q.Done()
		m.qwg.Done()
	}()
	m.wg.Add(1)
	go func() {
		defer m.wg.Done()
		r.execute()
		m.mu.Lock()
		delete(m.active, r.req.BuildID)
		m.mu.Unlock()
	}()
}

// Shutdown은 진행 중인 요청을 errShutdown으로 멈춘다. Job은 지우지 않는다 (재시작 후 Resume으로 이어간다).
func (m *Manager) Shutdown(ctx context.Context) error {
	m.mu.Lock()
	m.closed = true
	for _, r := range m.active {
		r.cancel(errShutdown)
	}
	m.mu.Unlock()

	var err error
	if !waitGroup(ctx, &m.wg) {
		err = context.Cause(ctx)
	}
	// 남은 콜백은 시간 안에서 마저 보낸다. 시간이 다 되면 큐를 멈춘다 (못 보낸 것은 재개 때 다시 알린다).
	if !waitGroup(ctx, &m.qwg) && err == nil {
		err = context.Cause(ctx)
	}
	m.stop()
	m.qwg.Wait()
	return err
}

// Wait은 진행 중인 요청과 콜백이 모두 끝날 때까지 기다린다 (테스트·종료용).
func (m *Manager) Wait(ctx context.Context) bool {
	return waitGroup(ctx, &m.wg) && waitGroup(ctx, &m.qwg)
}

func waitGroup(ctx context.Context, wg *sync.WaitGroup) bool {
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
		return true
	case <-ctx.Done():
		return false
	}
}

// Resume은 시작할 때 부른다. 빌더가 만든 Job 중 finished 표시가 없는 것을 이어간다 (지시서 4-5).
func (m *Manager) Resume(ctx context.Context) (int, error) {
	jobs, err := m.d.Jobs.ListManaged(ctx)
	if err != nil {
		return 0, err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	n := 0
	for _, j := range jobs {
		ann := j.Annotations
		if ann[job.AnnFinished] != "" {
			continue
		}
		var req contract.DeployRequest
		if err := json.Unmarshal([]byte(ann[job.AnnRequest]), &req); err != nil || req.Build == nil || req.BuildID == "" {
			m.d.Log.Warn("skip job without a readable request", "job", j.Name, "err", err)
			continue
		}
		if _, ok := m.active[req.BuildID]; ok {
			continue
		}
		acked, _ := strconv.ParseInt(ann[job.AnnAckedSeq], 10, 64)
		logLines, _ := strconv.ParseInt(ann[job.AnnAckedLogLines], 10, 64)
		st := resumeState{
			resumed:   true,
			digest:    ann[job.AnnDigest],
			commitSHA: ann[job.AnnCommitSHA],
			synced:    ann[job.AnnSynced] != "",
			firstSeq:  acked + 1 + seqResumeMargin,
			logLines:  logLines,
		}
		m.d.Log.Info("resuming build", "build_id", req.BuildID, "digest", st.digest != "", "commit", st.commitSHA != "", "synced", st.synced)
		m.startLocked(newRun(m, &req, st))
		n++
	}
	return n, nil
}
