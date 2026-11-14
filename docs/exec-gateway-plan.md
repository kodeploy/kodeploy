# KoDeploy exec 게이트웨이 작업 지시서

Claude Code 작업 지시서다. core가 직접 하던 Pod exec(앱·DB·Redis 터미널, DB 콘솔, 덤프, 복원)를 **테넌트마다 하나씩 뜨는 Go 서비스 `kodeploy-exec`** 로 옮기고, 마지막에 core에서 `pods/exec` 권한을 뺀다. 설계 논의에서 "gateway"라 부르던 것이다. Gateway API의 `Gateway`(kodeploy)와 헷갈리지 않게 이름은 `kodeploy-exec`로 한다.

원칙:
- 원본에서 찾을 수 있는 값은 추측하지 말고 원본에서 가져온다. 원본: `core/app/deploy/console/terminal.py`, `dbquery.py`, `dbbrowse.py`, `snapshots.py`, `core/app/deploy/router.py`(터미널·DB·스냅샷 엔드포인트), `core/app/deploy/stack/resources.py`(`_ensure_tenant_ns`), `core/app/deploy/build/naming.py`, `core/app/builder/sign.py`, `builder/internal/sign/`, `deploy/k8s/core/rbac.yaml`, `deploy/k8s/core/admission-policy.yaml`, `deploy/k8s/calico/globalnetworkpolicy.yaml`, `deploy/k8s/monitoring/vmagent.yaml`, 차트 저장소 `kodeploy/kodeploy-charts`.
- **예외: 복원의 우회 구조(임시 파일 → WebSocket close로 EOF 대신 → 두 번째 exec → echo 마커로 exit code)는 옮기지 않는다.** 4-6의 단계식으로 새로 만든다.
- 저장소 두 개를 쓴다. `kodeploy/kodeploy`(exec 모듈, core, web, VAP, RBAC, vmagent)와 `kodeploy/kodeploy-charts`(차트, AppProject). `kodeploy/kodeploy-apps`는 빌더가 커밋하는 앱별 values뿐이라 손대지 않는다.
- 이번 범위 밖: CLI, scale-to-zero(실측 후 결정, 11절), 감사 로그의 영구 보관, 기록 자동 대조, 세션 녹화, 명령 허용 목록, 공개 문의 `app exec` 작업, 티켓 발급 기록 전용 테이블.
- 서명 밖 값(헤더 등)은 기록용으로만 쓰고, 실행·권한 판단에는 쓰지 않는다.
- 단계(10절)마다 멈추고, 바꾼 파일과 테스트 결과를 보고한다. 원본과 다르게 처리한 부분은 이유를 같이 적는다.
- 12절은 0단계에서 확인하고 결정했다(2026-11-12). 이후 단계에서 실제가 12절과 다르면 추측해서 진행하지 말고 멈추고 보고한다.
- 1~4단계 구현 중 정한 것과 그 뒤 두 차례 수정(2026-11-13)은 각 절에 반영했고, 15절에 이유와 함께 모았다.

---

## 1. 무엇이 어디로 가나

| 지금 (core, 파이썬) | 원본 위치 | 이후 |
|---|---|---|
| 앱·DB·Redis 터미널 WS 중계 (텍스트, 채널 번호 버림, `{"__resize":` 접두어) | `terminal.py` `_relay`, `router.py` 터미널 엔드포인트 | 웹 → `kodeploy-exec` 공개 문. 채널 프로토콜, 바이트 그대로 |
| DB 콘솔 (`bash -c` + SQL 인자, 채널 1/2/3 분리) | `dbquery.py` `_exec`, `_query_cmd` | core → 내부 문 `query`. 원본 명령 그대로, SQL만 stdin + CLOSE |
| DB 탐색 (`bash -c` + SQL 인자, 읽기 전용·시간 제한 세션) | `dbbrowse.py` `_browse_cmd` (실행은 `dbquery._exec`) | core → 내부 문 `browse`. 원본 옵션 그대로, SQL만 stdin + CLOSE |
| 덤프 내보내기 (stdout 스트림, exit code 무시, pipefail 없음) | `snapshots.py` `export_stream`, `_dump_cmd` | core → 내부 문 `dump`. `set -o pipefail` 추가, 실패하면 core가 다운로드 스트림을 끊음 |
| 업로드 복원 (`cat >` → close → 적재 → 마커) | `snapshots.py` `restore`, `_write_dump`, `_exec_capture` | 업로드도 stage를 먼저 거침 → 내부 문 `restore` 단계식 |
| 배포 시 초기 복원 | `snapshots.py` `restore_staged`, `build/v2.py` | 같은 내부 문 `restore`. 먼저 exec Pod Ready를 최대 120초 기다린다(6-3) |
| Pod 찾기 (라벨) | `terminal.py` `_find_app_pod`, `snapshots.py` `_detect_target` | `kodeploy-exec` `target` |
| DB 비밀번호를 core가 Secret에서 읽어 명령에 넣음 | `snapshots.py`, `dbquery.py`, `dbbrowse.py`, `terminal.py` | 컨테이너 env에서 읽음(4-3 표). exec SA에는 Secret 권한 없음 |
| core-tenant의 `pods/exec` | `rbac.yaml` `kodeploy-core-tenant` | 마지막 단계에서 제거 (사람이 적용) |
| core에 남는 것 | 로그인, 권한 판단("터미널은 앱 주인만"), 감사 기록, stage 저장, 결과 파싱, `pods` list, `pods/log` | 그대로 |

---

## 2. 구조

Go 모듈 `github.com/kodeploy/kodeploy/exec`, Go 1.25 + client-go v0.35 (builder와 맞춘다: `builder/go.mod`가 go 1.25.0, client-go v0.35.9. client-go v0.35가 Go 1.25를 요구한다). 의존성은 client-go(+ k8s.io/api, apimachinery), `github.com/gorilla/websocket`(client-go가 이미 의존)만.

```
exec/
  cmd/kodeploy-exec/main.go
  internal/
    config/      env 로드
    sign/        HMAC 검증 (builder/internal/sign 복사. 같은 규칙)
    ticket/      발급·저장·소비 (메모리, 30초, 1회용), sha256 해시
    api/         공개 리스너(8080), 내부 리스너(8081), 지표(9090), /healthz
    proto/       채널 프레임 인코딩·디코딩, close 코드, Status JSON
    target/      Pod·컨테이너 선택 (자기 ns만)
    ops/         작업별 명령 조립: shell, db console, redis console, query, browse, dump, restore
    restore/     복원 단계 상태기계
    session/     remotecommand 연결, 프레임 ↔ 스트림 연결, 창 크기, exit code, 제한
    audit/       JSON 한 줄 로그 + 지표
  internal/testclient/   E2E용 Go 클라이언트 (나중에 CLI의 바탕)
  Dockerfile
.github/workflows/build-exec.yml
```

배포 단위는 테넌트 ns마다 Deployment 1개(replica 1, 상시 실행)이고, 차트가 만들고 Argo CD가 적용한다. 처음에는 카나리로 차트 `rollout` 목록의 ns에만 그린다(6-2, 6-3). 프로세스는 **자기 ns만** 다룬다(`EXEC_NAMESPACE`는 downward API로 받는다). 다른 ns의 요청은 4403으로 거부하고, K8s RBAC도 다른 ns를 막는다.

---

## 3. 계약

### 3-1. core → kodeploy-exec 내부 문 (8081)

- 서명은 builder 규칙 그대로다(`X-Kodeploy-Timestamp`, `X-Kodeploy-Signature`, 60초 창, path는 **쿼리를 뺀 경로 원문** `EscapedPath()`). `sign/`은 `builder/internal/sign`을 그대로 복사하고, core도 `app/builder/sign.py`를 그대로 쓴다.
- 그래서 작업 이름과 기대값은 쿼리가 아니라 **경로**에 넣는다. 내부 문은 쿼리 문자열을 읽지 않는다(서명 범위 밖이다).
- 서명 밖 값은 기록용으로만 쓰고 실행·권한 판단에는 쓰지 않는다. 내부 문이 서명 밖으로 받는 값은 `X-Kodeploy-Request-Id` 헤더 하나다(아래 내부 세션, 4-5).
- 키는 테넌트별 Secret `kodeploy-exec-hmac`(32바이트 무작위)이다. core가 ns를 만들 때 같이 만들고, exec Pod는 파일로 마운트한다.
- 접속은 NetworkPolicy로 core Pod에서만 가능하다.
- 내부 세션도 `kodeploy.exec.v1` 서브프로토콜을 요구한다. Origin은 보지 않는다(인증은 HMAC이고 core는 Origin을 보내지 않는다).

**티켓 발급** `POST /internal/v1/tickets`

```json
{"user_id": "...", "app_id": "...", "app": "<앱 이름>", "target": "app|db|redis", "op": "shell|console", "cols": 120, "rows": 32}
```

- `201 {"ticket": "<base64url 32바이트>", "expires_at": "..."}`
- `400`: target과 op 조합이 틀림. `404`: 대상 Pod가 없음. `401`: 서명 실패. `429`: 대기 중인 티켓이 상한(`TICKET_MAX`)에 닿음.
- exec는 발급도 감사 기록 한 줄로 남긴다(티켓은 sha256만).
- 허용 조합은 `app+shell`, `db+console`, `redis+console`뿐이다.
- `app`(앱 이름)은 대상 Pod 선택에, `user_id`·`app_id`는 감사 기록(4-5)에 쓴다. **exec는 `app_id`를 기록만 하고 판단에 쓰지 않는다**(대상 선택은 `app`, 범위는 자기 ns).

**내부 세션** (WebSocket 업그레이드, 서명 필요)

- `GET /internal/v1/sessions/{query|dump|browse}`
- `GET /internal/v1/sessions/restore/{size}/{sha256}`: `size`는 10진수 바이트 수, `sha256`은 hex 64자. 이 값이 기대값이다. 형식이 틀리면 400.
- core는 `X-Kodeploy-Request-Id` 헤더로 요청 id를 넘긴다. exec는 감사 기록(4-5)에 `request_id`로 남기기만 한다. 서명 밖 값이라 실행·권한 판단에는 쓰지 않는다.
- 내부 세션에는 티켓이 없어서 `user_id`·`app_id`를 받지 않는다(감사 기록에서 비워 둔다). 앱은 `ns`로 식별하고(ns 하나 = 앱 하나), core 로그와는 `request_id`로 잇는다.
- 프레임 규칙은 3-3과 같다.

