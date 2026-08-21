# 规则落库 MySQL + 项目管理设计

日期：2026-08-21
状态：已确认，待实施
前置：`2026-08-20-compare-custom-field-ids-design.md`（本次不改动其确立的字段键契约）

## 背景

当前规则的唯一真源是 `config/rules.yaml`（54 条，812 行）。读路径走 `config.get_rules()`（PyYAML + mtime 缓存），写路径走 `admin_rules.py` → `rules_io`（ruamel round-trip 保注释），写完调 `reload_rules()` 刷缓存。

这套结构有两个限制推动本次改造：

**其一，规则无法组织。** 54 条规则平铺在一个数组里，没有任何分组维度。实际业务里规则是按项目类型划分的，缺了这一层就只能靠 `id` 前缀和人脑记忆。

**其二，文件即真源难以扩展。** 加"所属项目"这类关联信息，在 yaml 里只能靠字符串约定维护，没有外键约束，改个项目名要全文替换。

因此把规则迁到 MySQL，`config/rules.yaml` 保留但**降级为导入导出的载体格式**，同时引入项目实体。

## 设计决策

### 决策 1：一条规则属于 0 或 1 个项目

`rules.project_id` 可空，`NULL` 表示未分组。查询规则时返回单个嵌套的 `project` 对象（未分组为 `null`）。

不做多对多。多对多需要中间表，接口返回 `projects` 数组，UI 上"当前属于哪些项目"的展示和编辑都要复杂一档，而实际诉求是"这条规则归哪个项目管"，一对多足够。

### 决策 2：`rule_id` 保持全局唯一

不改成"项目内唯一"。`POST /compare` 的请求体只有 `rule_id`，没有项目维度；若允许不同项目下 `rule_id` 重名，比对接口将无法定位规则。改变这一点意味着 `/compare` 契约破坏性变更，波及全部上游消费者。

### 决策 3：MySQL 是唯一真源，yaml 只做导入导出

落库后：

- `/compare` 每次从 DB 查规则，**不做进程内缓存**
- 直接编辑 `config/rules.yaml` **不再自动生效**，必须走导入
- `POST /admin/reload-rules` 降级为空操作，仅返回当前规则条数

去掉缓存是因为 mtime 消失后，进程内缓存在多实例部署下没有任何失效机制。而 `/compare` 本就要等 LLM 数百毫秒到数秒，一次主键查询（<1ms）的开销完全被淹没，换来彻底消除缓存一致性问题。

`config.yaml` 第 44-45 行与 README 中承诺"改文件下次请求自动 reload"的表述必须同步修订，否则成为假文档。

### 决策 4：导入是合并 upsert，且全文件单事务

按 `rule_id` 对比：库里有则覆盖，没有则新增；库里有但文件里没有的**保持不动**。因此可以导入只含数条规则的片段文件。

任何一条规则校验不过则**整体回滚**返 422。不允许出现"导入了一半"的中间状态——那种状态既不知道成功了哪几条，也不敢重试。

### 决策 5：导出的 yaml 携带完整项目信息

顶层新增 `projects:` 节点存项目定义，每条规则新增 `project: <key>` 字段。导入时先 upsert 项目，再 upsert 规则。这样导出→导入可完整还原，文件才真正具备备份价值。

规则引用 `key` 而非自增 `id`：自增 id 在开发库与生产库必然不同，跨环境导入会错乱。

现有 `rules.yaml` 无 `projects:` 节点、无 `project:` 字段，导入后全部为未分组——向后兼容。

### 决策 6：自动建库建表，不做字段自动迁移

启动时自动 `CREATE DATABASE IF NOT EXISTS` + `create_all`。表结构后续变更由人工执行 SQL，`docs/schema.sql` 作为参考。

不做字段自动迁移（`ADD COLUMN` / `CREATE INDEX` 的自动对齐）是本次明确排除的范围。

### 决策 7：`/compare` 响应不新增 `project` 字段

