"""Twilio Media Streams session: bidirectional μ-law over a WebSocket, driven by the dialog.

Inbound frames → μ-law decode → VAD → utterance → STT → ``VoiceDialog.step`` → TTS → μ-law
frames back to the caller. Barge-in: speech detected while the agent is talking clears the
playback buffer (Twilio ``clear`` event) and the agent listens. The class is transport
agnostic — ``on_message(dict)`` returns the list of outbound messages — so the whole loop is
unit-tested with synthetic audio and no socket.

Message shapes follow Twilio's spec: ``connected``, ``start`` (streamSid, customParameters),
``media`` (base64 μ-law payload, 20 ms), ``mark``, ``stop``; outbound ``media``, ``mark``, ``clear``.
"""
from __future__ import annotations

import base64
from typing import Any

from laundryops.domain.models import Language
from laundryops.voice.audio import EnergyVAD, pcm16_bytes, ulaw_decode
from laundryops.voice.dialog import Turn, VoiceDialog
from laundryops.voice.providers import STTPort, TTSPort

FRAME_BYTES = 160   # 20 ms at 8 kHz μ-law


class MediaStreamSession:
    def __init__(self, dialog: VoiceDialog, stt: STTPort, tts: TTSPort, *, barge_in: bool = True):
        self.dialog, self.stt, self.tts, self.barge_in = dialog, stt, tts, barge_in
        self.stream_sid: str | None = None
        self.call_id: str | None = None
        self.vad = EnergyVAD()
        self.language = Language.DE
        self.playing = False
        self.listening = False
        self.done = False
        self.handoff: Turn | None = None
        self.stats = {"frames_in": 0, "utterances": 0, "barge_ins": 0, "frames_out": 0}

    # ---- inbound ---------------------------------------------------------------------
    def on_message(self, msg: dict[str, Any]) -> list[dict[str, Any]]:
        ev = msg.get("event")
        if ev == "start":
            self.stream_sid = msg["start"]["streamSid"]
            params = msg["start"].get("customParameters", {}) or {}
            turn = self.dialog.start(call_id=msg["start"].get("callSid") or params.get("call_id"), from_number=params.get("from"))
            self.call_id = turn and self._call_id_from(turn, msg)
            return self._speak(turn)
        if ev == "media" and not self.done:
            self.stats["frames_in"] += 1
            samples = ulaw_decode(base64.b64decode(msg["media"]["payload"]))
            event = self.vad.push(samples)
            if event == "start" and self.playing and self.barge_in:
                self.playing = False
                self.stats["barge_ins"] += 1
                return [{"event": "clear", "streamSid": self.stream_sid}]
            if event == "end" and self.listening:
                utterance = self.vad.take_utterance()
                text = self.stt.transcribe(pcm16_bytes(utterance), 8000, self.language)
                self.stats["utterances"] += 1
                turn = self.dialog.step(self.call_id, text)
                return self._speak(turn)
            return []
        if ev == "mark" and msg.get("mark", {}).get("name") == "end_of_speech":
            self.playing = False
            return []
        if ev == "stop":
            self.done = True
            return []
        return []

    def _call_id_from(self, turn: Turn, msg: dict[str, Any]) -> str:
        return msg["start"].get("callSid") or (msg["start"].get("customParameters", {}) or {}).get("call_id") or next(iter(self.dialog.calls))

    # ---- outbound -------------------------------------------------------------------
    def _speak(self, turn: Turn) -> list[dict[str, Any]]:
        self.language = turn.language
        out: list[dict[str, Any]] = []
        if turn.say:
            audio = self.tts.synthesize(turn.say, turn.language)
            for i in range(0, len(audio), FRAME_BYTES):
                out.append({"event": "media", "streamSid": self.stream_sid,
                            "media": {"payload": base64.b64encode(audio[i:i + FRAME_BYTES]).decode()}})
            out.append({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": "end_of_speech"}})
            self.stats["frames_out"] += len(out) - 1
            self.playing = True
        self.listening = turn.listen and not turn.done
        if turn.handoff:
            self.handoff = turn
        if turn.done:
            self.done = True
        return out
