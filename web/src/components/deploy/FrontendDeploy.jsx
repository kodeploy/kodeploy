// 프론트엔드(정적 슬롯) 배포 화면 — design/라이트모드-시안/08_프론트엔드_배포.png 기준.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.37(시안 스케일 = 상단바 괘선 y81 ÷ 60)한 CSS px이다.
// 레이아웃: 좌 폼(시안 x95-1044 → 703) │ 세로 괘선(x1092) │ 우 정보 레일(x1139-1480 → 249)
//           → 바닥 전체 폭 괘선(y934) + 액션 바([취소] … [프론트엔드 배포 →]).
//
// 왜 서버 값을 같이 보내나:
//   POST /deploy는 "스택 전체 선언"이다(서버 슬롯 + 정적 슬롯을 한 번에 declare).
//   runtime을 안 보내면 서버가 헐리므로(pipeline._teardown_server), 최신 서버 빌드에서
//   읽은 값을 그대로 되돌려 보내 서버 선언을 보존한다. env는 빈 dict로 보내면
//   백엔드가 기존 Secret을 건드리지 않는다(pipeline: `if initial_env:`).
//   부작용: 제출하면 서버 슬롯도 같은 선언으로 다시 빌드된다 — API가 슬롯 단위 배포를
//   지원하지 않아 생기는 한계다.
//
// 빌드 환경변수는 서버 환경변수(Secret)와 성격이 다르다 — 빌드 스테이지 ENV로만 쓰여
// 번들 파일에 그대로 남는다(누구나 열어볼 수 있음). 레일 안내 문구로 그 사실을 알린다.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, ArrowUpRight, CircleCheck, CircleX, Plus, Trash2 } from "lucide-react";
import { createDeploy, getAppStatus, listBuilds } from "../../api/deploy.js";
import { useAuth } from "../../contexts/AuthContext.jsx";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";

// 백엔드 _validate_static_env와 같은 규칙 — 여기서 먼저 막아 왕복을 아낀다.
const KEY_PATTERN = /^[A-Z_][A-Z0-9_]*$/;

// 정적 빌드 기본값 — Vite 기준(시안에 그대로 적힌 값).
const DEFAULT_BUILD_CMD = "npm ci && npm run build";
const DEFAULT_OUTPUT_DIR = "dist";

let seq = 0;
const newRow = (key = "", value = "") => ({ id: ++seq, key, value });

// 정적 슬롯이 켜지면 서버는 {app}-api.kodeploy.com으로 옮겨간다
// (core routing/hostnames.py _slot_hostnames — site_enabled가 진실원).
// 이 화면은 정적을 켜는 화면이라 "배포 후" 주소인 -api 쪽을 보여줘야 맞다.
const apiHostOf = (appName) => `${appName}-api.kodeploy.com`;

