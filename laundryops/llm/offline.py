"""Deterministic, dependency-free triage and reply drafting.

Used (a) as the fallback when no model is reachable, (b) in CI and the eval harness so the
numbers are reproducible, and (c) as a floor: the live model has to beat this on the eval
set or it does not get deployed.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from laundryops.domain.models import Intent, Language, TriageResult

# ---- language detection -----------------------------------------------------------
_LANG_MARKERS: dict[Language, list[str]] = {
    # "Waschmaschine 3" / "Trockner 1" are sticker labels used by residents of every language: not markers.
    Language.DE: ["ich", "nicht", "und", "die ", "der ", "das ", "ist ", "habe", "wurde", "bitte", "ß", "ä", "ö", "ü",
                  "geld", "abgebucht", "seit", "mein", "noch", "aber", "wieder", "kaputt"],
    Language.EN: ["the ", "my ", " is ", "and ", "washer", "dryer", "charge", "money", "please", "doesn", "won't",
                  "didn", "have", "still", "for ", "cycle", "refund", "twice", "single", "stopped", "start"],
    Language.TR: ["ı", "ş", "ğ", "makine", "para", "değil", "çamaşır", "kurutma", "lütfen", "yıka", "ödeme",
                  "iade", "çalış"],
    Language.FR: ["je ", "la ", "pas ", "machine", "est ", "é", "lave", "sèche", "argent", "merci", "mon ",
                  "prélev", "débit", "rembours"],
    Language.ES: ["la ", "no ", "me ", "lavadora", "secadora", "máquina", "está", "dinero", "gracias",
                  "cobr", "reembolso", "mi "],
    Language.IT: ["lavatrice", "asciugatrice", "non ", "soldi", "rimborso", "grazie", "addebit", "macchina ", "porta "],
    Language.PT: ["máquina de lavar", "secadora", "não ", "dinheiro", "reembolso", "obrigad", "cobra", "porta "],
    Language.PL: ["pralka", "suszarka", "nie ", "pieniądze", "zwrot", "dziękuję", "drzwi", "zapłaci"],
    Language.NL: ["wasmachine", "droger", "niet ", "geld", "terugbetaling", "bedankt", "deur", "betaald"],
    Language.RO: ["mașina de spălat", "uscător", "nu ", "bani", "rambursare", "mulțumesc", "ușa"],
}
_SCRIPTS: list[tuple[str, Language]] = [
    (r"[\u0400-\u04FF]", Language.RU), (r"[\u0600-\u06FF]", Language.AR), (r"[\u0900-\u097F]", Language.HI),
    (r"[\u0980-\u09FF]", Language.BN), (r"[\u0370-\u03FF]", Language.EL), (r"[\u4E00-\u9FFF]", Language.ZH),
    (r"[\u3040-\u30FF]", Language.JA), (r"[\uAC00-\uD7AF]", Language.KO),
]
_SCRIPT_REFINE = {Language.RU: [(r"[іїєґ]", Language.UK)], Language.AR: [(r"[پچژگ]", Language.FA), (r"[ٹڈڑےں]", Language.UR)]}

# "Do you speak English?" in many spellings — an explicit request beats detection.
LANGUAGE_REQUESTS: dict[Language, str] = {
    Language.EN: r"\b(english|englisch|anglais|ingl[eé]s|ingilizce|inglese|angielski|engels|английск|انجليزي|अंग्रेज़ी|अंग्रेजी)\b",
    Language.DE: r"\b(deutsch|german|allemand|alem[aá]n|almanca|tedesco|niemiecki|duits|немецк)\b",
    Language.TR: r"\b(t[üu]rk[çc]e|turkish|t[üu]rkisch|turc|turco)\b",
    Language.FR: r"\b(fran[çc]ais|french|franz[öo]sisch|franc[eé]s)\b",
    Language.ES: r"\b(espa[ñn]ol|spanish|spanisch|castellano)\b",
    Language.IT: r"\b(italiano|italian|italienisch|italien)\b",
    Language.PT: r"\b(portugu[eê]s|portuguese|portugiesisch)\b",
    Language.PL: r"\b(polski|po polsku|polish|polnisch)\b",
    Language.NL: r"\b(nederlands|dutch|niederl[äa]ndisch)\b",
    Language.RO: r"\b(rom[âa]n[ăa]|romanian|rum[äa]nisch)\b",
    Language.EL: r"\b(ελληνικ|greek|griechisch)\b",
    Language.RU: r"\b(русск|по-русски|russian|russisch)\b",
    Language.UK: r"\b(україн|ukrainian|ukrainisch)\b",
    Language.AR: r"(عربي|العربية|\barabic\b|\barabisch\b)",
    Language.FA: r"(فارسی|\bfarsi\b|\bpersian\b|\bpersisch\b)",
    Language.UR: r"(اردو|\burdu\b)",
    Language.HI: r"(हिंदी|हिन्दी|\bhindi\b)",
    Language.BN: r"(বাংলা|\bbengali\b|\bbangla\b)",
    Language.ZH: r"(中文|汉语|普通话|\bchinese\b|\bmandarin\b|\bchinesisch\b)",
    Language.JA: r"(日本語|\bjapanese\b|\bjapanisch\b)",
    Language.KO: r"(한국어|\bkorean\b|\bkoreanisch\b)",
    Language.VI: r"(tiếng việt|\bvietnamese\b|\bvietnamesisch\b)",
}


def language_request(text: str) -> Language | None:
    t = text.lower()
    for lang, pat in LANGUAGE_REQUESTS.items():
        if re.search(pat, t):
            return lang
    return None


def fold_case(text: str) -> str:
    """Lower-case that survives Turkish dotted/dotless i."""
    return text.replace("İ", "i").replace("I", "i").lower()


def fold_accents(text: str) -> str:
    """Residents often type without accents (tur -> tür, cift -> çift); match on the folded form."""
    text = text.replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize(text: str) -> str:
    return fold_accents(fold_case(text))


def language_scores(text: str) -> dict[Language, int]:
    t = " " + fold_case(text) + " "
    return {lang: sum(t.count(m) for m in markers) for lang, markers in _LANG_MARKERS.items()}


def detect_language(text: str, hint: Language | None = None) -> Language:
    for pat, lang in _SCRIPTS:                      # non-Latin scripts are unambiguous
        if re.search(pat, text):
            for sub, refined in _SCRIPT_REFINE.get(lang, []):
                if re.search(sub, text):
                    return refined
            return lang
    scores = language_scores(text)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return hint or Language.EN
    # Break near-ties with the user's profile language.
    if hint and hint in scores and scores[best] - scores[hint] <= 1:
        return hint
    return best


# ---- intent lexicon (ordered by priority) --------------------------------------------
_LEXICON: list[tuple[Intent, list[str]]] = [
    (Intent.ESCALATION_REQUIRED, ["anwalt", "verbraucherzentrale", "anzeige erstatt", "erstatte anzeige", "betrüger", "klage", "lawyer",
                                  "legal action", "sue you", "scam", "fraud", "police", "avukat", "dava aç",
                                  "dolandırıcı", "avocat", "plainte", "arnaque", "abogado", "denuncia", "estafa"]),
    (Intent.DAMAGE_CLAIM, ["beschädigt", "kaputt gemacht", "zerrissen", "löcher", "verfärbt", "eingelaufen", "schaden an",
                           "damaged", "ruined", "torn", "tore ", "shrunk", "holes in", "stained", "destroyed my",
                           "zarar", "yırt", "çekti", "mahvoldu", "abîmé", "déchiré", "rétréci", "endommag",
                           "dañ", "rasgó", "encogió", "arruin"]),
    (Intent.DOUBLE_CHARGE, ["doppelt", "zweimal", "zwei mal", "2x", "2 mal", "charged twice", "double charge",
                            "two charges", "twice", "two times", "iki kez", "iki defa", "çift", "deux fois", "double prél",
                            "dos veces", "doble cobro", "cobrado dos"]),
    (Intent.CYCLE_ABORTED, ["abgebrochen", "mittendrin", "stehen geblieben", "ausgegangen", "noch nass", "mitten im",
                            "stopped mid", "aborted", "halfway", "cut out", "still wet", "stopped after",
                            "died ", "yarıda", "durdu", "ıslak", "s'est arrêtée", "arrêtée en plein", "interromp",
                            "encore mouillé", "se detuvo", "se paró", "a mitad", "mojada", "interrumpi"]),
    (Intent.MACHINE_NOT_STARTED, ["startet nicht", "geht nicht an", "bleibt verschlossen", "bleibt zu", "lässt sich nicht öffnen",
                                  "nicht gestartet", "freigeschaltet aber", "springt nicht an", "tür geht nicht",
                                  "won't start", "doesn't start", "does not start", "not start", "door is locked",
                                  "door locked", "stays locked", "nothing happens", "won't unlock", "not unlocking",
                                  "won't open", "başlamıyor", "çalışmıyor", "kapı kilitli", "açılmıyor", "kapı açılm",
                                  "ne démarre pas", "ne s'ouvre pas", "reste verrouillée", "porte bloquée", "rien ne se passe",
                                  "no arranca", "no inicia", "no se abre", "puerta bloqueada", "no pasa nada", "no enciende"]),
    (Intent.CHARGED_NO_SESSION, ["abgebucht aber", "abgebucht, aber", "geld abgezogen", "ohne zu waschen", "nie gewaschen",
                                 "keine maschine bekommen", "nichts gewaschen", "trotzdem abgebucht", "geld weg",
                                 "charged but", "money taken", "never got", "didn't wash", "no machine", "charged without",
                                 "took my money", "charged me although", "charged anyway",
                                 "para çekildi", "yıkamadım", "makine alamadım", "para gitti",
                                 "débité mais", "prélevé sans", "jamais lavé", "pas eu de machine", "prélevé alors",
                                 "me cobraron pero", "cobrado sin", "nunca lavé", "no obtuve", "cobraron"]),
    (Intent.RESERVATION_CONFLICT, ["reserviert", "jemand anderes", "benutzt jemand", "jemand anders", "reserved", "someone else",
                                   "somebody else", "using my machine", "rezerve", "başkası", "réservé", "quelqu'un d'autre",
                                   "reservé", "reservado", "otra persona", "alguien más"]),
    (Intent.REFUND_STATUS, ["wo bleibt", "rückerstattung", "erstattung", "wann bekomme ich mein geld", "refund", "where is my money",
                            "when will i get", "iade", "remboursement", "rembourse", "reembolso", "devolución"]),
    (Intent.PARTNER_PAYOUT, ["auszahlung", "umsatzbeteiligung", "unser anteil", "hausverwaltung", "gutschrift",
                             "payout", "revenue share", "our share", "property manag", "ödeme bekliyoruz",
                             "hesap özeti", "payımız", "versement", "notre part", "relevé", "nuestra parte", "liquidación"]),
    (Intent.MACHINE_REPORT, ["dreckig", "schmutzig", "stinkt", "seit wochen kaputt", "defekt", "flusensieb", "laut", "wackelt",
                             "dirty", "smells", "broken for", "out of order", "lint", "noisy", "vibrat", "kirli", "kokuyor",
                             "bozuk", "sale", "sent mauvais", "en panne", "sucia", "huele", "averiada", "rota"]),
    (Intent.APP_OR_PAYMENT_METHOD, ["einloggen", "passwort", "paypal", "kreditkarte", "guthaben aufladen", "zahlungsmethode",
                                    "app stürzt", "login", "password", "credit card", "top up", "payment method", "app crashes",
                                    "giriş yap", "şifre", "kredi kartı", "uygulama", "connexion", "mot de passe", "carte bancaire",
                                    "application", "iniciar sesión", "contraseña", "tarjeta", "aplicación"]),
    (Intent.GENERAL_QUESTION, ["wie viel kostet", "preis", "öffnungszeiten", "wie funktioniert", "wie lange dauert", "how much",
                               "price", "how long", "how does", "opening hours", "ne kadar", "fiyat", "nasıl", "combien", "prix",
                               "comment", "cuánto", "precio", "cómo"]),
]

_COMPILED: list[tuple[Intent, list[re.Pattern[str]]]] = [
    (intent, [re.compile(r"(?<!\w)" + re.escape(fold_accents(w))) for w in words]) for intent, words in _LEXICON
]
_APP_CONTEXT = re.compile(fold_accents(r"(?<!\w)(uygulama|şifre|giriş|app\b|login|log in|passwort|password|mot de passe|contraseña|einloggen|anmelden|sesión|connexion)"))
_MACHINE_CONTEXT = re.compile(fold_accents(r"(?<!\w)(makine|maschine|machine|washer|dryer|trockner|lavadora|secadora|lave|sèche|kurutma|wm-|td-|kapı|tür|door|porte|puerta)"))
_MACHINE_ID = re.compile(r"\b(WM|TD)[-\s]?(\d{1,4})\b", re.I)
_MACHINE_LABEL = re.compile(
    r"(maschine|waschmaschine|trockner|machine|washer|dryer|makine|kurutma|lave-linge|sèche-linge|lavadora|secadora|máquina)"
    r"\s*(nr\.?|no\.?|#|n°|numéro|número)?\s*(\d{1,2})\b", re.I)

_INJECTION = re.compile(r"(ignore (all )?(previous|prior) instructions|you are now (an? )?(admin|system|developer)|system prompt|"
                        r"als administrator|as an admin|override)", re.I)


def extract_machine_ref(text: str) -> str | None:
    m = _MACHINE_ID.search(text)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2)):04d}"
    m = _MACHINE_LABEL.search(text)
    if m:
        return f"label:{m.group(3)}"
    return None


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


class OfflineLLM:
    name = "offline-rules"

    def triage(self, text: str, hint_language: Language | None = None, is_partner: bool = False) -> TriageResult:
        t = normalize(text)
        lang = detect_language(text, hint_language)
        intent = Intent.UNKNOWN
        confidence = 0.35
        for cand, patterns in _COMPILED:
            hits = sum(1 for p in patterns if p.search(t))
            if hits:
                intent, confidence = cand, min(0.95, 0.6 + 0.15 * hits)
                break
        # "doesn't work" about the app is not a machine fault.
        if intent == Intent.MACHINE_NOT_STARTED and _APP_CONTEXT.search(t) and not _MACHINE_CONTEXT.search(t):
            intent, confidence = Intent.APP_OR_PAYMENT_METHOD, 0.7
        if is_partner and intent in (Intent.UNKNOWN, Intent.GENERAL_QUESTION, Intent.REFUND_STATUS):
            intent, confidence = Intent.PARTNER_PAYOUT, max(confidence, 0.7)
        return TriageResult(
            intent=intent, language=lang, confidence=confidence, machine_ref=extract_machine_ref(text),
            mentions_damage=intent == Intent.DAMAGE_CLAIM,
            mentions_legal_or_abuse=intent == Intent.ESCALATION_REQUIRED,
            summary=text[:140],
        )

    def draft_reply(self, facts: dict[str, Any], language: Language) -> str:
        from laundryops.support.templates import render_reply
        return render_reply(facts, language)

    def translate(self, text: str, language: Language) -> str:
        """No model offline: non-native languages are served in English."""
        return text
