// 项目管理 · 批量移入移出 · 导入导出
// 依赖 app.js 里的 api() / toast() / escape() / loadRules()，故必须在其后加载。
//
// 与后端的契约:
//   GET    /admin/projects            列表(带 rule_count)
//   POST   /admin/projects            新增
//   PUT    /admin/projects/{key}      修改(支持改 key)
//   DELETE /admin/projects/{key}      删除(其下规则变未分组)
//   POST   /admin/projects/{key}/rules  批量移入
//   POST   /admin/rules/unassign        批量移出
//   GET    /admin/export?project=      导出
//   POST   /admin/import               导入

let PROJECTS = [];
// null = 全部, '__none__' = 未分组, 其他 = 项目 key
let CURRENT_PROJECT = null;

const UNASSIGNED = '__none__';

// ---------------------------------------------------------------------------
// 项目列表
// ---------------------------------------------------------------------------

async function loadProjects() {
    try {
        const data = await api('GET', '/admin/projects');
        PROJECTS = data.projects || [];
        renderProjectList();
        if (typeof renderBulkProjectOptions === 'function') renderBulkProjectOptions();
    } catch (e) {
        toast('加载项目失败: ' + e.message, 'error');
    }
}

function renderProjectList() {
    const box = document.getElementById('project-list');
    if (!box) return;

    const total = RULES_TOTAL_COUNT;
    const assigned = PROJECTS.reduce((n, p) => n + p.rule_count, 0);
    const unassigned = Math.max(total - assigned, 0);

    const row = (key, label, count, cls, acts) => `
        <div class="project-item ${cls}" onclick="selectProject(${key === null ? 'null' : `'${escape(key)}'`})">
            <span class="truncate">${label}</span>
            <span class="flex items-center gap-1">
                <span class="count">${count}</span>
                ${acts || ''}
            </span>
        </div>`;

    let html = row(
        null,
        '<i class="ri-stack-line mr-1"></i>全部',
        total,
        CURRENT_PROJECT === null ? 'active' : '',
    );
    html += row(
        UNASSIGNED,
        '<i class="ri-scissors-cut-line mr-1"></i>未分组',
        unassigned,
        'unassigned ' + (CURRENT_PROJECT === UNASSIGNED ? 'active' : ''),
    );

    html += PROJECTS.map(p => row(
        p.key,
        `<i class="ri-leaf-line mr-1"></i>${escape(p.name)}`,
        p.rule_count,
        CURRENT_PROJECT === p.key ? 'active' : '',
        `<span class="acts">
            <button onclick="event.stopPropagation(); openProjectEdit('${escape(p.key)}')" title="编辑"><i class="ri-edit-line"></i></button>
            <button class="danger" onclick="event.stopPropagation(); openProjectDel('${escape(p.key)}')" title="删除"><i class="ri-delete-bin-line"></i></button>
        </span>`,
    )).join('');

    if (!PROJECTS.length) {
        html += `<p class="text-xs text-center py-4" style="color: var(--text-secondary);">
            还没有项目,点上方 + 新建一个
        </p>`;
    }

    box.innerHTML = html;
}

function selectProject(key) {
    CURRENT_PROJECT = key;
    if (typeof clearSelection === 'function') clearSelection();
    renderProjectList();
    const p = PROJECTS.find(x => x.key === key);
    document.getElementById('rules-title').textContent =
        key === null ? '规则列表'
        : key === UNASSIGNED ? '未分组规则'
        : `${p ? p.name : key} 的规则`;
    loadRules();
}

/** 当前筛选拼成 query string，供 loadRules 与 exportRules 共用。 */
function projectQuery() {
    return CURRENT_PROJECT === null
        ? ''
        : `?project=${encodeURIComponent(CURRENT_PROJECT)}`;
}

// ---------------------------------------------------------------------------
// 勾选与批量移入移出
// ---------------------------------------------------------------------------

let SELECTED = new Set();

function isSelected(id) {
    return SELECTED.has(id);
}

function toggleSelect(id, on) {
    if (on) SELECTED.add(id); else SELECTED.delete(id);
    syncBulkBar();
}

