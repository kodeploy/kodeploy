// Package session은 클라이언트 프레임과 원격 exec 스트림을 잇는다 (지시서 4-4).
//
//   - 채널 0 → stdin. CLOSE(255, 0)를 받으면 stdin을 닫는다.
//   - stdout·stderr → 채널 1·2 (tty면 K8s가 stdout으로 합친다).
//   - 채널 4 → TerminalSizeQueue.
//   - 끝나면 채널 3으로 종료 상태를 보내고 1000으로 닫는다. 바이트는 디코딩하지 않는다.
//
// 제한: 유휴 타임아웃(tty만), 최대 실행 시간, 종료 신호(tty만 4503). 동시 세션 수는 api가 센다.
package session

import (
	"context"
	"errors"
	"io"
	"sync"
	"time"

	"k8s.io/client-go/tools/remotecommand"

	"github.com/kodeploy/kodeploy/exec/internal/proto"
)

// JobIO는 작업이 쓰는 스트림이다. 없는 것은 nil이다(인터페이스 자체가 nil).
type JobIO struct {
	Stdin  io.Reader
	Stdout io.Writer
	Stderr io.Writer
	Size   remotecommand.TerminalSizeQueue
}

// Outcome은 작업 결과다. Status는 채널 3 내용, ExitCode는 감사 기록용(-1 = 없음).
type Outcome struct {
	Status   []byte
	ExitCode int
}

// Job은 세션 하나에서 할 일이다. 일반 작업은 ExecJob, 복원은 restore 패키지가 만든다.
// 명령이 끝까지 돌았으면(exit code가 0이 아니어도) error 없이 Outcome을 돌려준다.
type Job func(ctx context.Context, jio JobIO) (Outcome, error)

// Options는 세션 하나의 설정이다.
type Options struct {
	TTY          bool
	Stdin        bool
	StdinTap     io.Writer // stdin 바이트 사본 (감사 기록의 SQL 길이·해시)
	MaxStdin     int64     // 0 = 없음
	Cols, Rows   uint16    // tty 첫 창 크기
	IdleTimeout  time.Duration
	MaxDuration  time.Duration
	PingInterval time.Duration
	Shutdown     <-chan struct{} // 닫히면 tty 세션은 4503으로 끝낸다. 비대화형은 끝까지 간다
}

// Result는 세션이 어떻게 끝났는지다 (감사 기록용).
type Result struct {
	CloseCode int
	ExitCode  int
	BytesIn   int64
	BytesOut  int64
	Err       error
}

// 상대 쪽 비정상 종료 (RFC 6455 1006: 실제로 보내지는 코드가 아니라 기록용)
const closeAbnormal = 1006

var (
	errMaxDuration = errors.New("max duration exceeded")
	errIdle        = errors.New("idle timeout")
	errShutdown    = errors.New("shutting down")
)

