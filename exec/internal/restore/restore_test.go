package restore

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/target"
)

// fakeDB는 DB 컨테이너 흉내다. 명령 이름(ops.Cmd.Name)으로 동작을 고른다.
type fakeDB struct {
	mu         sync.Mutex
	availKB    int64
	missing    string // precheck에서 없는 도구
	backupExit int
	loadExit   int
	loadErr    error
	loadStderr string
	rbExit     int
	tables     string // restore.tables 출력
	corrupt    bool   // verify가 다른 해시를 보게
	file       []byte
	calls      []string
	countSQL   string
	cleanupRan bool
	cleanupErr error
	argv       map[string][]string // 명령 이름 → 마지막 argv
}

func (f *fakeDB) exec(ctx context.Context, cmd ops.Cmd, stdin io.Reader, stdout, stderr io.Writer) (int, error) {
	f.mu.Lock()
	f.calls = append(f.calls, cmd.Name)
	if f.argv == nil {
		f.argv = map[string][]string{}
	}
	f.argv[cmd.Name] = cmd.Argv
	f.mu.Unlock()
	switch cmd.Name {
	case "restore.precheck":
		if f.missing != "" {
			fmt.Fprintf(stderr, "missing:%s\n", f.missing)
			return 3, nil
		}
		fmt.Fprintf(stdout, "Filesystem 1024-blocks Used Available Capacity Mounted on\noverlay 47000000 1000 %d 1%% /\n", f.availKB)
		return 0, nil
	case "restore.backup":
		return f.backupExit, nil
	case "restore.upload":
		b, _ := io.ReadAll(stdin)
		f.file = b
		return 0, nil
	case "restore.verify":
		data := f.file
		if f.corrupt {
			data = append([]byte{}, data...)
			data = append(data, 'x')
		}
		sum := sha256.Sum256(data)
		fmt.Fprintf(stdout, "%d\n%s  /w/restore.dump\n", len(data), hex.EncodeToString(sum[:]))
		return 0, nil
	case "restore.gzip-test":
		zr, err := gzip.NewReader(bytes.NewReader(f.file))
		if err != nil {
			return 1, nil
		}
		if _, err := io.Copy(io.Discard, zr); err != nil {
			return 1, nil
		}
		return 0, nil
	case "restore.load":
		io.WriteString(stderr, f.loadStderr)
		return f.loadExit, f.loadErr
	case "restore.tables":
		io.WriteString(stdout, f.tables)
		return 0, nil
	case "restore.count":
		b, _ := io.ReadAll(stdin)
		f.countSQL = string(b)
		n := strings.Count(f.countSQL, "SELECT")
		for i := 0; i < n; i++ {
			fmt.Fprintf(stdout, "%d\t%d\n", i, (i+1)*10)
		}
		return 0, nil
	case "restore.rollback":
		return f.rbExit, nil
	case "restore.cleanup":
		f.cleanupRan, f.cleanupErr = true, ctx.Err()
		return 0, nil
	}
	return 127, fmt.Errorf("unexpected command %s", cmd.Name)
}

const backupName = "backup-20261113T000000Z-abcdef.sql.gz"

func params(kind target.Kind, data []byte) Params {
	sum := sha256.Sum256(data)
	return Params{Kind: kind, Workdir: "/w", Size: int64(len(data)), SHA256: hex.EncodeToString(sum[:]), Backup: backupName}
}

// removedBackup은 cleanup이 지운 백업 이름이다 ($2, 비었으면 남겼다)
func (f *fakeDB) removedBackup(t *testing.T) string {
	t.Helper()
	a := f.argv["restore.cleanup"]
	if len(a) != 6 || a[4] != "/w" {
		t.Fatalf("cleanup argv %q", a)
	}
	return a[5]
}

func names(steps []Step) string {
	var s []string
	for _, st := range steps {
		mark := "ok"
		if !st.OK {
			mark = "fail"
		}
		s = append(s, st.Name+":"+mark)
	}
	return strings.Join(s, " ")
}

