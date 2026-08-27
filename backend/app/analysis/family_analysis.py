"""Demo-oriented quote analysis against broad procurement item families."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.analysis.service import assess_variance
from app.analysis.target_price import AnalysisRunResult
from app.procurement.equipment import EquipmentDefinition, _equipment_definitions
from app.procurement.families import FAMILY_RULE_VERSION, classify_item_family, item_family_projection
from app.matching.normalization import normalize_search_text


KRW = Decimal("1")
PERCENT = Decimal("0.000001")
COMPARABLE_LOW = Decimal("0.70")
COMPARABLE_HIGH = Decimal("1.30")


def family_analysis_payload(
    session: Session,
    result: AnalysisRunResult,
    *,
    review_percent: Decimal,
    high_percent: Decimal,
) -> dict[str, object]:
    families = item_family_projection(session)
    by_code = {family["code"]: family for family in families}
    family_by_item = {
        member["standard_item_id"]: family
        for family in families
        for member in family["members"]
    }

    lines: list[dict[str, object]] = []
    targets: list[dict[str, object]] = []
    target_by_raw: dict[int, dict[str, object]] = {}
    matched_count = 0
    available_count = 0

    for source_line in result.analysis.lines:
        line = asdict(source_line)
        target = _unavailable_target(source_line.raw_item_id, "품목류 가격 근거를 적용할 수 없습니다.")
        family = (
            family_by_item.get(source_line.standard_item_id)
            if source_line.standard_item_id is not None
            else None
        )
        if family is None:
            match = classify_item_family(source_line.item_name, source_line.spec, None)
            family = by_code.get(match.code)

        can_analyze = source_line.match_status not in {"EXCLUDED", "REVIEW_REQUIRED"}
        quote_unit_price = source_line.quote_unit_price
        comparable_prices = _comparable_member_prices(
            family,
            unit=source_line.unit,
            quote_unit_price=quote_unit_price,
        )
        if can_analyze and family is not None and comparable_prices:
            sorted_prices = sorted(comparable_prices)
            median_price = _median(sorted_prices)
            minimum_price = min(sorted_prices)
            maximum_price = max(sorted_prices)
            average_price = sum(sorted_prices, Decimal("0")) / Decimal(len(sorted_prices))
            lower_prices = [price for price in sorted_prices if price <= quote_unit_price]
            target_unit_price = min(lower_prices) if lower_prices else quote_unit_price
            variance_amount = quote_unit_price - median_price
            variance_percent = (
                variance_amount / median_price * Decimal("100")
                if median_price > 0
                else Decimal("0")
            )
            target_amount = _scaled_target_amount(
                quote_amount=source_line.quote_amount,
                quote_unit_price=quote_unit_price,
                quantity=source_line.quantity,
                target_unit_price=target_unit_price,
            )
            assessment = assess_variance(
                variance_percent,
                review_percent=review_percent,
                high_percent=high_percent,
            )
            line.update(
                {
                    "match_status": "MATCHED",
                    "assessment": assessment,
                    "reference_price": median_price,
                    "minimum_price": minimum_price,
                    "average_price": average_price,
                    "maximum_price": maximum_price,
                    "variance_amount": variance_amount,
                    "variance_percent": variance_percent.quantize(PERCENT, rounding=ROUND_HALF_UP),
                    "canonical_name": family["name"],
                    "canonical_spec": f"상세 품목 {family['item_count']}개",
                    "canonical_unit": source_line.unit,
                    "standard_observation_count": len(sorted_prices),
                    "evidence_quality": "MULTI_OBSERVATION" if family["supplier_count"] > 1 else "SINGLE_OBSERVATION",
                    "market_price_lookup_required": False,
                    "market_price_lookup_status": "NOT_REQUIRED",
                    "candidates": [],
                    "family_code": family["code"],
                    "family_name": family["name"],
                    "family_item_count": family["item_count"],
                    "family_supplier_count": family["supplier_count"],
                }
            )
            target = {
                "raw_item_id": source_line.raw_item_id,
                "status": "AVAILABLE",
                "target_unit_price": target_unit_price,
                "target_amount": target_amount,
                "variance_amount": (
                    None if target_amount is None or source_line.quote_amount is None
                    else source_line.quote_amount - target_amount
                ),
                "variance_percent": (
                    None
                    if source_line.quote_amount in {None, Decimal("0")} or target_amount is None
                    else ((source_line.quote_amount - target_amount) / source_line.quote_amount * Decimal("100")).quantize(PERCENT, rounding=ROUND_HALF_UP)
                ),
                "unit_variance_amount": quote_unit_price - target_unit_price,
                "used_observation_count": len(sorted_prices),
                "excluded_observation_count": 0,
                "reason": (
                    f"{family['name']} 동일 단위·유사 가격대 상세 품목 "
                    f"{len(sorted_prices)}개 중 최저 중앙값"
                    if lower_prices
                    else f"{family['name']} 비교군보다 현재 단가가 낮아 현 견적 유지"
                ),
                "evidence": [],
            }
            matched_count += 1
            available_count += 1
        else:
            line.update(
                {
                    "family_code": None if family is None else family["code"],
                    "family_name": None if family is None else family["name"],
                    "family_item_count": 0 if family is None else family["item_count"],
                    "family_supplier_count": 0 if family is None else family["supplier_count"],
                }
            )
            if source_line.match_status == "EXCLUDED":
                target = _unavailable_target(source_line.raw_item_id, "합계·소계 등 분석 대상이 아닌 행입니다.", status="NOT_APPLICABLE")
            elif source_line.match_status == "REVIEW_REQUIRED":
                target = _unavailable_target(source_line.raw_item_id, "정제 검토 후 품목류 목표가를 적용할 수 있습니다.")
        lines.append(line)
        targets.append(target)
        target_by_raw[source_line.raw_item_id] = target

    equipment_groups = _family_equipment_groups(result, target_by_raw)
    quote_total = sum((line.quote_amount or Decimal("0") for line in result.analysis.lines), Decimal("0"))
    negotiation_total = sum((Decimal(str(group["negotiation_amount"])) for group in equipment_groups), Decimal("0"))
    return {
        "rule_version": FAMILY_RULE_VERSION,
        "matched_count": matched_count,
        "pending_count": len(lines) - matched_count,
        "lines": lines,
        "target_lines": targets,
        "target_available_count": available_count,
        "target_unavailable_count": len(targets) - available_count,
        "quote_total_amount": quote_total,
        "target_total_amount": max(quote_total - negotiation_total, Decimal("0")),
        "equipment_groups": equipment_groups,
    }


def _comparable_member_prices(
    family: dict[str, object] | None,
    *,
    unit: str | None,
    quote_unit_price: Decimal | None,
) -> list[Decimal]:
    if family is None or not unit or quote_unit_price is None or quote_unit_price <= 0:
        return []
    normalized_unit = normalize_search_text(unit)
    low = quote_unit_price * COMPARABLE_LOW
    high = quote_unit_price * COMPARABLE_HIGH
    prices: list[Decimal] = []
    for member in family.get("members", []):
        if normalize_search_text(member.get("unit")) != normalized_unit:
            continue
        price = member.get("price")
        if not price or price.get("median") is None:
            continue
        median_price = Decimal(str(price["median"]))
        if low <= median_price <= high:
            prices.append(median_price)
    return prices


def _median(values: list[Decimal]) -> Decimal:
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")


def _scaled_target_amount(
    *,
    quote_amount: Decimal | None,
    quote_unit_price: Decimal,
    quantity: Decimal | None,
    target_unit_price: Decimal,
) -> Decimal | None:
    if quote_amount is not None and quote_unit_price > 0:
        return (quote_amount * target_unit_price / quote_unit_price).quantize(KRW, rounding=ROUND_HALF_UP)
    if quantity is not None:
        return (quantity * target_unit_price).quantize(KRW, rounding=ROUND_HALF_UP)
    return None


def _unavailable_target(raw_item_id: int, reason: str, *, status: str = "MARKET_REFERENCE_REQUIRED") -> dict[str, object]:
    return {
        "raw_item_id": raw_item_id,
        "status": status,
        "target_unit_price": None,
        "target_amount": None,
        "variance_amount": None,
        "variance_percent": None,
        "unit_variance_amount": None,
        "used_observation_count": 0,
        "excluded_observation_count": 0,
        "reason": reason,
        "evidence": [],
    }


def _family_equipment_groups(
    result: AnalysisRunResult,
    target_by_raw: dict[int, dict[str, object]],
) -> list[dict[str, object]]:
    definitions = _equipment_definitions(result)
    by_sheet = {definition.sheet: definition for definition in definitions}
    has_cover = bool(definitions)
    grouped: dict[str, tuple[EquipmentDefinition, list[object]]] = {}
    for line in result.analysis.lines:
        definition = by_sheet.get(line.source.sheet or "")
        if has_cover and definition is None:
            continue
        if definition is None:
            sheet = (line.source.sheet or "").strip()
            key = f"sheet:{sheet or 'all-items'}"
            definition = EquipmentDefinition(key, sheet or "전체 설비", sheet, None, "LINE_ITEM_FALLBACK")
        grouped.setdefault(definition.key, (definition, []))[1].append(line)

    payloads: list[dict[str, object]] = []
    for index, (definition, source_lines) in enumerate(grouped.values(), start=1):
        quote_amount = sum((line.quote_amount or Decimal("0") for line in source_lines), Decimal("0")).quantize(KRW, rounding=ROUND_HALF_UP)
        if quote_amount <= 0:
            continue
        line_payloads = []
        target_sum = Decimal("0")
        negotiation_sum = Decimal("0")
        available = 0
        for line in source_lines:
            quote = max(line.quote_amount or Decimal("0"), Decimal("0"))
            target = target_by_raw[line.raw_item_id]
            target_amount = target["target_amount"]
            effective_target = quote if target_amount is None else min(quote, Decimal(str(target_amount)))
            negotiation = max(quote - effective_target, Decimal("0")).quantize(KRW, rounding=ROUND_HALF_UP)
            target_sum += effective_target
            negotiation_sum += negotiation
            available += target["status"] == "AVAILABLE"
            line_payloads.append({
                "raw_item_id": line.raw_item_id,
                "quote_amount": quote,
                "target_amount": target_amount,
                "negotiation_amount": negotiation,
            })
        payloads.append({
            "id": -index,
            "key": definition.key,
            "name": definition.name,
            "source_kind": definition.source_kind,
            "quote_amount": quote_amount,
            "target_amount": target_sum.quantize(KRW, rounding=ROUND_HALF_UP),
            "negotiation_amount": negotiation_sum.quantize(KRW, rounding=ROUND_HALF_UP),
            "unallocated_amount": Decimal("0"),
            "line_count": len(source_lines),
            "target_available_count": available,
            "lines": line_payloads,
        })
    return payloads
