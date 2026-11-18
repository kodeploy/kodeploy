package job

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// init 스크립트의 clone 조각(cloneSource)을 실제 sh와 git으로 돌린다. /workspace는 임시 폴더로 바꾼다.
// 저장소는 로컬 bare repo이고 file://로 받는다 (경로로 주면 --depth가 무시된다).

type cloneRepo struct {
	url     string
	commits []string // 오래된 것부터. 마지막이 main의 끝
	feature string   // feature 브랜치 끝
}

func needTools(t *testing.T) {
	t.Helper()
	for _, bin := range []string{"sh", "git", "base64"} {
		if _, err := exec.LookPath(bin); err != nil {
			t.Skipf("%s not installed", bin)
		}
	}
}

func git(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", args...)
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "GIT_CONFIG_NOSYSTEM=1", "HOME="+dir,
		"GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@example.com", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@example.com")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

// newCloneRepo는 main에 커밋 셋, feature에 하나를 가진 bare repo를 만든다.
func newCloneRepo(t *testing.T) cloneRepo {
	t.Helper()
	root := t.TempDir()
	work := filepath.Join(root, "work")
	bare := filepath.Join(root, "app.git")
	git(t, root, "init", "-q", "-b", "main", work)
	var commits []string
	for _, msg := range []string{"one", "two", "three"} {
		if err := os.WriteFile(filepath.Join(work, "f.txt"), []byte(msg), 0o644); err != nil {
			t.Fatal(err)
		}
		git(t, work, "add", "f.txt")
		git(t, work, "commit", "-q", "-m", msg)
		commits = append(commits, git(t, work, "rev-parse", "HEAD"))
	}
	git(t, work, "checkout", "-q", "-b", "feature", commits[0])
	if err := os.WriteFile(filepath.Join(work, "g.txt"), []byte("feature"), 0o644); err != nil {
		t.Fatal(err)
	}
	git(t, work, "add", "g.txt")
	git(t, work, "commit", "-q", "-m", "feature")
	feature := git(t, work, "rev-parse", "HEAD")
	git(t, root, "clone", "-q", "--bare", work, bare)
	return cloneRepo{url: "file://" + bare, commits: commits, feature: feature}
}

type scriptRun struct {
	out  string // stdout + stderr
	err  error
	ws   string // /workspace 자리
	home string
}

// runScript는 init 스크립트를 /workspace만 바꿔 sh로 돌린다. env는 REPO_URL·REF 등 스크립트가 읽는 값이다.
func runScript(t *testing.T, script string, env ...string) scriptRun {
	t.Helper()
	root := t.TempDir()
	ws := filepath.Join(root, "workspace")
	home := filepath.Join(root, "home")
	for _, d := range []string{ws, home} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	cmd := exec.Command("sh", "-c", strings.ReplaceAll(script, "/workspace", ws))
	// 바깥 환경(프록시·git 설정)이 섞이지 않게 필요한 것만 준다
	cmd.Env = append([]string{"PATH=" + os.Getenv("PATH"), "HOME=" + home, "GIT_CONFIG_NOSYSTEM=1", "GIT_TERMINAL_PROMPT=0"}, env...)
	out, err := cmd.CombinedOutput()
	return scriptRun{out: string(out), err: err, ws: ws, home: home}
}

func markerLines(out string) []string {
	var got []string
	for _, l := range strings.Split(out, "\n") {
		if strings.HasPrefix(l, SourceMarker) {
			got = append(got, l)
		}
	}
	return got
}

func TestInitScriptsShareTheCloneFragment(t *testing.T) {
	for name, script := range map[string]string{"clone": cloneScript, "static": staticScript, "nixpacks": nixpacksScript} {
		if strings.Count(script, cloneSource) != 1 {
			t.Errorf("%s script must contain the clone fragment exactly once", name)
		}
		if !strings.HasPrefix(script, "set -eu\n") {
			t.Errorf("%s script must keep set -eu", name)
		}
		if strings.Contains(script, "BRANCH") {
			t.Errorf("%s script still mentions BRANCH", name)
		}
	}
}

// 오래된 커밋을 지정하면 그 커밋만 받아 detached로 둔다. 마커는 그 커밋이다.
func TestCloneCheckoutsTheRequestedOldCommit(t *testing.T) {
	needTools(t)
	repo := newCloneRepo(t)
	old := repo.commits[0]
	r := runScript(t, cloneScript, "REPO_URL="+repo.url, "REF="+old)
	if r.err != nil {
		t.Fatalf("script failed: %v\n%s", r.err, r.out)
	}
	src := filepath.Join(r.ws, "src")
	if head := git(t, src, "rev-parse", "HEAD"); head != old {
		t.Fatalf("HEAD %s, want %s", head, old)
	}
	if body, _ := os.ReadFile(filepath.Join(src, "f.txt")); string(body) != "one" {
		t.Fatalf("worktree is not the old commit: %q", body)
	}
	if n := git(t, src, "rev-list", "--count", "HEAD"); n != "1" {
		t.Fatalf("history depth %s, want 1 (shallow fetch)", n)
	}
	if got := markerLines(r.out); len(got) != 1 || got[0] != SourceMarker+old {
		t.Fatalf("marker lines %q\n%s", got, r.out)
	}
	if !strings.Contains(r.out, "[1/1] cloning "+repo.url+" ("+old+")...") {
		t.Fatalf("progress line missing:\n%s", r.out)
	}
}

func TestCloneBranchReportsItsTip(t *testing.T) {
	needTools(t)
	repo := newCloneRepo(t)
	for ref, want := range map[string]string{"main": repo.commits[2], "feature": repo.feature} {
		r := runScript(t, cloneScript, "REPO_URL="+repo.url, "REF="+ref)
		if r.err != nil {
			t.Fatalf("%s: %v\n%s", ref, r.err, r.out)
		}
		if head := git(t, filepath.Join(r.ws, "src"), "rev-parse", "HEAD"); head != want {
			t.Fatalf("%s: HEAD %s, want %s", ref, head, want)
		}
		if got := markerLines(r.out); len(got) != 1 || got[0] != SourceMarker+want {
			t.Fatalf("%s: marker lines %q", ref, got)
		}
	}
}

// 없는 커밋이면 fetch에서 멈추고(set -eu) 마커를 찍지 않는다 — 빌드는 init 실패로 끝난다.
func TestCloneFailsForAnUnknownCommit(t *testing.T) {
	needTools(t)
	repo := newCloneRepo(t)
	r := runScript(t, cloneScript, "REPO_URL="+repo.url, "REF="+strings.Repeat("ab", 20))
	if r.err == nil {
		t.Fatalf("script must fail:\n%s", r.out)
	}
	if got := markerLines(r.out); len(got) != 0 {
		t.Fatalf("no marker expected after a failed fetch: %q", got)
	}
}

// 짧은 SHA·대문자 SHA는 커밋 경로로 가지 않는다 — 브랜치로 clone하려다 실패한다.
func TestShortOrUppercaseSHAIsTreatedAsABranch(t *testing.T) {
	needTools(t)
	repo := newCloneRepo(t)
	for _, ref := range []string{repo.commits[0][:7], strings.ToUpper(repo.commits[0])} {
		r := runScript(t, cloneScript, "REPO_URL="+repo.url, "REF="+ref)
		if r.err == nil {
			t.Fatalf("%s: script must fail", ref)
		}
		if _, err := os.Stat(filepath.Join(r.ws, "src", ".git", "FETCH_HEAD")); err == nil {
			t.Fatalf("%s: took the commit path", ref)
		}
		if !strings.Contains(r.out, "Remote branch") && !strings.Contains(r.out, "not found") {
			t.Fatalf("%s: expected a branch clone failure:\n%s", ref, r.out)
		}
	}
}

// 정적 사이트 스크립트도 같은 조각으로 받고, 이어서 Dockerfile을 쓴다.
func TestStaticScriptClonesTheCommitThenWritesTheDockerfile(t *testing.T) {
	needTools(t)
	repo := newCloneRepo(t)
	old := repo.commits[1]
	r := runScript(t, staticScript, "REPO_URL="+repo.url, "REF="+old, "DOCKERFILE_B64=RlJPTSBuZ2lueAo=") // "FROM nginx\n"
	if r.err != nil {
		t.Fatalf("script failed: %v\n%s", r.err, r.out)
	}
	if got := markerLines(r.out); len(got) != 1 || got[0] != SourceMarker+old {
		t.Fatalf("marker lines %q", got)
	}
	if body, _ := os.ReadFile(filepath.Join(r.ws, "df", "Dockerfile")); string(body) != "FROM nginx\n" {
		t.Fatalf("dockerfile %q", body)
	}
	if strings.Index(r.out, SourceMarker) > strings.Index(r.out, "===KODEPLOY_DOCKERFILE_START===") {
		t.Fatal("the source line must come before any repository-derived output")
	}
}

// private repo 토큰(insteadOf)이 커밋 경로의 fetch에도, 브랜치 경로의 clone에도 걸린다.
// 네트워크에 나가지 않게 프록시를 닫힌 포트로 두고, git이 원격 도우미에 넘긴 주소를 GIT_TRACE로 본다.
func TestTokenRewriteAppliesToFetchAndClone(t *testing.T) {
	needTools(t)
	const token = "tok-insteadof-check"
	for _, ref := range []string{strings.Repeat("0123456789", 4), "main"} {
		trace := filepath.Join(t.TempDir(), "trace")
		r := runScript(t, cloneScript,
			"REPO_URL=https://github.com/acme/app", "REF="+ref, "GIT_AUTH_TOKEN="+token,
			"GIT_TRACE="+trace, "https_proxy=http://127.0.0.1:1", "HTTPS_PROXY=http://127.0.0.1:1")
		if r.err == nil {
			t.Fatalf("%s: expected the network step to fail", ref)
		}
		tr, err := os.ReadFile(trace)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(tr), "remote-https origin https://x-access-token:"+token+"@github.com/acme/app") {
			t.Fatalf("%s: token URL not used for the transfer:\n%s", ref, tr)
		}
		if strings.Contains(r.out, token) {
			t.Fatalf("%s: token leaked into the build log:\n%s", ref, r.out)
		}

		// 대조군: 토큰이 없으면 같은 계측에서 원래 주소가 보여야 한다 (계측이 rewrite를 실제로 구분하는지)
		plain := filepath.Join(t.TempDir(), "trace")
		_ = runScript(t, cloneScript, "REPO_URL=https://github.com/acme/app", "REF="+ref,
			"GIT_TRACE="+plain, "https_proxy=http://127.0.0.1:1", "HTTPS_PROXY=http://127.0.0.1:1")
		pt, err := os.ReadFile(plain)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(string(pt), "remote-https origin https://github.com/acme/app") || strings.Contains(string(pt), "x-access-token") {
			t.Fatalf("%s: control trace without a token:\n%s", ref, pt)
		}
	}
}
