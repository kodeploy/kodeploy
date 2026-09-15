// xterm.js 색 테마 — xterm은 canvas 렌더라 CSS 변수(var(--…))를 파싱하지 못한다.
// 따라서 실제 hex 문자열을 줘야 한다. 값은 index.css의 --term-bg/--term-fg와 같은 의미.
// 터미널 컴포넌트는 useTheme()으로 현재 테마를 읽어 이 함수로 객체를 만들고,
// 토글 시 term.options.theme에 다시 할당해 (재생성/재접속 없이) 색만 라이브 갱신한다.
//
// 두 테마 모두 "잉크 면"이지만 기준이 반대다:
//   라이트 — 흰 카드 위에 올라앉은 #242424 블록 (시안의 터미널 색)
//   다크   — 카드(#1a1a18)보다 더 내려앉은 면으로 눌러 같은 위계를 유지
export function xtermTheme(theme) {
  if (theme === "dark") {
    return {
      background: "#101010", // --term-bg
      foreground: "#e8e9ea", // --term-fg
      cursor: "#e8e9ea",
      selectionBackground: "rgba(232,233,234,0.25)",
    };
  }
  return {
    background: "#242424", // --term-bg
    foreground: "#f4f4ef", // --term-fg
    cursor: "#f4f4ef",
    selectionBackground: "rgba(244,244,239,0.25)",
  };
}
