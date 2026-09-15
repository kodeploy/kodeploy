// 가이드 본문 공용 building blocks - Section/Bullet/Code/CodeBlock/Hl.
// 같은 본문을 두 곳이 쓴다: 문서 화면(/guide, 3단 셸)과 배포 폼의 가이드 드로어(GuidePanel, 폭 520).
// 드로어에 28px 제목을 그대로 쓰면 폭에 비해 과해서, 크기는 컨텍스트로 갈라둔다
// (기본값 "panel" = 지금까지의 크기. 문서 화면만 DocScale로 큰 램프를 켠다).
import { createContext, useContext, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";

const ScaleCtx = createContext("panel");

export function DocScale({ children }) {
  return <ScaleCtx.Provider value="doc">{children}</ScaleCtx.Provider>;
}

// CodeBlock 안인지 알려준다 - 강조(Hl)가 어두운 코드 블록 위에서 안 보이는 걸 막기 위함.
const InCodeCtx = createContext(false);

export function Section({ title, children }) {
  const doc = useContext(ScaleCtx) === "doc";
  return (
    <section style={{ marginBottom: doc ? 40 : 32 }}>
      <h2
        className={doc ? "kd-t-title" : "kd-t-section"}
        style={{
          color: "var(--fg-1)",
          marginBottom: doc ? 12 : 10,
          // 우측 목차에서 점프했을 때 제목이 스크롤 컨테이너 상단에 붙지 않게.
          scrollMarginTop: 24,
        }}
      >
        {title}
      </h2>
      <div className={doc ? "kd-t-body" : "kd-t-body-s"} style={{ color: "var(--fg-2)" }}>
        {children}
      </div>
    </section>
  );
}

export function Bullet({ children }) {
  return (
    <div className="flex gap-2" style={{ marginBottom: 6 }}>
      <span aria-hidden style={{ color: "var(--fg-4)" }}>
        ·
      </span>
      <span>{children}</span>
    </div>
  );
}

// inline 코드 - mono 폰트 X. 본문(fg-2)보다 진한 fg-1로 강조.
export function Code({ children }) {
  return <span style={{ color: "var(--fg-1)" }}>{children}</span>;
}

// 강조 - 산문에서는 잉크(accent), 어두운 코드 블록 안에서는 터미널 전경색.
// accent는 라이트 테마에서 검정이라 코드 블록 위에 그대로 쓰면 글자가 사라진다.
export function Hl({ children }) {
  const inCode = useContext(InCodeCtx);
  return (
    <span style={{ color: inCode ? "var(--term-fg)" : "var(--accent)", fontWeight: 600 }}>
      {children}
    </span>
  );
}

// 여러 줄 코드 - 시안의 어두운 블록(터미널과 같은 바탕). 줄바꿈/들여쓰기는 whiteSpace: pre.
// 우상단 복사 버튼 - pre의 innerText로 추출 (mixed children: string + Hl span 모두 지원).
export function CodeBlock({ children }) {
  const doc = useContext(ScaleCtx) === "doc";
  const ref = useRef(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!ref.current) return;
    try {
      await navigator.clipboard.writeText(ref.current.innerText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 차단 환경(HTTP, 권한 없음 등) - silent
    }
  };

  return (
    <InCodeCtx.Provider value>
      <div className="relative" style={{ marginBlock: doc ? 16 : 12 }}>
        <pre
          ref={ref}
          className="kd-t-code overflow-auto scroll-thin"
          style={{
            background: "var(--term-bg)",
            color: "var(--term-fg)",
            borderRadius: 8,
            // 시안 코드 블록: 안쪽 여백 20/18(원본 px) → ÷1.217
            padding: doc ? "16px 18px" : "12px 14px",
            whiteSpace: "pre",
          }}
        >
          {children}
        </pre>
        <button
          type="button"
          onClick={handleCopy}
          aria-label="복사"
          className="absolute rounded-md transition-colors"
          style={{
            top: 8,
            right: 8,
            padding: 6,
            color: copied ? "var(--term-fg)" : "var(--fg-4)",
          }}
        >
          {/* 인라인/버튼 안 아이콘 15(--ico-sm) */}
          {copied ? <Check size={15} strokeWidth={2} /> : <Copy size={15} strokeWidth={1.8} />}
        </button>
      </div>
    </InCodeCtx.Provider>
  );
}
