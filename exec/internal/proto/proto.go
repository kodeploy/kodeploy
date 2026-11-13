// Package proto는 kodeploy-exec WebSocket 프레임 규칙이다.
// 바이너리 프레임의 첫 바이트가 채널 번호다. 텍스트 프레임은 받지 않는다.
// 채널 번호는 K8s exec(v5.channel.k8s.io)와 같아서 바이트를 디코딩하지 않고 그대로 넘긴다.
package proto

import (
	"encoding/json"
	"errors"
	"strconv"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// 서브프로토콜. 협상되지 않으면 연결을 거부한다.
const (
	Subprotocol    = "kodeploy.exec.v1"
	TicketPrefix   = "ticket." // 브라우저: Sec-WebSocket-Protocol의 두 번째 값
	TicketAuthType = "Ticket"  // CLI: Authorization: Ticket <티켓>
)

// 채널 번호
const (
	ChStdin  byte = 0
	ChStdout byte = 1
	ChStderr byte = 2
	ChStatus byte = 3   // 종료 상태 JSON (서버 → 클라이언트)
	ChResize byte = 4   // 창 크기 JSON (클라이언트 → 서버)
	ChClose  byte = 255 // 두 번째 바이트의 채널을 닫는다 (보통 0 = stdin)
)

// WebSocket close 코드. 1000·1003·1011은 RFC 6455 표준 코드, 4xxx는 이 서비스가 정한 코드다.
const (
	CloseNormal          = 1000
	CloseUnsupportedData = 1003 // 텍스트 프레임 (RFC 6455 표준 코드)
	CloseInternal        = 1011 // exec를 시작하지 못함 등 서버 쪽 오류 (RFC 6455 표준 코드)
	CloseBadInput        = 4400 // 입력 상한 초과, 이 작업에 없는 채널, 잘못된 프레임
	CloseTicket          = 4401 // 티켓 없음·만료·이미 사용
	CloseForbidden       = 4403 // 이 문에서 허용하지 않는 작업, 또는 다른 ns
	CloseNoTarget        = 4404 // 대상 Pod 없음
	CloseIdle            = 4408 // 유휴 타임아웃 (tty만)
	CloseMaxDuration     = 4413 // 최대 실행 시간 초과
	CloseTooMany         = 4429 // 동시 세션 수 초과
	CloseShutdown        = 4503 // 종료 중 (SIGTERM)
)

var ErrEmptyFrame = errors.New("empty frame")

// Frame은 채널 번호를 붙인 새 슬라이스를 만든다.
func Frame(ch byte, p []byte) []byte {
	b := make([]byte, 1+len(p))
	b[0] = ch
	copy(b[1:], p)
	return b
}

// Parse는 프레임을 채널과 내용으로 나눈다. 내용은 msg를 그대로 가리킨다.
func Parse(msg []byte) (byte, []byte, error) {
	if len(msg) == 0 {
		return 0, nil, ErrEmptyFrame
	}
	return msg[0], msg[1:], nil
}

// CloseFrame은 ch를 닫는 CLOSE 프레임이다.
func CloseFrame(ch byte) []byte { return []byte{ChClose, ch} }

// Size는 채널 4의 창 크기다. 필드 이름은 K8s TerminalSize와 같다.
type Size struct {
	Width  uint16 `json:"Width"`
	Height uint16 `json:"Height"`
}

// 창 크기 상한. 터무니없는 값으로 원격 pty를 흔들지 않게 한다.
const maxDim = 1000

var ErrBadSize = errors.New("bad terminal size")

// ParseSize는 채널 4 내용을 읽는다. 0이거나 상한을 넘으면 오류.
func ParseSize(p []byte) (Size, error) {
	var s Size
	if err := json.Unmarshal(p, &s); err != nil {
		return Size{}, ErrBadSize
	}
	if s.Width == 0 || s.Height == 0 || s.Width > maxDim || s.Height > maxDim {
		return Size{}, ErrBadSize
	}
	return s, nil
}

// EncodeSize는 클라이언트(testclient, core)가 보낼 채널 4 내용을 만든다.
func EncodeSize(w, h uint16) []byte {
	b, _ := json.Marshal(Size{Width: w, Height: h})
	return b
}

// ExitStatus는 K8s exec가 채널 3에 보내는 것과 같은 모양의 Status다.
// core의 dbquery._parse_exit가 이 모양(details.causes[reason=ExitCode])을 그대로 읽는다.
func ExitStatus(code int) metav1.Status {
	if code == 0 {
		return metav1.Status{Status: metav1.StatusSuccess}
	}
	return metav1.Status{
		Status:  metav1.StatusFailure,
		Reason:  "NonZeroExitCode",
		Message: "command terminated with non-zero exit code: exit status " + strconv.Itoa(code),
		Details: &metav1.StatusDetails{Causes: []metav1.StatusCause{{
			Type:    "ExitCode",
			Message: strconv.Itoa(code),
		}}},
	}
}

// ErrorStatus는 명령이 끝나지 못한 경우의 Status다. message에 비밀값을 넣지 않는다.
func ErrorStatus(message string) metav1.Status {
	return metav1.Status{
		Status:  metav1.StatusFailure,
		Reason:  metav1.StatusReasonInternalError,
		Message: message,
	}
}

// MarshalStatus는 Status를 채널 3 내용으로 만든다.
func MarshalStatus(s metav1.Status) []byte {
	b, _ := json.Marshal(s)
	return b
}

// StatusExitCode는 채널 3 Status에서 exit code를 꺼낸다 (core _parse_exit와 같은 규칙).
func StatusExitCode(p []byte) int {
	var s metav1.Status
	if err := json.Unmarshal(p, &s); err != nil {
		return 0
	}
	if s.Status == metav1.StatusSuccess {
		return 0
	}
	if s.Details != nil {
		for _, c := range s.Details.Causes {
			if c.Type == "ExitCode" {
				if n, err := strconv.Atoi(c.Message); err == nil {
					return n
				}
				return 1
			}
		}
	}
	return 1
}
