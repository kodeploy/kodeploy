# Go 빌더 E2E 절차와 운영 메모

지시서: [go-builder-plan.md](go-builder-plan.md) (3부-2). 이 문서는 사람이 클러스터에서 할 일(Secret, 적용, E2E)과, 원본·지시서와 다르게 만든 부분, 4부 할 일을 모은다.

로컬(클러스터 접속 없음)에서 확인한 것: `go vet`, `go test -race ./...` 전부 통과, arm64 정적 바이너리 빌드, 매니페스트 strict 디코드, `hack/sign-request.sh` ↔ `cmd/mockcore` 서명 왕복. **이미지 빌드(Docker)와 클러스터 동작은 확인하지 못했다.**

---

## 1. 사람이 만들 Secret: `kodeploy-builder-secrets` (default 네임스페이스)

| 키 | 값 | 만드는 법 |
|---|---|---|
| `HMAC_SECRET` | core와 공유하는 서명 키. 4부에서 core에도 같은 값을 넣는다 | `openssl rand -hex 32` |
| `GITHUB_TOKEN` | `kodeploy/kodeploy-apps` 쓰기 | fine-grained PAT, 저장소는 kodeploy-apps 하나만, 권한 Contents: Read and write |
| `GHCR_TOKEN` | 빌드 이미지 digest 확인(HEAD) | classic PAT, `read:packages`만. GHCR은 fine-grained PAT를 받지 않는다. `GHCR_USER`(yuntyu01) 계정 것 |

값이 셸 기록에 남지 않게 파일로 만들고 지운다 (managed 노드):

```sh
umask 077
cat > /tmp/builder.env        # 아래 세 줄을 붙여넣고 Ctrl-D
# HMAC_SECRET=...
# GITHUB_TOKEN=...
# GHCR_TOKEN=...
kubectl -n default create secret generic kodeploy-builder-secrets --from-env-file=/tmp/builder.env
shred -u /tmp/builder.env
kubectl -n default get secret kodeploy-builder-secrets -o jsonpath='{.data}' | jq 'keys'   # 키 이름만 확인
```

## 2. 이미지와 적용

- 이미지는 `build-builder.yml`이 `main`의 `builder/**` 변경에서 만든다 (`ghcr.io/kodeploy/kodeploy-builder:sha-<7>`, `deploy/k8s/builder/deployment.yaml`에 digest 고정 커밋). 새 워크플로라 main에 들어가기 전에는 수동 실행도 안 된다. core와 연결되지 않은 상태라 main에 합쳐도 운영에는 영향이 없다.
- **pull 권한 확인**: 레포가 `kodeploy` 조직으로 옮겨져 이미지 소유자가 `kodeploy`가 된다. default 네임스페이스의 `ghcr-auth`(yuntyu01 계정)가 이 조직 패키지를 받을 수 있어야 한다. GitHub 패키지 설정에서 권한을 주거나 패키지를 public으로.

```sh
git pull
kubectl apply -f deploy/k8s/builder/
kubectl -n default rollout status deployment/kodeploy-builder
kubectl -n default logs deploy/kodeploy-builder | head      # "listening", early_trigger=true
```

권한 확인 (있어야 하는 것 yes, 없어야 하는 것 no):

```sh
SA=system:serviceaccount:default:kodeploy-builder
for a in "create jobs -n kodeploy-build" "watch jobs -n kodeploy-build" "patch jobs -n kodeploy-build" \
         "get pods/log -n kodeploy-build" "patch applications.argoproj.io -n argocd"; do
  echo "$a: $(kubectl auth can-i $a --as=$SA)"; done
for a in "get secrets -n tenant-d6d8b759" "create deployments -n default" "get secrets -n kodeploy-build" \
         "create pods/exec -n kodeploy-build" "list namespaces"; do
  echo "$a: $(kubectl auth can-i $a --as=$SA)"; done
```

NetworkPolicy: 적용 뒤 Pod이 Ready로 남는지 본다 (kubelet 프로브가 막히지 않는지).

## 3. E2E (mockcore)

**주의: 이 절차는 데모 앱 `tenant-d6d8b759`를 실제로 다시 배포한다** (새 이미지로 Pod 재시작, core DB는 모른다). 마지막에 `set-image` 예시로 원래 이미지로 되돌린다.

준비 (WSL):

```sh
cd kodeploy/builder
GOOS=linux GOARCH=arm64 CGO_ENABLED=0 go build -o /tmp/mockcore-arm64 ./cmd/mockcore
scp /tmp/mockcore-arm64 managed:~/mockcore
scp -r hack managed:~/builder-hack
```

managed 노드, 터미널 1 (mockcore):

```sh
export HMAC_SECRET=$(kubectl -n default get secret kodeploy-builder-secrets -o jsonpath='{.data.HMAC_SECRET}' | base64 -d)
~/mockcore -addr 0.0.0.0:9090
```

터미널 2 (콜백을 mockcore로 돌리고 빌더에 포트포워드):

