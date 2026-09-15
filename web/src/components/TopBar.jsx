import { useEffect, useRef, useState } from "react";
import { LogOut, Menu, Moon, Sun, X } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import Brand from "./Brand.jsx";
import { useAuth } from "../contexts/AuthContext.jsx";
import { useTheme } from "../contexts/ThemeContext.jsx";

// 시안 내비는 이용 방법 / 문서 / 블로그 / 피드백.
// "이용 방법"(design/라이트모드-시안/19_이용방법.png)과 "블로그"는 아직 화면이 없어 붙이지 않았다.
const NAV_ITEMS = [
  { label: "이용 방법", to: "/how" },
  { label: "문서", to: "/guide" },
  { label: "블로그", to: "/blog" },
  { label: "피드백", to: "/community" },
];

// 등급(admin/root)에게만 보이는 관리자 링크 — /auth/me의 role로 분기
const ADMIN_NAV_ITEM = { label: "관리자", to: "/admin" };
const ADMIN_ROLES = ["admin", "root"];

export default function TopBar({ onLogin }) {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();
  const [menu, setMenu] = useState(false);
  // 현재 페이지 표시 — 시안은 활성 메뉴 글자가 더 진하고 아래 밑줄이 있다.
  const isActive = (to) => pathname === to || pathname.startsWith(`${to}/`);
  // 대시보드는 목록(/apps)과 앱 상세(/dashboard) 두 경로를 함께 가리킨다
  const onDashboard = isActive("/apps") || isActive("/dashboard");
  // 앱 안(작업 화면)에서는 메뉴를 전부 왼쪽 사이드바가 들고 있다(읽는 화면도 셸 안에서
  // 열린다) — 상단바는 브레드크럼과 테마·프로필만 남긴다.
  const inApp = /^\/(dashboard|deploy)/.test(pathname);
  const links = [
    ...NAV_ITEMS,
    ...(user && ADMIN_ROLES.includes(user.role) ? [ADMIN_NAV_ITEM] : []),
  ];

  // 라우트가 바뀌면 모바일 메뉴는 닫는다.
  useEffect(() => setMenu(false), [pathname]);

  return (
    <header
      className="shrink-0"
      style={{ borderBottom: "1px solid var(--kd-border)", background: "var(--nav-bg)" }}
    >
      <div className="flex items-center h-[60px] px-7 gap-4">
        {/* Brand (left) + 앱 화면에서는 브레드크럼 (KoDeploy | 대시보드 / my-api) */}
        <Link to="/" className="flex items-center min-w-0 no-underline">
          <Brand size={19} />
        </Link>
        <Breadcrumb />

        {/* 데스크톱 nav — 메뉴는 전부 오른쪽에 모은다.
            맨 앞이 "대시보드"(가장 자주 가는 곳): 검정 버튼 대신 같은 글씨 크기에 진하고 굵게,
            현재 화면이면 밑줄. 뒤로 세로 괘선 하나를 두고 나머지 링크가 같은 간격으로 이어지며,
            테마·프로필은 여백을 더 주고 맨 끝에 묶는다. 랜딩·앱 화면에서 자리가 같다. */}
        <nav className="ml-auto hidden md:flex items-center shrink-0">
          {user && !inApp && (
            <>
              <Link
                to="/apps"
                aria-current={onDashboard ? "page" : undefined}
                className="kd-t-label transition-colors no-underline relative"
                style={{ color: "var(--fg-strong)", fontWeight: 650 }}
              >
                대시보드
                {onDashboard && <ActiveBar />}
              </Link>
              <span
                aria-hidden
                style={{ width: 1, height: 15, background: "var(--kd-border)", marginInline: 18 }}
              />
            </>
          )}

          <div className="flex items-center gap-[23px]">
            {(inApp ? [] : links).map((item) => {
              const on = isActive(item.to);
              return (
                <Link
                  key={item.label}
                  to={item.to}
                  aria-current={on ? "page" : undefined}
                  className="kd-t-label transition-colors no-underline relative"
                  style={{ color: on ? "var(--fg-strong)" : "var(--fg-2)", fontWeight: on ? 600 : undefined }}
                >
                  {item.label}
                  {on && <ActiveBar />}
                </Link>
              );
            })}
          </div>

          <div className="flex items-center gap-3" style={{ marginLeft: inApp ? 0 : 30 }}>
            <ThemeToggle />
            {loading ? null : user ? (
              <UserMenu />
            ) : (
              <button onClick={onLogin} className="kd-btn-secondary kd-btn-sm flex items-center">
                로그인
              </button>
            )}
          </div>
        </nav>

        {/* 모바일 — 로그인 버튼과 메뉴 버튼만 바에 두고 링크는 아래로 내린다 */}
        <div className="ml-auto md:hidden flex items-center gap-3 shrink-0">
          {loading || user ? null : (
            <button onClick={onLogin} className="kd-btn-secondary kd-btn-sm flex items-center">
              로그인
            </button>
          )}
          <button
            onClick={() => setMenu((v) => !v)}
            aria-label={menu ? "메뉴 닫기" : "메뉴 열기"}
            aria-expanded={menu}
            className="flex items-center justify-center w-8 h-8 text-fg-2 hover:text-fg-1 transition-colors"
          >
            {menu ? <X size={20} strokeWidth={1.7} /> : <Menu size={20} strokeWidth={1.7} />}
          </button>
        </div>
      </div>

      {/* 모바일 메뉴 — 데스크톱과 같은 차례(대시보드가 맨 위)로 내려온다 */}
      {menu && (
        <nav className="md:hidden kd-fade-in" style={{ borderTop: "1px solid var(--kd-border)" }}>
          {(user ? [{ label: "대시보드", to: "/apps" }, ...links] : links).map((item) => (
            <Link
              key={item.label}
              to={item.to}
              aria-current={isActive(item.to) ? "page" : undefined}
              className="kd-t-body-s flex items-center no-underline kd-hoverable"
              style={{
                height: 46,
                paddingInline: 28,
                color: isActive(item.to) ? "var(--fg-strong)" : "var(--fg-2)",
                fontWeight: isActive(item.to) ? 600 : undefined,
                borderBottom: "1px solid var(--kd-border)",
              }}
            >
              {item.label}
            </Link>
          ))}
          <div className="flex items-center gap-4" style={{ height: 46, paddingInline: 22 }}>
            <ThemeToggle withLabel />
            {user && <MobileSignOut />}
          </div>
        </nav>
      )}
    </header>
  );
}

