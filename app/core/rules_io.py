"""rules.yaml 的序列化与解析，使用 ruamel.yaml round-trip 模式。

自规则落库 MySQL 后，本模块的职责是 **yaml ↔ plain dict 的纯转换**，
服务于导入导出；不再是运行时真源的读写器。

为什么仍用 ruamel 而不是 PyYAML：导出时 ``prompt_template`` 必须是 ``|``
块格式，否则几百字的 prompt 会挤成一行带 ``\\n`` 的字符串，既没法看也没法改。
"""

from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

# round-trip 模式 = 读写时保留注释、字段顺序、缩进、引号风格
_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 4096  # 不要自动换行


def normalize_rule(cfg: dict[str, Any]) -> Any:
    """把前端传来的 plain dict 规则规整为 ruamel CommentedMap,
    使 ``prompt_template`` / ``system_prompt`` 这种多行字符串以 ``|`` 块格式
    保存,而不是堆成一长串带 ``\\n`` 的引号字符串。
    """
    from ruamel.yaml.comments import CommentedMap

    out = CommentedMap()
    # 固定字段顺序,与现有 yaml 视觉一致
    order = [
        "id", "project", "name", "description", "field_defs",
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
