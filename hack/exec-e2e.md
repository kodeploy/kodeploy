# kodeploy-exec E2E 절차

exec 게이트웨이 지시서 9절을 체크리스트로 옮긴 것이다. 클러스터에서 사람이 진행하고, 결과는 블로그·포트폴리오 재료라 수치를 남긴다.

- **운영 앱 DB로 복원·장애 재현을 하지 않는다.** 테스트 앱 하나를 따로 만들어 쓴다 (MySQL 하나, PostgreSQL 하나면 둘 다 확인된다).
- 판정은 상태 코드가 아니라 출력 본문의 sentinel로 한다. testclient의 점검은 입력 echo에 나오지 않는 값(`echo kd-$((6*7))` → `kd-42`)을 기다린다.
- 9절 8번(core-tenant `pods/exec` 제거)은 1~7번을 확인한 뒤 5단계에서 한다. 이 문서에는 확인 방법만 적는다.

```sh
NS=tenant-xxxxxxxx          # 테스트 앱 ns
APP=exec-test               # 테스트 앱 이름
TC="go run ./internal/testclient/cmd/testclient"   # exec/ 디렉토리에서
```

## 0. 적용 순서

앞 단계가 뒤 단계의 전제다. 순서를 바꾸면 exec Pod가 ContainerCreating(Secret 없음)이나 거부(VAP·AppProject)에 걸린다.

exec는 카나리로 넓힌다. 두 목록을 같이 맞춘다.
- **차트** `platform/exec.yaml`의 `rollout.namespaces`: exec Pod를 그릴 ns.
- **core** configMap의 `EXEC_AGENT_NAMESPACES`: 켠 플래그를 새 경로로 보낼 ns. 쉼표로 구분하고, 전부면 `*`다.

넓힐 때는 차트 먼저(exec Pod가 뜬 뒤), 그다음 core다. 좁힐 때는 반대로 한다. core 목록에만 있고 차트에 없는 ns는 새 경로가 exec를 못 찾아 실패한다.

| 순서 | 할 일 | 명령 | 확인 |
|---|---|---|---|
| 1 | kodeploy `feat/exec-gateway` 머지 → CI | main 머지. `Build Exec Image`(exec), `Build Core Image`(core), `Deploy Web`(web)이 돈다 | exec 워크플로 요약의 `ghcr.io/kodeploy/kodeploy-exec@sha256:...` |
| 2 | core 반영 (플래그 전부 off, `EXEC_AGENT_NAMESPACES` 없음) | `git pull && kubectl apply -f deploy/k8s/core/deployment.yaml` | 새 테넌트 배포 때 `kodeploy-exec-hmac`이 생기는지 |
| 3 | VAP | `kubectl apply --dry-run=server -f deploy/k8s/core/exec-admission-policy.yaml` → `kubectl apply -f ...` | `kubectl get validatingadmissionpolicy kodeploy-exec-pod-shape` |
| 4 | 기존 테넌트 HMAC 키 | `core/.venv/bin/python hack/backfill-exec-hmac.py` (dry-run) → `--apply` | 마지막 줄 `missing=0` |
| 5 | AppProject (kodeploy-charts 브랜치) | `kubectl apply -f argocd/appproject.yaml` | `kubectl get appproject kodeploy-apps -n argocd -o yaml`에 Role·ServiceAccount |
| 6 | vmagent | `kubectl apply -f deploy/k8s/monitoring/vmagent.yaml && kubectl rollout restart deploy/vmagent -n monitoring` | VM에서 `up{job="kodeploy-exec"}` |
| 7 | 차트 값 채우기 (kodeploy-charts PR 브랜치) | `charts/app/platform/exec.yaml`의 `image`, `apiserver.ip`, `envoySelector`, `nodeNets` 확인, **`rollout.namespaces: [$NS]`** (`all: false` 그대로) | `tests/exec_check.py` 통과, 마지막 줄 `repo values render exec in: ['tenant-…']` |
| 8 | 테스트 앱 ns에 서버 측 dry-run | 아래 "머지 전 dry-run" | 오류 없음 |
| 9 | 차트 PR 머지 | GitHub에서 머지 → Argo가 sync. exec는 `$NS`에만 생긴다 | 9절 1번 (`$NS` 하나) |
| 10 | 9절 2~4번 | `$NS`에서 | |
| 11 | core에 카나리 ns | configMap에 `EXEC_AGENT_NAMESPACES: "$NS"` → 9절 5~7번(플래그 하나씩) | |
| 12 | 전 테넌트로 넓히기 | 차트 `rollout.all: true` PR 머지 → 모든 exec Pod Ready 확인(9절 1번 전체) → configMap `EXEC_AGENT_NAMESPACES: "*"` | 9절 1번 RSS 합계 |

