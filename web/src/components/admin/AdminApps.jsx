// 관리자 — 전체 앱 표. 행을 누르면 선택 스택과 Pod 상태가 펼쳐지고, 앱 화면으로 들어갈 수 있다.
// 앱 화면에서 root는 주인과 같고(삭제·터미널·재배포), admin은 보기만 한다 — 권한은 서버가 정한다(apps/sharing.py).
import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, ChevronDown } from "lucide-react";
import { getAdminApp } from "../../api/admin.js";
import { relativeTime } from "../../lib/format.js";
import { BUILD_STATUS_COLORS, BUILD_STATUS_LABEL, Hint, TableHead } from "./atoms.jsx";

const RUNTIME_LABEL = { python: "Python", java: "Java", php: "PHP", javascript: "JavaScript", go: "Go", static: "정적" };

export default function AdminApps({ apps, me }) {
  const [expanded, setExpanded] = useState(null);
  const [details, setDetails] = useState({});   // id별 캐시

  if (apps === null) return <Hint>앱 목록을 불러오는 중…</Hint>;

  const toggle = (a) => {
    const next = expanded === a.id ? null : a.id;
    setExpanded(next);
    if (next && !details[a.id]) {
      getAdminApp(a.id)
        .then((d) => setDetails((prev) => ({ ...prev, [a.id]: d })))
        .catch((e) => setDetails((prev) => ({ ...prev, [a.id]: { error: e.message || "조회 실패" } })));
    }
  };

  return (
    <div className="kd-table-wrap overflow-x-auto scroll-thin" style={{ marginTop: 14 }}>
      <table className="w-full kd-t-body-s" style={{ borderCollapse: "collapse" }}>
        <TableHead cols={["앱", "주인", "경로", "멤버", "마지막 배포", "만든 날"]} />
        <tbody>
          {apps.map((a) => (
            <AppRow
              key={a.id}
              app={a}
              me={me}
              expanded={expanded === a.id}
              detail={details[a.id]}
              onToggle={() => toggle(a)}
            />
          ))}
          {apps.length === 0 && (
            <tr>
              <td colSpan={6} className="px-4 py-8 text-center kd-t-caption text-fg-3">
                앱이 없어요.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function AppRow({ app, me, expanded, detail, onToggle }) {
  const last = app.last_build;
  return (
    <>
      <tr
        onClick={onToggle}
        className="text-fg-2 transition-colors"
        style={{
          borderBottom: "1px solid var(--line-1)",
          cursor: "pointer",
          background: expanded ? "var(--sel-soft)" : "transparent",
        }}
        title={expanded ? "상세 접기" : "상세 보기"}
      >
        <td className="px-4" style={{ height: "var(--row-lg)" }}>
          <div className="flex items-center gap-2 min-w-0">
            <div className="min-w-0">
              <div className="kd-strong truncate" style={{ color: "var(--fg-1)" }}>{app.name}</div>
              <div className="kd-t-code text-fg-3 whitespace-nowrap">{app.namespace}</div>
            </div>
            <ChevronDown
              size={14}
              strokeWidth={1.8}
              className="text-fg-3 transition-transform shrink-0"
              style={{ transform: expanded ? "rotate(180deg)" : "none" }}
            />
          </div>
        </td>
        <td className="px-4">{app.owner_login || <span className="text-fg-4">—</span>}</td>
        <td className="px-4">
          <span className="kd-chip" style={{ color: app.pipeline === "v2" ? "var(--accent)" : "var(--fg-3)" }}>
            {app.pipeline}
          </span>
        </td>
        <td className="px-4 tabular-nums">{app.member_count}</td>
        <td className="px-4 whitespace-nowrap">
          {last ? (
            <span className="inline-flex items-center gap-2">
              <span className="kd-strong" style={{ color: BUILD_STATUS_COLORS[last.status] || "var(--fg-3)" }}>
                {BUILD_STATUS_LABEL[last.status] || last.status}
              </span>
              <span className="text-fg-3 tabular-nums">{relativeTime(last.created_at)}</span>
            </span>
          ) : (
            <span className="text-fg-4">배포 전</span>
          )}
        </td>
        <td className="px-4 text-fg-3 tabular-nums whitespace-nowrap">{relativeTime(app.created_at)}</td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: "1px solid var(--kd-border)" }}>
          <td colSpan={6} className="px-4 py-4" style={{ background: "var(--sel-soft)" }}>
            <AppDetail app={app} detail={detail} me={me} />
          </td>
        </tr>
      )}
    </>
  );
}

