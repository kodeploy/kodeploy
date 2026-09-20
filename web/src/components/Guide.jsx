// 문서 화면 ("/guide", "/guide/:section") — design/라이트모드-시안/20_문서_터미널_기본.png 기준의 3단 문서 셸.
//
// 구성: 좌 레일(검색 + 그룹별 문서 목록 · 세로 괘선) / 가운데 본문(브레드크럼 → 제목 → 리드문 → 본문)
//       / 우 목차(이 페이지에서) · 바닥 괘선 + 이전/다음 문서.
//
// 치수 주석의 숫자는 시안 원본 px(1536폭 렌더)이고 실제 값은 ÷1.217한 CSS px이다
//   (상단바 괘선이 y=73 → 코드의 TopBar 높이 60px 기준 스케일 1.217).
// 글자 크기는 시안 실측이 아니라 index.css의 타입 램프를 따른다.
//
// 문서 목록(GUIDES)을 시작하기 / 작업 공간 / 설정으로 묶는다.
// 우측 목차는 본문 컴포넌트가 렌더한 h2/h3에서 만든다.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { Search } from "lucide-react";
import Basics from "./guide/Basics.jsx";
import Java from "./guide/Java.jsx";
import JavaScript from "./guide/JavaScript.jsx";
import Php from "./guide/Php.jsx";
import Python from "./guide/Python.jsx";
import Static from "./guide/Static.jsx";
import Storage from "./guide/Storage.jsx";
import CustomDomain from "./guide/CustomDomain.jsx";
import Troubleshooting from "./guide/Troubleshooting.jsx";
import { DocScale } from "./guide/atoms.jsx";
import GettingStarted from "./guide/GettingStarted.jsx";
import Database from "./guide/Database.jsx";
import Workspace from "./guide/Workspace.jsx";
import Environment from "./guide/Environment.jsx";

// 가이드 목록 — 미래 가이드 추가 시 항목만 추가. 첫 탭 path가 사이드바 링크.
const GUIDES = [
  {
    id: "getting-started",
    label: "첫 배포 시작하기",
    title: "첫 배포 시작하기",
    desc: "GitHub 저장소 연결부터 앱 주소 확인까지, 첫 배포 순서를 안내해요.",
    tabs: [{ id: "getting-started", label: "개요", path: "/guide", Component: GettingStarted }],
  },
  {
    id: "dockerfile",
    label: "서버 앱 배포",
    title: "서버 앱 배포",
    desc: "자동 빌드와 Dockerfile 사용법, 런타임별 실행 설정을 확인하세요.",
    tabs: [
      { id: "basics", label: "기본 규칙", path: "/guide/basics", Component: Basics },
      { id: "python", label: "Python", path: "/guide/python", Component: Python },
      { id: "java", label: "Java", path: "/guide/java", Component: Java },
      { id: "php", label: "PHP", path: "/guide/php", Component: Php },
      { id: "javascript", label: "JavaScript", path: "/guide/javascript", Component: JavaScript },
    ],
  },
  {
    id: "static",
    label: "정적 사이트 배포",
    title: "정적 사이트 배포",
    desc: "React · Vue · 순수 HTML을 서버 없이 호스팅해요. 빌드 커맨드와 출력 디렉토리만 알면 됩니다.",
    tabs: [
      { id: "static", label: "개요", path: "/guide/static", Component: Static },
    ],
  },
  {
    id: "workspace",
    label: "터미널과 로그",
    title: "터미널과 로그",
    desc: "실행 중인 앱을 확인하고, 빌드 로그와 실행 로그로 문제를 살펴보세요.",
    tabs: [{ id: "workspace", label: "개요", path: "/guide/workspace", Component: Workspace }],
  },
  {
    id: "database",
    label: "데이터베이스와 Redis",
    title: "데이터베이스와 Redis",
    desc: "DB 연결, SQL 실행, 데이터 보존과 Redis 캐시 사용법을 안내해요.",
    tabs: [{ id: "database", label: "개요", path: "/guide/database", Component: Database }],
  },
  {
    id: "environment",
    label: "환경변수",
    title: "환경변수",
    desc: "서버 실행 설정과 프론트엔드 빌드에 필요한 값을 관리하세요.",
    tabs: [{ id: "environment", label: "개요", path: "/guide/environment", Component: Environment }],
  },
  {
    id: "storage",
    label: "저장소",
    title: "저장소",
    desc: "재배포에도 데이터를 남기는 두 가지 방법 - 로컬 디스크(PVC)와 오브젝트 스토리지(R2)예요.",
    tabs: [
      { id: "storage", label: "개요", path: "/guide/storage", Component: Storage },
    ],
  },
  {
    id: "custom-domain",
    label: "커스텀 도메인 연결",
    title: "커스텀 도메인 연결",
    desc: "내가 가진 도메인을 앱에 연결하는 법과 CNAME 설정을 안내해요.",
    tabs: [
      { id: "custom-domain", label: "개요", path: "/guide/custom-domain", Component: CustomDomain },
    ],
  },
  {
    id: "troubleshooting",
    label: "문제 해결",
    title: "문제 해결",
    desc: "빌드 실패 · Pod 시작 실패 · 재시작 반복 - 자주 만나는 실패의 원인과 확인 순서예요.",
    tabs: [
      { id: "troubleshooting", label: "개요", path: "/guide/troubleshooting", Component: Troubleshooting },
    ],
  },
];

