/* Versioned access-control editor. Secret keys live only in the open dialog. */
let ACCESS = null;
let ACCESS_BUSY = false;

function accessDate(value) {
    return value ? new Date(value).toLocaleString() : '永久有效';
}

function renderAccess() {
    const keys = document.getElementById('access-keys');
    const ips = document.getElementById('access-ips');
    keys.innerHTML = ACCESS.api_keys.map(key => {
        const expired = key.expires_at && Date.parse(key.expires_at) <= Date.now();
        return `<div class="access-row"><div><strong>${escape(key.name)}</strong>
            <p>${escape(key.hint)} · ${!key.enabled ? '已禁用' : expired ? '已过期' : '已启用'} · ${escape(accessDate(key.expires_at))}</p>
            <p class="access-muted">创建于 ${escape(accessDate(key.created_at))}</p></div>
            <div class="access-actions"><button data-kind="api_keys" data-id="${key.id}" data-action="toggle">${key.enabled ? '禁用' : '启用'}</button>
            <button data-kind="api_keys" data-id="${key.id}" data-action="rotate">重新生成</button>
            <button class="access-danger" data-kind="api_keys" data-id="${key.id}" data-action="delete">删除</button></div></div>`;
    }).join('') || '<p class="access-muted">尚未创建 API Key。为每个调用程序单独创建，便于停用。</p>';
    ips.innerHTML = ACCESS.ip_allowlist.map(ip => `<div class="access-row"><div><strong>${escape(ip.name)}</strong>
        <p>${escape(ip.network)} · ${ip.enabled ? '已启用' : '已禁用'}</p></div><div class="access-actions">
        <button data-kind="ip_allowlist" data-id="${ip.id}" data-action="toggle">${ip.enabled ? '禁用' : '启用'}</button>
        <button class="access-danger" data-kind="ip_allowlist" data-id="${ip.id}" data-action="delete">删除</button></div></div>`
    ).join('') || '<p class="access-muted">尚未添加白名单。程序需要携带有效 API Key。</p>';
}

async function loadAccess() {
    const error = document.getElementById('access-error');
    error.textContent = '';
    try {
        ACCESS = await api('GET', '/admin/access-control');
        renderAccess();
    } catch (err) { error.textContent = '加载失败：' + err.message; }
}

async function mutateAccess(kind, operation, id = null, values = {}) {
    if (ACCESS_BUSY) return false;
    if (!ACCESS) { await loadAccess(); if (!ACCESS) return false; }
    ACCESS_BUSY = true;
    const buttons = document.querySelectorAll('#page-access button');
    buttons.forEach(button => { button.disabled = true; });
    const error = document.getElementById('access-error');
    error.textContent = '';
    try {
        const result = await api('POST', '/admin/access-control/' + kind, { version: ACCESS.version, operation, id, values });
        const key = result.key;
        delete result.key;
        ACCESS = result;
        renderAccess();
        if (key) showAccessKey(key);
        return true;
    } catch (err) {
        if (err.status === 409) {
            await loadAccess();
            error.textContent = '其他页面已修改配置，列表已刷新。请核对后再次提交，输入内容已保留。';
        } else error.textContent = err.message;
        return false;
    } finally {
        ACCESS_BUSY = false;
        document.querySelectorAll('#page-access button').forEach(button => { button.disabled = false; });
    }
}

function showAccessKey(key) {
    const dialog = document.getElementById('access-secret');
    dialog.querySelector('code').textContent = key;
    dialog.querySelector('[role=status]').textContent = '';
    dialog.showModal();
}

document.addEventListener('DOMContentLoaded', () => {
    const expiry = document.getElementById('key-validity');
    const date = document.getElementById('key-expires');
    expiry.addEventListener('change', () => {
        date.disabled = expiry.value === 'permanent';
        date.required = !date.disabled;
    });
    document.getElementById('key-create').addEventListener('submit', async event => {
        event.preventDefault();
        let expires_at = null;
        if (expiry.value === 'custom') {
            const parsed = new Date(date.value);
            if (!Number.isFinite(parsed.getTime()) || parsed <= new Date()) {
                document.getElementById('access-error').textContent = '请选择未来的过期时间';
                return;
            }
            expires_at = parsed.toISOString();
        }
        if (await mutateAccess('api_keys', 'create', null, { name: document.getElementById('key-name').value, expires_at })) {
            event.target.reset();
            date.disabled = true;
            date.required = false;
        }
    });
    document.getElementById('ip-create').addEventListener('submit', async event => {
        event.preventDefault();
        if (await mutateAccess('ip_allowlist', 'create', null, {
            name: document.getElementById('ip-name').value, network: document.getElementById('ip-network').value,
        })) event.target.reset();
    });
    document.getElementById('page-access').addEventListener('click', async event => {
        const button = event.target.closest('[data-action]');
        if (!button || ACCESS_BUSY || !ACCESS) return;
        const { kind, id, action } = button.dataset;
        const entry = ACCESS[kind].find(item => item.id === id);
        if (!entry) return;
        if (action === 'delete' && !confirm(`删除「${entry.name}」？此操作不可撤销。`)) return;
        if (action === 'rotate' && !confirm(`重新生成「${entry.name}」？旧 Key 将立即作废，有效期和启用状态保持不变。`)) return;
        await mutateAccess(kind, action === 'toggle' ? 'update' : action, id, action === 'toggle' ? { enabled: !entry.enabled } : {});
    });
    const secret = document.getElementById('access-secret');
    secret.addEventListener('close', () => { secret.querySelector('code').textContent = ''; });
    secret.querySelector('[data-close]').addEventListener('click', () => secret.close());
    secret.querySelector('[data-copy]').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(secret.querySelector('code').textContent);
            secret.querySelector('[role=status]').textContent = '已复制';
        } catch (_) { secret.querySelector('[role=status]').textContent = '无法自动复制，请选中上方密钥手动复制'; }
    });
    window.addEventListener('auth:expired', () => { if (secret.open) secret.close(); });
});
