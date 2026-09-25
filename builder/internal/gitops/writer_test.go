package gitops

import (
	"context"
	"crypto/sha1"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

const (
	testRepo   = "kodeploy/kodeploy-apps"
	testToken  = "ghp_secret_token_value"
	testPath   = "apps/tenant-d6d8b759/values.yaml"
	testCommit = "c0ffee"
)

// fakeGitHub는 Contents API의 필요한 부분만 흉내 낸다.
type fakeGitHub struct {
	mu        sync.Mutex
	files     map[string][]byte
	commits   int
	conflicts int // 다음 쓰기 몇 번을 409로 거절할지
	fail500   bool
	puts      []string // 커밋 메시지
	authOK    bool
}

func (f *fakeGitHub) sha(b []byte) string { return fmt.Sprintf("%x", sha1.Sum(b)) }

func (f *fakeGitHub) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.authOK = r.Header.Get("Authorization") == "Bearer "+testToken && r.Header.Get("X-GitHub-Api-Version") != ""
	if f.fail500 {
		http.Error(w, `{"message":"server error"}`, 500)
		return
	}
	switch {
	case r.Method == "GET" && r.URL.Path == "/repos/"+testRepo+"/commits/main":
		fmt.Fprintf(w, "%s%d", testCommit, f.commits)
		return
	case strings.HasPrefix(r.URL.Path, "/repos/"+testRepo+"/contents/"):
	default:
		http.NotFound(w, r)
		return
	}
	path := strings.TrimPrefix(r.URL.Path, "/repos/"+testRepo+"/contents/")
	cur, exists := f.files[path]
	switch r.Method {
	case "GET":
		if r.URL.Query().Get("ref") != "main" {
			http.Error(w, "bad ref", 400)
			return
		}
		if !exists {
			http.Error(w, `{"message":"Not Found"}`, 404)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{"type": "file", "encoding": "base64", "sha": f.sha(cur),
			"content": base64.StdEncoding.EncodeToString(cur)})
	case "PUT", "DELETE":
		var body map[string]string
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["branch"] != "main" {
			http.Error(w, "bad branch", 400)
			return
		}
		if f.conflicts > 0 {
			f.conflicts--
			http.Error(w, `{"message":"is at abc but expected def"}`, 409)
			return
		}
		if exists && body["sha"] != f.sha(cur) {
			http.Error(w, `{"message":"sha mismatch"}`, 409)
			return
		}
		if !exists && r.Method == "PUT" && body["sha"] != "" {
			http.Error(w, `{"message":"no such file"}`, 422)
			return
		}
		f.commits++
		f.puts = append(f.puts, body["message"])
		if r.Method == "PUT" {
			c, _ := base64.StdEncoding.DecodeString(body["content"])
			f.files[path] = c
		} else {
			if !exists {
				http.Error(w, `{"message":"Not Found"}`, 404)
				return
			}
			delete(f.files, path)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"commit": map[string]string{"sha": fmt.Sprintf("%s%d", testCommit, f.commits)}})
	}
}

func setup(t *testing.T, files map[string][]byte) (*fakeGitHub, *Writer, func()) {
	t.Helper()
	fg := &fakeGitHub{files: files}
	srv := httptest.NewServer(fg)
	gh := NewGitHub(testRepo, "main", testToken)
	gh.BaseURL = srv.URL
	w := NewWriter(gh)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { w.Run(ctx); close(done) }()
	return fg, w, func() { cancel(); <-done; srv.Close() }
}

func setImageChange(img string) Change {
	req := &contract.DeployRequest{Kind: contract.KindSetImage, Slot: contract.SlotServer,
		Unit: &contract.Unit{Runtime: "java", Port: 8080}}
	return Change{Namespace: "tenant-d6d8b759", Message: "deploy(tenant-d6d8b759): set-image v2 [aa11bb22] by core",
		Apply: func(cur *Values) (*Values, error) { return Merge(cur, req, img) }}
}

func demoFiles(t *testing.T) map[string][]byte {
	b, err := os.ReadFile("testdata/demo-values.yaml")
	if err != nil {
		t.Fatal(err)
	}
	return map[string][]byte{testPath: b}
}

func TestCommitUpdatesFile(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	img := "ghcr.io/yuntyu01/d6d8b759/kodeploy-test-spring:v2@" + newDigest
	res, err := w.Commit(context.Background(), setImageChange(img))
	if err != nil || res.Skipped || res.CommitSHA != "c0ffee1" {
		t.Fatalf("got %+v %v", res, err)
	}
	if !fg.authOK {
		t.Fatal("auth headers missing")
	}
	got, _ := Parse(fg.files[testPath])
	if got.Image != img || got.DB != "mysql" {
		t.Fatalf("file not updated: %+v", got)
	}
	if fg.puts[0] != "deploy(tenant-d6d8b759): set-image v2 [aa11bb22] by core" {
		t.Fatalf("message %q", fg.puts[0])
	}
}

