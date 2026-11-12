import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { TERM_FONT_FAMILY, xtermTheme } from "../../lib/xtermTheme.js";
import { useTheme } from "../../contexts/ThemeContext.jsx";

import { openTerminalSocket } from "../../lib/terminalSocket.js";

// bare=true면 자체 헤더를 숨김 — DbConsolePanel의 [표|터미널] 토글 안에 끼울 때 헤더 중복 방지.
export default function DbTerminalPanel({ bare = false }) {
  const { theme } = useTheme();
  const containerRef = useRef(null);
  const termRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: TERM_FONT_FAMILY,
      theme: xtermTheme(theme),
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    // 웹폰트가 아직 안 붙은 상태로 open()하면 xterm이 대체 글꼴로 글자 폭을 재서 격자가
    // 어긋난다(한 칸 폭이 실제와 다르면 mysql 표 테두리가 깨져 보인다). 폰트가 준비되면
    // 한 번 더 재는 것으로 맞춘다 — dispose 뒤에 늦게 도착할 수 있어 플래그로 막는다.
    let disposed = false;
    document.fonts?.ready.then(() => {
      if (!disposed) fitAddon.fit();
    });

    // 옛 경로(core 중계)와 새 경로(kodeploy-exec, 플래그)를 같은 모양으로 연다 — lib/terminalSocket.js.
    // 연결되면 현재 xterm 크기를 보내 파드 pty 크기를 맞춤 → mysql 등이 실제 폭으로 출력 정렬
    const sock = openTerminalSocket({
      term,
      target: "db",
      legacyPath: "/deploy/app/db-terminal",
      onOpen: () => term.write("\x1b[1;34mDB 연결됨\x1b[0m\r\n"),
      onClose: (reason) => term.write(`\r\n\x1b[1;31m연결 종료${reason ? `: ${reason}` : ""}\x1b[0m\r\n`),
      onError: (message) => term.write(`\r\n\x1b[1;31m연결 실패${message ? `: ${message}` : ""}\x1b[0m\r\n`),
    });
    term.onData((data) => sock.input(data));
    term.onResize(() => sock.resize());

    const ro = new ResizeObserver(() => fitAddon.fit());
    ro.observe(containerRef.current);

    return () => {
      disposed = true;
      ro.disconnect();
      sock.close();
      term.dispose();
    };
  }, []);

  // 테마 토글 시 색만 라이브 갱신 (재생성/재접속 없이)
  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = xtermTheme(theme);
  }, [theme]);


  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {!bare && (
        <div
          className="flex items-center gap-2 px-4 py-1.5 shrink-0"
          style={{ borderBottom: "1px solid var(--line-2)" }}
        >
          <span className="text-[11px] text-fg-3" style={{ fontWeight: 510 }}>
            DB 터미널
          </span>
          <span className="text-[10px] text-fg-4" style={{ fontWeight: 450 }}>
            mysql / psql 쉘
          </span>
        </div>
      )}
      <div ref={containerRef} className="flex-1 min-h-0 p-1" style={{ background: "var(--kd-bg)" }} />
    </div>
  );
}
