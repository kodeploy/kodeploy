// 배포 마법사 2단계 — 실행 환경.
// 좌: 폼(앱 이름·포트·빌드 방식·DB·스토리지·접이식) / 세로 괘선 / 우: "현재 선택" 요약 레일.
// 값과 검증은 전부 DeployWizard에 있다. 여기서는 props로 받은 값을 그리고 setter를 부르기만 한다.
import { Eye, EyeOff, Plus, Trash2 } from "lucide-react";
import { RUNTIMES } from "../../../api/deploy.js";
import {
  DB_OPTIONS,
  RUNTIME_META,
  STORAGE_OPTIONS,
  labelOf,
} from "./options.js";
import {
  Checkbox,
  Disclosure,
  FieldLabel,
  Radio,
  Select,
  StepHeading,
} from "./wizardParts.jsx";

// 시안 실측(÷1.39): 폼 x348-1034 → 폭 494, 세로 괘선 x1083 → 739, 요약 레일 x1139-1482 → 779-1026.
// 즉 [폼 494][간격 36][괘선 1][간격 39][요약 246].
const FORM_W = 494;
const ASIDE_W = 286; // 괘선 1 + 좌측 여백 39 + 내용 246
const SUMMARY_TOP = 152; // "현재 선택" 잉크 y296 → 상단바 아래 152

