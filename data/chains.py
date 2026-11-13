"""Normalise supermarket chain names.

The source has one name per store format ("Carrefour Market", "Carrefour City") and several
spellings of the same chain ("E.Leclerc", "E. Leclerc", "Centre Commercial E.Leclerc").
To compare chains they must collapse into one name each.
"""

import re

# Order matters: the first pattern that matches wins.
CHAIN_RULES: list[tuple[str, str]] = [
    (r"leclerc", "E.Leclerc"),
    (r"carrefour", "Carrefour"),
    (r"intermarch", "Intermarché"),
    (r"auchan", "Auchan"),
    (r"\b(super u|hyper u|u express|utile|march[ée] u)\b", "U"),
    (r"monop", "Monoprix"),
    (r"franprix", "Franprix"),
    (r"casino", "Casino"),
    (r"\baldi\b", "Aldi"),
    (r"\blidl\b", "Lidl"),
    (r"biocoop", "Biocoop"),
    (r"naturalia", "Naturalia"),
    (r"bio c'? ?bon", "Bio c' Bon"),
    (r"la vie claire", "La Vie Claire"),
    (r"\bspar\b", "Spar"),
    (r"\bg20\b", "G20"),
    (r"\bnetto\b", "Netto"),
    (r"\bpicard\b", "Picard"),
    (r"satoriz", "Satoriz"),
    (r"eau vive", "L'Eau Vive"),
    (r"\bsitis\b", "Sitis"),
    (r"\bcora\b", "Cora"),
    (r"\bmatch\b", "Match"),
    (r"colruyt", "Colruyt"),
]

_COMPILED = [(re.compile(pattern, re.IGNORECASE), chain) for pattern, chain in CHAIN_RULES]


def normalise_chain(brand: str | None, name: str | None) -> str | None:
    """Return the chain for a store, or None if it is an independent shop.

    brand is OSM's brand tag, the reliable signal. name is the shop's own name, used only
    to recognise a known chain when the brand tag is missing.
    """
    for text in (brand, name):
        if not text:
            continue
        for pattern, chain in _COMPILED:
            if pattern.search(text):
                return chain
    # A brand we have no rule for is still a chain; a bare shop name is not.
    return brand.strip() if brand and brand.strip() else None
