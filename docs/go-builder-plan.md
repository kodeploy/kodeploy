# KoDeploy Go 빌더 작업 지시서 (3부-2)

Claude Code 작업 지시서다. Argo CD 경로는 완성됐다(데모 앱 `tenant-d6d8b759` Synced/Healthy, envRevision 시험 통과). 이번 작업은 core 안의 빌드 파이프라인(파이썬)을 **별도 Go 서비스 `builder`** 로 옮기는 것이다. core와 연결하는 작업은 다음(4부)이고, 이번에는 빌더가 단독으로 동작하고 테스트되는 것까지 한다.

원칙:
- 원본에서 찾을 수 있는 값은 추측하지 말고 원본에서 가져온다. 원본: `core/app/deploy/build/pipeline.py`, `core/app/deploy/stack/manifests/build.py`, `templates/buildkit_job.yaml.j2`, `core/app/config.py`.
- `kodeploy/` 모노레포 안에 `builder/`를 새로 만든다. core 코드는 수정하지 않는다. `deploy/k8s/builder/`, `.github/workflows/build-builder.yml`은 새로 추가한다.
- 이번 범위 밖: nixpacks·static 빌드 모드, private repo(git-auth Secret), AI 진단, DB 접근, CRD, controller-runtime.
- 각 단계 끝에 바꾼 파일과 테스트 결과를 보고한다. 원본과 다르게 처리한 부분은 이유와 함께.

---

## 1. 무엇이 어디로 가나

| 지금 (파이썬) | 원본 위치 | Go에서 |
|---|---|---|
| 빌드 Job 생성 | `build.py` `buildkit_job()`, `buildkit_job.yaml.j2` | `job`: client-go 타입 구조체로 같은 모양 |
| 로그 1초마다 전량 재조회 | `pipeline.py` `_tail_build_logs`, `_combined_job_logs` | `logs`: follow 스트림, 새 줄만 |
| push 마커 정규식 | `_EXPORT_IMAGE_RE`, `_PUSH_DONE_RE` | `marker`: 이 빌드의 이미지 이름으로 좁힘 + digest 추출 |
| Job 3초 폴링 | `_wait_for_job` | `job`: Watch + 재연결 |
| 마커 vs Job 종료 경주 | `_wait_first` | `run`: `select` |
| reap (뒤에서 끝난 export 결과 수거) | `_run_build` 뒷부분 | `run`: `finished` 이벤트 |
| 취소 | `_check_cancelled`, `_cleanup_build_job` | `DELETE` + context 취소 |
| 진행 상태 | 스레드 지역 변수 | Job 어노테이션 |
| 배포 적용 (patch/replace 등) | `stack/resources.py` | **없음**. values 커밋 → Argo가 적용 |
| 신규 | — | `registry`(digest 확인), `gitops`(커밋), `argo`(refresh·대기), `callback`(core 보고) |
| core에 남는 것 | 요청 검증, 앱 이름, `builds` 행, 시크릿·PVC, R2·Cloudflare, AI 진단, 계측 기록 | 그대로 (4부에서 연결) |

---

## 2. 구조

Go 모듈 `github.com/kodeploy/kodeploy/builder`, Go 1.23. 의존성은 client-go(+ k8s.io/api, apimachinery), go-containerregistry(digest 확인)만. git은 GitHub Contents API를 `net/http`로 직접 부른다. 서명은 표준 `crypto/hmac`.

```
builder/
  cmd/builder/main.go
  cmd/mockcore/main.go        # 개발용: core 대신 콜백을 받아 찍어주는 서버 (선택)
  internal/
    api/        HTTP 서버, 서명·요청 검증, 202/400/401/409, DELETE, /healthz
    run/        빌드 하나의 상태기계(select), 재시작 재개
    job/        Job 생성(원본 템플릿 이식), Watch, 어노테이션 patch, 삭제
    logs/       init → main 컨테이너 follow, 줄 채널, 1초 배치
    marker/     push 마커·digest 추출
    registry/   digest 존재 확인(HEAD), 태그 → digest 폴백
    gitops/     단일 writer 고루틴, Contents API, values 병합, 경로 검사
    argo/       refresh 어노테이션, Application 상태 대기
    callback/   core 이벤트 전송, 재시도, acked-seq
    sign/       HMAC 서명 생성·검증 (core와 같은 규칙)
    config/     env 로드
  Dockerfile
deploy/k8s/builder/           # Deployment, Service, SA, RBAC, NetworkPolicy
.github/workflows/build-builder.yml
```

