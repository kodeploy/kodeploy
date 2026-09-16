// 이용 방법 ("/how") — design/라이트모드-시안/19_이용방법.png 기준.
//
// 구성: 가운데 명조 히어로 → 3단계 목록(좌: 번호·제목·설명 / 우: 배포 화면을 축약한 미리보기 카드)
//       → 바닥 가운데 CTA. 행 사이는 콘텐츠 폭 괘선 하나로만 나눈다(면·그림자 없음).
// 시안은 CTA까지가 딱 한 화면(1024)이라 푸터 없이 끝난다 — 세로 리듬을 좁게 잡은 이유다.
//
// 치수 주석의 숫자는 시안 원본 px(1536폭 렌더)이고 실제 값은 ÷1.217한 CSS px이다
//   (상단바 괘선이 y=73 → 코드의 TopBar 높이 60px 기준 스케일 1.217).
//
// 미리보기 카드는 API를 부르지 않는다 — 값은 예시고 실제 내 앱 상태가 아니다(캡션 "예시 화면").
// 다만 브랜치·런타임·포트·데이터베이스는 직접 만져볼 수 있다: 고르면 실제 배포 폼과 같은 규칙으로
// 움직이고(런타임을 바꾸면 기본 포트가 따라온다), 3단계 로그의 Listening 포트도 같이 바뀐다.
// 선택지는 배포 폼과 같은 출처(steps/options.js)를 쓴다 — 여기서만 다른 목록을 들고 있지 않게.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowUpRight, ChevronDown, GitBranch } from "lucide-react";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { DB_OPTIONS, DEFAULT_PORTS, RUNTIME_META } from "../deploy/steps/options.js";

// 예시 저장소 — 고른 저장소 이름이 곧 앱 이름이 된다(3단계 카드 제목).
const REPOS = ["me / my-api", "me / my-web", "me / my-worker"];
const appNameOf = (repo) => repo.split("/").pop().trim();

// 브랜치 예시 — 실제 저장소에서 흔한 세 갈래.
const BRANCHES = ["main", "dev", "stage"];

// 서버 런타임 4종. "사용 안 함"(정적 사이트 단독)은 이 화면의 이야기가 아니라 뺀다.
const RUNTIME_IDS = Object.keys(RUNTIME_META).filter((id) => id !== "none");

// 시안 로그 5줄. 마지막 줄은 위에서 고른 포트를 따라간다.
const logLines = (port) => [
  "Installing dependencies...",
  "Build completed successfully",
  "Starting application...",
  "Application startup complete",
  `Listening on :${port || "8000"}`,
];

const STEPS = [
  {
    no: "01",
    title: "GitHub 저장소 연결",
    body: "배포할 저장소와 브랜치를 선택하세요.",
    Preview: RepoCard,
  },
  {
    no: "02",
    title: "실행 환경 설정",
    body: "앱의 포트와 필요한 데이터베이스를 선택하세요.",
    Preview: RuntimeCard,
  },
  {
    no: "03",
    title: "배포 결과 확인",
    body: "빌드 로그와 앱의 실행 상태를 확인하세요.",
    Preview: ResultCard,
  },
];

