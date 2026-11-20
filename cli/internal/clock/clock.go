// Package clock는 시간을 한 곳으로 모은다. 테스트는 Fake로 기다림 없이 시간을 흘린다.
package clock

import (
	"context"
	"sync"
	"time"
)

type Clock interface {
	Now() time.Time
	// Sleep은 d만큼 기다린다. ctx가 먼저 끝나면 ctx.Err()를 돌려준다.
	Sleep(ctx context.Context, d time.Duration) error
	// WithDeadline은 진짜 시계면 context.WithDeadline이다. 가짜 시계는 실제 시간으로 끊지 않는다
	// (가짜 시각은 실제 시각과 상관없어서, 시간 제한은 Now()로 따로 확인한다).
	WithDeadline(ctx context.Context, t time.Time) (context.Context, context.CancelFunc)
}

type Real struct{}

func (Real) Now() time.Time { return time.Now() }

func (Real) Sleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return ctx.Err()
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-t.C:
		return nil
	}
}

func (Real) WithDeadline(ctx context.Context, t time.Time) (context.Context, context.CancelFunc) {
	return context.WithDeadline(ctx, t)
}

// Fake는 Sleep을 부르면 그만큼 시각만 앞으로 옮긴다. Sleeps에 부른 간격을 남긴다.
type Fake struct {
	mu     sync.Mutex
	now    time.Time
	sleeps []time.Duration
	// OnSleep이 있으면 Sleep마다 (시각을 옮긴 뒤) 부른다 — 테스트가 특정 시점에 끼어들 때 쓴다.
	OnSleep func(now time.Time)
}

func NewFake(start time.Time) *Fake { return &Fake{now: start} }

func (f *Fake) Now() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.now
}

func (f *Fake) Sleep(ctx context.Context, d time.Duration) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	f.mu.Lock()
	if d > 0 {
		f.now = f.now.Add(d)
	}
	f.sleeps = append(f.sleeps, d)
	now, hook := f.now, f.OnSleep
	f.mu.Unlock()
	if hook != nil {
		hook(now)
	}
	return ctx.Err()
}

func (f *Fake) WithDeadline(ctx context.Context, _ time.Time) (context.Context, context.CancelFunc) {
	return context.WithCancel(ctx)
}

func (f *Fake) Advance(d time.Duration) {
	f.mu.Lock()
	f.now = f.now.Add(d)
	f.mu.Unlock()
}

func (f *Fake) Sleeps() []time.Duration {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]time.Duration(nil), f.sleeps...)
}
