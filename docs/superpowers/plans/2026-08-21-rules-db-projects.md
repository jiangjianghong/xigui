# 规则落库 MySQL + 项目管理 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把规则真源从 `config/rules.yaml` 迁到 MySQL，新增项目实体与规则归属，yaml 降级为导入导出载体。

**Architecture:** 新增 `app/db/` 承载 SQLAlchemy 2.0 同步 ORM 与三个单表仓储；`app/rules/` 领域层零改动，由 `repo_rules.to_rule_cfg()` 把 ORM 行还原成它熟悉的 plain dict；yaml 转换收敛到 `core/rules_io.py` 纯函数，事务编排在 `core/rules_sync.py`。

**Tech Stack:** SQLAlchemy 2.0（同步）、PyMySQL、FastAPI、ruamel.yaml、pytest + SQLite in-memory。

**Spec:** `docs/superpowers/specs/2026-08-21-rules-db-projects-design.md`

**本地命令：** 测试 `uv run pytest`，lint `uv run ruff check .`，类型 `uv run mypy app`

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `app/db/models.py` | 三张表的 ORM 定义，`Base` |
| `app/db/session.py` | 引擎、`SessionLocal`、`get_db` FastAPI 依赖 |
| `app/db/bootstrap.py` | 建库 + 建表 |
| `app/db/repo_projects.py` | 项目单表 CRUD |
| `app/db/repo_rules.py` | 规则单表 CRUD + `to_rule_cfg` / `to_api_dict` |
| `app/db/repo_history.py` | 历史单表写入与查询 |
| `app/schemas/rules.py` | `RuleIn` / `ProjectIn` 等 Pydantic 模型（从 `admin_rules.py` 抽出，避免 API 层与 core 层循环依赖） |
| `app/core/rules_io.py` | 改造：`dump_bundle` / `parse_bundle` 纯函数 |
| `app/core/rules_sync.py` | 导入导出编排、事务、warning 收集 |
| `app/api/admin_projects.py` | 项目 CRUD + 批量移入移出 |
| `app/api/admin_import_export.py` | 导入 / 导出端点 |
| `ui/projects.js` | 项目列表、项目 CRUD、批量操作、导入导出 |
| `tests/conftest.py` | SQLite in-memory 夹具、依赖覆盖、外键 PRAGMA |

---

## Task 1: 依赖与数据库配置

**Files:**
- Modify: `pyproject.toml:6-15`
- Modify: `app/core/config.py`（尾部追加）
- Test: `tests/unit/test_db_config.py`

- [ ] **Step 1: 加依赖**

`pyproject.toml` 的 `dependencies` 列表追加两项：

```toml
dependencies = [
    "openai>=1.50.0",
    "pyyaml>=6.0",
    "ruamel.yaml>=0.18.0",
    "httpx>=0.27.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "pydantic>=2.5.0",
    "python-dateutil>=2.8.2",
    "sqlalchemy>=2.0",
    "pymysql>=1.1",
]
```

- [ ] **Step 2: 安装**

Run: `uv sync --dev`
Expected: 输出含 `+ sqlalchemy` 与 `+ pymysql`

- [ ] **Step 3: 写失败测试**

Create `tests/unit/test_db_config.py`:

```python
"""数据库配置读取与连接串拼装。"""

from __future__ import annotations

import pytest

from app.core import config as cfg_module


@pytest.fixture
def fake_mysql_cfg(monkeypatch):
    """把 get_config 换成只含 mysql 节的假配置。"""
    def _fake():
        return {
            "mysql": {
                "host": "10.0.0.9",
                "port": 3307,
                "database": "mydb",
                "username": "alice",
                "password": "p@ss:word",
                "charset": "utf8mb4",
                "pool_size": 7,
            }
        }
    monkeypatch.setattr(cfg_module, "get_config", _fake)


def test_get_db_config_reads_yaml(fake_mysql_cfg):
    c = cfg_module.get_db_config()
    assert c["host"] == "10.0.0.9"
    assert c["port"] == 3307
    assert c["pool_size"] == 7


def test_env_overrides_yaml(fake_mysql_cfg, monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "envhost")
    monkeypatch.setenv("MYSQL_PORT", "3999")
    c = cfg_module.get_db_config()
    assert c["host"] == "envhost"
    assert c["port"] == 3999


def test_build_url_quotes_special_chars(fake_mysql_cfg):
    """密码含 @ 和 : 必须编码，否则连接串解析错位。"""
    url = cfg_module.build_db_url()
    assert "p%40ss%3Aword" in url
    assert "p@ss:word" not in url
    assert url.startswith("mysql+pymysql://alice:")
    assert url.endswith("@10.0.0.9:3307/mydb?charset=utf8mb4")


def test_empty_database_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(cfg_module, "get_config", lambda: {"mysql": {"database": ""}})
    assert cfg_module.get_db_config()["database"] == "ai_cross_check"
```

- [ ] **Step 4: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_db_config.py -v`
Expected: FAIL，`AttributeError: module 'app.core.config' has no attribute 'get_db_config'`

- [ ] **Step 5: 实现**

`app/core/config.py` 顶部 import 区加 `from urllib.parse import quote`，文件尾部追加：

```python
# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------

_DB_DEFAULTS: dict[str, Any] = {
    "host": "127.0.0.1",
    "port": 3306,
    "database": "ai_cross_check",
    "username": "root",
    "password": "",
    "charset": "utf8mb4",
    "pool_size": 5,
    "max_overflow": 10,
    "echo": False,
}


def get_db_config() -> dict[str, Any]:
    """读取 ``mysql:`` 节，缺项回落到 ``_DB_DEFAULTS``。

    其中 ``host`` ``port`` ``database`` ``username`` ``password`` 这五项额外支持
    同名环境变量覆盖（``MYSQL_HOST`` 等），优先级高于配置文件——这几项是跨环境
    部署时真正会变的。其余四项只读配置文件。

    分离字段而非单行连接串：改起来不易出错，也便于逐项做环境变量覆盖。
    """
    raw = get_config().get("mysql") or {}

    def take(key: str) -> Any:
        """yaml → 默认值。"""
        val = raw.get(key)
        return val if val not in (None, "") else _DB_DEFAULTS[key]

    def pick(key: str) -> Any:
        """环境变量 → yaml → 默认值。"""
        env = os.getenv(f"MYSQL_{key.upper()}")
        return env if env not in (None, "") else take(key)

    return {
        "host": str(pick("host")),
        "port": int(pick("port")),
        "database": str(pick("database")),
        "username": str(pick("username")),
        "password": str(pick("password")),
        "charset": str(take("charset")),
        "pool_size": int(take("pool_size")),
        "max_overflow": int(take("max_overflow")),
        "echo": bool(take("echo")),
    }


def build_db_url(*, with_database: bool = True) -> str:
    """拼装 SQLAlchemy 连接串。

    用户名密码必须过 ``quote``——含 ``@`` ``:`` ``/`` ``#`` 任一字符时，未编码的
    连接串会被解析错位，而报错通常指向「host 不存在」，极难定位。

    这里用 ``quote(x, safe="")`` 而非 ``quote_plus``：后者把空格编成 ``+``，而
    SQLAlchemy 解析 URL 时用的是 ``unquote`` 不是 ``unquote_plus``，``+`` 会原样
    留在密码里，认证报 Access denied，配置文件却看着完全正常。

    返回值含明文密码，**不要直接打进日志**；需要打印时走
    ``make_url(url).render_as_string(hide_password=True)``。

    ``with_database=False`` 用于建库前连到 server 本身。
    """
    c = get_db_config()
    user = quote(c["username"], safe="")
    pwd = quote(c["password"], safe="")
    db = c["database"] if with_database else ""
    return (
        f"mysql+pymysql://{user}:{pwd}@{c['host']}:{c['port']}/{db}"
        f"?charset={c['charset']}"
    )
```

除计划原有的 4 个测试外，另加两个（修复轮补入，见本任务末尾的实施记录）：

```python
def test_build_url_encodes_space_as_pct20(monkeypatch):
    """空格必须编成 %20 而非 +。

    quote_plus 会编成 +，而 SQLAlchemy 解析 URL 用的是 unquote 不是
    unquote_plus，+ 解不回空格，密码就错了。
    """
    monkeypatch.setattr(cfg_module, "get_config", lambda: {
        "mysql": {"username": "al ice", "password": "pass word"}
    })
    url = cfg_module.build_db_url()
    creds = url.split("//", 1)[1].split("@", 1)[0]
    assert "%20" in creds
    # 只看 userinfo 段——scheme 里的 mysql+pymysql 自带一个 +
    assert "+" not in creds


def test_build_url_without_database(fake_mysql_cfg):
    """建库前要连到 server 本身，库名段必须为空。Task 4 走的就是这条路。"""
    url = cfg_module.build_db_url(with_database=False)
    assert "@10.0.0.9:3307/?charset=utf8mb4" in url
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_db_config.py -v`
Expected: 6 passed

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml uv.lock app/core/config.py tests/unit/test_db_config.py
git commit -m "feat(db): 数据库配置读取与连接串拼装

密码过 quote_plus——含 @ 或 : 时未编码的连接串会解析错位，
报错却指向「host 不存在」，极难定位。
MYSQL_* 环境变量逐项覆盖配置文件。"
```

### 实施记录（2026-08-21）

Task 1 已完成，commit `53d0182` + 修复 `e1bb9c7`。上面的代码块已更新为最终实现。原始计划里有一个 bug，代码审查时被抓出：

**原写法用 `quote_plus`，是错的。** `quote_plus` 把空格编成 `+`，而 SQLAlchemy 解析连接串时对 username/password 调的是 `unquote` 而非 `unquote_plus`，`+` 会原样留下。密码 `pass word` 会以字面的 `pass+word` 去认证，报 Access denied，而配置文件看着完全正常——正是该函数 docstring 警告的那类难查故障。`@ : / #` 都没问题，唯独空格会坏。改用 `quote(x, safe="")` 后正确。

同轮一并修掉的：`_DB_DEFAULTS` 缺类型标注导致 `mypy app` 报两处错（`make lint` 门禁内）；`pool_size` / `max_overflow` 用 `raw.get(key) or 默认值` 会把配置的 `0` 静默吃掉（`max_overflow=0` 表示绝不超过 pool_size，是有人会真写的值），抽出 `take()` 与 `pick()` 统一判断；`get_db_config()` docstring 原称 `MYSQL_*` 全覆盖，实际只有 5/9 个字段读环境变量，已收窄措辞。

**遗留给 Task 5：** 宿主环境若存在 `MYSQL_HOST` / `MYSQL_PASSWORD`（docker compose 与官方 mysql 镜像用的正是这些变量名），本文件 6 个测试会挂 4 个——环境变量泄入了断言。`MYSQL_PASSWORD` 会把密码整段换进连接串，连带打死两条跟 host 无关的编码用例，故是 4 不是 2。Task 5 建 `conftest.py` 时要加 autouse fixture 清掉 `MYSQL_*`。

**遗留给 Task 3：** `host` / `database` / `charset` 三项目前是裸插进 f-string 的。当前值来自受信任的配置文件，没有问题；若 Task 3 想更稳，可改用 `URL.create(...)` 让 SQLAlchemy 自己组装，连编码都不用管。

---

## Task 2: ORM 模型

**Files:**
- Create: `app/db/__init__.py`, `app/db/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_models.py`:

```python
"""ORM 模型定义与方言兼容。"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect

from app.db.models import Base, Project, Rule


def test_tables_created_on_sqlite():
    """三张表能在 SQLite 上建起来——测试全靠这个。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    names = set(inspect(engine).get_table_names())
    assert names == {"projects", "rules", "compare_history"}


def test_autoincrement_works_on_sqlite():
    """BigInteger 主键在 SQLite 必须退化成 INTEGER 才会自增。

    SQLite 只把 `INTEGER PRIMARY KEY` 当作 rowid 别名；渲染成 BIGINT
    则不自增，插入时报 NOT NULL 约束失败。
    """
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        p = Project(key="demo", name="演示")
        s.add(p)
        s.commit()
        assert p.id == 1


def test_rule_project_relationship():
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        p = Project(key="demo", name="演示")
        s.add(p)
        s.flush()
        r = Rule(rule_id="R001", prompt_template="t", project_id=p.id)
        s.add(r)
        s.commit()
        assert r.project.key == "demo"
        assert [x.rule_id for x in p.rules] == ["R001"]


def test_field_defs_json_roundtrip():
    from sqlalchemy.orm import Session

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Rule(rule_id="R001", prompt_template="t", field_defs=["a", "b"]))
        s.commit()
    with Session(engine) as s:
        got = s.query(Rule).one()
        assert got.field_defs == ["a", "b"]


def test_history_has_no_foreign_key():
    """历史表存快照，不能有外键——规则删了历史必须还在。"""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    fks = inspect(engine).get_foreign_keys("compare_history")
    assert fks == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: 建包**

Create `app/db/__init__.py`（空文件）:

```python
```

- [ ] **Step 4: 写模型**

Create `app/db/models.py`:

```python
"""三张表的 ORM 定义。

字段增改约定
-----------
本项目不做启动时字段自动迁移(见 spec 决策 6),表结构变更需人工执行 SQL,
并同步更新 ``docs/schema.sql``。

方言兼容
--------
主键用 ``_BIGINT_PK``:MySQL 下是 BIGINT,SQLite 下退化成 INTEGER。
SQLite 只把 ``INTEGER PRIMARY KEY`` 当 rowid 别名,渲染成 BIGINT 就不自增了,
而测试全跑在 SQLite 上。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import sqlite
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_BIGINT_PK = BigInteger().with_variant(sqlite.INTEGER(), "sqlite")


class Base(DeclarativeBase):
    pass


class Project(Base):
    """项目。``key`` 供 yaml 跨环境引用,``id`` 供 DB 内部关联。"""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    rules: Mapped[list[Rule]] = relationship(back_populates="project")


class Rule(Base):
    """规则。``rule_id`` 是下游 ``/compare`` 传的业务 ID,全局唯一。

    主键用自增 ``id`` 而非 ``rule_id``:PUT 接口支持 rename,
    ``rule_id`` 若为主键则 rename 变成改主键。
    """

    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    project_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    field_defs: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    require_field_name: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_template: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project | None] = relationship(back_populates="rules")


class CompareHistory(Base):
    """比对历史。

    刻意不设外键:存的是调用当时的快照,规则后续改名/删除/换项目
    都不该影响既有历史。
    """

    __tablename__ = "compare_history"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rule_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    project_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fields: Mapped[list] = mapped_column(JSON, nullable=False)
    result: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_request: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: 5 passed

- [ ] **Step 6: 提交**

```bash
git add app/db/__init__.py app/db/models.py tests/unit/test_models.py
git commit -m "feat(db): 三张表的 ORM 模型

- 主键用 BigInteger().with_variant(sqlite.INTEGER)：SQLite 只把
  INTEGER PRIMARY KEY 当 rowid 别名，渲染成 BIGINT 就不自增，
  而测试全跑在 SQLite 上
- rules.project_id ON DELETE SET NULL，删项目不删规则
- compare_history 不设外键，存调用当时的快照"
```

---

## Task 3: 引擎与会话

**Files:**
- Create: `app/db/session.py`
- Test: `tests/unit/test_session.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_session.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db.session'`

- [ ] **Step 3: 实现**

Create `app/db/session.py`:

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_session.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add app/db/session.py tests/unit/test_session.py
git commit -m "feat(db): 引擎与会话依赖

pool_pre_ping + pool_recycle=3600 是连外部 MySQL 的刚需：
MySQL 默认 8 小时空闲断连，无 pre_ping 时服务闲置一夜后
第一个请求必拿到死连接报 MySQL server has gone away。"
```

### 实施记录（2026-08-24）

commit `383eabe` + 修复 `46f2643`。上面的实现已是最终版本，测试共 **7 条**。

**加锁是审查追加的。** 原计划的 `get_engine()` 是裸的 check-then-act，而 `get_db` 是同步依赖、会被 FastAPI 派到线程池。首批并发请求会交错赋值，最终 `_SessionLocal` 与 `_engine` 绑到不同引擎，多出来的连接池谁也 dispose 不掉——不崩溃，只是缓慢泄漏，没有任何报错指向根因。

**「发布最后化」比加锁本身更微妙。** 快路径只检查 `_engine`。若沿用「先赋值 `_engine` 再建 `_SessionLocal`」的朴素顺序，另一个线程会从快路径拿到引擎、直奔 `get_sessionmaker()`，读到还是 `None` 的 `_SessionLocal` 撞上 assert——比原缺陷更尖锐。所以必须等工厂建好再发布 `_engine`，那两行的顺序是承重的。

`dispose_engine()` 同样持锁，`get_sessionmaker()` 改成单次读取后 assert——都是同一个竞态的其他面，只修一半会给出「已处理过」的假象。

**测试清单**（后三条为审查追加，均做过缺陷注入验证，确认缺陷态下必红）：

| 用例 | 守什么 |
|---|---|
| `test_get_engine_is_singleton` | 单例语义 |
| `test_dispose_engine_resets` | dispose 后重建 |
| `test_sqlite_url_skips_pool_args` | URL 前缀分支；同时证明 monkeypatch 打中了 `sess_module` |
| `test_get_db_yields_session` | yield 出可用 Session |
| `test_get_db_closes_session` | `finally` 里 `close()` 真被调用 |
| `test_get_engine_is_thread_safe` | 并发下只建一个引擎 |
| `test_engine_published_after_sessionmaker` | 发布顺序不被善意重构改回去 |

原 `test_get_db_yields_and_closes` 已改名为 `test_get_db_yields_session`：它断言的 `db.is_active` 对任何新建 Session 恒为 True，从未验证过 close，名字却声称管——名字与断言对不上比测试弱更误导人。

**一个方法论教训，值得后续任务照做。** 实施者最初用固定 `sleep(0.02)` 探测发布顺序隐患，测试**通过**了，差点据此断定隐患不存在；换成轮询才命中。原因是首次 `create_engine` 本身耗时超过 20ms，探测线程反被锁挡在外面。这是典型的假阴性——测试绿了，但绿的原因和预期完全不同。**并发测试用固定 sleep 撑窗口时，要先确认窗口真的被撑开了。**

**遗留给 Task 4：** 本任务的测试桩 `lambda **kw: "sqlite://"` 对 `with_database` 的取值一视同仁。在引擎侧无害（引擎没有理由走 `with_database=False`），但 Task 4 的 bootstrap **建库正是走这条路**，那里的桩必须能区分取值，不能再一把梭。

---

## Task 4: 建库建表

**Files:**
- Create: `app/db/bootstrap.py`
- Test: `tests/unit/test_bootstrap.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_bootstrap.py`:

```python
"""建库建表。真实 MySQL 分支只测调用契约与 SQL 文本，不连库。"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, inspect
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_bootstrap.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db.bootstrap'`

- [ ] **Step 3: 实现**

Create `app/db/bootstrap.py`:

```python
"""启动时建库建表。

范围明确:只建库、只建表。**不做字段自动迁移**(spec 决策 6)——表结构变更
由人工执行 SQL,参考 ``docs/schema.sql``。
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
    """库名会直接拼进 DDL,不校验就是注入口子。"""
    if not _DB_NAME_RE.match(name or ""):
        raise ValueError(
            f"库名非法: {name!r},须匹配 ^[A-Za-z_][A-Za-z0-9_]*$"
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
    """库不存在则建。SQLite 无『库』概念,按方言跳过。"""
    url = build_db_url()
    if not url.startswith("mysql"):
        log.debug("非 MySQL 方言,跳过建库")
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

    外层的 try/except 兜多实例首次同时启动的窄竞态:``create_all`` 的
    checkfirst 与实际 CREATE 之间有间隙,两个进程可能同时判定「表不存在」。
    """
    try:
        Base.metadata.create_all(engine)
    except (OperationalError, ProgrammingError) as e:
        msg = str(e).lower()
        if "already exists" in msg or "duplicate" in msg:
            log.warning("建表时撞上并发创建,已忽略: %s", e)
            return
        log.error(
            "建表失败: %s\n应用账号需要 CREATE 权限,或参考 docs/schema.sql 手工执行 DDL。",
            e,
        )
        raise


def bootstrap_database(engine: Engine | None = None) -> Engine:
    """建库 + 建表,返回可用引擎。启动时调用一次。"""
    ensure_database()
    if engine is None:
        from app.db.session import get_engine

        engine = get_engine()
    ensure_tables(engine)
    return engine
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_bootstrap.py -v`
Expected: 18 passed

- [ ] **Step 5: 提交**

```bash
git add app/db/bootstrap.py tests/unit/test_bootstrap.py
git commit -m "feat(db): 启动时建库建表

- 库名先过正则再拼进 DDL，否则是注入口子
- create_all 外包 try/except 兜多实例首启的窄竞态
- 权限不足时直接打出该执行的 SQL 和该授的权，不甩 Access denied
- 不做字段自动迁移（spec 决策 6）"
```

### 实施记录（2026-08-24）

commit `8fa2ca3`，18 条测试，全量 126 通过。实现与上面粘贴的代码逐字一致（仅标点改全角）。

**三条守护非显然行为的测试做了缺陷注入验证，全部见红：**

| 注入的缺陷 | 转红的测试 |
|---|---|
| `build_db_url(with_database=False)` → `build_db_url()` | `test_ensure_database_connects_without_db_name` |
| 反转 `if "already exists" in msg` | `test_ensure_tables_swallows_concurrent_create` |
| 无条件吞掉所有异常 | `test_ensure_tables_reraises_other_errors` |

第一条正是 Task 3 遗留提醒的那个点——原计划的测试桩 `lambda **kw` 抹平了 `with_database` 的取值差异，这个 bug 溜过去也测不出来。

**两条需要知晓的性质：**

**1. `ensure_database()` 里「非 MySQL 就跳过」的分支在生产中永不执行。** `build_db_url()` 硬编码 `mysql+pymysql://` 前缀（`app/core/config.py:220`），`url.startswith("mysql")` 恒为真。该分支只在测试 monkeypatch 时走到。保留它是面向未来方言的防御，但**不要误以为本项目支持 SQLite 部署**——测试用 SQLite 走的是 `conftest.py` 的依赖覆盖，不经过 bootstrap。

