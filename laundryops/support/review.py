"""Human-review tickets: the resident says the answer did not help, or asks for a person.

The agent never argues. It logs a review ticket with a reference the resident keeps, links the
automated tickets so the colleague sees what was already done, notifies the resident, and gets
out of the way. Residents can ask for the status of a reference later, on any channel.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from laundryops.domain.models import Language, utcnow

# "I'm not happy" / "I want a person" in the languages we hand-write; other languages reach a person via the model's triage
# (escalation intent) or by pressing 0 on the phone.
DISSATISFIED = re.compile(
    r"(nicht zufrieden|hilft (mir )?nicht|bringt (mir )?nichts|keine l[öo]sung|mitarbeiter|echten? menschen|mensch(en)? sprechen|"
    r"kollegin|kollegen|beschwerde|not happy|unhappy|doesn'?t help|does not help|didn'?t help|useless|no help|"
    r"real person|human|speak to (a |an )?(agent|person|someone)|talk to (a |an )?(agent|person|someone)|complaint|"
    r"memnun değilim|yardımcı olmadı|gerçek bir insan|yetkili|şikayet|"
    r"pas satisfait|ne m'aide pas|une vraie personne|un humain|r[ée]clamation|"
    r"no estoy satisfech|no me ayuda|una persona real|un humano|reclamaci[óo]n)", re.I)
REFERENCE = re.compile(r"\bWR[- ]?(\d{6})[- ]?(\d{3})\b", re.I)

_SAY = {
    Language.DE: ("Verstanden. Ich habe ein Ticket für eine Kollegin oder einen Kollegen angelegt. Ihre Referenz ist {ref}. "
                  "Sie bekommen sie auch per Nachricht und hören innerhalb eines Werktags von uns."),
    Language.EN: ("Understood. I've logged a ticket for a colleague. Your reference is {ref}. "
                  "You'll also get it by message, and you'll hear from us within one business day."),
    Language.TR: ("Anladım. Bir meslektaşım için kayıt açtım. Referans numaranız {ref}. "
                  "Mesajla da göndereceğiz; bir iş günü içinde sizi arayacağız."),
    Language.FR: ("Compris. J'ai ouvert un ticket pour un collègue. Votre référence est {ref}. "
                  "Vous la recevrez aussi par message et nous vous répondrons sous un jour ouvré."),
    Language.ES: ("Entendido. He abierto un ticket para un compañero. Tu referencia es {ref}. "
                  "La recibirás también por mensaje y te responderemos en un día laborable."),
}
_STATUS = {
    Language.DE: {"open": "offen – eine Kollegin oder ein Kollege übernimmt innerhalb eines Werktags",
                  "in_review": "in Bearbeitung", "resolved": "erledigt", "none": "Dazu finde ich kein Ticket."},
    Language.EN: {"open": "open – a colleague will take it within one business day", "in_review": "being handled",
                  "resolved": "resolved", "none": "I can't find a ticket with that reference."},
    Language.TR: {"open": "açık – bir meslektaşım bir iş günü içinde ilgilenecek", "in_review": "işlemde",
                  "resolved": "çözüldü", "none": "Bu referansla bir kayıt bulamadım."},
    Language.FR: {"open": "ouvert – un collègue s'en occupe sous un jour ouvré", "in_review": "en cours",
                  "resolved": "résolu", "none": "Je ne trouve pas de ticket avec cette référence."},
    Language.ES: {"open": "abierto – un compañero lo atenderá en un día laborable", "in_review": "en curso",
                  "resolved": "resuelto", "none": "No encuentro un ticket con esa referencia."},
}
_STATUS_LINE = {Language.DE: "Ticket {ref}: {status}.", Language.EN: "Ticket {ref}: {status}.", Language.TR: "{ref} kaydı: {status}.",
                Language.FR: "Ticket {ref} : {status}.", Language.ES: "Ticket {ref}: {status}."}


def is_dissatisfied(text: str) -> bool:
    return bool(DISSATISFIED.search(text))


def find_reference(text: str) -> str | None:
    m = REFERENCE.search(text)
    return f"WR-{m.group(1)}-{m.group(2)}" if m else None


def spoken_reference(ref: str) -> str:
    """'WR-261112-003' -> 'W R, 2 6 1 1 1 2, 0 0 3' so a voice reads it digit by digit."""
    a, b, c = ref.split("-")
    return f"{' '.join(a)}, {' '.join(b)}, {' '.join(c)}"


def open_review(platform, *, user_id: str | None, partner_id: str | None, channel: str, reason: str, tickets: list[str],
                language: Language, call_id: str | None = None, now: datetime | None = None) -> tuple[dict[str, Any], str]:
    """Create the review ticket, notify the resident, return (review, what to say)."""
    now = now or utcnow()
    review = platform.store.create_review(user_id=user_id, partner_id=partner_id, channel=channel, reason=reason[:500],
                                          tickets=tickets, call_id=call_id, now=now)
    ref = review["reference"]
    platform.store.audit("agent", "review_opened", tickets[-1] if tickets else None, reference=ref, reason=reason[:200],
                         user_id=user_id, partner_id=partner_id, channel=channel)
    who = platform.directory.get_user(user_id) or platform.directory.get_partner(partner_id)
    to = (getattr(who, "phone", None) or getattr(who, "email", None) or getattr(who, "contact_email", None)) if who else None
    if to:
        platform.notifier.send(to, "sms" if to.startswith("+") else "email", f"Ticket {ref}",
                               f"Ihr Ticket {ref} ist angelegt; wir melden uns innerhalb eines Werktags. / Your ticket {ref} is logged; "
                               f"we will get back to you within one business day.", reference=ref)
    say = _SAY.get(language, _SAY[Language.EN]).format(ref="{ref}")
    return review, say


def status_line(platform, reference: str, language: Language) -> tuple[dict[str, Any] | None, str]:
    r = platform.store.get_review(reference)
    table = _STATUS.get(language, _STATUS[Language.EN])
    if not r:
        return None, table["none"]
    return r, _STATUS_LINE.get(language, _STATUS_LINE[Language.EN]).format(ref="{ref}", status=table.get(r["status"], r["status"]))
