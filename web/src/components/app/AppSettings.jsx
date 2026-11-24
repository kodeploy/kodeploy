// 앱 상세 · 설정 탭 — design/라이트모드-시안/17_설정.png 기준.
//
// 레이아웃: 페이지 헤더 → 좌 서브내비(일반 / 도메인 / 스토리지) · 세로 괘선 · 우 본문.
// 시안처럼 "고른 섹션만 펼치고 나머지는 아래에 접힌 행으로 쌓는다" — 내비와 접힌 행은
// 같은 섹션 집합을 가리키는 두 입구라서, 어느 쪽을 눌러도 같은 상태(?section=)로 간다.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
//
// 동작은 새로 짜지 않고 가져왔다:
//   · 도메인(폴링 8s/30s · 복사 1.5s 복귀 · 서브도메인 전용 CNAME 한 줄)
//     → CommitListPanel.jsx의 CustomDomainSection
//   · DB 내보내기/복원(앵커 스트림 다운로드 · 복원 2단계 확인)
//     → CommitListPanel.jsx의 ExtraBody
//   · 앱 삭제 확인 → 기존 DeleteAppModal.jsx (앱 이름 타이핑 확인)
//
// 없는 기능은 만들지 않았다: 빌드·실행 설정을 고치는 API가 없어서 현재 값을 읽기 전용으로만
// 보여주고, 변경은 재배포(/deploy)로 보낸다.
import { can } from "../../lib/roles.js";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useOutletContext, useSearchParams } from "react-router-dom";
import {
  Check,
  ChevronRight,
  Copy,
  Download,
  ArrowUpRight,
  Upload,
} from "lucide-react";
import {
  CUSTOM_DOMAIN_CNAME_TARGET,
  apiOrigin,
  dbExportUrl,
  deleteDomain,
  getCliToken,
  getDomain,
  issueCliToken,
  restoreDb,
  revokeCliToken,
  setCiDeploy,
  setDomain,
} from "../../api/deploy.js";
import DomainStatusBadge from "../DomainStatusBadge.jsx";
import DeleteAppModal from "../DeleteAppModal.jsx";
import { Checkbox, Disclosure } from "../deploy/steps/wizardParts.jsx";
import { useCurrentApp } from "./AppScope.jsx";
import { dayTime, repoSlug } from "../../lib/format.js";

// 서브내비 + 접힌 행이 공유하는 섹션 정의.
// danger는 파괴적이라 내비에 올리지 않는다 — 시안처럼 맨 아래 접힌 행으로만 닿는다.
const SECTIONS = [
  { id: "general", nav: "일반", row: "빌드와 실행 설정" },
  { id: "domain", nav: "도메인", row: "도메인 설정" },
  { id: "storage", nav: "스토리지", row: "스토리지 설정" },
  { id: "ci", nav: "GitHub Actions", row: "GitHub Actions 배포" },
  { id: "cli", nav: "CLI 토큰", row: "CLI 토큰" },
  { id: "danger", row: "앱 삭제" },
];
const SECTION_IDS = new Set(SECTIONS.map((s) => s.id));

// 라벨 맵 — 배포 폼(DeployForm)의 표기와 맞춘다.
const RUNTIME_LABEL = {
  python: "Python",
  java: "Java",
  php: "PHP",
  javascript: "JavaScript",
  go: "Go",
  static: "정적 사이트",
};
const BUILD_MODE_LABEL = { dockerfile: "Dockerfile", auto: "자동 빌드 · Nixpacks" };
const DB_LABEL = { mysql: "MySQL", postgres: "PostgreSQL" };

// 시안 실측(÷1.45): 세로 괘선 x=344 → 237, 콘텐츠 좌단 x=58 → 40 ⇒ 내비 열 197
const NAV_W = 197;
// 라벨 열 — 시안 라벨 x=377, 값 x=542 ⇒ (542-377)/1.45 = 114
const LABEL_W = 114;
// 입력칸 폭 — 시안 542→1293 ⇒ 518 (오른쪽 16 띄우고 상태 배지)
const INPUT_W = 518;

// 이 화면은 owner 이상만 — 공유받은 앱에서 URL로 들어와도 개요로 돌려보낸다 (서버도 막는다).
export default function AppSettings() {
  const { app, base } = useOutletContext();
  if (!can(app, "owner")) return <Navigate to={base} replace />;
  return <AppSettingsBody />;
}

