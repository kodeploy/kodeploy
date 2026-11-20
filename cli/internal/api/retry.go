package api

import (
	"context"
	"errors"
	"time"

	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

// 재배포 응답 중 같은 이름표(Idempotency-Key)로 다시 보내는 경우 (계약 3-2·3-4)
var (
	// 409 key_in_progress·key_released — 같은 키의 첫 요청이 아직 처리 중이거나 방금 풀렸다. 합 30초
	KeyRetryDelays = []time.Duration{2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}
	// 5xx·네트워크 오류 — 최대 5번
	ServerRetryDelays = []time.Duration{1 * time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}
)

// ErrDeadline은 다시 보내려고 기다리면 시간 제한을 넘기는 경우다.
var ErrDeadline = errors.New("시간 제한 안에 재배포 요청을 마치지 못했어요")

// RedeployWithRetry는 재배포를 요청하고, 다시 보낼 응답이면 같은 이름표로 다시 보낸다.
// 다시 보내지 않는 응답(400·403·404·422, 다른 409, 재시도를 다 쓴 오류)은 그대로 돌려준다.
// 401은 Redeploy 안에서 한 번 새 토큰으로 다시 보낸다. notice는 다시 보내기 전에 이유와 기다릴 시간을 받는다.
func (c *Client) RedeployWithRetry(
	ctx context.Context, clk clock.Clock, deadline time.Time,
	app, key string, req RedeployRequest, notice func(reason string, delay time.Duration),
) (*RedeployResponse, int, error) {
	keyTries, serverTries := 0, 0
	for {
		resp, status, err := c.Redeploy(ctx, app, key, req)
		if err == nil {
			return resp, status, nil
		}
		if ctx.Err() != nil {
			return nil, 0, ctx.Err()
		}
		var delay time.Duration
		var reason string
		if e, ok := AsError(err); ok && e.Status == 409 && (e.Code == CodeKeyInProgress || e.Code == CodeKeyReleased) {
			if keyTries >= len(KeyRetryDelays) {
				return nil, status, err
			}
			delay, reason = KeyRetryDelays[keyTries], "같은 이름표의 요청을 서버가 아직 처리 중"
			keyTries++
		} else if Retryable(err) {
			if serverTries >= len(ServerRetryDelays) {
				return nil, status, err
			}
			delay, reason = ServerRetryDelays[serverTries], err.Error()
			serverTries++
		} else {
			return nil, status, err
		}
		if clk.Now().Add(delay).After(deadline) {
			return nil, status, ErrDeadline
		}
		if notice != nil {
			notice(reason, delay)
		}
		if err := clk.Sleep(ctx, delay); err != nil {
			return nil, 0, err
		}
	}
}
