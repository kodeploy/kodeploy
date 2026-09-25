package registry

import (
	"context"
	"errors"
	"io"
	"log"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/registry"
	"github.com/google/go-containerregistry/pkg/v1/random"
	"github.com/google/go-containerregistry/pkg/v1/remote"
)

func TestExistsAndResolve(t *testing.T) {
	srv := httptest.NewServer(registry.New(registry.Logger(log.New(io.Discard, "", 0))))
	defer srv.Close()
	host := strings.TrimPrefix(srv.URL, "http://")
	repo := host + "/yuntyu01/d6d8b759/app"

	img, err := random.Image(256, 1)
	if err != nil {
		t.Fatal(err)
	}
	tag, _ := name.NewTag(repo+":3f9a2c1d", name.Insecure)
	if err := remote.Write(tag, img); err != nil {
		t.Fatal(err)
	}
	dg, _ := img.Digest()

	c := NewInsecure(nil)
	ctx := context.Background()
	if err := c.Exists(ctx, repo, dg.String()); err != nil {
		t.Fatalf("exists: %v", err)
	}
	got, err := c.Resolve(ctx, repo, "3f9a2c1d")
	if err != nil || got != dg.String() {
		t.Fatalf("resolve: %q %v", got, err)
	}

	missing := "sha256:" + strings.Repeat("0", 64)
	if err := c.Exists(ctx, repo, missing); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing digest: %v", err)
	}
	if _, err := c.Resolve(ctx, repo, "nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("missing tag: %v", err)
	}
	if err := c.Exists(ctx, repo, "sha256:xyz"); err == nil || errors.Is(err, ErrNotFound) {
		t.Fatalf("bad digest should be a reference error: %v", err)
	}
}
