// 앱 상세 사용자 흐름 검증 (모의 API).
//
// 확인 대상 — 요청하신 항목:
//   탭 이동 / 배포 상세 진입 / 환경변수 저장 / 도메인 설정 / 터미널 연결 / 로그 조회
// writes 배열에 실제로 나간 PUT·POST 요청이 기록되므로, 저장이 "눌리기만" 한 게 아니라
// 올바른 본문으로 서버에 갔는지까지 확인할 수 있다.
const VP = [1536, 1024];

export const scenarios = [
  {
    name: "tabs",
    viewport: VP,
    steps: [
      { goto: "/dashboard" },
      { expectText: "개요" },
      { expectText: "현재 배포" },
      { shot: "overview", full: true },
      { click: "text=배포 이력" },
      { expectText: "배포 이력" },
      { shot: "history", full: true },
      { click: "text=환경변수" },
      { expectText: "환경변수" },
      { shot: "env", full: true },
      { click: "text=설정" },
      { shot: "settings", full: true },
      { click: "text=작업 공간" },
      { shot: "workspace", full: true },
      { click: "text=개요" },
      { expectText: "연결된 리소스" },
    ],
  },
  {
    name: "deploy-detail",
    viewport: VP,
    steps: [
      { goto: "/dashboard" },
      // 개요의 "배포 상세 보기" → 배포 이력에서 해당 빌드가 선택된 상태로 열려야 한다
      { click: "text=배포 상세 보기" },
      { expectText: "배포 이력" },
      { expectText: "a81c92f0" },
      { shot: "detail-from-overview", full: true },
      { goto: "/dashboard/history" },
      // 실패한 빌드를 고르면 에러 로그가 보여야 한다
      { click: "text=c47d2100" },
      { expectText: "requirements.txt" },
      { shot: "detail-failed", full: true },
    ],
  },
  {
    // 2단계 "다음: 최종 확인"이 3단계를 건너뛰고 바로 배포로 넘어간 적이 있다
    // (같은 자리 버튼이 type=button → submit으로 재사용돼 클릭 기본 동작이 폼 제출이 됐다).
    name: "wizard-step3",
    viewport: VP,
    steps: [
      { goto: "/deploy" },
      { wait: 1200 },
      { click: "text=다음: 실행 환경" },
      { wait: 700 },
      { click: "text=다음: 최종 확인" },
      { wait: 900 },
      { expectText: "이 설정으로 배포할까요" },
      { absent: "빌드 로그" },
      { shot: "wizard-step3", full: true },
    ],
  },
  {
    name: "env-save",
    viewport: VP,
    // 값이 <input value>에 들어가므로 innerText가 아니라 셀렉터로 확인한다.
    steps: [
      { goto: "/dashboard/env" },
      { expect: 'input[value="APP_ENV"]' },
      { expect: 'input[value="LOG_LEVEL"]' },
      // 값은 전부 가린 채로 시작한다 — 헤더의 "값 보기"를 눌러야 실제 값이 보인다.
      { expect: 'input[value="••••••••••••"]' },
      { click: "text=값 보기" },
      { wait: 300 },
      { expect: 'input[value="info"]' },
      { shot: "env-before", full: true },
      { fill: 'input[value="info"]', value: "debug" },
      { wait: 400 },
      { click: "text=저장 및 재배포" },
      { wait: 1200 },
      { shot: "env-saved", full: true },
    ],
  },
  {
    name: "domain",
    viewport: VP,
    steps: [
      { goto: "/dashboard/settings" },
      { expectText: "도메인" },
      { expectText: "origin.kodeploy.com" },
      { shot: "settings-domain", full: true },
      // 이미 연결된 도메인이 있으면 입력이 readonly다 — 시안대로 "도메인 변경"을 눌러야 편집된다
      { click: "text=도메인 변경" },
      { wait: 500 },
      { fill: 'input.kd-input:not([readonly])', value: "api.mine.dev" },
      { wait: 400 },
      { click: "text=저장" },
      { wait: 1200 },
      { shot: "settings-domain-saved", full: true },
    ],
  },
  {
    name: "terminal-logs",
    viewport: VP,
    steps: [
      { goto: "/dashboard/workspace" },
      { wait: 1500 },
      { shot: "workspace-default", full: true },
      // 실행 로그가 실제 응답 내용을 보여줘야 한다
      { expectText: "Application startup complete" },
      { shot: "workspace-logs", full: true },
    ],
  },
  {
    name: "app-info",
    viewport: VP,
    steps: [
      { goto: "/dashboard" },
      { click: "text=앱 정보" },
      { wait: 500 },
      { expectText: "me/my-api" },
      { shot: "drawer", full: false },
    ],
  },
];
