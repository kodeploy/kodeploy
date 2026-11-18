package contract

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestEventSourceSHARoundTrip(t *testing.T) {
	sha := "0123456789abcdef0123456789abcdef01234567"
	in := Event{Seq: 3, Type: EventCommitted, At: time.Date(2026, 11, 18, 0, 0, 0, 0, time.UTC),
		Image: "ghcr.io/u/x/app:abcd1234@sha256:" + strings.Repeat("a", 64), CommitSHA: strings.Repeat("b", 40), SourceSHA: sha}
	b, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), `"source_sha":"`+sha+`"`) || !strings.Contains(string(b), `"commit_sha":"`+in.CommitSHA+`"`) {
		t.Fatalf("json %s", b)
	}
	var out Event
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	if out.SourceSHA != sha || out.CommitSHA != in.CommitSHA {
		t.Fatalf("round trip %+v", out)
	}

	// 모르면 칸을 아예 보내지 않는다
	b, _ = json.Marshal(Event{Seq: 1, Type: EventFailed})
	if strings.Contains(string(b), "source_sha") {
		t.Fatalf("empty source_sha must be omitted: %s", b)
	}
}

func TestIsCommitSHA(t *testing.T) {
	sha := "0123456789abcdef0123456789abcdef01234567"
	if !IsCommitSHA(sha) {
		t.Fatal("40 lowercase hex is a commit")
	}
	for _, ref := range []string{"main", sha[:7], sha[:39], sha + "0", strings.ToUpper(sha), "release/" + sha[:32]} {
		if IsCommitSHA(ref) {
			t.Fatalf("%q must be treated as a branch", ref)
		}
	}
}
