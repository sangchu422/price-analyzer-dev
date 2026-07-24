from sqlalchemy.orm import DeclarativeBase

from app.db import immutability as _immutability  # noqa: F401


class Base(DeclarativeBase):
    pass
