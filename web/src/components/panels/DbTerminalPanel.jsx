import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { xtermTheme } from "../../lib/xtermTheme.js";
import { useTheme } from "../../contexts/ThemeContext.jsx";

const API_BASE = (import.meta.env.VITE_API_BASE || "").replace(/\/$/, "");
const WS_URL = API_BASE.replace(/^http/, "ws") + "/deploy/app/db-terminal";

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
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      theme: xtermTheme(theme),
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    const ws = new WebSocket(WS_URL);

    // 현재 xterm 크기를 백엔드로 보내 파드 pty 크기를 맞춤 → mysql 등이 실제 폭으로 출력 정렬
    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ __resize: { cols: term.cols, rows: term.rows } }));
      }
    };

    ws.onopen = () => {
      term.write("\x1b[1;34mDB 연결됨\x1b[0m\r\n");
      sendResize();
    };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = (e) => term.write(`\r\n\x1b[1;31m연결 종료${e.reason ? `: ${e.reason}` : ""}\x1b[0m\r\n`);
    ws.onerror = () => term.write("\r\n\x1b[1;31m연결 실패\x1b[0m\r\n");
    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });
    term.onResize(() => sendResize());

    const ro = new ResizeObserver(() => fitAddon.fit());
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      ws.close();
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
