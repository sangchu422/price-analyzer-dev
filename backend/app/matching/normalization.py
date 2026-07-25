from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
_HYPHENATED = re.compile(r"(?<![A-Z0-9])([A-Z0-9]+)-([A-Z0-9]+)(?![A-Z0-9])")
_NUMERIC_SUFFIX = re.compile(r"\b(\d{3,})\s+([A-Z0-9]{1,4})\b")
_RATING_TOKEN = re.compile(
    r"^\d+(?:\.\d+)?(?:"
    r"W|KW|MW|V|VAC|VDC|KV|KVAC|KVDC|A|AAC|ADC|MA|KA|"
    r"VA|KVA|MVA|HP|PH|NM|NMM|BAR|MBAR|PSI|PA|KPA|MPA|"
    r"HZ|KHZ|MHZ|GHZ|RPM|MM|CM|M|KM|UM|KG|G|MG|L|ML|"
    r"OHM|KOHM|MOHM|C|F"
    r")$"
)
_NON_MODEL_SUFFIXES = {"EA", "PC", "PCS", "SET"}


def _preserve_model_hyphen(match: re.Match[str]) -> str:
    left, right = match.groups()
    combined = left + right
    if any(character.isdigit() for character in combined) and any(
        character.isalpha() for character in left
    ):
        return f"{left}-{right}"
    return f"{left} {right}"


def normalize_search_text(value: str | None) -> str:
    """Return stable text for lexical matching without erasing model IDs."""

    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value)).upper()
    normalized = _HYPHENATED.sub(_preserve_model_hyphen, normalized)

    output: list[str] = []
    for character in normalized:
        if character in "()":
            output.extend((" ", character, " "))
            continue
        if character == "-":
            output.append(character)
            continue
        if character.isalnum() or unicodedata.category(character).startswith(
            ("L", "N")
        ):
            output.append(character)
            continue
        output.append(" ")
    collapsed = _WHITESPACE.sub(" ", "".join(output)).strip()
    return collapsed.replace("( ", "(").replace(" )", ")")


def model_tokens(value: str | None) -> tuple[str, ...]:
    """Extract explicit model-like identifiers in deterministic order."""

    normalized = normalize_search_text(value)
    tokens: set[str] = set()
    for token in _TOKEN.findall(normalized):
        if not any(character.isdigit() for character in token):
            continue
        if any(character.isalpha() for character in token):
            tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)

    for match in _NUMERIC_SUFFIX.finditer(normalized):
        numeric, suffix = match.groups()
        joined = f"{numeric}{suffix}"
        if is_rating_token(joined):
            tokens.discard(numeric)
        elif suffix not in _NON_MODEL_SUFFIXES:
            tokens.add(f"{numeric}-{suffix}")
    return tuple(sorted(tokens))


def is_rating_token(token: str) -> bool:
    """Return whether a numeric token expresses a measurement, not a model."""

    compact = normalize_search_text(token).replace(" ", "").replace("-", "")
    return bool(_RATING_TOKEN.fullmatch(compact))