比对接口的请求与响应契约一字不动。"查询规则时带所属项目"指规则管理接口（`/admin/rules`），不含比对接口。项目信息以快照形式落在历史表中。

## 数据模型

三张表，`utf8mb4` / `utf8mb4_unicode_ci`。

```sql
CREATE TABLE projects (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  `key`       VARCHAR(64)  NOT NULL UNIQUE,
  name        VARCHAR(128) NOT NULL,
  description VARCHAR(512) NOT NULL DEFAULT '',
  sort_order  INT NOT NULL DEFAULT 0,
  created_at  DATETIME NOT NULL,
  updated_at  DATETIME NOT NULL
);

CREATE TABLE rules (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_id            VARCHAR(64)  NOT NULL UNIQUE,
  project_id         BIGINT NULL,
  name               VARCHAR(128) NOT NULL DEFAULT '',
  description        VARCHAR(512) NOT NULL DEFAULT '',
  field_defs         JSON NULL,
  require_field_name TINYINT(1) NOT NULL DEFAULT 0,
  system_prompt      TEXT NULL,
  prompt_template    TEXT NOT NULL,
  sort_order         INT NOT NULL DEFAULT 0,
  created_at         DATETIME NOT NULL,
  updated_at         DATETIME NOT NULL,
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE TABLE compare_history (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  rule_id      VARCHAR(64)  NOT NULL,
  rule_name    VARCHAR(128) NOT NULL DEFAULT '',
  project_key  VARCHAR(64)  NULL,
  project_name VARCHAR(128) NULL,
  fields       JSON NOT NULL,
  result       TINYINT(1) NOT NULL,
  reason       TEXT NULL,
  time_ms      INT NOT NULL,
  cost         JSON NULL,
  raw_request  JSON NULL,
  created_at   DATETIME NOT NULL,
  INDEX idx_rule_created (rule_id, created_at),
  INDEX idx_created (created_at)
);
```

字段选型理由：

| 决定 | 理由 |
|---|---|
| 项目同时有 `id` 与 `key` | `key` 供 yaml 跨环境引用，`id` 供 DB 内部关联 |
| 规则主键用自增 `id` 而非 `rule_id` | `PUT /admin/rules/{rule_id}` 支持 rename；`rule_id` 若为主键则 rename 变成改主键 |
| `ON DELETE SET NULL` | 删项目时规则变未分组，绝不级联删规则 |
| 历史表不设外键 | 存的是调用当时的快照；规则后续改名/删除/换项目都不应影响既有历史 |
| `field_defs` 用 JSON 列 | 纯 `list[str]`，永远整体读写，不需索引，拆表无收益 |
| `sort_order` | yaml 有序，UI 按此顺序展示。新建取 `max+1`，导入时按文件顺序往后排，**已存在规则的 `sort_order` 不被导入覆盖**——否则导入片段会打乱现有顺序 |

## 架构

### 模块结构

```
app/
  db/                          新增
    session.py                 引擎 / SessionLocal / get_db 依赖
    bootstrap.py               建库 + 建表
    models.py                  Project / Rule / CompareHistory
    repo_projects.py           项目仓储
    repo_rules.py              规则仓储（含 to_rule_cfg）
    repo_history.py            历史仓储
  core/
    config.py                  改：新增 get_db_config()；移除 get_rules / reload_rules
    rules_io.py                改：转为 yaml ↔ dict 纯函数
    rules_sync.py              新增：导入导出编排（解析 → 校验 → 事务内 upsert）
    history.py                 删除：内存实现由 repo_history 取代
  api/
    compare.py                 改：_find_rule_cfg 走 repo_rules
    admin_rules.py             改：CRUD 切到 repo_rules，增 project 字段
    admin_projects.py          新增：项目 CRUD + 批量移入移出
    admin_import_export.py     新增：导入 / 导出
    history.py                 改：走 repo_history
    admin.py                   改：reload-rules 降级为空操作
```

### 单元边界

