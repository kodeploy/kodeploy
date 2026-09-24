// 배포 마법사 1단계 — 저장소 연결.
// 순수 표시 컴포넌트다. 상태·검증(repoCheck 디바운스)·GitHub 목록 로딩은 전부 DeployWizard가 들고 있고
// 여기서는 그 값을 받아 그리기만 한다. 그래야 단계를 오가도 입력이 남는다.
import { ArrowUpRight, Check, GitBranch } from "lucide-react";
import { FieldHint, FieldLabel, Select, StepHeading } from "./wizardParts.jsx";

// 시안 실측(÷1.39): 입력 944x63 → 679x45, 불러오기 버튼 142x61 → 102x44,
// 폼 전체 폭 1096 → 789, 2단 필드 516/547 + 간격 34 → 371/393 + 24.
export const STEP1_W = 790;

export default function StepRepo({
  repoUrl,
  onRepoUrl,
  repoCheck,
  repoSlug,
  repoIsPrivate,
  githubConnected,
  ghRepos,
  repoListOpen,
  onToggleRepoList,
  onPickRepo,
  ghBranches,
  branch,
  onBranch,
  projectPath,
  onProjectPath,
  submitting,
  installUrl,
}) {
  // 브랜치 목록을 못 받아왔으면(미연결·비공개·형식오류) 자유 입력으로 떨어진다 —
  // select만 두면 목록이 빈 순간 브랜치를 아예 못 고른다.
  const hasBranchList = ghBranches.length > 0;
  const branchOptions = hasBranchList
    ? [...new Set([branch, ...ghBranches.map((b) => b.name)].filter(Boolean))]
    : [];

  return (
    <div style={{ paddingTop: 56, maxWidth: STEP1_W }}>
      <StepHeading title="저장소를 연결하세요." desc="배포할 앱의 GitHub 저장소를 선택하세요." />

      <FieldLabel htmlFor="wz-repo">GitHub 저장소</FieldLabel>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <input
          id="wz-repo"
          value={repoUrl}
          onChange={(e) => onRepoUrl(e.target.value)}
          placeholder="https://github.com/username/repo"
          spellCheck={false}
          autoCapitalize="off"
          disabled={submitting}
          className="kd-input"
          style={{ flex: 1, minWidth: 0 }}
        />
        <button
          type="button"
          onClick={onToggleRepoList}
          disabled={submitting}
          className="kd-btn-secondary kd-btn-md"
          style={{ flexShrink: 0, width: 102 }}
        >
          불러오기
        </button>
      </div>

      {/* 연결된 저장소 목록 — "불러오기"로 여닫는다. 항목 높이 var(--row-sm), 3개 넘으면 스크롤. */}
      {repoListOpen && (
        <div
          className="scroll-thin"
          style={{
            marginTop: 8,
            borderRadius: 4,
            border: "1px solid var(--kd-border)",
            background: "var(--kd-surface)",
            maxHeight: 96,
            overflowY: "auto",
          }}
        >
          {ghRepos.length > 0 ? (
            ghRepos.map((r) => (
              <button
                key={r.full_name}
                type="button"
                onClick={() => onPickRepo(r)}
                disabled={submitting}
                className="kd-t-body-s kd-hoverable"
                style={{
                  width: "100%",
                  height: "var(--row-sm)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  gap: 8,
                  padding: "0 12px",
                  color: "var(--fg-2)",
                }}
              >
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {r.full_name}
                </span>
                {r.private && (
                  <span className="kd-t-micro" style={{ color: "var(--fg-4)", flexShrink: 0 }}>
                    private
                  </span>
                )}
              </button>
            ))
          ) : (
            <div className="kd-t-caption" style={{ padding: "10px 12px", color: "var(--fg-4)" }}>
              {githubConnected ? (
                <>
                  연결된 저장소가 없어요{" "}
                  <a href={installUrl} className="kd-strong" style={{ color: "var(--accent)", textDecoration: "underline" }}>
                    저장소 추가
                  </a>
                </>
              ) : (
                <>
                  비공개 저장소는 GitHub 연결이 필요해요{" "}
                  <a href={installUrl} className="kd-strong" style={{ color: "var(--accent)", textDecoration: "underline" }}>
                    GitHub 연결하기
                  </a>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* 검증 줄 — repoCheck 상태를 한 줄로. 시안: 브랜치 글리프 · 슬러그 | 공개 저장소 | ✓ 확인됨 */}
      <div style={{ marginTop: 14, minHeight: 21, display: "flex", alignItems: "center", gap: 12 }}>
        {repoCheck.state === "checking" && (
          <span className="kd-t-body-s" style={{ color: "var(--fg-3)" }}>
            저장소 확인 중…
          </span>
        )}
        {repoCheck.state === "ok" && (
          <>
            <GitBranch aria-hidden size={18} strokeWidth={1.6} style={{ color: "var(--fg-1)" }} />
            <span className="kd-t-body-s kd-strong" style={{ color: "var(--fg-1)" }}>
              {repoSlug}
            </span>
            <Divider />
            <span className="kd-t-body-s" style={{ color: "var(--fg-3)" }}>
              {repoIsPrivate ? "연결된 저장소" : "공개 저장소"}
            </span>
            <Divider />
            <Check aria-hidden size={18} strokeWidth={1.8} style={{ color: "var(--ok-fg)" }} />
            <span className="kd-t-body-s" style={{ color: "var(--fg-2)" }}>
              확인됨
            </span>
          </>
        )}
        {repoCheck.state === "invalid" && (
          <span className="kd-t-body-s" style={{ color: "var(--err-fg)" }}>
            GitHub 저장소 주소 형식이 아니에요
          </span>
        )}
        {repoCheck.state === "notfound" && (
          <span className="kd-t-body-s" style={{ color: "var(--err-fg)" }}>
            저장소 또는 브랜치를 찾을 수 없어요 - 비공개라면 GitHub 연결이 필요해요
          </span>
        )}
        {repoCheck.state === "error" && (
          <span className="kd-t-body-s" style={{ color: "var(--fg-3)" }}>
            확인 실패{repoCheck.code ? ` (${repoCheck.code})` : ""}
          </span>
        )}
      </div>

      {/* 브랜치 / 프로젝트 경로 2단 — 시안 간격 24 */}
      <div
        style={{
          marginTop: 32,
          display: "grid",
          gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
          columnGap: 24,
          alignItems: "start",
        }}
      >
        <div>
          <FieldLabel htmlFor="wz-branch">브랜치</FieldLabel>
          {hasBranchList ? (
            <Select
              id="wz-branch"
              value={branch}
              onChange={onBranch}
              options={branchOptions.map((b) => ({ id: b, name: b }))}
              disabled={submitting}
              label="브랜치"
            />
          ) : (
            <input
              id="wz-branch"
              value={branch}
              onChange={(e) => onBranch(e.target.value)}
              placeholder="main"
              spellCheck={false}
              disabled={submitting}
              className="kd-input"
            />
          )}
        </div>
        <div>
          <FieldLabel htmlFor="wz-path">프로젝트 경로</FieldLabel>
          <input
            id="wz-path"
            value={projectPath}
            onChange={(e) => onProjectPath(e.target.value)}
            placeholder="."
            spellCheck={false}
            autoCapitalize="off"
            disabled={submitting}
            className="kd-input"
          />
          <FieldHint>저장소의 최상위 폴더에서 시작합니다.</FieldHint>
        </div>
      </div>

      <a
        href={installUrl}
        className="kd-t-body-s"
        style={{
          marginTop: 34,
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          color: "var(--fg-2)",
          textDecoration: "underline",
          textUnderlineOffset: 3,
        }}
      >
        저장소 접근 도움말
        <ArrowUpRight aria-hidden size={15} strokeWidth={1.7} />
      </a>
    </div>
  );
}

function Divider() {
  return <span aria-hidden style={{ width: 1, height: 15, background: "var(--kd-border)" }} />;
}
