# 开发指南

## 本地开发环境

推荐两种方式之一:

### 方式 A:容器内开发(与生产一致,推荐)

```bash
cp .env.example .env
# 编辑 config/config.yaml 填入 llm.api_key

make build
make up
make sh        # 进入容器
```

代码挂载已开启,在主机用任意编辑器修改 `app/` 源文件即可,保存后执行 `make restart`(或在容器内手动重启进程)。
修改 `config/rules.yaml` 后**无需任何动作**,下一次请求自动 reload。

### 方式 B:宿主机虚拟环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
python -m app.main
```

## 代码规范与测试

```bash
make lint                       # ruff + mypy(容器内)
make test                       # pytest(容器内)

# 本地直跑
python -m pytest tests/ -v
python -m pytest tests/unit/ -v         # 仅单元测试
python -m pytest tests/integration/ -v  # 仅集成测试(mock 模式)
```

## 目录约定

| 目录 | 放什么 |
| --- | --- |
| `app/api` | FastAPI 路由:`/compare`、`/health`、`/admin/reload-*`、`/admin/rules/*`(CRUD) |
| `app/core` | 配置加载(`config.py`)、LLM 客户端(`llm_client.py`)、日志(`logging.py`)、rules.yaml round-trip 读写(`rules_io.py`) |
| `app/rules` | 规则引擎:`base.py`(数据载体)、`llm_rule.py`(唯一 Rule 实现)、`registry.py`(构造 + require_field_name 读取) |
| `app/schemas` | 请求/响应 Pydantic 模型 |
| `config/` | 配置文件,`config.yaml` + `rules.yaml`(活配置) |
| `ui/` | Web 管理界面(单页 HTML + JS + Tailwind) |

## 如何添加/修改规则

**不需要改任何代码**。所有规则都走 LLM,判定逻辑由 `prompt_template` 描述。两种维护方式任选:

### 方式 1:Web UI(推荐非开发者使用)

打开 `http://localhost:6549/ui/`,进入"规则管理"页:

- **新增**:点右上角"新增规则",填写各字段(每个字段下方有用法说明),"保存"即生效
- **修改**:点对应行的"编辑",改完保存
- **删除**:点对应行的"删除",二次确认后删除
- **试跑**:切到"规则试跑"页,选规则、填两个字段值,看 LLM 实际输出,适合调 prompt 时反复迭代

UI 保存时会**保留 `rules.yaml` 顶部的字段说明注释和字段顺序**(用 ruamel.yaml round-trip),不会把文档级注释冲掉。

### 方式 2:直接编辑 `config/rules.yaml`

```yaml
- id: "R006"
  name: "项目名称基本一致"
  description: "判断两个项目名称是否指向同一项目"
  expected_field_names: ["项目名称", "项目名称"]
  require_field_name: false
  system_prompt: "你是熟悉政府投资项目文件比对的合规审核员,擅长项目名称的字面与语义一致性判定。"
  prompt_template: |
    判断两个"项目名称"是否指向同一项目。
    允许差异:措辞微调、空白、标点。
    不允许差异:关键字差异(如"道路改造"与"道路新建")。
    A: {a_value}
    B: {b_value}
    严格输出 JSON: {{"match": true|false, "reason": "<不超过30字>"}}
```

保存即生效(mtime 自动 reload)。可手动验证:

```bash
curl -X POST http://localhost:6549/admin/reload-rules
curl -X POST http://localhost:6549/compare -H "Content-Type: application/json" \
  -d '{"rule_id":"R006","field_a":{"value":"X 项目"},"field_b":{"value":"X项目"}}'
```

prompt 编写要点见 [`docs/api/rules.md §4`](../api/rules.md)。

### 关于 system_prompt

每条规则建议按自身领域显式声明 system_prompt(如"你是注册会计师"、"你是法律顾问"),空字符串/不写则不向模型发送 system 消息。
**注意**:旧版本曾内置过一个"合规审核专家"默认值,现已移除——避免不同领域规则共用同一个不太贴切的身份。

## 调试技巧

- **看日志**:`tail -f logs/ai_cross_check.log` 同时观察 docker stdout。日志含 `rule_id / match / ms / usage / reason`,可 grep
- **DEBUG 级别**:把 `config/config.yaml` 的 `app.log_level` 改成 `debug`,会打印 LLM 原始响应片段
- **JSON 模式不工作**:如果某些模型不支持 `response_format=json_object`,在 `config.yaml` 的 `llm` 节加 `use_json_mode: false`,依靠 prompt 强约束即可(系统有正则兜底)
- **测试模式**:`tests/integration/test_compare_api.py` 用 `monkeypatch` 替换 `chat_json`,无需真实 LLM key 也能跑通全部规则
