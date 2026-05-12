"""目标服务器 vLLM cost 自测脚本(可独立运行,不依赖项目其他模块)。

用法
----
1. 复制本文件到目标服务器任意位置
2. 确认已 `pip install openai>=1.50`
3. `python test_vllm_cost.py`(或 `python3 test_vllm_cost.py`)

期望
----
脚本会发一条"建设单位名称是否一致"的语义判定请求,模拟皖资规约项目里
`semantic_similar` 规则走 LLM 的真实路径,然后打印:

- HTTP 是否成功
- 返回内容(应该是合法 JSON,含 match/reason 字段)
- usage 三件套:prompt_tokens / completion_tokens / total_tokens

只要 usage 三个字段都 > 0,就证明项目里 `app/core/llm_client.py` 的
`_extract_usage` 能正确拿到 token 数,cost 不会全 0。
"""
from __future__ import annotations

import json
import re
import sys

from openai import OpenAI

# ===== 目标服务器 vLLM 配置(按 testLLM.py 拷过来)=====
BASE_URL = "http://10.5.0.103:30081/29783b28c8e74c7bab7519e4c991af18/v1"
API_KEY = "EMPTY"           # vLLM 不校验,占位即可
MODEL = "qwen3.5-27b"
TIMEOUT = 300
USE_JSON_MODE = True        # 与项目默认 use_json_mode 对齐

# 模拟 R004 semantic_similar 规则的真实请求
SYSTEM_PROMPT = "你是合规审核专家,严格按要求输出 JSON。"
USER_PROMPT = """判断两个"建设单位名称"是否指向同一主体。
允许:简称/全称、行政区划前缀差异、有限公司/有限责任公司互换。
A:贵州省XX建设有限公司
B:XX建设公司
只输出 JSON:{"match": true|false, "reason": "<不超过30字>"}"""


def main() -> int:
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=TIMEOUT)

    kwargs = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "max_tokens": 1024,
        "temperature": 0.1,
        # 关键:vLLM 后端的 enable_thinking 传参位置
        "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
    }
    if USE_JSON_MODE:
        kwargs["response_format"] = {"type": "json_object"}

    print(f"[req] model={MODEL} base_url={BASE_URL}")
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        print(f"[FAIL] LLM 调用失败:{type(e).__name__}: {e}")
        return 2

    # ---- 内容 ----
    content = (resp.choices[0].message.content or "") if resp.choices else ""
    print(f"\n[content] raw:\n{content}")

    parsed = None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    print(f"[content] parsed: {parsed!r}")

    # ---- usage(本脚本的核心检验点)----
    u = resp.usage
    if u is None:
        print("\n[FAIL] response.usage 为 None,目标服务无法提取 token 数")
        return 3

    prompt_tokens = getattr(u, "prompt_tokens", None)
    completion_tokens = getattr(u, "completion_tokens", None)
    total_tokens = getattr(u, "total_tokens", None)
    print("\n[usage]")
    print(f"  prompt_tokens     = {prompt_tokens}")
    print(f"  completion_tokens = {completion_tokens}")
    print(f"  total_tokens      = {total_tokens}")

    ok = all(isinstance(v, int) and v > 0 for v in (prompt_tokens, completion_tokens, total_tokens))
    if not ok:
        print("\n[FAIL] usage 字段缺失或为 0,皖资规约项目里 cost 将为 0")
        return 4

    print("\n[OK] usage 三件套齐全且 > 0,项目 cost 提取逻辑可信")
    return 0


if __name__ == "__main__":
    sys.exit(main())
