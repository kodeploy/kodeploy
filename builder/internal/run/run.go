package run

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"

	"github.com/kodeploy/kodeploy/builder/internal/argo"
	"github.com/kodeploy/kodeploy/builder/internal/callback"
	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/gitops"
	"github.com/kodeploy/kodeploy/builder/internal/job"
	"github.com/kodeploy/kodeploy/builder/internal/logs"
	"github.com/kodeploy/kodeploy/builder/internal/marker"
)

const (
	lastLinesKept = 50               // failed 이벤트의 last_lines
	logDrainGrace = 10 * time.Second // Job이 끝난 뒤 로그 스트림이 마저 끝나기를 기다리는 시간
	ackWriteEvery = 2 * time.Second  // log 이벤트의 acked-seq 어노테이션 기록 간격
	apiTimeout    = 10 * time.Second // 정리용 API 호출 (run ctx가 이미 끝났을 수 있다)
)

type resumeState struct {
	resumed   bool
	jobName   string
	digest    string
	commitSHA string
	synced    bool
	firstSeq  int64
	logLines  int64
}

type jobOutcome struct {
	res job.Result
	err error
}

type logPipe struct {
	stop context.CancelFunc
	done chan struct{}
}

type run struct {
	m   *Manager
	req *contract.DeployRequest
	st  resumeState
	log *slog.Logger

	ctx    context.Context
	cancel context.CancelCauseFunc
	q      *callback.Queue

	jobName   string
	reapingSt atomic.Bool

	// run 고루틴에서만 쓴다
	terminal   bool // deployed/failed/cancelled를 보냈다
	deployed   bool
	finished   bool // finished를 보냈다
	earlyKnown bool
	early      bool

	mu       sync.Mutex // 로그 고루틴과 나눠 쓰는 칸
	pushDig  string
	pushAt   time.Time
	lastRing []string

	ackMu        sync.Mutex
	lastAckWrite time.Time
}

func newRun(m *Manager, req *contract.DeployRequest, st resumeState) *run {
	ctx, cancel := context.WithCancelCause(m.base)
	r := &run{m: m, req: req, st: st, ctx: ctx, cancel: cancel,
		log: m.d.Log.With("build_id", req.BuildID, "kind", req.Kind, "namespace", req.Namespace)}
	var onAck func(callback.Ack)
	if req.Kind == contract.KindBuild {
		r.jobName = st.jobName
		if r.jobName == "" {
			r.jobName = job.Name(req.BuildID, *req.Values.UserID)
		}
		onAck = r.onAck
	}
	r.q = m.d.Events.Start(m.base, req.BuildID, callback.StartOptions{FirstSeq: st.firstSeq, LogOffset: st.logLines, OnAck: onAck})
	return r
}

func (r *run) reaping() bool { return r.reapingSt.Load() }
func (r *run) stopped() bool { return r.ctx.Err() != nil }

func (r *run) execute() {
	defer r.cancel(nil)
	defer r.q.Close()
	switch r.req.Kind {
	case contract.KindBuild:
		r.build()
	case contract.KindSetImage:
		r.setImage()
	case contract.KindConfig:
		r.config()
	case contract.KindDelete:
		r.delete()
	}
	if r.stopped() {
		r.cleanup()
	}
}

// ---- build --------------------------------------------------------------------------------------

func (r *run) build() {
	if r.st.commitSHA != "" || r.st.digest != "" {
		r.buildResumeAfterPush()
		return
	}
	if !r.st.resumed {
		if err := r.createJob(); err != nil {
			if !r.stopped() {
				r.fail(contract.StageBuild, "create build job: "+err.Error(), nil)
			}
			return
		}
	}
	jobDone := r.watchJob()
	pushDone := make(chan string, 1)
	pipe := r.startLogs(pushDone)

	var early <-chan string
	if r.m.d.Cfg.EarlyTrigger {
		early = pushDone
	}
	select {
	case d := <-early:
		r.earlyKnown, r.early = true, true
		ok := r.deploy(d)
		if r.stopped() {
			r.stopLogs(pipe)
			return
		}
		r.reap(ok, jobDone, pipe)
	case out := <-jobDone:
		r.afterJob(out, pipe)
	case <-r.ctx.Done():
		r.stopLogs(pipe)
	}
}