**2. `log.error` 里 `'<user>'@'%%'` 渲染成单个 `%` 的前提是该调用带 args。** logging 仅在 `record.args` 非空时才做 `%` 格式化。将来若有人把三个 `%s` 参数去掉、改成 f-string 直接拼库名，`%%` 会原样漏进给 DBA 的 SQL 里。当前形态已验证正确（`LogRecord.getMessage()` 输出 `GRANT ALL ON \`mydb\`.* TO '<user>'@'%';`）。

**`bootstrap_database()` 的函数内 import 是有意保留的。** 实测确认模块级导入无循环依赖，但函数内导入让 `bootstrap_database(engine)` 这条路径根本不加载 `app.db.session`，把「传入 engine 时绝不触碰进程级单例」从纪律约束变成结构保证。不要「顺手优化」成模块级。

---

## Task 5: 测试夹具

**Files:**
- Create: `tests/conftest.py`
- Test: `tests/unit/test_conftest_wiring.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_conftest_wiring.py`:

```python
"""验证测试夹具本身可用——夹具坏了后面全是假绿。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import Project, Rule


def test_db_fixture_is_empty(db):
    assert db.query(Rule).count() == 0
    assert db.query(Project).count() == 0


def test_foreign_keys_enabled(db):
    """SQLite 默认不启用外键，ON DELETE SET NULL 会静默失效。

    没有这个 PRAGMA，「删项目→规则变未分组」这条测了个寂寞。
    """
    on = db.execute(text("PRAGMA foreign_keys")).scalar()
    assert on == 1


def test_foreign_key_rejects_dangling_project_id(db):
    """外键真生效的话，指向不存在项目的 project_id 必须被拒。

    这是对 PRAGMA 最直接的验证：约束没开的话这行会安静地写进去。
    """
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=99999))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_on_delete_set_null_is_enforced_by_db(db):
    """删项目后规则的 project_id 变 NULL——且这必须是**数据库**做的。

    删除刻意走原生 SQL 而不是 ``db.delete(p)``：走 ORM 的话，SQLAlchemy
    会自己加载 Project.rules 并把子对象的 project_id 置 NULL（默认 cascade），
    那样即使 SQLite 外键完全没启用，这条测试照样绿——验证的就成了 ORM 行为，
    而不是本测试真正关心的 DB 约束。
    """
    p = Project(key="demo", name="演示")
    db.add(p)
    db.flush()
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=p.id))
    db.commit()
    pid = p.id

    db.execute(text("DELETE FROM projects WHERE id = :id"), {"id": pid})
    db.commit()
    db.expire_all()

    assert db.query(Rule).filter_by(rule_id="R001").one().project_id is None


def test_client_uses_same_session(client, db):
    """TestClient 与 db 夹具必须共用同一会话，否则测试里写的数据接口看不见。"""
    db.add(Rule(rule_id="R001", prompt_template="t"))
    db.commit()
    resp = client.get("/admin/rules")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_conftest_wiring.py -v`
Expected: FAIL，`fixture 'db' not found`

（`test_client_uses_same_session` 在 Task 12 改造 `admin_rules` 之前会一直失败，属预期，见 Step 4）

- [ ] **Step 3: 实现夹具**

Create `tests/conftest.py`:

```python
"""全局测试夹具。

所有 DB 测试跑 SQLite in-memory,不依赖真实 MySQL。两个必须处理的方言差异:

1. SQLite 默认**不启用**外键约束,``ON DELETE SET NULL`` 会静默失效。
   必须挂 connect 事件执行 ``PRAGMA foreign_keys=ON``。
2. 内存库随连接销毁而消失,故用 ``StaticPool`` 让整个引擎共用一条连接。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clean_mysql_env(monkeypatch):
    """清掉宿主环境的 ``MYSQL_*``,否则它们会泄进断言。

    ``get_db_config()`` 让 MYSQL_HOST / MYSQL_PASSWORD 等覆盖配置文件,而这几个
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
def db(engine) -> Session:
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = maker()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def client(db):
    """TestClient 与 ``db`` 夹具共用同一会话。

    共用是刻意的:测试里用 ``db`` 直接塞数据,再用 ``client`` 调接口断言,
    两者必须看到同一份未提交状态。因此覆盖里不关闭会话——生命周期归 ``db``。
    """
    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
```

- [ ] **Step 4: 跑测试确认前四条通过**

Run: `uv run pytest tests/unit/test_conftest_wiring.py -v -k "not client_uses"`
Expected: 4 passed

`test_client_uses_same_session` 依赖 Task 12 把 `/admin/rules` 切到 DB，现在仍读 yaml（会返回 `config/rules.yaml` 里真实的 54 条），所以断言 `count == 1` 必失败。下一步标记它。

- [ ] **Step 5: 标记待转绿用例**

在 `test_client_uses_same_session` 上方加装饰器（`import pytest` 已在文件顶部）：

```python
@pytest.mark.xfail(reason="Task 12 把 /admin/rules 切到 DB 后转绿", strict=True)
```

`strict=True` 是有意的：Task 12 完成后这条会 xpass，而 strict 模式下 xpass 算失败——等于自动提醒「该摘标记了」，不会悄悄留一个永久 xfail 在那儿。

Run: `uv run pytest tests/unit/test_conftest_wiring.py -v`
Expected: 4 passed, 1 xfailed

- [ ] **Step 6: 提交**

```bash
git add tests/conftest.py tests/unit/test_conftest_wiring.py
git commit -m "test: SQLite in-memory 夹具

- PRAGMA foreign_keys=ON：SQLite 默认不启用外键，否则
  ON DELETE SET NULL 静默失效，删项目的测试测了个寂寞
- StaticPool：内存库随连接销毁，须让引擎共用一条连接
- client 与 db 共用会话，测试里塞的数据接口才看得见"
```

### 实施记录（2026-08-24）

commit `40843cc`（夹具本体）+ `cdfac21`（收尾）。夹具最终 **5 条测试 + 1 xfail**，全量 131 passed。

**四条守护非显然行为的测试做了缺陷注入，全部见红：**

| 注入的缺陷 | 转红的测试 | 观察到的失败态 |
|---|---|---|
| 删掉 `PRAGMA foreign_keys=ON` | `test_foreign_keys_enabled`、`test_foreign_key_rejects_dangling_project_id`、`test_on_delete_set_null_is_enforced_by_db` 三条全红 | 外键静默失效 |
| 把 `db.delete(p)` 换回 ORM 删除（对照实验） | `test_on_delete_set_null...` 在 FK 关闭时**照样绿** | 证明原写法验证的是 ORM cascade 而非 DB 约束，改用原生 SQL 删除是必要的 |
| 摘掉 `poolclass=StaticPool` | `test_engine_is_shared_across_threads` 红 | `no such table: rules` |

**StaticPool 的真实承重点被查清了。** 摘掉它后单线程 4 条测试**全部照常通过**——因为 SQLAlchemy 给内存 SQLite 的默认池是 `SingletonThreadPool`，单线程内连接本就复用，schema 不会丢。它只在**跨线程**时承重：接口测试经 TestClient 调同步 endpoint 时 Starlette 派进工作线程池，那条新连接看到的是另一个空库。故补了 `test_engine_is_shared_across_threads` 显式跨线程查询钉死这个性质——否则它要到 Task 12 才隐性承重，届时报的 `no such table` 看着像建表没跑成，极难定位。conftest 模块 docstring 第 2 条也随之订正（原"内存库随连接销毁而消失"不准确，真相是"连接私有，换连接即换空库"）。

**修正两处计划里的数字：** (1) `MYSQL_*` 泄漏影响 test_db_config.py **4 个**测试而非 2 个——`MYSQL_PASSWORD` 会把密码整段换进连接串，连带打死两条跟 host 无关的编码用例（已在上方 Task 1 遗留提醒与本任务 conftest 代码块中订正）。(2) `db` 夹具是生成器，返回标注应为 `Iterator[Session]`。

**遗留给 Task 17：** `client` 夹具用 `app.dependency_overrides.clear()` 收尾。若 `with TestClient(app)` 的 lifespan 阶段（yield 之前）抛异常，`clear()` 会被跳过，污染后续用例的覆盖表。Task 17 重写 lifespan 时，把它改成 `finally` 里 `app.dependency_overrides.pop(get_db, None)`。当前无 lifespan 逻辑，尚不触发。

---

## Task 6: 项目仓储

**Files:**
- Create: `app/db/repo_projects.py`
- Test: `tests/unit/test_repo_projects.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_repo_projects.py`:

```python
"""项目仓储。"""

from __future__ import annotations

import pytest

from app.db import repo_projects as repo
from app.db.models import Rule


def test_create_and_get(db):
    p = repo.create(db, key="zhengfu", name="政府投资", description="d")
    db.commit()
    got = repo.get_by_key(db, "zhengfu")
    assert got is not None
    assert got.id == p.id
    assert got.name == "政府投资"


def test_get_missing_returns_none(db):
    assert repo.get_by_key(db, "nope") is None


def test_sort_order_increments(db):
    a = repo.create(db, key="a", name="A")
    b = repo.create(db, key="b", name="B")
    db.commit()
    assert b.sort_order == a.sort_order + 1


def test_list_ordered_by_sort_order(db):
    repo.create(db, key="b", name="B")
    repo.create(db, key="a", name="A")
    db.commit()
    assert [p.key for p in repo.list_projects(db)] == ["b", "a"]


def test_update_fields(db):
    p = repo.create(db, key="a", name="A")
    db.commit()
    repo.update(db, p, key="a2", name="A2", description="dd")
    db.commit()
    assert repo.get_by_key(db, "a2").description == "dd"
    assert repo.get_by_key(db, "a") is None


def test_delete_returns_affected_rule_count_and_keeps_rules(db):
    """删项目必须只解除归属，不能级联删规则。"""
    p = repo.create(db, key="a", name="A")
    db.flush()
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R002", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R003", prompt_template="t"))
    db.commit()

    n = repo.delete(db, p)
    db.commit()

    assert n == 2
    assert db.query(Rule).count() == 3
    assert all(r.project_id is None for r in db.query(Rule).all())


def test_rule_counts(db):
    p = repo.create(db, key="a", name="A")
    db.flush()
    db.add(Rule(rule_id="R001", prompt_template="t", project_id=p.id))
    db.add(Rule(rule_id="R002", prompt_template="t"))
    db.commit()
    assert repo.rule_counts(db) == {p.id: 1}


def test_to_api_dict_shape(db):
    p = repo.create(db, key="a", name="A", description="d")
    db.commit()
    assert repo.to_api_dict(p, rule_count=3) == {
        "key": "a", "name": "A", "description": "d", "rule_count": 3,
    }
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_repo_projects.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db.repo_projects'`

- [ ] **Step 3: 实现**

Create `app/db/repo_projects.py`:

```python
"""项目仓储。只认 ``projects`` 表,不碰 HTTP,不管事务提交。

提交由调用方(API 路由或 rules_sync)决定——仓储无从判断一次请求里
还有没有别的写操作要一起成败。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Project, Rule


def list_projects(db: Session) -> list[Project]:
    return list(
        db.scalars(select(Project).order_by(Project.sort_order, Project.id)).all()
    )


def get_by_key(db: Session, key: str) -> Project | None:
    return db.scalars(select(Project).where(Project.key == key)).first()


def next_sort_order(db: Session) -> int:
    cur = db.scalar(select(func.max(Project.sort_order)))
    return (cur or 0) + 1


def create(db: Session, *, key: str, name: str, description: str = "") -> Project:
    p = Project(
        key=key,
        name=name,
        description=description or "",
        sort_order=next_sort_order(db),
    )
    db.add(p)
    db.flush()
    return p


def update(
    db: Session,
    project: Project,
    *,
    key: str | None = None,
    name: str | None = None,
    description: str | None = None,
) -> Project:
    if key is not None:
        project.key = key
    if name is not None:
        project.name = name
    if description is not None:
        project.description = description
    db.flush()
    return project


def delete(db: Session, project: Project) -> int:
    """删项目,返回被解除归属的规则数。

    显式先把规则置 NULL 而不是依赖数据库的 ON DELETE SET NULL:后者在
    SQLite 需要 PRAGMA、在 MySQL 需要引擎支持,显式做一次两边行为一致,
    也才能拿到准确的受影响条数返回给 UI。
    """
    n = db.query(Rule).filter(Rule.project_id == project.id).update(
        {Rule.project_id: None}, synchronize_session=False
    )
    db.delete(project)
    db.flush()
    return int(n)


def rule_counts(db: Session) -> dict[int, int]:
    """``{project_id: 规则数}``,未分组不计入。"""
    rows = db.execute(
        select(Rule.project_id, func.count(Rule.id))
        .where(Rule.project_id.is_not(None))
        .group_by(Rule.project_id)
    ).all()
    return {int(pid): int(cnt) for pid, cnt in rows}


def unassigned_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count(Rule.id)).where(Rule.project_id.is_(None))) or 0
    )


def to_api_dict(project: Project, *, rule_count: int = 0) -> dict[str, Any]:
    return {
        "key": project.key,
        "name": project.name,
        "description": project.description or "",
        "rule_count": rule_count,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_repo_projects.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add app/db/repo_projects.py tests/unit/test_repo_projects.py
git commit -m "feat(db): 项目仓储

delete() 显式把规则 project_id 置 NULL 而非只依赖 ON DELETE SET NULL：
两种方言行为一致，也才能拿到准确的受影响条数返回给 UI。"
```

---

## Task 7: 规则仓储

**Files:**
- Create: `app/db/repo_rules.py`
- Test: `tests/unit/test_repo_rules.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_repo_rules.py`:

```python
"""规则仓储。重点是 to_rule_cfg —— app/rules/ 全靠它保持零改动。"""

from __future__ import annotations

from app.db import repo_projects, repo_rules as repo


BASE = {
    "id": "R001",
    "name": "总投资一致",
    "description": "d",
    "require_field_name": False,
    "system_prompt": "sys",
    "prompt_template": "A:{field_a} B:{field_b}",
}


def test_create_and_get(db):
    repo.create(db, BASE)
    db.commit()
    got = repo.get_by_rule_id(db, "R001")
    assert got is not None
    assert got.name == "总投资一致"


def test_sort_order_increments(db):
    a = repo.create(db, BASE)
    b = repo.create(db, dict(BASE, id="R002"))
    db.commit()
    assert b.sort_order == a.sort_order + 1


def test_list_ordered_by_sort_order(db):
    repo.create(db, dict(BASE, id="R002"))
    repo.create(db, dict(BASE, id="R001"))
    db.commit()
    assert [r.rule_id for r in repo.list_rules(db)] == ["R002", "R001"]


def test_list_filter_by_project(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE, project_id=p.id)
    repo.create(db, dict(BASE, id="R002"))
    db.commit()

    assert [r.rule_id for r in repo.list_rules(db, project_key="zf")] == ["R001"]
    assert [r.rule_id for r in repo.list_rules(db, unassigned=True)] == ["R002"]
    assert len(repo.list_rules(db)) == 2


def test_list_filter_unknown_project_returns_empty(db):
    repo.create(db, BASE)
    db.commit()
    assert repo.list_rules(db, project_key="nope") == []


def test_update_replaces_all_fields(db):
    r = repo.create(db, BASE)
    db.commit()
    repo.update(db, r, dict(BASE, id="R001", name="改后", field_defs=["a", "b"]))
    db.commit()
    got = repo.get_by_rule_id(db, "R001")
    assert got.name == "改后"
    assert got.field_defs == ["a", "b"]


def test_update_can_rename(db):
    r = repo.create(db, BASE)
    db.commit()
    repo.update(db, r, dict(BASE, id="R099"))
    db.commit()
    assert repo.get_by_rule_id(db, "R001") is None
    assert repo.get_by_rule_id(db, "R099") is not None


def test_update_keeps_sort_order(db):
    """改规则不应改变它在列表里的位置。"""
    r = repo.create(db, BASE)
    db.commit()
    before = r.sort_order
    repo.update(db, r, dict(BASE, name="改后"))
    db.commit()
    assert repo.get_by_rule_id(db, "R001").sort_order == before


def test_assign_project(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE)
    repo.create(db, dict(BASE, id="R002"))
    db.commit()

    n = repo.assign_project(db, ["R001", "R002"], p.id)
    db.commit()
    assert n == 2
    assert all(r.project_id == p.id for r in repo.list_rules(db))


def test_assign_project_none_unassigns(db):
    p = repo_projects.create(db, key="zf", name="政府")
    db.flush()
    repo.create(db, BASE, project_id=p.id)
    db.commit()

    repo.assign_project(db, ["R001"], None)
    db.commit()
    assert repo.get_by_rule_id(db, "R001").project_id is None


def test_assign_project_ignores_unknown_ids(db):
    repo.create(db, BASE)
    db.commit()
    assert repo.assign_project(db, ["R001", "NOPE"], None) == 1


# ---------------------------------------------------------------------------
# to_rule_cfg：app/rules/ 的输入契约，改坏了 /compare 就跟着坏
# ---------------------------------------------------------------------------

def test_to_rule_cfg_shape(db):
    r = repo.create(db, BASE)
    db.commit()
    assert repo.to_rule_cfg(r) == {
        "id": "R001",
        "name": "总投资一致",
        "description": "d",
        "require_field_name": False,
        "system_prompt": "sys",
        "prompt_template": "A:{field_a} B:{field_b}",
    }


def test_to_rule_cfg_omits_empty_field_defs(db):
    """field_keys.py 用 cfg.get('field_defs') 判断，空列表和缺键必须等价。"""
    r = repo.create(db, dict(BASE, field_defs=[]))
    db.commit()
    assert "field_defs" not in repo.to_rule_cfg(r)


def test_to_rule_cfg_includes_field_defs(db):
    r = repo.create(db, dict(BASE, field_defs=["x", "y"]))
    db.commit()
    assert repo.to_rule_cfg(r)["field_defs"] == ["x", "y"]


# ---------------------------------------------------------------------------
# to_api_dict：/admin/rules 的输出契约
# ---------------------------------------------------------------------------

def test_to_api_dict_without_project(db):
    r = repo.create(db, BASE)
    db.commit()
    d = repo.to_api_dict(r)
    assert d["id"] == "R001"
    assert d["project"] is None
    assert "field_defs" not in d


def test_to_api_dict_with_project(db):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    r = repo.create(db, BASE, project_id=p.id)
    db.commit()
    assert repo.to_api_dict(r)["project"] == {"key": "zf", "name": "政府投资"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_repo_rules.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db.repo_rules'`

- [ ] **Step 3: 实现**

Create `app/db/repo_rules.py`:

```python
"""规则仓储。

两个转换函数是本模块的重点:

- ``to_rule_cfg``  ORM 行 → ``app/rules/`` 期待的 plain dict。有它,规则领域层
  (``field_keys`` / ``llm_rule`` / ``registry``)才能对存储介质一无所知。
- ``to_api_dict``  ORM 行 → ``/admin/rules`` 的响应结构,带嵌套 ``project``。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Project, Rule

# 前端提交的规则字段 → 表列名。``id`` 单列出来因为列名不同。
_FIELDS = (
    "name",
    "description",
    "field_defs",
    "require_field_name",
    "system_prompt",
    "prompt_template",
)


def list_rules(
    db: Session,
    *,
    project_key: str | None = None,
    unassigned: bool = False,
) -> list[Rule]:
    stmt = select(Rule).options(joinedload(Rule.project))
    if unassigned:
        stmt = stmt.where(Rule.project_id.is_(None))
    elif project_key is not None:
        stmt = stmt.join(Project).where(Project.key == project_key)
    stmt = stmt.order_by(Rule.sort_order, Rule.id)
    return list(db.scalars(stmt).unique().all())


def get_by_rule_id(db: Session, rule_id: str) -> Rule | None:
    return db.scalars(
        select(Rule).options(joinedload(Rule.project)).where(Rule.rule_id == rule_id)
    ).first()


def count(db: Session) -> int:
    return int(db.scalar(select(func.count(Rule.id))) or 0)


def next_sort_order(db: Session) -> int:
    cur = db.scalar(select(func.max(Rule.sort_order)))
    return (cur or 0) + 1


def _apply(rule: Rule, data: dict[str, Any]) -> None:
    """把前端 dict 写进 ORM 行。``sort_order`` 与 ``project_id`` 不在此处理。"""
    rule.rule_id = data["id"]
    for key in _FIELDS:
        if key not in data:
            continue
        val = data[key]
        if key in ("name", "description", "system_prompt"):
            val = val or ""
        if key == "require_field_name":
            val = bool(val)
        if key == "field_defs":
            val = list(val) if val else None
        setattr(rule, key, val)


def create(db: Session, data: dict[str, Any], *, project_id: int | None = None) -> Rule:
    rule = Rule(prompt_template="", sort_order=next_sort_order(db))
    _apply(rule, data)
    rule.project_id = project_id
    db.add(rule)
    db.flush()
    return rule


def update(
    db: Session,
    rule: Rule,
    data: dict[str, Any],
    *,
    project_id: int | None = None,
    set_project: bool = False,
) -> Rule:
    """全量更新。``sort_order`` 保持不变——改内容不该改列表位置。

    ``set_project=False`` 时不动归属,便于「只改内容」的调用方少传参数。
    """
    _apply(rule, data)
    if set_project:
        rule.project_id = project_id
    db.flush()
    return rule


def delete(db: Session, rule: Rule) -> None:
    db.delete(rule)
    db.flush()


def assign_project(
    db: Session, rule_ids: list[str], project_id: int | None
) -> int:
    """批量改归属,返回实际命中的条数。不存在的 id 静默跳过。"""
    if not rule_ids:
        return 0
    n = (
        db.query(Rule)
        .filter(Rule.rule_id.in_(rule_ids))
        .update({Rule.project_id: project_id}, synchronize_session=False)
    )
    db.flush()
    return int(n)


def to_rule_cfg(rule: Rule) -> dict[str, Any]:
    """ORM 行 → ``app/rules/`` 的输入 dict。

    ``field_defs`` 为空时**不放这个键**:``field_keys.py`` 用
    ``cfg.get("field_defs")`` 判断,空列表与缺键必须等价。
    """
    cfg: dict[str, Any] = {
        "id": rule.rule_id,
        "name": rule.name or "",
        "description": rule.description or "",
        "require_field_name": bool(rule.require_field_name),
        "system_prompt": rule.system_prompt or "",
        "prompt_template": rule.prompt_template,
    }
    if rule.field_defs:
        cfg["field_defs"] = list(rule.field_defs)
    return cfg


def to_api_dict(rule: Rule) -> dict[str, Any]:
    """ORM 行 → ``/admin/rules`` 响应。在 ``to_rule_cfg`` 基础上加 ``project``。"""
    out = to_rule_cfg(rule)
    out["project"] = (
        {"key": rule.project.key, "name": rule.project.name} if rule.project else None
    )
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_repo_rules.py -v`
Expected: 16 passed

