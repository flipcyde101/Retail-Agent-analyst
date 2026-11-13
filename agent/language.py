"""Guess whether a short answer is written in English, Spanish or French.

Counting common words that belong to one of the three languages only is enough here: the
answers are a few sentences long and the alternative is a dependency. Words shared by two of
them (la, de, en, un, que, no) are left out, and a few words of this domain (prices, precios,
prix) are added. When the count is too low or tied the result is
"unknown", and an unknown answer is never reported as written in the wrong language.
"""

import re

MARKERS = {
    "en": frozenset(
        "the is are of and with has have at for there by to this from not which in was were "
        "its but than each prices stores products shops how many much what where when "
        "does did do".split()
    ),
    "es": frozenset(
        "el los las hay por para una más del con y según tiene tienen fue son entre sobre "
        "este esta pero cada desde precios tiendas productos cuántos cuántas cuánto cuál "
        "qué dónde cómo cuesta".split()
    ),
    "fr": frozenset(
        "le les des est du une et dans avec pour au aux sont il pas sur à chez ce cette mais "
        "ne entre soit ont prix magasins produits combien quel quelle quels quelles où "
        "coûte".split()
    ),
}
MIN_MARKERS = 2
NAMES = {"en": "English", "es": "Spanish", "fr": "French"}

_WORD = re.compile(r"[a-zàâäçéèêëîïôöùûüÿñáíóú]+")


def detect_language(text: str) -> str:
    words = _WORD.findall(text.lower())
    scores = {lang: sum(word in markers for word in words) for lang, markers in MARKERS.items()}
    if "¿" in text or "¡" in text:
        scores["es"] += MIN_MARKERS  # only Spanish opens a question or an exclamation
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    (best, best_score), (_, second_score) = ranked[0], ranked[1]
    if best_score < MIN_MARKERS or best_score == second_score:
        return "unknown"
    return best
