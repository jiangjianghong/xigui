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
