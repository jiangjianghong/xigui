# 规则配置 field_defs 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 配置规则时先用"+"添加字段(填字段名),再点字段 chip 插入占位符;字段定义落盘 `field_defs`,配置校验与试跑页联动。

**Architecture:** 规则 yaml 新增可选 `field_defs: list[str]`(下标即 `{field_N}` 下标),后端只做持久化透传(`/compare` 不读它);前端配置弹窗以字段列表为核心驱动 chip 生成与保存校验;试跑页优先用 `field_defs` 生成输入框。设计文档:`docs/superpowers/specs/2026-06-10-rule-config-field-defs-design.md`。

**Tech Stack:** FastAPI + Pydantic v2 + ruamel.yaml(后端);原生 JS + Tailwind 静态页(前端,无测试框架);pytest(后端测试,本地 `uv run pytest`)。

---

## 背景知识(给零上下文的实施者)

- 规则存于 `config/rules.yaml`,CRUD 由 `app/api/admin_rules.py` 提供,经 `app/core/rules_io.py`(ruamel round-trip,保留注释)读写。
- `normalize_rule()`(`app/core/rules_io.py:72`)按 `order` 列表固定字段顺序写 yaml,**未列出的字段也会兜底保留**。
- 前端是 `ui/index.html` + `ui/app.js` 纯静态页,由 FastAPI 挂载在 `/ui`(`app/main.py:72`)。无前端测试框架,验证手段是 `node --check` + 手动验收。
- 试跑页(Playground)当前从 `prompt_template` 反推字段个数(`ui/app.js:265-266`)。
- 测试隔离:`tests/integration/test_admin_rules.py` 的 `isolated_rules` fixture 把 rules.yaml 拷到 tmp_path,不污染真实配置。
- 本地跑测试:`uv run pytest tests/integration/test_admin_rules.py -v`(Windows bash)。

---

### Task 1: 后端 `field_defs` 持久化(TDD)

**Files:**
- Modify: `app/api/admin_rules.py`(RuleIn + 两处 model_dump)
- Modify: `app/core/rules_io.py:81-84`(order 列表)
- Test: `tests/integration/test_admin_rules.py`

- [ ] **Step 1: 写失败测试**

在 `tests/integration/test_admin_rules.py` 末尾(`test_create_then_compare_uses_new_rule` 之后)追加:

```python
# ---------------------------------------------------------------------------
# field_defs 持久化
# ---------------------------------------------------------------------------

def test_create_rule_with_field_defs(client, isolated_rules):
    """带 field_defs 创建 → 读取/落盘往返一致。"""
    payload = dict(NEW_RULE, field_defs=["申报金额", "批复金额", "合同金额"])
    resp = client.post("/admin/rules", json=payload)
    assert resp.status_code == 201

    body = client.get("/admin/rules/R999").json()
    assert body["field_defs"] == ["申报金额", "批复金额", "合同金额"]

    # yaml 文件里也确实写进去了
    with isolated_rules.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    saved = next(r for r in data["rules"] if r["id"] == "R999")
    assert saved["field_defs"] == ["申报金额", "批复金额", "合同金额"]


def test_create_rule_without_field_defs_omits_key(client, isolated_rules):
    """不带 field_defs 创建 → yaml 里不应出现 field_defs: null 的脏键。"""
    resp = client.post("/admin/rules", json=NEW_RULE)
    assert resp.status_code == 201

    body = client.get("/admin/rules/R999").json()
    assert "field_defs" not in body


def test_update_rule_field_defs(client, isolated_rules):
    """PUT 可以更新 field_defs。"""
    create = client.post(
        "/admin/rules", json=dict(NEW_RULE, field_defs=["甲", "乙"])
    )
    assert create.status_code == 201

    payload = dict(NEW_RULE, field_defs=["甲", "乙", "丙"])
    resp = client.put("/admin/rules/R999", json=payload)
    assert resp.status_code == 200

    after = client.get("/admin/rules/R999").json()
    assert after["field_defs"] == ["甲", "乙", "丙"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/integration/test_admin_rules.py -k field_defs -v`
Expected: 3 个测试 FAIL——`test_create_rule_with_field_defs` 和 `test_update_rule_field_defs` 因 Pydantic 默认忽略未知字段,GET 返回里没有 `field_defs` 键(KeyError / assert 失败);`test_create_rule_without_field_defs_omits_key` 此时恰好会过,没关系,关注前两个红。

- [ ] **Step 3: 实现**

`app/api/admin_rules.py` 的 `RuleIn`,在 `require_field_name` 字段定义之前加:

```python
    field_defs: list[str] | None = Field(
        default=None,
        description="字段定义列表,下标即 {field_N} 占位符下标,元素为字段业务名(可为空串)",
    )
```

