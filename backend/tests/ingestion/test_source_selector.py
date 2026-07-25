import json
import os
from dataclasses import FrozenInstanceError
from pathlib import Path, PureWindowsPath

import pytest

from app.ingestion.source_selector import (
    SourceGroup,
    build_source_groups,
    logical_stem,
)


def test_original_and_unlocked_quote_form_one_logical_group() -> None:
    original = Path("5. 견적서.xls")
    unlocked = Path("5. 견적서_보안해제.xlsx")

    groups = build_source_groups([original, unlocked])

    assert groups == [
        SourceGroup(
            logical_name="5. 견적서",
            variants=(unlocked, original),
            preferred=unlocked,
        )
    ]


def test_logical_stem_removes_only_a_trailing_unlock_suffix() -> None:
    assert (
        logical_stem(Path("보안해제 장비_보안해제.xlsx"))
        == "보안해제 장비"
    )
    assert (
        logical_stem(Path("보안해제 장비_보안해제_extra.xlsx"))
        == "보안해제 장비_보안해제_extra"
    )
    assert (
        logical_stem(Path("  보안해제 장비_보안해제  .XLSX"))
        == "보안해제 장비"
    )


def test_preference_is_unlock_state_then_extension_then_path() -> None:
    paths = [
        Path("quote.xlsx"),
        Path("quote.xls"),
        Path("quote_보안해제.xls"),
        Path("quote_보안해제.xlsx"),
    ]

    forward = build_source_groups(paths)
    backward = build_source_groups(reversed(paths))

    assert forward == backward
    assert forward[0].variants == (
        Path("quote_보안해제.xlsx"),
        Path("quote_보안해제.xls"),
        Path("quote.xlsx"),
        Path("quote.xls"),
    )
    assert forward[0].preferred == Path("quote_보안해제.xlsx")


def test_distinct_directories_with_the_same_stem_remain_separate() -> None:
    groups = build_source_groups(
        [
            Path("second/quote_보안해제.xlsx"),
            Path("first/quote.xls"),
            Path("second/quote.xls"),
            Path("first/quote_보안해제.xlsx"),
        ]
    )

    assert [group.logical_name for group in groups] == [
        "first/quote",
        "second/quote",
    ]
    assert all(len(group.variants) == 2 for group in groups)


def test_supplied_root_keeps_absolute_logical_names_machine_independent(
    tmp_path: Path,
) -> None:
    original = tmp_path / "nested" / "quote.xls"
    unlocked = tmp_path / "nested" / "quote_보안해제.xlsx"

    [group] = build_source_groups(
        [unlocked, original],
        root=tmp_path,
    )

    assert group.logical_name == "nested/quote"


def test_all_input_entries_are_preserved_without_deduplication() -> None:
    original = Path("quote.xls")
    unlocked = Path("quote_보안해제.xlsx")

    [group] = build_source_groups([original, unlocked, original])

    assert group.variants == (unlocked, original, original)


def test_source_group_is_deeply_immutable() -> None:
    path = Path("quote.xlsx")
    group = SourceGroup("quote", (path,), path)

    with pytest.raises(FrozenInstanceError):
        group.preferred = Path("other.xlsx")
    with pytest.raises(TypeError):
        group.variants[0] = Path("other.xlsx")


def test_source_group_requires_preferred_to_be_a_variant() -> None:
    with pytest.raises(ValueError, match="preferred"):
        SourceGroup(
            logical_name="quote",
            variants=(Path("quote.xls"),),
            preferred=Path("other.xlsx"),
        )


def test_source_group_rejects_a_mutable_variant_collection() -> None:
    path = Path("quote.xlsx")

    with pytest.raises(TypeError, match="tuple"):
        SourceGroup("quote", [path], path)


def test_absolute_paths_require_an_explicit_stable_root(
    tmp_path: Path,
) -> None:
    quote = tmp_path / "quote.xlsx"

    with pytest.raises(ValueError, match="explicit.*root"):
        build_source_groups([quote])


def test_absolute_path_identity_does_not_depend_on_batch_siblings(
    tmp_path: Path,
) -> None:
    original = tmp_path / "nested" / "quote.xls"
    unlocked = tmp_path / "nested" / "quote_보안해제.xlsx"
    sibling = tmp_path / "other" / "sibling.xlsx"

    pair_group = build_source_groups(
        [original, unlocked],
        root=tmp_path,
    )[0]
    batch_group = next(
        group
        for group in build_source_groups(
            [sibling, unlocked, original],
            root=tmp_path,
        )
        if group.preferred == unlocked.resolve()
    )

    assert pair_group.logical_name == "nested/quote"
    assert batch_group.logical_name == pair_group.logical_name


def test_absolute_traversal_is_rejected_after_normalization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    escaped = root / ".." / "outside" / "quote.xlsx"

    with pytest.raises(ValueError, match="outside.*root"):
        build_source_groups([escaped], root=root)


def test_absolute_path_outside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    outside = tmp_path / "outside" / "quote.xlsx"

    with pytest.raises(ValueError, match="outside.*root"):
        build_source_groups([outside], root=root)


def test_relative_traversal_is_rejected() -> None:
    with pytest.raises(ValueError, match="traversal"):
        build_source_groups([Path("../outside/quote.xlsx")])


def test_current_drive_rooted_path_is_not_portable_relative() -> None:
    path = PureWindowsPath(r"\quotes\q.xls")

    with pytest.raises(ValueError, match="portable relative"):
        build_source_groups([path])


def test_drive_relative_path_is_not_portable_relative() -> None:
    path = PureWindowsPath("C:quotes/q.xls")

    with pytest.raises(ValueError, match="portable relative"):
        build_source_groups([path])


