// 새 앱 배포 마법사 (/deploy) — 3단계: 저장소 연결 → 실행 환경 → 확인 및 배포.
//
// 이 파일은 "상태 컨테이너"다. 옛 DeployForm.jsx 한 장에 있던 입력 상태·검증 effect·제출 로직을
// 그대로 옮겨 두고, 각 단계는 값을 props로 받아 그리기만 하는 순수 컴포넌트로 뒀다.
// 단계 컴포넌트만 갈아 끼우므로 1↔3단계를 오가도 입력이 남는다.
//
// 정적(프론트엔드) 슬롯 입력은 /deploy/frontend 로 분리했다. 다만 use_static 계열 상태와
// createDeploy 페이로드 필드는 남겨 둔다 — 재배포 시 기존 사이트를 teardown 하지 않기 위해
// user.site_enabled 를 그대로 되돌려 보내야 하기 때문이다(빼면 사이트가 내려간다).
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  createDeploy,
  CUSTOM_DOMAIN_CNAME_TARGET,
  getEnvVars,
  getReservedKeys,
  listBuilds,
  listGithubBranches,
  listGithubRepos,
  RUNTIMES,
  setDomain,
  stageDump,
} from "../../api/deploy.js";
import { GITHUB_INSTALL_URL } from "../../api/auth.js";
import { useAuth } from "../../contexts/AuthContext.jsx";
import StepRepo, { STEP1_W } from "./steps/StepRepo.jsx";
import StepReview from "./steps/StepReview.jsx";
import StepRuntime from "./steps/StepRuntime.jsx";
import {
  DB_OPTIONS,
  DEFAULT_PORTS,
  RUNTIME_META,
  STORAGE_OPTIONS,
  buildModeLabel,
  labelOf,
  repoSlugOf,
} from "./steps/options.js";
import { RAIL_W, StepRail, WizardFooter } from "./steps/wizardParts.jsx";