1번에서 web은 바로 운영에 배포된다. 웹은 앱마다 `/apps/{id}/exec-features`를 물어서 새 터미널 경로를 쓸지 정한다. 플래그가 꺼져 있거나 앱 ns가 `EXEC_AGENT_NAMESPACES`에 없으면 `terminal: false`이고, 아직 옛 core라 404여도 옛 경로를 쓴다. 그래서 core보다 먼저 나가도 화면은 그대로다.

차트 값 확인 명령:

```sh
# apiserver endpoint (Service ClusterIP가 아니라 실제 주소)
kubectl get endpointslice -n default -l kubernetes.io/service-name=kubernetes \
  -o jsonpath='{.items[0].endpoints[*].addresses[*]}{"\n"}{.items[0].ports[*].port}{"\n"}'
# Envoy 프록시 Pod 라벨 (envoySelector가 맞는지)
kubectl get pods -n envoy-gateway-system --show-labels
# 노드 주소가 nodeNets(10.0.2.0/24) 안인지 — kubelet readiness probe가 여기서 온다
kubectl get nodes -o wide
```

머지 전 dry-run (VAP·PSS restricted·스키마를 실제 apiserver로 거친다. 저장하지 않는다):

```sh
cd kodeploy-charts
git -C ../kodeploy-apps fetch -q && git -C ../kodeploy-apps show origin/main:apps/$NS/values.yaml > /tmp/values.yaml
helm template $NS charts/app -n $NS -f /tmp/values.yaml \
  | kubectl apply --dry-run=server -n $NS -f -
```

## 1~8. 9절 체크리스트

### [ ] 1. 차트 적용 후 테넌트마다 exec Pod Running → 유휴 메모리(RSS)

카나리 단계(0절 9번)에서는 `$NS` 하나, 넓힌 뒤(12번)에는 전 테넌트다. Ready가 안 되면 readiness probe가 Calico 정책에 막힌 것이 아닌지 먼저 본다(`kubectl describe pod`의 `Readiness probe failed ... timeout`). 그렇다면 probe 출발지가 `nodeNets` 밖인 것이다. Calico IPIP 터널 주소일 수 있으니 `kubectl get nodes -o jsonpath='{.items[*].metadata.annotations.projectcalico\.org/IPv4IPIPTunnelAddr}'`로 보고 `/32`로 더한다.

```sh
kubectl get pods -A -l kodeploy.com/component=exec -o wide
kubectl get pods -A -l kodeploy.com/component=exec -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.status.containerStatuses[0].ready}{"\n"}{end}'
```

RSS는 VictoriaMetrics(cAdvisor)에서 잰다. 세션이 없는 상태로 10분 뒤 값을 남긴다.

```promql
container_memory_rss{namespace=~"(tenant|app)-.*", container="exec"}
container_memory_working_set_bytes{namespace=~"(tenant|app)-.*", container="exec"}
```

남길 것: ns별 RSS, 합계, 워커 할당 가능 메모리 대비 % (11절 scale-to-zero 판단 재료).

### [ ] 2. VAP: kodeploy-exec SA를 쓴 다른 Pod가 거부되는지

```sh
# restricted(PSS)는 맞춘 Pod라서, 거부 사유는 VAP 메시지여야 한다
# exec SA를 붙인 다른 이미지 Pod
kubectl run vap-test -n $NS --image=busybox:1.36 --restart=Never --dry-run=server \
  --overrides='{"spec":{"serviceAccountName":"kodeploy-exec","securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"vap-test","image":"busybox:1.36","securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
# exec 라벨만 흉내 낸 Pod (다른 SA)
kubectl run vap-test2 -n $NS --image=busybox:1.36 --restart=Never --dry-run=server -l kodeploy.com/component=exec \
  --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"vap-test2","image":"busybox:1.36","securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
# 떠 있는 exec Pod에 디버그 컨테이너 붙이기 (pods/ephemeralcontainers)
kubectl debug -n $NS "$(kubectl get pod -n $NS -l kodeploy.com/component=exec -o name)" \
  --image=busybox:1.36 --profile=restricted -- true
```