def test_mixed_drive_relative_paths_are_rejected() -> None:
    paths = [
        PureWindowsPath("C:quotes/q.xls"),
        PureWindowsPath("D:quotes/q_보안해제.xlsx"),
    ]

    with pytest.raises(ValueError, match="portable relative"):
        build_source_groups(paths)


def test_mixed_absolute_and_relative_paths_are_rejected(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="all relative or all absolute"):
        build_source_groups(
            [tmp_path / "quote.xlsx", Path("quote.xlsx")],
            root=tmp_path,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows drive semantics")
def test_absolute_path_on_a_different_drive_is_rejected() -> None:
    root = PureWindowsPath("C:/dataset")
    other_drive = PureWindowsPath("D:/dataset/quote.xlsx")

    with pytest.raises(ValueError, match="outside.*root"):
        build_source_groups([other_drive], root=root)


def test_windows_name_normalization_does_not_merge_distinct_unicode() -> None:
    groups = build_source_groups(
        [
            Path("straße/quote.xls"),
            Path("strasse/quote_보안해제.xlsx"),
        ]
    )

    assert [group.logical_name for group in groups] == [
        "strasse/quote",
        "straße/quote",
    ]


def test_windows_name_normalization_groups_ascii_case_variants() -> None:
    [group] = build_source_groups(
        [
            Path("Quotes/QUOTE.xls"),
            Path("quotes/quote_보안해제.XLSX"),
        ]
    )

    assert group.logical_name == "Quotes/QUOTE"
    assert group.preferred == Path("quotes/quote_보안해제.XLSX")


def test_real_unlocked_pair_fixture_groups_all_paths_in_one_batch() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "unlocked_pairs.json"
    )
    pairs = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert len(pairs) == 12
    expected_preferred = {
        (
            "견적서/1차 학습/재검토/"
            "4-1. 견적서 (RQ-B25-002)"
        ): (
            "견적서/1차 학습/재검토/"
            "4-1. 견적서 (RQ-B25-002)_보안해제.xlsx"
        ),
        (
            "견적서/1차 학습/재검토/"
            "4-2. 견적서 (RQ-D25-017)"
        ): (
            "견적서/1차 학습/재검토/"
            "4-2. 견적서 (RQ-D25-017)_보안해제.xlsx"
        ),
        "견적서/1차 학습/재검토/5. 견적서 (1)": (
            "견적서/1차 학습/재검토/5. 견적서 (1)_보안해제.xlsx"
        ),
        "견적서/1차 학습/재검토/5. 견적서": (
            "견적서/1차 학습/재검토/5. 견적서_보안해제.xlsx"
        ),
        "견적서/1차 학습/재검토/9. 견적서_메인조립기": (
            "견적서/1차 학습/재검토/"
            "9. 견적서_메인조립기_보안해제.xlsx"
        ),
        "견적서/1차 학습/재검토/9. 견적서_헤드서브조립기": (
            "견적서/1차 학습/재검토/"
            "9. 견적서_헤드서브조립기_보안해제.xlsx"
        ),
        "견적서/1차 학습/테스트/7. 견적서": (
            "견적서/1차 학습/테스트/7. 견적서_보안해제.xlsx"
        ),
        (
            "견적서/2차 학습/(BREAK DOWN_2025.11.18)"
            "위아 인도 PUNE PROJ' 신작 외경연삭기(GL5Ai-63) "
            "견적서_251103_TPA"
        ): (
            "견적서/2차 학습/(BREAK DOWN_2025.11.18)"
            "위아 인도 PUNE PROJ' 신작 외경연삭기(GL5Ai-63) "
            "견적서_251103_TPA_보안해제.xlsx"
        ),
        "견적서/2차 학습/4510307188 2022-10-28": (
            "견적서/2차 학습/4510307188 2022-10-28_보안해제.xlsx"
        ),
        (
            "견적서/2차 학습/구동 표준견적서/"
            "6. 견적서 CM862202604020106 창원2공장 승용액슬 "
            "12만 증량 대응 침탄 열처리 연속로 투자 件"
        ): (
            "견적서/2차 학습/구동 표준견적서/"
            "6. 견적서 CM862202604020106 창원2공장 승용액슬 "
            "12만 증량 대응 침탄 열처리 연속로 투자 件_보안해제.xlsx"
        ),
        (
            "견적서/2차 학습/QUOTATION_WIA_인도법인 푸네 "
            "CVJ 조립 메인 라인 개조_GD1_260202-001(입찰-2)"
        ): (
            "견적서/2차 학습/QUOTATION_WIA_인도법인 푸네 "
            "CVJ 조립 메인 라인 개조_GD1_260202-001"
            "(입찰-2)_보안해제.xlsx"
        ),
        (
            "견적서/2차 학습/QUOTATION_WIA_인도법인 푸네 "
            "TJ등급선별기 개조_GD1_260202-002(입찰-1)"
        ): (
            "견적서/2차 학습/QUOTATION_WIA_인도법인 푸네 "
            "TJ등급선별기 개조_GD1_260202-002"
            "(입찰-1)_보안해제.xlsx"
        ),
    }
    fixture_paths = [
        Path(pair[path_type])
        for pair in pairs
        for path_type in ("original", "unlocked")
    ]

    groups = build_source_groups(reversed(fixture_paths))

    assert len(groups) == 12
    assert {
        group.logical_name: group.preferred.as_posix()
        for group in groups
    } == expected_preferred
    assert all(len(group.variants) == 2 for group in groups)
