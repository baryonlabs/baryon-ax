// Building blocks for live voice conversation: microphone capture, an energy-based
// voice activity detector (VAD) and a sentence chunker that feeds text-to-speech.
// Everything runs in the browser; audio only goes to the on-prem API.

export const FRAME_MS = 20;

export type VadEvent = { type: "speech-start" } | { type: "speech-end"; samples: Float32Array } | { type: "discard" };

export type VadOptions = {
  rate: number;
  /** ms of voiced audio before speech counts as started */
  startMs?: number;
  /** ms of silence that ends an utterance */
  endMs?: number;
  /** ms kept before the detected start, so the first syllable is not cut */
  preRollMs?: number;
  /** utterances shorter than this (voiced ms) are dropped as noise */
  minSpeechMs?: number;
  maxUtteranceMs?: number;
};

/**
 * Energy VAD with an adaptive noise floor. A frame is voiced when its RMS is well above
 * the floor. `strict` raises the bar while the assistant is talking so its own voice
 * (after browser echo cancellation) does not count as an interruption.
 */
export class EnergyVad {
  readonly frameSize: number;
  floor = 0.004;
  strict = false;
  private readonly startFrames: number;
  private readonly endFrames: number;
  private readonly preRollFrames: number;
  private readonly minSpeechFrames: number;
  private readonly maxFrames: number;
  private voicedRun = 0;
  private silentRun = 0;
  private voicedTotal = 0;
  private speaking = false;
  private preRoll: Float32Array[] = [];
  private utterance: Float32Array[] = [];
  private pending = new Float32Array(0);

  constructor(options: VadOptions) {
    const frames = (ms: number) => Math.max(1, Math.round(ms / FRAME_MS));
    this.frameSize = Math.round(options.rate * FRAME_MS / 1000);
    this.startFrames = frames(options.startMs ?? 160);
    this.endFrames = frames(options.endMs ?? 700);
    this.preRollFrames = frames(options.preRollMs ?? 300);
    this.minSpeechFrames = frames(options.minSpeechMs ?? 250);
    this.maxFrames = frames(options.maxUtteranceMs ?? 30000);
  }

  get active(): boolean { return this.speaking; }

  threshold(): number {
    return this.strict ? Math.max(this.floor * 6, 0.035) : Math.max(this.floor * 3, 0.012);
  }

  /** Feed raw samples of any length; returns the events they caused. */
  push(samples: Float32Array): VadEvent[] {
    const joined = new Float32Array(this.pending.length + samples.length);
    joined.set(this.pending);
    joined.set(samples, this.pending.length);
    const events: VadEvent[] = [];
    let offset = 0;
    for (; offset + this.frameSize <= joined.length; offset += this.frameSize) {
      const event = this.frame(joined.slice(offset, offset + this.frameSize));
      if (event) events.push(event);
    }
    this.pending = joined.slice(offset);
    return events;
  }

  reset(): void {
    this.voicedRun = this.silentRun = this.voicedTotal = 0;
    this.speaking = false;
    this.preRoll = [];
    this.utterance = [];
    this.pending = new Float32Array(0);
  }

  private frame(frame: Float32Array): VadEvent | null {
    let sum = 0;
    for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
    const rms = Math.sqrt(sum / frame.length);
    const voiced = rms > this.threshold();
    if (!this.speaking) {
      if (!voiced) this.floor = Math.min(0.05, Math.max(0.001, this.floor * 0.95 + rms * 0.05));
      this.preRoll.push(frame);
      if (this.preRoll.length > this.preRollFrames) this.preRoll.shift();
      this.voicedRun = voiced ? this.voicedRun + 1 : 0;
      if (this.voicedRun >= this.startFrames) {
        this.speaking = true;
        this.utterance = [...this.preRoll];
        this.voicedTotal = this.voicedRun;
        this.silentRun = 0;
        this.preRoll = [];
        return { type: "speech-start" };
      }
      return null;
    }
    this.utterance.push(frame);
    if (voiced) {
      this.voicedTotal++;
      this.silentRun = 0;
    } else {
      this.silentRun++;
    }
    if (this.silentRun >= this.endFrames || this.utterance.length >= this.maxFrames) {
      const keep = this.utterance.length - Math.max(0, this.silentRun - Math.round(200 / FRAME_MS)); // keep 200 ms of tail
      const enough = this.voicedTotal >= this.minSpeechFrames;
      const samples = concat(this.utterance.slice(0, keep));
      this.speaking = false;
      this.utterance = [];
      this.voicedRun = this.silentRun = this.voicedTotal = 0;
      return enough ? { type: "speech-end", samples } : { type: "discard" };
    }
    return null;
  }
}

