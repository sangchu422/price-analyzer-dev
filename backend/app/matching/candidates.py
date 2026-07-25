from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from rapidfuzz import fuzz

from app.embeddings.base import (
    EmbeddingClient,
    EmbeddingUnavailableError,
)
from app.embeddings.index import EmbeddingIndex, IndexMismatchError
from app.matching.normalization import (
    is_rating_token,
    model_tokens,
    normalize_search_text,
)

SCORE_QUANTUM = Decimal("0.000001")
NAME_WEIGHT = Decimal("0.650000")
SPEC_WEIGHT = Decimal("0.250000")
TOKEN_WEIGHT = Decimal("0.100000")
LEXICAL_THRESHOLD = Decimal("0.650000")
MODEL_TOKEN_MINIMUM = Decimal("0.900000")
RATING_ONLY_SCORE_CEILING = MODEL_TOKEN_MINIMUM - SCORE_QUANTUM
MODEL_NAME_COMPATIBILITY_MINIMUM = Decimal("0.500000")
PERFECT_SCORE = Decimal("1.000000")
_ALPHANUMERIC_RUN = re.compile(r"[A-Z]+|\d+")


@dataclass(frozen=True)
class MatchQuery:
    name: str
    spec: str | None
    unit: str | None

    def __post_init__(self) -> None:
        if not normalize_search_text(self.name):
            raise ValueError("query name must not be blank")


@dataclass(frozen=True)
class CandidateItem:
    standard_item_id: int
    name: str
    spec: str | None = None
    unit: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not normalize_search_text(self.name):
            raise ValueError("candidate name must not be blank")


@dataclass(frozen=True)
class CandidateScore:
    standard_item_id: int
    name_score: Decimal
    spec_score: Decimal
    token_score: Decimal
    embedding_score: Decimal | None
    embedding_status: str
    final_score: Decimal
    matched_tokens: tuple[str, ...]
    method: str


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def _ratio(first: str, second: str) -> Decimal:
    if not first and not second:
        return PERFECT_SCORE
    if not first or not second:
        return Decimal("0.000000")
    return _quantize(Decimal(str(fuzz.WRatio(first, second))) / Decimal(100))


def _token_weight(token: str) -> Decimal:
    return Decimal(len(token.replace("-", "")))


def _token_score(
    query_tokens: tuple[str, ...],
    item_tokens: tuple[str, ...],
    matched_tokens: tuple[str, ...],
) -> Decimal:
    all_tokens = set(query_tokens) | set(item_tokens)
    if not all_tokens:
        return Decimal("0.000000")
    possible = sum((_token_weight(token) for token in all_tokens), Decimal())
    matched = sum((_token_weight(token) for token in matched_tokens), Decimal())
    return _quantize(matched / possible)


def _lexical_score(
    *,
    query_name: str,
    query_spec: str,
    query_tokens: tuple[str, ...],
    item: CandidateItem,
) -> tuple[Decimal, Decimal, Decimal]:
    names = (item.name, *item.aliases)
    name_score = max(
        (_ratio(query_name, normalize_search_text(name)) for name in names),
        default=Decimal("0.000000"),
    )
    item_spec = normalize_search_text(item.spec)
    spec_score = _ratio(query_spec, item_spec)
    item_tokens = model_tokens(f"{item.name} {item.spec or ''}")
    matched_tokens = tuple(sorted(set(query_tokens) & set(item_tokens)))
    token_score = _token_score(query_tokens, item_tokens, matched_tokens)
    return name_score, spec_score, token_score


def _normalized_tokens(name: str, spec: str | None) -> tuple[str, ...]:
    return model_tokens(f"{name} {spec or ''}")


def _units_conflict(query_unit: str, item_unit: str) -> bool:
    return bool(query_unit and item_unit and query_unit != item_unit)


def _model_tokens_conflict(
    query_tokens: tuple[str, ...],
    item_tokens: tuple[str, ...],
) -> bool:
    if not query_tokens or not item_tokens:
        return False
    query_identifiers = _identifier_tokens(query_tokens)
    item_identifiers = _identifier_tokens(item_tokens)
    if query_identifiers and item_identifiers:
        query_only = query_identifiers - item_identifiers
        item_only = item_identifiers - query_identifiers
        return bool(query_only and item_only)
    return not bool(set(query_tokens) & set(item_tokens))


def _identifier_tokens(tokens: tuple[str, ...]) -> set[str]:
    return {token for token in tokens if not is_rating_token(token)}


def _strong_identifier_tokens(tokens: tuple[str, ...]) -> set[str]:
    identifiers = _identifier_tokens(tokens)
    strong: set[str] = set()
    for token in identifiers:
        prefix = token.split("-", 1)[0]
        mixed_prefix = any(character.isalpha() for character in prefix) and any(
            character.isdigit() for character in prefix
        )
        hyphenated_model = (
            "-" in token
            and not is_rating_token(prefix)
            and (
                prefix[0].isalpha()
                or (prefix.isdigit() and len(prefix) >= 4)
                or mixed_prefix
            )
        )
        alpha_numeric_model = (
            len(token) >= 5
            and token[0].isalpha()
            and any(character.isdigit() for character in token)
        )
        complex_digit_leading_model = (
            "-" not in token
            and token[0].isdigit()
            and len(_ALPHANUMERIC_RUN.findall(token)) >= 4
        )
        if (
            hyphenated_model
            or alpha_numeric_model
            or complex_digit_leading_model
        ):
            strong.add(token)
    return strong


