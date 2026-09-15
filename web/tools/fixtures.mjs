// 브라우저 흐름 검증용 스텁 데이터.
// 운영 백엔드에 로그인한 세션이 없어도 인증 화면을 렌더/조작할 수 있게 API 응답을 가로챈다.
// 스키마는 core/app/deploy/schemas.py, core/app/auth/schemas.py와 맞춰 둔다.
const NOW = "2026-09-14T12:00:00+00:00";
const AGO = (m) => new Date(Date.parse(NOW) - m * 60000).toISOString().replace("Z", "+00:00");

export const ME = {
  id: "11111111-1111-1111-1111-111111111111",
  login: "yuntyu01",
  avatar_url: null,
  role: "root",
  app_name: "my-api",
  site_enabled: true,
};

const baseBuild = {
  repo_url: "https://github.com/me/my-api",
  branch: "main",
  app_name: "my-api",
  runtime: "python",
  build_mode: "auto",
  port: 8080,
  db_type: "postgres",
  use_redis: true,
  use_storage: true,
  storage: "object",
  volume_mount_path: "",
  volume_storage_class: "local-path",
  volume_size: "5Gi",
  kind: "build",
  dockerfile_path: "Dockerfile",
  project_path: "",
  build_cmd: "",
  output_dir: "",
  static_env: {},
  dockerfile_content: "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\nCMD [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8080\"]",
  error: null,
  env_change_summary: null,
  ai_analysis: null,
  logs: "[1/3] 소스 가져오기 완료\n[2/3] 이미지 빌드\n[3/3] 앱 시작\nApplication startup complete",
  total_seconds: 42.1,
};

export const BUILDS = [
  { ...baseBuild, build_id: "a81c92f0", status: "running", created_at: AGO(20), updated_at: AGO(19) },
  {
    ...baseBuild,
    build_id: "e3b1c7d9",
    kind: "env_change",
    status: "running",
    env_change_summary: "LOG_LEVEL (수정)",
    logs: null,
    total_seconds: null,
    created_at: AGO(90),
    updated_at: AGO(90),
  },
  {
    ...baseBuild,
    build_id: "c47d2100",
    status: "failed",
    error: "requirements.txt not found",
    logs: "[1/3] 소스 가져오기 완료\n[2/3] 이미지 빌드\nCOPY requirements.txt /app/\nERROR: /requirements.txt: not found\nBuild failed",
    total_seconds: 18.4,
    created_at: AGO(200),
    updated_at: AGO(199),
  },
  { ...baseBuild, build_id: "4bd381aa", status: "running", created_at: AGO(1400), updated_at: AGO(1399), total_seconds: 39.2 },
  {
    ...baseBuild,
    build_id: "9f0e12bc",
    runtime: "static",
    build_mode: "static",
    db_type: "none",
    use_redis: false,
    use_storage: false,
    build_cmd: "npm ci && npm run build",
    output_dir: "dist",
    status: "running",
    created_at: AGO(1500),
    updated_at: AGO(1499),
  },
];

export const APP_STATUS = {
  status: "running",
  started_at: AGO(20),
  server: { status: "running", started_at: AGO(20) },
  site: { status: "running", started_at: AGO(1500) },
};

export const ENV = {
  env: { APP_ENV: "production", LOG_LEVEL: "info", API_KEY: "example-not-a-real-key", MAX_WORKERS: "2" },
};

export const DOMAIN = { domain: "api.example.com", status: "pending", cname_target: "origin.kodeploy.com" };

export const LOGS = {
  current: "2026-09-14 10:21:03 Application startup complete\n2026-09-14 10:21:15 GET /health 200 OK\n2026-09-14 10:23:42 GET /api/items 200 OK\n2026-09-14 10:24:11 GET /health 200 OK",
  previous: "",
};

// core/app/deploy/console/metrics.py — range 계열은 [{ts, value}], instant 계열은 숫자.
const T0 = Math.floor(Date.parse(NOW) / 1000) - 3600;
const series = (n, fn) =>
  Array.from({ length: n }, (_, i) => ({ ts: T0 + i * (3600 / n), value: fn(i, n) }));
const wave = (base, amp, seed) => (i, n) =>
  Math.max(0, base + amp * (Math.sin((i / n) * 6.3 + seed) * 0.6 + Math.sin(i * 1.7 + seed) * 0.4));

