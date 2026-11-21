# kodeploy CLI

GitHub Actions나 터미널에서 KoDeploy 앱을 지금 설정 그대로 재배포하고, 끝날 때까지 기다린 뒤 결과를 exit code로 돌려준다.
서버와의 약속은 [`docs/deploy-api-contract.md`](../docs/deploy-api-contract.md)에 있다. CLI는 그 문서만 보고 만들었다.

## 설치

릴리스(`cli-v*` 태그)마다 `linux/amd64`, `linux/arm64`, `darwin/arm64`, `darwin/amd64` 바이너리와 `checksums.txt`가 올라간다.

```sh
VERSION=0.1.0
curl -fsSLO "https://github.com/kodeploy/kodeploy/releases/download/cli-v${VERSION}/kodeploy_${VERSION}_linux_amd64.tar.gz"
curl -fsSLO "https://github.com/kodeploy/kodeploy/releases/download/cli-v${VERSION}/checksums.txt"
sha256sum --ignore-missing -c checksums.txt
tar -xzf "kodeploy_${VERSION}_linux_amd64.tar.gz" kodeploy
```

Go가 있으면 커밋을 골라 설치할 수도 있다.

```sh
go install github.com/kodeploy/kodeploy/cli/cmd/kodeploy@<커밋>
```

## 명령

```
kodeploy deploy  --app <app_id> [--sha <40자>] [--timeout 25m] [--settle 60s] [--no-wait] [--output text|json]
kodeploy status  --app <app_id> <build_id> [--output text|json]
kodeploy version
```

| 설정 | 플래그 | 환경변수 | 기본값 |
|---|---|---|---|
| 서버 주소 | `--server` | `KODEPLOY_SERVER` | `https://api.kodeploy.com` |
| 앱 | `--app` | `KODEPLOY_APP` | 없음(필수) |
| OIDC audience | `--audience` | `KODEPLOY_AUDIENCE` | `kodeploy` |
| 토큰 직접 지정 | 없음 | `KODEPLOY_TOKEN` | 없음 |

플래그가 환경변수보다 우선한다. `KODEPLOY_DEBUG=1`이면 요청마다 `METHOD 경로 → 상태` 한 줄을 stderr에 쓴다(헤더·본문은 쓰지 않는다).

## 인증

1. `KODEPLOY_TOKEN`이 있으면 그대로 `Authorization: Bearer`로 쓴다. 401이면 다시 받지 않고 125로 끝난다.
2. GitHub Actions 러너에서 `ACTIONS_ID_TOKEN_REQUEST_URL`과 `ACTIONS_ID_TOKEN_REQUEST_TOKEN`이 있으면 GitHub ID 토큰(audience=`kodeploy`)을 받아 `POST /apps/{app}/ci/token`으로 CI 토큰과 바꾼다. 배포 중 401이 오면 새 ID 토큰으로 다시 교환하고 같은 요청을 한 번 더 보낸다(실행 전체에서 최대 5번).
3. 둘 다 없으면 125. 워크플로에 `permissions: id-token: write`가 있어야 2번 환경변수가 생긴다.

CI 토큰을 쓰려면 앱 주인이 웹 앱 설정에서 "GitHub Actions 배포 허용"을 켜야 한다. 그 앱의 배포 브랜치에서 `push` 또는 `workflow_dispatch`로 돈 워크플로만 교환할 수 있다.

토큰 원문(ID 토큰, CI 토큰, `ACTIONS_ID_TOKEN_REQUEST_TOKEN`, `KODEPLOY_TOKEN`)은 어떤 출력에도 남기지 않는다.

## GitHub Actions 예시

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
    timeout-minutes: 30
    steps:
      - name: install kodeploy
        run: |
          VERSION=0.1.0
          curl -fsSLO "https://github.com/kodeploy/kodeploy/releases/download/cli-v${VERSION}/kodeploy_${VERSION}_linux_amd64.tar.gz"
          curl -fsSLO "https://github.com/kodeploy/kodeploy/releases/download/cli-v${VERSION}/checksums.txt"
          sha256sum --ignore-missing -c checksums.txt
          tar -xzf "kodeploy_${VERSION}_linux_amd64.tar.gz" -C /usr/local/bin kodeploy
      - run: kodeploy deploy --app <앱 id>
