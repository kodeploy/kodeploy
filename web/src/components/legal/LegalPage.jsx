// 이용약관 · 개인정보처리방침 공용 틀 — 읽는 문서라 가이드 본문 조각(Section/Bullet)을 그대로 쓴다.
import { Link } from "react-router-dom";
import { DocScale } from "../guide/atoms.jsx";
import SiteFooter from "../marketing/SiteFooter.jsx";

export const CONTACT_EMAIL = "yuntyu01@gmail.com";
export const EFFECTIVE_DATE = "2026년 10월 10일";

export default function LegalPage({ title, children }) {
  return (
    <div className="flex-1 overflow-auto scroll-thin" data-kd-scroll="page">
      <div className="kd-page-narrow" style={{ paddingTop: 48, paddingBottom: 40 }}>
        <h1 className="kd-t-display text-fg-1">{title}</h1>
        <p className="kd-t-body-s text-fg-3" style={{ marginTop: 8, marginBottom: 36 }}>
          시행일 {EFFECTIVE_DATE} · 문의 {CONTACT_EMAIL}
        </p>
        <DocScale>{children}</DocScale>
        <div className="flex items-center gap-4" style={{ marginTop: 24 }}>
          <Link to="/terms" className="kd-t-body-s text-fg-2 hover:text-fg-1 no-underline">
            이용약관
          </Link>
          <span className="kd-t-body-s text-fg-4">|</span>
          <Link to="/privacy" className="kd-t-body-s text-fg-2 hover:text-fg-1 no-underline">
            개인정보처리방침
          </Link>
        </div>
      </div>
      <SiteFooter />
    </div>
  );
}
