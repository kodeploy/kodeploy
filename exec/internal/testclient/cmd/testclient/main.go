// testclient는 kodeploy-exec E2E 확인 도구다 (hack/exec-e2e.md 9절 체크리스트에서 쓴다).
//
//	go run ./internal/testclient/cmd/testclient <명령> [옵션]
//
//	ticket        공개 문용 티켓 발급                      -internal -key-file -app -target
//	shell         공개 문 대화형 세션 한 번                 -url -ticket [-header] [-input] [-resize WxH]
//	run           내부 세션 query·browse·dump              -internal -key-file -op [-sql | -in] [-out]
//	restore       내부 세션 restore (크기·sha256은 계산)    -internal -key-file -file [-sha] [-abort-after N]
//	hold          공개 문 세션 n개 동시에 열기 (4429 확인)   -internal -key-file -url -app -n
//	check-public  9절 4번 자동 확인 (PASS/FAIL)             -internal -key-file -url -app [-origin] [-other-ns-url] [-expiry]
//
// 판정은 상태 코드가 아니라 출력 본문의 sentinel로 한다. sentinel은 입력 echo에 나오지 않는 값이다
// (예: 입력 "echo kd-$((6*7))" → 출력에만 "kd-42").
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/kodeploy/kodeploy/exec/internal/testclient"
)

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: testclient ticket|shell|run|restore|hold|check-public [flags]")
		os.Exit(2)
	}
	cmd, args := os.Args[1], os.Args[2:]
	var err error
	switch cmd {
	case "ticket":
		err = cmdTicket(args)
	case "shell":
		err = cmdShell(args)
	case "run":
		err = cmdRun(args)
	case "restore":
		err = cmdRestore(args)
	case "hold":
		err = cmdHold(args)
	case "check-public":
		err = cmdCheckPublic(args)
	default:
		err = fmt.Errorf("unknown command %q", cmd)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

type internalFlags struct {
	base, keyFile string
}

func (f *internalFlags) register(fs *flag.FlagSet) {
	fs.StringVar(&f.base, "internal", "http://127.0.0.1:18081", "내부 문 주소 (kubectl port-forward)")
	fs.StringVar(&f.keyFile, "key-file", "", "kodeploy-exec-hmac의 hmac 키 파일 (base64 디코딩한 원본 바이트)")
}

func (f *internalFlags) client() (*testclient.Internal, error) {
	if f.keyFile == "" {
		return nil, fmt.Errorf("-key-file is required")
	}
	key, err := os.ReadFile(f.keyFile)
	if err != nil {
		return nil, err
	}
	return &testclient.Internal{Base: f.base, Key: key}, nil
}

var targetOp = map[string]string{"app": "shell", "db": "console", "redis": "console"}

func issue(ctx context.Context, in *testclient.Internal, app, target string, cols, rows uint16) (string, error) {
	t, err := in.Ticket(ctx, testclient.TicketRequest{UserID: "e2e", AppID: "e2e", App: app, Target: target, Op: targetOp[target], Cols: cols, Rows: rows})
	return t.Ticket, err
}

func cmdTicket(args []string) error {
	fs := flag.NewFlagSet("ticket", flag.ExitOnError)
	var inf internalFlags
	inf.register(fs)
	app := fs.String("app", "", "앱 이름")
	target := fs.String("target", "app", "app | db | redis")
	fs.Parse(args)
	in, err := inf.client()
	if err != nil {
		return err
	}
	t, err := in.Ticket(context.Background(), testclient.TicketRequest{UserID: "e2e", AppID: "e2e", App: *app, Target: *target, Op: targetOp[*target], Cols: 120, Rows: 32})
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(t)
}

func parseWxH(s string) (uint16, uint16, error) {
	w, h, ok := strings.Cut(s, "x")
	wi, e1 := strconv.Atoi(w)
	hi, e2 := strconv.Atoi(h)
	if !ok || e1 != nil || e2 != nil {
		return 0, 0, fmt.Errorf("bad size %q (want WxH)", s)
	}
	return uint16(wi), uint16(hi), nil
}

func cmdShell(args []string) error {
	fs := flag.NewFlagSet("shell", flag.ExitOnError)
	url := fs.String("url", "", "wss://api.kodeploy.com/exec/<ns>/v1/sessions")
	ticket := fs.String("ticket", "", "티켓 (비우면 티켓 없이)")
	header := fs.Bool("header", false, "티켓을 Authorization: Ticket 헤더로")
	input := fs.String("input", "exit\n", "보낼 입력 (\\n 이스케이프 가능)")
	resize := fs.String("resize", "", "입력 전에 보낼 창 크기 WxH")
	timeout := fs.Duration("timeout", 20*time.Second, "")
	fs.Parse(args)
	c, err := testclient.DialPublic(context.Background(), *url, *ticket, *header)
	if err != nil {
		return err
	}
	defer c.Close()
	if *resize != "" {
		w, h, err := parseWxH(*resize)
		if err != nil {
			return err
		}
		c.Resize(w, h)
	}
	c.Stdin([]byte(strings.ReplaceAll(*input, `\n`, "\n")))
	res, err := c.Read(*timeout, func(p []byte) { os.Stdout.Write(p) })
	fmt.Printf("\n--- exit=%d close=%d %s\n", res.ExitCode, res.CloseCode, res.CloseText)
	return err
}

func cmdRun(args []string) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	var inf internalFlags
	inf.register(fs)
	op := fs.String("op", "query", "query | browse | dump")
	sql := fs.String("sql", "", "query·browse SQL")
	inFile := fs.String("in", "", "SQL을 파일에서")
	out := fs.String("out", "", "stdout을 파일로 (dump)")
	timeout := fs.Duration("timeout", 30*time.Minute, "")
	fs.Parse(args)
	in, err := inf.client()
	if err != nil {
		return err
	}
	c, err := in.Dial(context.Background(), "/internal/v1/sessions/"+*op)
	if err != nil {
		return err
	}
	defer c.Close()
	if *op != "dump" {
		body := []byte(*sql)
		if *inFile != "" {
			if body, err = os.ReadFile(*inFile); err != nil {
				return err
			}
		}
		for len(body) > 0 {
			n := min(len(body), 256<<10)
			if err := c.Stdin(body[:n]); err != nil {
				break
			}
			body = body[n:]
		}
		c.CloseStdin()
	}
	res, err := c.Read(*timeout, nil)
	if *out != "" {
		if werr := os.WriteFile(*out, res.Stdout, 0o600); werr != nil {
			return werr
		}
		fmt.Printf("stdout → %s (%d bytes)\n", *out, len(res.Stdout))
	} else {
		os.Stdout.Write(res.Stdout)
	}
	if len(res.Stderr) > 0 {
		fmt.Printf("--- stderr\n%s\n", res.Stderr)
	}
	fmt.Printf("--- exit=%d close=%d %s\n", res.ExitCode, res.CloseCode, res.CloseText)
	return err
}

