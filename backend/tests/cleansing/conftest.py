from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.sqlite import configure_sqlite
from app.documents.models import SourceDocument, SourceVariant
from app.quotes.models import RawQuoteItem


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    database = tmp_path / "cleansing.sqlite3"
    engine = configure_sqlite(
        create_engine(f"sqlite:///{database.as_posix()}")
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db_session:
        yield db_session


@pytest.fixture
def make_raw(session: Session):
    document = SourceDocument(logical_name="cleansing-fixture")
    variant = SourceVariant(
        document=document,
        path="quotes/cleansing-fixture.xlsx",
        sha256="c" * 64,
        extension=".xlsx",
        security_state="UNLOCKED",
        selected_for_parsing_at_ingest=True,
    )
    session.add(document)

    def factory(
        *,
        item_name: str | None = "SERVO MOTOR",
        spec: str | None = "200 W",
        unit: str | None = "EA",
        quantity: str | None = "2",
        unit_price: str | None = "1000",
        amount: str | None = "2000",
        maker: str | None = "ACME",
        source_row: int = 1,
    ) -> RawQuoteItem:
        raw = RawQuoteItem(
            source_variant=variant,
            source_sheet="견적",
            source_row=source_row,
            source_cells=f"A{source_row}:G{source_row}",
            item_name_raw=item_name,
            spec_raw=spec,
            unit_raw=unit,
            quantity_raw=quantity,
            unit_price_raw=unit_price,
            amount_raw=amount,
            maker_raw=maker,
            parser_name="fixture",
            parser_version="1",
        )
        session.add(raw)
        session.flush()
        return raw

    return factory