export function concat(parts: Float32Array[]): Float32Array {
  const out = new Float32Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) { out.set(part, offset); offset += part.length; }
  return out;
}

/**
 * Splits a streamed answer into speakable chunks. The first chunk is flushed early
 * (at a comma after a few words) so the assistant starts talking sooner.
 */
export class SentenceChunker {
  private buffer = "";
  private first = true;

  push(token: string): string[] {
    this.buffer += token;
    const chunks: string[] = [];
    for (;;) {
      const end = this.boundary();
      if (end < 0) break;
      const chunk = this.buffer.slice(0, end).trim();
      this.buffer = this.buffer.slice(end);
      if (chunk) { chunks.push(chunk); this.first = false; }
    }
    return chunks;
  }

  flush(): string {
    const rest = this.buffer.trim();
    this.buffer = "";
    this.first = true;
    return rest;
  }

  private boundary(): number {
    const sentence = /[.!?。！？](?=\s|$)|\n+/g;
    let match: RegExpExecArray | null;
    while ((match = sentence.exec(this.buffer))) {
      const end = match.index + match[0].length;
      if (end >= this.buffer.length && !/\n/.test(match[0])) return -1; // wait: "3." may continue as "3.5"
      if (this.buffer.slice(0, end).trim().length >= 6) return end;
    }
    if (this.first) {
      const comma = /[,，]\s/g;
      while ((match = comma.exec(this.buffer))) if (match.index >= 18) return match.index + 1;
    }
    if (this.buffer.length > 160) { // very long run-on sentence: cut at a space
      const space = this.buffer.lastIndexOf(" ", 140);
      return space > 40 ? space : 140;
    }
    return -1;
  }
}

const WORKLET = `
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("baryon-capture", Capture);
`;

export type Microphone = { rate: number; stop: () => void };

/** Streams mono microphone samples (at the device rate) to `onSamples`. */
export async function openMicrophone(onSamples: (samples: Float32Array) => void): Promise<Microphone> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const context = new AudioContext();
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "text/javascript" }));
  try {
    await context.audioWorklet.addModule(url);
  } finally {
    URL.revokeObjectURL(url);
  }
  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, "baryon-capture");
  node.port.onmessage = event => onSamples(event.data as Float32Array);
  source.connect(node);
  return {
    rate: context.sampleRate,
    stop: () => {
      node.port.onmessage = null;
      source.disconnect();
      node.disconnect();
      stream.getTracks().forEach(track => track.stop());
      void context.close();
    },
  };
}

/** Plays WAV chunks back to back; `stop()` cuts playback for barge-in. */
export class SpeechPlayer {
  private context: AudioContext | null = null;
  private queue: Promise<ArrayBuffer | null>[] = [];
  private current: AudioBufferSourceNode | null = null;
  private running = false;
  private generation = 0;
  onStart?: () => void;
  onIdle?: () => void;

  get playing(): boolean { return this.running; }

  enqueue(audio: Promise<ArrayBuffer | null>): void {
    this.queue.push(audio);
    if (!this.running) void this.drain(this.generation);
  }

  stop(): void {
    this.generation++;
    this.queue = [];
    try { this.current?.stop(); } catch { /* already stopped */ }
    this.current = null;
    if (this.running) { this.running = false; this.onIdle?.(); }
  }

  close(): void {
    this.stop();
    void this.context?.close();
    this.context = null;
  }

  private async drain(generation: number): Promise<void> {
    this.running = true;
    this.context ??= new AudioContext();
    while (this.queue.length && generation === this.generation) {
      const data = await this.queue.shift()!;
      if (!data || generation !== this.generation) continue;
      const buffer = await this.context.decodeAudioData(data);
      if (generation !== this.generation) break;
      await new Promise<void>(resolve => {
        const source = this.context!.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context!.destination);
        source.onended = () => resolve();
        this.current = source;
        this.onStart?.();
        source.start();
      });
    }
    if (generation === this.generation) { this.running = false; this.current = null; this.onIdle?.(); }
  }
}
