// 앱 상세 셸 — 탭바(개요 / 작업 공간 …) + 우측 액션, 그 아래 탭별 화면(Outlet).
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

// 시안의 앱 상세 탭 5개.
const TABS = [
  { label: "개요", to: "/dashboard", end: true },
  { label: "작업 공간", to: "/dashboard/workspace" },
  { label: "배포 이력", to: "/dashboard/history" },
  { label: "환경변수", to: "/dashboard/env" },
  { label: "설정", to: "/dashboard/settings" },
];

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

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* ── 탭바 — 높이 48(시안 70), 아래 1px 괘선은 콘텐츠 폭까지만 ── */}
      <div className="kd-page shrink-0">
        <div
          className="flex items-center h-12 gap-2"
          style={{ borderBottom: "1px solid var(--kd-border)" }}
        >
          <nav className="flex items-center gap-2">
            {TABS.map((t) => {
              const active = t.end
                ? location.pathname === t.to
                : location.pathname.startsWith(t.to);
              return (
                <Link
                  key={t.to}
                  to={t.to}
                  className="kd-t-label h-12 px-4 inline-flex items-center no-underline transition-colors"
                  style={{
                    color: active ? "var(--fg-1)" : "var(--fg-2)",
                    fontWeight: active ? 650 : 500,
                    // 탭바 하단 괘선 위에 겹쳐 그린다 — 시안도 밑줄이 괘선을 덮는다
                    boxShadow: active ? "inset 0 -2px 0 0 var(--accent)" : "none",
                  }}
                >
                  {t.label}
                </Link>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-4">
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
          </div>
        </div>
      </div>

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

      {infoOpen && <AppInfoDrawer ctx={ctx} onClose={() => setInfoOpen(false)} />}
    </div>
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
