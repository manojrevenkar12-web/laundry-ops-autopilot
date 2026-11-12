"""Spoken strings for the hotline, in the five languages the platform serves.

Written for the ear, not the eye: short sentences, no sign-offs, numbers said the way people
say them. ``{machine}`` and ``{name}`` are filled from evidence, never from the caller's words.
"""
from __future__ import annotations

from laundryops.domain.models import Language

# BCP-47 tags for telephony speech recognition / synthesis.
SPEECH_LOCALE: dict[Language, str] = {
    Language.DE: "de-DE", Language.EN: "en-GB", Language.TR: "tr-TR", Language.FR: "fr-FR", Language.ES: "es-ES",
    Language.IT: "it-IT", Language.PT: "pt-PT", Language.PL: "pl-PL", Language.NL: "nl-NL", Language.RO: "ro-RO",
    Language.EL: "el-GR", Language.RU: "ru-RU", Language.UK: "uk-UA", Language.AR: "ar-SA", Language.FA: "fa-IR",
    Language.UR: "ur-PK", Language.HI: "hi-IN", Language.BN: "bn-IN", Language.ZH: "zh-CN", Language.JA: "ja-JP",
    Language.KO: "ko-KR", Language.VI: "vi-VN", Language.UNKNOWN: "de-DE",
}

# Words the recogniser should bias towards (Twilio `hints`, Deepgram keywords).
HINTS: dict[Language, str] = {
    Language.DE: "Waschmaschine, Trockner, Maschine, Tür, abgebucht, doppelt, Erstattung, Nummer, eins, zwei, drei, vier, fünf, sechs",
    Language.EN: "washing machine, washer, dryer, machine, door, charged, twice, refund, number, one, two, three, four, five, six",
    Language.TR: "çamaşır makinesi, kurutma makinesi, makine, kapı, para çekildi, iki kez, iade, numara, bir, iki, üç, dört, beş, altı",
    Language.FR: "lave-linge, sèche-linge, machine, porte, prélevé, deux fois, remboursement, numéro, un, deux, trois, quatre, cinq, six",
    Language.ES: "lavadora, secadora, máquina, puerta, cobrado, dos veces, reembolso, número, uno, dos, tres, cuatro, cinco, seis",
}

