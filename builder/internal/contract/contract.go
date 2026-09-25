// Package contract는 core ↔ 빌더 사이의 요청·이벤트 형식이다 (지시서 3절).
// api·run·gitops·callback이 함께 쓰므로 어느 한쪽에 두면 import 순환이 생겨 따로 뺐다.
package contract

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

const (
	KindBuild    = "build"
	KindConfig   = "config"
	KindSetImage = "set-image"
	KindDelete   = "delete"

	SlotServer = "server"
	SlotStatic = "static"
)

// DeployRequest는 POST /internal/deploys 본문이다.
type DeployRequest struct {
	BuildID   string      `json:"build_id"`
	Actor     string      `json:"actor,omitempty"`
	Namespace string      `json:"namespace"`
	Slot      string      `json:"slot,omitempty"`
	Kind      string      `json:"kind"`
	Values    *CoreValues `json:"values,omitempty"`
	Unit      *Unit       `json:"unit,omitempty"`
	Build     *BuildSpec  `json:"build,omitempty"`
	Image     string      `json:"image,omitempty"`
}

// Unit은 서버 슬롯의 빌드와 묶인 runtime·port다.
type Unit struct {
	Runtime string `json:"runtime"`
	Port    int    `json:"port"`
}

type BuildSpec struct {
	Repo           string `json:"repo"`
	Ref            string `json:"ref"`
	Mode           string `json:"mode"`
	DockerfileDir  string `json:"dockerfile_dir,omitempty"`
	DockerfileName string `json:"dockerfile_name,omitempty"`
	ImageRepo      string `json:"image_repo"`
	ImageTag       string `json:"image_tag"`
	CacheRef       string `json:"cache_ref,omitempty"`
	GitAuthSecret  string `json:"git_auth_secret,omitempty"` // 예약 (private repo)
	ProjectPath    string `json:"project_path,omitempty"`    // 예약 (nixpacks)
}

// CoreValues는 values.yaml에서 core가 주인인 칸이다 (지시서 3-4).
// 포인터 = 보냈는지 구분용. nil이면 git의 현재 값을 유지한다.
type CoreValues struct {
	Name        *string     `json:"name,omitempty"`
	UserID      *string     `json:"userId,omitempty"`
	Replicas    *int        `json:"replicas,omitempty"`
	EnvRevision *int        `json:"envRevision,omitempty"`
	DB          *string     `json:"db,omitempty"`
	Redis       *bool       `json:"redis,omitempty"`
	Volume      *CoreVolume `json:"volume,omitempty"`
	Static      *CoreStatic `json:"static,omitempty"`
	Hostnames   *[]string   `json:"hostnames,omitempty"`
}

type CoreVolume struct {
	MountPath *string `json:"mountPath,omitempty"`
}

type CoreStatic struct {
	Enabled   *bool     `json:"enabled,omitempty"`
	Hostnames *[]string `json:"hostnames,omitempty"`
}

// ForbiddenKeyError는 values에 빌더 소유 칸(이미지·runtime·port)이 들어온 경우다.
type ForbiddenKeyError struct{ Key string }

func (e *ForbiddenKeyError) Error() string {
	return fmt.Sprintf("values.%s is owned by the builder", e.Key)
}

// UnmarshalJSON은 금지 키를 먼저 이름으로 잡아 알려 주고, 나머지는 모르는 키를 거부하며 읽는다.
// (차트 values.schema.json이 모든 수준에서 additionalProperties:false)
func (v *CoreValues) UnmarshalJSON(b []byte) error {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		return err
	}
	for _, k := range []string{"image", "runtime", "port"} {
		if _, ok := raw[k]; ok {
			return &ForbiddenKeyError{Key: k}
		}
	}
	if s, ok := raw["static"]; ok && !bytes.Equal(bytes.TrimSpace(s), []byte("null")) {
		var st map[string]json.RawMessage
		if err := json.Unmarshal(s, &st); err != nil {
			return fmt.Errorf("values.static: %w", err)
		}
		if _, ok := st["image"]; ok {
			return &ForbiddenKeyError{Key: "static.image"}
		}
	}
	type plain CoreValues // 메서드 없는 별칭으로 재귀 호출을 피한다
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	var p plain
	if err := dec.Decode(&p); err != nil {
		return fmt.Errorf("values: %w", err)
	}
	*v = CoreValues(p)
	return nil
}

// 이벤트 종류 (지시서 3-2)
const (
	EventLog       = "log"
	EventCommitted = "committed"
	EventDeployed  = "deployed"
	EventFinished  = "finished"
	EventFailed    = "failed"
	EventCancelled = "cancelled"
	EventDeleted   = "deleted"
)

// failed 이벤트의 stage
const (
	StageBuild   = "build"
	StageCommit  = "commit"
	StageSync    = "sync"
	StageHealth  = "health"
	StageTimeout = "timeout"
)

// Event는 빌더 → core 콜백 본문이다. 종류별로 쓰는 칸만 채운다.
type Event struct {
	Seq  int64     `json:"seq"`
	Type string    `json:"type"`
	At   time.Time `json:"at"`

	Lines []string `json:"lines,omitempty"` // log

	Image          string     `json:"image,omitempty"`      // committed, deployed
	CommitSHA      string     `json:"commit_sha,omitempty"` // committed, deleted
	PushDoneAt     *time.Time `json:"push_done_at,omitempty"`
	TriggeredEarly *bool      `json:"triggered_early,omitempty"`

	SyncedAt  *time.Time `json:"synced_at,omitempty"` // deployed
	HealthyAt *time.Time `json:"healthy_at,omitempty"`

	JobEndedAt   *time.Time `json:"job_ended_at,omitempty"` // finished
	JobSucceeded *bool      `json:"job_succeeded,omitempty"`
	ExportFailed *bool      `json:"export_failed,omitempty"`

	Stage     string   `json:"stage,omitempty"` // failed
	Reason    string   `json:"reason,omitempty"`
	LastLines []string `json:"last_lines,omitempty"`
}

// Droppable은 큐가 넘칠 때 버려도 되는 이벤트인지다. log만 버린다.
func (e *Event) Droppable() bool { return e.Type == EventLog }

// 디스패처(run)가 api에 돌려주는 오류. api가 HTTP 상태로 바꾼다.
var ErrNotFound = errors.New("build not found")

// ConflictError → 409. BuildID는 진행 중인 쪽이다.
type ConflictError struct {
	BuildID string
	Reason  string
}

func (e *ConflictError) Error() string {
	return fmt.Sprintf("%s (in progress: %s)", e.Reason, e.BuildID)
}

// BusyError → 429 + Retry-After.
type BusyError struct{ RetryAfter time.Duration }

func (e *BusyError) Error() string { return "too many active builds" }
