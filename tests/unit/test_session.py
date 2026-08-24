"""引擎参数与会话依赖。"""

from __future__ import annotations

import pytest

from app.db import session as sess_module


@pytest.fixture(autouse=True)
def reset_engine():
    """每个用例前后清掉进程级引擎缓存，避免互相污染。"""
    sess_module.dispose_engine()
    yield
    sess_module.dispose_engine()


@pytest.fixture
def sqlite_cfg(monkeypatch):
    """把引擎指向内存 SQLite。

    注意必须 patch 到 ``sess_module`` 上而不是 ``app.core.config``：
    ``session.py`` 用的是 ``from app.core.config import ...``，那是另一个名字
    绑定，patch 源模块对它无效。``tests/integration/test_admin_rules.py``
    里也踩过同样的坑并留了注释。
    """
    monkeypatch.setattr(sess_module, "build_db_url", lambda **kw: "sqlite://")
    monkeypatch.setattr(
        sess_module, "get_db_config",
        lambda: {"pool_size": 5, "max_overflow": 10, "echo": False},
    )


def test_get_engine_is_singleton(sqlite_cfg):
    assert sess_module.get_engine() is sess_module.get_engine()


def test_dispose_engine_resets(sqlite_cfg):
    first = sess_module.get_engine()
    sess_module.dispose_engine()
    assert sess_module.get_engine() is not first


def test_sqlite_url_skips_pool_args(sqlite_cfg):
    """SQLite 的默认 pool 不接受 pool_size/max_overflow，传了会 TypeError。

    这条同时证明 sqlite_cfg 的 patch 真的生效了——若 patch 没打中，
    get_engine 会去读真实配置拼出 mysql:// 连接串。
    """
    assert sess_module.get_engine().dialect.name == "sqlite"


def test_get_db_yields_and_closes(sqlite_cfg):
    from app.db.models import Base

    Base.metadata.create_all(sess_module.get_engine())
    gen = sess_module.get_db()
    db = next(gen)
    assert db.is_active
    with pytest.raises(StopIteration):
        next(gen)
