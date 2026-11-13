// Package restore는 덤프 복원의 단계 상태기계다.
//
//	precheck → backup(MySQL만) → upload → verify → load → check → (rollback, MySQL) → cleanup(항상)
//
// 원본의 우회 구조(임시 파일 → WebSocket close로 EOF 대신 → 두 번째 exec → echo 마커로 exit code)는 쓰지 않는다.
// 단계마다 exec 하나이고, exit code는 K8s 종료 상태로 받는다. 업로드 끝은 CLOSE(255, 0) → stdin EOF다.
//
// db_changed는 core가 재시도해도 되는지 판단하는 값이다.
//   - load 전 실패(precheck·backup·upload·verify): false
//   - PostgreSQL load가 exit code로 실패: false (ON_ERROR_STOP=1 + 한 트랜잭션이라 이미 롤백됨)
//   - MySQL은 load가 시작되면 true. rollback이 성공해도 true로 둔다: backup 뒤에 앱이 쓴 내용은
//     rollback으로 사라졌을 수 있어서 "손대지 않은 상태"라고 할 수 없다. 자동 재시도도 막는다.
//   - load 도중 연결이 끊기면 두 DB 모두 true다. 컨테이너 안 적재는 계속 돌 수 있고(PostgreSQL도 끝나면 커밋된다),
//     결과를 모른다.
//
// 백업 파일(MySQL)은 복원마다 다른 이름이다. 결과를 모르는 경우(load 중 연결 끊김, rollback 실패)에는
// cleanup이 백업을 지우지 않고 남기고, 결과 JSON의 backup에 위치를 싣는다. 수동 복구는 kodeploy hack/exec-e2e.md.
//
// precheck는 먼저 작업 디렉토리에 남은 백업이 있는지 본다. 있으면 이전 복원이 미해결 상태다: 새 복원은 DB를
// 건드리지 않고 거부한다(db_changed=false, unresolved_backups에 위치). 새 복원이 DB를 또 바꾸면 남은 백업과
// 지금 DB를 비교해 판단할 근거가 사라진다. 운영자가 확인하고 백업을 지우면 다시 받는다.
package restore

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"path"
	"strconv"
	"strings"
	"time"

	"github.com/kodeploy/kodeploy/exec/internal/ops"
	"github.com/kodeploy/kodeploy/exec/internal/target"
)

// ExecFunc는 DB 컨테이너에서 명령 하나를 돌린다. exit code를 돌려주고,
// 명령이 끝나지 못했으면(연결 실패 등) error를 돌려준다.
type ExecFunc func(ctx context.Context, cmd ops.Cmd, stdin io.Reader, stdout, stderr io.Writer) (int, error)

// Params는 복원 요청 하나다. Size·SHA256은 core가 stage할 때 계산한 기대값이다.
type Params struct {
	Kind    target.Kind
	Workdir string
	Size    int64
	SHA256  string // 소문자 hex
	// Backup은 작업 디렉토리 안 백업 파일 이름이다. 비우면 Run이 BackupName으로 만든다.
	Backup string
}

// BackupName은 복원마다 다른 백업 파일 이름이다: backup-<UTC 시각>-<무작위 6자>.sql.gz
func BackupName(now time.Time) string {
	b := make([]byte, 3)
	_, _ = rand.Read(b)
	return "backup-" + now.UTC().Format("20060102T150405Z") + "-" + hex.EncodeToString(b) + ".sql.gz"
}

// 채널 3 결과
type Step struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Detail string `json:"detail,omitempty"`
}

type Table struct {
	Name string `json:"name"`
	Rows int64  `json:"rows"` // 행 수를 못 읽었으면 -1
}

type Result struct {
	Status     string  `json:"status"` // Success | Failure
	FailedStep string  `json:"failed_step,omitempty"`
	Steps      []Step  `json:"steps"`
	Tables     []Table `json:"tables"`
	Output     string  `json:"output"`
	DBChanged  bool    `json:"db_changed"`
	// Backup은 결과를 모르는 MySQL 복원에서 지우지 않고 남긴 백업이다. 그 밖에는 없다.
	Backup *Backup `json:"backup,omitempty"`
	// Unresolved는 precheck가 찾은, 이전 복원이 남긴 백업이다. 있으면 이 복원은 precheck에서 거부됐다.
	Unresolved []Backup `json:"unresolved_backups,omitempty"`
}