同文件两处 `payload.model_dump()`(`create_rule` 里一处、`update_rule` 里一处)都改为:

```python
payload.model_dump(exclude_none=True)
```

(不带 `field_defs` 时键整体不落盘,避免 yaml 出现 `field_defs: null`。)

`app/core/rules_io.py` 的 `order` 列表加入 `field_defs`(放 `description` 之后):

```python
    order = [
        "id", "name", "description", "field_defs",
        "require_field_name", "system_prompt", "prompt_template",
    ]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/integration/test_admin_rules.py -v`
Expected: 全部 PASS(含原有 14 个用例——回归确认 `exclude_none` 没破坏现有行为)。

- [ ] **Step 5: 提交**

```bash
git add app/api/admin_rules.py app/core/rules_io.py tests/integration/test_admin_rules.py
git commit -m "feat: 规则新增可选 field_defs 字段并持久化"
```

---

### Task 2: 配置弹窗字段定义区 + 动态 chip

**Files:**
- Modify: `ui/index.html:213-226`(prompt_template 区块前加字段定义区;静态 chip 改为动态容器;`#f-require` 加 onchange)
- Modify: `ui/app.js`(新增渲染/增删函数;改 `fillForm`)

- [ ] **Step 1: 改 index.html**

在 `<!-- prompt_template -->` 区块(`ui/index.html:213`)**之前**插入字段定义区:

```html
                <!-- field_defs -->
                <div>
                    <div class="field-label"><span class="name">字段定义</span><span class="required">*</span></div>
                    <p class="field-help">先在这里添加本规则要比对的字段(填业务名,如"申报金额"),下方模板区会生成对应 chip,点击即插入 <code>{field_N}</code> 占位符。</p>
                    <div id="f-fielddefs" class="space-y-2"></div>
                    <button type="button" class="btn-ghost mt-2" onclick="addFieldDef()">
                        <i class="ri-add-line"></i> 添加字段
                    </button>
                </div>
```

`#f-require` 的 `<select>`(`ui/index.html:200`)加 onchange:

```html
                    <select id="f-require" class="input" onchange="renderChips()">
```

prompt_template 区块里写死的 chip 行(`ui/index.html:217-224`,即整个 `<div class="mb-2">...</div>`)替换为动态容器(保留 JSON 范式 chip,由 JS 一并渲染):

```html
                    <div id="f-chips" class="mb-2"></div>
```

- [ ] **Step 2: app.js 加字段定义渲染/增删函数**

在 `ui/app.js` 的 `// 占位符 chip 插入到 prompt textarea` 注释(194 行附近)**之前**插入:

```js
// ---------------------------------------------------------------------------
// 字段定义区(modal)
// ---------------------------------------------------------------------------

function getFieldDefs() {
    return Array.from(document.querySelectorAll('#f-fielddefs .fd-name'))
        .map(el => el.value);
}

function renderFieldDefRows(defs) {
    const box = document.getElementById('f-fielddefs');
    const single = defs.length <= 1;  // 仅剩 1 行时禁用删除
    box.innerHTML = defs.map((name, i) => `
        <div class="flex items-center gap-2">
            <code class="font-mono text-xs text-surface-500 w-20 shrink-0">{field_${i}}</code>
            <input class="input fd-name" data-idx="${i}" value="${escape(name)}"
                   placeholder="字段名,如:申报金额" oninput="renderChips()">
            <button type="button" class="btn-ghost text-red-600 shrink-0" title="删除该字段"
                    onclick="removeFieldDef(${i})" ${single ? 'disabled' : ''}>
                <i class="ri-close-line"></i>
            </button>
        </div>
    `).join('');
    renderChips();
}

function addFieldDef() {
    renderFieldDefRows([...getFieldDefs(), '']);
}

function removeFieldDef(i) {
    // 基础版:仅删行重排;Task 3 会替换为带模板占位符自动调整的版本
    const defs = getFieldDefs();
    if (defs.length <= 1) return;
    defs.splice(i, 1);
    renderFieldDefRows(defs);
}

function renderChips() {
    const defs = getFieldDefs();
    const requireName = document.getElementById('f-require').value === 'true';
    const box = document.getElementById('f-chips');
    box.innerHTML = defs.map((name, i) => {
        const label = escape(name.trim() || `字段${i}`);
        const nameChip = requireName
            ? `<span class="chip" title="插入 {field_${i}_name}" onclick="insertPlaceholder('{field_${i}_name}')">${label}·名</span>`
            : '';
        return `<span class="chip" title="插入 {field_${i}}" onclick="insertPlaceholder('{field_${i}}')">${label}</span>${nameChip}`;
    }).join('') + `<span class="chip" onclick="insertPlaceholder('{{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;<不超过30字>&quot;}}')">JSON 输出范式</span>`;
}
```

