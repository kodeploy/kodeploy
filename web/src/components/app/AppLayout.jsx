// 앱 상세 셸 — 왼쪽 작업 메뉴(개요 / 작업 공간 …)와 그 아래 앱 액션, 오른쪽에 탭별 화면(Outlet).
// 화면 안에 제목 줄을 두지 않는다 — 지금 어디인지는 왼쪽 메뉴가, 무엇을 보는지는 화면 자체가 말한다.
//
// 데이터(빌드 목록 · Pod 상태 · 환경변수)는 여기서 한 번만 폴링해 Outlet context로 내려준다.
// 탭마다 각자 폴링하면 같은 엔드포인트를 N배로 두드리게 되고, 탭 전환마다 로딩이 번쩍인다.
//
// 치수는 design/라이트모드-시안/09_앱_개요.png에서 실측한 값(시안 px ÷ 1.45)이다 — 주석의 숫자가 시안 원본 px.
import { useCallback, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { getAppStatus, getEnvVars, listBuilds, redeployApp } from "../../api/deploy.js";
import { useAppShell } from "../../contexts/AppShellContext.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
import AppInfoDrawer from "./AppInfoDrawer.jsx";
import { can } from "../../lib/roles.js";
import { useCurrentApp } from "./AppScope.jsx";

const ACTIVE = new Set(["queued", "building", "built", "deploying"]);

// 앱 상세 메뉴. 화면 안에 제목을 다시 적지 않으므로, 지금 어디인지는 이 메뉴가 알려 준다.
// 가이드(문서)는 같은 셸 안에서 열려 작업 화면을 벗어나지 않아서 같은 묶음에 둔다.
// 블로그·피드백·관리자는 앱을 다루는 화면이 아니라 여기서 뺐다(랜딩 상단바에 그대로 있다).
// to는 앱 주소(/apps/:id) 뒤에 붙는 부분이다. need는 이 메뉴를 보려면 필요한 내 단계(없으면 누구나) —
// 공유받은 앱에서 못 하는 화면은 메뉴에서 뺀다 (서버가 같은 규칙으로 막는다).
const TABS = [
  { label: "개요", to: "", end: true },
  { label: "작업 공간", to: "/workspace" },
  { label: "배포 이력", to: "/history" },
  { label: "환경변수", to: "/env", need: "editor" },
  { label: "멤버", to: "/members", need: "owner" },
  { label: "설정", to: "/settings", need: "owner" },
  { label: "가이드", to: "/guide" },
];

const SIDEBAR_W = 200;
// 사이드바 한 줄 — 메뉴와 아래 앱 액션이 같은 높이·여백·글자 굵기를 쓴다.
const SIDE_ROW = { display: "flex", alignItems: "center", height: 44, paddingInline: 24 };

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading: authLoading, openLogin } = useAuth();
  const { setPodStatus } = useAppShell();
  const { app, base } = useCurrentApp();
  const tabs = TABS.filter((t) => !t.need || can(app, t.need));

  const [builds, setBuilds] = useState([]);
  const [slotStatus, setSlotStatus] = useState(null);
  const [envVars, setEnvVars] = useState(null);
  const [error, setError] = useState(null);
  const [infoOpen, setInfoOpen] = useState(false);
  // 배포하기 — busy 동안 버튼을 잠근다. 못 하면 needsForm(409: 성공한 배포 없음)이나 error를 띄운다
  const [redeploy, setRedeploy] = useState({ busy: false, error: null, needsForm: false });

  // 배포 안내는 그 자리에서만 — 다른 화면으로 옮기면 걷는다
  useEffect(() => {
    setRedeploy((r) => (r.busy ? r : { busy: false, error: null, needsForm: false }));
  }, [location.pathname]);

  // 미로그인 가드
  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      openLogin?.();
      navigate("/", { replace: true });
    }
  }, [authLoading, user, openLogin, navigate]);

  const onAuthError = useCallback(
    (err) => {
      if (err.status !== 401) return false;
      openLogin?.();
      navigate("/", { replace: true });
      return true;
    },
    [openLogin, navigate],
  );

  // 빌드 폴링 — 활성 빌드 있으면 2.5s, 안정이면 8s
  useEffect(() => {
    if (authLoading || !app) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const data = await listBuilds();
        if (cancelled) return;
        setBuilds(data);
        setError(null);
        timer = setTimeout(tick, data.some((b) => ACTIVE.has(b.status)) ? 2500 : 8000);
      } catch (err) {
        if (cancelled || onAuthError(err)) return;
        setError(err.message || "조회 실패");
        timer = setTimeout(tick, 8000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [authLoading, user?.id, app?.id, onAuthError]);

  // Pod 상태 폴링 — 빌드와 독립(지금 살아 있나). 슬롯별 status를 그대로 들고 있는다.
  useEffect(() => {
    if (authLoading || !app) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const data = await getAppStatus();
        if (!cancelled) setSlotStatus(data);
      } catch (err) {
        if (cancelled || onAuthError(err)) return;
      }
      if (!cancelled) timer = setTimeout(tick, 10000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [authLoading, user?.id, app?.id, onAuthError]);

  // 지금 살아 있나 — 상단바 브레드크럼도 같은 값을 쓴다(폴링은 위 한 곳뿐).
  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;
  useEffect(() => {
    setPodStatus(podStatus);
  }, [podStatus, setPodStatus]);
  // 셸을 벗어나면(랜딩·관리자 등) 상태 표시도 같이 걷는다
  useEffect(() => () => setPodStatus(null), [setPodStatus]);

  // 환경변수 — 개수만 쓰므로 한 번만 (값은 마스킹된 채로 온다). 값은 비밀이라 편집 권한부터 읽는다.
  useEffect(() => {
    if (authLoading || !app || !can(app, "editor")) return;
    let cancelled = false;
    getEnvVars()
      .then((res) => !cancelled && setEnvVars(res.env || {}))
      .catch(() => !cancelled && setEnvVars({}));
    return () => {
      cancelled = true;
    };
  }, [authLoading, user?.id, app?.id]);

  // 배포하기 — 설정은 지금 그대로 두고, 코드는 브랜치의 최신 커밋을 새로 받아 빌드·배포한다
  // (설정·환경변수는 서버가 성공한 마지막 배포에서 가져온다). 시작되면 롤백처럼 새 배포의 진행 화면으로 간다 —
  // 두 슬롯이면 첫 빌드(서버)를 따라간다.
  const redeployAsIs = async () => {
    setRedeploy({ busy: true, error: null, needsForm: false });
    try {
      const res = await redeployApp(app.id);
      const first = res.builds?.[0]?.build_id;
      navigate(first ? `${base}/deploy/progress?build=${first}` : `${base}/deploy/progress`);
    } catch (err) {
      if (onAuthError(err)) return;
      if (err.status === 409) {
        setRedeploy({ busy: false, error: null, needsForm: true });
      } else {
        setRedeploy({ busy: false, error: err.message?.replace(/^\d+ /, "") || "배포를 시작하지 못했어요", needsForm: false });
      }
    }
  };

  if (authLoading || !user) return null;

  // 서버 슬롯의 최신 빌드 — 런타임·DB·스토리지 토글의 "지금" 값.
  // 정적 슬롯 빌드가 더 최신일 수 있어 슬롯 필터가 필수다.
  const serverBuild = builds.find((b) => b.runtime !== "static" && b.kind !== "env_change");
  const appHost = `${app.name}.kodeploy.com`;

  // 배포하기가 빌드할 브랜치 — 누르기 전에 무엇이 나가는지 보여 준다. 서버가 설정을 다시 만들 때와 같은 기준이다:
  // 슬롯별로 성공한(running) 가장 최근 배포. 롤백 행은 원본 배포의 브랜치를 복사해 둬서 그대로 쓴다. 정적은 사이트가 켜져 있을 때만.
  // 정적만 남겨 서버를 내린 앱은 서버가 빼고 배포하는데, 여기선 그 판정까지 하지 않는다(브랜치 표시만 하는 자리라서).
  const isBuild = (b) => (b.kind || "build") === "build" && b.status === "running";
  const liveServer = builds.find((b) => b.runtime !== "static" && isBuild(b));
  const liveStatic = app.site_enabled ? builds.find((b) => b.runtime === "static" && isBuild(b)) : null;
  const deployHint = (() => {
    const s = liveServer?.branch;
    const t = liveStatic?.branch;
    if (!s && !t) return null;
    const branches = s && t && s !== t ? `서버 ${s}, 정적 ${t}` : s || t;
    return `${branches} 최신 커밋 · 현재 설정`;
  })();
  const hintEl = deployHint && (
    <span className="kd-t-caption text-fg-3 block" style={{ overflowWrap: "anywhere" }}>
      {deployHint}
    </span>
  );

  // 앱 액션 — 사이드바 맨 아래. 화면마다 머리 줄을 두지 않으므로 여기 한 곳에만 있다.
  // 배포하기만 검은 버튼으로 세워 이 묶음에서 바로 실행되는 동작이라는 걸 보이게 하고(아래에 무엇이 나가는지 한 줄),
  // 설정 변경(배포 폼으로 이동)은 괘선 버튼으로 둔다. "재설정"은 초기화로 읽혀서 쓰지 않는다.
  // 앱 액션 — 사이드바에서는 메뉴와 같은 줄 리듬으로 하나씩, 좁은 화면에서는 메뉴 줄 오른쪽에 모으고 배포 버튼 둘은 그 아래 줄에 둔다.
  const actionOpen = (
    <a
      href={`https://${appHost}`}
      target="_blank"
      rel="noopener noreferrer"
      className="kd-t-body-s text-fg-2 hover:text-fg-1 no-underline transition-colors whitespace-nowrap"
      style={{ fontWeight: 500 }}
    >
      서비스 열기
    </a>
  );
  const actionInfo = (
    <button
      onClick={() => setInfoOpen(true)}
      className="kd-t-body-s text-fg-2 hover:text-fg-1 transition-colors whitespace-nowrap text-left"
      style={{ fontWeight: 500 }}
    >
      앱 정보
    </button>
  );
  const canDeploy = can(app, "editor");
  const actionRedeploy = canDeploy && (
    <button
      onClick={redeployAsIs}
      disabled={redeploy.busy}
      title={deployHint || undefined}
      className="kd-btn-primary kd-btn-md w-full inline-flex items-center justify-center whitespace-nowrap"
    >
      {redeploy.busy ? "배포 요청 중…" : "배포하기"}
    </button>
  );
  const actionReconfigure = canDeploy && (
    <Link
      to={`${base}/deploy`}
      className="kd-btn-secondary kd-btn-md w-full inline-flex items-center justify-center no-underline whitespace-nowrap"
    >
      설정 변경
    </Link>
  );
  // 좁은 화면 — 배포 버튼 둘은 메뉴 줄에 같이 두면 메뉴가 0px까지 눌려서(360px 이하는 화면 밖으로 넘친다)
  // 메뉴 아래 한 줄에 반씩 나눠 둔다. 스크롤에 밀리지 않는 자리라는 점은 그대로다. 무엇이 나가는지는 그 아래 한 줄.
  const narrowRedeploy = canDeploy && (
    <div style={{ paddingBlock: 8, borderBottom: "1px solid var(--kd-border)" }}>
      <div className="flex items-center" style={{ gap: 8 }}>
        <button
          onClick={redeployAsIs}
          disabled={redeploy.busy}
          title={deployHint || undefined}
          className="kd-btn-primary kd-btn-sm flex-1 min-w-0 inline-flex items-center justify-center whitespace-nowrap"
        >
          {redeploy.busy ? "배포 요청 중…" : "배포하기"}
        </button>
        <Link
          to={`${base}/deploy`}
          className="kd-btn-secondary kd-btn-sm flex-1 min-w-0 inline-flex items-center justify-center no-underline whitespace-nowrap"
        >
          설정 변경
        </Link>
      </div>
      {hintEl && <div style={{ marginTop: 6 }}>{hintEl}</div>}
    </div>
  );

  const ctx = {
    user,
    app,
    base,
    builds,
    serverBuild,
    slotStatus,
    envVars,
    appHost,
    error,
  };

  const isActive = (t) =>
    t.end ? location.pathname === base : location.pathname.startsWith(base + t.to);

  return (
    <div className="flex-1 min-h-0 flex">
      {/* ── 왼쪽 작업 메뉴 — 앱 안에서만 쓰는 세로 메뉴. 아래쪽에 읽는 화면 링크를 묶는다 ── */}
      <aside
        className="hidden md:flex shrink-0 flex-col"
        style={{
          width: SIDEBAR_W,
          borderRight: "1px solid var(--kd-border)",
          // 첫 항목("개요")의 글자 가운데가 오른쪽 첫 줄(작업 공간의 뷰 탭 글자)과 같은
          // 높이(30)에 오도록. 제목 줄이 있던 시절의 12는 맞출 대상이 사라졌다.
          paddingTop: 8,
          paddingBottom: 22,
        }}
      >
        <nav className="flex flex-col">
          {tabs.map((t) => (
            <SideLink key={t.label} to={base + t.to} active={isActive(t)}>
              {t.label}
            </SideLink>
          ))}
        </nav>

        {/* 앱 액션 — 메뉴와 괘선으로 끊어 아래에 묶는다. 이동이 아니라 동작이라서 따로 둔다.
            줄 높이·좌우 여백·글자 굵기는 위 메뉴와 같은 값을 쓴다(괘선은 구분선 톤). */}
        <div className="mt-auto" style={{ paddingTop: 16 }}>
          <div
            aria-hidden
            style={{ height: 1, background: "var(--kd-rule)", marginInline: 24, marginBottom: 8 }}
          />
          <div style={SIDE_ROW}>{actionOpen}</div>
          <div style={SIDE_ROW}>{actionInfo}</div>
          {canDeploy && (
            <div className="flex flex-col" style={{ paddingInline: 24, paddingTop: 8, gap: 8 }}>
              <div className="flex flex-col" style={{ gap: 6 }}>
                {actionRedeploy}
                {hintEl}
              </div>
              {actionReconfigure}
            </div>
          )}
        </div>

      </aside>

      <div className="kd-app-main flex-1 min-w-0 min-h-0 flex flex-col">
        {/* 좁은 화면 — 세로 메뉴 대신 가로로 눕힌다(넘치면 옆으로 민다) */}
        <div className="md:hidden kd-page shrink-0">
          <div
            className="flex items-center h-12"
            style={{ borderBottom: "1px solid var(--kd-border)" }}
          >
            {/* 메뉴만 옆으로 밀리고 액션은 오른쪽에 고정된다 — 배포 버튼(아래 줄)도 스크롤 밖으로 나가면 안 된다 */}
            <nav className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto scroll-thin">
            {tabs.map((t) => {
              const on = isActive(t);
              return (
                <Link
                  key={t.label}
                  to={base + t.to}
                  aria-current={on ? "page" : undefined}
                  className="kd-t-label kd-pick-x kd-pick-x-edge h-12 px-3 inline-flex items-center no-underline shrink-0"
                  style={{ color: "var(--fg-2)", fontWeight: 500 }}
                >
                  <span className="kd-pick-name">{t.label}</span>
                </Link>
              );
            })}
            </nav>
            {/* 좁은 화면에는 사이드바가 없다 — 앱 액션을 같은 줄 오른쪽에 둔다 */}
            <span
              aria-hidden
              className="shrink-0"
              style={{ width: 1, height: 16, background: "var(--kd-border)", marginInline: 10 }}
            />
            <div className="flex items-center gap-3.5 shrink-0">
              {actionOpen}
              {actionInfo}
            </div>
          </div>
          {narrowRedeploy}
        </div>

        {/* 관리자 권한으로 보는 남의 앱 — 여기서 바꾸는 것은 관리자 기록에 남는다 */}
        {app?.admin && (
          <div className="kd-page shrink-0">
            <div
              className="kd-t-caption mt-3 px-3 py-2"
              style={{ border: "1px solid var(--kd-border)", borderRadius: 4, color: "var(--warn-fg)" }}
            >
              관리자로 보는 중 · 주인 {app.owner_login || "알 수 없음"} ·{" "}
              {app.role === "owner" ? "주인 권한 — 바꾸는 동작은 관리자 기록에 남아요" : "보기 전용"}
            </div>
          </div>
        )}

        {/* 배포하기를 못 했다 — 성공한 배포가 없으면(409) 설정 변경(배포 폼)으로 안내한다 */}
        {(redeploy.needsForm || redeploy.error) && (
          <div className="kd-page shrink-0">
            <div
              role="alert"
              className="kd-t-caption mt-3 px-3 py-2"
              style={{ border: "1px solid var(--kd-border)", borderRadius: 4, color: "var(--err-fg)" }}
            >
              {redeploy.needsForm ? (
                <>
                  아직 성공한 배포가 없어서 배포하기를 쓸 수 없어요. 첫 배포는 설정 변경에서 해주세요.{" "}
                  <Link to={`${base}/deploy`} className="text-fg-1 underline underline-offset-4">
                    설정 변경 열기
                  </Link>
                </>
              ) : (
                redeploy.error
              )}
            </div>
          </div>
        )}

        {error && (
          <div className="kd-page shrink-0">
            <div
              className="kd-t-caption mt-3 px-3 py-2"
              style={{
                border: "1px solid var(--kd-border)",
                borderRadius: 4,
                color: "var(--err-fg)",
              }}
            >
              {error}
            </div>
          </div>
        )}

        <Outlet context={ctx} />
      </div>

      {infoOpen && <AppInfoDrawer ctx={ctx} onClose={() => setInfoOpen(false)} />}
    </div>
  );
}

// 세로 메뉴 한 줄 — 선택 표시는 .kd-pick 공통 규칙(왼쪽 2px 잉크 선 + 중간 굵기)이 그린다.
function SideLink({ to, active, children }) {
  return (
    <Link
      to={to}
      aria-current={active ? "page" : undefined}
      className="kd-t-body-s kd-pick no-underline"
      style={{ ...SIDE_ROW, color: "var(--fg-2)", fontWeight: 500 }}
    >
      <span className="kd-pick-name">{children}</span>
    </Link>
  );
}
