// 앱 셸(AppLayout)이 폴링하는 Pod 상태를 상단바 브레드크럼이 함께 쓰기 위한 통로.
//
// 상단바는 라우트 바깥(App.jsx)에 있어서 AppLayout이 Outlet으로 내려주는 context가 닿지 않는다.
// 그렇다고 상단바가 /deploy/app/status를 따로 폴링하면 같은 엔드포인트를 두 번 두드리게 된다 —
// 폴링은 AppLayout 한 곳에만 두고, 결과만 여기로 올려 보낸다.
import { createContext, useContext, useMemo, useState } from "react";

const AppShellContext = createContext({ podStatus: null, setPodStatus: () => {} });

export function AppShellProvider({ children }) {
  const [podStatus, setPodStatus] = useState(null);
  const value = useMemo(() => ({ podStatus, setPodStatus }), [podStatus]);
  return <AppShellContext.Provider value={value}>{children}</AppShellContext.Provider>;
}

export const useAppShell = () => useContext(AppShellContext);
