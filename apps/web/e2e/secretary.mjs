// Voice secretary end to end with a fake microphone: a spoken request is answered aloud,
// and a request with a side effect waits for a spoken "no" and is not carried out.
//
//   SECRETARY_SUMMARY_WAV  e.g. "열린 PR이랑 CI 상태 알려줘" + ~40 s of silence
//   SECRETARY_CONFIRM_WAV  e.g. "웹클로한테 네이버 메인 열라고 시켜줘" + ~16 s + "아니" + silence
//
//   E2E_BASE_URL=http://127.0.0.1:5173 SECRETARY_SUMMARY_WAV=… SECRETARY_CONFIRM_WAV=… node e2e/secretary.mjs
//
// Needs the API with STT_* and SECRETARY_* set (the signed-in user in SECRETARY_OWNERS)
// and apps/secretary running.
import assert from "node:assert/strict";

import { chromium } from "playwright";

const baseUrl = (process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173").replace(/\/$/, "");
const summaryWav = process.env.SECRETARY_SUMMARY_WAV;
const confirmWav = process.env.SECRETARY_CONFIRM_WAV;
assert.ok(summaryWav, "Set SECRETARY_SUMMARY_WAV");

async function call(wav, run) {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}%noloop`,
      "--autoplay-policy=no-user-gesture-required"],
  });
  try {
    const context = await browser.newContext({ locale: "ko-KR", viewport: { width: 1280, height: 900 } });
    await context.grantPermissions(["microphone"], { origin: baseUrl });
    await context.addInitScript(() => localStorage.setItem("baryon.live", JSON.stringify({ mode: "secretary" })));
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    const spoken = [];
    page.on("request", request => {
      if (request.url().includes("/v1/audio/speech")) spoken.push(JSON.parse(request.postData() || "{}").input);
    });
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await page.locator(".voice-live").click();
    await page.waitForSelector(".live-mode button.active");
    assert.match(await page.locator(".live-mode button.active").innerText(), /비서|Secretary/);
    await run(page, spoken);
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
  }
}

await call(summaryWav, async (page, spoken) => {
  await page.waitForSelector(".live-voice.speaking", { timeout: 60000 });
  await page.waitForFunction(() => document.querySelector(".live-voice")?.classList.contains("listening"), null, { timeout: 120000 });
  console.log("heard:", (await page.locator(".live-heard").innerText()).replace(/\s+/g, " "));
  console.log("spoken:", spoken.join(" / "));
  console.log("latency:", await page.locator(".live-status small").innerText().catch(() => "-"));
  assert.ok(spoken.length >= 2, "status filler and an answer were spoken");
  assert.ok(spoken.some(text => /PR|CI|피알|씨아이|풀 리퀘스트/.test(text)), "the answer talks about PRs or CI");
  console.log("PASS secretary answered a spoken request aloud");
});

if (confirmWav) {
  await call(confirmWav, async (page, spoken) => {
    await page.waitForSelector(".live-question", { timeout: 60000 });
    const question = await page.locator(".live-question").innerText();
    console.log("question:", question.replace(/\s+/g, " "));
    await page.waitForSelector(".live-question", { state: "detached", timeout: 60000 }); // the spoken "아니" answered it
    await page.waitForFunction(() => document.querySelector(".live-voice")?.classList.contains("listening"), null, { timeout: 60000 });
    console.log("spoken:", spoken.join(" / "));
    assert.ok(spoken.some(text => /보낼까요|할까요/.test(text)), "the confirmation was asked aloud");
    console.log("PASS side effect waited for a spoken answer");
  });
}
