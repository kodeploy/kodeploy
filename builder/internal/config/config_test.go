package config

import (
	"strings"
	"testing"
	"time"
)

func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func base() map[string]string {
	return map[string]string{
		"CORE_URL":     "http://kodeploy-core/",
		"HMAC_SECRET":  "s3cret",
		"GITHUB_TOKEN": "gh",
		"GHCR_TOKEN":   "ghcr",
		"GHCR_USER":    "YunTyu01",
	}
}

func TestDefaults(t *testing.T) {
	c, err := Load(env(base()))
	if err != nil {
		t.Fatal(err)
	}
	if c.Listen != ":8080" || c.GitOpsRepo != "kodeploy/kodeploy-apps" || c.GitOpsBranch != "main" {
		t.Errorf("listen/gitops defaults: %+v", c)
	}
	if c.BuildNamespace != "kodeploy-build" || c.ArgoCDNamespace != "argocd" {
		t.Errorf("namespace defaults: %q %q", c.BuildNamespace, c.ArgoCDNamespace)
	}
	if c.BuildKitImage != "moby/buildkit:v0.30.0-rootless" {
		t.Errorf("buildkit image: %q", c.BuildKitImage)
	}
	if c.BuildActiveDeadlineSeconds != 900 {
		t.Errorf("deadline default = %d, want 600+300", c.BuildActiveDeadlineSeconds)
	}
	if !c.EarlyTrigger {
		t.Error("early trigger should default to on")
	}
	if c.ArgoWaitTimeout != 8*time.Minute || c.LogBatchInterval != time.Second || c.MaxActiveBuilds != 3 {
		t.Errorf("tuning defaults: %v %v %d", c.ArgoWaitTimeout, c.LogBatchInterval, c.MaxActiveBuilds)
	}
	if c.CoreURL != "http://kodeploy-core" {
		t.Errorf("core url trailing slash not trimmed: %q", c.CoreURL)
	}
	if c.GHCRUser != "yuntyu01" {
		t.Errorf("ghcr user not lowercased: %q", c.GHCRUser)
	}
}

func TestDeadlineRule(t *testing.T) {
	m := base()
	m["BUILD_TIMEOUT_SECONDS"] = "100"
	c, err := Load(env(m))
	if err != nil {
		t.Fatal(err)
	}
	if c.BuildActiveDeadlineSeconds != 400 {
		t.Errorf("deadline = %d, want 100+300", c.BuildActiveDeadlineSeconds)
	}
	m["BUILD_ACTIVE_DEADLINE_SECONDS"] = "1234"
	c, err = Load(env(m))
	if err != nil {
		t.Fatal(err)
	}
	if c.BuildActiveDeadlineSeconds != 1234 {
		t.Errorf("explicit deadline ignored: %d", c.BuildActiveDeadlineSeconds)
	}
}

func TestEarlyTriggerOff(t *testing.T) {
	for _, v := range []string{"false", "0", "no"} {
		m := base()
		m["EARLY_TRIGGER"] = v
		c, err := Load(env(m))
		if err != nil {
			t.Fatal(err)
		}
		if c.EarlyTrigger {
			t.Errorf("EARLY_TRIGGER=%q should be off", v)
		}
	}
}

func TestRequiredAndInvalid(t *testing.T) {
	_, err := Load(env(map[string]string{}))
	if err == nil {
		t.Fatal("expected error")
	}
	for _, k := range []string{"CORE_URL", "HMAC_SECRET", "GITHUB_TOKEN", "GHCR_TOKEN", "GHCR_USER"} {
		if !strings.Contains(err.Error(), k) {
			t.Errorf("missing %s not reported: %v", k, err)
		}
	}

	bad := map[string]string{
		"MAX_ACTIVE_BUILDS":  "0",
		"ARGO_WAIT_TIMEOUT":  "soon",
		"LOG_BATCH_INTERVAL": "-1s",
		"GITOPS_REPO":        "kodeploy-apps",
		"CORE_URL":           "kodeploy-core",
	}
	for k, v := range bad {
		m := base()
		m[k] = v
		if _, err := Load(env(m)); err == nil {
			t.Errorf("%s=%q accepted", k, v)
		}
	}
}