---

## 3. 계약

### 3-1. core → 빌더

```
POST /internal/deploys
{
  "build_id": "b-3f9a2c1d",
  "actor": "7c1e9a42",                     // 선택. 요청한 유저 id. 커밋 메시지 기록용 (권한과 무관)
  "namespace": "tenant-d6d8b759",
  "slot": "server",                        // server | static
  "kind": "build",                         // build | config | set-image
  "values": { ... },                       // core 소유 키만 (3-4)
  "unit": { "runtime": "java", "port": 8080 },   // build·set-image 이고 slot=server 일 때
  "build": {                               // kind=build 일 때만
    "repo": "https://github.com/foo/bar.git",
    "ref": "main",
    "mode": "dockerfile",
    "dockerfile_dir": "",
    "dockerfile_name": "Dockerfile",
    "image_repo": "ghcr.io/<GHCR_USER>/<hex8>/<app>",
    "image_tag": "b-3f9a2c1d",
    "cache_ref": "ghcr.io/<GHCR_USER>/<hex8>/<app>:buildcache",  // 없으면 캐시 플래그 생략
    "git_auth_secret": "",                 // 예약. private repo용 Secret 이름. 값이 오면 400 "not supported yet"
    "project_path": ""                     // 예약. nixpacks용. 값이 오면 400 "not supported yet"
  },
  "image": "ghcr.io/...:<tag>@sha256:<64hex>"    // kind=set-image 일 때만
}
```

응답:
- `202 {build_id}`
- `400` 검증 실패 (이유 포함). `build.mode`가 `dockerfile`이 아니거나 예약 칸에 값이 있으면 `not supported yet`.
- `401` 서명 실패
- `409` 같은 `build_id`가 진행 중이거나, **같은 `namespace`+`slot`의 빌드가 진행 중** (본문에 진행 중인 `build_id`). core는 먼저 그 빌드를 `DELETE`로 취소한 뒤 다시 보낸다 (지금 `_cancel_stale_builds`와 같은 역할).
- `429` 진행 중인 빌드가 `MAX_ACTIVE_BUILDS`에 도달 (`Retry-After` 헤더, 초). core는 "대기 중"으로 표시하고 다시 보낸다.

`DELETE /internal/deploys/{build_id}` → `202` (없으면 `404`). `GET /healthz` → `200`.

| kind | Job | 이미지 칸 | runtime·port (서버) | 쓰임 |
|---|---|---|---|---|
| `build` | 만듦 | 해당 slot 칸을 새 digest로 | `unit` 값 | 배포 |
| `config` | 없음 | 두 칸 모두 git 현재 값 유지 | git 현재 값 유지 | 설정 변경 (4부 이후) |
| `set-image` | 없음 | 해당 slot 칸을 `image`로 | `unit` 값 | 롤백, 기존 앱 넘겨받기 |
| `delete` | 없음 | `apps/<namespace>/values.yaml` 삭제 | — | 앱 삭제. 폴더가 사라지면 ApplicationSet이 Application을 지운다. 리소스 삭제는 core의 네임스페이스 삭제가 맡는다 (`preserveResourcesOnDeletion`) |

이번 작업에서 `slot=static`의 `build`는 Job 템플릿이 없으므로 `400`으로 거부한다. `set-image`·`config`는 static도 허용한다.

### 3-2. 빌더 → core

```
POST {CORE_URL}/internal/builds/{build_id}/events
{ "seq": 1, "type": "log",       "at": "...", "lines": ["..."] }
{ "seq": 7, "type": "committed", "at": "...", "image": "...", "commit_sha": "...", "push_done_at": "...", "triggered_early": true }
{ "seq": 8, "type": "deployed",  "at": "...", "image": "...", "synced_at": "...", "healthy_at": "..." }
{ "seq": 9, "type": "finished",  "at": "...", "job_ended_at": "...", "job_succeeded": true, "export_failed": false }
{ "seq": 9, "type": "failed",    "at": "...", "stage": "build|commit|sync|health|timeout", "reason": "...", "last_lines": ["..."] }
{ "seq": 9, "type": "cancelled", "at": "..." }
{ "seq": 2, "type": "deleted",   "at": "...", "commit_sha": "..." }
```