### 3-2. 웹 → kodeploy-exec 공개 문 (8080)

- 경로는 `wss://api.kodeploy.com/exec/<ns>/v1/sessions`이다. 테넌트 HTTPRoute가 이 테넌트 Pod로 보낸다. 서버는 `<ns>`가 자기 ns인지 확인한다.
- 티켓 전달은 두 가지를 받는다.
  - 브라우저: `Sec-WebSocket-Protocol: kodeploy.exec.v1, ticket.<티켓>`. 서버 응답은 `kodeploy.exec.v1`.
  - 나중의 CLI용: `Authorization: Ticket <티켓>` + `Sec-WebSocket-Protocol: kodeploy.exec.v1`. 이번에 같이 받게 만들어 둔다.
- **티켓은 URL(쿼리)로 받지 않는다.**
- `kodeploy.exec.v1`이 협상되지 않으면 연결을 거부한다(옛 클라이언트 대비).
- **Origin**: 헤더가 있으면 허용 목록(`EXEC_ALLOWED_ORIGINS`, 대소문자 무시)에 있어야 하고, 없으면(CLI·서버 측 클라이언트) 통과한다.
  - 목록 밖이면 업그레이드 전에 HTTP 403으로 끝내고 감사 기록(`error: origin not allowed`)을 남긴다. 티켓은 쓰지 않는다.
  - 목록은 차트 `platform/exec.yaml`의 `allowedOrigins`(운영 웹 https만)다. 로컬 web dev 서버(5173)는 `devOrigins`에 두고 꺼 둔다(6-2). core `ALLOWED_ORIGINS`는 5173을 받으므로, 로컬 web에서는 티켓 발급까지 되고 exec 업그레이드에서 403이다.

### 3-3. 프레임

바이너리 프레임이고, 첫 바이트가 채널 번호다. 텍스트 프레임은 받지 않는다.

| 채널 | 방향 | 내용 |
|---|---|---|
| 0 | 클라이언트 → 서버 | stdin |
| 1 | 서버 → 클라이언트 | stdout |
| 2 | 서버 → 클라이언트 | stderr (비대화형만. tty면 K8s가 stdout으로 합침) |
| 3 | 서버 → 클라이언트 | 종료 상태 JSON. 일반 작업은 K8s `metav1.Status` 그대로. `restore`는 3-5 형식 |
| 4 | 클라이언트 → 서버 | 창 크기 `{"Width":120,"Height":32}` |
| 255 | 클라이언트 → 서버 | CLOSE. 두 번째 바이트는 닫을 채널 번호(보통 0) |

### 3-4. WebSocket close 코드

| 코드 | 의미 |
|---|---|
| 1000 | 정상 종료 (채널 3을 보낸 뒤) |
| 1003 | 텍스트 프레임 (RFC 6455 표준 코드) |
| 1011 | exec를 시작하지 못함 등 서버 쪽 오류 (RFC 6455 표준 코드) |
| 4400 | stdin 상한 초과(query 80000·browse 128000바이트, restore `RESTORE_MAX_BYTES`), 이 작업에 없는 채널, 잘못된 프레임 |
| 4401 | 티켓 없음·만료·이미 사용 |
| 4403 | 이 문에서 허용하지 않는 작업, 또는 다른 ns |
| 4404 | 대상 Pod 없음 |
| 4408 | 유휴 타임아웃 (tty만) |
| 4413 | 최대 실행 시간 초과. restore는 결과 JSON(채널 3)을 먼저 보낸 뒤 닫는다(core가 `db_changed`를 알아야 한다) |
| 4429 | 동시 세션 수 초과(문마다 따로, 4-4), 또는 그 ns에서 복원이 이미 진행 중 |
| 4503 | 종료 중 (SIGTERM) |

### 3-5. restore 결과 (채널 3)

```json
{"status": "Success|Failure",
 "failed_step": "precheck|backup|upload|verify|load|check|rollback",
 "steps": [{"name": "upload", "ok": true, "detail": "bytes=..., sha256=..."}],
 "tables": [{"name": "users", "rows": 120}],
 "output": "에러 줄 (최대 50줄)",
 "db_changed": false,
 "backup": {"pod": "mysql-0", "container": "mysql", "path": "/tmp/kodeploy-restore/backup-<UTC>-<6hex>.sql.gz"},
 "unresolved_backups": [{"pod": "...", "container": "...", "path": "..."}]}
```

- `db_changed`는 core가 재시도해도 되는지 판단하는 값이다. `false`면 다시 보내도 안전하다. 규칙은 4-6.
- `backup`은 결과를 모르는 MySQL 복원(load 중 연결 끊김, rollback 실패)에서 지우지 않고 남긴 백업의 위치다. 그 밖에는 없다.
- `unresolved_backups`는 precheck가 찾은, 이전 복원이 남긴 백업이다. 있으면 이 복원은 DB를 건드리지 않고 거부됐다(`failed_step: precheck`, `db_changed: false`).
- `steps`의 `detail`과 `output`은 영어다. 사용자 메시지는 core가 만든다(6-3).

### 3-6. core API (웹용)

`POST /apps/{id}/exec-tickets` `{"target": "app|db|redis"}` → `{"ticket", "url", "protocol": "kodeploy.exec.v1", "expires_in": 30}`

`GET /apps/{id}/exec-features` → `{"terminal": true|false}`: 이 앱의 웹 터미널이 새 경로를 쓰는지. `EXEC_AGENT_TERMINAL`이 켜져 있고 앱 ns가 카나리 목록(`EXEC_AGENT_NAMESPACES`)에 들 때만 true다. 접근 권한이 없으면 404. 웹은 앱마다 한 번 묻고 캐시한다.

- 쿠키 인증과 기존 Origin 검사를 그대로 쓴다. "터미널은 앱 주인만" 규칙도 기존 엔드포인트와 같다.
- 발급 기록
  - 일반 발급: core 로그에 구조화된 한 줄로 남긴다. 필드는 `ticket_sha256`, `user_id`, `app_id`, `target`, `op`, `issued_at`로 고정한다(4-5 exec 감사 기록과 같은 이름). 형식은 3단계에서 정한다. 티켓 원문은 남기지 않는다.
  - 관리자가 남의 앱에 여는 경우만 기존 `audit.record`(admin_actions)도 남긴다. 판단은 기존 터미널 `_ws_app`과 같다(`by_admin`).
  - `admin_actions`는 "관리자 권한으로 남의 앱에 손댄 것만" 남기는 표라 일반 발급을 넣지 않고, 칸도 추가하지 않는다. 전용 테이블은 나중에(14절).
- core는 그 테넌트 exec Pod의 내부 문(`kodeploy-exec.<ns>.svc:8081`)에 발급을 요청하고, 받은 티켓과 `url`을 돌려준다.
- 플래그가 꺼져 있거나 ns가 카나리 밖이면 404다(그 ns에는 exec Pod가 없을 수 있다). 카나리 판단은 접근 권한 확인 뒤에 한다.

---

## 4. 동작

### 4-1. 티켓

- 32바이트 `crypto/rand` 값을 base64url로 인코딩한다.
- 저장 키는 sha256(티켓)이다. 원문은 메모리에도 남기지 않고 발급 응답으로만 내보낸다.
- 꺼내는 즉시 삭제한다(1회용). 30초가 지나면 만료되고, 만료된 항목은 주기적으로 청소한다. 저장 개수 상한은 1000개다.
- 저장하는 내용: `user_id`, `app_id`, 앱 이름, `target`, `op`, cols·rows, `issued_at`, 만료 시각.

### 4-2. 대상 Pod 선택

`terminal.py`와 `snapshots.py`의 라벨·컨테이너 규칙을 그대로 옮긴다. 기준은 app은 `app=<앱 이름>`이고 Running 중 가장 최근 것(컨테이너 `app`), db는 `app=mysql` 또는 `app=postgres`(mysql 먼저), redis는 `app=redis`다. 자기 ns 밖은 조회하지 않는다.

원본과 다른 점: 지워지는 중인 Pod(`deletionTimestamp`)는 뺀다. DB·Redis도 앱처럼 "Running 중 가장 최근 것"으로 고른다(원본은 목록의 첫 번째).

ns 하나에는 앱이 하나뿐이다(`apps.namespace` unique, values 파일 하나에 `name` 하나). 그래서 앱 이름은 티켓에 실린 값 하나로 정해진다. exec Pod 자신은 `app` 라벨을 쓰지 않으므로(6-2) 이 선택에 걸리지 않는다.

### 4-3. 명령 조립

- **shell**: 원본 `terminal.py`의 셸 명령을 그대로 쓴다. tty.
- **db console / redis console**: 원본 명령과 같다. 단 비밀번호는 명령 인자가 아니라 컨테이너 env에서 읽는다(원본은 `-p<비번>`, `-a <비번>`으로 인자에 넣었다).
- 작업별 env (차트에서 확인, 12절 1)

  | 작업 | 쓰는 env |
  |---|---|
  | MySQL 콘솔 (원본 `-u app`) | `MYSQL_PWD="$MYSQL_PASSWORD"`, 사용자 `$MYSQL_USER`, DB `$MYSQL_DATABASE` |
  | MySQL query·browse·dump·restore (원본 root) | `MYSQL_PWD="$MYSQL_ROOT_PASSWORD"`, `-u root`, DB `$MYSQL_DATABASE` |
  | PostgreSQL 콘솔 | 원본처럼 비밀번호 없이(로컬 소켓). 사용자 `$POSTGRES_USER`, DB `$POSTGRES_DB` |
  | PostgreSQL query·browse·dump·restore | `PGPASSWORD="$POSTGRES_PASSWORD"`, 사용자 `$POSTGRES_USER`, DB `$POSTGRES_DB` |
  | Redis 콘솔 | `REDISCLI_AUTH="$REDIS_PASSWORD"` |

  원본이 Secret에서 읽던 `DB_USER`·`DB_PASSWORD`·`DB_NAME`은 컨테이너 env에 없다. 값은 위 env와 같다(core가 항상 기본값 `app`으로 만든다).
