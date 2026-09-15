// 랜딩 페이지 ("/") — design/라이트모드-시안/01_랜딩.png(v2) 기준.
//
// 구성: Hero(명조 헤드라인 + CTA) + 배포 흐름 일러스트 → 운영 섹션(예시 워크스페이스 + 3열)
//       → 자주 묻는 질문 → 푸터. 섹션 사이 구분선은 콘텐츠 폭이 아니라 화면 전체를 가로지른다.
// 치수 주석의 숫자는 시안 원본 px(1024폭 렌더)이고 실제 값은 ÷1.082한 CSS px이다.
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  BarChart3,
  ChevronDown,
  Database,
  FileText,
  Folder,
  Globe,
  Image as ImageIcon,
  Maximize2,
  Minimize2,
  RefreshCw,
  Settings,
} from "lucide-react";
import { useAuth } from "../contexts/AuthContext.jsx";
import { listBuilds } from "../api/deploy.js";
import SiteFooter from "./marketing/SiteFooter.jsx";
import artRepo from "../assets/flow-repo.png";
import artArrow from "../assets/flow-arrow.png";
import artArrow2 from "../assets/flow-arrow2.png";
import artCube from "../assets/flow-cube.png";
import artApp from "../assets/flow-app.png";

// 운영 섹션 하단 3열
const OPS = [
  {
    icon: Database,
    title: "필요한 데이터 연결",
    lead: "MySQL · PostgreSQL · Redis",
    body: "몇 번의 클릭으로 데이터베이스를 연결하고 바로 사용할 수 있습니다.",
  },
  {
    icon: Globe,
    title: "서비스 주소와 HTTPS",
    lead: "도메인 연결과 HTTPS 설정",
    body: "커스텀 도메인을 연결하고 HTTPS로 안전하게 서비스하세요.",
  },
  {
    icon: Settings,
    title: "앱에 필요한 설정",
    lead: "환경변수 · 영구 저장소",
    body: "환경변수와 저장소를 설정해 안정적으로 운영할 수 있습니다.",
  },
];

// 질문은 시안 그대로, 답변은 실제 동작 기준으로 짧게.
const FAQ = [
  {
    q: "Dockerfile 없이도 시작할 수 있나요?",
    a: "네. 저장소의 언어와 설정을 자동으로 감지해 빌드합니다. 직접 작성한 Dockerfile이 있다면 그쪽을 쓰도록 지정할 수도 있습니다.",
  },
  {
    q: "어떤 데이터베이스를 사용할 수 있나요?",
    a: "MySQL과 PostgreSQL 중에 고를 수 있고, 캐시가 필요하면 Redis를 함께 붙일 수 있습니다. 접속 정보는 환경변수로 앱에 자동 주입됩니다.",
  },
  {
    q: "파일은 재배포해도 남나요?",
    a: "오브젝트 스토리지나 영구 저장소를 켜두면 남습니다. 둘 다 쓰지 않으면 앱 컨테이너 안의 파일은 재배포할 때 사라집니다.",
  },
];

// 모션 최소화 설정을 읽는다. 첫 렌더에서 알아야 "한 프레임 깜빡"이 없다(useEffect는 페인트 뒤에 돈다).
const prefersReduced = () =>
  typeof window !== "undefined" &&
  !!window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

// 뷰포트에 들어오면 한 번 표시 토글. prefers-reduced-motion이면 처음부터 보이고 전환도 걸지 않는다.
function useReveal() {
  const ref = useRef(null);
  const reduced = prefersReduced();
  const [shown, setShown] = useState(reduced);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reduced) {
      setShown(true);
      return;
    }
    const io = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) {
          setShown(true);
          io.disconnect();
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [reduced]);
  return [ref, shown, reduced];
}

// 스크롤 등장 래퍼 — 자식을 fade + slide-up. 종이 톤이라 이동량은 작게.
function Reveal({ children, className, style }) {
  const [ref, shown, reduced] = useReveal();
  // 등장이 끝나면 will-change를 내려 컴포지터 레이어에서 해제되게 한다.
  const [settled, setSettled] = useState(false);
  return (
    <div
      ref={ref}
      className={className}
      onTransitionEnd={() => setSettled(true)}
      style={{
        ...style,
        opacity: shown ? 1 : 0,
        transform: shown ? "none" : "translateY(14px)",
        // 모션을 끈 사용자에게는 전환을 아예 걸지 않는다(0→1 값 변화가 그대로 재생되던 문제)
        transition: reduced
          ? "none"
          : "opacity 0.7s ease, transform 0.7s cubic-bezier(0.22, 1, 0.36, 1)",
        willChange: settled ? "auto" : "opacity, transform",
      }}
    >
      {children}
    </div>
  );
}

// 콘텐츠 폭 래퍼 — .kd-page-narrow (시안 기준 콘텐츠 855px / 좌우 여백 52px).
function Container({ children, style }) {
  return (
    <div className="kd-page-narrow" style={style}>
      {children}
    </div>
  );
}

// 섹션 구분선 — 시안에서 화면 전체를 가로지른다(콘텐츠 폭이 아님).
function Rule() {
  return <div style={{ borderTop: "1px solid var(--kd-rule)" }} />;
}

