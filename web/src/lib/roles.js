// 앱에서 내 단계 — 서버(core/app/apps/sharing.py)와 같은 순서: owner > editor > viewer.
// 서버가 최종 판정이고(403), 여기서는 못 할 동작을 화면에서 미리 치우는 데만 쓴다.
const RANK = { viewer: 1, editor: 2, owner: 3 };

// app.role이 need 이상인가. role이 없는 옛 응답은 주인으로 본다.
export function can(app, need) {
  return (RANK[app?.role || "owner"] || 0) >= RANK[need];
}

export const ROLE_LABEL = { owner: "주인", editor: "편집", viewer: "보기" };
