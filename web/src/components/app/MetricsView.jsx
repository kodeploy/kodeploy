// 앱 상세 · 작업 공간 · 모니터링 — design/{라이트,다크}모드-시안/26_모니터링_지표상세.png 기준.
//
// 좌: 지표 목록(현재 값 + 보조값) · 우: 고른 지표의 시계열 차트.
// 이전에는 서비스별 표 한 장이었는데, 시안이 "지표를 고르면 시간별 변화를 본다"는
// 마스터/디테일로 바뀌었다. 지표를 고르는 행위 자체가 차트를 바꾸는 게 핵심이다.
//
// 값은 전부 GET /deploy/app/metrics 실응답이다 (core/app/deploy/console/metrics.py):
//   range  — cpu memory rps latency_p50 latency_p95 error_rate net_rx net_tx restarts
//   instant— cpu_now mem_now cpu_limit mem_limit restart_total oom_total
// 포매터와 차트 그리기는 panels/MonitoringPanel.jsx에서 가져왔다(같은 규칙 유지).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { getAppMetrics } from "../../api/deploy.js";

// 백엔드가 받는 range 키 그대로. 시안의 "최근 1시간"이 기본.
const RANGES = [
  { id: "15m", label: "최근 15분" },
  { id: "1h", label: "최근 1시간" },
  { id: "6h", label: "최근 6시간" },
  { id: "1d", label: "최근 1일" },
  { id: "7d", label: "최근 7일" },
  { id: "30d", label: "최근 30일" },
];

const fmtCpu = (v) => `${(v * 1000).toFixed(0)}m`;
const fmtBytes = (b) =>
  b < 1024 ? `${b.toFixed(0)} B` : b < 1048576 ? `${(b / 1024).toFixed(0)} KiB` : `${(b / 1048576).toFixed(0)} MiB`;