- [ ] **Step 5: 提交**

```bash
git add app/db/repo_rules.py tests/unit/test_repo_rules.py
git commit -m "feat(db): 规则仓储

to_rule_cfg 把 ORM 行还原成 app/rules/ 期待的 plain dict，
规则领域层因此对存储介质一无所知，本次改造零改动。
field_defs 为空时不放键——field_keys.py 用 .get() 判断，
空列表与缺键必须等价。
update() 不动 sort_order：改内容不该改列表位置。"
```

---

## Task 8: 历史仓储

**Files:**
- Create: `app/db/repo_history.py`
- Test: `tests/unit/test_repo_history.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_repo_history.py`:

```python
"""历史仓储。id 对外仍是字符串，避免前端跟着改。"""

from __future__ import annotations

from app.db import repo_history as repo


REC = {
    "rule_id": "R001",
    "rule_name": "总投资一致",
    "project_key": "zf",
    "project_name": "政府投资",
    "fields": [{"key": "field_a", "value": "1", "name": None}],
    "result": True,
    "reason": "ok",
    "time_ms": 120,
    "cost": {"total_tokens": 4},
    "raw_request": {"rule_id": "R001"},
}


def test_add_returns_str_id(db):
    """对外 id 是字符串：内存版曾用 uuid[:8]，改成数字会波及前端。"""
    rid = repo.add(db, REC)
    db.commit()
    assert isinstance(rid, str)


def test_get_roundtrip(db):
    rid = repo.add(db, REC)
    db.commit()
    got = repo.get(db, rid)
    assert got is not None
    assert repo.to_api_dict(got)["reason"] == "ok"


def test_get_non_numeric_id_returns_none(db):
    """前端传脏 id 不该 500。"""
    assert repo.get(db, "abc") is None


def test_newest_first(db):
    repo.add(db, dict(REC, rule_id="R001"))
    repo.add(db, dict(REC, rule_id="R002"))
    db.commit()
    items = repo.list_records(db)["items"]
    assert [i["rule_id"] for i in items] == ["R002", "R001"]


def test_filter_by_rule_id(db):
    repo.add(db, dict(REC, rule_id="R001"))
    repo.add(db, dict(REC, rule_id="R002"))
    db.commit()
    out = repo.list_records(db, rule_id="R002")
    assert out["total"] == 1


def test_filter_by_result(db):
    repo.add(db, dict(REC, result=True))
    repo.add(db, dict(REC, result=False))
    db.commit()
    assert repo.list_records(db, result=False)["total"] == 1


def test_pagination(db):
    for i in range(5):
        repo.add(db, dict(REC, rule_id=f"R{i}"))
    db.commit()
    out = repo.list_records(db, limit=2, offset=1)
    assert out["total"] == 5
    assert len(out["items"]) == 2


def test_clear(db):
    repo.add(db, REC)
    db.commit()
    assert repo.clear(db) == 1
    db.commit()
    assert repo.list_records(db)["total"] == 0


def test_to_api_dict_keeps_legacy_shape(db):
    """字段名与内存版一致，前端历史页零改动。"""
    rid = repo.add(db, REC)
    db.commit()
    d = repo.to_api_dict(repo.get(db, rid))
    assert set(d) >= {
        "id", "rule_id", "rule_name", "fields", "result",
        "reason", "time_ms", "cost", "created_at",
    }
    assert d["id"] == rid
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_repo_history.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.db.repo_history'`

- [ ] **Step 3: 实现**

Create `app/db/repo_history.py`:

```python
"""历史仓储。

对外 ``id`` 保持字符串:内存版用的是 ``uuid4()[:8]``,前端按字符串处理。
改成数字会波及历史页的详情展开逻辑,没必要为了内部主键类型去动 UI。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import CompareHistory


def add(db: Session, record: dict[str, Any]) -> str:
    row = CompareHistory(
        rule_id=record.get("rule_id", ""),
        rule_name=record.get("rule_name") or "",
        project_key=record.get("project_key"),
        project_name=record.get("project_name"),
        fields=record.get("fields") or [],
        result=bool(record.get("result")),
        reason=record.get("reason"),
        time_ms=int(record.get("time_ms") or 0),
        cost=record.get("cost") or {},
        raw_request=record.get("raw_request"),
    )
    db.add(row)
    db.flush()
    return str(row.id)


def get(db: Session, record_id: str) -> CompareHistory | None:
    """按 id 取。非数字 id 返 None 而非抛异常——前端传脏值不该 500。"""
    try:
        pk = int(record_id)
    except (TypeError, ValueError):
        return None
    return db.get(CompareHistory, pk)


def list_records(
    db: Session,
    *,
    rule_id: str | None = None,
    result: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(CompareHistory)
    count_stmt = select(func.count(CompareHistory.id))
    if rule_id is not None:
        stmt = stmt.where(CompareHistory.rule_id == rule_id)
        count_stmt = count_stmt.where(CompareHistory.rule_id == rule_id)
    if result is not None:
        stmt = stmt.where(CompareHistory.result == result)
        count_stmt = count_stmt.where(CompareHistory.result == result)

    total = int(db.scalar(count_stmt) or 0)
    rows = db.scalars(
        stmt.order_by(CompareHistory.id.desc()).limit(limit).offset(offset)
    ).all()
    return {"total": total, "items": [to_api_dict(r) for r in rows]}


def clear(db: Session) -> int:
    n = db.query(CompareHistory).delete(synchronize_session=False)
    db.flush()
    return int(n)


def to_api_dict(row: CompareHistory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "rule_id": row.rule_id,
        "rule_name": row.rule_name or "",
        "project_key": row.project_key,
        "project_name": row.project_name,
        "fields": row.fields or [],
        "result": bool(row.result),
        "reason": row.reason or "",
        "time_ms": row.time_ms,
        "cost": row.cost or {},
        "raw_request": row.raw_request,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_repo_history.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add app/db/repo_history.py tests/unit/test_repo_history.py
git commit -m "feat(db): 历史仓储

对外 id 保持字符串：内存版用 uuid[:8]，改成数字会波及
前端历史页的详情展开逻辑，没必要为内部主键类型动 UI。
非数字 id 返 None 而非抛异常——前端传脏值不该 500。"
```

---

## Task 9: Pydantic 模型抽出到 schemas

把 `RuleIn` 从 `app/api/admin_rules.py` 搬到 `app/schemas/rules.py`，并新增项目相关模型。**动机是避免循环依赖**：`core/rules_sync.py` 导入时要复用规则校验，而 `admin_rules.py`（API 层）反过来要用 `rules_sync`。

**Files:**
- Create: `app/schemas/rules.py`
- Modify: `app/api/admin_rules.py:33-91`（删掉 `_FIELD_ID_RE`、`RuleIn`、`RuleOut`，改为 import）
- Test: `tests/unit/test_schemas_rules.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_schemas_rules.py`:

```python
"""规则与项目的 Pydantic 模型。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.rules import ProjectIn, RuleIn, RuleIdsIn


BASE = {"id": "R001", "prompt_template": "A:{field_a}"}


def test_rule_minimal():
    r = RuleIn(**BASE)
    assert r.id == "R001"
    assert r.require_field_name is False
    assert r.project is None


def test_rule_accepts_project_key():
    assert RuleIn(**BASE, project="zf").project == "zf"


def test_rule_project_none_is_valid():
    assert RuleIn(**BASE, project=None).project is None


@pytest.mark.parametrize("bad", [
    ["建设单位A"], [""], ["has space"], ["1abc"], ["a-b"],
    ["same", "same"], ["rule_id"],
])
def test_invalid_field_defs(bad):
    with pytest.raises(ValidationError):
        RuleIn(**BASE, field_defs=bad)


@pytest.mark.parametrize("ok", [["a", "b"], ["_x"], ["A1"], []])
def test_valid_field_defs(ok):
    RuleIn(**BASE, field_defs=ok)


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------

def test_project_minimal():
    p = ProjectIn(key="zf", name="政府投资")
    assert p.description == ""


@pytest.mark.parametrize("bad", ["政府投资", "", "has space", "1abc", "a-b"])
def test_invalid_project_key(bad):
    """key 与 field_defs 用同一套正则，行为必须一致。"""
    with pytest.raises(ValidationError):
        ProjectIn(key=bad, name="x")


@pytest.mark.parametrize("ok", ["zf", "_x", "A1", "zhengfu_touzi"])
def test_valid_project_key(ok):
    ProjectIn(key=ok, name="x")


def test_project_name_required():
    with pytest.raises(ValidationError):
        ProjectIn(key="zf", name="")


def test_rule_ids_in():
    assert RuleIdsIn(rule_ids=["a", "b"]).rule_ids == ["a", "b"]


def test_rule_ids_rejects_empty():
    with pytest.raises(ValidationError):
        RuleIdsIn(rule_ids=[])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_schemas_rules.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.schemas.rules'`

- [ ] **Step 3: 实现**

Create `app/schemas/rules.py`:

```python
"""规则与项目的请求模型。

放在 schemas 层而非 ``api/admin_rules.py`` 里,是因为 ``core/rules_sync.py``
导入 yaml 时也要复用同一套校验;若模型留在 API 层,core 层反向 import 就成了
循环依赖。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# 标识符的合法形态。字段 ID 与项目 key 共用——两者都会成为对外的键/路径,
# 规则一致才不会让人记两套。
# 放模块级而非类体内:Pydantic v2 会把类体里以下划线开头的属性包成
# ModelPrivateAttr,validator 里再取就不是 Pattern 了。
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuleIn(BaseModel):
    """前端提交的规则结构。"""

    id: str = Field(..., min_length=1, description="规则唯一编号,全局唯一")
    project: str | None = Field(
        default=None,
        description="所属项目的 key。null 或省略表示未分组",
    )
    name: str | None = Field(default="", description="中文规则名(用于日志/展示)")
    description: str | None = Field(default="", description="规则描述,仅文档用")
    field_defs: list[str] | None = Field(
        default=None,
        description=(
            "自定义字段 ID 列表,决定本规则请求体里的字段键。"
            "不写/为空则用默认的 field_a/field_b/...。"
            "元素须匹配 ^[A-Za-z_][A-Za-z0-9_]*$、规则内不重复、不得为 rule_id"
        ),
    )
    require_field_name: bool = Field(
        default=False, description="是否要求请求里每个 field.name 都必填"
    )
    system_prompt: str | None = Field(
        default="", description="LLM 系统提示。留空则不向模型发送 system 消息"
    )
    prompt_template: str = Field(
        ...,
        min_length=1,
        description="prompt 模板,占位符与字段键同名:{field_a} 或 {keyan_name}",
    )

    @field_validator("field_defs")
    @classmethod
    def _check_field_defs(cls, v: list[str] | None) -> list[str] | None:
        """字段 ID 会直接成为请求体的 JSON 键,必须是合法标识符且不撞保留字。"""
        if not v:
            return v
        seen: set[str] = set()
        for item in v:
            if not IDENT_RE.match(item or ""):
                raise ValueError(
                    f"field_defs 元素非法: {item!r}，"
                    "须匹配 ^[A-Za-z_][A-Za-z0-9_]*$（字母或下划线开头，只含字母数字下划线）"
                )
            if item == "rule_id":
                raise ValueError("field_defs 不能包含 rule_id，它是请求体顶层保留字")
            if item in seen:
                raise ValueError(f"field_defs 元素重复: {item!r}")
            seen.add(item)
        return v


class RuleOut(RuleIn):
    """读取返回的规则结构,字段同 RuleIn。"""


class ProjectIn(BaseModel):
    """前端提交的项目结构。"""

    key: str = Field(..., min_length=1, description="项目稳定标识,yaml 导入导出引用它")
    name: str = Field(..., min_length=1, description="项目中文名")
    description: str = Field(default="", description="项目描述")

    @field_validator("key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        if not IDENT_RE.match(v or ""):
            raise ValueError(
                f"项目 key 非法: {v!r}，"
                "须匹配 ^[A-Za-z_][A-Za-z0-9_]*$（字母或下划线开头，只含字母数字下划线）"
            )
        return v


class RuleIdsIn(BaseModel):
    """批量移入/移出的请求体。"""

    rule_ids: list[str] = Field(..., min_length=1, description="规则 ID 列表")
```

- [ ] **Step 4: 改 `admin_rules.py` 引用**

删除 `app/api/admin_rules.py` 第 33-35 行的 `_FIELD_ID_RE` 定义、第 38-91 行的 `RuleIn` / `RuleOut` 类定义，并把 import 区改为：

```python
from app.core.config import PROJECT_ROOT, get_config, reload_rules
from app.core.rules_io import (
    find_rule_index,
    list_rules,
    load_rules_doc,
    normalize_rule,
    save_rules_doc,
)
from app.schemas.rules import RuleIn
```

（本任务只搬家不改行为，`admin_rules.py` 仍走 yaml，Task 12 才切 DB。
只导入 `RuleIn`：本模块的路由返回 `dict`，不引用 `RuleOut`，导入它会触发 F401。
`RuleOut` 定义留在 `schemas/rules.py` 供 Task 12 与文档引用。）

- [ ] **Step 5: 跑全量测试确认无回归**

Run: `uv run pytest tests/unit/test_schemas_rules.py tests/integration/test_admin_rules.py -v`
Expected: 全部 passed（新增 14 个 + 原有 admin_rules 用例仍绿）

- [ ] **Step 6: 提交**

```bash
git add app/schemas/rules.py app/api/admin_rules.py tests/unit/test_schemas_rules.py
git commit -m "refactor(schemas): RuleIn 搬到 schemas 层，新增 ProjectIn

core/rules_sync 导入 yaml 时要复用规则校验，模型若留在 API 层
就成了 core 反向 import api 的循环依赖。
项目 key 与 field_defs 共用 IDENT_RE，两者都会成为对外的键/路径，
规则一致才不用记两套。"
```

---

## Task 10: yaml 序列化与解析

`rules_io.py` 职责转向：从"yaml 是真源"的读写器，变成 yaml ↔ dict 的纯函数。本任务**只新增**，旧函数留给 `admin_rules.py` 用，Task 12 切换后再删。

**Files:**
- Modify: `app/core/rules_io.py`
- Test: `tests/unit/test_rules_io.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_rules_io.py`:

```python
"""yaml ↔ dict 纯转换。不碰 DB、不碰文件系统。"""

from __future__ import annotations

import pytest
import yaml as pyyaml

from app.core.rules_io import BundleParseError, dump_bundle, parse_bundle


PROJECTS = [{"key": "zf", "name": "政府投资", "description": "d"}]
RULES = [
    {
        "id": "R001",
        "project": "zf",
        "name": "总投资一致",
        "description": "d",
        "require_field_name": False,
        "system_prompt": "你是会计师。",
        "prompt_template": "请判断:\nA: {field_a}\nB: {field_b}\n",
    }
]


def test_dump_has_header_comment():
    text = dump_bundle(PROJECTS, RULES)
    assert text.startswith("#")
    assert "导入语义" in text


def test_dump_is_valid_yaml():
    data = pyyaml.safe_load(dump_bundle(PROJECTS, RULES))
    assert data["projects"][0]["key"] == "zf"
    assert data["rules"][0]["id"] == "R001"


def test_multiline_prompt_uses_block_style():
    """几百字的 prompt 挤成一行带 \\n 的字符串就没法看也没法改。"""
    text = dump_bundle([], RULES)
    assert "prompt_template: |" in text
    assert "\\n" not in text


def test_project_key_follows_id():
    """字段顺序:project 紧跟 id，视觉上归属信息在最前面。"""
    text = dump_bundle([], RULES)
    assert text.index("id: R001") < text.index("project: zf")
    assert text.index("project: zf") < text.index("name:")


def test_unassigned_rule_omits_project_key():
    """未分组不该写成 project: null。"""
    text = dump_bundle([], [dict(RULES[0], project=None)])
    assert "project:" not in text


def test_empty_projects_omits_node():
    assert "projects:" not in dump_bundle([], RULES)


def test_roundtrip():
    projects, rules = parse_bundle(dump_bundle(PROJECTS, RULES))
    assert projects == PROJECTS
    assert rules[0]["id"] == "R001"
    assert rules[0]["project"] == "zf"
    assert rules[0]["prompt_template"] == RULES[0]["prompt_template"]


def test_parse_legacy_file_without_projects():
    """现有 rules.yaml 没有 projects 节点、没有 project 字段，必须能直接导入。"""
    legacy = """
rules:
  - id: cross_rule_01
    name: 总投资一致
    prompt_template: "A:{field_a}"
"""
    projects, rules = parse_bundle(legacy)
    assert projects == []
    assert rules[0]["id"] == "cross_rule_01"
    assert "project" not in rules[0]


def test_parse_empty_text():
    assert parse_bundle("") == ([], [])


def test_parse_syntax_error_reports_line():
    bad = "rules:\n  - id: R001\n   bad_indent: x\n"
    with pytest.raises(BundleParseError) as ei:
        parse_bundle(bad)
    assert "第 3 行" in str(ei.value)


def test_parse_rejects_non_mapping_root():
    with pytest.raises(BundleParseError, match="顶层"):
        parse_bundle("- just\n- a\n- list\n")


def test_parse_rejects_non_list_rules():
    with pytest.raises(BundleParseError, match="rules"):
        parse_bundle("rules: not-a-list\n")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_rules_io.py -v`
Expected: FAIL，`ImportError: cannot import name 'BundleParseError'`

- [ ] **Step 3: 实现**

改 `app/core/rules_io.py`。模块 docstring 换成：

```python
"""rules.yaml 的序列化与解析,使用 ruamel.yaml round-trip 模式。

自规则落库 MySQL 后,本模块的职责是 **yaml ↔ plain dict 的纯转换**,
服务于导入导出;不再是运行时真源的读写器。

为什么仍用 ruamel 而不是 PyYAML:导出时 ``prompt_template`` 必须是 ``|``
块格式,否则几百字的 prompt 会挤成一行带 ``\\n`` 的字符串,既没法看也没法改。
"""
```

`normalize_rule` 的 `order` 列表在 `id` 之后插入 `project`：

```python
    order = [
        "id", "project", "name", "description", "field_defs",
        "require_field_name", "system_prompt", "prompt_template",
    ]
```

文件尾部追加：

```python
# ---------------------------------------------------------------------------
# 导入导出:yaml ↔ dict 纯函数
# ---------------------------------------------------------------------------

_EXPORT_HEADER = """\
# AI交叉校验 规则导出
#
# 导入方式: Web UI「导入」按钮,或 POST /admin/import
# 导入语义: 项目按 key、规则按 id 合并 upsert——库里有则覆盖,没有则新增;
#           库里有而本文件没有的,原封不动。因此可以只导入几条规则的片段文件。
#           任何一条规则校验不过则整体回滚,不会留下导入一半的中间状态。
#
# projects 节点(可选)
#   key           项目稳定标识,规则的 project 字段引用它。跨环境可移植
#   name          项目中文名
#   description   项目描述
#
# rules 节点
#   id                     规则唯一标识(上游传入的 rule_id),全局唯一
#   project                所属项目的 key。省略即未分组。引用未定义的项目
#                          会告警并置为未分组,不中断导入
#   name                   规则名称(仅用于日志/文档)
#   description            规则描述(仅用于日志/文档)
#   field_defs             自定义字段 ID 列表,决定请求体里的字段键。
#                          省略/为空则用默认的 field_a/field_b/...
#   require_field_name     bool。是否要求请求里每个 field.name 必填
#   system_prompt          LLM 系统提示。留空则不发送 system 消息
#   prompt_template        Prompt 模板。占位符与字段键一一对应:
#                            {field_a} {field_b} ...        字段值
#                            {field_a_name} {field_b_name}  字段名
#                          注意:JSON 示例里的大括号要写成双重 {{ }}
#                          注意:match 必须让模型输出 true|false,不能输出中文
#
# 规则在库中的排序按本文件的先后顺序追加;已存在的规则不改变原有位置。
"""


class BundleParseError(ValueError):
    """yaml 解析失败。消息里带行号,直接给用户看。"""


def dump_bundle(
    projects: list[dict[str, Any]], rules: list[dict[str, Any]]
) -> str:
    """把项目与规则序列化成带注释头的 yaml 文本。

    ``projects`` 为空时不写 ``projects:`` 节点;规则的 ``project`` 为空时
    不写该键——避免满屏的 ``project: null``。
    """
    from io import StringIO

    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    doc = CommentedMap()

    if projects:
        pseq = CommentedSeq()
        for p in projects:
            node = CommentedMap()
            node["key"] = p["key"]
            node["name"] = p.get("name", "")
            if p.get("description"):
                node["description"] = p["description"]
            pseq.append(node)
        doc["projects"] = pseq

    rseq = CommentedSeq()
    for r in rules:
        clean = {k: v for k, v in r.items() if not (k == "project" and not v)}
        rseq.append(normalize_rule(clean))
    doc["rules"] = rseq

    buf = StringIO()
    _yaml.dump(doc, buf)
    return _EXPORT_HEADER + "\n" + buf.getvalue()


def parse_bundle(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 yaml 文本,返回 ``(projects, rules)`` 两个 plain dict 列表。

    兼容没有 ``projects:`` 节点的旧格式——现有 ``config/rules.yaml`` 就是。
    """
    from ruamel.yaml.error import MarkedYAMLError

    if not (text or "").strip():
        return [], []

    try:
        data = _yaml.load(text)
    except MarkedYAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"第 {mark.line + 1} 行" if mark is not None else "位置未知"
        raise BundleParseError(
            f"YAML 语法错误({where}): {getattr(e, 'problem', e)}"
        ) from e

    if data is None:
        return [], []
    if not isinstance(data, dict):
        raise BundleParseError("YAML 顶层必须是映射,应含 rules 节点")

    raw_projects = data.get("projects") or []
    if not isinstance(raw_projects, list):
        raise BundleParseError("projects 节点必须是列表")

    raw_rules = data.get("rules")
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise BundleParseError("rules 节点必须是列表")

    return (
        [_plain(p) for p in raw_projects],
        [_plain(r) for r in raw_rules],
    )


def _plain(node: Any) -> dict[str, Any]:
    """ruamel CommentedMap → 普通 dict(值里的 list 也一并转)。"""
    if not isinstance(node, dict):
        raise BundleParseError(f"列表元素必须是映射,收到 {type(node).__name__}")
    out: dict[str, Any] = {}
    for k, v in node.items():
        out[str(k)] = list(v) if isinstance(v, list) else v
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_rules_io.py -v`
Expected: 12 passed