export default function Home() {
  const navigate = useNavigate();
  const { user, openLogin } = useAuth();
  // 이미 배포한 앱이 있으면 히어로 버튼도 "내 앱 열기"가 맞다 —
  // 로그인한 사용자가 랜딩에 오는 이유는 대부분 자기 앱으로 들어가려는 것이다.
  const [hasApp, setHasApp] = useState(false);

  useEffect(() => {
    if (!user) {
      setHasApp(false);
      return;
    }
    let alive = true;
    listBuilds()
      .then((builds) => alive && setHasApp(Array.isArray(builds) && builds.length > 0))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [user]);

  const handleStart = () => {
    if (!user) return openLogin?.();
    navigate(hasApp ? "/apps" : "/deploy");
  };

  return (
    <div className="flex-1 overflow-auto scroll-thin">
      {/* ── Hero — 좌 문장 / 우 배포 흐름 (한 화면) ── */}
      <div className="kd-page-narrow">
        <section className="kd-fade-in kd-hero-grid">
          <div className="min-w-0">
            {/* 시안 잉크 62px → 57 ÷ 0.76 ≈ 75px */}
            <h1 className="kd-t-hero text-fg-strong">
              만든 서비스,
              <br />
              배포와 운영을
              <br />
              가볍게.
            </h1>

            <p
              className="kd-t-lead mt-5 text-fg-2"
              style={{ maxWidth: 520, wordBreak: "keep-all" }}
            >
              GitHub 저장소를 연결하면, 빌드부터 앱 서버 실행까지.
              <br />
              터미널과 로그, 데이터베이스도 한곳에서 관리하세요.
            </p>

            <div className="mt-8 flex items-center gap-7 flex-wrap">
              <button
                onClick={handleStart}
                className="kd-btn-primary kd-btn-lg inline-flex items-center justify-center"
              >
                {hasApp ? "대시보드 열기" : "프로젝트 배포하기"}
              </button>
              <Link
                to="/how"
                className="kd-t-body text-fg-2 hover:text-fg-1 transition-colors underline"
                style={{ textUnderlineOffset: 4 }}
              >
                배포 과정 보기
              </Link>
            </div>
          </div>

          <DeployFlow />
        </section>
      </div>

      <Rule />

      {/* ── 운영 ── */}
      <Container>
        <section style={{ paddingBlock: "36px 0" }}>
          <Reveal>
            {/* 시안 잉크 41px → 38 ÷ 0.76 ≈ 50px */}
            <h2
              className="kd-t-display text-fg-1"
            >
              배포한 다음도, 어렵지 않게.
            </h2>
            <p className="kd-t-body mt-2 text-fg-2" style={{ wordBreak: "keep-all" }}>
              실행 상태를 확인하고, 필요한 작업을 바로 이어가세요.
            </p>
          </Reveal>

          <Reveal style={{ marginTop: 16 }}>
            <WorkspaceExample />
            <div className="kd-t-caption mt-2.5 text-fg-4">예시 화면</div>
          </Reveal>

          {/* 3열 — 셀 사이는 세로 괘선으로만 나눈다 */}
          <Reveal className="kd-ops-grid" style={{ marginTop: 34 }}>
            {OPS.map((o, i) => {
              const Icon = o.icon;
              return (
                <div key={o.title} className={i > 0 ? "kd-ops-cell" : undefined}>
                  {/* 시안은 아이콘이 제목 왼쪽에 붙고, 설명은 제목 기준선에 맞춰 들어간다 */}
                  <div className="flex items-center gap-3">
                    <Icon size={32} strokeWidth={1.2} className="text-fg-1 shrink-0" />
                    <h3 className="kd-t-section text-fg-1">{o.title}</h3>
                  </div>
                  <div style={{ paddingLeft: "calc(var(--ico-lg) + 12px)" }}>
                    <p className="kd-t-body-s mt-1.5 text-fg-2">{o.lead}</p>
                    <p
                      className="kd-t-body-s mt-3.5 text-fg-3"
                      style={{ wordBreak: "keep-all" }}
                    >
                      {o.body}
                    </p>
                  </div>
                </div>
              );
            })}
          </Reveal>
        </section>
      </Container>

      <div style={{ height: 40 }} />
      <Rule />

      {/* ── 자주 묻는 질문 — 좌 제목 / 우 아코디언 ── */}
      <Container>
        <section style={{ paddingBlock: "30px 30px" }}>
          <Reveal className="kd-faq-grid">
            <h2
              className="kd-t-display text-fg-1"
            >
              자주 묻는 질문
            </h2>
            <div style={{ borderTop: "1px solid var(--kd-rule)" }}>
              {FAQ.map((item) => (
                <FaqRow key={item.q} {...item} />
              ))}
            </div>
          </Reveal>
        </section>
      </Container>

      <SiteFooter />
    </div>
  );
}

// FAQ 한 줄 — 질문 + 우측 쉐브론. 카드로 감싸지 않고 괘선으로만 끊는다.
function FaqRow({ q, a }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--kd-rule)" }}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-4 text-left"
        style={{ height: 48 }}
      >
        <span className="kd-t-body-s text-fg-1 flex-1" style={{ fontWeight: 500 }}>
          {q}
        </span>
        <ChevronDown
          size={17}
          strokeWidth={1.6}
          className="text-fg-3 shrink-0 transition-transform duration-200"
          style={{ transform: open ? "rotate(180deg)" : "none" }}
        />
      </button>
      {open && (
        <p
          className="kd-t-body-s pb-5 pr-10 text-fg-3 kd-fade-in"
          style={{ wordBreak: "keep-all" }}
        >
          {a}
        </p>
      )}
    </div>
  );
}

