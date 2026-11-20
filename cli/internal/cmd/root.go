// Package cmd는 kodeploy 명령(cobra)이다. Run은 프로세스 밖 의존(환경변수, 출력, 시계, HTTP)을 받아서
// 테스트가 명령을 프로세스 안에서 그대로 실행하고 exit code를 확인할 수 있다.
package cmd

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/spf13/cobra"

	"github.com/kodeploy/kodeploy/cli/internal/clock"
)

// 빌드할 때 -ldflags "-X github.com/kodeploy/kodeploy/cli/internal/cmd.version=..."로 넣는다.
var version = "dev"

// exit code (계약 5절, README)
const (
	ExitOK          = 0
	ExitFailed      = 1
	ExitUsage       = 2
	ExitSuperseded  = 3
	ExitTimeout     = 4
	ExitAuth        = 125
	ExitInterrupted = 130
)

// Deps는 프로세스 밖 의존이다. 테스트는 가짜 시계·httptest 클라이언트·고정 환경변수를 넣는다.
type Deps struct {
	Getenv func(string) string
	Stdout io.Writer
	Stderr io.Writer
	Clock  clock.Clock
	HTTP   *http.Client
	NewKey func() string // GITHUB_RUN_ID가 없을 때의 이름표 (cli-<무작위 UUID>)
}

// exitError는 명령이 정한 exit code다. 메시지 출력은 명령이 이미 끝냈다.
type exitError struct{ code int }

func (e *exitError) Error() string { return fmt.Sprintf("exit %d", e.code) }

func exit(code int) error {
	if code == ExitOK {
		return nil
	}
	return &exitError{code: code}
}

func newRoot(d *Deps) *cobra.Command {
	root := &cobra.Command{
		Use:           "kodeploy",
		Short:         "KoDeploy 재배포 CLI",
		SilenceUsage:  true,
		SilenceErrors: true,
	}
	root.SetOut(d.Stdout)
	root.SetErr(d.Stderr)
	g := &globalFlags{}
	g.register(root)
	root.AddCommand(newDeployCmd(d, g))
	return root
}

// Run은 명령을 실행하고 exit code를 돌려준다.
func Run(ctx context.Context, args []string, d Deps) int {
	root := newRoot(&d)
	root.SetArgs(args)
	err := root.ExecuteContext(ctx)
	if err == nil {
		return ExitOK
	}
	var ee *exitError
	if errors.As(err, &ee) {
		return ee.code
	}
	// 플래그·인자 오류 (cobra가 만든 오류)
	fmt.Fprintf(d.Stderr, "kodeploy: %v\n", err)
	fmt.Fprintln(d.Stderr, "kodeploy --help로 사용법을 볼 수 있어요")
	return ExitUsage
}

// Main은 진짜 의존으로 Run을 부른다. Ctrl+C(SIGINT)·SIGTERM은 ctx를 끝낸다 — 기다리기만 멈추고 서버 빌드는 그대로 둔다.
func Main() int {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return Run(ctx, os.Args[1:], Deps{
		Getenv: os.Getenv,
		Stdout: os.Stdout,
		Stderr: os.Stderr,
		Clock:  clock.Real{},
		HTTP:   &http.Client{Timeout: 30 * time.Second},
		NewKey: randomKey,
	})
}
