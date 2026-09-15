// 앱 상세 · 개요 탭 — design/라이트모드-시안/09_앱_개요.png 기준.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
// 레이아웃: 페이지 헤더(전체 폭) → 2단 그리드(좌 본문 / 세로 괘선 / 우 사이드 310px).
//
// 데이터는 전부 AppLayout이 폴링한 실제 값이다. 시안에 있는 "커밋 메시지"는
// 빌드 레코드에 커밋 정보가 없어(Build 모델에 sha/message 컬럼 없음) 넣지 않았다 —
// 자리 대신 빌드 식별자(build_id)와 브랜치를 쓴다.
import { Link, useOutletContext } from "react-router-dom";
import {
  ArrowUpRight,
  BarChart3,
  ChevronRight,
  CircleCheck,
  CircleX,
  Database,
  FileText,
  Folder,
  Layers,
  Loader,
  SquareChevronRight,
} from "lucide-react";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
import { STYLES_BUILD, STYLES_ENV } from "../StatusBadge.jsx";
import { relativeTime, repoSlug } from "../../lib/format.js";

const DB_LABEL = { mysql: "MySQL", postgres: "PostgreSQL" };
const RUNTIME_LABEL = {
  python: "Python",
  java: "Java",
  php: "PHP",
  javascript: "JavaScript",
};

// 빌드 결과 → 아이콘. 성공/실패만 색을 쓰고 진행 중은 잉크 회색(모노크롬 원칙).
function resultIcon(status, size = 21) {
  if (status === "running") return <CircleCheck size={size} strokeWidth={1.6} style={{ color: "var(--ok-fg)" }} />;
  if (status === "failed") return <CircleX size={size} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />;
  if (status === "cancelled" || status === "queued")
    return <CircleCheck size={size} strokeWidth={1.6} style={{ color: "var(--fg-4)" }} />;
  return <Loader size={size} strokeWidth={1.6} className="kd-spin" style={{ color: "var(--fg-3)" }} />;
}