// Backup은 남긴 백업의 위치다. Pod·Container는 api가 채운다 (restore는 대상 Pod를 모른다).
type Backup struct {
	Pod       string `json:"pod,omitempty"`
	Container string `json:"container,omitempty"`
	Path      string `json:"path"`
}

const (
	maxOutputLines = 50
	maxTables      = 200
	captureBytes   = 64 << 10
	// rollback·cleanup은 요청이 끝나도(시간 초과·연결 끊김) 돌아야 하는 정리라서 따로 시한을 준다
	rollbackTimeout = 10 * time.Minute
	cleanupTimeout  = time.Minute
)

type run struct {
	ctx  context.Context
	exec ExecFunc
	p    Params
	res  Result
	// keepBackup: 복원 결과를 모른다 — cleanup이 백업을 지우지 않는다
	keepBackup bool
}

func (r *run) step(name string, ok bool, detail string) {
	r.res.Steps = append(r.res.Steps, Step{Name: name, OK: ok, Detail: detail})
	if !ok && r.res.FailedStep == "" {
		r.res.FailedStep = name
	}
}

// Run은 복원을 끝까지 돌린다. 실패도 Result로 돌려준다(cleanup은 항상 돈다).
func Run(ctx context.Context, exec ExecFunc, upload io.Reader, p Params) Result {
	if p.Backup == "" {
		p.Backup = BackupName(time.Now())
	}
	r := &run{ctx: ctx, exec: exec, p: p, res: Result{Status: "Failure", Steps: []Step{}, Tables: []Table{}}}
	r.steps(upload)
	r.cleanup()
	return r.res
}

func (r *run) steps(upload io.Reader) {
	p := r.p
	if d, ok := r.pending(); !ok {
		r.step("precheck", false, d)
		return
	}
	if d, ok := r.space(); !ok {
		r.step("precheck", false, d)
		return
	} else {
		r.step("precheck", true, d)
	}

	if p.Kind == target.MySQL {
		if code, out, err := r.run(ops.RestoreBackup(p.Workdir, p.Backup), nil); err != nil || code != 0 {
			r.step("backup", false, describe(code, err, out))
			return
		}
		// backup이 공간을 먹었으니 업로드 몫이 아직 남았는지 다시 본다
		if d, ok := r.space(); !ok {
			r.step("backup", false, "after backup: "+d)
			return
		}
		r.step("backup", true, "")
	}

	gz, d, ok := r.upload(upload)
	r.step("upload", ok, d)
	if !ok {
		return
	}
	if d, ok := r.verify(gz); !ok {
		r.step("verify", false, d)
		return
	} else {
		r.step("verify", true, d)
	}

	cmd, err := ops.RestoreLoad(p.Kind, p.Workdir, gz)
	if err != nil {
		r.step("load", false, err.Error())
		return
	}
	errLines := newTail(maxOutputLines)
	code, err := r.exec(r.ctx, cmd, nil, io.Discard, errLines)
	r.res.Output = errLines.String()
	if err != nil {
		// 적재 도중 연결이 끊겨 결과를 모른다. 컨테이너 안 적재가 계속 돌 수 있어 rollback하지 않고 백업을 남긴다.
		r.res.DBChanged = true
		r.keepBackup = true
		r.step("load", false, "load did not finish: "+err.Error()+"; rollback skipped")
		return
	}
	r.step("load", code == 0, "exit="+strconv.Itoa(code))
	r.res.FailedStep = "" // load의 exit code 판단은 check가 한다 (실패해도 check로 넘어간다)
	r.res.DBChanged = p.Kind == target.MySQL || code == 0

	if code != 0 {
		if p.Kind == target.Postgres {
			r.step("check", false, fmt.Sprintf("load failed (exit %d); PostgreSQL transaction rolled back", code))
			return
		}
		r.step("check", false, fmt.Sprintf("load failed (exit %d)", code))
		r.rollback()
		return
	}

	tables, d := r.tables()
	r.res.Tables = tables
	r.step("check", true, d)
	r.res.Status = "Success"
}

// run은 ctx로 명령을 돌리고 stdout과 stderr를 모아 준다(각각 상한까지).
func (r *run) run(cmd ops.Cmd, stdin io.Reader) (int, string, error) {
	return runWith(r.ctx, r.exec, cmd, stdin)
}