// ── Hero 일러스트 ─────────────────────────────────────────────────────────
// design/의 조각 그림(저장소 · 화살표 · 큐브 · 앱 창)을 얹어 흐름을 만든다.
// 조각을 따로 두는 이유는 순서대로 등장시키기 위해서다: 저장소 → 자동 빌드 → 앱 실행.
//
// 좌표는 시안 이미지에서 조각마다 잉크 경계를 재서 얻은 값이다(1761폭 렌더 기준):
//   저장소 x1011 y62 308×278 · 화살표1 x1321 y173 141×97 · 큐브 x1378 y278 133×150
//   화살표2 x1542 y373 54×83 · 창 x1061 y458 659×278 · 주소 x1228 y767
//   라벨 내GitHub저장소 x1218 y48 · 자동빌드 x1408 y167 · 앱실행 x1587 y381
// 콘텐츠 상자는 x1011-1720 / y48-796 = 709×748 (가로세로비 0.948) — 아래 %는 그 환산값이다.
// ⚠️ 상자 비율을 바꾸면 조각이 전부 어긋난다. 조각 이미지의 가로세로비도 시안과 같아야 한다
//    (저장소 1.107 · 화살표1 1.453 · 큐브 0.888 · 창 2.387 — 시안 실측과 1% 이내).
// 시안은 my-api.kodeploy.app 이지만 실제 기본 도메인은 .kodeploy.com 이다(AppLayout과 동일).
const EXAMPLE_HOST = "my-api.kodeploy.com";

// 등장 순서 — 한 단계 안의 그림과 글자는 같이 뜬다.
const FLOW_STEP = { repo: 0, build: 1, cube: 2, run: 3, app: 4 };
const STEP_MS = 300;

function DeployFlow() {
  // 접힘선 위라 스크롤 관측 없이 마운트와 함께 시작한다.
  const reduced = prefersReduced();
  // 첫 렌더부터 최종 상태로 — useEffect에서 올리면 "저장소만 보이는" 프레임이 한 번 그려진다
  const [step, setStep] = useState(reduced ? 99 : 0);

  useEffect(() => {
    if (reduced) return;
    const timers = [1, 2, 3, 4].map((i) =>
      setTimeout(() => setStep(i), STEP_MS * i + 160),
    );
    return () => timers.forEach(clearTimeout);
  }, [reduced]);

  // 단계가 오면 떠오른다(아래에서 살짝 올라오며 페이드).
  const on = (at) => ({
    opacity: step >= at ? 1 : 0,
    transform: step >= at ? "none" : "translateY(10px)",
    transition: reduced
      ? "none"
      : "opacity 420ms ease, transform 420ms cubic-bezier(0.22, 1, 0.36, 1)",
  });

  return (
    <figure
      className="kd-hero-art m-0"
      aria-label="배포 흐름: 내 GitHub 저장소 → 자동 빌드 → 앱 실행"
    >
      <div className="relative w-full" style={{ aspectRatio: "709 / 748" }}>
        {/* 1. 내 GitHub 저장소 */}
        <img
          src={artRepo}
          alt=""
          className="kd-flow-art absolute"
          style={{ left: "0%", top: "1.87%", width: "40.50%", ...on(FLOW_STEP.repo) }}
        />
        <div
          className="kd-flow-label absolute text-fg-1 whitespace-nowrap"
          style={{ left: "27.60%", top: "0%", ...on(FLOW_STEP.repo) }}
        >
          내 GitHub 저장소
        </div>

        {/* 2. 자동 빌드 */}
        <img
          src={artArrow}
          alt=""
          className="kd-flow-art absolute"
          style={{ left: "43.72%", top: "16.71%", width: "19.89%", ...on(FLOW_STEP.build) }}
        />
        <div
          className="kd-flow-label absolute text-fg-1 whitespace-nowrap"
          style={{ left: "59.30%", top: "15.10%", ...on(FLOW_STEP.build) }}
        >
          자동 빌드
        </div>

        {/* 3. 빌드 결과 — 시안 좌표(51.76%)면 왼쪽 화살표에 붙고 오른쪽 화살표와는 4.4% 떠서
            두 화살표 사이에서 왼쪽으로 치우쳐 보인다. 가운데로 2.8% 옮겼다. */}
        <img
          src={artCube}
          alt=""
          className="kd-flow-art absolute"
          style={{ left: "57.00%", top: "30.75%", width: "18.76%", ...on(FLOW_STEP.cube) }}
        />

        {/* 4. 앱 실행 — 시안의 둘째 화살표는 짧고 가파른 갈고리라 다른 조각으로 돌려 쓸 수 없다.
            시안 렌더에서 그 화살표만 떼어 왔다(회전 없음, 시안과 같은 모양).
            시안은 화살촉이 창 윗변에 0.6px까지 붙는데, 확대해 보면 겹쳐 보여 9px 띄웠다. */}
        <img
          src={artArrow2}
          alt=""
          className="kd-flow-art absolute"
          style={{ left: "76.40%", top: "40.60%", width: "7.62%", ...on(FLOW_STEP.run) }}
        />
        <div
          className="kd-flow-label absolute text-fg-1 whitespace-nowrap"
          style={{ left: "84.90%", top: "43.20%", ...on(FLOW_STEP.run) }}
        >
          앱 실행
        </div>

        {/* 5. 실행 중인 앱 — 창 아래 주소와 옅은 타원 그림자 */}
        <div
          className="absolute"
          style={{ left: "6.80%", top: "54.81%", width: "93.20%", ...on(FLOW_STEP.app) }}
        >
          <img src={artApp} alt="배포된 앱이 실행 중인 화면" className="kd-flow-art block w-full" />
          <div className="kd-flow-label text-center text-fg-2" style={{ marginTop: 17 }}>
            {EXAMPLE_HOST}
          </div>
          <div aria-hidden className="kd-art-shadow" />
        </div>
      </div>
    </figure>
  );
}

