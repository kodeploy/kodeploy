// 앱 상세 · 배포 이력 탭 — design/라이트모드-시안/14_배포_이력.png 기준.
//
// 좌: 빌드 목록(#N · 결과 · 시각 / 브랜치·빌드ID / 소요) · 우: 선택한 빌드 상세(BuildDetail).
// 선택 빌드는 URL 쿼리(?build=<id>)에 담는다 — 개요의 "배포 상세 보기"가 이 링크로 들어오고,
// 주소를 그대로 복사하면 같은 빌드가 다시 열린다. 쿼리가 없으면 최신 빌드를 자동 선택.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.3665(시안 스케일 = 1536폭 렌더 / CSS 1124)한
// CSS px이다. 콘텐츠 폭 1426px ÷ 1.3665 = 1043 ≈ --kd-w-app의 1040과 맞는다.
// 데이터는 AppLayout이 폴링해 내려준 GET /deploy 응답 그대로다. 커밋 sha/메시지는
// Build 모델에 없어서(시안의 "a81c92f" 자리) 빌드 ID로 대체했다 — 없는 값은 만들지 않는다.
import { useEffect, useMemo, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { ArrowUpRight, CircleCheck, CircleX } from "lucide-react";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";
import { listRecentCommits } from "../../api/deploy.js";
import { formatDuration, formatFull, parseDate, relativeTime } from "../../lib/format.js";
import BuildDetail, { resultIcon, statusLabel } from "./BuildDetail.jsx";

// 시안의 "오늘 14:20 / 어제 18:10" 표기. lib/format.js에는 상대시간(relativeTime)과
// 절대시간(formatFull)만 있어서 이 화면의 표기만 여기서 만든다 — 파싱은 parseDate 재사용.
function dayTime(iso) {
  const d = parseDate(iso);
  if (!d) return "";
  const pad = (n) => String(n).padStart(2, "0");
  const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (d >= today) return `오늘 ${hhmm}`;
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (d >= yesterday) return `어제 ${hhmm}`;
  return formatFull(iso); // 그 이전은 "YY.MM.DD HH:MM"
}

export default function AppHistory() {
  const { user, builds, slotStatus } = useOutletContext();
  const [searchParams, setSearchParams] = useSearchParams();
  const pinnedBuildId = searchParams.get("build");

  // #N — env_change는 번호를 안 매긴다(개요·활동 패널과 같은 규칙). 최신이 가장 큰 번호.
  const numbered = useMemo(() => {
    const m = new Map();
    let n = builds.filter((b) => (b.kind || "build") !== "env_change").length;
    for (const b of builds) if ((b.kind || "build") !== "env_change") m.set(b.build_id, n--);
    return m;
  }, [builds]);

  // 쿼리가 가리키는 빌드가 목록에 없으면(삭제·다른 앱) 조용히 최신으로 떨어진다.
  const selected = builds.find((b) => b.build_id === pinnedBuildId) || builds[0] || null;
  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;

  // replace — 행을 훑을 때마다 뒤로가기 스택이 쌓이면 탭을 빠져나가기 어려워진다.
  const select = (buildId) => setSearchParams({ build: buildId }, { replace: true });

  return (
    <div className="flex-1 overflow-auto scroll-thin">
      <div className="kd-page" style={{ paddingBottom: 72 }}>
        {/* ── 페이지 헤더 (시안 제목 y209 / 설명 y262 / 우측 앱 이름+상태 y213) ── */}
        <div className="flex items-start gap-6 flex-wrap" style={{ paddingTop: 28 }}>
          <div className="min-w-0">
            <h1 className="kd-t-title text-fg-1">배포 이력</h1>
            <p className="kd-t-body-s text-fg-2" style={{ marginTop: 6 }}>
              빌드 결과와 배포 내용을 확인하세요.
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2.5 shrink-0" style={{ paddingTop: 6 }}>
            {/* 앱 이름 — 개요 탭 헤더와 같은 크기(kd-t-subtitle)로 맞춘다 */}
            <span className="kd-t-subtitle text-fg-1">{user.app_name}</span>
            <span className="kd-t-label text-fg-4">·</span>
            <PodStatus status={podStatus} />
          </div>
        </div>

        {/* ── 2단 — 좌 목록 440(시안 세로 괘선 x657) / 우 상세(시안 x692에서 시작) ── */}
        <div
          className="grid grid-cols-1 lg:grid-cols-[440px_minmax(0,1fr)] border-kd-border"
          style={{ marginTop: 28 }}
        >
          {/* 좌 440 = 시안 세로 괘선 x657 */}
          <div className="lg:border-r border-kd-border lg:pr-5">
            {builds.length === 0 ? (
              <div className="kd-t-body-s text-fg-3" style={{ paddingBlock: 17 }}>
                아직 빌드 기록이 없어요.
              </div>
            ) : (
              <div style={{ borderTop: "1px solid var(--kd-border)" }}>
                {builds.map((b) => (
                  <BuildRow
                    key={b.build_id}
                    build={b}
                    number={numbered.get(b.build_id)}
                    selected={selected?.build_id === b.build_id}
                    onSelect={() => select(b.build_id)}
                  />
                ))}
              </div>
            )}

            <RecentCommits />
          </div>

          {/* 우 — 괘선에서 26 띄운다(시안 x692) */}
          <div className="mt-10 lg:mt-0 lg:pl-[26px]" style={{ paddingTop: 6 }}>
            {selected && (
              <BuildDetail
                key={selected.build_id}
                build={selected}
                number={numbered.get(selected.build_id)}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// 목록 행 — 높이 80(시안 110.5). 윗줄 #N·아이콘·상태·시각, 아랫줄 브랜치·빌드ID·소요.
// 선택 행은 잉크 테두리 + 옅은 활성 면. 시안에서 테두리 상자가 본문 좌측보다 4px 바깥(x51)이라
// 안쪽 상자에 marginInline:-4를 줘서 번호(#N)는 제목과 같은 세로선에 남게 한다.
function BuildRow({ build, number, selected, onSelect }) {
  const isEnv = (build.kind || "build") === "env_change";
  const failed = build.status === "failed";
  return (
    <button
      onClick={onSelect}
      className="block w-full text-left"
      style={{ height: 80, paddingBlock: 2, borderBottom: "1px solid var(--kd-border)" }}
    >
      <div
        className={`h-full flex items-center ${selected ? "" : "kd-hoverable"}`}
        style={{
          marginInline: -4,
          paddingLeft: 11, // 시안: 번호 좌측 x66 → 상자 안쪽 11
          paddingRight: 16,
          borderRadius: 8,
          border: `1px solid ${selected ? "var(--fg-1)" : "transparent"}`,
          background: selected ? "var(--sel-soft)" : undefined,
        }}
      >
        {/* 번호 열 — 폭 44 + 간격 8 = 아이콘 시작 58.5(시안 x136) */}
        <span className="kd-t-label text-fg-1 tabular-nums shrink-0" style={{ width: 44 }}>
          {isEnv ? "" : `#${number}`}
        </span>

        <span className="min-w-0 flex-1" style={{ marginLeft: 8 }}>
          <span className="flex items-center" style={{ gap: 10 }}>
            <span className="shrink-0 flex">{resultIcon(build.status, 18)}</span>
            <span
              className="kd-t-label truncate"
              style={{ color: failed ? "var(--err-fg)" : "var(--fg-1)" }}
            >
              {isEnv ? "환경변수 변경" : statusLabel(build)}
            </span>
          </span>
          {/* 아랫줄은 아이콘 왼쪽선에 맞춘다(시안 x137 ≈ 아이콘 x136) */}
          <span
            className="kd-t-caption text-fg-3 truncate block"
            style={{ marginTop: 4, paddingLeft: 28 }}
          >
            {isEnv
              ? build.env_change_summary || "(상세 없음)"
              : `${build.branch || "—"} · ${build.build_id}`}
          </span>
        </span>

        <span className="shrink-0 text-right" style={{ marginLeft: 12 }}>
          <span className="kd-t-label text-fg-2 block tabular-nums">
            {dayTime(build.created_at)}
          </span>
          <span className="kd-t-caption text-fg-3 block tabular-nums" style={{ marginTop: 4 }}>
            {formatDuration(build.total_seconds) || "—"}
          </span>
        </span>
      </div>
    </button>
  );
}

// 최근 커밋 — 활동 위젯(CommitListWidget)이 없어지면서 갈 곳이 없어진 기능을 여기로 옮겼다.
// 배포 이력(무엇이 배포됐나) 바로 아래에 소스 이력(무엇이 커밋됐나)이 오는 게 자연스럽다.
// 백엔드는 공개 저장소만 조회할 수 있어(unauthenticated GitHub 호출) 실패하면 조용히 감춘다.
function RecentCommits() {
  const [commits, setCommits] = useState(null); // null=로딩, []=없음/실패

  useEffect(() => {
    let alive = true;
    listRecentCommits()
      .then((d) => alive && setCommits(Array.isArray(d) ? d : []))
      .catch(() => alive && setCommits([]));
    return () => {
      alive = false;
    };
  }, []);

  if (!commits || commits.length === 0) return null;

  return (
    <section style={{ marginTop: 36 }}>
      <h2 className="kd-t-section text-fg-1">최근 커밋</h2>
      <div style={{ marginTop: 12, borderTop: "1px solid var(--kd-border)" }}>
        {commits.slice(0, 5).map((c) => (
          <a
            key={c.sha}
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            className="kd-hoverable flex items-center gap-3 no-underline"
            style={{ height: "var(--row-lg)", borderBottom: "1px solid var(--kd-border)" }}
          >
            <span className="kd-t-code text-fg-2 shrink-0" style={{ width: 64 }}>
              {(c.sha || "").slice(0, 7)}
            </span>
            <span className="kd-t-body-s text-fg-1 truncate flex-1">
              {(c.message || "").split("\n")[0]}
            </span>
            <span className="kd-t-caption text-fg-3 shrink-0">{relativeTime(c.date)}</span>
            <ArrowUpRight size={15} strokeWidth={1.7} className="text-fg-4 shrink-0" />
          </a>
        ))}
      </div>
    </section>
  );
}

// 헤더 우측 Pod 상태 — 지금 살아 있나. AppStatusBadge의 라벨 맵 재사용.
function PodStatus({ status }) {
  if (!status) return null;
  const s = APP_STATUS_STYLES[status] || { label: status };
  const bad = status === "crashing";
  return (
    <span className="kd-t-label text-fg-2 inline-flex items-center gap-1.5">
      {bad ? (
        <CircleX size={18} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />
      ) : (
        <CircleCheck
          size={18}
          strokeWidth={1.6}
          style={{ color: status === "running" ? "var(--ok-fg)" : "var(--fg-4)" }}
        />
      )}
      {s.label}
    </span>
  );
}