// 좌측 레일과 이전/다음 문서가 공유하는 읽기 순서.
const GROUPS = [
  { label: "시작하기", ids: ["getting-started", "dockerfile", "static"] },
  { label: "작업 공간", ids: ["workspace", "database", "storage", "troubleshooting"] },
  { label: "설정", ids: ["environment", "custom-domain"] },
];

// 모든 탭을 평탄화해 URL section → 탭/가이드 역매핑.
const ALL_TABS = GUIDES.flatMap((g) => g.tabs.map((t) => ({ ...t, guideId: g.id })));
const DEFAULT_TAB = ALL_TABS[0];

// 레일에 보이는 순서 그대로 평탄화한 문서 목록 = 바닥 이전/다음 문서의 순서 원본.
// 탭이 하나뿐인 가이드는 탭 라벨("개요") 대신 가이드 라벨을 쓴다.
const DOC_ORDER = GROUPS.flatMap((group) =>
  group.ids.flatMap((id) => {
    const guide = GUIDES.find((g) => g.id === id);
    const multi = guide.tabs.length > 1;
    return guide.tabs.map((t) => ({
      id: t.id,
      path: t.path,
      label: multi ? t.label : guide.label,
      group: group.label,
      guide,
    }));
  }),
);

// 문서는 단독 화면(/guide)과 대시보드 안(/dashboard/guide) 두 곳에서 같은 컴포넌트로 뜬다.
// 링크는 지금 서 있는 쪽을 따라가야 화면 밖으로 튕기지 않는다.
function useDocHref() {
  const { pathname } = useLocation();
  const base = pathname.startsWith("/dashboard") ? "/dashboard/guide" : "/guide";
  return (p) => base + p.slice("/guide".length);
}

