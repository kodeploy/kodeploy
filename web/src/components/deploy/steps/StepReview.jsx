// 배포 마법사 3단계 — 확인 및 배포.
// 입력은 없고 2단계까지 고른 값을 표로 되짚는다. 각 행의 "수정"은 해당 단계로 돌아가며 입력은 그대로 남는다.
// 행: { label, step, parts, focus?, code? } — code면 parts를 이름 칩(고정폭)으로, 아니면 " · "로 잇는다.
import { StepHeading } from "./wizardParts.jsx";

// 시안 실측(÷1.39): 표 x349-1479 → 폭 813, 라벨/값 경계 x552 → 라벨 열 146,
// 행 구분선 y493·579·665·751 → 행 피치 86/1.39 ≈ 62.
const LABEL_W = 146;
const ROW_H = 62;

export default function StepReview({ appLabel, appUrl, rows, onJump }) {
  return (
    <div style={{ paddingTop: 56 }}>
      <StepHeading title="이 설정으로 배포할까요?" desc="선택한 내용을 확인하고 배포를 시작하세요." />

      <div className="kd-t-title" style={{ color: "var(--fg-1)" }}>
        {appLabel}
      </div>
      <div className="kd-t-body-s" style={{ marginTop: 8, color: "var(--fg-3)" }}>
        배포 후 주소 <span aria-hidden>·</span>{" "}
        <span style={{ color: "var(--fg-2)" }}>{appUrl}</span>
      </div>

      <dl style={{ marginTop: 34 }}>
        {rows.map((row) => (
          <div
            key={row.label}
            style={{
              minHeight: ROW_H,
              display: "grid",
              gridTemplateColumns: `${LABEL_W}px minmax(0, 1fr) auto`,
              alignItems: "center",
              columnGap: 16,
              borderBottom: "1px solid var(--kd-border)",
            }}
          >
            <dt className="kd-t-body-s kd-strong" style={{ color: "var(--fg-1)" }}>
              {row.label}
            </dt>
            {row.code ? (
              <dd style={{ display: "flex", flexWrap: "wrap", gap: 6, paddingBlock: 14 }}>
                {row.parts.map((p) => (
                  <span
                    key={p}
                    className="kd-t-code"
                    style={{
                      color: "var(--fg-2)",
                      background: "var(--kd-surface2)",
                      borderRadius: 4,
                      padding: "3px 8px",
                    }}
                  >
                    {p}
                  </span>
                ))}
              </dd>
            ) : (
              <dd className="kd-t-body-s" style={{ color: "var(--fg-2)", display: "flex", flexWrap: "wrap", gap: 12 }}>
                {row.parts.map((p, i) => (
                  <span key={p + i} style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
                    {i > 0 && <span aria-hidden style={{ color: "var(--fg-4)" }}>·</span>}
                    {p}
                  </span>
                ))}
              </dd>
            )}
            <button
              type="button"
              onClick={() => onJump(row.step, row.focus)}
              className="kd-t-body-s"
              style={{ color: "var(--fg-2)", textDecoration: "underline", textUnderlineOffset: 3 }}
            >
              수정
            </button>
          </div>
        ))}
      </dl>
    </div>
  );
}
