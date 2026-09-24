// 배포 옵션 라벨 — 2단계 선택지와 2·3단계 요약이 같은 문구를 쓰도록 한 곳에 모은다.
// 값(key)은 백엔드 core/app/deploy/schemas.py의 리터럴과 1:1이다. 임의로 늘리지 말 것.

// 서버 슬롯 런타임. name은 화면 표기, tag는 가이드 보조 문구.
export const RUNTIME_META = {
  python: { name: "Python", tag: "FastAPI · uvicorn" },
  java: { name: "Java", tag: "Spring Boot · JDK 17+" },
  php: { name: "PHP", tag: "Apache · PHP 8" },
  javascript: { name: "JavaScript", tag: "Node.js · Express/Next" },
  none: { name: "사용 안 함", tag: "정적 사이트 단독" },
};

// 런타임 추정이 알려주는 "아직 지원하지 않는" 런타임 표기 (백엔드 _UNSUPPORTED_MARKERS와 같은 키).
export const UNSUPPORTED_RUNTIME_NAMES = {
  go: "Go",
  ruby: "Ruby",
  rust: "Rust",
  elixir: "Elixir",
};

// runtime별 기본 listen 포트 — runtime을 바꾸면 포트가 따라 바뀐다.
export const DEFAULT_PORTS = {
  python: 8000,
  java: 8080,
  php: 8080,
  javascript: 3000,
};

export const DB_OPTIONS = [
  { id: "none", name: "사용 안 함" },
  { id: "mysql", name: "MySQL 8.4" },
  { id: "postgres", name: "PostgreSQL 16" },
];

export const STORAGE_OPTIONS = [
  { id: "none", name: "사용 안 함", hint: "다시 배포하면 앱이 쓴 파일은 사라집니다." },
  { id: "local", name: "영구 저장소", hint: "지정한 경로에 영구 디스크를 붙여 재배포에도 파일을 유지합니다." },
  { id: "object", name: "객체 스토리지", hint: "앱에서 업로드한 파일을 R2에 보관합니다." },
];

export const labelOf = (list, id) => list.find((o) => o.id === id)?.name || id;

// 빌드 방식 — 시안은 2지선다(자동 감지 / Dockerfile 사용).
// 백엔드는 detect/dockerfile/auto 3값이라 "auto"(nixpacks 강제)는 자동 감지 쪽에 묶어 보존한다.
export const buildModeLabel = (m) => (m === "dockerfile" ? "Dockerfile 사용" : "자동 감지");

// https://github.com/me/my-api(.git) → me/my-api. 형식이 아니면 null.
export function repoSlugOf(url) {
  const m = (url || "").trim().match(/github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
  return m ? `${m[1]}/${m[2]}` : null;
}
