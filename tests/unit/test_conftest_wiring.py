"""验证测试夹具本身可用——夹具坏了后面全是假绿。"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import Project, Rule


def test_db_fixture_is_empty(db):
    assert db.query(Rule).count() == 0
    assert db.query(Project).count() == 0


def test_engine_is_shared_across_threads(engine):
    """StaticPool 必须让另一个线程看到同一个内存库。

    单线程下这条显得多余——SQLAlchemy 给内存 SQLite 的默认池是
    SingletonThreadPool，连接本就复用，schema 不会丢。但 Task 12 起
    接口测试会经 TestClient 调同步 endpoint，Starlette 把它派进工作
    线程池，那时没有 StaticPool 就是 no such table。而那个报错看着像
    建表没跑成，不像池配置问题，极难定位。所以现在就钉住。
    """
    import threading

    from sqlalchemy import text

    seen: dict = {}

    def _query() -> None:
        try:
            with engine.connect() as conn:
                seen["count"] = conn.execute(
                    text("SELECT COUNT(*) FROM rules")
                ).scalar()
        except Exception as e:  # noqa: BLE001
            seen["error"] = repr(e)

    t = threading.Thread(target=_query)
    t.start()
    t.join()

    assert seen.get("error") is None, f"跨线程访问失败: {seen.get('error')}"
    assert seen["count"] == 0


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
