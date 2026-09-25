// Package registry는 GHCR에 이미지가 실제로 있는지 확인하고, 태그로 digest를 찾는다 (지시서 4-2 6·8).
package registry

import (
	"context"
	"errors"
	"fmt"
	"net/http"

	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/v1/remote"
	"github.com/google/go-containerregistry/pkg/v1/remote/transport"
)

var ErrNotFound = errors.New("image not found in registry")

type Client struct {
	auth     authn.Authenticator
	nameOpts []name.Option
	tr       http.RoundTripper
}

// New는 GHCR 읽기 토큰으로 인증하는 클라이언트다.
func New(user, token string) *Client {
	return &Client{auth: &authn.Basic{Username: user, Password: token}}
}

// NewInsecure는 테스트용 (평문 HTTP 레지스트리, 인증 없음).
func NewInsecure(tr http.RoundTripper) *Client {
	return &Client{auth: authn.Anonymous, nameOpts: []name.Option{name.Insecure}, tr: tr}
}

func (c *Client) opts(ctx context.Context) []remote.Option {
	o := []remote.Option{remote.WithAuth(c.auth), remote.WithContext(ctx)}
	if c.tr != nil {
		o = append(o, remote.WithTransport(c.tr))
	}
	return o
}

// Exists는 <repo>@<digest> 매니페스트가 있는지 HEAD로 본다. 없으면 ErrNotFound.
func (c *Client) Exists(ctx context.Context, repo, digest string) error {
	ref, err := name.NewDigest(repo+"@"+digest, c.nameOpts...)
	if err != nil {
		return fmt.Errorf("bad image reference: %w", err)
	}
	d, err := remote.Head(ref, c.opts(ctx)...)
	if err != nil {
		return wrap(err)
	}
	if d.Digest.String() != digest {
		return fmt.Errorf("registry returned digest %s, want %s", d.Digest, digest)
	}
	return nil
}

// Resolve는 <repo>:<tag>의 digest("sha256:...")를 돌려준다 (마커를 못 본 경우의 폴백).
func (c *Client) Resolve(ctx context.Context, repo, tag string) (string, error) {
	ref, err := name.NewTag(repo+":"+tag, c.nameOpts...)
	if err != nil {
		return "", fmt.Errorf("bad image reference: %w", err)
	}
	d, err := remote.Head(ref, c.opts(ctx)...)
	if err != nil {
		return "", wrap(err)
	}
	return d.Digest.String(), nil
}

func wrap(err error) error {
	var te *transport.Error
	if errors.As(err, &te) && te.StatusCode == http.StatusNotFound {
		return ErrNotFound
	}
	return err
}
