// 관리자 페이지 (/admin) — role admin/root만 진입 (TopBar 링크도 동일 조건).
//
// 탭으로 나눈다 (?tab= 으로 새로고침·뒤로가기에도 남는다):
// - 개요: 통계 카드(가입자 · 앱 · 총 빌드 · 최근 24h) + 빌드 기록 토글 + 노드 사용량(30s 폴링, 카드 클릭 → Pod 표)
// - 유저: 권한 · 앱 등급 · 빌드 집계. 행을 펼치면 그 유저의 앱과 (root) 계정 강제 탈퇴
// - 앱: 전체 앱. 행을 펼치면 선택 스택 · Pod 상태 · 앱 화면 열기 (root=주인 권한, admin=보기)
// - 등급: 등급별 앱 수 (root만 고친다)
// - 기록: 관리자가 남의 앱·계정에 한 동작
// 권한 select·등급 select·강제 탈퇴는 root에게만, 대상이 root/본인이면 잠긴다.
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, ChevronDown } from "lucide-react";
import {
  getNodePods,
  getNodes,
  getOverview,
  listAdminActions,
  listAdminApps,
  listBuildRecords,
  listUsers,
  setUserRole,
  listTiers,
  setTierLimit,
  setUserTier,
} from "../api/admin.js";
import { useAuth } from "../contexts/AuthContext.jsx";
import { relativeTime } from "../lib/format.js";
import AdminActions from "./admin/AdminActions.jsx";
import AdminApps from "./admin/AdminApps.jsx";
import DeleteUserModal from "./admin/DeleteUserModal.jsx";
import { BUILD_STATUS_COLORS, BUILD_STATUS_LABEL, Hint, SectionTitle, TableHead } from "./admin/atoms.jsx";

const ADMIN_ROLES = ["admin", "root"];
const NODE_POLL_MS = 30000;

const TABS = [
  { id: "overview", label: "개요" },
  { id: "users", label: "유저" },
  { id: "apps", label: "앱" },
  { id: "tiers", label: "등급" },
  { id: "actions", label: "기록" },
];

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

const ROLE_COLORS = {
  root: "var(--warn-fg)",
  admin: "var(--accent)",
  user: "var(--fg-3)",
};

