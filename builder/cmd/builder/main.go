// builder는 core 대신 빌드 Job을 돌리고 결과 이미지를 kodeploy-apps에 커밋하는 서비스다.
package main

import (
	"context"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/clientcmd"

	"github.com/kodeploy/kodeploy/builder/internal/api"
	"github.com/kodeploy/kodeploy/builder/internal/argo"
	"github.com/kodeploy/kodeploy/builder/internal/callback"
	"github.com/kodeploy/kodeploy/builder/internal/config"
	"github.com/kodeploy/kodeploy/builder/internal/gitops"
	"github.com/kodeploy/kodeploy/builder/internal/job"
	"github.com/kodeploy/kodeploy/builder/internal/logs"
	"github.com/kodeploy/kodeploy/builder/internal/registry"
	"github.com/kodeploy/kodeploy/builder/internal/run"
)

// SIGTERM 후 종료까지 쓰는 시간 (지시서 4-6)
const shutdownGrace = 20 * time.Second

func main() {
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := serve(log); err != nil {
		log.Error("builder stopped", "err", err)
		os.Exit(1)
	}
}

// kubeConfig는 클러스터 안이면 SA 토큰, 밖이면 KUBECONFIG(개발·E2E용)를 쓴다.
func kubeConfig() (*rest.Config, error) {
	if c, err := rest.InClusterConfig(); err == nil {
		return c, nil
	}
	rules := clientcmd.NewDefaultClientConfigLoadingRules()
	return clientcmd.NewNonInteractiveDeferredLoadingClientConfig(rules, nil).ClientConfig()
}

func serve(log *slog.Logger) error {
	cfg, err := config.Load(os.Getenv)
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
	dyn, err := dynamic.NewForConfig(rc)
	if err != nil {
		return err
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()

	gh := gitops.NewGitHub(cfg.GitOpsRepo, cfg.GitOpsBranch, cfg.GitHubToken)
	writer := gitops.NewWriter(gh)
	writerCtx, stopWriter := context.WithCancel(context.Background())
	defer stopWriter()
	go writer.Run(writerCtx)

	mgr := run.NewManager(run.Deps{
		Cfg:      cfg,
		Jobs:     job.NewClient(cs, cfg.BuildNamespace),
		Logs:     logs.NewFollower(logs.NewKubeSource(cs, cfg.BuildNamespace)),
		Registry: registry.New(cfg.GHCRUser, cfg.GHCRToken),
		Git:      writer,
		Argo:     argo.New(dyn, cfg.ArgoCDNamespace, "https://github.com/"+cfg.GitOpsRepo, gh.IsAncestor),
		Events:   callback.NewSender(cfg.CoreURL, cfg.HMACSecret, log),
		Log:      log,
	})
	// 요청을 받기 전에 지난 실행에서 끊긴 빌드부터 이어간다 (지시서 4-5)
	if n, err := mgr.Resume(ctx); err != nil {
		log.Error("resume failed; unfinished builds stay as they are", "err", err)
	} else if n > 0 {
		log.Info("resumed builds", "count", n)
	}

	srv := api.New(api.Options{
		Secret:     cfg.HMACSecret,
		Validator:  api.Validator{GHCRUser: cfg.GHCRUser},
		Dispatcher: mgr,
		Logger:     log,
	})
	httpSrv := &http.Server{Addr: cfg.Listen, Handler: srv.Handler(), ReadHeaderTimeout: 10 * time.Second}
	errc := make(chan error, 1)
	go func() { errc <- httpSrv.ListenAndServe() }()
	log.Info("listening", "addr", cfg.Listen, "early_trigger", cfg.EarlyTrigger, "max_active_builds", cfg.MaxActiveBuilds)

	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
	}

	// 종료: 새 요청을 멈추고, 진행 중인 빌드는 Job을 남긴 채 멈춘다 (재시작 후 Resume)
	log.Info("shutting down")
	sctx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	_ = httpSrv.Shutdown(sctx)
	if err := mgr.Shutdown(sctx); err != nil {
		log.Warn("shutdown timed out", "err", err)
	}
	return nil
}
