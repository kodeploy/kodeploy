// Package config는 kodeploy-exec 설정을 환경변수에서 읽는다 (지시서 5절).
// 값은 차트가 고정하고, 앱 values에서는 아무것도 받지 않는다.
package config

import (
	"errors"
	"fmt"
	"path"
	"strconv"
	"strings"
	"time"
)

// 원본 snapshots.MAX_DUMP_BYTES (200 MiB)
const DefaultRestoreMaxBytes = 200 * 1024 * 1024

// HMAC 키 최소 길이. core가 secrets.token_bytes(32)로 만든다.
const minKeyBytes = 32

// Config는 설정 전체다. Load로 만든다.
type Config struct {
	Namespace string // 자기 ns. 이 밖은 다루지 않는다
	HMACKey   []byte

	PublicAddr   string
	InternalAddr string
	MetricsAddr  string

	TicketTTL time.Duration
	TicketMax int

	TTYIdleTimeout time.Duration // tty만
	TTYMax         time.Duration
	NonTTYMax      time.Duration
	// Pod당 동시 세션. 문마다 따로 센다: 터미널(공개)을 열어 둔 채 DB 콘솔·덤프(내부)를 써도 서로 막지 않게
	MaxSessionsPublic   int
	MaxSessionsInternal int
	PingInterval        time.Duration

	RestoreMaxBytes int64
	RestoreWorkdir  string // DB 컨테이너 안의 경로
}

// Load는 getenv로 설정을 읽고, HMAC 키는 readFile로 파일에서 읽는다(테스트에서 바꿔 끼울 수 있게 함수로 받는다).
func Load(getenv func(string) string, readFile func(string) ([]byte, error)) (*Config, error) {
	var errs []error
	str := func(key, def string) string {
		if v := strings.TrimSpace(getenv(key)); v != "" {
			return v
		}
		return def
	}
	integer := func(key string, def int64) int64 {
		v := strings.TrimSpace(getenv(key))
		if v == "" {
			return def
		}
		n, err := strconv.ParseInt(v, 10, 64)
		if err != nil || n <= 0 {
			errs = append(errs, fmt.Errorf("%s: not a positive integer", key))
			return def
		}
		return n
	}
	duration := func(key string, def time.Duration) time.Duration {
		v := strings.TrimSpace(getenv(key))
		if v == "" {
			return def
		}
		d, err := time.ParseDuration(v)
		if err != nil || d <= 0 {
			errs = append(errs, fmt.Errorf("%s: not a positive duration", key))
			return def
		}
		return d
	}

	c := &Config{
		Namespace:           strings.TrimSpace(getenv("EXEC_NAMESPACE")),
		PublicAddr:          str("PUBLIC_ADDR", ":8080"),
		InternalAddr:        str("INTERNAL_ADDR", ":8081"),
		MetricsAddr:         str("METRICS_ADDR", ":9090"),
		TicketTTL:           duration("TICKET_TTL", 30*time.Second),
		TicketMax:           int(integer("TICKET_MAX", 1000)),
		TTYIdleTimeout:      duration("TTY_IDLE_TIMEOUT", 15*time.Minute),
		TTYMax:              duration("TTY_MAX", 2*time.Hour),
		NonTTYMax:           duration("NONTTY_MAX", 30*time.Minute),
		MaxSessionsPublic:   int(integer("MAX_SESSIONS_PUBLIC", 3)),
		MaxSessionsInternal: int(integer("MAX_SESSIONS_INTERNAL", 4)),
		PingInterval:        duration("PING_INTERVAL", 30*time.Second),
		RestoreMaxBytes:     integer("RESTORE_MAX_BYTES", DefaultRestoreMaxBytes),
		RestoreWorkdir:      str("RESTORE_WORKDIR", "/tmp/kodeploy-restore"),
	}
	if c.Namespace == "" {
		errs = append(errs, errors.New("EXEC_NAMESPACE is required (downward API)"))
	}
	if !path.IsAbs(c.RestoreWorkdir) || path.Clean(c.RestoreWorkdir) != c.RestoreWorkdir || c.RestoreWorkdir == "/" {
		errs = append(errs, errors.New("RESTORE_WORKDIR must be a clean absolute path other than /"))
	}

	keyFile := str("EXEC_HMAC_KEY_FILE", "/etc/kodeploy-exec/hmac")
	key, err := readFile(keyFile)
	switch {
	case err != nil:
		errs = append(errs, fmt.Errorf("EXEC_HMAC_KEY_FILE: %w", err))
	case len(key) < minKeyBytes:
		errs = append(errs, fmt.Errorf("EXEC_HMAC_KEY_FILE: key must be at least %d bytes", minKeyBytes))
	default:
		c.HMACKey = key
	}

	if err := errors.Join(errs...); err != nil {
		return nil, err
	}
	return c, nil
}
