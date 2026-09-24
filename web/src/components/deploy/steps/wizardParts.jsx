// 배포 마법사 공통 조각 — 좌측 스텝 레일 · 필드 라벨 · 라디오/체크 · 접이식 · 하단 버튼 바.
//
// 왜 따로 두나: 3단계가 같은 골격(레일 + 폼 + 하단 버튼)을 공유하는데,
// 각 단계 컴포넌트가 제 나름의 치수를 들고 있으면 단계를 오갈 때 레이아웃이 흔들린다.
// 시안(1536폭 렌더, 스케일 1.39)에서 잰 값을 여기 한 곳에서만 CSS px로 환산해 둔다.
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ChevronRight } from "lucide-react";

// 시안 실측 → CSS 환산 (÷1.39). 레일 전체 폭 = 폼 시작 x348 - 콘텐츠 좌단 x56.
export const RAIL_W = 211;      // 시안 (348-56)/1.39
const CIRCLE = 29;              // 시안 원 지름 40~41
const CIRCLE_GAP = 16;          // 원 → 라벨 간격. 라벨 x119 → 콘텐츠 좌단 기준 45 = 29+16
const CONNECTOR = 33;           // 원 사이 세로선 길이. 원(29)+선(33) = 시안 피치 86/1.39 ≈ 62
export const RAIL_TOP = 60;     // 첫 원 상단 y167 → 상단바 아래 60

export const WIZARD_STEPS = [
  { id: 1, label: "저장소 연결" },
  { id: 2, label: "실행 환경" },
  { id: 3, label: "확인 및 배포" },
];

// 좌측 스텝 레일. 지난 단계는 체크된 괘선 원이고 누르면 그 단계로 돌아간다(입력은 보존).
export function StepRail({ step, onJump }) {
  return (
    <nav aria-label="배포 단계" style={{ paddingTop: RAIL_TOP }}>
      {WIZARD_STEPS.map((s, i) => {
        const done = s.id < step;
        const current = s.id === step;
        return (
          <div key={s.id}>
            {i > 0 && (
              <div
                aria-hidden
                style={{
                  width: 1,
                  height: CONNECTOR,
                  marginLeft: Math.round(CIRCLE / 2),
                  background: "var(--kd-border)",
                }}
              />
            )}
            <button
              type="button"
              onClick={() => done && onJump?.(s.id)}
              disabled={!done}
              aria-current={current ? "step" : undefined}
              style={{
                display: "flex",
                alignItems: "center",
                gap: CIRCLE_GAP,
                cursor: done ? "pointer" : "default",
                textAlign: "left",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: CIRCLE,
                  height: CIRCLE,
                  borderRadius: 999,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  background: current ? "var(--btn-primary-bg)" : "transparent",
                  border: `1px solid ${current ? "var(--btn-primary-bg)" : "var(--kd-border)"}`,
                  color: current ? "var(--btn-primary-fg)" : done ? "var(--fg-2)" : "var(--fg-4)",
                }}
                className="kd-t-micro"
              >
                {done ? <Check size={15} strokeWidth={2} /> : s.id}
              </span>
              <span
                className={current ? "kd-t-body-s kd-strong" : "kd-t-body-s"}
                style={{ color: current ? "var(--fg-1)" : done ? "var(--fg-2)" : "var(--fg-4)" }}
              >
                {s.label}
              </span>
            </button>
          </div>
        );
      })}
    </nav>
  );
}

// 단계 제목 — 마법사 h1은 큰 제목(.kd-t-display). 부제는 회색 본문.
export function StepHeading({ title, desc }) {
  return (
    <header style={{ marginBottom: 40 }}>
      <h1 className="kd-t-display" style={{ color: "var(--fg-1)" }}>
        {title}
      </h1>
      {desc && (
        <p className="kd-t-body" style={{ color: "var(--fg-3)", marginTop: 8 }}>
          {desc}
        </p>
      )}
    </header>
  );
}

// 입력 위 라벨. 시안 잉크 높이 18 → 13.5/600.
export function FieldLabel({ children, htmlFor }) {
  return (
    <label
      htmlFor={htmlFor}
      className="kd-t-label kd-strong"
      style={{ color: "var(--fg-1)", display: "block", marginBottom: 8 }}
    >
      {children}
    </label>
  );
}

// 필드 아래 회색 한 줄 설명.
export function FieldHint({ children }) {
  return (
    <p className="kd-t-caption" style={{ color: "var(--fg-3)", marginTop: 8 }}>
      {children}
    </p>
  );
}

