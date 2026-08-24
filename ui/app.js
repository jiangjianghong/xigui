// AI交叉校验 · 规则管理 + Playground
// 与后端的契约:
//   GET  /admin/rules           列表
//   GET  /admin/rules/{id}      详情
//   POST /admin/rules           新增
//   PUT  /admin/rules/{id}      修改
//   DELETE /admin/rules/{id}    删除
//   POST /compare               试跑

const API = '';  // 同源,空前缀

// ---------------------------------------------------------------------------
// 通用工具
// ---------------------------------------------------------------------------

function toast(msg, type = 'info') {
    const el = document.createElement('div');
    el.className = `toast toast-${type}`;
    el.textContent = msg;
    document.getElementById('toast-area').appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

async function api(method, path, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const resp = await fetch(API + path, opts);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
    if (!resp.ok) {
        const err = new Error((data && data.detail) || `HTTP ${resp.status}`);
        err.status = resp.status;
        err.body = data;
        throw err;
    }
    return data;
}

function escape(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// ---------------------------------------------------------------------------
// Tab 切换
// ---------------------------------------------------------------------------

function switchTab(tab) {
    for (const t of ['rules', 'playground', 'history']) {
        document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
        document.getElementById(`page-${t}`).classList.toggle('hidden', t !== tab);
    }
    if (tab === 'playground') refreshPgRules();
    if (tab === 'history') {
        refreshHistoryFilter();
        loadHistory();
    }
}

// ---------------------------------------------------------------------------
// 规则列表
// ---------------------------------------------------------------------------

let RULES = [];
let RULES_TOTAL_COUNT = 0;   // 不受项目筛选影响的总数，项目栏「全部」用它

async function loadRules() {
    try {
        const q = (typeof projectQuery === 'function') ? projectQuery() : '';
        const data = await api('GET', '/admin/rules' + q);
        RULES = data.rules || [];
        // 「全部」的计数不能用筛选后的结果，得单独取一次
        if (q) {
            const all = await api('GET', '/admin/rules');
            RULES_TOTAL_COUNT = all.count;
        } else {
            RULES_TOTAL_COUNT = data.count;
        }
        renderRules();
        if (typeof renderProjectList === 'function') renderProjectList();
    } catch (e) {
        toast('加载规则失败: ' + e.message, 'error');
    }
}

function renderRules() {
    const tbody = document.getElementById('rules-tbody');
    const empty = document.getElementById('rules-empty');
    if (!RULES.length) {
        tbody.innerHTML = '';
        empty.classList.remove('hidden');
        return;
    }
    empty.classList.add('hidden');
    tbody.innerHTML = RULES.map(r => {
        const reqBadge = r.require_field_name
            ? '<span class="badge badge-warning">true</span>'
            : '<span class="badge badge-gray">false</span>';
        const sysBadge = (r.system_prompt && r.system_prompt.trim())
            ? `<span class="badge badge-info" title="${escape(r.system_prompt)}">已配置</span>`
            : '<span class="badge badge-gray">空</span>';
        return `<tr class="table-row">
            <td><code class="font-mono text-brand-700">${escape(r.id)}</code></td>
            <td>${escape(r.name || '')}</td>
            <td>${reqBadge}</td>
            <td>${sysBadge}</td>
            <td>
                <button class="btn-ghost" onclick="openEdit('${escape(r.id)}')"><i class="ri-edit-line"></i> 编辑</button>
                <button class="btn-ghost btn-ghost-danger" onclick="openDel('${escape(r.id)}')"><i class="ri-delete-bin-line"></i> 删除</button>
            </td>
        </tr>`;
    }).join('');
}

// ---------------------------------------------------------------------------
// 新增 / 编辑 Modal
// ---------------------------------------------------------------------------

let MODAL_MODE = 'create';   // 'create' | 'edit'
let MODAL_ORIG_ID = null;

function openCreate() {
    MODAL_MODE = 'create';
    MODAL_ORIG_ID = null;
    document.getElementById('modal-title').textContent = '新增规则';
    document.getElementById('f-id').disabled = false;
    fillForm({ id: '', name: '', description: '',
               require_field_name: false, system_prompt: '', prompt_template: '' });
    document.getElementById('rule-modal').classList.remove('hidden');
}

async function openEdit(id) {
    MODAL_MODE = 'edit';
    MODAL_ORIG_ID = id;
    try {
        const rule = await api('GET', `/admin/rules/${encodeURIComponent(id)}`);
        document.getElementById('modal-title').textContent = `编辑规则 ${id}`;
        document.getElementById('f-id').disabled = false;  // 允许 rename(下游缓存风险由保存时确认)
        fillForm(rule);
        document.getElementById('rule-modal').classList.remove('hidden');
    } catch (e) {
        toast('加载规则失败: ' + e.message, 'error');
    }
}

function fillForm(r) {
    document.getElementById('f-id').value = r.id || '';
    document.getElementById('f-name').value = r.name || '';
    document.getElementById('f-desc').value = r.description || '';
    document.getElementById('f-require').value = r.require_field_name ? 'true' : 'false';
    document.getElementById('f-system').value = r.system_prompt || '';
    document.getElementById('f-prompt').value = r.prompt_template || '';

    // 字段行:优先 field_defs(自定义 ID);没配则按模板反推个数,ID 留空表示用字母键。
    // field_defs 比模板占位符少时(手改 yaml),按占位符补齐空行,暴露不一致。
    const inferred = inferKeyCount(r.prompt_template);
    let defs = Array.isArray(r.field_defs) && r.field_defs.length ? [...r.field_defs] : [];
    if (!defs.length) {
        defs = Array.from({length: inferred || 2}, () => '');
    } else {
        while (defs.length < inferred) defs.push('');
    }
    renderFieldDefRows(defs);
}

function closeModal() {
    document.getElementById('rule-modal').classList.add('hidden');
}

function readForm() {
    return {
        id: document.getElementById('f-id').value.trim(),
        name: document.getElementById('f-name').value.trim(),
        description: document.getElementById('f-desc').value.trim(),
        require_field_name: document.getElementById('f-require').value === 'true',
        system_prompt: document.getElementById('f-system').value,
        field_defs: getFieldDefs().map(s => s.trim()),
        prompt_template: document.getElementById('f-prompt').value,
    };
}

async function saveRule() {
    const payload = readForm();
    if (!payload.id) { toast('id 不能为空', 'error'); return; }
    if (!payload.prompt_template.trim()) { toast('prompt_template 不能为空', 'error'); return; }

    // 字段 ID 校验(与后端 admin_rules._check_field_defs 同规则)
    const rawDefs = payload.field_defs;
    const ID_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const filled = rawDefs.filter(s => s);
    // 要么全填(自定义 ID),要么全空(用字母键),不允许一半一半
    if (filled.length && filled.length !== rawDefs.length) {
        toast('字段 ID 要么全部填写,要么全部留空(留空则用 field_a/field_b/...)', 'error');
        return;
    }
    for (const id of filled) {
        if (!ID_RE.test(id)) {
            toast(`字段 ID "${id}" 非法:须字母或下划线开头,只含字母数字下划线`, 'error');
            return;
        }
        if (id === 'rule_id') {
            toast('字段 ID 不能叫 rule_id,它是请求体顶层保留字', 'error');
            return;
        }
    }
    if (new Set(filled).size !== filled.length) {
        toast('字段 ID 不能重复', 'error');
        return;
    }
    // 全空则不写 field_defs,让后端走字母键分支
    payload.field_defs = filled.length ? filled : undefined;

    // 定义了但模板没引用 → 非阻断提醒
    const keys = rawDefs.map((_, i) => keyOf(rawDefs, i));
    const usedKeys = new Set();
    for (const s of payload.prompt_template.match(/\{[A-Za-z_][A-Za-z0-9_]*\}/g) || []) {
        const inner = s.slice(1, -1);
        usedKeys.add(inner);   // {keyan_name} 本身可能就是字段 ID
        // 也可能是 {键_name} 形态。两种都记,宁可漏报也不误报
        if (inner.endsWith('_name')) usedKeys.add(inner.slice(0, -5));
    }
    const unused = keys.filter(k => !usedKeys.has(k));
    if (unused.length && !confirm(`以下字段已定义但模板未引用:${unused.map(k => `{${k}}`).join('、')}。确认继续保存吗?`)) return;

    // 友好提示:模板用了 {键_name} 但没勾 require_field_name
    if (/\{[A-Za-z_][A-Za-z0-9_]*_name\}/.test(payload.prompt_template) && !payload.require_field_name) {
        if (!confirm('prompt_template 引用了 {键_name},但 require_field_name 是 false,LLM 将拿到空字符串。确认继续吗?')) return;
    }

    // 编辑模式下 id 改了 = rename,提醒下游缓存风险
    if (MODAL_MODE === 'edit' && payload.id !== MODAL_ORIG_ID) {
        if (!confirm(`规则 ID 将从 "${MODAL_ORIG_ID}" 改为 "${payload.id}"。下游若缓存了旧 ID,调用会立即返 404。确认继续吗?`)) return;
    }

    const btn = document.getElementById('modal-save');
    btn.disabled = true;
    try {
        if (MODAL_MODE === 'create') {
            await api('POST', '/admin/rules', payload);
            toast('规则已创建', 'success');
        } else {
            await api('PUT', `/admin/rules/${encodeURIComponent(MODAL_ORIG_ID)}`, payload);
            toast('规则已更新', 'success');
        }
        closeModal();
        await loadRules();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    } finally {
        btn.disabled = false;
    }
}

// ---------------------------------------------------------------------------
// 字段定义区(modal)
// ---------------------------------------------------------------------------

// 与后端 app/rules/field_keys.py 同源:由 field_defs + 模板推出有序字段键。
// defs[i] 非空则用它,否则用 field_a/field_b/...
function keyOf(defs, i) {
    const id = (defs[i] || '').trim();
    return id || `field_${String.fromCharCode(97 + i)}`;
}

// 从模板反推需要几个默认字母键(与后端 field_keys_for 的反推分支一致)
function inferKeyCount(template) {
    const m = (template || '').match(/\{field_([a-z])(?:_name)?\}/g);
    if (!m) return 0;
    const maxIdx = Math.max(...m.map(s => s.match(/\{field_([a-z])/)[1].charCodeAt(0) - 97));
    return maxIdx + 1;
}

function getFieldDefs() {
    return Array.from(document.querySelectorAll('#f-fielddefs .fd-name'))
        .map(el => el.value);
}

function renderFieldDefRows(defs) {
    const box = document.getElementById('f-fielddefs');
    const single = defs.length <= 1;  // 仅剩 1 行时禁用删除
    box.innerHTML = defs.map((id, i) => `
        <div class="flex items-center gap-2">
            <code class="fd-key font-mono text-xs text-surface-500 w-36 shrink-0">{${escape(keyOf(defs, i))}}</code>
            <input class="input fd-name" data-idx="${i}" value="${escape(id)}"
                   placeholder="字段 ID,留空则用 field_${String.fromCharCode(97 + i)}"
                   oninput="onFieldIdInput(${i})">
            <button type="button" class="btn-ghost text-red-600 shrink-0" title="删除该字段"
                    onclick="removeFieldDef(${i})" ${single ? 'disabled' : ''}>
                <i class="ri-close-line"></i>
            </button>
        </div>
    `).join('');
    renderChips();
}

// 只更新受影响的那一行 code 标签 + chips,避免整块 re-render 丢焦点
function onFieldIdInput(i) {
    const defs = getFieldDefs();
    const codeEls = document.querySelectorAll('#f-fielddefs .fd-key');
    if (codeEls[i]) codeEls[i].textContent = `{${keyOf(defs, i)}}`;
    renderChips();
}

function addFieldDef() {
    renderFieldDefRows([...getFieldDefs(), '']);
}

function removeFieldDef(i) {
    const defs = getFieldDefs();
    if (defs.length <= 1) return;

    const ta = document.getElementById('f-prompt');
    const key = keyOf(defs, i);
    const usesCustomId = !!(defs[i] || '').trim();

    // 被删字段仍被模板引用 → 提醒(占位符会悬空,需手动清理)
    const usedRe = new RegExp(`\\{${key}(_name)?\\}`);
    if (usedRe.test(ta.value)) {
        if (!confirm(`模板仍引用 {${key}}(或其 _name 变体),删除后该占位符将失效,建议取消并先清理模板。确认删除吗?`)) return;
    }

    // 只有默认字母键需要重编号:{field_d} → {field_c}。
    // 自定义 ID 是名字不是位置,删掉后其余 ID 不动。
    if (!usesCustomId) {
        ta.value = ta.value.replace(/\{field_([a-z])(_name)?\}/g, (m, L, suffix) => {
            const n = L.charCodeAt(0) - 97;
            return n > i ? `{field_${String.fromCharCode(96 + n)}${suffix || ''}}` : m;
        });
    }

    defs.splice(i, 1);
    renderFieldDefRows(defs);
}

function renderChips() {
    const defs = getFieldDefs();
    const requireName = document.getElementById('f-require').value === 'true';
    const box = document.getElementById('f-chips');
    box.innerHTML = defs.map((_, i) => {
        const key = keyOf(defs, i);
        const nameChip = requireName
            ? `<span class="chip" title="插入 {${key}_name}" onclick="insertPlaceholder('{${key}_name}')">${escape(key)}·名</span>`
            : '';
        return `<span class="chip" title="插入 {${key}}" onclick="insertPlaceholder('{${key}}')">${escape(key)}</span>${nameChip}`;
    }).join('') + `<span class="chip" onclick="insertPlaceholder('{{&quot;match&quot;: true|false, &quot;reason&quot;: &quot;<不超过30字>&quot;}}')">JSON 输出范式</span>`;
}

// 占位符 chip 插入到 prompt textarea
function insertPlaceholder(text) {
    const ta = document.getElementById('f-prompt');
    const start = ta.selectionStart, end = ta.selectionEnd;
    const v = ta.value;
    ta.value = v.slice(0, start) + text + v.slice(end);
    ta.focus();
    ta.selectionStart = ta.selectionEnd = start + text.length;
}

// ---------------------------------------------------------------------------
// 删除
// ---------------------------------------------------------------------------

let DEL_ID = null;

function openDel(id) {
    DEL_ID = id;
    document.getElementById('del-id').textContent = id;
    document.getElementById('del-modal').classList.remove('hidden');
}

function closeDel() {
    DEL_ID = null;
    document.getElementById('del-modal').classList.add('hidden');
}

async function confirmDel() {
    if (!DEL_ID) return;
    try {
        await api('DELETE', `/admin/rules/${encodeURIComponent(DEL_ID)}`);
        toast(`规则 ${DEL_ID} 已删除`, 'success');
        closeDel();
        await loadRules();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// Playground
// ---------------------------------------------------------------------------

function refreshPgRules() {
    const sel = document.getElementById('pg-rule');
    sel.innerHTML = '<option value="">— 选择规则 —</option>' + RULES.map(r =>
        `<option value="${escape(r.id)}">${escape(r.id)} · ${escape(r.name || '')}</option>`
    ).join('');
    onPgRuleChange();
}

function currentPgRule() {
    const id = document.getElementById('pg-rule').value;
    return RULES.find(r => r.id === id);
}

function onPgRuleChange() {
    const r = currentPgRule();
    const hint = document.getElementById('pg-rule-hint');
    const container = document.getElementById('pg-fields-container');
    const preview = document.getElementById('pg-prompt-preview');
    if (!r) {
        hint.textContent = '';
        container.innerHTML = '';
        preview.textContent = '(选规则后展示)';
        return;
    }
    hint.innerHTML = `<span class="badge badge-info">${r.require_field_name ? 'name 必填' : 'name 可省'}</span>
        <span class="text-surface-500 ml-1">${escape(r.description || '')}</span>`;
    preview.textContent = r.prompt_template || '(空)';

    // 字段输入框:键名即 field_defs 的 ID,没配则 field_a/field_b/...
    const defs = Array.isArray(r.field_defs) && r.field_defs.length ? r.field_defs : null;
    const keys = defs
        ? [...defs]
        : Array.from({length: inferKeyCount(r.prompt_template) || 2}, (_, i) => `field_${String.fromCharCode(97 + i)}`);
    container.innerHTML = keys.map((key) => `
        <div class="card p-3">
            <label class="field-label"><span class="name">${escape(key)}</span>${r.require_field_name ? '<span class="required">*</span>' : ''}</label>
            ${r.require_field_name ? `<input class="input pg-field-name mb-2" data-key="${escape(key)}" placeholder="字段名(中文业务名)">` : ''}
            <textarea class="input pg-field-value" data-key="${escape(key)}" rows="2" placeholder="${escape(key)} 的值"></textarea>
        </div>`).join('');
}

async function runPlayground() {
    const r = currentPgRule();
    if (!r) { toast('请先选规则', 'error'); return; }

    const valueEls = document.querySelectorAll('.pg-field-value');
    const payload = { rule_id: r.id };
    for (const el of valueEls) {
        const key = el.dataset.key;
        const value = el.value.trim();
        if (!value) { toast(`${key}.value 不能为空`, 'error'); return; }
        const entry = { value };
        if (r.require_field_name) {
            const nameEl = document.querySelector(`.pg-field-name[data-key="${key}"]`);
            entry.name = nameEl ? nameEl.value.trim() : '';
        }
        payload[key] = entry;
    }

    const placeholder = document.getElementById('pg-result-placeholder');
    const box = document.getElementById('pg-result-box');
    placeholder.classList.add('hidden');
    box.classList.remove('hidden');
    box.innerHTML = `<div class="text-surface-500 text-sm py-6 text-center">
        <i class="ri-loader-4-line animate-spin text-2xl"></i><br>正在调用 LLM...
    </div>`;

    try {
        const t0 = performance.now();
        const data = await api('POST', '/compare', payload);
        const elapsed = Math.round(performance.now() - t0);
        renderPgResult(data, elapsed);
    } catch (e) {
        box.innerHTML = `<div class="result-card result-nomatch">
            <div class="font-semibold text-terra-800"><i class="ri-error-warning-line"></i> 调用失败 (HTTP ${e.status || '??'})</div>
            <div class="text-sm text-surface-700 mt-2">${escape(e.message)}</div>
        </div>`;
    }
}

function renderPgResult(data, browserElapsed) {
    const box = document.getElementById('pg-result-box');
    const cls = data.result ? 'result-match' : 'result-nomatch';
    const icon = data.result ? 'ri-checkbox-circle-fill text-brand-600' : 'ri-close-circle-fill text-terra-600';
    const label = data.result ? '一致 (match=true)' : '不一致 (match=false)';
    box.innerHTML = `
        <div class="result-card ${cls}">
            <div class="flex items-center gap-2 mb-2">
                <i class="${icon} text-xl"></i>
                <span class="font-semibold text-surface-900">${label}</span>
            </div>
            <div class="text-sm text-surface-700 leading-relaxed"><b>理由:</b> ${escape(data.reason)}</div>
        </div>
        <div class="mt-3 grid grid-cols-3 gap-2 text-sm">
            <div class="card p-3"><div class="text-xs text-surface-500">耗时(后端)</div><div class="font-semibold text-surface-900">${data.time} ms</div></div>
            <div class="card p-3"><div class="text-xs text-surface-500">耗时(含网络)</div><div class="font-semibold text-surface-900">${browserElapsed} ms</div></div>
            <div class="card p-3"><div class="text-xs text-surface-500">total_tokens</div><div class="font-semibold text-surface-900">${data.cost ? data.cost.total_tokens : 0}</div></div>
        </div>
        <details class="mt-3">
            <summary class="text-xs text-surface-500 cursor-pointer">展开 token 明细 / 原始响应</summary>
            <pre class="code-block mt-2">${escape(JSON.stringify(data, null, 2))}</pre>
        </details>
    `;
}

// ---------------------------------------------------------------------------
// 刷新全部
// ---------------------------------------------------------------------------

async function reloadAll() {
    try {
        await api('POST', '/admin/reload-rules');
        await loadRules();
        toast('已刷新缓存与规则列表', 'success');
    } catch (e) {
        toast('刷新失败: ' + e.message, 'error');
    }
}

// 启动
loadRules();

// ---------------------------------------------------------------------------
// 请求历史
// ---------------------------------------------------------------------------

let HISTORY_PAGE = 0;
const HISTORY_PAGE_SIZE = 20;

function refreshHistoryFilter() {
    const sel = document.getElementById('history-filter-rule');
    sel.innerHTML = '<option value="">全部规则</option>' + RULES.map(r =>
        `<option value="${escape(r.id)}">${escape(r.id)} · ${escape(r.name || '')}</option>`
    ).join('');
}

async function loadHistory() {
    const ruleId = document.getElementById('history-filter-rule').value;
    const result = document.getElementById('history-filter-result').value;
    const params = new URLSearchParams({
        limit: HISTORY_PAGE_SIZE,
        offset: HISTORY_PAGE * HISTORY_PAGE_SIZE,
    });
    if (ruleId) params.set('rule_id', ruleId);
    if (result !== '') params.set('result', result);

    try {
        const data = await api('GET', `/admin/history?${params}`);
        renderHistory(data);
    } catch (e) {
        toast('加载历史失败: ' + e.message, 'error');
    }
}

function renderHistory(data) {
    const list = document.getElementById('history-list');
    const empty = document.getElementById('history-empty');
    const pagination = document.getElementById('history-pagination');

    if (!data.items || data.items.length === 0) {
        list.innerHTML = '';
        empty.classList.remove('hidden');
        pagination.classList.add('hidden');
        return;
    }

    empty.classList.add('hidden');
    pagination.classList.remove('hidden');

    document.getElementById('history-total').textContent = data.total;
    const totalPages = Math.ceil(data.total / HISTORY_PAGE_SIZE) || 1;
    document.getElementById('history-page-info').textContent = `${HISTORY_PAGE + 1} / ${totalPages}`;
    document.getElementById('history-prev').disabled = HISTORY_PAGE <= 0;
    document.getElementById('history-next').disabled = HISTORY_PAGE >= totalPages - 1;

    list.innerHTML = data.items.map(record => {
        const resultBadge = record.result
            ? '<span class="badge badge-success">一致</span>'
            : '<span class="badge badge-warning">不一致</span>';
        const cost = record.cost || {};
        const totalTokens = cost.total_tokens || 0;
        const promptTokens = cost.prompt_tokens || 0;
        const completionTokens = cost.completion_tokens || 0;
        const createdAt = record.created_at ? new Date(record.created_at).toLocaleString('zh-CN') : '-';
        const timeMs = record.time_ms || 0;

        return `
        <div class="card p-4 history-record" data-id="${escape(record.id)}">
            <div class="flex items-center justify-between cursor-pointer" onclick="toggleHistoryDetail('${escape(record.id)}')">
                <div class="flex items-center gap-3">
                    ${resultBadge}
                    <code class="font-mono text-sm text-brand-700">${escape(record.rule_id)}</code>
                    <span class="text-sm text-surface-600">${escape(record.rule_name || '')}</span>
                </div>
                <div class="flex items-center gap-4 text-sm text-surface-500">
                    <span title="请求时间"><i class="ri-time-line mr-1"></i>${createdAt}</span>
                    <span title="耗时"><i class="ri-timer-line mr-1"></i>${timeMs} ms</span>
                    <span title="Token 消耗"><i class="ri-token-swap-line mr-1"></i>${totalTokens}</span>
                    <i class="ri-arrow-down-s-line history-arrow transition-transform"></i>
                </div>
            </div>
            <div class="history-detail hidden mt-4 pt-4 border-t border-surface-200">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                    <div>
                        <div class="text-xs text-surface-500 mb-1">判断理由</div>
                        <div class="text-sm text-surface-700">${escape(record.reason || '-')}</div>
                    </div>
                    <div>
                        <div class="text-xs text-surface-500 mb-1">Token 明细</div>
                        <div class="grid grid-cols-3 gap-2 text-sm">
                            <div><span class="text-surface-500">输入:</span> ${promptTokens}</div>
                            <div><span class="text-surface-500">输出:</span> ${completionTokens}</div>
                            <div><span class="text-surface-500">总计:</span> ${totalTokens}</div>
                        </div>
                    </div>
                </div>
                <div class="mb-3">
                    <div class="text-xs text-surface-500 mb-1">比对字段</div>
                    <div class="space-y-1">
                        ${(record.fields || []).map((f, i) => `
                            <div class="text-sm">
                                <span class="text-surface-500">${escape(f.name || f.key || `字段${i}`)}:</span>
                                <span class="text-surface-700 ml-1">${escape(f.value || '-')}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
                <details>
                    <summary class="text-xs text-surface-500 cursor-pointer hover:text-surface-700">
                        <i class="ri-code-line mr-1"></i>查看原始请求 JSON
                    </summary>
                    <pre class="code-block mt-2">${escape(JSON.stringify(record.raw_request || {}, null, 2))}</pre>
                </details>
            </div>
        </div>`;
    }).join('');
}

function toggleHistoryDetail(id) {
    const record = document.querySelector(`.history-record[data-id="${id}"]`);
    if (!record) return;
    const detail = record.querySelector('.history-detail');
    const arrow = record.querySelector('.history-arrow');
    detail.classList.toggle('hidden');
    if (arrow) {
        arrow.style.transform = detail.classList.contains('hidden') ? '' : 'rotate(180deg)';
    }
}

function historyPrevPage() {
    if (HISTORY_PAGE > 0) {
        HISTORY_PAGE--;
        loadHistory();
    }
}

function historyNextPage() {
    HISTORY_PAGE++;
    loadHistory();
}

async function clearHistory() {
    if (!confirm('确定要清空所有请求历史记录吗？此操作不可撤销。')) return;
    try {
        await api('DELETE', '/admin/history');
        toast('历史记录已清空', 'success');
        HISTORY_PAGE = 0;
        await loadHistory();
    } catch (e) {
        toast('清空失败: ' + e.message, 'error');
    }
}