func (r *run) createJob() error {
	b := r.req.Build
	reqJSON, err := json.Marshal(r.req)
	if err != nil {
		return err
	}
	j := job.Build(job.Params{
		Namespace:             r.m.d.Cfg.BuildNamespace,
		BuildID:               r.req.BuildID,
		UserID:                *r.req.Values.UserID,
		Image:                 b.ImageRepo + ":" + b.ImageTag,
		RepoURL:               b.Repo,
		Branch:                b.Ref,
		DockerfileSubdir:      b.DockerfileDir,
		DockerfileFilename:    b.DockerfileName,
		CacheRef:              b.CacheRef,
		BuildKitImage:         r.m.d.Cfg.BuildKitImage,
		ActiveDeadlineSeconds: r.m.d.Cfg.BuildActiveDeadlineSeconds,
		RequestJSON:           string(reqJSON),
	})
	err = r.m.d.Jobs.Create(r.ctx, j)
	if apierrors.IsAlreadyExists(err) {
		return nil // 같은 요청이 다시 왔다 = 있는 Job을 이어서 본다
	}
	if err == nil {
		r.log.Info("build job created", "job", j.Name)
	}
	return err
}

// watchJob은 Job 종료를 백그라운드로 기다린다. 상한은 원본과 같이 activeDeadlineSeconds + 30초.
func (r *run) watchJob() <-chan jobOutcome {
	ch := make(chan jobOutcome, 1)
	limit := time.Duration(r.m.d.Cfg.BuildActiveDeadlineSeconds+30) * time.Second
	go func() {
		ctx, cancel := context.WithTimeout(r.ctx, limit)
		defer cancel()
		res, err := r.m.d.Jobs.Wait(ctx, r.jobName)
		ch <- jobOutcome{res, err}
	}()
	return ch
}

// afterJob은 마커보다 Job 종료가 먼저 온 경우다 (early-trigger OFF, 마커 못 봄, 빌드 실패).
func (r *run) afterJob(out jobOutcome, pipe *logPipe) {
	switch {
	case out.err != nil:
		r.stopLogs(pipe)
		if r.stopped() {
			return
		}
		r.deleteJob()
		r.fail(contract.StageTimeout, fmt.Sprintf("build job did not finish within %ds", r.m.d.Cfg.BuildActiveDeadlineSeconds+30), r.lastLines())
	case out.res.Gone:
		r.stopLogs(pipe)
		r.terminal = true
		r.emit(contract.Event{Type: contract.EventCancelled, Reason: "build job was deleted"})
	case !out.res.Succeeded:
		r.waitLogs(pipe)
		r.markFinished()
		r.fail(contract.StageBuild, "build failed", r.lastLines())
	default:
		r.waitLogs(pipe) // 마커가 마지막 줄까지 보게 한다
		digest, _ := r.pushed()
		if digest == "" {
			// 폴백: 마커 없이 성공 → 태그로 digest 조회 (지시서 4-2 8)
			d, err := r.m.d.Registry.Resolve(r.ctx, r.req.Build.ImageRepo, r.req.Build.ImageTag)
			if err != nil {
				if !r.stopped() {
					r.markFinished()
					r.fail(contract.StageCommit, "build succeeded but the image digest was not found: "+err.Error(), nil)
				}
				return
			}
			digest = d
		}
		r.earlyKnown = true
		ok := r.deploy(digest)
		if r.stopped() {
			return
		}
		r.markFinished()
		if ok {
			r.emitFinished(out, false)
		}
	}
}

// reap은 배포 판정 뒤 Job(cache export)이 끝나기를 기다려 finished를 보낸다.
// push 뒤 Job이 실패하면 export_failed. 배포 결과는 건드리지 않는다.
func (r *run) reap(deployedOK bool, jobDone <-chan jobOutcome, pipe *logPipe) {
	r.reapingSt.Store(true)
	var out jobOutcome
	select {
	case out = <-jobDone:
	case <-r.ctx.Done():
		r.stopLogs(pipe)
		return
	}
	if out.err != nil && r.stopped() {
		r.stopLogs(pipe)
		return
	}
	r.waitLogs(pipe)
	if out.err != nil {
		r.deleteJob()
	}
	r.markFinished()
	if deployedOK {
		r.emitFinished(out, true)
	}
}

