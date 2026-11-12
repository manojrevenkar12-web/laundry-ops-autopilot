"""The only two things the language model is allowed to do.

1. ``triage``: turn free text into a structured TriageResult (intent, language, machine reference, flags).
2. ``draft_reply``: turn an approved ``facts`` dict into a reply in the resident's language.

It never decides money, never touches hardware, never sees the payment ledger. Those are
the policy engine's job (``laundryops.support.policy``) and it is plain code.
"""
from __future__ import annotations

from typing import Any, Protocol

from laundryops.domain.models import Language, TriageResult


class LLMPort(Protocol):
    name: str

    def triage(self, text: str, hint_language: Language | None = None, is_partner: bool = False) -> TriageResult: ...
    def draft_reply(self, facts: dict[str, Any], language: Language) -> str: ...
    def translate(self, text: str, language: Language) -> str: ...
