// builder는 core 대신 빌드 Job을 돌리고 결과 이미지를 kodeploy-apps에 커밋하는 서비스다.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/api"
	"github.com/kodeploy/kodeploy/builder/internal/config"
	"github.com/kodeploy/kodeploy/builder/internal/contract"
)

// SIGTERM 후 종료까지 쓰는 시간 (지시서 4-6)
const shutdownGrace = 20 * time.Second

// 3단계(run)에서 실제 디스패처로 바뀐다.
type unwired struct{}

func (unwired) Submit(*contract.DeployRequest) error { return errors.New("dispatcher not wired yet") }
func (unwired) Cancel(string) error                  { return contract.ErrNotFound }

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	cfg, err := config.Load(os.Getenv)
	if err != nil {
		log.Error("config", "err", err)
		os.Exit(1)
	}

	srv := api.New(api.Options{
		Secret:     cfg.HMACSecret,
		Validator:  api.Validator{GHCRUser: cfg.GHCRUser},
		Dispatcher: unwired{},
		Logger:     log,
	})
	httpSrv := &http.Server{Addr: cfg.Listen, Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	errc := make(chan error, 1)
	go func() { errc <- httpSrv.ListenAndServe() }()
	log.Info("listening", "addr", cfg.Listen)

	select {
	case err := <-errc:
		log.Error("server", "err", err)
		os.Exit(1)
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
}