// 고르는 칸 — 상자는 .kd-input, 펼친 목록은 소개 페이지 PickField·작업 공간 MiniSelect와 같은 모양
// (kd-card 면, --row-md 줄, 고른 값 --sel-soft · 호버 --sel).
// native <select>는 펼친 목록을 OS가 그려 다크 테마에서 흰 바탕에 흐린 글자로 나와서 직접 그린다.
// 키보드: 상자에서 Enter/Space/↓로 열고, ↑↓로 이동, Enter로 고르고, Esc·바깥 클릭으로 닫는다.
// options: [{ id, name }], onChange(id)
export function Select({ id, value, onChange, options, disabled, width, label }) {
  const [open, setOpen] = useState(false);
  const [hover, setHover] = useState(-1);
  const wrapRef = useRef(null);
  const btnRef = useRef(null);
  const current = options.find((o) => o.id === value);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => !wrapRef.current?.contains(e.target) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const openMenu = () => {
    setHover(Math.max(0, options.findIndex((o) => o.id === value)));
    setOpen(true);
  };
  const pick = (next) => {
    onChange(next);
    setOpen(false);
    btnRef.current?.focus();
  };

  const onKeyDown = (e) => {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHover((i) => Math.min(options.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHover((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[hover]) pick(options[hover].id);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} style={{ position: "relative", width: width || "100%", minWidth: 0 }}>
      <button
        ref={btnRef}
        id={id}
        type="button"
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={onKeyDown}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="kd-input"
        style={{
          display: "flex",
          alignItems: "center",
          textAlign: "left",
          cursor: disabled ? "default" : "pointer",
          borderColor: open ? "var(--fg-3)" : undefined,
        }}
      >
        <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {current?.name ?? "—"}
        </span>
        <ChevronDown
          aria-hidden
          size={15}
          strokeWidth={1.8}
          style={{
            flexShrink: 0,
            marginLeft: 8,
            color: "var(--fg-3)",
            transform: open ? "rotate(180deg)" : "none",
            transition: "transform 150ms ease",
          }}
        />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={label}
          onMouseLeave={() => setHover(-1)}
          className="kd-card"
          style={{
            position: "absolute",
            left: 0,
            top: "calc(100% + 4px)",
            zIndex: 30,
            width: "100%",
            minWidth: 140,
            maxHeight: 280,
            overflowY: "auto",
            paddingBlock: 6,
          }}
        >
          {options.map((o, i) => {
            const selected = o.id === value;
            return (
              <div
                key={o.id}
                role="option"
                aria-selected={selected}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o.id)}
                onMouseEnter={() => setHover(i)}
                className="kd-t-label"
                style={{
                  height: "var(--row-md)",
                  display: "flex",
                  alignItems: "center",
                  paddingInline: 14,
                  color: "var(--fg-1)",
                  cursor: "pointer",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  background: hover === i ? "var(--sel)" : selected ? "var(--sel-soft)" : "transparent",
                }}
              >
                {o.name}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// 라디오 — 시안의 원(26px≈19) + 라벨. 브라우저 기본 라디오는 테마색을 못 따라가서 직접 그린다.
export function Radio({ name, checked, onChange, disabled, children }) {
  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        cursor: disabled ? "default" : "pointer",
      }}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
      />
      <span
        aria-hidden
        style={{
          width: 19,
          height: 19,
          borderRadius: 999,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: `1px solid ${checked ? "var(--btn-primary-bg)" : "var(--kd-border)"}`,
          background: "var(--kd-surface)",
        }}
      >
        {checked && (
          <span style={{ width: 9, height: 9, borderRadius: 999, background: "var(--btn-primary-bg)" }} />
        )}
      </span>
      <span className="kd-t-body-s" style={{ color: "var(--fg-1)" }}>
        {children}
      </span>
    </label>
  );
}

// 체크박스 — 라디오와 같은 치수, 모서리만 4px.
export function Checkbox({ checked, onChange, disabled, children }) {
  return (
    <label
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        cursor: disabled ? "default" : "pointer",
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={onChange}
        disabled={disabled}
        style={{ position: "absolute", opacity: 0, width: 0, height: 0 }}
      />
      <span
        aria-hidden
        style={{
          width: 19,
          height: 19,
          borderRadius: 4,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: `1px solid ${checked ? "var(--btn-primary-bg)" : "var(--kd-border)"}`,
          background: checked ? "var(--btn-primary-bg)" : "var(--kd-surface)",
          color: "var(--btn-primary-fg)",
        }}
      >
        {checked && <Check size={15} strokeWidth={2.4} />}
      </span>
      <span className="kd-t-body-s" style={{ color: "var(--fg-1)" }}>
        {children}
      </span>
    </label>
  );
}

// 접이식 한 줄 — "＞ 환경변수 추가". 시안의 2·3단계 하단 패턴.
export function Disclosure({ open, onToggle, label, caption, children }) {
  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        style={{ display: "flex", alignItems: "center", gap: 12, height: 32, width: "100%" }}
      >
        <ChevronRight
          aria-hidden
          size={15}
          strokeWidth={2}
          style={{
            color: "var(--fg-3)",
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform 150ms ease",
          }}
        />
        <span className="kd-t-body-s kd-strong" style={{ color: "var(--fg-1)" }}>
          {label}
        </span>
        {caption && (
          <span className="kd-t-caption" style={{ color: "var(--fg-3)" }}>
            · {caption}
          </span>
        )}
      </button>
      {open && <div style={{ paddingTop: 16, paddingLeft: 27 }}>{children}</div>}
    </div>
  );
}

// 하단 버튼 바 — 위 괘선 + (안내 문구) + [보조] … [주요]. 시안 버튼 높이 57/1.39 ≈ 41 → .kd-btn-md(40).
export function WizardFooter({ maxWidth, back, next, error, note }) {
  return (
    <div style={{ maxWidth, marginTop: 40 }}>
      {error && (
        <div
          className="kd-t-body-s"
          role="alert"
          style={{
            marginBottom: 20,
            padding: "10px 14px",
            borderRadius: 4,
            border: "1px solid var(--err-fg)",
            background: "var(--sel-soft)",
            color: "var(--err-fg)",
          }}
        >
          {error}
        </div>
      )}
      <div style={{ borderTop: "1px solid var(--kd-border)", paddingTop: note ? 16 : 30 }}>
        {note && (
          <p className="kd-t-body-s" style={{ color: "var(--fg-3)", marginBottom: 14 }}>
            {note}
          </p>
        )}
        <div style={{ display: "flex", gap: 12 }}>
          {back}
          <div style={{ flex: 1 }} />
          {next}
        </div>
      </div>
    </div>
  );
}
