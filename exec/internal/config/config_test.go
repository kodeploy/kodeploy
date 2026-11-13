package config

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func env(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func keyFile(b []byte, err error) func(string) ([]byte, error) {
	return func(string) ([]byte, error) { return b, err }
}

var goodKey = []byte(strings.Repeat("k", 32))

func TestDefaults(t *testing.T) {
	c, err := Load(env(map[string]string{"EXEC_NAMESPACE": "tenant-0a1b2c3d"}), keyFile(goodKey, nil))
	if err != nil {
		t.Fatal(err)
	}
	if c.PublicAddr != ":8080" || c.InternalAddr != ":8081" || c.MetricsAddr != ":9090" {
		t.Errorf("addrs: %+v", c)
	}
	if c.TicketTTL != 30*time.Second || c.TicketMax != 1000 {
		t.Errorf("ticket: %v %d", c.TicketTTL, c.TicketMax)
	}
	if c.TTYIdleTimeout != 15*time.Minute || c.TTYMax != 2*time.Hour || c.NonTTYMax != 30*time.Minute {
		t.Errorf("limits: %v %v %v", c.TTYIdleTimeout, c.TTYMax, c.NonTTYMax)
	}
	if c.MaxSessionsPublic != 3 || c.MaxSessionsInternal != 4 || c.PingInterval != 30*time.Second {
		t.Errorf("sessions/ping: %d %d %v", c.MaxSessionsPublic, c.MaxSessionsInternal, c.PingInterval)
	}
	if c.RestoreMaxBytes != 200*1024*1024 || c.RestoreWorkdir != "/tmp/kodeploy-restore" {
		t.Errorf("restore: %d %s", c.RestoreMaxBytes, c.RestoreWorkdir)
	}
}

func TestAllowedOrigins(t *testing.T) {
	c, err := Load(env(map[string]string{"EXEC_NAMESPACE": "x"}), keyFile(goodKey, nil))
	if err != nil || len(c.AllowedOrigins) != 0 {
		t.Fatalf("default must be empty (browsers blocked): %v %v", c, err)
	}
	c, err = Load(env(map[string]string{"EXEC_NAMESPACE": "x",
		"EXEC_ALLOWED_ORIGINS": " https://app.kodeploy.com, http://localhost:5173 ,,"}), keyFile(goodKey, nil))
	if err != nil || strings.Join(c.AllowedOrigins, "|") != "https://app.kodeploy.com|http://localhost:5173" {
		t.Fatalf("%v %v", c.AllowedOrigins, err)
	}
}

func TestErrors(t *testing.T) {
	cases := []struct {
		name string
		env  map[string]string
		key  []byte
		kerr error
		want string
	}{
		{"no namespace", map[string]string{}, goodKey, nil, "EXEC_NAMESPACE"},
		{"short key", map[string]string{"EXEC_NAMESPACE": "x"}, []byte("short"), nil, "at least 32"},
		{"key missing", map[string]string{"EXEC_NAMESPACE": "x"}, nil, errors.New("no such file"), "EXEC_HMAC_KEY_FILE"},
		{"bad duration", map[string]string{"EXEC_NAMESPACE": "x", "TTY_MAX": "soon"}, goodKey, nil, "TTY_MAX"},
		{"bad int", map[string]string{"EXEC_NAMESPACE": "x", "MAX_SESSIONS_PUBLIC": "0"}, goodKey, nil, "MAX_SESSIONS_PUBLIC"},
		{"bad int internal", map[string]string{"EXEC_NAMESPACE": "x", "MAX_SESSIONS_INTERNAL": "x"}, goodKey, nil, "MAX_SESSIONS_INTERNAL"},
		{"relative workdir", map[string]string{"EXEC_NAMESPACE": "x", "RESTORE_WORKDIR": "tmp/x"}, goodKey, nil, "RESTORE_WORKDIR"},
		{"root workdir", map[string]string{"EXEC_NAMESPACE": "x", "RESTORE_WORKDIR": "/"}, goodKey, nil, "RESTORE_WORKDIR"},
		{"origin with path", map[string]string{"EXEC_NAMESPACE": "x", "EXEC_ALLOWED_ORIGINS": "https://app.kodeploy.com/"}, goodKey, nil, "EXEC_ALLOWED_ORIGINS"},
		{"origin wildcard", map[string]string{"EXEC_NAMESPACE": "x", "EXEC_ALLOWED_ORIGINS": "*"}, goodKey, nil, "EXEC_ALLOWED_ORIGINS"},
		{"origin scheme", map[string]string{"EXEC_NAMESPACE": "x", "EXEC_ALLOWED_ORIGINS": "ftp://a.example"}, goodKey, nil, "EXEC_ALLOWED_ORIGINS"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, err := Load(env(c.env), keyFile(c.key, c.kerr))
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("got %v, want error containing %q", err, c.want)
			}
		})
	}
}
