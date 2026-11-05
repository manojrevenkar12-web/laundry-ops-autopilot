"""Turn a written reply into something a voice should say.

Strips the greeting and sign-off, replaces instructions that only make sense on a screen
("reply with a screenshot") with phone-appropriate ones, and speaks amounts the way people do.
"""
from __future__ import annotations

import re

from laundryops.domain.models import Language

_SCREEN_TO_PHONE: dict[Language, list[tuple[str, str]]] = {
    Language.DE: [
        (r"antworten Sie bitte mit einem Screenshot", "schicken wir Ihnen gleich eine SMS, auf die Sie mit einem Foto antworten können"),
        (r"antworten Sie bitte kurz auf diese Nachricht", "sagen Sie es mir bitte jetzt"),
        (r"antworten Sie bitte mit dem Fehlertext", "sagen Sie mir bitte, was auf dem Bildschirm steht"),
        (r"Weitere Antworten finden Sie in der App unter „Hilfe“\.", ""),
        (r"Ihr Anliegen wurde an eine Kollegin / einen Kollegen übergeben, die sich innerhalb eines Werktags melden\.", ""),
    ],
    Language.EN: [
        (r"please reply with a screenshot", "we'll text you now so you can reply with a photo"),
        (r"just reply to this message", "just tell me now"),
        (r"reply with a screenshot", "we'll text you so you can send a photo"),
        (r"reply with the error text", "tell me what the screen says"),
        (r"More answers are under \"Help\" in the app\.", ""),
        (r"Your request has been handed to a colleague who will get back to you within one business day\.", ""),
    ],
    Language.TR: [
        (r"ekran görüntüsüyle yanıt verin", "size şimdi bir SMS göndereceğiz, fotoğrafla yanıtlayabilirsiniz"),
        (r"bu mesaja yanıt verin", "şimdi söyleyin lütfen"),
        (r"hata metniyle yanıt verin", "ekranda ne yazdığını söyleyin"),
        (r"Diğer yanıtlar uygulamada \"Yardım\" altında\.", ""),
        (r"Talebiniz bir iş günü içinde size dönecek bir meslektaşımıza iletildi\.", ""),
    ],
    Language.FR: [
        (r"répondez avec une capture d'écran", "nous vous envoyons un SMS pour que vous puissiez répondre avec une photo"),
        (r"répondez simplement à ce message", "dites-le-moi maintenant"),
        (r"répondez avec le message d'erreur", "dites-moi ce qu'affiche l'écran"),
        (r"D'autres réponses sont dans « Aide »\.", ""),
        (r"Votre demande a été transmise à un collègue qui vous répondra sous un jour ouvré\.", ""),
    ],
    Language.ES: [
        (r"responde con una captura", "te enviamos un SMS para que respondas con una foto"),
        (r"responde a este mensaje", "dímelo ahora"),
        (r"responde con el texto del error", "dime qué pone en la pantalla"),
        (r"Más respuestas en «Ayuda»\.", ""),
        (r"Tu solicitud se ha pasado a un compañero que te responderá en un día laborable\.", ""),
    ],
}

_MONEY = re.compile(r"(\d+),(\d{2}) €")
_MONEY_SPOKEN: dict[Language, str] = {
    Language.DE: "{e} Euro {c}", Language.EN: "{e} euros {c}", Language.TR: "{e} Euro {c}",
    Language.FR: "{e} euros {c}", Language.ES: "{e} euros con {c}",
}


def speak_money(text: str, language: Language) -> str:
    fmt = _MONEY_SPOKEN.get(language, _MONEY_SPOKEN[Language.EN])

    def repl(m: re.Match) -> str:
        e, c = int(m.group(1)), int(m.group(2))
        if c == 0:
            return fmt.format(e=e, c="").rstrip()
        return fmt.format(e=e, c=c)

    return _MONEY.sub(repl, text)


def shape_for_voice(reply: str, language: Language) -> str:
    """Body only, screen phrases swapped, money spoken, whitespace tidy."""
    paragraphs = [p.strip() for p in reply.split("\n\n") if p.strip()]
    if len(paragraphs) >= 3:
        body = " ".join(paragraphs[1:-1])
    elif len(paragraphs) == 2:
        body = paragraphs[1]
    else:
        body = paragraphs[0] if paragraphs else ""
    for pat, rep in _SCREEN_TO_PHONE.get(language, []):
        body = re.sub(pat, rep, body)
    body = speak_money(body, language)
    body = re.sub(r"\s+", " ", body).strip()
    body = re.sub(r"\s*\((Referenz|reference|referans|référence|referencia) [^)]+\)", "", body).strip()
    if body:
        body = body[0].upper() + body[1:]
    return body