```sh
MANAGED_IP=$(hostname -I | awk '{print $1}')
kubectl -n default set env deployment/kodeploy-builder CORE_URL=http://$MANAGED_IP:9090
kubectl -n default rollout status deployment/kodeploy-builder
kubectl -n default port-forward svc/kodeploy-builder 8080:8080
```

터미널 3 (요청):

```sh
export HMAC_SECRET=$(kubectl -n default get secret kodeploy-builder-secrets -o jsonpath='{.data.HMAC_SECRET}' | base64 -d)
cd ~/builder-hack
./sign-request.sh POST /internal/deploys examples/build.json        # HTTP 202
```

mockcore에 이 순서로 찍혀야 한다: `| === clone (init) ===` … `| === buildkit (main) ===` … `COMMITTED`(`triggered_early:true`, `push_done_at`) → `DEPLOYED`(`synced_at`, `healthy_at`) → (export 로그) → `FINISHED`(`job_succeeded:true`, `export_failed:false`).

같이 볼 것:

```sh
kubectl -n kodeploy-build get jobs -l kodeploy.io/managed=true -L build-id
kubectl -n kodeploy-build get job build-d6d8b759-e2e00001 -o json | jq '.metadata.annotations | del(."kodeploy.io/request")'
kubectl -n argocd get application tenant-d6d8b759 \
  -o jsonpath='{.status.sync.revisions}{"\n"}{.status.sync.status} {.status.health.status}{"\n"}'
kubectl -n tenant-d6d8b759 get deploy -o jsonpath='{..image}{"\n"}'
# kodeploy-apps: 커밋 메시지 "deploy(tenant-d6d8b759): build e2e00001 [e2e00001] by d6d8b759", 바뀐 줄은 image 하나
```

`build_id`는 요청마다 새로 (`e2e00002`, …, hex 8자리). 같은 id를 20분 안에 다시 쓰면 남아 있는 옛 Job을 이어받는다.

추가 시나리오:

| 확인 | 방법 | 기대 |
|---|---|---|
| 409 | 빌드 중에 같은 요청 다시 | `409 {"build_id":"e2e00001"}` |
| 400 | `examples/build.json`의 `ref`를 `-x`로 | `400 build.ref must not start with - or /` |
| 401 | `HMAC_SECRET=wrong ./sign-request.sh …` | `401` |
| 취소 | 새 id로 빌드 → `./sign-request.sh DELETE /internal/deploys/<id>` | Job 삭제, `CANCELLED` |
| 재개 | 새 id로 빌드 중 `kubectl -n default rollout restart deployment/kodeploy-builder` | Job 유지, 새 Pod 로그에 `resuming build`, 이어서 `COMMITTED`…, seq가 +100 뛴다 |
| 재시도 | mockcore를 `-fail-first 3`으로 | 같은 seq로 다시 와서 200 |
| 되돌리기 | `./sign-request.sh POST /internal/deploys examples/set-image.json` | 원래 이미지(`99483bd9`)로 `COMMITTED` → `DEPLOYED` |

끝나면:

```sh
kubectl -n default set env deployment/kodeploy-builder CORE_URL=http://kodeploy-core
# mockcore 종료 (Ctrl-C), 포트포워드 종료
```

## 4. 원본·지시서와 다르게 처리한 부분

원본(core 파이썬)을 따른 것:

1. **Go 1.25** (지시서 1.23). client-go v0.35·go-containerregistry v0.22가 `go 1.25.0`을 요구한다. Dockerfile도 `golang:1.25`.
2. **Job 이름 `build-<userId 앞 8자>-<build_id>`** (지시서 `build-<build_id>`). 원본 `_build_job_name`. 그래서 `kind=build`에는 `values.userId`가 필수다.
3. **`build_id`는 hex 8자리** (원본 `uuid4().hex[:8]`). 지시서 예시 `b-3f9a2c1d`는 받지 않는다.
4. **`EARLY_TRIGGER` 플래그 유지**, 기본 `true`(라이브와 같게). 끄면 마커를 봐도 Job 종료 후 배포한다.
5. **로그 머리줄** `=== clone (init) ===`, `=== buildkit (main) ===`와 사이 빈 줄 (원본 `_combined_job_logs`).
6. **Job 대기 상한** activeDeadlineSeconds + 30초, deadline 기본값 `BUILD_TIMEOUT_SECONDS(600) + 300` (원본 규칙, 두 env 모두 읽는다).
7. **`cache_ref`는 `<image_repo>:buildcache`만** 받는다 (원본 `_cache_ref`).

지시서에 없던 것을 더한 것:

