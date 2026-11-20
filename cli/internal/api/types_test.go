package api

import (
	"encoding/json"
	"testing"
)

// 계약 3-3·4절의 예시 본문이 타입으로 그대로 읽히는지
func TestContractExamplesDecode(t *testing.T) {
	var r RedeployResponse
	body := `{"app_id":"11111111-2222-4333-8444-555555555555","requested_sha":"0123456789abcdef0123456789abcdef01234567",
	"replayed":false,"builds":[{"build_id":"3f9a2c1d","slot":"server","status":"queued","ref":"0123456789abcdef0123456789abcdef01234567"},
	{"build_id":"8e7d6c5b","slot":"static","status":"queued","ref":"main"}]}`
	if err := json.Unmarshal([]byte(body), &r); err != nil {
		t.Fatal(err)
	}
	if len(r.Builds) != 2 || r.Builds[1].Slot != "static" || r.Builds[1].Ref != "main" || Str(r.RequestedSHA) == "" {
		t.Fatalf("unexpected %+v", r)
	}

	var b Build
	body = `{"build_id":"3f9a2c1d","status":"failed","error":"build: exit 1","source_sha":null,"requested_sha":null,
	"ai_status":"done","ai_analysis":"{}","total_seconds":12.5,
	"ci":{"actor":"octocat","repository":"acme/api","ref":"refs/heads/main","sha":"0123","run_id":"9","run_attempt":"1","workflow":"deploy","event_name":"push"},
	"logs":"ignored","created_at":"2026-11-21T00:00:00Z"}`
	if err := json.Unmarshal([]byte(body), &b); err != nil {
		t.Fatal(err)
	}
	if b.Status != StatusFailed || Str(b.Error) != "build: exit 1" || b.SourceSHA != nil || b.CI == nil || b.CI.RunID != "9" {
		t.Fatalf("unexpected %+v", b)
	}

	var x ExchangeResponse
	body = `{"token":"kdci_x","expires_in":900,"app_id":"a","repository":"owner/repo","ref":"refs/heads/main","sha":"0123"}`
	if err := json.Unmarshal([]byte(body), &x); err != nil || x.ExpiresIn != 900 || x.Repository != "owner/repo" {
		t.Fatalf("unexpected %+v %v", x, err)
	}
}

func TestRedeployRequestOmitsEmptySHA(t *testing.T) {
	b, _ := json.Marshal(RedeployRequest{})
	if string(b) != "{}" {
		t.Fatalf("got %s", b)
	}
	b, _ = json.Marshal(RedeployRequest{CommitSHA: "abc"})
	if string(b) != `{"commit_sha":"abc"}` {
		t.Fatalf("got %s", b)
	}
}
