// 배포하기 — 설정은 지금 그대로 두고 커밋만 골라 다시 빌드·배포한다 (CLI·CI와 같은 API, 설정을 바꾸려면 "설정 변경").
//
// 누르면 버튼 폭 그대로 버튼 위(좁은 화면은 아래)로 커밋 목록이 펼쳐진다: 브랜치, 최근 커밋 3개(짧은 sha 아래에 날짜),
// 지금 떠 있는 커밋에 "현재". 맨 위(최신)가 기본이다. 한 번 더 누르면 고른 커밋으로 배포하고, 화면은 그대로 둔 채
// 버튼이 "빌드 중"으로 바뀐다 — 그때 누르면 진행 화면으로 간다.
//
// 모양은 앱의 다른 드롭다운(환경변수 더보기·계정 메뉴)과 같은 면·괘선·그림자이고, 고른 줄은 사이드바·배포 이력과 같은
// 왼쪽 잉크 선(.kd-pick)으로 표시한다. "현재"는 상단바 "실행 중"과 같은 초록 점.
//
// 최신을 고르면 커밋을 지정하지 않고 보낸다(서버가 clone할 때의 브랜치 최신). 목록은 몇 초 늦을 수 있고, 다른 저장소·브랜치를
// 쓰는 정적 슬롯도 예전처럼 제 브랜치 최신으로 빌드되게 하려는 것이다. 옛 커밋을 고를 때만 그 커밋으로 고정한다.
//
// "빌드 중"은 이 버튼으로 시작한 것만이 아니라 앱의 진행 중인 빌드를 모두 따른다 — 새로고침하거나 배포 폼으로 시작해도 같다.
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { GitBranch, Loader } from "lucide-react";
import { listRedeployCommits, redeployApp } from "../../api/deploy.js";
import { dayTime, repoSlug } from "../../lib/format.js";

const ACTIVE = new Set(["queued", "building", "built", "deploying"]);
const BUILDING = new Set(["queued", "building"]);
// 응답 직후 빌드 목록에 아직 안 잡혔을 때 "빌드 중"으로 버티는 시간
const STARTED_GRACE_MS = 15000;

const short = (sha) => (sha || "").slice(0, 7);

// 진행 중인 빌드 — 서버 슬롯이 먼저(진행 화면이 따라갈 빌드). 막 시작해 아직 목록에 없으면 시작 응답의 첫 빌드.
export function activeDeploy(builds, started) {
  const live = builds.filter((b) => ACTIVE.has(b.status));
  const pick = live.find((b) => b.runtime !== "static") || live[0];
  if (pick) return { buildId: pick.build_id, label: (pick.kind || "build") === "build" && BUILDING.has(pick.status) ? "빌드 중" : "배포 중" };
  if (started && Date.now() - started.at < STARTED_GRACE_MS && !started.ids.some((id) => builds.some((b) => b.build_id === id))) {
    return { buildId: started.ids[0], label: "빌드 중" };
  }
  return null;
}

export default function RedeployButton({
  app, base, builds, started, onStarted, onFail, onAuthError, size = "md", placement = "up",
}) {
  const navigate = useNavigate();
  const wrapRef = useRef(null);
  const [open, setOpen] = useState(false);
  const [list, setList] = useState(null); // null=불러오는 중
  const [listError, setListError] = useState(null);
  const [picked, setPicked] = useState(null); // 고른 커밋 sha. null=브랜치 최신
  const [busy, setBusy] = useState(false);

  const active = activeDeploy(builds, started);

  // 바깥을 누르거나 Esc면 접는다
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => wrapRef.current && !wrapRef.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // 빌드가 시작되면(다른 곳에서 시작해도) 펼친 목록을 걷는다
  useEffect(() => {
    if (active) setOpen(false);
  }, [Boolean(active)]);

  const expand = async () => {
    setOpen(true);
    setList(null);
    setListError(null);
    setPicked(null);
    try {
      const res = await listRedeployCommits(app.id);
      setList(res);
    } catch (err) {
      if (onAuthError(err)) return;
      if (err.status === 409) {
        setOpen(false);
        onFail({ needsForm: true });
        return;
      }
      setList({ commits: [] });
      setListError(err.message?.replace(/^\d+ /, "") || "커밋 목록을 불러오지 못했어요");
    }
  };

  const deploy = async () => {
    setBusy(true);
    try {
      // 최신(맨 위)이면 커밋을 지정하지 않는다 — 위 설명 참고
      const latest = list?.commits?.[0]?.sha;
      const res = await redeployApp(app.id, { commitSha: picked && picked !== latest ? picked : undefined });
      setOpen(false);
      onStarted((res.builds || []).map((b) => b.build_id));
    } catch (err) {
      if (onAuthError(err)) return;
      setOpen(false);
      onFail(err.status === 409 ? { needsForm: true } : { error: err.message?.replace(/^\d+ /, "") || "배포를 시작하지 못했어요" });
    } finally {
      setBusy(false);
    }
  };

  const onClick = () => {
    if (active) {
      navigate(`${base}/deploy/progress?build=${active.buildId}`);
    } else if (!open) {
      expand();
    } else {
      deploy();
    }
  };

  const loading = open && list === null;

  return (
    <div ref={wrapRef} className="relative w-full min-w-0">
      {open && <CommitPanel up={placement === "up"} list={list} error={listError} picked={picked} onPick={setPicked} />}
      <button
        type="button"
        onClick={onClick}
        disabled={busy || loading}
        aria-expanded={active ? undefined : open}
        className={`kd-btn-primary kd-btn-${size} w-full inline-flex items-center justify-center whitespace-nowrap`}
        style={{ gap: 8 }}
      >
        {active ? (
          <>
            <Loader size={size === "sm" ? 14 : 15} strokeWidth={2} className="kd-spin shrink-0" />
            {active.label}
          </>
        ) : busy ? (
          "배포 요청 중…"
        ) : (
          "배포하기"
        )}
      </button>
    </div>
  );
}

