"""Group quote source variants and choose one deterministic parse source."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


_UNLOCKED_SUFFIX = re.compile(r"_보안해제$", re.IGNORECASE)


@dataclass(frozen=True)
class SourceGroup:
    logical_name: str
    variants: list[Path]
    preferred: Path


def logical_stem(path: Path) -> str:
    """Return a trimmed stem without a trailing ``_보안해제`` marker."""
    stem = path.stem.strip()
    return _UNLOCKED_SUFFIX.sub("", stem).strip()


def build_source_groups(
    paths: Iterable[Path],
    root: Path | None = None,
) -> list[SourceGroup]:
    """Group paths by relative parent and logical stem.

    Absolute paths are made relative to ``root`` when supplied. Without a
    root, their common parent is inferred so logical names remain portable.
    """
    source_paths = [Path(path) for path in paths]
    if not source_paths:
        return []

    relative_paths = _relative_paths(source_paths, root)
    grouped: dict[str, list[tuple[str, Path]]] = {}
    for source_path, relative_path in zip(
        source_paths,
        relative_paths,
        strict=True,
    ):
        logical_path = relative_path.parent / logical_stem(relative_path)
        logical_name = logical_path.as_posix()
        grouped.setdefault(logical_name.casefold(), []).append(
            (logical_name, source_path)
        )

    groups = []
    for entries in grouped.values():
        logical_name = min(
            (entry[0] for entry in entries),
            key=_text_sort_key,
        )
        variants = sorted(
            (entry[1] for entry in entries),
            key=_variant_sort_key,
        )
        groups.append(
            SourceGroup(
                logical_name=logical_name,
                variants=variants,
                preferred=variants[0],
            )
        )

    return sorted(groups, key=lambda group: _text_sort_key(group.logical_name))


def _relative_paths(paths: list[Path], root: Path | None) -> list[Path]:
    if root is not None:
        source_root = Path(root)
        return [path.relative_to(source_root) for path in paths]

    absolute_states = {path.is_absolute() for path in paths}
    if absolute_states == {False}:
        return paths
    if len(absolute_states) > 1:
        raise ValueError("paths must be all relative or all absolute")

    try:
        common_parent = Path(
            os.path.commonpath([str(path.parent) for path in paths])
        )
    except ValueError as exc:
        raise ValueError(
            "absolute paths on different roots require an explicit root"
        ) from exc
    return [path.relative_to(common_parent) for path in paths]


def _is_unlocked(path: Path) -> bool:
    return _UNLOCKED_SUFFIX.search(path.stem.strip()) is not None


def _variant_sort_key(path: Path) -> tuple[int, int, str, str]:
    extension = path.suffix.casefold()
    extension_priority = {
        ".xlsx": 0,
        ".xls": 1,
    }.get(extension, 2)
    normalized_path = path.as_posix()
    return (
        0 if _is_unlocked(path) else 1,
        extension_priority,
        normalized_path.casefold(),
        normalized_path,
    )


def _text_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value