_S: dict[Language, dict[str, str]] = {
    Language.DE: {
        "greet_known": "Hallo {name}, hier ist der Waschraum-Service. Was kann ich für Sie tun?",
        "what_else": "Gern. Was kann ich für Sie tun?",
        "greet_unknown": "Hallo, hier ist der Waschraum-Service. Sie können in Ihrer Sprache sprechen — you can speak your own language. Was kann ich für Sie tun?",
        "ask_machine": "Welche Maschine ist es? Die Nummer steht auf dem Aufkleber an der Maschine.",
        "ask_repeat": "Das habe ich leider nicht verstanden. Können Sie das bitte noch einmal sagen?",
        "anything_else": "Kann ich sonst noch etwas für Sie tun?",
        "handoff": "Dafür verbinde ich Sie mit einer Kollegin oder einem Kollegen. Einen Moment bitte.",
        "handoff_identity": "Ich kann Ihr Konto mit dieser Telefonnummer nicht finden. Ich verbinde Sie mit einer Kollegin oder einem Kollegen.",
        "callback": "Gerade ist niemand frei. Wir rufen Sie unter dieser Nummer innerhalb eines Werktags zurück.",
        "goodbye": "Danke für Ihren Anruf. Auf Wiederhören.",
        "yes": "ja|jo|genau|bitte|noch etwas|eine frage",
        "no": "nein|nee|das war's|das wars|das ist alles|danke das war|nichts mehr|tschüss|auf wiederhören",
    },
    Language.EN: {
        "greet_known": "Hi {name}, this is the laundry room service. What can I do for you?",
        "what_else": "Of course. What can I do for you?",
        "greet_unknown": "Hi, this is the laundry room service. You can speak your own language. What can I do for you?",
        "ask_machine": "Which machine is it? The number is on the sticker on the machine.",
        "ask_repeat": "Sorry, I didn't catch that. Could you say it again?",
        "anything_else": "Is there anything else I can do for you?",
        "handoff": "I'll put you through to a colleague for that. One moment please.",
        "handoff_identity": "I can't find an account for this phone number. I'll put you through to a colleague.",
        "callback": "Nobody is free right now. We'll call you back on this number within one business day.",
        "goodbye": "Thanks for calling. Goodbye.",
        "yes": "yes|yeah|yep|one more|another|actually",
        "no": "no|nope|that's all|thats all|that's it|nothing else|thanks bye|bye|goodbye",
    },
    Language.TR: {
        "greet_known": "Merhaba {name}, çamaşırhane servisi. Size nasıl yardımcı olabilirim?",
        "what_else": "Tabii. Size nasıl yardımcı olabilirim?",
        "greet_unknown": "Merhaba, çamaşırhane servisi. Size nasıl yardımcı olabilirim?",
        "ask_machine": "Hangi makine? Numara makinenin üzerindeki etikette yazıyor.",
        "ask_repeat": "Üzgünüm, anlayamadım. Tekrar söyleyebilir misiniz?",
        "anything_else": "Başka bir konuda yardımcı olabilir miyim?",
        "handoff": "Bunun için sizi bir meslektaşıma bağlıyorum. Bir saniye lütfen.",
        "handoff_identity": "Bu telefon numarasıyla bir hesap bulamıyorum. Sizi bir meslektaşıma bağlıyorum.",
        "callback": "Şu anda kimse müsait değil. Bir iş günü içinde bu numaradan sizi arayacağız.",
        "goodbye": "Aradığınız için teşekkürler. Hoşça kalın.",
        "yes": "evet|bir de|bir şey daha|başka",
        "no": "hayır|yok|bu kadar|teşekkürler|hoşça kal|görüşürüz",
    },
    Language.FR: {
        "greet_known": "Bonjour {name}, ici le service laverie. Que puis-je faire pour vous ?",
        "what_else": "Bien sûr. Que puis-je faire pour vous ?",
        "greet_unknown": "Bonjour, ici le service laverie. Que puis-je faire pour vous ?",
        "ask_machine": "De quelle machine s'agit-il ? Le numéro est sur l'étiquette de la machine.",
        "ask_repeat": "Désolé, je n'ai pas compris. Pouvez-vous répéter ?",
        "anything_else": "Puis-je faire autre chose pour vous ?",
        "handoff": "Pour cela, je vous passe un collègue. Un instant.",
        "handoff_identity": "Je ne trouve pas de compte pour ce numéro. Je vous passe un collègue.",
        "callback": "Personne n'est disponible pour le moment. Nous vous rappelons à ce numéro sous un jour ouvré.",
        "goodbye": "Merci pour votre appel. Au revoir.",
        "yes": "oui|ouais|encore|autre chose|une question",
        "no": "non|c'est tout|rien d'autre|merci au revoir|au revoir",
    },
    Language.ES: {
        "greet_known": "Hola {name}, servicio de lavandería. ¿En qué puedo ayudarte?",
        "what_else": "Claro. ¿En qué puedo ayudarte?",
        "greet_unknown": "Hola, servicio de lavandería. ¿En qué puedo ayudarte?",
        "ask_machine": "¿Qué máquina es? El número está en la pegatina de la máquina.",
        "ask_repeat": "Perdona, no lo he entendido. ¿Puedes repetirlo?",
        "anything_else": "¿Puedo ayudarte en algo más?",
        "handoff": "Para eso te paso con un compañero. Un momento, por favor.",
        "handoff_identity": "No encuentro una cuenta con este número de teléfono. Te paso con un compañero.",
        "callback": "Ahora mismo no hay nadie libre. Te llamamos a este número en un día laborable.",
        "goodbye": "Gracias por llamar. Adiós.",
        "yes": "sí|si|otra cosa|una más|una pregunta",
        "no": "no|eso es todo|nada más|gracias adiós|adiós",
    },
}


def say(key: str, language: Language, **kw) -> str:
    """Native languages have hand-written lines; any other language gets the English line (the dialog translates it)."""
    table = _S.get(language, _S[Language.EN])
    return table[key].format(**kw)