export default function FrontendDeploy() {
  const navigate = useNavigate();
  const { user, openLogin, refresh } = useAuth();

  // 서버 슬롯 선언(되돌려 보낼 값) + Pod 상태 — 레일의 "연결할 앱" 정보원.
  const [serverBuild, setServerBuild] = useState(null);
  const [slotStatus, setSlotStatus] = useState(null);
  const [loaded, setLoaded] = useState(false);

  // 폼 — 필드 이름은 DeployForm의 정적 슬롯과 동일(payload도 그대로 쓴다).
  const [staticRepoUrl, setStaticRepoUrl] = useState("");
  const [staticBranch, setStaticBranch] = useState("");
  const [staticProjectPath, setStaticProjectPath] = useState("");
  const [buildCmd, setBuildCmd] = useState(DEFAULT_BUILD_CMD);
  const [outputDir, setOutputDir] = useState(DEFAULT_OUTPUT_DIR);
  const [envRows, setEnvRows] = useState([newRow()]);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // 최신 빌드 2종(서버/정적) + Pod 상태. 정적 빌드가 있으면 재배포라 폼을 그 값으로 채운다.
  useEffect(() => {
    if (!user?.app_name) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [builds, status] = await Promise.all([
          listBuilds(),
          getAppStatus().catch(() => null),
        ]);
        if (cancelled) return;
        const real = builds.filter((b) => b.kind !== "env_change");
        const server = real.find((b) => b.runtime !== "static") || null;
        const site = real.find((b) => b.runtime === "static") || null;
        setServerBuild(server);
        setSlotStatus(status);
        if (site) {
          // 재배포 — 지난 정적 빌드 선언을 그대로 복원.
          // repo/branch는 서버와 같으면 백엔드가 서버 값으로 fallback 하므로 비워 둔다.
          if (server && site.repo_url !== server.repo_url) {
            setStaticRepoUrl(site.repo_url.replace(/\.git$/, ""));
          } else if (!server) {
            setStaticRepoUrl(site.repo_url.replace(/\.git$/, ""));
          }
          if (server && site.branch !== server.branch) setStaticBranch(site.branch || "");
          setStaticProjectPath(site.project_path || "");
          setBuildCmd(site.build_cmd || "");
          setOutputDir(site.output_dir || "");
          const entries = Object.entries(site.static_env || {});
          if (entries.length) setEnvRows(entries.map(([k, v]) => newRow(k, v)));
        } else if (server) {
          // 첫 프론트엔드 배포 — 이 화면의 목적이 "API 주소 연결"이라 그 한 줄을 미리 채운다.
          // 값은 지어낸 게 아니라 슬롯 규칙에서 계산한 실제 주소다.
          setEnvRows([newRow("VITE_API_URL", `https://${apiHostOf(user.app_name)}`)]);
        }
      } catch {
        // 목록 조회 실패는 폼을 막지 않는다 — 레일만 비고 배포는 그대로 가능.
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.id, user?.app_name]);

  const patchRow = (id, field, value) =>
    setEnvRows((rows) => rows.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
  const removeRow = (id) =>
    setEnvRows((rows) => (rows.length > 1 ? rows.filter((r) => r.id !== id) : [newRow()]));

  // 서버가 없으면 이 저장소가 곧 repo_url(정적 단독 배포)이라 입력이 필수다.
  const repoRequired = !serverBuild;
  const disabled = submitting || (repoRequired && !staticRepoUrl.trim());

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    if (disabled) return;
    if (!user) {
      openLogin?.();
      return;
    }
    // row → dict. 빈 이름은 무시, 같은 이름이면 뒤 row가 이긴다(DeployForm/AppEnv와 같은 규칙).
    const staticEnv = {};
    for (const { key, value } of envRows) {
      const k = key.trim();
      if (!k) continue;
      if (!KEY_PATTERN.test(k)) {
        setError(`${k} - 변수 이름은 영문 대문자·숫자·_ 만 쓸 수 있어요 (대문자나 _로 시작).`);
        return;
      }
      staticEnv[k] = value;
    }
    setError(null);
    setSubmitting(true);
    // "."은 저장소 루트를 뜻하는 표기라 빈 값으로 보낸다(경로로 그대로 넘기면 /./ 가 된다).
    const projectPath = staticProjectPath.trim().replace(/^\.$/, "").replace(/^\/+|\/+$/g, "");
    try {
      await createDeploy({
        // 서버 슬롯 — 최신 서버 빌드 선언을 그대로 되돌려 보내 보존. 없으면 정적 단독(runtime "none").
        repoUrl: serverBuild ? serverBuild.repo_url : staticRepoUrl.trim(),
        branch: (serverBuild ? serverBuild.branch : staticBranch.trim()) || "main",
        port: serverBuild?.port ?? 80,
        runtime: serverBuild ? serverBuild.runtime : "none",
        dbType: serverBuild?.db_type || "none",
        useRedis: serverBuild?.use_redis || false,
        storage: serverBuild?.storage || "none",
        volumeMountPath: serverBuild?.volume_mount_path || "",
        volumeStorageClass: serverBuild?.volume_storage_class || "local-path",
        volumeSize: serverBuild?.volume_size || "5Gi",
        buildMode: serverBuild?.build_mode || "detect",
        dockerfilePath: serverBuild?.dockerfile_path || "Dockerfile",
        projectPath: serverBuild?.project_path || "",
        env: {}, // 빈 dict = 기존 Secret 유지 (백엔드가 무시)
        // 정적 슬롯 — 이 화면이 다루는 값.
        useStatic: true,
        staticRepoUrl: serverBuild ? staticRepoUrl.trim() : "",
        staticBranch: serverBuild ? staticBranch.trim() : "",
        staticProjectPath: projectPath,
        buildCmd: buildCmd.trim(),
        outputDir: outputDir.trim(),
        staticEnv,
      });
      // site_enabled(+첫 배포면 app_name)가 백엔드에서 바뀌었으니 user 재조회 후 진행 화면으로.
      await refresh();
      navigate("/deploy/progress");
    } catch (err) {
      if (err.status === 401) {
        openLogin?.();
        setSubmitting(false);
        return;
      }
      setError(err.message || "배포 요청 실패");
      setSubmitting(false);
    }
  };

  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;

  return (
    /* 내용이 짧아도 액션 바는 화면 바닥에 붙는다 — 시안처럼 세로 괘선이 바닥 괘선까지 내려온다 */
    <form onSubmit={handleSubmit} className="min-h-full flex flex-col">
      <div className="kd-page flex-1">
        {/* ── 2단 (좌 폼 / 세로 괘선 / 우 레일) ──
            레일 열 286 = 텍스트 폭 252(시안 341÷1.37) + 괘선→글 여백 34(시안 46). */}
        {/* lg:grid-rows-1 — 행이 컨테이너 높이를 채워야 세로 괘선이 바닥 괘선까지 내려온다 */}
        <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_286px] lg:grid-rows-1">
          {/* ─────────── 좌: 폼 ─────────── */}
          <div className="lg:pr-9" style={{ paddingTop: 40, paddingBottom: 24 }}>
            {/* 제목 — 시안 잉크 y152 / 설명 y226 */}
            <h1 className="kd-t-display" style={{ color: "var(--fg-1)" }}>
              프론트엔드도 함께 배포하세요.
            </h1>
            <p className="kd-t-body" style={{ color: "var(--fg-2)", marginTop: 8 }}>
              빌드한 정적 파일을 배포합니다.
            </p>

            {/* 저장소 — 시안 라벨 y292 / 입력 950x49(→703x38) */}
            <Field label="GitHub 저장소" style={{ marginTop: 28 }}>
              <input
                className="kd-input"
                value={staticRepoUrl}
                onChange={(e) => setStaticRepoUrl(e.target.value)}
                // 비우면 백엔드가 서버 슬롯 repo로 fallback 한다 — 그 주소를 placeholder로 보여 준다.
                placeholder={serverBuild?.repo_url || "https://github.com/me/my-web"}
                spellCheck={false}
                disabled={submitting}
              />
            </Field>

            {/* 브랜치 · 프로젝트 경로 — 시안 460x49 2단, 열 사이 30(→22) */}
            <div className="grid grid-cols-1 sm:grid-cols-2" style={{ gap: 22, marginTop: 18 }}>
              <Field label="브랜치">
                <input
                  className="kd-input"
                  value={staticBranch}
                  onChange={(e) => setStaticBranch(e.target.value)}
                  placeholder={serverBuild?.branch || "main"}
                  spellCheck={false}
                  disabled={submitting}
                />
              </Field>
              <Field label="프로젝트 경로">
                <input
                  className="kd-input"
                  value={staticProjectPath}
                  onChange={(e) => setStaticProjectPath(e.target.value)}
                  placeholder="."
                  spellCheck={false}
                  disabled={submitting}
                />
              </Field>
            </div>

            <Rule />

            {/* 빌드 명령 — 시안 모노. 비우면 빌드 없이 저장소 파일을 그대로 서빙(백엔드 규칙). */}
            <Field label="빌드 명령">
              <input
                className="kd-input font-mono"
                value={buildCmd}
                onChange={(e) => setBuildCmd(e.target.value)}
                placeholder={DEFAULT_BUILD_CMD}
                spellCheck={false}
                disabled={submitting}
              />
            </Field>

            <Field label="결과 폴더" style={{ marginTop: 18 }}>
              <input
                className="kd-input"
                value={outputDir}
                onChange={(e) => setOutputDir(e.target.value)}
                placeholder={DEFAULT_OUTPUT_DIR}
                spellCheck={false}
                disabled={submitting}
              />
            </Field>

            <Rule />

            {/* 빌드 환경변수 — 시안 라벨 y754 / 작은 라벨 y785 / 입력 443·449x45 + 휴지통 */}
            <div className="kd-t-body-s kd-strong" style={{ color: "var(--fg-1)" }}>
              빌드 환경변수
            </div>
            <div
              className="grid items-center"
              style={{
                gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) 32px",
                columnGap: 18,
                marginTop: 10,
              }}
            >
              <span className="kd-t-micro" style={{ color: "var(--fg-3)" }}>
                이름
              </span>
              <span className="kd-t-micro" style={{ color: "var(--fg-3)" }}>
                값
              </span>
              <span />
            </div>
            <div style={{ marginTop: 6 }} className="flex flex-col gap-2">
              {envRows.map((row) => (
                <div
                  key={row.id}
                  className="grid items-center"
                  style={{
                    gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr) 32px",
                    columnGap: 18,
                  }}
                >
                  <input
                    className="kd-input font-mono"
                    value={row.key}
                    // 키는 대문자만 허용되는 규칙이라 입력 즉시 올려 준다(DeployForm과 동일).
                    onChange={(e) => patchRow(row.id, "key", e.target.value.toUpperCase())}
                    placeholder="VITE_API_URL"
                    spellCheck={false}
                    autoCapitalize="characters"
                    disabled={submitting}
                  />
                  <input
                    className="kd-input font-mono"
                    value={row.value}
                    onChange={(e) => patchRow(row.id, "value", e.target.value)}
                    placeholder={
                      user?.app_name ? `https://${apiHostOf(user.app_name)}` : "https://api.example.com"
                    }
                    spellCheck={false}
                    disabled={submitting}
                  />
                  <button
                    type="button"
                    onClick={() => removeRow(row.id)}
                    className="w-8 h-8 inline-flex items-center justify-center rounded-lg text-fg-3 hover:text-fg-1 transition-colors"
                    title="삭제"
                    aria-label="변수 삭제"
                    disabled={submitting}
                  >
                    <Trash2 size={15} strokeWidth={1.7} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setEnvRows((rows) => [...rows, newRow()])}
              className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5"
              style={{ marginTop: 10 }}
              disabled={submitting}
            >
              <Plus size={15} strokeWidth={1.9} />
              환경변수 추가
            </button>
          </div>

          {/* ─────────── 우: 정보 레일 (시안 세로 괘선 x1092) ─────────── */}
          <aside
            // 좁은 화면에서는 한 단으로 쌓이므로 세로 괘선 대신 가로 괘선으로 나눈다.
            className="border-t lg:border-t-0 lg:border-l lg:pl-[34px] border-kd-border"
            style={{ paddingTop: 40, paddingBottom: 24 }}
          >
            <div className="kd-t-label" style={{ color: "var(--fg-3)" }}>
              연결할 앱
            </div>
            {user?.app_name ? (
              <>
                {/* 앱 이름 — 시안 잉크 y195 (굵은 산세리프) */}
                <div className="kd-t-title truncate" style={{ color: "var(--fg-1)", marginTop: 8 }}>
                  {user.app_name}
                </div>
                <div style={{ marginTop: 10 }}>
                  <PodStatus status={podStatus} />
                </div>
                {/* 호스트 — 정적이 켜지면 서버는 {app}-api로 옮겨간다 */}
                <a
                  href={`https://${apiHostOf(user.app_name)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="kd-t-body-s inline-flex items-center gap-1.5 no-underline hover:underline"
                  style={{ color: "var(--fg-2)", marginTop: 10 }}
                >
                  <span className="truncate">{apiHostOf(user.app_name)}</span>
                  <ArrowUpRight size={15} strokeWidth={1.7} style={{ color: "var(--fg-3)" }} />
                </a>
              </>
            ) : (
              <div className="kd-t-body-s break-keep" style={{ color: "var(--fg-3)", marginTop: 8 }}>
                {loaded ? "아직 배포한 앱이 없어요. 프론트엔드만 단독으로 배포할 수 있어요." : "불러오는 중…"}
              </div>
            )}

            {/* 좁은 레일이라 한국어 어절이 잘리지 않게 keep-all (제목 램프와 같은 규칙) */}
            <div
              className="break-keep"
              style={{ borderTop: "1px solid var(--kd-border)", marginTop: 22, paddingTop: 22 }}
            >
              <p className="kd-t-body-s" style={{ color: "var(--fg-2)" }}>
                프론트엔드에서 사용할 API 주소를 설정하세요.
              </p>
              {/* 빌드 환경변수는 서버 Secret과 다르다 — 번들에 그대로 남는다는 사실을 알린다. */}
              <p className="kd-t-caption" style={{ color: "var(--fg-3)", marginTop: 14 }}>
                빌드 환경변수는 빌드할 때 결과 파일에 그대로 새겨져요. 브라우저로 받은 파일을 열면
                누구나 볼 수 있으니, 비밀번호·키 같은 값은 앱 환경변수에 두세요.
              </p>
            </div>
          </aside>
        </div>
      </div>

      {/* ── 바닥 — 전체 폭 괘선(시안 y934) + 액션 바 ── */}
      <div style={{ borderTop: "1px solid var(--kd-border)" }}>
        <div className="kd-page">
          <div className="flex items-center gap-5" style={{ paddingBlock: 14 }}>
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="kd-t-label transition-colors"
              style={{ color: "var(--fg-2)" }}
              disabled={submitting}
            >
              취소
            </button>
            {error && (
              <span className="kd-t-label min-w-0 truncate" style={{ color: "var(--err-fg)" }}>
                {error}
              </span>
            )}
            <button
              type="submit"
              disabled={disabled}
              className="kd-btn-primary kd-btn-md ml-auto inline-flex items-center gap-2 shrink-0"
            >
              {submitting ? "배포 요청 중…" : "프론트엔드 배포"}
            </button>
          </div>
        </div>
      </div>
    </form>
  );
}

// 라벨 + 컨트롤 — 시안 라벨 잉크→입력 12px(→9)
function Field({ label, style, children }) {
  return (
    <div style={style}>
      <div className="kd-t-body-s kd-strong" style={{ color: "var(--fg-1)", marginBottom: 8 }}>
        {label}
      </div>
      {children}
    </div>
  );
}

// 섹션 구분 괘선 — 시안 y499 / y734 (입력에서 26px 떨어짐 → 19)
function Rule() {
  return <div style={{ borderTop: "1px solid var(--kd-border)", marginBlock: 22 }} />;
}

// Pod 상태 — AppOverview/AppEnv와 같은 표기(같은 상태를 두 화면이 다르게 보이면 안 된다).
function PodStatus({ status }) {
  if (!status) return null;
  const s = APP_STATUS_STYLES[status] || { label: status };
  const ok = status === "running";
  const bad = status === "crashing";
  return (
    <span className="kd-t-label inline-flex items-center gap-1.5" style={{ color: "var(--fg-2)" }}>
      {bad ? (
        <CircleX size={18} strokeWidth={1.6} style={{ color: "var(--err-fg)" }} />
      ) : (
        <CircleCheck
          size={18}
          strokeWidth={1.6}
          style={{ color: ok ? "var(--ok-fg)" : "var(--fg-4)" }}
        />
      )}
      {s.label}
    </span>
  );
}
