import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { deleteAccount } from "../api/auth.js";
import { useAuth } from "../contexts/AuthContext.jsx";

// 회원 탈퇴 확인 — 앱 삭제 모달(DeleteAppModal)과 같은 문법: 내 GitHub 아이디를 타이핑해야 버튼이 열린다.
// 소유한 앱이 많으면 K8s 정리 때문에 수십 초 걸릴 수 있어 진행 중에는 닫지 못하게 한다.
export default function DeleteAccountModal({ onClose }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const login = user?.login || "";
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const isRoot = user?.role === "root";           // 소유자 계정은 서버가 탈퇴를 막는다 — 눌러 보기 전에 알린다
  const canDelete = typed === login && !submitting && !isRoot;

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && !submitting && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose, submitting]);

  const handleDelete = async () => {
    if (!canDelete) return;
    setSubmitting(true);
    setError(null);
    try {
      await deleteAccount(login);
      await logout();                 // 로컬 상태를 비운다 (서버 세션은 이미 지워졌다)
      onClose();
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message?.replace(/^\d+ /, "") || "탈퇴 실패");
      setSubmitting(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center kd-fade-in"
      style={{ background: "rgba(0,0,0,0.85)" }}
      onClick={() => !submitting && onClose()}
    >
      <div
        className="relative w-[460px] max-w-[92vw] px-8 pt-8 pb-7 rounded-xl"
        style={{
          background: "var(--kd-bg)",
          border: "1px solid var(--line-2)",
          boxShadow: "0 24px 60px rgba(0,0,0,0.6), 0 4px 12px rgba(0,0,0,0.4)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          disabled={submitting}
          className="absolute top-3 right-3 w-8 h-8 rounded-md text-fg-4 hover:text-fg-1 hover:bg-[var(--line-1)] transition-colors flex items-center justify-center disabled:opacity-40"
          aria-label="닫기"
        >
          <X size={14} strokeWidth={1.8} />
        </button>

        <h2 className="kd-t-section text-fg-1 mb-2" style={{ fontWeight: 590, letterSpacing: -0.3 }}>
          정말 탈퇴할까요?
        </h2>
        <p className="text-[13px] text-fg-3 mb-3" style={{ fontWeight: 450, lineHeight: 1.55 }}>
          내가 만든 모든 앱(서버 · DB 데이터 · 환경변수 · 파일 · 도메인)과 계정 정보, 커뮤니티 글과 댓글이 삭제됩니다.
          되돌릴 수 없어요.
        </p>
        <p className="text-[12px] text-fg-3 mb-5" style={{ fontWeight: 450, lineHeight: 1.55 }}>
          GitHub 쪽 연결은 GitHub 설정의 Applications에서 KoDeploy를 직접 제거해야 끊깁니다. 삭제 범위는{" "}
          <Link to="/privacy" onClick={onClose} className="underline" style={{ textUnderlineOffset: 3 }}>
            개인정보처리방침
          </Link>
          에서 볼 수 있어요.
        </p>

        {isRoot && (
          <p className="mb-4 text-[12px]" style={{ color: "var(--err-fg)", fontWeight: 510 }}>
            root(소유자) 계정은 탈퇴할 수 없어요.
          </p>
        )}

        <div className="mb-4">
          <div className="text-[10.5px] tracking-[0.08em] text-fg-3 mb-2" style={{ fontWeight: 590 }}>
            확인하려면 GitHub 아이디 <span style={{ color: "var(--accent)" }}>{login}</span>를 입력하세요
          </div>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={login}
            spellCheck={false}
            autoFocus
            disabled={submitting}
            className="w-full px-3 py-2 rounded-md bg-transparent outline-none text-[13px] text-fg-1 placeholder:text-fg-4"
            style={{ border: "1px solid var(--line-3)", fontWeight: 510 }}
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleDelete}
            disabled={!canDelete}
            className="flex-1 py-2.5 rounded-md text-[13px] transition-colors disabled:opacity-40"
            style={{ background: "transparent", border: "1px solid var(--line-3)", color: "var(--err-fg)", fontWeight: 510 }}
          >
            {submitting ? "삭제 중… (앱이 많으면 시간이 걸려요)" : "영구 탈퇴"}
          </button>
          <button
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2.5 rounded-md text-[13px] text-fg-2 hover:text-fg-1 hover:bg-[var(--line-1)] transition-colors disabled:opacity-40"
            style={{ fontWeight: 510, border: "1px solid var(--line-3)" }}
          >
            취소
          </button>
        </div>

        {error && (
          <p className="mt-3 text-[12px]" style={{ color: "var(--err-fg)", fontWeight: 510 }}>
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