def _evidence_tokens(tokens: tuple[str, ...]) -> tuple[str, ...]:
    compound_prefixes = {
        token.split("-", 1)[0] for token in tokens if "-" in token
    }
    return tuple(
        token
        for token in tokens
        if token not in compound_prefixes or "-" in token
    )


def _embedding_scores(
    *,
    query: MatchQuery,
    embedding_client: EmbeddingClient | None,
    embedding_index: EmbeddingIndex | None,
) -> tuple[dict[int, float], str]:
    if embedding_client is None and embedding_index is None:
        return {}, "DISABLED"
    if embedding_client is None or embedding_index is None:
        return {}, "UNAVAILABLE"
    try:
        batch = embedding_client.embed(
            [f"{query.name} {query.spec or ''}".strip()]
        )
        if batch.model != embedding_index.metadata.model:
            raise IndexMismatchError("embedding model mismatch")
        if batch.dimension != embedding_index.metadata.dimension:
            raise IndexMismatchError("embedding dimension mismatch")
        scores = embedding_index.scores(batch.vectors[0])
        if any(
            not math.isfinite(float(score))
            for score in scores.values()
        ):
            raise IndexMismatchError("embedding scores must be finite")
    except (
        EmbeddingUnavailableError,
        IndexMismatchError,
        TypeError,
        ValueError,
    ):
        return {}, "UNAVAILABLE"
    status = "MOCK_ONLY" if batch.model == "local-mock-v1" else "AVAILABLE"
    return scores, status


def rank_candidates(
    *,
    query: MatchQuery,
    items: Iterable[CandidateItem],
    top_n: int = 10,
    embedding_client: EmbeddingClient | None = None,
    embedding_index: EmbeddingIndex | None = None,
) -> list[CandidateScore]:
    """Rank compatible candidates without making a membership decision."""

    if isinstance(top_n, bool) or not isinstance(top_n, int):
        raise TypeError("top_n must be an integer")
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    query_name = normalize_search_text(query.name)
    query_spec = normalize_search_text(query.spec)
    query_unit = normalize_search_text(query.unit)
    query_tokens = _normalized_tokens(query.name, query.spec)
    semantic_scores, embedding_status = _embedding_scores(
        query=query,
        embedding_client=embedding_client,
        embedding_index=embedding_index,
    )
    results: list[CandidateScore] = []

    for item in items:
        item_name = normalize_search_text(item.name)
        item_spec = normalize_search_text(item.spec)
        item_unit = normalize_search_text(item.unit)
        item_tokens = _normalized_tokens(item.name, item.spec)
        if _units_conflict(query_unit, item_unit):
            continue
        if _model_tokens_conflict(query_tokens, item_tokens):
            continue

        score_tokens = tuple(sorted(set(query_tokens) & set(item_tokens)))
        matched_tokens = _evidence_tokens(score_tokens)
        matched_identifiers = (
            _identifier_tokens(query_tokens) & _identifier_tokens(item_tokens)
        )
        matched_strong_identifiers = (
            _strong_identifier_tokens(query_tokens)
            & _strong_identifier_tokens(item_tokens)
        )
        name_score, spec_score, token_score = _lexical_score(
            query_name=query_name,
            query_spec=query_spec,
            query_tokens=query_tokens,
            item=item,
        )
        raw_embedding_score = semantic_scores.get(item.standard_item_id)
        embedding_score = (
            _quantize(
                Decimal(str(max(0.0, min(1.0, raw_embedding_score))))
            )
            if raw_embedding_score is not None
            else None
        )
        exact = (
            query_name == item_name
            and query_spec == item_spec
            and query_unit == item_unit
        )
        if exact:
            final_score = PERFECT_SCORE
            method = "EXACT_RULE_V1"
        elif (
            matched_strong_identifiers
            or (
                matched_identifiers
                and name_score >= MODEL_NAME_COMPATIBILITY_MINIMUM
            )
        ):
            final_score = _quantize(
                MODEL_TOKEN_MINIMUM
                + (PERFECT_SCORE - MODEL_TOKEN_MINIMUM) * token_score
            )
            method = "MODEL_TOKEN_RULE_V1"
        else:
            final_score = _quantize(
                NAME_WEIGHT * name_score
                + SPEC_WEIGHT * spec_score
                + TOKEN_WEIGHT * token_score
            )
            if (
                embedding_score is not None
                and embedding_score > final_score
            ):
                final_score = embedding_score
                method = "EMBEDDING_CANDIDATE_V1"
            else:
                method = "LEXICAL_RULE_V1"
            if score_tokens:
                final_score = min(final_score, RATING_ONLY_SCORE_CEILING)
            if final_score < LEXICAL_THRESHOLD:
                continue

        results.append(
            CandidateScore(
                standard_item_id=item.standard_item_id,
                name_score=name_score,
                spec_score=spec_score,
                token_score=token_score,
                embedding_score=embedding_score,
                embedding_status=embedding_status,
                final_score=final_score,
                matched_tokens=matched_tokens,
                method=method,
            )
        )

    return sorted(
        results,
        key=lambda candidate: (
            -candidate.final_score,
            candidate.standard_item_id,
        ),
    )[:top_n]
