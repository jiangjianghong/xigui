"""全局测试夹具。

所有 DB 测试跑 SQLite in-memory，不依赖真实 MySQL。两个必须处理的方言差异：

1. SQLite 默认**不启用**外键约束，``ON DELETE SET NULL`` 会静默失效。
   必须挂 connect 事件执行 ``PRAGMA foreign_keys=ON``。
2. 内存库是**连接私有**的：换一条连接就是换一个空库，不存在"共享的内存库"。
   SQLAlchemy 给内存 SQLite 的默认池是 ``SingletonThreadPool``——每线程一条
   连接，单线程下察觉不到。但接口测试经 TestClient 调同步 endpoint 时，
   Starlette 会把它派进工作线程池，那条新连接看到的是另一个空库，报
   ``no such table``。故用 ``StaticPool`` 让整个引擎共用一条连接。
"""

from __future__ import annotations

from typing import Iterator

import pytest
import yaml
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture(scope="session")
def admin_hash():
    return PasswordHasher().hash("TestAdmin123")


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch, admin_hash):
    """Never read or mutate developer credentials or contact real LLM/MySQL."""
    from app.core import config
    from app.core.auth import admin_auth
    from app import main
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "settings": {"username": "admin", "password_hash": admin_hash},
        "llm": {"model": "test-model", "api_key": "test-only", "backend": "vllm"},
    }), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(path))
    monkeypatch.setattr(config, "_cfg_cache", {"data": None, "mtime": 0.0, "path": None})
    monkeypatch.setattr(main, "setup_logging", lambda: None)
    admin_auth.reset()
    yield path
    admin_auth.reset()


@pytest.fixture(autouse=True)
def _clean_mysql_env(monkeypatch):
    """清掉宿主环境的 ``MYSQL_*``，否则它们会泄进断言。

    ``get_db_config()`` 让 MYSQL_HOST / MYSQL_PASSWORD 等覆盖配置文件，而这几个
    正是 docker compose 和官方 mysql 镜像会设的变量名——开发机上存在的概率不低。
    真发生时 tests/unit/test_db_config.py 会挂 4 个（实测：仅 MYSQL_HOST 与
    MYSQL_PASSWORD 二者即可触发），且报错看着像配置读错了。
    """
    for name in (
        "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DATABASE",
        "MYSQL_USERNAME", "MYSQL_PASSWORD",
    ):

        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine) -> Iterator[Session]:
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = maker()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def anonymous_client(db):
    """TestClient 与 ``db`` 夹具共用同一会话。

    共用是刻意的：测试里用 ``db`` 直接塞数据，再用 ``client`` 调接口断言，
    两者必须看到同一份未提交状态。因此覆盖里不关闭会话——生命周期归 ``db``。
    """
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as c:
            yield c
    finally:
        # 放 finally 且用 pop：lifespan 现在会做建库/首次导入，yield 之前抛异常
        # 时旧写法的 clear() 会被跳过，把覆盖漏给后续用例。
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client(anonymous_client):
    """Existing business tests exercise the real admin session, never a bypass."""
    anonymous_client.headers["X-Requested-With"] = "XMLHttpRequest"
    response = anonymous_client.post("/auth/login", json={"username": "admin", "password": "TestAdmin123"})
    assert response.status_code == 200
    return anonymous_client
