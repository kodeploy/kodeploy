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
	if c.MaxSessions != 3 || c.PingInterval != 30*time.Second {
		t.Errorf("sessions/ping: %d %v", c.MaxSessions, c.PingInterval)
	}
	if c.RestoreMaxBytes != 200*1024*1024 || c.RestoreWorkdir != "/tmp/kodeploy-restore" {
		t.Errorf("restore: %d %s", c.RestoreMaxBytes, c.RestoreWorkdir)
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
		{"bad int", map[string]string{"EXEC_NAMESPACE": "x", "MAX_SESSIONS": "0"}, goodKey, nil, "MAX_SESSIONS"},
		{"relative workdir", map[string]string{"EXEC_NAMESPACE": "x", "RESTORE_WORKDIR": "tmp/x"}, goodKey, nil, "RESTORE_WORKDIR"},
		{"root workdir", map[string]string{"EXEC_NAMESPACE": "x", "RESTORE_WORKDIR": "/"}, goodKey, nil, "RESTORE_WORKDIR"},
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
