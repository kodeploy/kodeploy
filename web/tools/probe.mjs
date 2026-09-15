// DOM 계측기 — 실제 렌더된 박스 크기를 재서 시안과 대조한다.
// 사용: PW_CHROME=<chrome> node tools/probe.mjs <path> '<셀렉터,셀렉터,...>' [w] [h]
import { chromium } from "playwright-core";
import { ROUTES } from "./fixtures.mjs";

const [route, selectorList, w = "1536", h = "1024"] = process.argv.slice(2);
const selectors = (selectorList || "").split(",").map((s) => s.trim()).filter(Boolean);
const BASE = process.env.BASE || "http://127.0.0.1:5173";
const API = process.env.API_BASE || "http://localhost:8000";

const b = await chromium.launch({ executablePath: process.env.PW_CHROME, args: ["--no-sandbox"] });
const p = await b.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1 });
await p.route(`${API}/**`, (r) => {
  const path = new URL(r.request().url()).pathname;
  const hit = ROUTES.find(([pre]) => path.startsWith(pre));
  r.fulfill({
    status: hit ? 200 : 404,
    contentType: "application/json",
    body: JSON.stringify(hit ? hit[1]() : { detail: "stub miss " + path }),
  });
});
const errors = [];
p.on("pageerror", (e) => errors.push(e.message.slice(0, 200)));
await p.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(+(process.env.WAIT || 1500));

const out = await p.evaluate((sels) => {
  const info = (el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      x: Math.round(r.x), y: Math.round(r.y),
      w: Math.round(r.width), h: Math.round(r.height),
      font: `${cs.fontSize}/${cs.lineHeight} ${cs.fontWeight}`,
      family: cs.fontFamily.split(",")[0].replace(/"/g, ""),
      color: cs.color,
      text: (el.textContent || "").trim().slice(0, 40),
    };
  };
  const res = {};
  for (const s of sels) {
    const els = [...document.querySelectorAll(s)];
    res[s] = els.slice(0, 6).map(info);
  }
  return res;
}, selectors);

console.log(JSON.stringify({ route, viewport: [+w, +h], errors, boxes: out }, null, 1));
await b.close();
