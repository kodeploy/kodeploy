package wait

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/kodeploy/kodeploy/cli/internal/api"
	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

const (
	MinInterval = 2 * time.Second  // 상태가 바뀌면 이 간격으로 되돌린다
	MaxInterval = 10 * time.Second // 바뀌지 않으면 두 배씩 늘려 여기까지
	// 같은 빌드의 상태 조회가 이만큼 연속으로 실패(5xx·네트워크)하면 멈춘다
	MaxConsecutiveFailures = 10
	// 실패한 빌드의 AI 진단(ai_status=pending)을 더 기다리는 최대 시간
	AnalysisWait     = 30 * time.Second
	analysisInterval = 3 * time.Second
)

// Getter는 빌드 하나의 상태를 받는다 (api.Client.GetBuild).
type Getter func(ctx context.Context, buildID string) (*api.Build, error)

// Build는 기다리는 빌드 하나다.
type Build struct {
	Ref     api.BuildRef
	Status  string
	History []string      // 지나온 상태. 처음은 재배포 응답의 status
	Last    *api.Build    // 마지막으로 받은 상세 (아직 못 받았으면 nil)
	Elapsed time.Duration // running·failed·cancelled가 된 때 (요청부터). 진행 중이면 0
	fails   int
}

func (b *Build) Terminal() bool {
	return b.Status == api.StatusRunning || b.Status == api.StatusFailed || b.Status == api.StatusCancelled
}

type Kind int

const (
	KindSuccess Kind = iota
	KindFailed
	KindSuperseded
	KindTimeout
	KindInterrupted
	KindError
)

type Outcome struct {
	Kind   Kind
	Builds []*Build
	Err    error // KindError일 때 이유
}

type Options struct {
	Clock    clock.Clock
	Get      Getter
	Start    time.Time     // 재배포를 요청한 시각 (경과 시간의 기준)
	Deadline time.Time     // --timeout. 요청부터 안정화까지 전체
	Settle   time.Duration // 모두 running이 된 뒤 더 지켜볼 시간 (0이면 바로 성공)
	OnChange func(b *Build)
	OnSettle func(d time.Duration)
}

// ctxOutcome은 ctx가 끝난 이유로 결과를 정한다. 시간 제한이면 Timeout, 그 밖(신호)이면 Interrupted.
func ctxOutcome(ctx context.Context, builds []*Build) Outcome {
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		return Outcome{Kind: KindTimeout, Builds: builds}
	}
	return Outcome{Kind: KindInterrupted, Builds: builds}
}

func statuses(builds []*Build) []string {
	out := make([]string, len(builds))
	for i, b := range builds {
		out[i] = b.Status
	}
	return out
}

// Run은 빌드들을 판정이 날 때까지 조회한다.
//   - 간격은 2초에서 시작해 바뀌지 않으면 두 배씩 10초까지 늘리고, 어느 빌드든 상태가 바뀌면 2초로 되돌린다.
//   - 5xx·네트워크 오류는 다음 차례에 다시 조회하고, 같은 빌드가 연속 10번 실패하면 KindError다.
//   - 모두 running이 되면 Settle 동안 계속 조회한다. 서버는 running 뒤 60초 동안 앱 Pod을 더 보고, 재시작·OOM이
//     보이면 같은 빌드를 failed로 바꾼다. 그 사이 failed가 되면 KindFailed다.
//   - Deadline을 넘기면 KindTimeout, ctx가 끝나면(신호) KindInterrupted. 서버 빌드는 건드리지 않는다.
func Run(ctx context.Context, o Options, refs []api.BuildRef) Outcome {
	builds := make([]*Build, len(refs))
	for i, r := range refs {
		builds[i] = &Build{Ref: r, Status: r.Status, History: []string{r.Status}}
	}
	interval := MinInterval
	var settleFrom time.Time
	for {
		if ctx.Err() != nil {
			return ctxOutcome(ctx, builds)
		}
		if !o.Clock.Now().Before(o.Deadline) {
			return Outcome{Kind: KindTimeout, Builds: builds}
		}
		changed := false
		for _, b := range builds {
			if b.Status == api.StatusFailed || b.Status == api.StatusCancelled {
				continue
			}
			got, err := o.Get(ctx, b.Ref.BuildID)
			if err != nil {
				if ctx.Err() != nil {
					return ctxOutcome(ctx, builds)
				}
				if api.Retryable(err) {
					b.fails++
					if b.fails >= MaxConsecutiveFailures {
						return Outcome{Kind: KindError, Builds: builds,
							Err: fmt.Errorf("빌드 %s 상태 조회가 연속 %d번 실패했어요: %w", b.Ref.BuildID, b.fails, err)}
					}
					continue
				}
				return Outcome{Kind: KindError, Builds: builds, Err: err}
			}
			b.fails = 0
			b.Last = got
			if got.Status != b.Status {
				b.Status = got.Status
				b.History = append(b.History, got.Status)
				if b.Terminal() {
					b.Elapsed = o.Clock.Now().Sub(o.Start)
				}
				changed = true
				if o.OnChange != nil {
					o.OnChange(b)
				}
			}
		}

		now := o.Clock.Now()
		switch Judge(statuses(builds)) {
		case Failed:
			return Outcome{Kind: KindFailed, Builds: builds}
		case Superseded:
			return Outcome{Kind: KindSuperseded, Builds: builds}
		case Success:
			if o.Settle <= 0 {
				return Outcome{Kind: KindSuccess, Builds: builds}
			}
			if settleFrom.IsZero() {
				settleFrom = now
				if o.OnSettle != nil {
					o.OnSettle(o.Settle)
				}
			}
			if now.Sub(settleFrom) >= o.Settle {
				return Outcome{Kind: KindSuccess, Builds: builds}
			}
		default:
			settleFrom = time.Time{}
		}

		if changed {
			interval = MinInterval
		}
		sleep := interval
		if !settleFrom.IsZero() {
			if left := o.Settle - now.Sub(settleFrom); left < sleep {
				sleep = left
			}
		}
		if left := o.Deadline.Sub(now); left < sleep {
			sleep = left
		}
		if err := o.Clock.Sleep(ctx, sleep); err != nil {
			return ctxOutcome(ctx, builds)
		}
		interval = min(interval*2, MaxInterval)
	}
}

// WaitAnalysis는 실패한 빌드의 AI 진단이 만들어지는 중(ai_status=pending)이면 최대 30초(시간 제한 안에서) 더 조회한다.
// 진단은 보조 정보라 조회 오류는 그냥 넘긴다.
func WaitAnalysis(ctx context.Context, o Options, builds []*Build) {
	until := o.Clock.Now().Add(AnalysisWait)
	if o.Deadline.Before(until) {
		until = o.Deadline
	}
	for {
		pending := false
		for _, b := range builds {
			if b.Status != api.StatusFailed || b.Last == nil || api.Str(b.Last.AIStatus) != "pending" {
				continue
			}
			pending = true
			if got, err := o.Get(ctx, b.Ref.BuildID); err == nil {
				b.Last = got
			}
		}
		if !pending || ctx.Err() != nil {
			return
		}
		left := until.Sub(o.Clock.Now())
		if left <= 0 {
			return
		}
		if err := o.Clock.Sleep(ctx, min(analysisInterval, left)); err != nil {
			return
		}
	}
}
