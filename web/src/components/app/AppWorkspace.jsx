// 앱 상세 · 작업 공간 탭 — design/라이트모드-시안/10~13 기준.
//
// 화면은 카드 하나다. 헤더(앱 이름 + Pod 상태 + 뷰 탭 + 레이아웃 메뉴) 아래에 뷰 하나가 열린다.
//   터미널·로그 — 좌 터미널(대상 선택) / 우 런타임 로그   ← 들어오면 바로 이 뷰
//   데이터베이스 — SQL 편집기 + 조회 결과 (또는 DB 터미널)
//   스토리지    — 파일 목록 + 미리보기 (use_storage일 때만 탭이 뜬다)
//   모니터링    — 현재 사용량 표
//
// 치수 주석의 숫자는 시안 원본 px, 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
//   카드 폭 1426→982(kd-page 내부 폭) · 카드 헤더 87→60 · 패널 헤더 62→42(--row-lg)
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
import { useOutletContext, useSearchParams } from "react-router-dom";
import {
  AppWindow,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
  CircleCheck,
  CircleX,
  Copy,
  Database,
  Download,
  File as FileIcon,
  FileCode,
  Image as ImageIcon,
  Layers,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Play,
  RefreshCw,
  Search,
  Table2,
  TerminalSquare,
  Trash2,
} from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import {
  deleteStorageObject,
  getAppLogs,
  getAppMetrics,
  listStorageObjects,
  runDbQuery,
} from "../../api/deploy.js";
import { parseDate } from "../../lib/format.js";
import { xtermTheme } from "../../lib/xtermTheme.js";
import { useTheme } from "../../contexts/ThemeContext.jsx";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
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
  const { user, builds, serverBuild, slotStatus } = useOutletContext();
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
  // 확대 — 작업 공간만 화면을 꽉 채운다(다른 탭은 읽는 화면이라 그대로 둔다).
  // 상단바·탭바는 그대로 두고, 카드가 화면 폭 끝까지 + 화면 아래로도 넘치게 커진다
  // (넘친 만큼은 이 영역만 스크롤). 되돌리기는 같은 버튼 또는 Esc.
  const [wide, setWide] = useState(false);
  useEffect(() => {
    if (!wide) return;
    const onKey = (e) => e.key === "Escape" && setWide(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wide]);
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
      className={
        wide
          ? "kd-page-wide flex-1 min-h-0 flex flex-col overflow-y-auto scroll-thin"
          : "kd-page flex-1 min-h-0 flex flex-col"
      }
      style={wide ? { paddingTop: 10, paddingBottom: 12 } : { paddingTop: 30, paddingBottom: 40 }}
    >
      {/* 확대 중에는 높이를 화면 높이에 맞춰 못 박는다 — 기본 상태(화면 높이 − 상단바 −
          탭바 − 여백)보다 약 160px 크고, 화면 밖으로 나간 부분은 스크롤로 닿는다. */}
      <div
        className="kd-card flex-1 min-h-0 flex flex-col overflow-hidden"
        style={wide ? { flex: "none", height: "calc(100vh - 20px)" } : undefined}
      >
        {/* ── 카드 헤더 (시안 y196→283 = 60) ── */}
        <div
          className="shrink-0 flex items-center gap-5"
          style={{ height: 60, paddingInline: 20, borderBottom: "1px solid var(--kd-border)" }}
        >
          <h1 className="kd-t-subtitle text-fg-1 truncate">{user.app_name}</h1>
          <PodStatus status={slotStatus?.server?.status || slotStatus?.status} />

          <nav className="ml-auto flex items-center gap-6 shrink-0">
            {views.map((v) => (
              <button
                key={v.id}
                onClick={() => openView(v.id)}
                aria-pressed={view === v.id}
                className="kd-t-label flex flex-col items-center transition-colors"
                // 밑줄(2px)과 그 위 여백(6px)만큼 위를 띄워야 글자가 행 한가운데 온다
                style={{ color: view === v.id ? "var(--fg-1)" : "var(--fg-2)", paddingTop: 8 }}
              >
                {v.label}
                {/* 활성 밑줄 — 시안은 글자 아래 6px에 2px 바 */}
                <span
                  className="w-full"
                  style={{
                    height: 2,
                    marginTop: 6,
                    background: view === v.id ? "var(--accent)" : "transparent",
                  }}
                />
              </button>
            ))}
            {/* 확대 — 좌우는 화면 끝까지, 위아래는 화면 밖으로 넘치게 키운다 */}
            <span aria-hidden style={{ width: 1, height: 16, background: "var(--kd-border)" }} />
            <IconBtn
              icon={wide ? Minimize2 : Maximize2}
              label={wide ? "기본 크기" : "확대"}
              onClick={() => setWide((v) => !v)}
            />
          </nav>
        </div>

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

// Pod 상태 — 지금 살아 있나. 라벨은 AppStatusBadge의 맵이 단일 진실원.
function PodStatus({ status }) {
  if (!status) return null;
  const s = APP_STATUS_STYLES[status] || { label: status };
  const bad = status === "crashing";
  return (
    <span className="kd-t-label inline-flex items-center gap-2 text-fg-2 shrink-0">
      {bad ? (
        <CircleX size={18} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />
      ) : (
        <CircleCheck
          size={18}
          strokeWidth={1.6}
          style={{ color: status === "running" ? "var(--ok-fg)" : "var(--fg-4)" }}
        />
      )}
      {s.label}
    </span>
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
      className="shrink-0 flex items-center gap-3"
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
function MiniSelect({ value, options, onChange, dark, width }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const current = options.find((o) => o.id === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => !wrapRef.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
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
          borderRadius: 8,
          border: dark ? "none" : "1px solid var(--kd-border)",
          background: dark ? "transparent" : "var(--kd-surface)",
          color: dark ? "var(--term-fg)" : "var(--fg-1)",
        }}
      >
        <span className="truncate">{current?.label || "—"}</span>
        <ChevronDown size={15} strokeWidth={1.7} className="shrink-0" />
      </button>
      {open && (
        <div
          className="absolute left-0 z-30 kd-card"
          style={{ top: 34, minWidth: Math.max(width || 0, 140), paddingBlock: 6 }}
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
        </div>
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
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      theme: xtermTheme(theme),
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

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
                borderRadius: 8,
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
          style={{ padding: 2, borderRadius: 8, background: "var(--sel-soft)" }}
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
        <SqlConsole />
      </div>
      <div className="flex-1 min-h-0 flex-col" style={{ display: mode === "terminal" ? "flex" : "none" }}>
        <DbTerminalPanel bare />
      </div>
    </div>
  );
}

function SqlConsole() {
  const [sql, setSql] = useState("");
  const [result, setResult] = useState(null);
  const [executed, setExecuted] = useState(""); // 실행/페이징 중인 쿼리(편집과 분리)
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [tall, setTall] = useState(false);
  const [sort, setSort] = useState(null);
  const gutterRef = useRef(null);

  // 한 페이지 fetch — 실행(offset 0)과 페이지 이동이 공유한다.
  const fetchPage = async (q, offset) => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const res = await runDbQuery(q, offset);
      setResult(res);
      setExecuted(q);
    } catch (e) {
      setError(e.message || "쿼리 실패");
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const run = () => {
    const q = sql.trim();
    if (!q || loading) return;
    setFilter(""); // 새 쿼리마다 결과 필터·정렬 초기화
    setSort(null);
    fetchPage(q, 0);
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

  // 여러 페이지짜리 결과인지 — 이전/다음 버튼 노출 조건
  const multiPage = hasTable && result.paginated && (result.offset > 0 || result.has_more);
  const lineCount = Math.max(5, sql.split("\n").length);

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

        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          onScroll={(e) => {
            // 줄번호는 별도 박스라 스크롤을 따라가게 맞춰 준다
            if (gutterRef.current) gutterRef.current.scrollTop = e.target.scrollTop;
          }}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
          spellCheck={false}
          placeholder="SELECT id, title FROM posts ORDER BY id DESC LIMIT 5;"
          className="kd-t-code flex-1 min-w-0 resize-none bg-transparent outline-none scroll-thin"
          style={{ paddingTop: 30, paddingLeft: 14, paddingRight: 120, color: "var(--term-fg)" }}
        />

        <div className="absolute" style={{ right: 14, top: 10 }}>
          <IconBtn
            dark
            icon={tall ? Minimize2 : Maximize2}
            label={tall ? "편집기 줄이기" : "편집기 넓게 보기"}
            onClick={() => setTall((v) => !v)}
          />
        </div>

        <div className="absolute flex items-center gap-3" style={{ right: 14, bottom: 12 }}>
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

      {/* ── 조회 결과 ── */}
      <div
        className="shrink-0 flex items-center gap-3"
        style={{ height: 54, paddingInline: 20, borderBottom: "1px solid var(--kd-border)" }}
      >
        <h2 className="kd-t-section text-fg-1 shrink-0">조회 결과</h2>
        <span className="kd-t-body-s text-fg-3 shrink-0 tabular-nums">
          {error
            ? "실패"
            : result
              ? [
                  filter.trim() ? `${rows.length} / ${result.row_count}행` : `${result.row_count}행`,
                  `${result.duration_ms}ms`,
                ].join(" · ")
              : "—"}
        </span>

        {multiPage && (
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

        <div className="ml-auto relative shrink-0" style={{ width: 230 }}>
          <Search size={15} strokeWidth={1.7} className="absolute text-fg-4" style={{ left: 10, top: 10 }} />
          <input
            className="kd-input"
            style={{ height: 36, paddingLeft: 32 }}
            placeholder="결과 내 검색"
            value={filter}
            spellCheck={false}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto scroll-thin">
        {error ? (
          <pre
            className="kd-t-code whitespace-pre-wrap"
            style={{
              margin: 20,
              padding: 14,
              borderRadius: 8,
              border: "1px solid var(--kd-border)",
              color: "var(--err-fg)",
            }}
          >
            {error}
          </pre>
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
    </div>
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
// 3) 스토리지 — 파일 목록 + 미리보기 (use_storage일 때만 탭이 뜬다)
// 목록·삭제 호출과 이미지 판정은 panels/StoragePanel.jsx에서 가져왔다.
// ────────────────────────────────────────────────────────────────────────────
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp", ".ico"];
const isImage = (key) => IMAGE_EXTS.some((ext) => key.toLowerCase().endsWith(ext));
// 브라우저가 그대로 열 수 있는 형식은 미리보기를 붙인다.
// iframe으로 파일 주소를 그대로 띄우므로 CORS 설정이 필요 없다(내려받아 그리는 게 아니다).
const TEXT_EXTS = [".json", ".txt", ".md", ".csv", ".log", ".yaml", ".yml", ".xml", ".ini", ".toml"];
function previewKind(key) {
  const k = key.toLowerCase();
  if (isImage(key)) return "image";
  if (k.endsWith(".pdf")) return "pdf";
  if (TEXT_EXTS.some((ext) => k.endsWith(ext))) return "text";
  return "none";
}

function fileIcon(key) {
  if (isImage(key)) return ImageIcon;
  if (/\.(json|ya?ml|toml|js|ts|jsx|tsx|css|html|py|java|php|sh|sql)$/i.test(key)) return FileCode;
  return FileIcon;
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

  const selected = rows.find((o) => o.key === selectedKey) || rows[0] || null;

  const onDeleted = (key) => {
    setObjects((prev) => prev.filter((o) => o.key !== key));
    if (selectedKey === key) setSelectedKey(null);
  };

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
        <button
          onClick={load}
          disabled={loading}
          className="kd-btn-secondary kd-btn-sm ml-auto inline-flex items-center gap-1.5 disabled:opacity-50"
        >
          <RefreshCw size={15} strokeWidth={1.8} className={loading ? "kd-spin" : ""} />
          새로고침
        </button>
      </div>

      <div className="flex-1 min-h-0 flex">
        {/* 좌: 목록 (시안 802:622 → 56%) */}
        <div className="min-h-0 flex flex-col" style={{ width: "56%", flexShrink: 0 }}>
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

          <div className="flex-1 min-h-0 overflow-auto scroll-thin">
            {error ? (
              <p className="kd-t-body-s" style={{ padding: 20, color: "var(--err-fg)" }}>
                {error}
              </p>
            ) : !loaded && loading ? (
              <Centered>불러오는 중…</Centered>
            ) : rows.length === 0 ? (
              <Centered>올라온 파일이 없어요.</Centered>
            ) : (
              <>
                {rows.map((o) => {
                  const Icon = fileIcon(o.key);
                  const on = selected?.key === o.key;
                  return (
                    <button
                      key={o.key}
                      onClick={() => setSelectedKey(o.key)}
                      className="w-full grid items-center text-left"
                      style={{
                        gridTemplateColumns: STORAGE_COLS,
                        height: "var(--row-lg)",
                        paddingInline: 20,
                        borderBottom: "1px solid var(--kd-border)",
                        background: on ? "var(--sel-soft)" : "transparent",
                        boxShadow: on ? "inset 3px 0 0 0 var(--accent)" : "none",
                      }}
                    >
                      <span className="kd-t-body-s text-fg-1 flex items-center gap-3 min-w-0">
                        <Icon size={18} strokeWidth={1.5} className="shrink-0 text-fg-2" />
                        <span className="truncate">{o.key}</span>
                      </span>
                      <span className="kd-t-body-s text-fg-2 tabular-nums">{fmtBytes(o.size)}</span>
                      <span className="kd-t-body-s text-fg-2 tabular-nums">
                        {fmtDateTime(o.last_modified)}
                      </span>
                    </button>
                  );
                })}
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

        <span style={{ width: 1, background: "var(--kd-border)" }} />

        {/* 우: 미리보기 */}
        <div className="flex-1 min-w-0 min-h-0 overflow-auto scroll-thin flex flex-col">
          {selected ? (
            <StoragePreview obj={selected} onDeleted={onDeleted} />
          ) : (
            <Centered>왼쪽에서 파일을 고르세요.</Centered>
          )}
        </div>
      </div>
    </div>
  );
}

function StoragePreview({ obj, onDeleted }) {
  const [copied, setCopied] = useState(false);
  const [menu, setMenu] = useState(false);
  const [deleting, setDeleting] = useState(false);
  // 기본 미리보기는 152px에 object-cover라 사진이 잘린다. "크게 보기"를 켜면
  // 오른쪽 칸을 채우고 object-contain으로 바꿔 사진 전체가 보이게 한다.
  const [big, setBig] = useState(false);
  const Icon = fileIcon(obj.key);
  const kind = obj.url ? previewKind(obj.key) : "none";
  const img = kind === "image";
  const framed = kind === "pdf" || kind === "text";

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
        <h2 className="kd-t-subtitle text-fg-1 truncate">{obj.key.split("/").pop()}</h2>
        <div className="relative ml-auto shrink-0 flex items-center gap-1">
          {(img || framed) && (
            <IconBtn
              icon={big ? Minimize2 : Maximize2}
              label={big ? "원래 크기" : "크게 보기"}
              onClick={() => setBig((v) => !v)}
            />
          )}
          <IconBtn icon={MoreHorizontal} label="파일 작업" onClick={() => setMenu((v) => !v)} />
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

      <div
        className="w-full flex items-center justify-center overflow-hidden"
        style={{
          marginTop: 14,
          // 크게 보기: 오른쪽 칸 높이를 채우되 화면을 넘지 않게 상한을 둔다
          height: big ? "min(58vh, 520px)" : 152,
          borderRadius: 8,
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
          // PDF는 브라우저 PDF 뷰어가, 텍스트(JSON 등)는 브라우저가 그대로 그린다.
          <iframe
            src={obj.url}
            title={`${obj.key} 미리보기`}
            className="w-full h-full"
            style={{ border: 0, background: "var(--kd-surface)" }}
            sandbox={kind === "pdf" ? undefined : ""}
          />
        )}
        {kind === "none" && <Icon size={32} strokeWidth={1.3} className="text-fg-4" />}
      </div>

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
