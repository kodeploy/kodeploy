// 내 앱 — design/라이트모드-시안/03_내_앱_목록.png 기준. 로그인 후 앱으로 들어가는 관문 화면.
//
// 이 서비스는 1유저 = 1앱이고 슬롯이 둘(서버 / 정적)이라 "목록"이라기보다 슬롯 두 칸이다.
// 그래서 카드 격자 대신 시안처럼 섹션 세 개(앱 서버 / 프론트엔드 / 최근 배포)를 세로로 쌓고,
// 각 섹션은 굵은 제목 + 괘선 + 행 하나로 끝낸다.
//
// 이 라우트는 AppLayout(탭 셸) 밖이라 폴링해 줄 부모가 없다 — 여기서 직접 폴링한다.
// 빌드와 앱 상태를 한 tick에서 같이 받고, 진행 중인 빌드가 있으면 주기를 줄인다(위젯과 같은 규칙).
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
import { getAppStatus, listBuilds } from "../../api/deploy.js";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
import { STYLES_BUILD, STYLES_ENV } from "../StatusBadge.jsx";
import DeleteAppModal from "../DeleteAppModal.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
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

export default function AppsList() {
  const { user, loading, openLogin } = useAuth();
  const [builds, setBuilds] = useState([]);
  // { status, started_at, server:{status}, site:{status}|null } — 슬롯별 상태의 진실원
  const [slotStatus, setSlotStatus] = useState(null);
  const [showDelete, setShowDelete] = useState(false);

  const deployed = Boolean(user?.app_name);

  // 빌드 + 앱 상태 한 tick 폴링. 진행 중 빌드가 있으면 2.5s, 없으면 10s.
  useEffect(() => {
    if (!deployed) {
      setBuilds([]);
      setSlotStatus(null);
      return;
    }
    let cancelled = false;
    let timer;
    const tick = async () => {
      let active = false;
      try {
        const [list, status] = await Promise.all([listBuilds(), getAppStatus()]);
        if (!cancelled) {
          setBuilds(list || []);
          setSlotStatus(status || null);
          active = (list || []).some((b) => ACTIVE.has(b.status));
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
  }, [deployed, user?.id, user?.app_name]);

  // 슬롯별 최신 빌드 — AppLayout과 같은 판별식
  const serverBuild = builds.find((b) => b.runtime !== "static" && b.kind !== "env_change");
  const staticBuild = builds.find((b) => b.runtime === "static");

  // 정적 슬롯 유무 — site_enabled가 진실원이고, 옛 응답을 위해 슬롯 상태/빌드로 보강한다.
  const hasSite = Boolean(user?.site_enabled || slotStatus?.site || staticBuild);
  const appName = user?.app_name;
  const serverHost = hasSite ? `${appName}-api.kodeploy.com` : `${appName}.kodeploy.com`;
  const siteHost = `${appName}.kodeploy.com`;

  // Pod이 아직 없는 슬롯(missing)이라도 그 슬롯 빌드가 돌고 있으면 "빌드 중"으로 —
  // 빌드가 긴 런타임에서 행이 "중지"로 보이는 오해 방지 (위젯과 같은 규칙).
  const activeServerBuild = builds.some((b) => b.runtime !== "static" && ACTIVE.has(b.status));
  const activeStaticBuild = builds.some((b) => b.runtime === "static" && ACTIVE.has(b.status));
  const rawServerStatus = slotStatus?.server?.status || slotStatus?.status || null;
  const serverStatus =
    rawServerStatus === "missing" && activeServerBuild ? "building" : rawServerStatus;
  const rawSiteStatus = slotStatus?.site?.status || null;
  const siteStatus =
    rawSiteStatus === "missing" && activeStaticBuild ? "building" : rawSiteStatus;

  // 최근 배포 — #N은 kind="build"만 카운트(env_change는 번호 없음). 목록은 최신순.
  const latest = builds[0] || null;
  const latestNumber = useMemo(() => {
    if (!latest || (latest.kind || "build") === "env_change") return null;
    return builds.filter((b) => (b.kind || "build") !== "env_change").length;
  }, [builds, latest]);

  if (loading) return null;                          // /auth/me 첫 응답 전 깜빡임 방지

  return (
    <div className="kd-page" style={{ paddingTop: 48, paddingBottom: 64 }}>
      {/* ── 페이지 헤더 (시안 제목 잉크 y144 · 설명 y198) ── */}
      <h1 className="kd-t-display text-fg-1">대시보드</h1>
      <p className="kd-t-lead text-fg-2" style={{ marginTop: 2 }}>
        배포한 앱과 프론트엔드를 관리하세요.
      </p>

      {!deployed ? (
        <FirstDeploy loggedIn={Boolean(user)} onLogin={openLogin} />
      ) : (
        <>
          {/* ── 앱 서버 (시안 제목 y285 · 괘선 y327 · 행 y360-422 · 마감 괘선 y453) ── */}
          <Section title="앱 서버" first>
            {serverBuild ? (
              <SlotRow
                to="/dashboard"
                icon={SquareTerminal}
                name={appName}
                status={serverStatus}
                meta={[
                  RUNTIME_LABEL[serverBuild.runtime] || serverBuild.runtime,
                  serverBuild.branch,
                ]}
                host={serverHost}
                action={
                  <>
                    <Link
                      to="/dashboard/workspace"
                      className="kd-btn-primary kd-btn-lg inline-flex items-center gap-2 no-underline"
                    >
                      작업 공간 열기
                    </Link>
                    <AppMenu onDelete={() => setShowDelete(true)} />
                  </>
                }
              />
            ) : (
              /* 정적 단독 배포(runtime "none") — 서버 슬롯이 비어 있는 경우 */
              <EmptyRow
                icon={SquareTerminal}
                title="앱 서버가 아직 없어요."
                desc="깃허브 저장소를 연결해 서버를 배포할 수 있어요."
                to="/deploy"
                cta="앱 서버 배포"
              />
            )}
          </Section>

          {/* ── 프론트엔드 (시안 제목 y527 · 괘선 y569 · 행 y603-653 · 마감 괘선 y687) ── */}
          <Section title="프론트엔드">
            {staticBuild ? (
              <SlotRow
                to="/dashboard"
                icon={AppWindow}
                name={appName}
                status={siteStatus}
                meta={[RUNTIME_LABEL.static, staticBuild.branch]}
                host={siteHost}
                action={
                  <Link
                    to="/deploy/frontend"
                    className="kd-btn-secondary kd-btn-lg inline-flex items-center no-underline"
                  >
                    프론트엔드 다시 배포
                  </Link>
                }
              />
            ) : (
              <EmptyRow
                icon={AppWindow}
                title="프론트엔드가 아직 없어요."
                desc="앱과 연결할 웹 화면을 배포할 수 있어요."
                to="/deploy/frontend"
                cta="프론트엔드 배포"
              />
            )}
          </Section>

          {/* ── 최근 배포 (시안 제목 y758 · 괘선 y799 · 행 y825-841 · 마감 괘선 y867) ── */}
          <Section title="최근 배포">
            <div
              className="flex items-center gap-3 flex-wrap"
              style={{
                paddingBlock: 21,
                paddingLeft: 8,
                borderBottom: "1px solid var(--kd-border)",
              }}
            >
              {latest ? (
                <>
                  {latestNumber != null && (
                    <span className="kd-t-body kd-strong text-fg-1 tabular-nums">
                      #{latestNumber}
                    </span>
                  )}
                  <span className="kd-t-body text-fg-2">{buildLabel(latest)}</span>
                  <span className="kd-t-body text-fg-4">·</span>
                  <span className="kd-t-body text-fg-2 tabular-nums">
                    {dayTime(latest.created_at)}
                  </span>
                </>
              ) : (
                <span className="kd-t-body text-fg-2">아직 배포 기록이 없어요.</span>
              )}
              <Link
                to="/dashboard/history"
                className="kd-t-body text-fg-2 hover:text-fg-1 transition-colors no-underline
                           ml-auto inline-flex items-center gap-2 shrink-0"
              >
                배포 이력 보기
              </Link>
            </div>
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

      {showDelete && (
        <DeleteAppModal appName={appName} onClose={() => setShowDelete(false)} />
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
function Section({ title, first, children }) {
  return (
    <section style={{ marginTop: first ? 52 : 66 }}>
      <h2 className="kd-t-display-s text-fg-1">{title}</h2>
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
function AppMenu({ onDelete }) {
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
          <MenuLink to="/dashboard" label="앱 개요" onDone={() => setOpen(false)} />
          <MenuLink to="/dashboard/history" label="배포 이력" onDone={() => setOpen(false)} />
          <MenuLink to="/dashboard/env" label="환경변수" onDone={() => setOpen(false)} />
          <MenuLink to="/dashboard/settings" label="설정" onDone={() => setOpen(false)} />
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