export default function StepRuntime({
  // 이름·포트
  isFirstDeploy,
  appName,
  name,
  onName,
  port,
  onPort,
  // 빌드
  buildMode,
  onBuildMode,
  dockerfilePath,
  onDockerfilePath,
  runtime,
  onRuntime,
  // 데이터
  dbType,
  onDbType,
  useRedis,
  onUseRedis,
  // 스토리지
  storage,
  onStorage,
  volumeMountPath,
  onVolumeMountPath,
  // 환경변수
  envRows,
  onAddEnvRow,
  onRemoveEnvRow,
  onUpdateEnvRow,
  onToggleEnvVisible,
  activeReserved,
  showEnv,
  onToggleEnv,
  // 초기 덤프
  initDumpFile,
  onInitDumpFile,
  showInitDump,
  onToggleInitDump,
  // 커스텀 도메인
  customDomain,
  onCustomDomain,
  cnameTarget,
  appUrl,
  showCustomDomain,
  onToggleCustomDomain,
  // 요약
  repoSlug,
  branch,
  submitting,
  onRequestGuide,
}) {
  const storageHint = STORAGE_OPTIONS.find((s) => s.id === storage)?.hint;
  const hasDb = dbType === "mysql" || dbType === "postgres";

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `minmax(0, 1fr) ${ASIDE_W}px`,
        alignItems: "stretch",
      }}
    >
      <div style={{ paddingTop: 56, paddingRight: 36, maxWidth: FORM_W + 36 }}>
        <StepHeading title="실행 환경을 정해주세요." desc="앱에 필요한 항목만 선택하세요." />

        {/* 앱 이름 450 / 포트 216 (÷1.39 → 324 / 155), 간격 16 */}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 155px", columnGap: 16 }}>
          <div>
            <FieldLabel htmlFor="wz-name">앱 이름</FieldLabel>
            {/* 첫 배포에만 정할 수 있다 — 이후에는 서브도메인이 이 이름에 묶여 있어 고정. */}
            <input
              id="wz-name"
              value={isFirstDeploy ? name : appName || ""}
              onChange={(e) => onName(e.target.value)}
              placeholder={isFirstDeploy ? "비워두면 자동으로 지어져요" : ""}
              spellCheck={false}
              autoCapitalize="off"
              disabled={submitting || !isFirstDeploy}
              readOnly={!isFirstDeploy}
              className="kd-input"
            />
          </div>
          <div>
            <FieldLabel htmlFor="wz-port">포트</FieldLabel>
            <input
              id="wz-port"
              value={port}
              onChange={(e) => onPort(e.target.value)}
              inputMode="numeric"
              disabled={submitting}
              className="kd-input"
            />
          </div>
        </div>

        {/* 빌드 방식 — 시안은 2지선다. 백엔드의 "auto"(nixpacks 강제)도 자동 감지 쪽에 포함된다. */}
        <div style={{ marginTop: 32 }}>
          <FieldLabel>빌드 방식</FieldLabel>
          <div style={{ display: "flex", alignItems: "center", gap: 36, height: 27 }}>
            <Radio
              name="buildMode"
              checked={buildMode !== "dockerfile"}
              onChange={() => onBuildMode("detect")}
              disabled={submitting}
            >
              자동 감지
            </Radio>
            <Radio
              name="buildMode"
              checked={buildMode === "dockerfile"}
              onChange={() => onBuildMode("dockerfile")}
              disabled={submitting}
            >
              Dockerfile 사용
            </Radio>
          </div>

          {buildMode === "dockerfile" ? (
            <div style={{ marginTop: 14 }}>
              <input
                value={dockerfilePath}
                onChange={(e) => onDockerfilePath(e.target.value)}
                placeholder="Dockerfile"
                spellCheck={false}
                autoCapitalize="off"
                disabled={submitting}
                className="kd-input"
                style={{ maxWidth: 324 }}
                aria-label="Dockerfile 경로"
              />
              <div className="kd-t-caption" style={{ marginTop: 8, color: "var(--fg-3)" }}>
                저장소 기준 Dockerfile 경로{" "}
                <button
                  type="button"
                  onClick={() => onRequestGuide?.(runtime)}
                  className="kd-strong"
                  style={{ color: "var(--accent)", textDecoration: "underline" }}
                >
                  가이드 보기
                </button>
              </div>
            </div>
          ) : (
            // 시안의 "감지된 런타임 · Python" 줄. 런타임은 빌드 이미지와 기본 포트를 정하는 실제 입력이라
            // 읽기 전용 문구로 만들면 값을 잃는다 — 같은 줄 모양을 유지한 채 작은 선택기로 둔다.
            <div
              className="kd-t-body-s"
              style={{ marginTop: 14, display: "flex", alignItems: "center", gap: 8, color: "var(--fg-3)" }}
            >
              <span>감지된 런타임</span>
              <span aria-hidden>·</span>
              <select
                value={runtime}
                onChange={(e) => onRuntime(e.target.value)}
                disabled={submitting}
                aria-label="런타임"
                className="kd-t-body-s"
                style={{
                  appearance: "none",
                  background: "transparent",
                  border: 0,
                  color: "var(--fg-2)",
                  cursor: "pointer",
                  padding: 0,
                }}
              >
                {RUNTIMES.map((r) => (
                  <option key={r} value={r}>
                    {RUNTIME_META[r]?.name || r}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* 데이터베이스 + Redis */}
        <div style={{ marginTop: 32 }}>
          <FieldLabel htmlFor="wz-db">데이터베이스</FieldLabel>
          <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
            <Select
              value={dbType}
              onChange={(e) => onDbType(e.target.value)}
              disabled={submitting}
              width={324}
            >
              {DB_OPTIONS.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
            <Checkbox checked={useRedis} onChange={() => onUseRedis(!useRedis)} disabled={submitting}>
              Redis 사용
            </Checkbox>
          </div>
        </div>

        {/* 스토리지 */}
        <div style={{ marginTop: 32 }}>
          <FieldLabel>스토리지</FieldLabel>
          <div style={{ display: "flex", alignItems: "center", gap: 28, height: 27, flexWrap: "wrap" }}>
            {STORAGE_OPTIONS.map((s) => (
              <Radio
                key={s.id}
                name="storage"
                checked={storage === s.id}
                onChange={() => onStorage(s.id)}
                disabled={submitting}
              >
                {s.name}
              </Radio>
            ))}
          </div>
          {storageHint && (
            <p className="kd-t-caption" style={{ marginTop: 14, color: "var(--fg-3)" }}>
              {storageHint}{" "}
              {storage !== "none" && (
                <button
                  type="button"
                  onClick={() => onRequestGuide?.("storage")}
                  className="kd-strong"
                  style={{ color: "var(--accent)", textDecoration: "underline" }}
                >
                  가이드 보기
                </button>
              )}
            </p>
          )}
          {storage === "local" && (
            <input
              value={volumeMountPath}
              onChange={(e) => onVolumeMountPath(e.target.value)}
              placeholder="/var/www/html/data"
              spellCheck={false}
              autoCapitalize="off"
              disabled={submitting}
              aria-label="마운트 경로"
              className="kd-input"
              style={{ marginTop: 12, maxWidth: 324 }}
            />
          )}
        </div>

        {/* 접이식 — 시안은 "환경변수 추가" 한 줄. 초기 데이터·커스텀 도메인도 기존 폼에 있던
            입력이라 같은 모양의 접이식으로 이어 붙여 보존한다(기본 접힘). */}
        <div style={{ marginTop: 28, borderTop: "1px solid var(--kd-border)", paddingTop: 12 }}>
          <Disclosure open={showEnv} onToggle={onToggleEnv} label="환경변수 추가">
            <EnvRowsEditor
              rows={envRows}
              activeReserved={activeReserved}
              onAdd={onAddEnvRow}
              onRemove={onRemoveEnvRow}
              onUpdate={onUpdateEnvRow}
              onToggleVisible={onToggleEnvVisible}
              submitting={submitting}
            />
          </Disclosure>

          {hasDb && (
            <Disclosure
              open={showInitDump}
              onToggle={onToggleInitDump}
              label="초기 데이터"
              caption={initDumpFile ? initDumpFile.name : "선택된 파일 없음"}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <label className="kd-btn-secondary kd-btn-sm" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                  파일 선택
                  <input
                    type="file"
                    accept=".sql,.gz,.sql.gz,application/sql,application/gzip"
                    onChange={(e) => onInitDumpFile(e.target.files?.[0] || null)}
                    disabled={submitting}
                    style={{ display: "none" }}
                  />
                </label>
                {initDumpFile && (
                  <button
                    type="button"
                    onClick={() => onInitDumpFile(null)}
                    className="kd-t-caption"
                    style={{ color: "var(--fg-3)", textDecoration: "underline" }}
                  >
                    선택 취소
                  </button>
                )}
              </div>
              <p className="kd-t-caption" style={{ marginTop: 10, color: "var(--fg-3)" }}>
                .sql · .sql.gz 덤프를 올리면 배포 후 데이터베이스에 자동 복원돼요. 기존 데이터는 덮어씁니다.
              </p>
            </Disclosure>
          )}

          <Disclosure
            open={showCustomDomain}
            onToggle={onToggleCustomDomain}
            label="커스텀 도메인"
            caption={customDomain.trim() || "선택"}
          >
            <input
              value={customDomain}
              onChange={(e) => onCustomDomain(e.target.value.toLowerCase())}
              placeholder="app.example.com"
              spellCheck={false}
              autoCapitalize="off"
              disabled={submitting}
              aria-label="커스텀 도메인"
              className="kd-input"
              style={{ maxWidth: 324 }}
            />
            <p className="kd-t-caption" style={{ marginTop: 10, color: "var(--fg-3)" }}>
              서브도메인만 연결할 수 있어요. 배포 후 DNS에 CNAME {cnameTarget} 을 추가하세요.{" "}
              <button
                type="button"
                onClick={() => onRequestGuide?.("custom-domain")}
                className="kd-strong"
                style={{ color: "var(--accent)", textDecoration: "underline" }}
              >
                가이드 보기
              </button>
            </p>
          </Disclosure>
        </div>
      </div>

      {/* 우측 요약 레일 — 세로 괘선은 본문 맨 위부터 하단 버튼 괘선까지 이어진다(시안 x1083). */}
      <aside style={{ borderLeft: "1px solid var(--kd-border)", paddingLeft: 39, paddingTop: SUMMARY_TOP }}>
        <div className="kd-t-body kd-strong" style={{ color: "var(--fg-1)", marginBottom: 26 }}>
          현재 선택
        </div>
        <dl style={{ display: "grid", gridTemplateColumns: "122px minmax(0, 1fr)", rowGap: 14 }}>
          <SummaryPair label="저장소" value={repoSlug || "—"} />
          <SummaryPair label="브랜치" value={branch || "—"} />
          <SummaryPair label="런타임" value={RUNTIME_META[runtime]?.name || runtime} />
          <SummaryPair label="포트" value={String(port || "—")} />
          <SummaryPair label="데이터베이스" value={labelOf(DB_OPTIONS, dbType)} />
          <SummaryPair label="Redis" value={useRedis ? "사용" : "사용 안 함"} />
          <SummaryPair label="스토리지" value={labelOf(STORAGE_OPTIONS, storage)} />
          {/* 도메인 — 커스텀 도메인을 적었으면 그 주소, 아니면 기본으로 잡히는 주소 */}
          <SummaryPair label="도메인" value={customDomain.trim() || appUrl || "—"} />
        </dl>
      </aside>
    </div>
  );
}

function SummaryPair({ label, value }) {
  return (
    <>
      <dt className="kd-t-body-s" style={{ color: "var(--fg-3)" }}>
        {label}
      </dt>
      <dd className="kd-t-body-s" style={{ color: "var(--fg-1)", overflowWrap: "anywhere" }}>
        {value}
      </dd>
    </>
  );
}

// 환경변수 KEY/VALUE 편집기. 값은 기본 가림 — 포커스하면 보인다.
// 켜진 의존성이 자동 주입하는 예약 키와 겹치면 그 줄을 붉게 표시하고 제출을 막는다(상위에서 검사).
function EnvRowsEditor({ rows, activeReserved, onAdd, onRemove, onUpdate, onToggleVisible, submitting }) {
  return (
    <div>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {rows.map((row, i) => {
          const conflict = !!row.key.trim() && activeReserved.has(row.key.trim());
          return (
            <div key={i}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  value={row.key}
                  onChange={(e) => onUpdate(i, "key", e.target.value.toUpperCase())}
                  placeholder="KEY"
                  spellCheck={false}
                  autoCapitalize="characters"
                  disabled={submitting}
                  aria-label="환경변수 이름"
                  className="kd-input"
                  style={{
                    flex: 1,
                    minWidth: 0,
                    height: "var(--row-md)",
                    borderColor: conflict ? "var(--err-fg)" : undefined,
                  }}
                />
                <input
                  value={row.visible ? row.value : "•".repeat(Math.min(row.value.length, 12))}
                  onChange={(e) => row.visible && onUpdate(i, "value", e.target.value)}
                  onFocus={() => !row.visible && onToggleVisible(i)}
                  readOnly={!row.visible}
                  placeholder="value"
                  spellCheck={false}
                  disabled={submitting}
                  aria-label="환경변수 값"
                  className="kd-input"
                  style={{ flex: 1, minWidth: 0, height: "var(--row-md)" }}
                />
                <IconButton
                  label={row.visible ? "값 숨기기" : "값 보기"}
                  onClick={() => onToggleVisible(i)}
                >
                  {row.visible ? <EyeOff size={15} strokeWidth={1.8} /> : <Eye size={15} strokeWidth={1.8} />}
                </IconButton>
                <IconButton label="이 줄 삭제" onClick={() => onRemove(i)}>
                  <Trash2 size={15} strokeWidth={1.8} />
                </IconButton>
              </div>
              {conflict && (
                <p className="kd-t-caption" style={{ marginTop: 4, color: "var(--err-fg)" }}>
                  선택한 의존성이 자동으로 넣어주는 이름이에요 - 빼거나 해당 의존성을 끄세요.
                </p>
              )}
            </div>
          );
        })}
      </div>
      <button
        type="button"
        onClick={onAdd}
        className="kd-btn-secondary kd-btn-sm"
        style={{ marginTop: 12, display: "inline-flex", alignItems: "center", gap: 6 }}
      >
        <Plus size={15} strokeWidth={2} /> 변수 추가
      </button>
    </div>
  );
}

function IconButton({ label, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      className="kd-hoverable"
      style={{
        width: "var(--row-md)",
        height: "var(--row-md)",
        borderRadius: 8,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        color: "var(--fg-3)",
      }}
    >
      {children}
    </button>
  );
}
