// Package api는 KoDeploy core의 재배포·상태 조회·CI 토큰 교환 API 클라이언트다.
// 타입과 규칙은 docs/deploy-api-contract.md만 따른다 (서버 내부 구현에 기대지 않는다).
package api

// 빌드 상태 (계약 4절)
const (
	StatusQueued    = "queued"
	StatusBuilding  = "building"
	StatusBuilt     = "built"
	StatusDeploying = "deploying"
	StatusRunning   = "running"
	StatusFailed    = "failed"
	StatusCancelled = "cancelled"
)

// 오류 본문의 기계용 code 중 CLI가 갈라 처리하는 것 (계약 3-4, 10절)
const (
	CodeKeyInProgress    = "key_in_progress"
	CodeKeyReleased      = "key_released"
	CodeNoLiveDeploy     = "no_live_deploy"
	CodeNoSuccessfulSlot = "no_successful_slot"
)

// POST /apps/{app_id}/deploy/redeploy 본문 (계약 3절). CI 토큰이면 비워 두면 서버가 토큰의 커밋으로 채운다.
type RedeployRequest struct {
	CommitSHA string `json:"commit_sha,omitempty"`
}

type BuildRef struct {
	BuildID string `json:"build_id"`
	Slot    string `json:"slot"` // "server" | "static"
	Status  string `json:"status"`
	Ref     string `json:"ref"`
}

// 계약 3-3. 새로 시작하면 202, 같은 Idempotency-Key로 이미 처리한 요청이면 200 + replayed.
type RedeployResponse struct {
	AppID        string     `json:"app_id"`
	RequestedSHA *string    `json:"requested_sha"`
	Replayed     bool       `json:"replayed"`
	Builds       []BuildRef `json:"builds"`
}

// 상태 응답의 ci 칸 (계약 4절). 값은 모두 문자열이다.
type CIRun struct {
	Actor      string `json:"actor,omitempty"`
	Repository string `json:"repository"`
	Ref        string `json:"ref"`
	SHA        string `json:"sha"`
	RunID      string `json:"run_id,omitempty"`
	RunAttempt string `json:"run_attempt,omitempty"`
	Workflow   string `json:"workflow,omitempty"`
	EventName  string `json:"event_name"`
}

// GET /apps/{app_id}/deploy/{build_id} 응답 중 CLI가 쓰는 칸 (계약 4절)
type Build struct {
	BuildID      string   `json:"build_id"`
	Status       string   `json:"status"`
	Runtime      string   `json:"runtime,omitempty"`
	Branch       string   `json:"branch,omitempty"`
	Error        *string  `json:"error"`
	SourceSHA    *string  `json:"source_sha"`
	RequestedSHA *string  `json:"requested_sha"`
	AIStatus     *string  `json:"ai_status"`
	AIAnalysis   *string  `json:"ai_analysis"`
	TotalSeconds *float64 `json:"total_seconds"`
	CI           *CIRun   `json:"ci"`
}

// GET /apps/{app_id}/deploy/redeploy/commits — 배포하기에서 고를 커밋. sha는 40자.
type Commit struct {
	SHA     string  `json:"sha"`
	Message string  `json:"message"`
	Date    *string `json:"date"`
	URL     *string `json:"url"`
}

// 기준 저장소·브랜치의 최근 커밋(최신순 최대 3개)과 지금 떠 있는 커밋. GitHub 조회가 실패하면 Commits가 빈다.
// Current는 지금 떠 있는 커밋이 Commits에 없을 때만 온다.
type CommitsResponse struct {
	Repo       string   `json:"repo"`
	Branch     string   `json:"branch"`
	CurrentSHA *string  `json:"current_sha"`
	Commits    []Commit `json:"commits"`
	Current    *Commit  `json:"current"`
}

// POST /apps/{app_id}/ci/token (계약 9-3)
type ExchangeRequest struct {
	IDToken string `json:"id_token"`
}

type ExchangeResponse struct {
	Token      string `json:"token"`
	ExpiresIn  int    `json:"expires_in"`
	AppID      string `json:"app_id"`
	Repository string `json:"repository"`
	Ref        string `json:"ref"`
	SHA        string `json:"sha"`
}

// Str은 nil이면 빈 문자열.
func Str(p *string) string {
	if p == nil {
		return ""
	}
	return *p
}
