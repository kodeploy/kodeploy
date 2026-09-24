// 배포 진행 화면 — /deploy/progress. design/라이트모드-시안/07_배포_진행.png 기준.
//
// 배포 폼에서 "배포"를 누른 직후 사용자가 머무는 자리다. 좌우 레일 없이 가운데 한 줄로
// 세운다: 제목 → 4단계 트래커 → 빌드 로그(잉크 면) → 상태 한 줄 → (실패면 AI 분석) → 바닥 이동 링크.
// 앱 상세(AppLayout)처럼 탭·사이드가 없는 이유는 이 화면에 할 일이 "기다리기" 하나뿐이라서다.
//
// 진행률은 만들지 않는다. 백엔드가 주는 진행 정보는 build.status 하나뿐이고(schemas.StatusResponse),
// 퍼센트·ETA 같은 건 존재하지 않는다. 그래서 트래커는 status → 단계 인덱스 매핑만 한다.
//   queued→1 / building→2 / built·deploying→3 / running→4(완료)
// failed는 어느 단계에서 멈췄는지 기록에 없다. 이 화면이 지켜보는 동안 본 최고 단계를
// 기억해 두었다가(highWater) 거기에 실패 표시를 찍고, 이미 실패한 빌드로 들어와
// 본 적이 없으면 단계를 찍지 않는다(추측하느니 비워 둔다).
//
// 폴링은 기존 규칙 그대로다 — 목록은 활성 빌드가 있으면 2.5s 아니면 8s(AppLayout과 동일),
// 로그는 진행 중일 때만 1s(CommitListPanel.BuildLogsPanel / app/BuildDetail과 동일).
// 실패한 뒤 AI 분석을 기다리는 동안(ai_status="pending")은 2s로 이어 가 분석 카드를 채운다.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.3667(상단바 괘선 82÷60)한 CSS px이다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, Maximize2, Minimize2, X } from "lucide-react";
import { getBuild, listBuilds } from "../../api/deploy.js";
import AiDiagnosis, { isDiagnosing } from "../app/AiDiagnosis.jsx";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { parseDate } from "../../lib/format.js";

// 빌드가 진행 중인(로그가 계속 늘어나는) 상태들 — 목록 폴링 주기와 로그 폴링 on/off의 기준.
const ACTIVE_BUILD = new Set(["queued", "building", "built", "deploying"]);

// 시안의 4단계. 라벨 문구는 시안 그대로다.
const STEPS = ["소스 가져오기", "이미지 빌드", "앱 시작", "완료"];

// status → 도달한 단계(1~4). 이 매핑이 이 화면의 유일한 진행 정보다.
// cancelled·failed는 "몇 번째에서 멈췄나"를 백엔드가 남기지 않으므로 null.
function stageOf(status) {
  if (status === "queued") return 1;
  if (status === "building") return 2;
  if (status === "built" || status === "deploying") return 3;
  if (status === "running") return 4;
  return null;
}

// 에러 줄 강조 — app/BuildDetail.jsx와 같은 규칙(명시적 표지가 있는 줄만 붉게).
const ERROR_LINE = /\b(ERROR|FATAL|Traceback)\b|\berror:/i;