- [ ] **Step 5: 确认旧路径未回归**

Run: `uv run pytest tests/integration/test_admin_rules.py -v`
Expected: 全部 passed（`normalize_rule` 只是多认一个键，旧调用不传 `project` 无影响）

- [ ] **Step 6: 提交**

```bash
git add app/core/rules_io.py tests/unit/test_rules_io.py
git commit -m "feat(rules-io): 新增 dump_bundle / parse_bundle 纯函数

- 导出带固定头部注释：落库后原 rules.yaml 那 27 行文档不在 DB 里，
  由代码生成同源的一份
- 仍用 ruamel 而非 PyYAML：prompt_template 必须是 | 块格式，
  否则几百字 prompt 挤成一行带 \\n 的字符串，没法看也没法改
- project 为空时不写该键，避免满屏 project: null
- 兼容无 projects 节点的旧格式，现有 rules.yaml 可直接导入"
```

---

## Task 11: 导入导出编排

**Files:**
- Create: `app/core/rules_sync.py`
- Test: `tests/unit/test_rules_sync.py`

- [ ] **Step 1: 写失败测试**

Create `tests/unit/test_rules_sync.py`:

```python
"""导入导出编排。事务与校验的边界在这里。"""

from __future__ import annotations

import pytest

from app.core import rules_sync
from app.core.rules_io import parse_bundle
from app.db import repo_projects, repo_rules
from app.db.models import Rule


BUNDLE = """
projects:
  - key: zf
    name: 政府投资
    description: 政府投资项目审核

rules:
  - id: R001
    project: zf
    name: 总投资一致
    prompt_template: "A:{field_a} B:{field_b}"
  - id: R002
    name: 未分组的
    prompt_template: "A:{field_a}"
"""


def test_import_creates_projects_and_rules(db):
    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.projects_created == 1
    assert res.rules_created == 2
    assert res.rules_updated == 0
    assert repo_projects.get_by_key(db, "zf") is not None
    assert repo_rules.get_by_rule_id(db, "R001").project.key == "zf"
    assert repo_rules.get_by_rule_id(db, "R002").project_id is None


def test_import_is_upsert_not_replace(db):
    """库里有而文件里没有的规则必须原封不动。"""
    repo_rules.create(db, {"id": "KEEP", "prompt_template": "t"})
    db.commit()

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "KEEP") is not None
    assert repo_rules.count(db) == 3


def test_import_updates_existing_rule(db):
    repo_rules.create(db, {"id": "R001", "name": "旧名", "prompt_template": "old"})
    db.commit()

    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.rules_created == 1
    assert res.rules_updated == 1
    assert repo_rules.get_by_rule_id(db, "R001").name == "总投资一致"


def test_import_preserves_existing_sort_order(db):
    """导入片段不能打乱现有顺序。"""
    repo_rules.create(db, {"id": "R001", "prompt_template": "t"})
    repo_rules.create(db, {"id": "ZZZ", "prompt_template": "t"})
    db.commit()
    before = repo_rules.get_by_rule_id(db, "R001").sort_order

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "R001").sort_order == before
    assert [r.rule_id for r in repo_rules.list_rules(db)] == ["R001", "ZZZ", "R002"]


def test_import_updates_existing_project(db):
    repo_projects.create(db, key="zf", name="旧名")
    db.commit()

    res = rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    assert res.projects_created == 0
    assert res.projects_updated == 1
    assert repo_projects.get_by_key(db, "zf").name == "政府投资"


def test_undefined_project_warns_and_unassigns(db):
    text = """
rules:
  - id: R001
    project: ghost
    prompt_template: "A:{field_a}"
"""
    res = rules_sync.import_bundle(db, text)
    db.commit()

    assert repo_rules.get_by_rule_id(db, "R001").project_id is None
    assert len(res.warnings) == 1
    assert "ghost" in res.warnings[0]


def test_existing_project_in_db_counts_as_defined(db):
    """文件没定义但库里已有同 key 项目 → 直接关联，不算警告。"""
    repo_projects.create(db, key="zf", name="政府投资")
    db.commit()

    text = """
rules:
  - id: R001
    project: zf
    prompt_template: "A:{field_a}"
"""
    res = rules_sync.import_bundle(db, text)
    db.commit()

    assert res.warnings == []
    assert repo_rules.get_by_rule_id(db, "R001").project.key == "zf"


# ---------------------------------------------------------------------------
# 校验与回滚
# ---------------------------------------------------------------------------

def test_invalid_rule_raises_before_any_write(db):
    """一条非法 → 整体不落地。校验必须跑在所有写操作之前。"""
    text = """
rules:
  - id: GOOD
    prompt_template: "A:{field_a}"
  - id: BAD
    prompt_template: ""
"""
    with pytest.raises(rules_sync.BundleValidationError) as ei:
        rules_sync.import_bundle(db, text)

    assert "第 2 条" in str(ei.value)
    assert db.query(Rule).count() == 0


def test_invalid_field_defs_rejected(db):
    text = """
rules:
  - id: R001
    field_defs: ["建设单位A"]
    prompt_template: "A:{field_a}"
"""
    with pytest.raises(rules_sync.BundleValidationError):
        rules_sync.import_bundle(db, text)


def test_invalid_project_key_rejected(db):
    text = """
projects:
  - key: "有空格 key"
    name: x
rules: []
"""
    with pytest.raises(rules_sync.BundleValidationError, match="第 1 个项目"):
        rules_sync.import_bundle(db, text)


def test_duplicate_rule_id_in_file_rejected(db):
    text = """
rules:
  - id: DUP
    prompt_template: "a"
  - id: DUP
    prompt_template: "b"
"""
    with pytest.raises(rules_sync.BundleValidationError, match="重复"):
        rules_sync.import_bundle(db, text)


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def test_export_roundtrip(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    text = rules_sync.export_bundle(db)
    projects, rules = parse_bundle(text)

    assert [p["key"] for p in projects] == ["zf"]
    assert [r["id"] for r in rules] == ["R001", "R002"]
    assert rules[0]["project"] == "zf"
    assert "project" not in rules[1]


def test_export_reimport_into_empty_db_restores_everything(db, engine):
    """导出→导入必须完整还原，否则备份没有意义。"""
    from sqlalchemy.orm import sessionmaker

    rules_sync.import_bundle(db, BUNDLE)
    db.commit()
    text = rules_sync.export_bundle(db)

    from app.db.models import Base
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    fresh = sessionmaker(bind=engine)()

    rules_sync.import_bundle(fresh, text)
    fresh.commit()

    assert repo_rules.get_by_rule_id(fresh, "R001").project.key == "zf"
    assert repo_rules.get_by_rule_id(fresh, "R002").project_id is None
    fresh.close()


def test_export_filtered_by_project(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    projects, rules = parse_bundle(rules_sync.export_bundle(db, project_key="zf"))
    assert [r["id"] for r in rules] == ["R001"]
    assert [p["key"] for p in projects] == ["zf"]


def test_export_unassigned_only(db):
    rules_sync.import_bundle(db, BUNDLE)
    db.commit()

    projects, rules = parse_bundle(rules_sync.export_bundle(db, unassigned=True))
    assert [r["id"] for r in rules] == ["R002"]
    assert projects == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/test_rules_sync.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.core.rules_sync'`

- [ ] **Step 3: 实现**

Create `app/core/rules_sync.py`:

```python
"""导入导出编排:解析 → 校验 → 事务内 upsert。

事务边界:本模块只 ``flush`` 不 ``commit``。提交或回滚由调用方(路由)决定,
这样「全文件单事务」才成立——校验失败时路由 rollback,库里什么都不会留下。

校验一律跑在所有写操作**之前**。若边写边校验,第 5 条出错时前 4 条已经
flush 进会话,虽然最终会回滚,但错误定位和资源消耗都没必要。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.rules_io import dump_bundle, parse_bundle
from app.db import repo_projects, repo_rules
from app.schemas.rules import ProjectIn, RuleIn

log = logging.getLogger("aicheck.rules_sync")


class BundleValidationError(ValueError):
    """导入内容校验失败。消息里指明是第几条、错在哪。"""


@dataclass
class ImportResult:
    projects_created: int = 0
    projects_updated: int = 0
    rules_created: int = 0
    rules_updated: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "projects": {
                "created": self.projects_created,
                "updated": self.projects_updated,
            },
            "rules": {
                "created": self.rules_created,
                "updated": self.rules_updated,
            },
            "warnings": self.warnings,
        }


def _validate(
    raw_projects: list[dict[str, Any]], raw_rules: list[dict[str, Any]]
) -> tuple[list[ProjectIn], list[RuleIn]]:
    projects: list[ProjectIn] = []
    for i, p in enumerate(raw_projects, start=1):
        try:
            projects.append(ProjectIn(**p))
        except (ValidationError, TypeError) as e:
            raise BundleValidationError(f"第 {i} 个项目校验失败: {e}") from e

    seen_keys: set[str] = set()
    for p in projects:
        if p.key in seen_keys:
            raise BundleValidationError(f"文件内项目 key 重复: {p.key}")
        seen_keys.add(p.key)

    rules: list[RuleIn] = []
    for i, r in enumerate(raw_rules, start=1):
        try:
            rules.append(RuleIn(**r))
        except (ValidationError, TypeError) as e:
            raise BundleValidationError(f"第 {i} 条规则校验失败: {e}") from e

    seen_ids: set[str] = set()
    for r in rules:
        if r.id in seen_ids:
            raise BundleValidationError(f"文件内规则 id 重复: {r.id}")
        seen_ids.add(r.id)

    return projects, rules


def import_bundle(db: Session, text: str) -> ImportResult:
    """合并 upsert。**不提交**,由调用方 commit / rollback。"""
    raw_projects, raw_rules = parse_bundle(text)
    projects, rules = _validate(raw_projects, raw_rules)

    res = ImportResult()

    # 项目按 key upsert
    for p in projects:
        existing = repo_projects.get_by_key(db, p.key)
        if existing is None:
            repo_projects.create(db, key=p.key, name=p.name, description=p.description)
            res.projects_created += 1
        else:
            repo_projects.update(
                db, existing, name=p.name, description=p.description
            )
            res.projects_updated += 1

    # 规则按 id upsert
    for r in rules:
        data = r.model_dump(exclude_none=True)
        data.pop("project", None)

        project_id: int | None = None
        if r.project:
            target = repo_projects.get_by_key(db, r.project)
            if target is None:
                res.warnings.append(
                    f"规则 {r.id} 引用的项目 {r.project} 未在文件中定义,已置为未分组"
                )
            else:
                project_id = target.id

        existing = repo_rules.get_by_rule_id(db, r.id)
        if existing is None:
            repo_rules.create(db, data, project_id=project_id)
            res.rules_created += 1
        else:
            repo_rules.update(
                db, existing, data, project_id=project_id, set_project=True
            )
            res.rules_updated += 1

    log.info(
        "导入完成 项目 +%d/~%d 规则 +%d/~%d 警告 %d",
        res.projects_created, res.projects_updated,
        res.rules_created, res.rules_updated, len(res.warnings),
    )
    return res


def export_bundle(
    db: Session,
    *,
    project_key: str | None = None,
    unassigned: bool = False,
) -> str:
    """导出 yaml 文本。按项目导出时 ``projects:`` 只含该项目。"""
    rules = repo_rules.list_rules(db, project_key=project_key, unassigned=unassigned)

    if unassigned:
        projects = []
    elif project_key:
        one = repo_projects.get_by_key(db, project_key)
        projects = [one] if one is not None else []
    else:
        projects = repo_projects.list_projects(db)

    project_dicts = [
        {"key": p.key, "name": p.name, "description": p.description or ""}
        for p in projects
    ]
    rule_dicts = []
    for r in rules:
        d = repo_rules.to_rule_cfg(r)
        if r.project is not None:
            d = {"id": d["id"], "project": r.project.key, **{k: v for k, v in d.items() if k != "id"}}
        rule_dicts.append(d)

    return dump_bundle(project_dicts, rule_dicts)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/test_rules_sync.py -v`
Expected: 15 passed

- [ ] **Step 5: 提交**

```bash
git add app/core/rules_sync.py tests/unit/test_rules_sync.py
git commit -m "feat(rules-sync): 导入导出编排

- 只 flush 不 commit，事务边界交给路由，「全文件单事务」才成立
- 校验全部跑在写操作之前：边写边校验时第 5 条出错前 4 条已进会话，
  虽然终会回滚但错误定位和开销都没必要
- 引用未定义项目 → 警告 + 置未分组，不中断
- 已存在规则的 sort_order 不被覆盖，导入片段不打乱现有顺序"
```

### 实施记录（2026-08-24）

commit 见分支历史。测试 **15 条**（计划原写「16」是数错，实际 15，全绿）。

**修掉计划代码里的两处 mypy 报错（会挂 `mypy app` 门禁）：**

1. `import_bundle` 的项目循环与规则循环复用了同名变量 `existing`——前者 `Project | None`、后者 `Rule | None`，mypy 判类型冲突并级联出 10 处错。已拆成 `existing_project` / `existing_rule`。
2. `_validate` 里 `p` / `r` 在「dict 循环」（`enumerate(raw_projects)`，元素是 dict）与「模型循环」（`for p in projects`，元素是 ProjectIn/RuleIn）间复用，mypy 把 `.key` / `.id` 判成 dict 无此属性。dict 循环改用 `raw_p` / `raw_r`。

两处都是「同名变量在同一函数内承载两种类型」——mypy 只按首次赋值推断。计划代码显然没跑过 `mypy app`。

**事务保证做了缺陷注入验证：** 在 `_validate` 之前故意写一条 GOOD 规则，`test_invalid_rule_raises_before_any_write` 的 `count == 0` 立即变红——证明这条断言确实咬住了「校验先于任何写入」，而非摆设。这条保证的结构支撑是：写循环消费的是 `_validate` 返回的已校验对象，物理上无法「先写后校验」，除非有人蓄意插入旁路写入（正是注入的那种）。

---

## Task 12: 规则接口切到 DB

**Files:**
- Modify: `app/api/admin_rules.py`（整体重写路由部分）
- Modify: `app/core/rules_io.py`（删掉 yaml 真源时代的四个函数）
- Rewrite: `tests/integration/test_admin_rules.py`

- [ ] **Step 1: 重写集成测试**

覆盖 `tests/integration/test_admin_rules.py` 全文：

```python
"""``/admin/rules`` CRUD 集成测试。数据源已从 yaml 切到 DB。

夹具见 tests/conftest.py:SQLite in-memory + 依赖覆盖,不碰真实 MySQL。
"""

from __future__ import annotations

import pytest

from app.db import repo_projects, repo_rules
from app.rules import llm_rule


NEW_RULE = {
    "id": "R999",
    "name": "测试规则",
    "description": "集成测试用",
    "require_field_name": False,
    "system_prompt": "你是测试专家。",
    "prompt_template": 'A:{field_a} B:{field_b} JSON:{{"match":true,"reason":""}}',
}


@pytest.fixture
def seeded(db):
    """预置 5 条规则 R001-R005，R004 带 field_defs。"""
    for i in range(1, 6):
        data = {
            "id": f"R{i:03d}",
            "name": f"规则{i}",
            "prompt_template": "A:{field_a} B:{field_b}",
        }
        if i == 4:
            data["field_defs"] = ["danwei_shenbao", "danwei_pifu"]
            data["require_field_name"] = True
            data["prompt_template"] = "{danwei_shenbao_name} {danwei_pifu}"
        repo_rules.create(db, data)
    db.commit()


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------

def test_list_rules_returns_5(client, seeded):
    body = client.get("/admin/rules").json()
    assert body["count"] == 5
    assert [r["id"] for r in body["rules"]] == [
        "R001", "R002", "R003", "R004", "R005"
    ]


def test_get_single_rule(client, seeded):
    body = client.get("/admin/rules/R004").json()
    assert body["id"] == "R004"
    assert body["require_field_name"] is True
    assert body["field_defs"] == ["danwei_shenbao", "danwei_pifu"]


def test_get_unknown_rule_returns_404(client, seeded):
    assert client.get("/admin/rules/RXXX").status_code == 404


def test_every_rule_carries_project_field(client, seeded):
    for r in client.get("/admin/rules").json()["rules"]:
        assert "project" in r
        assert r["project"] is None


# ---------------------------------------------------------------------------
# 项目筛选
# ---------------------------------------------------------------------------

@pytest.fixture
def with_project(db, seeded):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    repo_rules.assign_project(db, ["R001", "R002"], p.id)
    db.commit()
    return p


def test_filter_by_project(client, with_project):
    body = client.get("/admin/rules?project=zf").json()
    assert [r["id"] for r in body["rules"]] == ["R001", "R002"]
    assert body["rules"][0]["project"] == {"key": "zf", "name": "政府投资"}


def test_filter_unassigned(client, with_project):
    body = client.get("/admin/rules?project=__none__").json()
    assert [r["id"] for r in body["rules"]] == ["R003", "R004", "R005"]


def test_filter_unknown_project_returns_empty(client, with_project):
    assert client.get("/admin/rules?project=ghost").json()["count"] == 0


# ---------------------------------------------------------------------------
# 增
# ---------------------------------------------------------------------------

def test_create_rule(client, seeded):
    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201
    assert resp.json()["rule_id"] == "R999"
    assert resp.json()["rules_loaded"] == 6
    assert any(r["id"] == "R999" for r in client.get("/admin/rules").json()["rules"])


def test_create_with_project(client, with_project):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, project="zf"))
    assert resp.status_code == 201
    assert client.get("/admin/rules/R999").json()["project"]["key"] == "zf"


def test_create_with_unknown_project_returns_422(client, seeded):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, project="ghost"))
    assert resp.status_code == 422
    assert "ghost" in resp.json()["detail"]


def test_create_duplicate_id_returns_422(client, seeded):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, id="R001"))
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


def test_create_missing_prompt_template_returns_422(client, seeded):
    payload = dict(NEW_RULE)
    payload.pop("prompt_template")
    assert client.post("/admin/rules", json=payload).status_code == 422


# ---------------------------------------------------------------------------
# 改
# ---------------------------------------------------------------------------

def test_update_rule(client, seeded):
    payload = dict(NEW_RULE, id="R001", name="改后名字",
                   prompt_template="新模板 A:{field_a} B:{field_b}")
    assert client.put("/admin/rules/R001", json=payload).status_code == 200

    after = client.get("/admin/rules/R001").json()
    assert after["name"] == "改后名字"
    assert "新模板" in after["prompt_template"]


def test_update_keeps_list_position(client, seeded):
    """改内容不该把规则挪到列表末尾。"""
    client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R001", name="改后"))
    assert client.get("/admin/rules").json()["rules"][0]["id"] == "R001"


def test_update_can_change_project(client, with_project):
    client.put("/admin/rules/R003", json=dict(NEW_RULE, id="R003", project="zf"))
    assert client.get("/admin/rules/R003").json()["project"]["key"] == "zf"


def test_update_can_clear_project(client, with_project):
    client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R001", project=None))
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_update_id_clash_returns_422(client, seeded):
    resp = client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R002"))
    assert resp.status_code == 422
    assert "已被占用" in resp.json()["detail"]


def test_update_with_rename(client, seeded):
    resp = client.put("/admin/rules/R001", json=dict(NEW_RULE, id="R099", name="重命名后"))
    assert resp.status_code == 200
    assert client.get("/admin/rules/R001").status_code == 404
    assert client.get("/admin/rules/R099").json()["name"] == "重命名后"


def test_update_unknown_returns_404(client, seeded):
    assert client.put("/admin/rules/RNONE", json=dict(NEW_RULE, id="RNONE")).status_code == 404


# ---------------------------------------------------------------------------
# 删
# ---------------------------------------------------------------------------

def test_delete_rule(client, seeded):
    resp = client.delete("/admin/rules/R005")
    assert resp.status_code == 200
    assert resp.json()["rules_loaded"] == 4
    assert client.get("/admin/rules/R005").status_code == 404


def test_delete_unknown_returns_404(client, seeded):
    assert client.delete("/admin/rules/RXXX").status_code == 404


# ---------------------------------------------------------------------------
# field_defs
# ---------------------------------------------------------------------------

def test_create_rule_with_field_defs(client, seeded):
    payload = dict(NEW_RULE, field_defs=["shenbao", "pifu", "hetong"])
    assert client.post("/admin/rules", json=payload).status_code == 201
    assert client.get("/admin/rules/R999").json()["field_defs"] == [
        "shenbao", "pifu", "hetong"
    ]


def test_create_rule_without_field_defs_omits_key(client, seeded):
    """不带 field_defs 时响应里不应出现该键。"""
    client.post("/admin/rules", json=NEW_RULE)
    assert "field_defs" not in client.get("/admin/rules/R999").json()


def test_update_rule_field_defs(client, seeded):
    client.post("/admin/rules", json=dict(NEW_RULE, field_defs=["jia", "yi"]))
    client.put("/admin/rules/R999", json=dict(NEW_RULE, field_defs=["jia", "yi", "bing"]))
    assert client.get("/admin/rules/R999").json()["field_defs"] == ["jia", "yi", "bing"]


@pytest.mark.parametrize("bad_defs, why", [
    (["建设单位A", "建设单位B"], "中文不能当 JSON 键"),
    (["", ""], "空串"),
    (["has space", "b"], "含空格"),
    (["1abc", "b"], "数字开头"),
    (["a-b", "c"], "含连字符"),
    (["same", "same"], "规则内重复"),
    (["rule_id", "b"], "占用顶层保留字"),
])
def test_invalid_field_defs_rejected(client, seeded, bad_defs, why):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=bad_defs))
    assert resp.status_code == 422, f"{why} 应被拒绝: {bad_defs}"


@pytest.mark.parametrize("good_defs", [
    ["keyan_name", "zixun_name"], ["_leading_underscore", "b"],
    ["a1", "b2"], ["ONE", "two"], [],
])
def test_valid_field_defs_accepted(client, seeded, good_defs):
    resp = client.post("/admin/rules", json=dict(NEW_RULE, field_defs=good_defs))
    assert resp.status_code in (200, 201), resp.text


# ---------------------------------------------------------------------------
# 写入后 /compare 立即生效
# ---------------------------------------------------------------------------

def test_create_then_compare_uses_new_rule(client, seeded, monkeypatch):
    captured: dict = {}

    def _fake(messages, **kwargs):
        captured["messages"] = messages
        return ('{"match": true, "reason": "ok"}',
                {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})

    monkeypatch.setattr(llm_rule, "chat_json", _fake)

    assert client.post("/admin/rules", json=NEW_RULE).status_code == 201

    resp = client.post("/compare", json={
        "rule_id": "R999",
        "field_a": {"value": "v1"},
        "field_b": {"value": "v2"},
    })
    assert resp.status_code == 200
    assert resp.json()["result"] is True
    assert "测试专家" in captured["messages"][0]["content"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_admin_rules.py -v`