- [ ] **Step 3: fillForm 回填字段定义(兼容旧规则)**

`ui/app.js` 的 `fillForm`(135-142 行)末尾追加:

```js
    // 字段定义:优先 field_defs;旧规则从模板反推个数,字段名留空
    let defs = Array.isArray(r.field_defs) && r.field_defs.length ? [...r.field_defs] : null;
    if (!defs) {
        const m = (r.prompt_template || '').match(/\{field_(\d+)(?:_name)?\}/g);
        const count = m ? Math.max(...m.map(s => parseInt(s.match(/\d+/)[0]))) + 1 : 2;
        defs = Array.from({length: count}, () => '');
    }
    renderFieldDefRows(defs);
```

(注意:`openCreate` 传入的空规则会走反推分支得到默认 2 行空字段,无需单独处理。)

- [ ] **Step 4: 语法检查**

Run: `node --check ui/app.js`
Expected: 无输出(语法 OK)。

- [ ] **Step 5: 提交**

```bash
git add ui/index.html ui/app.js
git commit -m "feat(ui): 配置弹窗增加字段定义区,占位符 chip 按字段动态生成"
```

---

### Task 3: 删除字段时模板占位符自动调整

**Files:**
- Modify: `ui/app.js`(Task 2 加的字段定义区代码块内,`addFieldDef` 之后)

- [ ] **Step 1: 实现 removeFieldDef**

把 `ui/app.js` 中 Task 2 写的基础版 `removeFieldDef` **整个函数替换**为:

```js
function removeFieldDef(i) {
    const defs = getFieldDefs();
    if (defs.length <= 1) return;

    const ta = document.getElementById('f-prompt');
    // 被删字段仍被模板引用 → 提醒(占位符会悬空,需手动清理)
    const usedRe = new RegExp(`\\{field_${i}(_name)?\\}`);
    if (usedRe.test(ta.value)) {
        if (!confirm(`模板仍引用 {field_${i}},删除后该占位符将失效,需手动清理。确认删除吗?`)) return;
    }
    // 更大下标自动减一:{field_3} → {field_2}(含 _name 变体)
    ta.value = ta.value.replace(/\{field_(\d+)(_name)?\}/g, (m, n, suffix) => {
        n = parseInt(n);
        return n > i ? `{field_${n - 1}${suffix || ''}}` : m;
    });

    defs.splice(i, 1);
    renderFieldDefRows(defs);
}
```

(JSON 示例的 `{{ }}` 双大括号不匹配 `\{field_(\d+)(_name)?\}` 正则,不受影响。)

- [ ] **Step 2: 语法检查**

Run: `node --check ui/app.js`
Expected: 无输出。

- [ ] **Step 3: 提交**

```bash
git add ui/app.js
git commit -m "feat(ui): 删除字段时模板占位符下标自动前移,悬空引用提醒"
```

---

### Task 4: 保存校验 + payload 带 field_defs

**Files:**
- Modify: `ui/app.js`(`readForm` 148-157 行、`saveRule` 159-191 行)

- [ ] **Step 1: readForm 增加 field_defs**

`ui/app.js` 的 `readForm` 返回对象里加一行(`prompt_template` 之前):

```js
        field_defs: getFieldDefs().map(s => s.trim()),
```

- [ ] **Step 2: saveRule 增加校验**

在 `saveRule` 现有的 `if (!payload.prompt_template.trim())` 校验**之后**、`{field_N_name}` 友好提示**之前**插入:

```js
    // 字段定义校验
    const defCount = payload.field_defs.length;
    const phMatches = payload.prompt_template.match(/\{field_(\d+)(?:_name)?\}/g) || [];
    const maxIdx = phMatches.length
        ? Math.max(...phMatches.map(s => parseInt(s.match(/\d+/)[0]))) : -1;
    if (maxIdx >= defCount) {
        toast(`模板引用了 {field_${maxIdx}},但只定义了 ${defCount} 个字段(最大下标 ${defCount - 1})`, 'error');
        return;
    }
    if (payload.require_field_name && payload.field_defs.some(n => !n)) {
        toast('require_field_name=true 时,所有字段名必填', 'error');
        return;
    }
    // 定义了但模板没用上 → 非阻断提醒
    const usedIdx = new Set(phMatches.map(s => parseInt(s.match(/\d+/)[0])));
    const unused = payload.field_defs
        .map((n, i) => usedIdx.has(i) ? null : `{field_${i}}${n ? `(${n})` : ''}`)
        .filter(Boolean);
    if (unused.length && !confirm(`以下字段已定义但模板未引用:${unused.join('、')}。确认继续保存吗?`)) return;
```

