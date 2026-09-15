// 랜딩 예시 카드의 4개 뷰(터미널·로그 / 데이터베이스 / 스토리지 / 모니터링)와
// 각 뷰 안의 조작(표|터미널 전환 · 파일 종류별 미리보기 · 크게 보기),
// 그리고 실제 작업 공간의 스토리지 미리보기.
export const scenarios = [
  { name: "excard", viewport: [1440, 1100], steps: [
    { goto: "/" }, { wait: 1200 },
    { expectText: "배포한 다음도" }, { shot: "1-termlog" },
    { click: 'button[aria-pressed] >> text="데이터베이스"' }, { wait: 500 }, { expectText: "3행 · 12ms" }, { shot: "2-db" },
    // 표 | 터미널 토글이 실제로 동작하는지
    { click: 'button[aria-pressed]:text-is("터미널")' }, { wait: 400 },
    { expectText: "psql (16.3)" }, { shot: "2b-db-term" },
    { click: 'button[aria-pressed]:text-is("표")' }, { wait: 400 },
    { click: 'button[aria-pressed] >> text="스토리지"' }, { wait: 500 }, { expectText: "cover.png" }, { shot: "3-storage" },
    { click: "text=크게 보기" }, { wait: 500 }, { expectText: "원래 크기" }, { shot: "4-storage-big" },
    { click: 'button[aria-pressed]:has-text("config.json")' }, { wait: 400 },
    { expectText: '"runtime": "python"' }, { shot: "5-storage-json" },
    { click: 'button[aria-pressed]:has-text("report.pdf")' }, { wait: 400 }, { shot: "6-storage-pdf" },
    { click: 'button[aria-pressed] >> text="모니터링"' }, { wait: 500 },
    { expectText: "자동 갱신 30초" }, { shot: "7-metrics" },
    { click: 'button[aria-pressed]:has-text("메모리")' }, { wait: 400 }, { shot: "8-metrics-mem" },
  ]},
  { name: "wsstorage", viewport: [1536, 1024], steps: [
    { goto: "/dashboard/workspace" }, { wait: 2000 },
    { click: 'button[aria-pressed]:has-text("스토리지")' }, { wait: 1500 }, { shot: "1-normal", full: true },
    { click: 'button[aria-label="크게 보기"]:visible' }, { wait: 600 }, { shot: "2-big", full: true },
    // 사진 말고도 브라우저가 열 수 있는 형식은 미리보기가 뜬다(svg · json · pdf)
    { click: 'button:has-text("logo.svg")' }, { wait: 600 }, { shot: "3-svg", full: true },
    { click: 'button:has-text("config.json")' }, { wait: 900 }, { shot: "4-json", full: true },
    { click: 'button:has-text("report.pdf")' }, { wait: 1500 }, { shot: "5-pdf", full: true },
  ]},
];