- **query**
  - 원본 `_query_cmd`의 명령·옵션을 그대로 쓰고, SQL만 stdin으로 받는다. 셸 문자열은 고정이고 SQL은 변수로만 들어간다.
    - PostgreSQL: `sh -c 'sql=$(cat); PGPASSWORD="$POSTGRES_PASSWORD" exec psql --csv -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$sql"'`
    - MySQL: `sh -c 'sql=$(cat); MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --batch --default-character-set=utf8mb4 -u root "$MYSQL_DATABASE" -e "$sql"'`
  - `-c`·`-e`로 실행하니 실행 의미가 원본과 같다(psql `-c`는 한 트랜잭션, 첫 에러에서 중단). SQL을 stdin 입력으로 바로 먹이면 psql은 문장마다 커밋하고 에러 뒤에도 계속해서 결과가 달라진다.
  - CLOSE(255, 0)를 받아 stdin을 닫아야 `cat`이 끝난다.
  - 출력 형식이 원본과 같으니 core 파서(`_parse_mysql`, `_parse_csv`)를 그대로 쓴다.
  - 사용자 SQL 길이 상한은 core에서만 검사한다(원본 `MAX_SQL_LENGTH` 20000자, 그대로).
  - exec는 방어용으로 stdin 바이트 상한 80000(20000자 × UTF-8 최대 4바이트, 고정값)을 두고, 넘으면 4400으로 닫는다. 인자 하나의 한도보다 낮으므로 `E2BIG`에 닿지 않는다(browse 참고).
- **browse**: 원본 `dbbrowse._browse_cmd`의 옵션을 그대로 쓴다. SQL 전달은 query와 같다(stdin → 변수 → `-e`/`-c`). SQL은 core가 만든다(`build_select`).
  - SQL 상한은 128000바이트로 고정한다. exec는 stdin이 이를 넘으면 거부한다. core도 `build_select` 직후 실제로 돌릴 SQL을 같은 값으로 검사해 `QueryError`(400 사용자 오류)로 돌려준다(6-3).
  - 고정값을 쓰는 이유(코드 주석에 적는다): SQL은 결국 `-e`/`-c` 인자 하나로 들어간다. 인자 하나의 한도(`MAX_ARG_STRLEN` = 32 × 페이지 크기)는 노드 커널 페이지 크기에 따라 128KiB에서 2MiB까지 달라진다. 가장 작은 경우(131072바이트)보다 아래 값으로 고정해 노드와 상관없이 같은 동작이 되게 한다.
  - 원본은 이 길이를 넘으면 exec가 `E2BIG`로 실패했다. 명확한 사용자 오류로 바꿨다. core의 이 검사는 플래그와 상관없이 옛 경로에도 적용한다.
  - MySQL: `--batch --binary-as-hex --default-character-set=utf8mb4 --init-command=<읽기 전용·max_execution_time 15000·sql_mode에서 NO_BACKSLASH_ESCAPES 제거>`, `-u root`
  - PostgreSQL: `PGOPTIONS`에 `default_transaction_read_only=on`, `statement_timeout=15000`, `standard_conforming_strings=on`. `psql -X --csv -P null=NULL`
- **dump**: 원본 `_dump_cmd`와 같은 출력(gzip 바이트)을 채널 1로 흘리고, 끝에 exit code를 채널 3으로 보낸다. 단 `set -o pipefail`을 더한다. 원본은 exit code가 gzip 것이라 mysqldump·pg_dump가 실패해도 0이었다(원본 버그 수정으로 보고한다).
- 사용자 입력(SQL, 앱 이름)이 셸 문자열에 섞이지 않게 한다. 셸이 필요하면 고정 문자열만 쓰고, 값은 env나 stdin으로 넘긴다.

### 4-4. 세션

- client-go `remotecommand.NewWebSocketExecutor`(v5)를 쓰고, 실패하면 SPDY로 넘어가는 `NewFallbackExecutor`를 쓴다. 실행은 `StreamWithContext`.
- 프레임과 스트림을 이렇게 연결한다.
  - 채널 0 → Stdin. CLOSE(255, 0)를 받으면 stdin pipe를 닫는다.
  - Stdout·Stderr → 채널 1·2.
  - 채널 4 → `TerminalSizeQueue`.
- 끝나면 `exec.CodeExitError`에서 exit code를 꺼내 `metav1.Status`로 만들어 채널 3에 보내고, 1000으로 닫는다.
- 바이트는 디코딩하지 않는다.
- goroutine 구성: 클라이언트 → 원격, 원격 → 클라이언트, 창 크기. 하나가 끝나면 context로 나머지를 정리한다.
- **제한**
  - 유휴 타임아웃은 tty만 적용한다(기본 15분). 비대화형은 출력 없이 오래 걸릴 수 있어서다.
  - 최대 실행 시간: tty 2시간, 비대화형 30분.
  - 동시 세션 수: 문마다 따로 센다. 공개 문 3개(`MAX_SESSIONS_PUBLIC`), 내부 문 4개(`MAX_SESSIONS_INTERNAL`). 합계 하나로 세면 터미널 두 개를 연 채 DB 콘솔을 쓸 때 4429가 났다.
  - 복원은 ns당 한 번에 하나다. 진행 중이면 두 번째는 4429("restore already running"). 두 적재가 섞이면 남긴 백업으로도 상태를 판단할 수 없다.
- ping 간격은 30초다(Cloudflare·Envoy 유휴 타임아웃을 확인해서 그보다 짧게 조정).

### 4-5. 감사 기록

- 세션마다 JSON 한 줄을 stdout으로 남긴다. 항목: `ns`, `ticket_sha256`(공개 문), `user_id`, `app_id`, `target`, `op`, `issued_at`(티켓 발급 시각), `request_id`(내부 문), 시작·끝 시각, 송수신 바이트, exit code, close 코드.
- `ticket_sha256`·`user_id`·`app_id`·`target`·`op`·`issued_at`은 core 발급 기록(3-6)과 같은 이름이다. 공개 문 세션은 `ticket_sha256`으로 core 기록과 잇는다.
- 내부 세션은 `ticket_sha256`·`user_id`·`app_id`·`issued_at`이 비어 있다. 앱은 `ns`(ns 하나 = 앱 하나)로 식별하고, core 로그와는 `request_id`로 잇는다.
- `ns`는 자기 ns(`EXEC_NAMESPACE`)라 항상 남는다.
- **SQL 본문, 터미널 입출력, 티켓 원문, HMAC 키는 남기지 않는다.** SQL은 길이와 sha256만 남긴다.
- 지표(9090 `/metrics`, Prometheus 텍스트. 클라이언트 라이브러리 없이 직접 쓴다)

  | 이름 | 내용 |
  |---|---|
  | `kodeploy_exec_sessions_total{op,result}` | 작업별 세션 수 (`result`: ok·fail) |
  | `kodeploy_exec_session_seconds_sum/_count{op}` | 작업별 실행 시간 |
  | `kodeploy_exec_tickets_total{event}` | 티켓 issued·consumed·expired |
  | `kodeploy_exec_sessions_active{door}` | 지금 열린 세션 수 (`door`: public·internal). 상한이 문마다 따로라 문별로 낸다. 합계 줄은 없다(`sum`으로 더한다) |
- 영구 보관은 이번 범위 밖이다. 코드에 TODO만 남긴다.

### 4-6. 복원 단계

| 단계 | 하는 일 | 실패하면 |
|---|---|---|
| precheck | ① 작업 디렉토리에 이전 복원이 남긴 백업(`backup-*.sql.gz`)이 있으면 거부한다(미해결 상태). ② gzip·sha256sum 존재 확인. ③ 남은 공간(`df /tmp`) ≥ size × 2 | 시작 안 함 (`db_changed=false`). ①이면 `unresolved_backups`에 위치 |
| backup (MySQL만) | 현재 DB를 작업 디렉토리의 `backup-<UTC 시각>-<6hex>.sql.gz`(복원마다 다른 이름)로 덤프. 끝나면 남은 공간을 다시 확인(백업 몫) | 시작 안 함 |
| upload | 채널 0 바이트를 `cat > <작업 디렉토리>/restore.dump`로 쓰고, CLOSE 받으면 stdin을 닫은 뒤 cat의 exit code 확인. 받은 바이트 수와 sha256을 gateway에서도 계산 | 파일 삭제, `db_changed=false` |
| verify | Pod 안에서 크기와 `sha256sum`을 계산해 기대값과 비교. gzip(첫 2바이트 `1f 8b`)이면 `gzip -t` | 파일 삭제, `db_changed=false` |
| load | MySQL은 원본과 같은 클라이언트(첫 에러에서 멈춤), PostgreSQL은 `-v ON_ERROR_STOP=1 --single-transaction` | check로 넘어감. 도중에 연결이 끊기면(결과 모름) rollback하지 않고 백업을 남긴다 |
| check | exit code, 에러 줄, 테이블 목록과 행 수 | MySQL은 rollback, PostgreSQL은 이미 롤백됨 |
| rollback (MySQL) | DB를 DROP·CREATE한 뒤 backup 파일로 되돌리기(load가 만든 표까지 지운다) | 결과에 rollback 실패를 명시하고 백업을 남긴다 |
| cleanup | 덤프와 이번 백업 파일 삭제. 성공이든 실패든 항상 실행(요청이 끝나도 따로 시한을 준다). 결과를 모르면(load 중 끊김, rollback 실패) 백업은 남기고 위치를 `backup`에 싣는다 | 결과에 남김 |