// Serve는 세션 하나를 끝까지 돌린다. 돌아올 때 연결은 닫혀 있다.
func Serve(parent context.Context, c *Conn, o Options, job Job) Result {
	ctx, cancel := context.WithCancelCause(parent)
	defer cancel(nil)
	if o.MaxDuration > 0 {
		var stop context.CancelFunc
		ctx, stop = context.WithTimeoutCause(ctx, o.MaxDuration, errMaxDuration)
		defer stop()
	}

	jio := JobIO{Stdout: chanWriter{c, proto.ChStdout}}
	ro := readOpts{tap: o.StdinTap, maxStdin: o.MaxStdin}
	var pr *io.PipeReader
	if o.Stdin {
		var pw *io.PipeWriter
		pr, pw = io.Pipe()
		jio.Stdin, ro.stdin = pr, pw
	}
	if o.TTY {
		q := newSizeQueue(ctx.Done())
		if o.Cols > 0 && o.Rows > 0 {
			q.push(o.Cols, o.Rows)
		}
		jio.Size, ro.sizes = q, q
	} else {
		jio.Stderr = chanWriter{c, proto.ChStderr}
	}

	readDone := make(chan error, 1)
	go func() { readDone <- c.readLoop(ro) }()

	var wg sync.WaitGroup
	bg := func(f func()) { wg.Add(1); go func() { defer wg.Done(); f() }() }
	if o.PingInterval > 0 {
		bg(func() { keepalive(ctx, c, o.PingInterval, cancel) })
	}
	if o.TTY && o.IdleTimeout > 0 {
		bg(func() { watchIdle(ctx, c, o.IdleTimeout, cancel) })
	}
	if o.TTY && o.Shutdown != nil {
		bg(func() {
			select {
			case <-o.Shutdown:
				cancel(errShutdown)
			case <-ctx.Done():
			}
		})
	}

	type jobResult struct {
		out Outcome
		err error
	}
	jobDone := make(chan jobResult, 1)
	go func() {
		out, err := job(ctx, jio)
		jobDone <- jobResult{out, err}
	}()

	var (
		jr       jobResult
		readErr  error
		readOver bool
	)
	select {
	case jr = <-jobDone:
	case readErr = <-readDone:
		readOver = true
		cancel(readErr) // 클라이언트가 끊었거나 규칙을 어겼다
		jr = <-jobDone
	}
	cause := context.Cause(ctx)
	if pr != nil {
		// 막혀 있을 수 있는 stdin 쓰기를 풀어 준다 (작업은 이미 끝났다)
		pr.CloseWithError(io.ErrClosedPipe)
	}
	cancel(nil)
	wg.Wait()

	res := Result{ExitCode: jr.out.ExitCode, Err: jr.err}
	var v violation
	switch {
	case errors.As(readErr, new(peerGone)) || errors.As(cause, new(peerGone)):
		res.CloseCode = closeAbnormal
	case errors.As(readErr, &v):
		res.CloseCode = v.code // 규칙 위반은 작업 결과보다 앞선다
	case jr.err == nil:
		// 작업이 결과를 냈다. 시간 초과 등으로 끊겨서 낸 결과(복원의 실패 결과)면 그 코드로 닫는다
		if jr.out.Status != nil {
			_ = c.Send(proto.ChStatus, jr.out.Status)
		}
		res.CloseCode = proto.CloseNormal
		if code := causeCode(cause); code != 0 {
			res.CloseCode = code
		}
	case errors.As(cause, &v):
		res.CloseCode = v.code
	case errors.Is(cause, errMaxDuration):
		res.CloseCode = proto.CloseMaxDuration
	case errors.Is(cause, errIdle):
		res.CloseCode = proto.CloseIdle
	case errors.Is(cause, errShutdown):
		res.CloseCode = proto.CloseShutdown
	default:
		_ = c.Send(proto.ChStatus, proto.MarshalStatus(proto.ErrorStatus(trim("exec failed: "+jr.err.Error(), 300))))
		res.CloseCode = proto.CloseInternal
	}
	if res.CloseCode != closeAbnormal {
		c.sendClose(res.CloseCode, closeReason(res.CloseCode))
	}
	if !readOver {
		select {
		case <-readDone:
			readOver = true
		case <-time.After(closeWait):
		}
	}
	_ = c.ws.Close()
	if !readOver {
		<-readDone
	}
	res.BytesIn, res.BytesOut = c.Stats()
	return res
}

func causeCode(cause error) int {
	switch {
	case errors.Is(cause, errMaxDuration):
		return proto.CloseMaxDuration
	case errors.Is(cause, errIdle):
		return proto.CloseIdle
	case errors.Is(cause, errShutdown):
		return proto.CloseShutdown
	}
	return 0
}

func closeReason(code int) string {
	switch code {
	case proto.CloseNormal:
		return ""
	case proto.CloseUnsupportedData:
		return "text frames are not accepted"
	case proto.CloseBadInput:
		return "bad input"
	case proto.CloseIdle:
		return "idle timeout"
	case proto.CloseMaxDuration:
		return "max duration exceeded"
	case proto.CloseShutdown:
		return "server shutting down"
	case proto.CloseInternal:
		return "exec failed"
	}
	return ""
}

func trim(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

// keepalive는 프록시(Cloudflare·Envoy) 유휴 타임아웃보다 짧게 ping을 보낸다. 실패하면 상대가 사라진 것이다.
func keepalive(ctx context.Context, c *Conn, every time.Duration, cancel context.CancelCauseFunc) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if err := c.ping(); err != nil {
				cancel(peerGone{err})
				return
			}
		}
	}
}

// watchIdle은 입출력이 idle 동안 없으면 세션을 끝낸다 (tty만).
func watchIdle(ctx context.Context, c *Conn, idle time.Duration, cancel context.CancelCauseFunc) {
	step := idle / 4
	if step > time.Second {
		step = time.Second
	}
	t := time.NewTicker(step)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			if c.idleFor() >= idle {
				cancel(errIdle)
				return
			}
		}
	}
}

// sizeQueue는 채널 4 → remotecommand.TerminalSizeQueue다. 밀리면 오래된 것을 버린다.
type sizeQueue struct {
	ch   chan remotecommand.TerminalSize
	done <-chan struct{}
}

func newSizeQueue(done <-chan struct{}) *sizeQueue {
	return &sizeQueue{ch: make(chan remotecommand.TerminalSize, 4), done: done}
}

func (q *sizeQueue) push(w, h uint16) {
	s := remotecommand.TerminalSize{Width: w, Height: h}
	for {
		select {
		case q.ch <- s:
			return
		default:
		}
		select {
		case <-q.ch:
		default:
		}
	}
}

// Next는 다음 창 크기다. 세션이 끝나면 nil.
func (q *sizeQueue) Next() *remotecommand.TerminalSize {
	select {
	case s := <-q.ch:
		return &s
	case <-q.done:
		return nil
	}
}