Expected: 大量 FAIL（路由仍读 yaml，`project` 字段不存在）

- [ ] **Step 3: 重写路由**

覆盖 `app/api/admin_rules.py` 全文：

```python
"""规则 CRUD 后端,服务 UI 的增删改查。

数据流:
    UI <--HTTP--> 本模块 <--repo_rules--> MySQL

规则真源已是 MySQL。``config/rules.yaml`` 只在导入导出时参与,见
``app/api/admin_import_export.py``。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import repo_projects, repo_rules
from app.db.session import get_db
from app.schemas.rules import RuleIn, RuleOut  # noqa: F401  (RuleOut 供文档引用)

router = APIRouter(tags=["admin-rules"])
log = logging.getLogger("aicheck.admin.rules")

UNASSIGNED = "__none__"


def _resolve_project_id(db: Session, key: str | None) -> int | None:
    """项目 key → id。key 为空返 None(未分组);key 不存在返 422。"""
    if not key:
        return None
    project = repo_projects.get_by_key(db, key)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"项目不存在: {key}",
        )
    return project.id


@router.get("/rules", summary="列出所有规则")
def list_all_rules(
    project: str | None = Query(
        None, description=f"按项目 key 筛选；传 {UNASSIGNED} 查未分组"
    ),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if project == UNASSIGNED:
        rows = repo_rules.list_rules(db, unassigned=True)
    else:
        rows = repo_rules.list_rules(db, project_key=project)
    items = [repo_rules.to_api_dict(r) for r in rows]
    return {"count": len(items), "rules": items}


@router.get("/rules/{rule_id}", summary="按 id 查询单条规则")
def get_single_rule(rule_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )
    return repo_rules.to_api_dict(row)


@router.post("/rules", summary="新增规则", status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if repo_rules.get_by_rule_id(db, payload.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"rule_id 已存在: {payload.id}",
        )

    project_id = _resolve_project_id(db, payload.project)
    data = payload.model_dump(exclude_none=True)
    data.pop("project", None)

    repo_rules.create(db, data, project_id=project_id)
    db.commit()

    n = repo_rules.count(db)
    log.info("新增规则 id=%s,当前共 %d 条", payload.id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.put("/rules/{rule_id}", summary="按 id 全量修改规则(支持 rename:body.id != url id)")
def update_rule(
    rule_id: str, payload: RuleIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    if payload.id != rule_id:
        clash = repo_rules.get_by_rule_id(db, payload.id)
        if clash is not None and clash.id != row.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"目标 id 已被占用: {payload.id}",
            )

    project_id = _resolve_project_id(db, payload.project)
    data = payload.model_dump(exclude_none=True)
    data.pop("project", None)

    repo_rules.update(db, row, data, project_id=project_id, set_project=True)
    db.commit()

    n = repo_rules.count(db)
    if payload.id != rule_id:
        log.info("更新规则 id=%s → %s,当前共 %d 条", rule_id, payload.id, n)
    else:
        log.info("更新规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.delete("/rules/{rule_id}", summary="按 id 删除规则")
def delete_rule(rule_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = repo_rules.get_by_rule_id(db, rule_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    repo_rules.delete(db, row)
    db.commit()

    n = repo_rules.count(db)
    log.info("删除规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": rule_id, "rules_loaded": n}
```

- [ ] **Step 4: 删掉 yaml 真源时代的函数**

从 `app/core/rules_io.py` 删除 `load_rules_doc`、`save_rules_doc`、`list_rules`、`find_rule_index` 四个函数，以及不再需要的 `_write_lock` 与 `threading` / `Path` import。保留 `_yaml`、`normalize_rule`、`_EXPORT_HEADER`、`BundleParseError`、`dump_bundle`、`parse_bundle`、`_plain`。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/integration/test_admin_rules.py tests/unit/test_conftest_wiring.py -v`
Expected: 全部 passed，且 `test_client_uses_same_session` 从 xfail 转为 **xpass 失败**（`strict=True` 会报错）

- [ ] **Step 6: 摘掉 xfail 标记**

删除 `tests/unit/test_conftest_wiring.py` 中 `test_client_uses_same_session` 上方的 `@pytest.mark.xfail(...)` 行。

Run: `uv run pytest tests/unit/test_conftest_wiring.py -v`
Expected: 4 passed

- [ ] **Step 7: 提交**

```bash
git add app/api/admin_rules.py app/core/rules_io.py tests/integration/test_admin_rules.py tests/unit/test_conftest_wiring.py
git commit -m "feat!: 规则 CRUD 切到 MySQL

- 响应新增嵌套 project 字段，未分组为 null
- 新增 ?project=<key> 筛选，__none__ 查未分组
- POST/PUT 接受 project 字段，引用不存在的项目返 422
- rules_loaded 字段保留，值改为 DB 中的规则总数
- 删掉 rules_io 里 yaml 真源时代的四个读写函数

BREAKING: 直接编辑 config/rules.yaml 不再自动生效，须走导入。"
```

### 实施记录（2026-08-24）

commit `185d68d`（切库）+ `b1844c3`（刷绿基线）。全量 226 passed, 1 xfailed。

**计划的测试文件里有一条跨任务依赖没标出来：** `test_create_then_compare_uses_new_rule` 建规则后调 `/compare`，但 `/compare` 的 `_find_rule_cfg` 到 Task 15 才切库，此刻仍走 `get_rules()` 读 yaml，看不见新建到测试 DB 的 R999。这条在 Task 12 无法通过。已按既有模式标 `@pytest.mark.xfail(strict=True)`，Task 15 切库后它会 xpass，strict 模式自动报错提醒摘标记——与 Task 5→12 的 `test_client_uses_same_session` 同一套自清理机制。计划 Step 5「全部 passed」的表述对这条不成立，应为「全部 passed + 1 xfailed」。

**顺带刷绿了全仓基线（单独 chore 提交）：** 切库后 `test_admin_rules.py` 重写、`rules_io.py` 清理，全仓只剩两个预存告警——`main.py` 未用的 `pathlib.Path`（F401）与 `config.py` 的 `import yaml`（mypy import-untyped，PyYAML 无 stub）。前者直接删；后者加 `[tool.mypy]` override 而非引 types-PyYAML 依赖（离线构建友好）。至此 `ruff check .` 与 `mypy app` 均全绿，Task 13-17 的全仓门禁才可信。

---

## Task 13: 项目接口

**Files:**
- Create: `app/api/admin_projects.py`
- Modify: `app/main.py:22`（import）、`app/main.py:63` 后（挂载路由）
- Test: `tests/integration/test_admin_projects.py`

- [ ] **Step 1: 写失败测试**

Create `tests/integration/test_admin_projects.py`:

```python
"""``/admin/projects`` CRUD 与批量移入移出。"""

from __future__ import annotations

import pytest

from app.db import repo_projects, repo_rules


NEW_PROJECT = {"key": "zf", "name": "政府投资", "description": "政府投资项目审核"}


@pytest.fixture
def seeded(db):
    for i in range(1, 4):
        repo_rules.create(db, {"id": f"R{i:03d}", "prompt_template": "t"})
    db.commit()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def test_list_empty(client):
    assert client.get("/admin/projects").json() == {"count": 0, "projects": []}


def test_create_and_list(client):
    assert client.post("/admin/projects", json=NEW_PROJECT).status_code == 201
    body = client.get("/admin/projects").json()
    assert body["count"] == 1
    assert body["projects"][0]["key"] == "zf"
    assert body["projects"][0]["rule_count"] == 0


def test_rule_count_reflects_assignment(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})
    assert client.get("/admin/projects").json()["projects"][0]["rule_count"] == 2


def test_get_single(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    assert client.get("/admin/projects/zf").json()["name"] == "政府投资"


def test_get_unknown_returns_404(client):
    assert client.get("/admin/projects/ghost").status_code == 404


def test_create_duplicate_key_returns_422(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects", json=NEW_PROJECT)
    assert resp.status_code == 422
    assert "已存在" in resp.json()["detail"]


@pytest.mark.parametrize("bad_key", ["政府投资", "has space", "1abc", "a-b"])
def test_create_illegal_key_returns_422(client, bad_key):
    assert client.post(
        "/admin/projects", json=dict(NEW_PROJECT, key=bad_key)
    ).status_code == 422


def test_update(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.put("/admin/projects/zf", json={
        "key": "zf", "name": "政府投资(改)", "description": "新描述"
    })
    assert resp.status_code == 200
    assert client.get("/admin/projects/zf").json()["name"] == "政府投资(改)"


def test_update_can_change_key_and_keeps_rules(client, seeded):
    """改 key 不影响归属——规则通过 project_id 关联。"""
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001"]})

    resp = client.put("/admin/projects/zf", json={"key": "zf2", "name": "政府投资"})
    assert resp.status_code == 200

    assert client.get("/admin/projects/zf").status_code == 404
    assert client.get("/admin/projects/zf2").json()["rule_count"] == 1
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf2"


def test_update_key_clash_returns_422(client):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects", json={"key": "other", "name": "另一个"})
    resp = client.put("/admin/projects/zf", json={"key": "other", "name": "x"})
    assert resp.status_code == 422
    assert "已被占用" in resp.json()["detail"]


def test_update_unknown_returns_404(client):
    assert client.put(
        "/admin/projects/ghost", json={"key": "ghost", "name": "x"}
    ).status_code == 404


# ---------------------------------------------------------------------------
# 删除：只解除归属，绝不级联删规则
# ---------------------------------------------------------------------------

def test_delete_unassigns_rules_but_keeps_them(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})

    resp = client.delete("/admin/projects/zf")
    assert resp.status_code == 200
    assert resp.json()["unassigned_rules"] == 2

    assert client.get("/admin/rules").json()["count"] == 3
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_delete_unknown_returns_404(client):
    assert client.delete("/admin/projects/ghost").status_code == 404


# ---------------------------------------------------------------------------
# 批量移入移出
# ---------------------------------------------------------------------------

def test_batch_assign(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})
    assert resp.status_code == 200
    assert resp.json()["moved"] == 2
    assert client.get("/admin/rules?project=zf").json()["count"] == 2


def test_batch_assign_unknown_project_returns_404(client, seeded):
    assert client.post(
        "/admin/projects/ghost/rules", json={"rule_ids": ["R001"]}
    ).status_code == 404


def test_batch_assign_ignores_unknown_rule_ids(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    resp = client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "NOPE"]})
    assert resp.json()["moved"] == 1


def test_batch_unassign(client, seeded):
    client.post("/admin/projects", json=NEW_PROJECT)
    client.post("/admin/projects/zf/rules", json={"rule_ids": ["R001", "R002"]})

    resp = client.post("/admin/rules/unassign", json={"rule_ids": ["R001"]})
    assert resp.status_code == 200
    assert resp.json()["moved"] == 1
    assert client.get("/admin/rules?project=zf").json()["count"] == 1
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_batch_empty_list_returns_422(client):
    assert client.post("/admin/rules/unassign", json={"rule_ids": []}).status_code == 422
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_admin_projects.py -v`
Expected: 全部 404（路由不存在）

- [ ] **Step 3: 实现路由**

Create `app/api/admin_projects.py`:

```python
"""项目 CRUD 与规则的批量移入移出。

删项目只解除规则归属,绝不级联删规则——删项目在 UI 上点两下就能干,
让它有能力抹掉几十条精心调过的 prompt 太危险。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import repo_projects, repo_rules
from app.db.session import get_db
from app.schemas.rules import ProjectIn, RuleIdsIn

router = APIRouter(tags=["admin-projects"])
log = logging.getLogger("aicheck.admin.projects")


def _must_get(db: Session, key: str):
    project = repo_projects.get_by_key(db, key)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目不存在: {key}",
        )
    return project


@router.get("/projects", summary="列出所有项目")
def list_all_projects(db: Session = Depends(get_db)) -> dict[str, Any]:
    counts = repo_projects.rule_counts(db)
    items = [
        repo_projects.to_api_dict(p, rule_count=counts.get(p.id, 0))
        for p in repo_projects.list_projects(db)
    ]
    return {"count": len(items), "projects": items}


@router.get("/projects/{key}", summary="按 key 查询单个项目")
def get_single_project(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _must_get(db, key)
    counts = repo_projects.rule_counts(db)
    return repo_projects.to_api_dict(project, rule_count=counts.get(project.id, 0))


@router.post("/projects", summary="新增项目", status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    if repo_projects.get_by_key(db, payload.key) is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"项目 key 已存在: {payload.key}",
        )

    repo_projects.create(
        db, key=payload.key, name=payload.name, description=payload.description
    )
    db.commit()
    log.info("新增项目 key=%s name=%s", payload.key, payload.name)
    return {"status": "ok", "key": payload.key}


@router.put("/projects/{key}", summary="按 key 修改项目(支持改 key)")
def update_project(
    key: str, payload: ProjectIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = _must_get(db, key)

    if payload.key != key:
        clash = repo_projects.get_by_key(db, payload.key)
        if clash is not None and clash.id != project.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"目标 key 已被占用: {payload.key}",
            )

    repo_projects.update(
        db, project, key=payload.key, name=payload.name,
        description=payload.description,
    )
    db.commit()
    log.info("更新项目 key=%s → %s", key, payload.key)
    return {"status": "ok", "key": payload.key}


@router.delete("/projects/{key}", summary="删除项目(其下规则变为未分组)")
def delete_project(key: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = _must_get(db, key)
    n = repo_projects.delete(db, project)
    db.commit()
    log.info("删除项目 key=%s,%d 条规则变为未分组", key, n)
    return {"status": "ok", "key": key, "unassigned_rules": n}


# ---------------------------------------------------------------------------
# 批量移入 / 移出
# ---------------------------------------------------------------------------

@router.post("/projects/{key}/rules", summary="批量把规则移入项目")
def assign_rules(
    key: str, payload: RuleIdsIn, db: Session = Depends(get_db)
) -> dict[str, Any]:
    project = _must_get(db, key)
    n = repo_rules.assign_project(db, payload.rule_ids, project.id)
    db.commit()
    log.info("移入项目 key=%s,%d 条规则", key, n)
    return {"status": "ok", "key": key, "moved": n}


@router.post("/rules/unassign", summary="批量把规则移出项目(置为未分组)")
def unassign_rules(payload: RuleIdsIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    """不设计成 ``DELETE /projects/{key}/rules``:带 body 的 DELETE 虽合法,
    但部分客户端与代理会静默丢弃 body;且移出本质是置 NULL,不需要知道原项目。
    """
    n = repo_rules.assign_project(db, payload.rule_ids, None)
    db.commit()
    log.info("移出项目,%d 条规则变为未分组", n)
    return {"status": "ok", "moved": n}
```

- [ ] **Step 4: 挂载路由**

`app/main.py` 第 22 行 import 加 `admin_projects`：

```python
from app.api import admin, admin_projects, admin_rules, compare, health, history
```

第 63 行 `app.include_router(admin_rules.router, prefix="/admin")` 之后加一行：

```python
app.include_router(admin_projects.router, prefix="/admin")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/integration/test_admin_projects.py -v`
Expected: 21 passed

- [ ] **Step 6: 提交**

```bash
git add app/api/admin_projects.py app/main.py tests/integration/test_admin_projects.py
git commit -m "feat(admin): 项目 CRUD 与规则批量移入移出

- 删项目只解除归属，返回受影响规则数供 UI 提示；绝不级联删规则
- 改 key 不影响归属，规则通过 project_id 关联
- 移出用 POST /admin/rules/unassign 而非带 body 的 DELETE：
  部分客户端与代理会静默丢弃 DELETE 的 body，且移出本质是置 NULL，
  不需要知道原项目是谁"
```

---

## Task 14: 导入导出接口

**Files:**
- Modify: `pyproject.toml`（加 `python-multipart`）
- Create: `app/api/admin_import_export.py`
- Modify: `app/main.py`（import + 挂载）
- Test: `tests/integration/test_import_export.py`

- [ ] **Step 1: 加 multipart 依赖**

FastAPI 处理 `UploadFile` 需要 `python-multipart`，缺了会在启动时报 `Form data requires "python-multipart"`。`pyproject.toml` 的 `dependencies` 追加：

```toml
    "python-multipart>=0.0.9",
```

Run: `uv sync --dev`
Expected: 输出含 `+ python-multipart`

- [ ] **Step 2: 写失败测试**

Create `tests/integration/test_import_export.py`:

```python
"""导入导出端点。事务回滚与警告是重点。"""

from __future__ import annotations

import pytest
import yaml as pyyaml

from app.db import repo_rules


BUNDLE = """
projects:
  - key: zf
    name: 政府投资

rules:
  - id: R001
    project: zf
    name: 总投资一致
    prompt_template: "A:{field_a} B:{field_b}"
  - id: R002
    name: 未分组的
    prompt_template: "A:{field_a}"
"""


def _upload(client, text: str, filename: str = "rules.yaml"):
    return client.post(
        "/admin/import",
        files={"file": (filename, text.encode("utf-8"), "application/x-yaml")},
    )


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------

def test_import_creates_everything(client):
    resp = _upload(client, BUNDLE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["projects"] == {"created": 1, "updated": 0}
    assert body["rules"] == {"created": 2, "updated": 0}
    assert body["warnings"] == []

    assert client.get("/admin/rules").json()["count"] == 2
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf"


def test_import_is_merge_not_replace(client, db):
    repo_rules.create(db, {"id": "KEEP", "prompt_template": "t"})
    db.commit()

    _upload(client, BUNDLE)
    assert client.get("/admin/rules/KEEP").status_code == 200
    assert client.get("/admin/rules").json()["count"] == 3


def test_import_second_time_updates(client):
    _upload(client, BUNDLE)
    body = _upload(client, BUNDLE).json()
    assert body["rules"] == {"created": 0, "updated": 2}


def test_import_undefined_project_warns(client):
    text = 'rules:\n  - id: R001\n    project: ghost\n    prompt_template: "x"\n'
    body = _upload(client, text).json()
    assert len(body["warnings"]) == 1
    assert "ghost" in body["warnings"][0]
    assert client.get("/admin/rules/R001").json()["project"] is None


def test_import_syntax_error_returns_422_with_line(client):
    resp = _upload(client, "rules:\n  - id: R001\n   bad: x\n")
    assert resp.status_code == 422
    assert "第 3 行" in resp.json()["detail"]


def test_import_invalid_rule_rolls_back_everything(client):
    """一条非法 → 库里什么都不该留下，连合法的那条也不留。"""
    text = """
projects:
  - key: zf
    name: 政府投资
rules:
  - id: GOOD
    prompt_template: "ok"
  - id: BAD
    prompt_template: ""
"""
    assert _upload(client, text).status_code == 422
    assert client.get("/admin/rules").json()["count"] == 0
    assert client.get("/admin/projects").json()["count"] == 0


def test_import_rejects_non_yaml_extension(client):
    resp = _upload(client, BUNDLE, filename="rules.txt")
    assert resp.status_code == 422
    assert "yaml" in resp.json()["detail"].lower()


def test_import_empty_file_returns_422(client):
    resp = _upload(client, "")
    assert resp.status_code == 422


def test_import_preserves_existing_sort_order(client, db):
    repo_rules.create(db, {"id": "R001", "prompt_template": "t"})
    repo_rules.create(db, {"id": "ZZZ", "prompt_template": "t"})
    db.commit()

    _upload(client, BUNDLE)
    ids = [r["id"] for r in client.get("/admin/rules").json()["rules"]]
    assert ids == ["R001", "ZZZ", "R002"]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def test_export_returns_yaml_attachment(client):
    _upload(client, BUNDLE)
    resp = client.get("/admin/export")
    assert resp.status_code == 200
    assert "yaml" in resp.headers["content-type"]
    assert "attachment" in resp.headers["content-disposition"]
    assert ".yaml" in resp.headers["content-disposition"]


def test_export_content_is_parseable(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export").text)
    assert [p["key"] for p in data["projects"]] == ["zf"]
    assert [r["id"] for r in data["rules"]] == ["R001", "R002"]


def test_export_filtered_by_project(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export?project=zf").text)
    assert [r["id"] for r in data["rules"]] == ["R001"]


def test_export_unassigned(client):
    _upload(client, BUNDLE)
    data = pyyaml.safe_load(client.get("/admin/export?project=__none__").text)
    assert [r["id"] for r in data["rules"]] == ["R002"]
    assert "projects" not in data


def test_export_unknown_project_returns_404(client):
    assert client.get("/admin/export?project=ghost").status_code == 404


def test_export_then_import_roundtrip(client):
    """导出→导入必须完整还原，否则备份没有意义。"""
    _upload(client, BUNDLE)
    text = client.get("/admin/export").text

    client.delete("/admin/rules/R001")
    client.delete("/admin/rules/R002")
    client.delete("/admin/projects/zf")
    assert client.get("/admin/rules").json()["count"] == 0

    _upload(client, text)
    assert client.get("/admin/rules/R001").json()["project"]["key"] == "zf"
    assert client.get("/admin/rules/R002").json()["project"] is None
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_import_export.py -v`
Expected: 全部 404

- [ ] **Step 4: 实现**

Create `app/api/admin_import_export.py`:

```python
"""规则与项目的 yaml 导入导出。

导入是**全文件单事务**:任何一条校验不过就整体回滚返 422。不允许出现
「导入了一半」的中间状态——那种状态既不知道成功了哪几条,也不敢重试。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.rules_io import BundleParseError
from app.core.rules_sync import BundleValidationError, export_bundle, import_bundle
from app.db import repo_projects
from app.db.session import get_db

router = APIRouter(tags=["admin-import-export"])
log = logging.getLogger("aicheck.admin.io")

UNASSIGNED = "__none__"
_ALLOWED_SUFFIX = (".yaml", ".yml")


