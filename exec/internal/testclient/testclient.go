// Package testclient는 kodeploy-exec E2E용 Go 클라이언트다(hack/exec-e2e.md). 나중에 CLI의 바탕이 된다.
//
//   - Internal: 내부 문(8081) — HMAC 서명(builder 규칙), 티켓 발급, query·browse·dump·restore 세션
//   - DialPublic: 공개 문(8080) — 티켓을 서브프로토콜(브라우저와 같음) 또는 Authorization 헤더로
//
// 운영 클러스터에서 내부 문에 붙을 때는 kubectl port-forward를 쓴다(Pod 네트워크 정책을 거치지 않는다).
package testclient

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/gorilla/websocket"

	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/sign"
)

const headerRequestID = "X-Kodeploy-Request-Id"

// Internal은 내부 문 클라이언트다. Base는 http://host:port.
type Internal struct {
	Base string
	Key  []byte
}

// TicketRequest는 POST /internal/v1/tickets 본문이다.
type TicketRequest struct {
	UserID string `json:"user_id"`
	AppID  string `json:"app_id"`
	App    string `json:"app"`
	Target string `json:"target"`
	Op     string `json:"op"`
	Cols   uint16 `json:"cols"`
	Rows   uint16 `json:"rows"`
}

type TicketResponse struct {
	Ticket    string `json:"ticket"`
	ExpiresAt string `json:"expires_at"`
}

