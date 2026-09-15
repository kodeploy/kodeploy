// 로그인 모달 — design/라이트모드-시안/02_로그인_모달.png 기준.
// 흐린 배경 위 흰 카드 한 장. Esc / 배경 클릭 / X 버튼으로 닫힘. body scroll lock.
// 치수는 시안 원본 px ÷ 1.45(그 렌더의 스케일).
import { useEffect } from "react";
import { ArrowUpRight, X } from "lucide-react";
import { Link } from "react-router-dom";
import Brand from "./Brand.jsx";
import { GITHUB_LOGIN_URL } from "../api/auth.js";

// GitHub mark — lucide-react 1.16.0에 브랜드 아이콘 없음 → inline SVG
function GithubMark({ size = 17 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

export default function LoginModal({ onClose }) {
  // GitHub OAuth는 백엔드가 state cookie 발급 후 GitHub로 302 redirect 해야 하므로
  // XHR 대신 full-page navigation (location 이동)으로 들어간다.
  const handleGithub = () => {
    window.location.href = GITHUB_LOGIN_URL;
  };

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center kd-fade-in"
      // 시안의 배경은 종이색(250)이 136까지 눌린 상태 — 알파 약 0.45(라이트).
      style={{ background: "var(--kd-scrim-strong)" }}
      onClick={onClose}
    >
      <div
        className="relative w-[422px] max-w-[92vw]"
        style={{
          background: "var(--kd-surface)",
          borderRadius: 6,
          paddingInline: 24,
          paddingTop: 16,
          paddingBottom: 22,
          boxShadow: "0 24px 60px rgba(23,23,23,0.18)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* 상단 — 워드마크 + 닫기 */}
        <div className="flex items-center">
          <Brand size={15} />
          <button
            onClick={onClose}
            className="ml-auto w-7 h-7 text-fg-3 hover:text-fg-1 transition-colors flex items-center justify-center"
            aria-label="닫기"
          >
            <X size={17} strokeWidth={1.7} />
          </button>
        </div>

        {/* 시안 잉크 43px → 30 ÷ 0.76 ≈ 39px. 모달 폭에 맞춰 34px로. */}
        <h2
          className="kd-t-display-s text-fg-1 text-center"
          style={{ marginTop: 26 }}
        >
          GitHub로 시작하세요.
        </h2>
        <p className="kd-t-body-s mt-2.5 text-center text-fg-2">
          저장소를 연결하고, 첫 앱을 배포해보세요.
        </p>

        <button
          onClick={handleGithub}
          className="kd-btn-primary kd-btn-lg w-full flex items-center justify-center gap-2.5"
          style={{ marginTop: 22 }}
        >
          <GithubMark size={17} />
          GitHub로 계속하기
        </button>

        <p className="kd-t-caption mt-4 text-center text-fg-3">
          GitHub 계정으로 로그인합니다.
        </p>

        <div className="mt-3 text-center">
          <Link
            to="/guide"
            onClick={onClose}
            className="kd-t-label inline-flex items-center gap-1 text-fg-2 hover:text-fg-1 underline transition-colors"
            style={{ textUnderlineOffset: 4 }}
          >
            로그인 도움말
            <ArrowUpRight size={13} strokeWidth={1.8} />
          </Link>
        </div>
      </div>
    </div>
  );
}