export default function AppOverview() {
  const { user, builds, serverBuild, slotStatus, envVars } = useOutletContext();

  // 빌드 번호(#N) — env_change는 번호를 안 매긴다(위젯과 같은 규칙).
  const numbered = new Map();
  let n = builds.filter((b) => b.kind !== "env_change").length;
  for (const b of builds) if (b.kind !== "env_change") numbered.set(b.build_id, n--);

  const latest = builds[0] || null;
  const previous = builds.slice(1, 3);
  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;

  return (
    <div className="flex-1 overflow-auto scroll-thin">
      <div className="kd-page" style={{ paddingBottom: 72 }}>
        {/* ── 페이지 헤더 (시안 y153→195, 제목 잉크 35px) ── */}
        <div className="flex items-start gap-6 flex-wrap" style={{ paddingTop: 28 }}>
          <div className="min-w-0">
            <h1 className="kd-t-title text-fg-1">
              개요
            </h1>
            <p className="kd-t-body-s mt-1.5 text-fg-2">현재 배포와 연결된 리소스를 확인하세요.</p>
          </div>
          <div className="ml-auto flex items-center gap-3 shrink-0" style={{ paddingTop: 6 }}>
            <span className="kd-t-subtitle text-fg-1">
              {user.app_name}
            </span>
            <PodStatus status={podStatus} />
          </div>
        </div>

        {/* ── 2단 그리드 (좌 본문 / 우 사이드 310px) ── */}
        <div
          className="kd-overview-grid"
          style={{ marginTop: 32 }}
        >
          {/* ─────────── 좌: 본문 ─────────── */}
          <div style={{ paddingRight: 40 }}>
            <Section title="현재 배포" action={{ label: "배포 상세 보기", to: latest ? `/dashboard/history?build=${latest.build_id}` : "/dashboard/history" }}>
              {latest ? (
                <div className="flex items-center gap-4" style={{ paddingBlock: 17 }}>
                  <span className="shrink-0">{resultIcon(latest.status)}</span>
                  <div className="min-w-0">
                    <div className="kd-t-body kd-strong text-fg-1 truncate">
                      {latest.kind === "env_change" ? "환경변수 변경" : `배포 #${numbered.get(latest.build_id)}`}
                    </div>
                    <div className="kd-t-caption mt-1.5 text-fg-3 truncate">
                      {[
                        latest.build_id,
                        latest.branch,
                        relativeTime(latest.created_at),
                      ]
                        .filter(Boolean)
                        .join("  ·  ")}
                    </div>
                  </div>
                  <span className="ml-auto shrink-0">
                    <ResultChip build={latest} />
                  </span>
                </div>
              ) : (
                <Empty>아직 배포 기록이 없어요.</Empty>
              )}
            </Section>

            <Section title="연결된 리소스">
              {(() => {
                const rows = [];
                const db = serverBuild?.db_type;
                if (db && db !== "none")
                  rows.push({ icon: Database, name: DB_LABEL[db] || db, kind: "데이터베이스", panel: "db" });
                if (serverBuild?.use_redis) rows.push({ icon: Layers, name: "Redis", kind: "캐시", panel: "db" });
                if (serverBuild?.use_storage)
                  rows.push({ icon: Folder, name: "오브젝트 스토리지", kind: "R2", panel: "storage" });
                if (serverBuild?.volume_mount_path)
                  rows.push({
                    icon: Folder,
                    name: "영구 저장소",
                    kind: serverBuild.volume_mount_path,
                  });
                if (!rows.length) return <Empty>연결된 리소스가 없어요.</Empty>;
                // 시안은 행 끝에 chevron이 있다 — 실제로 눌러 갈 곳이 있는 행만 링크로 만든다.
                return rows.map((r, i) => {
                  const Icon = r.icon;
                  const inner = (
                    <>
                      <Icon size={18} strokeWidth={1.5} className="text-fg-1 shrink-0" />
                      <span className="kd-t-body-s text-fg-1" style={{ width: 190 }}>
                        {r.name}
                      </span>
                      <span className="kd-t-caption text-fg-3 truncate">{r.kind}</span>
                      <span className="kd-t-caption ml-auto inline-flex items-center gap-1.5 text-fg-2 shrink-0">
                        <CircleCheck size={15} strokeWidth={1.6} style={{ color: "var(--ok-fg)" }} />
                        사용 중
                      </span>
                      <ChevronRight
                        size={15}
                        strokeWidth={1.7}
                        className="shrink-0"
                        style={{ color: r.panel ? "var(--fg-2)" : "transparent" }}
                      />
                    </>
                  );
                  const style = {
                    height: 42,
                    borderBottom: i < rows.length - 1 ? "1px solid var(--kd-border)" : "none",
                  };
                  return r.panel ? (
                    <Link
                      key={r.name}
                      to={`/dashboard/workspace?panel=${r.panel}`}
                      className="flex items-center gap-4 no-underline kd-hoverable"
                      style={style}
                    >
                      {inner}
                    </Link>
                  ) : (
                    <div key={r.name} className="flex items-center gap-4" style={style}>
                      {inner}
                    </div>
                  );
                });
              })()}
            </Section>

            <Section title="이전 배포" action={{ label: "전체 이력 보기", to: "/dashboard/history" }}>
              {previous.length ? (
                <div>
                  <div
                    className="kd-history-row kd-t-micro text-fg-3"
                    style={{ paddingBottom: 10, borderBottom: "1px solid var(--kd-border)" }}
                  >
                    <span>배포</span>
                    <span>결과</span>
                    <span>빌드 ID</span>
                    <span>브랜치</span>
                    <span>시간</span>
                  </div>
                  {previous.map((b, i) => (
                    <div
                      key={b.build_id}
                      className="kd-history-row kd-t-caption text-fg-2 items-center"
                      style={{
                        height: 32,
                        // 마지막 줄은 섹션 마감 괘선과 겹치지 않게 비운다
                        borderBottom: i < previous.length - 1 ? "1px solid var(--kd-border)" : "none",
                      }}
                    >
                      <span className="tabular-nums text-fg-3">
                        {b.kind === "env_change" ? "—" : `#${numbered.get(b.build_id)}`}
                      </span>
                      <span className="inline-flex items-center gap-1.5 min-w-0">
                        {resultIcon(b.status, 17)}
                        <ResultLabel build={b} />
                      </span>
                      <span className="font-mono text-fg-2 truncate">{b.build_id}</span>
                      <span className="truncate">{b.branch || "—"}</span>
                      <span className="text-fg-3">{relativeTime(b.created_at)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty>이전 배포가 없어요.</Empty>
              )}
            </Section>
          </div>

          {/* ─────────── 우: 사이드 ─────────── */}
          <aside style={{ borderLeft: "1px solid var(--kd-border)", paddingLeft: 24 }}>
            <SideHeading>바로 작업하기</SideHeading>
            {/* 시안 그대로 — 작업 공간의 뷰 4개와 1:1로 맞춘다(터미널·로그는 한 행) */}
            {[
              { icon: SquareChevronRight, label: "터미널·로그", panel: "terminal" },
              { icon: Database, label: "데이터베이스", panel: "db" },
              ...(serverBuild?.use_storage
                ? [{ icon: Folder, label: "스토리지", panel: "storage" }]
                : []),
              { icon: BarChart3, label: "모니터링", panel: "monitoring" },
            ].map((q, i, arr) => {
              const Icon = q.icon;
              return (
                <Link
                  key={q.label}
                  to={`/dashboard/workspace?panel=${q.panel}`}
                  className="flex items-center gap-4 no-underline kd-hoverable"
                  style={{
                    height: 37,
                    borderBottom: i < arr.length - 1 ? "1px solid var(--kd-border)" : "none",
                  }}
                >
                  <Icon size={15} strokeWidth={1.5} className="text-fg-1 shrink-0" />
                  <span className="kd-t-body-s text-fg-1">{q.label}</span>
                  <ChevronRight size={15} strokeWidth={1.7} className="ml-auto text-fg-2 shrink-0" />
                </Link>
              );
            })}

            <SideHeading style={{ marginTop: 24 }}>소스와 실행 환경</SideHeading>
            <InfoRow label="저장소">
              {serverBuild?.repo_url ? (
                <a
                  href={serverBuild.repo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-fg-1 no-underline hover:underline"
                >
                  {repoSlug(serverBuild.repo_url)}
                  <ArrowUpRight size={15} strokeWidth={1.8} className="text-fg-3" />
                </a>
              ) : (
                "—"
              )}
            </InfoRow>
            <InfoRow label="런타임">
              {serverBuild
                ? `${RUNTIME_LABEL[serverBuild.runtime] || serverBuild.runtime} · ${serverBuild.port}`
                : "—"}
            </InfoRow>
            <InfoRow label="환경변수" last>
              <Link
                to="/dashboard/env"
                className="inline-flex items-center gap-1 text-fg-1 no-underline hover:underline"
              >
                {envVars ? `${Object.keys(envVars).length}개` : "—"}
              </Link>
            </InfoRow>

            <div style={{ marginTop: 16 }}>
              <Link to="/dashboard/settings" className="kd-t-caption text-fg-1 underline underline-offset-4">
                설정 보기
              </Link>
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}

// 섹션 — 제목 + (우측 링크) + 아래 괘선. 시안: 제목 잉크 20px, 제목→괘선 18px.
function Section({ title, action, children }) {
  return (
    <section className="kd-section">
      <div className="flex items-baseline gap-4">
        <h2 className="kd-t-section text-fg-1">
          {title}
        </h2>
        {action && (
          <Link
            to={action.to}
            className="kd-t-label ml-auto inline-flex items-center gap-1 text-fg-2 hover:text-fg-1 no-underline transition-colors"
          >
            {action.label}
          </Link>
        )}
      </div>
      <div
        style={{
          marginTop: 12,
          borderTop: "1px solid var(--kd-border)",
          borderBottom: "1px solid var(--kd-border)",
        }}
      >
        {children}
      </div>
    </section>
  );
}

function SideHeading({ children, style }) {
  return (
    <h2
      className="kd-t-section text-fg-1"
      style={{ marginBottom: 12, ...style }}
    >
      {children}
    </h2>
  );
}

function InfoRow({ label, children, last }) {
  return (
    <div
      className="kd-t-caption flex items-center gap-4"
      style={{ height: 32, borderBottom: last ? "none" : "1px solid var(--kd-border)" }}
    >
      <span className="text-fg-3 shrink-0" style={{ width: 110 }}>
        {label}
      </span>
      <span className="text-fg-1 truncate">{children}</span>
    </div>
  );
}

function Empty({ children }) {
  return (
    <div className="kd-t-caption text-fg-3" style={{ paddingBlock: 17 }}>
      {children}
    </div>
  );
}

// 빌드 결과 라벨 — StatusBadge의 라벨 맵을 그대로 쓴다(단일 진실원)
function ResultLabel({ build }) {
  const map = build.kind === "env_change" ? STYLES_ENV : STYLES_BUILD;
  const s = map[build.status];
  const failed = build.status === "failed";
  return (
    <span className="truncate" style={failed ? { color: "var(--err-fg)" } : undefined}>
      {s?.label || build.status}
    </span>
  );
}

// 현재 배포 우측 칩 — 괘선 테두리의 알약. 시안 92x37(→ 높이 27)
function ResultChip({ build }) {
  const map = build.kind === "env_change" ? STYLES_ENV : STYLES_BUILD;
  const s = map[build.status];
  return (
    <span
      className="kd-chip"
    >
      {s?.label || build.status}
    </span>
  );
}

// Pod 상태 — 지금 살아 있나. AppStatusBadge의 라벨 맵 재사용.
function PodStatus({ status }) {
  if (!status) return null;
  const s = APP_STATUS_STYLES[status] || { label: status };
  const ok = status === "running";
  const bad = status === "crashing";
  return (
    <span className="kd-t-label inline-flex items-center gap-1.5 text-fg-2">
      {bad ? (
        <CircleX size={18} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />
      ) : (
        <CircleCheck
          size={18}
          strokeWidth={1.6}
          style={{ color: ok ? "var(--ok-fg)" : "var(--fg-4)" }}
        />
      )}
      {s.label}
    </span>
  );
}
