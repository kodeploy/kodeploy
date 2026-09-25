// mockcore는 E2E용 가짜 core다. 빌더 콜백의 서명을 검증하고, 받은 이벤트를 표준출력에 찍고, 200을 준다.
// core처럼 (build_id, seq)가 같은 이벤트는 중복으로 표시한다.
//
//	HMAC_SECRET=... go run ./cmd/mockcore -addr :9090 [-fail-first 3]
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/kodeploy/kodeploy/builder/internal/contract"
	"github.com/kodeploy/kodeploy/builder/internal/sign"
)

func main() {
	addr := flag.String("addr", ":9090", "listen address")
	failFirst := flag.Int("fail-first", 0, "answer 503 to the first N events (to watch retries)")
	flag.Parse()
	secret := os.Getenv("HMAC_SECRET")
	if secret == "" {
		log.Fatal("HMAC_SECRET is required")
	}

	var mu sync.Mutex
	seen := map[string]bool{}
	failLeft := *failFirst

	http.HandleFunc("POST /internal/builds/{build_id}/events", func(w http.ResponseWriter, r *http.Request) {
		id := r.PathValue("build_id")
		body, err := io.ReadAll(io.LimitReader(r.Body, 4<<20))
		if err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if err := sign.VerifyRequest([]byte(secret), r, body, time.Now()); err != nil {
			fmt.Printf("[%s] REJECTED signature: %v\n", id, err)
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		var ev contract.Event
		if err := json.Unmarshal(body, &ev); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}

		mu.Lock()
		defer mu.Unlock()
		if failLeft > 0 {
			failLeft--
			fmt.Printf("[%s] #%d %s -> 503 (fail-first)\n", id, ev.Seq, ev.Type)
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		key := fmt.Sprintf("%s/%d", id, ev.Seq)
		dup := ""
		if seen[key] {
			dup = " (duplicate, core would ignore)"
		}
		seen[key] = true

		if ev.Type == contract.EventLog {
			for _, l := range ev.Lines {
				fmt.Printf("[%s] #%d | %s%s\n", id, ev.Seq, l, dup)
			}
		} else {
			ev.Lines = nil
			pretty, _ := json.Marshal(ev)
			fmt.Printf("[%s] #%d %s%s\n", id, ev.Seq, strings.ToUpper(ev.Type), dup)
			fmt.Printf("    %s\n", pretty)
		}
		w.WriteHeader(http.StatusOK)
	})

	log.Printf("mockcore listening on %s", *addr)
	srv := &http.Server{Addr: *addr, ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}
