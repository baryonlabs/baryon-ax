"""Voice filter: pass the caller's own voice to speech-to-text, drop the room around them.

The VAD finds "speech", but on a real phone that includes fans, traffic and other people
talking nearby, which Whisper turns into words ("다음 영상에서 만나요", or a neighbour's
"그때 생각난다"). Each finished utterance goes through two checks before speech-to-text:

1. Denoise (GTCRN, a small speech-enhancement model run by sherpa-onnx, ~40 ms per second
   of audio on one CPU thread). Noise loses most of its energy; speech keeps almost all.
   keep = RMS after / RMS before. Measured on 123 utterances from real calls:
       room noise        keep median 0.25, RMS after denoise median 0.0003
       the caller        keep median 0.96, RMS after denoise 0.045 - 0.16
       people nearby     keep 0.5 - 0.9,   RMS after denoise 0.008 - 0.014
2. Near field. The caller holds the phone (or sits by the car's microphone); other voices
   come from further away and arrive much quieter even after denoising. "strict" requires
   the level of a near voice; "normal" only drops noise.

The denoised audio, not the raw audio, is what speech-to-text hears.

Modes (per call, chosen in the app; default GATEWAY_VOICE_FILTER):
    strict   only a voice close to the microphone (the owner)        near-field RMS 0.025
    normal   any clear voice, noise removed                          near-field RMS 0.006
    off      the plain level gate only (as before this filter)
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

import numpy as np

MODES = ("strict", "normal", "off")
NEAR_FIELD_RMS = {
    "strict": float(os.getenv("GATEWAY_NEAR_FIELD_RMS", "0.025")),
    "normal": float(os.getenv("GATEWAY_CLEAR_VOICE_RMS", "0.006")),
}
MIN_KEEP_RATIO = float(os.getenv("GATEWAY_MIN_KEEP_RATIO", "0.45"))


@dataclass
class Verdict:
    keep: bool
    samples: np.ndarray   # what speech-to-text should hear (denoised when the model is loaded)
    reason: str           # "", "noise" or "distant"
    rms: float
    ratio: float


def rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


class VoiceFilter:
    """One per gateway; the denoiser is shared and guarded by a lock (calls run in threads)."""

    def __init__(self, model: str | None = None, *, denoise=None) -> None:
        self._denoise = denoise
        self._lock = threading.Lock()
        if denoise is None and model and os.path.exists(model):
            import sherpa_onnx

            config = sherpa_onnx.OfflineSpeechDenoiserConfig(
                model=sherpa_onnx.OfflineSpeechDenoiserModelConfig(
                    gtcrn=sherpa_onnx.OfflineSpeechDenoiserGtcrnModelConfig(model=model), num_threads=1))
            denoiser = sherpa_onnx.OfflineSpeechDenoiser(config)
            self._denoise = lambda samples, rate: np.asarray(denoiser.run(samples, rate).samples, dtype=np.float32)

    @property
    def denoising(self) -> bool:
        return self._denoise is not None

    def check(self, samples: np.ndarray, rate: int, mode: str = "strict") -> Verdict:
        before = rms(samples)
        if mode == "off" or not len(samples):
            return Verdict(True, samples, "", before, 1.0)
        cleaned = samples
        if self._denoise is not None:
            with self._lock:
                cleaned = self._denoise(samples, rate)
        after = rms(cleaned)
        ratio = after / before if before > 0 else 0.0
        if self._denoise is not None and ratio < MIN_KEEP_RATIO:
            return Verdict(False, cleaned, "noise", after, ratio)
        if after < NEAR_FIELD_RMS.get(mode, NEAR_FIELD_RMS["strict"]):
            return Verdict(False, cleaned, "distant" if after >= NEAR_FIELD_RMS["normal"] else "noise", after, ratio)
        return Verdict(True, cleaned, "", after, ratio)