export const METRICS = {
  cpu: series(60, wave(0.04, 0.02, 0)),
  memory: series(60, wave(170e6, 14e6, 1)),
  rps: series(60, wave(12, 5, 2)),
  latency_p50: series(60, wave(24, 8, 3)),
  latency_p95: series(60, wave(86, 30, 4)),
  error_rate: series(60, wave(0.02, 0.015, 5)),
  net_rx: series(60, wave(32768, 12000, 6)),
  net_tx: series(60, wave(18432, 8000, 7)),
  restarts: series(60, () => 0),
  cpu_now: 0.04,
  mem_now: 172e6,
  cpu_limit: 0.5,
  mem_limit: 512 * 1024 * 1024,
  restart_total: 0,
  oom_total: 0,
};

export const COMMUNITY = [
  {
    id: 1, author: "yuntyu01", content: "로그 검색 조건을 기억하면 좋겠어요.\n작업 공간으로 돌아올 때 검색 조건이 유지되면 편할 것 같아요.",
    is_secret: false, is_mine: true, comment_count: 1, created_at: AGO(300),
  },
  {
    id: 2, author: "someone", content: "재배포할 때 저장된 파일은 유지되나요?\n앱을 다시 배포할 때 스토리지 동작이 궁금해요.",
    is_secret: false, is_mine: false, comment_count: 0, created_at: AGO(900),
  },
  {
    id: 3, author: "another", content: "모바일에서 터미널 가로 스크롤이 불편해요.",
    is_secret: false, is_mine: false, comment_count: 0, created_at: AGO(1800),
  },
];

export const COMMUNITY_DETAIL = {
  id: 1, author: "yuntyu01", is_secret: false, is_mine: true, created_at: AGO(300),
  content: "로그 검색 조건을 기억하면 좋겠어요.\n작업 공간으로 돌아올 때 검색 조건이 유지되면 편할 것 같아요.",
  comments: [
    { id: 10, post_id: 1, author: "KoDeploy", content: "어떤 검색 조건을 자주 사용하는지 알려주세요.", is_secret: false, is_mine: false, created_at: AGO(200) },
  ],
};

export const BLOG = [
  { title: "배포가 끝난 뒤에도, 관리가 쉬워지도록", url: "https://velog.io/@x/1", thumbnail: null, date: "2026-09-14", description: "터미널과 로그를 한곳에서 다루는 작업 공간의 방향을 정리했습니다." },
  { title: "GitHub에서 실행 중인 앱까지", url: "https://velog.io/@x/2", thumbnail: null, date: "2026-09-10", description: "소스 연결부터 빌드와 실행까지, 배포 과정을 살펴봅니다." },
];

export const STORAGE = {
  objects: [
    { key: "cover.png", size: 524288, last_modified: AGO(20), url: "data:image/png;base64,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" },
    { key: "logo.svg", size: 2458, last_modified: AGO(30), url: "https://assets.example.com/logo.svg" },
    { key: "config.json", size: 1232, last_modified: AGO(45), url: "https://assets.example.com/config.json" },
    { key: "report.pdf", size: 131072, last_modified: AGO(60), url: "https://assets.example.com/report.pdf" },
  ],
};


// ── 관리자 (/admin) ────────────────────────────────────────────────────────
export const ADMIN_OVERVIEW = {
  users: { total: 12, with_app: 7, signups_7d: 3 },
  builds: { total: 184, succeeded: 171, failed: 11, cancelled: 2, last_24h: 9, avg_success_seconds: 96 },
};

export const ADMIN_NODES = [
  { name: "oci-node-1", role: "control-plane", ready: true, cpu_used_cores: 0.82, cpu_capacity_cores: 4,
    memory_used_bytes: 6.1 * 1024 ** 3, memory_capacity_bytes: 24 * 1024 ** 3,
    disk_used_bytes: 12.4 * 1024 ** 3, disk_capacity_bytes: 29.5 * 1024 ** 3, pod_count: 21 },
  { name: "oci-node-2", role: "worker", ready: true, cpu_used_cores: 0.41, cpu_capacity_cores: 4,
    memory_used_bytes: 3.8 * 1024 ** 3, memory_capacity_bytes: 24 * 1024 ** 3,
    disk_used_bytes: 9.2 * 1024 ** 3, disk_capacity_bytes: 29.5 * 1024 ** 3, pod_count: 14 },
];

export const ADMIN_USERS = [
  { id: 1, login: "yuntyu01", email: "yuntyu01@example.com", avatar_url: null, role: "root",
    app_name: "my-api", tenant_id: "tenant-1a2b3c4d", custom_domain: "api.mine.dev",
    build_count: 42, last_build_at: AGO(25), created_at: AGO(60 * 24 * 90) },
  { id: 2, login: "someone", email: "someone@example.com", avatar_url: null, role: "user",
    app_name: "blog", tenant_id: "tenant-9f8e7d6c", custom_domain: null,
    build_count: 8, last_build_at: AGO(60 * 20), created_at: AGO(60 * 24 * 12) },
  { id: 3, login: "another", email: "another@example.com", avatar_url: null, role: "user",
    app_name: null, tenant_id: null, custom_domain: null,
    build_count: 0, last_build_at: null, created_at: AGO(60 * 24 * 2) },
];

