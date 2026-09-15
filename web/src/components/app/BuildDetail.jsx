// 배포 이력 · 우측 상세 — 선택한 빌드 하나를 보여준다. design/라이트모드-시안/14_배포_이력.png 기준.
//
// 제목(#N 결과) → 메타 줄(브랜치 · 빌드ID · 소요) → 탭(요약 / 빌드 로그 / Dockerfile).
// 빌드 로그는 잉크 면에 줄번호와 함께 깔고, 실패한 빌드면 그 아래 AI 진단을 붙인다.
//
// 진행 중인 빌드는 로그가 계속 늘어나므로 getBuild(id)로 1초 폴링한다 —
// CommitListPanel.BuildLogsPanel이 쓰던 규칙(활성 상태 집합 · 종료되면 자연 정지 ·
// build_id 바뀔 때만 부모 스냅샷으로 리셋 · live일 때만 맨 아래로 붙이기)을 그대로 가져왔다.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.3665(시안 스케일)한 CSS px이다.
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUpRight,
  CircleCheck,
  CircleX,
  Clock,
  Diamond,
  GitBranch,
  Loader,
  Maximize2,
  Minimize2,
} from "lucide-react";
import { getBuild } from "../../api/deploy.js";
import { STYLES_BUILD, STYLES_ENV } from "../StatusBadge.jsx";
import { formatDuration, formatFull, repoSlug } from "../../lib/format.js";

// 빌드가 진행 중인(로그가 계속 늘어나는) 상태들 — 이 동안만 1초 폴링한다.
const ACTIVE_BUILD = new Set(["queued", "building", "built", "deploying"]);

// 빌드 결과 → 아이콘. 성공/실패만 색을 쓰고 진행 중은 잉크 회색(모노크롬 원칙).
// 개요 탭(AppOverview.resultIcon)과 같은 규칙 — 그쪽은 모듈 밖으로 내보내지 않아 여기에 다시 둔다.
// 목록(AppHistory)도 같은 아이콘을 써야 해서 여기서 내보낸다.
export function resultIcon(status, size = 18) {
  if (status === "running")
    return <CircleCheck size={size} strokeWidth={1.6} style={{ color: "var(--ok-fg)" }} />;
  if (status === "failed")
    return <CircleX size={size} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />;
  if (status === "cancelled" || status === "queued")
    return <CircleCheck size={size} strokeWidth={1.6} style={{ color: "var(--fg-4)" }} />;
  return (
    <Loader size={size} strokeWidth={1.6} className="kd-spin" style={{ color: "var(--fg-3)" }} />
  );
}

// 상태 라벨은 StatusBadge의 맵이 단일 진실원 — 여기서 새 문구를 만들지 않는다.
// (시안의 "배포 완료"는 STYLES_BUILD.running 값인 "성공"으로 나온다.)
export function statusLabel(build) {
  const map = (build.kind || "build") === "env_change" ? STYLES_ENV : STYLES_BUILD;
  return map[build.status]?.label || build.status;
}

const RUNTIME_LABEL = {
  python: "Python",
  java: "Java",
  php: "PHP",
  javascript: "JavaScript",
  static: "정적 사이트",
};
const DB_LABEL = { mysql: "MySQL", postgres: "PostgreSQL" };
const BUILD_MODE_LABEL = {
  dockerfile: "Dockerfile",
  auto: "자동 감지 (nixpacks)",
  static: "정적 빌드",
};

// 에러 줄 강조 — "ERROR:" 같은 명시적 표지만 잡는다. 시안에서도 ERROR 줄만 붉고
// 바로 아래 "Build failed"는 보통 글자색이다(실패 단어를 전부 칠하면 면이 붉어진다).
const ERROR_LINE = /\b(ERROR|FATAL|Traceback)\b|\berror:/i;

