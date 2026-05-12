# 开发指南

## 本地开发环境

推荐两种方式之一：

### 方式 A：容器内开发（与生产一致，推荐）

```bash
cp .env.example .env
# 编辑 config/config.yaml 填入 llm.api_key

make build
make up
make sh        # 进入容器
```

代码挂载已开启，在主机用任意编辑器修改 `app/` 源文件即可，保存后执行 `make restart`（或在容器内手动重启进程）。
修改 `config/rules.yaml` 后无需任何动作，下一次请求自动 reload。

### 方式 B：宿主机虚拟环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
python -m app.main
```

## 代码规范与测试

```bash
make lint                       # ruff + mypy（容器内）
make test                       # pytest（容器内）

# 本地直跑
python -m pytest tests/ -v
python -m pytest tests/unit/ -v         # 仅单元测试
python -m pytest tests/integration/ -v  # 仅集成测试（mock 模式）
```

## 目录约定

| 目录 | 放什么 |
| --- | --- |
| `app/api` | FastAPI 路由：`/compare`、`/health`、`/admin/*` |
| `app/core` | 配置加载（`config.py`）、LLM 客户端（`llm_client.py`）、日志（`logging.py`） |
| `app/rules` | 规则引擎：`base.py`（抽象类）、`registry.py`（注册表）、各种 `*_match.py` 实现 |
| `app/schemas` | 请求/响应 Pydantic 模型 |
| `app/utils` | 与业务无关的纯函数（归一化、数值/日期解析等） |
| `config/` | 配置文件，`config.yaml` + `rules.yaml`（活配置） |

## 如何添加一条规则

### 情形 1：复用已有 type，只是参数不同

最常见情形——直接在 `config/rules.yaml` 加一项：

```yaml
- id: "R006"
  name: "项目名称基本一致"
  type: "semantic_similar"               # 复用已有 type
  expected_field_names: ["项目名称", "项目名称"]
  params: {strictness: "loose"}
  prompt_template: |
    判断两个"项目名称"是否指向同一项目（容忍措辞差异）。
    A: {a_value}
    B: {b_value}
    严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}
```

保存即生效（mtime 自动 reload）。可手动验证：

```bash
curl -X POST http://localhost:6549/admin/reload-rules
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R006","field_a":{"name":"项目名称","value":"X 项目"},"field_b":{"name":"项目名称","value":"X项目"}}'
```

### 情形 2：需要一种**新的 type**（如金额范围比较）

五步法：

1. **判断要不要调 LLM**：
   - 能用 Python 直算就别调 LLM（省钱、可测、稳定）
   - 涉及"语义"判断（同义改写、模糊一致）才走 LLM
2. **新建实现文件**：`app/rules/<type_name>.py`
   ```python
   from app.rules.base import CompareResult, Rule
   from app.rules.registry import register

   @register
   class MyNewRule(Rule):
       rule_type = "my_new_type"

       def compare(self, a_value: str, b_value: str) -> CompareResult:
           # 你的判定逻辑
           return CompareResult(match=..., reason="...")
   ```
3. **在 `app/rules/__init__.py` 加 import**，触发 `@register`：
   ```python
   from app.rules import ..., my_new_type   # noqa: F401
   ```
4. **加单元测试**：`tests/unit/test_my_new_type.py`，至少覆盖 1 正 1 负 + 边界。
5. **在 `config/rules.yaml` 加规则项**，调 `POST /admin/reload-rules` 或重启容器。

### 情形 3：语义规则需要新的 system prompt

如果新规则的"角色定位"与已有 `semantic_similar` / `semantic_exact` 不同（如审计专家、法务专家）：

- 复用 `app/rules/semantic_base.py` 的 `SemanticRule` 基类
- 新建子类，声明独立的 `rule_type` 和 `system_prompt`
- 业务相关的"什么算一致"放进 yaml 的 `prompt_template`

## 调试技巧

- **看日志**：`tail -f logs/wanzi_guizhe.log` 同时观察 docker stdout。日志含 `rule_id / match / ms / usage / reason`，可 grep。
- **DEBUG 级别**：把 `config/config.yaml` 的 `app.log_level` 改成 `debug`，会打印 LLM 原始响应、normalize 后的中间值等。
- **JSON 模式不工作**：如果某些模型不支持 `response_format=json_object`，在 `config.yaml` 的 `llm` 节加 `use_json_mode: false`，依靠 prompt 强约束即可（系统已有正则兜底）。
- **测试模式**：`tests/integration/test_compare_api.py` 用 `monkeypatch` 替换 `chat_json`，无需真实 LLM key 也能跑通全部规则。
