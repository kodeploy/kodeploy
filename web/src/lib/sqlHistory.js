// DB 콘솔 "최근 실행" 보관소 — 브라우저(localStorage)에만 둔다.
//
// 서버에 안 두는 이유: 실행 이력은 이 브라우저에서 방금 뭘 쳤는지 되짚는 작업 흔적이지
// 계정에 남길 자산이 아니다(그 자산은 "저장된 쿼리" 쪽이다). 서버에 쌓으면 유저가 친
// SQL 전문이 플랫폼 DB에 무기한 남는다 — 남길 이유 없는 것을 남기지 않는다.
//
// 저장하는 것: SQL 문자열과 결과 **요약**(성공 여부·소요시간·행 수·오류 메시지)뿐.
// 조회 결과 데이터(columns/rows)는 절대 넣지 않는다 — 용량도 문제지만, 앱 DB의 실데이터가
// 브라우저 저장소로 새어 나가는 경로를 만들지 않는다.
//
// 키는 (유저, 앱, DB) 세 축으로 나눈다. 공용 PC에서 계정을 바꾸거나 앱/DB를 갈아탄 뒤
// 남의(=다른 스코프의) 이력이 보이면 안 된다 — 저장된 쿼리의 서버측 스코프와 같은 규칙.

export const HISTORY_LIMIT = 50;

const KEY_PREFIX = "kd.sqlHistory.";

// 스코프 키. 셋 중 하나라도 없으면 null — 그 상태에서는 읽지도 쓰지도 않는다
// (유저 로딩 전에 "스코프 없음" 칸에 이력을 흘려 넣으면 나중에 누구 것인지 알 수 없다).
export function historyKey({ userId, appName, dbType }) {
  if (!userId || !appName || !dbType) return null;
  return `${KEY_PREFIX}${userId}:${appName}:${dbType}`;
}

// localStorage는 프라이빗 모드·차단·용량 초과에서 throw하거나 빈 값을 준다.
// 이력은 있으면 좋은 부가 기능이라, 실패하면 조용히 비어 있는 채로 동작한다.
export function loadHistory(scope) {
  const key = historyKey(scope);
  if (!key) return [];
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.slice(0, HISTORY_LIMIT) : [];
  } catch {
    return [];
  }
}

export function saveHistory(scope, entries) {
  const key = historyKey(scope);
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(entries.slice(0, HISTORY_LIMIT)));
  } catch {
    /* 용량 초과·차단 — 이력만 못 남기고 콘솔은 그대로 쓴다 */
  }
}

// 실행 1건 기록. outcome은 세 가지다:
//   "ok"      — 서버가 결과를 돌려줬다 (duration_ms·row_count 있음)
//   "error"   — 서버가 "이 SQL은 실패"라고 답했다 (error에 DB 메시지)
//   "unknown" — 답을 못 받았다(네트워크 끊김·5xx 등). 돌았는지 아닌지 **모른다** —
//               실패로 뭉뚱그리면 "안 돌았겠지" 하고 재실행했다가 두 번 도는 사고가 난다.
export function makeEntry(sql, result) {
  return {
    id:
      globalThis.crypto?.randomUUID?.() ??
      `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    sql,
    at: new Date().toISOString(),
    ...result,
  };
}

// 최신순으로 앞에 붙이고 상한까지 자른다.
export function pushEntry(entries, entry) {
  return [entry, ...entries].slice(0, HISTORY_LIMIT);
}
