# 배포 API 계약 (CLI·CI용)

CLI와 GitHub Actions가 "이 커밋을, 지금 설정 그대로" 배포할 때 쓰는 core API다. 웹 앱 화면의 "배포하기" 버튼(설정은 그대로, 코드는 브랜치 최신이나 고른 최근 커밋)도 같은 API를 쓴다. 설정을 바꾸는 배포는 "설정 변경"(배포 폼, `POST /deploy`)이다.
CLI는 이 문서만 보고 만들 수 있어야 한다. 서버 구현은 `core/app/deploy/build/redeploy.py`, 라우트는 `core/app/deploy/router.py`의 `redeploy_app`이다. CI 인증(GitHub Actions OIDC → CI 토큰)은 9절, 구현은 `core/app/ci/`다.

## 1. 범위와 제약

- 배포 설정(저장소, 브랜치, 런타임, 포트, DB, Redis, 저장소, 빌드 방식, 경로, 정적 사이트 설정)은 요청으로 받지 않는다. 서버가 그 앱의 라이브 배포에서 다시 만든다. 설정을 바꾸는 배포는 웹의 "설정 변경"(배포 폼, `POST /deploy`)이 맡는다.
- 환경변수와 초기 DB 덤프는 보내지 않고, 서버도 건드리지 않는다.
- 첫 배포는 이 API로 할 수 없다. 라이브 배포가 없으면 409다.
- 인증은 웹 세션 쿠키 또는 CI 토큰(`Authorization: Bearer kdci_...`, 9절)이다. CI 토큰은 재배포와 상태 조회 두 곳에서만 받는다.

## 2. 용어

| 이름 | 뜻 |
|---|---|
| `commit_sha` (요청) | 이 API에 지정하는 앱 소스 저장소의 커밋. 40자 hex |
| `requested_sha` | 빌드 행에 저장된, 요청이 지정한 커밋. 없으면 브랜치 최신을 빌드했다 |
| `source_sha` | 빌더가 실제로 clone해 빌드한 커밋. 40자 소문자 hex. 빌더가 init 로그에서 읽어 알려 준다 |
| GitOps 커밋 | 빌더가 kodeploy-apps 저장소에 values를 커밋한 SHA. 빌더 이벤트의 `commit_sha`이고 이 API에는 나오지 않는다 |

## 3. 재배포

```
POST /apps/{app_id}/deploy/redeploy
POST /deploy/redeploy?app_id={app_id}        (같은 동작)
Content-Type: application/json
Idempotency-Key: <선택, 1~100자, [A-Za-z0-9._-]>

{"commit_sha": "<선택, 40자 hex>", "repo": "<선택, 저장소 URL>"}
```

- `app_id`는 꼭 있어야 한다. 없으면 400이다. 옛 경로 `/deploy/...`가 app_id 없이 "내 첫 앱"으로 가는 규칙을 이 API는 따르지 않는다.
- 본문은 비워도 되고 `{}`도 된다. 둘 다 브랜치 최신을 그대로 다시 배포한다.
- 본문에 위 두 칸 말고 다른 칸(`env`, `init_dump_token` 등)이 있으면 422다.
- 권한은 편집(editor) 이상이다. 편집 권한 멤버는 그 앱이 쓰던 저장소로만 배포할 수 있다(주인은 제한 없음).
- CI 토큰으로 부르면 커밋과 저장소가 토큰의 값으로 정해진다(9-5).

### 3-1. 동작

1. 슬롯(서버, 정적)마다 라이브 배포를 찾는다. 기준은 `kind=build`이고 `status=running`인 가장 최근 행이다(자세한 규칙은 7절).
2. 그 행들의 설정으로 `POST /deploy`와 같은 배포를 시작한다. 선언된 슬롯은 모두 다시 빌드된다(슬롯 하나만 빌드할 수는 없다).
3. 커밋 지정은 기준 저장소를 쓰는 슬롯에만 적용한다.
   - 기준 저장소 = 요청의 `repo`, 없으면 서버 슬롯의 저장소, 서버 슬롯이 없으면 정적 슬롯의 저장소
   - 저장소 비교는 대소문자, `http`/`https`, 끝의 `.git`·`/` 차이를 무시한다
   - 다른 저장소를 쓰는 슬롯은 그 슬롯 브랜치의 최신으로 같이 다시 빌드된다
