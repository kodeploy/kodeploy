// 25개 시안 화면에 1:1로 대응하는 캡처 목록. 파일명이 시안 번호와 맞는다.
// THEME=dark 로 돌리면 다크 시안과, 안 주면 라이트 시안과 대조할 수 있다.
const VP = [1536, 1024];
const LANDING_VP = [1024, 1536];

const one = (name, route, steps = [], vp = VP, full = false) => ({
  name, viewport: vp,
  steps: [{ goto: route }, { wait: 1600 }, ...steps, { shot: "view", full }],
});

export const scenarios = [
  one("01_랜딩", "/", [], LANDING_VP, true),
  // 로그인 모달은 미로그인 상태에서만 열 수 있다
  { name: "02_로그인_모달", viewport: VP, anon: true, steps: [
    { goto: "/" }, { wait: 1200 }, { click: "text=로그인" }, { wait: 800 }, { shot: "view" } ]},
  one("03_내_앱_목록", "/apps", [], VP, true),
  one("04_배포_1단계_저장소", "/deploy", [], VP, true),
  { name: "05_배포_2단계_설정", viewport: VP, steps: [
    { goto: "/deploy" }, { wait: 1600 }, { click: "text=다음: 실행 환경" }, { wait: 1000 }, { shot: "view", full: true } ]},
  { name: "06_배포_3단계_확인", viewport: VP, steps: [
    { goto: "/deploy" }, { wait: 1600 }, { click: "text=다음: 실행 환경" }, { wait: 900 },
    { click: "text=다음: 최종 확인" }, { wait: 900 }, { shot: "view", full: true } ]},
  one("07_배포_진행", "/deploy/progress", [], VP, true),
  one("08_프론트엔드_배포", "/deploy/frontend", [], VP, true),
  one("09_앱_개요", "/dashboard", [], VP, true),
  one("10_작업공간_터미널_로그", "/dashboard/workspace", [{ wait: 1200 }], VP, true),
  { name: "11_데이터베이스", viewport: VP, steps: [
    { goto: "/dashboard/workspace" }, { wait: 2200 }, { click: "text=데이터베이스" }, { wait: 1200 }, { shot: "view", full: true } ]},
  { name: "12_스토리지", viewport: VP, steps: [
    { goto: "/dashboard/workspace" }, { wait: 2200 }, { click: "text=스토리지" }, { wait: 1400 }, { shot: "view", full: true } ]},
  { name: "13_모니터링", viewport: VP, steps: [
    { goto: "/dashboard/workspace" }, { wait: 2200 }, { click: "text=모니터링" }, { wait: 1600 }, { shot: "view", full: true } ]},
  one("14_배포_이력", "/dashboard/history", [], VP, true),
  one("15_환경변수", "/dashboard/env", [], VP, true),
  { name: "16_앱_정보_드로어", viewport: VP, steps: [
    { goto: "/dashboard" }, { wait: 1600 }, { click: "text=앱 정보" }, { wait: 700 }, { shot: "view" } ]},
  one("17_설정", "/dashboard/settings", [], VP, true),
  one("19_이용방법", "/how", [], VP, true),
  one("20_문서_터미널_기본", "/guide", [], VP, true),
  one("22_블로그_목록", "/blog", [], VP, true),
  one("25_피드백", "/community", [], VP, true),
  // 시안에는 없지만 같은 디자인 언어로 맞춘 화면
  one("27_관리자", "/admin", [{ wait: 600 }], VP, true),
];
