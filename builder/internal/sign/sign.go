// Package sign은 core ↔ 빌더 요청의 HMAC 서명 규칙이다 (지시서 3-3). 양방향 같은 규칙.
//
//	signature = hex(HMAC-SHA256(secret, timestamp + "\n" + METHOD + "\n" + path + "\n" + body))
package sign

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/http"
	"strconv"
	"time"
)

const (
	HeaderTimestamp = "X-Kodeploy-Timestamp"
	HeaderSignature = "X-Kodeploy-Signature"
	MaxSkew         = 60 * time.Second
)

var (
	ErrMissing  = errors.New("signature headers missing")
	ErrExpired  = errors.New("timestamp outside allowed window")
	ErrMismatch = errors.New("signature mismatch")
)

func mac(secret []byte, ts, method, path string, body []byte) []byte {
	m := hmac.New(sha256.New, secret)
	m.Write([]byte(ts))
	m.Write([]byte{'\n'})
	m.Write([]byte(method))
	m.Write([]byte{'\n'})
	m.Write([]byte(path))
	m.Write([]byte{'\n'})
	m.Write(body)
	return m.Sum(nil)
}

// Compute는 서명 hex 문자열을 만든다. ts는 헤더에 실리는 문자열 그대로 쓴다.
func Compute(secret []byte, ts, method, path string, body []byte) string {
	return hex.EncodeToString(mac(secret, ts, method, path, body))
}

// Verify는 헤더 값으로 서명을 검증한다. 시각 차이 60초 이내 + 상수 시간 비교.
func Verify(secret []byte, ts, sig, method, path string, body []byte, now time.Time) error {
	if ts == "" || sig == "" {
		return ErrMissing
	}
	sec, err := strconv.ParseInt(ts, 10, 64)
	if err != nil {
		return ErrExpired
	}
	skew := now.Sub(time.Unix(sec, 0))
	if skew > MaxSkew || skew < -MaxSkew {
		return ErrExpired
	}
	got, err := hex.DecodeString(sig)
	if err != nil {
		return ErrMismatch
	}
	if !hmac.Equal(mac(secret, ts, method, path, body), got) {
		return ErrMismatch
	}
	return nil
}

// VerifyRequest는 받은 요청을 검증한다. body는 이미 읽어 둔 본문이다.
func VerifyRequest(secret []byte, r *http.Request, body []byte, now time.Time) error {
	return Verify(secret, r.Header.Get(HeaderTimestamp), r.Header.Get(HeaderSignature),
		r.Method, r.URL.EscapedPath(), body, now)
}

// SignRequest는 보낼 요청에 서명 헤더를 붙인다. body는 요청에 실을 본문과 같아야 한다.
func SignRequest(secret []byte, r *http.Request, body []byte, now time.Time) {
	ts := strconv.FormatInt(now.Unix(), 10)
	r.Header.Set(HeaderTimestamp, ts)
	r.Header.Set(HeaderSignature, Compute(secret, ts, r.Method, r.URL.EscapedPath(), body))
}
