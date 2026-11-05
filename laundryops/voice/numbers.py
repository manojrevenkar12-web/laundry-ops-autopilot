"""Spoken numbers → digits, so "Maschine drei" and "machine number three" resolve to a machine."""
from __future__ import annotations

import re

from laundryops.llm.offline import fold_accents, fold_case

_WORDS: dict[str, int] = {}
for lang_words in (
    "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf".split(),
    "zero one two three four five six seven eight nine ten eleven twelve".split(),
    "sıfır bir iki üç dört beş altı yedi sekiz dokuz on onbir oniki".split(),
    "zéro un deux trois quatre cinq six sept huit neuf dix onze douze".split(),
    "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce".split(),
):
    for i, w in enumerate(lang_words):
        _WORDS[fold_accents(w)] = i
_WORDS.update({"une": 1, "una": 1, "eine": 1, "einer": 1, "zwo": 2, "on bir": 11, "on iki": 12})

_MACHINE_WORD = (r"(waschmaschine|maschine|trockner|machine|washer|dryer|kurutma makinesi|camasir makinesi|makinesi|makine|kurutma|"
                 r"lave-linge|seche-linge|lavadora|secadora|maquina)")
_NUM_WORD = "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True))
_PATTERN = re.compile(
    rf"{_MACHINE_WORD}\s*(nummer|number|numara|numero|no\.?|n°|#)?\s*({_NUM_WORD})(?!\w)", re.I)
_BARE_NUM = re.compile(rf"(?<!\w)(nummer|number|numara|numero|die|the|la|le|el)?\s*({_NUM_WORD})(?!\w)", re.I)


def spoken_numbers_to_digits(text: str) -> str:
    """Rewrite 'Waschmaschine drei' → 'Waschmaschine 3' (only next to a machine word, to leave prose alone)."""
    t = fold_accents(text)

    def repl(m: re.Match) -> str:
        return f"{m.group(1)} {_WORDS[fold_case(m.group(3))]}"

    return _PATTERN.sub(repl, t)


def parse_machine_number(utterance: str) -> int | None:
    """For the 'which machine?' answer: '3', 'die drei', 'number three', 'Waschmaschine 3'."""
    t = fold_accents(fold_case(utterance)).strip()
    m = re.search(r"\b(\d{1,2})\b", t)
    if m:
        return int(m.group(1))
    m = _PATTERN.search(t) or _BARE_NUM.search(t)
    if m:
        return _WORDS[m.group(m.lastindex)]
    return None
