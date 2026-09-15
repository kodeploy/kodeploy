// 피드백 페이지 ("/community") — design/라이트모드-시안/25_피드백.png 기준.
//
// 2단: 좌 의견 목록 / 세로 괘선 / 우 선택한 의견 상세(본문 + 댓글 + 댓글 입력).
// 상단 "의견 남기기"를 누르면 우측 단이 작성 폼으로 바뀐다 — 시안에 없는 화면이라
// 별도 모달을 만들지 않고 이미 있는 상세 자리를 그대로 쓴다(2단 구조 유지).
//
// 치수 주석의 숫자는 시안 원본 px(1536폭 렌더)이고 실제 값은 ÷1.3665(= CSS 1124)한 CSS px이다.
// 콘텐츠 폭 시안 1320px ÷ 1.3665 = 966 ≈ --kd-w-marketing(1040 - 좌우 56)의 928과 맞춘다.
//
// 데이터는 GET /community(PostOut) · GET /community/{id}(PostDetail)가 전부다.
// 시안의 분류 탭(기능 제안/오류 제보/질문)과 상태 칩(검토 중)은 백엔드에 해당 컬럼이
// 없어서 넣지 않았다 — 없는 값은 만들지 않는다. 카드 제목은 content의 첫 줄,
// 설명은 나머지 줄이고, 메타는 실제로 있는 작성자·작성시각·댓글 수다.
import { useCallback, useEffect, useState } from "react";
import SiteFooter from "./marketing/SiteFooter.jsx";
import { Lock, Trash2 } from "lucide-react";
import {
  createComment,
  createPost,
  deleteComment,
  deletePost,
  getPost,
  listPosts,
} from "../api/community.js";
import { relativeTime } from "../lib/format.js";
import { useAuth } from "../contexts/AuthContext.jsx";

// content 첫 줄을 제목처럼, 나머지를 설명처럼 쓴다 (제목 컬럼이 따로 없어서).
function splitContent(content) {
  const [head, ...rest] = (content || "").split("\n");
  return { head: head.trim(), body: rest.join("\n").trim() };
}

export default function Community() {
  const { user, openLogin } = useAuth();
  const [posts, setPosts] = useState(null);      // null=로딩 중
  const [detail, setDetail] = useState(null);    // 우측에 펼친 글 (PostDetail)
  const [composing, setComposing] = useState(false);

  const fetchPosts = useCallback(async () => {
    try {
      const data = await listPosts();
      setPosts(data);
      return data;
    } catch {
      setPosts([]);
      return [];
    }
  }, []);

  const openPost = useCallback(async (id) => {
    setComposing(false);
    try {
      setDetail(await getPost(id));
    } catch {
      setDetail(null);
    }
  }, []);

  // 시안은 항상 하나가 선택된 상태다 — 첫 글을 자동으로 펼쳐 우측 단을 비워두지 않는다.
  useEffect(() => {
    fetchPosts().then((data) => {
      if (data.length > 0) openPost(data[0].id);
    });
  }, [fetchPosts, openPost]);

  const startCompose = () => {
    if (!user) return openLogin?.();
    setDetail(null);
    setComposing(true);
  };

  const handleCreated = async (post) => {
    setComposing(false);
    await fetchPosts();
    openPost(post.id);
  };

  const handleDeletePost = async (id) => {
    try {
      await deletePost(id);
    } catch {
      return;
    }
    const next = await fetchPosts();
    if (next.length > 0) openPost(next[0].id);
    else setDetail(null);
  };

  return (
    <div className="kd-fade-in">
      <div className="kd-page-narrow" style={{ paddingTop: 56, paddingBottom: 72 }}>
      {/* ── 페이지 머리 (시안: 제목 잉크 y151-211 / 리드 y223 / 버튼 147x53 @x1283) ── */}
      <header className="flex items-start gap-8 flex-wrap">
        <div className="min-w-0">
          <h1 className="kd-t-display text-fg-strong">함께 더 나은 KoDeploy로.</h1>
          <p className="kd-t-lead text-fg-2" style={{ marginTop: 0, wordBreak: "keep-all" }}>
            불편했던 점이나 필요한 기능을 알려주세요.
          </p>
        </div>
        <button
          onClick={startCompose}
          className="kd-btn-primary kd-btn-md shrink-0 ml-auto"
          style={{ marginTop: 6 }}
        >
          의견 남기기
        </button>
      </header>

      {/* ── 2단 — 좌 목록 / 세로 괘선(시안 x843) / 우 상세(시안 x887, 폭 542 ÷1.3665 = 397) ── */}
      <div
        className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_397px]"
        style={{ marginTop: 28 }}
      >
        <section>
          {/* 목록 머리 — 시안의 탭 줄 자리. 분류가 없으니 개수 하나만 두고 괘선은 남긴다 */}
          <div style={{ borderBottom: "1px solid var(--kd-border)" }}>
            <span
              className="kd-t-label text-fg-1 inline-block"
              style={{ paddingBottom: 12, borderBottom: "2px solid var(--fg-1)" }}
            >
              의견{posts ? ` ${posts.length}` : ""}
            </span>
          </div>

          {posts === null ? (
            <div className="kd-t-body-s text-fg-3" style={{ paddingBlock: 24 }}>
              불러오는 중…
            </div>
          ) : posts.length === 0 ? (
            <div className="kd-t-body-s text-fg-3" style={{ paddingBlock: 24 }}>
              아직 남겨진 의견이 없어요. 첫 의견을 남겨주세요.
            </div>
          ) : (
            <div style={{ marginTop: 8 }}>
              {posts.map((p) => (
                <PostCard
                  key={p.id}
                  post={p}
                  selected={detail?.id === p.id}
                  onSelect={() => openPost(p.id)}
                />
              ))}
            </div>
          )}
        </section>

        {/* 우 — 괘선에서 32 띄운다(시안 x843 → x887). 좁은 화면에서는 아래로 쌓는다 */}
        <aside className="border-kd-border border-t lg:border-t-0 lg:border-l pt-10 lg:pt-0 lg:pl-8">
          {composing ? (
            <ComposeForm onCancel={() => setComposing(false)} onCreated={handleCreated} />
          ) : detail ? (
            <PostDetailPane
              post={detail}
              onDelete={() => handleDeletePost(detail.id)}
              onChanged={() => openPost(detail.id)}
              onCountChanged={fetchPosts}
            />
          ) : (
            <div className="kd-t-body-s text-fg-3">왼쪽 목록에서 의견을 선택하세요.</div>
          )}
        </aside>
      </div>
      </div>
      <SiteFooter />
    </div>
  );
}

