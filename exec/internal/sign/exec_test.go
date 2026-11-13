package sign

import (
	"errors"
	"net/http"
	"testing"
	"time"
)

// exec 내부 문은 작업 이름과 restore 기대값을 경로에 싣는다(쿼리는 서명 밖이라 쓰지 않는다).
// 경로의 어느 부분을 바꿔도 서명이 깨져야 한다.
func TestExecPathTamper(t *testing.T) {
	const (
		sha  = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"
		path = "/internal/v1/sessions/restore/1048576/" + sha
	)
	r, _ := http.NewRequest("GET", "http://kodeploy-exec:8081"+path, nil)
	SignRequest(secret, r, nil, now)

	tampered := []string{
		"/internal/v1/sessions/restore/1048577/" + sha,       // 크기
		"/internal/v1/sessions/restore/1048576/0" + sha[1:],  // 해시
		"/internal/v1/sessions/dump",                         // 작업 이름
		"/internal/v1/sessions/query",                        // 작업 이름
		"/internal/v1/sessions/restore/1048576/" + sha + "/", // 끝 슬래시
	}
	for _, p := range tampered {
		r2, _ := http.NewRequest("GET", "http://kodeploy-exec:8081"+p, nil)
		r2.Header = r.Header.Clone()
		if err := VerifyRequest(secret, r2, nil, now.Add(time.Second)); !errors.Is(err, ErrMismatch) {
			t.Errorf("%s: got %v, want mismatch", p, err)
		}
	}

	// 쿼리 문자열은 서명 밖이다. 그래서 내부 문은 쿼리를 읽지 않는다
	r3, _ := http.NewRequest("GET", "http://kodeploy-exec:8081"+path+"?size=1&sha256=00", nil)
	r3.Header = r.Header.Clone()
	if err := VerifyRequest(secret, r3, nil, now); err != nil {
		t.Fatalf("query is outside the signature, verify should pass: %v", err)
	}
}