| 单元 | 职责 | 依赖 |
|---|---|---|
| `db/models.py` | 表结构定义 | SQLAlchemy |
| `db/repo_*.py` | 单表 CRUD 与查询 | `models` |
| `db/repo_rules.to_rule_cfg` | ORM 行 → `app/rules/` 期待的 plain dict | `models` |
| `core/rules_io.py` | `dump_bundle(projects, rules) -> str`、`parse_bundle(text) -> (projects, rules)` | ruamel（纯函数，不碰 DB 与文件系统） |
| `core/rules_sync.py` | 导入导出编排、事务、warning 收集 | `rules_io` + `repo_*` |
| `api/*` | HTTP 契约、错误映射 | `repo_*` / `rules_sync` |

**`app/rules/` 零改动。** `field_keys.py` / `llm_rule.py` / `registry.py` 接收 plain dict，由 `to_rule_cfg` 负责把 ORM 行转回它们熟悉的形状。规则领域逻辑与存储介质解耦——换存储不惊动业务规则，是分层是否正确的判据。

仓储拆三个文件而非合并为 `repo.py`：三个实体的 CRUD 合计约 300 行，拆开后各 80~120 行，各自只认一张表。

`rules_io.py` 保留 ruamel round-trip：导出时 `prompt_template` 必须是 `|` 块格式，否则数百字的 prompt 会挤成一行带 `\n` 的字符串，既不可读也不可改。

### 连接与启动

`config/config.yaml` 新增 `mysql:` 节（沿用工作区已填入的分离字段写法，不用单行连接串——分离字段改起来不易出错，也便于逐项做环境变量覆盖）：

```yaml
mysql:
  host: "127.0.0.1"
  port: 3306
  database: "ai_cross_check"
  username: "root"
  password: "1940"
  charset: "utf8mb4"
  pool_size: 5
  max_overflow: 10
  echo: false          # true 时打印全部 SQL，排错用
```

连接串由 `get_db_config()` 拼装为 `mysql+pymysql://{username}:{password}@{host}:{port}/{database}?charset={charset}`。**密码必须过 `urllib.parse.quote_plus()`**——密码含 `@` `:` `/` `#` 任一字符时，未编码的连接串会被解析错位，且报错信息通常指向"host 不存在"，极难定位。

每项支持同名大写环境变量覆盖：`MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_DATABASE` / `MYSQL_USERNAME` / `MYSQL_PASSWORD`。生产不愿把密码写进文件时用。

引擎必须配 `pool_pre_ping=True` 与 `pool_recycle=3600`：MySQL 默认 8 小时空闲断连，无 pre_ping 时服务闲置一夜后第一个请求必定拿到死连接报 `MySQL server has gone away`。

`bootstrap.py` 启动顺序：

1. 从连接串剥离库名连到 server，执行 `CREATE DATABASE IF NOT EXISTS <db> CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`
2. 用完整连接串建引擎，`Base.metadata.create_all(engine)`（`checkfirst` 默认开启）
3. `create_all` 外包 try/except，兜住多实例首次同时启动的窄竞态窗口

SQLite 无"库"概念，步骤 1 按方言跳过（测试用）。

新增依赖：`sqlalchemy>=2.0`、`pymysql>=1.1`。选 PyMySQL 而非 mysqlclient——纯 Python 无 C 扩展，`docker/offline` 离线构建不需额外编译工具链。

## 接口

### 不变

`POST /compare`（内部改查 DB，契约不动）、`POST /admin/reload-config`。

`POST /admin/reload-rules` 保留但降级为空操作，返回当前规则条数，避免既有运维脚本突然 404。

### 规则

```
GET    /admin/rules?project=<key>     列表，新增筛选
GET    /admin/rules/{rule_id}
POST   /admin/rules
PUT    /admin/rules/{rule_id}
DELETE /admin/rules/{rule_id}
```

返回体每条规则新增嵌套 `project`：