export default function Admin() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { user, loading: authLoading, openLogin } = useAuth();
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [apps, setApps] = useState(null);                  // null=로딩
  const [tiers, setTiers] = useState([]);                  // 앱 개수 등급 [{name, max_apps, users}]
  const [nodes, setNodes] = useState(null);               // null=로딩
  const [actions, setActions] = useState(null);           // 기록 탭 첫 오픈 때 읽는다
  const [error, setError] = useState(null);
  // "총 빌드" 카드 드릴다운 — 열 때 1회 fetch 후 캐시 (닫았다 열어도 재요청 X)
  const [showBuilds, setShowBuilds] = useState(false);
  const [buildRecords, setBuildRecords] = useState(null);
  // 유저 row 드릴다운 — 한 번에 한 명만 펼친다
  const [expandedUser, setExpandedUser] = useState(null);
  const [deleting, setDeleting] = useState(null);         // 강제 탈퇴 확인 중인 유저

  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "overview";
  const setTab = (id) => setParams(id === "overview" ? {} : { tab: id }, { replace: true });
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

  // 통계 + 유저 + 앱 + 등급 — 마운트 시 1회. 강제 탈퇴 뒤에도 다시 읽는다.
  const loadAll = () =>
    Promise.all([getOverview(), listUsers(), listAdminApps(), listTiers()])
      .then(([ov, us, as, ts]) => {
        setOverview(ov);
        setUsers(us);
        setApps(as);
        setTiers(ts);
        setError(null);
      })
      .catch((e) => setError(e.message || "조회 실패"));

  useEffect(() => {
    if (isAdmin) loadAll();
  }, [isAdmin]);

  // 빌드 기록 — 드릴다운 첫 오픈 시 fetch.
  useEffect(() => {
    if (!isAdmin || !showBuilds || buildRecords !== null) return;
    listBuildRecords()
      .then(setBuildRecords)
      .catch(() => setBuildRecords([]));
  }, [isAdmin, showBuilds, buildRecords]);

  // 관리자 기록 — 기록 탭을 열 때마다 새로 읽는다 (앱 화면에서 바꾸고 돌아오면 바로 보이게).
  useEffect(() => {
    if (!isAdmin || tab !== "actions") return;
    listAdminActions()
      .then(setActions)
      .catch((e) => {
        setActions([]);
        setError(e.message || "기록 조회 실패");
      });
  }, [isAdmin, tab]);

  // 노드 리소스 — 개요 탭에서만 30초 폴링.
  useEffect(() => {
    if (!isAdmin || tab !== "overview") return;
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
  }, [isAdmin, tab]);

  if (authLoading || !isAdmin) return null;

  const handleRoleChange = async (target, role) => {
    try {
      await setUserRole(target.id, role);
      setUsers((prev) =>
        prev.map((u) => (u.id === target.id ? { ...u, role } : u)),
      );
    } catch (e) {
      setError(e.message || "권한 변경 실패");
    }
  };

  const handleTierChange = async (target, tier) => {
    try {
      await setUserTier(target.id, tier);
      setUsers((prev) => prev.map((u) => (u.id === target.id ? { ...u, tier } : u)));
      setTiers(await listTiers());                         // 등급별 유저 수 갱신
    } catch (e) {
      setError(e.message || "앱 등급 변경 실패");
    }
  };

  const handleLimitChange = async (name, maxApps) => {
    try {
      await setTierLimit(name, maxApps);
      setTiers(await listTiers());
      setError(null);
    } catch (e) {
      setError(e.message || "앱 수 변경 실패");
    }
  };

  const counts = { users: users.length, apps: apps?.length };

  return (
    <div className="kd-page kd-fade-in" style={{ paddingTop: 28, paddingBottom: 72 }}>
      <h1 className="kd-t-title text-fg-1">관리자</h1>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 6 }}>
        가입 · 앱 · 빌드 · 노드 현황과 관리자 조치
      </p>

      {/* 탭 — 빌드 상세(BuildDetail)와 같은 모양. 라벨 옆 숫자는 개수만 */}
      <div
        className="flex items-center overflow-x-auto scroll-thin"
        style={{ marginTop: 22, gap: 8, borderBottom: "1px solid var(--kd-border)" }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            aria-pressed={tab === t.id}
            className="kd-t-label kd-pick-x kd-pick-x-edge inline-flex items-center shrink-0"
            style={{ height: "var(--tabbar-h)", paddingInline: 17, color: "var(--fg-3)", gap: 6 }}
          >
            <span className="kd-pick-name">{t.label}</span>
            {counts[t.id] != null && <span className="kd-t-caption text-fg-3 tabular-nums">{counts[t.id]}</span>}
          </button>
        ))}
      </div>

      {error && (
        <div
          className="kd-t-caption"
          style={{
            marginTop: 16,
            padding: "10px 14px",
            borderRadius: 4,
            border: "1px solid var(--kd-border)",
            color: "var(--err-fg)",
          }}
        >
          {error}
        </div>
      )}

      {tab === "overview" && (
        <>
          {/* 통계 — 카드 한 장을 세로 괘선으로 4칸 나눈다(랜딩 피처 3열과 같은 규칙) */}
          <div className="kd-card kd-stat-row" style={{ marginTop: 26 }}>
            <StatCard
              label="가입자"
              value={overview?.users.total}
              sub={`7일 +${overview?.users.signups_7d ?? "—"}`}
            />
            <StatCard
              label="앱"
              value={apps?.length}
              sub={`앱 가진 유저 ${overview?.users.with_app ?? "—"}명`}
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

          <SectionTitle title="노드" />
          <div className="flex flex-col gap-3 mb-10">
            {nodes === null && <Hint>노드 정보를 불러오는 중…</Hint>}
            {nodes?.length === 0 && <Hint>노드 정보를 불러오지 못했어요.</Hint>}
            {nodes?.map((n) => (
              <NodeCard key={n.name} node={n} />
            ))}
          </div>
        </>
      )}

      {tab === "users" && (
        <div className="kd-table-wrap overflow-x-auto scroll-thin" style={{ marginTop: 14 }}>
          <table className="w-full kd-t-body-s" style={{ borderCollapse: "collapse" }}>
            <TableHead cols={["유저", "권한", "앱 등급", "빌드", "마지막 빌드", "가입"]} />
            <tbody>
              {users.map((u) => (
                <UserRow
                  key={u.id}
                  u={u}
                  me={user}
                  apps={(apps || []).filter((a) => a.owner_id === u.id)}
                  expanded={expandedUser === u.id}
                  onToggle={() => setExpandedUser((cur) => (cur === u.id ? null : u.id))}
                  onRoleChange={handleRoleChange}
                  tiers={tiers}
                  onTierChange={handleTierChange}
                  onDelete={() => setDeleting(u)}
                />
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center kd-t-caption text-fg-3">
                    가입자가 없어요.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "apps" && <AdminApps apps={apps} me={user} />}

      {tab === "tiers" && <TierTable tiers={tiers} me={user} onLimit={handleLimitChange} />}

      {tab === "actions" && <AdminActions actions={actions} appIds={new Set((apps || []).map((a) => a.id))} />}

      {deleting && (
        <DeleteUserModal
          target={deleting}
          onClose={() => setDeleting(null)}
          onDone={() => {
            setDeleting(null);
            setExpandedUser(null);
            setActions(null);
            loadAll();
          }}
        />
      )}
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

// 가입자 row + 클릭 펼침 (그 유저의 앱 · 강제 탈퇴). 권한·등급 select 클릭은 토글에 안 걸리게 차단.
function UserRow({ u, me, apps, expanded, onToggle, onRoleChange, tiers, onTierChange, onDelete }) {
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
        title={expanded ? "앱 목록 접기" : "앱 목록 보기"}
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
            <span className="truncate kd-strong" style={{ color: "var(--fg-1)" }}>
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
        <td className="px-4" style={{ height: "var(--row-lg)" }} onClick={(e) => e.stopPropagation()}>
          <TierCell user={u} me={me} tiers={tiers} onChange={onTierChange} />
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
          <td colSpan={6} className="px-4 py-4" style={{ background: "var(--sel-soft)" }}>
            <UserApps user={u} me={me} apps={apps} onDelete={onDelete} />
          </td>
        </tr>
      )}
    </>
  );
}

// 펼침 — 그 유저가 주인인 앱 (누르면 앱 화면) + root면 계정 강제 탈퇴.
function UserApps({ user: target, me, apps, onDelete }) {
  const canDelete = me.role === "root" && target.role !== "root" && target.id !== me.id;
  return (
    <div className="flex flex-col gap-3">
      {apps.length === 0 ? (
        <Hint>만든 앱이 없어요.</Hint>
      ) : (
        <div className="flex flex-col">
          {apps.map((a) => (
            <Link
              key={a.id}
              to={`/apps/${a.id}`}
              className="flex items-center gap-3 no-underline kd-hoverable"
              style={{ height: "var(--row-md)", paddingInline: 4, borderBottom: "1px solid var(--line-1)" }}
            >
              <span className="kd-strong" style={{ color: "var(--fg-1)" }}>{a.name}</span>
              <span className="kd-chip" style={{ color: a.pipeline === "v2" ? "var(--accent)" : "var(--fg-3)" }}>
                {a.pipeline}
              </span>
              {a.last_build ? (
                <span className="kd-t-caption" style={{ color: BUILD_STATUS_COLORS[a.last_build.status] || "var(--fg-3)" }}>
                  {BUILD_STATUS_LABEL[a.last_build.status] || a.last_build.status}
                  <span className="text-fg-3"> · {relativeTime(a.last_build.created_at)}</span>
                </span>
              ) : (
                <span className="kd-t-caption text-fg-4">배포 전</span>
              )}
              <ArrowRight size={14} strokeWidth={1.8} className="text-fg-3 ml-auto" />
            </Link>
          ))}
        </div>
      )}
      {canDelete && (
        <div className="flex items-center gap-3">
          <button
            onClick={onDelete}
            className="kd-btn-sm"
            style={{ background: "transparent", border: "1px solid var(--line-3)", color: "var(--err-fg)" }}
          >
            계정 강제 탈퇴
          </button>
          <span className="kd-t-caption text-fg-3">앱과 데이터, 계정 정보가 회원 탈퇴와 똑같이 삭제돼요.</span>
        </div>
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

// 앱 등급 표시/변경 — 권한(RoleCell)과 같은 규칙: root만 select, 아니면 배지. 옆에 지금 앱 수/한도.
function TierCell({ user: target, me, tiers, onChange }) {
  const limit = tiers.find((t) => t.name === target.tier)?.max_apps;
  const count = `${target.app_count ?? 0}/${limit == null ? "∞" : limit}`;
  if (me.role !== "root") {
    return (
      <span className="inline-flex items-center gap-2">
        <span className="kd-chip">{target.tier}</span>
        <span className="kd-t-caption text-fg-3 tabular-nums">{count}</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-2">
      <select
        value={target.tier}
        onChange={(e) => onChange(target, e.target.value)}
        className="kd-input"
        style={{ width: 104, height: 30 }}
      >
        {tiers.map((t) => (
          <option key={t.name} value={t.name}>
            {t.name}
          </option>
        ))}
      </select>
      <span className="kd-t-caption text-fg-3 tabular-nums">{count}</span>
    </span>
  );
}

// 등급 표 — 이름 · 만들 수 있는 앱 수 · 그 등급 유저 수. root는 앱 수를 고친다(빈 칸 = 무제한).
function TierTable({ tiers, me, onLimit }) {
  if (tiers.length === 0) return <Hint>등급 정보를 불러오는 중…</Hint>;
  return (
    <div className="kd-table-wrap" style={{ marginTop: 14 }}>
      <table className="w-full kd-t-body-s" style={{ borderCollapse: "collapse" }}>
        <thead>
          <tr className="kd-t-micro text-fg-3 text-left">
            {["등급", "앱 수", "유저"].map((h) => (
              <th
                key={h}
                className="px-4"
                style={{ height: "var(--row-md)", borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tiers.map((t) => (
            <tr key={t.name} style={{ borderBottom: "1px solid var(--kd-border)" }}>
              <td className="px-4 kd-strong" style={{ height: "var(--row-md)", color: "var(--fg-1)" }}>
                {t.name}
              </td>
              <td className="px-4">
                <LimitInput tier={t} editable={me.role === "root"} onSave={onLimit} />
              </td>
              <td className="px-4 tabular-nums text-fg-3">{t.users}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// 앱 수 입력 — 포커스를 잃거나 Enter면 저장한다. 비우면 무제한(null).
function LimitInput({ tier, editable, onSave }) {
  const shown = tier.max_apps == null ? "" : String(tier.max_apps);
  const [value, setValue] = useState(shown);
  useEffect(() => setValue(shown), [shown]);
  if (!editable) return <span className="tabular-nums">{tier.max_apps == null ? "무제한" : tier.max_apps}</span>;
  const commit = () => {
    if (value === shown) return;
    const n = value.trim() === "" ? null : Number(value);
    if (n !== null && (!Number.isInteger(n) || n < 1)) {
      setValue(shown);                                    // 1 이상의 정수만 — 아니면 되돌린다
      return;
    }
    onSave(tier.name, n);
  };
  return (
    <input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
      placeholder="무제한"
      inputMode="numeric"
      className="kd-input tabular-nums"
      style={{ width: 96, height: 30 }}
    />
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