func runWith(ctx context.Context, exec ExecFunc, cmd ops.Cmd, stdin io.Reader) (int, string, error) {
	var out, errb capBuf
	code, err := exec(ctx, cmd, stdin, &out, &errb)
	text := out.String()
	if e := strings.TrimSpace(errb.String()); e != "" {
		text = strings.TrimSpace(text + "\n" + e)
	}
	return code, text, err
}

func describe(code int, err error, out string) string {
	if err != nil {
		return "exec: " + err.Error()
	}
	return fmt.Sprintf("exit=%d %s", code, lastLines(out, 5))
}

// pending은 이전 복원이 남긴 백업이 있는지 본다. 있으면 그 위치를 결과에 싣고 거부한다.
func (r *run) pending() (string, bool) {
	var out, errb capBuf
	code, err := r.exec(r.ctx, ops.RestorePending(r.p.Workdir), nil, &out, &errb)
	if err != nil || code != 0 {
		return "backup check failed: " + describe(code, err, errb.String()), false
	}
	var paths []string
	for _, l := range strings.Split(out.String(), "\n") {
		if l = strings.TrimSpace(l); l != "" {
			paths = append(paths, l)
			r.res.Unresolved = append(r.res.Unresolved, Backup{Path: l})
		}
	}
	if len(paths) == 0 {
		return "", true
	}
	return "previous restore unresolved; backup remains: " + strings.Join(paths, ", "), false
}

// space는 작업 디렉토리의 남은 공간이 size × 2 이상인지 본다(df /tmp).
func (r *run) space() (string, bool) {
	var out, errb capBuf
	code, err := r.exec(r.ctx, ops.RestorePrecheck(r.p.Workdir), nil, &out, &errb)
	if err != nil {
		return "exec: " + err.Error(), false
	}
	if code == 3 {
		return strings.TrimSpace(errb.String()), false // missing:<도구>
	}
	if code != 0 {
		return describe(code, nil, errb.String()), false
	}
	avail, ok := parseDF(out.String())
	if !ok {
		return "cannot parse df output", false
	}
	need := 2 * r.p.Size
	d := fmt.Sprintf("available=%d need=%d", avail, need)
	return d, avail >= need
}

// parseDF는 df -Pk 마지막 줄의 Available(KB)을 바이트로 바꾼다.
func parseDF(out string) (int64, bool) {
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) < 2 {
		return 0, false
	}
	f := strings.Fields(lines[len(lines)-1])
	if len(f) < 4 {
		return 0, false
	}
	kb, err := strconv.ParseInt(f[3], 10, 64)
	if err != nil {
		return 0, false
	}
	return kb * 1024, true
}

// upload는 클라이언트 바이트를 작업 디렉토리에 쓰고, 받은 크기·sha256을 여기서도 계산해 기대값과 비교한다.
func (r *run) upload(src io.Reader) (gz bool, detail string, ok bool) {
	cr := &countReader{r: src, limit: r.p.Size}
	h := sha256.New()
	code, out, err := r.run(ops.RestoreUpload(r.p.Workdir), io.TeeReader(cr, h))
	got := hex.EncodeToString(h.Sum(nil))
	detail = fmt.Sprintf("bytes=%d, sha256=%s", cr.n, got)
	switch {
	case err != nil:
		return false, detail + "; exec: " + err.Error(), false
	case code != 0:
		return false, detail + "; " + describe(code, nil, out), false
	case cr.over:
		return false, detail + fmt.Sprintf("; more bytes than expected (%d)", r.p.Size), false
	case cr.err != nil && cr.err != io.EOF:
		return false, detail + "; upload aborted: " + cr.err.Error(), false
	case cr.n != r.p.Size:
		return false, detail + fmt.Sprintf("; expected %d bytes", r.p.Size), false
	case got != r.p.SHA256:
		return false, detail + "; sha256 mismatch (expected " + r.p.SHA256 + ")", false
	}
	return cr.head[0] == 0x1f && cr.head[1] == 0x8b && cr.n >= 2, detail, true
}