// 초 → "00:24" / "1:02:33". 시안의 "경과 00:24" 표기(mm:ss)라 lib/format.formatDuration
// ("24.0초")을 그대로 쓸 수 없다 — 흐르는 타이머는 자릿수가 고정돼야 눈이 안 흔들린다.
function clock(seconds) {
  const t = Math.max(0, Math.floor(seconds));
  const s = String(t % 60).padStart(2, "0");
  const m = Math.floor(t / 60) % 60;
  const h = Math.floor(t / 3600);
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${String(m).padStart(2, "0")}:${s}`;
}

// 상태 한 줄(로그 패널 아래) — 시안의 "이미지를 빌드하고 있어요." 자리.
// 실패는 백엔드가 준 build.error를 그대로 보여준다(우리가 문구를 지어내지 않는다).
function statusLine(build) {
  if (!build) return "";
  switch (build.status) {
    case "queued":
      return "차례를 기다리고 있어요.";
    case "building":
      return "이미지를 빌드하고 있어요.";
    case "built":
    case "deploying":
      return "앱을 시작하고 있어요.";
    case "running":
      return "앱이 준비됐어요. 작업 공간에서 로그와 터미널을 볼 수 있어요.";
    case "failed":
      return build.error || "빌드가 실패했어요.";
    case "cancelled":
      return "배포가 중지됐어요.";
    default:
      return "";
  }
}

export default function DeployProgress() {
  const { user, loading: authLoading, openLogin } = useAuth();
  const [params] = useSearchParams();
  const wanted = params.get("build");

  const [build, setBuild] = useState(null);
  const [loaded, setLoaded] = useState(false);      // 첫 조회가 끝났나 (빈 화면 문구 분기)
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  // 이 화면이 지켜보는 동안 실제로 본 최고 단계. 실패 표시를 찍을 자리를 여기서만 가져온다.
  const [highWater, setHighWater] = useState(0);
  const logBoxRef = useRef(null);
  const logPanelRef = useRef(null);

  const onAuthError = useCallback(
    (err) => {
      if (err.status !== 401) return false;
      openLogin?.();
      return true;
    },
    [openLogin],
  );

  const live = build ? ACTIVE_BUILD.has(build.status) : false;
  const diagnosing = isDiagnosing(build);

  // ── 목록 폴링 — 대상 빌드를 고르고 상태를 따라간다 (활성 2.5s / 안정 8s) ──
  // ?build=<id>가 있으면 그것, 없으면 가장 최근 빌드. env_change 행은 빌드가 아니라
  // (로그도 단계도 없다) 자동 선택에서 뺀다.
  useEffect(() => {
    if (authLoading || !user) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const list = await listBuilds();
        if (cancelled) return;
        const target = wanted
          ? list.find((b) => b.build_id === wanted)
          : list.find((b) => (b.kind || "build") !== "env_change");
        setBuild((prev) => (prev && prev.build_id === target?.build_id ? prev : target || null));
        setError(null);
        setLoaded(true);
        timer = setTimeout(tick, list.some((b) => ACTIVE_BUILD.has(b.status)) ? 2500 : 8000);
      } catch (err) {
        if (cancelled || onAuthError(err)) return;
        setError(err.message || "조회 실패");
        setLoaded(true);
        timer = setTimeout(tick, 8000);
      }
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [authLoading, user?.id, wanted, onAuthError]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── 로그 폴링 — 진행 중일 때만 1초. 종료 상태가 되면 자연 정지한다(AI 분석 대기 중이면 2초로 이어 간다). ──
  // 목록 폴링(2.5~8s)이 같은 build를 덮어쓰지만, 여기서 받은 스냅샷이 항상 더 최신이라
  // 로그가 되감기지 않는다(목록과 상세가 같은 StatusResponse다).
  const buildId = build?.build_id;
  useEffect(() => {
    if (!buildId || (!live && !diagnosing)) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const fresh = await getBuild(buildId);
        if (cancelled) return;
        setBuild(fresh);
        if (ACTIVE_BUILD.has(fresh.status)) timer = setTimeout(tick, 1000);
        else if (isDiagnosing(fresh)) timer = setTimeout(tick, 2000);
      } catch (err) {
        if (cancelled || onAuthError(err)) return;
        timer = setTimeout(tick, 1000);
      }
    };
    timer = setTimeout(tick, live ? 1000 : 2000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [buildId, live, diagnosing, onAuthError]);

  // ── 경과 타이머 — 진행 중일 때만 1초마다 현재 시각을 갱신한다(끝나면 값이 굳는다) ──
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [live]);

  // 도달 단계 기억 — 실패했을 때 "어디서 멈췄나"를 찍을 유일한 근거.
  const stage = stageOf(build?.status);
  useEffect(() => {
    if (stage) setHighWater((v) => Math.max(v, stage));
  }, [stage]);

  // 빌드가 바뀌면 기억도 리셋 — 앞 빌드의 단계를 뒷 빌드에 찍으면 거짓말이 된다.
  useEffect(() => {
    setHighWater(0);
  }, [buildId]);

  const failed = build?.status === "failed" || build?.status === "cancelled";
  const done = build?.status === "running";
  // 실패 표시를 찍을 단계 — 못 본 채로 들어왔으면 null(=아무 원에도 안 찍는다).
  const failedStage = failed ? highWater || null : null;
  const current = stage || failedStage || 0;

  // 경과 = created_at → (진행 중이면 지금 / 끝났으면 updated_at).
  const elapsed = useMemo(() => {
    const start = parseDate(build?.created_at);
    if (!start) return null;
    const end = live ? now : parseDate(build?.updated_at)?.getTime() || now;
    return (end - start.getTime()) / 1000;
  }, [build?.created_at, build?.updated_at, live, now]);

  // 진행 중(live)엔 갱신마다 맨 밑으로 붙여 tail -f처럼 흐르게 한다.
  // 끝나면 강제 스크롤을 풀어 위로 올려 읽게 둔다 — BuildDetail과 같은 규칙.
  // 실패는 로그가 바뀔 때 한 번 더 맨 밑으로 — 배포 실패면 크래시한 앱 로그가 끝에 붙는다.
  const failedNow = build?.status === "failed";
  useEffect(() => {
    const el = logBoxRef.current;
    if (el && (live || failedNow)) el.scrollTop = el.scrollHeight;
  }, [build?.logs, live, failedNow]);

  const title = done
    ? "앱이 준비됐어요."
    : failed
      ? "배포가 멈췄어요."
      : "앱을 준비하고 있어요.";
  const lead = done
    ? "작업 공간에서 로그·터미널·데이터베이스를 바로 열 수 있어요."
    : failed
      ? build?.ai_status || build?.ai_analysis
        ? "빌드 로그와 아래 AI 분석에서 멈춘 원인을 확인할 수 있어요."
        : "빌드 로그에서 멈춘 지점을 확인할 수 있어요."
      : "소스를 빌드하고 실행 환경을 준비합니다.";

  return (
    <div className="kd-page" style={{ paddingTop: 54, paddingBottom: 48 }}>
      {/* ── 제목 + 설명 (시안 y168-220 / y244-268) ── */}
      <h1 className="kd-t-display text-center" style={{ color: "var(--fg-1)" }}>
        {title}
      </h1>
      <p className="kd-t-lead text-center" style={{ color: "var(--fg-2)", marginTop: 4 }}>
        {lead}
      </p>

      {/* 경과 타이머 — 시안에서 설명 줄 바로 아래 우측 끝(x1476 = 콘텐츠 우측 끝)에 붙어 있다.
          음수 마진으로 설명 줄에 바짝 붙인다(시안 y269, 흐름대로 두면 10px 아래로 내려간다). */}
      <div
        className="kd-t-caption text-right tabular-nums"
        style={{ color: "var(--fg-3)", marginTop: -8, minHeight: 20 }}
      >
        {elapsed != null && `경과 ${clock(elapsed)}`}
      </div>

      {/* ── 4단계 트래커 (시안 원 42px=31 · 중심 x274/580/904/1220 → 간격 231 · 총폭 723) ── */}
      <div
        className="mx-auto flex items-center"
        style={{ maxWidth: 723, marginTop: 28, paddingBottom: 30 }}
      >
        {STEPS.map((label, i) => {
          const n = i + 1;
          const isFail = failedStage === n;
          const reached = !isFail && n <= current;
          const isCurrent = !isFail && n === current && !done;
          return (
            <StepMark
              key={label}
              n={n}
              label={label}
              reached={reached}
              isCurrent={isCurrent}
              isFail={isFail}
              lineBefore={i > 0}
              lineDone={n <= current}
            />
          );
        })}
      </div>

      {/* ── 빌드 로그 — 잉크 면 (시안 x84-1452 y453-838: 헤더 50=36 + 본문 335=245) ── */}
      <div
        ref={logPanelRef}
        style={{
          marginTop: 32,
          scrollMarginTop: 24,
          borderRadius: 4,
          border: "1px solid var(--kd-border)",
          background: "var(--term-bg)",
          overflow: "hidden",
        }}
      >
        {/* 면이 항상 어두우므로 안쪽을 dark 스코프로 씌운다 — 라이트 테마의 --err-fg를
            어두운 면에 그대로 쓰면 안 읽힌다(app/BuildDetail.InkFace와 같은 방법). */}
        <div data-theme="dark">
          <div
            className="flex items-center justify-between"
            style={{
              height: 36,
              paddingInline: 22,
              background: "var(--sel-soft)",
              borderBottom: "1px solid var(--sel)",
            }}
          >
            <span className="kd-t-label" style={{ color: "var(--term-fg)" }}>
              빌드 로그
            </span>
            <button
              onClick={() => setExpanded((v) => !v)}
              className="flex items-center justify-center"
              style={{ width: 24, height: 24, color: "var(--fg-2)" }}
              title={expanded ? "접기" : "펼치기"}
              aria-label={expanded ? "접기" : "펼치기"}
            >
              {expanded ? (
                <Minimize2 size={15} strokeWidth={1.8} />
              ) : (
                <Maximize2 size={15} strokeWidth={1.8} />
              )}
            </button>
          </div>
          <div
            ref={logBoxRef}
            className="scroll-thin"
            style={{ height: expanded ? "72vh" : 245, overflow: "auto", padding: "18px 22px" }}
          >
            <LogBody build={build} loaded={loaded} live={live} />
          </div>
        </div>
      </div>

      {/* ── 상태 한 줄 (시안 y861-879) ── */}
      <p
        className="kd-t-body-s"
        style={{ marginTop: 12, color: failed ? "var(--err-fg)" : "var(--fg-1)" }}
      >
        {error ? `상태를 불러오지 못했어요 - ${error}` : statusLine(build)}
      </p>

      {/* ── AI 분석 — 실패하면 여기서 바로 원인을 본다(예전엔 배포 이력으로 한 번 더 이동해야 했다).
          폭이 넓어 카드 안은 근거 로그 | 수정 방법 좌우 배치가 된다(.kd-diag-grid). ── */}
      {failed && (
        <AiDiagnosis
          build={build}
          onShowLogs={() => logPanelRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
          style={{ marginTop: 24 }}
        />
      )}

      {/* ── 바닥 — 좌 되돌아가기 / 우 다음 자리 (시안 버튼 x1240-1452 y913-967 = 156×40) ── */}
      <div className="flex items-center justify-between" style={{ marginTop: 24 }}>
        <Link
          to="/apps"
          className="kd-t-label inline-flex items-center"
          style={{ color: "var(--fg-1)", gap: 10 }}
        >
          대시보드로
        </Link>

        {/* 실패 — 원인은 위 AI 분석에서 봤으니 고친 뒤 다시 배포하는 자리.
            재배포 화면(/deploy)은 마지막 빌드 설정을 채워 열린다(사이드바 "재배포"와 같은 목적지). */}
        {failed ? (
          <div className="flex items-center flex-wrap justify-end" style={{ gap: "8px 16px" }}>
            <span className="kd-t-caption text-fg-3">수정 사항을 반영한 뒤 다시 배포해 주세요.</span>
            <Link
              to="/deploy"
              className="kd-btn-primary kd-btn-md inline-flex items-center no-underline"
              style={{ gap: 8 }}
            >
              재배포
            </Link>
          </div>
        ) : done ? (
          <Link
            to="/dashboard/workspace"
            className="kd-btn-primary kd-btn-md inline-flex items-center"
            style={{ gap: 8 }}
          >
            작업 공간 열기
          </Link>
        ) : (
          // 빌드가 끝나기 전엔 비활성 — 아직 열 앱이 없다.
          <button
            type="button"
            disabled
            className="kd-btn-primary kd-btn-md inline-flex items-center"
            style={{ gap: 8 }}
          >
            작업 공간 열기
          </button>
        )}
      </div>
    </div>
  );
}

// 트래커 한 칸 — 연결선(앞 칸과 사이) + 원 + 원 아래 라벨.
// 라벨은 absolute로 원 중심에 건다: 흐름에 두면 라벨 폭이 칸을 벌려 원 간격이 어긋난다
// (시안은 첫 원 중심 215.5에 라벨이 178.5-251.8 — 정확히 중심 정렬이다).
function StepMark({ n, label, reached, isCurrent, isFail, lineBefore, lineDone }) {
  return (
    <>
      {lineBefore && (
        <span
          className="flex-1"
          style={{
            height: 2,
            marginInline: 7,
            background: lineDone ? "var(--accent)" : "var(--kd-border)",
          }}
          aria-hidden="true"
        />
      )}
      <span className="relative shrink-0" style={{ width: 32, height: 32 }}>
        <span
          className="flex items-center justify-center rounded-full kd-t-label"
          style={{
            width: 32,
            height: 32,
            background: isFail ? "var(--err-fg)" : reached ? "var(--accent)" : "transparent",
            border: reached || isFail ? "none" : "1px solid var(--kd-border)",
            color: reached || isFail ? "var(--btn-primary-fg)" : "var(--fg-4)",
          }}
        >
          {/* 지나온 단계는 체크, 지금 단계는 번호, 남은 단계는 회색 번호 (시안 그대로) */}
          {isFail ? (
            <X size={15} strokeWidth={2.4} />
          ) : reached && !isCurrent ? (
            <Check size={15} strokeWidth={2.6} />
          ) : (
            n
          )}
        </span>
        <span
          className="kd-t-label absolute whitespace-nowrap"
          style={{
            top: 42,
            left: "50%",
            transform: "translateX(-50%)",
            color: "var(--fg-1)",
          }}
        >
          {label}
        </span>
      </span>
    </>
  );
}

// 로그 본문 — 시안엔 줄번호가 없다(잉크 면 왼쪽 끝에서 바로 텍스트가 시작한다).
function LogBody({ build, loaded, live }) {
  if (!build) {
    return (
      <p className="kd-t-code" style={{ color: "var(--fg-2)" }}>
        {loaded ? "아직 배포한 기록이 없어요." : "빌드를 불러오는 중..."}
      </p>
    );
  }
  const text = build.logs || (live ? "빌드 준비 중..." : "(로그 없음)");
  const lines = text.replace(/\s+$/, "").split("\n");
  return (
    <div className="kd-t-code">
      {lines.map((line, i) => (
        <div
          key={i}
          style={{
            color: ERROR_LINE.test(line) ? "var(--err-fg)" : "var(--term-fg)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {line || " "}
        </div>
      ))}
    </div>
  );
}
