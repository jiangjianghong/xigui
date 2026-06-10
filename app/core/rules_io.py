"""rules.yaml 读写封装,使用 ruamel.yaml round-trip 模式保留注释与字段顺序。

为什么单独抽一个模块:
- ``app/core/config.py`` 里的 ``get_rules()`` 走 PyYAML + mtime 缓存,只读不写,
  也不关心注释——那条路径继续用 PyYAML 性能更稳。
- 后台 CRUD(``admin_rules.py``)需要往 yaml 里**写**数据,这时必须保留注释
  否则文件级文档会被冲掉,因此走 ruamel.yaml。

两者作用域明确:
- 读旧路径(运行时执行 ``/compare``):``config.get_rules()``
- 写路径(UI 编辑):``rules_io.load_rules`` + ``rules_io.save_rules`` +
  ``config.reload_rules()`` 触发缓存刷新
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

# round-trip 模式 = 读写时保留注释、字段顺序、缩进、引号风格
_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096  # 不要自动换行

# 写文件时加进程内串行化,避免两个并发 PUT 互相覆盖
_write_lock = threading.Lock()


def load_rules_doc(path: Path) -> Any:
    """读取整个 yaml 文档(含 ``rules`` 节点之外的所有注释/字段)。

    返回 ruamel 的 CommentedMap;通常用法是 ``doc["rules"]`` 取规则 list。
    """
    with path.open("r", encoding="utf-8") as f:
        data = _yaml.load(f)
    if data is None:
        # 空文件:返回一个带 rules 节点的最小骨架
        from ruamel.yaml.comments import CommentedMap, CommentedSeq

        data = CommentedMap()
        data["rules"] = CommentedSeq()
    return data


def save_rules_doc(path: Path, doc: Any) -> None:
    """把整个 yaml 文档写回磁盘,保留注释、字段顺序、缩进、引号。"""
    with _write_lock:
        with path.open("w", encoding="utf-8") as f:
            _yaml.dump(doc, f)


def list_rules(doc: Any) -> list[Any]:
    """从文档中取规则 list。若没有 ``rules`` 节点则返回空 list。"""
    rules = doc.get("rules") if isinstance(doc, dict) else None
    return list(rules) if rules else []


def find_rule_index(doc: Any, rule_id: str) -> int:
    """按 id 在 ``doc["rules"]`` 里找下标,找不到返 -1。"""
    rules = doc.get("rules") or []
    for i, r in enumerate(rules):
        if r.get("id") == rule_id:
            return i
    return -1


def normalize_rule(cfg: dict[str, Any]) -> Any:
    """把前端传来的 plain dict 规则规整为 ruamel CommentedMap,
    使 ``prompt_template`` / ``system_prompt`` 这种多行字符串以 ``|`` 块格式
    保存,而不是堆成一长串带 ``\\n`` 的引号字符串。
    """
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    out = CommentedMap()
    # 固定字段顺序,与现有 yaml 视觉一致
    order = [
        "id", "name", "description", "field_defs",
        "require_field_name", "system_prompt", "prompt_template",
    ]
    for key in order:
        if key not in cfg:
            continue
        val = cfg[key]
        if key in ("prompt_template", "system_prompt") and isinstance(val, str) and "\n" in val:
            out[key] = LiteralScalarString(val)
        else:
            out[key] = val
    # 兜底:保留前端传了但 order 没列的字段
    for k, v in cfg.items():
        if k not in out:
            out[k] = v
    return out
