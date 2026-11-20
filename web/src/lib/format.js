// GitHub repo URL → "owner/repo" (e.g., "username/repo"). 못 파싱하면 원본 반환.
export function repoSlug(url) {
  if (!url) return "";
  const m = url.match(/github\.com[/:]([^/]+\/[^/]+?)(?:\.git)?$/);
  return m ? m[1] : url;
}

// 백엔드는 timezone-aware UTC ISO를 보냄 ("...+00:00"). 혹시 timezone 없는 옛 데이터면 UTC로 가정.
// 모든 시간 처리는 이 함수를 거쳐 Date 객체를 얻는다 — 직접 new Date(iso) 금지.
export function parseDate(iso) {
  if (!iso) return null;
  const hasTz = /Z$|[+-]\d{2}:?\d{2}$/.test(iso);
  const d = new Date(hasTz ? iso : iso + "Z");
  return Number.isNaN(d.getTime()) ? null : d;
}

// ISO → "방금" / "N분 전" / "N시간 전" / "N일 전" / "M월 D일"
// compact=true 면 "전" 생략 ("5분", "2시간") — 좁은 영역(widget) 용도.
export function relativeTime(iso, { compact = false } = {}) {
  const d = parseDate(iso);
  if (!d) return iso || "";
  const diffSec = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (diffSec < 30) return "방금";
  const suffix = compact ? "" : " 전";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}분${suffix}`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}시간${suffix}`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}일${suffix}`;
  return d.toLocaleDateString("ko-KR", { month: "long", day: "numeric" });
}

// 초(float) → "12.3초" / "1분 12초" / "2분". 1분 미만은 소수 한 자리, 이상은 분+초.
// null/undefined면 빈 문자열 (빌드 진행 중이거나 기록 없음).
export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "";
  if (seconds < 60) return `${seconds.toFixed(1)}초`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return s === 0 ? `${m}분` : `${m}분 ${s}초`;
}

// ISO → "YY.MM.DD HH:MM" 단일 형식 (브라우저 로컬 timezone).
export function formatFull(iso) {
  const d = parseDate(iso);
  if (!d) return iso || "";
  const pad = (n) => String(n).padStart(2, "0");
  const yy = String(d.getFullYear()).slice(-2);
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hh = pad(d.getHours());
  const min = pad(d.getMinutes());
  return `${yy}.${mm}.${dd} ${hh}:${min}`;
}

// ISO → "오늘 14:20" / "어제 18:10" / 그 이전은 "YY.MM.DD HH:MM"(formatFull). 시안의 목록 시각 표기.
export function dayTime(iso) {
  const d = parseDate(iso);
  if (!d) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d >= today) return `오늘 ${hhmm}`;
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d >= yesterday) return `어제 ${hhmm}`;
  return formatFull(iso);
}

// GitHub Actions(CI 토큰)로 시작한 빌드의 출처 한 줄 — "GitHub Actions · <actor> · run <run_id>".
// ci는 빌드 응답의 ci 칸({ actor, repository, run_id, ... }). 웹에서 시작한 빌드는 null이라 null을 돌려준다.
export function ciRunLabel(ci) {
  if (!ci) return null;
  return ["GitHub Actions", ci.actor, ci.run_id && `run ${ci.run_id}`].filter(Boolean).join(" · ");
}

// 그 Actions 실행 페이지 (저장소와 run_id가 있을 때만)
export function ciRunUrl(ci) {
  if (!ci?.repository || !ci?.run_id) return null;
  const attempt = ci.run_attempt && ci.run_attempt !== "1" ? `/attempts/${ci.run_attempt}` : "";
  return `https://github.com/${ci.repository}/actions/runs/${ci.run_id}${attempt}`;
}
