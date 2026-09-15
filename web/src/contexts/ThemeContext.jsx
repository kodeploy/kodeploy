// 테마(light/dark) 전역 컨텍스트.
// - 초기값은 <html data-theme>에서 읽는다 — index.html의 인라인 스크립트가 React 마운트 전에
//   localStorage(또는 OS 선호)를 보고 미리 박아 FOUC(깜빡임)를 막는다.
// - toggle()이 data-theme 속성 + localStorage + state를 한 번에 갱신.
//   CSS 변수(index.css)가 data-theme로 분기하므로 리렌더 없이 색이 바뀐다.
// - 기본은 light(종이) — 시안이 전부 라이트다.
import { createContext, useCallback, useContext, useState } from "react";

const ThemeContext = createContext(null);

export const THEME_STORAGE_KEY = "kd-theme";

function currentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(currentTheme);

  const apply = useCallback((next) => {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // 시크릿 모드 등 localStorage 불가 — 세션 한정으로만 적용
    }
    setTheme(next);
  }, []);

  const toggle = useCallback(() => {
    apply(currentTheme() === "dark" ? "light" : "dark");
  }, [apply]);

  return (
    <ThemeContext.Provider value={{ theme, toggle, setTheme: apply }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme는 ThemeProvider 안에서만 사용");
  return ctx;
}
