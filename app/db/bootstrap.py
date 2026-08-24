"""启动时建库建表。

范围明确：只建库、只建表。**不做字段自动迁移**（spec 决策 6）——表结构变更
由人工执行 SQL，参考 ``docs/schema.sql``。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.core.config import build_db_url, get_db_config
from app.db.models import Base

log = logging.getLogger("aicheck.db")

_DB_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_db_name(name: str) -> None:
    """库名会直接拼进 DDL，不校验就是注入口子。"""
    if not _DB_NAME_RE.match(name or ""):
        raise ValueError(
            f"库名非法: {name!r}，须匹配 ^[A-Za-z_][A-Za-z0-9_]*$"
        )


def _create_database(server_url: str, db_name: str) -> None:
    engine = create_engine(server_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
        log.info("已确保数据库存在: %s", db_name)
    finally:
        engine.dispose()


def ensure_database() -> None:
    """库不存在则建。SQLite 无『库』概念，按方言跳过。"""
    url = build_db_url()
    if not url.startswith("mysql"):
        log.debug("非 MySQL 方言，跳过建库")
        return

    db_name = get_db_config()["database"]
    _check_db_name(db_name)
    try:
        _create_database(build_db_url(with_database=False), db_name)
    except OperationalError as e:
        log.error(
            "建库失败: %s\n"
            "应用账号需要 CREATE 权限。请让 DBA 执行:\n"
            "  CREATE DATABASE `%s` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            "  GRANT ALL ON `%s`.* TO '<user>'@'%%';",
            e, db_name, db_name,
        )
        raise


def ensure_tables(engine: Engine) -> None:
    """表不存在则建。已存在的表不动。

    外层的 try/except 兜多实例首次同时启动的窄竞态：``create_all`` 的
    checkfirst 与实际 CREATE 之间有间隙，两个进程可能同时判定「表不存在」。
    """
    try:
        Base.metadata.create_all(engine)
    except (OperationalError, ProgrammingError) as e:
        msg = str(e).lower()
        if "already exists" in msg or "duplicate" in msg:
            log.warning("建表时撞上并发创建，已忽略: %s", e)
            return
        log.error(
            "建表失败: %s\n应用账号需要 CREATE 权限，或参考 docs/schema.sql 手工执行 DDL。",
            e,
        )
        raise


def bootstrap_database(engine: Engine | None = None) -> Engine:
    """建库 + 建表，返回可用引擎。启动时调用一次。"""
    ensure_database()
    if engine is None:
        from app.db.session import get_engine

        engine = get_engine()
    ensure_tables(engine)
    return engine
