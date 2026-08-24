"""启动流程：首次导入与 reload-rules 降级。"""

from __future__ import annotations

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


def test_lifespan_skips_bootstrap_when_db_overridden(db):
    """测试环境必须跳过建库建表——否则单测会去连真 MySQL。

    这条守着 lifespan 里那个 ``get_db in dependency_overrides`` 守卫。
    没有它，测试会连开发机上真实的 MySQL：本机恰好起着库时测试照样绿，
    CI 上或换台机器就挂，且报错指向连接超时而非守卫失效。
    因此让 bootstrap_database 一被调用就炸，client 能起来才算守卫生效。
    """
    from fastapi.testclient import TestClient

    from app.db import bootstrap
    from app.db.session import get_db
    from app.main import app

    called: list[str] = []

    def _boom() -> None:
        called.append("bootstrap")
        raise AssertionError("测试环境不应调用 bootstrap_database")

    # lifespan 内是函数级 import，patch 源模块属性即可在调用时命中
    original = bootstrap.bootstrap_database
    bootstrap.bootstrap_database = _boom  # type: ignore[assignment]

    def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
        bootstrap.bootstrap_database = original  # type: ignore[assignment]

    assert called == [], "守卫失效：测试环境调用了 bootstrap_database"
