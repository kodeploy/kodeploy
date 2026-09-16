// AI 분석 카드 — 실패한 빌드에 붙는 진단. 배포 진행 화면과 배포 이력 상세가 같은 컴포넌트를 쓴다.
//
// 구성: 머리 줄("AI 분석" · 접기) → 제목(할 일) → 원인 한 줄 → 근거 로그 | 수정 방법 → 바닥 줄.
// 근거와 수정 방법은 카드 폭으로 배치를 고른다(.kd-diag-grid 컨테이너 쿼리) — 진행 화면처럼
// 넓으면 좌우, 배포 이력 오른쪽 칸처럼 좁으면 위아래. 화면별 prop 분기를 두지 않는다.
//
// 데이터는 core/app/deploy/build/diagnose.py의 Diagnosis JSON이다(builds.ai_analysis).
// 옛 진단은 title·대안이 없고 fix_steps가 문자열 배열이라, 읽을 때 한 모양으로 맞춘다.
//
// 진행 상태는 builds.ai_status: 실패와 같은 커밋에 "pending"이 실리고 진단이 끝나면 "done".
// 프로세스가 진단 도중 죽으면 pending이 남으므로, 마지막 갱신에서 PENDING_CAP_MS가 지나면
// 더 기다리지 않는다(폴링도 여기서 끊는다 — isDiagnosing을 폴링 조건으로 같이 쓴다).
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Check, ChevronDown, ChevronRight, ChevronUp, Loader } from "lucide-react";
import { parseDate } from "../../lib/format.js";

const PENDING_CAP_MS = 5 * 60 * 1000;

// 진단을 기다리는 중인가 — 화면 표시와 폴링 연장의 공통 기준.
export function isDiagnosing(build) {
  if (build?.status !== "failed" || build.ai_status !== "pending" || build.ai_analysis) return false;
  const at = parseDate(build.updated_at);
  return !at || Date.now() - at.getTime() < PENDING_CAP_MS;
}

// 실패는 조용히 null — 진단은 부가정보라 화면을 깨뜨리면 안 되고, 호출부가 원문으로 떨어진다.
function parseDiagnosis(raw) {
  try {
    const d = JSON.parse(raw);
    if (!d?.cause) return null;
    const steps = (list) =>
      (Array.isArray(list) ? list : [])
        .map((s) => (typeof s === "string" ? { text: s, command: "" } : { text: s?.text || "", command: s?.command || "" }))
        .filter((s) => s.text || s.command);
    return {
      title: d.title || "",
      cause: d.cause,
      evidence: d.evidence || "",
      platform: !!d.kodeploy_specific,
      steps: steps(d.fix_steps),
      altLabel: d.alternative_label || "",
      altSteps: steps(d.alternative_steps),
    };
  } catch {
    return null;
  }
}