export const ADMIN_BUILDS = [
  { id: 1, build_id: "a81c92f0", login: "yuntyu01", seq: 42, app_name: "my-api", runtime: "python",
    build_mode: "auto", started_at: AGO(25), nixpacks_seconds: 38, buildkit_seconds: 51, total_seconds: 96, status: "running", error: null },
  { id: 2, build_id: "c47d2100", login: "someone", seq: 8, app_name: "blog", runtime: "javascript",
    build_mode: "dockerfile", started_at: AGO(210), nixpacks_seconds: null, buildkit_seconds: 74, total_seconds: 88, status: "failed", error: "npm ci 실패" },
];

export const ADMIN_PODS = [
  { namespace: "tenant-1a2b3c4d", name: "my-api-7d9f", component: "server", phase: "Running", ready: true,
    restarts: 0, started_at: AGO(300), cpu_used_cores: 0.04, cpu_limit_cores: 0.5,
    memory_used_bytes: 172 * 1024 ** 2, memory_limit_bytes: 512 * 1024 ** 2, disk_used_bytes: null, disk_limit_bytes: null },
];

// path 접두 → 응답. 위에서부터 먼저 매칭되는 항목이 이기므로 구체적인 경로를 앞에 둔다.
// 경로는 web/src/api/*.js의 실제 호출과 1:1로 맞춘다 (listBuilds는 GET /deploy 이다).
export const ROUTES = [
  ["/auth/me", () => ME],
  ["/deploy/app/status", () => APP_STATUS],
  ["/deploy/app/logs", () => LOGS],
  ["/deploy/app/metrics", () => METRICS],
  ["/deploy/app/storage/objects", () => ({ objects: STORAGE.objects, next: null })],
  ["/deploy/app/db/query", () => ({ db_type: "postgres", columns: ["id", "title"], rows: [[105, "Release notes"]], row_count: 1, duration_ms: 12, message: null })],
  // 실제 응답은 dep별 배열이다 (core/app/deploy/build/validation.py)
  ["/deploy/reserved-keys", () => ({
    mysql: ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "SPRING_DATASOURCE_URL"],
    postgres: ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "POSTGRES_HOST", "SPRING_DATASOURCE_URL"],
    redis: ["REDIS_HOST", "REDIS_PORT", "SPRING_DATA_REDIS_HOST", "SPRING_DATA_REDIS_PORT"],
    storage: ["S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
  })],
  ["/admin/overview", () => ADMIN_OVERVIEW],
  ["/admin/users/1/tenant", () => ({ login: "yuntyu01", app_name: "my-api", tenant_id: "tenant-1a2b3c4d", custom_domain: "api.mine.dev", config: { runtime: "python", db_type: "postgres", use_redis: true, use_storage: true, build_mode: "auto", port: 8080, repo_url: "https://github.com/me/my-api", branch: "main", status: "running", created_at: AGO(300) }, pods: ADMIN_PODS })],
  ["/admin/users", () => ADMIN_USERS],
  ["/admin/nodes/oci-node-1/pods", () => ADMIN_PODS],
  ["/admin/nodes", () => ADMIN_NODES],
  ["/admin/builds", () => ADMIN_BUILDS],
  ["/deploy/env", () => ENV],
  ["/deploy/domain", () => DOMAIN],
  ["/deploy/commits", () => [
    { sha: "a81c92f0d1", message: "health endpoint 추가", author: "yuntyu01", date: AGO(25), url: "https://github.com/me/my-api/commit/a81c92f0d1" },
    { sha: "c47d2100ab", message: "의존성 수정", author: "yuntyu01", date: AGO(210), url: "https://github.com/me/my-api/commit/c47d2100ab" },
    { sha: "4bd381aa77", message: "초기 배포", author: "yuntyu01", date: AGO(1410), url: "https://github.com/me/my-api/commit/4bd381aa77" },
  ]],
  ["/deploy/github/repos", () => [{ full_name: "me/my-api", html_url: "https://github.com/me/my-api", private: false, default_branch: "main" }]],
  ["/deploy/github/branches", () => ["main", "dev"]],
  ["/community/blog", () => BLOG],
  ["/community/1", () => COMMUNITY_DETAIL],
  ["/community", () => COMMUNITY],
  // 가장 느슨한 접두는 맨 끝 — GET /deploy = 빌드 목록
  ["/deploy", () => BUILDS],
];
