// Package callback은 빌더 → core 이벤트 전송이다 (지시서 3-2).
// 빌드마다 큐 하나, 보내는 고루틴 하나. 2xx를 받을 때까지 백오프로 다시 보낸다(최소 한 번 전달).
// 큐에 로그가 너무 쌓이면 오래된 로그 줄부터 버리고 "N줄 생략" 한 줄을 남긴다. log 외 이벤트는 버리지 않는다.
package callback

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

const (
	MaxLinesPerEvent = 200
	MaxQueuedLines   = 5000
)

// OmittedLine은 버린 줄 자리에 남기는 한 줄이다.
func OmittedLine(n int) string { return fmt.Sprintf("... %d줄 생략 ...", n) }

type Sender struct {
	baseURL                string
	secret                 []byte
	hc                     *http.Client
	log                    *slog.Logger
	backoffMin, backoffMax time.Duration
}

func NewSender(coreURL string, secret []byte, log *slog.Logger) *Sender {
	if log == nil {
		log = slog.New(slog.DiscardHandler)
	}
	return &Sender{baseURL: coreURL, secret: secret, hc: &http.Client{Timeout: 15 * time.Second}, log: log,
		backoffMin: time.Second, backoffMax: 30 * time.Second}
}

// Ack는 core가 받은 이벤트다. LogLines는 이 시점까지 큐에 들어온 로그 줄 중 처리가 끝난(보냈거나 버린) 줄 수다.
type Ack struct {
	Seq      int64
	Type     string
	LogLines int64
}

type item struct {
	ev      contract.Event // log가 아니면 이것을 보낸다
	isLog   bool
	lines   []string
	omitted int
	srcEnd  int64 // 이 항목까지 들어온 로그 줄 누계
	sending bool
}

type Queue struct {
	s       *Sender
	buildID string
	onAck   func(Ack)

	mu       sync.Mutex
	items    []*item
	queued   int // 큐의 로그 줄 수
	srcTotal int64
	ackedSrc int64
	nextSeq  int64
	closed   bool
	wake     chan struct{}
	done     chan struct{}
}

type StartOptions struct {
	FirstSeq  int64     // 보통 1. 재개하면 acked-seq 다음부터
	LogOffset int64     // 재개하면 이미 처리한 로그 줄 수 (Ack.LogLines가 이어지게)
	OnAck     func(Ack) // core가 받을 때마다 (보내는 고루틴에서 부른다)
}

// Start는 큐를 만들고 보내는 고루틴을 띄운다. ctx가 끝나면(빌더 종료) 남은 이벤트를 두고 멈춘다.
func (s *Sender) Start(ctx context.Context, buildID string, o StartOptions) *Queue {
	if o.FirstSeq < 1 {
		o.FirstSeq = 1
	}
	q := &Queue{s: s, buildID: buildID, onAck: o.OnAck, nextSeq: o.FirstSeq,
		srcTotal: o.LogOffset, ackedSrc: o.LogOffset,
		wake: make(chan struct{}, 1), done: make(chan struct{})}
	go q.run(ctx)
	return q
}

func (q *Queue) signal() {
	select {
	case q.wake <- struct{}{}:
	default:
	}
}

// Log는 로그 줄을 넣는다. 200줄씩 나눠 이벤트로 만든다.
func (q *Queue) Log(lines []string) {
	if len(lines) == 0 {
		return
	}
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed {
		return
	}
	for len(lines) > 0 {
		n := min(len(lines), MaxLinesPerEvent)
		q.srcTotal += int64(n)
		q.items = append(q.items, &item{isLog: true, lines: append([]string(nil), lines[:n]...), srcEnd: q.srcTotal})
		q.queued += n
		lines = lines[n:]
	}
	q.dropOldLocked()
	q.signal()
}