// ── 예시 워크스페이스 ───────────────────────────────────────────────────────
// 실제 작업 공간(/dashboard/workspace)의 축약본. 값은 전부 예시 문자열이지만
// 크롬(패널 바 · 대상 선택 · 연결 표시 · 세그먼트 토글)은 실제 화면과 같은 모양으로 맞춘다.
// 로그인 전에도 "무엇을 할 수 있는 도구인지"가 보이는 게 이 카드의 목적이다.

const EX_VIEWS = [
  { id: "termlog", label: "터미널 · 로그" },
  { id: "db", label: "데이터베이스" },
  { id: "storage", label: "스토리지" },
  { id: "metrics", label: "모니터링" },
];

const EX_LOGS = [
  ["10:21:03", "Application startup complete"],
  ["10:21:15", "GET /health 200 OK"],
  ["10:23:42", "GET /api/items 200 OK"],
  ["10:24:11", "GET /health 200 OK"],
];

// 예시 터미널 — 직접 쳐 볼 수 있다. 다만 여긴 붙을 앱이 없으므로 명령은 실행되지 않고,
// 첫 입력에 한 번만 그 사실을 알린다(실제 실행은 로그인 후 작업 공간의 터미널에서).
const EX_PROMPT = "app@my-api:~$";
const EX_TERM_START = [
  { kind: "cmd", text: "curl -s localhost:8080/health" },
  { kind: "out", text: '{"status":"ok"}' },
  { kind: "cmd", text: "printenv APP_ENV" },
  { kind: "out", text: "production" },
];
const EX_TERM_NOTE = "예시 화면이라 명령은 실행되지 않아요. 로그인하면 실제 터미널이 열립니다.";

// 스토리지 예시 파일 — 실제 작업 공간이 미리보기하는 종류를 그대로 담는다
// (사진 · 벡터 · 텍스트(JSON) · PDF).
const EX_JSON = `{
  "name": "my-api",
  "runtime": "python",
  "port": 8080,
  "database": "postgres"
}`;

const EX_FILES = [
  { name: "cover.png", size: "512 KB", kind: "image" },
  { name: "logo.svg", size: "2.4 KB", kind: "vector" },
  { name: "config.json", size: "1.2 KB", kind: "text" },
  { name: "report.pdf", size: "128 KB", kind: "pdf" },
];

// 예시 모니터링 — 실제 모니터링 뷰(MetricsView)와 같은 구성의 축약본.
const EX_METRICS = [
  { label: "CPU", value: "8%", sub: "40m / 500m" },
  { label: "메모리", value: "32%", sub: "164 / 512 MiB" },
  { label: "초당 요청", value: "12.4/s", sub: "RPS" },
  { label: "응답 시간", value: "86ms", sub: "p95" },
];

function WorkspaceExample() {
  const [view, setView] = useState("termlog");
  // 뷰를 갈아 끼우면 터미널이 통째로 사라지므로(조건부 렌더), 친 내용은 카드가 들고 있는다
  const term = useExTerminal();
  return (
    <div className="kd-card overflow-hidden">
      {/* 카드 헤더 — 앱 이름 + 상태 / 우측 뷰 전환 */}
      <div
        className="flex items-center gap-3 px-5 flex-wrap"
        style={{ minHeight: 48, borderBottom: "1px solid var(--kd-border)" }}
      >
        <span className="kd-t-subtitle text-fg-1">my-api</span>
        <span className="kd-t-label inline-flex items-center gap-1.5" style={{ color: "var(--ok-fg)" }}>
          <CheckDot />
          실행 중
        </span>
        {/* 좁은 화면에서는 네 칸이 한 줄에 안 들어간다 — 글자를 접지 말고(30px 칸을 넘친다)
            줄을 바꾸게 둔다. 카드 헤더가 그만큼 늘어난다(minHeight만 48). */}
        <div className="ml-auto flex items-center gap-1 flex-wrap justify-end">
          {EX_VIEWS.map((v) => {
            const on = view === v.id;
            return (
              <button
                key={v.id}
                type="button"
                onClick={() => setView(v.id)}
                aria-pressed={on}
                className="kd-t-label transition-colors whitespace-nowrap"
                style={{
                  height: 30,
                  paddingInline: 10,
                  borderRadius: 6,
                  color: on ? "var(--fg-1)" : "var(--fg-3)",
                  fontWeight: on ? 600 : 500,
                  background: on ? "var(--sel-soft)" : "transparent",
                }}
              >
                {v.label}
              </button>
            );
          })}
        </div>
      </div>

      <div style={{ height: 214 }}>
        {view === "termlog" && <ExTerminalLog term={term} />}
        {view === "db" && <ExDatabase />}
        {view === "storage" && <ExStorage />}
        {view === "metrics" && <ExMetrics />}
      </div>
    </div>
  );
}