// 목록 카드 — 시안 피치 178 ÷1.3665 = 130, 안쪽 여백 22 ÷1.3665 = 16.
// 선택 카드는 테두리 없이 옅은 채움만(시안 채움 x109-843 = 단 전체 폭).
function PostCard({ post, selected, onSelect }) {
  const { head, body } = splitContent(post.content);
  return (
    <button
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className={`block w-full text-left ${selected ? "" : "kd-hoverable"}`}
      style={{
        padding: 16,
        border: "none",
        borderBottom: "1px solid var(--kd-border)",
        background: selected ? "var(--sel-soft)" : "transparent",
        cursor: "pointer",
      }}
    >
      <span className="flex items-center gap-1.5">
        {/* 비밀글이면 본문이 서버에서 "비밀글입니다."로 가려져 온다 — 자물쇠로 이유를 밝힌다 */}
        {post.is_secret && (
          <Lock size={15} strokeWidth={1.8} className="shrink-0" style={{ color: "var(--fg-4)" }} />
        )}
        <span className="kd-t-subtitle text-fg-1 truncate min-w-0">{head || "(내용 없음)"}</span>
      </span>
      {body && (
        <span className="kd-t-body-s text-fg-2 truncate block" style={{ marginTop: 4 }}>
          {body}
        </span>
      )}
      <span className="kd-t-micro text-fg-4 block" style={{ marginTop: 10 }}>
        {post.author} · {relativeTime(post.created_at)}
        {post.comment_count > 0 ? ` · 댓글 ${post.comment_count}` : ""}
      </span>
    </button>
  );
}

