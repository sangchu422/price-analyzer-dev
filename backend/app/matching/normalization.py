from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[A-Z0-9]+(?:-[A-Z0-9]+)*")
_HYPHENATED = re.compile(r"(?<![A-Z0-9])([A-Z0-9]+)-([A-Z0-9]+)(?![A-Z0-9])")


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

    tokens: set[str] = set()
    for token in _TOKEN.findall(normalize_search_text(value)):
        if not any(character.isdigit() for character in token):
            continue
        if any(character.isalpha() for character in token):
            tokens.add(token)
        elif len(token) >= 3:
            tokens.add(token)
    return tuple(sorted(tokens))
