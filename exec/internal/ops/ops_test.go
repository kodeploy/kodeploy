package ops

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/kodeploy/kodeploy/exec/internal/target"
)

var passwordArg = regexp.MustCompile(`(^|\s)-p\S|(^|\s)-a\s|--password|(MYSQL_PWD|PGPASSWORD|REDISCLI_AUTH)=[^"]`)

// 사용자 입력에 쓰일 법한 위험한 값. 셸 문자열에 섞이면 깨지거나 실행된다.
const nastySQL = "SELECT 'a''b', \"c\", `d`, $HOME, $(id), \\n; -- 한글\nSELECT 2;"

func allCmds(t *testing.T) []Cmd {
	t.Helper()
	var out []Cmd
	add := func(c Cmd, err error) {
		if err != nil {
			t.Fatal(err)
		}
		out = append(out, c)
	}
	out = append(out, Shell("web"), RedisConsole(), RestorePrecheck("/w"), RestoreBackup("/w", "backup-x.sql.gz"), RestoreUpload("/w"),
		RestoreVerify("/w"), RestoreGzipTest("/w"), RestoreRollback("/w", "backup-x.sql.gz"), RestoreCleanup("/w", "backup-x.sql.gz"))
	for _, k := range []target.Kind{target.MySQL, target.Postgres} {
		add(DBConsole(k))
		add(Query(k))
		add(Browse(k))
		add(Dump(k))
		add(RestoreLoad(k, "/w", true))
		add(RestoreTables(k))
		add(RestoreCount(k))
	}
	return out
}

// 셸 문자열(argv[2])은 상수뿐이고, 비밀번호는 인자로 넘기지 않는다
func TestNoSecretsOrInputInArgv(t *testing.T) {
	for _, c := range allCmds(t) {
		if len(c.Argv) < 4 || c.Argv[1] != "-c" || c.Argv[3] != "sh" {
			t.Fatalf("%s: argv shape %q", c.Name, c.Argv)
		}
		script := c.Argv[2]
		// mysql -p<비번>, redis-cli -a <비번>, --password, 비밀번호 리터럴 대입
		if m := passwordArg.FindString(script); m != "" {
			t.Errorf("%s: password-like argument %q in script", c.Name, m)
		}
	}
	// 앱 이름은 셸 문자열이 아니라 위치 인자로만 들어간다
	c := Shell("evil$(id)")
	if strings.Contains(c.Argv[2], "evil") || c.Argv[4] != "evil$(id)" {
		t.Fatalf("app name leaked into script: %q", c.Argv)
	}
}

func TestFlags(t *testing.T) {
	for _, c := range allCmds(t) {
		switch c.Name {
		case "shell", "db-console", "redis-console":
			if !c.TTY || !c.Stdin {
				t.Errorf("%s: want tty+stdin", c.Name)
			}
		case "query", "browse", "restore.upload", "restore.count":
			if c.TTY || !c.Stdin {
				t.Errorf("%s: want stdin, no tty", c.Name)
			}
		default:
			if c.TTY || c.Stdin {
				t.Errorf("%s: want no tty, no stdin", c.Name)
			}
		}
	}
}

