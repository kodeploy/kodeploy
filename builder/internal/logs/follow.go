// Package logs는 빌드 Pod의 init(clone) → main(buildkit) 로그를 follow 스트림으로 읽어 새 줄만 흘려보낸다.
// 원본(_tail_build_logs)은 1초마다 전체 로그를 다시 읽었다. 출력 형식은 원본 _combined_job_logs와 같게
// 컨테이너마다 머리줄("=== clone (init) ===", "=== buildkit (main) ===")을 붙이고 사이에 빈 줄을 둔다.
package logs

import (
	"bufio"
	"context"
	"errors"
	"io"
	"strings"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/job"
)

// 원본 _combined_job_logs의 머리줄 (dockerfile 모드: init 이름이 clone)
const (
	InitHeader = "=== clone (init) ==="
	MainHeader = "=== buildkit (main) ==="
)

// 한 줄 상한. 넘는 부분은 잘라 버린다 (npm 진행 막대 같은 긴 줄이 메모리를 잡아먹지 않게).
const maxLineBytes = 64 * 1024

// 컨테이너가 끝났는데도 로그를 못 여는 경우 몇 번까지 다시 시도하나
const maxAttemptsAfterExit = 5

var ErrPodGone = errors.New("build pod is gone")

// State는 컨테이너 하나의 상태다.
type State struct {
	Started    bool // running 또는 terminated
	Terminated bool
	PodDone    bool // Pod phase가 Succeeded/Failed
	Gone       bool // Pod이 없다
}

// Source는 로그를 여는 쪽이다. 실제는 KubeSource, 테스트는 가짜를 쓴다.
type Source interface {
	WaitPod(ctx context.Context, buildID string) (string, error)
	// Open은 follow + timestamps로 연다. since가 0이 아니면 그 시각 이후만.
	Open(ctx context.Context, pod, container string, since time.Time) (io.ReadCloser, error)
	State(ctx context.Context, pod, container string) (State, error)
}

type Follower struct {
	src                    Source
	backoffMin, backoffMax time.Duration
}

func NewFollower(src Source) *Follower {
	return &Follower{src: src, backoffMin: 500 * time.Millisecond, backoffMax: 10 * time.Second}
}

// Follow는 빌드 Pod 로그를 줄 단위로 out에 보낸다. 두 컨테이너가 끝나면 nil로 돌아온다.
// out은 닫지 않는다 (호출부가 닫는다).
func (f *Follower) Follow(ctx context.Context, buildID string, out chan<- string) error {
	pod, err := f.src.WaitPod(ctx, buildID)
	if err != nil {
		return err
	}
	send := func(l string) bool {
		select {
		case out <- l:
			return true
		case <-ctx.Done():
			return false
		}
	}

	initLines := 0
	err = f.container(ctx, pod, job.InitContainer, func(l string) bool {
		if initLines == 0 && !send(InitHeader) {
			return false
		}
		initLines++
		return send(l)
	})
	if err != nil {
		return err
	}

	mainLines := 0
	return f.container(ctx, pod, job.MainContainer, func(l string) bool {
		if mainLines == 0 {
			if initLines > 0 && !send("") {
				return false
			}
			if !send(MainHeader) {
				return false
			}
		}
		mainLines++
		return send(l)
	})
}

// container는 한 컨테이너 로그를 끝까지 읽는다. 스트림이 끊기면 마지막 타임스탬프부터 다시 열고,
// 이미 보낸 줄(타임스탬프가 그 이하)은 건너뛴다.
func (f *Follower) container(ctx context.Context, pod, name string, emit func(string) bool) error {
	var last time.Time
	backoff := f.backoffMin
	failsAfterExit := 0
	for {
		rc, err := f.src.Open(ctx, pod, name, last)
		n := 0
		if err == nil {
			last, n, err = readLines(rc, last, emit)
			rc.Close()
		}
		if ctx.Err() != nil {
			return context.Cause(ctx)
		}
		if n > 0 {
			backoff = f.backoffMin
		}

		st, serr := f.src.State(ctx, pod, name)
		if serr == nil {
			switch {
			case st.Gone:
				return ErrPodGone
			case st.Terminated && err == nil:
				return nil // 끝난 컨테이너의 스트림을 끝까지 읽었다
			case !st.Started && st.PodDone:
				return nil // 한 번도 안 돌았다 (예: clone 실패로 main이 안 뜸)
			case st.Terminated:
				if failsAfterExit++; failsAfterExit >= maxAttemptsAfterExit {
					return err
				}
			}
		}
		if !sleep(ctx, backoff) {
			return context.Cause(ctx)
		}
		backoff = min(backoff*2, f.backoffMax)
	}
}

// readLines는 "<RFC3339Nano> <text>" 줄을 읽어 text를 emit한다. after 이하 타임스탬프는 건너뛴다.
// emit이 false를 주면(ctx 종료) 멈춘다.
func readLines(r io.Reader, after time.Time, emit func(string) bool) (time.Time, int, error) {
	br := bufio.NewReaderSize(r, 32*1024)
	last := after
	n := 0
	for {
		line, err := readLine(br)
		if line != "" || err == nil {
			ts, text := splitTimestamp(line)
			skip := !ts.IsZero() && !after.IsZero() && !ts.After(after)
			if !ts.IsZero() && ts.After(last) {
				last = ts
			}
			if !skip {
				if !emit(text) {
					return last, n, context.Canceled
				}
				n++
			}
		}
		if err == io.EOF {
			return last, n, nil
		}
		if err != nil {
			return last, n, err
		}
	}
}

// readLine은 줄 하나를 읽는다. maxLineBytes를 넘는 부분은 버리고 표시를 붙인다.
func readLine(br *bufio.Reader) (string, error) {
	var buf []byte
	truncated := false
	for {
		frag, err := br.ReadSlice('\n')
		if room := maxLineBytes - len(buf); room > 0 {
			buf = append(buf, frag[:min(len(frag), room)]...)
		}
		if len(buf) >= maxLineBytes && len(frag) > 0 && frag[len(frag)-1] != '\n' {
			truncated = true
		}
		if errors.Is(err, bufio.ErrBufferFull) {
			continue
		}
		s := strings.TrimRight(string(buf), "\r\n")
		if truncated {
			s += " [truncated]"
		}
		return s, err
	}
}

func splitTimestamp(line string) (time.Time, string) {
	i := strings.IndexByte(line, ' ')
	if i <= 0 {
		return time.Time{}, line
	}
	ts, err := time.Parse(time.RFC3339Nano, line[:i])
	if err != nil {
		return time.Time{}, line
	}
	return ts, line[i+1:]
}

func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
