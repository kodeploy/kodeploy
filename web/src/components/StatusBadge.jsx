// 상태 → dot 색 + 한글 라벨 (단일 진실원).
// 톤 원칙(모노크롬 시스템에서 색을 쓰는 유일한 자리):
//   - 작업 phase(앰버): building/built/deploying — 진행 중인 것만 pulse
//   - 최종 안정(녹): running — 일반 컨벤션
//   - 실패(적): failed
//   - 텍스트는 fg-1 고정 — 색은 dot에만. dot 색은 --dot-* 토큰이라 테마별로 갈린다
// 빌드 row용 — running은 "실행 중" (그 빌드 시점 Pod 정상 출발)
export const STYLES_BUILD = {
  queued:    { color: "var(--fg-3)", label: "대기" },
  building:  { color: "var(--dot-warn)", label: "빌드 중", pulse: true },
  built:     { color: "var(--dot-warn)", label: "빌드 완료" },
  deploying: { color: "var(--dot-warn)", label: "배포 중", pulse: true },
  running:   { color: "var(--dot-ok)", label: "성공" },
  failed:    { color: "var(--dot-err)", label: "실패" },
  cancelled: { color: "var(--fg-3)", label: "중지" },
};

// env_change row용 — 같은 status값이지만 의미 다름: "그 변경이 성공/실패했나"
export const STYLES_ENV = {
  applied: { color: "var(--dot-warn)", label: "적용 중", pulse: true },
  running: { color: "var(--dot-ok)", label: "성공" },
  failed:  { color: "var(--dot-err)", label: "실패" },
};

export default function StatusBadge({ status, kind = "build" }) {
  const styles = kind === "env" ? STYLES_ENV : STYLES_BUILD;
  const s = styles[status] || { color: "var(--fg-3)", label: status };
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
