"""Earcons: short sounds that say what the secretary is doing without words.

The caller is driving and cannot look at a screen, so state changes are heard:

    heard        띠링   two quick rising notes        your words were taken
    thinking     톡     a soft tick every few seconds  still working, nothing to say yet
    interrupted  뚝     two falling notes              it stopped (you spoke over it, or said 멈춰)
    question     딩-동? a rising question              it needs your yes or no
    task_done    도미솔  a short arpeggio               background work finished
    error        부-부  a low double buzz              something went wrong

They are generated (no audio files), kept quiet and short so the speech recognizer and the
echo filter do not take them for the caller's voice, and mixed into the call like speech.
"""

from __future__ import annotations

import numpy as np

# (frequency Hz, duration s) per note; 0 Hz is a pause.
PATTERNS: dict[str, tuple[list[tuple[float, float]], float]] = {
    "heard": ([(1318.5, 0.06), (1760.0, 0.09)], 0.22),
    "thinking": ([(523.3, 0.045)], 0.10),
    "interrupted": ([(880.0, 0.06), (587.3, 0.10)], 0.22),
    "question": ([(1046.5, 0.09), (0, 0.04), (1568.0, 0.16)], 0.22),
    "task_done": ([(1046.5, 0.08), (1318.5, 0.08), (1568.0, 0.18)], 0.20),
    "error": ([(220.0, 0.12), (0, 0.06), (196.0, 0.16)], 0.20),
}


def earcon(name: str, rate: int) -> np.ndarray:
    """Float32 samples for one earcon at the call's sample rate."""
    notes, volume = PATTERNS[name]
    parts = []
    for frequency, seconds in notes:
        count = int(rate * seconds)
        if not frequency:
            parts.append(np.zeros(count, dtype=np.float32))
            continue
        t = np.arange(count) / rate
        tone = np.sin(2 * np.pi * frequency * t) + 0.25 * np.sin(4 * np.pi * frequency * t)  # a little brightness
        fade = min(count // 2, int(rate * 0.008))
        envelope = np.ones(count)
        envelope[:fade] = np.linspace(0, 1, fade)               # no clicks at the edges
        envelope[-fade:] = np.linspace(1, 0, fade)
        envelope *= np.exp(-t * 6)                               # a soft bell-like decay
        parts.append((tone * envelope * volume / 1.25).astype(np.float32))
    parts.append(np.zeros(int(rate * 0.03), dtype=np.float32))   # a breath after
    return np.concatenate(parts)