남길 것: 세 거부 메시지 (`kodeploy-exec SA는 ...`, `kodeploy.com/component=exec 라벨은 ...`, `ephemeral container ...`).

### [ ] 3. NetworkPolicy: 같은 ns 앱 Pod에서 exec 8080·8081이 막히는지

같은 ns에 restricted 조건을 맞춘 임시 Pod를 띄워 접속해 본다. 열려 있으면 HTTP 응답(405·404)이 오고, 막혀 있으면 시간 초과다.

```sh
kubectl run np-test -n $NS --rm -i --restart=Never --image=busybox:1.36 \
  --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":65534,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"np-test","image":"busybox:1.36","command":["sh","-c","for p in 8080 8081 9090; do echo \"== $p\"; wget -T 3 -qO- http://kodeploy-exec:$p/ 2>&1 | head -1; done"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
```

통과: 세 포트 모두 `download timed out`. (`server returned error`가 나오면 열려 있는 것이다.)

남길 것: 출력 전문.

### [ ] 4. testclient로 공개 문: 재사용·만료 4401, 다른 ns 4403, 한글·바이너리 출력, 창 크기

```sh
# 내부 문 키 (끝나면 지운다) + 내부 문·공개 문 port-forward (Pod 네트워크 정책을 거치지 않는다)
kubectl get secret -n $NS kodeploy-exec-hmac -o jsonpath='{.data.hmac}' | base64 -d > /tmp/exec-hmac.key && chmod 600 /tmp/exec-hmac.key
kubectl port-forward -n $NS deploy/kodeploy-exec 18081:8081 18080:8080 &

cd exec
$TC check-public -internal http://127.0.0.1:18081 -key-file /tmp/exec-hmac.key -app $APP \
  -url wss://api.kodeploy.com/exec/$NS/v1/sessions \
  -other-ns-url ws://127.0.0.1:18080/exec/tenant-ffffffff/v1/sessions \
  -expiry

rm /tmp/exec-hmac.key; kill %1
```

`-url`은 실제 경로(Cloudflare → Envoy → 테넌트 HTTPRoute)를 탄다. 다른 ns 4403은 같은 Pod에 다른 ns 경로로 붙어야 나온다(Envoy를 거치면 그 ns의 Pod로 가서 티켓이 없으니 4401).

통과: `PASS` 10줄(`-other-ns-url`·`-expiry`를 뺐으면 8줄), `all public-door checks passed`.

남길 것: 출력 전문.

### [ ] 5. 플래그를 하나씩 켜고 기존 경로와 비교

플래그는 `deploy/k8s/configMap/kodeploy-core-env.yaml`에 넣고 core를 재시작한다. 하나 켜고 확인, 다음으로. `EXEC_AGENT_NAMESPACES: "<테스트 앱 ns>"`가 먼저 들어 있어야 한다. 없으면 플래그를 켜도 옛 경로로 간다.

```sh
# 예: EXEC_AGENT_NAMESPACES: "tenant-xxxxxxxx", EXEC_AGENT_QUERY: "true" 를 configMap에 추가한 뒤
kubectl apply -f deploy/k8s/configMap/kodeploy-core-env.yaml
kubectl rollout restart deploy/kodeploy-core -n default
```

| 플래그 | 비교 방법 | 통과 |
|---|---|---|
| `EXEC_AGENT_QUERY` | 켜기 전·후에 같은 SQL 5개(SELECT 한글, 다중 문장, 오류 문장, INSERT, 500행 넘는 SELECT)를 DB 콘솔에서 돌려 결과 JSON을 저장하고 `duration_ms`를 뺀 나머지를 diff | diff 없음 |
| `EXEC_AGENT_BROWSE` | 같은 표·조건·정렬로 탐색 결과를 저장해 diff | diff 없음 |
| `EXEC_AGENT_DUMP` | 켜기 전·후 덤프를 받아 날짜 줄만 빼고 비교 (아래) | 내용 같음 |
| `EXEC_AGENT_RESTORE` | 덤프를 복원하고 결과의 표별 행 수를 `SELECT COUNT(*)`와 비교 | 행 수 같음 |
| `EXEC_AGENT_TERMINAL` | 7번 | |

```sh
# 덤프 비교 (mysqldump 끝의 "-- Dump completed on ..." 줄, pg_dump는 그대로)
diff <(zcat old.sql.gz | grep -v '^-- Dump completed') <(zcat new.sql.gz | grep -v '^-- Dump completed')
```

