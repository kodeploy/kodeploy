// Package argo는 Argo CD Application에 refresh를 요청하고, 우리 커밋이 Synced + Healthy가 될 때까지 기다린다
// (지시서 4-4). Argo 모듈은 쓰지 않고 dynamic client로 Application을 unstructured로 읽는다.
package argo

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/dynamic"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

var ApplicationGVR = schema.GroupVersionResource{Group: "argoproj.io", Version: "v1alpha1", Resource: "applications"}

const refreshAnnotation = "argocd.argoproj.io/refresh"

// Failure는 대기가 실패로 끝난 경우다. Stage는 contract.StageSync/Health/Timeout.
type Failure struct {
	Stage  string
	Reason string
}

// Error는 "<stage>: <reason>"이다.
func (f *Failure) Error() string { return f.Stage + ": " + f.Reason }

// Synced는 대기가 성공했을 때의 시각이다 (deployed 이벤트의 synced_at, healthy_at).
type Synced struct {
	SyncedAt  time.Time
	HealthyAt time.Time
}

// AncestorFunc는 base 커밋이 head에 들어 있는지다 (gitops.GitHub.IsAncestor).
type AncestorFunc func(ctx context.Context, base, head string) (bool, error)

// Client는 argocd 네임스페이스의 Application을 dynamic client로 다룬다.
type Client struct {
	dyn        dynamic.Interface
	ns         string
	valuesRepo string // 값 source의 repoURL (정규화 비교)
	ancestor   AncestorFunc
	poll       time.Duration
}

// New는 Client를 만든다. valuesRepo는 values source의 repoURL, ancestor는 커밋 포함 여부 확인 함수다.
func New(dyn dynamic.Interface, namespace, valuesRepo string, ancestor AncestorFunc) *Client {
	return &Client{dyn: dyn, ns: namespace, valuesRepo: normRepo(valuesRepo), ancestor: ancestor, poll: 2 * time.Second}
}

// normRepo는 repoURL 비교용 정규화다 (소문자, scheme·끝 슬래시·.git 제거).
func normRepo(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	s = strings.TrimSuffix(strings.TrimSuffix(s, "/"), ".git")
	s = strings.TrimPrefix(strings.TrimPrefix(s, "https://"), "http://")
	return s
}

// apps는 Application 리소스 클라이언트다.
func (c *Client) apps() dynamic.ResourceInterface {
	return c.dyn.Resource(ApplicationGVR).Namespace(c.ns)
}

// Refresh는 refresh 어노테이션을 붙인다. Application이 아직 없으면(새 앱: ApplicationSet이 만들 때까지)
// poll 간격으로 다시 시도한다. ctx가 끝나면 그만둔다.
func (c *Client) Refresh(ctx context.Context, app string) error {
	patch, _ := json.Marshal(map[string]any{"metadata": map[string]any{"annotations": map[string]string{refreshAnnotation: "normal"}}})
	for {
		_, err := c.apps().Patch(ctx, app, types.MergePatchType, patch, metav1.PatchOptions{})
		if err == nil {
			return nil
		}
		if !apierrors.IsNotFound(err) && !isTransient(err) {
			return err
		}
		if !sleep(ctx, c.poll) {
			return context.Cause(ctx)
		}
	}
}

// Wait는 poll 간격으로 Application을 읽는다. 값 source의 revision이 sha이거나 sha를 포함하고,
// sync=Synced, health=Healthy면 끝. 이 대기 이후(since) 시작된 operation이 Failed/Error면 Failure(sync),
// 우리 revision으로 Synced인데 Degraded면 Failure(health), timeout이 지나면 Failure(timeout).
// ctx 자체가 끝나면(취소·종료) ctx 원인을 돌려준다.
func (c *Client) Wait(ctx context.Context, app, sha string, since time.Time, timeout time.Duration) (Synced, error) {
	wctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var out Synced
	var last string
	known := map[string]bool{} // revision → sha를 포함하는지 (compare API 호출 줄이기)
	for {
		u, err := c.apps().Get(wctx, app, metav1.GetOptions{})
		switch {
		case err == nil:
			st := readStatus(u, c.valuesRepo)
			last = fmt.Sprintf("sync=%s health=%s revision=%s", st.sync, st.health, short(st.revision))

			if st.opFailed && !st.opStarted.Before(since.Add(-5*time.Second)) {
				return out, &Failure{Stage: contract.StageSync, Reason: nonEmpty(st.opMessage, "sync operation "+st.opPhase)}
			}
			ok, cerr := c.contains(wctx, known, st.revision, sha)
			if cerr == nil && ok && st.sync == "Synced" {
				if out.SyncedAt.IsZero() {
					out.SyncedAt = time.Now()
				}
				switch st.health {
				case "Healthy":
					out.HealthyAt = time.Now()
					return out, nil
				case "Degraded":
					return out, &Failure{Stage: contract.StageHealth, Reason: nonEmpty(st.healthMessage, "application degraded")}
				}
			}
		case apierrors.IsNotFound(err):
			last = "application not found yet"
		}

		if !sleep(wctx, c.poll) {
			if ctx.Err() != nil {
				return out, context.Cause(ctx)
			}
			return out, &Failure{Stage: contract.StageTimeout,
				Reason: fmt.Sprintf("not synced and healthy within %s (%s)", timeout, last)}
		}
	}
}

