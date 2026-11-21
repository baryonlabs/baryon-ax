// Live voice conversation end to end with a fake microphone.
//
//   LIVE_TURN_WAV      a spoken question followed by ~25 s of silence
//   LIVE_BARGEIN_WAV   (optional) a question, a pause, then speech while the answer plays
//
//   E2E_BASE_URL=http://127.0.0.1:5173 LIVE_TURN_WAV=/path/turn.wav node e2e/live.mjs
//
// Needs the API with STT_BASE_URL pointing at deploy/stt with a dictation model and
// Supertonic 3 installed, and a chat model (MODEL_BASE_URL) for natural answers.
import assert from "node:assert/strict";

import { chromium } from "playwright";

const baseUrl = (process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173").replace(/\/$/, "");
const turnWav = process.env.LIVE_TURN_WAV;
const bargeInWav = process.env.LIVE_BARGEIN_WAV;
assert.ok(turnWav, "Set LIVE_TURN_WAV");

async function session(wav, run) {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}%noloop`,
      "--autoplay-policy=no-user-gesture-required"],
  });
  try {
    const context = await browser.newContext({ locale: "ko-KR", viewport: { width: 1280, height: 900 } });
    await context.grantPermissions(["microphone"], { origin: baseUrl });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    // Record every phase change of the live panel with a timestamp.
    await page.evaluate(() => {
      window.__phases = [];
      const start = performance.now();
      new MutationObserver(() => {
        const panel = document.querySelector(".live-voice");
        const phase = panel?.className.replace("live-voice", "").trim();
        const last = window.__phases.at(-1);
        if (phase && phase !== last?.phase) window.__phases.push({ phase, at: Math.round(performance.now() - start) });
      }).observe(document.body, { subtree: true, attributes: true, childList: true, attributeFilter: ["class"] });
    });
    const live = page.locator(".voice-live");
    await live.waitFor();
    assert.equal(await live.isEnabled(), true, "live button is enabled when STT and TTS models are installed");
    await live.click();
    await page.waitForSelector(".live-voice");
    await run(page);
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
  }
}

const phases = page => page.evaluate(() => window.__phases);

await session(turnWav, async page => {
  const speech = [];
  page.on("response", response => { if (response.url().includes("/v1/audio/speech")) speech.push(response.status()); });
  await page.waitForSelector(".live-voice.speaking", { timeout: 60000 });
  if (process.env.LIVE_SCREENSHOT) await page.screenshot({ path: process.env.LIVE_SCREENSHOT });
  await page.waitForSelector(".live-voice.listening", { timeout: 90000 }); // answer finished, listening again
  const timeline = await phases(page);
  console.log("phases:", timeline.map(item => `${item.phase}@${item.at}`).join(" → "));
  const heard = await page.locator(".live-heard").innerText();
  const latency = await page.locator(".live-status small").innerText().catch(() => "");
  console.log("heard:", heard.replace(/\s+/g, " "), "|", latency);
  const answer = await page.locator(".message").last().innerText();
  console.log("answer:", answer.replace(/\s+/g, " ").slice(0, 200));
  assert.ok(speech.length >= 1 && speech.every(status => status === 200), `speech requests ${speech}`);
  for (const phase of ["hearing", "transcribing", "thinking", "speaking", "listening"]) {
    assert.ok(timeline.some(item => item.phase === phase), `phase ${phase} reached`);
  }
  console.log(`PASS one spoken turn (${speech.length} speech chunks)`);
  await page.keyboard.press("Escape");
  await page.waitForSelector(".live-voice", { state: "detached" });
  console.log("PASS Esc ends the conversation");
});

if (bargeInWav) {
  await session(bargeInWav, async page => {
    await page.waitForSelector(".live-voice.speaking", { timeout: 60000 });
    await page.waitForFunction(() => window.__phases.some((item, index) => index > 0 && item.phase === "hearing"
      && window.__phases.slice(0, index).some(prior => prior.phase === "speaking")), null, { timeout: 30000 });
    await page.waitForSelector(".live-voice.listening", { timeout: 30000 });
    const timeline = await phases(page);
    console.log("phases:", timeline.map(item => `${item.phase}@${item.at}`).join(" → "));
    const interrupted = timeline.findIndex((item, index) => item.phase === "hearing" && timeline.slice(0, index).some(prior => prior.phase === "speaking"));
    const after = timeline.slice(interrupted + 1);
    assert.ok(!after.some(item => item.phase === "speaking"), "assistant stops talking after the interruption");
    console.log("heard:", (await page.locator(".live-heard").innerText()).replace(/\s+/g, " "));
    console.log("PASS barge-in stops the answer");
  });
}