- `seq`는 빌드마다 1부터 단조 증가. core는 `(build_id, seq)`로 중복을 무시한다(4부).
- **최소 한 번 전달**: 2xx를 받을 때까지 백오프(1s → 최대 30s)로 재전송한다. 순서를 지키기 위해 빌드마다 큐 하나, 보내는 고루틴 하나. 받은 마지막 seq를 Job 어노테이션 `kodeploy.io/acked-seq`에 적는다.
- `log`는 1초 배치, 이벤트당 최대 200줄. 큐에 5,000줄 넘게 쌓이면 오래된 줄부터 버리고 "N줄 생략" 한 줄을 남긴다.
- `committed`·`deployed`·`failed`·`finished`는 절대 버리지 않는다.

### 3-3. 서명 (양방향 같은 규칙)

- 헤더 `X-Kodeploy-Timestamp`(unix 초), `X-Kodeploy-Signature`
- `signature = hex(HMAC-SHA256(secret, timestamp + "\n" + METHOD + "\n" + path + "\n" + body))`
- 검증: 시각 차이 60초 이내, 상수 시간 비교. 실패는 `401`. 본문은 검증 후에만 파싱한다.

### 3-4. values 병합 규칙 (칸 주인)

- **빌드와 묶인 칸**: `image`, `static.image`, `runtime`, `port`. 빌더만 쓴다. core가 보낸 `values`에 이 키가 있으면 `400`.
- **core 소유 칸**: 나머지 전부 (`name`, `userId`, `replicas`, `envRevision`, `db`, `redis`, `volume`, `static.enabled`, `static.hostnames`, `hostnames`). 차트의 `values.schema.json`이 허용하는 키를 기준으로 한다.
- 커밋할 파일 만들기:
  1. git에 파일이 있으면 그 내용에서 시작, 없으면 빈 값(`image: ""`, `runtime: none`, `port: null`)에서 시작.
  2. core 소유 칸은 `values`의 값으로 덮는다.
  3. 요청 slot의 묶인 칸은 위 표대로. 다른 slot의 묶인 칸은 현재 값 유지.
  4. 키 순서를 고정해서 직렬화하고, 현재 파일과 같으면 커밋하지 않는다.
- 경로는 `apps/<namespace>/values.yaml` 하나뿐이며 `namespace`는 서명된 요청에서만 가져온다.

---

## 4. 동작

### 4-1. 요청 검증 (서명 다음)
- `namespace`: `^(tenant|app)-[a-f0-9]{8}$`
- `build.image_repo`, `image`: `ghcr.io/<GHCR_USER>/` 아래. 경로의 hex8 세그먼트가 `namespace`의 hex8과 같아야 한다.
- `image`(set-image): `<repo>:<tag>@sha256:<64hex>` 형식.
- `values`: 금지 키 없음.
- 같은 `build_id`가 진행 중이면 `409`. 같은 `namespace`+`slot`의 `build`가 진행 중이어도 `409`.
- 진행 중인 빌드 수가 `MAX_ACTIVE_BUILDS` 이상이면 `429`.
- `delete`는 `values`·`unit`·`build`·`image`가 없어야 한다. 파일이 이미 없으면 성공으로 본다.
- `actor`는 `^[a-f0-9]{8,32}$`. 없어도 된다.