4. 같은 슬롯에서 진행 중이던 빌드는 취소된다(웹 배포 폼과 같은 규칙). 응답을 받기 전에 시작된 다른 배포에 밀리면 그 빌드는 `cancelled`가 된다.
5. `commit_sha`는 형식만 검사한다. 저장소에 없는 커밋이면 빌드가 clone 단계에서 `failed`가 된다. 대문자 hex는 소문자로 바꿔 받는다. 짧은 SHA는 400이다.

### 3-2. Idempotency-Key

- 키가 있으면 서버는 `(app_id, key)`를 먼저 선점한 뒤 빌드를 시작한다.
- 같은 키로 다시 오면 그때 만든 빌드들을 지금 상태 그대로 돌려준다(`200`, `replayed: true`). 그 빌드가 이미 `failed`나 `cancelled`여도 다시 빌드하지 않는다. 다시 빌드하려면 새 키를 쓴다.
- 같은 키의 첫 요청이 아직 처리 중이면 서버가 잠깐(약 5초) 기다렸다가 다시 읽는다. 그래도 끝나지 않았으면 409다. 같은 키로 다시 보내면 된다.
- 빌드 시작 자체가 실패하면(400 등) 선점을 풀어서, 같은 키로 다시 보낼 수 있다.
- 다시 보낸 요청의 본문이 처음과 달라도 처음 결과를 돌려준다.
- 키가 없으면 매번 새 요청이다. CI는 재시도에 대비해 실행마다 키 하나를 정해(예: `gha-<run_id>-<run_attempt>`) 같은 실행 안에서는 같은 키를 쓰기를 권한다.

### 3-3. 응답

새로 시작하면 `202`, 같은 키로 이미 처리한 요청이면 `200`.

```json
{
  "app_id": "11111111-2222-4333-8444-555555555555",
  "requested_sha": "0123456789abcdef0123456789abcdef01234567",
  "replayed": false,
  "builds": [
    {"build_id": "3f9a2c1d", "slot": "server", "status": "queued", "ref": "0123456789abcdef0123456789abcdef01234567"},
    {"build_id": "8e7d6c5b", "slot": "static", "status": "queued", "ref": "main"}
  ]
}
```

- `builds`는 이 요청이 만든 빌드다. 선언된 슬롯마다 하나씩, 서버가 먼저다.
- `ref`는 빌더에 보낸 값이다. 그 슬롯에 커밋을 지정했으면 그 커밋, 아니면 브랜치 이름이다.
- `requested_sha`는 요청의 `commit_sha`(소문자)다. 없으면 `null`.
- `status`는 응답 시점의 값이다. 새 요청이면 보통 `queued`다.

### 3-4. 오류

본문은 `{"detail": "<화면에 보여도 되는 이유>", "code": "<기계용 코드>"}`다. `detail`은 문구가 바뀔 수 있으니 프로그램은 `code`로 가른다. `code`가 없는 오류도 있다(로그인 안 됨, 권한 부족, 없는 앱, 422). 코드 전체 목록은 10절.

| 상태 | 언제 | `code` |
|---|---|---|
| 400 | `app_id` 없음 | (쿠키) 없음, (CI 토큰) `app_id_required` |
| 400 | `commit_sha` 형식 오류 | `invalid_commit_sha` |
| 400 | `Idempotency-Key` 형식 오류 | `invalid_idempotency_key` |
| 400 | `repo`를 쓰는 슬롯 없음 | `repo_not_used` |
| 400 | 배포 조건 검사 실패(예: R2 미설정) | `deploy_rejected` |
| 401 | 로그인 안 됨 | 없음 |
| 401 | CI 토큰 문제(9-4) | `ci_token_invalid`, `ci_token_expired`, `ci_deploy_disabled` |
| 403 | 편집 권한 없음(보기 권한) | 없음 |
| 403 | 허용된 저장소 밖(편집 권한 멤버) | `repo_not_allowed` |
| 403 | CI 토큰과 다른 커밋·저장소, 토큰의 브랜치를 쓰는 슬롯 없음(9-5) | `commit_mismatch`, `repo_mismatch`, `branch_mismatch` |
| 403 | 다른 앱용 CI 토큰 | `ci_token_app_mismatch` |
| 404 | 앱이 없거나 접근 권한이 없음 | 없음 |
| 409 | 라이브 배포가 하나도 없음(첫 배포는 웹 배포 폼으로) | `no_live_deploy` |
| 409 | 선언된 슬롯(서버 또는 정적)에 성공한 배포가 없음 | `no_successful_slot` |
| 409 | 같은 키의 첫 요청이 아직 처리 중 | `key_in_progress` |
| 409 | 같은 키의 첫 요청이 빌드 시작 전에 실패해 선점이 풀림 | `key_released` |
| 422 | 본문 형식 오류(모르는 칸, 문자열이 아닌 값) | 없음 |

