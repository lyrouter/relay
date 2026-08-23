"""Engines, one per role (MT-3).

Keeping these apart in code is what keeps them apart in production. The runtime
must never reach for the owner engine to work around a permission error — that
would silently switch RLS off, and nothing would fail.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from relay.config import settings


@lru_cache(maxsize=1)
def app_engine() -> Engine:
    """Runtime. Non-owner, NOBYPASSRLS, subject to FORCE ROW LEVEL SECURITY."""
    return create_engine(settings.app_dsn, echo=settings.sql_echo, pool_pre_ping=True)


@lru_cache(maxsize=1)
def owner_engine() -> Engine:
    """Migrations only. Owns the tables, so policies would not bind it."""
    return create_engine(settings.owner_dsn, echo=settings.sql_echo, poolclass=None)


@lru_cache(maxsize=1)
def system_engine() -> Engine:
    """BYPASSRLS. ``SystemRepository`` only, and every call lands in the audit log."""
    return create_engine(settings.system_dsn, echo=settings.sql_echo, pool_pre_ping=True)


def dispose_all() -> None:
    for factory in (app_engine, owner_engine, system_engine):
        if factory.cache_info().currsize:
            factory().dispose()
        factory.cache_clear()
