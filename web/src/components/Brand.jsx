// 워드마크 — 마크(어긋난 평행사변형 두 장) + "KoDeploy".
// 마크는 design/로고-마크.png 원본을 외곽선 추적해 옮긴 패스다(폴리곤 2장, 98.7% 일치).
// 색은 currentColor 단색(잉크)만 — 모노크롬 시스템이라 브랜드색 분기가 없다.
// 두 판(plate)이 한 칸씩 어긋나 겹치는 형태 = 배포로 교체되는 버전을 뜻한다.

// 원본 잉크 박스 916x643. 라운드 조인은 추적에서 평평해져 얇은 stroke로 되살린다.
const MARK_W = 916;
const MARK_H = 643;
const PLATE_TOP =
  "M287,0 L731,0 L741,8 L740,16 L449,352 L441,360 L440,358 L424,282 L605,72 L613,62 L610,59 " +
  "L315,60 L309,63 L114,298 L390,299 L336,360 L17,361 L8,357 L1,348 L0,336 L3,329 L273,9 L286,1Z";
const PLATE_BOTTOM =
  "M587,240 L894,240 L907,245 L915,257 L914,270 L899,290 L600,640 L173,642 L161,637 L154,624 " +
  "L157,611 L397,333 L413,411 L271,578 L567,579 L805,303 L536,303 L534,302 L538,296 L586,241Z";

// 마크만 필요한 자리(파비콘·빈 상태 일러스트 등)를 위해 따로 export.
export function BrandMark({ size = 22 }) {
  return (
    <svg
      width={size * (MARK_W / MARK_H)}
      height={size}
      viewBox={`0 0 ${MARK_W} ${MARK_H}`}
      fill="currentColor"
      stroke="currentColor"
      strokeWidth="6"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ display: "block", flexShrink: 0 }}
    >
      <path d={PLATE_TOP} />
      <path d={PLATE_BOTTOM} />
    </svg>
  );
}

export default function Brand({ size = 22, showWordmark = true }) {
  return (
    <span
      aria-label="KoDeploy"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: size * 0.42,
        color: "var(--fg-strong)",
        userSelect: "none",
      }}
    >
      <BrandMark size={size * 0.92} />
      {showWordmark && (
        <span
          style={{
            fontFamily:
              '"Pretendard Variable", Pretendard, "Inter Variable", Inter, sans-serif',
            fontWeight: 700,
            fontSize: size,
            lineHeight: 1,
            letterSpacing: "-0.035em",
          }}
        >
          KoDeploy
        </span>
      )}
    </span>
  );
}
