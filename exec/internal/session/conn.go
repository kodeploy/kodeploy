package session

import (
	"errors"
	"io"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"

	"github.com/kodeploy/kodeploy/exec/internal/proto"
)

const (
	// 쓰기 한 번이 이보다 오래 막히면 상대가 멈춘 것으로 본다
	writeWait = 10 * time.Second
	// close 프레임을 보낸 뒤 상대의 close를 기다리는 시간. 바로 TCP를 끊으면 읽지 않은 데이터 때문에
	// RST가 나가고, 상대가 마지막 프레임(채널 3, close 코드)을 못 받을 수 있다.
	closeWait = 2 * time.Second
	// 프레임 하나의 상한. core는 업로드를 256KiB씩 보낸다.
	maxMessage = 1<<20 + 1
)

var errClosed = errors.New("connection closed")

// Conn은 클라이언트 WebSocket이다. 쓰기는 하나씩만 나가게 묶는다(gorilla 규칙).
type Conn struct {
	ws  *websocket.Conn
	wmu sync.Mutex

	in, out atomic.Int64
	last    atomic.Int64 // 마지막 입출력 시각 (unix nano). ping은 넣지 않는다
	closed  atomic.Bool
}

// NewConn은 업그레이드된 연결을 감싼다.
func NewConn(ws *websocket.Conn) *Conn {
	ws.SetReadLimit(maxMessage)
	c := &Conn{ws: ws}
	c.touch()
	return c
}

func (c *Conn) touch() { c.last.Store(time.Now().UnixNano()) }

func (c *Conn) idleFor() time.Duration { return time.Since(time.Unix(0, c.last.Load())) }

// Stats는 받은·보낸 바이트(채널 내용 기준)다.
func (c *Conn) Stats() (in, out int64) { return c.in.Load(), c.out.Load() }

// Send는 채널 프레임 하나를 보낸다.
func (c *Conn) Send(ch byte, p []byte) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	if c.closed.Load() {
		return errClosed
	}
	_ = c.ws.SetWriteDeadline(time.Now().Add(writeWait))
	if err := c.ws.WriteMessage(websocket.BinaryMessage, proto.Frame(ch, p)); err != nil {
		return err
	}
	c.out.Add(int64(len(p)))
	c.touch()
	return nil
}

func (c *Conn) ping() error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	if c.closed.Load() {
		return errClosed
	}
	return c.ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(writeWait))
}

// sendClose는 close 프레임만 보낸다. TCP는 상대 응답을 기다린 뒤 끊는다.
func (c *Conn) sendClose(code int, reason string) {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	if c.closed.Swap(true) {
		return
	}
	if len(reason) > 120 { // close reason은 123바이트까지
		reason = reason[:120]
	}
	_ = c.ws.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(code, reason), time.Now().Add(writeWait))
}

// Reject는 세션을 시작하지 않고 close 코드로 끝낸다 (티켓 없음, 다른 ns 등).
// 읽기 루프가 없으므로 여기서 상대의 close를 잠깐 기다린다.
func (c *Conn) Reject(code int, reason string) {
	c.sendClose(code, reason)
	_ = c.ws.SetReadDeadline(time.Now().Add(closeWait))
	for {
		if _, _, err := c.ws.NextReader(); err != nil {
			break
		}
	}
	_ = c.ws.Close()
}

// chanWriter는 원격 stdout·stderr를 채널 프레임으로 보낸다. 바이트는 디코딩하지 않는다.
type chanWriter struct {
	c  *Conn
	ch byte
}

func (w chanWriter) Write(p []byte) (int, error) {
	if err := w.c.Send(w.ch, p); err != nil {
		return 0, err
	}
	return len(p), nil
}

// 읽기 루프가 끝난 이유
type peerGone struct{ err error }

func (e peerGone) Error() string { return "peer gone: " + e.err.Error() }

type violation struct {
	code   int
	reason string
}

func (e violation) Error() string { return e.reason }

type readOpts struct {
	stdin    *io.PipeWriter // nil이면 채널 0은 위반
	tap      io.Writer      // stdin 바이트를 함께 흘려보낼 곳 (SQL 해시)
	maxStdin int64          // 0 = 없음
	sizes    *sizeQueue     // nil이면 채널 4는 무시 (비대화형)
}

// 비정상 종료 때 stdin을 읽던 작업이 받는 오류. EOF가 아니라서 업로드가 끝난 것으로 오해하지 않는다.
var errStdinAborted = errors.New("stdin aborted: connection ended before CLOSE")

// readLoop는 클라이언트 프레임을 읽어 stdin·창 크기로 넘긴다. 연결이 끊기거나 규칙을 어기면 끝난다.
// 그때 stdin이 아직 열려 있으면 오류로 닫아서, stdin을 읽던 작업이 멈춰 있지 않게 한다.
func (c *Conn) readLoop(o readOpts) (err error) {
	var total int64
	stdinOpen := o.stdin != nil
	defer func() {
		if stdinOpen {
			_ = o.stdin.CloseWithError(errStdinAborted)
		}
	}()
	for {
		mt, msg, err := c.ws.ReadMessage()
		if err != nil {
			return peerGone{err}
		}
		c.touch()
		if mt != websocket.BinaryMessage {
			return violation{proto.CloseUnsupportedData, "text frames are not accepted"}
		}
		ch, p, err := proto.Parse(msg)
		if err != nil {
			return violation{proto.CloseBadInput, "empty frame"}
		}
		c.in.Add(int64(len(p)))
		switch ch {
		case proto.ChStdin:
			if o.stdin == nil {
				return violation{proto.CloseBadInput, "this session has no stdin"}
			}
			if !stdinOpen {
				continue // CLOSE 뒤에 온 입력은 버린다
			}
			total += int64(len(p))
			if o.maxStdin > 0 && total > o.maxStdin {
				return violation{proto.CloseBadInput, "stdin limit exceeded"}
			}
			if o.tap != nil {
				_, _ = o.tap.Write(p)
			}
			if _, err := o.stdin.Write(p); err != nil {
				stdinOpen = false // 원격이 더 읽지 않는다 (작업이 끝남)
			}
		case proto.ChResize:
			if o.sizes == nil {
				continue
			}
			s, err := proto.ParseSize(p)
			if err != nil {
				return violation{proto.CloseBadInput, "bad terminal size"}
			}
			o.sizes.push(s.Width, s.Height)
		case proto.ChClose:
			if len(p) == 1 && p[0] == proto.ChStdin && stdinOpen {
				_ = o.stdin.Close()
				stdinOpen = false
			}
		default:
			return violation{proto.CloseBadInput, "unknown channel"}
		}
	}
}