// NewRequestID는 X-Kodeploy-Request-Id 값이다.
func NewRequestID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// Ticket은 공개 문용 티켓을 받는다.
func (c *Internal) Ticket(ctx context.Context, req TicketRequest) (TicketResponse, error) {
	body, _ := json.Marshal(req)
	r, err := http.NewRequestWithContext(ctx, "POST", strings.TrimRight(c.Base, "/")+"/internal/v1/tickets", bytes.NewReader(body))
	if err != nil {
		return TicketResponse{}, err
	}
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set(headerRequestID, NewRequestID())
	sign.SignRequest(c.Key, r, body, time.Now())
	resp, err := http.DefaultClient.Do(r)
	if err != nil {
		return TicketResponse{}, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusCreated {
		return TicketResponse{}, fmt.Errorf("ticket: %d %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	var out TicketResponse
	return out, json.Unmarshal(b, &out)
}

// Dial은 서명한 내부 세션을 연다. path 예: /internal/v1/sessions/query
func (c *Internal) Dial(ctx context.Context, path string) (*Conn, error) {
	u, err := url.Parse(strings.TrimRight(c.Base, "/") + path)
	if err != nil {
		return nil, err
	}
	ts := strconv.FormatInt(time.Now().Unix(), 10)
	h := http.Header{}
	h.Set(sign.HeaderTimestamp, ts)
	h.Set(sign.HeaderSignature, sign.Compute(c.Key, ts, "GET", u.EscapedPath(), nil))
	h.Set(headerRequestID, NewRequestID())
	u.Scheme = strings.Replace(u.Scheme, "http", "ws", 1)
	d := websocket.Dialer{Subprotocols: []string{proto.Subprotocol}, HandshakeTimeout: 10 * time.Second}
	ws, resp, err := d.DialContext(ctx, u.String(), h)
	if err != nil {
		return nil, handshakeErr(err, resp)
	}
	return &Conn{ws: ws}, nil
}

// RestorePath는 restore 세션 경로다 (기대값이 경로에 들어간다).
func RestorePath(size int64, sha256 string) string {
	return fmt.Sprintf("/internal/v1/sessions/restore/%d/%s", size, sha256)
}

// DialPublic은 공개 문에 붙는다. viaHeader면 Authorization: Ticket, 아니면 서브프로토콜 ticket.<t>.
// ticket이 비어 있으면 티켓 없이 붙는다(4401 확인용). Origin 헤더는 보내지 않는다(CLI).
func DialPublic(ctx context.Context, wsURL, ticket string, viaHeader bool) (*Conn, error) {
	return DialPublicOrigin(ctx, wsURL, ticket, viaHeader, "")
}

// DialPublicOrigin은 브라우저처럼 Origin 헤더를 붙여 공개 문에 붙는다 (origin이 비면 DialPublic과 같다).
func DialPublicOrigin(ctx context.Context, wsURL, ticket string, viaHeader bool, origin string) (*Conn, error) {
	protocols := []string{proto.Subprotocol}
	h := http.Header{}
	if origin != "" {
		h.Set("Origin", origin)
	}
	switch {
	case ticket == "":
	case viaHeader:
		h.Set("Authorization", proto.TicketAuthType+" "+ticket)
	default:
		protocols = append(protocols, proto.TicketPrefix+ticket)
	}
	d := websocket.Dialer{Subprotocols: protocols, HandshakeTimeout: 10 * time.Second}
	ws, resp, err := d.DialContext(ctx, wsURL, h)
	if err != nil {
		return nil, handshakeErr(err, resp)
	}
	if ws.Subprotocol() != proto.Subprotocol {
		ws.Close()
		return nil, fmt.Errorf("subprotocol not negotiated: %q", ws.Subprotocol())
	}
	return &Conn{ws: ws}, nil
}

func handshakeErr(err error, resp *http.Response) error {
	if resp != nil {
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return fmt.Errorf("handshake: %d %s", resp.StatusCode, strings.TrimSpace(string(b)))
	}
	return err
}

// Conn은 열린 세션이다.
type Conn struct {
	ws *websocket.Conn
}

func (c *Conn) send(ch byte, p []byte) error {
	return c.ws.WriteMessage(websocket.BinaryMessage, proto.Frame(ch, p))
}

// Stdin은 채널 0으로 보낸다.
func (c *Conn) Stdin(p []byte) error { return c.send(proto.ChStdin, p) }

// CloseStdin은 CLOSE(255, 0)이다.
func (c *Conn) CloseStdin() error {
	return c.ws.WriteMessage(websocket.BinaryMessage, proto.CloseFrame(proto.ChStdin))
}

// Resize는 채널 4다.
func (c *Conn) Resize(w, h uint16) error { return c.send(proto.ChResize, proto.EncodeSize(w, h)) }

// Abort는 CLOSE 없이 연결을 끊는다 (업로드 중 연결 끊기 재현).
func (c *Conn) Abort() error { return c.ws.Close() }

// Result는 세션이 끝날 때까지 받은 것이다.
type Result struct {
	Stdout    []byte
	Stderr    []byte
	Status    []byte // 채널 3 그대로
	ExitCode  int    // Status에서 (-1 = 없음)
	CloseCode int
	CloseText string
}

// Read는 close까지 읽는다. onStdout이 있으면 채널 1이 올 때마다 부른다(대화형 확인용).
func (c *Conn) Read(timeout time.Duration, onStdout func([]byte)) (Result, error) {
	res := Result{ExitCode: -1}
	deadline := time.Now().Add(timeout)
	for {
		_ = c.ws.SetReadDeadline(deadline)
		_, msg, err := c.ws.ReadMessage()
		if err != nil {
			var ce *websocket.CloseError
			if errors.As(err, &ce) {
				res.CloseCode, res.CloseText = ce.Code, ce.Text
				return res, nil
			}
			return res, err
		}
		ch, p, err := proto.Parse(msg)
		if err != nil {
			continue
		}
		switch ch {
		case proto.ChStdout:
			res.Stdout = append(res.Stdout, p...)
			if onStdout != nil {
				onStdout(p)
			}
		case proto.ChStderr:
			res.Stderr = append(res.Stderr, p...)
		case proto.ChStatus:
			res.Status = append([]byte{}, p...)
			res.ExitCode = proto.StatusExitCode(p)
		}
	}
}

// ReadUntil은 stdout에 want가 나올 때까지 읽어 그때까지의 stdout을 돌려준다.
// 그 전에 닫히거나 시간이 지나면 오류다. 대화형 셸 확인에 쓴다(입력 echo에 없는 값을 want로 고른다).
func (c *Conn) ReadUntil(want []byte, timeout time.Duration) ([]byte, error) {
	var out []byte
	deadline := time.Now().Add(timeout)
	for !bytes.Contains(out, want) {
		_ = c.ws.SetReadDeadline(deadline)
		_, msg, err := c.ws.ReadMessage()
		if err != nil {
			return out, fmt.Errorf("waiting for %q: %w", want, err)
		}
		if ch, p, err := proto.Parse(msg); err == nil && ch == proto.ChStdout {
			out = append(out, p...)
		}
	}
	return out, nil
}

// Close는 연결을 닫는다.
func (c *Conn) Close() error { return c.ws.Close() }