`key_in_progress`와 `key_released`는 같은 키로 다시 보내면 된다. `no_live_deploy`, `no_successful_slot`은 다시 보내도 같다(웹 배포 폼으로 배포해야 풀린다).

### 3-5. 고를 커밋 목록

```
GET /apps/{app_id}/deploy/redeploy/commits
```

웹의 배포하기가 커밋을 고를 때 쓴다. CLI는 쓰지 않아도 된다.

```json
{
  "repo": "https://github.com/acme/api.git",
  "branch": "main",
  "current_sha": "<지금 떠 있는 커밋, 기록 전 배포면 null>",
  "commits": [{"sha": "<40자>", "message": "<첫 줄>", "date": "2026-11-18T06:20:00Z", "url": "<GitHub 커밋 주소>"}],
  "current": null
}
```

- `repo`·`branch`는 `repo` 없이 커밋을 지정할 때의 기준 저장소(3-1)와 그 슬롯의 브랜치다.
- `commits`는 그 브랜치의 최근 커밋, 최신순 최대 3개다. GitHub 조회에 실패하면 빈 목록이다. 비공개 저장소는 앱 주인의 GitHub 연결로 읽는다.
- `current`는 `current_sha`가 `commits`에 없을 때만 그 커밋을 준다(조회에 실패하면 `sha`만).
- 권한과 409 조건은 재배포와 같다.
- 웹은 최신을 고르면 `commit_sha` 없이 보낸다. 목록은 몇 초 늦을 수 있고, 정적 슬롯이 같은 저장소의 다른 브랜치를 쓸 때 그 슬롯까지 서버 브랜치의 커밋으로 고정되지 않게 하려는 것이다.

## 4. 상태 조회

```
GET /apps/{app_id}/deploy/{build_id}
```

`app_id`를 꼭 경로에 넣는다. `/deploy/{build_id}`만 쓰면 "내 첫 앱"의 빌드로 찾는다(CI 토큰이면 400 `app_id_required`).
쿠키 또는 CI 토큰으로 부른다. CI 토큰은 그 앱의 빌드만 보이고, 다른 앱의 빌드는 404다.

| `status` | 뜻 |
|---|---|
| `queued`, `building`, `built`, `deploying` | 진행 중 |
| `running` | 성공 (배포됨) |
| `failed` | 실패 |
| `cancelled` | 취소됨 (같은 슬롯의 다른 배포에 밀렸다) |

함께 볼 칸

- `error`: 실패 이유. 빌더 단계가 붙는다(`build: ...`, `health: ...` 등).
- `source_sha`: 실제로 빌드한 커밋. clone이 끝나야 생긴다. 롤백 배포는 원본 배포의 값을 가진다.
- `requested_sha`: 지정한 커밋. 지정했는데 빌더가 다른 커밋을 받으면 그 빌드는 `failed`이고 `error`가 `build: source mismatch: requested <a>, got <b>`다.
- `ai_status`, `ai_analysis`: 실패 진단. `ai_status`가 `pending`이면 진단을 만드는 중이고 `done`이면 끝났다. `ai_analysis`는 JSON 문자열이며 없을 수 있다.
- `ci`: CI 토큰으로 시작한 빌드면 그 Actions 실행 `{"actor", "repository", "ref", "sha", "run_id", "run_attempt", "workflow", "event_name"}`, 아니면 `null`. 값은 모두 문자열이다(`run_id`도).