func TestCommitSkipsWhenUnchanged(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	cur, _ := Parse(fg.files[testPath])
	res, err := w.Commit(context.Background(), setImageChange(cur.Image))
	if err != nil || !res.Skipped || res.CommitSHA != "c0ffee0" {
		t.Fatalf("got %+v %v", res, err)
	}
	if fg.commits != 0 {
		t.Fatal("must not commit")
	}
}

func TestCommitCreatesMissingFile(t *testing.T) {
	fg, w, stop := setup(t, map[string][]byte{})
	defer stop()
	res, err := w.Commit(context.Background(), setImageChange("ghcr.io/u/d6d8b759/a:v1@"+newDigest))
	if err != nil || res.CommitSHA == "" {
		t.Fatalf("got %+v %v", res, err)
	}
	if _, ok := fg.files[testPath]; !ok {
		t.Fatal("file not created")
	}
}

func TestCommitRetriesConflicts(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	fg.conflicts = maxAttempts - 1
	if _, err := w.Commit(context.Background(), setImageChange("ghcr.io/u/d6d8b759/a:v3@"+newDigest)); err != nil {
		t.Fatalf("should succeed on the last attempt: %v", err)
	}
	fg.conflicts = maxAttempts
	_, err := w.Commit(context.Background(), setImageChange("ghcr.io/u/d6d8b759/a:v4@"+newDigest))
	if !errors.Is(err, ErrConflict) || !strings.Contains(err.Error(), "gave up") {
		t.Fatalf("got %v", err)
	}
}

func TestCommitServerErrorNoRetryAndNoToken(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	fg.fail500 = true
	_, err := w.Commit(context.Background(), setImageChange("ghcr.io/u/d6d8b759/a:v5@"+newDigest))
	if err == nil || errors.Is(err, ErrConflict) {
		t.Fatalf("got %v", err)
	}
	if strings.Contains(err.Error(), testToken) {
		t.Fatal("token leaked into error")
	}
}

func TestDelete(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	res, err := w.Commit(context.Background(), Change{Namespace: "tenant-d6d8b759", Message: "delete", Delete: true})
	if err != nil || res.CommitSHA == "" || res.Skipped {
		t.Fatalf("got %+v %v", res, err)
	}
	if _, ok := fg.files[testPath]; ok {
		t.Fatal("file not deleted")
	}
	// 이미 없으면 성공 (지시서 4-1)
	res, err = w.Commit(context.Background(), Change{Namespace: "tenant-d6d8b759", Message: "delete", Delete: true})
	if err != nil || !res.Skipped || res.CommitSHA != "" {
		t.Fatalf("second delete: %+v %v", res, err)
	}
}

func TestConfigOnMissingFileFails(t *testing.T) {
	_, w, stop := setup(t, map[string][]byte{})
	defer stop()
	req := &contract.DeployRequest{Kind: contract.KindConfig, Slot: "server", Values: &contract.CoreValues{EnvRevision: intp(1)}}
	_, err := w.Commit(context.Background(), Change{Namespace: "tenant-d6d8b759", Message: "config",
		Apply: func(cur *Values) (*Values, error) { return Merge(cur, req, "") }})
	if !errors.Is(err, ErrNoValuesFile) {
		t.Fatalf("got %v", err)
	}
}

func TestRejectsBadNamespace(t *testing.T) {
	fg, w, stop := setup(t, demoFiles(t))
	defer stop()
	if _, err := w.Commit(context.Background(), Change{Namespace: "../charts", Delete: true}); err == nil {
		t.Fatal("bad namespace accepted")
	}
	if fg.commits != 0 {
		t.Fatal("nothing should be written")
	}
}

func TestIsAncestor(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/repos/" + testRepo + "/compare/aaa...bbb":
			fmt.Fprint(w, `{"status":"ahead"}`)
		case "/repos/" + testRepo + "/compare/bbb...aaa":
			fmt.Fprint(w, `{"status":"behind"}`)
		case "/repos/" + testRepo + "/compare/aaa...ccc":
			fmt.Fprint(w, `{"status":"diverged"}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()
	gh := NewGitHub(testRepo, "main", testToken)
	gh.BaseURL = srv.URL
	ctx := context.Background()
	for _, c := range []struct {
		base, head string
		want       bool
	}{{"aaa", "aaa", true}, {"aaa", "bbb", true}, {"bbb", "aaa", false}, {"aaa", "ccc", false}} {
		got, err := gh.IsAncestor(ctx, c.base, c.head)
		if err != nil || got != c.want {
			t.Errorf("%s...%s: got %v %v", c.base, c.head, got, err)
		}
	}
	if _, err := gh.IsAncestor(ctx, "aaa", "zzz"); err == nil {
		t.Error("404 should be an error")
	}
}