- **작업 디렉토리는 두 DB 모두 `/tmp/kodeploy-restore`다.** DB 컨테이너의 쓰기 레이어(노드 디스크)로, 원본 `/tmp/kd_restore.dump`와 같은 곳이다. PVC에는 두지 않는다. MySQL은 PVC 루트가 곧 데이터 디렉토리(`/var/lib/mysql`)이고, local-path라 PVC도 같은 노드 디스크여서 PVC에 둬도 얻는 것이 없다(12절 2).
- **`db_changed` 규칙**
  - load 전 실패(precheck·backup·upload·verify): `false`.
  - PostgreSQL load가 exit code로 실패: `false` (한 트랜잭션이라 이미 롤백됨).
  - MySQL은 load가 시작되면 `true`. rollback이 성공해도 `true`로 둔다. backup 뒤에 앱이 쓴 내용이 rollback으로 사라졌을 수 있어 "손대지 않은 상태"가 아니다.
  - load 도중 연결이 끊기면 두 DB 모두 `true`. 컨테이너 안 적재는 계속 돌 수 있고 PostgreSQL도 끝나면 커밋된다.
- **남긴 백업**: 결과를 모르는 MySQL 복원에서만 남는다. DB 컨테이너 `/tmp`(쓰기 레이어)라 컨테이너가 재시작되면 사라진다. 남아 있는 동안 그 ns의 새 복원은 precheck에서 거부된다. 운영자가 `hack/exec-e2e.md` "복원 백업이 남았을 때"대로 꺼내고·판단하고·되돌린 뒤 백업을 지우면 다시 열린다.
- **재시도는 core가 한다.** `db_changed=false`인 실패면 5초 간격으로 최대 2회 다시 보낸다. 단 `unresolved_backups`가 있으면(다시 보내도 같은 거부) 보내지 않는다. 세션을 열지 못한 실패(연결 실패)도 다시 보내지 않는다. 첫 배포의 초기 복원은 보내기 전에 exec Pod Ready를 기다린다(6-3).
- 크기 상한은 원본 `MAX_DUMP_BYTES`(200 MiB)이다.

### 4-7. 종료

SIGTERM을 받으면 새 연결을 거부한다. tty 세션은 4503으로 닫고, 진행 중인 비대화형 세션(특히 restore)은 `terminationGracePeriodSeconds` 안에서 끝까지 마친다. grace 값은 비대화형 최대 실행 시간에 맞춘다.

---

## 5. 설정 (env)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `EXEC_NAMESPACE` | (downward API) | 자기 ns. 비어 있으면 시작 실패 |
| `EXEC_HMAC_KEY_FILE` | `/etc/kodeploy-exec/hmac` | 테넌트별 Secret 마운트 경로 |
| `PUBLIC_ADDR` / `INTERNAL_ADDR` / `METRICS_ADDR` | `:8080` / `:8081` / `:9090` | |
| `TICKET_TTL` | `30s` | |
| `TICKET_MAX` | `1000` | |
| `TTY_IDLE_TIMEOUT` / `TTY_MAX` / `NONTTY_MAX` | `15m` / `2h` / `30m` | |
| `MAX_SESSIONS_PUBLIC` / `MAX_SESSIONS_INTERNAL` | `3` / `4` | 문마다 따로 센다(4-4). 차트는 기본값을 쓴다 |
| `EXEC_ALLOWED_ORIGINS` | (비어 있음) | 공개 문 Origin 허용 목록, 쉼표 구분 `scheme://host[:port]`. 형식이 틀리면 시작 실패. 비어 있으면 Origin을 보내는 클라이언트(브라우저)는 전부 거부. 차트가 `allowedOrigins`(+ 켜져 있으면 `devOrigins`)로 넣는다(3-2) |
| `PING_INTERVAL` | `30s` | |
| `RESTORE_MAX_BYTES` | 원본 `MAX_DUMP_BYTES` | |
| `RESTORE_WORKDIR` | `/tmp/kodeploy-restore` | DB 컨테이너 안의 경로. 4-6, 12절 2 |

---

## 6. 배포물

### 6-1. kodeploy 저장소

- **Dockerfile**: builder와 같다(`CGO_ENABLED=0 GOOS=linux GOARCH=arm64` → `gcr.io/distroless/static:nonroot`).
- **CI** `.github/workflows/build-exec.yml`: `build-builder.yml`을 본뜬다. `exec/**`가 바뀌면 실행하고, 이미지는 `ghcr.io/kodeploy/kodeploy-exec`. digest를 출력만 한다. 차트에 digest를 반영하는 건 사람이 kodeploy-charts의 `platform/` 파일에 PR로 한다.
- **VAP**: `deploy/k8s/core/admission-policy.yaml` 옆에 새 정책을 둔다. 테넌트 ns(`^(tenant|app)-[0-9a-f]{8}$`)에서 `serviceAccountName: kodeploy-exec`인 Pod는 다음을 모두 만족해야 생성·수정된다.
  - 라벨 `kodeploy.com/component=exec`
  - 컨테이너 정확히 1개, initContainer 없음, ephemeral container 없음(`pods/ephemeralcontainers`도 검사)
  - 이미지가 `ghcr.io/kodeploy/kodeploy-exec@sha256:`로 시작
  - 반대로 라벨이 `kodeploy.com/component=exec`인데 다른 SA를 쓰는 것도 거부
- **vmagent**: `deploy/k8s/monitoring/vmagent.yaml`에 exec 수집 job을 추가한다. `role: pod`로 테넌트 ns의 `kodeploy.com/component=exec` Pod 9090만 keep. vmagent ClusterRole에는 이미 전 ns `pods` list·watch가 있다. 적용은 사람이 한다.
- **core RBAC**: 이번 단계에서는 바꾸지 않는다. `pods/exec` 제거는 10절 5단계에서 별도 PR로 만들고 적용은 사람이 한다.

### 6-2. kodeploy-charts 저장소 (차트)

exec 리소스는 **앱 values에서 아무것도 받지 않는다.** 이름, 이미지 digest, 명령, SA, 볼륨, 포트, apiserver endpoint는 차트에 고정한다. 이미지 digest 같은 플랫폼 값은 기존 방식대로 `platform/*.yaml`에 두고 `.Files.Get`으로 읽는다(앱 values로 덮을 수 없다).

**그리는 조건** (셋 다 맞아야 한다)
- DB·Redis와 같은 조건(`app.depsEnabled`: runtime이 none이 아님). 서버 이미지가 아직 없어도(첫 빌드 전·실패) DB·Redis가 있으면 exec도 있다. 서버 없이 DB·Redis만 있는 앱은 만들 수 없다(core `pipeline.run_deploy`가 거부하고 차트도 그리지 않는다).
- `platform/exec.yaml`의 `image`·`apiserver.ip`가 채워져 있다. 비어 있으면 머지돼도 테넌트에 변화가 없다.
- **카나리**: `rollout.all: true`이거나 ns가 `rollout.namespaces`에 있다. 기본은 `all: false`, 빈 목록. `all`이 bool이 아니면 렌더 실패. core의 `EXEC_AGENT_NAMESPACES`(6-3)와 같은 목록으로 맞춘다. 넓힐 때는 차트 먼저(exec Pod Ready 확인) → core, 좁힐 때는 core 먼저 → 차트.

**`platform/exec.yaml` 키**: `rollout`, `image`, `apiserver.{ip,port}`, `nodeNets`, `publicHost`, `allowedOrigins`, `devOrigins.{enabled,origins}`, `envoySelector`, `resources`, `terminationGracePeriodSeconds`.

exec Pod를 고르는 곳(Deployment selector, Service, NetworkPolicy, VAP, vmagent)은 전부 `app=` 대신 **`kodeploy.com/component: exec`** 라벨을 쓴다. exec Pod에는 `app` 라벨을 붙이지 않고, 셀렉터는 이 라벨 하나만 쓴다. 설명용으로 `app.kubernetes.io/name: kodeploy-exec`, `app.kubernetes.io/part-of: kodeploy`는 붙여도 된다(셀렉터에는 쓰지 않는다).

- `ServiceAccount` `kodeploy-exec`
- `Role` `kodeploy-exec`: `pods` `list`, `pods/exec` `get, create`. 이것만 둔다. Secret 권한은 없다.
- `RoleBinding` `kodeploy-exec`: 같은 ns의 SA에만 연결
- `Deployment` `kodeploy-exec`: replica 1, restricted 보안 설정(`runAsNonRoot`, `readOnlyRootFilesystem`, `allowPrivilegeEscalation: false`, capabilities 전부 drop, seccomp `RuntimeDefault`). SA 토큰 마운트 필요. 요청 CPU 10m·메모리 64Mi, 메모리 상한 128Mi(실측 후 조정). `platform/runtimes.yaml`의 "64·128·… 중 하나" 규칙은 주석일 뿐이고 스키마·템플릿이 거부하지 않는다(0단계 확인). 그래서 CPU 10m을 유지한다. 실제로 거부될 때만 허용 최소값으로 올리고 이유를 주석으로 남긴다. `imagePullSecrets: ghcr-auth`(테넌트 ns마다 core가 만든다). readiness는 9090 `/healthz`, `periodSeconds: 1`. liveness는 두지 않는다(종료 중 503으로 재시작되지 않게). env는 `EXEC_NAMESPACE`(downward API)와 `EXEC_ALLOWED_ORIGINS`(`allowedOrigins`, `devOrigins.enabled`면 `devOrigins.origins`를 뒤에 붙인다. `enabled`가 bool이 아니면 렌더 실패). HMAC Secret은 읽기 전용 볼륨. `terminationGracePeriodSeconds`는 1830초(비대화형 최대 30분 + 30초, 4-7).
- `Service` `kodeploy-exec`: selector `kodeploy.com/component: exec`. 8080, 8081, 9090
- `HTTPRoute`: parentRef `Gateway kodeploy`(default ns, sectionName `https`). hostname `api.kodeploy.com`. `PathPrefix /exec/<ns>` → 8080 (끝 슬래시 없이. Gateway API PathPrefix는 경로 요소 단위로 맞춰서 `/exec/<ns>/`와 같은 뜻이다).
  - **`RequestHeaderModifier`로 Cookie 헤더 제거**(`remove: [Cookie]`). `app.route` 헬퍼에는 필터가 없어서, 그쪽에 맞추다 보면 빠지기 쉽다. 7절 차트 테스트로 확인한다.
  - `X-Origin-Verify`는 차트 `app.route`와 같은 방식으로 맞춘다: `platform/gateway.yaml`의 `originVerify`를 `.Files.Get`으로 읽어 Exact 매칭. private 저장소의 평문이고, 0단계에서 이 방식을 그대로 쓰기로 했다. 헬퍼는 `PathPrefix /`와 values의 hostnames를 쓰므로 그대로는 못 쓴다.
  - http→https 리다이렉트 라우트는 만들지 않는다. 기존 `kodeploy-core-redirect`가 `api.kodeploy.com` 전체를 처리한다.
