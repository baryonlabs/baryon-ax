// Voice dictation end to end: a fake microphone plays a WAV file into the real UI,
// the API forwards it to a running STT sidecar, and we check the composer or view.
//
//   E2E_BASE_URL=http://127.0.0.1:5173 \
//   VOICE_COMMAND_WAV=/path/open-knowledge.wav VOICE_DICTATION_WAV=/path/sentence.wav \
//   node e2e/voice.mjs
//
// Use 16 kHz mono WAVs, e.g. macOS: say -v Yuna "지식 화면 열어 줘" -o a.aiff && afconvert -f WAVE -d LEI16@16000 -c 1 a.aiff a.wav
import assert from "node:assert/strict";

import { chromium } from "playwright";

const baseUrl = (process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173").replace(/\/$/, "");
const commandWav = process.env.VOICE_COMMAND_WAV;
const dictationWav = process.env.VOICE_DICTATION_WAV;
const recordMs = Number(process.env.VOICE_RECORD_MS ?? 2600);
assert.ok(commandWav && dictationWav, "Set VOICE_COMMAND_WAV and VOICE_DICTATION_WAV");

async function withMic(wav, run) {
  const browser = await chromium.launch({
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${wav}%noloop`],
  });
  try {
    const context = await browser.newContext({ locale: "ko-KR", viewport: { width: 1280, height: 900 } });
    await context.grantPermissions(["microphone"], { origin: baseUrl });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(String(error)));
    await page.goto(baseUrl, { waitUntil: "networkidle" });
    await run(page);
    assert.deepEqual(errors, [], "no page errors");
  } finally {
    await browser.close();
  }
}

async function speak(page) {
  const mic = page.locator(".voice-mic");
  await mic.waitFor();
  assert.equal(await mic.isEnabled(), true, "mic button is enabled when the STT server is reachable");
  await mic.click();                      // auto mode: tap starts
  await page.waitForSelector(".voice-mic.recording");
  await page.waitForTimeout(recordMs);
  await mic.click();                      // tap again stops and transcribes
  const response = await page.waitForResponse(r => r.url().includes("/v1/audio/transcriptions"), { timeout: 30000 });
  assert.equal(response.status(), 200, "transcription succeeded");
  return response.json();
}

await withMic(commandWav, async page => {
  const result = await speak(page);
  console.log("command:", JSON.stringify(result.text), result.command);
  assert.equal(result.command?.action, "open_view");
  await page.waitForFunction(() => document.querySelector("nav button.active")?.textContent?.includes("지식"));
  console.log("PASS voice command opens the knowledge view");
});

await withMic(dictationWav, async page => {
  const result = await speak(page);
  console.log("dictation:", JSON.stringify(result.text));
  assert.equal(result.command, null);
  await page.waitForFunction(() => (document.querySelector(".composer textarea")?.value ?? "").length > 3);
  const value = await page.locator(".composer textarea").inputValue();
  assert.equal(value, result.text, "dictation is inserted into the composer");
  console.log("PASS dictation fills the composer");
  await page.locator(".voice-settings-toggle").click();
  await page.waitForSelector(".voice-panel");
  await page.screenshot({ path: process.env.VOICE_SCREENSHOT ?? "voice-panel.png" });
  console.log("PASS settings panel opens");
});
