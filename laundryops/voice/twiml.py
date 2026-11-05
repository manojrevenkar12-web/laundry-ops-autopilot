"""TwiML for the day-one integration: Twilio does speech recognition, we do everything else.

Three verbs are enough: <Say> (with per-language neural voices), <Gather input="speech dtmf">
(language + hints set per turn, so the recogniser follows the caller's language), and
<Dial> to a human with a whisper URL that reads the hand-off context to the agent first.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from laundryops.voice.dialog import Turn

VOICES = {"de-DE": "Polly.Vicki-Neural", "en-GB": "Polly.Amy-Neural", "tr-TR": "Polly.Filiz", "fr-FR": "Polly.Lea-Neural",
          "es-ES": "Polly.Lucia-Neural"}


def _say(text: str, locale: str) -> str:
    return f'<Say language="{locale}" voice="{VOICES.get(locale, "Polly.Vicki-Neural")}">{escape(text)}</Say>'


def twiml_for_turn(turn: Turn, call_id: str, *, turn_url: str, human_number: str, whisper_url: str | None = None,
                   sms_from: str | None = None) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<Response>"]
    if turn.sms and sms_from:
        parts.append(f'<Sms from="{escape(sms_from)}">{escape(turn.sms)}</Sms>')
    if turn.say:
        parts.append(_say(turn.say, turn.locale))
    if turn.handoff:
        number = f'<Number url="{escape(whisper_url)}">{escape(human_number)}</Number>' if whisper_url else escape(human_number)
        parts.append(f'<Dial timeout="25">{number}</Dial>')
        parts.append(_say("Gerade ist niemand frei. Wir rufen Sie zurück. / Nobody is free right now, we will call you back.", "de-DE"))
        parts.append("<Hangup/>")
    elif turn.done or not turn.listen:
        parts.append("<Hangup/>")
    else:
        hints = f' hints="{escape(turn.hints)}"' if turn.hints else ""
        parts.append(
            f'<Gather input="speech dtmf" language="{turn.locale}" speechTimeout="auto" timeout="6" numDigits="1" '
            f'actionOnEmptyResult="true" action="{escape(turn_url)}?call={escape(call_id)}" method="POST"{hints}/>'
        )
    parts.append("</Response>")
    return "".join(parts)


def twiml_whisper(text: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{_say(text, "en-GB")}</Response>'