// verify는 Pod 안 파일의 크기·sha256을 다시 계산해 비교하고, gzip이면 무결성을 본다.
func (r *run) verify(gz bool) (string, bool) {
	code, out, err := r.run(ops.RestoreVerify(r.p.Workdir), nil)
	if err != nil || code != 0 {
		return describe(code, err, out), false
	}
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if len(lines) < 2 {
		return "cannot parse verify output", false
	}
	size, err := strconv.ParseInt(strings.TrimSpace(lines[0]), 10, 64)
	fields := strings.Fields(lines[1])
	if err != nil || len(fields) == 0 {
		return "cannot parse verify output", false
	}
	sum := strings.ToLower(fields[0])
	d := fmt.Sprintf("bytes=%d, sha256=%s", size, sum)
	if size != r.p.Size || sum != r.p.SHA256 {
		return d + "; does not match expected", false
	}
	if gz {
		if code, out, err := r.run(ops.RestoreGzipTest(r.p.Workdir), nil); err != nil || code != 0 {
			return d + "; gzip -t failed: " + describe(code, err, out), false
		}
		d += ", gzip ok"
	}
	return d, true
}

// rollback은 MySQL을 backup으로 되돌린다. 요청 ctx가 끝났어도 돈다.
func (r *run) rollback() {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.ctx), rollbackTimeout)
	defer cancel()
	code, out, err := runWith(ctx, r.exec, ops.RestoreRollback(r.p.Workdir, r.p.Backup), nil)
	if err != nil || code != 0 {
		r.keepBackup = true // DROP DATABASE 뒤에 멈췄을 수 있다 — 백업이 유일한 사본이다
		r.step("rollback", false, describe(code, err, out))
		r.res.FailedStep = "rollback"
		r.res.Output = strings.TrimSpace(r.res.Output + "\nrollback failed: " + describe(code, err, out))
		return
	}
	r.step("rollback", true, "restored from backup")
}

// cleanup은 덤프와 백업 파일을 지운다. 성공이든 실패든 항상 돈다.
// 결과를 모르는 MySQL 복원이면 백업은 남기고 위치를 결과에 싣는다.
func (r *run) cleanup() {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(r.ctx), cleanupTimeout)
	defer cancel()
	remove, detail := r.p.Backup, ""
	if r.keepBackup && r.p.Kind == target.MySQL {
		remove = ""
		r.res.Backup = &Backup{Path: path.Join(r.p.Workdir, r.p.Backup)}
		detail = "backup kept: " + r.res.Backup.Path
	}
	code, out, err := runWith(ctx, r.exec, ops.RestoreCleanup(r.p.Workdir, remove), nil)
	if err != nil || code != 0 {
		r.res.Steps = append(r.res.Steps, Step{Name: "cleanup", OK: false,
			Detail: strings.TrimPrefix(detail+"; "+describe(code, err, out), "; ")})
		return
	}
	r.res.Steps = append(r.res.Steps, Step{Name: "cleanup", OK: true, Detail: detail})
}

// tables는 표 목록과 행 수다. 실패해도 복원 결과는 바꾸지 않고 detail에 남긴다.
func (r *run) tables() ([]Table, string) {
	cmd, _ := ops.RestoreTables(r.p.Kind)
	code, out, err := r.run(cmd, nil)
	if err != nil || code != 0 {
		return []Table{}, "table list failed: " + describe(code, err, out)
	}
	type ref struct{ schema, name string }
	var refs []ref
	for _, line := range strings.Split(strings.TrimRight(out, "\n"), "\n") {
		if line == "" {
			continue
		}
		if r.p.Kind == target.MySQL {
			refs = append(refs, ref{name: unescapeMySQL(line)})
		} else if s, n, ok := strings.Cut(line, "\t"); ok {
			refs = append(refs, ref{schema: s, name: n})
		}
	}
	note := ""
	if len(refs) > maxTables {
		note = fmt.Sprintf(" (first %d of %d)", maxTables, len(refs))
		refs = refs[:maxTables]
	}
	tables := make([]Table, len(refs))
	for i, t := range refs {
		tables[i] = Table{Name: t.name, Rows: -1}
		if t.schema != "" && t.schema != "public" {
			tables[i].Name = t.schema + "." + t.name
		}
	}
	if len(refs) == 0 {
		return tables, "tables=0"
	}

	// 행 수: 표마다 COUNT(*) 한 줄씩. 표 이름은 DB에서 온 값이라 식별자 인용으로만 넣는다
	var sql strings.Builder
	for i, t := range refs {
		if i > 0 {
			sql.WriteString(" UNION ALL ")
		}
		if r.p.Kind == target.MySQL {
			fmt.Fprintf(&sql, "SELECT %d, COUNT(*) FROM %s", i, quoteMySQL(t.name))
		} else {
			fmt.Fprintf(&sql, "SELECT %d, count(*) FROM %s.%s", i, quotePG(t.schema), quotePG(t.name))
		}
	}
	countCmd, _ := ops.RestoreCount(r.p.Kind)
	code, out, err = r.run(countCmd, strings.NewReader(sql.String()))
	if err != nil || code != 0 {
		return tables, fmt.Sprintf("tables=%d%s; row count failed: %s", len(tables), note, describe(code, err, out))
	}
	for _, line := range strings.Split(out, "\n") {
		i, n, ok := strings.Cut(strings.TrimSpace(line), "\t")
		if !ok {
			continue
		}
		idx, e1 := strconv.Atoi(i)
		rows, e2 := strconv.ParseInt(n, 10, 64)
		if e1 == nil && e2 == nil && idx >= 0 && idx < len(tables) {
			tables[idx].Rows = rows
		}
	}
	return tables, fmt.Sprintf("tables=%d%s", len(tables), note)
}