export default function DeployWizard({ onRequestGuide }) {
  const navigate = useNavigate();
  const { user, openLogin, refresh } = useAuth();
  // 1유저=1앱 — user.app_name이 있으면 첫 배포가 끝난 상태. 이름은 그때 확정되고 이후 고정된다.
  const isFirstDeploy = !user?.app_name;

  const [step, setStep] = useState(1);

  const [repoUrl, setRepoUrl] = useState("");          // 서버 슬롯 GitHub 링크
  const [ghRepos, setGhRepos] = useState([]);          // 연결된 installation repo 목록 (비공개 repo 선택용)
  const [repoListOpen, setRepoListOpen] = useState(false); // "불러오기"로 여닫는 연결 저장소 목록
  const [ghBranches, setGhBranches] = useState([]);    // 선택 repo의 브랜치 목록 (브랜치 드롭다운)
  const [name, setName] = useState("");
  const [branch, setBranch] = useState("main");
  const [port, setPort] = useState(DEFAULT_PORTS[RUNTIMES[0]] ?? 80);
  const [buildMode, setBuildMode] = useState("detect"); // detect=자동감지(기본) / dockerfile / auto(nixpacks)
  const [dockerfilePath, setDockerfilePath] = useState("Dockerfile");
  const [projectPath, setProjectPath] = useState("");
  // 정적 슬롯 — 입력 화면은 /deploy/frontend 로 분리했지만 상태는 남겨 페이로드로 되돌려 보낸다.
  const [useStatic, setUseStatic] = useState(false);
  const [staticRepoUrl, setStaticRepoUrl] = useState("");
  const [staticBranch, setStaticBranch] = useState("");
  const [staticProjectPath, setStaticProjectPath] = useState("");
  const [buildCmd, setBuildCmd] = useState("npm ci && npm run build");
  const [outputDir, setOutputDir] = useState("dist");
  // 정적 빌드 타임 변수 — 번들에 공개되는 값(VITE_*)이라 visibility 토글 없는 단순 key/value
  const [staticEnvRows, setStaticEnvRows] = useState([{ key: "", value: "" }]);
  const [dbType, setDbType] = useState("none");
  const [useRedis, setUseRedis] = useState(false);
  // 영속저장소(런타임 무관) — 단일 셀렉터. "none"=ephemeral / "local"=PVC / "object"=R2.
  const [storage, setStorage] = useState("none");
  const [volumeMountPath, setVolumeMountPath] = useState("/var/www/html/data"); // local 전용
  const [runtime, setRuntime] = useState(RUNTIMES[0]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  // 접이식 — 시안 기본값은 전부 접힘.
  const [showEnv, setShowEnv] = useState(false);              // 2단계 환경변수 / 3단계 요약 공용
  const [showInitDump, setShowInitDump] = useState(false);
  const [showCustomDomain, setShowCustomDomain] = useState(false);
  // 초기 DB 덤프 파일 — DB 선택 + 첨부 시 배포 후 자동 복원 (stage → token → 자동 restore).
  const [initDumpFile, setInitDumpFile] = useState(null);
  // 커스텀 도메인(서브도메인 전용) — 배포 성공 후 setDomain 호출. 입력 안 하면 스킵.
  const [customDomain, setCustomDomain] = useState("");
  // 환경변수 row 편집. 재배포 시점에 기존 env를 받아와 채운다. 첫 배포면 빈 row 하나.
  const [envRows, setEnvRows] = useState([{ key: "", value: "", visible: true }]);
  // repo + branch 유효성 — "idle" | "checking" | "ok" | "notfound" | "invalid" | "error"
  const [repoCheck, setRepoCheck] = useState({ state: "idle" });
  // dep별 자동 주입 env 키 맵 — 로그인 시 1회 fetch, 이후 토글/타이핑은 로컬 비교.
  const [reservedMap, setReservedMap] = useState(null);

  // runtime 변경 시 기본 포트 자동 적용. 초기 복원 중에는 skip해야 저장된 포트를 덮지 않는다.
  const restoredRef = useRef(false);

  // 재배포 시 기존 세팅 복원 — 최신 빌드 + 환경변수를 폼에 채움
  useEffect(() => {
    if (!user?.app_name) {
      restoredRef.current = true;
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [builds, envData] = await Promise.all([listBuilds(), getEnvVars()]);
        if (cancelled) return;
        // 슬롯별 최신 빌드 — 서버(runtime≠static)와 정적을 따로 복원
        const serverLatest = builds.find((b) => b.kind !== "env_change" && b.runtime !== "static");
        const staticLatest = builds.find((b) => b.kind !== "env_change" && b.runtime === "static");
        if (serverLatest) {
          setRepoUrl(serverLatest.repo_url.replace(/\.git$/, ""));
          setBranch(serverLatest.branch || "main");
          setRuntime(RUNTIMES.includes(serverLatest.runtime) ? serverLatest.runtime : RUNTIMES[0]);
          setDbType(serverLatest.db_type || "none");
          setUseRedis(serverLatest.use_redis || false);
          setStorage(serverLatest.storage || "none");
          if (serverLatest.volume_mount_path) setVolumeMountPath(serverLatest.volume_mount_path);
          setBuildMode(
            ["dockerfile", "auto"].includes(serverLatest.build_mode) ? serverLatest.build_mode : "auto",
          );
          if (serverLatest.build_mode === "dockerfile") {
            setDockerfilePath(serverLatest.dockerfile_path || "Dockerfile");
          }
          if (serverLatest.build_mode === "auto" && serverLatest.project_path) {
            setProjectPath(serverLatest.project_path);
          }
          setPort(serverLatest.port || DEFAULT_PORTS[serverLatest.runtime] || 80);
        } else if (user?.site_enabled) {
          // 정적 단독 구성 — 서버 "사용 안 함" 복원
          setRuntime("none");
        }
        // 정적 토글은 user.site_enabled(선언값)가 진실원 — 토글 off 후에도
        // 빌드 히스토리에 static 빌드가 남아 있으므로 히스토리로 판단하면 안 됨.
        setUseStatic(!!user?.site_enabled);
        if (staticLatest) {
          if (!serverLatest) {
            setStaticRepoUrl(staticLatest.repo_url.replace(/\.git$/, ""));
            setStaticBranch(staticLatest.branch || "");
          } else if (staticLatest.repo_url !== serverLatest.repo_url) {
            setStaticRepoUrl(staticLatest.repo_url.replace(/\.git$/, ""));
            if (staticLatest.branch !== serverLatest.branch) {
              setStaticBranch(staticLatest.branch || "");
            }
          }
          setStaticProjectPath(staticLatest.project_path || "");
          setBuildCmd(staticLatest.build_cmd ?? ""); // ""도 유효(빌드 없음) — ||로 덮으면 안 됨
          setOutputDir(staticLatest.output_dir || "dist");
          const staticEnvEntries = Object.entries(staticLatest.static_env || {});
          if (staticEnvEntries.length) {
            setStaticEnvRows(staticEnvEntries.map(([k, v]) => ({ key: k, value: v })));
          }
        }
        restoredRef.current = true;
        const entries = Object.entries(envData.env || {});
        if (entries.length) {
          setEnvRows(entries.map(([k, v]) => ({ key: k, value: v, visible: false })));
        }
      } catch {
        // 401 등 — 기본값 그대로
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [user?.app_name, user?.site_enabled]);

  // repo URL + branch 유효성 확인.
  // 연결된 repo(installation)면 ghRepos/ghBranches로 판정 — unauthenticated 404 오판 방지
  // (private도 연결돼 있으면 접근 가능하므로 "찾을 수 없음"으로 떨어지면 안 됨).
  // 미연결이면 디바운스 500ms 후 unauthenticated GitHub API로 확인(공개 repo만).
  useEffect(() => {
    const url = repoUrl.trim();
    if (!url) {
      setRepoCheck({ state: "idle" });
      return;
    }
    const m = url.match(/github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?\/?$/);
    if (!m) {
      setRepoCheck({ state: "invalid" });
      return;
    }
    const [, owner, repo] = m;
    const br = branch.trim() || "main";

    // 연결된 저장소면 접근 가능 확정 — 브랜치 목록이 있으면 브랜치 존재까지 판정, 없으면 통과.
    const slug = `${owner}/${repo}`.toLowerCase();
    const connected = ghRepos.some((r) => (r.full_name || "").toLowerCase() === slug);
    if (connected) {
      if (ghBranches.length > 0) {
        const hasBranch = ghBranches.some((b) => b.name === br);
        setRepoCheck({ state: hasBranch ? "ok" : "notfound" });
      } else {
        setRepoCheck({ state: "ok" });
      }
      return;
    }

    setRepoCheck({ state: "checking" });
    const timer = setTimeout(async () => {
      try {
        const res = await fetch(
          `https://api.github.com/repos/${owner}/${repo}/branches/${encodeURIComponent(br)}`,
          { headers: { Accept: "application/vnd.github+json" } },
        );
        if (res.status === 200) {
          setRepoCheck({ state: "ok" });
        } else if (res.status === 404) {
          setRepoCheck({ state: "notfound" });
        } else {
          setRepoCheck({ state: "error", code: res.status });
        }
      } catch {
        setRepoCheck({ state: "error" });
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [repoUrl, branch, ghRepos, ghBranches]);

  // repo가 유효하면 그 repo의 브랜치 목록을 받아 드롭다운 채움 (백엔드 경유 — private도).
  // repoCheck와 같은 500ms 디바운스. repo 형식이 안 맞으면 빈 목록.
  useEffect(() => {
    const url = repoUrl.trim();
    if (!url.match(/github\.com[/:]([^/]+)\/([^/]+?)(?:\.git)?\/?$/)) {
      setGhBranches([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      listGithubBranches(url)
        .then((bs) => !cancelled && setGhBranches(normalizeBranches(bs)))
        .catch(() => !cancelled && setGhBranches([]));
    }, 500);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [repoUrl]);

  // 연결됨(installation 있음)이면 접근 가능한 repo 목록 받아 드롭다운 채움. 미연결/실패면 빈 배열.
  useEffect(() => {
    if (!user?.github_connected) {
      setGhRepos([]);
      return;
    }
    let cancelled = false;
    listGithubRepos()
      .then((repos) => !cancelled && setGhRepos(repos || []))
      .catch(() => !cancelled && setGhRepos([]));
    return () => {
      cancelled = true;
    };
  }, [user?.github_connected]);

  // runtime 변경 시 default 포트 자동 적용. 초기 복원 중에는 skip.
  useEffect(() => {
    if (!restoredRef.current) return;
    const def = DEFAULT_PORTS[runtime];
    if (def) setPort(def);
  }, [runtime]);

  // dep 예약 키 맵 1회 로드 (로그인 후). 실패는 무시 — 백엔드 POST가 최종 방어선.
  useEffect(() => {
    if (!user) return;
    getReservedKeys().then(setReservedMap).catch(() => {});
  }, [user]);

  const addEnvRow = () => setEnvRows((r) => [...r, { key: "", value: "", visible: true }]);
  const removeEnvRow = (i) =>
    setEnvRows((r) =>
      r.length === 1 ? [{ key: "", value: "", visible: true }] : r.filter((_, idx) => idx !== i),
    );
  const updateEnvRow = (i, field, val) =>
    setEnvRows((r) => r.map((row, idx) => (idx === i ? { ...row, [field]: val } : row)));
  const toggleEnvVisible = (i) =>
    setEnvRows((r) => r.map((row, idx) => (idx === i ? { ...row, visible: !row.visible } : row)));

  const serverNone = runtime === "none";
  // 도메인 안내용 이름 — 첫 배포 입력값/확정 app_name, 없으면 placeholder
  const appLabel = user?.app_name || name.trim() || "앱이름";
  // 1차 repo — 서버 ON이면 서버 링크, OFF(정적 단독)면 프론트 링크가 곧 repo.
  const primaryRepo = (serverNone ? staticRepoUrl : repoUrl).trim();
  // 서버도 정적도 없으면 배포할 게 없음 / 1차 repo 비면 불가
  const disabled = !primaryRepo || submitting || (serverNone && !useStatic);

  // 지금 켜진 dep들이 자동 주입하는 예약 키 집합 (서버 슬롯에만 env 주입되므로 serverNone이면 빈 집합).
  // env가 이 키와 충돌하면 Option A로 유저 값이 이겨 관리형 연결이 깨지므로 폼에서 미리 막는다.
  const activeReserved = useMemo(() => {
    const s = new Set();
    if (serverNone || !reservedMap) return s;
    if (dbType === "mysql" || dbType === "postgres") (reservedMap[dbType] || []).forEach((k) => s.add(k));
    if (useRedis) (reservedMap.redis || []).forEach((k) => s.add(k));
    if (storage === "object") (reservedMap.storage || []).forEach((k) => s.add(k));
    return s;
  }, [serverNone, reservedMap, dbType, useRedis, storage]);
  const reservedConflicts = [
    ...new Set(envRows.map((r) => r.key.trim()).filter((k) => activeReserved.has(k))),
  ];

  const handleSubmit = async (e) => {
    e?.preventDefault?.();
    if (disabled) return;
    // 미로그인이면 API 호출 전에 LoginModal 띄움 (API도 401로 막지만 UX 단축)
    if (!user) {
      openLogin?.();
      return;
    }
    if (reservedConflicts.length > 0) {
      setError(
        `${reservedConflicts.join(", ")} 는 선택한 의존성이 자동 주입하는 예약 키예요. ` +
          `환경변수에서 빼거나 해당 의존성을 끄세요.`,
      );
      return;
    }
    setError(null);
    setSubmitting(true);
    // 환경변수 row → dict (빈 KEY는 무시, 같은 KEY 중복이면 뒤 row가 이김)
    const envDict = {};
    for (const { key, value } of envRows) {
      const k = key.trim();
      if (!k) continue;
      envDict[k] = value;
    }
    // 정적 빌드 타임 변수 — 동일 규칙
    const staticEnvDict = {};
    for (const { key, value } of staticEnvRows) {
      const k = key.trim();
      if (!k) continue;
      staticEnvDict[k] = value;
    }
    // 서버 사용 안 함이면 서버 전용 옵션(DB/캐시/스토리지/환경변수)은 무의미 — 폼 상태에 남은 옛 값 무시.
    const effectiveDbType = serverNone ? "none" : dbType;
    try {
      // 초기 DB 덤프가 있으면 먼저 stage → 토큰 발급 (mysql/postgres 선택 시에만 의미 있음).
      let initDumpToken = null;
      if ((effectiveDbType === "mysql" || effectiveDbType === "postgres") && initDumpFile) {
        const staged = await stageDump(initDumpFile);
        initDumpToken = staged.token;
      }
      await createDeploy({
        // 서버 OFF(정적 단독)면 프론트 링크가 곧 repo_url, static_repo_url은 비워 fallback.
        repoUrl: serverNone ? staticRepoUrl.trim() : repoUrl.trim(),
        // 첫 배포: 사용자가 입력한 이름 또는 자동 생성(서버 측). 두 번째부터는 user.app_name 재사용.
        name: isFirstDeploy ? name.trim() || undefined : undefined,
        branch: (serverNone ? staticBranch.trim() : branch.trim()) || "main",
        port: Number(port) || 80,
        runtime, // "python" | "java" | "php" | "javascript" | "none"
        dbType: effectiveDbType,
        useRedis: serverNone ? false : useRedis,
        storage: serverNone ? "none" : storage,
        volumeMountPath: !serverNone && storage === "local" ? volumeMountPath.trim() : "",
        buildMode: serverNone ? "auto" : buildMode, // 서버 없으면 미사용 — 스키마 통과용 placeholder
        dockerfilePath:
          !serverNone && buildMode === "dockerfile" ? dockerfilePath.trim() || "Dockerfile" : "Dockerfile",
        projectPath:
          !serverNone && buildMode !== "dockerfile" ? projectPath.trim().replace(/^\/+|\/+$/g, "") : "",
        useStatic,
        // 서버 ON일 때만 별도 정적 repo/branch — OFF면 repo_url이 곧 정적 repo라 비움.
        staticRepoUrl: useStatic && !serverNone ? staticRepoUrl.trim() : "",
        staticBranch: useStatic && !serverNone ? staticBranch.trim() : "",
        staticProjectPath: useStatic ? staticProjectPath.trim().replace(/^\/+|\/+$/g, "") : "",
        buildCmd: useStatic ? buildCmd.trim() : "",
        outputDir: useStatic ? outputDir.trim() : "",
        staticEnv: useStatic ? staticEnvDict : {},
        env: serverNone ? {} : envDict,
        initDumpToken,
      });
      // 첫 배포면 user.app_name이 백엔드에 박혔으니 AuthContext 갱신
      if (isFirstDeploy) await refresh();
      // 커스텀 도메인 입력 시 — 배포로 app_name 확정됐으니 이어서 연결 (서브도메인 전용).
      const cd = customDomain.trim();
      if (cd) {
        try {
          await setDomain(cd);
        } catch (err2) {
          setError(`배포는 시작됐어요. 단, 커스텀 도메인 연결 실패: ${err2.message}`);
          setSubmitting(false);
          return;
        }
      }
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

  // Enter 키로도 제출되므로 마지막 단계가 아니면 "다음"으로 돌린다.
  const handleFormSubmit = (e) => {
    if (step < 3) {
      e.preventDefault();
      setStep(step + 1);
      return;
    }
    handleSubmit(e);
  };

  const repoSlug = repoSlugOf(repoUrl);
  const repoIsPrivate = !!ghRepos.find(
    (r) => (r.full_name || "").toLowerCase() === (repoSlug || "").toLowerCase(),
  )?.private;
  // 두 슬롯 모델 — 정적 사이트가 켜져 있으면 {app}은 사이트, 서버는 {app}-api 로 붙는다.
  const appUrl = `${appLabel}${useStatic ? "-api" : ""}.kodeploy.com`;
  const envKeys = envRows.map((r) => r.key.trim()).filter(Boolean);

  // 3단계 요약 표 — "수정"이 가리킬 단계를 함께 들고 다닌다.
  const reviewRows = [
    { label: "소스", step: 1, parts: [repoSlug || repoUrl || "—", branch || "main"] },
    {
      label: "실행 환경",
      step: 2,
      parts: [RUNTIME_META[runtime]?.name || runtime, `포트 ${port}`, buildModeLabel(buildMode)],
    },
    {
      label: "데이터 연결",
      step: 2,
      parts:
        dbType === "none" && !useRedis
          ? ["사용 안 함"]
          : [
              ...(dbType === "none" ? [] : [labelOf(DB_OPTIONS, dbType)]),
              ...(useRedis ? ["Redis 사용"] : []),
            ],
    },
    {
      label: "스토리지",
      step: 2,
      parts: [
        labelOf(STORAGE_OPTIONS, storage),
        ...(storage === "object" ? ["R2"] : []),
        ...(storage === "local" && volumeMountPath.trim() ? [volumeMountPath.trim()] : []),
      ],
    },
  ];

  const nextDisabled = step === 1 ? !primaryRepo || submitting : submitting;

  return (
    <form onSubmit={handleFormSubmit} className="kd-page kd-fade-in" style={{ paddingBottom: 72 }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: `minmax(0, ${RAIL_W}px) minmax(0, 1fr)`,
          alignItems: "start",
        }}
      >
        <StepRail step={step} onJump={setStep} />

        <div>
          {step === 1 && (
            <StepRepo
              repoUrl={repoUrl}
              onRepoUrl={setRepoUrl}
              repoCheck={repoCheck}
              repoSlug={repoSlug || repoUrl}
              repoIsPrivate={repoIsPrivate}
              githubConnected={!!user?.github_connected}
              ghRepos={ghRepos}
              repoListOpen={repoListOpen}
              onToggleRepoList={() => setRepoListOpen((v) => !v)}
              onPickRepo={(r) => {
                setRepoUrl(r.html_url);
                setBranch(r.default_branch || "main");
                setRepoListOpen(false); // 고르면 목록은 닫는다
              }}
              ghBranches={ghBranches}
              branch={branch}
              onBranch={setBranch}
              projectPath={projectPath}
              onProjectPath={setProjectPath}
              submitting={submitting}
              installUrl={GITHUB_INSTALL_URL}
            />
          )}

          {step === 2 && (
            <StepRuntime
              isFirstDeploy={isFirstDeploy}
              appName={user?.app_name}
              name={name}
              onName={setName}
              port={port}
              onPort={setPort}
              buildMode={buildMode}
              onBuildMode={setBuildMode}
              dockerfilePath={dockerfilePath}
              onDockerfilePath={setDockerfilePath}
              runtime={runtime}
              onRuntime={setRuntime}
              dbType={dbType}
              onDbType={setDbType}
              useRedis={useRedis}
              onUseRedis={setUseRedis}
              storage={storage}
              onStorage={setStorage}
              volumeMountPath={volumeMountPath}
              onVolumeMountPath={setVolumeMountPath}
              envRows={envRows}
              onAddEnvRow={addEnvRow}
              onRemoveEnvRow={removeEnvRow}
              onUpdateEnvRow={updateEnvRow}
              onToggleEnvVisible={toggleEnvVisible}
              activeReserved={activeReserved}
              showEnv={showEnv}
              onToggleEnv={() => setShowEnv((v) => !v)}
              initDumpFile={initDumpFile}
              onInitDumpFile={setInitDumpFile}
              showInitDump={showInitDump}
              onToggleInitDump={() => setShowInitDump((v) => !v)}
              customDomain={customDomain}
              onCustomDomain={setCustomDomain}
              cnameTarget={CUSTOM_DOMAIN_CNAME_TARGET}
              showCustomDomain={showCustomDomain}
              onToggleCustomDomain={() => setShowCustomDomain((v) => !v)}
              repoSlug={repoSlug}
              branch={branch}
              appUrl={appUrl}
              submitting={submitting}
              onRequestGuide={onRequestGuide}
            />
          )}

          {step === 3 && (
            <StepReview
              appLabel={appLabel}
              appUrl={appUrl}
              rows={reviewRows}
              envCount={envKeys.length}
              envSummary={envKeys}
              showEnv={showEnv}
              onToggleEnv={() => setShowEnv((v) => !v)}
              onJump={setStep}
            />
          )}

          <WizardFooter
            maxWidth={step === 1 ? STEP1_W : undefined}
            error={error}
            note={step === 3 ? "소스를 빌드한 뒤 앱 서버를 실행합니다." : undefined}
            back={
              step === 1 ? (
                <button type="button" onClick={() => navigate(-1)} className="kd-btn-secondary kd-btn-lg">
                  취소
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setStep(step - 1)}
                  className="kd-btn-secondary kd-btn-lg"
                  style={{ display: "inline-flex", alignItems: "center", gap: 10 }}
                >
                  이전
                </button>
              )
            }
            next={
              step < 3 ? (
                // key로 노드 재사용을 끊는다. 같은 자리 같은 태그라 React가 노드를 그대로 쓰면
                // 2단계에서 누른 순간 type이 submit으로 바뀌고, 그 클릭의 기본 동작이
                // 그대로 폼 제출로 이어져 3단계(최종 확인)를 건너뛰고 배포가 시작됐다.
                <button
                  key="next"
                  type="button"
                  onClick={() => setStep(step + 1)}
                  disabled={nextDisabled}
                  className="kd-btn-primary kd-btn-lg"
                  style={{ display: "inline-flex", alignItems: "center", gap: 10 }}
                >
                  {step === 1 ? "다음: 실행 환경" : "다음: 최종 확인"}
                </button>
              ) : (
                <button
                  key="submit"
                  type="submit"
                  disabled={disabled}
                  className="kd-btn-primary kd-btn-lg"
                  style={{ display: "inline-flex", alignItems: "center", gap: 10 }}
                >
                  {submitting ? (
                    <>
                      <span
                        aria-hidden
                        className="kd-spin"
                        style={{
                          width: 14,
                          height: 14,
                          borderRadius: 999,
                          border: "2px solid var(--kd-border)",
                          borderTopColor: "var(--btn-primary-fg)",
                        }}
                      />
                      배포 요청 중
                    </>
                  ) : (
                    <>
                      배포 시작
                    </>
                  )}
                </button>
              )
            }
          />
        </div>
      </div>
    </form>
  );
}

// GET /deploy/github/branches 는 [{name, protected}]를 준다.
// 목록이 비거나 형태가 달라도 화면이 죽지 않도록 이름만 뽑아 정규화한다.
function normalizeBranches(bs) {
  if (!Array.isArray(bs)) return [];
  return bs
    .map((b) => (typeof b === "string" ? { name: b } : b))
    .filter((b) => b && typeof b.name === "string");
}