```

CI 토큰이면 `--sha`를 주지 않아도 서버가 워크플로가 돈 커밋(`GITHUB_SHA`)으로 배포한다.

## deploy가 하는 일

1. 이름표(Idempotency-Key)를 정한다. GitHub Actions면 `gha-<GITHUB_RUN_ID>-<GITHUB_RUN_ATTEMPT>`, 아니면 `cli-<무작위 UUID>`. 한 번 실행하는 동안 다시 보낼 때는 같은 이름표를 쓰고, Actions에서 Re-run하면 attempt가 바뀌어 새 배포가 된다.
2. `POST /apps/{app}/deploy/redeploy`를 보낸다.
   - 202(새 배포)나 200(`replayed`, 같은 이름표로 이미 처리됨)이면 그 빌드들을 기다린다.
   - 409 `key_in_progress`·`key_released`면 2·4·8·16초 뒤(합 30초) 같은 이름표로 다시 보낸다.
   - 5xx·네트워크 오류는 1·2·4·8·16초 뒤 같은 이름표로 다시 보낸다(최대 5번).
   - 409는 `detail` 문구가 아니라 기계용 `code`로 가른다. `no_live_deploy`면 "첫 배포는 웹에서" 안내와 함께 2, 그 밖의 409와 400·422는 2, 403·404는 125.
3. `--no-wait`이면 빌드 목록만 출력하고 0으로 끝난다.
4. 빌드마다 `GET /apps/{app}/deploy/{build_id}`로 조회한다. 간격은 2초에서 시작해 바뀌지 않으면 두 배씩 10초까지 늘고, 상태가 바뀌면 2초로 돌아간다. 상태가 바뀔 때만 한 줄씩 출력한다. 같은 빌드 조회가 연속 10번 실패하면 125.
5. 판정(계약 5절): 하나라도 `failed`면 실패, 실패 없이 `cancelled`가 있으면 밀림, 모두 `running`이면 성공.
6. 모두 `running`이 된 뒤에도 `--settle`(기본 60초) 동안 계속 조회한다. 서버가 그 사이 앱 재시작이나 OOM을 보고 빌드를 `failed`로 바꾸면 1로 끝난다. `--settle 0`이면 바로 성공이다.
7. `--timeout`(기본 25분)은 요청부터 안정화까지 전체에 걸린다. 넘으면 4. 시간 초과나 Ctrl+C(130)로 멈춰도 서버 빌드는 취소하지 않는다.
8. 실패하면 빌드의 `error`를 출력하고, AI 진단이 만들어지는 중(`ai_status: pending`)이면 최대 30초 더 기다렸다가 요약해서 보여 준다.
9. 실패하거나 밀린 배포를 새 이름표로 다시 배포하지 않는다.

## 출력

진행 상황은 stderr, 최종 결과는 stdout이다. 터미널이 아니거나 `NO_COLOR`가 있으면 색을 쓰지 않는다.

```
kodeploy: 재배포 요청 app=<id> key=gha-98765-1
[server 3f9a2c1d] queued → building → built → deploying → running (6m12s)
[static 8e7d6c5b] queued → building → deploying → running (2m40s)
kodeploy: 안정화 확인 1m0s...
kodeploy: 성공 source=3f9a1c2
```

`--output json`이면 stdout에 결과 객체 하나만 쓴다. `result`는 `success`, `failed`, `superseded`, `timeout`, `error`, `interrupted`, `requested`(`--no-wait`) 중 하나다.

```json
{"result":"success","exit_code":0,"app_id":"...","requested_sha":"...","builds":[{"build_id":"3f9a2c1d","slot":"server","status":"running","source_sha":"...","error":null}],"message":"성공 source=3f9a1c2"}
```

GitHub Actions 안이면(`GITHUB_ACTIONS=true`) 실패 때 `::error title=kodeploy::<이유>` 한 줄을 쓰고, `$GITHUB_STEP_SUMMARY`에 앱·커밋·슬롯별 상태와 시간·실패 이유 표를 더한다.

## exit code

| 코드 | 뜻 |
|---|---|
| 0 | 성공 (모든 빌드 `running`, 안정화 통과) |
| 1 | 배포 실패 (`failed`, 안정화 중 `failed` 포함) |
| 2 | 사용법 오류, 다시 보내도 같은 409(첫 배포 아님, 칸의 커밋 기록 없음 등), 400·422 |
| 3 | 다른 배포에 밀림 (실패 없이 `cancelled`) |
| 4 | CLI 시간 초과 |
| 125 | 인증, 권한, 연결 오류 |
| 130 | 사용자가 중단 |

## 개발

```sh
cd cli
go vet ./...
go test -race ./...
go test ./internal/output -update   # 출력 골든 파일을 새로 쓸 때
go build -ldflags "-X github.com/kodeploy/kodeploy/cli/internal/cmd.version=dev" ./cmd/kodeploy
```

테스트는 계약 문서대로 만든 가짜 KoDeploy 서버와 가짜 GitHub OIDC 엔드포인트(`httptest`), 가짜 시계로 돈다.