// 원본 명령의 옵션이 그대로 있어야 core 파서를 그대로 쓸 수 있다
func TestOriginalOptions(t *testing.T) {
	want := map[string][]string{
		"mysql/query":     {"mysql --batch --default-character-set=utf8mb4 -u root"},
		"postgres/query":  {"psql --csv -U"},
		"mysql/browse":    {"mysql --batch --binary-as-hex --default-character-set=utf8mb4 --init-command="},
		"postgres/browse": {"psql -X --csv -P null=NULL -U", `PGOPTIONS="$1"`},
		"mysql/dump":      {"set -o pipefail", "mysqldump -u root --single-transaction --routines --triggers --no-tablespaces", "| gzip -c"},
		"postgres/dump":   {"set -o pipefail", "pg_dump -U \"$POSTGRES_USER\" --clean --if-exists", "| gzip -c"},
	}
	for _, k := range []target.Kind{target.MySQL, target.Postgres} {
		q, _ := Query(k)
		b, _ := Browse(k)
		d, _ := Dump(k)
		for _, c := range []Cmd{q, b, d} {
			for _, s := range want[string(k)+"/"+c.Name] {
				if !strings.Contains(c.Argv[2], s) {
					t.Errorf("%s/%s: missing %q in %q", k, c.Name, s, c.Argv[2])
				}
			}
		}
	}
	b, _ := Browse(target.MySQL)
	if b.Argv[4] != "SET SESSION transaction_read_only = ON, SESSION max_execution_time = 15000, SESSION sql_mode = REPLACE(@@SESSION.sql_mode, 'NO_BACKSLASH_ESCAPES', '')" {
		t.Errorf("mysql browse init differs from dbbrowse.py: %q", b.Argv[4])
	}
	b, _ = Browse(target.Postgres)
	if b.Argv[4] != "-c default_transaction_read_only=on -c statement_timeout=15000 -c standard_conforming_strings=on" {
		t.Errorf("pg browse options differ from dbbrowse.py: %q", b.Argv[4])
	}
	l, _ := RestoreLoad(target.Postgres, "/w", false)
	if !strings.Contains(l.Argv[2], "-v ON_ERROR_STOP=1 --single-transaction") || l.Argv[5] != "plain" {
		t.Errorf("pg load: %q", l.Argv)
	}
}

func TestUnsupportedKind(t *testing.T) {
	for _, f := range []func(target.Kind) (Cmd, error){DBConsole, Query, Browse, Dump, RestoreTables, RestoreCount} {
		if _, err := f(target.Redis); err == nil {
			t.Error("redis must be rejected for db ops")
		}
	}
}

// --- 실제 셸로 돌려 보는 테스트: 가짜 mysql/psql이 받은 인자와 env를 그대로 출력한다 ---

const fakeClient = `#!/bin/sh
for a in "$@"; do printf '%s\0' "$a"; done
printf 'MYSQL_PWD=%s\0PGPASSWORD=%s\0PGOPTIONS=%s\0REDISCLI_AUTH=%s\0LANG=%s\0' "$MYSQL_PWD" "$PGPASSWORD" "$PGOPTIONS" "$REDISCLI_AUTH" "$LANG"
`

