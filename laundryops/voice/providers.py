"""Speech ports and providers for the real-time pipeline.

``STTPort.transcribe`` takes one utterance of 16-bit PCM (8 kHz) and returns text;
``TTSPort.synthesize`` returns μ-law 8 kHz bytes ready for the phone leg.

Offline implementations keep CI and the demo free of network and keys:
* ``ScriptedSTT`` returns the next scripted transcript (tests feed audio, read back text).
* ``ToneTTS`` synthesizes a short tone burst whose length tracks the text, so timing,
  barge-in and playback mechanics are exercised end to end.

The Deepgram and ElevenLabs clients are thin REST wrappers (httpx) and are not exercised in CI.
"""
from __future__ import annotations

from typing import Protocol

from laundryops.domain.models import Language
from laundryops.voice.audio import pcm16_bytes, silence, tone, ulaw_encode
from laundryops.voice.prompts import SPEECH_LOCALE


class STTPort(Protocol):
    def transcribe(self, pcm16: bytes, sample_rate: int, language: Language) -> str: ...


class TTSPort(Protocol):
    def synthesize(self, text: str, language: Language) -> bytes: ...


class ScriptedSTT:
    def __init__(self, transcripts: list[str] | None = None):
        self.transcripts = list(transcripts or [])
        self.received: list[tuple[int, Language]] = []

    def transcribe(self, pcm16: bytes, sample_rate: int, language: Language) -> str:
        self.received.append((len(pcm16) // 2, language))
        return self.transcripts.pop(0) if self.transcripts else ""


class ToneTTS:
    """~55 ms of audio per word, as a 440 Hz tone with a gap: enough to time playback and barge-in."""

    def synthesize(self, text: str, language: Language) -> bytes:
        words = max(1, len(text.split()))
        samples = tone(440.0, 55 * words) + silence(120)
        return ulaw_encode(samples)


class DeepgramSTT:
    def __init__(self, api_key: str, model: str = "nova-2", timeout_s: float = 10.0):
        import httpx
        self._c = httpx.Client(base_url="https://api.deepgram.com", headers={"Authorization": f"Token {api_key}"}, timeout=timeout_s)
        self.model = model

    def transcribe(self, pcm16: bytes, sample_rate: int, language: Language) -> str:
        lang = SPEECH_LOCALE.get(language, "de-DE").split("-")[0]
        r = self._c.post("/v1/listen", params={"model": self.model, "language": lang, "encoding": "linear16",
                                                "sample_rate": str(sample_rate), "smart_format": "true"},
                         content=pcm16, headers={"Content-Type": "application/octet-stream"})
        r.raise_for_status()
        return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


class ElevenLabsTTS:
    def __init__(self, api_key: str, voice_id: str, model_id: str = "eleven_multilingual_v2", timeout_s: float = 15.0):
        import httpx
        self._c = httpx.Client(base_url="https://api.elevenlabs.io", headers={"xi-api-key": api_key}, timeout=timeout_s)
        self.voice_id, self.model_id = voice_id, model_id

    def synthesize(self, text: str, language: Language) -> bytes:
        r = self._c.post(f"/v1/text-to-speech/{self.voice_id}", params={"output_format": "ulaw_8000"},
                         json={"text": text, "model_id": self.model_id,
                               "language_code": SPEECH_LOCALE.get(language, "de-DE").split("-")[0]})
        r.raise_for_status()
        return r.content


def pcm_to_bytes(samples: list[int]) -> bytes:
    return pcm16_bytes(samples)
