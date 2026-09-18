// 앱 상세 · 작업 공간 탭 — design/라이트모드-시안/10~13 기준.
//
// 화면은 뷰 탭 줄 하나 + 카드 하나다. 카드 안에 뷰 하나가 열린다.
// 앱 이름·실행 상태는 상단바가, 앱 액션(서비스 열기·앱 정보·재배포)은 사이드바가 맡는다.
//   터미널·로그 — 좌 터미널(대상 선택) / 우 런타임 로그   ← 들어오면 바로 이 뷰
//   데이터베이스 — SQL 편집기 + 조회 결과 (또는 DB 터미널)
//   스토리지    — 파일 목록 + 미리보기 (use_storage일 때만 탭이 뜬다)
//   모니터링    — 현재 사용량 표
//
// 치수 주석의 숫자는 시안 원본 px, 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
//   카드 폭 1426→982(kd-page 내부 폭) · 뷰 탭 줄 44 · 패널 헤더 62→42(--row-lg)
//   터미널:로그 = 775:649 → 55:45 · 스토리지 목록:미리보기 = 802:622 → 56:44
//
// 뷰 전환은 unmount가 아니라 display 토글이다(Pane.jsx가 탭을 다루는 방식과 같다).
// 터미널 WebSocket·로그 스크롤·입력 중인 SQL이 탭을 옮겨도 살아 있어야 하기 때문.
// 대신 한 번도 안 연 뷰는 아예 마운트하지 않는다 — 열지도 않은 터미널이 소켓을 잡으면 안 된다.
//
// 예전 작업 공간의 패널 추가·분할·크기 조절은 없애지 않고 [레이아웃 ▾] 메뉴로 옮겼다.
// 분할(좌우/위아래)은 두지 않는다. 대신 각 칸의 "크게 보기"가 그 칸을 카드 전체로 넓힌다 —
// (로그·터미널·모니터링·스토리지)와 중첩 분할을 그대로 쓴다.
//
// 데이터는 전부 실제 API다. 시안에 있지만 백엔드에 없는 값(DB 이름 "app_db",
// DB/Redis의 CPU·메모리)은 지어내지 않고 생략하거나 "—"로 둔다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useOutletContext, useSearchParams } from "react-router-dom";
import {
  AppWindow,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
  Copy,
  Database,
  Download,
  File as FileIcon,
  FileCode,
  FileText,
  Image as ImageIcon,
  Layers,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Table2,
  TerminalSquare,
  Trash2,
  X,
} from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import {
  createSavedQuery,
  deleteSavedQuery,
  deleteStorageObject,
  getAppLogs,
  getAppMetrics,
  listSavedQueries,
  listStorageObjects,
  readStorageObject,
  runDbQuery,
  updateSavedQuery,
} from "../../api/deploy.js";
import { parseDate } from "../../lib/format.js";
import {
  historyKey,
  loadHistory,
  makeEntry,
  pushEntry,
  saveHistory,
} from "../../lib/sqlHistory.js";
import { tokenizeSql } from "../../lib/sqlHighlight.js";
import { TERM_FONT_FAMILY, xtermTheme } from "../../lib/xtermTheme.js";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { useTheme } from "../../contexts/ThemeContext.jsx";
import StatusBadge from "../StatusBadge.jsx";
import DbTerminalPanel from "../panels/DbTerminalPanel.jsx";
import MetricsView from "./MetricsView.jsx";

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
const WS_BASE = API_BASE.replace(/^http/, "ws");

const ACTIVE = new Set(["queued", "building", "built", "deploying"]);
const DB_LABEL = { mysql: "MySQL", postgres: "PostgreSQL" };

// 개요의 "바로 작업하기"(?panel=…)가 가리키는 패널 → 이 화면의 뷰.
const PANEL_TO_VIEW = {
  terminal: "console",
  logs: "console",
  db: "db",
  storage: "storage",
  monitoring: "metrics",
};

// 사용자가 고르지 않았을 때의 기본 빌드 — running > 진행 중 > 최신 (기존 동작 그대로)
function pickAutoBuild(builds) {
  if (!builds.length) return null;
  return (
    builds.find((b) => b.status === "running") ||
    builds.find((b) => ACTIVE.has(b.status)) ||
    builds[0]
  );
}

const pad2 = (n) => String(n).padStart(2, "0");
const clockOf = (d) => (d ? `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}` : "");

