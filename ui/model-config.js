let MODEL_CONFIG = null;
let MODEL_CONFIG_BUSY = false;

function modelField(id) { return document.getElementById(`model-${id}`); }

function renderModelConfig() {
    const cfg = MODEL_CONFIG.config;
    modelField('backend').value = cfg.backend;
    modelField('api-base').value = cfg.api_base;
    modelField('name').value = cfg.model;
    modelField('api-key').value = '';
    modelField('timeout').value = cfg.timeout;
    modelField('max-tokens').value = cfg.max_tokens;
    modelField('temperature').value = cfg.temperature;
    modelField('top-p').value = cfg.top_p ?? '';
    modelField('max-retries').value = cfg.max_retries;
    modelField('thinking').checked = cfg.enable_thinking;
    modelField('json-mode').checked = cfg.use_json_mode;
    modelField('clear-key').checked = false;
    modelField('extra-params').value = JSON.stringify(cfg.extra_params || {}, null, 2);
    modelField('key-status').textContent = cfg.api_key_configured ? 'API Key 已配置；留空可保留现有密钥。' : '尚未配置 API Key。';
}

async function loadModelConfig() {
    const error = modelField('config-error');
    error.textContent = '';
    try {
        MODEL_CONFIG = await api('GET', '/admin/model-config');
        renderModelConfig();
    } catch (err) { error.textContent = '加载失败：' + err.message; }
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('model-config-form');
    if (!form) return;
    const clearKey = modelField('clear-key');
    const keyInput = modelField('api-key');
    clearKey.addEventListener('change', () => { keyInput.disabled = clearKey.checked; });
    form.addEventListener('submit', async event => {
        event.preventDefault();
        if (MODEL_CONFIG_BUSY || !MODEL_CONFIG) return;
        const error = modelField('config-error');
        error.textContent = '';
        let extraParams;
        try {
            extraParams = JSON.parse(modelField('extra-params').value || '{}');
            if (!extraParams || Array.isArray(extraParams) || typeof extraParams !== 'object') throw new Error();
        } catch (_) {
            error.textContent = '附加参数必须是有效的 JSON 对象。';
            return;
        }
        const config = {
            backend: modelField('backend').value.trim(),
            api_base: modelField('api-base').value.trim(),
            model: modelField('name').value.trim(),
            timeout: Number(modelField('timeout').value),
            max_tokens: Number(modelField('max-tokens').value),
            temperature: Number(modelField('temperature').value),
            top_p: modelField('top-p').value === '' ? null : Number(modelField('top-p').value),
            max_retries: Number(modelField('max-retries').value),
            enable_thinking: modelField('thinking').checked,
            use_json_mode: modelField('json-mode').checked,
            extra_params: extraParams,
        };
        const apiKey = keyInput.value;
        const apiKeyAction = clearKey.checked ? 'clear' : apiKey ? 'replace' : 'keep';
        const button = document.getElementById('model-config-save');
        MODEL_CONFIG_BUSY = true;
        button.disabled = true;
        try {
            MODEL_CONFIG = await api('PUT', '/admin/model-config', {
                version: MODEL_CONFIG.version, config,
                api_key_action: apiKeyAction,
                ...(apiKeyAction === 'replace' ? { api_key: apiKey } : {}),
            });
            renderModelConfig();
            toast('模型配置已保存并即时生效', 'success');
        } catch (err) {
            if (err.status === 409) {
                await loadModelConfig();
                error.textContent = '配置已被其他管理员修改，已刷新当前值。请核对后重新提交。';
            } else error.textContent = err.message;
        } finally {
            MODEL_CONFIG_BUSY = false;
            button.disabled = false;
        }
    });
});