func (r *run) buildResumeAfterPush() {
	jobDone := r.watchJob()
	b := r.req.Build
	image := b.ImageRepo + ":" + b.ImageTag + "@" + r.st.digest
	ok := false
	switch {
	case r.st.commitSHA == "":
		ok = r.deploy(r.st.digest)
	case r.st.synced:
		// 이미 배포됐다. core가 못 받았을 수 있으니 다시 알린다 (core는 같은 내용이면 무시).
		r.emitCommitted(image, r.st.commitSHA)
		r.terminal, r.deployed, ok = true, true, true
		r.emit(contract.Event{Type: contract.EventDeployed, Image: image})
	default:
		r.emitCommitted(image, r.st.commitSHA)
		ok = r.waitArgo(image, r.st.commitSHA)
	}
	if r.stopped() {
		return
	}
	r.reap(ok, jobDone, nil)
}

// ---- 로그 파이프라인: follow → 마커·최근 줄 → 1초 배치 → 콜백 큐 ---------------------------------------

func (r *run) startLogs(pushDone chan<- string) *logPipe {
	fctx, stop := context.WithCancel(r.ctx)
	lines := make(chan string, 256)
	batchIn := make(chan string, 256)
	done := make(chan struct{})
	b := r.req.Build

	go func() {
		if err := r.m.d.Logs.Follow(fctx, r.req.BuildID, lines); err != nil && fctx.Err() == nil {
			r.log.Warn("log follow ended", "err", err)
		}
		close(lines)
	}()
	go func() {
		m := marker.New(b.ImageRepo, b.ImageTag)
		skip := r.st.logLines // 재개: 이미 core에 보낸 줄은 마커만 보고 다시 보내지 않는다
		for l := range lines {
			r.remember(l)
			if d, ok := m.Feed(l); ok {
				r.setPushed(d)
				pushDone <- d // 버퍼 1, 한 번만
			}
			if skip > 0 {
				skip--
				continue
			}
			batchIn <- l
		}
		close(batchIn)
	}()
	go func() {
		logs.Batch(batchIn, r.m.d.Cfg.LogBatchInterval, callback.MaxLinesPerEvent, r.q.Log)
		close(done)
	}()
	return &logPipe{stop: stop, done: done}
}

func (r *run) stopLogs(p *logPipe) {
	if p == nil {
		return
	}
	p.stop()
	<-p.done
}

// waitLogs는 로그 스트림이 스스로 끝나기를 잠깐 기다린다 (Pod이 끝나면 곧 EOF).
func (r *run) waitLogs(p *logPipe) {
	if p == nil {
		return
	}
	t := time.NewTimer(logDrainGrace)
	defer t.Stop()
	select {
	case <-p.done:
	case <-t.C:
	case <-r.ctx.Done():
	}
	r.stopLogs(p)
}

func (r *run) remember(l string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.lastRing = append(r.lastRing, l)
	if len(r.lastRing) > lastLinesKept {
		r.lastRing = r.lastRing[len(r.lastRing)-lastLinesKept:]
	}
}

func (r *run) lastLines() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]string(nil), r.lastRing...)
}

func (r *run) setPushed(d string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pushDig, r.pushAt = d, time.Now().UTC()
}

func (r *run) pushed() (string, time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.pushDig, r.pushAt
}

// ---- 배포 경로 (build·set-image 공통): registry 확인 → 커밋 → committed → Argo → deployed --------------

func (r *run) deploy(digest string) bool {
	b := r.req.Build
	return r.deployImage(b.ImageRepo, b.ImageTag, digest)
}

func (r *run) deployImage(repo, tag, digest string) bool {
	image := repo + ":" + tag + "@" + digest
	if err := r.m.d.Registry.Exists(r.ctx, repo, digest); err != nil {
		if !r.stopped() {
			r.fail(contract.StageCommit, "image is not in the registry: "+err.Error(), nil)
		}
		return false
	}
	r.annotate(map[string]string{job.AnnDigest: digest})
	res, ok := r.commit(tag, image)
	if !ok {
		return false
	}
	r.annotate(map[string]string{job.AnnCommitSHA: res.CommitSHA})
	r.emitCommitted(image, res.CommitSHA)
	return r.waitArgo(image, res.CommitSHA)
}

