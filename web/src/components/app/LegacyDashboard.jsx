// 옛 /dashboard/… 주소 → 내 첫 앱의 같은 화면(/apps/:id/…). 앱이 없으면 대시보드 목록으로 보낸다.
import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { listApps } from "../../api/deploy.js";
import { useAuth } from "../../contexts/AuthContext.jsx";

export default function LegacyDashboard() {
  const { user, loading } = useAuth();
  const { pathname, search } = useLocation();
  const [target, setTarget] = useState(null);

  useEffect(() => {
    if (loading) return;
    if (!user) {
      setTarget("/");
      return;
    }
    let cancelled = false;
    listApps()
      .then((apps) => {
        if (cancelled) return;
        const rest = pathname.replace(/^\/dashboard/, "");
        setTarget(apps?.length ? `/apps/${apps[0].id}${rest}${search}` : "/apps");
      })
      .catch(() => !cancelled && setTarget("/apps"));
    return () => {
      cancelled = true;
    };
  }, [loading, user, pathname, search]);

  return target ? <Navigate to={target} replace /> : null;
}
