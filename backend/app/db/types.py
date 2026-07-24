from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import BigInteger, DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class ExactDecimal(TypeDecorator[Decimal]):
    """Persist six-place decimals as signed scaled 64-bit integers."""

    impl = BigInteger
    cache_ok = True

    _quantum = Decimal("0.000001")
    _scale = Decimal("1000000")
    _max_abs = Decimal("9223372036854.775807")

    def process_bind_param(
        self,
        value: Decimal | str | int | None,
        dialect: Dialect,
    ) -> int | None:
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
        if decimal_value.as_tuple().exponent < -6:
            raise ValueError("exact decimals support at most 6 fractional digits")
        try:
            quantized = decimal_value.quantize(self._quantum)
        except InvalidOperation as error:
            raise ValueError(f"invalid decimal value: {value!r}") from error
        if abs(quantized) > self._max_abs:
            raise OverflowError("exact decimal exceeds signed SQLite bound")
        return int(quantized * self._scale)

    def process_result_value(
        self,
        value: Any,
        dialect: Dialect,
    ) -> Decimal | None:
        return None if value is None else Decimal(value).scaleb(-6)


class NaiveUTCDateTime(TypeDecorator[datetime]):
    """Store aware values as naive UTC and treat naive inputs as UTC."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
