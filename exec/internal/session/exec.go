package session

import (
	"context"
	"errors"
	"io"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/httpstream"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/remotecommand"
	utilexec "k8s.io/client-go/util/exec"

	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/proto"
	"github.com/kodeploy/kodeploy/exec/internal/target"
)

// Exec는 원격 exec 한 번이다. 없는 스트림은 nil.
type Exec struct {
	Pod, Container string
	Command        []string
	TTY            bool
	Stdin          io.Reader
	Stdout, Stderr io.Writer
	Size           remotecommand.TerminalSizeQueue
}

// Runner는 원격 exec를 돌린다. 운영은 KubeRunner, 테스트는 가짜.
// 명령이 0이 아닌 코드로 끝나면 ExitCode로 읽히는 오류를 돌려준다.
type Runner interface {
	Run(ctx context.Context, e Exec) error
}

// ExitCode는 Runner 오류에서 exit code를 꺼낸다. 명령이 끝나지 못한 오류면 false.
func ExitCode(err error) (int, bool) {
	var ee utilexec.ExitError
	if errors.As(err, &ee) && ee.Exited() {
		return ee.ExitStatus(), true
	}
	return 0, false
}

// ExecJob은 명령 하나를 돌리고 K8s와 같은 모양의 종료 상태를 채널 3에 싣는 작업이다.
func ExecJob(r Runner, t target.Target, cmd ops.Cmd) Job {
	return func(ctx context.Context, jio JobIO) (Outcome, error) {
		e := Exec{Pod: t.Pod, Container: t.Container, Command: cmd.Argv, TTY: cmd.TTY, Stdout: jio.Stdout}
		if cmd.Stdin {
			e.Stdin = jio.Stdin
		}
		if cmd.TTY {
			e.Size = jio.Size
		} else {
			e.Stderr = jio.Stderr
		}
		err := r.Run(ctx, e)
		if err == nil {
			return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(0)), ExitCode: 0}, nil
		}
		if code, ok := ExitCode(err); ok {
			return Outcome{Status: proto.MarshalStatus(proto.ExitStatus(code)), ExitCode: code}, nil
		}
		return Outcome{ExitCode: -1}, err
	}
}

// KubeRunner는 client-go remotecommand로 exec한다. WebSocket(v5)을 먼저 쓰고,
// 업그레이드에 실패하면 SPDY로 넘어간다(kubectl과 같은 규칙).
type KubeRunner struct {
	Config *rest.Config
	Client kubernetes.Interface
	NS     string
}

// Run은 exec 한 번을 끝까지 돌린다. ctx가 끝나면 스트림을 닫는다.
func (k *KubeRunner) Run(ctx context.Context, e Exec) error {
	req := k.Client.CoreV1().RESTClient().Post().
		Resource("pods").Namespace(k.NS).Name(e.Pod).SubResource("exec").
		VersionedParams(&corev1.PodExecOptions{
			Container: e.Container,
			Command:   e.Command,
			Stdin:     e.Stdin != nil,
			Stdout:    e.Stdout != nil,
			Stderr:    e.Stderr != nil,
			TTY:       e.TTY,
		}, scheme.ParameterCodec)

	wsExec, err := remotecommand.NewWebSocketExecutor(k.Config, "GET", req.URL().String())
	if err != nil {
		return err
	}
	spdyExec, err := remotecommand.NewSPDYExecutor(k.Config, "POST", req.URL())
	if err != nil {
		return err
	}
	ex, err := remotecommand.NewFallbackExecutor(wsExec, spdyExec, func(err error) bool {
		return httpstream.IsUpgradeFailure(err) || httpstream.IsHTTPSProxyError(err)
	})
	if err != nil {
		return err
	}
	return ex.StreamWithContext(ctx, remotecommand.StreamOptions{
		Stdin:             e.Stdin,
		Stdout:            e.Stdout,
		Stderr:            e.Stderr,
		Tty:               e.TTY,
		TerminalSizeQueue: e.Size,
	})
}
