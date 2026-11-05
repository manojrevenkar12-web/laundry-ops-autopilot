"""G.711 μ-law codec and an energy VAD — no numpy, no audioop (removed in Python 3.13).

Telephony audio is 8 kHz mono μ-law. We decode to 16-bit PCM for the recogniser and the
VAD, and encode synthesized speech back to μ-law for the caller.
"""
from __future__ import annotations

import math
import struct

_BIAS = 0x84
_CLIP = 32635


def _encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0
    if sample < 0:
        sample = -sample
    sample = min(sample, _CLIP) + _BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (sample >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


def _decode_sample(byte: int) -> int:
    byte = ~byte & 0xFF
    sign = byte & 0x80
    exponent = (byte >> 4) & 0x07
    mantissa = byte & 0x0F
    sample = ((mantissa << 3) + _BIAS) << exponent
    sample -= _BIAS
    return -sample if sign else sample


_DECODE = [_decode_sample(b) for b in range(256)]


def ulaw_decode(data: bytes) -> list[int]:
    return [_DECODE[b] for b in data]


def ulaw_encode(samples: list[int]) -> bytes:
    return bytes(_encode_sample(max(-32768, min(32767, s))) for s in samples)


def pcm16_bytes(samples: list[int]) -> bytes:
    return struct.pack("<%dh" % len(samples), *samples)


def rms(samples: list[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def tone(freq_hz: float, ms: int, sample_rate: int = 8000, amplitude: int = 9000) -> list[int]:
    n = sample_rate * ms // 1000
    return [int(amplitude * math.sin(2 * math.pi * freq_hz * i / sample_rate)) for i in range(n)]


def silence(ms: int, sample_rate: int = 8000) -> list[int]:
    return [0] * (sample_rate * ms // 1000)


class EnergyVAD:
    """Frame-level voice activity with an adaptive noise floor and utterance end-pointing.

    ``push(samples)`` returns one of: None, "start" (speech began), "end" (an utterance finished).
    """

    def __init__(self, sample_rate: int = 8000, frame_ms: int = 20, min_speech_ms: int = 240, end_silence_ms: int = 700,
                 ratio: float = 3.0, floor: float = 120.0):
        self.frame = sample_rate * frame_ms // 1000
        self.frame_ms = frame_ms
        self.min_speech_ms, self.end_silence_ms = min_speech_ms, end_silence_ms
        self.ratio, self.noise = ratio, floor
        self._buf: list[int] = []
        self.in_speech = False
        self._speech_ms = 0
        self._silence_ms = 0
        self.utterance: list[int] = []

    def push(self, samples: list[int]) -> str | None:
        self._buf.extend(samples)
        event = None
        while len(self._buf) >= self.frame:
            frame, self._buf = self._buf[: self.frame], self._buf[self.frame:]
            e = rms(frame)
            voiced = e > max(self.noise * self.ratio, 200.0)
            if not voiced:
                self.noise = 0.95 * self.noise + 0.05 * e          # track the floor only on quiet frames
            if self.in_speech:
                self.utterance.extend(frame)
                if voiced:
                    self._speech_ms += self.frame_ms
                    self._silence_ms = 0
                else:
                    self._silence_ms += self.frame_ms
                    if self._silence_ms >= self.end_silence_ms:
                        self.in_speech = False
                        if self._speech_ms >= self.min_speech_ms:
                            event = "end"
                        else:
                            self.utterance = []
                        self._speech_ms = self._silence_ms = 0
            elif voiced:
                self.in_speech = True
                self._speech_ms, self._silence_ms = self.frame_ms, 0
                self.utterance = list(frame)
                event = event or "start"
        return event

    def take_utterance(self) -> list[int]:
        u, self.utterance = self.utterance, []
        return u
