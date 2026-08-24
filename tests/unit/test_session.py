"""引擎参数与会话依赖。"""

from __future__ import annotations

import threading
import time

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


def test_get_db_yields_session(sqlite_cfg):
    from app.db.models import Base

    Base.metadata.create_all(sess_module.get_engine())
    gen = sess_module.get_db()
    db = next(gen)
    assert db.is_active
    with pytest.raises(StopIteration):
        next(gen)


def test_get_db_closes_session(sqlite_cfg, monkeypatch):
    """finally 里的 close() 必须真被调用。

    原先只断言 db.is_active，而它对任何新建 Session 恒为 True，
    等于什么都没验证——依赖泄漏连接是压垮连接池的经典方式。
    """
    gen = sess_module.get_db()
    db = next(gen)
    closed: list[int] = []
    monkeypatch.setattr(db, "close", lambda: closed.append(1))
    with pytest.raises(StopIteration):
        next(gen)
    assert closed == [1]


def test_get_engine_is_thread_safe(sqlite_cfg, monkeypatch):
    """并发首次调用只能建出一个引擎。

    用 sleep 把 create_engine 的耗时撑开，让 check-then-act 的窗口必然命中；
    不加锁时四个线程会各拿到一个引擎，多出来的连接池 dispose_engine 永远
    释放不掉。
    """
    real_create = sess_module.create_engine

    def slow_create(*args, **kwargs):
        time.sleep(0.05)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(sess_module, "create_engine", slow_create)

    engines: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        eng = sess_module.get_engine()
        with lock:
            engines.append(eng)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(engines) == 4
    assert len({id(e) for e in engines}) == 1
    # 工厂必须绑在最终发布的那个引擎上
    assert sess_module.get_sessionmaker().kw["bind"] is sess_module.get_engine()


def test_engine_published_after_sessionmaker(sqlite_cfg, monkeypatch):
    """_engine 必须等工厂建好之后再发布。

    顺序反了就会留出一个窗口：别的线程从快路径拿到引擎、直奔
    get_sessionmaker，却读到还是 None 的 _SessionLocal。这里不靠并发定时，
    而是在「建工厂」的那一刻回头检查 _engine 尚未发布——等价，且稳定。
    """
    real_sessionmaker = sess_module.sessionmaker
    engine_at_build: list[object] = []

    def spy(*args, **kwargs):
        engine_at_build.append(sess_module._engine)
        return real_sessionmaker(*args, **kwargs)

    monkeypatch.setattr(sess_module, "sessionmaker", spy)
    sess_module.get_engine()
    assert engine_at_build == [None]