- `NetworkPolicy`: **Calico** `projectcalico.org/v3` NetworkPolicy, `order: 50`, selector `kodeploy.com/component == 'exec'`, types Ingress·Egress
  - order 50인 이유: 차트의 `tenant-rules`(order 100)가 같은 ns 전체와 Envoy ns를 모든 포트에 허용하고, `tenant-egress`(GlobalNetworkPolicy, order 200)가 사설망(10/8 등)을 막는다. K8s NetworkPolicy는 Calico 안에서 order 1000이라 이 둘이 먼저 결정해 버린다.
  - ingress: Allow TCP 8080 ← `envoy-gateway-system` ns의 Envoy 프록시 Pod(`envoySelector`), Allow TCP 8081 ← `default` ns의 `app == 'kodeploy-core'`, Allow TCP 9090 ← `monitoring` ns의 `app == 'vmagent'`, **Allow TCP 9090 ← 노드 대역 `nodeNets`**(kubelet readiness probe. 셀렉터 없이 nets만). 그다음 명시적 Deny. 같은 ns 앱 Pod와 Envoy의 8081·9090 접속은 여기서 막힌다.
  - `nodeNets`는 노드 INTERNAL-IP 대역이다. 지금 `10.0.2.0/24`(terraform `private_subnet_cidr`). 비면 렌더가 실패한다: Calico에서 빈 nets는 "모든 출발지"라 9090이 열린다. Ready가 안 되면 probe 출발지가 IPIP 터널 주소일 수 있다(`hack/exec-e2e.md` 9절 1번).
  - egress: Allow TCP 6443 → apiserver endpoint 주소(Service ClusterIP가 아니라 실제 endpoint. 차트 `platform/` 상수), Allow UDP·TCP 53. 그다음 명시적 Deny.
- `AppProject`(`argocd/appproject.yaml`, 사람이 kubectl로 적용): 허용 목록 방식 그대로 `ServiceAccount`(core 그룹)와 `Role`(`rbac.authorization.k8s.io`)을 추가한다. `RoleBinding`과 Calico `NetworkPolicy`는 이미 허용돼 있다.
- `allowedOrigins`는 운영 웹 주소(https)만 둔다: `https://app.kodeploy.com`, `https://kodeploy.pages.dev`. 로컬 web dev 서버(`http://localhost:5173`, `http://127.0.0.1:5173`)는 `devOrigins.origins`에 두고 `enabled: false`. 켜면 exec가 있는 모든 테넌트 ns에 적용되므로 잠깐만 켠다. core `ALLOWED_ORIGINS`(kodeploy configMap)는 바꾸지 않았고 5173을 받는다.
- 차트 검증: `tests/exec_check.py`(`helm template` 렌더 + `values.schema.json` 검증 + 저장소 값 형식, 53개 항목). 저장소 값이 지금 어느 ns에 exec를 그리는지도 출력한다. `tests/diff.py`는 v1 코드 삭제 뒤 import가 깨져 있어(`manifests.app` 없음) 쓰지 않는다.

### 6-3. core (kodeploy 저장소)

- `_ensure_tenant_ns`에서 `kodeploy-exec-hmac` Secret이 없으면 만든다. ns를 만드는 경로는 이것 하나이고, `build/v2.py`가 빌더 제출 전에 부르므로 Argo가 동기화하기 전에 Secret이 준비된다(0단계 확인). 키는 `secrets.token_bytes(32)`. 기존 테넌트용 일괄 생성 스크립트 `hack/backfill-exec-hmac.py`(dry-run 기본. `hack/`은 새로 만든다).
- `deploy/build/naming.py`: `kodeploy-` 접두어를 예약한다(기존 `app-` 접두어 거절과 같은 방식). 지금 그 접두어를 쓰는 앱은 없다(0단계 확인).
- `app/exec/` 클라이언트: 서명(`app/builder/sign.py` 그대로. 작업·기대값은 경로에 넣는다), aiohttp WebSocket, 프레임 인코딩, close 코드 → 예외 변환.
- 내부 세션마다 요청 id를 만들어 `X-Kodeploy-Request-Id`로 보낸다. core 로그에는 같은 `request_id`와 `user_id`·`app_id`·`op`를 남긴다(exec 쪽은 4-5).
- browse: `build_select` 직후 실제로 돌릴 SQL이 128000바이트를 넘으면 `QueryError`(400)로 돌려준다(4-3). 원본에서 `E2BIG`로 실패하던 경로다.
- `POST /apps/{id}/exec-tickets` (3-6). 발급 기록은 3-6대로 core 로그 + 관리자 접근만 `audit.record`.
- 기능별 플래그(기본 off): `EXEC_AGENT_TERMINAL`, `EXEC_AGENT_QUERY`, `EXEC_AGENT_BROWSE`, `EXEC_AGENT_DUMP`, `EXEC_AGENT_RESTORE`. off면 기존 경로, on이면 새 경로.
- **카나리 ns 목록** `EXEC_AGENT_NAMESPACES`(쉼표 구분, 전부면 `*`, 기본 비어 있음). 플래그는 전역이라, 켜진 기능도 이 목록에 든 ns만 새 경로로 간다(`config.exec_agent_on(flag, ns)`). 목록이 없으면 플래그를 켜는 순간 exec Pod가 없는 테넌트가 전부 새 경로로 가서 실패한다. 차트 `rollout`과 같은 목록으로 맞춘다(6-2의 순서).
- 웹용 `GET /apps/{id}/exec-features`(3-6). 처음 만든 전역 `GET /exec/features`는 카나리 때문에 앱 단위로 바꿨다.
- 첫 배포의 초기 복원(`build/v2.py` → `restore_staged(wait_exec=120)`): 새 경로면 보내기 전에 pods list(`kodeploy.com/component=exec`, 2초 간격)로 exec Pod Ready(phase Running, Ready=True, 지워지는 중 아님)를 최대 120초 기다린다. 그래도 Ready가 아니면 `exec_not_ready`를 로그에 남기고 한 번 보낸다. 실패하면 그 실패로 끝난다(빌드 행 error). 새 앱 ns는 배포 때 생겨 카나리 목록에 미리 넣을 수 없으므로, 이 경로는 `EXEC_AGENT_NAMESPACES="*"` 뒤에만 확인할 수 있다.
- 복원 실패 메시지: 결과를 몰라 백업을 남겼으면 "복원 결과를 알 수 없어 복원 전 백업을 남겼습니다: <pod>:<path>"를, 이전 복원이 미해결이면 "이전 복원이 미해결 상태라 새 복원을 하지 않았습니다 (DB는 바뀌지 않았습니다)"와 남은 백업 위치를 보여준다. core 로그(`exec_session`)에도 `backup_pod`·`backup_path`, `unresolved_backups`가 남는다.
- 기록 형식: JSON 한 줄. core에는 로깅 설정이 없어 `logger.info`가 운영에서 버려지므로 이 로거에만 stdout 핸들러를 붙였다.
- 새 경로는 DB 종류를 Pod 목록으로만 판단하고 DB Secret을 읽지 않는다.
- 업로드 복원도 `stage_dump`를 먼저 거치게 바꾼다. stage할 때 크기와 sha256을 계산해 `<token>.json`에 저장한다.
- 덤프: 채널 3의 exit code가 0이 아니면 `StreamingResponse`를 정상 종료하지 않고 예외로 끊는다.
- 덤프 실패 처리와 stage 통일은 새 경로에만 넣었다. 옛 경로는 9절 5번의 비교 기준이라 그대로 둔다.
- 옛 exec 코드는 이번에 지우지 않는다(10절 5단계).

### 6-4. web

- `AppWorkspace.jsx`, `DbTerminalPanel.jsx`: `GET /apps/{id}/exec-features`의 `terminal`이 true면(앱마다 캐시) `exec-tickets` → `new WebSocket(url, ["kodeploy.exec.v1", "ticket." + ticket])`.
- `binaryType = "arraybuffer"`. 채널 1·2는 `Uint8Array` 그대로 xterm에 `write`. 입력은 `[0, ...utf8 바이트]`, 창 크기는 `[4, ...JSON]`. 채널 3과 close 코드는 사용자 메시지로 보여준다.

---

## 7. 테스트

