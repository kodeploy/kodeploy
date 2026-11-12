// Package ops는 작업별 exec 명령을 조립한다 (지시서 4-3).
//
// 규칙
//   - 셸 문자열은 이 파일의 상수뿐이다. 사용자 입력(SQL, 앱 이름)은 셸 문자열에 섞지 않는다.
//     SQL은 stdin으로 받아 변수로만 쓰고, 앱 이름이나 고정값은 위치 인자($1, $2)로 넘긴다.
//   - 비밀번호는 명령 인자에 넣지 않는다. 컨테이너 env(차트가 Secret에서 넣은 값)를 그 자리에서 읽는다.
//   - 옵션은 원본(terminal.py, dbquery._query_cmd, dbbrowse._browse_cmd, snapshots._dump_cmd)과 같다.
//     그래야 core 파서(_parse_mysql, _parse_csv)를 그대로 쓸 수 있다.
package ops

import (
	"fmt"

	"github.com/kodeploy/kodeploy/exec/internal/target"
)

// Cmd는 exec 한 번이다. Name은 감사 기록·테스트용 이름이다.
type Cmd struct {
	Name  string
	Argv  []string
	TTY   bool
	Stdin bool
}

// SQL 바이트 상한 (지시서 4-3). 고정값이다.
//
// query: 사용자 상한은 core가 원본대로 20000자로 검사한다. 여기서는 방어용으로 20000자 × UTF-8 최대 4바이트.
// browse: SQL은 core가 만들고, core도 생성 직후 같은 값으로 검사한다.
//
// 둘 다 SQL이 결국 -e/-c 인자 하나로 들어가는데, 인자 하나의 한도(MAX_ARG_STRLEN = 32 × 페이지 크기)는
// 노드 커널 페이지 크기에 따라 128KiB~2MiB로 달라진다. 가장 작은 경우(131072바이트)보다 아래 고정값을 써서
// 노드와 상관없이 같은 동작이 되게 한다. 원본은 이 길이를 넘으면 exec가 E2BIG로 실패했다.
const (
	QueryMaxBytes  = 80000
	BrowseMaxBytes = 128000
)

// 원본 dbbrowse.TIMEOUT_MS
const browseTimeoutMS = 15000

var (
	// 원본 dbbrowse._browse_cmd의 MySQL --init-command (읽기 전용·시간 제한·NO_BACKSLASH_ESCAPES 제거)
	browseMySQLInit = fmt.Sprintf("SET SESSION transaction_read_only = ON, "+
		"SESSION max_execution_time = %d, "+
		"SESSION sql_mode = REPLACE(@@SESSION.sql_mode, 'NO_BACKSLASH_ESCAPES', '')", browseTimeoutMS)
	// 원본 dbbrowse._browse_cmd의 PGOPTIONS
	browsePGOptions = fmt.Sprintf("-c default_transaction_read_only=on "+
		"-c statement_timeout=%d "+
		"-c standard_conforming_strings=on", browseTimeoutMS)
)

func sh(name, script string, args ...string) []string {
	return append([]string{name, "-c", script, "sh"}, args...)
}

// 앱 터미널 프롬프트. "\u@$1\\\$ "는 셸을 거쳐 원본과 같은 PS1 '\u@<앱>\$ '가 된다.
const ps1Assign = `export PS1="\u@$1\\\$ "`

// Shell은 앱 터미널이다. 원본 terminal.py의 명령과 같고, 앱 이름만 셸 문자열 밖($1)으로 뺐다.
func Shell(app string) Cmd {
	const script = ps1Assign + `; command -v bash >/dev/null && exec bash --norc || exec sh`
	return Cmd{Name: "shell", Argv: sh("/bin/sh", script, app), TTY: true, Stdin: true}
}

// DBConsole은 mysql·psql 대화형 콘솔이다. 원본은 MySQL 비밀번호를 -p 인자로 넣었다.
func DBConsole(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `export MYSQL_PWD="$MYSQL_PASSWORD"; exec mysql --default-character-set=utf8mb4 -u "$MYSQL_USER" "$MYSQL_DATABASE"`
		return Cmd{Name: "db-console", Argv: sh("/bin/sh", script), TTY: true, Stdin: true}, nil
	case target.Postgres:
		// 원본처럼 비밀번호 없이 로컬 소켓으로 붙는다
		const script = `exec psql -U "$POSTGRES_USER" "$POSTGRES_DB"`
		return Cmd{Name: "db-console", Argv: sh("/bin/sh", script), TTY: true, Stdin: true}, nil
	}
	return Cmd{}, fmt.Errorf("db console: unsupported kind %q", kind)
}