8. **입력 검증**: repo는 `https://github.com/<owner>/<repo>(.git)`만, ref는 허용 문자 `[A-Za-z0-9._/-]` + `-`·`/`로 시작·`..`·`//`·`.lock` 거부, dockerfile_dir/name은 `..`·절대경로 거부. values 내용은 차트 `values.schema.json` 규칙으로 먼저 400. 모르는 키는 요청·values 모두 거부.
9. **계약 타입 패키지 `internal/contract`** (api ↔ run import 순환 방지).
10. **마커를 BuildKit 진행 줄(`^#<n> `)로 고정**. 유저 RUN 출력(`#<n> <경과초> …`)이 걸러진다. bench 실제 로그 11개에서 원본과 같은 줄에서 잡힌다.
11. **로그 읽기는 `bufio.Reader` + 줄당 64KiB 상한** (지시서 `bufio.Scanner`). Scanner는 긴 줄에서 멈추고, 재연결하면 같은 줄에서 다시 멈춘다. 재연결은 마지막 타임스탬프부터, 겹치는 줄은 버린다.
12. **Argo 대기의 revision 판정**: 값 source revision이 우리 커밋이거나 **우리 커밋을 포함하면**(GitHub compare API) 통과. 다른 앱 커밋이 뒤따르면 Argo는 더 새 커밋을 보고하기 때문이다. 바뀐 게 없어 커밋을 건너뛰면 브랜치 끝 sha로 기다린다. 이번 대기 전에 시작된 옛 operation 실패는 무시, `Degraded`면 `failed(stage=health)`.
13. **새 앱은 Application이 생길 때까지 refresh를 다시 시도**한다 (ApplicationSet git 제너레이터 주기, 최대 3분 정도). 전체는 `ARGO_WAIT_TIMEOUT` 안.
14. **`config`인데 values 파일이 없으면 `failed(commit)`** (설정만으로 앱을 만들지 않는다).
15. **409 규칙 확장**: 같은 namespace+slot이면 kind와 상관없이 409, `delete`는 그 namespace의 모든 요청과 409. reap 중(배포 판정 뒤 export만 도는)인 빌드는 막지 않는다. `MAX_ACTIVE_BUILDS`는 reap 중 Job도 센다. `Retry-After: 15`.
16. **종료 이벤트**: `finished`는 `deployed` 뒤에만 보낸다. `failed`·`cancelled`는 그 자체로 끝. reap 중 취소는 `cancelled` 대신 `finished{export_failed:true}`. Job이 밖에서 지워지면 `cancelled`.
17. **재개**: seq는 `acked-seq + 101`부터 (log 이벤트의 acked-seq는 2초에 한 번만 기록하므로, core가 받았지만 기록 못 한 seq를 다시 쓰지 않게 여유를 둔다. core는 seq 간격을 허용해야 한다). 어노테이션 `kodeploy.io/acked-log-lines`를 더해 이미 보낸 로그 줄은 다시 보내지 않는다 (최대 2초 분량은 중복될 수 있다). 커밋 뒤에 끊겼으면 `committed`를(동기화까지 끝났으면 `deployed`도) 다시 보낸다.
18. **Deployment `strategy: Recreate`**, `terminationGracePeriodSeconds: 30`. 두 빌더가 겹치면 같은 Job을 둘 다 재개한다.
19. **CI**: 이미지 전에 `go vet`, `go test -race`. 고정 커밋·`latest` 태그는 main에서만 (다른 브랜치에서 돌면 `HEAD:main` push가 그 브랜치를 main에 올린다. core 워크플로에도 같은 위험이 있다).
20. **HTTP**: 본문 1MiB 초과는 413, 디스패처 내부 오류(종료 중 포함)는 500 `internal error` (내부 메시지는 밖으로 내지 않는다).
21. **직접 의존성**: 지시서 목록 외에 `go.yaml.in/yaml/v3`(values 키 순서 유지, 기존 파일과 글자 단위로 같게), `k8s.io/utils`(포인터 헬퍼), `go.uber.org/goleak`(테스트). 앞의 둘은 client-go가 이미 끌어오는 모듈이다.

## 5. 4부 할 일 (빌더 계약에서 core가 지켜야 할 것)

- **core에도 같은 입력 검증을 넣는다** (repo 형식, ref 허용 문자·`-` 시작·`..` 거부, dockerfile 경로 `..`·절대경로 거부). 빌더 검증은 마지막 방어선이고, core에서 먼저 막아야 유저가 화면에서 바로 이유를 본다. v1 경로(Jinja 템플릿)로 남는 앱은 지금도 YAML 인젝션에 노출돼 있다.
- `HMAC_SECRET`을 core에 같은 값으로 넣고, 콜백 `POST /internal/builds/{build_id}/events`를 서명 검증 후 받는다.
- `(build_id, seq)` 중복은 무시하되 **seq 간격은 허용**한다 (재개 시 +100). 재개 때 다시 오는 `committed`·`deployed`는 같은 내용이면 무시해도 되게 멱등으로 처리한다.
- 409면 본문의 `build_id`를 `DELETE`한 뒤 다시 보내고, 429면 `Retry-After` 뒤, 5xx면 백오프로 다시 보낸다.
- `kind=build` 요청에는 `values.userId`를 넣는다 (Job 이름·라벨).
