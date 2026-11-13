// 터미널 WebSocket — core에서 1회용 티켓을 받아 그 테넌트의 kodeploy-exec 공개 문에 바로 붙는다.
// (core가 직접 중계하던 옛 경로는 없다. core에는 pods/exec 권한이 없다.)
//
// 프레임: 바이너리, 첫 바이트가 채널 번호
//   0 stdin, 1 stdout, 2 stderr, 3 종료 상태 JSON, 4 창 크기 {"Width","Height"}
// 바이트는 디코딩하지 않고 Uint8Array 그대로 xterm에 쓴다 (xterm이 조각난 UTF-8도 이어 붙인다).
import { createExecTicket, hasActiveApp } from "../api/deploy.js";

const CH_STDIN = 0;
const CH_STDOUT = 1;
const CH_STDERR = 2;
const CH_STATUS = 3;
const CH_RESIZE = 4;

// exec close 코드 → 터미널에 보여 줄 말
const CLOSE_TEXT = {
  1003: "프로토콜 오류",
  1011: "exec를 시작하지 못했어요",
  4400: "입력이 너무 길어요",
  4401: "티켓이 만료됐거나 이미 쓰였어요 — 다시 열어 주세요",
  4403: "허용되지 않은 접근이에요",
  4404: "대상 Pod가 없어요 — 실행 중인지 확인해 주세요",
  4408: "오래 입력이 없어 연결을 닫았어요",
  4413: "최대 연결 시간이 지났어요",
  4429: "동시에 열 수 있는 터미널 수를 넘었어요",
  4503: "exec 서비스가 재시작 중이에요 — 잠시 뒤 다시 열어 주세요",
};

const enc = new TextEncoder();
const dec = new TextDecoder();

function frame(ch, bytes) {
  const b = new Uint8Array(bytes.length + 1);
  b[0] = ch;
  b.set(bytes, 1);
  return b;
}

// 채널 3 Status에서 exit code (K8s 모양: details.causes[reason=ExitCode])
function exitCode(payload) {
  try {
    const s = JSON.parse(dec.decode(payload));
    if (s.status === "Success") return 0;
    const c = (s.details?.causes || []).find((x) => x.reason === "ExitCode");
    return c ? Number(c.message) : 1;
  } catch {
    return null;
  }
}

// term: xterm 인스턴스. target: app | db | redis.
// onOpen() · onClose(reason) · onError(message) — 화면 문구는 호출하는 쪽이 쓴다.
export function openTerminalSocket({ term, target, onOpen, onClose, onError }) {
  let ws = null;
  let disposed = false;
  let lastExit = null;

  const ready = () => ws && ws.readyState === WebSocket.OPEN;

  const resize = () => {
    if (!ready()) return;
    ws.send(frame(CH_RESIZE, enc.encode(JSON.stringify({ Width: term.cols, Height: term.rows }))));
  };

  const input = (data) => {
    if (!ready()) return;
    ws.send(frame(CH_STDIN, enc.encode(data)));
  };

  const attach = (socket) => {
    ws = socket;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      onOpen?.();
      resize();
    };
    ws.onmessage = (e) => {
      const b = new Uint8Array(e.data);
      if (b.length === 0) return;
      const payload = b.subarray(1);
      if (b[0] === CH_STDOUT || b[0] === CH_STDERR) term.write(payload);
      else if (b[0] === CH_STATUS) lastExit = exitCode(payload);
    };
    ws.onclose = (e) => {
      const parts = [];
      if (CLOSE_TEXT[e.code]) parts.push(CLOSE_TEXT[e.code]);
      if (lastExit) parts.push(`exit ${lastExit}`);
      onClose?.(parts.join(" · "));
    };
    ws.onerror = () => onError?.();
  };

  (async () => {
    // 티켓은 앱마다 받는다 — 앱 화면(/apps/:appId/...) 밖에서는 열 수 없다
    if (!hasActiveApp()) {
      onError?.("앱 화면에서 열어 주세요");
      return;
    }
    try {
      const t = await createExecTicket(target, term.cols, term.rows);
      if (disposed) return;
      attach(new WebSocket(t.url, [t.protocol, `ticket.${t.ticket}`]));
    } catch (err) {
      if (!disposed) onError?.(err.message);
    }
  })();

  return {
    input,
    resize,
    close() {
      disposed = true;
      ws?.close();
    },
  };
}
