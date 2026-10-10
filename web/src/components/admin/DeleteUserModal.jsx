// 계정 강제 탈퇴 확인 — 앱 삭제 확인(DeleteAppModal)과 같은 모양. 아이디를 그대로 입력해야 지울 수 있다.
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { deleteUserAccount } from "../../api/admin.js";

// target: { id, login, app_count } — 지우는 계정. 끝나면 onDone()으로 목록을 다시 읽게 한다.
export default function DeleteUserModal({ target, onClose, onDone }) {
  const [typed, setTyped] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const canDelete = typed === target.login && !submitting;

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
      await deleteUserAccount(target.id);
      onDone();
    } catch (err) {
      setError(err.message || "탈퇴 처리 실패");
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
        className="relative w-[440px] max-w-[92vw] px-8 pt-8 pb-7 rounded-xl"
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
          이 계정을 탈퇴시킬까요?
        </h2>
        <p className="text-[13px] text-fg-3 mb-5" style={{ fontWeight: 450, lineHeight: 1.55 }}>
          {target.login}의 앱 {target.app_count ?? 0}개와 그 데이터, 계정 정보가 회원 탈퇴와 똑같이 모두
          삭제됩니다. 되돌릴 수 없어요. 이 조치는 관리자 기록에 남습니다.
        </p>

        <div className="mb-4">
          <div className="text-[10.5px] tracking-[0.08em] text-fg-3 mb-2" style={{ fontWeight: 590 }}>
            확인하려면 아이디 <span style={{ color: "var(--accent)" }}>{target.login}</span>을 입력하세요
          </div>
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={target.login}
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
            {submitting ? "탈퇴 처리 중…" : "강제 탈퇴"}
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
