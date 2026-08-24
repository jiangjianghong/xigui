"""引擎、会话工厂与 FastAPI 依赖。

引擎是进程级单例，懒创建。测试通过 ``app.dependency_overrides[get_db]``
换成 SQLite 会话，不碰这里的全局状态。
"""

from __future__ import annotations

import logging
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import build_db_url, get_db_config

log = logging.getLogger("aicheck.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """返回进程级引擎，首次调用时创建。

    ``pool_pre_ping`` 与 ``pool_recycle`` 是连外部 MySQL 的刚需：MySQL 默认
    8 小时空闲断连，没有 pre_ping 的话服务闲置一夜后第一个请求必定拿到
    死连接报 ``MySQL server has gone away``。
    """
    global _engine, _SessionLocal
    if _engine is None:
        cfg = get_db_config()
        url = build_db_url()
        kwargs: dict = {
            "pool_pre_ping": True,
            "pool_recycle": 3600,
            "echo": cfg.get("echo", False),
            "future": True,
        }
        # SQLite（测试）不支持连接池参数
        if not url.startswith("sqlite"):
            kwargs["pool_size"] = cfg.get("pool_size", 5)
            kwargs["max_overflow"] = cfg.get("max_overflow", 10)
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False
        )
        log.info("数据库引擎已创建 dialect=%s", _engine.dialect.name)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    """返回会话工厂；引擎尚未创建时先把它建出来。"""
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def dispose_engine() -> None:
    """释放引擎并清空缓存。测试与优雅关闭时调用。"""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：每请求一个会话，请求结束必定关闭。"""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()