func quoteMySQL(id string) string { return "`" + strings.ReplaceAll(id, "`", "``") + "`" }
func quotePG(id string) string    { return `"` + strings.ReplaceAll(id, `"`, `""`) + `"` }

// mysql -B 출력의 이스케이프(\t \n \\ \0)를 푼다 (core dbquery._unescape_mysql과 같다).
func unescapeMySQL(s string) string {
	return strings.NewReplacer(`\t`, "\t", `\n`, "\n", `\0`, "\x00", `\\`, `\`).Replace(s)
}

// countReader는 받은 바이트를 세고, 기대 크기를 넘으면 멈춘다. 앞 두 바이트(gzip 판별)도 남긴다.
type countReader struct {
	r     io.Reader
	limit int64
	n     int64
	head  [2]byte
	over  bool
	err   error
}

func (c *countReader) Read(p []byte) (int, error) {
	if c.over {
		return 0, c.err
	}
	n, err := c.r.Read(p)
	for i := 0; i < n && c.n+int64(i) < 2; i++ {
		c.head[c.n+int64(i)] = p[i]
	}
	c.n += int64(n)
	if c.n > c.limit {
		c.over = true
		c.err = fmt.Errorf("more than %d bytes", c.limit)
		return n, c.err
	}
	if err != nil {
		c.err = err
	}
	return n, err
}

// capBuf는 상한까지만 모으는 버퍼다. 넘치는 부분은 버린다(쓰기는 성공으로 둔다).
type capBuf struct{ b bytes.Buffer }

func (c *capBuf) Write(p []byte) (int, error) {
	if room := captureBytes - c.b.Len(); room > 0 {
		if len(p) > room {
			c.b.Write(p[:room])
		} else {
			c.b.Write(p)
		}
	}
	return len(p), nil
}

func (c *capBuf) String() string { return c.b.String() }

// tail은 마지막 n줄만 남기는 writer다 (에러 줄 최대 50줄).
type tail struct {
	n     int
	lines []string
	part  []byte
}

func newTail(n int) *tail { return &tail{n: n} }

func (t *tail) Write(p []byte) (int, error) {
	t.part = append(t.part, p...)
	for {
		i := bytes.IndexByte(t.part, '\n')
		if i < 0 {
			break
		}
		t.push(string(t.part[:i]))
		t.part = t.part[i+1:]
	}
	if len(t.part) > captureBytes { // 줄바꿈 없는 긴 출력
		t.push(string(t.part[:captureBytes]))
		t.part = t.part[:0]
	}
	return len(p), nil
}

func (t *tail) push(line string) {
	if len(line) > 1000 {
		line = line[:1000]
	}
	t.lines = append(t.lines, line)
	if len(t.lines) > t.n {
		t.lines = t.lines[len(t.lines)-t.n:]
	}
}

func (t *tail) String() string {
	lines := t.lines
	if len(t.part) > 0 {
		lines = append(append([]string{}, lines...), string(t.part))
		if len(lines) > t.n {
			lines = lines[len(lines)-t.n:]
		}
	}
	return strings.Join(lines, "\n")
}

func lastLines(s string, n int) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return strings.Join(lines, " | ")
}