function AppSettingsBody() {
  const { user, app, base, serverBuild, appHost } = useOutletContext();
  const [params, setParams] = useSearchParams();
  const [deleteOpen, setDeleteOpen] = useState(false);

  // 섹션은 URL에 둔다 — 새로고침·뒤로가기·링크 공유에서 같은 화면이 열린다.
  const q = params.get("section");
  // 초대하기는 멤버 탭으로 나갔다 — 예전 링크(?section=share)는 그쪽으로 보낸다
  if (q === "share") return <Navigate to={`${base}/members`} replace />;
  // GitHub Actions 배포는 그 칸을 주는 서버에서만 보인다 (서버가 먼저 올라가기 전에 웹이 배포돼도 깨지지 않게)
  const sections = SECTIONS.filter((s) => s.id !== "ci" || app.ci_deploy_enabled !== undefined);
  const section = SECTION_IDS.has(q) && sections.some((s) => s.id === q) ? q : "domain"; // 시안 기본 선택 = 도메인
  const select = (id) => setParams({ section: id }, { replace: true });


  return (
    <div className="flex-1 overflow-auto scroll-thin">
      {/* 좁은 화면에서 두 단을 한 단으로 — index.css는 다른 화면이 소유해서 여기서만 쓰는
          클래스를 지역 선언한다(kd-set- 접두로 충돌 방지). */}
      <style>{`
        .kd-set-grid { display: grid; grid-template-columns: ${NAV_W}px minmax(0, 1fr); }
        @media (max-width: 760px) {
          .kd-set-grid { grid-template-columns: minmax(0, 1fr); gap: 24px; }
          .kd-set-grid > nav { padding-right: 0; }
          .kd-set-grid > section { border-left: none !important; padding-left: 0 !important; }
          /* 좁은 화면에선 값 열 들여쓰기를 풀어 표·버튼이 폭을 다 쓰게 한다 */
          .kd-set-indent { padding-left: 0 !important; }
        }
      `}</style>

      <div className="kd-page" style={{ paddingTop: 28, paddingBottom: 72 }}>
        {/* ── 본문 2단 (좌 내비 197 / 세로 괘선 / 우 섹션, 시안 y296~) ── */}
        <div className="kd-set-grid">
          {/* 서브내비 — 활성 항목은 잉크 워시 + 좌측 2px 바 (시안 x56-323 / 바 x56-59) */}
          <nav
            className="flex flex-col"
            style={{ paddingTop: 6, paddingRight: 13, gap: 3 }}
          >
            {sections.filter((s) => s.nav).map((s) => {
              const on = s.id === section;
              return (
                <button
                  key={s.id}
                  onClick={() => select(s.id)}
                  className="kd-t-label kd-pick flex items-center text-left"
                  style={{ height: "var(--row-sm)", paddingLeft: 13, color: "var(--fg-2)" }}
                  aria-current={on ? "page" : undefined}
                >
                  <span className="kd-pick-name">{s.nav}</span>
                </button>
              );
            })}
          </nav>

          <section style={{ borderLeft: "1px solid var(--kd-border)", paddingLeft: 24 }}>
            {section === "domain" && <DomainSection appHost={appHost} />}
            {section === "general" && <GeneralSection build={serverBuild} />}
            {section === "storage" && <StorageSection build={serverBuild} />}
            {section === "ci" && <CiSection app={app} branch={serverBuild?.branch} />}
            {section === "cli" && <CliTokenSection app={app} />}
            {section === "danger" && <DangerSection onDelete={() => setDeleteOpen(true)} />}

            {/* 접힌 나머지 섹션 — 시안 괘선 y819/882/945, 행 높이 63 ⇒ 42(var(--row-lg)) */}
            <div style={{ marginTop: 24 }}>
              {sections.filter((s) => s.id !== section).map((s) => (
                <button
                  key={s.id}
                  onClick={() => select(s.id)}
                  className="kd-hoverable flex items-center w-full text-left"
                  style={{ height: "var(--row-lg)", borderTop: "1px solid var(--kd-border)" }}
                >
                  <span className="kd-t-section text-fg-1">{s.row}</span>
                  <ChevronRight
                    size={15}
                    strokeWidth={1.7}
                    className="ml-auto shrink-0 text-fg-3"
                  />
                </button>
              ))}
            </div>
          </section>
        </div>
      </div>

      {/* 앱 삭제는 반드시 확인 단계를 거친다 — 앱 이름을 타이핑해야 버튼이 열린다 */}
      {deleteOpen && (
        <DeleteAppModal app={app} onClose={() => setDeleteOpen(false)} />
      )}
    </div>
  );
}

/* ─────────────────────────── 도메인 ─────────────────────────── */