core 로그와 exec 감사 기록을 request_id로 이어 보는 법:

```sh
kubectl logs -n default deploy/kodeploy-core | grep '"event":"exec_' | tail -3
kubectl logs -n $NS deploy/kodeploy-exec | grep <request_id>
```

남길 것: 플래그별 diff 결과, 덤프 비교 결과, 복원 행 수 표.

### [ ] 6. 장애 재현 (테스트 앱 DB로만)

| 장애 | 명령 | 통과 |
|---|---|---|
| 업로드 중 연결 끊기 | `$TC restore -internal ... -key-file ... -file big.sql.gz -abort-after 100000` | 감사 기록 close 1006, DB 그대로(행 수), `/tmp/kodeploy-restore` 비어 있음 |
| 기대 해시 틀리게 | `$TC restore ... -file d.sql -sha 0000…(64자)` | `failed_step: upload`, `db_changed: false` |
| 동시 세션 초과 | `$TC hold ... -url wss://api.kodeploy.com/exec/$NS/v1/sessions -app $APP -n 4` | 4번째가 `closed 4429` |
| 작업 디렉토리 채우기 | 하지 않는 것을 권한다 (아래) | |

작업 디렉토리는 DB 컨테이너의 `/tmp`, 곧 노드 루트 디스크다. 업로드 상한이 200 MiB라서 precheck(크기 × 2)를 실패시키려면 노드 디스크를 400 MiB 아래로 채워야 하고, 그러면 같은 노드의 모든 Pod가 위험하다. 이 경로는 단위 테스트(`exec/internal/restore` `TestPrecheckFailures`)로 갈음하거나, 로컬 k3s 실험 클러스터에서만 재현한다.

확인할 곳:

```sh
kubectl logs -n $NS deploy/kodeploy-exec | tail -5                       # 감사 기록 (close_code, exit_code)
kubectl exec -n $NS sts/mysql -- ls -la /tmp/kodeploy-restore             # cleanup 확인 (restore.dump 없음)
```

`/tmp/kodeploy-restore`에 `backup-*.sql.gz`가 남아 있는 건 결과를 모르는 복원(load 중 연결 끊김, rollback 실패)뿐이다. 그때는 아래 "복원 백업이 남았을 때"를 따른다.

남길 것: 장애별 before/after (행 수, 결과 JSON, close 코드).

### [ ] 7. 웹 터미널 3종

`EXEC_AGENT_TERMINAL: "true"`로 core를 재시작한 뒤, 테스트 앱 워크스페이스의 터미널에서:

| 대상 | 입력 | 통과 |
|---|---|---|
| 앱 서버 | `echo kd-$((6*7))-한글` | `kd-42-한글`, 창 크기를 바꾼 뒤 `stty size`가 화면과 맞음 |
| DB | MySQL `SELECT '한글';` / PostgreSQL `SELECT '한글';` | 한글이 깨지지 않음 |
| Redis | `PING` | `PONG` (`NOAUTH`가 아님 — 차트 `redis:7-alpine`에서 REDISCLI_AUTH가 먹힌다) |

브라우저 개발자 도구 Network → WS에서 요청 URL이 `wss://api.kodeploy.com/exec/$NS/v1/sessions`, 응답 `Sec-WebSocket-Protocol: kodeploy.exec.v1`인지도 본다.

남길 것: 화면 캡처 3장.

### [ ] 8. (5단계 뒤) core-tenant에서 pods/exec 제거 후 실제 exec 요청이 403인지

`can-i`가 아니라 실제 요청으로 본다 (connecter 서브리소스는 `can-i` 답과 실제 인가가 다를 수 있다 — rbac.yaml 주석).

```sh
kubectl exec -n default deploy/kodeploy-core -- python -c '
import asyncio
from kubernetes_asyncio import client, config
from kubernetes_asyncio.stream import WsApiClient
async def main():
    config.load_incluster_config()
    async with WsApiClient() as api:
        try:
            await client.CoreV1Api(api).connect_get_namespaced_pod_exec(
                name="<테스트 앱 Pod>", namespace="'$NS'", container="app", command=["true"],
                stdout=True, stderr=True, stdin=False, tty=False)
            print("ALLOWED")
        except Exception as e:
            print("DENIED", getattr(e, "status", e))
asyncio.run(main())'
```

통과: `DENIED 403`.

## 복원 백업이 남았을 때 (수동 복구)

