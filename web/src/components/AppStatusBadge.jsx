// 현재 Pod 상태 표시 — 빌드와 독립. AppLayout이 폴링해 각 탭에 내려준다.
// StatusBadge(빌드)와 시각적으로 같은 톤이지만 의미는 "지금 살아있나"라는 실시간 상태.
export const APP_STATUS_STYLES = {
  running:  { color: "var(--dot-ok)", label: "실행 중" },
  pending:  { color: "var(--dot-warn)", label: "시작 중", pulse: true },
  crashing: { color: "var(--dot-err)", label: "오류" },
  missing:  { color: "var(--fg-3)", label: "중지" },
  building: { color: "var(--dot-warn)", label: "빌드 중", pulse: true },  // 슬롯 Pod 미존재 + 활성 빌드 — 위젯이 계산해서 전달
};

export default function AppStatusBadge({ status }) {
  const s = APP_STATUS_STYLES[status] || { color: "var(--fg-3)", label: status || "—" };
  return (
    <span
      className="inline-flex items-center gap-1.5 text-[10.5px] text-fg-1 shrink-0"
      style={{ fontWeight: 510 }}
    >
      <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          s.pulse ? "kd-pulse-soft" : ""
        }`}
        style={{ background: s.color }}
      />
      {s.label}
    </span>
  );
}