// dropOldLocked는 큐의 로그가 MaxQueuedLines를 넘으면 보내는 중이 아닌 가장 오래된 로그부터 버린다.
func (q *Queue) dropOldLocked() {
	for i := 0; q.queued > MaxQueuedLines && i < len(q.items); {
		it := q.items[i]
		if !it.isLog || it.sending || len(it.lines) == 0 {
			i++
			continue
		}
		k := min(len(it.lines), q.queued-MaxQueuedLines)
		it.lines = it.lines[k:]
		it.omitted += k
		q.queued -= k
		// 통째로 빈 항목은 바로 뒤 로그 항목에 합친다 (생략 표시가 여러 번 나오지 않게)
		if len(it.lines) == 0 && i+1 < len(q.items) {
			if next := q.items[i+1]; next.isLog && !next.sending {
				next.omitted += it.omitted
				q.items = append(q.items[:i], q.items[i+1:]...)
				continue
			}
		}
		if len(it.lines) == 0 {
			i++
		}
	}
}

// Event는 log가 아닌 이벤트를 넣는다. 버리지 않는다.
func (q *Queue) Event(ev contract.Event) {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed {
		return
	}
	q.items = append(q.items, &item{ev: ev})
	q.signal()
}

// Close는 더 넣지 않는다는 표시다. 고루틴은 남은 것을 다 보낸 뒤 끝난다.
func (q *Queue) Close() {
	q.mu.Lock()
	q.closed = true
	q.mu.Unlock()
	q.signal()
}

// Done은 보내는 고루틴이 끝나면 닫힌다.
func (q *Queue) Done() <-chan struct{} { return q.done }

// next는 보낼 항목을 고르고 seq를 붙인다. 없고 닫혔으면 nil.
func (q *Queue) next(ctx context.Context) (*item, contract.Event) {
	for {
		q.mu.Lock()
		if len(q.items) > 0 {
			it := q.items[0]
			if !it.sending {
				it.sending = true
				if it.isLog {
					lines := it.lines
					if it.omitted > 0 {
						lines = append([]string{OmittedLine(it.omitted)}, lines...)
					}
					it.ev = contract.Event{Type: contract.EventLog, At: time.Now().UTC(), Lines: lines}
				}
				it.ev.Seq = q.nextSeq
				q.nextSeq++
			}
			ev := it.ev
			q.mu.Unlock()
			return it, ev
		}
		if q.closed {
			q.mu.Unlock()
			return nil, contract.Event{}
		}
		q.mu.Unlock()
		select {
		case <-ctx.Done():
			return nil, contract.Event{}
		case <-q.wake:
		}
	}
}

func (q *Queue) run(ctx context.Context) {
	defer close(q.done)
	for {
		it, ev := q.next(ctx)
		if it == nil {
			return
		}
		backoff := q.s.backoffMin
		for {
			err := q.s.post(ctx, q.buildID, ev)
			if err == nil {
				break
			}
			q.s.log.Warn("callback failed, retrying", "build_id", q.buildID, "seq", ev.Seq, "type", ev.Type, "err", err)
			if !sleep(ctx, backoff) {
				return
			}
			backoff = min(backoff*2, q.s.backoffMax)
		}
		q.mu.Lock()
		q.items = q.items[1:]
		if it.isLog {
			q.queued -= len(it.lines)
			q.ackedSrc = it.srcEnd
		}
		ack := Ack{Seq: ev.Seq, Type: ev.Type, LogLines: q.ackedSrc}
		q.mu.Unlock()
		if q.onAck != nil {
			q.onAck(ack)
		}
	}
}

func (s *Sender) post(ctx context.Context, buildID string, ev contract.Event) error {
	body, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		s.baseURL+"/internal/builds/"+url.PathEscape(buildID)+"/events", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	sign.SignRequest(s.secret, req, body, time.Now())
	resp, err := s.hc.Do(req)
	if err != nil {
		return err
	}
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
	resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return fmt.Errorf("core answered %d", resp.StatusCode)
	}
	return nil
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