// 진단 JSON(core/app/deploy/build/diagnose.py Diagnosis) 파싱.
// 실패는 조용히 null — 진단은 부가정보라 화면을 깨뜨리면 안 되고, 호출부가 원문으로 떨어진다.
// (CommitListPanel.parseDiagnosis와 같은 규칙)
function parseDiagnosis(raw) {
  try {
    const d = JSON.parse(raw);
    return d?.cause ? d : null;
  } catch {
    return null;
  }
}

export default function BuildDetail({ build: initialBuild, number }) {
  const [build, setBuild] = useState(initialBuild);
  const [tab, setTab] = useState(() => defaultTab(initialBuild));
  const [expanded, setExpanded] = useState(false);
  const logBoxRef = useRef(null);

  const isEnv = (build.kind || "build") === "env_change";
  const failed = build.status === "failed";
  const live = ACTIVE_BUILD.has(build.status);

  // 부모(AppLayout)가 새 스냅샷을 넘기면 반영 — build_id 바뀔 때만.
  // 매 렌더 동기화하면 2.5~8초짜리 목록 폴링이 1초짜리 상세 폴링을 덮어써 로그가 되감긴다.
  // (의존성은 build_id만 — 객체 자체를 넣으면 폴링 스냅샷마다 되돌아간다)
  useEffect(() => {
    setBuild(initialBuild);
  }, [initialBuild.build_id]);

  // 진행 중 빌드면 1초마다 getBuild로 갱신. 종료 상태가 되면 자연 정지.
  useEffect(() => {
    if (!ACTIVE_BUILD.has(initialBuild.status)) return;
    let cancelled = false;
    let timer;
    const tick = async () => {
      try {
        const fresh = await getBuild(initialBuild.build_id);
        if (cancelled) return;
        setBuild(fresh);
        if (ACTIVE_BUILD.has(fresh.status)) timer = setTimeout(tick, 1000);
      } catch {
        if (!cancelled) timer = setTimeout(tick, 1000);
      }
    };
    timer = setTimeout(tick, 1000);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [initialBuild.build_id, initialBuild.status]);

  // 탭 구성 — 로그/Dockerfile은 내용이 있을 때만 연다.
  // env_change는 빌드가 아니라 로그·Dockerfile 자체가 없다.
  const tabs = useMemo(() => {
    const t = [{ id: "summary", label: "요약" }];
    if (isEnv) return t; // 환경변수 변경은 빌드가 아니라 로그도 Dockerfile도 없다
    t.push({ id: "logs", label: "빌드 로그" });
    if (build.dockerfile_content) t.push({ id: "dockerfile", label: "Dockerfile" });
    return t;
  }, [isEnv, build.dockerfile_content]);
  const active = tabs.some((t) => t.id === tab) ? tab : "summary";

  // 진행 중(live)엔 갱신마다 맨 밑으로 붙여 tail -f처럼 흐르게 한다.
  // 끝나면 강제 스크롤을 풀어 사용자가 위로 올려 읽을 수 있게 둔다(에러는 보통 맨 끝).
  useEffect(() => {
    const el = logBoxRef.current;
    if (el && live && active === "logs") el.scrollTop = el.scrollHeight;
  }, [build.logs, live, active]);

  const durationText = formatDuration(build.total_seconds);

  return (
    <div>
      {/* ── 제목 — "#17 빌드 실패" (시안 y340, 번호와 결과 사이 14) ── */}
      <h2 className="kd-t-subtitle flex items-baseline flex-wrap" style={{ gap: 15 }}>
        {!isEnv && <span className="text-fg-1 tabular-nums">#{number}</span>}
        <span style={{ color: failed ? "var(--err-fg)" : "var(--fg-1)" }}>
          {isEnv ? "환경변수 변경" : statusLabel(build)}
        </span>
      </h2>

      {/* ── 메타 줄 — 높이 var(--row-lg)=42(시안 60 → 44), 그룹 사이 세로 구분선 (시안 x801 · x966) ── */}
      <div
        className="flex items-center flex-wrap"
        style={{
          minHeight: "var(--row-lg)",
          gap: 24,
          marginTop: 14,
          borderTop: "1px solid var(--kd-border)",
          borderBottom: "1px solid var(--kd-border)",
        }}
      >
        {isEnv ? (
          <>
            <Meta icon={Diamond}>{build.build_id}</Meta>
            <MetaDivider />
            <Meta icon={Clock}>{formatFull(build.created_at)}</Meta>
          </>
        ) : (
          <>
            <Meta icon={GitBranch}>{build.branch || "—"}</Meta>
            <MetaDivider />
            <Meta icon={Diamond}>{build.build_id}</Meta>
            {durationText && (
              <>
                <MetaDivider />
                <Meta icon={Clock}>{durationText}</Meta>
              </>
            )}
          </>
        )}
      </div>

      {/* ── 탭 — 높이 48 = --tabbar-h (시안 67 → 49). 첫 탭(요약) 뒤에만 세로 구분선 (시안 x771) ── */}
      <div
        className="flex items-center"
        style={{ gap: 8, borderBottom: "1px solid var(--kd-border)" }}
      >
        {tabs.map((t, i) => (
          <Fragment key={t.id}>
            {i === 1 && (
              <span
                style={{ width: 1, height: 18, background: "var(--kd-border)" }}
                aria-hidden="true"
              />
            )}
            <button
              onClick={() => setTab(t.id)}
              aria-pressed={active === t.id}
              className="kd-t-label kd-pick-x kd-pick-x-edge inline-flex items-center"
              style={{ height: "var(--tabbar-h)", paddingInline: 17, color: "var(--fg-3)" }}
            >
              <span className="kd-pick-name">{t.label}</span>
            </button>
          </Fragment>
        ))}
      </div>

      {active === "summary" && <SummaryBody build={build} isEnv={isEnv} />}

      {active === "logs" && (
        <InkFace
          boxRef={logBoxRef}
          text={build.logs || (live ? "빌드 준비 중..." : "(로그 없음)")}
          highlightErrors
          expanded={expanded}
          onToggleExpand={() => setExpanded((v) => !v)}
        />
      )}

      {active === "dockerfile" && (
        <InkFace
          text={build.dockerfile_content || ""}
          expanded={expanded}
          onToggleExpand={() => setExpanded((v) => !v)}
        />
      )}

      {/* 진단은 실패한 빌드에만 붙는다(성공·env_change면 NULL) → 있을 때만 노출.
          Dockerfile 탭에서는 숨긴다 — 거긴 원인이 아니라 입력을 보는 자리다. */}
      {build.ai_analysis && active !== "dockerfile" && <Diagnosis build={build} />}
    </div>
  );
}

// 처음 열 탭 — 실패했거나 진행 중이면 로그(그때 보러 오는 게 로그다), 그 외엔 요약.
function defaultTab(build) {
  if ((build.kind || "build") === "env_change") return "summary";
  if (build.status === "failed" || ACTIVE_BUILD.has(build.status)) return "logs";
  return "summary";
}

function Meta({ icon: Icon, children }) {
  return (
    <span className="kd-t-label text-fg-1 inline-flex items-center min-w-0" style={{ gap: 9 }}>
      <Icon size={15} strokeWidth={1.7} className="text-fg-2 shrink-0" />
      <span className="truncate">{children}</span>
    </span>
  );
}

function MetaDivider() {
  return (
    <span
      style={{ width: 1, height: 18, background: "var(--kd-border)" }}
      aria-hidden="true"
    />
  );
}

// 잉크 면 — 로그/Dockerfile 공용. 줄번호 + 본문 2열 그리드.
//
// 바깥 상자만 현재 테마의 --term-bg로 칠하고, 안쪽에 data-theme="dark" 스코프를 씌운다.
// 면이 항상 어둡기 때문에 라이트 테마의 --err-fg(#c4161d)를 그대로 쓰면 어두운 바탕에
// 어두운 적색이 되어 안 읽힌다. 스코프 안에서는 같은 토큰이 어두운 면용 값으로 풀린다
// (시안의 에러 줄 색과 일치). hex를 직접 쓰지 않고 토큰만으로 해결하는 방법.
function InkFace({ boxRef, text, highlightErrors, expanded, onToggleExpand }) {
  const lines = (text || "").replace(/\s+$/, "").split("\n");
  return (
    <div
      style={{
        position: "relative",
        marginTop: 14,
        borderRadius: 4,
        border: "1px solid var(--kd-border)",
        background: "var(--term-bg)",
        overflow: "hidden",
      }}
    >
      <div data-theme="dark">
        <button
          onClick={onToggleExpand}
          className="absolute flex items-center justify-center opacity-70 hover:opacity-100 transition-opacity"
          style={{ top: 10, right: 10, width: 28, height: 28, color: "var(--fg-2)" }}
          title={expanded ? "접기" : "펼치기"}
          aria-label={expanded ? "접기" : "펼치기"}
        >
          {expanded ? (
            <Minimize2 size={15} strokeWidth={1.8} />
          ) : (
            <Maximize2 size={15} strokeWidth={1.8} />
          )}
        </button>
        <div
          ref={boxRef}
          className="scroll-thin"
          style={{
            overflow: "auto",
            // 시안 상자는 166(5줄) — 짧은 로그도 그 높이를 유지하고, 긴 로그는 잘라 스크롤.
            minHeight: 166,
            maxHeight: expanded ? "72vh" : 420,
            padding: "24px 18px",
          }}
        >
          <div
            className="kd-t-code"
            style={{ display: "grid", gridTemplateColumns: "auto minmax(0,1fr)", columnGap: 6 }}
          >
            {lines.map((line, i) => (
              <Fragment key={i}>
                <span
                  className="tabular-nums select-none text-right"
                  style={{ color: "var(--fg-2)", minWidth: 24 }}
                >
                  {i + 1}
                </span>
                <span
                  style={{
                    color:
                      highlightErrors && ERROR_LINE.test(line)
                        ? "var(--err-fg)"
                        : "var(--term-fg)",
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                  }}
                >
                  {line || " "}
                </span>
              </Fragment>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// 요약 탭 — 이 빌드가 "무엇으로 무엇을 만들었나". 전부 StatusResponse의 실제 필드다.
function SummaryBody({ build, isEnv }) {
  const rows = [];
  if (isEnv) {
    rows.push({ label: "변경", value: build.env_change_summary || "(상세 없음)" });
    rows.push({ label: "결과", value: statusLabel(build) });
    rows.push({ label: "시각", value: formatFull(build.created_at) });
  } else {
    rows.push({
      label: "저장소",
      value: build.repo_url ? (
        <a
          href={build.repo_url.replace(/\.git$/, "")}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-fg-1 no-underline hover:underline"
        >
          {repoSlug(build.repo_url)}
          <ArrowUpRight size={15} strokeWidth={1.7} className="text-fg-3" />
        </a>
      ) : (
        "—"
      ),
    });
    rows.push({ label: "브랜치", value: build.branch || "—" });
    rows.push({
      label: "런타임",
      value:
        build.runtime === "static"
          ? RUNTIME_LABEL.static
          : `${RUNTIME_LABEL[build.runtime] || build.runtime} · 포트 ${build.port}`,
    });
    rows.push({
      label: "빌드 방식",
      value: BUILD_MODE_LABEL[build.build_mode] || build.build_mode,
    });

    const resources = [];
    if (build.db_type && build.db_type !== "none")
      resources.push(DB_LABEL[build.db_type] || build.db_type);
    if (build.use_redis) resources.push("Redis");
    if (build.use_storage) resources.push("오브젝트 스토리지");
    if (build.volume_mount_path) resources.push(`영구 저장소 ${build.volume_mount_path}`);
    if (resources.length) rows.push({ label: "리소스", value: resources.join(" · ") });

    rows.push({ label: "시작", value: formatFull(build.created_at) });
    rows.push({ label: "소요", value: formatDuration(build.total_seconds) || "—" });
    if (build.error)
      rows.push({ label: "에러", value: build.error, color: "var(--err-fg)" });
  }

  return (
    <div style={{ marginTop: 8 }}>
      {rows.map((r, i) => (
        <div
          key={r.label}
          className="flex items-center gap-4"
          style={{
            minHeight: "var(--row-md)",
            borderBottom: i < rows.length - 1 ? "1px solid var(--kd-border)" : "none",
          }}
        >
          <span className="kd-t-label text-fg-3 shrink-0" style={{ width: 84 }}>
            {r.label}
          </span>
          <span
            className="kd-t-label min-w-0 flex-1"
            style={{ color: r.color || "var(--fg-1)", paddingBlock: 8 }}
          >
            {r.value}
          </span>
        </div>
      ))}
    </div>
  );
}

// AI 진단 — 원인(제목) / 로그(근거 인용) / 방법(조치). 시안은 아이콘 + 원인 + 안내 + 저장소 링크.
// 자유 텍스트 한 덩어리가 아니라 섹션이 나뉜 JSON이라 각각 다른 모양으로 보여줄 수 있다.
function Diagnosis({ build }) {
  const d = parseDiagnosis(build.ai_analysis);
  const steps = Array.isArray(d?.fix_steps) ? d.fix_steps : [];
  return (
    <div className="flex" style={{ gap: 18, marginTop: 38 }}>
      {/* 아이콘 26 — 시안 실측 27(잉크 23.4)에 가장 가까운 승인 치수(--ico-lg) */}
      <span className="shrink-0 flex">{resultIcon("failed", 26)}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline flex-wrap" style={{ gap: 10 }}>
          {/* 파싱 실패(옛 포맷·잘린 JSON)면 원문을 그대로라도 보여준다 */}
          <span className="kd-t-section text-fg-1">
            {d ? d.cause : build.error || "빌드가 실패했어요."}
          </span>
          {d?.kodeploy_specific && <span className="kd-chip">플랫폼 제약</span>}
        </div>

        {!d && build.ai_analysis && (
          <p className="kd-t-body-s text-fg-2" style={{ marginTop: 8 }}>
            {build.ai_analysis}
          </p>
        )}

        {d?.evidence && (
          <>
            <div className="kd-t-micro text-fg-3" style={{ marginTop: 16 }}>
              로그
            </div>
            <pre
              className="kd-t-code"
              style={{
                marginTop: 6,
                padding: "10px 12px",
                borderRadius: 4,
                background: "var(--kd-surface)",
                border: "1px solid var(--kd-border)",
                color: "var(--fg-2)",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {d.evidence}
            </pre>
          </>
        )}

        {steps.length > 0 && (
          <>
            <div className="kd-t-micro text-fg-3" style={{ marginTop: 16 }}>
              방법
            </div>
            <ol style={{ marginTop: 6 }}>
              {steps.map((step, i) => (
                <li
                  key={i}
                  className="kd-t-body-s text-fg-2 flex"
                  style={{ gap: 8, marginTop: i === 0 ? 0 : 6 }}
                >
                  <span className="text-fg-4 tabular-nums shrink-0">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </>
        )}

        {build.repo_url && (
          <a
            href={build.repo_url.replace(/\.git$/, "")}
            target="_blank"
            rel="noopener noreferrer"
            className="kd-t-label text-fg-1 underline inline-flex items-center gap-1"
            style={{ marginTop: 20 }}
          >
            저장소 열기
            <ArrowUpRight size={15} strokeWidth={1.7} />
          </a>
        )}

        <p className="kd-t-caption text-fg-3" style={{ marginTop: 16 }}>
          AI가 로그를 읽고 생성한 추정입니다. 실제 원인과 다를 수 있어요.
        </p>
      </div>
    </div>
  );
}