## 5. 판정 규칙 (CLI)

응답 `builds`의 빌드를 모두 조회해서 판정한다.

1. 하나라도 `failed`면 실패
2. 실패가 없고 하나라도 `cancelled`면 "다른 배포에 밀림"
3. 모두 `running`이면 성공
4. 그 외(진행 중이 남음)면 계속 조회

`running`이 된 서버 빌드는 그 뒤 60초 동안 서버가 앱 Pod을 더 본다(`core/app/deploy/build/postwatch.py`, `WATCH_SECONDS = 60`). 그 사이 재시작이나 OOM이 보이면 같은 빌드가 `failed`로 바뀐다. CLI는 모두 `running`이 된 뒤에도 `--settle`(기본 60초) 동안 계속 조회하고, 그 사이 `failed`가 되면 실패로 끝낸다.

### CLI exit code

`cli/README.md`와 같은 표다.

| 코드 | 뜻 |
|---|---|
| 0 | 성공 (모든 빌드 `running`, 안정화 통과) |
| 1 | 배포 실패 (`failed`, 안정화 중 `failed` 포함) |
| 2 | 사용법 오류, 첫 배포 아님(409), 400·422 |
| 3 | 다른 배포에 밀림 (실패 없이 `cancelled`) |
| 4 | CLI 시간 초과 |
| 125 | 인증, 권한, 연결 오류 |
| 130 | 사용자가 중단 |

## 6. 시간 제한

서버 쪽 기본값(운영 매니페스트에서 바꾸지 않았다)

- 빌드 Job: `activeDeadlineSeconds` 900초 = `BUILD_TIMEOUT_SECONDS` 600 + 300 (`builder/internal/config/config.go`). 빌더는 여기에 30초를 더 기다린 뒤 `failed(timeout)`을 보낸다.
- Argo 대기: `ARGO_WAIT_TIMEOUT` 8분 (같은 파일). 넘으면 `failed(timeout)`.

합계는 약 23분이다. CLI 기본 `--timeout`은 이보다 길게 25분으로 잡는다.

## 7. 라이브 배포 판정 (서버가 하는 일)

CLI가 따로 할 일은 없지만, "그대로"가 무엇인지 알아야 결과를 해석할 수 있어 적어 둔다.

- 서버 슬롯: `kind=build` 서버 행 중 `status=running`인 가장 최근 행.
  - 그 행이 롤백이면 저장소·브랜치·런타임·포트·빌드 방식·경로는 롤백으로 되돌아간 원본 배포에서 가져온다.
  - DB·Redis·저장소 설정은 롤백이 아닌 가장 최근 `running` 서버 행에서 가져온다. 롤백은 이미지만 바꾸고 이 설정들은 롤백 전 배포 그대로 두기 때문이다.
  - 가장 최근 제출이 실패했으면 그 전의 성공한 배포가 기준이다.
- 정적 슬롯: 앱의 정적 사이트가 켜져 있을 때만, `status=running`인 가장 최근 정적 행.
- 정적만 남기는 배포로 서버를 내린 앱은 서버를 빼고 다시 배포한다. 서버 행 뒤에 정적 행이 하나뿐이면 같은 제출의 짝인지 구분할 수 없어 서버가 있는 쪽으로 본다.
- 선언된 슬롯에 `running` 행이 없으면 409다.

## 8. 한계

- 실패한 배포는 기준이 되지 않는다. 마지막 제출이 실패한 상태에서 재배포하면 그 전의 성공한 설정으로 돌아간다.
- 서버 슬롯을 내렸는지는 빌드 행만으로 정확히 알 수 없는 경우가 있다(7절). 그때는 서버를 다시 올리는 쪽을 택한다.
- 빌드 상태는 조회(polling)로만 알 수 있다. 진행 이벤트를 밀어 주는 경로는 없다.
- CI 토큰은 GitHub Actions에서만 받을 수 있다. 사람이 터미널에서 쓸 토큰(로그인)은 아직 없다.

## 9. CI 인증 (GitHub Actions OIDC)

GitHub Actions는 저장된 비밀 없이 재배포를 부를 수 있다. Actions가 GitHub에서 받은 ID 토큰(OIDC JWT)을 core가 검증하고, 그 앱의 그 커밋만 배포할 수 있는 짧은 CI 토큰을 준다.

