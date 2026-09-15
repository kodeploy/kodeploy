// 앱 상세 · 환경변수 탭 — design/라이트모드-시안/15_환경변수.png 기준.
//
// 치수 주석의 숫자는 시안 원본 px이고, 실제 값은 ÷1.45(시안 스케일)한 CSS px이다.
// 레이아웃: 페이지 헤더 → 이름/값 2열 표 → 자동 주입 예약 키 접이식 → 바닥 고정 저장 바.
//
// 편집은 전부 로컬 상태다. 진실원은 Secret({app}-env)이고 PUT /deploy/env가 성공해야 바뀐다.
// 저장하면 백엔드가 Pod을 rolling restart 하므로 버튼 문구가 "저장 및 재배포"다.
// 검증 규칙(빈 키·중복·예약 키 충돌·대문자 키·개수/길이 상한)과 .env 직렬화는
// CommitListPanel의 EnvBody / DeployForm의 reservedConflicts에서 그대로 가져왔다.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  ChevronRight,
  CircleCheck,
  CircleX,
  Copy,
  Download,
  Eye,
  EyeOff,
  MoreHorizontal,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { getReservedKeys, setEnvVars } from "../../api/deploy.js";
import { APP_STATUS_STYLES } from "../AppStatusBadge.jsx";

// 백엔드 검증과 같은 규칙 (core/app/deploy/stack/env.py) — 여기서 먼저 막아 왕복을 아낀다.
const KEY_PATTERN = /^[A-Z_][A-Z0-9_]*$/;
const MAX_KEYS = 50;
const MAX_VALUE_LENGTH = 4096;

// 열 비율 — 시안 열 경계 x(이름 55→635, 값 635→1381, 액션 1381→1483)를 콘텐츠 폭 1428로 나눈 값.
// 41% / 나머지 / 70px 이면 1062폭에서 각각 403 / 509 / 70 CSS px이 된다(시안 400 / 514 / 70).
const GRID = {
  display: "grid",
  gridTemplateColumns: "41% minmax(0, 1fr) 70px",
  alignItems: "stretch",
};
// 셀 좌우 여백 — 시안: 표 왼쪽 끝→입력 22px(15), 입력→열 괘선 16px(11).
const PAD_FIRST = 15;
const PAD = 11;

// 값은 전부 가린 채로 시작한다.
//
// 예전엔 키 이름에 정규식(KEY|SECRET|TOKEN…)을 걸어 "비밀처럼 보이는" 행만 가렸는데,
// 그 추측이 계속 틀렸다 — KAKAO_CLIENT_ID, MAIL_USERNAME 처럼 이름만으로는 판단이 안 되고,
// DATABASE_URL·*_DSN 처럼 접속 문자열 안에 비밀번호가 박힌 값은 그대로 노출됐다.
// 백엔드는 어느 값이 민감한지 알려주지 않으므로(Secret을 전부 평문으로 내려준다) 프론트가
// 옳게 맞출 방법이 없다. 그래서 추측을 버리고 기본을 "가림"으로 두고, 행별 눈 아이콘과
// 헤더의 "값 보기"로 필요할 때 연다.

// .env 직렬화 — CommitListPanel의 envValueToken/rowsToEnvFile 그대로.
// 공백·#·따옴표·=·역슬래시가 든 값은 큰따옴표로 감싸고 이스케이프(다시 source 가능하게).
function envValueToken(v) {
  if (v === "" || /[\s#"'=\\]/.test(v)) {
    return `"${v.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n")}"`;
  }
  return v;
}
function rowsToEnvFile(rows) {
  const lines = rows
    .map(({ key, value }) => [key.trim(), value])
    .filter(([k]) => k)
    .map(([k, v]) => `${k}=${envValueToken(v)}`);
  return lines.length ? lines.join("\n") + "\n" : "";
}

// 위 직렬화의 역연산. 따옴표 안은 한 번만 훑어서 푼다(\\ → \, \" → ", \n → 줄바꿈).
function parseEnvFile(text) {
  const out = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim().replace(/^export\s+/, "");
    let v = line.slice(eq + 1).trim();
    const q = v[0];
    if (v.length >= 2 && (q === '"' || q === "'") && v.at(-1) === q) {
      v = v.slice(1, -1);
      if (q === '"') {
        let s = "";
        for (let i = 0; i < v.length; i++) {
          if (v[i] === "\\" && i + 1 < v.length) {
            const n = v[++i];
            s += n === "n" ? "\n" : n;
          } else s += v[i];
        }
        v = s;
      }
    }
    out.push({ key, value: v });
  }
  return out;
}

