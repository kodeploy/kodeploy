// 마케팅 화면 공통 푸터 — 전체 폭 괘선 + 워드마크 + 링크 3개.
// 랜딩·블로그·피드백이 같은 마감을 쓴다(시안 01·22·25 동일).
import { Link } from "react-router-dom";
import Brand from "../Brand.jsx";

const LINK = "kd-t-label text-fg-2 hover:text-fg-1 transition-colors no-underline";

export default function SiteFooter() {
  return (
    <>
      <div style={{ borderTop: "1px solid var(--kd-rule)", marginTop: 8 }} />
      <div className="kd-page-narrow">
        <footer className="py-8 flex items-center gap-6 flex-wrap">
          <Brand size={16} />
          <div className="ml-auto flex items-center gap-7">
            <Link to="/guide" className={LINK}>
              가이드
            </Link>
            <a
              href="https://github.com/yuntyu01/kodeploy"
              target="_blank"
              rel="noopener noreferrer"
              className={LINK}
            >
              GitHub
            </a>
            <Link to="/community" className={LINK}>
              피드백
            </Link>
          </div>
        </footer>
      </div>
    </>
  );
}
