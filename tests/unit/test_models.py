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