- `ticket`: 발급·소비·재사용 거부·만료·상한. 원문이 저장소와 로그에 없는지.
- `sign`: 정상, 시각 초과, 서명 불일치, 경로 변조(작업 이름, restore의 size·sha256).
- `proto`: 프레임 인코딩·디코딩, CLOSE, 창 크기, Status JSON, 텍스트 프레임 거부.
- `ops`: mysql·postgres 명령 조립 표 테스트. SQL과 앱 이름이 셸 문자열에 들어가지 않는지. query·browse 옵션이 원본 `_query_cmd`·`_browse_cmd`와 같은지. dump에 `pipefail`이 있는지. 비밀번호가 명령 인자에 없는지(env로만, Redis는 `REDISCLI_AUTH`).
- `restore`: 가짜 실행기로 단계마다 실패 경로, MySQL rollback 경로, `db_changed` 값, cleanup이 항상 도는지. 결과를 모를 때 백업을 남기고(`backup`) 아는 경우엔 지우는지, 이전 복원이 남긴 백업이 있으면 precheck에서 DB를 건드리지 않고 거부하는지(`unresolved_backups`), 백업 이름이 precheck가 찾는 모양(`backup-*.sql.gz`)과 맞는지. 남은 백업 찾기·cleanup은 실제 셸로도 본다.
- `session`: 가짜 executor로 exit code → Status, 유휴 타임아웃은 tty에만, 최대 시간, 동시 세션 4429, SIGTERM 4503, stdin 상한 초과 거부(query 80000, browse 128000바이트).
- 지표: `sessions_active`가 `door`별로 나오고 라벨 없는 합계 줄이 없는지.
- `audit`: 감사 기록 필드 이름이 `ticket_sha256`·`user_id`·`app_id`·`target`·`op`·`issued_at`인지. `ns`가 항상, `request_id`가 내부 세션에서 남는지. 내부 세션은 `user_id`·`app_id`가 비어 있는지.
- `api` (`httptest`)
  - 공개: 티켓 없음 4401, 다른 ns 경로 4403, 서브프로토콜 협상, 쿼리로 온 티켓 무시, 목록 밖 Origin 403(감사 기록, 티켓 그대로)·Origin 없음 통과·대소문자 무시
  - 문마다 동시 세션 상한(공개를 다 채워도 내부가 열린다), 복원 동시 실행 4429, 내부 문 복원이 남긴 백업·미해결 거부에 Pod·컨테이너가 채워지는지
  - 내부: 서명 실패 401, 허용되지 않은 조합 400, restore 경로의 size·sha256 형식 오류 400, 쿼리 문자열은 읽지 않음, `X-Kodeploy-Request-Id`와 티켓 요청의 `app_id`는 값을 바꿔도 실행 결과(대상 Pod, 허용 여부)가 같음
- 고루틴 누수: `go.uber.org/goleak`.
- `go vet`, `go test ./...` 통과.
- 차트: `helm template`으로 exec 리소스 렌더 확인, `values.schema.json` 검증(앱 values에 exec 관련 키를 넣으면 거부되는지 포함). 렌더된 exec HTTPRoute에 Cookie 제거 필터(`RequestHeaderModifier` `remove: [Cookie]`)와 `X-Origin-Verify` 매칭이 둘 다 있는지.
- core: exec 클라이언트 단위 테스트, query·browse 결과가 기존 경로와 같은지 비교하는 테스트(같은 SQL, 같은 파서), `naming.py`의 `kodeploy-` 접두어 거절, browse SQL 128000바이트 초과 시 400, 내부 세션 요청에 `X-Kodeploy-Request-Id`가 실리고 같은 id가 core 로그에 남는지. 카나리(`EXEC_AGENT_NAMESPACES` 밖이면 옛 경로, `exec-features`가 앱마다), 초기 복원이 Ready를 기다린 뒤 한 번 보내는지, 남긴 백업·미해결 거부 메시지와 재시도하지 않는지.
- 차트: `tests/exec_check.py` — 카나리(목록 안·밖, `all`), `nodeNets` 규칙과 빈 값 실패, Origin 목록(저장소 값은 https만, `devOrigins` 꺼짐, 켜면 뒤에 붙음), DB·Redis와 같은 조건.

---

## 8. 완료 조건

- [x] 2의 구조대로 `exec/` 생성, `go build ./...` 통과
- [x] 7의 테스트 전부 통과
- [x] Dockerfile, CI, VAP, vmagent 수집 설정 작성
- [x] kodeploy-charts 차트 변경 PR (6-2)
- [x] core 변경(6-3), web 변경(6-4). 플래그 기본 off
- [x] `internal/testclient`와 `hack/`의 E2E 절차 문서 (9절 표를 체크리스트로)
- [x] 보고: 원본과 다르게 처리한 부분, 사람이 만들어야 하는 Secret·값, 배포·확인 명령, 12절과 다르게 된 부분

---

## 9. 검증 (사람이 클러스터에서 한다. 결과는 블로그·포트폴리오 재료라 수치를 남긴다)

명령·통과 기준·적용 순서(카나리 포함 12단계)는 `hack/exec-e2e.md`에 있다. 1~7은 카나리 테스트 ns 하나에서 하고, 초기 복원은 `EXEC_AGENT_NAMESPACES="*"` 뒤에 새 앱으로 본다.

| 순서 | 확인 | 남길 것 |
|---|---|---|
| 1 | 차트 적용 후 테넌트마다 exec Pod Running | 유휴 메모리(RSS) |
| 2 | VAP: 앱 Deployment에 `serviceAccountName: kodeploy-exec`를 넣은 테스트 Pod 생성이 거부되는지 | 거부 메시지 |
| 3 | NetworkPolicy: 같은 ns 앱 Pod에서 exec Pod 8080·8081 접속이 실패하는지 | 결과 |
| 4 | testclient로 공개 문: 티켓 재사용·만료 4401, 다른 ns 4403, 목록 밖 Origin 403(같은 티켓 + 허용 Origin은 열림), 한글·바이너리 출력, 창 크기 | 결과 |
| 5 | 플래그를 하나씩 켜고 기존 경로와 비교. query·browse 결과 동일, 덤프는 `--skip-dump-date`·`gzip -n` 또는 복원 후 행 수로 비교, 복원 후 행 수 | 비교 결과 |
| 6 | 장애 재현 (테스트 앱 DB로만): 업로드 중 연결 끊기, 작업 디렉토리 채우기(운영 노드에서는 하지 않는다), 기대 해시 틀리게 보내기, 동시 세션 초과(문마다 따로, `sessions_active{door}`), 미해결 백업이 남은 채 복원 | before/after |
| 7 | 웹 터미널 3종 동작. Redis 콘솔이 차트 이미지(`redis:7-alpine`)에서 `REDISCLI_AUTH`로 인증되는지(`PING` → `PONG`, `NOAUTH`가 아님) | 확인 |
| 8 | core-tenant에서 `pods/exec` 제거 후, 기존 경로로 실제 exec 요청 시 403 (`can-i` 말고) | 응답 |

---

## 10. 진행 단계 (각 단계 끝에 멈추고 보고)

0. **조사** (완료, 2026-11-12): kodeploy-charts의 차트·AppProject를 읽고 12절에 답했다. 결정은 12절과 각 절에 반영했다.
1. **exec 모듈**: `exec/` 전체와 7절 단위 테스트. 클러스터 없이 끝나는 범위다.
2. **배포물**: Dockerfile, CI, VAP, vmagent 수집 설정, 차트 PR(kodeploy-charts), core의 Secret 생성과 일괄 생성 스크립트, `naming.py`의 `kodeploy-` 예약. 클러스터 적용은 사람이 한다.
3. **연결**: core 클라이언트, `exec-tickets`, 플래그, stage 통일, 덤프 실패 처리, web. 플래그는 기본 off.
4. **검증 준비**: testclient와 `hack/` E2E 문서. 사람이 9절 1~7을 진행한다.
5. **권한 회수와 정리** (사람이 9절 1~7을 확인한 뒤에만): core-tenant `pods/exec` 제거 PR, 옛 exec 코드·플래그 삭제 PR. 적용은 사람이 한다.