var dump = []byte("CREATE TABLE users (id int);\nINSERT INTO users VALUES (1);\n")

func gz(b []byte) []byte {
	var buf bytes.Buffer
	w := gzip.NewWriter(&buf)
	w.Write(b)
	w.Close()
	return buf.Bytes()
}

func TestMySQLSuccess(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, tables: "users\norders\nweird\\ttab\n"}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.Status != "Success" || res.FailedStep != "" || !res.DBChanged {
		t.Fatalf("result %+v", res)
	}
	if got := names(res.Steps); got != "precheck:ok backup:ok upload:ok verify:ok load:ok check:ok cleanup:ok" {
		t.Fatalf("steps %s", got)
	}
	if len(res.Tables) != 3 || res.Tables[0] != (Table{"users", 10}) || res.Tables[2] != (Table{"weird\ttab", 30}) {
		t.Fatalf("tables %+v", res.Tables)
	}
	if !strings.Contains(f.countSQL, "FROM `weird\ttab`") {
		t.Fatalf("count sql %q", f.countSQL)
	}
	// 평문 덤프는 gzip 검사를 하지 않는다
	for _, c := range f.calls {
		if c == "restore.gzip-test" {
			t.Fatal("gzip test on plain dump")
		}
	}
}

func TestPostgresGzipSuccess(t *testing.T) {
	data := gz(dump)
	f := &fakeDB{availKB: 1 << 20, tables: "public\tusers\naudit\tlog\"s\n"}
	res := Run(context.Background(), f.exec, bytes.NewReader(data), params(target.Postgres, data))
	if res.Status != "Success" || !res.DBChanged {
		t.Fatalf("result %+v", res)
	}
	// PostgreSQL은 backup이 없다
	if got := names(res.Steps); got != "precheck:ok upload:ok verify:ok load:ok check:ok cleanup:ok" {
		t.Fatalf("steps %s", got)
	}
	if !strings.Contains(res.Steps[2].Detail, "gzip ok") {
		t.Fatalf("verify detail %q", res.Steps[2].Detail)
	}
	if res.Tables[1].Name != `audit.log"s` || !strings.Contains(f.countSQL, `FROM "audit"."log""s"`) {
		t.Fatalf("tables %+v sql %q", res.Tables, f.countSQL)
	}
}

func TestPrecheckFailures(t *testing.T) {
	f := &fakeDB{availKB: 0}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.FailedStep != "precheck" || res.DBChanged || names(res.Steps) != "precheck:fail cleanup:ok" {
		t.Fatalf("no space: %+v", res)
	}
	f = &fakeDB{availKB: 1 << 20, missing: "sha256sum"}
	res = Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.Postgres, dump))
	if res.FailedStep != "precheck" || !strings.Contains(res.Steps[0].Detail, "missing:sha256sum") {
		t.Fatalf("missing tool: %+v", res)
	}
}

func TestBackupFailure(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, backupExit: 2}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.FailedStep != "backup" || res.DBChanged || names(res.Steps) != "precheck:ok backup:fail cleanup:ok" {
		t.Fatalf("%+v", res)
	}
}

type abortReader struct {
	data []byte
	n    int
}

func (a *abortReader) Read(p []byte) (int, error) {
	if a.n >= len(a.data)/2 {
		return 0, errors.New("stdin aborted: connection ended before CLOSE")
	}
	n := copy(p[:1], a.data[a.n:])
	a.n += n
	return n, nil
}

func TestUploadFailures(t *testing.T) {
	cases := []struct {
		name   string
		src    io.Reader
		p      Params
		detail string
	}{
		{"hash mismatch", bytes.NewReader(dump), func() Params { p := params(target.MySQL, dump); p.SHA256 = strings.Repeat("0", 64); return p }(), "sha256 mismatch"},
		{"short", bytes.NewReader(dump[:10]), params(target.MySQL, dump), "expected"},
		{"too long", bytes.NewReader(append(dump, 'x')), params(target.MySQL, dump), "more bytes than expected"},
		{"aborted", &abortReader{data: dump}, params(target.MySQL, dump), "upload aborted"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := &fakeDB{availKB: 1 << 20}
			res := Run(context.Background(), f.exec, c.src, c.p)
			if res.FailedStep != "upload" || res.DBChanged || !strings.Contains(res.Steps[2].Detail, c.detail) {
				t.Fatalf("%+v", res)
			}
			if res.Steps[len(res.Steps)-1].Name != "cleanup" {
				t.Fatal("cleanup must run")
			}
		})
	}
}

