package cmd

import (
	"context"
	"os"
	"sync"

	"golang.org/x/term"

	"github.com/kodeploy/kodeploy/cli/internal/shell"
)

// Terminal은 셸·DB 접속이 쓰는 터미널이다. 테스트는 가짜를 넣는다.
type Terminal interface {
	// IsTerminal은 stdin과 stdout이 모두 터미널인가.
	IsTerminal() bool
	// Size는 지금 창 크기다. 모르면 80x24.
	Size() shell.Size
	// MakeRaw는 stdin을 raw 모드로 바꾸고 되돌리는 함수를 준다 (여러 번 불러도 한 번만 되돌린다).
	MakeRaw() (restore func(), err error)
	// Resized는 창 크기가 바뀔 때마다 새 크기를 보낸다. ctx가 끝나면 그만 보낸다.
	Resized(ctx context.Context) <-chan shell.Size
}

type osTerminal struct{ in, out *os.File }

func (t osTerminal) IsTerminal() bool {
	return term.IsTerminal(int(t.in.Fd())) && term.IsTerminal(int(t.out.Fd()))
}

func (t osTerminal) Size() shell.Size {
	w, h, err := term.GetSize(int(t.out.Fd()))
	if err != nil || w <= 0 || h <= 0 {
		return shell.Size{Cols: 80, Rows: 24}
	}
	return shell.Size{Cols: w, Rows: h}
}

func (t osTerminal) MakeRaw() (func(), error) {
	fd := int(t.in.Fd())
	st, err := term.MakeRaw(fd)
	if err != nil {
		return func() {}, err
	}
	var once sync.Once
	return func() { once.Do(func() { _ = term.Restore(fd, st) }) }, nil
}

func (t osTerminal) Resized(ctx context.Context) <-chan shell.Size {
	sig := notifyResize()
	if sig == nil {
		return nil
	}
	out := make(chan shell.Size, 1)
	go func() {
		defer stopResize(sig)
		for {
			select {
			case <-ctx.Done():
				return
			case <-sig:
				select {
				case out <- t.Size():
				default: // 아직 못 보낸 크기가 있으면 다음 신호에서 최신 크기로 보낸다
				}
			}
		}
	}()
	return out
}
