// 앱 상세 셸 — 왼쪽 작업 메뉴(개요 / 작업 공간 …) + 페이지 머리(제목 · 앱 액션),
// 그 오른쪽에 탭별 화면(Outlet).
//
// 데이터(빌드 목록 · Pod 상태 · 환경변수)는 여기서 한 번만 폴링해 Outlet context로 내려준다.
// 탭마다 각자 폴링하면 같은 엔드포인트를 N배로 두드리게 되고, 탭 전환마다 로딩이 번쩍인다.
//
// 치수는 design/라이트모드-시안/09_앱_개요.png에서 실측한 값(시안 px ÷ 1.45)이다 — 주석의 숫자가 시안 원본 px.
import { useCallback, useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ArrowUpRight, Info, RotateCw } from "lucide-react";
import { getAppStatus, getEnvVars, listBuilds } from "../../api/deploy.js";
import { useAuth } from "../../contexts/AuthContext.jsx";
import AppInfoDrawer from "./AppInfoDrawer.jsx";

const ACTIVE = new Set(["queued", "building", "built", "deploying"]);

// 앱 상세 메뉴 5개. label이 곧 그 화면의 제목이다(페이지 머리에 그대로 쓴다).
const TABS = [
  { label: "개요", to: "/dashboard", end: true },
  { label: "작업 공간", to: "/dashboard/workspace" },
  { label: "배포 이력", to: "/dashboard/history" },
  { label: "환경변수", to: "/dashboard/env" },
  { label: "설정", to: "/dashboard/settings" },
];

// 사이드바 아래쪽 — 읽는 화면. 같은 셸 안에서 열리므로 작업 화면을 벗어나지 않는다.
// "이용 방법"은 넣지 않는다 — 여기까지 온 사람은 이미 배포를 했고, 같은 내용을 문서의
// "첫 배포 시작하기"가 더 자세히 다룬다(랜딩 상단바에는 그대로 있다).
const SITE_LINKS = [
  { label: "문서", to: "/dashboard/guide" },
  { label: "블로그", to: "/dashboard/blog" },
  { label: "피드백", to: "/dashboard/community" },
];
// 관리자 화면은 앱과 무관한 운영 화면이라 셸 밖으로 나간다.
const ADMIN_LINK = { label: "관리자", to: "/admin", away: true };
const ADMIN_ROLES = ["admin", "root"];

const SIDEBAR_W = 200;

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading: authLoading, openLogin } = useAuth();

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
  const current = [...TABS].reverse().find(isActive);
  const siteLinks = [
    ...SITE_LINKS,
    ...(ADMIN_ROLES.includes(user.role) ? [ADMIN_LINK] : []),
  ];
  const actions = (
    <>
      <a
        href={`https://${appHost}`}
        target="_blank"
        rel="noopener noreferrer"
        className="kd-t-label inline-flex items-center gap-1 text-fg-2 hover:text-fg-1 no-underline transition-colors"
      >
        서비스 열기
        <ArrowUpRight size={15} strokeWidth={1.8} />
      </a>
      {/* 시안은 액션 사이를 세로 괘선으로 끊는다 */}
      <span aria-hidden style={{ width: 1, height: 16, background: "var(--kd-border)" }} />
      <button
        onClick={() => setInfoOpen(true)}
        className="kd-t-label inline-flex items-center gap-1.5 text-fg-2 hover:text-fg-1 transition-colors"
      >
        <Info size={15} strokeWidth={1.7} />
        앱 정보
      </button>
      <Link
        to="/deploy"
        className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5 no-underline"
      >
        <RotateCw size={15} strokeWidth={1.9} />
        재배포
      </Link>
    </>
  );

  return (
    <div className="flex-1 min-h-0 flex">
      {/* ── 왼쪽 작업 메뉴 — 앱 안에서만 쓰는 세로 메뉴. 아래쪽에 읽는 화면 링크를 묶는다 ── */}
      <aside
        className="hidden md:flex shrink-0 flex-col"
        style={{
          width: SIDEBAR_W,
          borderRight: "1px solid var(--kd-border)",
          // 첫 항목의 가운데가 본문 제목과 같은 높이에 오도록 맞춘 값
          paddingTop: 12,
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

        <div className="mt-auto" style={{ paddingTop: 24 }}>
          <div
            aria-hidden
            style={{ height: 1, background: "var(--kd-border)", marginInline: 24, marginBottom: 12 }}
          />
          <nav className="flex flex-col">
            {siteLinks.map((l) => (
              <Link
                key={l.to}
                to={l.to}
                aria-current={!l.away && isActive(l) ? "page" : undefined}
                className="kd-t-label no-underline transition-colors"
                style={{
                  padding: "8px 24px",
                  color: !l.away && isActive(l) ? "var(--fg-1)" : "var(--fg-2)",
                  fontWeight: !l.away && isActive(l) ? 650 : 500,
                }}
              >
                {l.label}
              </Link>
            ))}
          </nav>
        </div>

      </aside>

      <div className="kd-app-main flex-1 min-w-0 min-h-0 flex flex-col">
        {/* 좁은 화면 — 세로 메뉴 대신 가로로 눕힌다(넘치면 옆으로 민다) */}
        <div className="md:hidden kd-page shrink-0">
          <div
            className="flex items-center gap-1 h-12 overflow-x-auto scroll-thin"
            style={{ borderBottom: "1px solid var(--kd-border)" }}
          >
            {[...TABS, ...siteLinks].map((t) => {
              const on = !t.away && isActive(t);
              return (
                <Link
                  key={t.to}
                  to={t.to}
                  className="kd-t-label h-12 px-3 inline-flex items-center no-underline shrink-0 transition-colors"
                  style={{
                    color: on ? "var(--fg-1)" : "var(--fg-2)",
                    fontWeight: on ? 650 : 500,
                    boxShadow: on ? "inset 0 -2px 0 0 var(--accent)" : "none",
                  }}
                >
                  {t.label}
                </Link>
              );
            })}
          </div>
        </div>

        {/* ── 페이지 머리 — 지금 화면 이름 + 앱 액션(서비스 열기 · 앱 정보 · 재배포).
            문서·이용 방법 같은 읽는 화면은 제 제목을 갖고 오므로 머리를 달지 않는다. ── */}
        {current && (
          <div className="kd-page shrink-0">
            <div
              className="flex items-center gap-4 flex-wrap"
              style={{ paddingTop: 20, paddingBottom: 16 }}
            >
              <h1 className="kd-t-title text-fg-1">{current.label}</h1>
              <div className="ml-auto flex items-center gap-4">{actions}</div>
            </div>
          </div>
        )}

        {error && (
          <div className="kd-page shrink-0">
            <div
              className="kd-t-caption mt-3 px-3 py-2"
              style={{
                border: "1px solid var(--kd-border)",
                borderRadius: 8,
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

// 세로 메뉴 한 줄 — 활성 항목은 왼쪽 세로 바(2px)와 굵은 글씨로 표시한다.
function SideLink({ to, active, children }) {
  return (
    <Link
      to={to}
      aria-current={active ? "page" : undefined}
      className="kd-t-body-s no-underline transition-colors"
      style={{
        display: "flex",
        alignItems: "center",
        height: 44,
        paddingInline: 24,
        color: active ? "var(--fg-1)" : "var(--fg-2)",
        fontWeight: active ? 650 : 500,
        boxShadow: active ? "inset 2px 0 0 0 var(--accent)" : "none",
      }}
    >
      {children}
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