func TestVerifyFailures(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, corrupt: true}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.Postgres, dump))
	if res.FailedStep != "verify" || res.DBChanged {
		t.Fatalf("corrupt: %+v", res)
	}
	// gzip 머리(1f 8b)는 맞는데 내용이 깨진 덤프
	bad := append([]byte{0x1f, 0x8b}, []byte("not really gzip")...)
	f = &fakeDB{availKB: 1 << 20}
	res = Run(context.Background(), f.exec, bytes.NewReader(bad), params(target.Postgres, bad))
	if res.FailedStep != "verify" || !strings.Contains(res.Steps[2].Detail, "gzip -t failed") {
		t.Fatalf("bad gzip: %+v", res)
	}
}

func TestMySQLLoadFailRollback(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, loadExit: 1, loadStderr: "ERROR 1064 (42000) at line 2: syntax\n"}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.Status != "Failure" || res.FailedStep != "check" || !res.DBChanged {
		t.Fatalf("%+v", res)
	}
	// 백업·rollback·cleanup이 같은 백업 이름을 쓰고, 되돌렸으니 백업은 지운다
	if f.argv["restore.backup"][5] != backupName || f.argv["restore.rollback"][5] != backupName {
		t.Fatalf("backup name: %q %q", f.argv["restore.backup"], f.argv["restore.rollback"])
	}
	if res.Backup != nil || f.removedBackup(t) != backupName {
		t.Fatalf("rolled back: backup must be removed: %+v", res.Backup)
	}
	if got := names(res.Steps); got != "precheck:ok backup:ok upload:ok verify:ok load:fail check:fail rollback:ok cleanup:ok" {
		t.Fatalf("steps %s", got)
	}
	if !strings.Contains(res.Output, "ERROR 1064") {
		t.Fatalf("output %q", res.Output)
	}

	// rollback 실패: DB 상태를 모른다 — 백업을 남기고 위치를 싣는다
	f = &fakeDB{availKB: 1 << 20, loadExit: 1, rbExit: 1}
	res = Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.FailedStep != "rollback" || !strings.Contains(res.Output, "rollback failed") {
		t.Fatalf("rollback fail: %+v", res)
	}
	if res.Backup == nil || res.Backup.Path != "/w/"+backupName || f.removedBackup(t) != "" {
		t.Fatalf("rollback fail: backup must be kept: %+v", res.Backup)
	}
	last := res.Steps[len(res.Steps)-1]
	if last.Name != "cleanup" || !last.OK || last.Detail != "backup kept: /w/"+backupName {
		t.Fatalf("cleanup step %+v", last)
	}
}

func TestPostgresLoadFail(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, loadExit: 3, loadStderr: "psql:/w/restore.dump:2: ERROR:  syntax error\n"}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.Postgres, dump))
	if res.FailedStep != "check" || res.DBChanged {
		t.Fatalf("%+v", res)
	}
	for _, c := range f.calls {
		if c == "restore.rollback" {
			t.Fatal("postgres must not run rollback")
		}
	}
}

