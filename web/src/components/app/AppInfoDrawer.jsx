// 앱 정보 드로어 — design/라이트모드-시안/16_앱_정보_드로어.png 기준.
//
// 어느 탭에 있든(작업 공간·환경변수·설정…) "이 앱이 뭐였더라"를 즉시 확인하는 참조용 패널이다.
// 그래서 편집 컨트롤은 하나도 두지 않고, 주소·소스·실행 환경·연결 서비스만 읽기 전용으로 보여준다.
// 자세히 다루는 화면(배포 이력 / 설정)으로는 바닥 링크로 넘긴다.
//
// 데이터는 전부 AppLayout이 이미 폴링해 둔 ctx 값이다 — 드로어는 자기 폴링을 하지 않는다.
// (탭마다 같은 엔드포인트를 또 두드리지 않게 하려는 AppLayout의 원칙을 그대로 따른다.)
//
// 치수 주석의 숫자는 시안 원본 px이고 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { ArrowRight, ArrowUpRight, Check, CircleCheck, CircleX, Copy, X } from "lucide-react";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
import { relativeTime, repoSlug } from "../../lib/format.js";

// 표시용 라벨 — 백엔드 enum 값(schemas.py)을 사람이 읽는 이름으로.
const DB_LABEL = { mysql: "MySQL", postgres: "PostgreSQL" };
const RUNTIME_LABEL = {
  python: "Python",
  java: "Java",
  php: "PHP",
  javascript: "JavaScript",
};