// 서비스 주소(기본 주소) + 사용자 도메인(연결·확인·변경·해제).
// 재배포 없이 PUT/DELETE /deploy/domain 만으로 반영된다.
function DomainSection({ appHost }) {
  const [info, setInfo] = useState(null); // { domain, status, ssl_status }
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const [error, setError] = useState(null);

  // 폴링 주기는 CustomDomainSection 그대로 — 레코드 추가 직후를 잡아야 해서
  // pending이면 8s, 안정 상태면 30s. 실패는 조용히 넘기고 다음 주기에 다시 시도한다.
  useEffect(() => {
    let cancelled = false;
    let timer;
    const tick = async () => {
      let pending = false;
      try {
        const d = await getDomain();
        if (!cancelled) {
          setInfo(d);
          pending = !!d?.domain && d.status !== "active";
        }
      } catch {
        /* 폴링 — 조용히 */
      }
      if (!cancelled) {
        setLoading(false);
        timer = setTimeout(tick, pending ? 8000 : 30000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const domain = info?.domain;
  const active = info?.status === "active";
  // 서브도메인 전용(apex 미지원)이라 넣을 레코드는 CNAME 한 줄. active면 숨긴다.
  const records =
    !active && domain
      ? [{ key: "cname", type: "CNAME", name: domain, value: CUSTOM_DOMAIN_CNAME_TARGET }]
      : [];
  const editable = editing || !domain;

  const onConnect = async () => {
    const d = draft.trim();
    if (!d || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await setDomain(d);
      setInfo(res);
      setDraft("");
      setEditing(false);
    } catch (e) {
      setError(e.message || "연결 실패");
    } finally {
      setBusy(false);
    }
  };

  // "연결 확인" — 폴링을 기다리지 않고 지금 상태를 다시 읽는다(검증은 서버 쪽 CF for SaaS가 한다).
  const onCheck = async () => {
    if (checking) return;
    setChecking(true);
    setError(null);
    try {
      setInfo(await getDomain());
    } catch (e) {
      setError(e.message || "확인 실패");
    } finally {
      setChecking(false);
    }
  };

  // 해제는 되돌릴 수 없어서 두 번 눌러야 한다(ExtraBody 복원과 같은 확인 방식).
  const onDisconnect = async () => {
    if (busy) return;
    if (!confirmOff) {
      setConfirmOff(true);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await deleteDomain();
      setInfo({ domain: null });
      setDraft("");
    } catch (e) {
      setError(e.message || "해제 실패");
    } finally {
      setBusy(false);
      setConfirmOff(false);
    }
  };

  return (
    <>
      {/* ── 서비스 주소 (시안 제목 y307 / 행 y353 / 구분선 y404) ── */}
      <SectionHead>서비스 주소</SectionHead>
      <div style={{ marginTop: 8 }}>
        <Field label="기본 주소">
          <span className="flex items-center gap-3.5 min-w-0">
            <a
              href={`https://${appHost}`}
              target="_blank"
              rel="noopener noreferrer"
              className="kd-t-body-s text-fg-1 no-underline hover:underline truncate"
            >
              {appHost}
            </a>
            <a
              href={`https://${appHost}`}
              target="_blank"
              rel="noopener noreferrer"
              title="새 탭에서 열기"
              aria-label="새 탭에서 열기"
              className="shrink-0 text-fg-3 hover:text-fg-1 transition-colors"
            >
              <ArrowUpRight size={15} strokeWidth={1.7} />
            </a>
            <CopyButton text={appHost} title="주소 복사" />
          </span>
        </Field>
      </div>
      <div style={{ borderTop: "1px solid var(--kd-border)" }} />

      {/* ── 사용자 도메인 (시안 제목 y434 / 입력 y479-524 / 표 y588-682 / 버튼 y738) ── */}
      <SectionHead style={{ marginTop: 20 }}>사용자 도메인</SectionHead>

      {loading ? (
        <div className="kd-t-body-s text-fg-3" style={{ marginTop: 12 }}>
          불러오는 중…
        </div>
      ) : (
        <div style={{ marginTop: 8 }}>
          <Field label="도메인">
            <div className="flex items-center gap-4 min-w-0">
              <input
                className="kd-input"
                style={{ maxWidth: INPUT_W }}
                // 도메인은 대소문자를 가리지 않으므로 소문자로 고정해 서버 비교와 어긋나지 않게 한다
                value={editable ? draft : domain}
                onChange={(e) => setDraft(e.target.value.toLowerCase())}
                onKeyDown={(e) => e.key === "Enter" && editable && onConnect()}
                placeholder="api.example.com"
                spellCheck={false}
                readOnly={!editable}
                disabled={busy}
              />
              {domain && !editable && <DomainStatusBadge status={info.status} />}
            </div>
          </Field>

          {/* 안내·레코드 표·버튼은 라벨 열만큼 들여써서 입력칸(값 열)에 맞춘다 —
              시안에서도 이 셋의 좌단이 입력칸 좌단(x542)과 같다. */}
          <div className="kd-set-indent" style={{ paddingLeft: LABEL_W }}>
          {editable ? (
            <p className="kd-t-body-s text-fg-2" style={{ marginTop: 12 }}>
              서브도메인만 연결할 수 있어요 - 루트 도메인(example.com)은 아직 지원하지 않아요.
            </p>
          ) : (
            <>
              {records.length > 0 && (
                <>
                  <p className="kd-t-body-s text-fg-2" style={{ marginTop: 12 }}>
                    도메인 제공업체에서 다음 DNS 레코드를 추가하세요.
                  </p>
                  <RecordTable records={records} />
                </>
              )}
              <p className="kd-t-body-s text-fg-2" style={{ marginTop: 12 }}>
                {active
                  ? "연결이 끝났어요. HTTPS 인증서까지 발급된 상태예요."
                  : "DNS 연결이 확인되면 HTTPS 설정을 진행합니다."}
              </p>
            </>
          )}

          {/* 버튼 — 시안 높이 46 ⇒ 32 (kd-btn-sm), 사이 간격 15 ⇒ 10 */}
          <div className="flex items-center gap-2.5" style={{ marginTop: 16 }}>
            {editable ? (
              <>
                <button
                  className="kd-btn-primary kd-btn-sm"
                  onClick={onConnect}
                  disabled={busy || !draft.trim()}
                >
                  {busy ? "연결 중…" : domain ? "저장" : "연결"}
                </button>
                {domain && (
                  <button
                    className="kd-btn-secondary kd-btn-sm"
                    onClick={() => {
                      setEditing(false);
                      setDraft("");
                      setError(null);
                    }}
                    disabled={busy}
                  >
                    취소
                  </button>
                )}
              </>
            ) : (
              <>
                <button
                  className="kd-btn-primary kd-btn-sm"
                  onClick={onCheck}
                  disabled={checking}
                >
                  {checking ? "확인 중…" : "연결 확인"}
                </button>
                <button
                  className="kd-btn-secondary kd-btn-sm"
                  onClick={() => {
                    setDraft(domain);
                    setEditing(true);
                    setConfirmOff(false);
                  }}
                >
                  도메인 변경
                </button>
                <button
                  className="kd-t-label ml-auto shrink-0 transition-colors disabled:opacity-40"
                  style={{ color: confirmOff ? "var(--err-fg)" : "var(--fg-3)" }}
                  onClick={onDisconnect}
                  onBlur={() => setConfirmOff(false)}
                  disabled={busy}
                >
                  {confirmOff ? "한 번 더 누르면 해제돼요" : "연결 해제"}
                </button>
              </>
            )}
          </div>
          </div>
        </div>
      )}

      {error && (
        <p
          className="kd-t-caption kd-set-indent"
          style={{ color: "var(--err-fg)", marginTop: 10, paddingLeft: LABEL_W }}
        >
          {error}
        </p>
      )}
    </>
  );
}

// DNS 레코드 표 — 열 시작은 시안 x556/755/1014(표 좌단 541) 비율에서 뽑았다.
function RecordTable({ records }) {
  const cols = { display: "grid", gridTemplateColumns: "22% 28% minmax(0, 1fr)", alignItems: "center" };
  return (
    <div style={{ marginTop: 10, borderTop: "1px solid var(--kd-border)" }}>
      <div
        className="kd-t-micro text-fg-3"
        style={{ ...cols, height: "var(--row-sm)", paddingInline: 10, borderBottom: "1px solid var(--kd-border)" }}
      >
        <span>유형</span>
        <span>이름</span>
        <span>대상</span>
      </div>
      {records.map((r) => (
        <div
          key={r.key}
          className="kd-t-body-s text-fg-1"
          style={{ ...cols, height: "var(--row-md)", paddingInline: 10, borderBottom: "1px solid var(--kd-border)" }}
        >
          <span>{r.type}</span>
          {/* 제공업체 화면에 그대로 붙일 수 있게 전체 도메인을 보여준다(레이블만 받는 곳은 알아서 자른다) */}
          <span className="truncate">{r.name}</span>
          <span className="flex items-center gap-2 min-w-0">
            <span className="truncate">{r.value}</span>
            <CopyButton text={r.value} title="대상 복사" />
          </span>
        </div>
      ))}
    </div>
  );
}

/* ─────────────────────────── 일반(빌드와 실행) ─────────────────────────── */

// 읽기 전용 — 런타임·포트·빌드 방식을 고치는 API가 없다. 값 변경은 재배포로만 가능하다.
function GeneralSection({ build }) {
  const rows = [
    { label: "런타임", value: build ? RUNTIME_LABEL[build.runtime] || build.runtime : "—" },
    { label: "포트", value: build?.port != null ? String(build.port) : "—" },
    {
      label: "빌드 방식",
      value: build ? BUILD_MODE_LABEL[build.build_mode] || build.build_mode : "—",
    },
    { label: "브랜치", value: build?.branch || "—" },
  ];

  return (
    <>
      <SectionHead>빌드와 실행 설정</SectionHead>
      <div style={{ marginTop: 8 }}>
        <Field label="저장소" divider>
          {build?.repo_url ? (
            <a
              href={build.repo_url}
              target="_blank"
              rel="noopener noreferrer"
              className="kd-t-body-s text-fg-1 no-underline hover:underline inline-flex items-center gap-1.5 min-w-0"
            >
              <span className="truncate">{repoSlug(build.repo_url)}</span>
              <ArrowUpRight size={15} strokeWidth={1.7} className="shrink-0 text-fg-3" />
            </a>
          ) : (
            <span className="kd-t-body-s text-fg-3">—</span>
          )}
        </Field>
        {rows.map((r, i) => (
          <Field key={r.label} label={r.label} divider={i < rows.length - 1}>
            <span className="kd-t-body-s text-fg-1">{r.value}</span>
          </Field>
        ))}
      </div>

      <p className="kd-t-caption text-fg-3" style={{ marginTop: 14 }}>
        실행 설정은 이 화면에서 바꿀 수 없어요. 값을 바꾸려면 같은 저장소로 다시 배포하세요.
      </p>
      <Link
        to={`${base}/deploy`}
        className="kd-btn-secondary kd-btn-sm inline-flex items-center no-underline"
        style={{ marginTop: 12 }}
      >
        재배포에서 변경
      </Link>
    </>
  );
}

/* ─────────────────────────── 스토리지 ─────────────────────────── */

// 연결된 저장소(읽기 전용) + DB 스냅샷 내보내기/복원.
// 내보내기·복원 로직은 ExtraBody 그대로 — 앵커 클릭 스트림 다운로드, 복원은 2단계 확인.
function StorageSection({ build }) {
  const [file, setFile] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null); // { kind: "ok" | "err", text }

  const db = build?.db_type && build.db_type !== "none" ? build.db_type : null;
  const storageLabel =
    build?.storage === "object"
      ? "오브젝트 스토리지 · R2"
      : build?.storage === "local"
        ? `영구 볼륨 · ${build.volume_mount_path || "경로 미지정"}`
        : "없음";

  // GET + cookie 인증 → 앵커 클릭으로 스트림 다운로드 (브라우저 메모리에 담지 않는다)
  const onExport = () => {
    const a = document.createElement("a");
    a.href = dbExportUrl();
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const onPick = (e) => {
    setFile(e.target.files?.[0] || null);
    setConfirming(false);
    setMsg(null);
  };

  const onRestore = async () => {
    if (!file) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const res = await restoreDb(file);
      setMsg({ kind: "ok", text: res.output ? res.output : "복원 완료" });
    } catch (err) {
      setMsg({ kind: "err", text: err.message || "복원 실패" });
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <>
      <SectionHead>스토리지 설정</SectionHead>
      <div style={{ marginTop: 8 }}>
        <Field label="파일 저장소" divider>
          <span className="kd-t-body-s text-fg-1">{storageLabel}</span>
        </Field>
        <Field label="데이터베이스">
          <span className="kd-t-body-s text-fg-1">{db ? DB_LABEL[db] || db : "없음"}</span>
        </Field>
      </div>
      <div style={{ borderTop: "1px solid var(--kd-border)" }} />

      <SectionHead style={{ marginTop: 20 }}>DB 스냅샷</SectionHead>
      {!db ? (
        <p className="kd-t-body-s text-fg-3" style={{ marginTop: 10 }}>
          연결된 데이터베이스가 없어 내보내거나 복원할 스냅샷이 없어요.
        </p>
      ) : (
        <>
          <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10 }}>
            현재 앱의 데이터베이스를 통째로 내려받거나, 백업 파일로 되돌립니다.
          </p>

          <div style={{ marginTop: 12, borderTop: "1px solid var(--kd-border)" }}>
            {/* 내보내기 */}
            <div
              className="flex items-center gap-3.5"
              style={{ height: "var(--row-lg)", borderBottom: "1px solid var(--kd-border)" }}
            >
              <Download size={18} strokeWidth={1.6} className="shrink-0 text-fg-1" />
              <span className="kd-t-body-s text-fg-1">내보내기</span>
              <span className="kd-t-caption text-fg-3 truncate">.sql.gz 파일로 다운로드</span>
              <button className="kd-btn-secondary kd-btn-sm ml-auto shrink-0" onClick={onExport}>
                다운로드
              </button>
            </div>

            {/* 복원 — 기존 데이터를 덮어쓰므로 파일 선택 후 두 번 눌러야 실행된다 */}
            <div
              className="flex items-center gap-3.5"
              style={{ minHeight: "var(--row-lg)", borderBottom: "1px solid var(--kd-border)" }}
            >
              <Upload size={18} strokeWidth={1.6} className="shrink-0 text-fg-1" />
              <span className="kd-t-body-s text-fg-1">복원</span>
              <span className="kd-t-caption truncate" style={{ color: file ? "var(--fg-1)" : "var(--err-fg)" }}>
                {file ? file.name : "기존 데이터를 덮어써요"}
              </span>
              <div className="ml-auto shrink-0 flex items-center gap-2.5">
                {file && (
                  <button
                    className="kd-t-label text-fg-3 hover:text-fg-1 transition-colors"
                    onClick={() => {
                      setFile(null);
                      setConfirming(false);
                      setMsg(null);
                    }}
                    disabled={busy}
                  >
                    취소
                  </button>
                )}
                {!file ? (
                  <label className="kd-btn-secondary kd-btn-sm inline-flex items-center cursor-pointer">
                    파일 선택
                    <input
                      type="file"
                      accept=".sql,.gz,.sql.gz,application/sql,application/gzip"
                      onChange={onPick}
                      className="hidden"
                      disabled={busy}
                    />
                  </label>
                ) : (
                  <button
                    className="kd-btn-secondary kd-btn-sm disabled:opacity-40"
                    style={{ color: "var(--err-fg)" }}
                    onClick={onRestore}
                    disabled={busy}
                  >
                    {busy ? "복원 중…" : confirming ? "한 번 더 누르면 덮어써요" : "복원 실행"}
                  </button>
                )}
              </div>
            </div>
          </div>

          {msg && (
            <pre
              className="kd-t-code whitespace-pre-wrap break-words max-h-40 overflow-auto scroll-thin"
              style={{
                marginTop: 12,
                padding: 12,
                borderRadius: 4,
                background: "var(--kd-surface)",
                border: "1px solid var(--kd-border)",
                color: msg.kind === "ok" ? "var(--ok-fg)" : "var(--err-fg)",
              }}
            >
              {msg.text}
            </pre>
          )}
        </>
      )}
    </>
  );
}

/* ─────────────────────────── GitHub Actions 배포 ─────────────────────────── */

// "GitHub Actions 배포 허용" 토글 + 워크플로 예시(접힘). 주인만 이 화면에 들어온다 (서버도 주인만 받는다).
// 켜면 이 앱 저장소의 배포 브랜치에서 돈 Actions가 GitHub ID 토큰을 CI 토큰으로 바꿔 재배포를 부른다.
// 끄면 이미 발급된 CI 토큰도 바로 무효가 된다.
function CiSection({ app, branch }) {
  const { refreshApp } = useCurrentApp();
  const [enabled, setEnabled] = useState(!!app.ci_deploy_enabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);
  // 배포를 받을 워크플로 파일 — 서버가 이 칸을 주기 전(옛 서버)에는 입력을 보이지 않는다
  const hasWorkflow = "ci_workflow" in app;
  const [workflow, setWorkflow] = useState(app.ci_workflow || "");

  useEffect(() => setEnabled(!!app.ci_deploy_enabled), [app.ci_deploy_enabled]);
  useEffect(() => setWorkflow(app.ci_workflow || ""), [app.ci_workflow]);

  const workflowChanged = workflow.trim() !== (app.ci_workflow || "");

  const onSaveWorkflow = async () => {
    if (busy || !workflowChanged) return;
    setBusy(true);
    setError(null);
    try {
      const res = await setCiDeploy(app.id, enabled, workflow.trim());
      setWorkflow(res.ci_workflow || "");
      refreshApp?.();
    } catch (e) {
      setError(e.message || "저장하지 못했어요");
    } finally {
      setBusy(false);
    }
  };

  const onToggle = async () => {
    if (busy) return;
    const next = !enabled;
    setBusy(true);
    setError(null);
    try {
      const res = await setCiDeploy(app.id, next);
      setEnabled(!!res.ci_deploy_enabled);
      refreshApp?.();
    } catch (e) {
      setError(e.message || "바꾸지 못했어요");
    } finally {
      setBusy(false);
    }
  };

  const yaml = ciWorkflowExample(app.id, branch || "main");

  return (
    <>
      <SectionHead>GitHub Actions 배포</SectionHead>
      <div style={{ marginTop: 14 }}>
        <Checkbox checked={enabled} onChange={onToggle} disabled={busy}>
          GitHub Actions 배포 허용
        </Checkbox>
      </div>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10 }}>
        켜면 이 앱 저장소의 배포 브랜치에서 실행된 GitHub Actions가 이 앱을 배포할 수 있어요. 저장소에 push할 수 있는
        사람이 배포할 수 있게 돼요.
      </p>
      {hasWorkflow && (
        <div style={{ marginTop: 12 }}>
          <Field label="워크플로 파일">
            <div className="flex items-center gap-2.5 min-w-0">
              <input
                className="kd-input"
                style={{ maxWidth: INPUT_W }}
                value={workflow}
                onChange={(e) => setWorkflow(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onSaveWorkflow()}
                placeholder=".github/workflows/deploy.yml"
                spellCheck={false}
                disabled={busy}
              />
              <button
                className="kd-btn-primary kd-btn-sm shrink-0"
                onClick={onSaveWorkflow}
                disabled={busy || !workflowChanged}
              >
                저장
              </button>
            </div>
          </Field>
          <p className="kd-t-caption text-fg-3 kd-set-indent" style={{ paddingLeft: LABEL_W }}>
            지정하면 이 파일에서 실행된 잡만 배포할 수 있어요. 비워 두면 저장소의 어느 워크플로에서든 배포할 수 있어요.
          </p>
        </div>
      )}
      {error && (
        <p className="kd-t-caption" style={{ color: "var(--err-fg)", marginTop: 8 }}>
          {error}
        </p>
      )}

      <div style={{ marginTop: 16, borderTop: "1px solid var(--kd-border)", paddingTop: 8 }}>
        <Disclosure open={open} onToggle={() => setOpen((v) => !v)} label="워크플로 예시">
          <p className="kd-t-caption text-fg-3">
            저장소의 .github/workflows/ 아래에 넣으면 {branch || "main"} 브랜치에 push할 때마다 지금 설정 그대로 그 커밋을
            배포해요. 다른 브랜치나 pull request에서 실행하면 거절돼요.
          </p>
          <div className="relative" style={{ marginTop: 10 }}>
            <pre
              className="kd-t-code whitespace-pre overflow-auto scroll-thin"
              style={{
                padding: 12,
                borderRadius: 4,
                background: "var(--kd-surface)",
                border: "1px solid var(--kd-border)",
                color: "var(--fg-1)",
              }}
            >
              {yaml}
            </pre>
            <span className="absolute" style={{ top: 10, right: 10 }}>
              <CopyButton text={yaml} title="워크플로 복사" />
            </span>
          </div>
        </Disclosure>
      </div>
    </>
  );
}

// 워크플로 예시 — ID 토큰(audience=kodeploy)을 CI 토큰으로 바꾸고 재배포를 부른다.
// 이름표(Idempotency-Key)는 실행마다 하나라서, 같은 실행 안에서 다시 보내도 빌드는 한 번만 시작된다.
function ciWorkflowExample(appId, branch) {
  return `name: kodeploy
on:
  push:
    branches: [${branch}]
permissions:
  id-token: write
  contents: read
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: deploy to KoDeploy
        env:
          APP_ID: ${appId}
          API: ${apiOrigin()}
        run: |
          set -eu
          ID_TOKEN=$(curl -fsS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \\
            "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=kodeploy" | jq -r .value)
          TOKEN=$(curl -fsS -X POST "$API/apps/$APP_ID/ci/token" \\
            -H 'Content-Type: application/json' -d "{\\"id_token\\":\\"$ID_TOKEN\\"}" | jq -r .token)
          curl -fsS -X POST "$API/apps/$APP_ID/deploy/redeploy" \\
            -H "Authorization: Bearer $TOKEN" \\
            -H "Idempotency-Key: gha-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT" \\
            -H 'Content-Type: application/json' -d '{}'
`;
}

/* ─────────────────────────── CLI 토큰 ─────────────────────────── */

// 로컬 터미널의 kodeploy CLI에 넣는 토큰. 주인만 이 화면에 들어온다 (서버도 주인 세션만 받는다).
// 원문은 발급 응답에만 있어서 이 화면을 벗어나면 다시 볼 수 없다 — 잃어버리면 다시 발급한다(이전 토큰은 바로 끝난다).
// GitHub Actions 배포 허용과는 따로다.
function CliTokenSection({ app }) {
  const [state, setState] = useState(null); // { current, idle_seconds, max_seconds }
  const [raw, setRaw] = useState(null); // 방금 발급한 원문 — 이 화면에서만 들고 있는다
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(false);

  const load = async () => {
    try {
      setState(await getCliToken(app.id));
    } catch (e) {
      setError(e.message || "불러오지 못했어요");
    }
  };

  useEffect(() => {
    load();
  }, [app.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn, fallback) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e.message || fallback);
    } finally {
      setBusy(false);
    }
  };

  const onIssue = () =>
    run(async () => {
      const res = await issueCliToken(app.id);
      setRaw(res.token);
    }, "발급하지 못했어요");

  const onRevoke = () =>
    run(async () => {
      await revokeCliToken(app.id);
      setRaw(null);
    }, "폐기하지 못했어요");

  const current = state?.current;
  const idleMin = Math.round((state?.idle_seconds ?? 900) / 60);
  const maxHours = Math.round((state?.max_seconds ?? 8 * 3600) / 3600);
  const usage = cliUsageExample(app.id);

  return (
    <>
      <SectionHead>CLI 토큰</SectionHead>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10 }}>
        로컬 터미널의 kodeploy CLI로 이 앱을 배포하거나 셸·DB 콘솔을 열 때 쓰는 토큰이에요. 마지막으로 쓴 뒤 {idleMin}분 동안 쓰지 않으면
        만료되고, 쓸 때마다 {idleMin}분으로 늘어나요. 발급하고 {maxHours}시간이 지나면 쓰는 중이어도 만료돼요. 앱마다 하나만
        살아 있어서 새로 발급하면 이전 토큰은 바로 쓸 수 없어요. GitHub Actions 배포 허용과는 따로예요.
      </p>

      {raw && (
        <div style={{ marginTop: 14 }}>
          <Field label="토큰">
            <div className="flex items-center gap-2.5 min-w-0">
              <code
                className="kd-t-code min-w-0 overflow-auto scroll-thin whitespace-nowrap"
                style={{
                  maxWidth: INPUT_W,
                  padding: "6px 10px",
                  borderRadius: 4,
                  background: "var(--kd-surface)",
                  border: "1px solid var(--kd-border)",
                  color: "var(--fg-1)",
                }}
              >
                {raw}
              </code>
              <CopyButton text={raw} title="토큰 복사" />
            </div>
          </Field>
          <p className="kd-t-caption text-fg-3 kd-set-indent" style={{ paddingLeft: LABEL_W }}>
            이 화면을 벗어나면 다시 볼 수 없어요. 지금 복사해 두세요.
          </p>
        </div>
      )}

      {current && (
        <div style={{ marginTop: raw ? 6 : 14 }}>
          <Field label="발급" divider>
            <span className="kd-t-body-s text-fg-1">
              {current.issued_by_login || "—"} · {dayTime(current.issued_at)}
            </span>
          </Field>
          <Field label="마지막 사용" divider>
            <span className="kd-t-body-s text-fg-1">
              {current.last_used_at ? dayTime(current.last_used_at) : "아직 안 썼어요"}
            </span>
          </Field>
          <Field label="만료">
            <span className="kd-t-body-s text-fg-1">
              {dayTime(current.expires_at)}
              <span className="text-fg-3"> · 쓰면 늘어나요, 늦어도 {dayTime(current.max_expires_at)}</span>
            </span>
          </Field>
        </div>
      )}

      <div className="flex items-center gap-2.5" style={{ marginTop: 14 }}>
        <button className="kd-btn-primary kd-btn-sm" onClick={onIssue} disabled={busy || state === null}>
          {current ? "다시 발급" : "발급"}
        </button>
        {current && (
          <button
            className="kd-btn-secondary kd-btn-sm"
            style={{ color: "var(--err-fg)" }}
            onClick={onRevoke}
            disabled={busy}
          >
            폐기
          </button>
        )}
      </div>
      {error && (
        <p className="kd-t-caption" style={{ color: "var(--err-fg)", marginTop: 8 }}>
          {error}
        </p>
      )}

      <div style={{ marginTop: 16, borderTop: "1px solid var(--kd-border)", paddingTop: 8 }}>
        <Disclosure open={open} onToggle={() => setOpen((v) => !v)} label="사용 예">
          <p className="kd-t-caption text-fg-3">
            토큰은 셸 기록에 남지 않게 read로 넣어요. 토큰이 만료되면 CLI가 종료 코드 125로 끝나요 — 여기서 다시
            발급하면 돼요.
          </p>
          <div className="relative" style={{ marginTop: 10 }}>
            <pre
              className="kd-t-code whitespace-pre overflow-auto scroll-thin"
              style={{
                padding: 12,
                borderRadius: 4,
                background: "var(--kd-surface)",
                border: "1px solid var(--kd-border)",
                color: "var(--fg-1)",
              }}
            >
              {usage}
            </pre>
            <span className="absolute" style={{ top: 10, right: 10 }}>
              <CopyButton text={usage} title="사용 예 복사" />
            </span>
          </div>
        </Disclosure>
      </div>
    </>
  );
}