const fmtRate = (b) =>
  b < 1024 ? `${b.toFixed(0)} B/s` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB/s` : `${(b / 1048576).toFixed(1)} MB/s`;
const fmtMs = (v) => (v < 1 ? "<1ms" : v < 1000 ? `${v.toFixed(0)}ms` : `${(v / 1000).toFixed(1)}s`);
const fmtRps = (v) => (v < 0.01 ? "0" : v < 1 ? v.toFixed(2) : v < 100 ? v.toFixed(1) : v.toFixed(0));
const pct = (cur, lim) => (lim ? `${Math.round((cur / lim) * 100)}%` : "—");
// 시안은 "164 / 512 MiB" — 단위가 같으면 뒤에 한 번만 붙인다.
const pairBytes = (cur, lim) => {
  const a = fmtBytes(cur);
  const b = fmtBytes(lim);
  const ua = a.slice(a.indexOf(" ") + 1);
  return ua === b.slice(b.indexOf(" ") + 1) ? `${a.slice(0, a.indexOf(" "))} / ${b}` : `${a} / ${b}`;
};
// 눈금은 데이터 최댓값이 아니라 그 위의 깔끔한 수(50m·100m·200m…)로 올린다.
const niceMax = (v) => {
  if (!(v > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  const f = v / e;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
};
const last = (a) => (a && a.length ? a[a.length - 1].value : 0);

function clock(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return { hm: `${p(d.getHours())}:${p(d.getMinutes())}`, md: `${p(d.getMonth() + 1)}/${p(d.getDate())}` };
}

function uptime(startedAt) {
  if (!startedAt) return "—";
  const s = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
  if (s < 0) return "—";
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}분`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}시간 ${m % 60}분`;
  return `${Math.floor(h / 24)}일 ${h % 24}시간`;
}

// 지표 정의 — 목록 행과 차트가 같은 정의를 쓴다.
// value/sub 는 목록에 보이는 현재 값, series 는 차트가 그릴 계열이다.
const METRICS = [
  {
    id: "cpu",
    label: "CPU",
    series: "cpu",
    fmt: fmtCpu,
    value: (d) => pct(d.cpu_now ?? 0, d.cpu_limit),
    sub: (d) => (d.cpu_limit ? `${fmtCpu(d.cpu_now ?? 0)} / ${fmtCpu(d.cpu_limit)}` : fmtCpu(d.cpu_now ?? 0)),
    caption: "시간별 CPU 사용량",
  },
  {
    id: "memory",
    label: "메모리",
    series: "memory",
    fmt: fmtBytes,
    value: (d) => pct(d.mem_now ?? 0, d.mem_limit),
    sub: (d) => (d.mem_limit ? pairBytes(d.mem_now ?? 0, d.mem_limit) : fmtBytes(d.mem_now ?? 0)),
    caption: "시간별 메모리 사용량",
  },
  {
    id: "rps",
    label: "초당 요청",
    series: "rps",
    fmt: (v) => `${fmtRps(v)}/s`,
    value: (d) => `${fmtRps(last(d.rps))}/s`,
    sub: () => "RPS",
    caption: "시간별 요청량",
  },
  {
    id: "latency",
    label: "응답 시간",
    series: "latency_p95",
    fmt: fmtMs,
    value: (d) => fmtMs(last(d.latency_p95)),
    sub: (d) => `p95 · p50 ${fmtMs(last(d.latency_p50))}`,
    caption: "시간별 응답 시간(p95)",
  },
  {
    id: "error",
    label: "오류 요청",
    series: "error_rate",
    fmt: (v) => `${fmtRps(v)}/s`,
    value: (d) => `${fmtRps(last(d.error_rate))}/s`,
    sub: () => "4xx + 5xx",
    caption: "시간별 오류 요청",
  },
  {
    id: "net",
    label: "네트워크",
    series: "net_rx",
    fmt: fmtRate,
    value: (d) => `수신 ${fmtRate(last(d.net_rx))}`,
    sub: (d) => `송신 ${fmtRate(last(d.net_tx))}`,
    caption: "시간별 네트워크 수신",
  },
];

const AUTO_REFRESH_MS = 30000;

export default function MetricsView({ slotStatus }) {
  const [range, setRange] = useState("1h");
  const [picked, setPicked] = useState("cpu");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [fetchedAt, setFetchedAt] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getAppMetrics(range);
      setData(res);
      setError(null);
    } catch (e) {
      setError(e.message || "메트릭 조회 실패");
    } finally {
      setLoading(false);
      setFetchedAt(new Date());
    }
  }, [range]);

  // 시안의 "자동 갱신 30초" — 구간을 바꾸면 즉시 다시 부르고 타이머도 새로 건다.
  useEffect(() => {
    load();
    const t = setInterval(load, AUTO_REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const metric = METRICS.find((m) => m.id === picked) || METRICS[0];
  const series = data?.[metric.series] || [];
  const started = slotStatus?.server?.started_at || slotStatus?.started_at;

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* ── 상단: 현재 상태 + 갱신 ── */}
      <div
        className="shrink-0 flex items-start gap-4 flex-wrap"
        style={{ padding: "16px 20px", borderBottom: "1px solid var(--kd-border)" }}
      >
        <div className="min-w-0">
          <h2 className="kd-t-section text-fg-1">현재 상태</h2>
          <p className="kd-t-caption text-fg-3" style={{ marginTop: 4 }}>
            {data
              ? `가동 ${uptime(started)} · 재시작 ${Math.floor(data.restart_total ?? 0)}회 · OOM ${Math.floor(
                  data.oom_total ?? 0,
                )}회`
              : "불러오는 중…"}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3 shrink-0">
          <span className="kd-t-caption text-fg-3">자동 갱신 30초</span>
          <button
            onClick={load}
            disabled={loading}
            className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5"
          >
            <RefreshCw size={15} strokeWidth={1.8} className={loading ? "kd-spin" : undefined} />
            새로고침
          </button>
        </div>
      </div>

      {error && (
        <div className="kd-t-caption shrink-0" style={{ padding: "10px 20px", color: "var(--err-fg)" }}>
          {error}
        </div>
      )}

      {/* ── 본문: 좌 지표 목록 / 우 차트 ── */}
      <div className="flex-1 min-h-0 flex flex-col lg:flex-row">
        <div
          className="lg:w-[38%] min-w-0 overflow-auto scroll-thin"
          style={{ borderRight: "1px solid var(--kd-border)" }}
        >
          <div
            className="flex items-center kd-t-micro text-fg-3"
            style={{ height: "var(--row-md)", paddingInline: 20, borderBottom: "1px solid var(--kd-border)" }}
          >
            <span className="flex-1">지표</span>
            <span style={{ width: 150 }}>현재 값</span>
          </div>
          {METRICS.map((m) => {
            const on = m.id === picked;
            return (
              <button
                key={m.id}
                onClick={() => setPicked(m.id)}
                aria-pressed={on}
                className="w-full flex items-center text-left kd-hoverable"
                style={{
                  paddingInline: 20,
                  paddingBlock: 12,
                  background: on ? "var(--sel-soft)" : "transparent",
                  borderBottom: "1px solid var(--kd-border)",
                }}
              >
                <span className="kd-t-body-s text-fg-1 flex-1 truncate">{m.label}</span>
                <span style={{ width: 150 }}>
                  <span className="kd-t-body-s kd-strong text-fg-1 block tabular-nums">
                    {data ? m.value(data) : "—"}
                  </span>
                  <span className="kd-t-micro text-fg-3 block tabular-nums" style={{ marginTop: 2 }}>
                    {data ? m.sub(data) : ""}
                  </span>
                </span>
                <ChevronRight size={15} strokeWidth={1.7} className="text-fg-4 shrink-0" />
              </button>
            );
          })}
        </div>

        <div className="flex-1 min-w-0 min-h-0 flex flex-col" style={{ padding: "16px 20px" }}>
          <div className="flex items-start gap-4 flex-wrap shrink-0">
            <div className="min-w-0">
              <h3 className="kd-t-subtitle text-fg-1">{metric.label} 사용량</h3>
              <p className="kd-t-caption text-fg-3" style={{ marginTop: 2 }}>
                {data ? `${metric.sub(data)} · ${metric.value(data)}` : "—"}
              </p>
            </div>
            <label className="ml-auto shrink-0">
              <span className="sr-only">조회 구간</span>
              <select
                className="kd-input"
                style={{ width: 132, height: 32 }}
                value={range}
                onChange={(e) => setRange(e.target.value)}
              >
                {RANGES.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {/* 차트는 남는 높이를 그대로 채운다. MetricChart가 실제 픽셀 크기로 좌표를
              잡기 때문에 늘어나도 곡선 비율이 깨지지 않는다. */}
          <div className="flex-1 min-h-0" style={{ marginTop: 14, minHeight: 200, maxHeight: 330 }}>
            <MetricChart data={series} fmt={metric.fmt} id={metric.id} />
          </div>

          <div className="flex items-center gap-4 shrink-0" style={{ marginTop: 12 }}>
            <span className="kd-t-caption text-fg-3">{metric.caption}</span>
            <span className="kd-t-caption text-fg-4 ml-auto tabular-nums">
              {fetchedAt
                ? `${String(fetchedAt.getHours()).padStart(2, "0")}:${String(fetchedAt.getMinutes()).padStart(2, "0")}:${String(
                    fetchedAt.getSeconds(),
                  ).padStart(2, "0")} 갱신`
                : ""}
            </span>
          </div>
        </div>
      </div>

      <div
        className="kd-t-caption text-fg-3 shrink-0"
        style={{ padding: "12px 20px", borderTop: "1px solid var(--kd-border)" }}
      >
        지표를 선택하면 시간별 변화를 확인할 수 있어요.
      </div>
    </div>
  );
}

// 시계열 한 장 — panels/MonitoringPanel.jsx의 ChartCard에서 그리기 로직을 가져왔다.
//
// viewBox를 실제 박스 픽셀과 1:1로 맞춘다. 고정 viewBox + preserveAspectRatio="none"으로
// 늘리면 글자와 곡선이 축마다 다른 배율로 찌그러진다.
function MetricChart({ data, fmt, id }) {
  const wrapRef = useRef(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [hover, setHover] = useState(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => {
      const r = e.contentRect;
      setBox({ w: Math.round(r.width), h: Math.round(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const geom = useMemo(() => {
    if (!data || data.length === 0 || box.w < 120 || box.h < 120) return null;
    const max = niceMax(Math.max(...data.map((d) => d.value)));
    const labelW = 48;
    const bottomH = 24;
    const padT = 10;
    const padR = 12;
    const x0 = labelW;
    const x1 = box.w - padR;
    const y0 = padT;
    const y1 = box.h - bottomH;
    const n = data.length;
    const pts = data.map((d, i) => [
      x0 + (i / (n - 1 || 1)) * (x1 - x0),
      y1 - (Math.min(d.value, max) / max) * (y1 - y0),
    ]);
    return { max, labelW, x0, x1, y0, y1, pts };
  }, [data, box.w, box.h]);

  const onMove = (e) => {
    if (!geom) return;
    const rect = wrapRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (x < geom.x0 - 8 || x > geom.x1 + 8) return setHover(null);
    const f = (x - geom.x0) / (geom.x1 - geom.x0 || 1);
    setHover(Math.max(0, Math.min(data.length - 1, Math.round(f * (data.length - 1)))));
  };

  const hoverPt = geom && hover !== null ? geom.pts[hover] : null;
  const hoverData = hover !== null ? data?.[hover] : null;

  return (
    <div ref={wrapRef} className="relative w-full h-full" style={{ minHeight: 180 }}>
      {!data || data.length === 0 ? (
        <div
          className="w-full h-full flex items-center justify-center kd-t-caption text-fg-4"
          style={{ borderRadius: 8, background: "var(--sel-soft)" }}
        >
          아직 수집된 데이터가 없어요.
        </div>
      ) : (
        geom && (
          <svg
            viewBox={`0 0 ${box.w} ${box.h}`}
            width={box.w}
            height={box.h}
            style={{ cursor: "crosshair", display: "block" }}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            role="img"
            aria-label={`${id} 시계열`}
          >
            {[1, 0.5, 0].map((f, i) => {
              const y = geom.y1 - f * (geom.y1 - geom.y0);
              return (
                <g key={i}>
                  <line x1={geom.labelW} y1={y} x2={box.w} y2={y} stroke="var(--kd-border)" strokeWidth="1" />
                  <text x={geom.labelW - 8} y={y + 4} textAnchor="end" fill="var(--fg-4)" fontSize="11">
                    {fmt(geom.max * f)}
                  </text>
                </g>
              );
            })}
            {[0, 0.25, 0.5, 0.75, 1].map((f, i, arr) => {
              const idx = Math.round(f * (data.length - 1));
              return (
                <text
                  key={`x${i}`}
                  x={geom.pts[idx][0]}
                  y={box.h - 6}
                  textAnchor={i === 0 ? "start" : i === arr.length - 1 ? "end" : "middle"}
                  fill="var(--fg-4)"
                  fontSize="11"
                >
                  {clock(data[idx].ts).hm}
                </text>
              );
            })}
            <path
              d={geom.pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")}
              fill="none"
              stroke="var(--chart-1)"
              strokeWidth="1.5"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {hoverPt && (
              <>
                <line x1={hoverPt[0]} y1={geom.y0} x2={hoverPt[0]} y2={geom.y1} stroke="var(--fg-4)" strokeWidth="1" />
                <circle cx={hoverPt[0]} cy={hoverPt[1]} r="3.5" fill="var(--chart-1)" />
              </>
            )}
          </svg>
        )
      )}
      {hoverPt && hoverData && (
        <div
          className="kd-t-micro absolute pointer-events-none whitespace-nowrap tabular-nums"
          style={{
            left: hoverPt[0],
            top: hoverPt[1],
            transform: "translate(-50%, -150%)",
            padding: "4px 9px",
            borderRadius: 6,
            background: "var(--kd-surface)",
            border: "1px solid var(--kd-border)",
            color: "var(--fg-1)",
            boxShadow: "var(--shadow-pop)",
          }}
        >
          {clock(hoverData.ts).hm} · {fmt(hoverData.value)}
        </div>
      )}
    </div>
  );
}