// 커밋 목록 — 버튼과 같은 폭으로 버튼 위(아래)에 뜬다. 다른 드롭다운처럼 버튼과 4px 띄운다.
function CommitPanel({ up, list, error, picked, onPick }) {
  const commits = list?.commits || [];
  const currentSha = list?.current_sha || null;
  // 지금 떠 있는 커밋이 최근 3개 밖이면 맨 아래 따로 한 줄 (조회에 실패했으면 sha만)
  const extra = list?.current || null;
  const value = picked || commits[0]?.sha || null;

  return (
    <div
      className="absolute left-0 right-0 kd-fade-in"
      style={{
        [up ? "bottom" : "top"]: "calc(100% + 4px)",
        paddingBottom: 4,
        background: "var(--dropdown-bg)",
        border: "1px solid var(--kd-border)",
        borderRadius: "var(--kd-radius)",
        boxShadow: "var(--shadow-pop)",
        zIndex: 30,
      }}
    >
      {/* 머리 — 계정 메뉴의 로그인 이름 줄과 같은 글자·괘선 */}
      <div
        className="kd-t-micro flex items-center text-fg-3 min-w-0"
        style={{ gap: 6, padding: "8px 12px", borderBottom: "1px solid var(--line-2)", marginBottom: 4 }}
        title={list?.branch ? `${repoSlug(list.repo)} · ${list.branch}` : undefined}
      >
        <GitBranch size={12} strokeWidth={1.8} className="shrink-0" />
        <span className="truncate">{list === null ? "불러오는 중" : list.branch}</span>
        {list === null && <Loader size={12} strokeWidth={1.8} className="kd-spin shrink-0" />}
      </div>

      {list !== null && (
        <div role="radiogroup" aria-label="배포할 커밋">
          {commits.length === 0 && (
            <CommitRow
              name="브랜치 최신"
              sub={error || "커밋 목록을 못 불러왔어요"}
              selected={!picked}
              onSelect={() => onPick(null)}
            />
          )}
          {commits.map((c) => (
            <CommitRow
              key={c.sha}
              name={short(c.sha)}
              sub={dayTime(c.date)}
              title={c.message}
              current={c.sha === currentSha}
              selected={value === c.sha}
              onSelect={() => onPick(c.sha)}
            />
          ))}
          {extra && (
            <div style={{ borderTop: "1px solid var(--line-2)", marginTop: 4, paddingTop: 4 }}>
              <CommitRow
                name={short(extra.sha)}
                sub={dayTime(extra.date)}
                title={extra.message}
                current
                selected={picked === extra.sha}
                onSelect={() => onPick(extra.sha)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// 한 줄 — 배포 이력 행과 같은 짜임(윗줄 이름표, 아랫줄 옅은 보조). 고르면 왼쪽 잉크 선 + 이름표 굵게,
// 호버는 면 없이 글자만 진해진다(.kd-pick 규칙). 날짜는 고른 줄에서도 옅은 회색 그대로 둔다.
function CommitRow({ name, sub, title, current, selected, onSelect }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      title={title || undefined}
      className="kd-pick block w-full text-left"
      style={{ paddingBlock: 6, paddingLeft: 12, paddingRight: 10 }}
    >
      <span className="flex items-center min-w-0" style={{ gap: 8 }}>
        <span className="kd-t-label kd-pick-name text-fg-2 tabular-nums truncate">{name}</span>
        {current && (
          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-fg-1 shrink-0" style={{ fontWeight: 510 }}>
            <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: "var(--dot-ok)" }} />
            현재
          </span>
        )}
      </span>
      {sub && (
        <span className="kd-t-caption text-fg-3 block truncate tabular-nums" style={{ marginTop: 1 }}>
          {sub}
        </span>
      )}
    </button>
  );
}