export default function HowTo() {
  const navigate = useNavigate();
  const { user, openLogin } = useAuth();
  // 카드 3장이 한 앱을 따라가야 하므로 선택은 여기서 들고 있는다.
  const [repo, setRepo] = useState(REPOS[0]);
  const [branch, setBranch] = useState("main");
  const [runtime, setRuntime] = useState("python");
  const [port, setPort] = useState(String(DEFAULT_PORTS.python));
  const [db, setDb] = useState("postgres");

  // 실제 폼과 같은 규칙 — 런타임을 바꾸면 그 런타임의 기본 포트로 맞춘다.
  const pickRuntime = (id) => {
    setRuntime(id);
    setPort(String(DEFAULT_PORTS[id] ?? ""));
  };

  const example = {
    repo,
    onRepo: setRepo,
    branch,
    onBranch: setBranch,
    runtime,
    onRuntime: pickRuntime,
    port,
    // 포트 칸은 숫자만, 5자리까지(65535)
    onPort: (v) => setPort(v.replace(/\D/g, "").slice(0, 5)),
    db,
    onDb: setDb,
  };

  // 랜딩(Home.jsx)의 시작 버튼과 같은 동작 — 로그인 상태면 배포 화면, 아니면 로그인 모달.
  const handleStart = () => {
    if (user) navigate("/deploy");
    else openLogin?.();
  };

  return (
    // 시안: 상단바 괘선(73) 아래 61 → 히어로 잉크 134
    <div className="kd-page-narrow" style={{ paddingTop: 40, paddingBottom: 48 }}>
      <header className="text-center">
        <h1 className="kd-t-hero" style={{ color: "var(--fg-1)" }}>
          처음이라면, 이 순서로.
        </h1>
        {/* 시안: 히어로 잉크 아래 24 → 리드문 잉크 230 */}
        <p className="kd-t-lead" style={{ color: "var(--fg-2)", marginTop: 8 }}>
          저장소 연결부터 앱 실행까지 따라가세요.
        </p>
      </header>

      {/* 스텝 목록 — 시안 카드 상단 300(리드문 잉크 아래 47 ≈ CSS 30) */}
      <ol style={{ marginTop: 30, listStyle: "none", padding: 0 }}>
        {STEPS.map((step, i) => (
          <li
            key={step.no}
            // 시안: 카드 아래 21 → 괘선 → 21 위 다음 카드 (≈ CSS 17). 첫 행 위에는 괘선이 없다.
            style={{
              paddingBlock: 17,
              borderTop: i === 0 ? "none" : "1px solid var(--kd-border)",
            }}
          >
            {/* 시안 카드가 콘텐츠 폭의 50.5% 지점에서 시작해 오른쪽 끝까지 간다(폭 49.5%).
                gap 20이 1fr에서 빠져 나가 카드 시작점이 정확히 50.5%가 된다. */}
            <div className="grid items-center gap-y-5 md:gap-x-5 md:[grid-template-columns:minmax(0,1fr)_49.5%]">
              <div className="flex">
                {/* 시안: 번호 x0-30, 제목 x92부터 */}
                <span
                  className="kd-t-lead shrink-0 w-12 md:w-[92px]"
                  style={{ color: "var(--fg-4)" }}
                  aria-hidden
                >
                  {step.no}
                </span>
                <div className="min-w-0">
                  <h2 className="kd-t-title" style={{ color: "var(--fg-1)" }}>
                    {step.title}
                  </h2>
                  <p
                    className="kd-t-body"
                    style={{ color: "var(--fg-2)", marginTop: 4, wordBreak: "keep-all" }}
                  >
                    {step.body}
                  </p>
                </div>
              </div>

              {/* 미리보기 — 값이 전부 예시라 캡션으로 밝힌다(랜딩의 "예시 화면"과 같은 표기) */}
              <figure className="m-0 min-w-0">
                <step.Preview {...example} />
                <figcaption className="kd-t-caption" style={{ color: "var(--fg-4)", marginTop: 6 }}>
                  예시 화면
                </figcaption>
              </figure>
            </div>
          </li>
        ))}
      </ol>

      {/* 시안: 마지막 카드 아래 32 → 버튼 216x55(≈ CSS 177x45 → .kd-btn-lg가 가장 가깝다) */}
      <div className="flex items-center justify-center flex-wrap gap-x-6 gap-y-3" style={{ marginTop: 22 }}>
        <button
          type="button"
          onClick={handleStart}
          className="kd-btn-primary kd-btn-lg inline-flex items-center justify-center gap-2"
        >
          첫 앱 배포하기
        </button>
        <Link
          to="/guide"
          className="kd-t-body-s inline-flex items-center gap-1.5 underline"
          style={{ color: "var(--fg-2)", textUnderlineOffset: 4 }}
        >
          자세한 가이드 보기
          <ArrowUpRight size={15} aria-hidden />
        </Link>
      </div>
    </div>
  );
}

// ── 미리보기 카드 ──────────────────────────────────────────────────────────
// 시안 카드: 폭 651 / 높이 152·177·187, 안쪽 여백 22(≈ CSS 18).

// 카드 머리 — 제목 + 한 줄 설명. 세 카드가 같은 간격을 쓴다.
function CardHead({ title, body }) {
  return (
    <>
      <div className="kd-t-section" style={{ color: "var(--fg-1)" }}>
        {title}
      </div>
      <p className="kd-t-body-s" style={{ color: "var(--fg-3)", marginTop: 2 }}>
        {body}
      </p>
    </>
  );
}

