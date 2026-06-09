"""规则 CRUD 后端,服务 UI 的增删改查。

数据流:
    UI <--HTTP--> 本模块 <--ruamel.yaml--> config/rules.yaml
                                    \\--reload_rules()--> compare 路由的缓存

所有写操作完成后会立即调用 ``reload_rules()``,让 ``/compare`` 端点在下一次请求时
使用最新规则;同时由于 yaml 文件 mtime 也变了,容器外的其他进程(若存在)同样会
自动 reload。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import PROJECT_ROOT, get_config, reload_rules
from app.core.rules_io import (
    find_rule_index,
    list_rules,
    load_rules_doc,
    normalize_rule,
    save_rules_doc,
)

router = APIRouter(tags=["admin-rules"])
log = logging.getLogger("aicheck.admin.rules")


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class RuleIn(BaseModel):
    """前端提交的规则结构。字段说明详见 docs/api/rules.md。"""

    id: str = Field(..., min_length=1, description="规则唯一编号(如 R001),建议三位数字")
    name: str | None = Field(default="", description="中文规则名(用于日志/展示)")
    description: str | None = Field(default="", description="规则描述,仅文档用")
    expected_field_names: list[str] | None = Field(
        default=None,
        description="预期的两个字段名,仅文档化不强校验",
    )
    require_field_name: bool = Field(
        default=False,
        description="是否要求请求里 field_a.name / field_b.name 必填",
    )
    system_prompt: str | None = Field(
        default="",
        description="LLM 系统提示。留空则不向模型发送 system 消息",
    )
    prompt_template: str = Field(
        ..., min_length=1,
        description="prompt 模板,支持占位符 {a_value}/{b_value}/{a_name}/{b_name}",
    )


class RuleOut(RuleIn):
    """读取返回的规则结构,字段同 RuleIn。"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _rules_yaml_path():
    """解析 rules.yaml 绝对路径,沿用 config.yaml 里 ``rules_config_path``。"""
    cfg = get_config()
    rel = cfg.get("rules_config_path", "config/rules.yaml")
    from pathlib import Path

    p = Path(rel)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _to_plain_dict(rule: Any) -> dict[str, Any]:
    """把 ruamel CommentedMap 转成普通 dict,便于 JSON 序列化。"""
    out: dict[str, Any] = {}
    for k, v in rule.items():
        if isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.get("/rules", summary="列出所有规则")
def list_all_rules() -> dict[str, Any]:
    path = _rules_yaml_path()
    doc = load_rules_doc(path)
    items = [_to_plain_dict(r) for r in list_rules(doc)]
    return {"count": len(items), "rules": items}


@router.get("/rules/{rule_id}", summary="按 id 查询单条规则")
def get_single_rule(rule_id: str) -> dict[str, Any]:
    path = _rules_yaml_path()
    doc = load_rules_doc(path)
    idx = find_rule_index(doc, rule_id)
    if idx < 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )
    return _to_plain_dict(doc["rules"][idx])


@router.post("/rules", summary="新增规则", status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleIn) -> dict[str, Any]:
    path = _rules_yaml_path()
    doc = load_rules_doc(path)
    if find_rule_index(doc, payload.id) >= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"rule_id 已存在: {payload.id}",
        )

    rule_node = normalize_rule(payload.model_dump())
    if doc.get("rules") is None:
        from ruamel.yaml.comments import CommentedSeq
        doc["rules"] = CommentedSeq()
    doc["rules"].append(rule_node)

    save_rules_doc(path, doc)
    n = reload_rules()
    log.info("新增规则 id=%s,当前共 %d 条", payload.id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.put("/rules/{rule_id}", summary="按 id 全量修改规则(支持 rename:body.id != url id)")
def update_rule(rule_id: str, payload: RuleIn) -> dict[str, Any]:
    path = _rules_yaml_path()
    doc = load_rules_doc(path)
    idx = find_rule_index(doc, rule_id)
    if idx < 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    # rename:新 id 不能撞上其他规则
    if payload.id != rule_id:
        clash = find_rule_index(doc, payload.id)
        if clash >= 0 and clash != idx:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"目标 id 已被占用: {payload.id}",
            )

    doc["rules"][idx] = normalize_rule(payload.model_dump())
    save_rules_doc(path, doc)
    n = reload_rules()
    if payload.id != rule_id:
        log.info("更新规则 id=%s → %s,当前共 %d 条", rule_id, payload.id, n)
    else:
        log.info("更新规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": payload.id, "rules_loaded": n}


@router.delete("/rules/{rule_id}", summary="按 id 删除规则")
def delete_rule(rule_id: str) -> dict[str, Any]:
    path = _rules_yaml_path()
    doc = load_rules_doc(path)
    idx = find_rule_index(doc, rule_id)
    if idx < 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"rule_id 不存在: {rule_id}",
        )

    del doc["rules"][idx]
    save_rules_doc(path, doc)
    n = reload_rules()
    log.info("删除规则 id=%s,当前共 %d 条", rule_id, n)
    return {"status": "ok", "rule_id": rule_id, "rules_loaded": n}
