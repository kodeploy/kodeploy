// 앱 상세 셸 — 왼쪽 작업 메뉴(개요 / 작업 공간 …)와 그 아래 앱 액션, 오른쪽에 탭별 화면(Outlet).
// 화면 안에 제목 줄을 두지 않는다 — 지금 어디인지는 왼쪽 메뉴가, 무엇을 보는지는 화면 자체가 말한다.
//
// 데이터(빌드 목록 · Pod 상태 · 환경변수)는 여기서 한 번만 폴링해 Outlet context로 내려준다.
// 탭마다 각자 폴링하면 같은 엔드포인트를 N배로 두드리게 되고, 탭 전환마다 로딩이 번쩍인다.
//
// 치수는 design/라이트모드-시안/09_앱_개요.png에서 실측한 값(시안 px ÷ 1.45)이다 — 주석의 숫자가 시안 원본 px.
import { useCallback, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { getAppStatus, getEnvVars, listBuilds } from "../../api/deploy.js";
import { useAppShell } from "../../contexts/AppShellContext.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
import AppInfoDrawer from "./AppInfoDrawer.jsx";

const ACTIVE = new Set(["queued", "building", "built", "deploying"]);

// 앱 상세 메뉴. 화면 안에 제목을 다시 적지 않으므로, 지금 어디인지는 이 메뉴가 알려 준다.
// 가이드(문서)는 같은 셸 안에서 열려 작업 화면을 벗어나지 않아서 같은 묶음에 둔다.
// 블로그·피드백·관리자는 앱을 다루는 화면이 아니라 여기서 뺐다(랜딩 상단바에 그대로 있다).
const TABS = [
  { label: "개요", to: "/dashboard", end: true },
  { label: "작업 공간", to: "/dashboard/workspace" },
  { label: "배포 이력", to: "/dashboard/history" },
  { label: "환경변수", to: "/dashboard/env" },
  { label: "설정", to: "/dashboard/settings" },
  { label: "가이드", to: "/dashboard/guide" },
];

const SIDEBAR_W = 200;
// 사이드바 한 줄 — 메뉴와 아래 앱 액션이 같은 높이·여백·글자 굵기를 쓴다.
const SIDE_ROW = { display: "flex", alignItems: "center", height: 44, paddingInline: 24 };

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading: authLoading, openLogin } = useAuth();
  const { setPodStatus } = useAppShell();

  const [builds, setBuilds] = useState([]);
  const [slotStatus, setSlotStatus] = useState(null);
  const [envVars, setEnvVars] = useState(null);
  const [error, setError] = useState(null);
  const [infoOpen, setInfoOpen] = useState(false);

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
    if (authLoading || !user?.app_name) return;
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
  }, [authLoading, user?.id, user?.app_name, onAuthError]);

  // Pod 상태 폴링 — 빌드와 독립(지금 살아 있나). 슬롯별 status를 그대로 들고 있는다.
  useEffect(() => {
    if (authLoading || !user?.app_name) return;
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
  }, [authLoading, user?.id, user?.app_name, onAuthError]);

  // 지금 살아 있나 — 상단바 브레드크럼도 같은 값을 쓴다(폴링은 위 한 곳뿐).
  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;
  useEffect(() => {
    setPodStatus(podStatus);
  }, [podStatus, setPodStatus]);
  // 셸을 벗어나면(랜딩·관리자 등) 상태 표시도 같이 걷는다
  useEffect(() => () => setPodStatus(null), [setPodStatus]);

  // 환경변수 — 개수만 쓰므로 한 번만 (값은 마스킹된 채로 온다)
  useEffect(() => {
    if (authLoading || !user?.app_name) return;
    let cancelled = false;
    getEnvVars()
      .then((res) => !cancelled && setEnvVars(res.env || {}))
      .catch(() => !cancelled && setEnvVars({}));
    return () => {
      cancelled = true;
    };
  }, [authLoading, user?.id, user?.app_name]);

  if (authLoading || !user) return null;
  if (!user.app_name) return <NoAppYet />;

  // 서버 슬롯의 최신 빌드 — 런타임·DB·스토리지 토글의 "지금" 값.
  // 정적 슬롯 빌드가 더 최신일 수 있어 슬롯 필터가 필수다.
  const serverBuild = builds.find((b) => b.runtime !== "static" && b.kind !== "env_change");
  const appHost = `${user.app_name}.kodeploy.com`;

  // 앱 액션 — 사이드바 맨 아래. 화면마다 머리 줄을 두지 않으므로 여기 한 곳에만 있다.
  // 재배포만 검은 버튼으로 세워 이 묶음에서 유일한 실행 동작이라는 걸 보이게 한다.
  // 앱 액션 — 사이드바에서는 메뉴와 같은 줄 리듬으로 하나씩, 좁은 화면에서는 한 줄에 모아 쓴다.
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
  const actionRedeploy = (
    <Link
      to="/deploy"
      className="kd-btn-primary kd-btn-md w-full inline-flex items-center justify-center no-underline"
    >
      재배포
    </Link>
  );
  const actions = (
    <>
      {actionOpen}
      {actionInfo}
      {actionRedeploy}
    </>
  );

  const ctx = {
    user,
    builds,
    serverBuild,
    slotStatus,
    envVars,
    appHost,
    error,
  };

  const isActive = (t) =>
    t.end ? location.pathname === t.to : location.pathname.startsWith(t.to);

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
          {TABS.map((t) => (
            <SideLink key={t.to} to={t.to} active={isActive(t)}>
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
          <div style={{ paddingInline: 24, paddingTop: 8 }}>{actionRedeploy}</div>
        </div>

      </aside>

      <div className="kd-app-main flex-1 min-w-0 min-h-0 flex flex-col">
        {/* 좁은 화면 — 세로 메뉴 대신 가로로 눕힌다(넘치면 옆으로 민다) */}
        <div className="md:hidden kd-page shrink-0">
          <div
            className="flex items-center h-12"
            style={{ borderBottom: "1px solid var(--kd-border)" }}
          >
            {/* 메뉴만 옆으로 밀리고 액션은 오른쪽에 고정된다 — 재배포가 스크롤 밖으로 나가면 안 된다 */}
            <nav className="flex items-center gap-1 flex-1 min-w-0 overflow-x-auto scroll-thin">
            {TABS.map((t) => {
              const on = isActive(t);
              return (
                <Link
                  key={t.to}
                  to={t.to}
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
            <div className="flex items-center gap-3.5 shrink-0">{actions}</div>
          </div>
        </div>

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

// 첫 배포 전 — 탭을 보여줄 대상 자체가 없다
function NoAppYet() {
  return (
    <div className="flex-1 overflow-auto scroll-thin">
      <div className="kd-page" style={{ paddingTop: 120, maxWidth: 620 }}>
        <h2 className="kd-t-title text-fg-1 mb-3">
          아직 배포한 앱이 없어요
        </h2>
        <p className="kd-t-body text-fg-2 mb-8">
          첫 배포가 끝나면 이 화면에서 앱의 주소와 연결된 리소스, 배포 이력을 볼 수 있어요.
        </p>
        <Link to="/deploy" className="kd-btn-primary kd-btn-lg inline-flex items-center no-underline">
          배포하기
        </Link>
      </div>
    </div>
  );
}
