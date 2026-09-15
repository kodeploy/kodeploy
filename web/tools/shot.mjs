// 스크린샷 유틸 — 시안과 같은 뷰포트로 찍고 콘솔/페이지 에러를 함께 보고한다.
// 사용: node tools/shot.mjs <url> <out.png> [width] [height]
// 환경변수: PW_CHROME=크로미움 실행 파일, FULL=1이면 전체 페이지, WAIT=ms 추가 대기
import { chromium } from "playwright-core";
import fs from "node:fs";

const [url, out, w = "1536", h = "1024"] = process.argv.slice(2);
const browser = await chromium.launch({
  executablePath: process.env.PW_CHROME,
  args: ["--no-sandbox", "--disable-dev-shm-usage"],
});
const page = await browser.newPage({
  viewport: { width: +w, height: +h },
  deviceScaleFactor: 1,
});
const errors = [];
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text().slice(0, 300));
});
page.on("pageerror", (e) => errors.push("PAGEERROR: " + e.message.slice(0, 300)));
page.on("requestfailed", (r) =>
  errors.push("REQFAIL: " + r.url().slice(0, 110) + " " + (r.failure()?.errorText || "")),
);
try {
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(+(process.env.WAIT || 1500));
  await page.screenshot({ path: out, fullPage: process.env.FULL === "1" });
} catch (e) {
  errors.push("NAV: " + e.message.slice(0, 200));
}
console.log(
  JSON.stringify({ out, bytes: fs.existsSync(out) ? fs.statSync(out).size : 0, errors: errors.slice(0, 12) }, null, 1),
);
await browser.close();
