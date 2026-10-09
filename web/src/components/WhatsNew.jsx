import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { useAuth } from "../contexts/AuthContext.jsx";
import { PATCH_NOTES } from "../lib/patchNotes.js";

// 로그인하면 새 기능 안내를 한 번 보여 준다.
//   · "확인"          이번 접속에서만 닫는다 (탭을 닫기 전까지 다시 안 뜬다) — sessionStorage
//   · "다시 보지 않기"  이 안내 버전은 이 브라우저에서 다시 안 띄운다 — localStorage (유저별)
// 보관소를 못 쓰는 환경(시크릿 창 등)에서도 화면은 뜨고 닫힌다 — 저장만 건너뛴다.
// 이 안내의 기준 날짜 이후에 가입한 유저는 처음부터 새 기능을 쓰니 보여 주지 않는다.
const seenKey = (uid) => `kd-whatsnew:${uid}`;

function read(storage, key) {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}
function write(storage, key, value) {
  try {
    storage.setItem(key, value);
  } catch {
    /* 저장 불가 — 이번 화면에서만 닫힌다 */
  }
}

export default function WhatsNew() {
  const { user, loading } = useAuth();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (loading || !user) {
      setOpen(false);
      return;
    }
    const key = seenKey(user.id);
    const joinedAfter = user.created_at && new Date(user.created_at) >= new Date(PATCH_NOTES.version);
    const hidden =
      read(localStorage, key) === PATCH_NOTES.version || read(sessionStorage, key) === PATCH_NOTES.version;
    setOpen(!joinedAfter && !hidden);
  }, [loading, user?.id, user?.created_at]);

  const close = (forever) => {
    if (user) write(forever ? localStorage : sessionStorage, seenKey(user.id), PATCH_NOTES.version);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && close(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;
  const { title, items, contact } = PATCH_NOTES;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center kd-fade-in"
      style={{ background: "rgba(0,0,0,0.6)" }}
      onClick={() => close(false)}
    >
      <div
        role="dialog"
        aria-label={title}
        className="relative w-[520px] max-w-[92vw] max-h-[88vh] overflow-auto scroll-thin px-8 pt-8 pb-7 rounded-xl"
        style={{
          background: "var(--kd-bg)",
          border: "1px solid var(--line-2)",
          boxShadow: "0 24px 60px rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.3)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={() => close(false)}
          className="absolute top-3 right-3 w-8 h-8 rounded-md text-fg-4 hover:text-fg-1 hover:bg-[var(--line-1)] transition-colors flex items-center justify-center"
          aria-label="닫기"
        >
          <X size={14} strokeWidth={1.8} />
        </button>

        <div className="kd-t-micro text-fg-3" style={{ letterSpacing: "0.04em" }}>
          {PATCH_NOTES.version}
        </div>
        <h2 className="kd-t-section text-fg-1" style={{ fontWeight: 590, letterSpacing: -0.3, marginTop: 4 }}>
          {title}
        </h2>

        <ul style={{ marginTop: 18 }}>
          {items.map((it, i) => (
            <li
              key={it.title}
              style={{ paddingBlock: 14, borderTop: i === 0 ? "1px solid var(--kd-border)" : "none", borderBottom: "1px solid var(--kd-border)" }}
            >
              <div className="kd-t-body-s kd-strong text-fg-1">{it.title}</div>
              <p className="kd-t-body-s text-fg-2" style={{ marginTop: 4, lineHeight: 1.55 }}>
                {it.body}
              </p>
            </li>
          ))}
        </ul>

        <p className="kd-t-body-s text-fg-2" style={{ marginTop: 18, lineHeight: 1.55 }}>
          {contact.text}{" "}
          <a
            href={`mailto:${contact.email}`}
            className="kd-strong"
            style={{ color: "var(--accent)", textDecoration: "underline", textUnderlineOffset: 3 }}
          >
            {contact.email}
          </a>
        </p>

        <div className="flex items-center gap-2" style={{ marginTop: 22 }}>
          <button className="kd-btn-primary kd-btn-md flex-1" onClick={() => close(false)}>
            확인
          </button>
          <button className="kd-btn-secondary kd-btn-md" onClick={() => close(true)}>
            다시 보지 않기
          </button>
        </div>
      </div>
    </div>
  );
}