### 4-2. `build`
1. **Job 생성**: `buildkit_job.yaml.j2`를 구조체로 옮긴다. 이름 `build-<build_id>`. 원본 라벨(`app=kodeploy-build`, `build-id`, `user-id`)은 core의 `service.py`가 로그를 찾는 데 쓰므로 유지하고, `kodeploy.io/managed=true`를 추가한다. 어노테이션 `kodeploy.io/request`에 요청 JSON을 저장한다(재개용). `BUILDKIT_IMAGE`, `activeDeadlineSeconds`, `ttlSecondsAfterFinished: 1200`, `backoffLimit: 0`, seccomp 설정, init(alpine/git) → emptyDir → main 구조 전부 원본 그대로. `cache_ref`가 있을 때만 `--import-cache`/`--export-cache`.
2. **로그**: Pod이 생기면 init 컨테이너를 follow로 EOF까지 읽고, 이어서 main 컨테이너를 follow로 읽는다. `bufio.Scanner`로 줄 단위. 각 줄을 배치 채널로 보내고 마커를 검사한다. 스트림이 끊기면 재연결.
3. **마커**: 원본은 `exporting to image` 다음에 오는 `pushing manifest .*\bdone\b`이다. Go에서는 이 빌드의 이미지 이름으로 좁힌다. `exporting to image`를 본 뒤, `pushing manifest for <image_repo>:<image_tag>@sha256:([a-f0-9]{64})` 이고 같은 줄에 `done`이 있는 첫 줄. 캐시(`:buildcache`) 줄과 유저 `RUN` 출력은 걸러진다. digest는 첫 번째 것만 (`sync.Once`), `pushDone` 채널(버퍼 1).
4. **Job Watch**: 이름 field selector로 Watch. succeeded/failed → `jobDone`(버퍼 1). Watch가 끊기면 백오프로 재연결. API 에러는 반환값으로 처리하고 재시도한다. Job이 사라졌으면 취소로 본다.
5. **경주**:
   ```go
   select {
   case d := <-pushDone:   deploy(d); res := <-jobDone; finished(res)
   case r := <-jobDone:    if r.ok { deploy(lookupByTag()) } else { failed("build") }
   case <-ctx.Done():      cleanup(context.Cause(ctx))
   }
   ```
6. **배포 경로**: `registry`로 digest가 `image_repo`에 실제로 있는지 HEAD 확인 → `gitops`로 커밋 → Job 어노테이션(`digest`, `commit-sha`) → `committed` 콜백 → `argo` refresh → 대기 → `deployed` 콜백.
7. **reap**: `deployed` 뒤에도 Job 종료를 기다려 `finished`를 보낸다. push 이후 Job이 실패했으면 `export_failed: true`, 배포 상태는 건드리지 않는다.
8. **폴백**: 마커 없이 Job이 성공하면 `image_repo:image_tag`의 digest를 레지스트리에서 조회해 같은 배포 경로로 간다.

### 4-3. `set-image`, `config`, `delete`
Job 없음. `set-image`는 4-1의 이미지 검증 + 레지스트리 HEAD → 커밋 → refresh → 대기 → `committed`, `deployed` 또는 `failed`. `config`는 커밋 → refresh → 대기. `delete`는 파일 삭제 커밋 → 콜백 `deleted`(commit_sha 포함). Argo 대기는 하지 않는다(Application이 사라지는 쪽이라). 진행 상태는 메모리로 충분하다. 빌더가 재시작되어 끊기면 core가 같은 요청을 다시 보내고, 같은 내용이면 커밋은 건너뛴다.

### 4-4. Argo refresh와 대기
- refresh: argocd 네임스페이스의 Application `<namespace>`에 어노테이션 `argocd.argoproj.io/refresh: normal`을 patch.
- 대기: 2초마다 Application을 읽어 `status.sync.revisions`에 커밋 SHA가 있고, `status.sync.status == Synced`, `status.health.status == Healthy`면 완료. `status.operationState.phase`가 `Failed`/`Error`면 그 `message`로 `failed(stage=sync)`. 타임아웃(`ARGO_WAIT_TIMEOUT`, 기본 8분)이면 `failed(stage=timeout)`. 8분은 Argo 폴링 3분 + Java 기동을 감안한 값이다.

### 4-5. 재시작 재개
시작할 때 `kodeploy.io/managed=true` 라벨 Job 중 `kodeploy.io/finished` 어노테이션이 없는 것을 나열해, 어노테이션에서 요청을 복원하고 없는 단계부터 이어간다. `digest` 없음 → 로그·Watch부터, `digest` 있고 `commit-sha` 없음 → 커밋부터, `commit-sha` 있고 `synced` 없음 → 대기부터. 이벤트 seq는 `acked-seq` 다음부터 이어 붙인다.