```json
{
  "id": "cross_rule_01",
  "name": "总投资金额一致",
  "description": "...",
  "field_defs": null,
  "require_field_name": false,
  "system_prompt": "...",
  "prompt_template": "...",
  "project": { "key": "zhengfu_touzi", "name": "政府投资项目" }
}
```

未分组时 `project` 为 `null`。筛选参数：`?project=<key>` 查指定项目，`?project=__none__` 查未分组，不传查全部。

`POST` / `PUT` 的 body 接受 `project` 字段（项目 key 或 `null`），单条编辑时即可改归属。

### 项目

```
GET    /admin/projects              列表，每项带 rule_count
GET    /admin/projects/{key}
POST   /admin/projects
PUT    /admin/projects/{key}        支持改 key
DELETE /admin/projects/{key}
```

URL 用 `key` 而非自增 id，与规则接口用 `rule_id` 保持一致。

`key` 校验复用 `admin_rules.py` 中已有的 `^[A-Za-z_][A-Za-z0-9_]*$`（同一个正则，行为一致）。

改 `key` **不影响规则归属**：规则通过 `project_id` 关联，`key` 只是对外标识。但导出的 yaml 引用的是 `key`，故改 key 后旧导出文件再导入会因项目未定义而使相关规则落到未分组（触发 warning，不中断）。

新建项目的 `sort_order` 取当前 `max+1`；导入时按文件中 `projects:` 的先后顺序追加。

`DELETE` 返回受影响规则数：`{"status":"ok","unassigned_rules":12}`，供 UI 提示"已将 12 条规则移出该项目"。

### 规则移入移出

```
POST /admin/projects/{key}/rules    批量移入  body: {"rule_ids": ["a","b"]}
POST /admin/rules/unassign          批量移出  body: {"rule_ids": ["a","b"]}
```

批量端点服务于 UI 的"勾选多条 → 移入项目"。单条改归属走 `PUT /admin/rules/{rule_id}`，两条路并存。

移出不设计为 `DELETE /admin/projects/{key}/rules`，原因有二：带 body 的 DELETE 虽然合法，但部分 HTTP 客户端与代理会静默丢弃其 body；且"移出"本质是把 `project_id` 置 `NULL`，不需要知道原项目是谁，`{key}` 在路径里是冗余信息。

### 导入导出

```
GET  /admin/export?project=<key>     导出 yaml，text/yaml 附件；不传参导全部
POST /admin/import                   multipart 上传 yaml，合并 upsert
```

导出文件名 `rules-YYYYMMDD.yaml`，按项目导出时为 `rules-<key>-YYYYMMDD.yaml`。`?project` 接受与规则列表相同的取值，含 `__none__`（只导未分组规则，此时 `projects:` 节点为空）。按项目导出时 `projects:` 节点只含该项目。

导入的 upsert 依据：项目按 `key`、规则按 `id`。库中已存在同 `key` 的项目，则更新其 `name` / `description`；已存在同 `id` 的规则，则覆盖除 `sort_order` 外的全部字段。

导入返回：

```json
{
  "status": "ok",
  "projects": { "created": 2, "updated": 1 },
  "rules":    { "created": 10, "updated": 5 },
  "warnings": ["规则 cross_rule_09 引用的项目 xxx 未在文件中定义，已置为未分组"]
}
```

### 导出文件格式

```yaml
# AI交叉校验 规则导出
# 导出时间: 2026-08-21 14:30:00
# 导入方式: Web UI「导入」按钮，或 POST /admin/import
# 导入语义: 按 id 合并 upsert，库中存在则覆盖，不存在则新增；
#           库中有而本文件没有的规则保持不动。
# （下略：字段说明，与现有 rules.yaml 头部注释同源，补充 projects / project 两项说明）

projects:
  - key: zhengfu_touzi
    name: 政府投资项目
    description: 政府投资项目审核规则集

rules:
  - id: cross_rule_01
    project: zhengfu_touzi
    name: 总投资金额一致
    description: 判断两个总投资金额是否数值相等(允许单位差异)
    require_field_name: false
    system_prompt: ...
    prompt_template: |
      ...
```