func cmdRestore(args []string) error {
	fs := flag.NewFlagSet("restore", flag.ExitOnError)
	var inf internalFlags
	inf.register(fs)
	file := fs.String("file", "", "덤프 파일 (.sql 또는 .sql.gz)")
	sha := fs.String("sha", "", "기대 sha256을 일부러 다르게 줄 때 (장애 재현)")
	abortAfter := fs.Int64("abort-after", 0, "이만큼 보낸 뒤 CLOSE 없이 끊기 (장애 재현)")
	timeout := fs.Duration("timeout", 30*time.Minute, "")
	fs.Parse(args)
	in, err := inf.client()
	if err != nil {
		return err
	}
	data, err := os.ReadFile(*file)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(data)
	expected := hex.EncodeToString(sum[:])
	if *sha != "" {
		expected = *sha
	}
	c, err := in.Dial(context.Background(), testclient.RestorePath(int64(len(data)), expected))
	if err != nil {
		return err
	}
	defer c.Close()
	var sent int64
	for off := 0; off < len(data); off += 256 << 10 {
		end := min(off+256<<10, len(data))
		if *abortAfter > 0 && sent+int64(end-off) > *abortAfter {
			end = off + int(*abortAfter-sent)
		}
		if err := c.Stdin(data[off:end]); err != nil {
			break
		}
		sent += int64(end - off)
		if *abortAfter > 0 && sent >= *abortAfter {
			c.Abort()
			fmt.Printf("aborted after %d bytes (CLOSE 없이 끊음) — exec 감사 기록과 DB가 그대로인지 확인\n", sent)
			return nil
		}
	}
	c.CloseStdin()
	res, err := c.Read(*timeout, nil)
	var pretty bytes.Buffer
	if json.Indent(&pretty, res.Status, "", "  ") == nil {
		fmt.Println(pretty.String())
	}
	fmt.Printf("--- close=%d %s\n", res.CloseCode, res.CloseText)
	return err
}