### 4-6. 취소와 종료
- `DELETE`: `cancel(errUserCancelled)` → Job 삭제 → `cancelled` 콜백.
- SIGTERM: `cancel(errShutdown)` → **Job은 지우지 않는다** → 20초 안에 종료. 재시작 후 4-5로 이어간다.
- `context.WithCancelCause`로 둘을 구분한다.

### 4-7. gitops writer
고루틴 하나가 채널로 요청을 받아 순서대로 처리한다. GET(현재 내용과 sha) → 병합 → 같으면 건너뜀 → PUT(sha 포함, 메시지 `deploy(<namespace>): <kind> <image_tag> [<build_id>] by <actor>` (`actor`가 없으면 `by core`)) → 409면 다시 GET 후 재시도(최대 5회) → 커밋 SHA 반환.

---

## 5. 설정 (env)

| 이름 | 뜻 | 기본 |
|---|---|---|
| `LISTEN` | 수신 주소 | `:8080` |
| `CORE_URL` | 콜백 대상 | — |
| `HMAC_SECRET` | 서명 키 (core와 공유) | — |
| `GITHUB_TOKEN` | `kodeploy-apps` 쓰기 토큰 | — |
| `GITOPS_REPO`, `GITOPS_BRANCH` | `kodeploy/kodeploy-apps`, `main` | |
| `GHCR_TOKEN` | 레지스트리 읽기 (digest 확인) | — |
| `GHCR_USER` | 이미지 경로 검증용 | — |
| `BUILD_NAMESPACE` | `kodeploy-build` | |
| `ARGOCD_NAMESPACE` | `argocd` | |
| `BUILDKIT_IMAGE` | 원본 config 값 | `moby/buildkit:v0.30.0-rootless` |
| `BUILD_ACTIVE_DEADLINE_SECONDS` | 원본 config와 같은 규칙 | |
| `ARGO_WAIT_TIMEOUT` | 4-4 | `8m` |
| `LOG_BATCH_INTERVAL` | 3-2 | `1s` |
| `MAX_ACTIVE_BUILDS` | 동시 빌드 상한 (Job이 있는 `build`만 센다) | `3` |

비밀값 세 개(`HMAC_SECRET`, `GITHUB_TOKEN`, `GHCR_TOKEN`)는 K8s Secret `kodeploy-builder-secrets`에서 env로 받는다. Secret 자체는 git에 넣지 않는다(사람이 만든다).

---

## 6. 배포물

- **Dockerfile**: `golang:1.23`에서 `CGO_ENABLED=0 GOOS=linux GOARCH=arm64`로 빌드 → `gcr.io/distroless/static:nonroot`. 바이너리 하나.
- **CI** `.github/workflows/build-builder.yml`: `build-core.yml`을 본떠 만든다. `builder/**` 변경 시 실행, 이미지 `ghcr.io/kodeploy/kodeploy-builder`(소유자 이름은 소문자로 변환), core처럼 `deploy/k8s/builder/deployment.yaml`에 digest를 고정 커밋.
- **`deploy/k8s/builder/`** (네임스페이스 `default`, core 옆):
  - `deployment.yaml`: replicas 1, 요청 50m/64Mi, 상한 메모리 512Mi, worker2 선호(강제 아님), restricted 보안 설정, SA 토큰 마운트 필요.
  - `service.yaml`: ClusterIP 8080.
  - `serviceaccount.yaml`: `kodeploy-builder`.
  - `rbac.yaml`: Role(kodeploy-build): jobs `create get list watch delete patch`, pods `get list watch`, pods/log `get`. Role(argocd): applications.argoproj.io `get list watch patch`. RoleBinding 둘. **ClusterRole 없음.**
  - `networkpolicy.yaml`: ingress는 core Pod에서만(8080). egress는 제한하지 않는다(API 서버, GitHub, GHCR 필요).

---

## 7. 테스트

