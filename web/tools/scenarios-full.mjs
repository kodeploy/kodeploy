// 전체 화면 사용자 흐름 검증 (모의 API).
// 페이지별 렌더 + 주요 조작이 의도대로 동작하는지, 그리고 쓰기 요청이 올바른 본문으로 나가는지.
const VP = [1536, 1024];
const s = (name, steps) => ({ name, viewport: VP, steps });

export const scenarios = [
  s("marketing", [
    { goto: "/" },            { expectText: "만든 서비스," },        { shot: "landing", full: true },
    { goto: "/how" },         { wait: 800 },                         { shot: "howto", full: true },
    { goto: "/guide" },       { wait: 800 },                         { shot: "docs", full: true },
    { goto: "/blog" },        { wait: 1000 },                        { shot: "blog", full: true },
    { goto: "/community" },   { wait: 1000 },                        { shot: "feedback", full: true },
  ]),
  s("nav", [
    { goto: "/" },
    { click: "text=이용 방법" }, { expectText: "처음이라면" },
    { click: "text=블로그" },    { wait: 800 },
    { click: "text=피드백" },    { wait: 800 },
    { click: "text=문서" },      { wait: 800 },
    { shot: "nav-end" },
  ]),
  s("apps", [
    { goto: "/apps" }, { wait: 1200 },
    { expectText: "대시보드" }, { expectText: "my-api" },
    { shot: "apps", full: true },
  ]),
  s("wizard", [
    { goto: "/deploy" }, { wait: 1200 },
    { shot: "wizard-1", full: true },
  ]),
  s("frontend-deploy", [
    { goto: "/deploy/frontend" }, { wait: 1000 },
    { shot: "frontend", full: true },
  ]),
  s("deploy-progress", [
    { goto: "/deploy/progress" }, { wait: 1500 },
    { shot: "progress", full: true },
  ]),
  s("app-tabs", [
    { goto: "/dashboard" },            { expectText: "현재 배포" },   { shot: "overview", full: true },
    { click: "text=배포 이력" },        { wait: 900 },                 { shot: "history", full: true },
    { click: "text=환경변수" },         { wait: 900 },                 { shot: "env", full: true },
    { click: "text=설정" },             { wait: 900 },                 { shot: "settings", full: true },
    { click: "text=작업 공간" },        { wait: 1800 },                { shot: "workspace", full: true },
  ]),
  s("theme", [
    { goto: "/" },
    { click: 'header button[aria-label="다크 모드로 전환"]' }, { wait: 600 },
    { expect: 'html[data-theme="dark"]' }, { shot: "landing-dark", full: true },
    { goto: "/dashboard" }, { wait: 1200 }, { shot: "overview-dark", full: true },
  ]),
];