export default function AppInfoDrawer({ ctx, onClose }) {
  // Esc 닫기 + body 스크롤 잠금 — LoginModal/DeleteAppModal과 같은 처리.
  // capture 단계로 듣는 이유: 작업 공간 패널(터미널 등)이 Esc를 먼저 먹고 삼키기 때문.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("keydown", onKey, true);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  const { user, builds = [], serverBuild, slotStatus, appHost } = ctx || {};

  // 배포 번호(#N) — env_change는 번호를 안 매긴다(개요·위젯과 같은 규칙).
  const numbered = new Map();
  let n = builds.filter((b) => b.kind !== "env_change").length;
  for (const b of builds) if (b.kind !== "env_change") numbered.set(b.build_id, n--);

  const latest = builds[0] || null;
  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;

  // 연결 서비스 — 켜진 것만. 값은 서버 슬롯의 최신 빌드가 진실원이다.
  const services = [];
  if (serverBuild?.db_type && serverBuild.db_type !== "none")
    services.push(["데이터베이스", DB_LABEL[serverBuild.db_type] || serverBuild.db_type]);
  if (serverBuild?.use_redis) services.push(["캐시", "Redis"]);
  if (serverBuild?.use_storage) services.push(["스토리지", "R2"]);
  // local 저장소는 R2와 상호배타 — 켜져 있으면 마운트 경로가 곧 그 서비스의 정체다.
  if (serverBuild?.volume_mount_path)
    services.push(["영구 볼륨", serverBuild.volume_mount_path]);

  return createPortal(
    <>
      {/* 백드롭 — 시안의 종이색(250)이 201까지 눌린 상태라 알파 약 0.2 */}
      <div
        className="fixed inset-0 kd-fade-in"
        style={{ background: "var(--kd-scrim)", zIndex: 40 }}
        onClick={onClose}
      />

      {/* 패널 — 시안 폭 545px(x991→1536) ÷ 1.45 = 376 */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="앱 정보"
        className="fixed top-0 right-0 bottom-0 flex flex-col kd-slide-in-right"
        style={{
          zIndex: 45,
          width: 376,
          maxWidth: "92vw",
          background: "var(--kd-surface)",
          borderLeft: "1px solid var(--kd-border)",
          boxShadow: "var(--shadow-drawer)",
        }}
      >
        <div className="flex-1 min-h-0 overflow-auto scroll-thin" style={{ paddingInline: 32 }}>
          {/* ── 머리: 제목 + 닫기 (시안 제목 y36-63 → 25-43) ── */}
          <div className="flex items-center" style={{ height: 32, marginTop: 20 }}>
            <h2 className="kd-t-subtitle text-fg-1">앱 정보</h2>
            <button
              type="button"
              aria-label="닫기"
              onClick={onClose}
              className="ml-auto inline-flex items-center justify-center rounded-lg text-fg-2 hover:text-fg-1 kd-hoverable"
              // 시안의 X는 콘텐츠 여백(32)보다 12px 더 바깥에 붙어 있다
              style={{ width: 32, height: 32, marginRight: -12 }}
            >
              <X size={18} strokeWidth={1.7} />
            </button>
          </div>

          {/* ── 신원: 앱 이름 + 지금 상태, 그 아래 주소 ── */}
          <div className="flex items-center" style={{ marginTop: 20, gap: 16 }}>
            <span className="kd-t-subtitle text-fg-1 truncate">{user?.app_name || "—"}</span>
            <PodStatus status={podStatus} />
          </div>

          {appHost && (
            <a
              href={`https://${appHost}`}
              target="_blank"
              rel="noopener noreferrer"
              className="kd-t-body-s text-fg-1 inline-flex items-center gap-1 underline"
              style={{ marginTop: 4 }}
            >
              {appHost}
              <ArrowUpRight size={15} strokeWidth={1.7} className="text-fg-3 shrink-0" />
            </a>
          )}

          <div style={{ height: 20 }} />

          {/* ── 배포 ── */}
          <Section title="배포">
            <Row label="GitHub 저장소">
              {serverBuild?.repo_url ? (
                <a
                  href={serverBuild.repo_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-fg-1 no-underline hover:underline max-w-full"
                >
                  <span className="truncate">{repoSlug(serverBuild.repo_url)}</span>
                  <ArrowUpRight size={15} strokeWidth={1.7} className="text-fg-3 shrink-0" />
                </a>
              ) : (
                "—"
              )}
            </Row>
            <Row label="브랜치">{serverBuild?.branch || "—"}</Row>
            {/* 시안의 "현재 커밋" 자리 — Build 레코드에 커밋 sha가 없어(모델에 컬럼 자체가 없다)
                같은 자리에 빌드 식별자를 둔다. 없는 값을 지어내지 않는다. */}
            <Row label="빌드 ID">
              {serverBuild ? <CopyableId value={serverBuild.build_id} /> : "—"}
            </Row>
            <Row label="최근 배포">
              {latest
                ? `${
                    latest.kind === "env_change"
                      ? "환경변수 변경"
                      : `#${numbered.get(latest.build_id)}`
                  } · ${relativeTime(latest.created_at)}`
                : "—"}
            </Row>
          </Section>

          {/* ── 실행 환경 ──
              시안의 "Python 3.12"처럼 버전까지는 못 쓴다 — 빌드 레코드에는 런타임 종류만 있다. */}
          <Section title="실행 환경">
            <Row label="런타임">
              {serverBuild ? RUNTIME_LABEL[serverBuild.runtime] || serverBuild.runtime : "—"}
            </Row>
            <Row label="포트">{serverBuild?.port ?? "—"}</Row>
          </Section>

          {/* ── 연결 서비스 ── */}
          <Section title="연결 서비스" last>
            {services.length ? (
              services.map(([label, value]) => (
                <Row key={label} label={label}>
                  {value}
                </Row>
              ))
            ) : (
              <div className="kd-t-body-s text-fg-3" style={{ height: "var(--row-sm)" }}>
                연결된 서비스가 없어요.
              </div>
            )}
          </Section>
        </div>

        {/* ── 바닥: 더 자세한 화면으로 ──
            괘선은 섹션 괘선과 같은 인셋(좌우 32)이어야 해서 패딩 박스 안쪽에 그린다. */}
        <div className="shrink-0" style={{ paddingInline: 32 }}>
          <div
            className="flex items-center"
            style={{ paddingBlock: 20, gap: 24, borderTop: "1px solid var(--kd-border)" }}
          >
            <FooterLink to="/dashboard/history" onClose={onClose}>
              배포 이력 보기
            </FooterLink>
            <span style={{ width: 1, height: 23, background: "var(--kd-border)" }} />
            <FooterLink to="/dashboard/settings" onClose={onClose}>
              설정 열기
            </FooterLink>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}

// 섹션 — 위 괘선 + 제목 + 32px 행들. 시안: 괘선→제목 18, 제목→첫 행 6, 마지막 행→다음 괘선 20.
function Section({ title, last, children }) {
  return (
    <section
      style={{
        borderTop: "1px solid var(--kd-border)",
        paddingTop: 18,
        paddingBottom: last ? 8 : 20,
      }}
    >
      <h2 className="kd-t-section text-fg-1" style={{ marginBottom: 6 }}>
        {title}
      </h2>
      {children}
    </section>
  );
}

// 정보 행 — 라벨/값 2열. 값 열 시작 x는 시안 1249 → 콘텐츠 좌측에서 145px.
function Row({ label, children }) {
  return (
    <div
      className="grid items-center"
      style={{ gridTemplateColumns: "145px minmax(0, 1fr)", height: "var(--row-sm)" }}
    >
      <span className="kd-t-label text-fg-3">{label}</span>
      <span className="kd-t-body-s text-fg-1 truncate">{children}</span>
    </div>
  );
}

function FooterLink({ to, onClose, children }) {
  return (
    <Link
      to={to}
      onClick={onClose}
      className="kd-t-label text-fg-2 hover:text-fg-1 no-underline inline-flex items-center gap-1.5 transition-colors"
    >
      {children}
    </Link>
  );
}

// 지금 살아 있나 — 라벨은 AppStatusBadge의 맵(단일 진실원)을 그대로 쓴다.
function PodStatus({ status }) {
  if (!status) return null;
  const s = APP_STATUS_STYLES[status] || { label: status };
  const ok = status === "running";
  return (
    <span className="kd-t-label text-fg-2 inline-flex items-center gap-2 shrink-0">
      {status === "crashing" ? (
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

// 빌드 ID는 로그·문의에 그대로 붙여 쓰는 값이라 복사 버튼을 붙인다(시안에도 있다).
function CopyableId({ value }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
      setTimeout(() => setDone(false), 1400);
    } catch {
      /* 클립보드 권한이 없으면 조용히 넘어간다 */
    }
  };
  return (
    <span className="inline-flex items-center gap-2 min-w-0">
      <span className="kd-t-code text-fg-1 truncate">{value}</span>
      <button
        onClick={copy}
        title="빌드 ID 복사"
        aria-label="빌드 ID 복사"
        className="shrink-0 text-fg-3 hover:text-fg-1 transition-colors"
      >
        {done ? <Check size={15} strokeWidth={1.8} /> : <Copy size={15} strokeWidth={1.7} />}
      </button>
    </span>
  );
}