export default function Guide() {
  const { section } = useParams();
  const activeTab = ALL_TABS.find((t) => t.id === section) || DEFAULT_TAB;
  const activeGuide = GUIDES.find((g) => g.id === activeTab.guideId);
  const Body = activeTab.Component;
  const multi = activeGuide.tabs.length > 1;

  const order = DOC_ORDER.findIndex((d) => d.id === activeTab.id);
  const current = DOC_ORDER[order];
  const prev = order > 0 ? DOC_ORDER[order - 1] : null;
  const next = order >= 0 && order < DOC_ORDER.length - 1 ? DOC_ORDER[order + 1] : null;

  const [query, setQuery] = useState("");
  const bodyRef = useRef(null);
  const [toc, setToc] = useState([]);
  const [activeHeading, setActiveHeading] = useState(null);

  // 우측 목차 — 본문이 렌더한 h2/h3를 훑어서 만든다. 문서를 바꾸면 다시 훑는다.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const headings = Array.from(root.querySelectorAll("h2, h3"));
    setToc(
      headings.map((el, i) => {
        // 한글 제목을 슬러그로 만들면 URL에 인코딩이 끼어 읽기 나쁘다 - 순번 id면 충분하다.
        if (!el.id) el.id = `doc-sec-${i}`;
        return { id: el.id, text: el.textContent, sub: el.tagName === "H3" };
      }),
    );
    setActiveHeading(headings[0]?.id ?? null);
  }, [activeTab.id]);

  // 문서를 바꾸면 스크롤을 위로. 스크롤 컨테이너는 App.jsx의 overflow-auto div다.
  useEffect(() => {
    bodyRef.current?.closest(".scroll-thin")?.scrollTo({ top: 0 });
  }, [activeTab.id]);

  // 스크롤 위치에 따라 목차에서 현재 섹션을 강조.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root || toc.length === 0) return;
    const scroller = root.closest(".scroll-thin");
    if (!scroller) return;
    const onScroll = () => {
      const headings = Array.from(root.querySelectorAll("h2, h3"));
      let cur = headings[0]?.id ?? null;
      for (const el of headings) {
        // 상단바(60) 조금 아래를 지나간 마지막 제목이 현재 섹션.
        if (el.getBoundingClientRect().top <= 140) cur = el.id;
      }
      setActiveHeading(cur);
    };
    onScroll();
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [toc]);

  // 검색 API가 없다 — GUIDES의 라벨만 클라이언트에서 거른다.
  const q = query.trim().toLowerCase();
  const rail = useMemo(() => buildRail(q, activeGuide.id), [q, activeGuide.id]);

  const jumpTo = (id) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="kd-fade-in">
      {/* 시안 콘텐츠 x33-1502(폭 1469 → CSS 1207) = --kd-w-docs(1280) - 좌우 36 */}
      <div
        className="mx-auto flex"
        style={{
          width: "100%",
          maxWidth: "var(--kd-w-docs)",
          paddingInline: "clamp(16px, 3vw, 36px)",
          minHeight: "100%",
        }}
      >
        {/* ── 좌측 레일: 시안 x33-343(폭 310 → 255), 오른쪽 세로 괘선 x343 ── */}
        <aside
          className="hidden lg:block shrink-0"
          style={{ width: 255, borderRight: "1px solid var(--kd-border)" }}
        >
          <div
            className="sticky scroll-thin"
            style={{
              top: 0,
              maxHeight: "calc(100vh - 60px)",
              overflowY: "auto",
              // 시안: 괘선(74) 아래 43 → 검색 입력 상단 117
              paddingTop: 40,
              paddingBottom: 40,
              paddingLeft: 25,
              paddingRight: 24,
            }}
          >
            {/* 검색 입력 — 시안 254x47(→ 209x38 = .kd-input) */}
            <div className="relative">
              <Search
                size={15}
                aria-hidden
                className="absolute pointer-events-none"
                style={{ left: 12, top: "50%", marginTop: -7.5, color: "var(--fg-4)" }}
              />
              <input
                type="search"
                className="kd-input"
                style={{ paddingLeft: 34 }}
                placeholder="문서 검색"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="문서 검색"
              />
            </div>

            {rail.length === 0 && (
              <p className="kd-t-caption" style={{ color: "var(--fg-3)", marginTop: 28 }}>
                일치하는 문서가 없어요.
              </p>
            )}

            {rail.map((group) => (
              <div key={group.label} style={{ marginTop: 28 }}>
                {/* 그룹 제목 — 시안: 항목보다 왼쪽(x65)에 붙고 잉크색 */}
                <div
                  className="kd-t-label kd-strong"
                  style={{ color: "var(--fg-1)", marginBottom: 6 }}
                >
                  {group.label}
                </div>
                {group.rows.map((row) => (
                  <RailRow
                    key={row.key}
                    to={row.path}
                    label={row.label}
                    sub={row.sub}
                    active={row.id === activeTab.id || (row.isGuideRow && row.guideActive && !row.hasOpenTabs)}
                    open={row.isGuideRow && row.guideActive}
                  />
                ))}
              </div>
            ))}
          </div>
        </aside>

        {/* ── 가운데 본문: 시안 x385-1215(폭 831 → 683) ── */}
        <div
          className="flex-1 min-w-0 flex flex-col"
          style={{ paddingLeft: 34, paddingRight: 36, paddingTop: 40, paddingBottom: 56 }}
        >
          {/* 브레드크럼 — 시안 y123 잉크 */}
          <nav className="kd-t-label flex items-center gap-2" style={{ color: "var(--fg-3)" }}>
            <span>{current?.group ?? GROUPS[0].label}</span>
            <span aria-hidden style={{ color: "var(--fg-4)" }}>
              /
            </span>
            <span style={{ color: "var(--fg-1)" }}>{activeGuide.label}</span>
            {multi && (
              <>
                <span aria-hidden style={{ color: "var(--fg-4)" }}>
                  /
                </span>
                <span style={{ color: "var(--fg-1)" }}>{activeTab.label}</span>
              </>
            )}
          </nav>

          {/* 제목 — 시안 잉크 57 */}
          <h1
            className="kd-t-display"
            style={{ color: "var(--fg-1)", marginTop: 16 }}
          >
            {multi ? activeTab.label : activeGuide.title}
          </h1>
          <p className="kd-t-lead" style={{ color: "var(--fg-2)", marginTop: 10 }}>
            {activeGuide.desc}
          </p>

          {/* 본문 — 섹션 제목 28(kd-t-title) + 본문 16(kd-t-body)은 atoms.jsx의 DocScale이 켠다 */}
          <div ref={bodyRef} style={{ marginTop: 40 }}>
            <DocScale>
              <Body />
            </DocScale>
          </div>

          {/* 바닥 — 시안 y940 괘선 + 이전/다음 문서 */}
          <div
            className="flex items-center justify-between gap-4"
            style={{
              marginTop: "auto",
              paddingTop: 20,
              borderTop: "1px solid var(--kd-border)",
            }}
          >
            {prev ? (
              <DocNavLink doc={prev} />
            ) : (
              <span aria-hidden />
            )}
            {next ? <DocNavLink doc={next} /> : <span aria-hidden />}
          </div>
        </div>

        {/* ── 우측 목차: 시안 x1257-1502(폭 245 → 201) ── */}
        <aside className="hidden xl:block shrink-0" style={{ width: 200 }}>
          <div
            className="sticky scroll-thin"
            style={{
              top: 0,
              maxHeight: "calc(100vh - 60px)",
              overflowY: "auto",
              paddingTop: 40,
              paddingBottom: 40,
              paddingLeft: 24,
            }}
          >
            <div className="kd-t-label kd-strong" style={{ color: "var(--fg-1)" }}>
              이 페이지에서
            </div>
            <nav style={{ marginTop: 8 }}>
              {toc.map((h) => {
                const on = h.id === activeHeading;
                return (
                  <a
                    key={h.id}
                    href={`#${h.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      jumpTo(h.id);
                    }}
                    className="kd-t-body-s flex items-center no-underline transition-colors"
                    style={{
                      minHeight: "var(--row-sm)",
                      paddingBlock: 4,
                      paddingLeft: h.sub ? 12 : 0,
                      color: on ? "var(--fg-1)" : "var(--fg-3)",
                      fontWeight: on ? 600 : 400,
                    }}
                  >
                    {h.text}
                  </a>
                );
              })}
            </nav>
          </div>
        </aside>
      </div>
    </div>
  );
}

// 레일 한 줄 — 활성 항목은 옅은 채움 + 왼쪽 잉크 바(시안 x63, 폭 4 → 3).
function RailRow({ to, label, sub, active, open }) {
  const href = useDocHref();
  return (
    <Link
      to={href(to)}
      aria-current={active ? "page" : undefined}
      className="kd-t-body-s kd-pick flex items-center no-underline"
      style={{
        height: "var(--row-sm)",
        paddingLeft: sub ? 28 : 15,
        paddingRight: 10,
        color: open ? "var(--fg-1)" : "var(--fg-2)",
      }}
    >
      <span className="kd-pick-name truncate">{label}</span>
    </Link>
  );
}

// 바닥 이전/다음 문서 — 밑줄 친 문서 이름만. 방향은 좌/우 배치로 읽는다.
function DocNavLink({ doc }) {
  const href = useDocHref();
  return (
    <Link
      to={href(doc.path)}
      className="kd-t-body-s flex items-center gap-2 no-underline"
      style={{ color: "var(--fg-1)" }}
    >
      <span style={{ textDecoration: "underline", textUnderlineOffset: 3 }}>{doc.label}</span>
    </Link>
  );
}

// 레일에 그릴 줄들을 만든다.
// - 탭이 여러 개인 가이드는 가이드 줄 + (열렸을 때) 탭 줄들. 열림 조건은 "지금 보고 있는 가이드"이거나
//   검색어가 탭 라벨에 걸렸을 때.
// - 검색어가 있으면 라벨이 걸리는 문서만 남긴다(검색 API가 없어 클라이언트 필터).
function buildRail(q, activeGuideId) {
  const hit = (s) => !q || s.toLowerCase().includes(q);
  const out = [];
  for (const group of GROUPS) {
    const rows = [];
    for (const id of group.ids) {
      const guide = GUIDES.find((g) => g.id === id);
      const guideActive = guide.id === activeGuideId;
      if (guide.tabs.length === 1) {
        if (!hit(guide.label)) continue;
        rows.push({
          key: guide.id,
          id: guide.tabs[0].id,
          path: guide.tabs[0].path,
          label: guide.label,
        });
        continue;
      }
      const guideHit = hit(guide.label);
      const tabHits = guide.tabs.filter((t) => hit(t.label));
      if (!guideHit && tabHits.length === 0) continue;
      const openTabs = q ? (guideHit ? guide.tabs : tabHits) : guideActive ? guide.tabs : [];
      rows.push({
        key: guide.id,
        id: null,
        path: guide.tabs[0].path,
        label: guide.label,
        isGuideRow: true,
        guideActive,
        hasOpenTabs: openTabs.length > 0,
      });
      for (const t of openTabs) {
        rows.push({ key: `${guide.id}-${t.id}`, id: t.id, path: t.path, label: t.label, sub: true });
      }
    }
    if (rows.length) out.push({ label: group.label, rows });
  }
  return out;
}
