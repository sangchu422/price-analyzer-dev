from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.db import models as _models
from app.db.sqlite import configure_sqlite


settings.database_path.parent.mkdir(parents=True, exist_ok=True)

engine = configure_sqlite(
    create_engine(
        f"sqlite:///{settings.database_path}",
        connect_args={"check_same_thread": False},
    )
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
