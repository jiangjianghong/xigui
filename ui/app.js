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
    for (const t of ['rules', 'playground']) {
        document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
        document.getElementById(`page-${t}`).classList.toggle('hidden', t !== tab);
    }
    if (tab === 'playground') refreshPgRules();
}

// ---------------------------------------------------------------------------
// 规则列表
// ---------------------------------------------------------------------------

let RULES = [];

async function loadRules() {
    try {
        const data = await api('GET', '/admin/rules');
        RULES = data.rules || [];
        renderRules();
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

    // 字段定义:优先 field_defs;旧规则从模板反推个数,字段名留空
    let defs = Array.isArray(r.field_defs) && r.field_defs.length ? [...r.field_defs] : null;
    if (!defs) {
        const m = (r.prompt_template || '').match(/\{field_(\d+)(?:_name)?\}/g);
        const count = m ? Math.max(...m.map(s => parseInt(s.match(/\d+/)[0]))) + 1 : 2;
        defs = Array.from({length: count}, () => '');
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
        prompt_template: document.getElementById('f-prompt').value,
    };
}

async function saveRule() {
    const payload = readForm();
    if (!payload.id) { toast('id 不能为空', 'error'); return; }
    if (!payload.prompt_template.trim()) { toast('prompt_template 不能为空', 'error'); return; }

    // 友好提示:模板用了 {field_N_name} 但没勾 require_field_name
    if (/\{field_\d+_name\}/.test(payload.prompt_template) && !payload.require_field_name) {
        if (!confirm('prompt_template 引用了 {field_N_name},但 require_field_name 是 false,LLM 将拿到空字符串。确认继续吗?')) return;
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
    const defs = getFieldDefs();
    if (defs.length <= 1) return;

    const ta = document.getElementById('f-prompt');
    // 被删字段仍被模板引用 → 提醒(占位符会悬空,需手动清理)
    const usedRe = new RegExp(`\\{field_${i}(_name)?\\}`);
    if (usedRe.test(ta.value)) {
        if (!confirm(`模板仍引用 {field_${i}}(或其 _name 变体),删除后该占位符将失效或指向其他字段,建议取消并先清理模板。确认删除吗?`)) return;
    }
    // 更大下标自动减一:{field_3} → {field_2}(含 _name 变体)
    ta.value = ta.value.replace(/\{field_(\d+)(_name)?\}/g, (m, n, suffix) => {
        n = parseInt(n);
        return n > i ? `{field_${n - 1}${suffix || ''}}` : m;
    });

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

    // 从 prompt_template 推断字段数量
    const matches = r.prompt_template.match(/\{field_\d+\}/g);
    const count = matches ? Math.max(...matches.map(m => parseInt(m.match(/\d+/)[0]))) + 1 : 2;
    container.innerHTML = Array.from({length: count}, (_, i) => `
        <div class="card p-3">
            <label class="field-label"><span class="name">fields[${i}]</span>${r.require_field_name ? '<span class="required">*</span>' : ''}</label>
            ${r.require_field_name ? `<input class="input pg-field-name mb-2" data-idx="${i}" placeholder="字段名">` : ''}
            <textarea class="input pg-field-value" data-idx="${i}" rows="2" placeholder="字段 ${i} 的值"></textarea>
        </div>
    `).join('');
}

async function runPlayground() {
    const r = currentPgRule();
    if (!r) { toast('请先选规则', 'error'); return; }

    const valueEls = document.querySelectorAll('.pg-field-value');
    const fields = [];
    for (const el of valueEls) {
        const idx = parseInt(el.dataset.idx);
        const value = el.value.trim();
        if (!value) { toast(`fields[${idx}].value 不能为空`, 'error'); return; }
        const entry = { value };
        if (r.require_field_name) {
            const nameEl = document.querySelector(`.pg-field-name[data-idx="${idx}"]`);
            entry.name = nameEl ? nameEl.value.trim() : '';
        }
        fields.push(entry);
    }

    const payload = { rule_id: r.id, fields };

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