@router.get("/export", summary="导出规则为 yaml 文件")
def export_rules(
    project: str | None = Query(
        None, description=f"按项目 key 导出；传 {UNASSIGNED} 只导未分组；不传导全部"
    ),
    db: Session = Depends(get_db),
) -> Response:
    unassigned = project == UNASSIGNED
    project_key = None if unassigned else project

    if project_key and repo_projects.get_by_key(db, project_key) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"项目不存在: {project_key}",
        )

    text = export_bundle(db, project_key=project_key, unassigned=unassigned)

    stamp = datetime.now().strftime("%Y%m%d")
    if unassigned:
        filename = f"rules-unassigned-{stamp}.yaml"
    elif project_key:
        filename = f"rules-{project_key}-{stamp}.yaml"
    else:
        filename = f"rules-{stamp}.yaml"

    log.info("导出规则 project=%s file=%s", project or "全部", filename)
    return Response(
        content=text,
        media_type="application/x-yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", summary="从 yaml 文件导入规则(合并 upsert)")
async def import_rules(
    file: UploadFile, db: Session = Depends(get_db)
) -> dict[str, Any]:
    name = (file.filename or "").lower()
    if not name.endswith(_ALLOWED_SUFFIX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"只接受 .yaml / .yml 文件,收到: {file.filename}",
        )

    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件不是 UTF-8 编码",
        ) from e

    if not text.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="文件为空",
        )

    try:
        result = import_bundle(db, text)
        db.commit()
    except (BundleParseError, BundleValidationError) as e:
        db.rollback()
        log.warning("导入失败已回滚: %s", e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except Exception:
        db.rollback()
        log.exception("导入未捕获异常,已回滚")
        raise

    return result.to_api_dict()
```

- [ ] **Step 5: 挂载路由**

`app/main.py` import 行改为：

```python
from app.api import (
    admin,
    admin_import_export,
    admin_projects,
    admin_rules,
    compare,
    health,
    history,
)
```

在 `app.include_router(admin_projects.router, prefix="/admin")` 之后加：

```python
app.include_router(admin_import_export.router, prefix="/admin")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/integration/test_import_export.py -v`
Expected: 16 passed

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml uv.lock app/api/admin_import_export.py app/main.py tests/integration/test_import_export.py
git commit -m "feat(admin): yaml 导入导出接口

- 导入全文件单事务，任一条校验不过整体回滚返 422：
  「导入一半」的状态既不知道成功了哪几条，也不敢重试
- 语法错误带行号，直接给用户看
- 导出按 ?project 联动，文件名带日期
- 加 python-multipart：FastAPI 处理 UploadFile 缺它会启动报错"
```

---

## Task 15: /compare 切到 DB

**Files:**
- Modify: `app/api/compare.py:21-36`（import 与 `_find_rule_cfg`）、`:67-156`（路由体）
- Modify: `app/main.py`（加 `OperationalError` 全局处理器）
- Modify: `tests/integration/test_compare_api.py`（规则来源改 DB）
- Test: `tests/integration/test_compare_db.py`

- [ ] **Step 1: 写失败测试**

Create `tests/integration/test_compare_db.py`:

```python
"""/compare 从 DB 取规则后的新增行为：历史快照、DB 故障降级。"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from app.db import repo_history, repo_projects, repo_rules
from app.rules import llm_rule


@pytest.fixture
def stub_llm(monkeypatch):
    def _fake(messages, **kwargs):
        return ('{"match": true, "reason": "ok"}',
                {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4})
    monkeypatch.setattr(llm_rule, "chat_json", _fake)


@pytest.fixture
def rule_in_project(db):
    p = repo_projects.create(db, key="zf", name="政府投资")
    db.flush()
    repo_rules.create(
        db,
        {"id": "R001", "name": "总投资一致", "prompt_template": "A:{field_a} B:{field_b}"},
        project_id=p.id,
    )
    db.commit()


PAYLOAD = {
    "rule_id": "R001",
    "field_a": {"value": "1.2亿"},
    "field_b": {"value": "12000万元"},
}


def test_compare_reads_rule_from_db(client, rule_in_project, stub_llm):
    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_compare_response_has_no_project_field(client, rule_in_project, stub_llm):
    """契约不变：比对响应不新增 project，下游无需改动。"""
    assert "project" not in client.post("/compare", json=PAYLOAD).json()


def test_history_records_project_snapshot(client, db, rule_in_project, stub_llm):
    client.post("/compare", json=PAYLOAD)
    item = repo_history.list_records(db)["items"][0]
    assert item["project_key"] == "zf"
    assert item["project_name"] == "政府投资"
    assert item["rule_name"] == "总投资一致"


def test_history_snapshot_survives_project_deletion(client, db, rule_in_project, stub_llm):
    """历史是快照：项目删了，历史里的项目名还在。"""
    client.post("/compare", json=PAYLOAD)
    client.delete("/admin/projects/zf")

    item = repo_history.list_records(db)["items"][0]
    assert item["project_name"] == "政府投资"


def test_history_write_failure_does_not_break_compare(
    client, rule_in_project, stub_llm, monkeypatch
):
    """LLM 已调用付费、结果已算出，因记流水失败而返 500 不合理。"""
    def _boom(*a, **kw):
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(repo_history, "add", _boom)

    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["result"] is True


def test_unknown_rule_returns_404(client, rule_in_project, stub_llm):
    assert client.post("/compare", json=dict(PAYLOAD, rule_id="NOPE")).status_code == 404


def test_db_unavailable_returns_503(client, rule_in_project, stub_llm, monkeypatch):
    """依赖不可用是 503，不是 500。"""
    def _boom(*a, **kw):
        raise OperationalError("SELECT", {}, Exception("db down"))

    monkeypatch.setattr(repo_rules, "get_by_rule_id", _boom)

    resp = client.post("/compare", json=PAYLOAD)
    assert resp.status_code == 503
    assert "数据库" in resp.json()["detail"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_compare_db.py -v`
Expected: FAIL（`/compare` 仍读 yaml，规则不存在返 404）

- [ ] **Step 3: 改 compare.py**

`app/api/compare.py` 的 import 区（21-26 行）改为：

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.llm_client import LLMTimeout, LLMUpstreamError
from app.db import repo_history, repo_rules
from app.db.session import get_db
from app.rules.field_keys import field_keys_for
from app.rules.registry import build_rule, should_require_field_name
from app.schemas.compare import CompareRequest, CompareResponse, Cost
```

删除 `_find_rule_cfg` 函数（32-36 行）。

路由签名改为：

```python
@router.post(
    "/compare",
    response_model=CompareResponse,
    response_model_exclude_none=True,
    summary="按指定规则比对若干字段",
)
def compare(req: CompareRequest, db: Session = Depends(get_db)) -> CompareResponse:
    t0 = time.perf_counter()
    inputs = req.field_inputs()
    log.info("收到比对请求 rule_id=%s fields=%d", req.rule_id, len(inputs))

    rule_row = repo_rules.get_by_rule_id(db, req.rule_id)
    if rule_row is None:
        log.warning("rule_id 不存在: %s", req.rule_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {req.rule_id}",
        )
    rule_cfg = repo_rules.to_rule_cfg(rule_row)
```

其后从 `expected_keys = field_keys_for(rule_cfg)` 到 `log.debug(...)` 保持原样不动。

历史记录那一段（136-147 行）替换为：

```python
    # 记录历史。写失败不能拖垮本次比对——LLM 已调用、费用已产生、结果已算出,
    # 因记流水失败而丢弃结果不合理。
    try:
        repo_history.add(db, {
            "rule_id": req.rule_id,
            "rule_name": rule_row.name or "",
            "project_key": rule_row.project.key if rule_row.project else None,
            "project_name": rule_row.project.name if rule_row.project else None,
            "fields": [{"key": k, **f.model_dump()} for k, f in ordered],
            "result": res.match,
            "reason": res.reason,
            "time_ms": elapsed_ms,
            "cost": res.usage or {},
            "raw_request": req.model_dump(),
        })
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("历史写入失败,已忽略 rule_id=%s", req.rule_id)
```

- [ ] **Step 4: 加 503 全局处理器**

`app/main.py` 在 `app = FastAPI(...)` 之后、`include_router` 之前插入：

```python
from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError


@app.exception_handler(OperationalError)
def _db_unavailable(_request: Request, exc: OperationalError) -> JSONResponse:
    """数据库不可用是依赖故障,返 503 而非 500。

    连接池已配 pool_pre_ping,能自愈的断连不会走到这里;走到这里说明
    库确实连不上,让调用方知道该重试而不是当成业务错误。
    """
    log.error("数据库不可用: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "数据库不可用,请稍后重试"},
    )
```

- [ ] **Step 5: 改现有 compare 集成测试的数据源**

`tests/integration/test_compare_api.py` 里注入规则的方式从写 yaml 改为写 DB。在文件顶部加：

```python
from app.db import repo_rules
```

把原先构造 `isolated_rules` / monkeypatch `get_rules` 的夹具替换为：

```python
@pytest.fixture
def seeded_rules(db):
    """把该文件原先注入 yaml 的规则改为写入 DB，断言部分不变。"""
    repo_rules.create(db, {
        "id": "R001",
        "name": "总投资一致",
        "prompt_template": "A:{field_a} B:{field_b}",
    })
    repo_rules.create(db, {
        "id": "R_CUSTOM",
        "name": "自定义键",
        "field_defs": ["keyan_jine", "zixun_jine"],
        "prompt_template": "A:{keyan_jine} B:{zixun_jine}",
    })
    repo_rules.create(db, {
        "id": "R_ONE",
        "name": "单字段",
        "prompt_template": "只有 {field_a}",
    })
    repo_rules.create(db, {
        "id": "R_NAME_REQUIRED",
        "name": "name 必填",
        "require_field_name": True,
        "prompt_template": "A:{field_a} B:{field_b}",
    })
    db.commit()
```

把该文件中所有测试函数的 `isolated_rules` 参数换成 `seeded_rules`，`client` 夹具改用 `conftest.py` 里的（删除文件内原有的 `client` 定义）。若原有规则 id 与上面不同，按原文件实际 id 调整这里的 `create` 调用，**断言一律不改**——契约没变，测试断言就不该变。

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/integration/ -v`
Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add app/api/compare.py app/main.py tests/integration/test_compare_db.py tests/integration/test_compare_api.py
git commit -m "feat(compare): 规则改从 DB 读取

- 请求与响应契约一字不动，下游无需改造
- 历史写入包 try/except：LLM 已调用付费、结果已算出，
  因记流水失败而返 500 不合理
- 历史带 project 快照，项目删了历史里的名字还在
- OperationalError 全局处理器返 503：依赖不可用不是业务错误"
```

---

## Task 16: 历史接口切到 DB

**Files:**
- Modify: `app/api/history.py`（全文）
- Delete: `app/core/history.py`
- Test: `tests/integration/test_history_api.py`

- [ ] **Step 1: 写失败测试**

Create `tests/integration/test_history_api.py`:

```python
"""``/admin/history`` 落库后的行为。响应结构与内存版保持一致。"""

from __future__ import annotations

import pytest

from app.db import repo_history


REC = {
    "rule_id": "R001",
    "rule_name": "总投资一致",
    "project_key": "zf",
    "project_name": "政府投资",
    "fields": [{"key": "field_a", "value": "1"}],
    "result": True,
    "reason": "ok",
    "time_ms": 120,
    "cost": {"total_tokens": 4},
    "raw_request": {"rule_id": "R001"},
}


@pytest.fixture
def seeded(db):
    ids = [
        repo_history.add(db, dict(REC, rule_id="R001", result=True)),
        repo_history.add(db, dict(REC, rule_id="R002", result=False)),
    ]
    db.commit()
    return ids


def test_list_newest_first(client, seeded):
    body = client.get("/admin/history").json()
    assert body["total"] == 2
    assert body["items"][0]["rule_id"] == "R002"


def test_survives_restart_semantics(client, db):
    """落库的意义：数据不随进程内状态消失。"""
    repo_history.add(db, REC)
    db.commit()
    assert client.get("/admin/history").json()["total"] == 1


def test_filter_by_rule_id(client, seeded):
    assert client.get("/admin/history?rule_id=R002").json()["total"] == 1


def test_filter_by_result(client, seeded):
    body = client.get("/admin/history?result=false").json()
    assert body["total"] == 1
    assert body["items"][0]["rule_id"] == "R002"


def test_pagination(client, seeded):
    body = client.get("/admin/history?limit=1&offset=0").json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_get_detail(client, seeded):
    rid = seeded[0]
    body = client.get(f"/admin/history/{rid}").json()
    assert body["id"] == rid
    assert body["raw_request"] == {"rule_id": "R001"}


def test_get_unknown_returns_404(client, seeded):
    assert client.get("/admin/history/99999").status_code == 404


def test_get_non_numeric_id_returns_404_not_500(client, seeded):
    assert client.get("/admin/history/abc").status_code == 404


def test_clear(client, seeded):
    resp = client.delete("/admin/history")
    assert resp.status_code == 200
    assert "2" in resp.json()["message"]
    assert client.get("/admin/history").json()["total"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_history_api.py -v`
Expected: FAIL（路由仍读内存 store，`total` 为 0）

- [ ] **Step 3: 重写路由**

覆盖 `app/api/history.py` 全文：

```python
"""``/admin/history`` 路由实现。

历史已落库(``compare_history`` 表),重启不再丢数据。响应结构与此前的
内存实现保持一致,前端历史页无需改动。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import repo_history
from app.db.session import get_db

router = APIRouter(tags=["history"])


@router.get("/history", summary="查询请求历史记录列表")
def list_history(
    rule_id: str | None = Query(None, description="按规则 ID 过滤"),
    result: bool | None = Query(
        None, description="按比对结果过滤 (true=一致, false=不一致)"
    ),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    db: Session = Depends(get_db),
) -> dict:
    return repo_history.list_records(
        db, rule_id=rule_id, result=result, limit=limit, offset=offset
    )


@router.get("/history/{record_id}", summary="获取单条历史记录详情")
def get_history_record(record_id: str, db: Session = Depends(get_db)) -> dict:
    row = repo_history.get(db, record_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"记录不存在: {record_id}",
        )
    return repo_history.to_api_dict(row)


@router.delete("/history", summary="清空所有历史记录")
def clear_history(db: Session = Depends(get_db)) -> dict:
    count = repo_history.clear(db)
    db.commit()
    return {"message": f"已清空 {count} 条记录"}
```

- [ ] **Step 4: 删掉内存实现**

```bash
git rm app/core/history.py
```

确认无残留引用：

Run: `uv run python -c "import subprocess,sys; sys.exit(subprocess.run(['grep','-rn','core.history','app/','tests/']).returncode == 0)"`

或直接：`grep -rn "core.history\|get_history_store" app/ tests/`
Expected: 无输出

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/integration/test_history_api.py -v`
Expected: 9 passed

- [ ] **Step 6: 提交**

```bash
git add app/api/history.py tests/integration/test_history_api.py
git rm app/core/history.py
git commit -m "feat(history): 历史记录落库

响应结构与内存版一致（含 id 仍为字符串），前端历史页零改动。
非数字 id 返 404 而非 500。
删除 app/core/history.py 内存实现。"
```

---

## Task 17: 启动接线与首次导入

**Files:**
- Modify: `app/main.py:31-47`（lifespan）
- Modify: `app/core/config.py`（删 `get_rules` / `reload_rules` / `_resolve_rules_path` / `_rules_cache`）
- Modify: `app/api/admin.py`（`reload-rules` 降级）
- Create: `scripts/import_rules.py`
- Test: `tests/integration/test_startup.py`

- [ ] **Step 1: 写失败测试**

Create `tests/integration/test_startup.py`:

```python
"""启动流程：首次导入与 reload-rules 降级。"""

from __future__ import annotations

import pytest

from app.core import startup
from app.db import repo_rules


LEGACY_YAML = """
# 老格式：没有 projects 节点，没有 project 字段
rules:
  - id: cross_rule_01
    name: 总投资一致
    prompt_template: "A:{field_a} B:{field_b}"
  - id: cross_rule_02
    name: 项目名称一致
    prompt_template: "A:{field_a} B:{field_b}"
"""


def test_seed_imports_when_empty(db, tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(LEGACY_YAML, encoding="utf-8")

    n = startup.seed_rules_if_empty(db, path)
    db.commit()

    assert n == 2
    assert repo_rules.count(db) == 2


def test_seeded_rules_are_unassigned(db, tmp_path):
    """老 yaml 没有项目信息，导入后全部未分组——不自动造默认项目。"""
    path = tmp_path / "rules.yaml"
    path.write_text(LEGACY_YAML, encoding="utf-8")

    startup.seed_rules_if_empty(db, path)
    db.commit()

    assert all(r.project_id is None for r in repo_rules.list_rules(db))


def test_seed_skips_when_not_empty(db, tmp_path):
    """库里有规则就不导——否则删光规则重启会全部长回来。"""
    repo_rules.create(db, {"id": "EXISTING", "prompt_template": "t"})
    db.commit()

    path = tmp_path / "rules.yaml"
    path.write_text(LEGACY_YAML, encoding="utf-8")

    assert startup.seed_rules_if_empty(db, path) == 0
    assert repo_rules.count(db) == 1


def test_seed_skips_when_file_missing(db, tmp_path):
    assert startup.seed_rules_if_empty(db, tmp_path / "nope.yaml") == 0


def test_seed_bad_file_does_not_crash_startup(db, tmp_path, caplog):
    """种子文件坏了只该告警，不该让服务起不来。"""
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - id: X\n    prompt_template: ''\n", encoding="utf-8")

    assert startup.seed_rules_if_empty(db, path) == 0
    assert repo_rules.count(db) == 0


def test_reload_rules_is_noop_returning_count(client, db):
    """降级为空操作，但保留端点：怕有运维脚本在调它。"""
    repo_rules.create(db, {"id": "R001", "prompt_template": "t"})
    db.commit()

    resp = client.post("/admin/reload-rules")
    assert resp.status_code == 200
    assert resp.json()["rules_loaded"] == 1


def test_config_no_longer_exposes_get_rules():
    """yaml 真源时代的接口必须删干净，留着会有人误用。"""
    from app.core import config

    assert not hasattr(config, "get_rules")
    assert not hasattr(config, "reload_rules")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_startup.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'app.core.startup'`

- [ ] **Step 3: 实现 startup 模块**

Create `app/core/startup.py`:

```python
"""启动期的一次性动作。

首次导入只在 ``rules`` 表**为空**时触发。限定这个条件是为了避免
「删光规则后重启又全部长回来」——空库等同首次部署。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_config
from app.core.rules_io import BundleParseError
from app.core.rules_sync import BundleValidationError, import_bundle
from app.db import repo_rules

log = logging.getLogger("aicheck.boot")


def resolve_rules_path() -> Path:
    """种子 / 默认导出路径,沿用 config.yaml 里的 ``rules_config_path``。"""
    rel = get_config().get("rules_config_path", "config/rules.yaml")
    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p


def seed_rules_if_empty(db: Session, path: Path | None = None) -> int:
    """库空则从 yaml 导入,返回导入的规则条数。

    种子文件有问题只告警不抛——种子是便利功能,不该让服务起不来。
    """
    if repo_rules.count(db) > 0:
        return 0

    path = path or resolve_rules_path()
    if not path.is_file():
        log.info("种子文件不存在,跳过首次导入: %s", path)
        return 0

    try:
        text = path.read_text(encoding="utf-8")
        result = import_bundle(db, text)
        db.commit()
    except (BundleParseError, BundleValidationError) as e:
        db.rollback()
        log.error("首次导入失败(种子文件有问题,服务照常启动): %s", e)
        return 0
    except Exception:
        db.rollback()
        log.exception("首次导入未捕获异常,服务照常启动")
        return 0

    log.info(
        "首次启动,从 %s 导入 %d 条规则、%d 个项目",
        path, result.rules_created, result.projects_created,
    )
    for w in result.warnings:
        log.warning("  导入告警: %s", w)
    return result.rules_created
```

- [ ] **Step 4: 改 lifespan**

`app/main.py` 的 lifespan（31-47 行）替换为：

```python
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from app.core.startup import seed_rules_if_empty
    from app.db import repo_rules
    from app.db.bootstrap import bootstrap_database
    from app.db.session import get_sessionmaker

    cfg = get_config()
    llm_cfg = cfg.get("llm", {})

    bootstrap_database()

    with get_sessionmaker()() as db:
        seed_rules_if_empty(db)
        rules = repo_rules.list_rules(db)
        log.info(
            "服务启动 host=%s port=%s llm.model=%s backend=%s rules=%d",
            cfg.get("app", {}).get("host"),
            cfg.get("app", {}).get("port"),
            llm_cfg.get("model"),
            llm_cfg.get("backend"),
            len(rules),
        )
        for r in rules:
            log.info("  规则 %s %s", r.rule_id, r.name or "")
    yield
    log.info("服务关闭")
```

同时把顶部 `from app.core.config import PROJECT_ROOT, get_config, get_rules` 改为：

```python
from app.core.config import PROJECT_ROOT, get_config
```

> **注意**：`bootstrap_database()` 在测试里会尝试连真 MySQL。`conftest.py` 的 `client` 夹具用 `TestClient(app)` 会触发 lifespan。因此下一步要让 bootstrap 在依赖被覆盖时跳过。

- [ ] **Step 5: 让 lifespan 在测试下跳过 bootstrap**

在 `bootstrap_database()` 调用前加守卫：

```python
    from app.db.session import get_db

    # 测试通过 dependency_overrides 注入自己的会话,此时不应连真 MySQL
    if get_db in _app.dependency_overrides:
        log.info("检测到会话依赖被覆盖(测试环境),跳过建库建表与首次导入")
        yield
        log.info("服务关闭")
        return

    bootstrap_database()
```

- [ ] **Step 6: 清掉 config.py 里的 yaml 真源代码**

删除 `app/core/config.py` 中的 `_rules_cache`（30 行）、`_resolve_rules_path`（72-76 行）、`get_rules`（79-99 行）、`reload_rules`（102-110 行）。

- [ ] **Step 7: 降级 reload-rules**

覆盖 `app/api/admin.py`：

```python
"""管理类接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import reload_config
from app.db import repo_rules
from app.db.session import get_db

router = APIRouter(tags=["admin"])
log = logging.getLogger("aicheck.admin")


@router.post("/reload-rules", summary="[已降级] 规则改由 DB 实时读取,本接口仅返回当前条数")
def admin_reload_rules(db: Session = Depends(get_db)) -> dict:
    """规则落库后每次请求都实时查库,没有缓存可 reload。

    端点保留而非删除:怕有运维脚本还在调它,突然 404 比空操作更难排查。
    """
    n = repo_rules.count(db)
    log.info("reload-rules 已降级为空操作,当前 %d 条规则", n)
    return {"status": "ok", "rules_loaded": n, "note": "规则已实时读库,无需 reload"}


@router.post("/reload-config", summary="强制重新加载主配置 config.yaml")
def admin_reload_config() -> dict:
    cfg = reload_config()
    log.info("管理接口触发 reload-config，llm.model=%s", cfg.get("llm", {}).get("model"))
    return {
        "status": "ok",
        "llm_model": cfg.get("llm", {}).get("model"),
        "llm_backend": cfg.get("llm", {}).get("backend"),
    }
```

- [ ] **Step 8: 写导入脚本**

Create `scripts/import_rules.py`:

```python
"""手工把 yaml 导入数据库。

用法:
    uv run python scripts/import_rules.py                    # 导入 config/rules.yaml
    uv run python scripts/import_rules.py path/to/other.yaml

语义与 Web UI 的「导入」一致:按 id 合并 upsert,全文件单事务。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging  # noqa: E402
from app.core.rules_sync import import_bundle  # noqa: E402
from app.core.startup import resolve_rules_path  # noqa: E402
from app.db.bootstrap import bootstrap_database  # noqa: E402
from app.db.session import get_sessionmaker  # noqa: E402


def main() -> int:
    setup_logging()
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else resolve_rules_path()
    if not path.is_file():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    bootstrap_database()
    with get_sessionmaker()() as db:
        try:
            result = import_bundle(db, path.read_text(encoding="utf-8"))
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            print(f"导入失败,已回滚: {e}", file=sys.stderr)
            return 1

    print(
        f"导入完成: 项目 新增 {result.projects_created} / 更新 {result.projects_updated}，"
        f"规则 新增 {result.rules_created} / 更新 {result.rules_updated}"
    )
    for w in result.warnings:
        print(f"  告警: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 9: 跑全量测试**

Run: `uv run pytest -v`
Expected: 全部 passed，无 error

- [ ] **Step 10: lint 与类型检查**

Run: `uv run ruff check .`
Expected: `All checks passed!`

Run: `uv run mypy app`
Expected: 无 error（若有 SQLAlchemy 相关误报，在具体行加 `# type: ignore[...]` 并注明原因）

- [ ] **Step 11: 提交**

```bash
git add app/core/startup.py app/core/config.py app/main.py app/api/admin.py scripts/import_rules.py tests/integration/test_startup.py
git commit -m "feat(boot): 启动建库建表 + 首次导入，config 清掉 yaml 真源代码

- 首次导入只在 rules 表为空时触发，避免「删光规则重启又长回来」
- 种子文件有问题只告警不抛：种子是便利功能，不该让服务起不来
- 导入后规则全部未分组，不自动造默认项目——项目划分由人决定
- reload-rules 降级为空操作但保留端点：怕运维脚本还在调，
  突然 404 比空操作更难排查
- 删除 config.get_rules / reload_rules / _rules_cache"
```

---

# 前端部分

> **前端没有测试框架**，本仓库也不打算为这次改动引入一套。因此 Task 18-21 用**手工验证步骤**代替自动化测试——每步写明点什么、看到什么算过。不要假装写了前端测试。
>
> **验证前提**：`config/config.yaml` 的 `mysql.database` 已填、MySQL 可连，然后
> `uv run python -m app.main`，浏览器开 `http://localhost:6549/ui/`。

## Task 18: 左侧项目栏

**Files:**
- Modify: `ui/index.html`（样式区尾部加类；`page-rules` section 改左右分栏；底部加 `projects.js`）
- Create: `ui/projects.js`

- [ ] **Step 1: 加样式**

`ui/index.html` 的 `<style>` 块尾部（`.badge-*` 那几行之后）追加：

```css
        /* ---- 项目栏 ---- */
        .project-item {
            display: flex; align-items: center; justify-content: space-between;
            gap: 8px; padding: 8px 10px; margin-bottom: 4px;
            border-radius: 10px 10px 10px 3px;
            font-size: 13px; color: var(--bio-dark); cursor: pointer;
            border: 1px solid transparent;
            transition: background 0.18s var(--ease-soft), border-color 0.18s var(--ease-soft);
        }
        .project-item:hover { background: var(--bio-pale); }
        .project-item.active {
            background: var(--bio-pale); border-color: var(--bio-light); font-weight: 600;
        }
        .project-item.unassigned { border-style: dashed; border-color: rgba(141, 170, 145, 0.5); }
        .project-item .count {
            font-size: 11px; color: var(--text-secondary);
            background: rgba(255, 255, 255, 0.7); padding: 1px 7px; border-radius: 50px;
        }
        .project-item .acts { display: none; gap: 2px; }
        .project-item:hover .acts { display: flex; }
        .project-item .acts button {
            background: none; border: none; cursor: pointer; padding: 2px 4px;
            color: var(--text-secondary); font-size: 13px; line-height: 1;
        }
        .project-item .acts button:hover { color: var(--bio-dark); }
        .project-item .acts button.danger:hover { color: var(--error); }

        /* ---- 批量操作条 ---- */
        .bulk-bar {
            display: flex; align-items: center; gap: 10px;
            margin-top: 14px; padding: 10px 14px;
            background: var(--bio-pale); border: 1px solid rgba(141, 170, 145, 0.45);
            border-radius: 12px 12px 12px 4px; font-size: 13px; color: var(--bio-dark);
        }
```

- [ ] **Step 2: 改 `page-rules` 为左右分栏**

把 `ui/index.html` 第 348 行 `<section id="page-rules">` 起、到该 section 结束 `</section>` 为止的整段替换为：

```html
        <section id="page-rules">
            <div class="flex gap-5 items-start">

                <!-- 左栏:项目 -->
                <aside class="card p-4" style="width: 236px; flex-shrink: 0;">
                    <div class="flex items-center justify-between mb-3">
                        <h3 class="text-sm font-bold flex items-center gap-2" style="color: var(--bio-dark);">
                            <i class="ri-plant-line text-brand-600"></i> 项目
                        </h3>
                        <button class="btn-ghost" style="padding: 4px 8px;" onclick="openProjectCreate()" title="新建项目">
                            <i class="ri-add-line"></i>
                        </button>
                    </div>
                    <div id="project-list"></div>
                </aside>

                <!-- 右栏:规则 -->
                <div class="card p-6 flex-1 min-w-0">
                    <div class="flex items-center justify-between mb-5">
                        <div>
                            <h2 class="text-base font-bold text-surface-900 flex items-center gap-2">
                                <i class="ri-leaf-line text-brand-600"></i>
                                <span id="rules-title">规则列表</span>
                            </h2>
                            <p class="text-sm text-surface-500 mt-1">所有规则统一走 LLM,判定标准由 <code>prompt_template</code> 描述。修改后立即生效,无需重启。</p>
                        </div>
                        <div class="flex items-center gap-2">
                            <button class="btn-outline" onclick="triggerImport()" title="从 yaml 导入(合并 upsert)">
                                <i class="ri-upload-2-line mr-1"></i> 导入
                            </button>
                            <button class="btn-outline" onclick="exportRules()" title="导出当前筛选范围为 yaml">
                                <i class="ri-download-2-line mr-1"></i> 导出
                            </button>
                            <button class="btn-primary" onclick="openCreate()">
                                <i class="ri-add-line mr-1"></i> 新增规则
                            </button>
                        </div>
                    </div>

                    <div class="overflow-x-auto">
                        <table class="w-full">
                            <thead class="table-header">
                                <tr>
                                    <th style="width: 36px;">
                                        <input type="checkbox" id="rules-check-all" onchange="toggleSelectAll(this.checked)">
                                    </th>
                                    <th>ID</th>
                                    <th>规则名</th>
                                    <th>项目</th>
                                    <th>name 必填</th>
                                    <th>system_prompt</th>
                                    <th>操作</th>
                                </tr>
                            </thead>
                            <tbody id="rules-tbody"></tbody>
                        </table>
                    </div>

                    <div id="rules-empty" class="hidden text-center py-14 text-surface-400">
                        <i class="ri-seedling-line text-5xl sway"></i>
                        <p class="mt-3">暂无规则,点右上角"新增规则"播下第一颗种子</p>
                    </div>

                    <div id="bulk-bar" class="bulk-bar hidden">
                        <span>已选 <b id="bulk-count">0</b> 条</span>
                        <select id="bulk-project" class="input" style="width: 200px; padding: 6px 10px;"></select>
                        <button class="btn-outline" onclick="bulkAssign()">
                            <i class="ri-folder-transfer-line mr-1"></i> 移入
                        </button>
                        <button class="btn-outline" id="bulk-unassign-btn" onclick="bulkUnassign()">
                            <i class="ri-inbox-unarchive-line mr-1"></i> 移出
                        </button>
                        <button class="btn-ghost ml-auto" onclick="clearSelection()">取消选择</button>
                    </div>

                    <input type="file" id="import-file" accept=".yaml,.yml" class="hidden" onchange="doImport(this)">
                </div>
            </div>
        </section>
```

- [ ] **Step 3: 引入 projects.js**

`ui/index.html` 底部 `<script src="app.js"></script>` 之后加一行（必须在 `app.js` 之后，因为 `projects.js` 用到 `api()` / `toast()` / `escape()`）：

```html
    <script src="projects.js"></script>
```

- [ ] **Step 4: 写 projects.js 的项目列表部分**

Create `ui/projects.js`:

```js
// 项目管理 · 批量移入移出 · 导入导出
// 依赖 app.js 里的 api() / toast() / escape() / loadRules()，故必须在其后加载。
//
// 与后端的契约:
//   GET    /admin/projects            列表(带 rule_count)
//   POST   /admin/projects            新增
//   PUT    /admin/projects/{key}      修改(支持改 key)
//   DELETE /admin/projects/{key}      删除(其下规则变未分组)
//   POST   /admin/projects/{key}/rules  批量移入
//   POST   /admin/rules/unassign        批量移出
//   GET    /admin/export?project=      导出
//   POST   /admin/import               导入

let PROJECTS = [];
// null = 全部, '__none__' = 未分组, 其他 = 项目 key
let CURRENT_PROJECT = null;

const UNASSIGNED = '__none__';

// ---------------------------------------------------------------------------
// 项目列表
// ---------------------------------------------------------------------------

async function loadProjects() {
    try {
        const data = await api('GET', '/admin/projects');
        PROJECTS = data.projects || [];
        renderProjectList();
        // 批量条那套在 Task 19 才加，这里守一道，Task 18 单独验证时不至于报错
        if (typeof renderBulkProjectOptions === 'function') renderBulkProjectOptions();
    } catch (e) {
        toast('加载项目失败: ' + e.message, 'error');
    }
}

function renderProjectList() {
    const box = document.getElementById('project-list');
    if (!box) return;

    const total = RULES_TOTAL_COUNT;
    const assigned = PROJECTS.reduce((n, p) => n + p.rule_count, 0);
    const unassigned = Math.max(total - assigned, 0);

    const row = (key, label, count, cls, acts) => `
        <div class="project-item ${cls}" onclick="selectProject(${key === null ? 'null' : `'${escape(key)}'`})">
            <span class="truncate">${label}</span>
            <span class="flex items-center gap-1">
                <span class="count">${count}</span>
                ${acts || ''}
            </span>
        </div>`;

    let html = row(
        null,
        '<i class="ri-stack-line mr-1"></i>全部',
        total,
        CURRENT_PROJECT === null ? 'active' : '',
    );
    html += row(
        UNASSIGNED,
        '<i class="ri-scissors-cut-line mr-1"></i>未分组',
        unassigned,
        'unassigned ' + (CURRENT_PROJECT === UNASSIGNED ? 'active' : ''),
    );

    html += PROJECTS.map(p => row(
        p.key,
        `<i class="ri-leaf-line mr-1"></i>${escape(p.name)}`,
        p.rule_count,
        CURRENT_PROJECT === p.key ? 'active' : '',
        `<span class="acts">
            <button onclick="event.stopPropagation(); openProjectEdit('${escape(p.key)}')" title="编辑"><i class="ri-edit-line"></i></button>
            <button class="danger" onclick="event.stopPropagation(); openProjectDel('${escape(p.key)}')" title="删除"><i class="ri-delete-bin-line"></i></button>
        </span>`,
    )).join('');

    if (!PROJECTS.length) {
        html += `<p class="text-xs text-center py-4" style="color: var(--text-secondary);">
            还没有项目,点上方 + 新建一个
        </p>`;
    }

    box.innerHTML = html;
}

function selectProject(key) {
    CURRENT_PROJECT = key;
    if (typeof clearSelection === 'function') clearSelection();
    renderProjectList();
    const p = PROJECTS.find(x => x.key === key);
    document.getElementById('rules-title').textContent =
        key === null ? '规则列表'
        : key === UNASSIGNED ? '未分组规则'
        : `${p ? p.name : key} 的规则`;
    loadRules();
}

/** 当前筛选拼成 query string，供 loadRules 与 exportRules 共用。 */
function projectQuery() {
    return CURRENT_PROJECT === null
        ? ''
        : `?project=${encodeURIComponent(CURRENT_PROJECT)}`;
}
```

- [ ] **Step 5: 让 app.js 的规则加载带上筛选**

`ui/app.js` 第 67 行 `let RULES = [];` 下面加一行：

```js
let RULES_TOTAL_COUNT = 0;   // 不受项目筛选影响的总数，项目栏「全部」用它
```

`loadRules()`（69-77 行）替换为：

```js
async function loadRules() {
    try {
        const q = (typeof projectQuery === 'function') ? projectQuery() : '';
        const data = await api('GET', '/admin/rules' + q);
        RULES = data.rules || [];
        // 「全部」的计数不能用筛选后的结果，得单独取一次
        if (q) {
            const all = await api('GET', '/admin/rules');
            RULES_TOTAL_COUNT = all.count;
        } else {
            RULES_TOTAL_COUNT = data.count;
        }
        renderRules();
        if (typeof renderProjectList === 'function') renderProjectList();
    } catch (e) {
        toast('加载规则失败: ' + e.message, 'error');
    }
}
```

- [ ] **Step 6: 页面初始化时加载项目**

在 `ui/projects.js` 末尾追加：

```js
// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    loadProjects();
});
```

- [ ] **Step 7: 手工验证**

启动服务后打开 `/ui/`：

| 检查 | 预期 |
|---|---|
| 规则页布局 | 左侧出现 236px 宽的项目栏，右侧规则表格，两者上边缘对齐 |
| 项目栏内容 | 「全部」带规则总数、「未分组」虚线边框带未分组数 |
| 无项目时 | 项目栏显示"还没有项目,点上方 + 新建一个" |
| 点「未分组」 | 该项高亮，右侧标题变"未分组规则"，列表只剩未分组的 |
| 点「全部」 | 恢复完整列表 |
| 视觉一致性 | 项目条目圆角是 `10px 10px 10px 3px` 的不对称形态，hover 变 `--bio-pale`，与页面其余部分同一套语言 |

- [ ] **Step 8: 提交**

```bash
git add ui/index.html ui/projects.js ui/app.js
git commit -m "feat(ui): 规则页改左右分栏，新增项目栏

- 左栏常驻项目列表，带每项规则数，点击即筛选
- 未分组用虚线边框与正式项目做视觉区分
- 沿用现有植物语汇：ri-plant-line 作栏标题、ri-leaf-line 作条目、
  不对称圆角，不回落到 Tailwind 默认样式
- 项目那套约 250 行拆进 projects.js，app.js 已 655 行不宜再涨"
```

---

## Task 19: 规则表格加项目列与批量操作

**Files:**
- Modify: `ui/app.js`（`renderRules`）
- Modify: `ui/projects.js`（选择状态与批量端点调用）

- [ ] **Step 1: 改 renderRules 渲染新的两列**

`ui/app.js` 的 `renderRules()`（79-106 行）替换为：

```js
function renderRules() {
    const tbody = document.getElementById('rules-tbody');
    const empty = document.getElementById('rules-empty');
    if (!RULES.length) {
        tbody.innerHTML = '';
        empty.classList.remove('hidden');
        if (typeof syncBulkBar === 'function') syncBulkBar();
        return;
    }
    empty.classList.add('hidden');
    tbody.innerHTML = RULES.map(r => {
        const reqBadge = r.require_field_name
            ? '<span class="badge badge-warning">true</span>'
            : '<span class="badge badge-gray">false</span>';
        const sysBadge = (r.system_prompt && r.system_prompt.trim())
            ? `<span class="badge badge-info" title="${escape(r.system_prompt)}">已配置</span>`
            : '<span class="badge badge-gray">空</span>';
        const projBadge = r.project
            ? `<span class="badge badge-success">${escape(r.project.name)}</span>`
            : '<span class="badge badge-gray">未分组</span>';
        const checked = (typeof isSelected === 'function' && isSelected(r.id)) ? 'checked' : '';
        return `<tr class="table-row">
            <td><input type="checkbox" class="rule-check" data-id="${escape(r.id)}" ${checked} onchange="toggleSelect('${escape(r.id)}', this.checked)"></td>
            <td><code class="font-mono text-brand-700">${escape(r.id)}</code></td>
            <td>${escape(r.name || '')}</td>
            <td>${projBadge}</td>
            <td>${reqBadge}</td>
            <td>${sysBadge}</td>
            <td>
                <button class="btn-ghost" onclick="openEdit('${escape(r.id)}')"><i class="ri-edit-line"></i> 编辑</button>
                <button class="btn-ghost btn-ghost-danger" onclick="openDel('${escape(r.id)}')"><i class="ri-delete-bin-line"></i> 删除</button>
            </td>
        </tr>`;
    }).join('');
    if (typeof syncBulkBar === 'function') syncBulkBar();
}
```

- [ ] **Step 2: 在 projects.js 里实现选择与批量**

`ui/projects.js` 的初始化块之前插入：

```js
// ---------------------------------------------------------------------------
// 勾选与批量移入移出
// ---------------------------------------------------------------------------

let SELECTED = new Set();

function isSelected(id) {
    return SELECTED.has(id);
}

function toggleSelect(id, on) {
    if (on) SELECTED.add(id); else SELECTED.delete(id);
    syncBulkBar();
}

function toggleSelectAll(on) {
    SELECTED = on ? new Set(RULES.map(r => r.id)) : new Set();
    for (const cb of document.querySelectorAll('.rule-check')) cb.checked = on;
    syncBulkBar();
}

function clearSelection() {
    SELECTED = new Set();
    for (const cb of document.querySelectorAll('.rule-check')) cb.checked = false;
    const all = document.getElementById('rules-check-all');
    if (all) all.checked = false;
    syncBulkBar();
}

/** 只在有选中时显示批量条；当前已是「未分组」视图时「移出」无意义，置灰。 */
function syncBulkBar() {
    const bar = document.getElementById('bulk-bar');
    if (!bar) return;
    bar.classList.toggle('hidden', SELECTED.size === 0);
    document.getElementById('bulk-count').textContent = SELECTED.size;
    document.getElementById('bulk-unassign-btn').disabled =
        CURRENT_PROJECT === UNASSIGNED;
}

function renderBulkProjectOptions() {
    const sel = document.getElementById('bulk-project');
    if (!sel) return;
    sel.innerHTML = PROJECTS.length
        ? PROJECTS.map(p => `<option value="${escape(p.key)}">${escape(p.name)}</option>`).join('')
        : '<option value="">(还没有项目)</option>';
}

async function bulkAssign() {
    const key = document.getElementById('bulk-project').value;
    if (!key) { toast('请先新建一个项目', 'error'); return; }
    const ids = [...SELECTED];
    if (!ids.length) return;

    try {
        const res = await api('POST', `/admin/projects/${encodeURIComponent(key)}/rules`,
                              { rule_ids: ids });
        toast(`已移入 ${res.moved} 条规则`, 'success');
        clearSelection();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('移入失败: ' + e.message, 'error');
    }
}

async function bulkUnassign() {
    const ids = [...SELECTED];
    if (!ids.length) return;

    try {
        const res = await api('POST', '/admin/rules/unassign', { rule_ids: ids });
        toast(`已移出 ${res.moved} 条规则`, 'success');
        clearSelection();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('移出失败: ' + e.message, 'error');
    }
}
```

- [ ] **Step 3: 规则编辑弹窗加"所属项目"下拉**

`ui/index.html` 的规则弹窗里，`f-desc` 那个 `<div>` 之后插入：

```html
                <!-- project -->
                <div>
                    <div class="field-label"><span class="name">project</span></div>
                    <p class="field-help">所属项目。留空即未分组。改归属也可以在列表里勾选后批量操作。</p>
                    <select id="f-project" class="input"></select>
                </div>
```

`ui/app.js` 的 `fillForm(r)` 里，在设置 `f-desc` 之后加：

```js
    const psel = document.getElementById('f-project');
    if (psel) {
        const opts = ['<option value="">(未分组)</option>'].concat(
            (typeof PROJECTS !== 'undefined' ? PROJECTS : []).map(
                p => `<option value="${escape(p.key)}">${escape(p.name)}</option>`
            )
        );
        psel.innerHTML = opts.join('');
        psel.value = (r.project && r.project.key) ? r.project.key : '';
    }
```

`ui/app.js` 的 `readForm()` 返回对象里加一项（`project` 为空串时传 `null`，后端据此置未分组）：

```js
        project: (document.getElementById('f-project') || {}).value || null,
```

- [ ] **Step 4: 手工验证**

| 操作 | 预期 |
|---|---|
| 打开规则页 | 表格 7 列，"项目"列显示绿色徽章或灰色"未分组" |
| 勾选 2 条规则 | 表格下方浮出批量条，显示"已选 2 条" |
| 点表头全选框 | 当前视图所有行被勾选，计数正确 |
| 选中后点「移入」 | toast 提示移入条数，项目栏计数更新，被选行的项目列变成目标项目 |
| 切到「未分组」视图 | 「移出」按钮置灰 |
| 在「全部」视图选中已分组的规则点「移出」 | toast 提示，项目列变"未分组" |
| 编辑某条规则 | 弹窗里"所属项目"下拉回显当前项目；改成别的保存后列表同步 |
| 编辑时选「(未分组)」保存 | 该规则项目列变"未分组" |

- [ ] **Step 5: 提交**

```bash
git add ui/index.html ui/app.js ui/projects.js
git commit -m "feat(ui): 规则表格加项目列与批量移入移出

- 表格加勾选框与项目列，选中后浮出批量操作条
- 当前视图已是未分组时「移出」置灰，避免无意义操作
- 规则编辑弹窗加所属项目下拉，空串提交为 null 即未分组"
```

---

## Task 20: 项目新建 / 编辑 / 删除弹窗

**Files:**
- Modify: `ui/index.html`（两个弹窗 DOM）
- Modify: `ui/projects.js`（弹窗逻辑）

- [ ] **Step 1: 加弹窗 DOM**

`ui/index.html` 中 `<div id="del-modal" class="modal-overlay hidden">` 之前插入：

```html
    <!-- 项目 新增/编辑 -->
    <div id="project-modal" class="modal-overlay hidden">
        <div class="modal">
            <div class="flex items-center justify-between mb-4">
                <h3 id="project-modal-title" class="text-lg font-bold text-surface-900">新建项目</h3>
                <button class="btn-ghost" onclick="closeProjectModal()"><i class="ri-close-line text-xl"></i></button>
            </div>

            <div class="space-y-4">
                <div>
                    <div class="field-label"><span class="name">key</span><span class="required">*</span></div>
                    <p class="field-help">项目稳定标识,导出的 yaml 靠它引用项目。只能用字母/数字/下划线,字母或下划线开头。<b>跨环境导入靠它对齐</b>,定了就别轻易改。</p>
                    <input id="p-key" class="input" placeholder="zhengfu_touzi">
                </div>
                <div>
                    <div class="field-label"><span class="name">name</span><span class="required">*</span></div>
                    <p class="field-help">项目中文名,项目栏和规则列表里显示的就是它。</p>
                    <input id="p-name" class="input" placeholder="政府投资项目">
                </div>
                <div>
                    <div class="field-label"><span class="name">description</span></div>
                    <p class="field-help">项目描述,仅文档用。写清这个项目管的是哪一类审核。</p>
                    <input id="p-desc" class="input" placeholder="政府投资项目审核规则集">
                </div>
            </div>

            <div class="flex justify-end gap-2 mt-6">
                <button class="btn-outline" onclick="closeProjectModal()">取消</button>
                <button class="btn-primary" onclick="saveProject()">
                    <i class="ri-check-line mr-1"></i> 保存
                </button>
            </div>
        </div>
    </div>

    <!-- 项目 删除确认 -->
    <div id="project-del-modal" class="modal-overlay hidden">
        <div class="modal" style="max-width: 460px;">
            <h3 class="text-lg font-bold text-surface-900 mb-3">确认删除项目</h3>
            <p class="text-sm text-surface-600 mb-5">
                将删除项目 <code id="p-del-name" class="font-mono" style="color: var(--error);"></code>，
                其下 <b id="p-del-count">0</b> 条规则将变为<b>未分组</b>。
                规则本身不会被删除,下游调用不受影响。
            </p>
            <div class="flex justify-end gap-2">
                <button class="btn-outline" onclick="closeProjectDel()">取消</button>
                <button class="btn-primary" style="background: var(--error);" onclick="confirmProjectDel()">
                    <i class="ri-delete-bin-line mr-1"></i> 删除
                </button>
            </div>
        </div>
    </div>

    <!-- 导入结果 -->
    <div id="import-result-modal" class="modal-overlay hidden">
        <div class="modal" style="max-width: 560px;">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-bold text-surface-900">导入完成</h3>
                <button class="btn-ghost" onclick="closeImportResult()"><i class="ri-close-line text-xl"></i></button>
            </div>
            <div id="import-result-body" class="text-sm text-surface-600 space-y-3"></div>
            <div class="flex justify-end mt-6">
                <button class="btn-primary" onclick="closeImportResult()">知道了</button>
            </div>
        </div>
    </div>
```

- [ ] **Step 2: 实现弹窗逻辑**

`ui/projects.js` 的「初始化」块之前插入：

```js
// ---------------------------------------------------------------------------
// 项目 新增 / 编辑 / 删除
// ---------------------------------------------------------------------------

let P_MODE = 'create';       // 'create' | 'edit'
let P_ORIG_KEY = null;
let P_DEL_KEY = null;

function openProjectCreate() {
    P_MODE = 'create';
    P_ORIG_KEY = null;
    document.getElementById('project-modal-title').textContent = '新建项目';
    document.getElementById('p-key').value = '';
    document.getElementById('p-name').value = '';
    document.getElementById('p-desc').value = '';
    document.getElementById('project-modal').classList.remove('hidden');
}

function openProjectEdit(key) {
    const p = PROJECTS.find(x => x.key === key);
    if (!p) { toast('项目不存在: ' + key, 'error'); return; }
    P_MODE = 'edit';
    P_ORIG_KEY = key;
    document.getElementById('project-modal-title').textContent = `编辑项目 ${key}`;
    document.getElementById('p-key').value = p.key;
    document.getElementById('p-name').value = p.name;
    document.getElementById('p-desc').value = p.description || '';
    document.getElementById('project-modal').classList.remove('hidden');
}

function closeProjectModal() {
    document.getElementById('project-modal').classList.add('hidden');
}

async function saveProject() {
    const key = document.getElementById('p-key').value.trim();
    const name = document.getElementById('p-name').value.trim();
    const description = document.getElementById('p-desc').value.trim();

    if (!key) { toast('key 不能为空', 'error'); return; }
    if (!name) { toast('name 不能为空', 'error'); return; }
    // 与后端 IDENT_RE 同规则，先在前端拦一道，省一次往返
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
        toast('key 只能用字母/数字/下划线,且字母或下划线开头', 'error');
        return;
    }

    const body = { key, name, description };
    try {
        if (P_MODE === 'create') {
            await api('POST', '/admin/projects', body);
            toast('项目已创建', 'success');
        } else {
            await api('PUT', `/admin/projects/${encodeURIComponent(P_ORIG_KEY)}`, body);
            toast('项目已更新', 'success');
            // 改了 key 且当前正筛这个项目，筛选条件要跟着改
            if (CURRENT_PROJECT === P_ORIG_KEY) CURRENT_PROJECT = key;
        }
        closeProjectModal();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

function openProjectDel(key) {
    const p = PROJECTS.find(x => x.key === key);
    if (!p) return;
    P_DEL_KEY = key;
    document.getElementById('p-del-name').textContent = `${p.name} (${p.key})`;
    document.getElementById('p-del-count').textContent = p.rule_count;
    document.getElementById('project-del-modal').classList.remove('hidden');
}

function closeProjectDel() {
    document.getElementById('project-del-modal').classList.add('hidden');
    P_DEL_KEY = null;
}

async function confirmProjectDel() {
    if (!P_DEL_KEY) return;
    try {
        const res = await api('DELETE', `/admin/projects/${encodeURIComponent(P_DEL_KEY)}`);
        toast(`项目已删除,${res.unassigned_rules} 条规则变为未分组`, 'success');
        if (CURRENT_PROJECT === P_DEL_KEY) CURRENT_PROJECT = null;
        closeProjectDel();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}
```

- [ ] **Step 3: 手工验证**

| 操作 | 预期 |
|---|---|
| 点项目栏的 `+` | 弹出"新建项目"，三个字段为空 |
| key 填中文或带空格 | 前端直接 toast 拦下，不发请求 |
| 正常填写保存 | toast 成功，项目栏出现新项目，规则数为 0 |
| 重复 key 保存 | toast 显示后端的"项目 key 已存在" |
| hover 项目条目 | 右侧浮出编辑/删除两个小图标 |
| 点编辑改名保存 | 项目栏名字更新，若当前正筛该项目则标题同步 |
| 改 key 保存 | 项目栏更新；该项目下的规则仍在（归属没丢） |
| 点删除 | 弹窗写明"其下 N 条规则将变为未分组,规则本身不会被删除" |
| 确认删除 | toast 提示解除归属条数；规则总数不变，它们出现在"未分组"里 |

- [ ] **Step 4: 提交**

```bash
git add ui/index.html ui/projects.js
git commit -m "feat(ui): 项目新建/编辑/删除弹窗

- key 的正则前端先拦一道，与后端 IDENT_RE 同规则，省一次往返
- 删除确认写明「N 条规则将变为未分组，规则本身不会被删除」，
  对齐现有删规则弹窗把后果讲清楚的写法
- 改 key 或删项目后同步修正当前筛选条件，避免筛到一个不存在的 key"
```

---

## Task 21: 导入导出

**Files:**
- Modify: `ui/projects.js`（导入导出逻辑）
- Modify: `ui/app.js`（`reloadAll` 语义修正）
- Modify: `ui/index.html`（刷新按钮 title）

- [ ] **Step 1: 实现导入导出**

`ui/projects.js` 的「初始化」块之前插入：

```js
// ---------------------------------------------------------------------------
// 导入 / 导出
// ---------------------------------------------------------------------------

/** 导出与左栏联动:当前筛的是什么就导什么,不额外追问。 */
function exportRules() {
    window.location.href = '/admin/export' + projectQuery();
}

function triggerImport() {
    const el = document.getElementById('import-file');
    el.value = '';          // 清掉上次的选择，否则选同一个文件不触发 change
    el.click();
}

async function doImport(input) {
    const file = input.files && input.files[0];
    if (!file) return;

    const fd = new FormData();
    fd.append('file', file);

    try {
        // 不能走 api()——它会设 Content-Type: application/json，
        // multipart 的 boundary 必须交给浏览器自己生成
        const resp = await fetch('/admin/import', { method: 'POST', body: fd });
        const text = await resp.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }

        if (!resp.ok) {
            throw new Error((data && data.detail) || `HTTP ${resp.status}`);
        }

        await loadProjects();
        await loadRules();

        const warnings = data.warnings || [];
        if (warnings.length) {
            showImportResult(data);
        } else {
            toast(
                `导入完成:规则 新增 ${data.rules.created} / 更新 ${data.rules.updated}`,
                'success',
            );
        }
    } catch (e) {
        toast('导入失败: ' + e.message, 'error');
    }
}

/** 有警告时用弹窗而非 toast:警告可能多条，且需要逐条看完再决定要不要补救。 */
function showImportResult(data) {
    const body = document.getElementById('import-result-body');
    const w = data.warnings || [];
    body.innerHTML = `
        <div class="flex gap-6">
            <div><b>项目</b>　新增 ${data.projects.created} / 更新 ${data.projects.updated}</div>
            <div><b>规则</b>　新增 ${data.rules.created} / 更新 ${data.rules.updated}</div>
        </div>
        <div>
            <p class="font-bold mb-2" style="color: var(--warning-deep);">
                <i class="ri-alert-line mr-1"></i>${w.length} 条告警
            </p>
            <ul class="space-y-1" style="max-height: 260px; overflow-y: auto;">
                ${w.map(x => `<li class="text-xs" style="color: var(--text-secondary);">· ${escape(x)}</li>`).join('')}
            </ul>
        </div>`;
    document.getElementById('import-result-modal').classList.remove('hidden');
}

function closeImportResult() {
    document.getElementById('import-result-modal').classList.add('hidden');
}
```

- [ ] **Step 2: 修正刷新按钮语义**

规则已实时读库，"刷新缓存"这个说法不再成立。`ui/index.html` 第 339 行：

```html
                <button class="btn-outline" onclick="reloadAll()" title="重新拉取数据">
```

`ui/app.js` 的 `reloadAll()`（493 行起）改为重新拉取列表，并带上项目：

```js
async function reloadAll() {
    try {
        if (typeof loadProjects === 'function') await loadProjects();
        await loadRules();
        toast('数据已刷新', 'success');
    } catch (e) {
        toast('刷新失败: ' + e.message, 'error');
    }
}
```

> 若原 `reloadAll()` 还调了 `/admin/reload-rules` 与 `/admin/reload-config`，前者已降级为空操作可以去掉；`reload-config` 若原本就在调则保留。

- [ ] **Step 3: 手工验证**

| 操作 | 预期 |
|---|---|
| 「全部」视图点导出 | 浏览器下载 `rules-YYYYMMDD.yaml`，打开可见 `projects:` 节点与每条规则的 `project:` |
| 选中某项目再点导出 | 下载 `rules-<key>-YYYYMMDD.yaml`，只含该项目的规则 |
| 「未分组」视图点导出 | 下载 `rules-unassigned-YYYYMMDD.yaml`，无 `projects:` 节点 |
| 导出的文件直接再导入 | toast 提示"新增 0 / 更新 N"，数据无变化 |
| 导入一个只含 2 条新规则的片段 | 只新增这 2 条，原有规则和顺序不变 |
| 导入引用了不存在项目的文件 | 弹出结果框列出告警，该规则落到"未分组" |
| 导入 `.txt` 文件 | toast 显示后端的"只接受 .yaml / .yml 文件" |
| 导入语法错误的 yaml | toast 显示带行号的报错 |
| 连续两次选同一个文件导入 | 第二次仍能触发（`input.value` 已清空） |
| 点顶栏刷新按钮 | 项目栏与规则列表都重新拉取，toast"数据已刷新" |

- [ ] **Step 4: 提交**

```bash
git add ui/index.html ui/app.js ui/projects.js
git commit -m "feat(ui): 导入导出

- 导出与左栏筛选联动，不额外追问导哪些
- 导入不走 api()：multipart 的 boundary 必须交给浏览器生成，
  手动设 Content-Type 会让后端解析失败
- 触发前清空 input.value，否则连选同一个文件不触发 change
- 有告警时用弹窗而非 toast：可能多条，且要逐条看完才好决定补救
- 刷新按钮 title 从「手动刷新缓存」改为「重新拉取数据」——
  规则已实时读库，没有缓存这回事"
```

---

## Task 22: 文档同步

**Files:**
- Create: `docs/schema.sql`
- Modify: `config/config.yaml`（注释）、`config/rules.yaml`（头部注释）
- Modify: `README.md`、`api.md`、`.env.example`

- [ ] **Step 1: 写 schema.sql**

Create `docs/schema.sql`:

```sql
-- AI交叉校验 表结构参考
--
-- 应用启动时会自动建库建表(见 app/db/bootstrap.py),本文件用于:
--   1. 应用账号无 CREATE 权限时,交给 DBA 手工执行
--   2. 后续表结构变更的对照基准——本项目不做字段自动迁移,
--      改表结构需人工执行 ALTER 并同步更新本文件与 app/db/models.py
--
-- 字符集固定 utf8mb4:规则里全是中文 prompt。

CREATE DATABASE IF NOT EXISTS `ai_cross_check`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `ai_cross_check`;

CREATE TABLE IF NOT EXISTS `projects` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `key`         VARCHAR(64)  NOT NULL COMMENT '稳定标识,yaml 导入导出引用它',
  `name`        VARCHAR(128) NOT NULL,
  `description` VARCHAR(512) NOT NULL DEFAULT '',
  `sort_order`  INT          NOT NULL DEFAULT 0,
  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_projects_key` (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `rules` (
  `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
  `rule_id`            VARCHAR(64)  NOT NULL COMMENT '业务 ID,下游 /compare 传的就是它',
  `project_id`         BIGINT       NULL COMMENT 'NULL = 未分组',
  `name`               VARCHAR(128) NOT NULL DEFAULT '',
  `description`        VARCHAR(512) NOT NULL DEFAULT '',
  `field_defs`         JSON         NULL COMMENT 'list[str];NULL 表示用默认 field_a/field_b',
  `require_field_name` TINYINT(1)   NOT NULL DEFAULT 0,
  `system_prompt`      TEXT         NULL,
  `prompt_template`    TEXT         NOT NULL,
  `sort_order`         INT          NOT NULL DEFAULT 0,
  `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_rules_rule_id` (`rule_id`),
  KEY `idx_rules_project` (`project_id`),
  CONSTRAINT `fk_rules_project` FOREIGN KEY (`project_id`)
    REFERENCES `projects` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 刻意不设外键:存的是调用当时的快照。规则后续改名/删除/换项目
-- 都不该影响既有历史。
CREATE TABLE IF NOT EXISTS `compare_history` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `rule_id`      VARCHAR(64)  NOT NULL,
  `rule_name`    VARCHAR(128) NOT NULL DEFAULT '',
  `project_key`  VARCHAR(64)  NULL,
  `project_name` VARCHAR(128) NULL,
  `fields`       JSON         NOT NULL,
  `result`       TINYINT(1)   NOT NULL,
  `reason`       TEXT         NULL,
  `time_ms`      INT          NOT NULL,
  `cost`         JSON         NULL,
  `raw_request`  JSON         NULL,
  `created_at`   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_hist_rule_created` (`rule_id`, `created_at`),
  KEY `idx_hist_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: 改 config.yaml 的假文档**

`config/config.yaml` 第 44-46 行那段注释（"修改 rules.yaml 后无需重启容器,下一次请求会基于 mtime 自动 reload"）替换为：

```yaml
# 规则种子文件路径(相对项目根)。
#
# 注意:规则真源已是 MySQL,**直接编辑本文件不会生效**。它现在只在两处参与:
#   1. 首次启动且 rules 表为空时,自动导入一次
#   2. Web UI「导出」的默认文件名参考
# 改规则请走 Web UI,或用「导入」上传 yaml(合并 upsert)。
rules_config_path: "config/rules.yaml"
```

同时把工作区已有的 `mysql:` 节缩进规范为两个空格，并补齐字段：

```yaml
# 数据库连接。每项均可被同名大写环境变量覆盖(MYSQL_HOST / MYSQL_PORT /
# MYSQL_DATABASE / MYSQL_USERNAME / MYSQL_PASSWORD),生产不想把密码写进
# 文件时用环境变量。
# 库和表在启动时自动创建,需要应用账号有 CREATE 权限;若无权限,
# 参考 docs/schema.sql 交给 DBA 手工执行。
mysql:
  host: "127.0.0.1"
  port: 3306
  database: "ai_cross_check"
  username: "root"
  password: "1940"
  charset: "utf8mb4"
  pool_size: 5
  max_overflow: 10
  echo: false        # true 时打印所有 SQL，排错用
```

- [ ] **Step 3: 改 rules.yaml 头部注释**

`config/rules.yaml` 头部注释块（第 1-27 行）在开头插入一段身份说明，并补 `project` 字段说明：

```yaml
# 规则种子 / 导入导出文件
#
# 注意:规则真源是 MySQL,**直接编辑本文件不会生效**。
#   - 首次启动且 rules 表为空时,本文件被自动导入一次
#   - 之后改规则请走 Web UI,或用「导入」上传本文件(按 id 合并 upsert)
#   - Web UI「导出」产出的就是同样格式的文件
#
# 字段说明:
#   id                     规则唯一标识(上游传入的 rule_id),全局唯一
#   project                所属项目的 key。省略即未分组。引用未定义的项目
#                          会告警并置为未分组,不中断导入
```

其余原有字段说明保持不动。

- [ ] **Step 4: 改 README 与 .env.example**

`README.md` 中凡是描述"规则配置见 config/rules.yaml,支持热加载""改 yaml 自动生效"的段落，改为说明规则存于 MySQL、yaml 用于导入导出，并补一节数据库配置与首次启动说明。

`.env.example` 追加：

```bash
# 数据库连接（覆盖 config/config.yaml 的 mysql: 节，生产不想把密码写进文件时用）
# MYSQL_HOST=127.0.0.1
# MYSQL_PORT=3306
# MYSQL_DATABASE=ai_cross_check
# MYSQL_USERNAME=root
# MYSQL_PASSWORD=
```

- [ ] **Step 5: 更新 api.md**

在 `api.md` 中：

1. 「5. 规则 CRUD」小节：每个响应示例的规则对象补 `project` 字段（对象或 `null`）；`GET /admin/rules` 补 `?project=<key>` 与 `?project=__none__` 两个查询参数说明；`POST`/`PUT` 的请求体补 `project` 字段
2. 「3. 重新加载规则」小节：标注**已降级为空操作**，说明规则改为实时读库
3. 新增「8. 项目管理」小节，列出 5 个 CRUD 端点 + 2 个批量端点，含请求/响应示例与错误码
4. 新增「9. 导入导出」小节，含 `GET /admin/export`、`POST /admin/import` 的示例、导入返回结构、错误码
5. 「配置说明」补 `mysql:` 节与环境变量覆盖说明
6. 全文检查"规则配置见 config/rules.yaml""热加载""mtime"等表述，改为 MySQL 真源

- [ ] **Step 6: 全量验证**

Run: `uv run pytest -v`
Expected: 全部 passed

Run: `uv run ruff check .`
Expected: `All checks passed!`

Run: `uv run mypy app`
Expected: 无 error

- [ ] **Step 7: 提交**

```bash
git add docs/schema.sql config/config.yaml config/rules.yaml README.md api.md .env.example
git commit -m "docs: 同步落库后的文档

- 新增 docs/schema.sql：应用账号无 CREATE 权限时交给 DBA 执行，
  也是后续改表结构的对照基准
- config.yaml 与 rules.yaml 头部改掉「改文件自动生效」的承诺——
  留着就是假文档
- api.md 补项目管理与导入导出两节，规则接口补 project 字段，
  reload-rules 标注降级"
```

---

## 完成标准

全部任务做完后，以下每条都要能当场验证：

| 项 | 验证方式 |
|---|---|
| 全量测试通过 | `uv run pytest` 全绿，无 xfail 残留 |
| 静态检查通过 | `uv run ruff check .` 与 `uv run mypy app` 均无输出 |
| 首次启动自动导入 | 清空 `rules` 表后重启，日志出现"首次启动,从 ... 导入 54 条规则" |
| `/compare` 契约未变 | 用改造前的请求体调用，响应字段与改造前逐字一致（无 `project`） |
| 导出→导入完整还原 | 导出全部 → 删光规则与项目 → 导入该文件 → 规则数与归属全部恢复 |
| 删项目不删规则 | 建项目、移入若干规则、删项目 → 规则总数不变，它们出现在"未分组" |
| 历史落库 | 跑几次 `/compare` → 重启服务 → 历史页记录仍在 |
| 导入事务性 | 导入一个含非法规则的文件 → 返 422 且库中状态完全不变 |

## 未纳入本次范围

以下在 spec 中已明确排除，实施时不要顺手做：

- **字段自动迁移**（启动时 `ADD COLUMN` / `CREATE INDEX` 对齐）—— 表结构变更走人工 SQL
- **`/compare` 响应新增 `project` 字段** —— 下游契约保持一字不变
- **历史记录自动清理 / 保留天数** —— 只保留手动清空
- **规则领域层重构**（`app/rules/`）—— 本次零改动，验证分层是否正确的试金石