// onShowLogs: 원본 로그로 가는 동작(이력은 로그 탭 전환, 진행 화면은 로그 칸으로 스크롤). 없으면 링크를 숨긴다.
// style: 바깥 여백 — 카드를 안 그릴 때 빈 여백만 남지 않게 호출부가 감싸지 않고 여기로 넘긴다.
export default function AiDiagnosis({ build, onShowLogs, style }) {
  const [open, setOpen] = useState(true);
  const raw = build?.ai_analysis;
  const pending = isDiagnosing(build);
  // 기다렸지만 진단이 안 붙은 경우(LLM 실패·시간 상한) — 조용히 사라지면 기다린 자리가 헛돈다.
  const missing = !raw && !pending && build?.status === "failed" && !!build.ai_status;
  if (!raw && !pending && !missing) return null;

  const d = raw ? parseDiagnosis(raw) : null;
  const repoHref = build.repo_url ? build.repo_url.replace(/\.git$/, "") : null;

  return (
    <section className="kd-card kd-diag" style={{ padding: "18px 22px 20px", ...style }} aria-label="AI 분석">
      <div className="flex items-center justify-between" style={{ gap: 12 }}>
        <span className="kd-t-label text-fg-2">AI 분석</span>
        {d && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="kd-t-label text-fg-3 hover:text-fg-1 inline-flex items-center transition-colors"
            style={{ gap: 4 }}
          >
            {open ? "접기" : "펼치기"}
            {open ? <ChevronUp size={15} strokeWidth={1.8} /> : <ChevronDown size={15} strokeWidth={1.8} />}
          </button>
        )}
      </div>

      {pending && (
        <p className="kd-t-body-s text-fg-2 inline-flex items-center" style={{ gap: 8, marginTop: 10 }}>
          <Loader size={15} strokeWidth={1.8} className="kd-spin text-fg-3" />
          로그를 읽고 원인을 찾고 있어요.
        </p>
      )}

      {missing && (
        <p className="kd-t-body-s text-fg-3" style={{ marginTop: 10 }}>
          이번 실패는 AI 분석을 만들지 못했어요. 빌드 로그에서 멈춘 지점을 확인해 주세요.
        </p>
      )}

      {/* 옛 포맷·잘린 JSON — 원문이라도 보여준다 */}
      {raw && !d && (
        <p className="kd-t-body-s text-fg-2" style={{ marginTop: 10, whiteSpace: "pre-wrap" }}>
          {raw}
        </p>
      )}

      {d && open && (
        <>
          <div style={{ marginTop: 10 }}>
            <div className="flex items-baseline flex-wrap" style={{ gap: 10 }}>
              <h3 className="kd-t-subtitle text-fg-1" style={{ wordBreak: "keep-all" }}>
                {d.title || d.cause}
              </h3>
              {d.platform && <span className="kd-chip">플랫폼 제약</span>}
            </div>
            {/* 옛 진단은 제목이 없어 원인을 제목으로 올렸다 — 같은 문장을 두 번 쓰지 않는다 */}
            {d.title && (
              <p className="kd-t-body-s text-fg-2" style={{ marginTop: 4, wordBreak: "keep-all" }}>
                {d.cause}
              </p>
            )}
          </div>

          <div className="kd-diag-grid" style={{ marginTop: 18 }}>
            {d.evidence && (
              <div className="kd-diag-col min-w-0">
                <div className="flex items-center justify-between" style={{ gap: 12, minHeight: 22 }}>
                  <span className="kd-t-label text-fg-1">근거 로그</span>
                  {onShowLogs && (
                    <button
                      type="button"
                      onClick={onShowLogs}
                      className="kd-t-caption text-fg-2 hover:text-fg-1 underline transition-colors"
                      style={{ textUnderlineOffset: 3 }}
                    >
                      원본 로그 보기
                    </button>
                  )}
                </div>
                <pre className="kd-diag-code kd-t-code" style={{ marginTop: 8 }}>
                  {d.evidence}
                </pre>
              </div>
            )}

            {d.steps.length > 0 && (
              <div className="kd-diag-col min-w-0">
                <div className="flex items-center" style={{ minHeight: 22 }}>
                  <span className="kd-t-label text-fg-1">수정 방법</span>
                </div>
                <Steps steps={d.steps} />
                {d.altLabel && d.altSteps.length > 0 && (
                  <Alternative label={d.altLabel} steps={d.altSteps} />
                )}
              </div>
            )}
          </div>

          <div
            className="flex items-center justify-between flex-wrap"
            style={{ gap: "8px 16px", marginTop: 18 }}
          >
            {repoHref ? (
              <a
                href={repoHref}
                target="_blank"
                rel="noopener noreferrer"
                className="kd-t-label text-fg-1 underline inline-flex items-center"
                style={{ gap: 4, textUnderlineOffset: 3 }}
              >
                저장소 열기
                <ArrowUpRight size={15} strokeWidth={1.7} />
              </a>
            ) : (
              <span />
            )}
            <span className="kd-t-caption text-fg-3">로그를 바탕으로 한 AI 추정이라 실제 원인과 다를 수 있어요.</span>
          </div>
        </>
      )}
    </section>
  );
}

// 단계 목록 — 하나면 번호 없이, 여럿이면 번호. 명령어는 문장 아래 복사 박스로.
function Steps({ steps }) {
  const numbered = steps.length > 1;
  return (
    <ol style={{ marginTop: 8 }}>
      {steps.map((s, i) => (
        <li key={i} className="flex" style={{ gap: 8, marginTop: i === 0 ? 0 : 12 }}>
          {numbered && <span className="kd-t-body-s text-fg-4 tabular-nums shrink-0">{i + 1}.</span>}
          <div className="min-w-0 flex-1">
            {s.text && (
              <p className="kd-t-body-s text-fg-2" style={{ wordBreak: "keep-all" }}>
                {s.text}
              </p>
            )}
            {s.command && <CommandBox command={s.command} />}
          </div>
        </li>
      ))}
    </ol>
  );
}

// 명령어 박스 + 복사. 클립보드가 막힌 환경(HTTP·권한 없음)에서는 조용히 넘어간다(guide/atoms와 같은 방침).
function CommandBox({ command }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 차단 — silent
    }
  };

  return (
    <div className="kd-diag-code flex items-start" style={{ marginTop: 8, gap: 12 }}>
      <pre className="kd-t-code min-w-0 flex-1 overflow-x-auto scroll-thin" style={{ whiteSpace: "pre" }}>
        {command}
      </pre>
      <button
        type="button"
        onClick={copy}
        className="kd-t-caption shrink-0 inline-flex items-center text-fg-2 hover:text-fg-1 transition-colors"
        style={{ gap: 4 }}
        aria-label="명령어 복사"
      >
        {copied && <Check size={14} strokeWidth={2} />}
        {copied ? "복사됨" : "복사"}
      </button>
    </div>
  );
}

// 다른 경로의 방법 — 기본은 접어 둔다(주 방법이 먼저 읽혀야 한다).
function Alternative({ label, steps }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 14 }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="kd-t-label text-fg-2 hover:text-fg-1 inline-flex items-center transition-colors"
        style={{ gap: 6 }}
      >
        {open ? <ChevronDown size={15} strokeWidth={1.8} /> : <ChevronRight size={15} strokeWidth={1.8} />}
        {label}
      </button>
      {open && (
        <div style={{ paddingLeft: 21 }}>
          <Steps steps={steps} />
        </div>
      )}
    </div>
  );
}