```
① 앱 주인이 웹 앱 설정에서 "GitHub Actions 배포 허용"을 켠다 (기본 꺼짐)
② 워크플로(permissions: id-token: write)가 GitHub에서 ID 토큰을 받는다 (audience=kodeploy)
③ POST /apps/{app_id}/ci/token  {"id_token": "..."}  → CI 토큰
④ Authorization: Bearer <CI 토큰>으로 재배포(3절)와 상태 조회(4절)
```

### 9-1. 앱 설정: GitHub Actions 배포 허용

- 앱의 `ci_deploy_enabled`. 기본 꺼짐. `GET /apps/{app_id}` 응답에 나온다.
- 켜고 끄는 건 앱 주인만 한다(편집 권한 멤버는 403). `PUT /apps/{app_id}/ci-deploy`, 본문 `{"enabled": true}`, 응답 `{"ci_deploy_enabled": true}`.
- 끄면 이미 발급된 CI 토큰도 그 자리에서 만료된다. 다시 켜도 예전 토큰은 살아나지 않는다. 새로 교환해야 한다.
- 켜면 저장소에 push할 수 있는 사람이 이 앱을 배포할 수 있게 된다.

### 9-2. ID 토큰 받기 (워크플로 안)

워크플로에 `permissions: id-token: write`가 있어야 러너에 아래 두 환경변수가 생긴다.

```
GET $ACTIONS_ID_TOKEN_REQUEST_URL&audience=kodeploy
Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN
→ {"value": "<ID 토큰>", ...}
```

`ACTIONS_ID_TOKEN_REQUEST_URL`에는 이미 쿼리가 붙어 있어서 `&audience=`로 잇는다. 응답의 `value`가 ID 토큰이다. ID 토큰 한 장은 한 번만 교환할 수 있으니 교환할 때마다 새로 받는다.

### 9-3. 교환 API

```
POST /apps/{app_id}/ci/token
Content-Type: application/json

{"id_token": "<GitHub ID 토큰>"}
```

인증 헤더는 없다. 본문의 ID 토큰이 인증이다. 성공하면 `201`.

```json
{"token": "kdci_...", "expires_in": 900, "app_id": "...", "repository": "owner/repo", "ref": "refs/heads/main", "sha": "<40자 소문자>"}
```

- `token`은 이 응답으로만 나온다. 서버에는 sha256만 남는다. 로그·출력에 남기지 않는다.
- `expires_in`은 초다(기본 900). 만료되면 9-6대로 다시 교환한다.

검사 순서. 하나라도 걸리면 거절한다. 오류 본문은 `{"detail", "code"}`이고 `detail`에는 어느 검사에서 걸렸는지만 쓴다(토큰 내용은 쓰지 않는다).

| # | 검사 | 상태 | `code` |
|---|---|---|---|
| 1 | 서명: GitHub JWKS의 키, `RS256`만 (`none`·`HS256` 거절). 형식이 깨진 토큰, 모르는 `kid` 포함 | 401 | `invalid_token` |
| 2 | `iss` == `https://token.actions.githubusercontent.com` | 401 | `invalid_issuer` |
| 3 | `aud` == `kodeploy` | 401 | `invalid_audience` |
| 4 | `exp`, `iat`, `nbf` (허용 시계 차이 60초) | 401 | `token_expired` |
| - | 필수 클레임(`exp`, `iat`, `iss`, `aud`, `jti`, `sub`) 없음 | 401 | `invalid_token` |
| 5 | `jti` 1회용. 이미 교환에 쓴 ID 토큰 | 401 | `token_reused` |
| 6 | 앱이 있음 / GitHub Actions 배포 허용이 켜져 있음 | 404 / 403 | `app_not_found` / `ci_deploy_disabled` |
| 7 | `event_name`이 `push` 또는 `workflow_dispatch` | 403 | `event_not_allowed` |
| 8 | 라이브 슬롯 중 저장소와 브랜치가 모두 토큰과 같은 슬롯이 하나 이상 | 403 | `repo_mismatch`(어느 슬롯도 그 저장소가 아님), `branch_mismatch`(저장소는 같은데 브랜치가 다름, 태그·PR ref 포함) |
| 8 | 라이브 배포가 없음 | 409 | `no_live_deploy`, `no_successful_slot` (3-4와 같다) |
| 9 | `sha`가 40자 hex | 401 | `invalid_sha` |
| - | GitHub JWKS를 받지 못함 | 503 | `jwks_unavailable` |
| - | 본문 형식 오류(칸 없음, 16KB 넘음, 모르는 칸) | 422 | 없음 |