func fakeBin(t *testing.T, names ...string) string {
	t.Helper()
	dir := t.TempDir()
	for _, n := range names {
		if err := os.WriteFile(filepath.Join(dir, n), []byte(fakeClient), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

var dbEnv = []string{
	"MYSQL_ROOT_PASSWORD=rootpw", "MYSQL_USER=app", "MYSQL_PASSWORD=apppw", "MYSQL_DATABASE=app",
	"POSTGRES_USER=app", "POSTGRES_PASSWORD=pgpw", "POSTGRES_DB=app", "REDIS_PASSWORD=redispw",
}

func runCmd(t *testing.T, c Cmd, bin, stdin string) []string {
	t.Helper()
	if _, err := exec.LookPath(strings.TrimPrefix(c.Argv[0], "/bin/")); err != nil {
		t.Skipf("%s not available", c.Argv[0])
	}
	cmd := exec.Command(c.Argv[0], c.Argv[1:]...)
	cmd.Env = append([]string{"PATH=" + bin + ":/usr/bin:/bin"}, dbEnv...)
	cmd.Stdin = strings.NewReader(stdin)
	var out, errb bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errb
	if err := cmd.Run(); err != nil {
		t.Fatalf("%s: %v (%s)", c.Name, err, errb.String())
	}
	return strings.Split(strings.TrimSuffix(out.String(), "\x00"), "\x00")
}

func TestQueryRunsWithSQLIntact(t *testing.T) {
	bin := fakeBin(t, "mysql", "psql")
	q, _ := Query(target.MySQL)
	got := runCmd(t, q, bin, nastySQL+"\n")
	// $(cat)은 끝 줄바꿈만 지운다
	wantArgs := []string{"--batch", "--default-character-set=utf8mb4", "-u", "root", "app", "-e", nastySQL}
	if !slices.Equal(got[:len(wantArgs)], wantArgs) || !slices.Contains(got, "MYSQL_PWD=rootpw") {
		t.Fatalf("mysql query args: %q", got)
	}

	q, _ = Query(target.Postgres)
	got = runCmd(t, q, bin, nastySQL)
	wantArgs = []string{"--csv", "-U", "app", "-d", "app", "-c", nastySQL}
	if !slices.Equal(got[:len(wantArgs)], wantArgs) || !slices.Contains(got, "PGPASSWORD=pgpw") {
		t.Fatalf("psql query args: %q", got)
	}
}

func TestBrowseRuns(t *testing.T) {
	bin := fakeBin(t, "mysql", "psql")
	b, _ := Browse(target.MySQL)
	got := runCmd(t, b, bin, "SELECT 1")
	wantArgs := []string{"--batch", "--binary-as-hex", "--default-character-set=utf8mb4", "--init-command=" + browseMySQLInit, "-u", "root", "app", "-e", "SELECT 1"}
	if !slices.Equal(got[:len(wantArgs)], wantArgs) {
		t.Fatalf("mysql browse args: %q", got)
	}
	b, _ = Browse(target.Postgres)
	got = runCmd(t, b, bin, "SELECT 1")
	wantArgs = []string{"-X", "--csv", "-P", "null=NULL", "-U", "app", "-d", "app", "-c", "SELECT 1"}
	if !slices.Equal(got[:len(wantArgs)], wantArgs) || !slices.Contains(got, "PGOPTIONS="+browsePGOptions) {
		t.Fatalf("psql browse args: %q", got)
	}
}

func TestConsolesRun(t *testing.T) {
	bin := fakeBin(t, "mysql", "psql", "redis-cli")
	c, _ := DBConsole(target.MySQL)
	got := runCmd(t, c, bin, "")
	if !slices.Equal(got[:4], []string{"--default-character-set=utf8mb4", "-u", "app", "app"}) || !slices.Contains(got, "MYSQL_PWD=apppw") {
		t.Fatalf("mysql console: %q", got)
	}
	// mysql 이미지는 LANG이 비어 있어서, 로케일을 주지 않으면 줄 편집기가 한글 입력을 버린다
	if !slices.Contains(got, "LANG=C.UTF-8") {
		t.Fatalf("mysql console needs a UTF-8 locale: %q", got)
	}
	c, _ = DBConsole(target.Postgres)
	got = runCmd(t, c, bin, "")
	if !slices.Equal(got[:3], []string{"-U", "app", "app"}) || !slices.Contains(got, "PGPASSWORD=") {
		t.Fatalf("psql console: %q", got)
	}
	got = runCmd(t, RedisConsole(), bin, "")
	if got[0] != "REDISCLI_AUTH=redispw" && !slices.Contains(got, "REDISCLI_AUTH=redispw") {
		t.Fatalf("redis console: %q", got)
	}
	for _, a := range got {
		if a == "-a" {
			t.Fatal("redis password passed as argument")
		}
	}
}

func TestShellPrompt(t *testing.T) {
	cmd := exec.Command("/bin/sh", "-c", ps1Assign+`; printf '%s' "$PS1"`, "sh", "web")
	out, err := cmd.Output()
	if err != nil {
		t.Fatal(err)
	}
	if string(out) != `\u@web\$ ` {
		t.Fatalf("PS1 = %q, want %q (원본 '\\u@web\\$ ')", out, `\u@web\$ `)
	}
}

// pipefail: mysqldump가 실패하면 gzip이 성공해도 exit code가 실패여야 한다 (원본 버그)
func TestDumpPipefail(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not available")
	}
	if _, err := exec.LookPath("gzip"); err != nil {
		t.Skip("gzip not available")
	}
	dir := t.TempDir()
	failing := "#!/bin/sh\necho partial\nexit 2\n"
	for _, n := range []string{"mysqldump", "pg_dump"} {
		os.WriteFile(filepath.Join(dir, n), []byte(failing), 0o755)
	}
	for _, k := range []target.Kind{target.MySQL, target.Postgres} {
		d, _ := Dump(k)
		cmd := exec.Command(d.Argv[0], d.Argv[1:]...)
		cmd.Env = append([]string{"PATH=" + dir + ":/usr/bin:/bin"}, dbEnv...)
		err := cmd.Run()
		var ee *exec.ExitError
		if !errorsAs(err, &ee) || ee.ExitCode() != 2 {
			t.Fatalf("%s dump: want exit 2, got %v", k, err)
		}
	}
}

func errorsAs(err error, target **exec.ExitError) bool {
	e, ok := err.(*exec.ExitError)
	if ok {
		*target = e
	}
	return ok
}

// cleanup: 덤프는 늘 지우고, 백업은 이름을 받았을 때만 지운다 (비면 남긴다). 다른 복원이 남긴 백업은 건드리지 않는다
func TestRestoreCleanupKeepsBackup(t *testing.T) {
	run := func(c Cmd) {
		t.Helper()
		if out, err := exec.Command(c.Argv[0], c.Argv[1:]...).CombinedOutput(); err != nil {
			t.Fatalf("%s: %v %s", c.Name, err, out)
		}
	}
	exists := func(p string) bool { _, err := os.Stat(p); return err == nil }
	dir := t.TempDir()
	files := []string{"restore.dump", "backup-a.sql.gz", "backup-old.sql.gz"}
	for _, f := range files {
		os.WriteFile(filepath.Join(dir, f), []byte("x"), 0o600)
	}
	run(RestoreCleanup(dir, ""))
	if exists(filepath.Join(dir, "restore.dump")) || !exists(filepath.Join(dir, "backup-a.sql.gz")) {
		t.Fatal("keep: dump must go, backup must stay")
	}
	os.WriteFile(filepath.Join(dir, "restore.dump"), []byte("x"), 0o600)
	run(RestoreCleanup(dir, "backup-a.sql.gz"))
	if exists(filepath.Join(dir, "restore.dump")) || exists(filepath.Join(dir, "backup-a.sql.gz")) || !exists(filepath.Join(dir, "backup-old.sql.gz")) {
		t.Fatal("remove: dump and this backup must go, other backups stay")
	}
	// 백업·rollback은 이름을 셸 문자열이 아니라 $2로 받는다
	for _, c := range []Cmd{RestoreBackup("/w", "backup-$(id).sql.gz"), RestoreRollback("/w", "backup-$(id).sql.gz")} {
		if strings.Contains(c.Argv[2], "$(id)") || c.Argv[5] != "backup-$(id).sql.gz" || !strings.Contains(c.Argv[2], `"$1/$2"`) {
			t.Fatalf("%s argv %q", c.Name, c.Argv)
		}
	}
}

// 남은 백업 검사는 실제 셸로 본다: 백업만 한 줄씩, 없거나 디렉토리가 없으면 출력 없이 exit 0
func TestRestorePending(t *testing.T) {
	list := func(dir string) string {
		t.Helper()
		c := RestorePending(dir)
		out, err := exec.Command(c.Argv[0], c.Argv[1:]...).Output()
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		return string(out)
	}
	dir := t.TempDir()
	if got := list(filepath.Join(dir, "missing")); got != "" {
		t.Fatalf("missing dir: %q", got)
	}
	os.WriteFile(filepath.Join(dir, "restore.dump"), []byte("x"), 0o600)
	if got := list(dir); got != "" {
		t.Fatalf("no backup: %q", got)
	}
	for _, f := range []string{"backup-b.sql.gz", "backup-a.sql.gz", "backup-a.sql.gz.tmp"} {
		os.WriteFile(filepath.Join(dir, f), []byte("x"), 0o600)
	}
	want := filepath.Join(dir, "backup-a.sql.gz") + "\n" + filepath.Join(dir, "backup-b.sql.gz") + "\n"
	if got := list(dir); got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}
