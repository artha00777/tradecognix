"""Database engine and session helpers. SQLite by default, Postgres via DATABASE_URL."""
from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_engine = None
log = logging.getLogger("tradecognix.db")


def _normalise_url(url: str) -> str:
    # Supabase / Heroku style URLs -> SQLAlchemy psycopg3 driver
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def get_engine():
    global _engine
    if _engine is None:
        url = _normalise_url(get_settings().database_url)
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kwargs)
    return _engine


def set_engine(engine) -> None:
    """Used by tests to swap in an in-memory database."""
    global _engine
    _engine = engine


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    eng = get_engine()
    SQLModel.metadata.create_all(eng)
    add_missing_columns(eng)


def _default_sql(col) -> str | None:
    default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
    if default is None:
        return None
    if isinstance(default, bool):
        return "TRUE" if default else "FALSE"
    if isinstance(default, (int, float)):
        return str(default)
    return "'" + str(default).replace("'", "''") + "'"


def add_missing_columns(eng) -> list[str]:
    """Minimal forward-only migration: ADD COLUMN for model columns missing from existing tables.

    `create_all` never alters existing tables, so new nullable/defaulted fields (e.g. User.plan)
    would break old databases. Unique constraints/indexes on added columns are not created here;
    use Alembic for anything beyond additive changes.
    """
    added = []
    insp = inspect(eng)
    existing_tables = set(insp.get_table_names())
    with eng.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or col.primary_key:
                    continue
                ddl_type = col.type.compile(dialect=eng.dialect)
                default = _default_sql(col)
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl_type}'
                if default is not None:
                    stmt += f" DEFAULT {default}"
                    if not col.nullable:
                        stmt += " NOT NULL"
                conn.execute(text(stmt))
                added.append(f"{table.name}.{col.name}")
    if added:
        log.warning("added missing columns: %s", ", ".join(added))
    return added


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
