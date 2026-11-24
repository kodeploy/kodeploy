//go:build unix

package cmd

import (
	"os"
	"os/signal"
	"syscall"
)

// 창 크기 변경은 SIGWINCH로 온다.
func notifyResize() chan os.Signal {
	c := make(chan os.Signal, 1)
	signal.Notify(c, syscall.SIGWINCH)
	return c
}

func stopResize(c chan os.Signal) { signal.Stop(c) }