func cmdHold(args []string) error {
	fs := flag.NewFlagSet("hold", flag.ExitOnError)
	var inf internalFlags
	inf.register(fs)
	url := fs.String("url", "", "공개 문 주소")
	app := fs.String("app", "", "앱 이름")
	n := fs.Int("n", 4, "열 세션 수 (공개 문 MAX_SESSIONS_PUBLIC+1이면 마지막이 4429)")
	fs.Parse(args)
	in, err := inf.client()
	if err != nil {
		return err
	}
	ctx := context.Background()
	var open []*testclient.Conn
	defer func() {
		for _, c := range open {
			c.Close()
		}
	}()
	for i := 1; i <= *n; i++ {
		t, err := issue(ctx, in, *app, "app", 80, 24)
		if err != nil {
			return err
		}
		c, err := testclient.DialPublic(ctx, *url, t, false)
		if err != nil {
			return err
		}
		// 프롬프트가 오면 세션이 자리를 잡은 것이다. 먼저 닫히면 그 close 코드가 답이다
		c.Stdin([]byte("echo kd-hold-$((40+2))\n"))
		if _, err := c.ReadUntil([]byte("kd-hold-42"), 15*time.Second); err != nil {
			res, _ := c.Read(2*time.Second, nil)
			fmt.Printf("session %d: closed %d %s\n", i, res.CloseCode, res.CloseText)
			continue
		}
		fmt.Printf("session %d: open\n", i)
		open = append(open, c)
	}
	return nil
}

type checker struct{ fail int }

func (k *checker) check(ok bool, name, detail string) {
	mark := "PASS"
	if !ok {
		mark = "FAIL"
		k.fail++
	}
	fmt.Printf("%s  %-28s %s\n", mark, name, detail)
}