// "2026-09-14 14:20" — 표에 들어가는 절대 시각 (상대 시각은 개요·이력 쪽 규칙)
function fmtDateTime(iso) {
  const d = parseDate(iso);
  if (!d) return "—";
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// panels/StoragePanel.jsx의 fmtBytes 그대로
function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// ────────────────────────────────────────────────────────────────────────────
// 분할 리사이즈 — PanelWorkspace.jsx의 드래그 로직을 훅으로 옮긴 것.
// (비율 20~80% 클램프, 드래그 중 body 커서/선택 잠금까지 동일)
// ────────────────────────────────────────────────────────────────────────────
function useDragRatio(initial) {
  const [ratio, setRatio] = useState(initial);
  const containerRef = useRef(null);
  const axisRef = useRef(null);

  useEffect(() => {
    const onMove = (e) => {
      if (!axisRef.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const v =
        axisRef.current === "h"
          ? ((e.clientX - rect.left) / rect.width) * 100
          : ((e.clientY - rect.top) / rect.height) * 100;
      setRatio(Math.min(80, Math.max(20, v)));
    };
    const onUp = () => {
      if (!axisRef.current) return;
      axisRef.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, []);

  const startDrag = (axis) => (e) => {
    e.preventDefault();
    axisRef.current = axis;
    document.body.style.cursor = axis === "h" ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
  };

  return { ratio, containerRef, startDrag };
}

function Divider({ axis, onMouseDown }) {
  return (
    <div
      onMouseDown={onMouseDown}
      className={`shrink-0 ${axis === "h" ? "w-[5px] cursor-col-resize" : "h-[5px] cursor-row-resize"}`}
      style={{ background: "var(--kd-border)" }}
      title="드래그해서 크기 조절"
    />
  );
}

// ────────────────────────────────────────────────────────────────────────────
// 화면 본체
// ────────────────────────────────────────────────────────────────────────────
export default function AppWorkspace() {
  const { builds, serverBuild, slotStatus } = useOutletContext();
  const [searchParams] = useSearchParams();

  // 빌드 선택 — ?build=<id> 우선, 없으면 자동 선택 (기존 동작 유지)
  const pinned = searchParams.get("build");
  const build = builds.find((b) => b.build_id === pinned) || pickAutoBuild(builds);

  // 스토리지/Redis 노출은 최신 **서버** 빌드 기준. builds[0]은 정적 슬롯 빌드일 수 있다.
  const storageEnabled = serverBuild?.use_storage ?? false;
  const redisEnabled = serverBuild?.use_redis ?? false;
  const dbType = serverBuild?.db_type && serverBuild.db_type !== "none" ? serverBuild.db_type : null;

  const views = useMemo(
    () => [
      { id: "console", label: "터미널·로그" },
      { id: "db", label: "데이터베이스" },
      ...(storageEnabled ? [{ id: "storage", label: "스토리지" }] : []),
      { id: "metrics", label: "모니터링" },
    ],
    [storageEnabled],
  );

  const seed = PANEL_TO_VIEW[searchParams.get("panel")] || "console";
  const [view, setView] = useState(seed === "storage" && !storageEnabled ? "console" : seed);
  // 한 번이라도 연 뷰만 마운트해 둔다 (이후로는 display로만 감춘다).
  const [opened, setOpened] = useState(() => new Set([view]));
  const openView = (id) => {
    setView(id);
    setOpened((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  };

  if (!build) {
    return (
      <div className="kd-page flex-1 flex items-center justify-center">
        <span className="kd-t-body-s text-fg-3">불러오는 중…</span>
      </div>
    );
  }

  return (
    <div
      className="kd-page flex-1 min-h-0 flex flex-col"
      // 탭 줄이 이 화면 안에 있으므로 위 여백은 0이다(줄 자체가 높이 44를 갖는다).
      style={{ paddingTop: 0, paddingBottom: 24 }}
    >
      {/* ── 뷰 탭 줄 — 아래 괘선은 두지 않는다(바로 밑 카드 테두리와 두 줄로 겹쳐 보였다).
          여백은 밑줄이 아니라 글자를 기준으로 맞춘다: 상단바 ~ 카드 58px 안에서 글자 위 23.5 /
          글자 아래 22.5 (밑줄은 글자 아래 6px에 얹히는 덤이라 가운데 계산에서 뺀다). ── */}
      <div className="shrink-0 flex items-end" style={{ height: 48 }}>
        {/* 탭 줄이 다 안 들어가면(아주 좁은 화면) 잘리는 대신 옆으로 민다 */}
        <nav
          className="flex items-end gap-6 sm:gap-10 shrink-0 overflow-x-auto scroll-thin"
          style={{ paddingBottom: 0, maxWidth: "100%" }}
        >
          {views.map((v) => (
            <button
              key={v.id}
              onClick={() => openView(v.id)}
              aria-pressed={view === v.id}
              className="kd-t-body-s kd-pick-x flex flex-col items-center shrink-0"
              style={{ color: "var(--fg-2)", fontWeight: 500 }}
            >
              <span className="kd-pick-name">{v.label}</span>
              {/* 활성 밑줄 — 세로 메뉴의 선과 같은 2px 잉크 바, 글자 아래 6px */}
              <span className="kd-pick-bar" style={{ marginTop: 6 }} />
            </button>
          ))}
        </nav>
      </div>

      <div
        className="kd-card flex-1 min-h-0 flex flex-col overflow-hidden"
        style={{ marginTop: 10 }}
      >
        {/* ── 본문 — 한 번에 한 뷰. 분할 대신 각 칸의 "크게 보기"로 넓힌다. ── */}
        <div className="flex-1 min-h-0 flex">
          <div className="flex-1 min-h-0 min-w-0 flex flex-col overflow-hidden">
            {views.map((v) =>
              opened.has(v.id) ? (
                <div
                  key={v.id}
                  className="flex-1 min-h-0 min-w-0 flex-col"
                  style={{ display: view === v.id ? "flex" : "none" }}
                >
                  {v.id === "console" && (
                    <ConsoleView build={build} dbType={dbType} redisEnabled={redisEnabled} />
                  )}
                  {v.id === "db" && <DatabaseView dbType={dbType} />}
                  {v.id === "storage" && <StorageView />}
                  {v.id === "metrics" && <MetricsView slotStatus={slotStatus} />}
                </div>
              ) : null,
            )}
          </div>

        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// 공통 조각
// ────────────────────────────────────────────────────────────────────────────

// 패널 위 툴바 한 줄 — 시안의 62px(=42 CSS) 행. dark면 터미널 잉크 면 위에 얹는다.
// 잉크 면은 두 테마 모두 어두우므로 그 위의 헤어라인만 흰색 알파를 쓴다(대응 토큰 없음).
function PaneBar({ dark, children }) {
  return (
    <div
      className="shrink-0 flex items-center gap-3 overflow-x-auto scroll-thin"
      style={{
        height: "var(--row-lg)",
        paddingInline: 16,
        borderBottom: `1px solid ${dark ? "rgba(255,255,255,0.10)" : "var(--kd-border)"}`,
      }}
    >
      {children}
    </div>
  );
}

// 아이콘만 있는 보조 버튼 (넓게 보기, 더보기 등)
function IconBtn({ icon: Icon, label, onClick, dark }) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="shrink-0 inline-flex items-center justify-center"
      style={{ width: 24, height: 24, color: dark ? "var(--term-fg)" : "var(--fg-3)", opacity: 0.75 }}
      onMouseEnter={(e) => (e.currentTarget.style.opacity = 1)}
      onMouseLeave={(e) => (e.currentTarget.style.opacity = 0.75)}
    >
      <Icon size={15} strokeWidth={1.8} />
    </button>
  );
}

// 작은 드롭다운 — 터미널 대상("앱 서버 ⌄")과 로그 필터("전체 ⌄")에 쓴다.
// 메뉴는 body로 포털해 fixed로 띄운다 — PaneBar가 overflow-x-auto라 안에서 absolute로 띄우면
// 42px 툴바 높이에서 잘려 아래 터미널·로그 뒤로 숨는다.
function MiniSelect({ value, options, onChange, dark, width }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const wrapRef = useRef(null);
  const menuRef = useRef(null);
  const current = options.find((o) => o.id === value);

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = wrapRef.current?.getBoundingClientRect();
      if (r) setPos({ left: r.left, top: r.bottom + 4 });
    };
    place();
    const onDown = (e) =>
      !wrapRef.current?.contains(e.target) && !menuRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  return (
    <div ref={wrapRef} className="relative shrink-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="kd-t-label inline-flex items-center justify-between gap-1.5"
        style={{
          // 잉크 면(터미널 헤더) 위에서는 테두리 없이 글자폭에 맞춰 — 시안의 "앱 서버 ⌄"
          width: dark ? "auto" : width,
          height: 30,
          paddingInline: dark ? 0 : 10,
          borderRadius: 4,
          border: dark ? "none" : "1px solid var(--kd-border)",
          background: dark ? "transparent" : "var(--kd-surface)",
          color: dark ? "var(--term-fg)" : "var(--fg-1)",
        }}
      >
        <span className="truncate">{current?.label || "—"}</span>
        <ChevronDown size={15} strokeWidth={1.7} className="shrink-0" />
      </button>
      {open && pos && createPortal(
        <div
          ref={menuRef}
          className="fixed z-50 kd-card"
          style={{ left: pos.left, top: pos.top, minWidth: Math.max(width || 0, 140), paddingBlock: 6 }}
        >
          {options.map((o) => (
            <button
              key={o.id}
              onClick={() => {
                onChange(o.id);
                setOpen(false);
              }}
              className="kd-t-label w-full text-left flex items-center"
              style={{
                height: "var(--row-md)",
                paddingInline: 14,
                color: "var(--fg-1)",
                background: o.id === value ? "var(--sel-soft)" : "transparent",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--sel)")}
              onMouseLeave={(e) =>
                (e.currentTarget.style.background = o.id === value ? "var(--sel-soft)" : "transparent")
              }
            >
              {o.label}
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}

// 표 헤더 셀 — 클릭하면 정렬(오름 → 내림 → 해제). DbConsolePanel.ResultTable과 같은 규칙.
function SortHeader({ label, active, dir, onClick }) {
  return (
    <button
      onClick={onClick}
      className="kd-t-micro inline-flex items-center gap-1 min-w-0"
      style={{ color: active ? "var(--fg-1)" : "var(--fg-3)" }}
      title="클릭하면 정렬 (오름 → 내림 → 해제)"
    >
      <span className="truncate">{label}</span>
      {active ? (
        dir === "asc" ? (
          <ChevronUp size={15} strokeWidth={1.9} />
        ) : (
          <ChevronDown size={15} strokeWidth={1.9} />
        )
      ) : (
        <ChevronsUpDown size={15} strokeWidth={1.5} style={{ color: "var(--fg-4)" }} />
      )}
    </button>
  );
}

// 정렬 토글 (오름 → 내림 → 해제)
function nextSort(prev, key) {
  if (!prev || prev.key !== key) return { key, dir: "asc" };
  if (prev.dir === "asc") return { key, dir: "desc" };
  return null;
}

// 카드 안 툴바의 밑줄 탭 — 결과 영역 탭(조회 결과/최근 실행/저장된 쿼리)과 스토리지의
// 목록/격자가 같이 쓴다.
//
// 밑줄을 흐름에서 빼는 것이 이 조각의 요점이다. 글자·간격·선을 한 덩어리로 세면 그 덩어리
// (29px)의 가운데가 줄 가운데에 맞춰져 정작 글자는 위로 4.5px 밀린다. 줄에서 눈이 잡는 기준은
// 글자지 밑줄이 아니므로, 밑줄은 글자 아래 6px에 얹히는 덤으로 두고 가운데 계산에서 뺀다
// (화면 탭 줄이 "밑줄은 덤"으로 여백을 재는 것과 같은 규칙).
function UnderlineTab({ label, count, active, onClick }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className="kd-t-body-s kd-pick-x flex items-center shrink-0"
      style={{ color: "var(--fg-2)", fontWeight: 500 }}
    >
      <span className="relative inline-flex items-baseline gap-1.5 whitespace-nowrap">
        <span className="kd-pick-name">{label}</span>
        {count > 0 && <span className="kd-t-caption text-fg-3 tabular-nums">{count}</span>}
        <span className="kd-pick-bar absolute" style={{ left: 0, top: "100%", marginTop: 6 }} />
      </span>
    </button>
  );
}

function Centered({ children }) {
  return (
    <div className="flex-1 min-h-0 flex items-center justify-center" style={{ padding: 20 }}>
      <span className="kd-t-body-s text-fg-3">{children}</span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// 1) 터미널·로그
// ────────────────────────────────────────────────────────────────────────────
function ConsoleView({ build, dbType, redisEnabled }) {
  const { ratio, containerRef, startDrag } = useDragRatio(55);
  const [zoom, setZoom] = useState(null); // null | "term" | "log" — 한쪽만 크게

  // 넓게 보기에서도 반대쪽을 unmount하지 않는다(터미널 세션 유지). display만 끈다.
  const side = (me) => {
    if (zoom === me) return { flex: 1, display: "flex" };
    if (zoom) return { display: "none" };
    return me === "term"
      ? { width: `${ratio}%`, flexShrink: 0, display: "flex" }
      : { flex: 1, display: "flex" };
  };

  return (
    <div ref={containerRef} className="flex-1 min-h-0 flex">
      <div className="min-h-0 min-w-0 flex-col" style={side("term")}>
        <TerminalSide
          dbType={dbType}
          redisEnabled={redisEnabled}
          zoomed={zoom === "term"}
          onZoom={() => setZoom((z) => (z === "term" ? null : "term"))}
        />
      </div>
      {!zoom && <Divider axis="h" onMouseDown={startDrag("h")} />}
      <div className="min-h-0 min-w-0 flex-col" style={side("log")}>
        <LogSide
          build={build}
          zoomed={zoom === "log"}
          onZoom={() => setZoom((z) => (z === "log" ? null : "log"))}
        />
      </div>
    </div>
  );
}

// ── 터미널 ──────────────────────────────────────────────────────────────────
// xterm 구성·리사이즈 프로토콜·테마 라이브 갱신은 panels/TerminalPanel.jsx와 같은 코드다.
// 따로 두는 이유는 둘: (1) 시안의 패널 헤더가 하나뿐이라 패널이 자체 헤더를 또 그리면 안 되고,
// (2) 헤더의 "연결됨" 표시에 소켓 상태가 필요한데 기존 패널은 그걸 터미널 버퍼에만 쓴다.
function WorkspaceTerminal({ wsPath, onStatus }) {
  const { theme } = useTheme();
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const statusRef = useRef(onStatus);
  statusRef.current = onStatus; // 콜백이 바뀌어도 재접속하지 않게 ref로 받는다

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: TERM_FONT_FAMILY,
      theme: xtermTheme(theme),
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    // 웹폰트가 아직 안 붙은 상태로 open()하면 xterm이 대체 글꼴로 글자 폭을 재서 격자가
    // 어긋난다(한 칸 폭이 실제와 다르면 mysql 표 테두리가 깨져 보인다). 폰트가 준비되면
    // 한 번 더 재는 것으로 맞춘다 — dispose 뒤에 늦게 도착할 수 있어 플래그로 막는다.
    let disposed = false;
    document.fonts?.ready.then(() => {
      if (!disposed) fitAddon.fit();
    });

    const ws = new WebSocket(WS_BASE + wsPath);

    // 현재 xterm 크기를 백엔드로 보내 파드 pty 크기를 맞춤 → 쉘 출력이 실제 폭으로 정렬
    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ __resize: { cols: term.cols, rows: term.rows } }));
      }
    };

    ws.onopen = () => {
      statusRef.current?.("open");
      sendResize();
    };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = (e) => {
      statusRef.current?.("closed");
      term.write(`\r\n\x1b[1;31m연결 종료${e.reason ? `: ${e.reason}` : ""}\x1b[0m\r\n`);
    };
    ws.onerror = () => {
      statusRef.current?.("error");
      term.write("\r\n\x1b[1;31m연결 실패\x1b[0m\r\n");
    };
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });
    term.onResize(() => sendResize());

    // 숨겨진(display:none) 동안은 0x0이라 fit하면 cols/rows가 최소값으로 눌린다.
    // 다시 보일 때 ResizeObserver가 한 번 더 불리므로 그때 맞추면 된다.
    const ro = new ResizeObserver(() => {
      const el = containerRef.current;
      if (!el || el.offsetWidth === 0 || el.offsetHeight === 0) return;
      fitAddon.fit();
    });
    ro.observe(containerRef.current);

    return () => {
      disposed = true;
      ro.disconnect();
      ws.close();
      term.dispose();
    };
  }, [wsPath]);

  // 테마 토글 시 색만 라이브 갱신 (재생성/재접속 없이)
  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = xtermTheme(theme);
  }, [theme]);

  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0"
      style={{ background: "var(--term-bg)", padding: "6px 0 6px 14px" }}
    />
  );
}

// 정상은 잉크 면의 글자색 그대로(시안의 "연결됨"), 끊겼을 때만 색을 쓴다.
const CONN_LABEL = {
  connecting: { text: "연결 중…", color: "var(--term-fg)", dim: 0.55 },
  open: { text: "연결됨", color: "var(--term-fg)", dim: 0.85 },
  closed: { text: "연결 끊김", color: "var(--err-fg)", dim: 1 },
  error: { text: "연결 실패", color: "var(--err-fg)", dim: 1 },
};

function TerminalSide({ dbType, redisEnabled, zoomed, onZoom }) {
  // Redis 대상은 use_redis일 때만 (Pane.TerminalSelector와 같은 방침).
  const targets = useMemo(
    () => [
      { id: "app", label: "앱 서버", path: "/deploy/app/terminal" },
      { id: "db", label: DB_LABEL[dbType] || "데이터베이스", path: "/deploy/app/db-terminal" },
      ...(redisEnabled ? [{ id: "redis", label: "Redis", path: "/deploy/app/redis-terminal" }] : []),
    ],
    [dbType, redisEnabled],
  );

  const [target, setTarget] = useState("app");
  // 대상을 바꿔도 이전 세션을 끊지 않는다 — 열어 본 대상만 마운트해 두고 display로 전환.
  const [openedTargets, setOpenedTargets] = useState(() => new Set(["app"]));
  const [conn, setConn] = useState({ app: "connecting" });

  const select = (id) => {
    setTarget(id);
    setOpenedTargets((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
    setConn((prev) => (prev[id] ? prev : { ...prev, [id]: "connecting" }));
  };

  const state = CONN_LABEL[conn[target]] || CONN_LABEL.connecting;

  return (
    <div className="flex-1 min-h-0 flex flex-col" style={{ background: "var(--term-bg)" }}>
      <PaneBar dark>
        <span className="kd-t-label shrink-0" style={{ color: "var(--term-fg)" }}>
          터미널
        </span>
        <span style={{ width: 1, height: 16, background: "rgba(255,255,255,0.18)" }} />
        <MiniSelect value={target} options={targets} onChange={select} dark width={112} />
        <span className="ml-auto kd-t-caption shrink-0" style={{ color: state.color, opacity: state.dim }}>
          {state.text}
        </span>
        <IconBtn
          dark
          icon={zoomed ? Minimize2 : Maximize2}
          label={zoomed ? "원래 크기" : "크게 보기"}
          onClick={onZoom}
        />
      </PaneBar>

      {targets.map((t) =>
        openedTargets.has(t.id) ? (
          <div
            key={t.id}
            className="flex-1 min-h-0 flex-col"
            style={{ display: target === t.id ? "flex" : "none" }}
          >
            <WorkspaceTerminal
              wsPath={t.path}
              onStatus={(s) => setConn((prev) => ({ ...prev, [t.id]: s }))}
            />
          </div>
        ) : null,
      )}
    </div>
  );
}

// ── 로그 ────────────────────────────────────────────────────────────────────
// 타임스탬프 파싱·에러/경고 판정 규칙은 panels/RuntimeLogPanel.jsx에서 그대로 가져왔다.
const TS_PATTERNS = [
  /^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)\]?\s*[-–]?\s*/,
  /^\[?(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]?\s*[-–]?\s*/,
];
const LEVEL_RE = /^\[?(TRACE|DEBUG|INFO|NOTICE|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\]?\s*[:|-]?\s*/i;

const isErrorLine = (l) =>
  /^\s*(ERROR|FATAL)\b/i.test(l) ||
  /\]\s*(ERROR|FATAL)\b/i.test(l) ||
  /^ERROR:/i.test(l) ||
  /^Traceback \(/i.test(l);

const isWarnLine = (l) =>
  /^\s*WARN(ING)?\b/i.test(l) || /\]\s*WARN(ING)?\b/i.test(l) || /^WARNING:/i.test(l);

// 한 줄 → { time, level, text }. 시간·레벨이 없으면 빈 칸(지어내지 않는다).
function parseLine(raw) {
  let text = raw;
  let time = "";
  for (const re of TS_PATTERNS) {
    const m = text.match(re);
    if (m) {
      const d = new Date(m[1]);
      time = Number.isNaN(d.getTime()) ? m[1] : clockOf(d);
      text = text.slice(m[0].length);
      break;
    }
  }
  let level = "";
  const lm = text.match(LEVEL_RE);
  if (lm) {
    level = lm[1].toUpperCase();
    text = text.slice(lm[0].length);
  }
  return { time, level, text, error: isErrorLine(raw), warn: isWarnLine(raw) };
}

// 백엔드(console/logs.py)는 splitlines() 배열을 준다. 통짜 문자열로 와도 받아 낸다.
const toLines = (v) => (Array.isArray(v) ? v : typeof v === "string" && v ? v.split("\n") : []);

const LOG_FILTERS = [
  { id: "all", label: "전체" },
  { id: "error", label: "에러" },
  { id: "warn", label: "경고" },
];

function LogSide({ build, zoomed, onZoom }) {
  const [lines, setLines] = useState([]);
  const [previous, setPrevious] = useState([]);
  const [source, setSource] = useState("current");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [fetchedAt, setFetchedAt] = useState(null);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("all");

  // RuntimeLogPanel과 같이 "스냅샷 + 수동 갱신" — 스트리밍이 아니다.
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getAppLogs();
      if (data.error) {
        setError(data.error);
        setLines([]);
        setPrevious([]);
      } else {
        setError(null);
        setLines(toLines(data.current).map(parseLine));
        setPrevious(toLines(data.previous).map(parseLine));
      }
      setFetchedAt(new Date());
    } catch (e) {
      setError(e.message || "조회 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rows = source === "previous" ? previous : lines;
  const q = query.trim().toLowerCase();
  const shown = rows.filter((l) => {
    if (level === "error" && !l.error) return false;
    if (level === "warn" && !l.warn) return false;
    if (q && !l.text.toLowerCase().includes(q) && !l.time.includes(q)) return false;
    return true;
  });

  return (
    <div className="flex-1 min-h-0 flex flex-col" style={{ background: "var(--kd-surface)" }}>
      <PaneBar>
        <span className="kd-t-label text-fg-1 shrink-0">로그</span>
        {/* 분할로 좁아져도 입력이 사라지지 않게 최소 폭을 준다 */}
        <div className="relative" style={{ flex: "1 1 110px", minWidth: 90, maxWidth: 220 }}>
          <Search size={15} strokeWidth={1.7} className="absolute text-fg-4" style={{ left: 10, top: 8 }} />
          {/* 툴바 행(42)에 맞춰 높이만 32로 줄여 쓴다 — 타입·모서리·색은 .kd-input 그대로 */}
          <input
            className="kd-input"
            style={{ height: 32, paddingLeft: 32 }}
            placeholder="로그 검색"
            value={query}
            spellCheck={false}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <MiniSelect value={level} options={LOG_FILTERS} onChange={setLevel} width={86} />
        <button
          onClick={load}
          disabled={loading}
          className="kd-t-label inline-flex items-center gap-1.5 text-fg-2 shrink-0 disabled:opacity-50"
        >
          <RefreshCw size={15} strokeWidth={1.8} className={loading ? "kd-spin" : ""} />
          갱신
        </button>
        <IconBtn
          icon={zoomed ? Minimize2 : Maximize2}
          label={zoomed ? "원래 크기" : "크게 보기"}
          onClick={onZoom}
        />
      </PaneBar>

      {/* 이전 인스턴스 로그 — 재시작이 있었을 때만 백엔드가 채워 준다 */}
      {previous.length > 0 && (
        <div
          className="shrink-0 flex items-center gap-2"
          style={{
            height: "var(--row-sm)",
            paddingInline: 16,
            borderBottom: "1px solid var(--kd-border)",
          }}
        >
          {[
            { id: "current", label: "현재 인스턴스" },
            { id: "previous", label: "이전 인스턴스" },
          ].map((s) => (
            <button
              key={s.id}
              onClick={() => setSource(s.id)}
              className="kd-t-micro"
              style={{
                height: 22,
                paddingInline: 10,
                borderRadius: 4,
                color: source === s.id ? "var(--fg-1)" : "var(--fg-3)",
                background: source === s.id ? "var(--sel)" : "transparent",
              }}
            >
              {s.label}
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto scroll-thin" style={{ paddingBlock: 10 }}>
        {error ? (
          <p className="kd-t-body-s" style={{ padding: "10px 16px", color: "var(--err-fg)" }}>
            {error}
          </p>
        ) : shown.length === 0 ? (
          <p className="kd-t-body-s text-fg-3" style={{ padding: "10px 16px" }}>
            {loading ? "불러오는 중…" : rows.length ? "조건에 맞는 로그가 없어요." : "로그가 없어요."}
          </p>
        ) : (
          shown.map((l, i) => (
            <div
              key={i}
              className="kd-t-code flex gap-4 text-fg-1"
              style={{ paddingInline: 16, paddingBlock: 2 }}
            >
              <span className="shrink-0 tabular-nums text-fg-3" style={{ width: 62 }}>
                {l.time}
              </span>
              <span
                className="shrink-0"
                style={{
                  width: 44,
                  // 팔레트가 닫힌 집합이라 경고색이 따로 없다 — 경고는 잉크(진하게)로만 구분
                  color: l.error ? "var(--err-fg)" : l.warn ? "var(--fg-1)" : "var(--fg-3)",
                }}
              >
                {l.level}
              </span>
              <span className="min-w-0 break-all whitespace-pre-wrap">{l.text}</span>
            </div>
          ))
        )}
      </div>

      <div
        className="shrink-0 flex items-center gap-3"
        style={{
          height: "var(--row-lg)",
          paddingInline: 16,
          borderTop: "1px solid var(--kd-border)",
        }}
      >
        <span className="kd-t-caption text-fg-3 tabular-nums">
          {fetchedAt ? `마지막 조회 ${clockOf(fetchedAt)}` : "조회 전"}
        </span>
        {build?.status === "failed" && (
          <span className="kd-t-caption ml-auto truncate" style={{ color: "var(--err-fg)" }}>
            최근 빌드 실패 · 빌드 로그는 배포 이력에서 볼 수 있어요
          </span>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// 2) 데이터베이스 — SQL 편집기 + 조회 결과 / DB 터미널
// 실행·페이지네이션·결과 필터·정렬 규칙은 panels/DbConsolePanel.jsx에서 가져왔다.
// ────────────────────────────────────────────────────────────────────────────
function DatabaseView({ dbType }) {
  const [mode, setMode] = useState("table"); // "table" | "terminal"

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <PaneBar>
        <Database size={18} strokeWidth={1.5} className="text-fg-1 shrink-0" />
        {/* 시안의 "PostgreSQL · app_db"에서 DB 이름은 뺐다 — 프론트로 내려오는 값이 없다 */}
        <span className="kd-t-body text-fg-1 truncate">{DB_LABEL[dbType] || "데이터베이스"}</span>
        <div
          className="ml-auto flex items-center shrink-0"
          style={{ padding: 2, borderRadius: 4, background: "var(--sel-soft)" }}
        >
          {[
            { id: "table", label: "표", icon: Table2 },
            { id: "terminal", label: "터미널", icon: TerminalSquare },
          ].map((m) => {
            const Icon = m.icon;
            const on = mode === m.id;
            return (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                className="kd-t-label inline-flex items-center gap-1.5"
                style={{
                  height: 26,
                  paddingInline: 12,
                  borderRadius: 6,
                  // 선택된 칸만 잉크로 채운다. 글자는 카드 면 색이라 두 테마 모두 대비가 맞는다
                  background: on ? "var(--accent)" : "transparent",
                  color: on ? "var(--kd-surface)" : "var(--fg-2)",
                }}
              >
                <Icon size={15} strokeWidth={1.7} />
                {m.label}
              </button>
            );
          })}
        </div>
      </PaneBar>

      {/* 두 모드 모두 마운트 유지 — 터미널 WebSocket이 토글로 끊기지 않게 display로 전환
          (DbConsolePanel과 같은 이유). 터미널은 헤더 없는 bare 모드로 얹는다. */}
      <div className="flex-1 min-h-0 flex-col" style={{ display: mode === "table" ? "flex" : "none" }}>
        <SqlConsole dbType={dbType} />
      </div>
      <div className="flex-1 min-h-0 flex-col" style={{ display: mode === "terminal" ? "flex" : "none" }}>
        <DbTerminalPanel bare />
      </div>
    </div>
  );
}

// 탭 3장 — 조회 결과 / 최근 실행 / 저장된 쿼리.
// 탭은 "결과 영역을 무엇으로 채울까"만 고른다. 편집 중인 SQL과 마지막 결과는 SqlConsole의
// state라 탭을 옮겨도 그대로 살아 있다(뷰 탭이 display로만 전환하는 것과 같은 이유).
const SQL_TABS = [
  { id: "result", label: "조회 결과" },
  { id: "recent", label: "최근 실행" },
  { id: "saved", label: "저장된 쿼리" },
];

// 목록 한 줄의 글자 위계 — 1줄은 그 행의 주인공, 2줄은 보조. 두 목록이 같은 쌍을 쓴다
// (최근 실행 = SQL/실행요약, 저장된 쿼리 = 이름/SQL 미리보기).
// kd-t-code는 코드 블록 기준의 12px이라 목록에서는 한 단 올려 쓴다 — 안 그러면 행의 주인공인
// SQL이 제 보조정보(13px)보다 작아져 위아래가 뒤집혀 보인다.
const ROW_PRIMARY = { fontSize: 14, lineHeight: 1.5 };
const ROW_SECONDARY = { fontSize: 13, lineHeight: 1.5 };

// 목록 한 줄에 넣을 SQL — 줄바꿈·들여쓰기를 한 칸 공백으로 눌러 한 줄로 만든다.
// 원문은 title(툴팁)과 "불러오기"가 그대로 들고 있으므로 여기서 잃는 정보는 없다.
const oneLine = (sql) => (sql || "").replace(/\s+/g, " ").trim();

// request()가 만드는 "400 알 수 없는 컬럼: x"에서 상태코드 접두사를 떼고 한 줄로 줄인다.
// 목록은 "왜 실패했나"를 한눈에 보여주는 자리고, 전문은 조회 결과 탭의 오류 칸에 있다.
function shortError(msg) {
  const text = oneLine(msg).replace(/^\d{3}\s+/, "");
  return text.length > 90 ? `${text.slice(0, 90)}…` : text;
}

// "오늘 18:24" / "9월 14일 18:24" — 이력은 대부분 방금 것이라 오늘은 날짜를 생략한다.
function historyTime(iso) {
  const d = parseDate(iso);
  if (!d) return "";
  const hm = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  return sameDay ? `오늘 ${hm}` : `${d.getMonth() + 1}월 ${d.getDate()}일 ${hm}`;
}

function SqlConsole({ dbType }) {
  const { user } = useAuth();

  const [sql, setSql] = useState("");
  const [result, setResult] = useState(null);
  const [executed, setExecuted] = useState(""); // 실행/페이징 중인 쿼리(편집과 분리)
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");     // 조회 결과 내 검색
  const [queryFilter, setQueryFilter] = useState(""); // 최근 실행·저장된 쿼리 검색
  const [tall, setTall] = useState(false);
  const [sort, setSort] = useState(null);
  const [tab, setTab] = useState("result");
  const [history, setHistory] = useState([]);
  const [saved, setSaved] = useState([]);
  const [savedError, setSavedError] = useState(null);
  const [dialog, setDialog] = useState(null);   // 저장/수정 폼 또는 편집기 덮어쓰기 확인
  const gutterRef = useRef(null);
  const editorRef = useRef(null);

  // 최근 실행의 보관 스코프 — 유저·앱·DB. 저장된 쿼리의 서버측 스코프와 같은 세 축이라
  // 계정을 바꾸거나 DB를 갈아탄 뒤 남의 이력이 보이지 않는다.
  const scope = useMemo(
    () => ({ userId: user?.id, appName: user?.app_name, dbType }),
    [user?.id, user?.app_name, dbType],
  );
  const scopeKey = historyKey(scope);

  // 스코프가 정해지거나 바뀌면 그 칸의 이력을 통째로 다시 읽는다.
  useEffect(() => {
    setHistory(loadHistory(scope));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopeKey]);

  // 저장된 쿼리 — 스코프는 서버가 정하므로 요청에 아무것도 싣지 않는다.
  // DB 없는 앱이면 API가 400이라 아예 부르지 않고 빈 목록으로 둔다.
  useEffect(() => {
    if (!dbType || !user?.app_name) {
      setSaved([]);
      return;
    }
    let cancelled = false;
    listSavedQueries()
      .then((rows) => !cancelled && (setSaved(rows), setSavedError(null)))
      .catch((e) => !cancelled && setSavedError(e.message || "저장된 쿼리를 불러오지 못했어요"));
    return () => {
      cancelled = true;
    };
  }, [dbType, user?.app_name, user?.id]);

  // 실행 1건 기록. 저장은 setState 안에서 — 스코프가 바뀌는 순간과 엇갈려 옛 이력이
  // 새 칸에 덮어써지는 일이 없도록, 항상 "지금 읽은 그 목록"과 함께 쓴다.
  const record = (entry) =>
    setHistory((prev) => {
      const next = pushEntry(prev, entry);
      saveHistory(scope, next);
      return next;
    });

  // 한 페이지 fetch — 실행(offset 0)과 페이지 이동이 공유한다.
  // log는 실행일 때만 true — 페이지 넘김은 같은 쿼리라 이력을 더럽히지 않는다.
  const fetchPage = async (q, offset, { log = false } = {}) => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await runDbQuery(q, offset);
      setResult(res);
      setExecuted(q);
      if (log)
        record(
          makeEntry(q, {
            outcome: "ok",
            duration_ms: res.duration_ms,
            row_count: res.row_count,
          }),
        );
    } catch (e) {
      const msg = e.message || "쿼리 실패";
      setError(msg);
      setResult(null);
      // 400은 백엔드가 "이 SQL이 실패했다"고 답한 것(QueryError). 그 외(응답 없음·5xx·
      // 인증 만료)는 쿼리가 돌았는지조차 모르므로 실패라고 단정하지 않는다.
      if (log)
        record(
          makeEntry(q, e.status === 400
            ? { outcome: "error", error: msg }
            : { outcome: "unknown", error: msg }),
        );
    } finally {
      setLoading(false);
    }
  };

  const run = () => {
    const q = sql.trim();
    if (!q || loading) return;
    setFilter(""); // 새 쿼리마다 결과 필터·정렬 초기화
    setSort(null);
    setResult(null); // 옛 결과를 먼저 치운다 — 로딩 중에 지난 표가 지금 결과처럼 보이면 안 된다
    setTab("result"); // 실행하면 결과를 보여 준다 (로딩·결과·오류 모두 이 탭)
    fetchPage(q, 0, { log: true });
  };

  // 불러오기 — 편집기에 SQL을 **넣기만** 한다. 자동 실행하지 않는다.
  const putIntoEditor = (nextSql) => {
    setSql(nextSql);
    setDialog(null);
    editorRef.current?.focus();
  };

  // 편집기에 살아 있는 내용이 조용히 사라지지 않게. 지금 내용이 비었거나, 불러올 것과
  // 같거나, 방금 실행해서 최근 실행에 남아 있는 것이면 되돌릴 수 있으니 바로 바꾼다.
  const loadIntoEditor = (nextSql) => {
    const incoming = (nextSql || "").trim();
    const current = sql.trim();
    if (!current || current === incoming || current === executed.trim()) {
      putIntoEditor(incoming);
      return;
    }
    setDialog({ kind: "replace", sql: incoming });
  };

  const openSave = (text) => {
    const target = (text || "").trim();
    if (!target) return;
    setDialog({ kind: "create", name: "", sql: target });
  };

  // 저장/수정 제출 — 실패하면 다이얼로그를 열어 둔 채 오류를 보여 준다(성공처럼 닫지 않는다).
  const submitDialog = async ({ name, sql: text }) => {
    if (dialog.kind === "create") {
      const row = await createSavedQuery(name, text);
      setSaved((prev) => [row, ...prev]);
      setSavedError(null);
      setTab("saved"); // 방금 저장한 것이 목록에 있음을 바로 보여 준다
    } else {
      const row = await updateSavedQuery(dialog.id, { name, sql: text });
      setSaved((prev) => prev.map((q) => (q.id === row.id ? row : q)));
      setSavedError(null);
    }
    setDialog(null);
  };

  const removeSaved = async (id) => {
    setSavedError(null);
    try {
      await deleteSavedQuery(id);
      setSaved((prev) => prev.filter((q) => q.id !== id));
    } catch (e) {
      setSavedError(e.message || "삭제하지 못했어요");
    }
  };

  const hasTable = result && result.columns && result.columns.length > 0;

  // 필터 — 셀 중 하나라도 검색어를 포함하면 통과(대소문자 무시)
  const filtered = useMemo(() => {
    if (!hasTable) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return result.rows;
    return result.rows.filter((r) => r.some((v) => (v ?? "").toLowerCase().includes(q)));
  }, [hasTable, result, filter]);

  const rows = useMemo(() => {
    if (!sort) return filtered;
    const sign = sort.dir === "asc" ? 1 : -1;
    const nullish = (v) => v === "" || v === "NULL";
    return [...filtered].sort((ra, rb) => {
      const a = ra[sort.key] ?? "";
      const b = rb[sort.key] ?? "";
      if (nullish(a) && nullish(b)) return 0;
      if (nullish(a)) return 1; // 빈 값은 방향과 무관하게 항상 아래
      if (nullish(b)) return -1;
      const na = Number(a);
      const nb = Number(b);
      if (Number.isFinite(na) && Number.isFinite(nb)) return (na - nb) * sign;
      return a.localeCompare(b, undefined, { numeric: true }) * sign;
    });
  }, [filtered, sort]);

  // 쿼리 검색 — 최근 실행은 SQL을, 저장된 쿼리는 이름과 SQL을 훑는다.
  const needle = queryFilter.trim().toLowerCase();
  const shownHistory = useMemo(
    () => (needle ? history.filter((h) => h.sql.toLowerCase().includes(needle)) : history),
    [history, needle],
  );
  const shownSaved = useMemo(
    () =>
      needle
        ? saved.filter(
            (q) =>
              q.name.toLowerCase().includes(needle) || q.sql.toLowerCase().includes(needle),
          )
        : saved,
    [saved, needle],
  );

  // 여러 페이지짜리 결과인지 — 이전/다음 버튼 노출 조건
  const multiPage = hasTable && result.paginated && (result.offset > 0 || result.has_more);
  const lineCount = Math.max(5, sql.split("\n").length);
  const counts = { result: 0, recent: history.length, saved: saved.length };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* ── SQL 편집기 (시안 잉크 면 1425x225 → 높이 155) ── */}
      <div
        className="shrink-0 relative flex"
        style={{ height: tall ? 300 : 155, background: "var(--term-bg)" }}
      >
        <div
          ref={gutterRef}
          className="kd-t-code shrink-0 overflow-hidden text-right select-none"
          style={{
            width: 56,
            paddingTop: 30,
            paddingRight: 12,
            color: "var(--term-fg)",
            opacity: 0.45,
            borderRight: "1px solid rgba(255,255,255,0.10)",
          }}
        >
          {Array.from({ length: lineCount }, (_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>

        <span
          className="kd-t-micro absolute"
          style={{ left: 16, top: 10, color: "var(--term-fg)", opacity: 0.55 }}
        >
          SQL
        </span>

        <SqlCodeInput
          className="flex-1 min-w-0"
          inputRef={editorRef}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          // 줄번호는 별도 박스라 스크롤을 따라가게 맞춰 준다
          onScrollSync={(e) => {
            if (gutterRef.current) gutterRef.current.scrollTop = e.target.scrollTop;
          }}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
          placeholder="SELECT id, title FROM posts ORDER BY id DESC LIMIT 5;"
          pad={{ paddingTop: 30, paddingLeft: 14, paddingRight: 170 }}
        />

        <div className="absolute" style={{ right: 14, top: 10 }}>
          <IconBtn
            dark
            icon={tall ? Minimize2 : Maximize2}
            label={tall ? "편집기 줄이기" : "편집기 넓게 보기"}
            onClick={() => setTall((v) => !v)}
          />
        </div>

        {/* 저장은 실행 옆 — "지금 편집기에 있는 SQL"을 다루는 동작이라 같은 줄에 둔다.
            잉크 면 위라 테두리 없이 글자만, 높이는 실행 버튼(32)과 맞춘다. */}
        <div className="absolute flex items-center gap-3" style={{ right: 14, bottom: 12 }}>
          <button
            onClick={() => openSave(sql)}
            disabled={!sql.trim()}
            className="kd-t-label inline-flex items-center"
            style={{
              height: 32,
              paddingInline: 6,
              color: "var(--term-fg)",
              opacity: sql.trim() ? 0.9 : 0.4,
              cursor: sql.trim() ? "pointer" : "default",
            }}
          >
            저장
          </button>
          <span className="kd-t-caption" style={{ color: "var(--term-fg)", opacity: 0.55 }}>
            Ctrl + Enter
          </span>
          <button
            onClick={run}
            disabled={loading || !sql.trim()}
            className="kd-btn-primary kd-btn-sm inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <Play size={15} strokeWidth={2} fill="currentColor" />
            {loading ? "실행 중…" : "실행"}
          </button>
        </div>
      </div>

      {/* ── 결과 영역 탭 줄 ──
          왼쪽은 탭, 오른쪽은 조회 결과의 요약(행 수·소요시간)과 검색.
          요약은 조회 결과 탭에서만 — 저장된 쿼리 옆에 붙으면 저장 개수로 읽힌다. */}
      <div
        className="shrink-0 flex items-center gap-3 overflow-x-auto scroll-thin"
        style={{ height: 54, paddingInline: 20, borderBottom: "1px solid var(--kd-border)" }}
      >
        <nav className="flex items-center gap-6 sm:gap-8 shrink-0">
          {SQL_TABS.map((t) => (
            <UnderlineTab
              key={t.id}
              label={t.label}
              count={counts[t.id]}
              active={tab === t.id}
              onClick={() => setTab(t.id)}
            />
          ))}
        </nav>

        {tab === "result" && (
          <span className="kd-t-caption text-fg-3 ml-auto shrink-0 tabular-nums">
            {error
              ? "실패"
              : result
                ? [
                    filter.trim()
                      ? `${rows.length} / ${result.row_count}행`
                      : `${result.row_count}행`,
                    `${result.duration_ms}ms`,
                  ].join(" · ")
                : "—"}
          </span>
        )}

        {tab === "result" && multiPage && (
          <span className="flex items-center gap-1 shrink-0">
            <PageBtn
              icon={ChevronLeft}
              disabled={loading || result.offset === 0}
              onClick={() => fetchPage(executed, Math.max(0, result.offset - result.page_size))}
            />
            <span className="kd-t-caption text-fg-3 tabular-nums">
              {result.offset + 1}–{result.offset + result.row_count}
            </span>
            <PageBtn
              icon={ChevronRight}
              disabled={loading || !result.has_more}
              onClick={() => fetchPage(executed, result.offset + result.page_size)}
            />
          </span>
        )}

        {/* 좁은 화면에서는 검색칸이 먼저 줄어든다(탭은 그대로 읽혀야 한다) */}
        <div
          className={`relative min-w-0 ${tab === "result" ? "" : "ml-auto"}`}
          style={{ width: 230, minWidth: 140, flexShrink: 1 }}
        >
          <Search size={15} strokeWidth={1.7} className="absolute text-fg-4" style={{ left: 10, top: 10 }} />
          <input
            className="kd-input"
            style={{ height: 36, paddingLeft: 32 }}
            placeholder={tab === "result" ? "결과 내 검색" : "쿼리 검색"}
            value={tab === "result" ? filter : queryFilter}
            spellCheck={false}
            onChange={(e) =>
              tab === "result" ? setFilter(e.target.value) : setQueryFilter(e.target.value)
            }
          />
        </div>
      </div>

      {/* 세 탭 모두 마운트를 유지하고 display로만 전환 — 결과 표의 스크롤 위치와
          입력 중인 검색어가 탭을 옮겨도 살아 있어야 한다(뷰 탭과 같은 규칙). */}
      <div
        className="flex-1 min-h-0 overflow-auto scroll-thin"
        style={{ display: tab === "result" ? "block" : "none" }}
      >
        {error ? (
          <pre
            className="kd-t-code whitespace-pre-wrap"
            style={{
              margin: 20,
              padding: 14,
              borderRadius: 4,
              border: "1px solid var(--kd-border)",
              color: "var(--err-fg)",
            }}
          >
            {error}
          </pre>
        ) : loading && !result ? (
          <Centered>실행 중…</Centered>
        ) : !result ? (
          <Centered>쿼리를 입력하고 실행하세요.</Centered>
        ) : !hasTable ? (
          <Centered>{result.message || "결과 없음"}</Centered>
        ) : rows.length === 0 ? (
          <Centered>{filter.trim() ? "검색 결과가 없어요." : "0행"}</Centered>
        ) : (
          <table style={{ borderCollapse: "collapse", width: "max-content", minWidth: "100%" }}>
            <thead>
              <tr>
                {result.columns.map((c, i) => (
                  <th
                    key={i}
                    className="text-left"
                    style={{
                      height: "var(--row-lg)",
                      paddingInline: i === 0 ? 20 : 12,
                      background: "var(--kd-surface)",
                      borderBottom: "1px solid var(--kd-border)",
                      position: "sticky",
                      top: 0,
                      whiteSpace: "nowrap",
                    }}
                  >
                    <SortHeader
                      label={c}
                      active={sort?.key === i}
                      dir={sort?.dir}
                      onClick={() => setSort((p) => nextSort(p, i))}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((v, ci) => (
                    <td
                      key={ci}
                      title={v}
                      className="kd-t-caption text-fg-1"
                      style={{
                        height: "var(--row-sm)",
                        paddingInline: ci === 0 ? 20 : 12,
                        borderBottom: "1px solid var(--kd-border)",
                        whiteSpace: "nowrap",
                        maxWidth: 420,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {v === "NULL" ? <span className="text-fg-4">NULL</span> : v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* ── 최근 실행 — 이 브라우저에만 남는 실행 흔적(최대 50개). 결과 데이터는 안 남는다 ── */}
      <div
        className="flex-1 min-h-0 overflow-auto scroll-thin"
        style={{ display: tab === "recent" ? "block" : "none" }}
      >
        {shownHistory.length === 0 ? (
          <Centered>
            {needle
              ? "검색 결과가 없어요."
              : scopeKey
                ? "실행한 쿼리가 없어요. 위에서 SQL을 실행하면 여기에 쌓여요."
                : "실행 이력을 보관할 수 없는 상태예요."}
          </Centered>
        ) : (
          shownHistory.map((h) => (
            <QueryRow
              key={h.id}
              actions={
                <>
                  <button
                    onClick={() => loadIntoEditor(h.sql)}
                    className="kd-btn-secondary kd-btn-sm"
                  >
                    불러오기
                  </button>
                  <RowTextBtn onClick={() => openSave(h.sql)}>저장</RowTextBtn>
                </>
              }
            >
              <SqlPreview sql={h.sql} className="text-fg-1" style={ROW_PRIMARY} />
              <div
                className="kd-t-caption text-fg-3 flex items-center gap-2 min-w-0"
                style={{ marginTop: 6 }}
              >
                <span className="tabular-nums shrink-0">{historyTime(h.at)}</span>
                <MetaDot />
                <StatusBadge status={h.outcome} kind="query" />
                {h.outcome === "ok" && (
                  <>
                    <MetaDot />
                    <span className="tabular-nums shrink-0">{h.row_count}행</span>
                    <MetaDot />
                    <span className="tabular-nums shrink-0">{h.duration_ms}ms</span>
                  </>
                )}
                {h.error && (
                  <>
                    <MetaDot />
                    <span className="truncate" title={h.error}>
                      {shortError(h.error)}
                    </span>
                  </>
                )}
              </div>
            </QueryRow>
          ))
        )}
      </div>

      {/* ── 저장된 쿼리 — 플랫폼 DB 보관(유저 앱 DB엔 아무것도 안 만든다) ── */}
      <div
        className="flex-1 min-h-0 overflow-auto scroll-thin"
        style={{ display: tab === "saved" ? "block" : "none" }}
      >
        {savedError && (
          <p
            className="kd-t-body-s"
            style={{ paddingInline: 20, paddingTop: 16, color: "var(--err-fg)" }}
          >
            {savedError}
          </p>
        )}
        {shownSaved.length === 0 ? (
          <Centered>
            {needle
              ? "검색 결과가 없어요."
              : !dbType
                ? "DB를 추가한 앱에서만 쿼리를 저장할 수 있어요."
                : "저장한 쿼리가 없어요. 편집기의 저장으로 추가하세요."}
          </Centered>
        ) : (
          shownSaved.map((q) => (
            <QueryRow
              key={q.id}
              actions={
                <>
                  <button
                    onClick={() => loadIntoEditor(q.sql)}
                    className="kd-btn-secondary kd-btn-sm"
                  >
                    불러오기
                  </button>
                  <RowMenu
                    label="쿼리 작업"
                    items={[
                      {
                        id: "edit",
                        label: "수정",
                        icon: Pencil,
                        onClick: () =>
                          setDialog({ kind: "edit", id: q.id, name: q.name, sql: q.sql }),
                      },
                      {
                        id: "delete",
                        label: "삭제",
                        icon: Trash2,
                        danger: true,
                        onClick: () => removeSaved(q.id),
                      },
                    ]}
                  />
                </>
              }
            >
              <p className="kd-t-body-s text-fg-1 truncate" style={ROW_PRIMARY}>
                {q.name}
              </p>
              <SqlPreview
                sql={q.sql}
                className="text-fg-3"
                style={{ ...ROW_SECONDARY, marginTop: 6 }}
              />
            </QueryRow>
          ))
        )}
      </div>

      {dialog?.kind === "replace" ? (
        <ReplaceEditorDialog
          onConfirm={() => putIntoEditor(dialog.sql)}
          onClose={() => setDialog(null)}
        />
      ) : dialog ? (
        <SaveQueryDialog
          key={`${dialog.kind}-${dialog.id ?? "new"}`}
          mode={dialog.kind}
          initialName={dialog.name}
          initialSql={dialog.sql}
          onSubmit={submitDialog}
          onClose={() => setDialog(null)}
        />
      ) : null}
    </div>
  );
}

// SQL 입력 — 잉크 면 위에서 키워드·리터럴·주석에 색을 입힌다.
//
// textarea는 글자를 투명으로 두고 캐럿만 남기고, 정확히 같은 자리에 겹친 <pre>가 색칠한
// 같은 문자열을 그린다. 그래서 둘의 폰트·줄높이·패딩·줄바꿈 규칙이 하나라도 어긋나면 글자와
// 색이 어긋나 보인다 — 치수(metrics)를 한 벌만 만들어 양쪽에 그대로 넣는 이유다.
// 편집은 여전히 진짜 textarea가 받는다(IME·되돌리기·선택이 브라우저 기본 그대로다).
function SqlCodeInput({
  value, onChange, onKeyDown, onScrollSync, placeholder,
  inputRef, pad, className = "", style = {},
}) {
  const hiRef = useRef(null);
  const tokens = useMemo(() => tokenizeSql(value), [value]);
  const metrics = { ...pad, whiteSpace: "pre-wrap", overflowWrap: "break-word" };

  return (
    <div className={`relative ${className}`} style={style}>
      <pre
        ref={hiRef}
        aria-hidden="true"
        className="kd-t-code absolute inset-0 overflow-hidden pointer-events-none"
        style={{ ...metrics, margin: 0, color: "var(--term-fg)" }}
      >
        {tokens.map((t, i) => (
          <span key={i} style={t.kind === "text" ? undefined : { color: `var(--sql-${t.kind})` }}>
            {t.text}
          </span>
        ))}
        {"\n"}
      </pre>
      <textarea
        ref={inputRef}
        value={value}
        onChange={onChange}
        onKeyDown={onKeyDown}
        onScroll={(e) => {
          // 색 레이어는 스크롤이 없는 박스라 textarea를 따라 직접 밀어 준다
          if (hiRef.current) {
            hiRef.current.scrollTop = e.target.scrollTop;
            hiRef.current.scrollLeft = e.target.scrollLeft;
          }
          onScrollSync?.(e);
        }}
        spellCheck={false}
        placeholder={placeholder}
        className="kd-t-code kd-code-input absolute inset-0 w-full h-full resize-none bg-transparent outline-none scroll-thin"
        style={metrics}
      />
    </div>
  );
}

// 종이 면(카드) 위에서 쓰는 SQL 색. 잉크 면용 --sql-* 은 어두운 바탕 기준이라 흰 카드에서는
// 흐려진다 — 무엇을 칠하는지(키워드/리터럴/주석)는 그대로 두고 명도만 면에 맞춘다.
// 새 색을 만들지 않고 기존 신호색을 쓴다: 두 토큰 모두 라이트·다크에 각각 정의돼 있어
// 카드가 어느 테마든 그대로 읽힌다.
const PAPER_SQL_COLOR = {
  keyword: "var(--info-fg)",
  literal: "var(--warn-fg)",
  comment: "var(--fg-4)",
};

// 목록 한 줄짜리 SQL 미리보기 — 한 줄로 눌러 담고 편집기와 같은 규칙으로 칠한다.
// 바탕 글자색은 부르는 쪽이 정한다: 최근 실행은 SQL이 행의 주인공이라 fg-1,
// 저장된 쿼리는 이름 아래 보조라 fg-3. 색은 그 위에 얹히는 것이라 위계를 흔들지 않는다.
function SqlPreview({ sql, className = "", style }) {
  const tokens = useMemo(() => tokenizeSql(oneLine(sql)), [sql]);
  return (
    <p className={`kd-t-code truncate ${className}`} style={style} title={sql}>
      {tokens.map((t, i) => (
        <span key={i} style={t.kind === "text" ? undefined : { color: PAPER_SQL_COLOR[t.kind] }}>
          {t.text}
        </span>
      ))}
    </p>
  );
}

// 최근 실행 · 저장된 쿼리가 공유하는 한 줄. 왼쪽은 글(2줄), 오른쪽은 액션이며
// 액션 칸은 높이를 고정해 두 목록의 버튼이 같은 자리에 선다.
function QueryRow({ children, actions }) {
  return (
    <div
      className="flex items-center gap-4"
      style={{
        paddingInline: 20,
        paddingBlock: 12,
        borderBottom: "1px solid var(--kd-border)",
      }}
    >
      <div className="flex-1 min-w-0">{children}</div>
      <div className="shrink-0 flex items-center gap-2" style={{ height: 32 }}>
        {actions}
      </div>
    </div>
  );
}

// 보조 정보 사이의 가운뎃점
function MetaDot() {
  return <span className="text-fg-4 shrink-0">·</span>;
}

// 목록 오른쪽 끝 액션 칸의 고정 폭 — 글자 버튼("저장")과 아이콘 메뉴("…")가 같은 폭을
// 차지해야 그 앞의 "불러오기"가 두 목록에서 같은 자리에 선다.
const ROW_ACTION_W = 44;

// 테두리 없는 글자 버튼 — 목록 오른쪽의 보조 액션("저장"). 높이는 옆 버튼과 같다.
function RowTextBtn({ onClick, children }) {
  return (
    <button
      onClick={onClick}
      className="kd-t-label text-fg-2 hover:text-fg-1 inline-flex items-center justify-center transition-colors"
      style={{ height: 32, width: ROW_ACTION_W }}
    >
      {children}
    </button>
  );
}

// 행 오른쪽 "…" 메뉴. 목록이 overflow-auto라 안에서 absolute로 띄우면 아래쪽 행에서
// 잘린다 — MiniSelect와 같은 이유로 body에 포털해 fixed로 띄운다.
function RowMenu({ label, items }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const wrapRef = useRef(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    const place = () => {
      const r = wrapRef.current?.getBoundingClientRect();
      if (r) setPos({ right: window.innerWidth - r.right, top: r.bottom + 4 });
    };
    place();
    const onDown = (e) =>
      !wrapRef.current?.contains(e.target) && !menuRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  return (
    <span
      ref={wrapRef}
      className="inline-flex items-center justify-center"
      style={{ width: ROW_ACTION_W }}
    >
      <IconBtn icon={MoreHorizontal} label={label} onClick={() => setOpen((v) => !v)} />
      {open && pos && createPortal(
        <div
          ref={menuRef}
          className="fixed z-50 kd-card"
          style={{ right: pos.right, top: pos.top, width: 150, paddingBlock: 6 }}
        >
          {items.map((it) => {
            const Icon = it.icon;
            return (
              <button
                key={it.id}
                onClick={() => {
                  setOpen(false);
                  it.onClick();
                }}
                className="kd-t-label w-full flex items-center gap-2.5 text-left"
                style={{
                  height: "var(--row-md)",
                  paddingInline: 14,
                  color: it.danger ? "var(--err-fg)" : "var(--fg-1)",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--sel-soft)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {Icon && <Icon size={15} strokeWidth={1.7} />}
                {it.label}
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </span>
  );
}

// 모달 껍데기 — LoginModal과 같은 면·그림자·스크림을 쓴다.
function ConsoleDialog({ title, onClose, children, busy }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose, busy]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center kd-fade-in"
      style={{ background: "var(--kd-scrim-strong)" }}
      onClick={() => !busy && onClose()}
    >
      <div
        className="relative w-[460px] max-w-[92vw]"
        style={{
          background: "var(--kd-surface)",
          borderRadius: 6,
          padding: 24,
          boxShadow: "0 24px 60px rgba(23,23,23,0.18)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center">
          <h2 className="kd-t-section text-fg-1">{title}</h2>
          <button
            onClick={onClose}
            disabled={busy}
            className="ml-auto w-7 h-7 text-fg-3 hover:text-fg-1 transition-colors flex items-center justify-center disabled:opacity-40"
            aria-label="닫기"
          >
            <X size={17} strokeWidth={1.7} />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

// 저장/수정 폼 — 이름과 SQL을 함께 다룬다(수정 메뉴가 둘 다 고칠 수 있어야 한다).
// 실패하면 닫지 않고 오류를 이 안에 띄운다 — 저장 안 된 것을 저장된 것처럼 보이면 안 된다.
function SaveQueryDialog({ mode, initialName, initialSql, onSubmit, onClose }) {
  const [name, setName] = useState(initialName || "");
  const [sql, setSql] = useState(initialSql || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const canSubmit = name.trim() && sql.trim() && !busy;

  const submit = async (e) => {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit({ name: name.trim(), sql: sql.trim() });
    } catch (err) {
      setError(err.message || "저장하지 못했어요");
      setBusy(false);
    }
  };

  return (
    <ConsoleDialog title={mode === "edit" ? "쿼리 수정" : "쿼리 저장"} onClose={onClose} busy={busy}>
      <form onSubmit={submit}>
        <p className="kd-t-label text-fg-1" style={{ marginTop: 18, marginBottom: 8 }}>
          이름
        </p>
        <input
          className="kd-input"
          value={name}
          autoFocus
          maxLength={100}
          spellCheck={false}
          placeholder="예: 최근 가입 회원"
          onChange={(e) => setName(e.target.value)}
        />

        <p className="kd-t-label text-fg-1" style={{ marginTop: 16, marginBottom: 8 }}>
          SQL
        </p>
        {/* 편집기와 같은 잉크 면 + 같은 색 규칙 — 저장 직전에 보는 SQL이 방금 친 것과
            다르게 보이면 "이게 그거 맞나"를 눈으로 확인할 수 없다. */}
        <SqlCodeInput
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder="SELECT id, title FROM posts LIMIT 5;"
          pad={{ paddingTop: 10, paddingLeft: 12, paddingRight: 12, paddingBottom: 10 }}
          style={{
            height: 130,
            background: "var(--term-bg)",
            borderRadius: "var(--kd-radius)",
            overflow: "hidden",
          }}
        />

        {error && (
          <p className="kd-t-caption" style={{ marginTop: 12, color: "var(--err-fg)" }}>
            {error}
          </p>
        )}

        <div className="flex items-center gap-2" style={{ marginTop: 18 }}>
          <button type="submit" disabled={!canSubmit} className="kd-btn-primary kd-btn-md disabled:opacity-50">
            {busy ? "저장 중…" : "저장"}
          </button>
          <button type="button" onClick={onClose} disabled={busy} className="kd-btn-secondary kd-btn-md disabled:opacity-50">
            취소
          </button>
        </div>
      </form>
    </ConsoleDialog>
  );
}

// 편집기 덮어쓰기 확인 — 불러오기가 작성 중인 SQL을 조용히 지우지 않게.
function ReplaceEditorDialog({ onConfirm, onClose }) {
  return (
    <ConsoleDialog title="편집기 내용을 바꿀까요?" onClose={onClose}>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 12 }}>
        편집기에 아직 실행하지 않은 SQL이 있어요. 불러오면 그 내용은 사라집니다.
      </p>
      <div className="flex items-center gap-2" style={{ marginTop: 20 }}>
        <button onClick={onConfirm} className="kd-btn-primary kd-btn-md">
          불러오기
        </button>
        <button onClick={onClose} className="kd-btn-secondary kd-btn-md">
          취소
        </button>
      </div>
    </ConsoleDialog>
  );
}

function PageBtn({ icon: Icon, disabled, onClick }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{ color: disabled ? "var(--fg-4)" : "var(--fg-2)", cursor: disabled ? "default" : "pointer" }}
    >
      <Icon size={15} strokeWidth={1.9} />
    </button>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// 3) 스토리지 — 파일 목록/격자 + 미리보기 (use_storage일 때만 탭이 뜬다)
//
// 보기가 둘인 이유: 이 버킷의 파일 이름은 대개 앱이 지어 준 UUID다. 이름을 읽어서는 사진을
// 못 찾으므로 격자(썸네일)가 기본이고, 이름·크기·수정일을 나란히 비교할 때만 목록으로 간다.
// 격자에서도 JSON·TXT는 억지로 그림을 만들지 않는다 — 종류만 알아보게 하고, 고르면 오른쪽에서
// 내용을 읽는다.
// ────────────────────────────────────────────────────────────────────────────
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"];
const isImage = (key) => IMAGE_EXTS.some((ext) => key.toLowerCase().endsWith(ext));
// 내용을 글로 읽는 형식 — core를 거쳐 앞부분을 받아 그린다.
const TEXT_EXTS = [".txt", ".md", ".csv", ".log", ".yaml", ".yml", ".xml", ".ini", ".toml"];
function previewKind(key) {
  const k = key.toLowerCase();
  if (isImage(key)) return "image";
  if (k.endsWith(".pdf")) return "pdf";
  // JSON은 text와 갈라 둔다 — 같은 글이지만 줄 번호를 달고 들여쓰기를 정규화해서 읽힌다.
  if (k.endsWith(".json")) return "json";
  if (TEXT_EXTS.some((ext) => k.endsWith(ext))) return "text";
  return "none";
}

function fileIcon(key) {
  if (isImage(key)) return ImageIcon;
  if (/\.(json|ya?ml|toml|js|ts|jsx|tsx|css|html|py|java|php|sh|sql)$/i.test(key)) return FileCode;
  return FileIcon;
}

const baseName = (key) => key.split("/").pop();
// 격자 칸에 적을 종류 표시. 확장자가 없으면 "파일".
function fileExt(key) {
  const name = baseName(key);
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toUpperCase().slice(0, 6) : "파일";
}

const STORAGE_COLS = "46% 26% 28%";

function StorageView() {
  const [objects, setObjects] = useState([]);
  const [next, setNext] = useState(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [sort, setSort] = useState(null);
  // 격자가 기본 — 이름이 UUID면 목록에서는 찾을 수가 없다.
  const [view, setView] = useState("grid");
  const [previewOpen, setPreviewOpen] = useState(true);

  // 첫 페이지 로드 / 새로고침 — 목록을 초기화하고 처음부터.
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listStorageObjects(null);
      setObjects(res.objects || []);
      setNext(res.next || null);
    } catch (e) {
      setError(e.message || "목록 조회 실패");
      setObjects([]);
      setNext(null);
    } finally {
      setLoading(false);
      setLoaded(true);
    }
  }, []);

  // 다음 페이지 — 기존 목록에 이어붙인다.
  const loadMore = async () => {
    if (!next || loading) return;
    setLoading(true);
    try {
      const res = await listStorageObjects(next);
      setObjects((prev) => [...prev, ...(res.objects || [])]);
      setNext(res.next || null);
    } catch (e) {
      setError(e.message || "목록 조회 실패");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [load]);

  const rows = useMemo(() => {
    if (!sort) return objects;
    const sign = sort.dir === "asc" ? 1 : -1;
    return [...objects].sort((a, b) => {
      if (sort.key === "size") return ((a.size || 0) - (b.size || 0)) * sign;
      if (sort.key === "date")
        return (
          ((parseDate(a.last_modified)?.getTime() || 0) - (parseDate(b.last_modified)?.getTime() || 0)) *
          sign
        );
      return a.key.localeCompare(b.key, undefined, { numeric: true }) * sign;
    });
  }, [objects, sort]);

  // 미리보기를 닫으면 고른 것이 없는 상태 — 목록/격자가 칸 전체를 쓴다.
  const selected = previewOpen ? rows.find((o) => o.key === selectedKey) || rows[0] || null : null;

  const pick = (key) => {
    setSelectedKey(key);
    setPreviewOpen(true);
  };

  const onDeleted = (key) => {
    setObjects((prev) => prev.filter((o) => o.key !== key));
    if (selectedKey === key) setSelectedKey(null);
  };

  const empty = !loaded && loading ? "불러오는 중…" : rows.length === 0 ? "올라온 파일이 없어요." : null;

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* 툴바 (시안 y281→357 = 52) */}
      <div
        className="shrink-0 flex items-center gap-3"
        style={{ height: 54, paddingInline: 20, borderBottom: "1px solid var(--kd-border)" }}
      >
        <h2 className="kd-t-section text-fg-1">파일</h2>
        <span className="kd-t-body-s text-fg-3 tabular-nums">
          {loaded && !error ? `${objects.length}개` : "—"}
        </span>

        {/* 보기 전환 — 결과 영역 탭과 같은 잉크 밑줄 */}
        <nav className="ml-auto flex items-center gap-5 shrink-0">
          {[
            { id: "list", label: "목록" },
            { id: "grid", label: "격자" },
          ].map((v) => (
            <UnderlineTab
              key={v.id}
              label={v.label}
              active={view === v.id}
              onClick={() => setView(v.id)}
            />
          ))}
        </nav>

        <button
          onClick={load}
          disabled={loading}
          className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5 shrink-0 disabled:opacity-50"
        >
          <RefreshCw size={15} strokeWidth={1.8} className={loading ? "kd-spin" : ""} />
          새로고침
        </button>
      </div>

      <div className="flex-1 min-h-0 flex">
        {/* 좌: 목록 또는 격자. 미리보기가 닫히면 칸 전체를 쓴다. */}
        <div
          className="min-h-0 flex flex-col"
          style={selected ? { width: view === "grid" ? "62%" : "56%", flexShrink: 0 } : { flex: 1, minWidth: 0 }}
        >
          {view === "list" && (
            <div
              className="shrink-0 grid items-center"
              style={{
                gridTemplateColumns: STORAGE_COLS,
                height: 40,
                paddingInline: 20,
                borderBottom: "1px solid var(--kd-border)",
              }}
            >
              <SortHeader
                label="파일명"
                active={sort?.key === "name"}
                dir={sort?.dir}
                onClick={() => setSort((p) => nextSort(p, "name"))}
              />
              <SortHeader
                label="크기"
                active={sort?.key === "size"}
                dir={sort?.dir}
                onClick={() => setSort((p) => nextSort(p, "size"))}
              />
              <SortHeader
                label="수정일"
                active={sort?.key === "date"}
                dir={sort?.dir}
                onClick={() => setSort((p) => nextSort(p, "date"))}
              />
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-auto scroll-thin">
            {error ? (
              <p className="kd-t-body-s" style={{ padding: 20, color: "var(--err-fg)" }}>
                {error}
              </p>
            ) : empty ? (
              <Centered>{empty}</Centered>
            ) : (
              <>
                {view === "grid" ? (
                  <div
                    style={{
                      display: "grid",
                      // 미리보기가 열려 있으면 3열 고정(시안), 닫히면 넓어진 만큼 칸이 늘어난다
                      gridTemplateColumns: selected
                        ? "repeat(3, minmax(0, 1fr))"
                        : "repeat(auto-fill, minmax(170px, 1fr))",
                      gap: 18,
                      padding: 20,
                    }}
                  >
                    {rows.map((o) => (
                      <StorageCard
                        key={o.key}
                        obj={o}
                        selected={selected?.key === o.key}
                        onSelect={() => pick(o.key)}
                      />
                    ))}
                  </div>
                ) : (
                  rows.map((o) => {
                    const Icon = fileIcon(o.key);
                    const on = selected?.key === o.key;
                    return (
                      <button
                        key={o.key}
                        onClick={() => pick(o.key)}
                        aria-current={on ? "true" : undefined}
                        className="kd-pick w-full grid items-center text-left"
                        style={{
                          gridTemplateColumns: STORAGE_COLS,
                          height: "var(--row-lg)",
                          paddingInline: 20,
                          borderBottom: "1px solid var(--kd-border)",
                        }}
                      >
                        <span className="kd-t-body-s text-fg-1 flex items-center gap-3 min-w-0">
                          <Icon size={18} strokeWidth={1.5} className="shrink-0 text-fg-2" />
                          <span className="kd-pick-name truncate">{o.key}</span>
                        </span>
                        <span className="kd-t-body-s text-fg-2 tabular-nums">{fmtBytes(o.size)}</span>
                        <span className="kd-t-body-s text-fg-2 tabular-nums">
                          {fmtDateTime(o.last_modified)}
                        </span>
                      </button>
                    );
                  })
                )}
                {next && (
                  <div className="flex justify-center" style={{ padding: 14 }}>
                    <button
                      onClick={loadMore}
                      disabled={loading}
                      className="kd-btn-secondary kd-btn-sm disabled:opacity-50"
                    >
                      {loading ? "불러오는 중…" : "더 보기"}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        {selected && (
          <>
            <span style={{ width: 1, background: "var(--kd-border)" }} />
            <div className="flex-1 min-w-0 min-h-0 overflow-auto scroll-thin flex flex-col">
              <StoragePreview
                key={selected.key}
                obj={selected}
                onDeleted={onDeleted}
                onClose={() => setPreviewOpen(false)}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// 격자 한 칸 — 표지 + 파일명 + 용량.
// 면(회색 배경)이나 그림자를 두지 않고 여백으로 나눈다. 고른 칸만 얇은 잉크 테두리가 생기며,
// 안 고른 칸도 같은 두께의 투명 테두리를 들고 있어 선택해도 격자가 흔들리지 않는다.
function StorageCard({ obj, selected, onSelect }) {
  return (
    <button
      onClick={onSelect}
      aria-pressed={selected}
      title={obj.key}
      className="text-left min-w-0"
      style={{
        padding: 8,
        borderRadius: 6,
        border: `1px solid ${selected ? "var(--accent)" : "transparent"}`,
      }}
    >
      <span
        className="flex items-center justify-center overflow-hidden"
        style={{
          aspectRatio: "5 / 4",
          borderRadius: 4,
          border: "1px solid var(--kd-border)",
          background: "var(--kd-surface)",
        }}
      >
        <FileGlyph obj={obj} />
      </span>
      <span
        className="kd-t-body-s text-fg-1 truncate block"
        style={{ marginTop: 8 }}
      >
        {baseName(obj.key)}
      </span>
      <span className="kd-t-caption text-fg-3 block tabular-nums">{fmtBytes(obj.size)}</span>
    </button>
  );
}

// 격자 칸의 표지 — 사진은 실물, 그 외는 종류만.
// JSON만 중괄호로 따로 세운다: 미리보기가 코드로 열리는 유일한 종류라 고르기 전에 알 수 있다.
function FileGlyph({ obj }) {
  const kind = previewKind(obj.key);
  if (kind === "image" && obj.url) {
    return <img src={obj.url} alt="" loading="lazy" className="w-full h-full object-cover" />;
  }
  return (
    <span className="flex flex-col items-center justify-center gap-2">
      {kind === "json" ? (
        <span className="kd-t-code text-fg-2" style={{ fontSize: 26, lineHeight: 1 }}>
          {"{ }"}
        </span>
      ) : (
        <FileText size={30} strokeWidth={1.3} className="text-fg-2" />
      )}
      <span className="kd-t-micro text-fg-3">{fileExt(obj.key)}</span>
    </span>
  );
}

function StoragePreview({ obj, onDeleted, onClose }) {
  const [copied, setCopied] = useState(false);
  const [menu, setMenu] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // 기본 미리보기는 152px에 object-cover라 사진이 잘린다. "크게 보기"를 켜면
  // 오른쪽 칸을 채우고 object-contain으로 바꿔 사진 전체가 보이게 한다.
  const [big, setBig] = useState(false);
  const Icon = fileIcon(obj.key);
  const kind = previewKind(obj.key);
  const img = kind === "image" && obj.url;
  const reads = kind === "json" || kind === "text";   // core를 거쳐 글로 읽는 종류
  const framed = kind === "pdf" && obj.url;

  const copy = async () => {
    if (!obj.url) return;
    try {
      await navigator.clipboard.writeText(obj.url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard 거부 — 조용히 무시 */
    }
  };

  const del = async () => {
    setDeleting(true);
    try {
      await deleteStorageObject(obj.key);
      onDeleted(obj.key);
    } finally {
      setDeleting(false);
      setMenu(false);
    }
  };

  return (
    <div style={{ padding: 20 }}>
      <div className="flex items-start gap-3">
        <h2 className="kd-t-subtitle text-fg-1 truncate">{baseName(obj.key)}</h2>
        <div className="relative ml-auto shrink-0 flex items-center gap-1">
          {(img || framed || reads) && (
            <IconBtn
              icon={big ? Minimize2 : Maximize2}
              label={big ? "원래 크기" : "크게 보기"}
              onClick={() => setBig((v) => !v)}
            />
          )}
          <IconBtn icon={MoreHorizontal} label="파일 작업" onClick={() => setMenu((v) => !v)} />
          {/* 닫으면 왼쪽 격자/목록이 칸 전체로 넓어진다 */}
          <IconBtn icon={X} label="미리보기 닫기" onClick={onClose} />
          {menu && (
            <div className="absolute right-0 z-30 kd-card" style={{ top: 28, width: 168, paddingBlock: 6 }}>
              <button
                onClick={del}
                disabled={deleting}
                className="kd-t-label w-full flex items-center gap-2.5 text-left"
                style={{ height: "var(--row-md)", paddingInline: 14, color: "var(--err-fg)" }}
              >
                <Trash2 size={15} strokeWidth={1.7} />
                {deleting ? "삭제 중…" : "파일 삭제"}
              </button>
            </div>
          )}
        </div>
      </div>

      {reads ? (
        <TextPreview obj={obj} json={kind === "json"} tall={big} />
      ) : (
        <div
          className="w-full flex items-center justify-center overflow-hidden"
          style={{
            marginTop: 14,
            // 크게 보기: 오른쪽 칸 높이를 채우되 화면을 넘지 않게 상한을 둔다
            height: big ? "min(58vh, 520px)" : 152,
            borderRadius: 4,
            background: "var(--sel-soft)",
            border: framed ? "1px solid var(--kd-border)" : undefined,
            transition: "height 180ms ease",
          }}
        >
          {img && (
            <button
              type="button"
              onClick={() => setBig((v) => !v)}
              aria-label={big ? "원래 크기로" : "사진 전체 보기"}
              className="w-full h-full"
              style={{ cursor: big ? "zoom-out" : "zoom-in" }}
            >
              <img
                src={obj.url}
                alt={obj.key}
                // 접힌 상태는 썸네일처럼 꽉 채우고(cover), 크게 보기는 잘리지 않게(contain)
                className={`w-full h-full ${big ? "object-contain" : "object-cover"}`}
              />
            </button>
          )}
          {framed && (
            // PDF는 브라우저 PDF 뷰어가 그대로 그린다.
            <iframe
              src={obj.url}
              title={`${obj.key} 미리보기`}
              className="w-full h-full"
              style={{ border: 0, background: "var(--kd-surface)" }}
            />
          )}
          {!img && !framed && <Icon size={32} strokeWidth={1.3} className="text-fg-4" />}
        </div>
      )}

      <div style={{ marginTop: 18, borderTop: "1px solid var(--kd-border)" }}>
        <InfoRow label="크기">{fmtBytes(obj.size) || "—"}</InfoRow>
        <InfoRow label="수정일">{fmtDateTime(obj.last_modified)}</InfoRow>
      </div>

      <p className="kd-t-label text-fg-1" style={{ marginTop: 18, marginBottom: 8 }}>
        파일 주소
      </p>
      {obj.url ? (
        <>
          <div className="flex items-center gap-2">
            <input className="kd-input" readOnly value={obj.url} onFocus={(e) => e.target.select()} />
            <button
              onClick={copy}
              className="kd-btn-secondary kd-btn-md inline-flex items-center gap-1.5 shrink-0"
            >
              {copied ? (
                <Check size={15} strokeWidth={2} style={{ color: "var(--ok-fg)" }} />
              ) : (
                <Copy size={15} strokeWidth={1.7} />
              )}
              주소 복사
            </button>
          </div>
          <a
            href={obj.url}
            target="_blank"
            rel="noreferrer"
            download
            className="kd-btn-primary kd-btn-md inline-flex items-center gap-1.5 no-underline"
            style={{ marginTop: 14 }}
          >
            <Download size={15} strokeWidth={1.8} />
            다운로드
          </a>
        </>
      ) : (
        <p className="kd-t-body-s text-fg-3">공개 URL이 없는 객체예요.</p>
      )}
    </div>
  );
}

// 글로 읽는 미리보기 — core를 거쳐 앞부분만 받아 그린다.
// 공개 URL로 브라우저가 직접 읽지 못하는 이유는 R2 공개 버킷에 CORS가 없어서다. 미리보기
// 하나 때문에 버킷에 CORS를 여느니, 이미 인가를 거치는 core가 제 자격증명으로 읽어 넘긴다.
function TextPreview({ obj, json, tall }) {
  const [state, setState] = useState({ loading: true });
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true });
    readStorageObject(obj.key)
      .then((res) => !cancelled && setState({ loading: false, text: res.text, truncated: res.truncated }))
      .catch((e) => !cancelled && setState({ loading: false, error: e.message || "파일을 읽지 못했어요" }));
    return () => {
      cancelled = true;
    };
  }, [obj.key]);

  // JSON은 들여쓰기를 정규화한다 — 한 줄로 저장된 JSON이 그대로 한 줄로 보이면 읽을 수 없다.
  // 잘린 파일은 파싱이 안 되므로(중간에서 끊긴 JSON) 원문 그대로 둔다.
  const body = useMemo(() => {
    const raw = state.text || "";
    if (!json || !raw || state.truncated) return raw;
    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return raw;
    }
  }, [state.text, state.truncated, json]);

  const lines = useMemo(() => body.split("\n"), [body]);

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(body);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard 거부 — 조용히 무시 */
    }
  };

  return (
    <div
      style={{
        marginTop: 14,
        border: "1px solid var(--kd-border)",
        borderRadius: 4,
        overflow: "hidden",
      }}
    >
      <div
        className="flex items-center gap-2"
        style={{ height: 34, paddingInline: 12, borderBottom: "1px solid var(--kd-border)" }}
      >
        <span className="kd-t-micro text-fg-3">{json ? "JSON" : "TEXT"}</span>
        {state.truncated && (
          <span className="kd-t-micro text-fg-4">앞부분만</span>
        )}
        <button
          onClick={copyText}
          disabled={!body}
          className="kd-t-micro text-fg-2 hover:text-fg-1 ml-auto transition-colors disabled:opacity-40"
        >
          {copied ? "복사됨" : "복사"}
        </button>
      </div>

      <div
        className="overflow-auto scroll-thin"
        style={{ height: tall ? "min(58vh, 520px)" : 220, transition: "height 180ms ease" }}
      >
        {state.loading ? (
          <p className="kd-t-body-s text-fg-3" style={{ padding: 14 }}>
            불러오는 중…
          </p>
        ) : state.error ? (
          <p className="kd-t-body-s" style={{ padding: 14, color: "var(--err-fg)" }}>
            {state.error}
          </p>
        ) : !body ? (
          <p className="kd-t-body-s text-fg-3" style={{ padding: 14 }}>
            빈 파일이에요.
          </p>
        ) : json ? (
          // 줄 번호는 별도 칸 — 본문을 골라 복사할 때 번호가 섞이지 않는다.
          <div className="flex" style={{ minHeight: "100%" }}>
            <div
              className="kd-t-code text-right select-none shrink-0"
              style={{
                width: 44,
                paddingBlock: 12,
                paddingRight: 10,
                color: "var(--fg-4)",
                borderRight: "1px solid var(--kd-border)",
              }}
            >
              {lines.map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
            </div>
            <pre
              className="kd-t-code text-fg-1"
              style={{ margin: 0, paddingBlock: 12, paddingInline: 12, whiteSpace: "pre" }}
            >
              {body}
            </pre>
          </div>
        ) : (
          <pre
            className="kd-t-code text-fg-1"
            style={{
              margin: 0,
              padding: 12,
              whiteSpace: "pre-wrap",
              overflowWrap: "break-word",
            }}
          >
            {body}
          </pre>
        )}
      </div>
    </div>
  );
}

function InfoRow({ label, children }) {
  return (
    <div
      className="flex items-center gap-4"
      style={{ height: "var(--row-md)", borderBottom: "1px solid var(--kd-border)" }}
    >
      <span className="kd-t-body-s text-fg-3 shrink-0" style={{ width: 110 }}>
        {label}
      </span>
      <span className="kd-t-body-s text-fg-1 truncate tabular-nums">{children}</span>
    </div>
  );
}