function cliUsageExample(appId) {
  return `export KODEPLOY_APP=${appId}
read -rs KODEPLOY_TOKEN && export KODEPLOY_TOKEN   # 토큰을 붙여 넣고 엔터

kodeploy commits               # 최근 커밋 3개와 지금 떠 있는 커밋
kodeploy deploy                # 브랜치 최신으로 배포
kodeploy deploy --pick         # 최근 커밋 3개 중에서 골라 배포
kodeploy deploy --sha <sha>    # 그 커밋(40자)으로 배포

kodeploy shell                 # 앱 컨테이너 셸 (끝내려면 exit)
kodeploy db                    # DB 콘솔
kodeploy redis                 # Redis 콘솔
`;
}

/* ─────────────────────────── 앱 삭제 ─────────────────────────── */

function DangerSection({ onDelete }) {
  return (
    <>
      <SectionHead>앱 삭제</SectionHead>
      <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10 }}>
        K8s 리소스 · DB 데이터(PVC) · 환경변수 · 빌드 히스토리가 모두 삭제됩니다. 되돌릴 수 없어요.
      </p>
      <button
        className="kd-btn-secondary kd-btn-sm"
        style={{ marginTop: 14, color: "var(--err-fg)" }}
        onClick={onDelete}
      >
        앱 삭제
      </button>
    </>
  );
}

