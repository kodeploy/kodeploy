// Package wait는 재배포가 만든 빌드들을 끝날 때까지 조회하고 결과를 판정한다.
package wait

import "github.com/kodeploy/kodeploy/cli/internal/api"

type Result int

const (
	Pending    Result = iota // 진행 중이 남음 — 계속 조회
	Success                  // 모두 running
	Failed                   // 하나라도 failed
	Superseded               // 실패 없이 cancelled가 있음 (같은 슬롯의 다른 배포에 밀림)
)

func (r Result) String() string {
	switch r {
	case Success:
		return "success"
	case Failed:
		return "failed"
	case Superseded:
		return "superseded"
	default:
		return "pending"
	}
}

// Judge는 계약 5절의 판정 규칙이다.
//  1. 하나라도 failed면 실패
//  2. 실패가 없고 하나라도 cancelled면 "다른 배포에 밀림"
//  3. 모두 running이면 성공
//  4. 그 외(진행 중이 남음)면 계속 조회
//
// 빌드가 하나도 없으면 판정할 것이 없어 Pending이다 (서버는 빌드 없이 202를 주지 않는다).
func Judge(statuses []string) Result {
	if len(statuses) == 0 {
		return Pending
	}
	failed, cancelled, running := false, false, 0
	for _, s := range statuses {
		switch s {
		case api.StatusFailed:
			failed = true
		case api.StatusCancelled:
			cancelled = true
		case api.StatusRunning:
			running++
		}
	}
	switch {
	case failed:
		return Failed
	case cancelled:
		return Superseded
	case running == len(statuses):
		return Success
	default:
		return Pending
	}
}
