"""OpenRouter client hardening, exercised with a stub transport (no network)."""
from __future__ import annotations

import json
from types import SimpleNamespace

from laundryops.config import Settings
from laundryops.domain.models import Intent, Language
from laundryops.llm.openrouter import OpenRouterLLM


class _Stub:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r))])


def _llm(replies):
    s = Settings(db_path=":memory:", llm_mode="openrouter", openrouter_api_key="sk-test")
    llm = OpenRouterLLM(s)
    llm._client = _Stub(replies)
    return llm


def test_triage_parses_structured_json_and_prefers_regex_machine_id():
    good = json.dumps({"intent": "cycle_aborted", "language": "de", "confidence": 0.93, "machine_ref": "label:9",
                       "mentions_damage": False, "mentions_legal_or_abuse": False, "summary": "s"})
    llm = _llm([good])
    tr = llm.triage("WM-0012 ist mittendrin stehen geblieben")
    assert tr.intent == Intent.CYCLE_ABORTED and tr.language == Language.DE and tr.machine_ref == "WM-0012"
    assert "UNTRUSTED_MESSAGE" in llm._client.calls[0]["messages"][1]["content"]


def test_triage_retries_bad_json_then_falls_back_offline():
    llm = _llm(["not json at all", "{\"intent\": \"not-an-intent\"}"])
    tr = llm.triage("The washer stopped mid cycle, clothes still wet")
    assert tr.intent == Intent.CYCLE_ABORTED            # offline fallback result
    assert len(llm._client.calls) == 2


def test_triage_provider_outage_falls_back_offline():
    llm = _llm([RuntimeError("502 bad gateway")])
    assert llm.triage("Makine çalışmıyor, kapı kilitli").intent == Intent.MACHINE_NOT_STARTED


def test_reply_scrubs_hallucinated_amounts():
    facts = {"name": "Tom", "machine_label": "Waschmaschine 2", "refund_eta_days": 5,
             "actions": [{"type": "refund", "success": True, "amount_cents": 350}]}
    llm = _llm(["Hi Tom, we refunded 3,50 € for Waschmaschine 2 plus a 20,00 € voucher; expect it within 5 business days."])
    reply = llm.draft_reply(facts, Language.EN)
    assert "20,00" not in reply and "3,50" in reply and llm.last_scrubbed == ["20,00"]


def test_reply_falls_back_to_template_when_model_output_unusable():
    facts = {"name": "Tom", "machine_label": "Waschmaschine 2", "refund_eta_days": 5,
             "actions": [{"type": "refund", "success": True, "amount_cents": 350}]}
    llm = _llm(["99 € 77 € 55 €"])        # mostly invented numbers -> too many scrubs -> template
    reply = llm.draft_reply(facts, Language.EN)
    assert "we've refunded 3,50 €" in reply
