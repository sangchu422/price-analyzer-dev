"""Group quote source variants and choose one deterministic parse source."""

from __future__ import annotations

import ntpath
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


_UNLOCKED_SUFFIX = "_보안해제"


@dataclass(frozen=True)
class SourceGroup:
    logical_name: str
    variants: tuple[Path, ...]
    preferred: Path

    def __post_init__(self) -> None:
        if not isinstance(self.variants, tuple):
            raise TypeError("variants must be a tuple")
        if self.preferred not in self.variants:
            raise ValueError("preferred must be a member of variants")


def logical_stem(path: Path) -> str:
    """Return a trimmed stem without a trailing ``_보안해제`` marker."""
    stem = path.stem.strip()
    if _has_unlocked_suffix(stem):
        return stem[: -len(_UNLOCKED_SUFFIX)].strip()
    return stem


def build_source_groups(
    paths: Iterable[Path],
    root: Path | None = None,
) -> list[SourceGroup]:
    """Group paths by relative parent and logical stem.

    Relative paths use their portable path from the caller. Absolute paths
    require an explicit absolute ``root`` and are normalized before their
    containment and logical names are determined.
    """
    source_paths = [Path(path) for path in paths]
    if not source_paths:
        return []

    source_paths, relative_paths = _normalize_sources(source_paths, root)
    grouped: dict[str, list[tuple[str, Path]]] = {}
    for source_path, relative_path in zip(
        source_paths,
        relative_paths,
        strict=True,
    ):
        logical_path = relative_path.parent / logical_stem(relative_path)
        logical_name = logical_path.as_posix()
        grouped.setdefault(ntpath.normcase(logical_name), []).append(
            (logical_name, source_path)
        )

    groups = []
    for entries in grouped.values():
        logical_name = min(
            (entry[0] for entry in entries),
            key=_text_sort_key,
        )
        variants = tuple(
            sorted(
                (entry[1] for entry in entries),
                key=_variant_sort_key,
            )
        )
        groups.append(
            SourceGroup(
                logical_name=logical_name,
                variants=variants,
                preferred=variants[0],
            )
        )

    return sorted(groups, key=lambda group: _text_sort_key(group.logical_name))


def _normalize_sources(
    paths: list[Path],
    root: Path | None,
) -> tuple[list[Path], list[Path]]:
    absolute_states = {path.is_absolute() for path in paths}
    if len(absolute_states) > 1:
        raise ValueError("paths must be all relative or all absolute")

    if absolute_states == {False}:
        if root is not None:
            raise ValueError("root must be omitted for relative paths")
        if any(".." in path.parts for path in paths):
            raise ValueError("relative path traversal is not allowed")
        return paths, paths

    if root is None:
        raise ValueError("absolute paths require an explicit stable root")

    source_root = Path(root)
    if not source_root.is_absolute():
        raise ValueError("root must be absolute for absolute paths")
    normalized_root = source_root.resolve(strict=False)
    normalized_paths: list[Path] = []
    relative_paths: list[Path] = []
    for path in paths:
        normalized_path = path.resolve(strict=False)
        try:
            relative_path = normalized_path.relative_to(normalized_root)
        except ValueError as exc:
            raise ValueError(
                f"path is outside the declared root: {path}"
            ) from exc
        normalized_paths.append(normalized_path)
        relative_paths.append(relative_path)
    return normalized_paths, relative_paths


def _is_unlocked(path: Path) -> bool:
    return _has_unlocked_suffix(path.stem.strip())


def _has_unlocked_suffix(stem: str) -> bool:
    if len(stem) < len(_UNLOCKED_SUFFIX):
        return False
    return ntpath.normcase(stem[-len(_UNLOCKED_SUFFIX) :]) == ntpath.normcase(
        _UNLOCKED_SUFFIX
    )


def _variant_sort_key(path: Path) -> tuple[int, int, str, str]:
    extension = ntpath.normcase(path.suffix)
    extension_priority = {
        ".xlsx": 0,
        ".xls": 1,
    }.get(extension, 2)
    normalized_path = path.as_posix()
    return (
        0 if _is_unlocked(path) else 1,
        extension_priority,
        ntpath.normcase(normalized_path),
        normalized_path,
    )


def _text_sort_key(value: str) -> tuple[str, str]:
    return ntpath.normcase(value), value
