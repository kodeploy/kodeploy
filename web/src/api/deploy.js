// API base는 빌드 시 VITE_API_BASE로 주입 (없으면 같은 origin 사용)
const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",                       // cookie session 첨부
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    const err = new Error(`${res.status} ${detail}`);
    err.status = res.status;                      // UI에서 401 분기 가능
    throw err;
  }
  return res.json();
}

// 서버 슬롯 런타임 (백엔드 schemas.ServerRuntime의 "none" 제외분과 sync).
// 정적 사이트는 런타임이 아니라 별도 슬롯(use_static 토글).
export const RUNTIMES = ["python", "java", "php", "javascript"];
// 백엔드 schemas.BuildMode와 sync ("dockerfile"=유저 Dockerfile / "auto"=nixpacks 자동)
export const BUILD_MODES = ["dockerfile", "auto"];
// 백엔드 schemas.DbType과 sync — 한 앱에 한 DB만
export const DB_TYPES = ["none", "mysql", "postgres"];

export function createDeploy({
  repoUrl,
  branch = "main",
  port = 80,
  runtime,
  name,
  dbType = "none",
  useRedis = false,
  storage = "none",                             // 영속저장소 — "none" | "local" | "object"
  volumeMountPath = "",                         // local 전용 — PVC 마운트 경로
  volumeStorageClass = "local-path",
  volumeSize = "5Gi",
  buildMode = "dockerfile",
  dockerfilePath = "Dockerfile",
  projectPath = "",
  useStatic = false,
  staticRepoUrl = "",
  staticBranch = "",
  staticProjectPath = "",
  buildCmd = "",
  outputDir = "",
  staticEnv = {},
  env = {},
  initDumpToken = null,
}) {
  return request("/deploy", {
    method: "POST",
    body: JSON.stringify({
      repo_url: repoUrl,
      branch,
      port,
      runtime,                                    // "python" | "java" | "none"(서버 없음 — 정적 단독)
      name: name?.trim() || null,
      db_type: dbType,
      use_redis: useRedis,
      // 영속저장소(런타임 무관) — 단일 셀렉터. object=R2 / local=PVC / none=ephemeral.
      storage,
      volume_mount_path: volumeMountPath || "",   // local 전용 — 마운트 절대경로 (예: /var/www/html/data)
      volume_storage_class: volumeStorageClass || "local-path",
      volume_size: volumeSize || "5Gi",
      build_mode: buildMode,
      dockerfile_path: dockerfilePath || "Dockerfile",
      project_path: projectPath || "",            // 서버 auto 모드 — 서브디렉토리. 빈 값=repo root
      // --- 정적 슬롯 ---
      use_static: useStatic,                      // off면 기존 사이트 teardown (DB 토글과 동일 철학)
      static_repo_url: staticRepoUrl || "",       // 빈 값=서버 repo 사용
      static_branch: staticBranch || "",          // 빈 값=서버 branch 사용
      static_project_path: staticProjectPath || "",
      build_cmd: buildCmd,                        // 빈 값이면 빌드 없이 repo 그대로 서빙
      output_dir: outputDir,                      // 빌드 산출물 디렉토리 (기본 dist)
      static_env: staticEnv,                      // 빌드 타임 변수 (VITE_* — 번들에 공개, 시크릿 금지)
      env,                                        // 첫 배포: Secret 생성. 재배포: replace. 빈 dict면 backend가 무시.
      init_dump_token: initDumpToken,             // 초기 DB .sql(.gz) stage 토큰. mysql Ready 후 자동 복원.
    }),
  });
}

