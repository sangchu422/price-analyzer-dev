from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine


_ALLOWED_BEGIN_MODES = frozenset({"DEFERRED", "IMMEDIATE"})
_BUSY_TIMEOUT_MILLISECONDS = 5000


def _configure_dbapi_connection(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    if isinstance(dbapi_connection, SQLiteConnection):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(
            f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MILLISECONDS}"
        )
        cursor.close()


def _begin_explicitly(connection: Connection) -> None:
    mode = connection.get_execution_options().get(
        "sqlite_begin_mode",
        "DEFERRED",
    )
    if mode not in _ALLOWED_BEGIN_MODES:
        raise ValueError(
            "sqlite_begin_mode must be DEFERRED or IMMEDIATE"
        )
    connection.exec_driver_sql(f"BEGIN {mode}")


def configure_sqlite(engine: Engine) -> Engine:
    """Configure modern SQLite transaction control and foreign keys.

    Call this before the engine opens its first DBAPI connection. SQLAlchemy
    owns BEGIN/COMMIT/ROLLBACK while sqlite3 legacy implicit transaction
    control is disabled, ensuring a SAVEPOINT cannot commit by itself.
    """

    if engine.dialect.name != "sqlite":
        return engine
    if not event.contains(
        engine,
        "connect",
        _configure_dbapi_connection,
    ):
        event.listen(engine, "connect", _configure_dbapi_connection)
    if not event.contains(engine, "begin", _begin_explicitly):
        event.listen(engine, "begin", _begin_explicitly)
    return engine
