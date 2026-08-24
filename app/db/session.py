"""引擎、会话工厂与 FastAPI 依赖。

引擎是进程级单例，懒创建。测试通过 ``app.dependency_overrides[get_db]``
换成 SQLite 会话，不碰这里的全局状态。
"""

from __future__ import annotations

import logging
import threading
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import build_db_url, get_db_config

log = logging.getLogger("aicheck.db")

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_lock = threading.Lock()


def get_engine() -> Engine:
    """返回进程级引擎，首次调用时创建。

    ``pool_pre_ping`` 与 ``pool_recycle`` 是连外部 MySQL 的刚需：MySQL 默认
    8 小时空闲断连，没有 pre_ping 的话服务闲置一夜后第一个请求必定拿到
    死连接报 ``MySQL server has gone away``。

    双重检查加锁，与 ``app/core/config.py`` 的缓存同一套模式：``get_db`` 是同步
    依赖，FastAPI 会把它派到线程池，裸的 check-then-act 会让首批并发请求各建
    一个引擎并交错赋值，最终 ``_SessionLocal`` 与 ``_engine`` 绑到不同引擎上，
    多出来的连接池谁也 dispose 不掉。
    """
    global _engine, _SessionLocal
    engine = _engine
    if engine is not None:
        return engine
    with _lock:
        if _engine is not None:
            return _engine
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
        new_engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(
            bind=new_engine, autoflush=False, expire_on_commit=False
        )
        # 这两行的先后顺序是有意的：快路径只看 _engine，必须等工厂建好之后
        # 再发布它。反过来写就会留出一个窗口——别的线程从快路径拿到引擎、
        # 直奔 get_sessionmaker，却读到还是 None 的 _SessionLocal。
        _engine = new_engine
        log.info("数据库引擎已创建 dialect=%s", new_engine.dialect.name)
        return new_engine


def get_sessionmaker() -> sessionmaker[Session]:
    """返回会话工厂；引擎尚未创建时先把它建出来。"""
    get_engine()
    factory = _SessionLocal
    assert factory is not None
    return factory


def dispose_engine() -> None:
    """释放引擎并清空缓存。测试与优雅关闭时调用。

    同样持锁，否则与并发的 ``get_engine()`` 交错时会留下一个已 dispose 的引擎
    配一个仍指向它的会话工厂。
    """
    global _engine, _SessionLocal
    with _lock:
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