// 읽기 전용 칸 — 저장소처럼 고를 게 없는 값(GitHub에서 불러오는 목록이라 예시로 고정).
function FakeField({ lead, value, chevron = true }) {
  return (
    <div className="kd-input flex items-center min-w-0" style={{ paddingInline: 0 }}>
      {lead}
      <span
        className="flex-1 min-w-0 truncate"
        style={{ color: "var(--fg-1)", paddingInline: lead ? 10 : 12 }}
      >
        {value}
      </span>
      {chevron && (
        <ChevronDown
          size={15}
          aria-hidden
          className="shrink-0"
          style={{ color: "var(--fg-4)", marginRight: 10 }}
        />
      )}
    </div>
  );
}

// 고르는 칸 — 상자는 .kd-input(테두리·높이·배경), 펼친 목록은 작업 공간의 MiniSelect와 같은
// 모양(kd-card 면, --row-md 줄, 고른 값 --sel-soft · 호버 --sel)이다.
// native <select>는 펼친 목록을 OS가 그려 테마·톤을 못 따라가서 직접 그린다.
// 키보드: 상자에서 Enter/Space/↓로 열고, ↑↓로 이동, Enter로 고르고, Esc·바깥 클릭으로 닫는다.
function PickField({ lead, label, value, onChange, options }) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(-1);
  const wrapRef = useRef(null);
  const btnRef = useRef(null);
  const current = options.find((o) => o.id === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => !wrapRef.current?.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const openMenu = () => {
    setHover(Math.max(0, options.findIndex((o) => o.id === value)));
    setOpen(true);
  };
  const pick = (id) => {
    onChange(id);
    setOpen(false);
    btnRef.current?.focus();
  };

  const onKeyDown = (e) => {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHover((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHover((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[hover]) pick(options[hover].id);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className="relative min-w-0">
      <button
        ref={btnRef}
        type="button"
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="kd-input flex items-center min-w-0 text-left"
        style={{ paddingInline: 0, cursor: "pointer", borderColor: open ? "var(--fg-3)" : undefined }}
      >
        {lead}
        <span
          className="flex-1 min-w-0 truncate"
          style={{ color: "var(--fg-1)", paddingInline: lead ? 10 : 12 }}
        >
          {current?.name ?? "—"}
        </span>
        <ChevronDown
          size={15}
          aria-hidden
          className="shrink-0"
          style={{
            color: "var(--fg-4)",
            marginRight: 10,
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 150ms ease",
          }}
        />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={label}
          onMouseLeave={() => setHover(-1)}
          className="absolute left-0 z-30 kd-card"
          style={{ top: "calc(100% + 4px)", width: "100%", minWidth: 140, paddingBlock: 6 }}
        >
          {options.map((o, i) => {
            const selected = o.id === value;
            return (
              <div
                key={o.id}
                role="option"
                aria-selected={selected}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o.id)}
                onMouseEnter={() => setHover(i)}
                className="kd-t-label flex items-center truncate"
                style={{
                  height: "var(--row-md)",
                  paddingInline: 14,
                  color: "var(--fg-1)",
                  cursor: "pointer",
                  background: hover === i ? "var(--sel)" : selected ? "var(--sel-soft)" : "transparent",
                }}
              >
                {o.name}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// 입력 칸 — 포트처럼 직접 치는 값.
function InputField({ label, value, onChange, placeholder }) {
  return (
    <div className="kd-input flex items-center min-w-0" style={{ paddingInline: 0 }}>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
        inputMode="numeric"
        placeholder={placeholder}
        className="flex-1 min-w-0"
        style={{
          background: "transparent",
          border: 0,
          outline: "none",
          font: "inherit",
          color: "var(--fg-1)",
          paddingInline: 12,
        }}
      />
    </div>
  );
}

// 01 — 저장소·브랜치. 시안 두 칸 비율 325:261 ≈ 1.25:1
function RepoCard({ repo, onRepo, branch, onBranch }) {
  return (
    <div className="kd-card" style={{ padding: "16px 18px 18px" }}>
      <CardHead title="소스 코드 (GitHub)" body="배포할 저장소와 브랜치를 선택하세요." />
      {/* 폰 폭에서는 두 칸이 서로 글자를 잘라먹는다 — 한 줄에 하나씩 쌓는다 */}
      <div
        className="grid gap-3 grid-cols-1 sm:[grid-template-columns:1.25fr_1fr]"
        style={{ marginTop: 12 }}
      >
        <PickField
          lead={
            <span
              className="flex items-center justify-center shrink-0 self-stretch"
              style={{ width: 38, borderRight: "1px solid var(--kd-border)", color: "var(--fg-1)" }}
            >
              <GithubMark size={18} />
            </span>
          }
          label="저장소"
          value={repo}
          onChange={onRepo}
          options={REPOS.map((r) => ({ id: r, name: r }))}
        />
        <PickField
          lead={
            <GitBranch
              size={18}
              aria-hidden
              className="shrink-0"
              style={{ color: "var(--fg-1)", marginLeft: 12 }}
            />
          }
          label="브랜치"
          value={branch}
          onChange={onBranch}
          options={BRANCHES.map((b) => ({ id: b, name: b }))}
        />
      </div>
    </div>
  );
}

// 02 — 런타임·포트·DB 3단. 시안 세 칸 등폭(각 ~187/651), 포트만 직접 치는 칸이라 쉐브론 없음
function RuntimeCard({ runtime, onRuntime, port, onPort, db, onDb }) {
  return (
    <div className="kd-card" style={{ padding: "16px 18px 18px" }}>
      <CardHead title="실행 환경" body="앱에 맞는 실행 환경을 설정하세요." />
      <div className="grid gap-3 grid-cols-1 sm:grid-cols-3" style={{ marginTop: 14 }}>
        <LabeledField label="런타임">
          <PickField
            label="런타임"
            value={runtime}
            onChange={onRuntime}
            options={RUNTIME_IDS.map((id) => ({ id, name: RUNTIME_META[id].name }))}
          />
        </LabeledField>
        <LabeledField label="포트">
          <InputField label="포트" value={port} onChange={onPort} placeholder="8000" />
        </LabeledField>
        <LabeledField label="데이터베이스 (선택)">
          <PickField label="데이터베이스" value={db} onChange={onDb} options={DB_OPTIONS} />
        </LabeledField>
      </div>
    </div>
  );
}

function LabeledField({ label, children }) {
  return (
    <div className="min-w-0">
      <div className="kd-t-label truncate" style={{ color: "var(--fg-1)", marginBottom: 6 }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// 03 — 실행 상태 + 빌드 로그. 시안에서 로그 블록만 카드 여백(22)보다 좁게(14) 들어가 거의 꽉 찬다.
function ResultCard({ repo, port }) {
  return (
    <div className="kd-card" style={{ padding: 10 }}>
      <div className="flex items-center gap-3 flex-wrap" style={{ padding: "6px 8px 10px" }}>
        <span className="kd-t-subtitle" style={{ color: "var(--fg-1)" }}>
          {appNameOf(repo)}
        </span>
        <span className="kd-t-label inline-flex items-center gap-1.5" style={{ color: "var(--ok-fg)" }}>
          <CheckDot />
          실행 중
        </span>
        {/* 앱 주소를 새 탭에서 여는 자리 — 미리보기라 실제 링크는 걸지 않는다 */}
        <span className="kd-btn-secondary kd-btn-sm ml-auto inline-flex items-center gap-1.5">
          서비스 열기
          <ArrowUpRight size={15} aria-hidden />
        </span>
      </div>

      <div
        className="kd-t-code relative overflow-hidden"
        style={{
          background: "var(--term-bg)",
          color: "var(--term-fg)",
          borderRadius: 4,
          padding: "10px 12px",
          lineHeight: 1.55,
        }}
      >
        {logLines(port).map((line) => (
          <div key={line} className="truncate">
            <span style={{ opacity: 0.5 }}>{"> "}</span>
            {line}
          </div>
        ))}
        {/* 시안의 스크롤 막대 — 로그가 더 있다는 표시(장식) */}
        <span
          aria-hidden
          className="absolute"
          style={{
            right: 5,
            top: 8,
            bottom: 8,
            width: 4,
            borderRadius: 2,
            background: "var(--term-fg)",
            opacity: 0.22,
          }}
        />
      </div>
    </div>
  );
}

// 채워진 원 + 체크 — 랜딩 예시 카드(Home.jsx)와 같은 초록 상태 표시. 인라인 아이콘이라 15(--ico-sm)
function CheckDot({ size = 15 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true" className="shrink-0">
      <circle cx="10" cy="10" r="9" fill="currentColor" />
      <path
        d="M6 10.2 L8.7 12.9 L14 7.4"
        fill="none"
        stroke="var(--kd-surface)"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// GitHub mark — lucide-react 1.16.0에 브랜드 아이콘이 없다(LoginModal.jsx와 같은 path).
function GithubMark({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