function toggleSelectAll(on) {
    SELECTED = on ? new Set(RULES.map(r => r.id)) : new Set();
    for (const cb of document.querySelectorAll('.rule-check')) cb.checked = on;
    syncBulkBar();
}

function clearSelection() {
    SELECTED = new Set();
    for (const cb of document.querySelectorAll('.rule-check')) cb.checked = false;
    const all = document.getElementById('rules-check-all');
    if (all) all.checked = false;
    syncBulkBar();
}

/** 只在有选中时显示批量条；当前已是「未分组」视图时「移出」无意义，置灰。 */
function syncBulkBar() {
    const bar = document.getElementById('bulk-bar');
    if (!bar) return;
    bar.classList.toggle('hidden', SELECTED.size === 0);
    document.getElementById('bulk-count').textContent = SELECTED.size;
    document.getElementById('bulk-unassign-btn').disabled =
        CURRENT_PROJECT === UNASSIGNED;
}

function renderBulkProjectOptions() {
    const sel = document.getElementById('bulk-project');
    if (!sel) return;
    sel.innerHTML = PROJECTS.length
        ? PROJECTS.map(p => `<option value="${escape(p.key)}">${escape(p.name)}</option>`).join('')
        : '<option value="">(还没有项目)</option>';
}

async function bulkAssign() {
    const key = document.getElementById('bulk-project').value;
    if (!key) { toast('请先新建一个项目', 'error'); return; }
    const ids = [...SELECTED];
    if (!ids.length) return;

    try {
        const res = await api('POST', `/admin/projects/${encodeURIComponent(key)}/rules`,
                              { rule_ids: ids });
        toast(`已移入 ${res.moved} 条规则`, 'success');
        clearSelection();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('移入失败: ' + e.message, 'error');
    }
}

