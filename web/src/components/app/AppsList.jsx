// 대시보드 — design/라이트모드-시안/03_내_앱_목록.png 기준. 로그인 후 앱으로 들어가는 관문 화면.
//
// 슬롯이 둘(서버 / 정적)이라 카드 격자 대신 시안처럼 섹션 세 개(앱 서버 / 프론트엔드 / 최근 배포)를
// 세로로 쌓고, 각 섹션은 굵은 제목 + 괘선 + 행으로 끝낸다. 앱이 여러 개면 섹션 안의 행이 앱 수만큼 늘어난다
// (새 카드·배지를 만들지 않고 같은 행 문법을 반복한다). 남이 공유해 준 앱은 이름 줄 메타에
// "공유받음 · 주인"이 붙는 것만 다르다.
//
// 이 라우트는 AppLayout(탭 셸) 밖이라 폴링해 줄 부모가 없다 — 여기서 직접 폴링한다.
// 앱 목록을 받고 앱마다 빌드와 앱 상태를 한 tick에서 같이 받고, 진행 중인 빌드가 있으면 주기를 줄인다(위젯과 같은 규칙).
//
// 슬롯 판별·호스트 규칙은 CommitListWidget / AppLayout과 동일하게 맞췄다:
//   - 서버 빌드 = runtime !== "static" && kind !== "env_change" 중 최신
//   - 정적 빌드 = runtime === "static" 중 최신
//   - 정적 슬롯이 있으면 {app}=정적 · {app}-api=서버, 없으면 {app}=서버
//
// 치수 주석의 숫자는 시안(1536폭) 실측 px이다. 콘텐츠 폭 1083 ≈ .kd-page의 1040이라
// 세로 값은 그대로 CSS px로 쓴다. 글자 크기는 시안 잉크가 아니라 index.css 타입 램프를 따른다.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AppWindow,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  ArrowUpRight,
  MoreHorizontal,
  SquareTerminal,
} from "lucide-react";
import { acceptInvite, declineInvite, getAppStatus, listApps, listBuilds, listInvites } from "../../api/deploy.js";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
import { STYLES_BUILD, STYLES_ENV } from "../StatusBadge.jsx";
import DeleteAppModal from "../DeleteAppModal.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { ROLE_LABEL, can } from "../../lib/roles.js";
import { formatFull, parseDate } from "../../lib/format.js";

// 진행 중인 빌드 — 폴링 주기 단축 + Pod이 아직 없는 슬롯의 "빌드 중" 판정에 쓴다.
const ACTIVE = new Set(["queued", "building", "built", "deploying"]);
const POLL_ACTIVE = 2500;
const POLL_IDLE = 10000;

// 런타임 표기 — 다른 앱 화면들(AppOverview/AppSettings/BuildDetail)과 같은 한글 라벨.
const RUNTIME_LABEL = {
  python: "Python",
  java: "Java",
  php: "PHP",
  javascript: "JavaScript",
  go: "Go",
  static: "정적 사이트",
};

// 상태 아이콘 — 시안은 이름 옆 동그라미 체크다. 라벨은 AppStatusBadge의 맵을 그대로 쓰고
// (문구 진실원은 한 곳), 색만 이 화면의 팔레트 변수로 고른다.
const STATUS_ICON = {
  running: CircleCheck,
  pending: CircleDashed,
  building: CircleDashed,
  crashing: CircleX,
  missing: CircleMinus,
};
const STATUS_COLOR = {
  running: "var(--ok-fg)",
  pending: "var(--warn-fg)",
  building: "var(--warn-fg)",
  crashing: "var(--err-fg)",
  missing: "var(--fg-4)",
};

