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
// 初始化
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    loadProjects();
});
