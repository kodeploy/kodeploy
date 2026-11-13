package sign

import (
	"bytes"
	"errors"
	"net/http"
	"strconv"
	"testing"
	"time"
)

var (
	secret = []byte("shared-secret")
	now    = time.Unix(1_790_000_000, 0)
	body   = []byte(`{"build_id":"3f9a2c1d"}`)
)

func TestVerify(t *testing.T) {
	ts := strconv.FormatInt(now.Unix(), 10)
	good := Compute(secret, ts, "POST", "/internal/deploys", body)

	cases := []struct {
		name   string
		ts     string
		sig    string
		method string
		path   string
		body   []byte
		secret []byte
		at     time.Time
		want   error
	}{
		{"ok", ts, good, "POST", "/internal/deploys", body, secret, now, nil},
		{"ok at +60s", ts, good, "POST", "/internal/deploys", body, secret, now.Add(60 * time.Second), nil},
		{"ok uppercase hex", ts, string(bytes.ToUpper([]byte(good))), "POST", "/internal/deploys", body, secret, now, nil},
		{"expired past", ts, good, "POST", "/internal/deploys", body, secret, now.Add(61 * time.Second), ErrExpired},
		{"expired future", ts, good, "POST", "/internal/deploys", body, secret, now.Add(-61 * time.Second), ErrExpired},
		{"bad timestamp", "yesterday", good, "POST", "/internal/deploys", body, secret, now, ErrExpired},
		{"missing sig", ts, "", "POST", "/internal/deploys", body, secret, now, ErrMissing},
		{"missing ts", "", good, "POST", "/internal/deploys", body, secret, now, ErrMissing},
		{"body changed", ts, good, "POST", "/internal/deploys", []byte(`{"build_id":"00000000"}`), secret, now, ErrMismatch},
		{"method changed", ts, good, "DELETE", "/internal/deploys", body, secret, now, ErrMismatch},
		{"path changed", ts, good, "POST", "/internal/deploys/x", body, secret, now, ErrMismatch},
		{"wrong secret", ts, good, "POST", "/internal/deploys", body, []byte("other"), now, ErrMismatch},
		{"not hex", ts, "zz" + good[2:], "POST", "/internal/deploys", body, secret, now, ErrMismatch},
		{"truncated", ts, good[:32], "POST", "/internal/deploys", body, secret, now, ErrMismatch},
		// 같은 시각이라도 헤더 문자열이 다르면 다른 서명이다
		{"ts reformatted", "0" + ts, good, "POST", "/internal/deploys", body, secret, now, ErrMismatch},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			err := Verify(c.secret, c.ts, c.sig, c.method, c.path, c.body, c.at)
			if !errors.Is(err, c.want) {
				t.Fatalf("got %v, want %v", err, c.want)
			}
		})
	}
}

func TestRequestRoundTrip(t *testing.T) {
	r, _ := http.NewRequest("POST", "http://builder:8080/internal/deploys?ignored=1", bytes.NewReader(body))
	SignRequest(secret, r, body, now)
	if err := VerifyRequest(secret, r, body, now.Add(5*time.Second)); err != nil {
		t.Fatalf("round trip: %v", err)
	}
	// 쿼리는 서명 대상이 아니다 (path만)
	r.URL.RawQuery = "other=2"
	if err := VerifyRequest(secret, r, body, now); err != nil {
		t.Fatalf("query should not matter: %v", err)
	}
	r.URL.Path = "/internal/deploys/3f9a2c1d"
	if err := VerifyRequest(secret, r, body, now); !errors.Is(err, ErrMismatch) {
		t.Fatalf("path change not detected: %v", err)
	}
}

// 다른 구현(core 파이썬, hack/sign-request.sh)과 맞추기 위한 고정 벡터.
// 기대값은 python hmac과 openssl dgst -hmac으로 따로 계산했다.
func TestKnownVector(t *testing.T) {
	got := Compute([]byte("k"), "1700000000", "POST", "/internal/deploys", []byte("{}"))
	const want = "b667dc8d0d3b6aceef1df8ccc81d840550dad21280fd97e22d130af861f895a4"
	if got != want {
		t.Fatalf("got %s, want %s", got, want)
	}
}
