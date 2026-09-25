package gitops

import (
	"context"
	"errors"
	"fmt"
)

// 409 재시도 상한 (지시서 4-7)
const maxAttempts = 5

// Contents는 Writer가 쓰는 GitHub 기능이다 (테스트에서 httptest로 GitHub를 흉내 낸다).
type Contents interface {
	Get(ctx context.Context, path string) (*File, error)
	Put(ctx context.Context, path string, content []byte, sha, message string) (string, error)
	Delete(ctx context.Context, path, sha, message string) (string, error)
	Head(ctx context.Context) (string, error)
}

// Change는 커밋 요청 하나다.
type Change struct {
	Namespace string
	Message   string
	Delete    bool
	// Apply는 현재 값(파일이 없으면 nil)으로 커밋할 값을 만든다. Delete면 쓰지 않는다.
	Apply func(cur *Values) (*Values, error)
}

type Result struct {
	CommitSHA string  // 건너뛰었으면 브랜치 끝 sha, 지울 파일이 없었으면 ""
	Skipped   bool    // 바뀐 게 없어 커밋하지 않음
	Values    *Values // 커밋된(또는 이미 있던) 값. Delete면 nil
}

type request struct {
	ctx   context.Context
	ch    Change
	reply chan reply
}

type reply struct {
	res Result
	err error
}

// Writer는 kodeploy-apps 쓰기를 고루틴 하나에서 순서대로 처리한다.
type Writer struct {
	gh   Contents
	reqs chan request
}

func NewWriter(gh Contents) *Writer {
	return &Writer{gh: gh, reqs: make(chan request)}
}

// Run은 ctx가 끝날 때까지 요청을 처리한다.
func (w *Writer) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case r := <-w.reqs:
			res, err := w.apply(r.ctx, r.ch)
			r.reply <- reply{res, err}
		}
	}
}

// Commit은 요청을 writer 고루틴에 넘기고 결과를 기다린다.
func (w *Writer) Commit(ctx context.Context, ch Change) (Result, error) {
	r := request{ctx: ctx, ch: ch, reply: make(chan reply, 1)}
	select {
	case w.reqs <- r:
	case <-ctx.Done():
		return Result{}, context.Cause(ctx)
	}
	select {
	case rep := <-r.reply:
		return rep.res, rep.err
	case <-ctx.Done():
		return Result{}, context.Cause(ctx)
	}
}

func (w *Writer) apply(ctx context.Context, ch Change) (Result, error) {
	path, err := Path(ch.Namespace)
	if err != nil {
		return Result{}, err
	}
	var lastErr error
	for attempt := 0; attempt < maxAttempts; attempt++ {
		if err := ctx.Err(); err != nil {
			return Result{}, context.Cause(ctx)
		}
		res, err := w.once(ctx, path, ch)
		if err == nil {
			return res, nil
		}
		if !errors.Is(err, ErrConflict) {
			return Result{}, err
		}
		lastErr = err
	}
	return Result{}, fmt.Errorf("gave up after %d attempts: %w", maxAttempts, lastErr)
}

func (w *Writer) once(ctx context.Context, path string, ch Change) (Result, error) {
	f, err := w.gh.Get(ctx, path)
	if err != nil {
		return Result{}, err
	}

	if ch.Delete {
		if f == nil {
			return Result{Skipped: true}, nil // 이미 없다 = 성공
		}
		sha, err := w.gh.Delete(ctx, path, f.SHA, ch.Message)
		return Result{CommitSHA: sha}, err
	}

	var cur *Values
	if f != nil {
		if cur, err = Parse(f.Content); err != nil {
			return Result{}, err
		}
	}
	next, err := ch.Apply(cur)
	if err != nil {
		return Result{}, err
	}
	if cur != nil && cur.Equal(next) {
		head, err := w.gh.Head(ctx)
		if err != nil {
			return Result{}, err
		}
		return Result{CommitSHA: head, Skipped: true, Values: cur}, nil
	}
	content, err := next.Marshal()
	if err != nil {
		return Result{}, err
	}
	sha := ""
	if f != nil {
		sha = f.SHA
	}
	commit, err := w.gh.Put(ctx, path, content, sha, ch.Message)
	if err != nil {
		return Result{}, err
	}
	return Result{CommitSHA: commit, Values: next}, nil
}
