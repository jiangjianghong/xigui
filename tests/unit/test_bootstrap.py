"""建库建表。真实 MySQL 分支只测调用契约与 SQL 文本，不连库。"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

from app.db import bootstrap


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------

def test_ensure_tables_creates_all():
    engine = create_engine("sqlite://")
    bootstrap.ensure_tables(engine)
    assert set(inspect(engine).get_table_names()) == {
        "projects", "rules", "compare_history"
    }


def test_ensure_tables_is_idempotent():
    """跑两次不报错，且表还在——多实例同时启动时会撞上。"""
    engine = create_engine("sqlite://")
    bootstrap.ensure_tables(engine)
    bootstrap.ensure_tables(engine)
    assert set(inspect(engine).get_table_names()) == {
        "projects", "rules", "compare_history"
    }


def test_ensure_tables_adds_missing_custom_columns():
    """旧表只补缺失字段，重复启动仍然安全。"""
    engine = create_engine("sqlite://")
    bootstrap.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE rules DROP COLUMN output_schema"))
        conn.execute(text("ALTER TABLE rules DROP COLUMN rule_type"))
        conn.execute(text("ALTER TABLE compare_history DROP COLUMN rule_type"))

    bootstrap.ensure_tables(engine)
    bootstrap.ensure_tables(engine)

    assert {"rule_type", "output_schema"} <= {
        c["name"] for c in inspect(engine).get_columns("rules")
    }
    assert "rule_type" in {
        c["name"] for c in inspect(engine).get_columns("compare_history")
    }


def test_migrate_legacy_history_result_to_json_booleans():
    """升级历史结果列时保留旧的 true/false 值。"""
    engine = create_engine("sqlite://")
    bootstrap.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE compare_history"))
        conn.execute(text(
            "CREATE TABLE compare_history "
            "(id INTEGER PRIMARY KEY, result BOOLEAN NOT NULL)"
        ))
        conn.execute(text(
            "INSERT INTO compare_history (id, result) VALUES (1, 1), (2, 0)"
        ))

    bootstrap.ensure_tables(engine)

    with engine.connect() as conn:
        values = conn.execute(
            text("SELECT result FROM compare_history ORDER BY id")
        ).scalars().all()
    assert values == ["true", "false"]
    assert "result_json" not in {
        c["name"] for c in inspect(engine).get_columns("compare_history")
    }


def test_ensure_tables_swallows_concurrent_create(monkeypatch):
    """并发撞车报的『已存在』要吞掉，否则第二个实例起不来。"""
    engine = create_engine("sqlite://")

    def _boom(*a, **kw):
        raise OperationalError(
            "CREATE TABLE", {}, Exception("table projects already exists")
        )

    monkeypatch.setattr(bootstrap.Base.metadata, "create_all", _boom)
    bootstrap.ensure_tables(engine)  # 不该抛


def test_ensure_tables_reraises_other_errors(monkeypatch):
    """只吞『已存在』，别的错误必须照抛——否则真故障被静默。"""
    engine = create_engine("sqlite://")

    def _boom(*a, **kw):
        raise OperationalError("CREATE TABLE", {}, Exception("disk full"))

    monkeypatch.setattr(bootstrap.Base.metadata, "create_all", _boom)
    with pytest.raises(OperationalError):
        bootstrap.ensure_tables(engine)


# ---------------------------------------------------------------------------
# 建库
# ---------------------------------------------------------------------------

def _mysql_url_stub(*, with_database: bool = True) -> str:
    """镜像 build_db_url 的真实签名，且**按取值返回不同结果**。

    不能用 ``lambda **kw: "..."`` 一把梭：那样两种取值返回同一个串，
    实现里误用 ``build_db_url()`` 建库也测不出来。
    """
    if with_database:
        return "mysql+pymysql://u:p@h:3306/mydb?charset=utf8mb4"
    return "mysql+pymysql://u:p@h:3306/?charset=utf8mb4"


def test_ensure_database_skips_non_mysql(monkeypatch):
    """SQLite 没有『库』的概念，必须跳过建库。"""
    called = []
    monkeypatch.setattr(
        bootstrap, "build_db_url", lambda *, with_database=True: "sqlite://"
    )
    monkeypatch.setattr(
        bootstrap, "_create_database", lambda *a, **kw: called.append(1)
    )
    bootstrap.ensure_database()
    assert called == []


def test_ensure_database_connects_without_db_name(monkeypatch):
    """建库必须连到 server 本身，不能带库名。

    误用 build_db_url()（带库名）会去连一个还不存在的库来建它，必然失败。
    这条断言就是钉这个的。
    """
    seen: dict = {}
    monkeypatch.setattr(bootstrap, "build_db_url", _mysql_url_stub)
    monkeypatch.setattr(bootstrap, "get_db_config", lambda: {"database": "mydb"})
    monkeypatch.setattr(
        bootstrap, "_create_database",
        lambda server_url, db_name: seen.update(url=server_url, name=db_name),
    )

    bootstrap.ensure_database()

    assert seen["name"] == "mydb"
    assert seen["url"].endswith("@h:3306/?charset=utf8mb4"), (
        f"建库连接串不该带库名，实际: {seen['url']}"
    )


def test_ensure_database_checks_name_before_creating(monkeypatch):
    """库名先过校验再拼进 DDL，非法名不能走到 _create_database。"""
    called = []
    monkeypatch.setattr(bootstrap, "build_db_url", _mysql_url_stub)
    monkeypatch.setattr(bootstrap, "get_db_config", lambda: {"database": "bad;DROP"})
    monkeypatch.setattr(
        bootstrap, "_create_database", lambda *a, **kw: called.append(1)
    )

    with pytest.raises(ValueError, match="库名非法"):
        bootstrap.ensure_database()
    assert called == []


def test_ensure_database_logs_actionable_sql_on_permission_error(
    monkeypatch, caplog
):
    """权限不足时要打出该执行的 SQL 和该授的权，而不是甩一个 Access denied。"""
    monkeypatch.setattr(bootstrap, "build_db_url", _mysql_url_stub)
    monkeypatch.setattr(bootstrap, "get_db_config", lambda: {"database": "mydb"})

    def _denied(*a, **kw):
        raise OperationalError("CREATE DATABASE", {}, Exception("Access denied"))

    monkeypatch.setattr(bootstrap, "_create_database", _denied)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(OperationalError):
            bootstrap.ensure_database()

    assert "CREATE DATABASE" in caplog.text
    assert "GRANT" in caplog.text
    assert "mydb" in caplog.text


# ---------------------------------------------------------------------------
# 库名校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["my db", "db;DROP", "db`x", "库-名", "", "1db"])
def test_reject_illegal_database_name(bad):
    """库名直接拼进 DDL，必须先校验，否则是注入口子。"""
    with pytest.raises(ValueError, match="库名非法"):
        bootstrap._check_db_name(bad)


@pytest.mark.parametrize("ok", ["ai_cross_check", "db1", "_x"])
def test_accept_legal_database_name(ok):
    bootstrap._check_db_name(ok)


# ---------------------------------------------------------------------------
# 编排入口
# ---------------------------------------------------------------------------

def test_bootstrap_database_uses_given_engine(monkeypatch):
    """传了 engine 就用它，不去碰进程级全局引擎。"""
    calls = []
    monkeypatch.setattr(bootstrap, "ensure_database", lambda: calls.append("db"))
    engine = create_engine("sqlite://")

    result = bootstrap.bootstrap_database(engine)

    assert result is engine
    assert calls == ["db"]
    assert set(inspect(engine).get_table_names()) == {
        "projects", "rules", "compare_history"
    }