- `marker`: 표 테스트. BuildKit 실제 형식 줄로 최소 6개. 정답: 이 빌드의 이미지 push 줄. 오답: `:buildcache` push 줄, `exporting to image` 앞에 나온 push 줄, 유저 RUN이 찍은 가짜 줄, digest 길이가 틀린 줄.
- `gitops` 병합: kind 3종 × slot 2종, 금지 키 거부, 파일 없음, 같은 내용이면 커밋 안 함.
- 경로·이미지 검증: namespace 패턴, hex8 불일치, 다른 GHCR_USER.
- `sign`: 정상, 시각 초과, 서명 불일치.
- `gitops`·`callback`: `httptest` 서버로 200/409/5xx 흐름.
- 취소·종료 시 고루틴 누수: `go.uber.org/goleak`.
- `go vet`, `go test ./...` 통과.
- E2E(사람이 한다): `cmd/mockcore`를 띄우고, 공개 테스트 repo로 서명한 요청을 curl로 보내 Job → 로그 → committed → deployed까지 확인.

---

## 8. 완료 조건

- [ ] 2의 구조대로 `builder/` 생성, `go build ./...` 통과
- [ ] 7의 테스트 전부 통과
- [ ] Dockerfile, CI 워크플로, `deploy/k8s/builder/` 작성
- [ ] `cmd/mockcore`로 E2E 절차와 서명 예시 스크립트 제공 (`hack/sign-request.sh` 같은 것)
- [ ] 보고: 원본과 다르게 처리한 부분, 사람이 만들어야 하는 Secret과 값, 실행·확인 명령

---

## 9. 하지 말 것

- core 코드 수정. DB 접근. 테넌트 네임스페이스 쓰기.
- 차트 파일 수정, `apps/<namespace>/values.yaml` 외 경로 쓰기.
- core가 보낸 이미지·runtime·port 값을 그대로 쓰기 (금지 키는 `400`).
- 비밀값을 로그·에러 메시지·커밋에 남기기. 요청 JSON을 어노테이션에 저장할 때도 비밀값은 없어야 한다(설계상 없음).
- 폴링으로 Job 상태 읽기(Watch를 쓴다). `time.Sleep` 기반 대기에 context 없이 들어가기.
- 서비스 외부 노출(HTTPRoute, Ingress).

---

## 10. 다음 작업 미리보기 (4부, 이번엔 하지 않는다)

core 쪽 연결이다. 빌더 계약은 이 내용과 맞아야 한다.
- Alembic: `pipeline`(v1/v2), `build_records.git_committed_at`, `argo_synced_at`.
- `/deploy`: v2 앱이면 values(core 소유 키만)와 `unit`을 만들어 서명해서 빌더에 POST. `spawn_background(_run_build)`는 부르지 않는다.
- 콜백 엔드포인트: 서명 검증, `(build_id, seq)` 중복 무시, `builds` 갱신, `failed`면 기존 AI 진단 호출.
- 기존 앱 넘겨받기: core가 그 앱 리소스의 `managedFields`를 비우고, 지금 떠 있는 이미지(digest)와 runtime·port로 `set-image`를 보낸다. `deployed`가 오면 `pipeline = v2`.
- v2 앱의 env·도메인·취소는 `config`가 붙기 전까지 501. 삭제는 `delete` → Application이 사라진 것을 확인 → core가 네임스페이스 삭제(시크릿·PVC 포함) → R2·Cloudflare 정리 순서로 만든다. 순서가 바뀌면 Argo가 네임스페이스를 다시 만든다.
- 다중앱 전환 때 새 앱의 이미지 경로는 `ghcr.io/<GHCR_USER>/<app hex8>/<app>`로 만든다. 그래야 빌더의 "네임스페이스 hex8 = 이미지 경로 hex8" 검사가 `app-<hex8>` 네임스페이스에서도 통한다.
- 앱 소유권 이전 기능을 붙일 때는 차트에서 `userId` 라벨을 Pod 템플릿에서 빼고 Deployment에만 남긴다. 안 그러면 소유자가 바뀔 때 앱이 재시작된다.
- 데모 앱 `tenant-d6d8b759`는 4부 전까지 KoDeploy 화면에서 재배포하지 않는다.
