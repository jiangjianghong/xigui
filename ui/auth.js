/* All admin requests share CSRF headers and session-expiry handling. */
(() => {
    let expiryTimer;
    let expiryDialog;
    let lastFocus;
    let checking = false;
    let currentUser = 'admin';
    const isLoginPage = () => document.body.hasAttribute('data-login-page');

    function rememberSession(session) {
        clearTimeout(expiryTimer);
        if (!session.authenticated && !session.authorized) return;
        if (session.username) currentUser = session.username;
        const identityLabel = session.username || session.identity || currentUser;
        const label = document.getElementById('admin-account');
        if (label) label.textContent = identityLabel;
        const logout = document.getElementById('logout-admin');
        if (logout) logout.hidden = !session.authenticated;
        // API Key and IP authorization have no browser-session deadline.
        // Periodic checks still notice expired keys and removed allowlist entries.
        if (!session.authenticated || !session.expires_at) return;
        const delay = Math.max(0, Date.parse(session.expires_at) - Date.now());
        // Always ask the server at the deadline: browser clocks may differ.
        expiryTimer = setTimeout(checkSession, Math.max(1000, delay));
    }

    async function submitLogin(event) {
        event.preventDefault();
        const form = event.currentTarget;
        const button = form.querySelector('button[type=submit]');
        const error = form.querySelector('[role=alert]');
        button.disabled = true;
        error.textContent = '';
        try {
            const response = await fetch('/auth/login', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ username: form.elements.username.value, password: form.elements.password.value }),
            });
            const data = await response.json();
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '登录失败，请检查输入');
            form.elements.password.value = '';
            if (isLoginPage()) {
                window.location.replace('/ui/');
                return;
            }
            rememberSession(data);
            expiryDialog.close();
            lastFocus?.focus();
            window.dispatchEvent(new Event('auth:login'));
            if (typeof toast === 'function') toast('已重新登录，未提交的内容已保留，请再次提交', 'success');
        } catch (err) {
            error.textContent = err.message || '无法连接服务器，请重试';
        } finally {
            button.disabled = false;
        }
    }

    function requireLogin() {
        if (isLoginPage() || expiryDialog?.open) return;
        lastFocus = document.activeElement;
        // Clear any one-time key displayed in the background before locking the UI.
        window.dispatchEvent(new Event('auth:expired'));
        if (!expiryDialog) {
            expiryDialog = document.createElement('dialog');
            expiryDialog.className = 'auth-dialog auth-ui';
            expiryDialog.setAttribute('aria-labelledby', 'reauth-title');
            expiryDialog.innerHTML = `<h2 id="reauth-title">请重新登录</h2>
                <p class="auth-muted">登录已失效。未提交的表单仍保留在当前页面。</p>
                <form><label>管理员账号<input name="username" autocomplete="username" maxlength="100" required></label>
                <label>密码（6–12 个字符）<input name="password" type="password" autocomplete="current-password" minlength="6" maxlength="12" required></label>
                <p class="auth-error" role="alert"></p><button type="submit" class="auth-primary">重新登录</button></form>`;
            expiryDialog.addEventListener('cancel', event => event.preventDefault());
            expiryDialog.querySelector('form').addEventListener('submit', submitLogin);
            document.body.appendChild(expiryDialog);
        }
        expiryDialog.querySelector('[name=username]').value = currentUser;
        expiryDialog.querySelector('[name=password]').value = '';
        expiryDialog.querySelector('[role=alert]').textContent = '';
        expiryDialog.showModal();
        expiryDialog.querySelector('[name=password]').focus();
    }

    window.authFetch = async (url, options = {}) => {
        const headers = new Headers(options.headers || {});
        headers.set('X-Requested-With', 'XMLHttpRequest');
        const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
        if (response.status === 401) requireLogin();
        // Recheck authentication after a rejected request without replaying it.
        if (response.status === 403) await checkSession();
        return response; // Never replay a mutation automatically.
    };

    async function checkSession() {
        if (checking || expiryDialog?.open) return;
        checking = true;
        try {
            const response = await fetch('/auth/session', { credentials: 'same-origin', cache: 'no-store' });
            if (!response.ok) return;
            const session = await response.json();
            if (session.authorized) {
                if (isLoginPage()) window.location.replace('/ui/');
                else rememberSession(session);
            } else requireLogin();
        } catch (_) { /* A network error is not evidence that the session expired. */ }
        finally { checking = false; }
    }

    window.logoutAdmin = async () => {
        try {
            const response = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!response.ok && response.status !== 401) {
                const status = await fetch('/auth/session', { credentials: 'same-origin', cache: 'no-store' });
                if (!status.ok || (await status.json()).authenticated) throw new Error('退出失败，请重试');
            }
            window.location.replace('/ui/login');
        } catch (err) { if (typeof toast === 'function') toast(err.message, 'error'); }
    };

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('login-form')?.addEventListener('submit', submitLogin);
        checkSession();
        if (!isLoginPage()) {
            setInterval(checkSession, 60000);
            document.addEventListener('visibilitychange', () => { if (!document.hidden) checkSession(); });
        }
    });
})();