MySQL 복원이 load 도중 끊기거나(연결 끊김·시간 초과) rollback이 실패하면 exec는 DB 상태를 모른다. 그때는 복원 전 백업을 지우지 않고 남긴다.

- 위치는 복원 결과 JSON의 `backup`(`pod`, `container`, `path`)에 있다.
- core 로그에는 `exec_session` 줄의 `backup_pod`·`backup_path`로, exec 로그(stderr)에는 `restore outcome unknown; backup kept`로 남는다.
- 사용자에게는 복원 실패 메시지에 같은 경로가 나간다.
- PostgreSQL은 백업 단계가 없다. 연결이 끊겨도 적재는 한 트랜잭션이라 끝까지 가면 커밋되고, 아니면 롤백된다. 3번 확인만 하면 된다.

**백업은 DB 컨테이너의 `/tmp`(컨테이너 쓰기 레이어)에 있다. 컨테이너가 재시작되면 사라지므로 1번부터 한다.**

```sh
POD=mysql-0                                                     # 결과의 backup.pod
BK=/tmp/kodeploy-restore/backup-20261113T010203Z-a1b2c3.sql.gz  # 결과의 backup.path

# 1. 백업을 클러스터 밖으로 꺼낸다 (mysql 이미지에 tar가 없을 수 있어 kubectl cp 대신 cat)
kubectl exec -n $NS $POD -c mysql -- cat "$BK" > ./restore-backup.sql.gz
gzip -t ./restore-backup.sql.gz && ls -l ./restore-backup.sql.gz

# 2. 끊긴 적재가 아직 도는지 본다. 컨테이너 안 적재는 연결이 끊겨도 계속 돈다
kubectl exec -n $NS $POD -c mysql -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root -e "SHOW FULL PROCESSLIST"'
#    Command=Query인 줄이 남아 있으면 끝날 때까지 기다린다 (중간에 되돌리면 둘이 섞인다)

# 3. 지금 DB를 본다: 표 목록과 행 수가 올린 덤프대로인지
kubectl exec -n $NS $POD -c mysql -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root "$MYSQL_DATABASE" -e "SHOW TABLES"'
```

4. 판단한다.
   - **덤프대로 다 들어갔다** (적재가 끝까지 갔다): 되돌리지 않는다. 5번으로 간다.
   - **아니다**: 백업으로 되돌린다. exec rollback과 같은 명령이다. DB를 비우고 백업을 넣는다.

```sh
kubectl exec -n $NS $POD -c mysql -- bash -c 'set -o pipefail; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; db="$MYSQL_DATABASE";
  mysql -u root -e "DROP DATABASE IF EXISTS \`$db\`; CREATE DATABASE \`$db\`" && gunzip -c "$1" | mysql -u root "$db"' sh "$BK"
# 컨테이너 안 파일이 이미 없으면 1번에서 꺼낸 사본으로:
#   gunzip -c ./restore-backup.sql.gz | kubectl exec -i -n $NS $POD -c mysql -- sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -u root "$MYSQL_DATABASE"'
#   (이 경우 DROP/CREATE DATABASE를 먼저 따로 돌린다)
```

5. 앱을 다시 띄워 새 데이터로 연결하게 한다. 백업 파일은 확인이 끝나면 지운다. 꺼낸 사본(`./restore-backup.sql.gz`)도 개인 데이터가 든 파일이니 지운다.

```sh
kubectl rollout restart deploy/$APP -n $NS
kubectl exec -n $NS $POD -c mysql -- rm -f "$BK"
```

## 되돌리기

| 상황 | 되돌리는 법 |
|---|---|
| 새 경로가 이상하다 | 해당 `EXEC_AGENT_*`를 configMap에서 지우고 core 재시작 → 즉시 옛 경로. 특정 ns만이면 `EXEC_AGENT_NAMESPACES`에서 그 ns만 뺀다 |
| exec Pod가 문제 | core `EXEC_AGENT_NAMESPACES`에서 먼저 빼고(재시작), kodeploy-charts에서 `rollout`을 좁히거나(그 ns만) `image`를 비우는 PR 머지 → Argo가 exec 리소스를 지운다 (prune) |
| VAP가 무언가를 막는다 | `kubectl delete -f deploy/k8s/core/exec-admission-policy.yaml` |
| 사고 (키 유출 등) | 지시서 14절 사고 대응: Argo auto-sync 일시정지 → exec Deployment 0 → 테넌트별 HMAC 키 교체 → 재개 |
