// Package shell은 kodeploy-exec 공개 문에 붙는 대화형 세션이다. 프레임은 웹 터미널(web/src/lib/terminalSocket.js)과 같고,
// 인증만 다르다 — 티켓을 Authorization: Ticket 헤더로 싣는다(URL·서브프로토콜에 넣지 않는다). Origin 헤더는 보내지 않는다.
//
// 프레임: 바이너리, 첫 바이트가 채널 번호
//
//	0 stdin, 1 stdout, 2 stderr, 3 종료 상태 JSON(서버 → 클라이언트), 4 창 크기 {"Width","Height"}, 255 채널 닫기
package shell

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	chStdin  byte = 0
	chStdout byte = 1
	chStderr byte = 2
	chStatus byte = 3
	chResize byte = 4
	chClose  byte = 255
)

// Size는 터미널 창 크기다 (칸, 줄).
type Size struct {
	Cols, Rows int
}

type Options struct {
	URL      string // wss://…/exec/<ns>/v1/sessions — core가 티켓과 같이 준 주소
	Ticket   string // 1회용, 30초
	Protocol string // kodeploy.exec.v1
	Stdin    io.Reader
	Stdout   io.Writer
	Stderr   io.Writer
	Size     Size        // 처음 창 크기
	Resize   <-chan Size // 창 크기가 바뀔 때마다 (nil이면 보내지 않는다)
	Dialer   *websocket.Dialer
}

// Result는 세션이 끝난 모양이다. ExitCode는 채널 3 상태의 종료 코드이고, 상태를 못 받았으면 -1이다.
// CloseCode는 서버가 보낸 닫기 코드다 (닫기 프레임 없이 끊겼으면 0).
type Result struct {
	ExitCode  int
	CloseCode int
	CloseText string
}

// DialError는 WebSocket을 열지 못한 것이다. Status는 업그레이드 응답의 HTTP 상태(응답이 없으면 0).
type DialError struct {
	Status int
	Err    error
}

func (e *DialError) Error() string {
	if e.Status != 0 {
		return fmt.Sprintf("exec 연결이 거절됐어요 (HTTP %d)", e.Status)
	}
	return "exec에 연결하지 못했어요: " + e.Err.Error()
}

func (e *DialError) Unwrap() error { return e.Err }

const writeWait = 10 * time.Second

// Run은 세션을 열고 끝날 때까지 stdin을 보내고 stdout·stderr를 쓴다. ctx가 끝나면 닫기 프레임을 보내고 돌아온다.
func Run(ctx context.Context, o Options) (Result, error) {
	d := websocket.Dialer{HandshakeTimeout: 15 * time.Second, Proxy: http.ProxyFromEnvironment}
	if o.Dialer != nil {
		d = *o.Dialer
	}
	d.Subprotocols = []string{o.Protocol}
	header := http.Header{}
	header.Set("Authorization", "Ticket "+o.Ticket)
	conn, resp, err := d.DialContext(ctx, o.URL, header)
	if err != nil {
		status := 0
		if resp != nil {
			status = resp.StatusCode
			resp.Body.Close()
		}
		if ctx.Err() != nil {
			return Result{ExitCode: -1}, ctx.Err()
		}
		return Result{ExitCode: -1}, &DialError{Status: status, Err: err}
	}
	defer conn.Close()

	var wmu sync.Mutex
	send := func(ch byte, p []byte) error {
		frame := make([]byte, 1+len(p))
		frame[0] = ch
		copy(frame[1:], p)
		wmu.Lock()
		defer wmu.Unlock()
		_ = conn.SetWriteDeadline(time.Now().Add(writeWait))
		return conn.WriteMessage(websocket.BinaryMessage, frame)
	}
	resize := func(s Size) error {
		if s.Cols <= 0 || s.Rows <= 0 {
			return nil
		}
		b, _ := json.Marshal(map[string]int{"Width": s.Cols, "Height": s.Rows})
		return send(chResize, b)
	}

	done := make(chan struct{})
	defer close(done)
	_ = resize(o.Size)

	go func() { // stdin → 채널 0. 입력이 끝나면(EOF) stdin 채널을 닫는다
		buf := make([]byte, 32<<10)
		for {
			n, err := o.Stdin.Read(buf)
			if n > 0 {
				if send(chStdin, buf[:n]) != nil {
					return
				}
			}
			if err != nil {
				if errors.Is(err, io.EOF) {
					_ = send(chClose, []byte{chStdin})
				}
				return
			}
		}
	}()
	go func() { // 창 크기 → 채널 4, ctx가 끝나면 닫기
		for {
			select {
			case <-done:
				return
			case s, ok := <-o.Resize:
				if !ok {
					o.Resize = nil
					continue
				}
				_ = resize(s)
			case <-ctx.Done():
				wmu.Lock()
				_ = conn.WriteControl(websocket.CloseMessage,
					websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""), time.Now().Add(time.Second))
				wmu.Unlock()
				_ = conn.Close()
				return
			}
		}
	}()

	res := Result{ExitCode: -1}
	for {
		_, msg, err := conn.ReadMessage()
		if err != nil {
			var ce *websocket.CloseError
			if errors.As(err, &ce) {
				res.CloseCode, res.CloseText = ce.Code, ce.Text
				return res, nil
			}
			if ctx.Err() != nil {
				return res, ctx.Err()
			}
			if res.ExitCode >= 0 {
				return res, nil // 종료 상태는 받았고 닫기 프레임 없이 끊겼다
			}
			return res, fmt.Errorf("exec 연결이 끊겼어요: %w", err)
		}
		if len(msg) == 0 {
			continue
		}
		switch msg[0] {
		case chStdout:
			_, _ = o.Stdout.Write(msg[1:])
		case chStderr:
			_, _ = o.Stderr.Write(msg[1:])
		case chStatus:
			res.ExitCode = ExitCode(msg[1:])
		}
	}
}

// ExitCode는 채널 3 상태(K8s Status 모양)의 종료 코드다. Success면 0, details.causes의 ExitCode면 그 값, 아니면 1.
func ExitCode(payload []byte) int {
	var st struct {
		Status  string `json:"status"`
		Details struct {
			Causes []struct {
				Reason  string `json:"reason"`
				Message string `json:"message"`
			} `json:"causes"`
		} `json:"details"`
	}
	if json.Unmarshal(payload, &st) != nil {
		return 1
	}
	if st.Status == "Success" {
		return 0
	}
	for _, c := range st.Details.Causes {
		if c.Reason == "ExitCode" {
			if n, err := strconv.Atoi(c.Message); err == nil {
				return n
			}
		}
	}
	return 1
}
