"""Pure string normalisation for addresses, names, phones and zips.

This module imports nothing else from the package on purpose. It is the file
most likely to need a live tweak (a new street suffix, a new abbreviation), so
it stays trivially readable and trivially testable.
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

#: Compass prefixes. "Northwest Sylvania Avenue" and "NW Sylvania Ave" are the
#: same street; the CRM and the website disagree about which form to store.
DIRECTIONALS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
    "n": "n", "s": "s", "e": "e", "w": "w",
    "nw": "nw", "ne": "ne", "sw": "sw", "se": "se",
}

#: Street-type suffixes, collapsed to a canonical short form.
SUFFIXES = {
    "avenue": "ave", "ave": "ave", "av": "ave",
    "street": "st", "st": "st",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "boulevard": "blvd", "blvd": "blvd",
    "lane": "ln", "ln": "ln",
    "pike": "pike", "pk": "pike",
    "parkway": "pkwy", "pkwy": "pkwy",
    "court": "ct", "ct": "ct",
    "circle": "cir", "cir": "cir",
    "square": "sq", "sq": "sq",
    "place": "pl", "pl": "pl",
    "terrace": "ter", "ter": "ter",
    "trail": "trl", "trl": "trl",
    "highway": "hwy", "hwy": "hwy",
    "way": "way",
}

_PO_BOX = re.compile(r"^\s*p\.?\s*o\.?\s*box\b", re.IGNORECASE)


def _tokens(s) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split()


def is_po_box(s) -> bool:
    """A PO box is a mailing address, not a location. Street comparison against
    one is meaningless, so the matcher skips address tiers when it sees one."""
    return bool(_PO_BOX.match(str(s or "")))


def norm_street(s) -> str:
    """Canonical street form.

    Known limitation: 'St' is treated as 'Street' even where it means 'Saint'
    (e.g. '45 St Lawrence Dr'). Harmless, because both sides of every comparison
    go through this same function and collapse identically.
    """
    return " ".join(DIRECTIONALS.get(w) or SUFFIXES.get(w) or w for w in _tokens(s))


def norm_zip(z) -> str:
    """Five-digit string. Stays a string so a leading zero can't be lost."""
    digits = re.sub(r"\D", "", str(z if z is not None else ""))
    if not digits:
        return ""
    return digits[:5].zfill(5)


def norm_city(c) -> str:
    return " ".join(_tokens(c))


def norm_phone(p) -> str:
    """Last ten digits, or empty when there aren't ten."""
    digits = re.sub(r"\D", "", str(p or ""))
    return digits[-10:] if len(digits) >= 10 else ""


def norm_name(n) -> str:
    """Casefold, expand '&', drop punctuation and a leading 'The'."""
    text = str(n or "").lower().replace("&", " and ")
    words = re.sub(r"[^a-z0-9 ]", " ", text).split()
    if words and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def name_sim(a, b) -> float:
    """0-100 similarity. token_set_ratio so word order and extra words
    ('Care Center' vs 'Center') don't tank an otherwise good match.

    NEVER use this without the city/state gate in match.py — see the Amberly
    and Willowbrook decoys.
    """
    return fuzz.token_set_ratio(norm_name(a), norm_name(b))
