"""
database.py — Async SQLAlchemy engine + session factory.

The DATABASE_URL env-var is injected by Docker Compose.
Falls back to a local SQLite file for running without Docker (dev convenience).
"""

import os
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Docker Compose injects:  postgresql+asyncpg://monitor:monitor@db:5432/monitordb
# Local fallback uses SQLite for zero-config development
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./monitor_dev.db",
)

# echo=False in production; set env var SQLALCHEMY_ECHO=true for query logging
_echo = os.getenv("SQLALCHEMY_ECHO", "false").lower() == "true"

# SQLite uses StaticPool and does not support pool_size / max_overflow.
# Postgres (asyncpg) benefits from a real connection pool.
_is_sqlite = DATABASE_URL.startswith("sqlite")
_pool_kwargs: dict = {} if _is_sqlite else {"pool_size": 5, "max_overflow": 10}

engine = create_async_engine(
    DATABASE_URL,
    echo=_echo,
    **_pool_kwargs,
)

SessionLocal: sessionmaker = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)
