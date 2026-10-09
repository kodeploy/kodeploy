// /apps/:appId/... 아래 모든 화면이 공유하는 "지금 보고 있는 앱".
//
// 앱 하나를 가리키는 일은 주소가 한다 — 탭 두 개로 앱 둘을 열어도 서로 섞이지 않는다.
// 여기서는 그 앱을 서버에서 한 번 찾아 context로 내려주고, api/deploy.js의 요청 경로도 그 앱으로 맞춘다
// (/deploy/... 요청이 /apps/{id}/deploy/... 로 간다). 화면 쪽은 user.app_name 대신 useCurrentApp()을 쓴다.
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Link, Navigate, Outlet, useParams } from "react-router-dom";
import { listApps, setActiveApp } from "../../api/deploy.js";
import { useAppShell } from "../../contexts/AppShellContext.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { can } from "../../lib/roles.js";

const CurrentAppContext = createContext(null);

// { app, base, refreshApp } — app은 { id, name, site_enabled, custom_domain, role, ... }
export function useCurrentApp() {
  const ctx = useContext(CurrentAppContext);
  if (!ctx) throw new Error("useCurrentApp은 /apps/:appId 화면 안에서만 사용");
  return ctx;
}

// 앱 화면 안이면 { app, base, refreshApp }, 밖이면 null — 앱 안팎에서 모두 뜨는 화면(배포 마법사 등)이 쓴다.
export function useOptionalApp() {
  return useContext(CurrentAppContext);
}

// 이 화면은 need 이상인 단계만 — 모자라면 앱 개요로 돌려보낸다 (서버도 같은 규칙으로 403을 준다).
export function RequireRole({ need, children }) {
  const { app, base } = useCurrentApp();
  return can(app, need) ? children : <Navigate to={base} replace />;
}

export default function AppScope() {
  const { appId } = useParams();
  const { user, loading: authLoading, openLogin } = useAuth();
  const [app, setApp] = useState(null);
  const [state, setState] = useState("loading"); // loading | ready | missing
  const { setAppName } = useAppShell();

  // 렌더 중에 먼저 맞춘다 — 자식들의 첫 요청(effect)이 이 앱 경로로 나가야 한다.
  setActiveApp(appId);
  useEffect(() => () => setActiveApp(null), []);

  // 상단바 브레드크럼에 앱 이름을 알린다 (상단바는 이 라우트 바깥이라 context가 닿지 않는다)
  useEffect(() => {
    setAppName(app?.name || null);
    return () => setAppName(null);
  }, [app?.name, setAppName]);

  const load = useCallback(async () => {
    try {
      const apps = await listApps();
      const found = (apps || []).find((a) => a.id === appId);
      setApp(found || null);
      setState(found ? "ready" : "missing");
    } catch (err) {
      if (err.status === 401) openLogin?.();
      setState("missing");
    }
  }, [appId, openLogin]);

  useEffect(() => {
    setState("loading");
    if (authLoading || !user) return;
    load();
  }, [authLoading, user?.id, load]);

  if (authLoading || state === "loading") return null;
  if (!user) return <Gate title="로그인이 필요해요" desc="로그인하면 내 앱을 볼 수 있어요." />;
  if (state === "missing") {
    return <Gate title="앱을 찾을 수 없어요" desc="삭제됐거나 볼 수 있는 앱이 아니에요." />;
  }
  return (
    <CurrentAppContext.Provider value={{ app, base: `/apps/${appId}`, refreshApp: load }}>
      <Outlet />
    </CurrentAppContext.Provider>
  );
}

function Gate({ title, desc }) {
  return (
    <div className="flex-1 overflow-auto scroll-thin">
      <div className="kd-page" style={{ paddingTop: 120, maxWidth: 620 }}>
        <h2 className="kd-t-title text-fg-1 mb-3">{title}</h2>
        <p className="kd-t-body text-fg-2 mb-8">{desc}</p>
        <Link to="/apps" className="kd-btn-primary kd-btn-lg inline-flex items-center no-underline">
          대시보드로
        </Link>
      </div>
    </div>
  );
}