func (r *run) commit(tag, image string) (gitops.Result, bool) {
	res, err := r.m.d.Git.Commit(r.ctx, gitops.Change{
		Namespace: r.req.Namespace,
		Message:   r.commitMessage(tag),
		Apply:     func(cur *gitops.Values) (*gitops.Values, error) { return gitops.Merge(cur, r.req, image) },
	})
	if err != nil {
		if !r.stopped() {
			r.fail(contract.StageCommit, err.Error(), nil)
		}
		return res, false
	}
	r.log.Info("committed", "sha", res.CommitSHA, "skipped", res.Skipped)
	return res, true
}

// 지시서 4-7: deploy(<namespace>): <kind> <image_tag> [<build_id>] by <actor>
func (r *run) commitMessage(tag string) string {
	actor := r.req.Actor
	if actor == "" {
		actor = "core"
	}
	parts := []string{"deploy(" + r.req.Namespace + "):", r.req.Kind}
	if tag != "" {
		parts = append(parts, tag)
	}
	return strings.Join(append(parts, "["+r.req.BuildID+"]", "by", actor), " ")
}

func (r *run) waitArgo(image, sha string) bool {
	cfg := r.m.d.Cfg
	since := time.Now()
	deadline := since.Add(cfg.ArgoWaitTimeout)
	rctx, cancel := context.WithDeadline(r.ctx, deadline)
	err := r.m.d.Argo.Refresh(rctx, r.req.Namespace)
	cancel()
	if err != nil {
		switch {
		case r.stopped():
		case errors.Is(err, context.DeadlineExceeded):
			r.fail(contract.StageTimeout, "argo application "+r.req.Namespace+" did not appear within "+cfg.ArgoWaitTimeout.String(), nil)
		default:
			r.fail(contract.StageSync, "argo refresh: "+err.Error(), nil)
		}
		return false
	}
	s, err := r.m.d.Argo.Wait(r.ctx, r.req.Namespace, sha, since, time.Until(deadline))
	if err != nil {
		if r.stopped() {
			return false
		}
		var f *argo.Failure
		if errors.As(err, &f) {
			r.fail(f.Stage, f.Reason, nil)
		} else {
			r.fail(contract.StageSync, err.Error(), nil)
		}
		return false
	}
	r.annotate(map[string]string{job.AnnSynced: s.SyncedAt.UTC().Format(time.RFC3339)})
	r.terminal, r.deployed = true, true
	syncedAt, healthyAt := s.SyncedAt.UTC(), s.HealthyAt.UTC()
	r.emit(contract.Event{Type: contract.EventDeployed, Image: image, SyncedAt: &syncedAt, HealthyAt: &healthyAt})
	r.log.Info("deployed", "image", image)
	return true
}

// ---- set-image · config · delete (Job 없음, 진행 상태는 메모리) --------------------------------------

func (r *run) setImage() {
	at := strings.LastIndex(r.req.Image, "@")
	repoTag, digest := r.req.Image[:at], r.req.Image[at+1:]
	colon := strings.LastIndex(repoTag, ":")
	r.deployImage(repoTag[:colon], repoTag[colon+1:], digest)
}

func (r *run) config() {
	res, ok := r.commit("", "")
	if !ok {
		return
	}
	image := res.Values.Image
	if r.req.Slot == contract.SlotStatic {
		image = res.Values.Static.Image
	}
	r.emitCommitted(image, res.CommitSHA)
	r.waitArgo(image, res.CommitSHA)
}

func (r *run) delete() {
	res, err := r.m.d.Git.Commit(r.ctx, gitops.Change{Namespace: r.req.Namespace, Message: r.commitMessage(""), Delete: true})
	if err != nil {
		if !r.stopped() {
			r.fail(contract.StageCommit, err.Error(), nil)
		}
		return
	}
	r.terminal = true
	r.emit(contract.Event{Type: contract.EventDeleted, CommitSHA: res.CommitSHA})
}

// ---- 취소·정리 ------------------------------------------------------------------------------------

