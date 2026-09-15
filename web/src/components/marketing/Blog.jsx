// 블로그 목록 ("/blog") — design/라이트모드-시안/22_블로그_목록.png 기준.
//
// 구성: 제목 + 리드문 + 외부 글 안내 → (개수 · 검색) 줄 → 글 행 목록 → 전체 폭 괘선 + 푸터.
// 치수 주석의 숫자는 시안 원본 px(1536폭 렌더)이고 실제 값은 ÷1.217한 CSS px이다
//   (상단바 괘선이 y=73 → 코드의 TopBar 높이 60px 기준 스케일 1.217).
//
// 글은 전부 velog 원문으로 나가는 외부 링크다 — 백엔드(/community/blog)는 velog RSS 프록시라
// 본문을 주지 않는다. 그래서 인앱 본문 페이지가 없고, 행마다 ArrowUpRight로 "새 탭"을 알린다.
// 시안의 분류 탭(개발 기록·운영 노트·업데이트)은 응답에 category 필드가 없어 넣지 않았다.
import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { listBlogPosts } from "../../api/community.js";
import SiteFooter from "./SiteFooter.jsx";

// 글 목록은 Community의 블로그 섹션과 같은 velog 시리즈다 — 빈 상태에서 직접 보라고 안내.
const SERIES_URL = "https://velog.io/@yun60/series/KoDeploy";

export default function Blog() {
  const [posts, setPosts] = useState(null); // null = 불러오는 중
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let alive = true;
    listBlogPosts()
      .then((data) => alive && setPosts(Array.isArray(data) ? data : []))
      .catch(() => {
        if (!alive) return;
        setFailed(true);
        setPosts([]);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 검색 API가 없으므로 받아온 목록을 제목·설명으로 클라이언트 필터.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !posts) return posts || [];
    return posts.filter(
      (p) =>
        (p.title || "").toLowerCase().includes(q) ||
        (p.description || "").toLowerCase().includes(q),
    );
  }, [posts, query]);

  return (
    <div>
      {/* 시안 콘텐츠 x200-1336(폭 1136 → CSS 934) = .kd-page-narrow */}
      <div className="kd-page-narrow" style={{ paddingTop: 44, paddingBottom: 8 }}>
        {/* 시안: 상단바 괘선(74) 아래 66 → 제목 잉크 60 */}
        <h1 className="kd-t-display" style={{ color: "var(--fg-1)" }}>
          만들고, 운영하며 배운 것들.
        </h1>
        <p className="kd-t-lead" style={{ color: "var(--fg-2)", marginTop: 10 }}>
          KoDeploy의 개발과 운영 이야기를 기록합니다.
        </p>
        {/* 외부 글이라는 사실을 목록에 들어가기 전에 한 줄로 알린다 */}
        <p className="kd-t-body-s" style={{ color: "var(--fg-3)", marginTop: 6 }}>
          velog에 쓴 글을 모아 보여줍니다 - 제목을 누르면 velog 원문이 새 탭에서 열립니다.
        </p>

        {/* 시안의 (분류 탭 · 검색) 줄 — 분류는 데이터에 없어 개수 표시로 대체했다.
            시안 입력 229x48 → CSS 188x39 ≈ .kd-input(38) */}
        <div
          className="flex items-center justify-between gap-4 flex-wrap"
          style={{
            marginTop: 28,
            paddingBottom: 12,
            borderBottom: "1px solid var(--kd-border)",
          }}
        >
          {/* 목록이 비었을 때는 "글 0개" 대신 아래 빈 상태 문구가 사정을 설명한다 */}
          <span className="kd-t-label" style={{ color: "var(--fg-3)" }}>
            {posts === null
              ? "불러오는 중…"
              : query.trim()
                ? `검색 결과 ${shown.length}개`
                : posts.length > 0
                  ? `글 ${posts.length}개`
                  : ""}
          </span>
          <div style={{ position: "relative", width: 190 }}>
            <Search
              size={15}
              aria-hidden
              style={{
                position: "absolute",
                left: 12,
                top: "50%",
                transform: "translateY(-50%)",
                color: "var(--fg-4)",
                pointerEvents: "none",
              }}
            />
            <input
              className="kd-input"
              type="search"
              aria-label="글 검색"
              placeholder="글 검색"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={{ paddingLeft: 34 }}
            />
          </div>
        </div>

        {/* 글 행 — 시안 피치 152(CSS 125), 행 사이 괘선. 마지막 행 아래는 전체 폭 괘선이 받는다 */}
        {posts === null ? (
          <p className="kd-t-body-s" style={{ color: "var(--fg-4)", paddingBlock: 40 }}>
            블로그 글을 불러오는 중…
          </p>
        ) : shown.length === 0 ? (
          <EmptyState failed={failed} query={query.trim()} />
        ) : (
          shown.map((post, i) => (
            <PostRow key={post.url || i} post={post} last={i === shown.length - 1} />
          ))
        )}
      </div>

      <SiteFooter />
    </div>
  );
}

// 글 한 행 — 캡션(날짜) → 제목 + 외부 링크 글리프 → 한 줄 설명.
// 행 전체가 링크라 제목 줄만 따로 감싸 화살표를 제목 가운데에 맞춘다(시안: 화살표 y415-431 = 제목 잉크 408-436).
function PostRow({ post, last }) {
  // 백엔드는 "2026.09.14" 형태로 내려준다. 혹시 ISO(2026-09-14)로 와도 표기를 맞춘다.
  const date = (post.date || "").replace(/-/g, ".") || "—";

  return (
    <a
      href={post.url}
      target="_blank"
      rel="noopener noreferrer"
      className="group block no-underline"
      style={{
        paddingBlock: 20, // 시안: 괘선→캡션 잉크 29, 설명 잉크→괘선 23 (행 피치 152 → CSS 125)
        borderBottom: last ? "none" : "1px solid var(--kd-border)",
      }}
    >
      <div className="kd-t-caption" style={{ color: "var(--fg-4)" }}>
        {date}
      </div>
      <div className="flex items-center gap-6" style={{ marginTop: 4 }}>
        <h2
          className="kd-t-display-s kd-t-sans flex-1 min-w-0"
          style={{ color: "var(--fg-1)" }}
        >
          {post.title}
        </h2>
        {/* 목록 행 아이콘 18(--ico-md). 위로 뻗은 화살표 = 사이트 밖으로 나간다 */}
        <ArrowUpRight
          size={18}
          aria-hidden
          className="shrink-0 transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
          style={{ color: "var(--fg-3)" }}
        />
      </div>
      {post.description && (
        <p className="kd-t-body" style={{ color: "var(--fg-3)", marginTop: 3 }}>
          {post.description}
        </p>
      )}
    </a>
  );
}

// 빈 상태 — 세 가지: 검색 결과 없음 / velog 호출 실패 / 글 없음.
// 실패·없음일 때는 시리즈 원문으로 직접 갈 길을 남긴다.
function EmptyState({ failed, query }) {
  return (
    <div style={{ paddingBlock: 56 }}>
      <p className="kd-t-body" style={{ color: "var(--fg-2)" }}>
        {query
          ? `‘${query}’에 해당하는 글이 없어요.`
          : failed
            ? "velog에서 글을 가져오지 못했어요."
            : "아직 올린 글이 없어요."}
      </p>
      {!query && (
        <a
          href={SERIES_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="kd-t-label inline-flex items-center gap-1.5 no-underline"
          style={{ color: "var(--fg-3)", marginTop: 8 }}
        >
          velog 시리즈에서 보기
          <ArrowUpRight size={15} aria-hidden />
        </a>
      )}
    </div>
  );
}