// 예시 카드 안의 패널 바 — 실제 작업 공간의 PaneBar와 같은 높이·구성.
function ExBar({ dark, children }) {
  return (
    <div
      className="flex items-center gap-2.5 px-3.5 shrink-0"
      style={{
        height: 34,
        borderBottom: dark ? "1px solid rgba(255,255,255,0.10)" : "1px solid var(--kd-border)",
      }}
    >
      {children}
    </div>
  );
}

function ExTerminalLog({ term }) {
  return (
    <div className="flex flex-col sm:flex-row h-full">
      {/* 좌 — 터미널. 실제 화면과 같은 잉크 면 + 대상 선택 + 연결 표시 */}
      <ExTerminal term={term} />

      {/* 우 — 실행 로그 */}
      <div className="sm:w-[45%] flex flex-col min-w-0" style={{ borderLeft: "1px solid var(--kd-border)" }}>
        <ExBar>
          <span className="kd-t-label text-fg-1 shrink-0">로그</span>
          <span className="kd-t-caption ml-auto text-fg-3 shrink-0">전체</span>
          <RefreshCw size={15} strokeWidth={1.7} className="text-fg-3 shrink-0" />
        </ExBar>
        <div className="px-4 py-3 min-w-0">
          {EX_LOGS.map(([t, line]) => (
            <div key={t} className="kd-t-code flex gap-3 min-w-0">
              <span className="text-fg-4 shrink-0 tabular-nums">{t}</span>
              <span className="text-fg-2 truncate">{line}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// 예시 터미널 — 카드가 들고 있는 상태(뷰를 옮겼다 와도 친 내용이 남는다).
function useExTerminal() {
  const [lines, setLines] = useState(EX_TERM_START);
  const [value, setValue] = useState("");
  const [noted, setNoted] = useState(false);
  const history = useRef([]); // 입력했던 명령
  const cursor = useRef(-1);  // 위/아래 탐색 위치 (-1이면 편집 중인 줄)
  return { lines, setLines, value, setValue, noted, setNoted, history, cursor };
}

// 터미널 칸 — 패널 바 + 스크롤백 + 입력 한 줄.
// 셸처럼 굴러야 "쳐 볼 수 있는 화면"으로 읽힌다: 위/아래로 지난 명령, Ctrl+C는 ^C를 남기고
// 줄을 버리고, Ctrl+L은 화면을 지운다. 한글 조합 중의 Enter는 확정이지 실행이 아니다.
function ExTerminal({ term }) {
  const { lines, setLines, value, setValue, noted, setNoted, history, cursor } = term;
  const [focused, setFocused] = useState(false);
  const inputRef = useRef(null);
  const bodyRef = useRef(null);
  const downAt = useRef(null);

  // 줄이 쌓이면 맨 아래를 보여준다
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  const push = (...added) => setLines((prev) => [...prev, ...added]);

  const submit = () => {
    const cmd = value.trim();
    setValue("");
    cursor.current = -1;
    if (!cmd) {
      push({ kind: "cmd", text: "" });
      return;
    }
    history.current = [...history.current, cmd];
    // 안내는 첫 명령에 한 번만 — 매번 붙으면 잔소리가 된다
    push({ kind: "cmd", text: cmd }, ...(noted ? [] : [{ kind: "note", text: EX_TERM_NOTE }]));
    setNoted(true);
  };

  // 위/아래 — 지난 명령 되감기. 끝까지 내려오면 빈 줄로 돌아온다.
  const recall = (dir) => {
    const h = history.current;
    if (!h.length) return;
    let i = cursor.current;
    if (dir < 0) i = i < 0 ? h.length - 1 : Math.max(0, i - 1);
    else {
      if (i < 0) return;
      i += 1;
      if (i >= h.length) {
        cursor.current = -1;
        setValue("");
        return;
      }
    }
    cursor.current = i;
    setValue(h[i]);
  };

  const onKeyDown = (e) => {
    // 한글 조합 중의 Enter는 글자 확정이다 — 여기서 실행하면 한 글자씩 날아간다
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter") {
      e.preventDefault();
      submit();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      recall(-1);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      recall(1);
    } else if (e.ctrlKey && (e.key === "c" || e.key === "C")) {
      e.preventDefault();
      push({ kind: "cmd", text: `${value}^C` });
      setValue("");
      cursor.current = -1;
    } else if (e.ctrlKey && (e.key === "l" || e.key === "L")) {
      e.preventDefault();
      setLines([]);
    }
  };

  return (
    <div
      className="sm:w-[55%] flex flex-col min-w-0 cursor-text"
      style={{ background: "var(--term-bg)", color: "var(--term-fg)" }}
      onPointerDown={(e) => (downAt.current = { x: e.clientX, y: e.clientY })}
      // 칸 안 어디를 눌러도(패널 바까지) 입력으로 들어간다. 단, 끌고 있었으면 가만히 둔다 —
      // 마우스로는 글자를 고르는 중이고(포커스를 주면 그 선택이 풀려 복사를 못 한다),
      // 손가락으로는 스크롤 중이다. click을 쓰는 이유가 그것 — 터치 스크롤로 끝난 제스처는
      // 브라우저가 click을 아예 안 보내고, 탭이면 mouseup 합성 여부와 무관하게 반드시 온다.
      // 손가락은 제자리 탭도 몇 px 흔들리므로 문턱은 마우스보다 넉넉히 둔다.
      onClick={(e) => {
        const d = downAt.current;
        downAt.current = null;
        if (d && Math.hypot(e.clientX - d.x, e.clientY - d.y) > 8) return;
        inputRef.current?.focus();
      }}
    >
      <ExBar dark>
        <span className="kd-t-label shrink-0">터미널</span>
        <span style={{ width: 1, height: 14, background: "rgba(255,255,255,0.18)" }} />
        <span className="kd-t-label inline-flex items-center gap-1 shrink-0" style={{ opacity: 0.8 }}>
          앱 서버
          <ChevronDown size={15} strokeWidth={1.7} />
        </span>
        <span className="kd-t-caption ml-auto shrink-0" style={{ color: "var(--ok-fg)" }}>
          연결됨
        </span>
      </ExBar>

      <div
        ref={bodyRef}
        className="kd-t-code flex-1 min-h-0 px-4 py-3 min-w-0 overflow-y-auto scroll-thin"
      >
        {lines.map((l, i) => (
          <div
            key={i}
            className={l.kind === "note" ? "" : "truncate"}
            style={{
              // 명령 줄 앞은 한 칸 띄운다(첫 줄 제외) — 결과 묶음이 눈에 끊겨 보이게
              marginTop: i > 0 && l.kind !== "out" ? 6 : 0,
              opacity: l.kind === "note" ? 0.55 : 1,
              wordBreak: l.kind === "note" ? "keep-all" : undefined,
            }}
          >
            {l.kind === "cmd" && <span style={{ opacity: 0.6 }}>{EX_PROMPT} </span>}
            {l.text}
          </div>
        ))}

        <div className="flex items-center gap-1.5" style={{ marginTop: lines.length ? 6 : 0 }}>
          <span className="shrink-0" style={{ opacity: 0.6 }}>
            {EX_PROMPT}
          </span>
          <input
            ref={inputRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            aria-label="예시 터미널 입력"
            spellCheck={false}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            className="kd-t-code flex-1 min-w-0 bg-transparent border-0 p-0 outline-none"
            style={{ color: "var(--term-fg)", caretColor: "var(--term-fg)" }}
          />
          {/* 포커스 전에는 예전처럼 네모 커서가 깜빡인다 — 포커스하면 진짜 캐럿이 대신한다 */}
          {!focused && !value && (
            <span
              aria-hidden
              className="inline-block kd-pulse-soft shrink-0"
              style={{ width: 7, height: 14, background: "var(--term-fg)" }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ExDatabase() {
  const [mode, setMode] = useState("표");
  return (
    <div className="flex flex-col h-full">
      <ExBar>
        <Database size={18} strokeWidth={1.5} className="text-fg-1 shrink-0" />
        <span className="kd-t-body-s text-fg-1 truncate">PostgreSQL</span>
        <div
          className="ml-auto flex items-center shrink-0"
          style={{ padding: 2, borderRadius: 8, background: "var(--sel-soft)" }}
        >
          {["표", "터미널"].map((m) => {
            const on = mode === m;
            return (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                aria-pressed={on}
                className="kd-t-label inline-flex items-center transition-colors"
                style={{
                  height: 24,
                  paddingInline: 10,
                  borderRadius: 6,
                  background: on ? "var(--btn-primary-bg)" : "transparent",
                  color: on ? "var(--btn-primary-fg)" : "var(--fg-3)",
                }}
              >
                {m}
              </button>
            );
          })}
        </div>
      </ExBar>

      {mode === "표" ? (
        <>
          <div
            className="kd-t-code px-4 py-2.5 shrink-0"
            style={{ background: "var(--term-bg)", color: "var(--term-fg)" }}
          >
            <div className="truncate">SELECT id, title, status FROM posts ORDER BY id DESC LIMIT 3;</div>
          </div>

          <div className="px-4 py-2.5 min-w-0 overflow-hidden">
            <div
              className="kd-t-micro text-fg-3 flex items-center gap-4"
              style={{ paddingBottom: 6, borderBottom: "1px solid var(--kd-border)" }}
            >
              <span className="shrink-0" style={{ width: 40 }}>id</span>
              <span className="flex-1">title</span>
              <span className="shrink-0">status</span>
            </div>
            {[
              ["105", "Release notes", "published"],
              ["104", "API guide", "published"],
              ["103", "Getting started", "draft"],
            ].map(([id, title, st]) => (
              <div
                key={id}
                className="kd-t-caption flex items-center gap-4 min-w-0"
                style={{ height: 30, borderBottom: "1px solid var(--kd-border)" }}
              >
                <span className="text-fg-3 tabular-nums shrink-0" style={{ width: 40 }}>{id}</span>
                <span className="text-fg-1 truncate flex-1">{title}</span>
                <span className="text-fg-3 shrink-0">{st}</span>
              </div>
            ))}
            <div className="kd-t-micro text-fg-3" style={{ paddingTop: 6 }}>3행 · 12ms</div>
          </div>
        </>
      ) : (
        <div
          className="kd-t-code flex-1 min-h-0 px-4 py-3 overflow-hidden"
          style={{ background: "var(--term-bg)", color: "var(--term-fg)" }}
        >
          <div className="truncate">psql (16.3)</div>
          <div className="truncate" style={{ marginTop: 6 }}>
            <span style={{ opacity: 0.6 }}>my-api=#</span> \dt
          </div>
          <div className="truncate">posts | comments | users</div>
          <div className="truncate" style={{ marginTop: 6 }}>
            <span style={{ opacity: 0.6 }}>my-api=#</span> SELECT count(*) FROM posts;
          </div>
          <div className="truncate">105</div>
          <div className="flex items-center gap-1.5" style={{ marginTop: 6 }}>
            <span style={{ opacity: 0.6 }}>my-api=#</span>
            <span
              className="inline-block kd-pulse-soft"
              style={{ width: 7, height: 14, background: "var(--term-fg)" }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ExStorage() {
  const [big, setBig] = useState(false);
  const [picked, setPicked] = useState("cover.png");
  const file = EX_FILES.find((f) => f.name === picked) || EX_FILES[0];
  const zoomable = file.kind === "image" || file.kind === "vector";

  return (
    <div className="flex flex-col sm:flex-row h-full min-h-0">
      {/* 좌 - 파일 목록 */}
      <div className="sm:w-[52%] flex flex-col min-w-0">
        <ExBar>
          <Folder size={18} strokeWidth={1.5} className="text-fg-1 shrink-0" />
          <span className="kd-t-body-s text-fg-1">파일</span>
          <span className="kd-t-caption text-fg-3">{EX_FILES.length}개</span>
        </ExBar>
        <div className="min-w-0">
          {EX_FILES.map((f) => {
            const on = f.name === picked;
            const Icon = f.kind === "image" || f.kind === "vector" ? ImageIcon : FileText;
            return (
              <button
                key={f.name}
                type="button"
                onClick={() => {
                  setPicked(f.name);
                  setBig(false);
                }}
                aria-pressed={on}
                className="w-full flex items-center gap-3 px-4 text-left kd-hoverable"
                style={{
                  height: 36,
                  background: on ? "var(--sel-soft)" : "transparent",
                  borderBottom: "1px solid var(--kd-border)",
                }}
              >
                <Icon size={15} strokeWidth={1.6} className="text-fg-3 shrink-0" />
                <span className="kd-t-caption text-fg-1 truncate flex-1">{f.name}</span>
                <span className="kd-t-micro text-fg-3 shrink-0 tabular-nums">{f.size}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* 우 - 미리보기. 사진·벡터는 크게 보기, JSON은 내용, PDF는 첫 장 미리보기 */}
      <div
        className="sm:w-[48%] flex flex-col min-w-0"
        style={{ borderLeft: "1px solid var(--kd-border)" }}
      >
        <ExBar>
          <span className="kd-t-label text-fg-1 truncate">{file.name}</span>
          {zoomable && (
            <button
              type="button"
              onClick={() => setBig((v) => !v)}
              className="kd-t-caption ml-auto inline-flex items-center gap-1 text-fg-2 hover:text-fg-1 shrink-0 transition-colors"
            >
              {big ? <Minimize2 size={15} strokeWidth={1.7} /> : <Maximize2 size={15} strokeWidth={1.7} />}
              {big ? "원래 크기" : "크게 보기"}
            </button>
          )}
        </ExBar>
        <div className="flex-1 min-h-0 p-3.5">
          <div
            className="w-full h-full overflow-hidden"
            style={{ borderRadius: 8, background: file.kind === "text" ? "var(--term-bg)" : "var(--sel-soft)" }}
          >
            {file.kind === "image" && (
              <div className="w-full h-full flex items-center justify-center">
                <SamplePhoto contain={big} />
              </div>
            )}
            {file.kind === "vector" && (
              <div
                className="w-full h-full flex items-center justify-center"
                style={{ padding: big ? 0 : 18 }}
              >
                <SampleVector />
              </div>
            )}
            {file.kind === "text" && (
              <pre
                className="kd-t-code w-full h-full overflow-hidden"
                style={{ color: "var(--term-fg)", padding: "10px 12px", margin: 0 }}
              >
                {EX_JSON}
              </pre>
            )}
            {file.kind === "pdf" && <SamplePdf />}
          </div>
        </div>
      </div>
    </div>
  );
}

// 예시 모니터링 - 실제 모니터링 뷰와 같은 구성(좌 지표 목록 / 우 시계열)의 축약본.
function ExMetrics() {
  const [picked, setPicked] = useState("CPU");
  return (
    <div className="flex flex-col sm:flex-row h-full min-h-0">
      <div className="sm:w-[46%] flex flex-col min-w-0" style={{ borderRight: "1px solid var(--kd-border)" }}>
        <ExBar>
          <BarChart3 size={18} strokeWidth={1.5} className="text-fg-1 shrink-0" />
          <span className="kd-t-body-s text-fg-1">지표</span>
          <span className="kd-t-caption text-fg-3 ml-auto">최근 1시간</span>
        </ExBar>
        {EX_METRICS.map((m) => {
          const on = m.label === picked;
          return (
            <button
              key={m.label}
              type="button"
              onClick={() => setPicked(m.label)}
              aria-pressed={on}
              className="w-full flex items-center gap-3 px-4 text-left kd-hoverable"
              style={{
                height: 45,
                background: on ? "var(--sel-soft)" : "transparent",
                borderBottom: "1px solid var(--kd-border)",
              }}
            >
              <span className="kd-t-caption text-fg-1 flex-1 truncate">{m.label}</span>
              <span className="text-right shrink-0">
                <span className="kd-t-caption kd-strong text-fg-1 block tabular-nums">{m.value}</span>
                <span className="kd-t-micro text-fg-3 block tabular-nums">{m.sub}</span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="sm:w-[54%] flex flex-col min-w-0">
        <ExBar>
          <span className="kd-t-label text-fg-1 truncate">{picked}</span>
          <span className="kd-t-caption text-fg-3 ml-auto shrink-0">자동 갱신 30초</span>
        </ExBar>
        <div className="flex-1 min-h-0 px-4 py-3">
          <SampleChart seed={picked} />
        </div>
      </div>
    </div>
  );
}

// 지표별로 모양이 다른 예시 곡선 - 값은 예시지만 축·격자는 실제 차트와 같은 규칙이다.
function SampleChart({ seed }) {
  const n = 48;
  const base = { CPU: 0.42, "메모리": 0.62, "초당 요청": 0.5, "응답 시간": 0.35 }[seed] ?? 0.45;
  const pts = Array.from({ length: n }, (_, i) => {
    const t = i / (n - 1);
    const wave =
      Math.sin(t * 7 + seed.length) * 0.12 + Math.sin(t * 17 + seed.length * 2) * 0.06;
    const y = Math.min(0.95, Math.max(0.06, base + wave));
    return [8 + t * 304, 92 - y * 84];
  });
  return (
    <svg viewBox="0 0 320 100" className="w-full h-full" role="img" aria-label={`${seed} 시계열 예시`}>
      {[8, 50, 92].map((y) => (
        <line key={y} x1="0" y1={y} x2="320" y2={y} stroke="var(--kd-border)" strokeWidth="1" />
      ))}
      <path
        d={pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")}
        fill="none"
        stroke="var(--chart-1)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

// 예시 벡터(.svg) - 미리보기가 사진과 같은 방식으로 동작한다는 걸 보여준다.
function SampleVector() {
  return (
    <svg
      viewBox="0 0 120 120"
      preserveAspectRatio="xMidYMid meet"
      className="w-full h-full"
      role="img"
      aria-label="예시 벡터"
    >
      <circle cx="60" cy="60" r="44" fill="none" stroke="var(--fg-2)" strokeWidth="6" />
      <path d="M38 62 L54 78 L84 44" fill="none" stroke="var(--fg-1)" strokeWidth="8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// 예시 PDF - 첫 장 축소본 모양만 낸다(실제 화면은 브라우저 PDF 뷰어로 띄운다).
function SamplePdf() {
  return (
    <div className="w-full h-full flex items-center justify-center">
      <div
        className="flex flex-col gap-1.5"
        style={{
          width: 88,
          height: "88%",
          padding: 10,
          borderRadius: 4,
          background: "var(--kd-surface)",
          border: "1px solid var(--kd-border)",
        }}
      >
        <span style={{ height: 6, width: "72%", background: "var(--fg-3)", borderRadius: 2 }} />
        {[100, 92, 96, 64].map((w, i) => (
          <span key={i} style={{ height: 3, width: `${w}%`, background: "var(--kd-border)", borderRadius: 2 }} />
        ))}
        <span style={{ marginTop: 4, height: 22, width: "100%", background: "var(--sel-soft)", borderRadius: 3 }} />
      </div>
    </div>
  );
}

// 예시 이미지 — 실제 사진을 싣지 않고 같은 모노크롬 톤의 SVG로 대신한다.
// contain이면 전체가 보이고, 아니면 칸을 꽉 채워 잘린다 — "크게 보기"의 차이를 그대로 보여준다.
function SamplePhoto({ contain }) {
  return (
    <svg
      viewBox="0 0 320 200"
      preserveAspectRatio={contain ? "xMidYMid meet" : "xMidYMid slice"}
      className="w-full h-full"
      role="img"
      aria-label="예시 이미지"
    >
      <defs>
        <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--fg-4)" stopOpacity="0.30" />
          <stop offset="1" stopColor="var(--fg-4)" stopOpacity="0.08" />
        </linearGradient>
      </defs>
      <rect width="320" height="200" fill="url(#sky)" />
      <circle cx="248" cy="52" r="20" fill="var(--fg-4)" opacity="0.45" />
      <path d="M0 200 L78 96 L134 152 L186 78 L262 200 Z" fill="var(--fg-3)" opacity="0.75" />
      <path d="M118 200 L196 112 L320 200 Z" fill="var(--fg-2)" opacity="0.85" />
    </svg>
  );
}

// 채워진 원 + 체크 — 시안의 초록 상태 표시
function CheckDot({ size = 17 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="9" fill="currentColor" />
      <path
        d="M6 10.2 L8.7 12.9 L14 7.4"
        fill="none"
        stroke="var(--kd-surface)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
