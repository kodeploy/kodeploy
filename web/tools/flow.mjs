// 브라우저 사용자 흐름 검증기.
//
// 운영 백엔드에 붙은 로그인 세션 없이도 인증 화면을 검증하려고 API 응답을 가로챈다
// (tools/fixtures.mjs). 실제 백엔드 통합이 아니라 "화면 배선"을 확인하는 용도다.
//
// 사용: PW_CHROME=<chrome> node tools/flow.mjs <시나리오파일.mjs> [--out <디렉토리>]
// 시나리오 파일은 { scenarios: [{ name, viewport:[w,h], steps:[...] }] } 를 export 한다.
//
// step 종류:
//   {goto: "/path"}                       페이지 이동
//   {click: "셀렉터"}                      클릭 (텍스트 셀렉터 허용: text=배포 이력)
//   {fill: "셀렉터", value: "값"}          입력
//   {expect: "셀렉터"}                     보이는지 확인
//   {expectText: "문자열"}                 본문에 해당 문자열이 있는지 확인
//   {absent: "문자열"}                     본문에 없어야 하는 문자열
//   {wait: 800}                           대기(ms)
//   {shot: "이름"}                         스크린샷
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";
import { ROUTES } from "./fixtures.mjs";

const args = process.argv.slice(2);
const scenarioFile = args[0];
const outDir = args.includes("--out") ? args[args.indexOf("--out") + 1] : "/tmp/kd-flow";
const BASE = process.env.BASE || "http://127.0.0.1:5173";
const API = process.env.API_BASE || "http://localhost:8000";
// NO_STUB=1 이면 API를 가로채지 않고 실제 백엔드로 보낸다 (실서버 연동 검증용).
const NO_STUB = process.env.NO_STUB === "1";
// ANON=1 이면 /auth/me 를 401로 돌려 미로그인 화면(로그인 모달 등)을 찍을 수 있다.
// 시나리오 하나만 미로그인으로 찍고 싶으면 그 시나리오에 { anon: true }를 준다.
const ANON = process.env.ANON === "1";
// THEME=dark|light 면 페이지 로드 전에 테마를 박아 둔다(토글을 누르지 않고 바로 그 테마로).
const THEME = process.env.THEME || "";

fs.mkdirSync(outDir, { recursive: true });
const { scenarios } = await import(path.resolve(scenarioFile));

const browser = await chromium.launch({
  executablePath: process.env.PW_CHROME,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});

const report = [];
for (const sc of scenarios) {
  const [w, h] = sc.viewport || [1536, 1024];
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  if (THEME)
    await page.addInitScript((t) => {
      try {
        localStorage.setItem("kd-theme", t);
      } catch {}
      document.documentElement.dataset.theme = t;
    }, THEME);
  const errors = [];
  const steps = [];

  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text().slice(0, 240));
  });
  page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message.slice(0, 240)));
  // 실서버 모드에서는 어떤 요청이 어떤 상태로 돌아왔는지 기록해 둔다
  const calls = [];
  if (NO_STUB)
    page.on("response", (r) => {
      const u = r.url();
      if (u.includes("/auth/") || u.includes("/deploy") || u.includes("/community"))
        calls.push(`${r.status()} ${r.request().method()} ${new URL(u).pathname}`);
    });

  // 오브젝트 스토리지 주소 가로채기 — 미리보기(iframe/img)가 실제로 그려지는지 보려면
  // 파일 본문이 필요하다. 확장자별로 최소한의 실제 바이트를 돌려준다.
  const ASSET_BODY = {
    ".json": ["application/json", '{\n  "name": "my-api",\n  "runtime": "python",\n  "port": 8080\n}\n'],
    ".txt": ["text/plain; charset=utf-8", "sample\n"],
    ".svg": [
      "image/svg+xml",
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"><circle cx="60" cy="60" r="44" fill="none" stroke="#333" stroke-width="6"/><path d="M38 62 L54 78 L84 44" fill="none" stroke="#111" stroke-width="8" stroke-linecap="round"/></svg>',
    ],
    // 실제 PDF 뷰어가 뜨는지 보려면 유효한 파일이어야 한다 (tools/fixtures/report.pdf)
    ".pdf": ["application/pdf", fs.readFileSync(new URL("./fixtures/report.pdf", import.meta.url))],
  };
  if (!NO_STUB)
    await page.route("https://assets.example.com/**", async (route) => {
      const u = new URL(route.request().url());
      const ext = u.pathname.slice(u.pathname.lastIndexOf("."));
      const hit = ASSET_BODY[ext];
      if (!hit) return route.fulfill({ status: 404, body: "" });
      return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    });

  // API 가로채기 — 쓰기(POST/PUT/DELETE)는 요청 내용을 기록하고 성공 응답을 돌려준다.
  const writes = [];
  if (!NO_STUB)
  await page.route(`${API}/**`, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (req.method() !== "GET") {
      let body = null;
      try { body = req.postDataJSON(); } catch {}
      writes.push({ method: req.method(), path: p, body });
      const hit = ROUTES.find(([pre]) => p.startsWith(pre));
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hit ? hit[1]() : {}) });
    }
    if ((ANON || sc.anon) && p.startsWith("/auth/me"))
      return route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"인증 필요"}' });
    const hit = ROUTES.find(([pre]) => p.startsWith(pre));
    if (!hit) return route.fulfill({ status: 404, contentType: "application/json", body: '{"detail":"stub miss"}' });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(hit[1]()) });
  });

  for (const [i, step] of (sc.steps || []).entries()) {
    const label = JSON.stringify(step).slice(0, 110);
    try {
      if (step.goto) {
        await page.goto(BASE + step.goto, { waitUntil: "domcontentloaded", timeout: 20000 });
        await page.waitForTimeout(900);
      } else if (step.click) {
        await page.click(step.click, { timeout: 8000 });
        await page.waitForTimeout(600);
      } else if (step.fill) {
        await page.fill(step.fill, step.value, { timeout: 8000 });
      } else if (step.expect) {
        await page.waitForSelector(step.expect, { state: "visible", timeout: 8000 });
      } else if (step.expectText) {
        const body = await page.innerText("body");
        if (!body.includes(step.expectText)) throw new Error(`본문에 "${step.expectText}" 없음`);
      } else if (step.absent) {
        const body = await page.innerText("body");
        if (body.includes(step.absent)) throw new Error(`본문에 "${step.absent}" 가 있으면 안 됨`);
      } else if (step.wait) {
        await page.waitForTimeout(step.wait);
      } else if (step.shot) {
        const f = path.join(outDir, `${sc.name}-${step.shot}.png`);
        await page.screenshot({ path: f, fullPage: !!step.full });
        steps.push({ i, step: label, ok: true, shot: f });
        continue;
      }
      steps.push({ i, step: label, ok: true });
    } catch (e) {
      steps.push({ i, step: label, ok: false, error: e.message.slice(0, 200) });
      const f = path.join(outDir, `${sc.name}-FAIL-${i}.png`);
      await page.screenshot({ path: f }).catch(() => {});
    }
  }

  report.push({ scenario: sc.name, steps, writes, calls: calls.slice(0, 30), errors: errors.slice(0, 15) });
  await ctx.close();
}

await browser.close();
const failed = report.flatMap((r) => r.steps.filter((s) => !s.ok).map((s) => `${r.scenario}#${s.i} ${s.error}`));
console.log(JSON.stringify({ report, failedCount: failed.length, failed }, null, 1));
process.exit(failed.length ? 1 : 0);
