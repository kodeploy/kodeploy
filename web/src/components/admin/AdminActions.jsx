// 관리자 — 남의 앱·계정에 한 동작 기록 (최신 200건). 서버는 라우트 경로로 적고(admin/audit.py), 아는 경로는 우리말로 보인다.
import { Link } from "react-router-dom";
import { relativeTime } from "../../lib/format.js";
import { Hint, TableHead } from "./atoms.jsx";

// 서버 라우트(deploy/router.py · apps/router.py · admin/router.py) — 모르는 경로는 그대로 보인다.
const ACTION_LABEL = {
  "POST /deploy": "배포",
  "POST /deploy/{build_id}/rollback": "롤백",
  "PUT /deploy/env": "환경변수 변경",
  "PUT /deploy/domain": "도메인 연결",
  "DELETE /deploy/domain": "도메인 해제",
  "DELETE /deploy/app": "앱 삭제",
  "POST /deploy/app/db/query": "DB 콘솔 쿼리",
  "POST /deploy/app/db/queries": "DB 쿼리 저장",
  "PATCH /deploy/app/db/queries/{query_id}": "저장한 DB 쿼리 수정",
  "DELETE /deploy/app/db/queries/{query_id}": "저장한 DB 쿼리 삭제",
  "DELETE /deploy/app/storage/objects": "저장소 파일 삭제",
  "POST /deploy/db/stage-dump": "DB 덤프 올리기",
  "POST /deploy/db/restore": "DB 복원",
  "WS /deploy/app/terminal": "터미널 접속",
  "WS /deploy/app/db-terminal": "DB 터미널 접속",
  "WS /deploy/app/redis-terminal": "Redis 터미널 접속",
  "POST /apps/{app_id}/invites": "초대",
  "DELETE /apps/{app_id}/invites/{invite_id}": "초대 취소",
  "PATCH /apps/{app_id}/members/{user_id}": "멤버 권한 변경",
  "DELETE /apps/{app_id}/members/{user_id}": "멤버 내보내기",
  "DELETE /admin/users/{user_id}": "계정 강제 탈퇴",
};

export default function AdminActions({ actions, appIds }) {
  if (actions === null) return <Hint>기록을 불러오는 중…</Hint>;
  if (actions.length === 0) return <Hint>아직 관리자가 남의 앱이나 계정에 한 일이 없어요.</Hint>;
  return (
    <div className="kd-table-wrap overflow-x-auto scroll-thin" style={{ marginTop: 14 }}>
      <table className="w-full kd-t-body-s" style={{ borderCollapse: "collapse" }}>
        <TableHead cols={["시각", "관리자", "동작", "앱", "대상"]} />
        <tbody>
          {actions.map((a) => (
            <tr key={a.id} className="text-fg-2" style={{ borderBottom: "1px solid var(--kd-border)" }}>
              <td className="px-4 text-fg-3 tabular-nums whitespace-nowrap" style={{ height: "var(--row-md)" }}>
                {relativeTime(a.created_at)}
              </td>
              <td className="px-4 kd-strong" style={{ color: "var(--fg-1)" }}>
                {a.actor_login || "—"}
              </td>
              <td className="px-4" title={a.action}>
                {ACTION_LABEL[a.action] || <span className="kd-t-code">{a.action}</span>}
              </td>
              <td className="px-4">
                {a.app_name ? (
                  // 지금도 있는 앱이면 앱 화면으로 간다 (지워진 앱은 이름만)
                  appIds.has(a.app_id) ? (
                    <Link to={`/apps/${a.app_id}`} className="hover:underline" style={{ color: "var(--accent)" }}>
                      {a.app_name}
                    </Link>
                  ) : (
                    <span className="text-fg-3">{a.app_name}</span>
                  )
                ) : (
                  <span className="text-fg-4">—</span>
                )}
              </td>
              <td className="px-4">{a.target_login || <span className="text-fg-4">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
