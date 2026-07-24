from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine


def _enable_foreign_keys(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    if isinstance(dbapi_connection, SQLiteConnection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def configure_sqlite(engine: Engine) -> Engine:
    """Enable SQLite foreign keys before the engine opens its first connection."""

    if (
        engine.dialect.name == "sqlite"
        and not event.contains(engine, "connect", _enable_foreign_keys)
    ):
        event.listen(engine, "connect", _enable_foreign_keys)
    return engine