- 저장소 비교는 `https://github.com/<repository>`를 3-1과 같은 규칙(대소문자, `http`/`https`, `.git`·`/`)으로 슬롯의 저장소와 견준다. 브랜치는 `ref`가 `refs/heads/<슬롯 브랜치>`인지 본다.
- 거절된 교환은 `jti`를 쓰지 않는다. 예를 들어 허용이 꺼져 403을 받은 ID 토큰은 허용을 켠 뒤 다시 교환할 수 있다.
- 503은 잠시 뒤 새 ID 토큰으로 다시 보내면 된다.

### 9-4. Bearer를 받는 곳

`Authorization: Bearer kdci_...`는 아래 두 곳에서만 받는다.

- 재배포: `POST /apps/{app_id}/deploy/redeploy` (같은 동작의 `POST /deploy/redeploy?app_id=`도)
- 상태 조회: `GET /apps/{app_id}/deploy/{build_id}`

다른 모든 라우트는 쿠키만 받는다. Bearer만 보낸 요청은 401이다. Bearer 헤더가 있으면 쿠키는 보지 않는다.

요청마다 확인한다.

| 상태 | 언제 | `code` |
|---|---|---|
| 401 | 모르는 토큰, `kdci_`로 시작하지 않음, 앱이 지워짐 | `ci_token_invalid` |
| 401 | 만료됨(발급 후 `expires_in`초가 지났거나, 주인이 허용을 꺼서 만료됨) | `ci_token_expired` |
| 401 | 그 앱의 허용이 지금 꺼져 있음(토큰은 아직 만료 전) | `ci_deploy_disabled` |
| 400 | 경로에 `app_id`가 없음 | `app_id_required` |
| 403 | 경로의 앱이 토큰의 앱이 아님 | `ci_token_app_mismatch` |

### 9-5. CI 토큰으로 재배포할 때의 규칙

- `commit_sha`: 비우면 토큰의 `sha`다. 넣었는데 토큰과 다르면 403 `commit_mismatch`. 형식이 틀리면 400 `invalid_commit_sha`.
- `repo`: 비우면 토큰의 저장소다. 넣었는데 다르면 403 `repo_mismatch`.
- 커밋 고정: 저장소와 브랜치가 모두 토큰과 같은 슬롯에만 토큰의 커밋을 고정한다. 나머지 슬롯(같은 저장소의 다른 브랜치, 예를 들어 정적 슬롯의 `gh-pages`, 또는 다른 저장소의 슬롯)은 그 슬롯 브랜치의 최신으로 같이 다시 빌드된다. 응답 `builds[].ref`로 어느 슬롯이 고정됐는지 보인다. 웹 세션 경로는 지금처럼 저장소만 본다(3-1).
- 교환 뒤에 설정이 바뀌어 토큰의 저장소·브랜치를 쓰는 슬롯이 없어지면 403 `repo_mismatch` / `branch_mismatch`.
- 빌드는 앱 주인 이름으로 시작한다(소유권·쿼터 규칙이 그대로 돈다).
- 만든 빌드에는 Actions 실행 정보가 남는다(4절 `ci`, 웹 빌드 목록·상세의 "GitHub Actions · <actor> · run <run_id>").
- Idempotency-Key 규칙은 3-2와 같다. 키는 앱 단위라 토큰을 다시 교환한 뒤 같은 키로 보내도 처음 빌드를 돌려준다.

### 9-6. 401을 받으면

재배포나 상태 조회가 401을 받으면, 새 ID 토큰(9-2)을 받아 다시 교환(9-3)하고 같은 요청을 한 번 다시 보낸다. 재배포라면 같은 Idempotency-Key를 쓴다. 교환이 401·403·404로 거절되면 다시 해도 같으니 멈추고 `detail`을 보여 준다(예: 허용이 꺼짐 `ci_deploy_disabled`, 다른 브랜치 `branch_mismatch`).

