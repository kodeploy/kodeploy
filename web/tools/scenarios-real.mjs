// 실서버 연동 검증 — API를 가로채지 않고 운영 백엔드(api.kodeploy.com)로 직접 보낸다.
// 로그인 세션이 없으므로 공개 엔드포인트만 실제로 확인할 수 있다.
// 인증이 필요한 화면은 401 → 홈 리다이렉트가 "의도대로" 동작하는지까지가 확인 범위다.
export const scenarios = [
  {
    name: "real-public",
    viewport: [1536, 1024],
    steps: [
      { goto: "/" },
      { expectText: "GitHub 주소 하나로" },
      { shot: "landing" },
      { goto: "/blog" },
      { wait: 2500 },
      { shot: "blog" },
      { goto: "/community" },
      { wait: 2000 },
      { shot: "feedback" },
      { goto: "/guide" },
      { wait: 1500 },
      { shot: "docs" },
      { goto: "/how" },
      { wait: 1200 },
      { shot: "howto" },
    ],
  },
  {
    name: "real-authwall",
    viewport: [1536, 1024],
    steps: [
      // 미로그인으로 앱 화면에 들어가면 로그인 모달 + 홈 이동이 되어야 한다
      { goto: "/dashboard" },
      { wait: 2000 },
      { shot: "dashboard-unauth" },
    ],
  },
];