规则字段顺序在 `normalize_rule` 现有 `order` 基础上，于 `id` 之后插入 `project`。

现有 `rules.yaml` 头部那 27 行注释是有价值的文档，但落库后不存在于 DB 中。因此**导出时由代码生成一段固定的头部注释模板**（存于 `rules_io.py`），内容沿用现有注释并补充 `projects` / `project` 两项说明。

## 前端

### 布局

规则管理页改为左右分栏。左栏常驻项目列表（"全部 / 未分组 / 各项目"，每项带规则数），点击切换筛选并高亮选中态。右栏为规则表格。

表格从 5 列扩为 7 列：**勾选框 / ID / 规则名 / 项目 / name 必填 / system_prompt / 操作**。选中任意行后表格下方浮出批量操作条"已选 N 条 [移入 ▾] [移出]"；当前筛选为"未分组"时"移出"置灰。

项目的新建 / 编辑 / 删除走弹窗，入口为左栏标题旁的 `+` 与条目 hover 出的图标。

### 文件

```
ui/index.html    +左栏 DOM、表格两列、批量操作条、项目弹窗、导入结果弹窗
ui/app.js        改：规则列表渲染带 project、编辑弹窗加所属项目下拉
ui/projects.js   新增：项目列表、项目 CRUD、批量移入移出、导入导出
```

`app.js` 现 655 行，项目那套约 250 行若并入将逼近 900 行，故拆出 `projects.js`，`app.js` 继续只管规则与试跑。

### 设计语言

现有 UI 有完整的植物语汇——`--bio-dark` 深绿、米色底、`sway` 摇摆动画、logo 的 `50% 50% 50% 8px` 不对称圆角、空状态"播下第一颗种子"配 `ri-seedling-line`。新增界面全部沿用，不得回落到 Tailwind 默认样式。

具体：项目栏标题配 `ri-plant-line`，项目条目用 `ri-leaf-line`，"未分组"以虚线边框与正式项目做视觉区分，项目空状态延续同一文案调性。

### 交互

**导出**与左栏联动：选中"全部"导全部，选中某项目只导该项目，不额外追问。点击后浏览器直接下载。

**导入**触发隐藏的 `<input type="file" accept=".yaml,.yml">`。完成后：成功且无警告 → toast 提示"新增 N 条，更新 M 条"；有警告 → 弹结果框逐条列出统计与警告。

**顶栏刷新按钮**（`reloadAll()`，现 title 为"手动刷新缓存"）语义变更为重新拉取列表数据，title 改"刷新数据"。

**删项目确认弹窗**须说明后果："删除项目 X，其下 32 条规则将变为未分组（规则本身不会被删除）"，措辞对齐现有删规则弹窗。

## 数据迁移

`rules` 表为空且 `config/rules.yaml` 存在时，启动自动导入，日志记录"首次启动，从 config/rules.yaml 导入 N 条规则"。另提供 `scripts/import_rules.py` 供手工重跑。

限定"表为空"才触发，是为避免"删光规则后重启又全部恢复"。空库等同首次部署。

导入后 54 条规则**全部为未分组**。现有 yaml 无项目信息，不自动创建"默认项目"兜底——未分组是合法状态，项目划分由人在页面上决定。

`config/rules.yaml` 留在原地，身份由"运行时真源"变为"初始数据 + 导入导出模板"。`rules_config_path` 配置项保留，用于首次导入与默认导出路径。

## 错误矩阵

