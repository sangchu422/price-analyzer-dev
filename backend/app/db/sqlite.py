from sqlite3 import Connection as SQLiteConnection
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine


def _configure_dbapi_connection(
    dbapi_connection: Any,
    connection_record: Any,
) -> None:
    if isinstance(dbapi_connection, SQLiteConnection):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _begin_explicitly(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")


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