- [ ] **Step 3: 语法检查**

Run: `node --check ui/app.js`
Expected: 无输出。

- [ ] **Step 4: 提交**

```bash
git add ui/app.js
git commit -m "feat(ui): 保存规则时校验占位符下标越界/字段名必填/未引用提醒"
```

---

### Task 5: 试跑页联动 field_defs

**Files:**
- Modify: `ui/app.js`(`onPgRuleChange` 249-274 行)

- [ ] **Step 1: 输入框数量/标签/预填名取自 field_defs**

把 `onPgRuleChange` 中"从 prompt_template 推断字段数量"到函数结尾的代码(`ui/app.js:264-273`)替换为:

```js
    // 字段输入框:优先 field_defs(数量+业务名标签);旧规则回落模板反推
    const defs = Array.isArray(r.field_defs) && r.field_defs.length ? r.field_defs : null;
    let count;
    if (defs) {
        count = defs.length;
    } else {
        const matches = r.prompt_template.match(/\{field_\d+\}/g);
        count = matches ? Math.max(...matches.map(m => parseInt(m.match(/\d+/)[0]))) + 1 : 2;
    }
    container.innerHTML = Array.from({length: count}, (_, i) => {
        const label = defs && defs[i] ? escape(defs[i]) : `fields[${i}]`;
        const preName = defs && defs[i] ? escape(defs[i]) : '';
        return `
        <div class="card p-3">
            <label class="field-label"><span class="name">${label}</span>${r.require_field_name ? '<span class="required">*</span>' : ''}</label>
            ${r.require_field_name ? `<input class="input pg-field-name mb-2" data-idx="${i}" placeholder="字段名" value="${preName}">` : ''}
            <textarea class="input pg-field-value" data-idx="${i}" rows="2" placeholder="${label} 的值"></textarea>
        </div>`;
    }).join('');
```

(`runPlayground` 不用动——name 仍从输入框读,预填值可被用户修改。)

- [ ] **Step 2: 语法检查**

Run: `node --check ui/app.js`
Expected: 无输出。

- [ ] **Step 3: 提交**

```bash
git add ui/app.js
git commit -m "feat(ui): 试跑页输入框联动 field_defs(业务名标签+name 预填)"
```

---

### Task 6: 文档更新 + 全量回归 + 手动验收

**Files:**
- Modify: `docs/api/rules.md:33-43`(§2 字段表加 field_defs 行)

- [ ] **Step 1: rules.md §2 字段表加一行**

在 `docs/api/rules.md` §2 表格中 `description` 行之后插入:

```markdown
| `field_defs` | ⭕ | 字符串数组。字段定义列表,**下标即 `{field_N}` 占位符下标**,元素为字段业务名。供管理 UI 生成配置 chip 与试跑页输入框标签;`/compare` 运行时不读取,不影响判定 |
```

- [ ] **Step 2: 全量后端回归**

Run: `uv run pytest -v`
Expected: 全部 PASS。

- [ ] **Step 3: 手动验收**

启动服务:`uv run uvicorn app.main:app --port 6549`,浏览器开 `http://localhost:6549/ui/`(注意强刷 Ctrl+F5 避免旧 app.js 缓存),逐项核对:

| # | 操作 | 预期 |
|---|---|---|
| 1 | 新增规则 | 字段定义区默认 2 行空字段;仅剩 1 行时 ✕ 禁用 |
| 2 | 点"+ 添加字段"两次,给 4 个字段填名 | chip 区出现 4 个以字段名命名的 chip + JSON 范式 chip |
| 3 | 点字段 chip | 光标处插入对应 `{field_N}` |
| 4 | require_field_name 切 true | 每个 chip 旁出现"名"chip,点击插 `{field_N_name}` |
| 5 | 模板写 `{field_5}` 后保存 | 阻止保存,toast 提示下标越界 |
| 6 | 删除中间字段(模板已引用更大下标) | 更大下标自动减一;被删字段若被引用先弹 confirm |
| 7 | 保存带 4 字段的规则 → 重新打开编辑 | 字段名原样回显 |
| 8 | 试跑页选该规则 | 4 个输入框,标签是业务字段名;require=true 时 name 预填 |
| 9 | 编辑旧规则(R001,无 field_defs) | 反推出 2 行空字段名,功能正常 |
| 10 | 试跑旧规则 | 行为与改动前一致(`fields[i]` 标签) |

- [ ] **Step 4: 提交**

```bash
git add docs/api/rules.md
git commit -m "docs: rules.md 补充 field_defs 字段说明"
```