// 여러 이름을 한국어로 잇는다 — 마지막 앞만 "와/과", 나머지는 쉼표.
function joinKo(names) {
  if (names.length <= 1) return names[0] || "";
  const last = names[names.length - 1];
  const head = names.slice(0, -1).join(", ");
  return `${head}와 ${last}`;
}

let seq = 0;
const toRows = (env) => {
  const entries = Object.entries(env || {});
  const rows = entries.map(([key, value]) => ({
    id: ++seq,
    key,
    value,
    visible: false,
  }));
  // 빈 표 대신 입력 준비가 된 행 하나 — EnvBody와 같은 규칙.
  return rows.length ? rows : [{ id: ++seq, key: "", value: "", visible: true }];
};

// 행 배열 → 저장할 dict. 빈 KEY는 무시(EnvBody와 동일), 같은 KEY면 뒤 행이 이긴다.
const toDict = (rows) => {
  const env = {};
  for (const { key, value } of rows) {
    const k = key.trim();
    if (!k) continue;
    env[k] = value;
  }
  return env;
};
const sameEnv = (a, b) => {
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  return ka.length === kb.length && ka.every((k, i) => kb[i] === k && a[k] === b[k]);
};

export default function AppEnv() {
  const { user, serverBuild, slotStatus, envVars } = useOutletContext();

  const [rows, setRows] = useState(null); // null = 아직 초기 로드 전
  const [saved, setSaved] = useState(null); // 마지막으로 저장된 env (변경 여부 판정 기준)
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null); // 저장됨 / 가져오기 안내
  const [reservedMap, setReservedMap] = useState(null);
  const [reservedOpen, setReservedOpen] = useState(false);
  const fileRef = useRef(null);

  // AppLayout이 한 번 받아온 env로 초기화. 이후 화면 상태는 여기서만 움직인다.
  useEffect(() => {
    if (!envVars || rows) return;
    setRows(toRows(envVars));
    setSaved(envVars);
  }, [envVars, rows]);

  // dep별 예약 키 맵 1회 로드. 실패는 무시 — 백엔드 PUT이 최종 방어선이다(DeployForm과 같은 태도).
  useEffect(() => {
    getReservedKeys()
      .then(setReservedMap)
      .catch(() => {});
  }, []);

  // 지금 켜진 의존성이 자동 주입하는 예약 키 — DeployForm의 activeReserved 규칙 그대로.
  const reservedGroups = useMemo(() => {
    if (!reservedMap || !serverBuild) return [];
    const g = [];
    const db = serverBuild.db_type;
    if (db === "mysql" || db === "postgres")
      g.push({
        label: "데이터베이스",
        name: db === "mysql" ? "MySQL" : "PostgreSQL",
        keys: reservedMap[db] || [],
      });
    if (serverBuild.use_redis)
      g.push({ label: "Redis", name: "Redis", keys: reservedMap.redis || [] });
    if (serverBuild.use_storage)
      g.push({
        label: "오브젝트 스토리지",
        name: "오브젝트 스토리지",
        keys: reservedMap.storage || [],
      });
    return g.filter((x) => x.keys.length);
  }, [reservedMap, serverBuild]);

  const reservedKeys = useMemo(
    () => new Set(reservedGroups.flatMap((g) => g.keys)),
    [reservedGroups],
  );
  const reservedCount = reservedKeys.size;

  const dict = useMemo(() => (rows ? toDict(rows) : {}), [rows]);
  const dirty = rows && saved ? !sameEnv(dict, saved) : false;

  // 저장 전 검증 — 문제가 있으면 저장 버튼을 막고 바닥 바에 이유를 띄운다.
  const issue = useMemo(() => {
    if (!rows) return null;
    const keys = rows.map((r) => r.key.trim());
    if (rows.some((r, i) => !keys[i] && r.value)) return "이름이 비어 있는 행이 있어요.";
    const bad = keys.filter((k) => k && !KEY_PATTERN.test(k));
    if (bad.length) return `${bad.join(", ")} - 이름은 영문 대문자·숫자·_ 만 쓸 수 있어요.`;
    const seen = new Set();
    const dup = new Set();
    for (const k of keys) {
      if (!k) continue;
      if (seen.has(k)) dup.add(k);
      seen.add(k);
    }
    if (dup.size) return `${[...dup].join(", ")} - 이름이 중복됐어요.`;
    const clash = [...new Set(keys.filter((k) => reservedKeys.has(k)))];
    if (clash.length)
      return `${clash.join(", ")} - 자동으로 제공되는 연결 정보라 직접 넣을 수 없어요.`;
    if (seen.size > MAX_KEYS) return `환경변수는 최대 ${MAX_KEYS}개예요.`;
    const long = rows.find((r) => r.value.length > MAX_VALUE_LENGTH);
    if (long) return `${long.key} - 값이 ${MAX_VALUE_LENGTH}자를 넘었어요.`;
    return null;
  }, [rows, reservedKeys]);

  const patch = useCallback((id, field, val) => {
    setRows((r) => r.map((row) => (row.id === id ? { ...row, [field]: val } : row)));
    setNotice(null);
    setError(null);
  }, []);


  // 값 일괄 보기/숨기기 — 하나라도 가려져 있으면 전부 열고, 다 열려 있으면 전부 닫는다.
  const allVisible = !!rows && rows.length > 0 && rows.every((r) => r.visible);
  const toggleAll = () =>
    setRows((prev) => prev.map((r) => ({ ...r, visible: !allVisible })));

  const addRow = () => {
    setRows((r) => [...r, { id: ++seq, key: "", value: "", visible: true }]);
    setNotice(null);
  };

  // 마지막 한 행을 지우면 빈 행으로 되돌린다 — 표가 통째로 사라지지 않게(EnvBody와 동일).
  const removeRow = (id) =>
    setRows((r) =>
      r.length === 1
        ? [{ id: ++seq, key: "", value: "", visible: true }]
        : r.filter((row) => row.id !== id),
    );

  const onExport = () => {
    const blob = new Blob([rowsToEnvFile(rows)], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = ".env";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  // 가져오기는 덮어쓰기가 아니라 병합 — 파일에 없는 기존 값을 말없이 지우지 않는다.
  const onImport = async (file) => {
    if (!file) return;
    const parsed = parseEnvFile(await file.text());
    if (!parsed.length) {
      setError("불러올 값이 없어요. KEY=value 형식인지 확인해 주세요.");
      return;
    }
    setError(null);
    setRows((r) => {
      const next = [...r];
      for (const { key, value } of parsed) {
        const k = key.trim().toUpperCase();
        const hit = next.findIndex((row) => row.key.trim() === k);
        if (hit >= 0) next[hit] = { ...next[hit], value, visible: false };
        else next.push({ id: ++seq, key: k, value, visible: false });
      }
      // 가져오기 전 표가 빈 행 하나뿐이었다면 그 행은 버린다.
      return next.filter((row) => row.key.trim() || row.value);
    });
    setNotice(`${parsed.length}개를 불러왔어요. 저장해야 반영돼요.`);
  };

  const onReset = () => {
    setRows(toRows(saved));
    setError(null);
    setNotice(null);
  };

  const onSave = async () => {
    if (!dirty || issue || saving) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await setEnvVars(dict);
      const next = res.env || dict;
      setSaved(next);
      setRows(toRows(next)); // 편집 상태 초기화 — 빈 행 정리 + 비밀값 다시 마스킹
      setNotice("저장됨 · 새 값으로 앱을 다시 시작하고 있어요.");
    } catch (err) {
      setError(err.message || "저장 실패");
    } finally {
      setSaving(false);
    }
  };

  const podStatus = slotStatus?.server?.status || slotStatus?.status || null;
  const footer = error || issue
    ? { text: error || issue, color: "var(--err-fg)" }
    : notice
      ? { text: notice, color: "var(--ok-fg)" }
      : { text: "저장한 값으로 앱을 다시 배포합니다.", color: "var(--fg-3)" };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex-1 min-h-0 overflow-auto scroll-thin">
        <div className="kd-page" style={{ paddingBottom: 40 }}>
          {/* ── 페이지 헤더 (시안 제목 잉크 y208, 설명 y262) ── */}
          <div className="flex items-start gap-6 flex-wrap" style={{ paddingTop: 28 }}>
            <div className="min-w-0">
              <h1 className="kd-t-title" style={{ color: "var(--fg-1)" }}>
                환경변수
              </h1>
              <p className="kd-t-body-s" style={{ color: "var(--fg-2)", marginTop: 4 }}>
                앱 실행에 필요한 값을 관리하세요.
              </p>
            </div>
            <div className="ml-auto flex items-center gap-2.5 shrink-0" style={{ paddingTop: 6 }}>
              <span className="kd-t-section" style={{ color: "var(--fg-1)" }}>
                {user.app_name}
              </span>
              <span className="kd-t-label" style={{ color: "var(--fg-4)" }}>
                ·
              </span>
              <PodStatus status={podStatus} />
            </div>
          </div>

          {/* ── 표 위 액션 (시안: 버튼 우측 정렬, 상자 137x41 → 94x28) ── */}
          <div className="flex items-center justify-end gap-4 flex-wrap" style={{ marginTop: 2 }}>
            {/* 값은 기본으로 가려져 있다. 평범한 설정값을 확인할 때 행마다 누르지 않도록 일괄 토글을 둔다. */}
            <QuietButton
              onClick={toggleAll}
              icon={allVisible ? EyeOff : Eye}
              disabled={!rows || !rows.length}
            >
              {allVisible ? "값 숨기기" : "값 보기"}
            </QuietButton>
            <QuietButton onClick={onExport} icon={Download} disabled={!rows}>
              내보내기
            </QuietButton>
            <QuietButton onClick={() => fileRef.current?.click()} icon={Upload} disabled={!rows}>
              가져오기
            </QuietButton>
            <input
              ref={fileRef}
              type="file"
              accept=".env,text/plain"
              hidden
              onChange={(e) => {
                onImport(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            <button
              onClick={addRow}
              disabled={!rows}
              className="kd-btn-secondary kd-btn-sm inline-flex items-center gap-1.5"
            >
              <Plus size={15} strokeWidth={1.9} />
              변수 추가
            </button>
          </div>

          {/* ── 이름/값 표 (시안 y345→700, 머리 41px→28, 행 76px→52) ── */}
          <div
            style={{
              marginTop: 12,
              background: "var(--kd-surface)",
              // 시안은 표가 네 변이 닫힌 상자다(예전엔 행 아래 헤어라인만 있었다)
              border: "1px solid var(--kd-border)",
              borderRadius: 8,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                ...GRID,
                height: "var(--row-md)",
                background: "var(--sel-soft)",
                borderBottom: "1px solid var(--kd-border)",
              }}
            >
              <HeadCell pad={PAD_FIRST}>이름</HeadCell>
              <HeadCell pad={PAD} divider>
                값
              </HeadCell>
              <div style={{ borderLeft: "1px solid var(--kd-border)" }} />
            </div>

            {!rows ? (
              <div
                className="kd-t-body-s"
                style={{
                  color: "var(--fg-3)",
                  padding: `10px ${PAD_FIRST}px`,
                  borderBottom: "1px solid var(--kd-border)",
                }}
              >
                불러오는 중…
              </div>
            ) : (
              rows.map((row) => (
                <EnvRow
                  key={row.id}
                  row={row}
                  onPatch={patch}
                  onRemove={() => removeRow(row.id)}
                />
              ))
            )}
          </div>

          {/* ── 자동 주입 예약 키 (시안 y747 제목 / y780 설명) ── */}
          {reservedCount > 0 && (
            <div style={{ marginTop: 20 }}>
              <button
                onClick={() => setReservedOpen((v) => !v)}
                className="flex items-start gap-5 text-left w-full"
                style={{ paddingBlock: 10, paddingLeft: 10 }}
                aria-expanded={reservedOpen}
              >
                <ChevronRight
                  size={18}
                  strokeWidth={1.9}
                  style={{
                    color: "var(--fg-3)",
                    marginTop: 2,
                    flexShrink: 0,
                    transform: reservedOpen ? "rotate(90deg)" : "none",
                    transition: "transform 150ms ease",
                  }}
                />
                <span className="min-w-0">
                  <span className="kd-t-label block" style={{ color: "var(--fg-1)" }}>
                    자동으로 제공되는 연결 정보
                    <span style={{ color: "var(--fg-3)" }}>{`  ·  ${reservedCount}개`}</span>
                  </span>
                  <span
                    className="kd-t-caption block"
                    style={{ color: "var(--fg-3)", marginTop: 2 }}
                  >
                    {joinKo(reservedGroups.map((g) => g.label))} 연결 정보
                  </span>
                </span>
              </button>

              {reservedOpen && (
                <div style={{ paddingLeft: 48, paddingTop: 6 }}>
                  <p className="kd-t-caption" style={{ color: "var(--fg-3)", marginBottom: 14 }}>
                    앱을 배포할 때 자동으로 넣어 주는 값이에요. 직접 고치거나 같은 이름을 쓸 수 없어요.
                  </p>
                  {reservedGroups.map((g) => (
                    <div key={g.label} style={{ marginBottom: 18 }}>
                      <div
                        className="kd-t-micro"
                        style={{ color: "var(--fg-3)", marginBottom: 8 }}
                      >
                        {g.name}
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {g.keys.map((k) => (
                          <span
                            key={k}
                            className="kd-chip"
                            style={{ color: "var(--fg-2)" }}
                          >
                            {k}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── 바닥 고정 바 (시안 괘선 y857, 버튼 상자 151x51 → 104x36) ── */}
      <div className="kd-page shrink-0">
        <div
          className="flex items-center gap-5 flex-wrap"
          style={{ borderTop: "1px solid var(--kd-border)", paddingBlock: 20 }}
        >
          <span className="kd-t-label min-w-0 truncate" style={{ color: footer.color }}>
            {footer.text}
          </span>
          <button
            onClick={onReset}
            disabled={!dirty || saving}
            className="kd-t-label ml-auto transition-colors disabled:opacity-40"
            style={{ color: "var(--fg-2)" }}
          >
            변경 취소
          </button>
          <button
            onClick={onSave}
            disabled={!dirty || !!issue || saving}
            className="kd-btn-primary kd-btn-md"
          >
            {saving ? "저장 중…" : "저장 및 재배포"}
          </button>
        </div>
      </div>
    </div>
  );
}

// 표 머리 셀 — 12px 미니 라벨(시안 y363 잉크 18px).
function HeadCell({ pad, divider, children }) {
  return (
    <div
      className="kd-t-micro flex items-center"
      style={{
        color: "var(--fg-3)",
        paddingLeft: pad,
        borderLeft: divider ? "1px solid var(--kd-border)" : "none",
      }}
    >
      {children}
    </div>
  );
}

// 값 한 줄 — 이름 입력 / 값 입력(+비밀값 눈 토글) / 더보기 메뉴.
// 시안: 입력 높이 47px(→32=var(--row-sm)), 행 높이 76px(→52 = 32 + 위아래 10).
function EnvRow({ row, onPatch, onRemove }) {
  const masked = !row.visible;
  const showEye = true;

  return (
    <div style={{ ...GRID, borderBottom: "1px solid var(--kd-border)" }}>
      <div style={{ paddingLeft: PAD_FIRST, paddingRight: PAD, paddingBlock: 10 }}>
        <input
          value={row.key}
          // 백엔드 키 규칙이 대문자라 입력 단계에서 올려 준다(EnvBody와 동일).
          onChange={(e) => onPatch(row.id, "key", e.target.value.toUpperCase())}
          placeholder="NAME"
          spellCheck={false}
          autoCapitalize="characters"
          aria-label="환경변수 이름"
          className="kd-input"
          style={{ height: "var(--row-sm)" }}
        />
      </div>

      <div
        className="relative"
        style={{
          paddingInline: PAD,
          paddingBlock: 10,
          borderLeft: "1px solid var(--kd-border)",
        }}
      >
        <input
          // 가린 상태에서는 점만 보여주고 읽기 전용. 포커스하면 바로 평문으로 바뀐다(EnvBody 규칙).
          // 점 개수를 값 길이에 맞추면 길이가 새어 나가고("2" → 점 하나) 보기도 들쭉날쭉하다. 고정 길이로 둔다.
          value={masked ? "••••••••••••" : row.value}
          onChange={(e) => !masked && onPatch(row.id, "value", e.target.value)}
          onFocus={() => masked && onPatch(row.id, "visible", true)}
          readOnly={masked}
          placeholder="value"
          spellCheck={false}
          aria-label="환경변수 값"
          className="kd-input"
          style={{ height: "var(--row-sm)", paddingRight: showEye ? 34 : undefined }}
        />
        {showEye && (
          <button
            onClick={() => onPatch(row.id, "visible", !row.visible)}
            className="absolute flex items-center justify-center transition-colors"
            style={{ right: PAD + 6, top: "50%", transform: "translateY(-50%)", width: 22, height: 22, color: "var(--fg-3)" }}
            title={row.visible ? "값 숨기기" : "값 보기"}
            aria-label={row.visible ? "값 숨기기" : "값 보기"}
          >
            {row.visible ? (
              <EyeOff size={15} strokeWidth={1.7} />
            ) : (
              <Eye size={15} strokeWidth={1.7} />
            )}
          </button>
        )}
      </div>

      <div
        className="flex items-center justify-center"
        style={{ borderLeft: "1px solid var(--kd-border)" }}
      >
        <RowMenu row={row} onPatch={onPatch} onRemove={onRemove} />
      </div>
    </div>
  );
}

// 행 더보기 — 바깥 클릭/Esc로 닫힘(TopBar의 UserMenu와 같은 방식).
function RowMenu({ row, onPatch, onRemove }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const copy = () => {
    // 클립보드는 권한/보안 컨텍스트에 따라 막힐 수 있어 실패해도 조용히 넘어간다.
    navigator.clipboard?.writeText(row.value).catch(() => {});
    setOpen(false);
  };

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-center transition-colors"
        style={{ width: 28, height: 28, color: "var(--fg-3)" }}
        title="더보기"
        aria-label="더보기"
      >
        <MoreHorizontal size={18} strokeWidth={1.8} />
      </button>

      {open && (
        <div
          className="absolute right-0 kd-fade-in"
          style={{
            top: "calc(100% + 4px)",
            width: 152,
            paddingBlock: 4,
            background: "var(--kd-surface)",
            border: "1px solid var(--kd-border)",
            borderRadius: 8,
            boxShadow: "var(--shadow-pop)",
            zIndex: 30,
          }}
        >
          <MenuItem
            icon={row.visible ? EyeOff : Eye}
            onClick={() => {
              onPatch(row.id, "visible", !row.visible);
              setOpen(false);
            }}
          >
            {row.visible ? "값 숨기기" : "값 보기"}
          </MenuItem>
          <MenuItem icon={Copy} onClick={copy}>
            값 복사
          </MenuItem>
          <MenuItem
            icon={Trash2}
            danger
            onClick={() => {
              onRemove();
              setOpen(false);
            }}
          >
            삭제
          </MenuItem>
        </div>
      )}
    </div>
  );
}

function MenuItem({ icon: Icon, danger, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className="kd-hoverable kd-t-label w-full flex items-center gap-2.5 text-left"
      style={{
        height: "var(--row-sm)",
        paddingInline: 12,
        color: danger ? "var(--err-fg)" : "var(--fg-2)",
      }}
    >
      <Icon size={15} strokeWidth={1.8} />
      {children}
    </button>
  );
}

// 표 위 보조 동작 — 시안엔 없지만 EnvBody의 .env 내보내기를 잃지 않으려고 남긴 자리.
// "변수 추가"보다 확실히 약하게(테두리 없는 회색 글자) 둬서 시안의 위계를 흐리지 않는다.
function QuietButton({ icon: Icon, onClick, disabled, children }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="kd-t-label inline-flex items-center gap-1.5 transition-colors disabled:opacity-40"
      style={{ color: "var(--fg-3)" }}
    >
      <Icon size={15} strokeWidth={1.8} />
      {children}
    </button>
  );
}

// 지금 살아 있나 — AppStatusBadge의 라벨 맵 재사용(단일 진실원).
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