진행 상태 (2026-11-13): **0~5단계 전부 끝.** 운영 적용·9절 1~8 확인·권한 회수까지 마쳤다. 결과 수치는 16절.
- kodeploy: 3ad8939(#3 본체) → 07ada96(#4 mysql 콘솔 로케일) → 8fdac6b(플래그 저장소 반영) → 983a593(#5 옛 경로·플래그 삭제) → 7fcdd2d(#6 core-tenant `pods/exec` 회수). 운영 core `sha-983a593`.
- kodeploy-charts: 31199e9(#1) → 7bf655a(#2 digest) → 1404ee9(#3 `rollout.all: true`) → 541bae2(#4 주석). exec 이미지 `sha256:3bb45a84…056a3`.

---

## 11. scale-to-zero 판단 (이번엔 하지 않는다. 측정 기준만 미리 고정)

- **측정**: Pod가 떠 있을 때와 0개일 때 각각 "터미널 클릭 → 프롬프트 표시"를 n=10씩 재고, 차이(늘어난 지연)를 본다. 구간도 나눠서 기록한다: scale 요청 → 스케줄 → 컨테이너 실행 → Ready → Envoy endpoint 반영 → WS 연결 → 프롬프트.
- **결정 기준** (측정 전에 고정한다)

  | 늘어난 지연 | 상시 실행 메모리 합계 | 결정 |
  |---|---|---|
  | 1초 이하 | 워커 할당 가능 메모리의 5% 이상 | 도입 |
  | 1초 이하 | 5% 미만 | 보류 |
  | 1초 초과 | 상관없음 | 미도입 |

  1초 기준의 근거는 Nielsen의 응답 시간 기준이다(1초: 생각의 흐름이 끊기지 않는 한계).
- 도입할 때 할 일: core에 그 Deployment 이름 하나에 대한 `deployments/scale` 권한, Argo `ignoreDifferences: /spec/replicas`, core의 `pods` watch로 Ready 확인 후 티켓 발급.

---

## 12. 확인 결과와 결정 (0단계, 2026-11-12)

| # | 항목 | 확인 결과 | 결정·반영 |
|---|---|---|---|
| 1 | 비밀번호 env 이름 | MySQL `MYSQL_ROOT_PASSWORD`·`MYSQL_USER`·`MYSQL_PASSWORD`·`MYSQL_DATABASE`, PostgreSQL `POSTGRES_USER`·`POSTGRES_PASSWORD`·`POSTGRES_DB`, Redis `REDIS_PASSWORD`. 원본이 읽던 Secret 키 `DB_*`는 컨테이너 env에 없다(값은 같다) | 4-3 표 |
| 2 | PVC와 데이터 디렉토리 | MySQL은 같다(`/var/lib/mysql`). PostgreSQL은 다르다(`PGDATA`가 하위 `pgdata`). local-path라 PVC도 노드 디스크 | 두 DB 모두 `/tmp/kodeploy-restore`, precheck는 `df /tmp`로 size × 2 (4-6) |
| 3 | `sha256sum`·`gzip` | `mysql:8.4`, `postgres:16-alpine` arm64 레이어에 둘 다 있다(bash도). 태그는 고정돼 있지 않다 | 그대로 |
| 4 | 앱 Pod SA | 서버·정적은 default SA + `automountServiceAccountToken: false`, 차트 고정. 스키마가 `additionalProperties: false`라 values로 못 넣는다. DB·Redis Pod는 default SA 토큰이 마운트된다(권한 바인딩 없음) | 그대로 |
| 5 | AppProject | 허용 목록 방식. 지금 Deployment·StatefulSet·Service·HTTPRoute·Calico NetworkPolicy·RoleBinding | `ServiceAccount`·`Role` 추가 (6-2) |
| 6 | HTTPRoute·`X-Origin-Verify` | 차트 `app.route`가 `platform/gateway.yaml`의 `originVerify`(private 저장소 평문)로 Exact 매칭. core 라우트는 공개 저장소라 `${ORIGIN_VERIFY_SECRET}`을 적용할 때 치환 | 차트 방식 재사용 (6-2) |
| 7 | ns 하나에 앱 여러 개 | 없다. `apps.namespace` unique, ns 하나 = values 하나 = 앱 하나. `kodeploy-exec`는 예약 이름이 아니었다 | `kodeploy-` 접두어 예약, exec 선택 라벨은 `kodeploy.com/component: exec` (6-2, 6-3) |

0단계에서 함께 바뀐 것: 차트 저장소 이름(kodeploy-charts), Go 1.25·client-go v0.35(2절), 서명은 builder 규칙 그대로·경로에 작업과 기대값(3-1), Calico NetworkPolicy order 50(6-2), `browse` 작업 추가(4-3), dump `pipefail`(4-3), query는 원본 명령 + SQL만 stdin(4-3), 발급 기록과 필드 이름(3-6, 4-5), 내부 세션의 `ns`·`request_id` 기록과 "서명 밖 값은 기록용만" 원칙(3-1, 4-5), SQL 상한(query: core 20000자·exec 80000바이트, browse: core·exec 128000바이트 고정, 4-3), 차트 검증은 `helm template` + 스키마, CPU 요청 10m 유지·메모리 요청 64Mi·상한 128Mi, `imagePullSecrets: ghcr-auth`, vmagent 수집 설정(6-1).

---

## 13. 하지 말 것

- core에 새 exec 코드 추가. exec는 `kodeploy-exec`만 한다.
- exec SA에 Secret 권한이나 Pod 생성 권한 주기. core나 exec에 `pods create` 주기.
- exec 리소스의 어떤 필드든 앱 values에서 받기.
- 공개 문에서 query·browse·dump·restore 허용. 티켓을 URL로 받기.
- 내부 문에서 쿼리 문자열 읽기 (서명 범위 밖이다).
- 서명 밖 값(`X-Kodeploy-Request-Id` 등)이나 기록용 값(`app_id`)으로 실행 대상·권한 정하기.
- exec Pod를 `app=` 라벨로 고르기. K8s `networking.k8s.io` NetworkPolicy로 exec를 막으려 하기 (Calico order 100·200 정책이 먼저 결정한다).
- 일반 티켓 발급을 `admin_actions`(`audit.record`)에 남기기.
- 텍스트 디코딩, `{"__resize":` 같은 대역 내 신호.
- 티켓 원문, HMAC 키, SQL 본문, 터미널 입출력을 로그·에러 메시지·커밋에 남기기.
- 복원의 옛 우회 구조(임시 파일 + close + 마커)를 옮기기.
- 운영 앱 DB로 복원·장애 재현 시험하기. 테스트 앱으로만 한다.
- 9절 확인 전에 core-tenant `pods/exec` 제거하기.
- `time.Sleep` 기반 대기에 context 없이 들어가기, 폴링으로 Pod 상태 읽기. 예외는 core 초기 복원의 exec Ready 대기 하나다(pods list 2초 간격, 최대 120초. 2026-11-13 결정, 6-3).
- 카나리 두 목록을 어긋나게 바꾸기. core `EXEC_AGENT_NAMESPACES`를 차트 `rollout`보다 먼저 넓히거나, 차트를 core보다 먼저 좁히기.
- `devOrigins.enabled: true`를 켠 채로 두기.
- 결과를 모르는 복원이 남긴 백업을 꺼내고 판단하기 전에 지우기(지우면 다음 복원이 열린다).

---

## 14. 다음 작업 미리보기 (이번엔 하지 않는다)

- **CLI**: 기존 세션 저장소 재사용(`kind`·`name`·`last_used_at`, 세션 ID 해시 저장 마이그레이션, 헤더는 cli 세션만·쿠키는 web 세션만), 공개 문 `app exec` 작업, 비대화형 exit code와 125 규칙. CI 사용은 범위 제한 세션이나 GitHub Actions OIDC 이후.
- 감사 로그 영구 보관과 core 발급 기록과의 자동 대조(core 밖에서).
- 티켓 발급 기록 전용 테이블 (지금은 core 로그, 관리자 접근만 `admin_actions`).
- 라벨·어노테이션 도메인 통일 (범위 밖). 지금 core·차트는 `kodeploy.com/`(`env-revision`, `r2-token-id`, 이번의 `component`)이고, builder는 `kodeploy.io/`(Job 라벨 `kodeploy.io/managed`, 어노테이션 `kodeploy.io/request` 등)다. builder 것은 이번에 그대로 둔다.
- scale-to-zero (11절 결과에 따라).
- 사고 대응 절차 문서: Argo auto-sync 일시정지 → `kodeploy-exec` Deployment 전부 0 → core 정리 → 테넌트별 HMAC 키 교체 → 재개.
---

## 15. 1~4단계 구현과 그 뒤 수정 (2026-11-13)

각 절 본문에 이미 반영했다. 여기는 원래 지시서와 달라진 것과 이유만 모은다.

**1~4단계 구현 중 정한 것**

| 항목 | 정한 것 | 이유 | 절 |
|---|---|---|---|
| close 코드 | 1003·1011·4400 추가 | 지시서가 "1단계에서 정해 보고"로 남겨 둔 자리 | 3-4 |
| 내부 문 서브프로토콜 | 내부 문도 `kodeploy.exec.v1` 요구 | 두 문 프레임 규칙이 같다 | 3-1 |
| 티켓 상한 | 대기 티켓이 1000개면 발급 429, 발급도 감사 기록 | 상한에 닿았을 때 동작이 정해져 있지 않았다 | 3-1 |
| 대상 Pod | 지워지는 중인 Pod 제외, DB도 "Running 중 가장 최근" | 롤링 중 끝나는 Pod에 붙지 않게 | 4-2 |
| `db_changed` | MySQL은 load 시작부터 true(rollback 성공해도), load 중 끊기면 rollback 안 함 | backup 뒤 앱이 쓴 내용이 rollback으로 사라졌을 수 있다. 적재가 컨테이너 안에서 계속 돌 수 있다 | 4-6 |
| 백업 몫 | backup 뒤 남은 공간을 다시 확인 | 백업 크기를 미리 알 수 없다 | 4-6 |
| 시간 초과 복원 | 결과 JSON을 먼저 보내고 4413 | core가 `db_changed`를 알아야 한다 | 3-4 |
| 차트 | 경로 `/exec/<ns>`(끝 슬래시 없이), liveness 없음, grace 1830초 | PathPrefix는 경로 요소 단위라 같은 뜻이다. 종료 중 503으로 재시작되지 않게. 비대화형 최대 30분 + 30초 | 4-7, 6-2 |
| core 기록 | 이 로거에만 stdout 핸들러 | core에 로깅 설정이 없어 운영에서 버려진다 | 6-3 |
| browse 상한 | 128000바이트 검사는 옛 경로에도 | 원본의 E2BIG 실패를 사용자 오류로 | 4-3 |
| 옛 경로 | 덤프 실패 처리·stage 통일은 새 경로에만 | 9절 5번 비교 기준 | 6-3 |

**1차 수정 (클러스터 적용 전 7건)**

| 항목 | 바뀐 것 | 이유 | 절 |
|---|---|---|---|
| 복원 백업 보존 | 결과를 모르면(load 중 끊김, rollback 실패) 백업을 남기고 결과 `backup`에 위치. 백업 이름은 복원마다 다르게. 수동 복구는 `hack/exec-e2e.md` | 백업이 유일한 사본일 수 있다. 다음 복원이 덮어쓰지 않게 | 3-5, 4-6 |
| PostgreSQL load 중 끊김 | `db_changed=true` | 끊긴 뒤에도 적재가 끝나면 커밋된다 | 4-6 |
| 복원 동시 실행 | ns당 하나, 두 번째는 4429 | 두 적재가 섞이면 백업으로도 판단할 수 없다 | 4-4 |
| Calico | 노드 대역(`nodeNets` 10.0.2.0/24) → 9090 Allow를 Deny 앞에. 비면 렌더 실패 | kubelet readiness probe. 빈 nets는 "모든 출발지" | 6-2 |
| 카나리 | 차트 `rollout{all,namespaces}`, core `EXEC_AGENT_NAMESPACES`, 앱 단위 `exec-features` | 플래그가 전역이라 exec Pod가 없는 ns가 새 경로로 가면 실패한다 | 3-6, 6-2, 6-3 |
| 초기 복원 | 연결 실패 고정 재시도를 빼고 exec Pod Ready를 최대 120초 기다린 뒤 한 번 | exec와 DB가 같이 뜨는 첫 배포. `db_changed=false` 재시도(최대 2회)는 유지 | 4-6, 6-3 |
| 동시 세션 상한 | 공개 3·내부 4로 분리 (`MAX_SESSIONS` → `MAX_SESSIONS_PUBLIC`·`MAX_SESSIONS_INTERNAL`) | 합계 3이면 터미널 둘 + DB 콘솔에서 4429 | 4-4, 5 |
| 공개 문 Origin | 헤더가 있으면 허용 목록만, 없으면 통과. 403은 업그레이드 전, 티켓은 쓰지 않음 | 처음 구현은 Origin을 보지 않았다(티켓·Cookie 제거로 충분하다고 봤다) | 3-2 |
| DB·Redis만 있는 앱 | 만들 수 없음을 확인. exec는 원래 DB·Redis와 같은 조건이라 범위는 그대로, 주석·테스트만 | | 6-2 |

**2차 수정**

| 항목 | 바뀐 것 | 이유 | 절 |
|---|---|---|---|
| 미해결 복원 | precheck가 남은 백업을 찾으면 새 복원 거부(`db_changed=false`, `unresolved_backups`). core는 다시 보내지 않고 "이전 복원이 미해결 상태" 메시지 | 새 복원이 DB를 또 바꾸면 남은 백업으로 판단할 근거가 사라진다 | 3-5, 4-6, 6-3 |
| 지표 | `sessions_active`에 `door` 라벨, 합계 줄 없음 | 상한이 문마다 따로다 | 4-5 |
| Origin 목록 | 차트 `allowedOrigins`는 운영 웹(https)만, 로컬 dev 서버는 `devOrigins`(기본 꺼짐). core `ALLOWED_ORIGINS`는 그대로(5173 포함) | 운영 exec가 개발 서버 Origin을 받지 않게 | 3-2, 5, 6-2 |

**3차 수정과 5단계 (2026-11-13, 운영 적용 중)**

| 항목 | 바뀐 것 | 이유 | 절 |
|---|---|---|---|
| mysql DB 콘솔 | `LANG=C.UTF-8`로 띄운다(#4) | mysql 이미지는 LANG이 비어(C 로케일) 줄 편집기가 한글 입력을 버렸다(`kd-42-`). `--default-character-set`은 서버 쪽 문자셋이라 소용없다. 옛 경로도 같은 증상이었다. postgres 이미지는 `LANG=en_US.utf8`이 있다 | 4-3 |
| 직접 쓴 복원 SQL | 첫 줄에 `SET NAMES utf8mb4` (문서) | 없으면 클라이언트 기본 문자셋으로 적재돼 한글이 이중 인코딩된다. 옛·새 경로 같음 | 9 |
| 5단계 범위 | 옛 터미널 WS 3개, query·browse·dump·업로드 복원의 직접 exec, `EXEC_AGENT_*`·`EXEC_AGENT_NAMESPACES`, `GET /apps/{id}/exec-features`, web의 옛 경로 분기를 지웠다(#5). `pods/exec` 회수는 따로(#6) | 지시서대로 두 PR. web은 머지 즉시 나가므로 지금 core(플래그 on, `*`)와도 동작하는 형태로 먼저 | 6-3, 6-4, 10 |
| web 앱 화면 밖 | 터미널을 열지 않고 "앱 화면에서 열어 주세요" | 티켓은 앱마다 받는다. 옛 경로의 "내 첫 앱" 대체가 없다 | 6-4 |
| 관리자 기록 | `POST /apps/{app_id}/exec-tickets` 이름("터미널 접속") 추가, 옛 `WS /deploy/app/*-terminal` 이름은 옛 기록용으로 둔다 | 관리자 화면에 옛 행이 남아 있다 | 3-6 |
| 되돌리기 | 플래그로 즉시 되돌리는 길이 없어졌다. 비상 시 RBAC `pods/exec`(get·create) 되살림 + core `sha-3ad8939` (hack/exec-e2e.md) | 옛 경로가 있는 마지막 core 이미지 | 10 |
| 차트 `rollout` | `all: true` 유지, 주석만 지금 상태로(#4) | core에 카나리 목록이 없어 exec가 없는 ns는 exec 기능이 아예 없다 | 6-2 |
| exec 자원 값 | 요청 64Mi·상한 128Mi 그대로 | 실측 최고 RSS 9.6 MiB·working set 12.5 MiB지만, 수집 간격 사이의 짧은 최고값과 200 MiB 복원 부하는 재지 않았다. 64는 runtimes.yaml 관례의 최소 단계 | 6-2 |

---

## 16. 운영 적용 결과 (2026-11-13)

카나리는 dailo(tenant-b1545e9c, 유저가 시험용으로 허락), 넓힌 뒤 확인은 runboo(Redis)·covebook(PostgreSQL), 초기 복원은 새 테스트 앱(exec-test, kodeploy-test-spring java+mysql+redis — 확인 뒤 삭제).

| 9절 | 결과 |
|---|---|
| 1 | exec Pod 7개 Ready. 유휴 RSS(세션 끝 10분 뒤) Pod당 8.0~9.5 MiB, 합 61 MiB = 워커 할당 17576 MiB의 0.35%. 3시간 최고 RSS 9.6 MiB·working set 12.5 MiB. 7개 모두 worker1에 스케줄됨(affinity 없음) |
| 2 | VAP 세 거부: "kodeploy-exec SA는 kodeploy.com/component=exec 라벨이 있는 Pod만 쓸 수 있습니다", "kodeploy.com/component=exec 라벨은 kodeploy-exec SA로만 쓸 수 있습니다", "kodeploy-exec Pod에는 ephemeral container를 붙일 수 없습니다" |
| 3 | 같은 ns 앱 Pod에서 8080·8081·9090 모두 `download timed out` (대조: mysql 3306 연결됨) |
| 4 | 실제 경로(Cloudflare → Envoy)로 PASS 12줄, `all public-door checks passed` |
| 5 | QUERY 6건·BROWSE 4건 diff 없음, DUMP diff 0줄(둘 다 310,990바이트), RESTORE 표별 행 수 일치(옛 0.88초 / 새 4.04초) |
| 6 | 업로드 중 끊기 close 1006·DB 그대로·작업 디렉토리 비움 / 해시 틀림 `failed_step: upload`·`db_changed: false` / 공개 문 4번째 4429 / 공개 3개 붙잡은 채 내부 문 콘솔 정상 / 지표 `{door="public"} 3`·`{door="internal"} 0` / 미해결 백업이면 precheck 거부·재시도 없음, 지운 뒤 복원 성공. 작업 디렉토리 채우기는 하지 않음(운영 노드) |
| 7 | 앱 `kd-42-한글`·`stty size` 45 123, DB(mysql) `kd-42-한글`(#4 뒤. 처음엔 `kd-42-`로 FAIL), PostgreSQL `kd-42-한글`, Redis `PONG`. 서브프로토콜 `kodeploy.exec.v1` |
| 초기 복원 | `"*"` 뒤 새 앱: `exec_not_ready` 없음, 1.4초, 표 317/29행 = 덤프, 한글 바이트 그대로, 작업 디렉토리 비움 |
| 8 | 회수 전 core SA 실제 exec `ALLOWED`(도구 확인) → 회수 뒤 `DENIED 403`. 회수 뒤 query·browse·dump(93표)·restore(7단계)·터미널 3종·앱 로그 정상 |

테넌트 응답은 단계마다 기준값과 같았고 core 5xx는 0.

11절 판단: 상시 실행 합계가 0.35%로 5% 기준 아래라 지연과 상관없이 "보류" 또는 "미도입"이다. 지금은 scale-to-zero를 하지 않는다. 지연(n=10)은 재지 않았다.

### 16-1. 남은 확인 (2026-11-14)

| 항목 | 결과 |
|---|---|
| 9절 7 브라우저 화면 | 로그인 세션 생성이 막혀서(자동 승인 거부) 실제 로그인 화면 대신 하네스로 대신했다. headless Chromium에 운영 오리진(`https://app.kodeploy.com`) 하네스를 띄웠다. 그 안에서 운영 web의 `terminalSocket.js`·`xtermTheme.js`와 xterm 6을 돌리고, `api/deploy.js`만 core 발급 티켓을 돌려주는 대역으로 바꿨다. 앱 `kd-42-한글`, MySQL 콘솔 `kd-42-한글`, Redis `PONG` 화면을 찍었다 |
| 6-2 최대 크기 복원(198.5 MiB SQL, dailo) | **안전하지 않음.** exec는 최고 25 MiB(memory.peak 30 MiB)로 멀쩡했다. 문제는 컨트롤 플레인이다. 업로드가 apiserver를 지나면서 master(3.6 GB)가 메모리 스래싱에 빠졌다. 1차는 적재 61초에 apiserver 퐁이 끊겨 결과를 모르게 됐다(적재는 끝까지 됨). 2차는 kube-apiserver가 liveness로 재시작됐고 dailo MySQL(512Mi)이 OOMKilled됐다. 자세한 건 `hack/exec-e2e.md` "큰 복원과 컨트롤 플레인". 테넌트 응답·core 5xx는 기준값 그대로 |
| 조용한 긴 적재 | `SELECT SLEEP(75)` 덤프 복원 성공(7단계). 출력이 없는 시간만으로는 끊기지 않는다 |
| 큰 덤프 다운로드 | gz 119 MB 48초, gzip 정상 |
| TTY 유휴 | 앱·DB 터미널 90초 무입력 뒤에도 입력·출력 정상 |
| 콜드 스타트(11절 지연) | exec Pod 생성→Ready: 이미지가 있는 노드 1~2초, 처음 받는 노드 6초(pull 4.7초). scale-to-zero로 깨우는 장치의 지연은 별도다. 판단(미도입)은 그대로 |
| 배치 | 차트 #5(4f94e32): exec가 같은 ns의 DB·Redis(가중치 100)·앱(50) 노드를 고른다. worker2 4개·worker1 3개로 나뉘었다. 7개 ns 내부 문·상수 쿼리, 공개 문 터미널 3종 정상 |
| 앱↔DB 배치 | 차트 PR #6(머지 안 함). 앱이 DB 노드를 고르게 한다. 머지하면 Recreate라 7개 앱이 모두 재시작되고, worker2(1코어)는 CPU 요청 약 90%가 된다. modo·ami-lab은 앱 PVC가 다른 노드라 효과가 없다 |
| 14절 사고 대응 절차 | `hack/exec-e2e.md` "사고 대응"으로 썼다. dailo 하나로 1~5를 해 봤다. ApplicationSet 컨트롤러를 멈추고 auto-sync를 빼자 replicas 0이 유지됐다. 키를 교체하고 재개하니 39초 뒤 새 키로 Ready가 됐고 쿼리가 통과했다. core는 401이면 키 캐시를 버리므로 재시작하지 않는다 |

14절의 나머지(CLI, 감사 로그 영구 보관·자동 대조, 발급 기록 테이블, 라벨 도메인 통일)는 하지 않았다. 각각 따로 설계가 필요하다.