// 모바일 메뉴 안의 계정 줄 — 데스크톱에서는 아바타 드롭다운이 같은 일을 한다.
function MobileSignOut() {
  const { user, logout } = useAuth();
  return (
    <button
      onClick={logout}
      className="kd-t-body-s ml-auto inline-flex items-center gap-2 text-fg-2 hover:text-fg-1 transition-colors"
    >
      <LogOut size={15} strokeWidth={1.7} />
      로그아웃 · {user.login}
    </button>
  );
}

// 활성 메뉴 밑줄
function ActiveBar() {
  return (
    <span
      aria-hidden
      style={{ position: "absolute", left: 0, right: 0, bottom: -8, height: 2, background: "var(--fg-strong)" }}
    />
  );
}

// 라이트/다크 토글 — 시안 상단바의 달 아이콘 자리.
function ThemeToggle({ withLabel }) {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      onClick={toggle}
      aria-label={dark ? "라이트 모드로 전환" : "다크 모드로 전환"}
      title={dark ? "라이트 모드" : "다크 모드"}
      className="kd-t-body-s flex items-center gap-2.5 h-8 text-fg-2 hover:text-fg-1 transition-colors"
      style={{ paddingInline: withLabel ? 6 : 0, width: withLabel ? undefined : 32, justifyContent: withLabel ? undefined : "center" }}
    >
      {dark ? <Sun size={18} strokeWidth={1.6} /> : <Moon size={18} strokeWidth={1.6} />}
      {withLabel && (dark ? "라이트 모드" : "다크 모드")}
    </button>
  );
}

// 앱 화면(/dashboard…)에서만 보이는 현재 위치 — 상단 메뉴가 비는 자리라 여기가 돌아가는 길이다.
function Breadcrumb() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  if (!user?.app_name || !/^\/(dashboard|deploy)/.test(pathname)) return null;
  return (
    <div className="flex items-center min-w-0" style={{ marginLeft: 18 }}>
      <span aria-hidden style={{ width: 1, height: 15, background: "var(--kd-border)" }} />
      <Link
        to="/apps"
        className="kd-t-label text-fg-2 hover:text-fg-1 no-underline transition-colors"
        style={{ marginLeft: 20 }}
      >
        대시보드
      </Link>
      <span className="kd-t-label text-fg-4" style={{ marginInline: 10 }}>
        /
      </span>
      <span className="kd-t-label text-fg-1 truncate" style={{ fontWeight: 600 }}>
        {user.app_name}
      </span>
    </div>
  );
}

// avatar + login + dropdown(로그아웃). 외부 클릭 / Esc로 닫힘.
function UserMenu() {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const initial = (user.login || "?").slice(0, 1).toUpperCase();

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 transition-opacity hover:opacity-75"
        title={user.login}
      >
        {user.avatar_url ? (
          <img
            src={user.avatar_url}
            alt=""
            className="w-[26px] h-[26px] rounded-full"
            style={{ border: "1px solid var(--kd-border)" }}
          />
        ) : (
          <span
            className="w-[26px] h-[26px] rounded-full flex items-center justify-center text-[11px]"
            style={{
              background: "var(--btn-primary-bg)",
              color: "var(--btn-primary-fg)",
              fontWeight: 590,
            }}
          >
            {initial}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-[calc(100%+8px)] w-40 py-1 kd-fade-in"
          style={{
            background: "var(--dropdown-bg)",
            border: "1px solid var(--kd-border)",
            boxShadow: "var(--shadow-pop)",
            zIndex: 30,
          }}
        >
          <div
            className="kd-t-micro px-3 py-2 text-fg-3 truncate"
            style={{ borderBottom: "1px solid var(--line-2)" }}
          >
            {user.login}
          </div>
          <button
            onClick={() => {
              setOpen(false);
              logout();
            }}
            className="kd-hoverable kd-t-caption w-full flex items-center gap-2 px-3 py-2 text-fg-2 hover:text-fg-1 transition-colors"
          >
            <LogOut size={13} strokeWidth={1.8} className="text-fg-3" />
            로그아웃
          </button>
        </div>
      )}
    </div>
  );
}
