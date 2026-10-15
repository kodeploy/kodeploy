// 앱 상세 · 멤버 탭 — 다른 유저에게 이 앱을 보여 주거나 편집하게 한다.
//
// 예전엔 설정 탭의 한 섹션("초대하기")이었는데, 설정 안쪽에 접혀 있어 찾기 어려워서 왼쪽 메뉴로 꺼냈다.
// 초대는 이메일 또는 GitHub 아이디로 하고, 상대가 대시보드에서 수락해야 멤버가 된다.
// 주인은 역할을 바꾸고 내보내고 초대를 취소한다. 멤버 API는 주인만 부른다(서버도 막는다).
import { useEffect, useState } from "react";
import { Navigate, useOutletContext } from "react-router-dom";
import { can } from "../../lib/roles.js";
import {
  cancelInvite,
  getMembers,
  inviteToApp,
  removeMember,
  setMemberRole,
} from "../../api/deploy.js";

const ROLE_OPTIONS = [
  { id: "viewer", label: "보기" },
  { id: "editor", label: "편집" },
];
const ROLE_HINT = "보기: 상태·배포 이력·로그·모니터링 / 편집: 보기 + 재배포·환경변수. 터미널·DB·도메인·삭제는 주인만 해요.";

// 입력칸 폭 — 설정 탭 입력칸과 같은 값
const INPUT_W = 518;

// 이 화면은 owner 이상만 — 공유받은 앱에서 URL로 들어와도 개요로 돌려보낸다 (서버도 막는다).
export default function AppMembers() {
  const { app, base } = useOutletContext();
  if (!can(app, "owner")) return <Navigate to={base} replace />;
  return <AppMembersBody appId={app.id} />;
}

function AppMembersBody({ appId }) {
  const [data, setData] = useState(null); // { members, invites }
  const [target, setTarget] = useState("");
  const [role, setRole] = useState("viewer");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      setData(await getMembers(appId));
    } catch (e) {
      setError(e.message || "불러오지 못했어요");
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  // 동작 하나 — 끝나면 목록을 다시 읽는다. 실패는 한 줄로 보인다.
  const run = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      setError(e.message.replace(/^\d+ /, "") || "실패했어요");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const onInvite = async (e) => {
    e.preventDefault();
    if (!target.trim() || busy) return;
    if (await run(() => inviteToApp(appId, target.trim(), role))) setTarget("");
  };

  return (
    <div className="flex-1 overflow-auto scroll-thin">
      <div className="kd-page" style={{ paddingTop: 28, paddingBottom: 72 }}>
        <section className="kd-section">
          <h2 className="kd-t-section text-fg-1">초대하기</h2>
          <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10 }}>
            {ROLE_HINT}
          </p>

          <form onSubmit={onInvite} className="flex items-center gap-2 flex-wrap" style={{ marginTop: 18 }}>
            <input
              className="kd-input"
              style={{ flex: 1, minWidth: 220, maxWidth: INPUT_W }}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="이메일 또는 GitHub 아이디"
              spellCheck={false}
              autoCapitalize="off"
              disabled={busy}
            />
            <select
              className="kd-input"
              style={{ width: 88 }}
              value={role}
              onChange={(e) => setRole(e.target.value)}
              disabled={busy}
              aria-label="권한"
            >
              {ROLE_OPTIONS.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
            <button type="submit" className="kd-btn-primary kd-btn-md" disabled={busy || !target.trim()}>
              초대
            </button>
          </form>
          {error && (
            <p className="kd-t-caption" style={{ marginTop: 10, color: "var(--err-fg)" }}>
              {error}
            </p>
          )}
        </section>

        <section className="kd-section">
          <h2 className="kd-t-section text-fg-1">멤버</h2>
          <div style={{ marginTop: 12, borderTop: "1px solid var(--kd-border)" }}>
            {data === null && <p className="kd-t-body-s text-fg-3" style={{ paddingBlock: 16 }}>불러오는 중…</p>}
            {data?.members.map((m) => (
              <ShareRow
                key={m.user_id}
                name={m.login}
                note="멤버"
                role={m.role}
                busy={busy}
                onRole={(r) => run(() => setMemberRole(appId, m.user_id, r))}
                actionLabel="내보내기"
                onAction={() => run(() => removeMember(appId, m.user_id))}
              />
            ))}
            {data?.invites.map((i) => (
              <ShareRow
                key={i.id}
                name={i.email || `@${i.github_login}`}
                note="수락 대기 중"
                role={i.role}
                busy={busy}
                actionLabel="초대 취소"
                onAction={() => run(() => cancelInvite(appId, i.id))}
              />
            ))}
            {data && data.members.length === 0 && data.invites.length === 0 && (
              <p className="kd-t-body-s text-fg-3" style={{ paddingBlock: 16 }}>
                아직 공유한 사람이 없어요.
              </p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

// 멤버·대기 초대 한 줄 — 이름 · 상태 · 권한(멤버만 바꾼다) · 우측 동작.
function ShareRow({ name, note, role, busy, onRole, actionLabel, onAction }) {
  return (
    <div
      className="flex items-center gap-3 flex-wrap"
      style={{ minHeight: "var(--row-lg)", borderBottom: "1px solid var(--kd-border)", paddingBlock: 6 }}
    >
      <span className="kd-t-body-s kd-strong text-fg-1 truncate" style={{ maxWidth: 260 }}>
        {name}
      </span>
      <span className="kd-t-caption text-fg-3">{note}</span>
      {onRole ? (
        <select
          className="kd-input ml-auto"
          style={{ width: 88, height: 30 }}
          value={role}
          onChange={(e) => onRole(e.target.value)}
          disabled={busy}
          aria-label="권한"
        >
          {ROLE_OPTIONS.map((r) => (
            <option key={r.id} value={r.id}>
              {r.label}
            </option>
          ))}
        </select>
      ) : (
        <span className="kd-t-caption text-fg-2 ml-auto">{ROLE_OPTIONS.find((r) => r.id === role)?.label || role}</span>
      )}
      <button className="kd-btn-secondary kd-btn-sm" disabled={busy} onClick={onAction}>
        {actionLabel}
      </button>
    </div>
  );
}
