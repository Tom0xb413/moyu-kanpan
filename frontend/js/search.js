/* 搜索浮层：近期/收藏 chips、星标、↑↓ 选择、Enter 确认 */
window.Search = (function () {
  let layer, input, listEl, countEl, tipEl;
  let chipBar, chipRecent, chipWatch, chipRecentSec, chipWatchSec;
  let items = [];
  let sel = 0;
  let timer = null;
  let onPick = null;
  let onToggleFav = null;
  let seq = 0;
  let state = { recent: [], watch: [] };
  let watchSet = new Set();
  let lastPick = { symbol: '', at: 0 };

  function init(pickCb, favCb) {
    onPick = pickCb;
    onToggleFav = favCb;
    layer = document.getElementById('searchLayer');
    input = document.getElementById('q');
    listEl = document.getElementById('results');
    countEl = document.getElementById('searchCount');
    tipEl = document.getElementById('searchTip');
    chipBar = document.getElementById('chipBar');
    chipRecent = document.getElementById('chipRecent');
    chipWatch = document.getElementById('chipWatch');
    chipRecentSec = document.getElementById('chipRecentSec');
    chipWatchSec = document.getElementById('chipWatchSec');

    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => query(input.value.trim()), 220);
    });
    // 每次启动换一个字段名，避免浏览器按 name 记忆历史输入并弹建议
    input.name = 'q_' + Math.random().toString(36).slice(2, 8);
    document.getElementById('qClear').addEventListener('click', () => {
      input.value = '';
      query('');
      input.focus();
    });
    layer.addEventListener('mousedown', (e) => {
      if (e.target === layer) close();
    });

    listEl.addEventListener('click', (e) => {
      const fav = e.target.closest('.fav');
      if (fav) {
        e.preventDefault();
        e.stopPropagation();
        const idx = Number(fav.dataset.i);
        const it = items[idx];
        if (it && onToggleFav) onToggleFav(it);
        sel = idx;
        try { input.focus({ preventScroll: true }); } catch (err) { /* ignore */ }
        render();
        return;
      }
      const row = e.target.closest('.r');
      if (!row) return;
      pick(Number(row.dataset.i));
    });

    // 双击直接打开（第一下 click 通常已经打开，这里兜住「第一下没命中行」的情况）
    listEl.addEventListener('dblclick', (e) => {
      if (e.target.closest('.fav') || !isOpen()) return;
      const row = e.target.closest('.r');
      if (!row) return;
      const idx = Number(row.dataset.i);
      if (items[idx]) pick(idx);
    });

    if (chipBar) {
      chipBar.addEventListener('click', (e) => {
        const chip = e.target.closest('.chip');
        if (!chip) return;
        const sym = chip.dataset.sym;
        if (!sym) return;
        pickSymbol({ symbol: sym });
      });
      // 双击 chip 同样直达（第一下 click 已处理时这里是空操作）
      chipBar.addEventListener('dblclick', (e) => {
        const chip = e.target.closest('.chip');
        if (!chip || !isOpen()) return;
        const sym = chip.dataset.sym;
        if (sym) pickSymbol({ symbol: sym });
      });
      [chipRecent, chipWatch].forEach((row) => {
        if (!row) return;
        row.addEventListener('wheel', (e) => {
          if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
            row.scrollLeft += e.deltaY;
            e.preventDefault();
          }
        }, { passive: false });
      });
    }

    // 提升到 document 并捕获，焦点丢了也能用；IME 组合输入时全部让位
    document.addEventListener('keydown', onKey, true);
  }

  function setState(next) {
    state = {
      recent: (next && next.recent) || [],
      watch: (next && next.watch) || [],
    };
    watchSet = new Set(state.watch.map((x) => x.symbol));
    renderChips();
    if (isOpen()) render();
  }

  function open(initial) {
    layer.classList.remove('hidden');
    input.value = initial || '';
    sel = 0;
    if (chipRecent) chipRecent.scrollLeft = 0;
    if (chipWatch) chipWatch.scrollLeft = 0;
    renderChips();
    query(input.value.trim());
    const grab = () => {
      try {
        input.focus({ preventScroll: true });
        input.select();
      } catch (e) { /* ignore */ }
    };
    requestAnimationFrame(grab);
    setTimeout(grab, 120);
  }

  function close() {
    layer.classList.add('hidden');
    input.blur();
  }

  function isOpen() {
    return layer && !layer.classList.contains('hidden');
  }

  async function query(q) {
    const my = ++seq;
    let data;
    try {
      data = await API.search(q);
    } catch (e) {
      items = [];
      render('搜索失败：' + e.message);
      return;
    }
    if (my !== seq) return;
    items = (data && data.items) || [];
    sel = 0;
    render();
  }

  function render(emptyText) {
    if (!items.length) {
      listEl.innerHTML = '<div class="grp">' + (emptyText || '没有匹配的标的，试试代码 BTC / NVDA') + '</div>';
      countEl.textContent = '';
      return;
    }
    // 保持后端的相关性顺序，只在市场切换处插入分组标题
    let html = '';
    let last = '';
    items.forEach((it, i) => {
      const label = it.marketLabel || '其他';
      if (label !== last) {
        html += '<div class="grp">' + label + '</div>';
        last = label;
      }
      const cls = 'r' + (i === sel ? ' sel' : '');
      const mk = it.market || 'crypto';
      const tag = it.tag ? '<span class="mk ' + mk + '">' + it.tag + '</span>' : '';
      const name = it.name && it.name !== it.code ? it.name : it.code;
      const fav = watchSet.has(it.symbol);
      html += '<div class="' + cls + '" data-i="' + i + '">' +
        '<span class="nm">' + escapeHtml(name) + '</span>' + tag +
        '<span class="cd">' + escapeHtml(it.code) + '</span>' +
        '<button class="fav' + (fav ? ' on' : '') + '" data-i="' + i + '" tabindex="-1" title="' +
        (fav ? '取消收藏' : '收藏') + '">' + (fav ? '★' : '☆') + '</button></div>';
    });
    listEl.innerHTML = html;
    countEl.textContent = items.length + ' 个结果';
    tipEl.textContent = '↑↓ 选择 · Enter/双击 打开 · Esc 关闭';
  }

  function renderChips() {
    if (!chipBar) return;
    const rowHtml = (arr, kind) => arr.map((it) => {
      const name = it.name || shortCode(it.symbol);
      return '<button class="chip" data-kind="' + kind + '" data-sym="' + escapeHtml(it.symbol) +
        '" title="' + escapeHtml(it.symbol) + '">' +
        '<span class="cn">' + escapeHtml(name) + '</span>' +
        (it.tag ? '<span class="tg">' + escapeHtml(it.tag) + '</span>' : '') +
        '</button>';
    }).join('');
    const r = rowHtml(state.recent, 'recent');
    const w = rowHtml(state.watch, 'watch');
    chipRecent.innerHTML = r;
    chipWatch.innerHTML = w;
    chipRecentSec.classList.toggle('hidden', !r);
    chipWatchSec.classList.toggle('hidden', !w);
    chipBar.classList.toggle('hidden', !r && !w);
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function shortCode(symbol) {
    return (symbol || '').split(':')[1] || symbol || '';
  }

  function move(d) {
    if (!items.length) return;
    sel = (sel + d + items.length) % items.length;
    render();
    const el = listEl.querySelector('.r.sel');
    if (el) el.scrollIntoView({ block: 'nearest' });
  }

  function pick(i) {
    const it = items[i];
    if (!it) return;
    pickSymbol(it);
  }

  // 600ms 内重复选中同一标的只发一次请求（双击 / 单击连点）
  function pickSymbol(it) {
    if (!it || !it.symbol) return;
    const now = Date.now();
    if (it.symbol === lastPick.symbol && now - lastPick.at < 600) {
      close();
      return;
    }
    lastPick = { symbol: it.symbol, at: now };
    close();
    if (onPick) onPick(it);
  }

  function onKey(e) {
    if (!isOpen()) return;
    if (e.isComposing) return;
    const k = e.key;
    if (k === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); move(1); }
    else if (k === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); move(-1); }
    else if (k === 'Enter') { e.preventDefault(); e.stopPropagation(); pick(sel); }
    else if (k === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (k === 'PageDown') { e.preventDefault(); e.stopPropagation(); move(6); }
    else if (k === 'PageUp') { e.preventDefault(); e.stopPropagation(); move(-6); }
  }

  return { init, open, close, isOpen, setState };
})();
