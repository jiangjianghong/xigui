"""``semantic_similar``：语义"基本一致"。

容忍措辞差异、简称/全称、行政区划前缀差异等。具体业务边界由
``rules.yaml`` 的 ``prompt_template`` 描述。
"""

from __future__ import annotations

from app.rules.registry import register
from app.rules.semantic_base import SemanticRule


@register
class SemanticSimilarRule(SemanticRule):
    rule_type = "semantic_similar"

    system_prompt = (
        "你是一位资深的投资项目文件合规审核专家，擅长在两个文本字段之间识别它们"
        "是否指向同一事物或语义上'基本一致'。判断标准应当宽松：允许措辞差异、"
        "简称与全称、有/无前后缀、组织形式变体（如'有限公司'与'有限责任公司'）"
        "等。只有当两者明显指向不同对象、或关键信息不同时，才判定为不一致。\n"
        "严格按 JSON 输出，不要任何额外说明、Markdown 标记或注释："
        '{"match": true|false, "reason": "<不超过30字的中文理由>"}'
    )