### 9-7. curl 예시

```yaml
on:
  push:
    branches: [main]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - env:
          APP_ID: <앱 id>
          API: https://api.kodeploy.com
        run: |
          set -eu
          ID_TOKEN=$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=kodeploy" | jq -r .value)
          TOKEN=$(curl -fsS -X POST "$API/apps/$APP_ID/ci/token" \
            -H 'Content-Type: application/json' -d "{\"id_token\":\"$ID_TOKEN\"}" | jq -r .token)
          curl -fsS -X POST "$API/apps/$APP_ID/deploy/redeploy" \
            -H "Authorization: Bearer $TOKEN" -H "Idempotency-Key: gha-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" \
            -H 'Content-Type: application/json' -d '{}'
```

## 10. 오류 코드 목록

오류 본문의 `code` 전체. 새 코드가 생길 수 있으니 모르는 값은 일반 오류로 다룬다.

| `code` | 상태 | 어디서 | 뜻 |
|---|---|---|---|
| `app_id_required` | 400 | 재배포·상태(CI 토큰) | 경로에 `app_id`가 없음 |
| `invalid_commit_sha` | 400 | 재배포 | `commit_sha`가 40자 hex가 아님 |
| `invalid_idempotency_key` | 400 | 재배포 | 키 형식 오류 |
| `repo_not_used` | 400 | 재배포 | `repo`를 쓰는 슬롯이 없음 |
| `deploy_rejected` | 400 | 재배포 | 배포 조건 검사 실패 |
| `repo_not_allowed` | 403 | 재배포 | 편집 권한 멤버가 이 앱이 쓰던 저장소 밖으로 |
| `commit_mismatch` | 403 | 재배포(CI 토큰) | 토큰과 다른 커밋 |
| `repo_mismatch` | 403 | 재배포(CI 토큰)·교환 | 토큰의 저장소를 쓰는 슬롯이 없음, 또는 요청 `repo`가 토큰과 다름 |
| `branch_mismatch` | 403 | 재배포(CI 토큰)·교환 | 저장소는 같지만 토큰의 브랜치를 쓰는 슬롯이 없음 |
| `no_live_deploy` | 409 | 재배포·교환 | 라이브 배포가 하나도 없음(첫 배포는 웹에서) |
| `no_successful_slot` | 409 | 재배포·교환 | 선언된 슬롯에 성공한 배포가 없음 |
| `key_in_progress` | 409 | 재배포 | 같은 키의 첫 요청이 아직 처리 중. 같은 키로 다시 |
| `key_released` | 409 | 재배포 | 같은 키의 첫 요청이 실패해 선점이 풀림. 같은 키로 다시 |
| `ci_token_invalid` | 401 | 재배포·상태 | 모르는 CI 토큰 |
| `ci_token_expired` | 401 | 재배포·상태 | 만료된 CI 토큰. 다시 교환 |
| `ci_deploy_disabled` | 401 / 403 | 재배포·상태 / 교환 | GitHub Actions 배포 허용이 꺼짐 |
| `ci_token_app_mismatch` | 403 | 재배포·상태 | 다른 앱용 CI 토큰 |
| `invalid_token` | 401 | 교환 | 서명·알고리즘·형식·필수 클레임 |
| `invalid_issuer` | 401 | 교환 | `iss`가 GitHub Actions가 아님 |
| `invalid_audience` | 401 | 교환 | `aud`가 `kodeploy`가 아님 |
| `token_expired` | 401 | 교환 | `exp`·`iat`·`nbf` 밖 |
| `token_reused` | 401 | 교환 | 이미 교환에 쓴 ID 토큰 |
| `invalid_sha` | 401 | 교환 | ID 토큰의 `sha`가 40자 hex가 아님 |
| `app_not_found` | 404 | 교환 | 앱이 없음 |
| `event_not_allowed` | 403 | 교환 | `push`·`workflow_dispatch`가 아닌 이벤트 |
| `jwks_unavailable` | 503 | 교환 | GitHub 서명 키를 받지 못함. 잠시 뒤 다시 |