// RedisConsole은 redis-cli다. 원본은 비밀번호를 -a 인자로 넣었다. REDISCLI_AUTH는 redis-cli 5.0부터 읽는다.
func RedisConsole() Cmd {
	const script = `export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli`
	return Cmd{Name: "redis-console", Argv: sh("/bin/sh", script), TTY: true, Stdin: true}
}

// Query는 DB 콘솔 SQL 실행이다. 원본 _query_cmd와 같은 명령에 SQL만 stdin → 변수로 넘긴다.
// -e/-c로 실행해서 원본과 실행 의미가 같다(psql -c는 한 트랜잭션, 첫 에러에서 중단).
func Query(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `sql=$(cat); export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql --batch --default-character-set=utf8mb4 -u root "$MYSQL_DATABASE" -e "$sql"`
		return Cmd{Name: "query", Argv: sh("/bin/sh", script), Stdin: true}, nil
	case target.Postgres:
		const script = `sql=$(cat); export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql --csv -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$sql"`
		return Cmd{Name: "query", Argv: sh("/bin/sh", script), Stdin: true}, nil
	}
	return Cmd{}, fmt.Errorf("query: unsupported kind %q", kind)
}

// Browse는 DB 탐색이다. 원본 _browse_cmd의 옵션 그대로, SQL 전달은 Query와 같다.
// 고정 옵션 문자열은 따옴표가 섞여 있어 위치 인자($1)로 넘긴다.
func Browse(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `sql=$(cat); export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql --batch --binary-as-hex --default-character-set=utf8mb4 --init-command="$1" -u root "$MYSQL_DATABASE" -e "$sql"`
		return Cmd{Name: "browse", Argv: sh("/bin/sh", script, browseMySQLInit), Stdin: true}, nil
	case target.Postgres:
		const script = `sql=$(cat); export PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="$1"; exec psql -X --csv -P null=NULL -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$sql"`
		return Cmd{Name: "browse", Argv: sh("/bin/sh", script, browsePGOptions), Stdin: true}, nil
	}
	return Cmd{}, fmt.Errorf("browse: unsupported kind %q", kind)
}

// Dump는 원본 _dump_cmd와 같은 gzip 출력이다. set -o pipefail을 더했다:
// 원본은 exit code가 gzip 것이라 mysqldump·pg_dump가 실패해도 0이었다.
func Dump(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `set -o pipefail; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; mysqldump -u root --single-transaction --routines --triggers --no-tablespaces "$MYSQL_DATABASE" | gzip -c`
		return Cmd{Name: "dump", Argv: sh("/bin/bash", script)}, nil
	case target.Postgres:
		const script = `set -o pipefail; export PGPASSWORD="$POSTGRES_PASSWORD"; pg_dump -U "$POSTGRES_USER" --clean --if-exists "$POSTGRES_DB" | gzip -c`
		return Cmd{Name: "dump", Argv: sh("/bin/bash", script)}, nil
	}
	return Cmd{}, fmt.Errorf("dump: unsupported kind %q", kind)
}

// --- 복원 단계 (지시서 4-6). $1은 작업 디렉토리다. ---

// RestorePrecheck는 작업 디렉토리를 만들고 도구를 확인한 뒤 남은 공간(df -Pk)을 출력한다.
// 도구가 없으면 "missing:<이름>"을 stderr에 쓰고 exit 3.
func RestorePrecheck(workdir string) Cmd {
	const script = `mkdir -p "$1" || exit 2; ` +
		`for t in gzip sha256sum; do command -v "$t" >/dev/null || { echo "missing:$t" >&2; exit 3; }; done; ` +
		`df -Pk "$1"`
	return Cmd{Name: "restore.precheck", Argv: sh("/bin/sh", script, workdir)}
}

// RestoreBackup은 MySQL 현재 DB를 작업 디렉토리에 덤프한다 (rollback용).
func RestoreBackup(workdir string) Cmd {
	const script = `set -o pipefail; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; ` +
		`mysqldump -u root --single-transaction --routines --triggers --no-tablespaces "$MYSQL_DATABASE" | gzip -c > "$1/backup.sql.gz"`
	return Cmd{Name: "restore.backup", Argv: sh("/bin/bash", script, workdir)}
}

// RestoreUpload는 stdin을 작업 디렉토리의 restore.dump로 쓴다.
func RestoreUpload(workdir string) Cmd {
	const script = `cat > "$1/restore.dump"`
	return Cmd{Name: "restore.upload", Argv: sh("/bin/sh", script, workdir), Stdin: true}
}

// RestoreVerify는 Pod 안에서 크기와 sha256을 출력한다 (두 줄: 크기, "해시  경로").
func RestoreVerify(workdir string) Cmd {
	const script = `f="$1/restore.dump"; wc -c < "$f" && sha256sum "$f"`
	return Cmd{Name: "restore.verify", Argv: sh("/bin/sh", script, workdir)}
}

