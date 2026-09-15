// 커스텀 도메인 연결 상태 — StatusBadge/AppStatusBadge와 같은 dot+라벨 톤 (단일 진실원).
// pending=인증서 검증 대기(주황·pulse), active=연결 완료(초록).
// 색은 테마 토큰으로 — 하드코딩하면 다크에서 대비가 무너진다.
const STYLES = {
  pending: { color: "var(--warn-fg)", label: "대기 중", pulse: true },
  active: { color: "var(--ok-fg)", label: "연결됨" },
};

export default function DomainStatusBadge({ status }) {
  const s = STYLES[status] || { color: "var(--fg-3)", label: status || "—" };
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[10.5px] text-fg-1 shrink-0"
      style={{ fontWeight: 510 }}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${s.pulse ? "kd-pulse-soft" : ""}`}
        style={{ background: s.color }}
      />
      {s.label}
    </span>
  );
}
