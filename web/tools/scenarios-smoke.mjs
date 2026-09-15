// 현재 존재하는 화면에 대한 최소 스모크 — 하네스 자체를 검증한다.
export const scenarios = [
  {
    name: "smoke",
    viewport: [1536, 1024],
    steps: [
      { goto: "/" },
      { expectText: "만든 서비스," },
      { shot: "landing" },
      { goto: "/dashboard" },
      { expectText: "개요" },
      { expectText: "my-api" },
      { shot: "overview" },
    ],
  },
];
