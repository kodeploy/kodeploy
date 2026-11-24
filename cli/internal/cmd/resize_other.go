//go:build !unix

package cmd

import "os"

// SIGWINCH가 없는 OS — 처음 크기만 보낸다.
func notifyResize() chan os.Signal { return nil }

func stopResize(chan os.Signal) {}