// contains는 revision rev가 우리 커밋 sha를 포함하는지다.
// 같으면 바로 true, 아니면 compare API로 확인하고 결과를 known에 캐시한다.
func (c *Client) contains(ctx context.Context, known map[string]bool, rev, sha string) (bool, error) {
	if rev == "" {
		return false, nil
	}
	if rev == sha {
		return true, nil
	}
	if v, ok := known[rev]; ok {
		return v, nil
	}
	v, err := c.ancestor(ctx, sha, rev)
	if err != nil {
		return false, err
	}
	known[rev] = v
	return v, nil
}

// status는 Application에서 대기 판정에 필요한 칸만 뽑은 것이다.
type status struct {
	revision      string
	sync          string
	health        string
	healthMessage string
	opPhase       string
	opMessage     string
	opStarted     time.Time
	opFailed      bool
}

// readStatus는 필요한 칸만 뽑는다. multi-source면 spec.sources에서 값 repo의 순번을 찾아
// status.sync.revisions의 같은 자리를 쓴다 (kodeploy-charts ApplicationSet: [차트, 값]).
func readStatus(u *unstructured.Unstructured, valuesRepo string) status {
	var st status
	st.sync, _, _ = unstructured.NestedString(u.Object, "status", "sync", "status")
	st.health, _, _ = unstructured.NestedString(u.Object, "status", "health", "status")
	st.healthMessage, _, _ = unstructured.NestedString(u.Object, "status", "health", "message")

	if sources, ok, _ := unstructured.NestedSlice(u.Object, "spec", "sources"); ok {
		revs, _, _ := unstructured.NestedStringSlice(u.Object, "status", "sync", "revisions")
		for i, s := range sources {
			m, _ := s.(map[string]any)
			if repo, _ := m["repoURL"].(string); normRepo(repo) == valuesRepo && i < len(revs) {
				st.revision = revs[i]
			}
		}
	} else {
		st.revision, _, _ = unstructured.NestedString(u.Object, "status", "sync", "revision")
	}

	st.opPhase, _, _ = unstructured.NestedString(u.Object, "status", "operationState", "phase")
	st.opMessage, _, _ = unstructured.NestedString(u.Object, "status", "operationState", "message")
	started, _, _ := unstructured.NestedString(u.Object, "status", "operationState", "startedAt")
	st.opStarted, _ = time.Parse(time.RFC3339, started)
	st.opFailed = st.opPhase == "Failed" || st.opPhase == "Error"
	return st
}

// isTransient는 다시 시도할 만한 API 오류(타임아웃, 429, 503, 500)인지다.
func isTransient(err error) bool {
	return apierrors.IsServerTimeout(err) || apierrors.IsTimeout(err) || apierrors.IsTooManyRequests(err) ||
		apierrors.IsServiceUnavailable(err) || apierrors.IsInternalError(err) || errors.Is(err, context.DeadlineExceeded)
}

// nonEmpty는 s가 비면 def를 돌려준다.
func nonEmpty(s, def string) string {
	if s == "" {
		return def
	}
	return s
}

// short는 커밋 sha를 7자로 줄인다 (메시지용).
func short(s string) string {
	if len(s) > 7 {
		return s[:7]
	}
	return s
}

// sleep은 ctx를 존중하는 대기다. ctx가 먼저 끝나면 false.
func sleep(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}
