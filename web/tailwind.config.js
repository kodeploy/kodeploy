/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // 표면·전경 토큰은 CSS 변수로 — [data-theme]에 따라 런타임 전환.
        // 인라인 style의 var(--…)와 동일 소스라 테마가 한 곳에서 일관된다.
        "kd-bg": "var(--kd-bg)",
        "kd-panel": "var(--kd-panel)",
        "kd-surface": "var(--kd-surface)",
        "kd-surface2": "var(--kd-surface2)",
        "kd-nav": "var(--nav-bg)",
        "kd-border": "var(--kd-border)",
        "fg-strong": "var(--fg-strong)",
        "fg-1": "var(--fg-1)",
        "fg-2": "var(--fg-2)",
        "fg-3": "var(--fg-3)",
        "fg-4": "var(--fg-4)",
        // 모노크롬 — 브랜드색이 따로 없다. ink는 "가장 진한 전경"으로 테마에 따라 뒤집힌다.
        ink: "var(--accent)",
        "ink-fill": "var(--btn-primary-bg)",
        "ink-fill-fg": "var(--btn-primary-fg)",
        // 신호색은 최소한으로만 — 상태 배지·에러 텍스트 용도
        "green-ok": "var(--ok-fg)",
        "amber-warn": "var(--warn-fg)",
        "red-err": "var(--err-fg)",
      },
      fontFamily: {
        // 본문·UI — 국문 산세리프 우선(Pretendard). 헤드라인과 대비를 만든다.
        sans: [
          '"Pretendard Variable"',
          "Pretendard",
          '"Inter Variable"',
          "Inter",
          "system-ui",
          "sans-serif",
        ],
        // 헤드라인 — 국문 명조. .kd-display와 같은 스택.
        display: ['"Nanum Myeongjo"', '"Noto Serif KR"', "Georgia", "serif"],
        // JetBrains Mono엔 한글 글리프가 없어 로그·코드의 한글이 두부(□)가 된다.
        // 스택 끝에 Pretendard를 둬 한글만 폴백시킨다 (라틴은 그대로 모노).
        mono: [
          '"JetBrains Mono"',
          "ui-monospace",
          "SF Mono",
          "Menlo",
          '"Pretendard Variable"',
          "Pretendard",
          "monospace",
        ],
      },
      fontWeight: {
        med: "510",
        emp: "590",
      },
    },
  },
  plugins: [],
};
