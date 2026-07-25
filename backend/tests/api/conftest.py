from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.db.session import get_session
from app.db.sqlite import configure_sqlite
from app.main import app


@pytest.fixture
def api_session(tmp_path: Path) -> Iterator[Session]:
    database = tmp_path / "api.sqlite3"
    engine = configure_sqlite(
        create_engine(
            f"sqlite:///{database.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(
    api_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    quote_root = tmp_path / "quotes"
    quote_root.mkdir()
    monkeypatch.setattr(settings, "quote_folder", quote_root)

    def override_session() -> Iterator[Session]:
        yield api_session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def quote_root(tmp_path: Path) -> Path:
    return tmp_path / "quotes"