func TestLoadInterrupted(t *testing.T) {
	f := &fakeDB{availKB: 1 << 20, loadExit: -1, loadErr: errors.New("stream closed")}
	res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if res.FailedStep != "load" || !res.DBChanged || !strings.Contains(res.Steps[4].Detail, "rollback skipped") {
		t.Fatalf("%+v", res)
	}
	for _, c := range f.calls {
		if c == "restore.rollback" {
			t.Fatal("rollback must be skipped when load outcome is unknown")
		}
	}
	// 결과를 모른다: 백업은 남기고(덤프만 지운다) 위치를 결과에 싣는다
	if res.Backup == nil || res.Backup.Path != "/w/"+backupName || f.removedBackup(t) != "" {
		t.Fatalf("backup must be kept: %+v", res.Backup)
	}
	b, _ := json.Marshal(res)
	if !strings.Contains(string(b), `"backup":{"path":"/w/`+backupName+`"}`) {
		t.Fatalf("json %s", b)
	}

	// PostgreSQL: 끊긴 뒤에도 적재가 끝나면 커밋된다 — db_changed=true (재시도 금지). 백업은 원래 없다
	f = &fakeDB{availKB: 1 << 20, loadExit: -1, loadErr: errors.New("stream closed")}
	res = Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.Postgres, dump))
	if res.FailedStep != "load" || !res.DBChanged || res.Backup != nil {
		t.Fatalf("postgres interrupted: %+v", res)
	}
}

// 결과를 아는 실패·성공에서는 백업을 남기지 않는다
func TestBackupRemovedWhenOutcomeKnown(t *testing.T) {
	for name, f := range map[string]*fakeDB{
		"success":     {availKB: 1 << 20, tables: "users\n"},
		"backup fail": {availKB: 1 << 20, backupExit: 2},
		"verify fail": {availKB: 1 << 20, corrupt: true},
	} {
		res := Run(context.Background(), f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
		if res.Backup != nil || f.removedBackup(t) != backupName {
			t.Fatalf("%s: backup %+v removed %q", name, res.Backup, f.removedBackup(t))
		}
	}
	b, _ := json.Marshal(Result{Status: "Success"})
	if strings.Contains(string(b), "backup") {
		t.Fatalf("backup must be omitted: %s", b)
	}
}

func TestBackupName(t *testing.T) {
	now := time.Date(2026, 11, 13, 1, 2, 3, 0, time.FixedZone("KST", 9*3600))
	a, b := BackupName(now), BackupName(now)
	if !regexp.MustCompile(`^backup-20261112T160203Z-[0-9a-f]{6}\.sql\.gz$`).MatchString(a) || a == b {
		t.Fatalf("%q %q", a, b)
	}
	// Run은 이름이 없으면 만든다
	f := &fakeDB{availKB: 1 << 20, tables: "users\n"}
	p := params(target.MySQL, dump)
	p.Backup = ""
	Run(context.Background(), f.exec, bytes.NewReader(dump), p)
	if n := f.argv["restore.backup"][5]; !strings.HasPrefix(n, "backup-") || f.removedBackup(t) != n {
		t.Fatalf("generated name %q", n)
	}
}

// 요청 ctx가 이미 끝났어도 cleanup은 끝나지 않은 ctx로 돈다
func TestCleanupAlwaysRuns(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	f := &fakeDB{availKB: 0}
	Run(ctx, f.exec, bytes.NewReader(dump), params(target.MySQL, dump))
	if !f.cleanupRan || f.cleanupErr != nil {
		t.Fatal("cleanup must run with a live context")
	}
}

func TestTail(t *testing.T) {
	tl := newTail(50)
	for i := 0; i < 120; i++ {
		fmt.Fprintf(tl, "line %d\n", i)
	}
	io.WriteString(tl, "partial")
	lines := strings.Split(tl.String(), "\n")
	if len(lines) != 50 || lines[0] != "line 71" || lines[49] != "partial" {
		t.Fatalf("%d lines: %q ... %q", len(lines), lines[0], lines[len(lines)-1])
	}
}

func TestParseDF(t *testing.T) {
	n, ok := parseDF("Filesystem     1024-blocks    Used Available Capacity Mounted on\n/dev/sda1  47000000 1 123 1% /tmp\n")
	if !ok || n != 123*1024 {
		t.Fatalf("%d %v", n, ok)
	}
	if _, ok := parseDF("garbage"); ok {
		t.Fatal("want failure")
	}
}
