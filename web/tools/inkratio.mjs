// 잉크 높이 → 폰트 크기 역산기.
//
// 시안에서 잰 "글자 잉크 높이"로 폰트 크기를 추정할 때, 잉크/em 비율을 눈대중하면 틀린다
// (한글만인지, 라틴 어센더가 있는지, 쉼표 디센더가 있는지에 따라 달라진다).
// 브라우저 캔버스의 TextMetrics는 실제 글리프의 상단/하단을 정확히 알려주므로,
// 같은 문자열을 같은 폰트로 100px에 그려 비율을 구하고 그걸로 나눈다.
//
// 사용: PW_CHROME=<chrome> node tools/inkratio.mjs '<JSON 배열>'
//   [{ "label":"히어로", "text":"만든 서비스,", "font":"700 100px \"Nanum Myeongjo\"",
//      "inkPx": 62, "scale": 1.068 }, ...]
//   inkPx  = 시안에서 잰 잉크 높이(시안 픽셀)
//   scale  = 시안/CSS 배율 (상단바 괘선으로 구한 값)
// 출력: 각 항목의 ink/em 비율과, 그로부터 나온 시안의 CSS 폰트 크기.
import { chromium } from "playwright-core";

const items = JSON.parse(process.argv[2]);
const browser = await chromium.launch({ executablePath: process.env.PW_CHROME, args: ["--no-sandbox"] });
const page = await browser.newPage();
// 웹폰트가 실제로 로드된 상태에서 재야 한다 — 앱 페이지를 열어 폰트를 상속받는다.
await page.goto(process.env.BASE || "http://127.0.0.1:5173/", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);

const out = await page.evaluate(async (items) => {
  await document.fonts.ready;
  const c = document.createElement("canvas").getContext("2d");
  return items.map((it) => {
    c.font = it.font;
    const m = c.measureText(it.text);
    const ink = m.actualBoundingBoxAscent + m.actualBoundingBoxDescent;
    const ratio = ink / 100; // font는 100px 기준으로 넘긴다
    const cssInk = it.inkPx / (it.scale || 1);
    return {
      label: it.label,
      text: it.text,
      ratio: +ratio.toFixed(4),
      mockupInk: it.inkPx,
      cssInk: +cssInk.toFixed(1),
      impliedFontPx: +(cssInk / ratio).toFixed(1),
      fontLoaded: document.fonts.check(it.font.replace(/^[\d\s]*\d+px/, "16px")),
    };
  });
}, items);

console.log(JSON.stringify(out, null, 1));
await browser.close();
