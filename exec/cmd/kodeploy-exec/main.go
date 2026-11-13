// kodeploy-exec는 테넌트 ns마다 하나씩 떠서, 그 ns 안의 Pod exec(터미널, DB 콘솔, 덤프, 복원)를 대신하는 서비스다.
// core는 pods/exec 권한 없이 이 서비스의 내부 문(8081)에 서명한 요청을 보내고,
// 웹은 core가 발급받아 준 1회용 티켓으로 공개 문(8080)에 붙는다.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/kodeploy/kodeploy/exec/internal/api"
	"github.com/kodeploy/kodeploy/exec/internal/audit"
	"github.com/kodeploy/kodeploy/exec/internal/config"
	"github.com/kodeploy/kodeploy/exec/internal/session"
	"github.com/kodeploy/kodeploy/exec/internal/target"
	"github.com/kodeploy/kodeploy/exec/internal/ticket"
)

// 리스너를 닫을 때 진행 중인 일반 HTTP 요청(티켓 발급)을 기다리는 시간
const listenerGrace = 10 * time.Second

// main은 serve를 돌리고, 오류로 끝나면 종료 코드 1로 나간다.
// stdout은 감사 기록 전용이라 운영 로그는 stderr로 쓴다.
func main() {
	log := slog.New(slog.NewJSONHandler(os.Stderr, nil))
	if err := serve(log); err != nil {
		log.Error("kodeploy-exec stopped", "err", err)
		os.Exit(1)
	}
}

// kubeConfig는 클러스터 안이면 SA 토큰, 밖이면 KUBECONFIG(개발용)를 쓴다.
func kubeConfig() (*rest.Config, error) {
	if c, err := rest.InClusterConfig(); err == nil {
		return c, nil
	}
	rules := clientcmd.NewDefaultClientConfigLoadingRules()
	return clientcmd.NewNonInteractiveDeferredLoadingClientConfig(rules, nil).ClientConfig()
}

func serve(log *slog.Logger) error {
	cfg, err := config.Load(os.Getenv, os.ReadFile)
	if err != nil {
		return err
	}
	rc, err := kubeConfig()
	if err != nil {
		return err
	}
	cs, err := kubernetes.NewForConfig(rc)
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	tickets := ticket.New(cfg.TicketTTL, cfg.TicketMax)
	sweepCtx, stopSweep := context.WithCancel(context.Background())
	defer stopSweep()
	go tickets.Run(sweepCtx, cfg.TicketTTL)

	var srv *api.Server
	metrics := audit.NewMetrics(func() (int, int) { return srv.ActiveByDoor() }, tickets.Stats)
	srv = api.New(api.Options{
		Namespace:           cfg.Namespace,
		Key:                 cfg.HMACKey,
		Tickets:             tickets,
		Finder:              target.NewFinder(cs, cfg.Namespace),
		Runner:              &session.KubeRunner{Config: rc, Client: cs, NS: cfg.Namespace},
		Audit:               audit.New(os.Stdout),
		Metrics:             metrics,
		TTYIdleTimeout:      cfg.TTYIdleTimeout,
		TTYMax:              cfg.TTYMax,
		NonTTYMax:           cfg.NonTTYMax,
		PingInterval:        cfg.PingInterval,
		MaxSessionsPublic:   cfg.MaxSessionsPublic,
		MaxSessionsInternal: cfg.MaxSessionsInternal,
		AllowedOrigins:      cfg.AllowedOrigins,
		RestoreMaxBytes:     cfg.RestoreMaxBytes,
		RestoreWorkdir:      cfg.RestoreWorkdir,
		Log:                 log,
	})
	defer srv.Close()

	public := &http.Server{Addr: cfg.PublicAddr, Handler: srv.Public(), ReadHeaderTimeout: 10 * time.Second}
	internal := &http.Server{Addr: cfg.InternalAddr, Handler: srv.Internal(), ReadHeaderTimeout: 10 * time.Second}
	opsSrv := &http.Server{Addr: cfg.MetricsAddr, Handler: srv.Ops(), ReadHeaderTimeout: 10 * time.Second}
	errc := make(chan error, 3)
	for _, s := range []*http.Server{public, internal, opsSrv} {
		go func(s *http.Server) {
			if err := s.ListenAndServe(); err != nil && err != http.ErrServerClosed {
				errc <- err
			}
		}(s)
	}
	log.Info("listening", "namespace", cfg.Namespace, "public", cfg.PublicAddr, "internal", cfg.InternalAddr, "metrics", cfg.MetricsAddr)

	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
	}

	// 종료: 새 연결을 받지 않고(/healthz 503 → Ready에서 빠진다), tty 세션은 4503으로 닫는다.
	// 진행 중인 비대화형 세션(특히 restore)은 끝까지 마친다. 차트의 terminationGracePeriodSeconds를
	// 비대화형 최대 실행 시간에 맞춰 두었고, 그 안에 못 끝나면 kubelet이 SIGKILL한다.
	log.Info("shutting down", "active_sessions", srv.Active())
	srv.BeginShutdown()
	lctx, cancel := context.WithTimeout(context.Background(), listenerGrace)
	defer cancel()
	_ = public.Shutdown(lctx)
	_ = internal.Shutdown(lctx)

	wctx, wcancel := context.WithTimeout(context.Background(), cfg.NonTTYMax+15*time.Second)
	defer wcancel()
	if err := srv.Wait(wctx); err != nil {
		log.Error("sessions did not finish in time", "err", err)
	}
	octx, ocancel := context.WithTimeout(context.Background(), time.Second)
	defer ocancel()
	_ = opsSrv.Shutdown(octx)
	return nil
}
