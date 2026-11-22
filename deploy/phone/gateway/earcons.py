"""Earcons: short sounds that say what the secretary is doing without words.

The caller is driving and cannot look at a screen, so state changes are heard:

    heard        띠링   two quick rising notes        your words were taken
    thinking     동     a low, round bell, 동… 동… 동…  still working, nothing to say yet
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
    "thinking": ([(349.2, 0.36)], 0.16),   # F4, rendered as a bell (see _bell)
    "interrupted": ([(880.0, 0.06), (587.3, 0.10)], 0.22),
    "question": ([(1046.5, 0.09), (0, 0.04), (1568.0, 0.16)], 0.22),
    "task_done": ([(1046.5, 0.08), (1318.5, 0.08), (1568.0, 0.18)], 0.20),
    "error": ([(220.0, 0.12), (0, 0.06), (196.0, 0.16)], 0.20),
}


def _bell(frequency: float, seconds: float, volume: float, rate: int) -> np.ndarray:
    """A soft "동": a low fundamental with a quiet octave and an inharmonic bell partial, struck gently
    (no click) and left to ring down. Owner's request: thinking should sound like 동… 동… 동…, not a tick."""
    count = int(rate * seconds)
    t = np.arange(count) / rate
    tone = (np.sin(2 * np.pi * frequency * t)
            + 0.30 * np.sin(2 * np.pi * 2.0 * frequency * t) * np.exp(-t * 6)
            + 0.12 * np.sin(2 * np.pi * 2.76 * frequency * t) * np.exp(-t * 14))
    attack = int(rate * 0.012)
    envelope = np.exp(-t * 7.5)
    envelope[:attack] *= np.linspace(0, 1, attack)
    envelope[-int(rate * 0.02):] *= np.linspace(1, 0, int(rate * 0.02))
    return (tone * envelope * volume / 1.42).astype(np.float32)


def earcon(name: str, rate: int) -> np.ndarray:
    """Float32 samples for one earcon at the call's sample rate."""
    notes, volume = PATTERNS[name]
    if name == "thinking":
        frequency, seconds = notes[0]
        return np.concatenate([_bell(frequency, seconds, volume, rate), np.zeros(int(rate * 0.03), dtype=np.float32)])
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
