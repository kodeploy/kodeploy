// 터미널 WebSocket — 옛 경로(core가 중계, 텍스트)와 새 경로(kodeploy-exec, 채널 프레임)를 같은 모양으로 감싼다.
// 서버 플래그 EXEC_AGENT_TERMINAL이 켜져 있으면 새 경로: core에서 티켓을 받아 exec 공개 문에 바로 붙는다.
//
// 새 경로 프레임 (exec 게이트웨이 지시서 3-3): 바이너리, 첫 바이트가 채널 번호
//   0 stdin, 1 stdout, 2 stderr, 3 종료 상태 JSON, 4 창 크기 {"Width","Height"}
// 바이트는 디코딩하지 않고 Uint8Array 그대로 xterm에 쓴다 (xterm이 조각난 UTF-8도 이어 붙인다).
import { createExecTicket, getExecFeatures, hasActiveApp, wsPath } from "../api/deploy.js";

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

// term: xterm 인스턴스. target: app | db | redis. legacyPath: 옛 경로(/deploy/app/...-terminal).
// onOpen() · onClose(reason) · onError(message) — 화면 문구는 호출하는 쪽이 지금처럼 쓴다.
export function openTerminalSocket({ term, target, legacyPath, onOpen, onClose, onError }) {
  let ws = null;
  let modern = false;
  let disposed = false;
  let lastExit = null;

  const ready = () => ws && ws.readyState === WebSocket.OPEN;

  const resize = () => {
    if (!ready()) return;
    if (modern) ws.send(frame(CH_RESIZE, enc.encode(JSON.stringify({ Width: term.cols, Height: term.rows }))));
    else ws.send(JSON.stringify({ __resize: { cols: term.cols, rows: term.rows } }));
  };

  const input = (data) => {
    if (!ready()) return;
    if (modern) ws.send(frame(CH_STDIN, enc.encode(data)));
    else ws.send(data);
  };

  const attach = (socket) => {
    ws = socket;
    ws.onopen = () => {
      onOpen?.();
      resize();
    };
    ws.onmessage = modern
      ? (e) => {
          const b = new Uint8Array(e.data);
          if (b.length === 0) return;
          const payload = b.subarray(1);
          if (b[0] === CH_STDOUT || b[0] === CH_STDERR) term.write(payload);
          else if (b[0] === CH_STATUS) lastExit = exitCode(payload);
        }
      : (e) => term.write(e.data);
    ws.onclose = (e) => {
      if (!modern) return onClose?.(e.reason);
      const parts = [];
      if (CLOSE_TEXT[e.code]) parts.push(CLOSE_TEXT[e.code]);
      if (lastExit) parts.push(`exit ${lastExit}`);
      onClose?.(parts.join(" · "));
    };
    ws.onerror = () => onError?.();
  };

  (async () => {
    const features = await getExecFeatures();
    if (disposed) return;
    if (!features.terminal || !hasActiveApp()) {
      attach(new WebSocket(wsPath(legacyPath)));
      return;
    }
    try {
      const t = await createExecTicket(target, term.cols, term.rows);
      if (disposed) return;
      modern = true;
      const socket = new WebSocket(t.url, [t.protocol, `ticket.${t.ticket}`]);
      socket.binaryType = "arraybuffer";
      attach(socket);
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
