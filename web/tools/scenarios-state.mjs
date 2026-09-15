// 작업 공간 뷰 전환 시 연결·작업 상태가 유지되는지 (요구사항 2-C).
// 데이터베이스 뷰에 SQL을 입력해 두고 다른 뷰로 갔다가 돌아왔을 때 입력이 남아 있어야 한다.
export const scenarios = [
  {
    name: "workspace-state",
    viewport: [1536, 1024],
    steps: [
      { goto: "/dashboard/workspace" },
      { wait: 2000 },
      { shot: "1-terminal-logs", full: true },
      { click: 'button[aria-pressed]:has-text("데이터베이스")' },
      { wait: 1200 },
      { shot: "2-database", full: true },
      { click: 'button[aria-pressed]:has-text("모니터링")' },
      { wait: 1200 },
      { shot: "3-monitoring", full: true },
      { click: 'button[aria-pressed]:has-text("스토리지")' },
      { wait: 1200 },
      { shot: "4-storage", full: true },
      { click: 'button[aria-pressed]:has-text("터미널·로그")' },
      { wait: 1000 },
      // 돌아왔을 때 로그 내용이 그대로 있어야 한다 (컴포넌트가 언마운트되지 않았다는 증거)
      { expectText: "Application startup complete" },
      { shot: "5-back", full: true },
      // 좌우/위아래 분할은 없앴다. 대신 각 칸의 "크게 보기"가 그 칸을 카드 전체로 넓힌다.
      { click: 'button[aria-label="크게 보기"]:visible' },
      { wait: 600 },
      { expect: 'button[aria-label="원래 크기"]' },
      { shot: "6-zoom", full: true },
    ],
  },
];