async function bulkUnassign() {
    const ids = [...SELECTED];
    if (!ids.length) return;

    try {
        const res = await api('POST', '/admin/rules/unassign', { rule_ids: ids });
        toast(`已移出 ${res.moved} 条规则`, 'success');
        clearSelection();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('移出失败: ' + e.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// 项目 新增 / 编辑 / 删除
// ---------------------------------------------------------------------------

let P_MODE = 'create';       // 'create' | 'edit'
let P_ORIG_KEY = null;
let P_DEL_KEY = null;

function openProjectCreate() {
    P_MODE = 'create';
    P_ORIG_KEY = null;
    document.getElementById('project-modal-title').textContent = '新建项目';
    document.getElementById('p-key').value = '';
    document.getElementById('p-name').value = '';
    document.getElementById('p-desc').value = '';
    document.getElementById('project-modal').classList.remove('hidden');
}

function openProjectEdit(key) {
    const p = PROJECTS.find(x => x.key === key);
    if (!p) { toast('项目不存在: ' + key, 'error'); return; }
    P_MODE = 'edit';
    P_ORIG_KEY = key;
    document.getElementById('project-modal-title').textContent = `编辑项目 ${key}`;
    document.getElementById('p-key').value = p.key;
    document.getElementById('p-name').value = p.name;
    document.getElementById('p-desc').value = p.description || '';
    document.getElementById('project-modal').classList.remove('hidden');
}

function closeProjectModal() {
    document.getElementById('project-modal').classList.add('hidden');
}

async function saveProject() {
    const key = document.getElementById('p-key').value.trim();
    const name = document.getElementById('p-name').value.trim();
    const description = document.getElementById('p-desc').value.trim();

    if (!key) { toast('key 不能为空', 'error'); return; }
    if (!name) { toast('name 不能为空', 'error'); return; }
    // 与后端 IDENT_RE 同规则，先在前端拦一道，省一次往返
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
        toast('key 只能用字母/数字/下划线,且字母或下划线开头', 'error');
        return;
    }

    const body = { key, name, description };
    try {
        if (P_MODE === 'create') {
            await api('POST', '/admin/projects', body);
            toast('项目已创建', 'success');
        } else {
            await api('PUT', `/admin/projects/${encodeURIComponent(P_ORIG_KEY)}`, body);
            toast('项目已更新', 'success');
            // 改了 key 且当前正筛这个项目，筛选条件要跟着改
            if (CURRENT_PROJECT === P_ORIG_KEY) CURRENT_PROJECT = key;
        }
        closeProjectModal();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('保存失败: ' + e.message, 'error');
    }
}

function openProjectDel(key) {
    const p = PROJECTS.find(x => x.key === key);
    if (!p) return;
    P_DEL_KEY = key;
    document.getElementById('p-del-name').textContent = `${p.name} (${p.key})`;
    document.getElementById('p-del-count').textContent = p.rule_count;
    document.getElementById('project-del-modal').classList.remove('hidden');
}

function closeProjectDel() {
    document.getElementById('project-del-modal').classList.add('hidden');
    P_DEL_KEY = null;
}

async function confirmProjectDel() {
    if (!P_DEL_KEY) return;
    try {
        const res = await api('DELETE', `/admin/projects/${encodeURIComponent(P_DEL_KEY)}`);
        toast(`项目已删除,${res.unassigned_rules} 条规则变为未分组`, 'success');
        if (CURRENT_PROJECT === P_DEL_KEY) CURRENT_PROJECT = null;
        closeProjectDel();
        await loadProjects();
        await loadRules();
    } catch (e) {
        toast('删除失败: ' + e.message, 'error');
    }
}

// ---------------------------------------------------------------------------
// 导入 / 导出
// ---------------------------------------------------------------------------

/** 导出与左栏联动:当前筛的是什么就导什么,不额外追问。 */
async function exportRules() {
    try {
        const response = await authFetch('/admin/export' + projectQuery());
        if (!response.ok) throw new Error(response.status === 401 ? '请重新登录后导出' : '导出失败');
        const url = URL.createObjectURL(await response.blob());
        const link = document.createElement('a');
        link.href = url;
        link.download = 'rules.yaml';
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { toast(err.message, 'error'); }
}

function triggerImport() {
    const el = document.getElementById('import-file');
    el.value = '';          // 清掉上次的选择，否则选同一个文件不触发 change
    el.click();
}

async function doImport(input) {
    const file = input.files && input.files[0];
    if (!file) return;

    const fd = new FormData();
    fd.append('file', file);

    try {
        // 不能走 api()——它会设 Content-Type: application/json，
        // multipart 的 boundary 必须交给浏览器自己生成
        const resp = await authFetch('/admin/import', { method: 'POST', body: fd });
        const text = await resp.text();
        let data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }

        if (!resp.ok) {
            throw new Error((data && data.detail) || `HTTP ${resp.status}`);
        }

        await loadProjects();
        await loadRules();

        const warnings = data.warnings || [];
        if (warnings.length) {
            showImportResult(data);
        } else {
            toast(
                `导入完成:规则 新增 ${data.rules.created} / 更新 ${data.rules.updated}`,
                'success',
            );
        }
    } catch (e) {
        toast('导入失败: ' + e.message, 'error');
    }
}

/** 有警告时用弹窗而非 toast:警告可能多条，且需要逐条看完再决定要不要补救。 */
function showImportResult(data) {
    const body = document.getElementById('import-result-body');
    const w = data.warnings || [];
    body.innerHTML = `
        <div class="flex gap-6">
            <div><b>项目</b>　新增 ${data.projects.created} / 更新 ${data.projects.updated}</div>
            <div><b>规则</b>　新增 ${data.rules.created} / 更新 ${data.rules.updated}</div>
        </div>
        <div>
            <p class="font-bold mb-2" style="color: var(--warning-deep);">
                <i class="ri-alert-line mr-1"></i>${w.length} 条告警
            </p>
            <ul class="space-y-1" style="max-height: 260px; overflow-y: auto;">
                ${w.map(x => `<li class="text-xs" style="color: var(--text-secondary);">· ${escape(x)}</li>`).join('')}
            </ul>
        </div>`;
    document.getElementById('import-result-modal').classList.remove('hidden');
}

function closeImportResult() {
    document.getElementById('import-result-modal').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    loadProjects();
});