func cmdCheckPublic(args []string) error {
	fs := flag.NewFlagSet("check-public", flag.ExitOnError)
	var inf internalFlags
	inf.register(fs)
	url := fs.String("url", "", "공개 문 주소 wss://api.kodeploy.com/exec/<ns>/v1/sessions")
	app := fs.String("app", "", "앱 이름")
	otherNS := fs.String("other-ns-url", "", "같은 Pod에 다른 ns 경로로 붙는 주소 (port-forward, 4403 확인)")
	expiry := fs.Bool("expiry", false, "티켓 만료(31초 대기) 확인")
	origin := fs.String("origin", "https://app.kodeploy.com", "허용 목록에 있는 Origin (차트 platform/exec.yaml allowedOrigins)")
	fs.Parse(args)
	in, err := inf.client()
	if err != nil {
		return err
	}
	ctx := context.Background()
	k := &checker{}

	// 1) 정상 세션: 한글·바이너리 출력, 창 크기, exit code
	tA, err := issue(ctx, in, *app, "app", 80, 24)
	if err != nil {
		return err
	}
	c, err := testclient.DialPublic(ctx, *url, tA, false)
	k.check(err == nil, "subprotocol negotiated", fmt.Sprint(err))
	if err != nil {
		return fmt.Errorf("%d checks failed", k.fail)
	}
	c.Stdin([]byte("echo kd-$((6*7))-한글\n"))
	_, err = c.ReadUntil([]byte("kd-42-한글"), 15*time.Second)
	k.check(err == nil, "korean output", fmt.Sprint(err))
	c.Stdin([]byte(`printf 'kd-bin-\001\377-end\n'` + "\n"))
	_, err = c.ReadUntil([]byte("kd-bin-\x01\xff-end"), 15*time.Second)
	k.check(err == nil, "binary output (01 ff)", fmt.Sprint(err))
	c.Resize(123, 45)
	c.Stdin([]byte("stty size\n"))
	_, err = c.ReadUntil([]byte("45 123"), 15*time.Second)
	k.check(err == nil, "resize → stty size 45 123", fmt.Sprint(err))
	c.Stdin([]byte("exit 7\n"))
	res, err := c.Read(15*time.Second, nil)
	k.check(err == nil && res.CloseCode == 1000 && res.ExitCode == 7, "exit 7 → status + 1000", fmt.Sprintf("exit=%d close=%d %v", res.ExitCode, res.CloseCode, err))

	// 2) 같은 티켓 재사용 → 4401
	c, err = testclient.DialPublic(ctx, *url, tA, false)
	if err == nil {
		res, _ = c.Read(10*time.Second, nil)
	}
	k.check(err == nil && res.CloseCode == 4401, "ticket reuse → 4401", fmt.Sprintf("close=%d %v", res.CloseCode, err))

	// 3) 쿼리로만 준 티켓은 보지 않는다 → 4401
	tQ, _ := issue(ctx, in, *app, "app", 80, 24)
	c, err = testclient.DialPublic(ctx, *url+"?ticket="+tQ, "", false)
	if err == nil {
		res, _ = c.Read(10*time.Second, nil)
	}
	k.check(err == nil && res.CloseCode == 4401, "ticket in query ignored → 4401", fmt.Sprintf("close=%d %v", res.CloseCode, err))

	// 4) Authorization: Ticket 헤더 (CLI용)
	tH, _ := issue(ctx, in, *app, "app", 80, 24)
	c, err = testclient.DialPublic(ctx, *url, tH, true)
	if err == nil {
		c.Stdin([]byte("exit 0\n"))
		res, _ = c.Read(15*time.Second, nil)
	}
	k.check(err == nil && res.CloseCode == 1000 && res.ExitCode == 0, "Authorization: Ticket", fmt.Sprintf("close=%d exit=%d %v", res.CloseCode, res.ExitCode, err))

	// 5) Origin: 목록 밖이면 업그레이드 없이 403이고 티켓은 남는다. 같은 티켓으로 허용 Origin이면 열린다
	tO, _ := issue(ctx, in, *app, "app", 80, 24)
	_, err = testclient.DialPublicOrigin(ctx, *url, tO, false, "https://evil.example")
	k.check(err != nil && strings.Contains(err.Error(), "handshake: 403"), "foreign Origin → 403", fmt.Sprint(err))
	c, err = testclient.DialPublicOrigin(ctx, *url, tO, false, *origin)
	if err == nil {
		c.Stdin([]byte("exit 0\n"))
		res, _ = c.Read(15*time.Second, nil)
	}
	k.check(err == nil && res.CloseCode == 1000 && res.ExitCode == 0, "allowed Origin, same ticket", fmt.Sprintf("close=%d exit=%d %v", res.CloseCode, res.ExitCode, err))

	// 6) 다른 ns 경로 → 4403 (같은 Pod에 직접 붙어야 한다: Envoy를 거치면 그 ns의 Pod로 가서 4401)
	if *otherNS != "" {
		tN, _ := issue(ctx, in, *app, "app", 80, 24)
		c, err = testclient.DialPublic(ctx, *otherNS, tN, false)
		if err == nil {
			res, _ = c.Read(10*time.Second, nil)
		}
		k.check(err == nil && res.CloseCode == 4403, "other namespace → 4403", fmt.Sprintf("close=%d %v", res.CloseCode, err))
	}

	// 7) 만료 (30초) → 4401
	if *expiry {
		tE, _ := issue(ctx, in, *app, "app", 80, 24)
		fmt.Println("...waiting 31s for ticket expiry")
		time.Sleep(31 * time.Second)
		c, err = testclient.DialPublic(ctx, *url, tE, false)
		if err == nil {
			res, _ = c.Read(10*time.Second, nil)
		}
		k.check(err == nil && res.CloseCode == 4401, "expired ticket → 4401", fmt.Sprintf("close=%d %v", res.CloseCode, err))
	}

	if k.fail > 0 {
		return fmt.Errorf("%d checks failed", k.fail)
	}
	fmt.Println("all public-door checks passed")
	return nil
}
