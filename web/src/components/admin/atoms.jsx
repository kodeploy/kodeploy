// 관리자 페이지 공용 조각 — 섹션 제목, 빈 상태 문구, 표 머리줄.

// 섹션 제목 + 아래 괘선 — 앱 개요(AppOverview.Section)와 같은 모양.
export function SectionTitle({ title, action }) {
  return (
    <div
      className="flex items-baseline gap-4"
      style={{ marginTop: 34, paddingBottom: 12, borderBottom: "1px solid var(--kd-border)" }}
    >
      <h2 className="kd-t-section text-fg-1">{title}</h2>
      {action}
    </div>
  );
}

export function Hint({ children }) {
  return (
    <div className="kd-t-caption text-fg-3" style={{ paddingBlock: 16 }}>
      {children}
    </div>
  );
}

// 표 머리줄 — 가입자·등급 표와 같은 높이·굵기.
export function TableHead({ cols, height = "var(--row-md)", className = "px-4" }) {
  return (
    <thead>
      <tr className="kd-t-micro text-fg-3 text-left">
        {cols.map((h) => (
          <th
            key={h}
            className={`${className} whitespace-nowrap`}
            style={{ height, borderBottom: "1px solid var(--kd-border)", fontWeight: 500 }}
          >
            {h}
          </th>
        ))}
      </tr>
    </thead>
  );
}

// 마지막 배포 상태 글자색 — 빌드 기록 표와 같은 색.
export const BUILD_STATUS_COLORS = {
  running: "var(--ok-fg)",   // 성공 (롤아웃 완료)
  failed: "var(--err-fg)",
  cancelled: "var(--fg-3)",
  building: "var(--warn-fg)",  // 진행 중 (아직 마감 안 됨)
  deploying: "var(--warn-fg)",
  queued: "var(--warn-fg)",
};

export const BUILD_STATUS_LABEL = {
  running: "성공",
  failed: "실패",
  cancelled: "취소",
  building: "빌드 중",
  deploying: "배포 중",
  queued: "대기",
};
