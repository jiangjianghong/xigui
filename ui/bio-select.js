// bio-select: 把页面上的 select.input 渐进增强为亲自然风格的自定义下拉。
// 原生 select 保留在 DOM 里(display:none)作为数据载体,app.js 对它的
// 读值(.value)、赋值(fillForm)、重建选项(refreshPgRules 改 innerHTML)、
// 以及 inline onchange 都无需任何改动:
//   - 选项变化由 MutationObserver 捕获后重建弹层
//   - 程序赋值通过劫持实例的 value setter 同步触发器文案
//   - 用户在弹层点选后回写原生 select 并 dispatch('change')
(function () {
    const NATIVE_VALUE = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');

    const ARROW_SVG = '<svg class="bio-select-arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" '
        + 'stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">'
        + '<path d="M6 9l6 6 6-6"/></svg>';

    function enhance(select) {
        if (select._bioSelect) return;
        select._bioSelect = true;

        // 包一层 wrapper,select 移入其中并隐藏
        const wrap = document.createElement('div');
        wrap.className = 'bio-select';
        select.parentNode.insertBefore(wrap, select);
        wrap.appendChild(select);

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'bio-select-trigger input';
        trigger.innerHTML = '<span class="bio-select-label"></span>' + ARROW_SVG;

        const panel = document.createElement('div');
        panel.className = 'bio-select-panel';

        wrap.appendChild(trigger);
        wrap.appendChild(panel);

        function syncLabel() {
            const opt = select.options[select.selectedIndex];
            trigger.querySelector('.bio-select-label').textContent = opt ? opt.textContent : '';
            Array.from(panel.children).forEach((el, i) =>
                el.classList.toggle('selected', i === select.selectedIndex));
        }

        function setValue(v, fireChange) {
            NATIVE_VALUE.set.call(select, v);
            syncLabel();
            if (fireChange) select.dispatchEvent(new Event('change'));  // 触发 inline onchange
        }

        function rebuild() {
            panel.innerHTML = '';
            Array.from(select.options).forEach(opt => {
                const el = document.createElement('div');
                el.className = 'bio-select-option';
                const check = document.createElement('i');
                check.className = 'ri-leaf-fill bio-select-check';
                const label = document.createElement('span');
                label.textContent = opt.textContent;
                el.appendChild(check);
                el.appendChild(label);
                el.addEventListener('click', () => { setValue(opt.value, true); close(); });
                panel.appendChild(el);
            });
            syncLabel();
        }

        // 劫持实例 value:fillForm 等程序赋值后触发器文案同步
        Object.defineProperty(select, 'value', {
            get() { return NATIVE_VALUE.get.call(select); },
            set(v) { NATIVE_VALUE.set.call(select, v); syncLabel(); },
        });

        function close() { wrap.classList.remove('open'); }

        trigger.addEventListener('click', () => wrap.classList.toggle('open'));
        document.addEventListener('click', e => { if (!wrap.contains(e.target)) close(); });
        trigger.addEventListener('keydown', e => {
            if (e.key === 'Escape') { close(); return; }
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault();
                const d = e.key === 'ArrowDown' ? 1 : -1;
                const idx = Math.min(Math.max(select.selectedIndex + d, 0), select.options.length - 1);
                if (idx !== select.selectedIndex) setValue(select.options[idx].value, true);
            }
        });

        new MutationObserver(rebuild).observe(select, { childList: true });
        rebuild();
    }

    document.querySelectorAll('select.input').forEach(enhance);
})();
