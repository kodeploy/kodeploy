// 관리자 페이지 (/admin) — role admin/root만 진입 (TopBar 링크도 동일 조건).
//
// - 통계 카드: 가입자 · 배포된 앱 · 총 빌드(성공/실패) · 최근 24h
//   "총 빌드" 클릭 → 빌드 기록 테이블 토글 (단계별 소요시간 포함)
// - 노드: CPU/메모리/디스크 사용량 바 (kubelet stats/summary, 30s 폴링)
//   노드 카드 클릭 → 그 노드 Pod별 사용량+limit 테이블 펼침
// - 가입자 테이블: tenant · 앱 · 도메인 · 빌드 수 · 마지막 빌드 · 등급
//   등급 select는 root에게만, 대상이 root/본인이면 잠김.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import {
  getNodePods,
  getNodes,
  getOverview,
  getUserTenant,
  listBuildRecords,
  listUsers,
  setUserRole,
} from "../api/admin.js";
import { useAuth } from "../contexts/AuthContext.jsx";
import { relativeTime } from "../lib/format.js";

const ADMIN_ROLES = ["admin", "root"];
const NODE_POLL_MS = 30000;

const GiB = 1024 ** 3;
const MiB = 1024 ** 2;
const fmtGiB = (bytes) =>
  bytes == null ? "—" : `${(bytes / GiB).toFixed(1)}Gi`;
// Pod 단위는 MiB가 읽기 좋음 (수십~수백 Mi). 1GiB 넘으면 Gi로.
const fmtMem = (bytes) => {
  if (bytes == null) return "—";
  if (bytes >= GiB) return `${(bytes / GiB).toFixed(2)}Gi`;
  return `${Math.round(bytes / MiB)}Mi`;
};
const fmtCores = (cores) => (cores == null ? "—" : cores.toFixed(2));
// Pod CPU는 millicores가 직관적 (0.0032 cores → 3m)
const fmtMilli = (cores) =>
  cores == null ? "—" : `${Math.round(cores * 1000)}m`;
const fmtDuration = (sec) => {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m > 0 ? `${m}분 ${s}초` : `${s}초`;
};

const BUILD_STATUS_COLORS = {
  running: "var(--ok-fg)",   // 성공 (롤아웃 완료)
  failed: "var(--err-fg)",
  cancelled: "var(--fg-3)",
  building: "var(--warn-fg)",  // 진행 중 (아직 마감 안 됨)
};

const ROLE_COLORS = {
  root: "var(--warn-fg)",
  admin: "var(--accent)",
  user: "var(--fg-3)",
};