// RestoreGzipTest는 gzip 무결성을 확인한다.
func RestoreGzipTest(workdir string) Cmd {
	const script = `gzip -t "$1/restore.dump"`
	return Cmd{Name: "restore.gzip-test", Argv: sh("/bin/sh", script, workdir)}
}

// RestoreLoad는 덤프를 적재한다. $2는 gz|plain.
// MySQL은 원본과 같은 클라이언트(첫 에러에서 멈춤), PostgreSQL은 ON_ERROR_STOP=1 + 한 트랜잭션.
func RestoreLoad(kind target.Kind, workdir string, gz bool) (Cmd, error) {
	mode := "plain"
	if gz {
		mode = "gz"
	}
	switch kind {
	case target.MySQL:
		const script = `set -o pipefail; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; f="$1/restore.dump"; ` +
			`if [ "$2" = gz ]; then gunzip -c "$f" | mysql -u root "$MYSQL_DATABASE"; else mysql -u root "$MYSQL_DATABASE" < "$f"; fi`
		return Cmd{Name: "restore.load", Argv: sh("/bin/bash", script, workdir, mode)}, nil
	case target.Postgres:
		const script = `set -o pipefail; export PGPASSWORD="$POSTGRES_PASSWORD"; f="$1/restore.dump"; ` +
			`if [ "$2" = gz ]; then gunzip -c "$f" | psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -; ` +
			`else psql -X -q -v ON_ERROR_STOP=1 --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$f"; fi`
		return Cmd{Name: "restore.load", Argv: sh("/bin/bash", script, workdir, mode)}, nil
	}
	return Cmd{}, fmt.Errorf("restore load: unsupported kind %q", kind)
}

// RestoreTables는 표 목록을 탭 구분으로 출력한다. MySQL은 "이름", PostgreSQL은 "스키마\t이름".
func RestoreTables(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -N -B -u root "$MYSQL_DATABASE" -e 'SHOW TABLES'`
		return Cmd{Name: "restore.tables", Argv: sh("/bin/sh", script)}, nil
	case target.Postgres:
		const script = `export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -At -F "$(printf '\t')" -U "$POSTGRES_USER" -d "$POSTGRES_DB" ` +
			`-c 'SELECT schemaname, relname FROM pg_catalog.pg_stat_user_tables ORDER BY 1, 2'`
		return Cmd{Name: "restore.tables", Argv: sh("/bin/sh", script)}, nil
	}
	return Cmd{}, fmt.Errorf("restore tables: unsupported kind %q", kind)
}

// RestoreCount는 stdin으로 받은 SQL(표마다 행 수, restore 패키지가 만든다)을 돌려 탭 구분으로 출력한다.
func RestoreCount(kind target.Kind) (Cmd, error) {
	switch kind {
	case target.MySQL:
		const script = `sql=$(cat); export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql -N -B -u root "$MYSQL_DATABASE" -e "$sql"`
		return Cmd{Name: "restore.count", Argv: sh("/bin/sh", script), Stdin: true}, nil
	case target.Postgres:
		const script = `sql=$(cat); export PGPASSWORD="$POSTGRES_PASSWORD"; exec psql -X -At -F "$(printf '\t')" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$sql"`
		return Cmd{Name: "restore.count", Argv: sh("/bin/sh", script), Stdin: true}, nil
	}
	return Cmd{}, fmt.Errorf("restore count: unsupported kind %q", kind)
}

// RestoreRollback은 MySQL을 backup 파일로 되돌린다. load가 만든 표까지 지우려고 DB를 다시 만든 뒤 적재한다.
// DB 이름은 차트가 넣은 env(MYSQL_DATABASE = app)이고 사용자 입력이 아니다.
func RestoreRollback(workdir string) Cmd {
	script := `set -o pipefail; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; db="$MYSQL_DATABASE"; [ -n "$db" ] || exit 2; ` +
		"mysql -u root -e \"DROP DATABASE IF EXISTS \\`$db\\`; CREATE DATABASE \\`$db\\`\" && " +
		`gunzip -c "$1/backup.sql.gz" | mysql -u root "$db"`
	return Cmd{Name: "restore.rollback", Argv: sh("/bin/bash", script, workdir)}
}

// RestoreCleanup은 덤프와 백업 파일을 지운다. 성공이든 실패든 항상 돈다.
func RestoreCleanup(workdir string) Cmd {
	const script = `rm -f "$1/restore.dump" "$1/backup.sql.gz"`
	return Cmd{Name: "restore.cleanup", Argv: sh("/bin/sh", script, workdir)}
}
