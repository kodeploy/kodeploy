// 테마 전환 스모크 — 토글 클릭 후 다크가 실제로 적용되는지.
export const scenarios = [
  {
    name: "theme",
    viewport: [1536, 1024],
    steps: [
      { goto: "/dashboard" },
      { shot: "light" },
      { click: 'header button[aria-label="다크 모드로 전환"]' },
      { wait: 500 },
      { expect: 'html[data-theme="dark"]' },
      { shot: "dark" },
      { click: 'header button[aria-label="라이트 모드로 전환"]' },
      { wait: 400 },
      { expect: 'html[data-theme="light"]' },
    ],
  },
];
