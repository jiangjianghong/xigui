/* Tree editor for the JSON Schema subset accepted by the custom-rule API. */
const OutputSchemaEditor = (() => {
    const TYPES = ['string', 'number', 'integer', 'boolean', 'object', 'array', 'null'];
    let state = null;
    let rootEl = null;
    let jsonEl = null;
    let previewEl = null;

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, char => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        })[char]);
    }

    function newNode(key = '', type = 'string', required = false) {
        return {
            key, type, required, title: '', description: '', additionalProperties: undefined,
            enumEnabled: false, enumValues: [], enumInputs: [], children: [],
            items: type === 'array' ? newNode('', 'string') : null,
        };
    }

    function fromSchemaNode(schema, key = '', required = false) {
        const type = TYPES.includes(schema?.type) ? schema.type : 'string';
        const node = newNode(key, type, required);
        node.title = schema?.title || '';
        node.description = schema?.description || '';
        node.additionalProperties = schema?.additionalProperties;
        node.enumEnabled = Array.isArray(schema?.enum);
        node.enumValues = node.enumEnabled ? [...schema.enum] : [];
        node.enumInputs = node.enumValues.map(value => enumToText(value, type));

        if (type === 'object') {
            const properties = schema.properties && typeof schema.properties === 'object'
                ? schema.properties : {};
            const requiredKeys = new Set(Array.isArray(schema.required) ? schema.required : []);
            node.children = Object.entries(properties).map(([childKey, childSchema]) =>
                fromSchemaNode(childSchema, childKey, requiredKeys.has(childKey))
            );
        } else if (type === 'array') {
            node.items = fromSchemaNode(schema.items || { type: 'string' });
        }
        return node;
    }

    function fromSchema(schema) {
        const properties = schema?.properties && typeof schema.properties === 'object'
            ? schema.properties : {};
        const requiredKeys = new Set(Array.isArray(schema?.required) ? schema.required : []);
        return {
            title: schema?.title || '',
            description: schema?.description || '',
            additionalProperties: schema?.additionalProperties,
            children: Object.entries(properties).map(([key, child]) =>
                fromSchemaNode(child, key, requiredKeys.has(key))
            ),
        };
    }

    function parseEnumValue(raw, type) {
        if (type === 'string') return { ok: true, value: raw };
        if (type === 'number' || type === 'integer') {
            if (raw.trim() === '') return { ok: false };
            const value = Number(raw);
            return {
                ok: Number.isFinite(value) && (type !== 'integer' || Number.isInteger(value)),
                value,
            };
        }
        if (type === 'boolean') {
            return { ok: raw === 'true' || raw === 'false', value: raw === 'true' };
        }
        if (type === 'null') return { ok: raw.trim() === 'null', value: null };
        return { ok: false };
    }

    function enumToText(value, type) {
        if (type === 'null') return 'null';
        if (type === 'object' || type === 'array') return JSON.stringify(value);
        return String(value);
    }

    function toSchemaNode(node) {
        const schema = { type: node.type };
        if (node.title.trim()) schema.title = node.title.trim();
        if (node.description.trim()) schema.description = node.description.trim();

        if (node.type === 'object') {
            schema.properties = {};
            const required = [];
            node.children.forEach(child => {
                const key = child.key.trim();
                if (!key) return;
                schema.properties[key] = toSchemaNode(child);
                if (child.required) required.push(key);
            });
            if (required.length) schema.required = required;
            if (typeof node.additionalProperties === 'boolean') {
                schema.additionalProperties = node.additionalProperties;
            }
        } else if (node.type === 'array') {
            schema.items = toSchemaNode(node.items || newNode());
        }
        if (node.enumEnabled) {
            if (node.type === 'object' || node.type === 'array') {
                schema.enum = node.enumValues;
            } else {
                const parsed = node.enumInputs.map(value => parseEnumValue(value, node.type));
                schema.enum = parsed.every(item => item.ok)
                    ? parsed.map(item => item.value)
                    : node.enumValues;
            }
        }
        return schema;
    }

    function toSchema(source = state) {
        if (!source) return null;
        const schema = { type: 'object', properties: {} };
        if (source.title.trim()) schema.title = source.title.trim();
        if (source.description.trim()) schema.description = source.description.trim();
        if (typeof source.additionalProperties === 'boolean') {
            schema.additionalProperties = source.additionalProperties;
        }
        const required = [];
        source.children.forEach(node => {
            const key = node.key.trim();
            if (!key) return;
            schema.properties[key] = toSchemaNode(node);
            if (node.required) required.push(key);
        });
        if (required.length) schema.required = required;
        return schema;
    }

    function validateNodes(nodes, path = '输出字段') {
        const names = new Set();
        for (const node of nodes) {
            const key = node.key.trim();
            if (!key) return `${path}名称不能为空`;
            if (names.has(key)) return `${path}名称不能重复：${key}`;
            names.add(key);
            if (!TYPES.includes(node.type)) return `${path}.${key} 的类型无效`;
            if (node.type === 'object') {
                if (!node.children.length) return `${path}.${key} 是对象，至少要添加一个子字段`;
                const error = validateNodes(node.children, `${path}.${key}`);
                if (error) return error;
            } else if (node.type === 'array') {
                if (!node.items) return `${path}.${key} 缺少数组元素结构`;
                const error = validateItem(node.items, `${path}.${key}[]`);
                if (error) return error;
            } else if (node.enumEnabled) {
                if (!node.enumInputs.length) return `${path}.${key} 的枚举至少需要一个值`;
                if (node.enumInputs.some(value => !parseEnumValue(value, node.type).ok)) {
                    return `${path}.${key} 的枚举值与字段类型不匹配`;
                }
            }
        }
        return '';
    }

    function validateItem(node, path) {
        if (node.type === 'array') {
            if (!node.items) return `${path} 缺少数组元素结构`;
            return validateItem(node.items, `${path}[]`);
        }
        if (node.type === 'object') {
            if (!node.children.length) return `${path} 至少要添加一个子字段`;
            return validateNodes(node.children, path);
        }
        if (node.enumEnabled) {
            if (!node.enumInputs.length) return `${path} 的枚举至少需要一个值`;
            if (node.enumInputs.some(value => !parseEnumValue(value, node.type).ok)) {
                return `${path} 的枚举值与元素类型不匹配`;
            }
        }
        return '';
    }

    function sampleNode(node) {
        if (node.enumEnabled && node.enumInputs.length) {
            const first = parseEnumValue(node.enumInputs[0], node.type);
            if (first.ok) return first.value;
        }
        if (node.type === 'object') {
            const value = {};
            node.children.forEach(child => {
                if (child.key.trim()) value[child.key.trim()] = sampleNode(child);
            });
            return value;
        }
        if (node.type === 'array') return [sampleNode(node.items || newNode())];
        if (node.type === 'number' || node.type === 'integer') return 0;
        if (node.type === 'boolean') return false;
        if (node.type === 'null') return null;
        return '';
    }

    function sample(source = state) {
        const value = {};
        (source?.children || []).forEach(node => {
            if (node.key.trim()) value[node.key.trim()] = sampleNode(node);
        });
        return value;
    }

    function resolve(path) {
        let current = state;
        for (const token of path.split('.').filter(Boolean)) {
            if (token === 'children' || token === 'items') current = current[token];
            else if (current === state) current = current.children?.[Number(token)];
            else current = current?.[Number(token)];
            if (!current) return null;
        }
        return current;
    }

    function typeOptions(selected) {
        return TYPES.map(type =>
            `<option value="${type}" ${selected === type ? 'selected' : ''}>${type}</option>`
        ).join('');
    }

    function renderEnum(node, path) {
        if (['object', 'array'].includes(node.type)) return '';
        const values = node.enumEnabled ? node.enumInputs : [];
        return `
            <div class="schema-enum">
                <label class="schema-enum-toggle">
                    <input type="checkbox" data-schema-role="enum-enabled" data-path="${path}" ${node.enumEnabled ? 'checked' : ''}>
                    <span>限制允许值</span>
                </label>
                ${node.enumEnabled ? `
                    <div class="schema-enum-values">
                        ${values.map((value, index) => `
                            <div class="schema-enum-value">
                                <input class="input" data-schema-role="enum-value" data-path="${path}" data-index="${index}" value="${escapeHtml(value)}" aria-label="枚举值 ${index + 1}">
                                <button type="button" class="schema-icon-button" data-schema-action="remove-enum" data-path="${path}" data-index="${index}" title="删除枚举值" aria-label="删除枚举值">
                                    <i class="ri-close-line"></i>
                                </button>
                            </div>`).join('')}
                        <button type="button" class="schema-add-link" data-schema-action="add-enum" data-path="${path}">
                            <i class="ri-add-line" aria-hidden="true"></i> 添加枚举值
                        </button>
                    </div>` : ''}
            </div>`;
    }

    function renderNode(node, path, depth = 0, isItem = false) {
        const nested = node.type === 'object' || node.type === 'array';
        const nestedContent = node.type === 'object'
            ? `<div class="schema-tree-children">
                    ${node.children.map((child, index) => renderNode(child, `${path}.children.${index}`, depth + 1)).join('')}
                    <button type="button" class="schema-add-link" data-schema-action="add-child" data-path="${path}">
                        <i class="ri-add-line" aria-hidden="true"></i> 添加子字段
                    </button>
               </div>`
            : node.type === 'array'
                ? `<div class="schema-tree-children schema-array-item">
                    <div class="schema-subheading">数组元素</div>
                    ${renderNode(node.items || newNode(), `${path}.items`, depth + 1, true)}
                   </div>`
                : '';
        return `
            <div class="schema-node" data-depth="${Math.min(depth, 6)}">
                <div class="schema-node-row">
                    ${isItem
                        ? '<span class="schema-item-label">元素</span>'
                        : `<input class="input schema-key" data-schema-role="key" data-path="${path}" value="${escapeHtml(node.key)}" placeholder="字段名" aria-label="字段名称">`}
                    <select class="input schema-type" data-schema-role="type" data-path="${path}" aria-label="${isItem ? '元素类型' : '字段类型'}">${typeOptions(node.type)}</select>
                    ${isItem ? '' : `
                        <label class="schema-required">
                            <input type="checkbox" data-schema-role="required" data-path="${path}" ${node.required ? 'checked' : ''}>
                            <span>必填</span>
                        </label>
                        <button type="button" class="schema-icon-button schema-delete" data-schema-action="remove-node" data-path="${path}" title="删除字段" aria-label="删除字段">
                            <i class="ri-delete-bin-6-line"></i>
                        </button>`}
                </div>
                <div class="schema-node-meta">
                    <input class="input" data-schema-role="description" data-path="${path}" value="${escapeHtml(node.description)}" placeholder="字段说明（可选）" aria-label="字段说明">
                    <input class="input" data-schema-role="title" data-path="${path}" value="${escapeHtml(node.title)}" placeholder="展示标题（可选）" aria-label="展示标题">
                </div>
                ${node.type === 'object' ? `
                    <label class="schema-extra-toggle">
                        <input type="checkbox" data-schema-role="additional-properties" data-path="${path}" ${node.additionalProperties === false ? 'checked' : ''}>
                        <span>禁止未定义字段</span>
                    </label>` : ''}
                ${renderEnum(node, path)}
                ${nested ? nestedContent : ''}
            </div>`;
    }

    function render() {
        if (!rootEl || !state) return;
        rootEl.innerHTML = `
            <label class="schema-extra-toggle schema-root-extra">
                <input type="checkbox" data-schema-role="root-additional-properties" ${state.additionalProperties === false ? 'checked' : ''}>
                <span>禁止根对象包含未定义字段</span>
            </label>
            <div class="schema-tree-list">
                ${state.children.map((node, index) => renderNode(node, String(index))).join('')}
            </div>`;
        sync();
    }

    function sync() {
        if (jsonEl && state) jsonEl.value = JSON.stringify(toSchema());
        if (previewEl && state) {
            const error = validationError();
            previewEl.textContent = error
                ? `结构尚未完成：${error}`
                : JSON.stringify(sample(), null, 2);
        }
        if (typeof updatePromptPreview === 'function') updatePromptPreview();
    }

    function validationError(source = state) {
        if (!source) return '输出结构尚未初始化';
        if (!source.children.length) return '至少添加一个输出字段';
        return validateNodes(source.children);
    }

    function bindEvents() {
        if (rootEl.dataset.schemaBound) return;
        rootEl.dataset.schemaBound = 'true';
        rootEl.addEventListener('input', event => {
            const input = event.target.closest('[data-schema-role]');
            if (!input) return;
            const path = input.dataset.path || '';
            const node = path ? resolve(path) : null;
            switch (input.dataset.schemaRole) {
                case 'key': if (node) node.key = input.value; break;
                case 'description': if (node) node.description = input.value; break;
                case 'title': if (node) node.title = input.value; break;
                case 'enum-value':
                    if (node) node.enumInputs[Number(input.dataset.index)] = input.value;
                    break;
                default: return;
            }
            sync();
        });
        rootEl.addEventListener('change', event => {
            const input = event.target.closest('[data-schema-role]');
            if (!input) return;
            const node = input.dataset.path ? resolve(input.dataset.path) : null;
            switch (input.dataset.schemaRole) {
                case 'type':
                    if (node && node.type !== input.value) {
                        const updated = newNode(node.key, input.value, node.required);
                        updated.title = node.title;
                        updated.description = node.description;
                        Object.assign(node, updated);
                        render();
                    }
                    return;
                case 'required': if (node) node.required = input.checked; break;
                case 'additional-properties': if (node) node.additionalProperties = !input.checked; break;
                case 'root-additional-properties': state.additionalProperties = !input.checked; break;
                case 'enum-enabled':
                    if (node) {
                        node.enumEnabled = input.checked;
                        if (input.checked && !node.enumInputs.length) node.enumInputs.push('');
                    }
                    render();
                    return;
                default: return;
            }
            sync();
        });
        rootEl.addEventListener('click', event => {
            const button = event.target.closest('[data-schema-action]');
            if (!button) return;
            const { schemaAction: action, path = '', index } = button.dataset;
            if (action === 'add-root') {
                state.children.push(newNode());
            } else if (action === 'add-child') {
                const node = resolve(path);
                if (node?.type === 'object') node.children.push(newNode());
            } else if (action === 'remove-node') {
                const parts = path.split('.');
                const indexToRemove = Number(parts.pop());
                const list = parts.length ? resolve(parts.join('.')) : state.children;
                if (Array.isArray(list)) list.splice(indexToRemove, 1);
            } else if (action === 'add-enum') {
                const node = resolve(path);
                if (node) node.enumInputs.push('');
            } else if (action === 'remove-enum') {
                const node = resolve(path);
                if (node) node.enumInputs.splice(Number(index), 1);
            }
            render();
        });
    }

    function mount(editorId, jsonId, previewId) {
        rootEl = document.getElementById(editorId);
        jsonEl = document.getElementById(jsonId);
        previewEl = document.getElementById(previewId);
        if (!rootEl || !jsonEl) return;
        let schema = {};
        try { schema = JSON.parse(jsonEl.value || '{}'); } catch (_) { schema = {}; }
        state = fromSchema(schema);
        bindEvents();
        render();
    }

    function addRootField() {
        if (!state) return;
        state.children.push(newNode());
        render();
        const fields = rootEl.querySelectorAll('[data-schema-role="key"]');
        fields[fields.length - 1]?.focus();
    }

    function collect() {
        return validationError() ? null : toSchema();
    }

    function focusFirstInvalid() {
        const error = validationError();
        if (!error || !rootEl) return;
        const emptyKey = [...rootEl.querySelectorAll('[data-schema-role="key"]')]
            .find(input => !input.value.trim());
        (emptyKey || document.getElementById('f-output-schema-add'))?.focus();
    }

    return {
        mount,
        addRootField,
        collect,
        validationError,
        focusFirstInvalid,
        fromSchema,
        toSchema,
        sample,
        validate: validationError,
    };
})();

if (typeof window !== 'undefined') window.OutputSchemaEditor = OutputSchemaEditor;
if (typeof module !== 'undefined' && module.exports) module.exports = OutputSchemaEditor;
