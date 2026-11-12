"""OpenRouter-backed implementation of LLMPort (OpenAI-compatible API).

Hardening that matters in production:
* structured JSON output validated with pydantic; one retry on bad JSON, then offline fallback,
* every reply is run through ``scrub_unapproved_numbers`` so a hallucinated amount can never reach a resident,
* timeouts and a hard fallback to the deterministic rules engine when the provider is down.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from laundryops.config import Settings
from laundryops.domain.models import Language, TriageResult
from laundryops.llm import prompts
from laundryops.llm.offline import OfflineLLM, extract_machine_ref

log = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def approved_numbers(facts: dict[str, Any]) -> set[str]:
    """All numeric tokens that may legitimately appear in a reply, in the spellings a model might use."""
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            out.add(str(v))
            if isinstance(v, int):
                out.add(f"{v / 100:.2f}")          # cents -> euros
                out.add(f"{v / 100:.2f}".replace(".", ","))
                out.add(f"{v / 100:g}")
        elif isinstance(v, str):
            for m in _NUMBER.findall(v):
                out.add(m)
                out.add(m.replace(",", "."))
                out.add(m.replace(".", ","))

    walk(facts)
    return out


def scrub_unapproved_numbers(reply: str, facts: dict[str, Any]) -> tuple[str, list[str]]:
    """Replace any number the facts did not supply. Returns (clean_reply, removed_tokens)."""
    allowed = approved_numbers(facts)
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        tok = m.group(0)
        if tok in allowed or tok.replace(",", ".") in allowed or tok.replace(".", ",") in allowed:
            return tok
        removed.append(tok)
        return "[…]"

    return _NUMBER.sub(repl, reply), removed


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, settings: Settings, fallback: OfflineLLM | None = None):
        from openai import OpenAI  # imported lazily so offline installs don't need it configured

        if not settings.openrouter_api_key:
            raise RuntimeError("LOPS_OPENROUTER_API_KEY is not set")
        self._client = OpenAI(api_key=settings.openrouter_api_key, base_url=settings.openrouter_base_url,
                              timeout=settings.llm_timeout_s)
        self._model = settings.openrouter_model
        self._fallback = fallback or OfflineLLM()
        self._allow_fallback = settings.llm_fallback_to_offline
        self.last_scrubbed: list[str] = []

    def _chat(self, system: str, user: str, json_mode: bool) -> str:
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(
            model=self._model, temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            extra_headers={"HTTP-Referer": "https://github.com/manojrevenkar12-web/laundry-ops-autopilot",
                           "X-Title": "laundry-ops-autopilot"},
            **kwargs,
        )
        return resp.choices[0].message.content or ""

    def triage(self, text: str, hint_language: Language | None = None, is_partner: bool = False) -> TriageResult:
        user = prompts.triage_user_message(text, hint_language, is_partner)
        for attempt in range(2):
            try:
                raw = self._chat(prompts.TRIAGE_SYSTEM, user, json_mode=True)
                data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
                data["language"] = Language.parse(data.get("language")).value
                tr = TriageResult.model_validate(data)
                # The regex is more reliable than the model for ids; prefer it when it finds one.
                tr.machine_ref = extract_machine_ref(text) or tr.machine_ref
                return tr
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                log.warning("triage parse failure (attempt %d): %s", attempt + 1, e)
            except Exception as e:  # provider down, auth, timeout
                log.error("triage provider failure: %s", e)
                break
        if not self._allow_fallback:
            raise RuntimeError("triage failed and fallback disabled")
        return self._fallback.triage(text, hint_language, is_partner)

    def draft_reply(self, facts: dict[str, Any], language: Language) -> str:
        try:
            raw = self._chat(prompts.REPLY_SYSTEM, prompts.reply_user_message(facts, language), json_mode=False).strip()
            clean, removed = scrub_unapproved_numbers(raw, facts)
            self.last_scrubbed = removed
            if removed:
                log.warning("scrubbed unapproved numbers from reply: %s", removed)
            if len(clean) < 20 or clean.count("[…]") > 1:
                return self._fallback.draft_reply(facts, language)
            return clean
        except Exception as e:
            log.error("reply provider failure: %s", e)
            if not self._allow_fallback:
                raise
            return self._fallback.draft_reply(facts, language)


TRANSLATE_SYSTEM = """Translate the message into the requested language for a laundry-room resident on the phone or in chat.
Keep every number, amount, machine name and reference exactly as given. Keep it the same length. Output the translation only."""


def _translate(self: "OpenRouterLLM", text: str, language: Language) -> str:
    from laundryops.domain.models import NATIVE_LANGUAGES

    if language in NATIVE_LANGUAGES or language == Language.UNKNOWN or not text.strip():
        return text
    try:
        raw = self._chat(TRANSLATE_SYSTEM, f"language={language.value}\n{text}", json_mode=False).strip()
        clean, removed = scrub_unapproved_numbers(raw, {"text": text})
        return text if removed or len(clean) < 2 else clean
    except Exception as e:  # provider down: English rather than silence
        log.error("translate failure: %s", e)
        return text


OpenRouterLLM.translate = _translate


def build_llm(settings: Settings):
    if settings.llm_mode == "openrouter":
        return OpenRouterLLM(settings)
    return OfflineLLM()
