"""数据库 engine/session 工厂（来自 settings.DATABASE_URL）。"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobfit.config.settings import Settings, get_settings


def build_engine(settings: Settings | None = None) -> Engine:
    s = settings or get_settings()
    return create_engine(s.database_url, pool_pre_ping=True)


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def make_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    return build_session_factory(build_engine(settings))


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """短事务帮助：yield 一个 session，退出即关闭。

    调用方负责 commit/rollback；本函数只保证连接归还。
    禁止在单个事务内执行 LLM/parser/external HTTP（architecture.md §4.3-13）。
    """
    session = factory()
    try:
        yield session
    finally:
        session.close()