// 시안의 "오늘 14:20" 표기. 그 이전 날짜는 lib/format의 절대시간으로 떨어진다.
// (AppHistory에도 같은 표기가 있지만 export가 아니라 여기서 parseDate만 재사용한다)
function dayTime(iso) {
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

// 앱 하나의 화면용 값 — 슬롯 판별과 호스트 규칙을 한 곳에서 계산한다.
function viewOf(app, builds, slotStatus) {
  const serverBuild = builds.find((b) => b.runtime !== "static" && b.kind !== "env_change");
  const staticBuild = builds.find((b) => b.runtime === "static");
  // 정적 슬롯 유무 — site_enabled가 진실원이고, 옛 응답을 위해 슬롯 상태/빌드로 보강한다.
  const hasSite = Boolean(app.site_enabled || slotStatus?.site || staticBuild);
  // Pod이 아직 없는 슬롯(missing)이라도 그 슬롯 빌드가 돌고 있으면 "빌드 중"으로 —
  // 빌드가 긴 런타임에서 행이 "중지"로 보이는 오해 방지 (위젯과 같은 규칙).
  const activeServerBuild = builds.some((b) => b.runtime !== "static" && ACTIVE.has(b.status));
  const activeStaticBuild = builds.some((b) => b.runtime === "static" && ACTIVE.has(b.status));
  const rawServerStatus = slotStatus?.server?.status || slotStatus?.status || null;
  const rawSiteStatus = slotStatus?.site?.status || null;
  const latest = builds[0] || null;
  return {
    app,
    serverBuild,
    staticBuild,
    serverHost: hasSite ? `${app.name}-api.kodeploy.com` : `${app.name}.kodeploy.com`,
    siteHost: `${app.name}.kodeploy.com`,
    serverStatus: rawServerStatus === "missing" && activeServerBuild ? "building" : rawServerStatus,
    siteStatus: rawSiteStatus === "missing" && activeStaticBuild ? "building" : rawSiteStatus,
    latest,
    // #N은 kind="build"만 카운트(env_change는 번호 없음). 목록은 최신순.
    latestNumber:
      latest && (latest.kind || "build") !== "env_change"
        ? builds.filter((b) => (b.kind || "build") !== "env_change").length
        : null,
    active: builds.some((b) => ACTIVE.has(b.status)),
  };
}

// 남이 공유해 준 앱 표시 — 내 앱이면 없다. 주인 아이디는 서버가 owner_login으로 줄 때만 붙는다.
function sharedMark(app) {
  if (!app.role || app.role === "owner") return null;
  return app.owner_login ? `공유받음 · ${app.owner_login}` : "공유받음";
}

export default function AppsList() {
  const { user, loading, openLogin } = useAuth();
  const [apps, setApps] = useState([]);
  // 앱 id → { builds, status } — 앱마다 한 tick에서 같이 받는다
  const [byApp, setByApp] = useState({});
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [invites, setInvites] = useState([]);          // 내가 받은 초대 (수락 전)
  const [reload, setReload] = useState(0);             // 수락하면 목록을 바로 다시 읽는다
  const [inviteBusy, setInviteBusy] = useState(false);

  // 앱 목록 + 앱별 빌드/상태 한 tick 폴링. 진행 중 빌드가 있으면 2.5s, 없으면 10s.
  useEffect(() => {
    if (loading || !user) {
      setApps([]);
      setByApp({});
      return;
    }
    let cancelled = false;
    let timer;
    const tick = async () => {
      let active = false;
      try {
        const [list, inv] = await Promise.all([listApps().then((r) => r || []), listInvites().catch(() => [])]);
        if (!cancelled) setInvites(inv || []);
        const pairs = await Promise.all(
          list.map(async (a) => {
            const [builds, status] = await Promise.all([
              listBuilds(a.id).catch(() => []),
              getAppStatus(a.id).catch(() => null),
            ]);
            return [a.id, { builds: builds || [], status }];
          }),
        );
        if (!cancelled) {
          setApps(list);
          setByApp(Object.fromEntries(pairs));
          active = pairs.some(([, v]) => v.builds.some((b) => ACTIVE.has(b.status)));
        }
      } catch {
        // 배경 폴링 — 화면에 빨간 에러를 띄우지 않는다 (다음 tick에서 회복)
      }
      if (!cancelled) timer = setTimeout(tick, active ? POLL_ACTIVE : POLL_IDLE);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [loading, user?.id, reload]);

  // 초대 수락·거절 — 끝나면 앱 목록을 바로 다시 읽는다 (수락한 앱이 아래 행으로 나타난다)
  const answer = async (inv, accept) => {
    if (inviteBusy) return;
    setInviteBusy(true);
    try {
      await (accept ? acceptInvite(inv.id) : declineInvite(inv.id));
    } catch {
      // 이미 취소된 초대 등 — 목록을 다시 읽으면 사라진다
    } finally {
      setInviteBusy(false);
      setReload((n) => n + 1);
    }
  };

  const views = useMemo(
    () => apps.map((a) => viewOf(a, byApp[a.id]?.builds || [], byApp[a.id]?.status || null)),
    [apps, byApp],
  );
  const serverViews = views.filter((v) => v.serverBuild);
  const siteViews = views.filter((v) => v.staticBuild);
  const firstOwned = apps.find((a) => !a.role || a.role === "owner") || null;

  // 새 앱을 만들 수 있나 — 등급 한도(max_apps, null=무제한). 서버도 같은 규칙으로 막는다.
  const canCreate = user?.max_apps == null || (user?.app_count ?? 0) < user.max_apps;

  if (loading) return null;                          // /auth/me 첫 응답 전 깜빡임 방지

  return (
    <div className="kd-page" style={{ paddingTop: 48, paddingBottom: 64 }}>
      {/* ── 페이지 헤더 (시안 제목 잉크 y144 · 설명 y198) ── */}
      <h1 className="kd-t-display text-fg-1">대시보드</h1>
      <p className="kd-t-lead text-fg-2" style={{ marginTop: 2 }}>
        배포한 앱과 프론트엔드를 관리하세요.
      </p>

      {/* ── 받은 초대 — 다른 사람이 공유해 준 앱. 수락해야 아래 목록에 나타난다 ── */}
      {user && invites.length > 0 && (
        <Section title="받은 초대" first>
          {invites.map((inv) => (
            <div
              key={inv.id}
              className="flex items-center gap-4 flex-wrap"
              style={{ paddingBlock: 21, paddingLeft: 14, borderBottom: "1px solid var(--kd-border)" }}
            >
              <span className="kd-t-title text-fg-1">{inv.app_name}</span>
              <span className="kd-t-body text-fg-2">
                {inv.owner_login ? `${inv.owner_login} 님이 ` : ""}
                {ROLE_LABEL[inv.role] || inv.role} 권한으로 초대했어요
              </span>
              <div className="ml-auto flex items-center gap-2.5 shrink-0">
                <button
                  className="kd-btn-primary kd-btn-md"
                  disabled={inviteBusy}
                  onClick={() => answer(inv, true)}
                >
                  수락
                </button>
                <button
                  className="kd-btn-secondary kd-btn-md"
                  disabled={inviteBusy}
                  onClick={() => answer(inv, false)}
                >
                  거절
                </button>
              </div>
            </div>
          ))}
        </Section>
      )}

      {!user || views.length === 0 ? (
        <FirstDeploy loggedIn={Boolean(user)} onLogin={openLogin} />
      ) : (
        <>
          {/* ── 앱 서버 (시안 제목 y285 · 괘선 y327 · 행 y360-422 · 마감 괘선 y453) ── */}
          <Section
            title="앱 서버"
            first={invites.length === 0}
            aside={<NewAppAction canCreate={canCreate} user={user} />}
          >
            {serverViews.length > 0 ? (
              serverViews.map((v) => (
                <SlotRow
                  key={v.app.id}
                  to={`/apps/${v.app.id}`}
                  icon={SquareTerminal}
                  name={v.app.name}
                  status={v.serverStatus}
                  meta={[
                    RUNTIME_LABEL[v.serverBuild.runtime] || v.serverBuild.runtime,
                    v.serverBuild.branch,
                    sharedMark(v.app),
                  ]}
                  host={v.serverHost}
                  action={
                    <>
                      <Link
                        to={`/apps/${v.app.id}/workspace`}
                        className="kd-btn-primary kd-btn-lg inline-flex items-center gap-2 no-underline"
                      >
                        작업 공간 열기
                      </Link>
                      <AppMenu
                        base={`/apps/${v.app.id}`}
                        app={v.app}
                        onDelete={!v.app.role || v.app.role === "owner" ? () => setDeleteTarget(v.app) : null}
                      />
                    </>
                  }
                />
              ))
            ) : (
              /* 정적 단독 배포(runtime "none") — 서버 슬롯이 비어 있는 경우 */
              <EmptyRow
                icon={SquareTerminal}
                title="앱 서버가 아직 없어요."
                desc="깃허브 저장소를 연결해 서버를 배포할 수 있어요."
                to={firstOwned ? `/apps/${firstOwned.id}/deploy` : "/deploy"}
                cta="앱 서버 배포"
              />
            )}
          </Section>

          {/* ── 프론트엔드 (시안 제목 y527 · 괘선 y569 · 행 y603-653 · 마감 괘선 y687) ── */}
          <Section title="프론트엔드">
            {siteViews.length > 0 ? (
              siteViews.map((v) => (
                <SlotRow
                  key={v.app.id}
                  to={`/apps/${v.app.id}`}
                  icon={AppWindow}
                  name={v.app.name}
                  status={v.siteStatus}
                  meta={[RUNTIME_LABEL.static, v.staticBuild.branch, sharedMark(v.app)]}
                  host={v.siteHost}
                  action={
                    <Link
                      to={`/apps/${v.app.id}/deploy/frontend`}
                      className="kd-btn-secondary kd-btn-lg inline-flex items-center no-underline"
                    >
                      프론트엔드 다시 배포
                    </Link>
                  }
                />
              ))
            ) : (
              <EmptyRow
                icon={AppWindow}
                title="프론트엔드가 아직 없어요."
                desc="앱과 연결할 웹 화면을 배포할 수 있어요."
                to={firstOwned ? `/apps/${firstOwned.id}/deploy/frontend` : "/deploy/frontend"}
                cta="프론트엔드 배포"
              />
            )}
          </Section>

          {/* ── 최근 배포 (시안 제목 y758 · 괘선 y799 · 행 y825-841 · 마감 괘선 y867) ── */}
          <Section title="최근 배포">
            {views.map((v) => (
              <div
                key={v.app.id}
                className="flex items-center gap-3 flex-wrap"
                style={{
                  paddingBlock: 21,
                  paddingLeft: 8,
                  borderBottom: "1px solid var(--kd-border)",
                }}
              >
                {views.length > 1 && (
                  <span className="kd-t-body kd-strong text-fg-1">{v.app.name}</span>
                )}
                {v.latest ? (
                  <>
                    {v.latestNumber != null && (
                      <span className="kd-t-body kd-strong text-fg-1 tabular-nums">
                        #{v.latestNumber}
                      </span>
                    )}
                    <span className="kd-t-body text-fg-2">{buildLabel(v.latest)}</span>
                    <span className="kd-t-body text-fg-4">·</span>
                    <span className="kd-t-body text-fg-2 tabular-nums">
                      {dayTime(v.latest.created_at)}
                    </span>
                  </>
                ) : (
                  <span className="kd-t-body text-fg-2">아직 배포 기록이 없어요.</span>
                )}
                <Link
                  to={`/apps/${v.app.id}/history`}
                  className="kd-t-body text-fg-2 hover:text-fg-1 transition-colors no-underline
                             ml-auto inline-flex items-center gap-2 shrink-0"
                >
                  배포 이력 보기
                </Link>
              </div>
            ))}
          </Section>
        </>
      )}

      {/* ── 바닥 텍스트 링크 (시안 y952) ── */}
      <div className="flex items-center gap-4" style={{ marginTop: 80 }}>
        <Link
          to="/guide"
          className="kd-t-body-s text-fg-2 hover:text-fg-1 transition-colors no-underline"
        >
          이용 가이드
        </Link>
        <span className="kd-t-body-s text-fg-4">|</span>
        <Link
          to="/community"
          className="kd-t-body-s text-fg-2 hover:text-fg-1 transition-colors no-underline"
        >
          피드백
        </Link>
      </div>

      {deleteTarget && (
        <DeleteAppModal app={deleteTarget} onClose={() => setDeleteTarget(null)} />
      )}
    </div>
  );
}

// 새 앱 만들기 — 앱 서버 섹션 제목 줄 오른쪽. 지금 앱 수와 등급 한도를 늘 보여 주고(무제한이면 ∞),
// 한도에 닿으면 버튼 대신 한도 안내만 남는다.
function NewAppAction({ canCreate, user }) {
  const limit = user.max_apps == null ? "∞" : user.max_apps;
  return (
    <div className="flex items-center gap-3.5">
      <span className="kd-t-body-s text-fg-2 tabular-nums">
        앱 {user.app_count ?? 0}/{limit}
        {!canCreate && " · 한도에 도달했어요"}
      </span>
      {canCreate && (
        <Link to="/deploy" className="kd-btn-secondary kd-btn-md inline-flex items-center no-underline">
          새 앱 배포
        </Link>
      )}
    </div>
  );
}

// 빌드 한 줄 라벨 — 문구 진실원은 StatusBadge의 맵(빌드/환경변수 의미가 다르다).
function buildLabel(build) {
  if ((build.kind || "build") === "env_change") {
    return `환경변수 변경 · ${STYLES_ENV[build.status]?.label || build.status}`;
  }
  return STYLES_BUILD[build.status]?.label || build.status;
}

// 섹션 = 굵은 제목 + 바로 아래 괘선 + 행. 괘선 폭은 콘텐츠 폭 그대로(시안 227-1309).
// 첫 섹션만 조금 좁다 — 위가 설명 문단이라 line-height 여백이 이미 붙어 있다
// (시안: 설명 잉크→제목 잉크 68 / 앞 섹션 마감 괘선→제목 잉크 74).
function Section({ title, first, aside, children }) {
  return (
    <section style={{ marginTop: first ? 52 : 66 }}>
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <h2 className="kd-t-display-s text-fg-1">{title}</h2>
        {aside}
      </div>
      <div style={{ borderTop: "1px solid var(--kd-border)", marginTop: 12 }} />
      {children}
    </section>
  );
}

// 배포된 슬롯 한 줄 — 글리프 / 이름+상태 / 메타(런타임 · 브랜치 · 호스트) / 우측 액션.
// 시안: 글리프 55x47(x242) · 이름 x332 · 메타 y404 · 버튼 48 높이(x1071-1309).
//
// 행 전체가 앱 개요로 가는 링크다(to). 목록에서 앱을 누르면 상세로 들어가는 게 자연스럽고,
// 버튼만 누를 수 있으면 클릭 대상이 좁아진다. 행 안의 호스트 링크와 우측 버튼은
// 각자 다른 곳으로 가야 하므로 stopPropagation으로 행 이동을 막는다.
function SlotRow({ icon: Icon, name, status, meta, host, action, to }) {
  const navigate = useNavigate();
  const stop = (e) => e.stopPropagation();
  return (
    <div
      role={to ? "link" : undefined}
      tabIndex={to ? 0 : undefined}
      onClick={to ? () => navigate(to) : undefined}
      onKeyDown={
        to
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                navigate(to);
              }
            }
          : undefined
      }
      className={`flex items-center gap-9 flex-wrap${to ? " kd-hoverable" : ""}`}
      style={{
        paddingBlock: 30,
        paddingLeft: 14,
        borderBottom: "1px solid var(--kd-border)",
        cursor: to ? "pointer" : undefined,
      }}
    >
      <div className="shrink-0 flex items-center" style={{ width: 55 }}>
        <Icon size={40} strokeWidth={1.4} style={{ color: "var(--fg-2)" }} />
      </div>

      <div className="min-w-0">
        <div className="flex items-center gap-3.5 flex-wrap">
          <span className="kd-t-title text-fg-1">{name}</span>
          <StatusMark status={status} />
        </div>
        <div
          className="flex items-center gap-2 flex-wrap kd-t-body text-fg-2"
          style={{ marginTop: 2 }}
        >
          {meta.filter(Boolean).map((m) => (
            <span key={m} className="inline-flex items-center gap-2">
              <span>{m}</span>
              <span className="text-fg-4">·</span>
            </span>
          ))}
          <a
            href={`https://${host}`}
            target="_blank"
            rel="noopener noreferrer"
            onClick={stop}
            className="inline-flex items-center gap-1.5 text-fg-2 hover:text-fg-1 transition-colors"
            style={{ textDecoration: "underline", textUnderlineOffset: 3 }}
          >
            {host}
            <ArrowUpRight size={15} strokeWidth={1.8} className="shrink-0" />
          </a>
        </div>
      </div>

      <div className="ml-auto flex items-center gap-2.5 shrink-0" onClick={stop}>
        {action}
      </div>
    </div>
  );
}

// 상태 표시 — 시안의 동그라미 체크 + "실행 중". 라벨은 AppStatusBadge 맵에서 가져온다.
function StatusMark({ status }) {
  if (!status) return null;
  const Icon = STATUS_ICON[status] || CircleDashed;
  const label = APP_STATUS_STYLES[status]?.label || status;
  return (
    <span className="inline-flex items-center gap-2 shrink-0">
      <Icon
        size={18}
        strokeWidth={1.8}
        style={{ color: STATUS_COLOR[status] || "var(--fg-3)" }}
      />
      <span className="kd-t-body text-fg-2">{label}</span>
    </span>
  );
}

// 비어 있는 슬롯 — 글리프 + 굵은 한 줄 + 회색 한 줄 + 우측 괘선 버튼 (시안 프론트엔드 행).
function EmptyRow({ icon: Icon, title, desc, to, cta }) {
  return (
    <div
      className="flex items-center gap-9 flex-wrap"
      style={{
        paddingBlock: 30,
        paddingLeft: 14,
        borderBottom: "1px solid var(--kd-border)",
      }}
    >
      <div className="shrink-0 flex items-center" style={{ width: 55 }}>
        <Icon size={40} strokeWidth={1.4} style={{ color: "var(--fg-3)" }} />
      </div>
      <div className="min-w-0">
        <div className="kd-t-body kd-strong text-fg-1">{title}</div>
        <div className="kd-t-body text-fg-2" style={{ marginTop: 2 }}>
          {desc}
        </div>
      </div>
      <Link
        to={to}
        className="kd-btn-secondary kd-btn-lg ml-auto shrink-0 inline-flex items-center no-underline"
      >
        {cta}
      </Link>
    </div>
  );
}

// 앱 단위 동작 묶음 — 시안의 "…" 사각 버튼. 새 화면을 만들지 않고 기존 라우트/모달로만 보낸다.
function AppMenu({ base, app, onDelete }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label="앱 메뉴"
        className="kd-btn-secondary flex items-center justify-center"
        style={{ width: 48, height: 48, paddingInline: 0 }}
      >
        <MoreHorizontal size={18} strokeWidth={1.8} />
      </button>
      {open && (
        <div
          className="absolute right-0 kd-card overflow-hidden"
          style={{ top: 54, minWidth: 168, zIndex: 20 }}
        >
          <MenuLink to={base} label="앱 개요" onDone={() => setOpen(false)} />
          <MenuLink to={`${base}/history`} label="배포 이력" onDone={() => setOpen(false)} />
          {can(app, "editor") && (
            <MenuLink to={`${base}/env`} label="환경변수" onDone={() => setOpen(false)} />
          )}
          {can(app, "owner") && (
            <MenuLink to={`${base}/settings`} label="설정" onDone={() => setOpen(false)} />
          )}
          {onDelete && (
            <button
              onClick={() => {
                setOpen(false);
                onDelete();
              }}
              className="w-full text-left kd-t-body-s px-3.5 flex items-center"
              style={{
                height: "var(--row-md)",
                color: "var(--err-fg)",
                borderTop: "1px solid var(--kd-border)",
              }}
            >
              앱 삭제
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function MenuLink({ to, label, onDone }) {
  return (
    <Link
      to={to}
      onClick={onDone}
      className="kd-t-body-s text-fg-2 hover:text-fg-1 transition-colors no-underline
                 px-3.5 flex items-center"
      style={{ height: "var(--row-md)" }}
    >
      {label}
    </Link>
  );
}

// 미로그인 · 미배포 — 섹션 셋 대신 배포 유도 한 덩어리. 시안의 빈 행과 같은 문법을 쓴다.
function FirstDeploy({ loggedIn, onLogin }) {
  return (
    <div style={{ marginTop: 52 }}>
      <div style={{ borderTop: "1px solid var(--kd-border)" }} />
      <div
        className="flex items-center gap-9 flex-wrap"
        style={{
          paddingBlock: 30,
          paddingLeft: 14,
          borderBottom: "1px solid var(--kd-border)",
        }}
      >
        <div className="shrink-0 flex items-center" style={{ width: 55 }}>
          <SquareTerminal
            size={40}
            strokeWidth={1.4}
            style={{ color: "var(--fg-3)" }}
          />
        </div>
        <div className="min-w-0">
          <div className="kd-t-body kd-strong text-fg-1">아직 배포한 앱이 없어요.</div>
          <div className="kd-t-body text-fg-2" style={{ marginTop: 2 }}>
            깃허브 저장소를 연결하면 앱 서버와 프론트엔드를 여기서 관리해요.
          </div>
        </div>
        {loggedIn ? (
          <Link
            to="/deploy"
            className="kd-btn-primary kd-btn-lg ml-auto shrink-0 inline-flex items-center gap-2 no-underline"
          >
            앱 배포하기
          </Link>
        ) : (
          <button
            onClick={onLogin}
            className="kd-btn-primary kd-btn-lg ml-auto shrink-0 inline-flex items-center"
          >
            로그인하고 시작하기
          </button>
        )}
      </div>
    </div>
  );
}
