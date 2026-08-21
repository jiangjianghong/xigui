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