// 초기 DB 덤프를 배포 전에 임시 업로드 → 토큰 반환. createDeploy의 initDumpToken으로 전달.
export async function stageDump(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/deploy/db/stage-dump`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    const err = new Error(`${res.status} ${detail}`);
    err.status = res.status;
    throw err;
  }
  return res.json(); // { token }
}

export function getBuild(buildId) {
  return request(`/deploy/${buildId}`);
}

export function listBuilds() {
  return request("/deploy");
}

// 최근 GitHub 커밋 (public repo만 — backend가 unauthenticated로 호출).
// 응답: [{sha, message, author, date, url}, ...]
export function listRecentCommits() {
  return request("/deploy/commits");
}

// 연결된 GitHub App installation이 접근 가능한 repo 목록 (private repo 선택용). 미연결이면 [].
// 응답: [{full_name, html_url, private, default_branch}, ...]
export function listGithubRepos() {
  return request("/deploy/github/repos");
}

// 특정 repo의 브랜치 목록 (배포 폼 브랜치 드롭다운). repo URL을 쿼리로 전달.
// 응답: [{name, protected}, ...]. 미연결·형식오류·실패면 백엔드가 [] 반환.
export function listGithubBranches(repoUrl) {
  return request(`/deploy/github/branches?repo=${encodeURIComponent(repoUrl)}`);
}

// 사용자 앱 환경변수 — {app_name}-env Secret을 진실원으로 GET/PUT.
// 첫 배포 전이거나 한 번도 설정 안 했으면 빈 dict.
export function getEnvVars() {
  return request("/deploy/env");
}

// dep별 자동 주입 env 키 맵 — 배포 폼 환경변수 인라인 충돌 검증용 (정적, 1회 fetch).
// 응답: { mysql: [...], postgres: [...], redis: [...], storage: [...] }
export function getReservedKeys() {
  return request("/deploy/reserved-keys");
}

// 전체 replace — 보낸 dict가 새 전체 상태. 저장 직후 Pod 자동 재시작.
export function setEnvVars(env) {
  return request("/deploy/env", {
    method: "PUT",
    body: JSON.stringify({ env }),
  });
}

// 앱 완전 삭제 — K8s 리소스 + PVC + builds + user.app_name 리셋.
// 응답 후 AuthContext.refresh()로 user 재조회해야 UI가 empty state로 전환됨.
export function deleteApp() {
  return request("/deploy/app", { method: "DELETE" });
}

// 커스텀 도메인 (CF for SaaS) — 서브도메인 전용 (CNAME). 루트(apex)는 미지원.
// 응답: { domain, status, ssl_status }  status: null|"pending"|"active"
// 유저가 자기 DNS에 걸 CNAME 타깃 (백엔드 config.CUSTOM_DOMAIN_CNAME_TARGET 기본값과 동기).
export const CUSTOM_DOMAIN_CNAME_TARGET = "origin.kodeploy.com";
export function getDomain() {
  return request("/deploy/domain");
}
export function setDomain(domain) {
  return request("/deploy/domain", { method: "PUT", body: JSON.stringify({ domain }) });
}
export function deleteDomain() {
  return request("/deploy/domain", { method: "DELETE" });
}

// R2 오브젝트 목록 — 응답: { objects: [{key, size, last_modified, url}], next }
// next(continuation token)가 있으면 다음 페이지 존재. token으로 이어받기.
export function listStorageObjects(token) {
  const q = token ? `?token=${encodeURIComponent(token)}` : "";
  return request(`/deploy/app/storage/objects${q}`);
}

// R2 오브젝트 1개 삭제 (파괴적). 응답: { status: "deleted" }
export function deleteStorageObject(key) {
  return request(`/deploy/app/storage/objects?key=${encodeURIComponent(key)}`, {
    method: "DELETE",
  });
}

// R2 오브젝트 1개의 본문 (텍스트·JSON 미리보기 전용). 응답: { key, text, truncated }
// 공개 URL로 브라우저가 직접 읽지 않고 core를 거친다 — R2 공개 버킷에 CORS가 없어서
// 다른 오리진의 fetch가 막히고, 미리보기 하나 때문에 버킷에 CORS를 열 이유는 없다.
// 상한(256KB)을 넘는 파일은 앞부분만 오고 truncated=true로 알려 준다.
export function readStorageObject(key) {
  return request(`/deploy/app/storage/object?key=${encodeURIComponent(key)}`);
}

// 현재 앱 Pod 상태 — 빌드와 독립. 응답: { status: "running" | "pending" | "crashing" | "missing" }
export function getAppStatus() {
  return request("/deploy/app/status");
}

// 런타임 로그 스냅샷 (현재 + 이전 인스턴스)
export function getAppLogs() {
  return request("/deploy/app/logs");
}

// 앱 메트릭 (VictoriaMetrics 프록시) — range: "15m"|"1h"|"6h"|"1d"|"3d"|"7d"|"14d"|"30d"
export function getAppMetrics(range = "1h") {
  return request(`/deploy/app/metrics?range=${encodeURIComponent(range)}`);
}

// DB 콘솔 — 단발 SQL 실행 후 구조화된 결과 반환.
// offset: SELECT/WITH 쿼리의 다음 페이지 시작 위치(500개씩). 그 외 쿼리는 무시됨.
// 응답: { db_type, columns, rows, row_count, paginated, offset, page_size, has_more, truncated, duration_ms, message }
export function runDbQuery(sql, offset = 0) {
  return request("/deploy/app/db/query", {
    method: "POST",
    body: JSON.stringify({ sql, offset }),
  });
}

// 저장된 쿼리 (DB 콘솔) — 플랫폼 DB에 보관. 유저 앱 DB는 건드리지 않는다.
// 스코프(유저·앱·DB)는 **보내지 않는다** — 서버가 세션 user와 최신 서버 빌드에서 정한다.
// 응답: [{ id, name, sql, created_at, updated_at }] (최신 저장순)
export function listSavedQueries() {
  return request("/deploy/app/db/queries");
}

export function createSavedQuery(name, sql) {
  return request("/deploy/app/db/queries", {
    method: "POST",
    body: JSON.stringify({ name, sql }),
  });
}

// 부분 수정 — 준 필드만 바뀐다 ({ name } 만 주면 SQL은 그대로).
export function updateSavedQuery(id, fields) {
  return request(`/deploy/app/db/queries/${id}`, {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}

export function deleteSavedQuery(id) {
  return request(`/deploy/app/db/queries/${id}`, { method: "DELETE" });
}

// DB 스냅샷 다운로드 URL — 현재 앱 MySQL을 mysqldump → .sql.gz (cookie 인증).
// 앵커/창 이동으로 직접 다운로드(스트림 → 디스크, 메모리 안 씀).
export function dbExportUrl() {
  return `${API_BASE}/deploy/db/export`;
}

// 업로드한 .sql(.gz)을 현재 앱 MySQL에 적재 (파괴적 — 기존 데이터 덮어씀). multipart 전송.
export async function restoreDb(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_BASE}/deploy/db/restore`, {
    method: "POST",
    credentials: "include",
    body: form, // Content-Type은 브라우저가 boundary 포함해 자동 설정
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {}
    const err = new Error(`${res.status} ${detail}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}