| 场景 | 码 | 行为 |
|---|---|---|
| 启动时连不上 DB | — | 启动失败，日志打脱敏连接串 + 排查建议 |
| 建库 / 建表权限不足 | — | 启动失败，日志直接打出该手工执行的 SQL 与所需权限，不甩 `Access denied` |
| 运行时 DB 断连 | 503 | `pool_pre_ping` 先尝试重连；确实不可用返 503（依赖不可用，非 500） |
| 导入 YAML 语法错 | 422 | 附 ruamel 给出的行号 |
| 导入内容校验失败 | 422 | 指出第几条规则错在何处，**整体回滚** |
| 导入引用未定义项目 | 200 | 不中断，warning + 该规则置未分组 |
| `rule_id` 重复（新建） | 422 | 沿用现有措辞 |
| 项目 `key` 重复 | 422 | 同上 |
| 项目 `key` 非法 | 422 | 复用 `field_defs` 的正则错误措辞 |
| 项目 / 规则不存在 | 404 | 沿用现有措辞 |

`/compare` 原有矩阵（404 / 422 / 502 / 504 / 500）保留，仅新增 503。

## 事务边界

导入、批量移入移出各自单事务。规则与项目的单条 CRUD 各自单事务。

**历史写入失败不得影响 `/compare`。** 现 `compare.py:137` 在算完结果后调 `history.add()`，落库后该行成为一次 DB 写入。若此时库抖动导致写入失败，不应使整个比对请求返 500——LLM 已调用、费用已产生、结果已得出，因记录流水失败而丢弃结果不合理。故历史写入单独包 try/except，失败仅 `log.error`，照常返回比对结果。

## 测试策略

仓储层与集成测试跑 **SQLite in-memory**，通过 FastAPI 依赖覆盖注入，`pytest` 不依赖真实 MySQL。

两个必须处理的方言差异：

- SQLite 默认**不启用外键约束**，`ON DELETE SET NULL` 会静默失效，导致"删项目 → 规则变未分组"形同虚测。须挂 connect 事件执行 `PRAGMA foreign_keys=ON`
- SQLite 无"库"概念，`bootstrap` 建库步骤按方言跳过

| 文件 | 覆盖 |
|---|---|
| `tests/unit/test_rules_io.py`（新建） | yaml ↔ dict 转换、round-trip、`\|` 块格式保持 |
| `tests/integration/test_admin_projects.py`（新建） | 项目 CRUD、改 key、批量移入移出、删项目后规则变未分组 |
| `tests/integration/test_import_export.py`（新建） | upsert 语义、片段导入不打乱 `sort_order`、非法规则整体回滚、未定义项目告警 |
| `tests/integration/test_admin_rules.py`（改造） | yaml CRUD → DB CRUD，新增 `project` 字段与筛选 |
| `tests/integration/test_compare_api.py`（改造） | 规则来源改 DB，其余断言不变 |

必须覆盖的关键用例：

- 导出→导入 round-trip 完整还原（含项目归属）
- 导入片段**不改变**已存在规则的 `sort_order`
- 导入文件含一条非法规则时，DB 状态**完全不变**
- 删项目后规则仍在，仅 `project_id` 变 `NULL`
- `rule_id` 全局唯一约束生效

## 文档同步

以下位置对"yaml 是真源"或"改文件自动生效"的表述必须一并修订，否则成为假文档：

| 位置 | 处理 |
|---|---|
| `config/config.yaml` 第 44-45 行注释 | 改为说明 yaml 仅供导入导出 |
| `README.md` 相关段落 | 同上 |
| `api.md` | 新增项目、导入导出接口；规则接口补 `project` 字段；`reload-rules` 标注降级 |
| `config/rules.yaml` 头部注释 | 补充 `projects` / `project` 说明，与导出模板同源 |
| `.env.example` | 补 `MYSQL_*` 五项环境变量说明 |
| `docs/schema.sql` | 新建，作为人工执行 DDL 的参考 |

## 上线提示

对**上游消费者**：`/compare` 契约不变，无需改动。

对**运维**：两项行为变更需知晓——直接编辑 `config/rules.yaml` 不再自动生效（须走导入）；`POST /admin/reload-rules` 变为空操作。首次部署需确保应用账号具备 `CREATE` 权限，否则建库失败导致启动失败。
