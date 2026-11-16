# KoDeploy

GitHub 저장소 URL을 받아 컨테이너로 빌드하고 `{앱이름}.kodeploy.com` 서브도메인으로 서비스하는 미니 PaaS. self-service 배포, 런타임별 golden path, K8s 추상화, 멀티테넌시를 갖춘 작은 IDP(Internal Developer Platform)다.

Heroku 무료 티어가 사라진 뒤 학생이 캡스톤이나 해커톤 결과물을 올릴 곳이 마땅치 않다. Railway나 Render는 슬립이 잦거나 금방 과금으로 넘어간다. 학교나 동아리 규모의 신뢰 기반 사용자라면 클러스터 하나를 공유해서 이 문제를 풀 수 있겠다 싶어 만들었다. 지금은 지인 초대 기반으로 [app.kodeploy.com](https://app.kodeploy.com)에서 운영 중이다.

OCI Always Free ARM 한도(4 OCPU / 24GB) 안에서 노드 3대로 운영하고, 월 운영비는 0원이다. 노드 위에는 kubeadm으로 직접 구성한 Kubernetes 클러스터(Calico, Envoy Gateway)가 돌아간다. 사용자는 kubectl을 쓰지 않는다. 웹에서 요청하면 빌드는 Go로 만든 빌더가, 클러스터 적용은 Argo CD가 맡는다. 앱마다 네임스페이스를 따로 두고 PSS restricted와 Calico 정책으로 격리한다. 단독 운영하는 프로젝트다. 백엔드(core)도 이 클러스터 위에서 돌아가고, 프론트는 Cloudflare에서 정적으로 서빙한다.

설계 결정과 구축 과정은 [기술 블로그](https://yuntyu01.github.io/Archive/blog/)에 기록하고 있다.

![배포 폼 - 백엔드와 프론트엔드 슬롯 구성](docs/images/deployform.png)
![모니터링과 빌드 로그 패널](docs/images/monitoring_log.png)

## 배포 흐름

1. 웹 폼에서 저장소 URL과 런타임(Python, Java, PHP, JavaScript, Go, 정적)을 골라 제출한다.
2. core가 빌더에 빌드를 요청하고, 빌더는 `kodeploy-build` 네임스페이스에 rootless BuildKit Job을 만든다. 저장소에 Dockerfile이 있으면 그대로 쓰고, 없으면 nixpacks가 Dockerfile을 만든다. 빌드 캐시는 GHCR에 두고, 빌드 로그는 진행 중에 1초 단위로 웹에 그대로 보인다.
3. 빌더가 이미지를 GHCR에 올린 뒤 이미지 digest를 GitOps 저장소(`kodeploy-apps`)의 앱별 `values.yaml`에 커밋한다.
4. Argo CD가 공통 차트(`kodeploy-charts`)와 그 값으로 앱 네임스페이스에 적용한다(server-side apply, prune, selfHeal). 빌더는 동기화와 앱 Pod 상태를 지켜보다가 앱이 뜨지 않으면 로그 끝부분을 붙여 실패로 알린다.
5. `*.kodeploy.com` 와일드카드 DNS가 이미 로드밸런서를 가리키고 있어 별도 DNS 작업 없이 바로 접속된다. HTTPS는 Cloudflare 프록시와 Envoy Gateway의 TLS 종료로 적용된다.

private repo는 GitHub App 설치로 지원한다. 빌드 시점에 1시간짜리 installation token을 발급해 clone하고, 토큰은 해당 유저의 repo에만 스코프된다.

비밀번호 같은 Secret 값은 Git에 넣지 않고 core가 클러스터에 직접 쓴다.

## 앱 구성

한 사용자가 여러 앱을 만들 수 있고, 앱 개수 상한은 등급으로 정한다. 앱마다 네임스페이스가 따로 생긴다. 앱은 다른 사용자와 공유할 수 있고, 권한은 보기(viewer)와 편집(editor) 두 가지다.

앱은 두 슬롯으로 구성된다. 서버 슬롯은 Python, Java, PHP, JavaScript, Go 런타임으로 돌고 `{앱이름}-api.kodeploy.com`으로 접속한다. 정적 슬롯은 빌드 산출물을 nginx로 서빙한다. 정적 슬롯을 켜면 `{앱이름}.kodeploy.com`이 정적 사이트로 넘어가고 서버 주소는 그대로 유지된다.

서버 슬롯에는 MySQL, PostgreSQL, Redis, 영속 스토리지를 토글로 붙일 수 있다. 토글을 꺼도 PVC와 데이터는 보존되고, 다시 켜면 그대로 복원된다. 오브젝트 스토리지는 Cloudflare R2에 앱마다 버킷을 만들고 그 버킷에만 쓸 수 있는 토큰을 발급하는 방식이다.

그 밖의 기능은 다음과 같다.

- 커스텀 도메인 연결 (Cloudflare for SaaS custom hostname)
- 이전 빌드로 롤백
- 빌드 실패 원인을 LLM으로 분석하는 AI 진단
- 웹 터미널과 DB 콘솔. 앱 네임스페이스마다 둔 exec 게이트웨이를 거쳐 Pod에 접속하고, core는 Pod exec 권한을 갖지 않는다.
- DB 덤프와 복원, 메트릭 대시보드(VictoriaMetrics), R2 스토리지 브라우저

## 구조

```
terraform/   OCI 인프라(VCN, 노드, NLB)와 Cloudflare(DNS, 터널, 보안)
ansible/     클러스터 부트스트랩. containerd부터 Envoy Gateway까지 순서대로
deploy/k8s/  플랫폼 매니페스트 (core, 빌더, Argo CD, Gateway, 모니터링, Calico)
core/        백엔드. FastAPI. 앱, 사용자, 배포 요청 관리
builder/     빌더. Go. BuildKit Job으로 빌드하고 GitOps 저장소에 값을 커밋
exec/        exec 게이트웨이. Go. 웹 터미널과 DB 콘솔의 Pod 접속
web/         프론트. React + Vite. Cloudflare에 정적 배포
docs/        기술 문서
```

앱 차트와 앱별 값은 별도 저장소(`kodeploy-charts`, `kodeploy-apps`)에 있고, 둘 다 비공개다.

## 로컬 실행

호스티드 서비스라 로컬 실행은 개발용이다. 백엔드와 DB만 docker-compose로 뜬다.

```sh
docker compose up        # MySQL + API (localhost:8000)
cd web && npm run dev    # 프론트 (localhost:5173)
```

GitHub 로그인이 필요하면 `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` 환경변수를 넣어야 한다. 빌드와 배포는 K8s 클러스터, 빌더, Argo CD, GHCR 자격증명이 있어야 동작한다.

## 다음에 할 것

- gVisor로 사용자 앱 격리 강화. 지금은 사용자 컨테이너가 노드 커널을 그대로 공유한다.
- Vault로 앱 Secret 이전. 지금은 core가 K8s Secret을 직접 쓰기 때문에 앱 네임스페이스의 Secret 권한이 core에 남아 있다.
- HPA로 앱 오토스케일링. 지금은 앱마다 Pod 수가 고정이다.
- 운영 중인 앱에서 버튼 하나로 테스트용 stage, dev 환경을 띄우는 기능
- 사용자 알림 (사이트, 이메일)