// cleanup은 ctx가 끝나 멈춘 경우다. 종료(errShutdown)면 아무것도 지우지 않고 알리지도 않는다
// (재시작 후 이어간다). 사용자 취소면 Job을 지우고 cancelled를 보낸다.
func (r *run) cleanup() {
	cause := context.Cause(r.ctx)
	if errors.Is(cause, errShutdown) {
		r.log.Info("stopped for shutdown; job kept for resume")
		return
	}
	if r.req.Kind == contract.KindBuild {
		r.deleteJob()
	}
	switch {
	case r.finished:
		// 다 끝난 뒤에 온 취소
	case r.deployed:
		// reap 중 취소: 배포는 유효하고 export만 잘렸다
		now := time.Now().UTC()
		ok, failed := false, true
		r.emit(contract.Event{Type: contract.EventFinished, JobEndedAt: &now, JobSucceeded: &ok, ExportFailed: &failed})
	case r.terminal:
	default:
		r.terminal = true
		r.emit(contract.Event{Type: contract.EventCancelled})
	}
	r.log.Info("cancelled", "cause", cause)
}

func (r *run) deleteJob() {
	ctx, cancel := context.WithTimeout(r.m.base, apiTimeout)
	defer cancel()
	if err := r.m.d.Jobs.Delete(ctx, r.jobName); err != nil {
		r.log.Warn("delete job", "err", err)
	}
}

// ---- 이벤트·어노테이션 ------------------------------------------------------------------------------

func (r *run) emit(ev contract.Event) {
	ev.At = time.Now().UTC()
	r.q.Event(ev)
}

func (r *run) fail(stage, reason string, lines []string) {
	r.terminal = true
	r.log.Warn("failed", "stage", stage, "reason", reason)
	r.emit(contract.Event{Type: contract.EventFailed, Stage: stage, Reason: reason, LastLines: lines})
}

func (r *run) emitCommitted(image, sha string) {
	ev := contract.Event{Type: contract.EventCommitted, Image: image, CommitSHA: sha}
	if r.req.Kind == contract.KindBuild {
		if _, at := r.pushed(); !at.IsZero() {
			ev.PushDoneAt = &at
		}
		if r.earlyKnown {
			early := r.early
			ev.TriggeredEarly = &early
		}
	}
	r.emit(ev)
}

func (r *run) emitFinished(out jobOutcome, afterPush bool) {
	ended := out.res.EndedAt.UTC()
	if ended.IsZero() || out.err != nil {
		ended = time.Now().UTC()
	}
	ok := out.err == nil && out.res.Succeeded
	exportFailed := afterPush && !ok
	r.finished = true
	r.emit(contract.Event{Type: contract.EventFinished, JobEndedAt: &ended, JobSucceeded: &ok, ExportFailed: &exportFailed})
}

func (r *run) markFinished() {
	r.annotate(map[string]string{job.AnnFinished: time.Now().UTC().Format(time.RFC3339)})
}

// annotate는 build Job에 진행 상태를 적는다. 실패해도 흐름은 계속한다 (재개 정보가 조금 늦을 뿐).
func (r *run) annotate(kv map[string]string) {
	if r.req.Kind != contract.KindBuild {
		return
	}
	ctx, cancel := context.WithTimeout(r.m.base, apiTimeout)
	defer cancel()
	if err := r.m.d.Jobs.Annotate(ctx, r.jobName, kv); err != nil && !apierrors.IsNotFound(err) {
		r.log.Warn("annotate job", "err", err)
	}
}

// onAck은 콜백 큐 고루틴에서 불린다. log 이벤트는 ackWriteEvery마다만 기록한다.
func (r *run) onAck(a callback.Ack) {
	r.ackMu.Lock()
	now := time.Now()
	if a.Type == contract.EventLog && now.Sub(r.lastAckWrite) < ackWriteEvery {
		r.ackMu.Unlock()
		return
	}
	r.lastAckWrite = now
	r.ackMu.Unlock()
	r.annotate(map[string]string{
		job.AnnAckedSeq:      strconv.FormatInt(a.Seq, 10),
		job.AnnAckedLogLines: strconv.FormatInt(a.LogLines, 10),
	})
}
