from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator[Decimal]):
    """Persist finite decimals as text without a binary-float conversion."""

    impl = Text
    cache_ok = True

    def process_bind_param(
        self,
        value: Decimal | str | int | None,
        dialect: Dialect,
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(
            value,
            (Decimal, str, int),
        ):
            raise TypeError("exact decimals require Decimal, string, or int")
        try:
            decimal_value = Decimal(value)
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"invalid decimal value: {value!r}") from error
        if not decimal_value.is_finite():
            raise ValueError("exact decimals must be finite")
        return format(decimal_value, "f")

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,
    ) -> Decimal | None:
        return None if value is None else Decimal(value)