// 펼침 — 앱 화면 들어가기 + 선택 스택 chips + Pod 상태 표.
function AppDetail({ app, detail, me }) {
  const isRoot = me.role === "root";
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <Link
          to={`/apps/${app.id}`}
          className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5 no-underline"
        >
          앱 화면 열기
          <ArrowRight size={14} strokeWidth={1.8} />
        </Link>
        <span className="kd-t-caption text-fg-3">
          {isRoot
            ? "주인 권한으로 열려요 — 삭제는 앱 설정, 터미널은 터미널 탭에서. 바꾼 것은 기록 탭에 남아요."
            : "보기 전용으로 열려요."}
        </span>
      </div>
      <Stack detail={detail} />
    </div>
  );
}

function Stack({ detail }) {
  if (!detail) return <Hint>앱 정보를 불러오는 중…</Hint>;
  if (detail.error)
    return (
      <div className="kd-t-caption" style={{ color: "var(--err-fg)" }}>
        {detail.error}
      </div>
    );

  const cfg = detail.config;
  // 선택 스택 → chips. db "none"/redis off/storage off는 표시 안 함.
  const chips = [];
  if (cfg) {
    chips.push({ label: RUNTIME_LABEL[cfg.runtime] || cfg.runtime, color: "var(--brand-fg)" });
    if (cfg.db_type && cfg.db_type !== "none")
      chips.push({ label: cfg.db_type === "mysql" ? "MySQL" : "PostgreSQL", color: "var(--info-fg)" });
    if (cfg.use_redis) chips.push({ label: "Redis", color: "var(--err-fg)" });
    if (cfg.use_storage) chips.push({ label: "스토리지 (R2)", color: "var(--warn-fg)" });
    if (cfg.volume_mount_path) chips.push({ label: "스토리지 (로컬)", color: "var(--warn-fg)" });
    chips.push({ label: cfg.build_mode === "auto" ? "auto (nixpacks)" : "Dockerfile", color: "var(--fg-3)" });
  }

  return (
    <>
      {cfg && (
        <>
          <div className="flex items-center gap-1.5 flex-wrap">
            {chips.map((c) => (
              <span key={c.label} className="kd-chip" style={{ color: c.color }}>
                {c.label}
              </span>
            ))}
            <span className="kd-t-caption text-fg-3 ml-1">포트 {cfg.port}</span>
          </div>
          <div className="kd-t-caption text-fg-3">
            <a
              href={cfg.repo_url.replace(/\.git$/, "")}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:underline"
              style={{ color: "var(--accent)" }}
            >
              {cfg.repo_url.replace(/^https?:\/\/(www\.)?github\.com\//, "").replace(/\.git$/, "")}
            </a>
            <span className="text-fg-4"> · {cfg.branch} 브랜치 · 마지막 서버 빌드 {relativeTime(cfg.created_at)}</span>
          </div>
        </>
      )}
      {detail.pods.length > 0 ? (
        <div className="kd-table-wrap overflow-x-auto scroll-thin">
          <table className="w-full kd-t-caption" style={{ borderCollapse: "collapse" }}>
            <TableHead cols={["Pod", "컴포넌트", "상태", "재시작", "시작"]} height="var(--row-sm)" className="px-3" />
            <tbody>
              {detail.pods.map((p) => (
                <tr key={p.name} className="text-fg-2" style={{ borderBottom: "1px solid var(--kd-border)" }}>
                  <td className="px-3 font-mono" style={{ color: "var(--fg-1)" }}>
                    {p.name}
                  </td>
                  <td className="px-3 text-fg-3">{p.component || "—"}</td>
                  <td className="px-3">
                    <span
                      style={{
                        color:
                          p.phase === "Running" && p.ready
                            ? "var(--ok-fg)"
                            : p.phase === "Running"
                              ? "var(--warn-fg)"
                              : "var(--err-fg)",
                        fontWeight: 590,
                      }}
                    >
                      {p.phase}
                      {p.phase === "Running" && !p.ready && " (NotReady)"}
                    </span>
                  </td>
                  <td className="px-3 tabular-nums">{p.restarts}</td>
                  <td className="px-3 text-fg-3 tabular-nums whitespace-nowrap">
                    {p.started_at ? relativeTime(p.started_at) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Hint>{cfg ? "앱 네임스페이스에 실행 중인 Pod이 없어요." : "아직 배포하지 않은 앱이에요."}</Hint>
      )}
    </>
  );
}
