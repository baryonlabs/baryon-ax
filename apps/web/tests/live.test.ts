// Unit tests for the live-conversation building blocks (Node's built-in runner with type stripping).
//   node --test tests/
import assert from "node:assert/strict";
import { test } from "node:test";

import { EnergyVad, SentenceChunker } from "../src/live.ts";

const rate = 48000;
const tone = (ms: number, amp: number) => Float32Array.from({ length: rate * ms / 1000 }, (_, i) => amp * Math.sin(i / 8));
const noise = (ms: number, amp: number) => Float32Array.from({ length: rate * ms / 1000 }, () => amp * (Math.random() * 2 - 1));
const types = (events: { type: string }[]) => events.map(event => event.type);

test("VAD finds an utterance in background noise and keeps pre-roll and a short tail", () => {
  const vad = new EnergyVad({ rate });
  assert.deepEqual(vad.push(noise(1000, 0.003)), []);
  assert.deepEqual(types(vad.push(tone(800, 0.1))), ["speech-start"]);
  const events = vad.push(noise(900, 0.003));
  assert.deepEqual(types(events), ["speech-end"]);
  const seconds = (events[0] as { samples: Float32Array }).samples.length / rate;
  assert.ok(seconds > 1.0 && seconds < 1.5, `utterance length ${seconds}`);
});

test("VAD ignores clicks and drops too-short speech", () => {
  const vad = new EnergyVad({ rate });
  vad.push(noise(500, 0.003));
  assert.deepEqual([...vad.push(tone(100, 0.1)), ...vad.push(noise(900, 0.003))], []);
  assert.deepEqual(types([...vad.push(tone(200, 0.1)), ...vad.push(noise(900, 0.003))]), ["speech-start", "discard"]);
});

test("strict mode (assistant speaking) ignores echo-level audio but hears a real interruption", () => {
  const vad = new EnergyVad({ rate });
  vad.push(noise(500, 0.003));
  vad.strict = true;
  assert.deepEqual(vad.push(tone(500, 0.02)), []);
  assert.deepEqual(types(vad.push(tone(500, 0.2))), ["speech-start"]);
});

test("chunker splits streamed answers into sentences without breaking decimals", () => {
  const chunker = new SentenceChunker();
  const out: string[] = [];
  for (const token of ["네, ", "연차 ", "이월은 ", "다음 해 ", "3", ".", "5", "월이 아니라 3월 말까지입니다", ".", " 인사규정을", " 참고하세요!", " 더 궁금한"]) {
    out.push(...chunker.push(token));
  }
  out.push(chunker.flush());
  assert.deepEqual(out, ["네, 연차 이월은 다음 해 3.5월이 아니라 3월 말까지입니다.", "인사규정을 참고하세요!", "더 궁금한"]);
});

test("chunker flushes the first chunk early at a comma to start speaking sooner", () => {
  const chunker = new SentenceChunker();
  const out: string[] = [];
  for (const token of ["회의실 예약은 ", "사내 포털에서 ", "하시면 되고, ", "하루 전까지 ", "신청해야 합니다. "]) out.push(...chunker.push(token));
  assert.deepEqual(out, ["회의실 예약은 사내 포털에서 하시면 되고,", "하루 전까지 신청해야 합니다."]);
});