// 우측 상세 — 메타 / 제목(첫 줄) / 본문(나머지) / 댓글 / 댓글 입력.
function PostDetailPane({ post, onDelete, onChanged, onCountChanged }) {
  const { user, openLogin } = useAuth();
  const { head, body } = splitContent(post.content);
  const [text, setText] = useState("");
  const [secret, setSecret] = useState(false);
  const [busy, setBusy] = useState(false);

  // 글이 바뀌면 쓰다 만 댓글은 따라가지 않게 비운다.
  useEffect(() => {
    setText("");
    setSecret(false);
  }, [post.id]);

  const submit = async () => {
    if (!user) return openLogin?.();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await createComment(post.id, { content: text.trim(), isSecret: secret });
      setText("");
      setSecret(false);
      onChanged();        // 상세 다시 읽기
      onCountChanged();   // 목록의 댓글 수도 같이 갱신
    } catch {}
    setBusy(false);
  };

  const removeComment = async (id) => {
    try {
      await deleteComment(id);
      onChanged();
      onCountChanged();
    } catch {}
  };

  return (
    <div>
      {/* 시안의 "선택한 의견 · 예시" 자리 — 실제로 있는 값은 작성자와 작성시각이다 */}
      <div className="flex items-center gap-2">
        <span className="kd-t-label text-fg-3 truncate min-w-0">
          {post.author} · {relativeTime(post.created_at)}
        </span>
        {post.is_secret && (
          <Lock size={15} strokeWidth={1.8} className="shrink-0" style={{ color: "var(--fg-4)" }} />
        )}
        {post.is_mine && (
          <button
            onClick={onDelete}
            title="의견 삭제"
            className="ml-auto shrink-0 kd-hoverable"
            style={{ border: "none", background: "none", cursor: "pointer", padding: 4, borderRadius: 4 }}
          >
            <Trash2 size={15} strokeWidth={1.8} style={{ color: "var(--fg-4)" }} />
          </button>
        )}
      </div>

      {/* 시안 제목 두 줄 (잉크 30, 줄 간격 43 ÷1.3665 ≈ 31) */}
      <h2 className="kd-t-title text-fg-1" style={{ marginTop: 10, wordBreak: "keep-all" }}>
        {head || "(내용 없음)"}
      </h2>

      {body && (
        <p
          className="kd-t-body text-fg-2"
          style={{ marginTop: 14, whiteSpace: "pre-wrap", wordBreak: "keep-all" }}
        >
          {body}
        </p>
      )}

      {/* ── 댓글 (시안: 본문 끝 괘선 y597 / 제목 y623 / 첫 댓글 y666 / 입력창 y735) ── */}
      <div style={{ marginTop: 26, borderTop: "1px solid var(--kd-border)" }} />
      <div className="kd-t-body kd-strong text-fg-1" style={{ marginTop: 18 }}>
        댓글 {post.comments.length}
      </div>

      {post.comments.map((c) => (
        <div key={c.id} style={{ marginTop: 12 }}>
          <div className="flex items-center gap-2">
            <span className="kd-t-body-s kd-strong text-fg-1 truncate min-w-0">{c.author}</span>
            <span className="kd-t-caption text-fg-4 shrink-0">{relativeTime(c.created_at)}</span>
            {c.is_secret && (
              <Lock size={15} strokeWidth={1.8} className="shrink-0" style={{ color: "var(--fg-4)" }} />
            )}
            {c.is_mine && (
              <button
                onClick={() => removeComment(c.id)}
                title="댓글 삭제"
                className="ml-auto shrink-0 kd-hoverable"
                style={{ border: "none", background: "none", cursor: "pointer", padding: 4, borderRadius: 4 }}
              >
                <Trash2 size={15} strokeWidth={1.8} style={{ color: "var(--fg-4)" }} />
              </button>
            )}
          </div>
          <p
            className="kd-t-body-s text-fg-2"
            style={{ marginTop: 2, whiteSpace: "pre-wrap", wordBreak: "keep-all" }}
          >
            {c.content}
          </p>
        </div>
      ))}

      {/* 입력창 — 시안 542x103 ÷1.3665 = 397x75 (3줄) */}
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onFocus={() => !user && openLogin?.()}
        placeholder="댓글을 남겨주세요."
        rows={3}
        className="kd-input"
        style={{ marginTop: 14, display: "block" }}
      />
      <div className="flex items-center gap-3" style={{ marginTop: 8 }}>
        <SecretToggle on={secret} onToggle={() => setSecret((v) => !v)} label="비밀 댓글" />
        <button
          onClick={submit}
          disabled={!text.trim() || busy}
          className="kd-btn-primary kd-btn-sm ml-auto"
        >
          등록
        </button>
      </div>
    </div>
  );
}

// 새 의견 작성 — 시안에 없는 화면이라 상세 단의 리듬(제목 / 입력 / 우측 아래 버튼)을 그대로 쓴다.
function ComposeForm({ onCancel, onCreated }) {
  const [content, setContent] = useState("");
  const [secret, setSecret] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!content.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      onCreated(await createPost({ content: content.trim(), isSecret: secret }));
    } catch (e) {
      setError(e?.message || "등록하지 못했어요.");
    }
    setBusy(false);
  };

  return (
    <div>
      <div className="kd-t-label text-fg-3">새 의견</div>
      <h2 className="kd-t-title text-fg-1" style={{ marginTop: 14, wordBreak: "keep-all" }}>
        무엇이 불편했나요?
      </h2>
      <p className="kd-t-body-s text-fg-3" style={{ marginTop: 8, wordBreak: "keep-all" }}>
        첫 줄은 목록에 제목으로 보여요.
      </p>
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={"로그 검색 조건을 기억하면 좋겠어요.\n작업 공간으로 돌아올 때 검색 조건이 유지되면 편할 것 같아요."}
        rows={7}
        className="kd-input"
        style={{ marginTop: 20, display: "block" }}
        autoFocus
      />
      {error && (
        <div className="kd-t-caption" style={{ marginTop: 8, color: "var(--err-fg)" }}>
          {error}
        </div>
      )}
      <div className="flex items-center gap-3" style={{ marginTop: 8 }}>
        <SecretToggle on={secret} onToggle={() => setSecret((v) => !v)} label="비밀글" />
        <button onClick={onCancel} className="kd-btn-secondary kd-btn-sm ml-auto">
          취소
        </button>
        <button
          onClick={submit}
          disabled={!content.trim() || busy}
          className="kd-btn-primary kd-btn-sm"
        >
          등록
        </button>
      </div>
    </div>
  );
}

// 비밀글·비밀 댓글 토글 — 켜면 작성자와 관리자에게만 내용이 보인다(서버가 가린다).
function SecretToggle({ on, onToggle, label }) {
  return (
    <button
      onClick={onToggle}
      aria-pressed={on}
      className="kd-t-caption flex items-center gap-1.5"
      style={{
        border: "none",
        background: "none",
        cursor: "pointer",
        color: on ? "var(--fg-1)" : "var(--fg-4)",
      }}
    >
      <Lock size={15} strokeWidth={1.8} />
      {label}
    </button>
  );
}
