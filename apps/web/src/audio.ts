// Convert a browser recording (webm/ogg/mp4 from MediaRecorder) into 16 kHz mono
// 16-bit PCM WAV, the format the on-prem STT sidecar accepts. Doing it here keeps
// ffmpeg out of the server image.

export const TARGET_RATE = 16000;

export async function recordingToWav(blob: Blob): Promise<Blob> {
  const data = await blob.arrayBuffer();
  const decoder = new AudioContext();
  try {
    const decoded = await decoder.decodeAudioData(data);
    const frames = Math.max(1, Math.ceil(decoded.duration * TARGET_RATE));
    const offline = new OfflineAudioContext(1, frames, TARGET_RATE);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination); // downmixes to mono and resamples
    source.start();
    const rendered = await offline.startRendering();
    return encodeWav(rendered.getChannelData(0), TARGET_RATE);
  } finally {
    void decoder.close();
  }
}

/** Resample raw microphone samples (any rate) to a 16 kHz mono WAV. */
export async function samplesToWav(samples: Float32Array, rate: number): Promise<Blob> {
  if (rate === TARGET_RATE) return encodeWav(samples, rate);
  const frames = Math.max(1, Math.ceil(samples.length * TARGET_RATE / rate));
  const offline = new OfflineAudioContext(1, frames, TARGET_RATE);
  const buffer = offline.createBuffer(1, samples.length, rate);
  buffer.getChannelData(0).set(samples);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  return encodeWav((await offline.startRendering()).getChannelData(0), TARGET_RATE);
}

export function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);       // PCM chunk size
  view.setUint16(20, 1, true);        // PCM
  view.setUint16(22, 1, true);        // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // byte rate
  view.setUint16(32, 2, true);        // block align
  view.setUint16(34, 16, true);       // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}
