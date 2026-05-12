"""``semantic_exact``：语义"严格一致"。

要求两个字段在内容项上一一对应，允许排序/标点/空白/格式差异，但不允许
任一边多出或缺少子项目。
"""

from __future__ import annotations

from app.rules.registry import register
from app.rules.semantic_base import SemanticRule


@register
class SemanticExactRule(SemanticRule):
    rule_type = "semantic_exact"

    system_prompt = (
        "你是一位资深的投资项目文件合规审核专家。任务：判断两段文本在所列的"
        "内容项上是否'严格一致'。允许差异：项目顺序、标点符号、空白、序号格式、"
        "措辞微调。不允许：任一段多出或缺少任何子项目，或子项目的数量/规格不同。\n"
        "严格按 JSON 输出，不要任何额外说明、Markdown 标记或注释："
        '{"match": true|false, "reason": "<不超过50字的中文理由>"}'
    )
