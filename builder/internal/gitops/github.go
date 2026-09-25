package gitops

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ErrConflict는 파일 sha가 바뀌어 쓰기가 거절된 경우다 (다시 읽고 재시도).
var ErrConflict = errors.New("contents changed concurrently")

// GitHub는 Contents API를 net/http로 직접 부른다. 토큰은 헤더에만 싣고 오류 메시지에는 넣지 않는다.
type GitHub struct {
	BaseURL string // https://api.github.com (테스트에서 바꾼다)
	Repo    string // owner/name
	Branch  string
	token   string
	hc      *http.Client
}

func NewGitHub(repo, branch, token string) *GitHub {
	return &GitHub{BaseURL: "https://api.github.com", Repo: repo, Branch: branch, token: token,
		hc: &http.Client{Timeout: 30 * time.Second}}
}

type File struct {
	Content []byte
	SHA     string
}

func (g *GitHub) do(ctx context.Context, method, path string, body any, accept string) (*http.Response, []byte, error) {
	var rd io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, nil, err
		}
		rd = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, strings.TrimRight(g.BaseURL, "/")+path, rd)
	if err != nil {
		return nil, nil, err
	}
	req.Header.Set("Authorization", "Bearer "+g.token)
	req.Header.Set("Accept", accept)
	req.Header.Set("X-GitHub-Api-Version", "2022-11-28")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := g.hc.Do(req)
	if err != nil {
		return nil, nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	return resp, data, err
}

func (g *GitHub) contentsPath(path string) string {
	return "/repos/" + g.Repo + "/contents/" + path
}

func apiError(op string, resp *http.Response, data []byte) error {
	var msg struct {
		Message string `json:"message"`
	}
	_ = json.Unmarshal(data, &msg)
	return fmt.Errorf("github %s: %d %s", op, resp.StatusCode, msg.Message)
}

// Get은 파일 내용과 sha를 읽는다. 없으면 (nil, nil).
func (g *GitHub) Get(ctx context.Context, path string) (*File, error) {
	resp, data, err := g.do(ctx, http.MethodGet, g.contentsPath(path)+"?ref="+url.QueryEscape(g.Branch), nil, "application/vnd.github+json")
	if err != nil {
		return nil, err
	}
	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusNotFound:
		return nil, nil
	default:
		return nil, apiError("get "+path, resp, data)
	}
	var out struct {
		SHA      string `json:"sha"`
		Content  string `json:"content"`
		Encoding string `json:"encoding"`
		Type     string `json:"type"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, err
	}
	if out.Type != "file" || out.Encoding != "base64" {
		return nil, fmt.Errorf("github get %s: not a base64 file (type=%s)", path, out.Type)
	}
	content, err := base64.StdEncoding.DecodeString(strings.ReplaceAll(out.Content, "\n", ""))
	if err != nil {
		return nil, err
	}
	return &File{Content: content, SHA: out.SHA}, nil
}

type commitResponse struct {
	Commit struct {
		SHA string `json:"sha"`
	} `json:"commit"`
}

// Put은 파일을 만들거나 바꾼다. sha는 현재 파일 sha(새 파일이면 "").
func (g *GitHub) Put(ctx context.Context, path string, content []byte, sha, message string) (string, error) {
	body := map[string]string{
		"message": message,
		"content": base64.StdEncoding.EncodeToString(content),
		"branch":  g.Branch,
	}
	if sha != "" {
		body["sha"] = sha
	}
	return g.commit(ctx, http.MethodPut, path, body)
}

// Delete는 파일을 지운다.
func (g *GitHub) Delete(ctx context.Context, path, sha, message string) (string, error) {
	return g.commit(ctx, http.MethodDelete, path, map[string]string{"message": message, "sha": sha, "branch": g.Branch})
}

func (g *GitHub) commit(ctx context.Context, method, path string, body map[string]string) (string, error) {
	resp, data, err := g.do(ctx, method, g.contentsPath(path), body, "application/vnd.github+json")
	if err != nil {
		return "", err
	}
	switch resp.StatusCode {
	case http.StatusOK, http.StatusCreated:
	case http.StatusConflict, http.StatusUnprocessableEntity, http.StatusNotFound:
		// 409: sha 불일치, 422: sha 누락(그 사이 누가 만듦), 404(삭제): 그 사이 누가 지움. 다시 읽고 재시도한다.
		return "", fmt.Errorf("%w: %w", ErrConflict, apiError(strings.ToLower(method)+" "+path, resp, data))
	default:
		return "", apiError(strings.ToLower(method)+" "+path, resp, data)
	}
	var out commitResponse
	if err := json.Unmarshal(data, &out); err != nil {
		return "", err
	}
	if out.Commit.SHA == "" {
		return "", errors.New("github: commit sha missing in response")
	}
	return out.Commit.SHA, nil
}

// Head는 브랜치 끝 커밋 sha다 (바꿀 게 없어 커밋을 건너뛴 경우 Argo 대기 기준으로 쓴다).
func (g *GitHub) Head(ctx context.Context) (string, error) {
	resp, data, err := g.do(ctx, http.MethodGet, "/repos/"+g.Repo+"/commits/"+url.PathEscape(g.Branch), nil, "application/vnd.github.sha")
	if err != nil {
		return "", err
	}
	if resp.StatusCode != http.StatusOK {
		return "", apiError("head", resp, data)
	}
	return strings.TrimSpace(string(data)), nil
}

// IsAncestor는 base 커밋이 head에 포함돼 있는지다 (base == head 포함).
// 다른 앱 커밋이 뒤따라 들어오면 Argo가 보고하는 revision이 우리 커밋보다 앞서 있기 때문에 필요하다.
func (g *GitHub) IsAncestor(ctx context.Context, base, head string) (bool, error) {
	if base == head {
		return true, nil
	}
	resp, data, err := g.do(ctx, http.MethodGet, "/repos/"+g.Repo+"/compare/"+url.PathEscape(base)+"..."+url.PathEscape(head)+"?per_page=1", nil, "application/vnd.github+json")
	if err != nil {
		return false, err
	}
	if resp.StatusCode != http.StatusOK {
		return false, apiError("compare", resp, data)
	}
	var out struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		return false, err
	}
	return out.Status == "ahead" || out.Status == "identical", nil
}