export default function Admin() {
  const navigate = useNavigate();
  const { user, loading: authLoading, openLogin } = useAuth();
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [nodes, setNodes] = useState(null);               // null=로딩
  const [error, setError] = useState(null);
  // "총 빌드" 카드 드릴다운 — 열 때 1회 fetch 후 캐시 (닫았다 열어도 재요청 X)
  const [showBuilds, setShowBuilds] = useState(false);
  const [buildRecords, setBuildRecords] = useState(null);
  // 유저 row 드릴다운 — 한 번에 한 명만 펼침. 상세는 id별 캐시.
  const [expandedUser, setExpandedUser] = useState(null);
  const [tenantDetails, setTenantDetails] = useState({});

  const isAdmin = user && ADMIN_ROLES.includes(user.role);

  // 가드 — 미로그인은 로그인 유도, 일반 user는 홈으로.
  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      openLogin?.();
      navigate("/", { replace: true });
      return;
    }
    if (!ADMIN_ROLES.includes(user.role)) {
      navigate("/", { replace: true });
    }
  }, [authLoading, user, openLogin, navigate]);

  // 통계 + 유저 목록 — 마운트 시 1회 (수동 새로고침은 노드 영역 버튼).
  useEffect(() => {
    if (!isAdmin) return;
    Promise.all([getOverview(), listUsers()])
      .then(([ov, us]) => {
        setOverview(ov);
        setUsers(us);
        setError(null);
      })
      .catch((e) => setError(e.message || "조회 실패"));
  }, [isAdmin]);

  // 빌드 기록 — 드릴다운 첫 오픈 시 fetch.
  useEffect(() => {
    if (!isAdmin || !showBuilds || buildRecords !== null) return;
    listBuildRecords()
      .then(setBuildRecords)
      .catch(() => setBuildRecords([]));
  }, [isAdmin, showBuilds, buildRecords]);

  // 노드 리소스 — 30초 폴링.
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const data = await getNodes();
        if (cancelled) return;
        setNodes(data);
      } catch {
        if (cancelled) return;
        setNodes([]);
      }
      timer = setTimeout(tick, NODE_POLL_MS);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isAdmin]);

  if (authLoading || !isAdmin) return null;

  // 유저 row 클릭 — 펼침 토글 + 첫 오픈 시 테넌트 상세 fetch.
  const toggleUser = (u) => {
    const next = expandedUser === u.id ? null : u.id;
    setExpandedUser(next);
    if (next && !tenantDetails[u.id]) {
      getUserTenant(u.id)
        .then((d) => setTenantDetails((prev) => ({ ...prev, [u.id]: d })))
        .catch((e) =>
          setTenantDetails((prev) => ({
            ...prev,
            [u.id]: { error: e.message || "조회 실패" },
          })),
        );
    }
  };

  const handleRoleChange = async (target, role) => {
    try {
      await setUserRole(target.id, role);
      setUsers((prev) =>
        prev.map((u) => (u.id === target.id ? { ...u, role } : u)),
      );
    } catch (e) {
      setError(e.message || "등급 변경 실패");
    }
  };

  return (
    <div className="kd-page kd-fade-in" style={{ paddingTop: 28, paddingBottom: 72 }}>
      <h1 className="kd-t-title text-fg-1">관리자</h1>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 6 }}>
        가입 · 빌드 · 노드 현황
      </p>

      {error && (
        <div
          className="kd-t-caption"
          style={{
            marginTop: 16,
            padding: "10px 14px",
            borderRadius: 8,
            border: "1px solid var(--kd-border)",
            color: "var(--err-fg)",
          }}
        >
          {error}
        </div>
      )}

      {/* 통계 — 카드 한 장을 세로 괘선으로 4칸 나눈다(랜딩 피처 3열과 같은 규칙) */}
      <div className="kd-card kd-stat-row" style={{ marginTop: 26 }}>
        <StatCard
          label="가입자"
          value={overview?.users.total}
          sub={`7일 +${overview?.users.signups_7d ?? "—"}`}
        />
        <StatCard
          label="배포된 앱"
          value={overview?.users.with_app}
          sub="app_name 보유 유저"
        />
        <StatCard
          label="총 빌드"
          value={overview?.builds.total}
          sub={`성공 ${overview?.builds.succeeded ?? "—"} · 실패 ${overview?.builds.failed ?? "—"}`}
          onClick={() => setShowBuilds((v) => !v)}
          active={showBuilds}
        />
        <StatCard
          label="최근 24시간"
          value={overview?.builds.last_24h}
          sub={`평균 성공 소요 ${fmtDuration(overview?.builds.avg_success_seconds)}`}
        />
      </div>

      {/* 빌드 기록 — "총 빌드" 카드 토글 */}
      {showBuilds && (
        <>
          <SectionTitle title="빌드 기록 (최근 100건)" />
          <div className="mb-10">
            <BuildRecordsTable records={buildRecords} />
          </div>
        </>
      )}

      {/* 노드 현황 */}
      <SectionTitle title="노드" />
      <div className="flex flex-col gap-3 mb-10">
        {nodes === null && <Hint>노드 정보를 불러오는 중…</Hint>}
        {nodes?.length === 0 && <Hint>노드 정보를 불러오지 못했어요.</Hint>}
        {nodes?.map((n) => (
          <NodeCard key={n.name} node={n} />
        ))}
      </div>

      {/* 가입자 테이블 */}
      <SectionTitle title="가입자" />
      <div className="kd-table-wrap" style={{ marginTop: 14 }}>
        <table className="w-full kd-t-body-s" style={{ borderCollapse: "collapse" }}>
          <thead>
            <tr className="kd-t-micro text-fg-3 text-left">
              {["유저", "등급", "앱 / 테넌트", "도메인", "빌드", "마지막 빌드", "가입"].map(
                (h) => (
                  <th
                    key={h}
                    className="px-4"
                    style={{ height: "var(--row-md)", borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <UserRow
                key={u.id}
                u={u}
                me={user}
                expanded={expandedUser === u.id}
                detail={tenantDetails[u.id]}
                onToggle={() => toggleUser(u)}
                onRoleChange={handleRoleChange}
              />
            ))}
            {users.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center kd-t-caption text-fg-3">
                  가입자가 없어요.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// 섹션 제목 + 아래 괘선 — 앱 개요(AppOverview.Section)와 같은 모양.
function SectionTitle({ title, action }) {
  return (
    <div
      className="flex items-baseline gap-4"
      style={{ marginTop: 34, paddingBottom: 12, borderBottom: "1px solid var(--kd-border)" }}
    >
      <h2 className="kd-t-section text-fg-1">{title}</h2>
      {action}
    </div>
  );
}

function Hint({ children }) {
  return (
    <div className="kd-t-caption text-fg-3" style={{ paddingBlock: 16 }}>
      {children}
    </div>
  );
}

// onClick 있으면 클릭 가능 카드 (총 빌드 → 기록 토글). active면 강조 테두리.
function StatCard({ label, value, sub, onClick, active }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      onClick={onClick}
      className={`kd-stat-cell text-left ${onClick ? "kd-hoverable" : ""}`}
      style={{ background: active ? "var(--sel-soft)" : "transparent" }}
    >
      <div className="kd-t-micro text-fg-3 flex items-center gap-1">
        {label}
        {onClick && (
          <ChevronDown
            size={14}
            strokeWidth={1.8}
            className="transition-transform"
            style={{ transform: active ? "rotate(180deg)" : "none" }}
          />
        )}
      </div>
      <div className="kd-t-title text-fg-1 tabular-nums" style={{ marginTop: 6 }}>
        {value ?? "—"}
      </div>
      <div className="kd-t-caption text-fg-3 truncate" style={{ marginTop: 4 }}>
        {sub}
      </div>
    </Tag>
  );
}

// 빌드 기록 테이블 — build_records 최신순. 단계 시간(nixpacks/buildkit)은
// dockerfile 모드나 타임아웃이면 비어 있을 수 있음 ("—").
function BuildRecordsTable({ records }) {
  if (records === null) return <Hint>빌드 기록을 불러오는 중…</Hint>;
  if (records.length === 0) return <Hint>빌드 기록이 없어요.</Hint>;
  return (
    <div className="kd-table-wrap overflow-x-auto scroll-thin" style={{ marginTop: 14 }}>
      <table className="w-full kd-t-caption" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr className="kd-t-micro text-fg-3 text-left">
            {["시작", "유저", "앱", "#", "모드", "nixpacks", "buildkit", "총", "상태"].map((h) => (
              <th
                key={h}
                className="px-3 whitespace-nowrap"
                style={{ height: "var(--row-md)", borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((r) => (
            <tr
              key={r.id}
              className="text-fg-2"
              style={{ borderBottom: "1px solid var(--kd-border)" }}
              title={r.error || undefined}
            >
              <td className="px-3 text-fg-3 tabular-nums whitespace-nowrap">
                {relativeTime(r.started_at)}
              </td>
              <td className="px-3" className="kd-strong" style={{ color: "var(--fg-1)" }}>
                {r.login}
              </td>
              <td className="px-3">{r.app_name}</td>
              <td className="px-3 tabular-nums text-fg-3">#{r.seq}</td>
              <td className="px-3 text-fg-3">
                {r.build_mode === "auto" ? "auto" : "dockerfile"}
              </td>
              <td className="px-3 tabular-nums">{fmtDuration(r.nixpacks_seconds)}</td>
              <td className="px-3 tabular-nums">{fmtDuration(r.buildkit_seconds)}</td>
              <td className="px-3 tabular-nums" className="kd-strong" style={{ color: "var(--fg-1)" }}>
                {fmtDuration(r.total_seconds)}
              </td>
              <td className="px-3">
                <span
                  className="kd-strong"
                  style={{ color: BUILD_STATUS_COLORS[r.status] || "var(--fg-3)" }}
                >
                  {r.status === "running" ? "성공" : r.status === "failed" ? "실패" : r.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 가입자 row + 클릭 펼침 (테넌트 상세). 등급 select·앱 링크 클릭은 토글에 안 걸리게 차단.
function UserRow({ u, me, expanded, detail, onToggle, onRoleChange }) {
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
        title={expanded ? "테넌트 상세 접기" : "테넌트 상세 보기"}
      >
        <td className="px-4" style={{ height: "var(--row-lg)" }}>
          <div className="flex items-center gap-2 min-w-0">
            {u.avatar_url && (
              <img
                src={u.avatar_url}
                alt=""
                className="w-[22px] h-[22px] rounded-full shrink-0"
                style={{ border: "1px solid var(--kd-border)" }}
              />
            )}
            <span className="truncate" className="kd-strong" style={{ color: "var(--fg-1)" }}>
              {u.login}
            </span>
            <ChevronDown
              size={14}
              strokeWidth={1.8}
              className="text-fg-3 transition-transform shrink-0"
              style={{ transform: expanded ? "rotate(180deg)" : "none" }}
            />
          </div>
        </td>
        <td className="px-4" style={{ height: "var(--row-lg)" }} onClick={(e) => e.stopPropagation()}>
          <RoleCell user={u} me={me} onChange={onRoleChange} />
        </td>
        <td className="px-4" style={{ height: "var(--row-lg)" }}>
          {u.app_name ? (
            <div className="min-w-0">
              <a
                href={`https://${u.app_name}.kodeploy.com`}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline"
                style={{ color: "var(--accent)", fontWeight: 510 }}
                onClick={(e) => e.stopPropagation()}
              >
                {u.app_name}
              </a>
              <div className="kd-t-code text-fg-3">{u.tenant_id}</div>
            </div>
          ) : (
            <span className="text-fg-4">—</span>
          )}
        </td>
        <td className="px-4" style={{ height: "var(--row-lg)" }}>
          {u.custom_domain || <span className="text-fg-4">—</span>}
        </td>
        <td className="px-4 tabular-nums">{u.build_count}</td>
        <td className="px-4 text-fg-3 tabular-nums">
          {u.last_build_at ? relativeTime(u.last_build_at) : "—"}
        </td>
        <td className="px-4 text-fg-3 tabular-nums">
          {relativeTime(u.created_at)}
        </td>
      </tr>
      {expanded && (
        <tr style={{ borderBottom: "1px solid var(--kd-border)" }}>
          <td colSpan={7} className="px-4 py-4" style={{ background: "var(--sel-soft)" }}>
            <TenantDetail detail={detail} />
          </td>
        </tr>
      )}
    </>
  );
}

// 펼침 내용 — 선택 스택 chips + 테넌트 Pod 상태 테이블.
function TenantDetail({ detail }) {
  if (!detail) return <Hint>테넌트 정보를 불러오는 중…</Hint>;
  if (detail.error)
    return (
      <div className="kd-t-caption" style={{ color: "var(--err-fg)" }}>
        {detail.error}
      </div>
    );

  const cfg = detail.config;
  if (!cfg && detail.pods.length === 0) {
    return <Hint>아직 배포한 앱이 없어요.</Hint>;
  }

  // 선택 스택 → chips. db "none"/redis off/storage off는 표시 안 함.
  const chips = [];
  if (cfg) {
    chips.push({ label: { python: "Python", java: "Java", php: "PHP", javascript: "JavaScript" }[cfg.runtime] || cfg.runtime, color: "var(--brand-fg)" });
    if (cfg.db_type && cfg.db_type !== "none")
      chips.push({ label: cfg.db_type === "mysql" ? "MySQL" : "PostgreSQL", color: "var(--info-fg)" });
    if (cfg.use_redis) chips.push({ label: "Redis", color: "var(--err-fg)" });
    if (cfg.use_storage) chips.push({ label: "스토리지 (R2)", color: "var(--warn-fg)" });
    if (cfg.volume_mount_path) chips.push({ label: "스토리지 (로컬)", color: "var(--warn-fg)" });
    chips.push({
      label: cfg.build_mode === "auto" ? "auto (nixpacks)" : "Dockerfile",
      color: "var(--fg-3)",
    });
  }

  return (
    <div className="flex flex-col gap-3">
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
            <span className="text-fg-4"> · {cfg.branch} 브랜치 · 마지막 빌드 {relativeTime(cfg.created_at)}</span>
          </div>
        </>
      )}
      {detail.pods.length > 0 ? (
        <div className="kd-table-wrap overflow-x-auto scroll-thin">
          <table className="w-full kd-t-caption" style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr className="kd-t-micro text-fg-3 text-left">
                {["Pod", "컴포넌트", "상태", "재시작", "시작"].map((h) => (
                  <th
                    key={h}
                    className="px-3 whitespace-nowrap"
                    style={{ height: "var(--row-sm)", borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {detail.pods.map((p) => (
                <tr
                  key={p.name}
                  className="text-fg-2"
                  style={{ borderBottom: "1px solid var(--kd-border)" }}
                >
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
        <Hint>테넌트 네임스페이스에 실행 중인 Pod이 없어요.</Hint>
      )}
    </div>
  );
}

// 등급 표시/변경 — root만 select 노출, 대상이 root나 본인이면 배지 고정.
function RoleCell({ user: target, me, onChange }) {
  const locked = me.role !== "root" || target.role === "root" || target.id === me.id;
  if (locked) {
    return (
      <span className="kd-chip" style={{ color: ROLE_COLORS[target.role] || "var(--fg-3)" }}>
        {target.role}
      </span>
    );
  }
  return (
    <select
      value={target.role}
      onChange={(e) => onChange(target, e.target.value)}
      className="kd-input"
      style={{ width: 96, height: 30, color: ROLE_COLORS[target.role] || "var(--fg-1)" }}
    >
      <option value="user">user</option>
      <option value="admin">admin</option>
    </select>
  );
}

function NodeCard({ node }) {
  // 클릭 펼침 — 첫 오픈 때 Pod 목록 fetch 후 캐시.
  const [expanded, setExpanded] = useState(false);
  const [pods, setPods] = useState(null);
  const [podsError, setPodsError] = useState(null);

  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    if (next && pods === null) {
      getNodePods(node.name)
        .then(setPods)
        .catch((e) => setPodsError(e.message || "Pod 조회 실패"));
    }
  };

  const cpuPct =
    node.cpu_used_cores != null && node.cpu_capacity_cores
      ? (node.cpu_used_cores / node.cpu_capacity_cores) * 100
      : null;
  const memPct =
    node.memory_used_bytes != null && node.memory_capacity_bytes
      ? (node.memory_used_bytes / node.memory_capacity_bytes) * 100
      : null;
  const diskPct =
    node.disk_used_bytes != null && node.disk_capacity_bytes
      ? (node.disk_used_bytes / node.disk_capacity_bytes) * 100
      : null;

  return (
    <div className="kd-card" style={{ padding: "16px 20px" }}>
      <button
        onClick={toggle}
        className="w-full flex items-center gap-2.5 mb-3 text-left"
        style={{ cursor: "pointer" }}
        title={expanded ? "Pod 목록 접기" : "Pod 목록 보기"}
      >
        <span
          className="w-1.5 h-1.5 rounded-full shrink-0"
          style={{ background: node.ready ? "var(--ok-fg)" : "var(--err-fg)" }}
          title={node.ready ? "Ready" : "NotReady"}
        />
        <span className="kd-t-body-s kd-strong text-fg-1">{node.name}</span>
        <span className="kd-chip" style={{ color: node.role === "master" ? "var(--warn-fg)" : "var(--fg-3)" }}>
          {node.role}
        </span>
        {node.pod_count != null && (
          <span className="kd-t-caption text-fg-3 tabular-nums">Pod {node.pod_count}</span>
        )}
        {node.error && (
          <span className="kd-t-caption" style={{ color: "var(--err-fg)" }}>
            {node.error}
          </span>
        )}
        <ChevronDown
          size={15}
          strokeWidth={1.8}
          className="ml-auto text-fg-3 transition-transform shrink-0"
          style={{ transform: expanded ? "rotate(180deg)" : "none" }}
        />
      </button>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-2">
        <UsageBar
          label="CPU"
          percent={cpuPct}
          detail={`${fmtCores(node.cpu_used_cores)} / ${fmtCores(node.cpu_capacity_cores)} cores`}
        />
        <UsageBar
          label="메모리"
          percent={memPct}
          detail={`${fmtGiB(node.memory_used_bytes)} / ${fmtGiB(node.memory_capacity_bytes)}`}
        />
        <UsageBar
          label="디스크"
          percent={diskPct}
          detail={`${fmtGiB(node.disk_used_bytes)} / ${fmtGiB(node.disk_capacity_bytes)}`}
        />
      </div>

      {/* Pod별 사용량 + limit — 클릭 펼침 */}
      {expanded && (
        <div className="mt-4 kd-fade-in">
          {podsError && (
            <div className="kd-t-caption py-2" style={{ color: "var(--err-fg)" }}>
              {podsError}
            </div>
          )}
          {!podsError && pods === null && <Hint>Pod 목록을 불러오는 중…</Hint>}
          {pods?.length === 0 && <Hint>실행 중인 Pod이 없어요.</Hint>}
          {pods?.length > 0 && <NodePodsTable pods={pods} />}
        </div>
      )}
    </div>
  );
}

// 노드 드릴다운 Pod 테이블 — "사용 / limit" 형식. limit 없으면(무제한) "—".
function NodePodsTable({ pods }) {
  return (
    <div className="kd-table-wrap overflow-x-auto scroll-thin">
      <table className="w-full kd-t-caption" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr className="kd-t-micro text-fg-3 text-left">
            {["네임스페이스", "Pod", "CPU", "메모리", "디스크"].map((h) => (
              <th
                key={h}
                className="px-3 whitespace-nowrap"
                style={{ height: "var(--row-sm)", borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {pods.map((p) => (
            <tr
              key={`${p.namespace}/${p.name}`}
              className="text-fg-2"
              style={{ borderBottom: "1px solid var(--kd-border)" }}
            >
              <td className="px-3 text-fg-4 font-mono whitespace-nowrap">
                {p.namespace}
              </td>
              <td className="px-3 font-mono" style={{ color: "var(--fg-1)" }}>
                {p.name}
              </td>
              <td className="px-3 tabular-nums whitespace-nowrap">
                {fmtMilli(p.cpu_used_cores)}
                <span className="text-fg-4"> / {fmtMilli(p.cpu_limit_cores)}</span>
              </td>
              <td className="px-3 tabular-nums whitespace-nowrap">
                {fmtMem(p.memory_used_bytes)}
                <span className="text-fg-4"> / {fmtMem(p.memory_limit_bytes)}</span>
              </td>
              <td className="px-3 tabular-nums whitespace-nowrap">
                {fmtMem(p.disk_used_bytes)}
                <span className="text-fg-4"> / {fmtMem(p.disk_limit_bytes)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 사용량 바 — 65% 이상 주황, 85% 이상 빨강.
function UsageBar({ label, percent, detail }) {
  const color =
    percent == null
      ? "var(--line-3)"
      : percent >= 85
        ? "var(--err-fg)"
        : percent >= 65
          ? "var(--warn-fg)"
          : "var(--ok-fg)";
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <span className="kd-t-micro text-fg-3">{label}</span>
        <span className="kd-t-caption text-fg-2 tabular-nums">
          {percent == null ? "—" : `${percent.toFixed(0)}%`}
          <span className="text-fg-4 ml-1.5">{detail}</span>
        </span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--sel-soft)" }}>
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${Math.min(percent ?? 0, 100)}%`,
            background: color,
          }}
        />
      </div>
    </div>
  );
}