/* ─────────────────────────── 공용 조각 ─────────────────────────── */

function SectionHead({ children, style }) {
  return (
    <h2 className="kd-t-section text-fg-1" style={style}>
      {children}
    </h2>
  );
}

// 라벨 + 값 한 줄. 라벨 열 폭은 시안 실측 114, 행 높이는 var(--row-lg)=42.
function Field({ label, children, divider }) {
  return (
    <div
      className="flex items-center"
      style={{
        minHeight: "var(--row-lg)",
        borderBottom: divider ? "1px solid var(--kd-border)" : "none",
      }}
    >
      <span className="kd-t-label text-fg-2 shrink-0" style={{ width: LABEL_W }}>
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

// 클릭 복사 — 1.5초 뒤 원래 아이콘으로 돌아온다(CustomDomainSection과 같은 규칙).
function CopyButton({ text, title = "복사" }) {
  const [done, setDone] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const onClick = () => {
    navigator.clipboard?.writeText(text);
    setDone(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setDone(false), 1500);
  };

  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className="shrink-0 text-fg-3 hover:text-fg-1 transition-colors"
    >
      {done ? (
        <Check size={15} strokeWidth={2.2} style={{ color: "var(--ok-fg)" }} />
      ) : (
        <Copy size={15} strokeWidth={1.7} />
      )}
    </button>
  );
}

