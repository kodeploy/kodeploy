// Package ticket은 공개 문 접속용 1회용 티켓을 발급·저장·소비한다 (지시서 4-1).
//
// 티켓은 32바이트 crypto/rand 값의 base64url이다. 저장 키는 sha256(티켓)이고 원문은 저장하지 않는다.
// 원문은 발급 응답으로만 나간다. 꺼내는 즉시 지우고(1회용), TTL이 지나면 만료된다.
package ticket

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"io"
	"sync"
	"sync/atomic"
	"time"
)

// Info는 티켓에 묶이는 내용이다. 원문은 여기에 없다.
type Info struct {
	UserID    string // 기록용 (판단에 쓰지 않는다)
	AppID     string // 기록용 (판단에 쓰지 않는다)
	App       string // 앱 이름 — target=app일 때 Pod 선택에 쓴다
	Target    string // app | db | redis
	Op        string // shell | console
	Cols      uint16
	Rows      uint16
	IssuedAt  time.Time
	ExpiresAt time.Time
}

var ErrFull = errors.New("ticket store is full")

// Store는 메모리 티켓 저장소다. 같은 Pod 안에서만 유효하다.
type Store struct {
	mu    sync.Mutex
	items map[[sha256.Size]byte]Info
	ttl   time.Duration
	max   int
	now   func() time.Time
	rand  io.Reader

	issued, consumed, expired atomic.Int64
}

// New는 저장소를 만든다.
func New(ttl time.Duration, max int) *Store {
	return &Store{items: map[[sha256.Size]byte]Info{}, ttl: ttl, max: max, now: time.Now, rand: rand.Reader}
}

// Hash는 감사 기록용 sha256 hex다.
func Hash(ticket string) string {
	h := sha256.Sum256([]byte(ticket))
	return hex.EncodeToString(h[:])
}

// Issue는 새 티켓을 만들어 저장하고 원문을 돌려준다. IssuedAt·ExpiresAt은 여기서 채운다.
func (s *Store) Issue(info Info) (string, Info, error) {
	var raw [32]byte
	if _, err := io.ReadFull(s.rand, raw[:]); err != nil {
		return "", Info{}, err
	}
	t := base64.RawURLEncoding.EncodeToString(raw[:])
	now := s.now()
	info.IssuedAt, info.ExpiresAt = now, now.Add(s.ttl)

	s.mu.Lock()
	defer s.mu.Unlock()
	s.sweepLocked(now)
	if len(s.items) >= s.max {
		return "", Info{}, ErrFull
	}
	s.items[sha256.Sum256([]byte(t))] = info
	s.issued.Add(1)
	return t, info, nil
}

// Consume은 티켓을 꺼내고 지운다. 없거나 만료됐으면 false.
func (s *Store) Consume(ticket string) (Info, bool) {
	if ticket == "" {
		return Info{}, false
	}
	key := sha256.Sum256([]byte(ticket))
	s.mu.Lock()
	info, ok := s.items[key]
	delete(s.items, key)
	s.mu.Unlock()
	if !ok {
		return Info{}, false
	}
	if !s.now().Before(info.ExpiresAt) {
		s.expired.Add(1)
		return Info{}, false
	}
	s.consumed.Add(1)
	return info, true
}

// Sweep은 만료된 항목을 지우고 지운 수를 돌려준다.
func (s *Store) Sweep() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.sweepLocked(s.now())
}

func (s *Store) sweepLocked(now time.Time) int {
	n := 0
	for k, v := range s.items {
		if !now.Before(v.ExpiresAt) {
			delete(s.items, k)
			n++
		}
	}
	s.expired.Add(int64(n))
	return n
}

// Run은 ctx가 끝날 때까지 every마다 Sweep한다.
func (s *Store) Run(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.Sweep()
		}
	}
}

// Len은 저장된 항목 수다.
func (s *Store) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.items)
}

// Stats는 지표용 누계다.
func (s *Store) Stats() (issued, consumed, expired int64) {
	return s.issued.Load(), s.consumed.Load(), s.expired.Load()
}
