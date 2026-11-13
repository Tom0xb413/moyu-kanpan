/* 收起模式：常驻滚动条（5 个/页，6 秒自动翻页，后台报价） */
window.Strip = (function () {
  const PER_PAGE = 5;
  const PAGE_MS = 6000;
  const POLL_MS = 4000;
  const MAX = 20;

  let listEl = null;
  let pageEl = null;
  let items = [];
  const quotes = new Map();
  let page = 0;
  let onPick = null;
  let pageTimer = null;
  let pollTimer = null;
  let running = false;
  let seq = 0;

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  function codeOf(sym) {
    return (sym || '').split(':')[1] || sym || '';
  }

  function baseOf(sym) {
    const code = codeOf(sym || '');
    if ((sym || '').indexOf('tx:') === 0) {
      return code.toLowerCase().startsWith('us') ? code.slice(2).split('.')[0].toUpperCase() : code.toUpperCase();
    }
    return code.split('_')[0];
  }

  function pageCount() {
    return Math.max(1, Math.ceil(items.length / PER_PAGE));
  }

  function init(opts) {
    onPick = (opts && opts.onPick) || null;
    listEl = document.getElementById('stripItems');
    pageEl = document.getElementById('stripPage');
    if (listEl) {
      listEl.addEventListener('click', (e) => {
        const si = e.target.closest('.si');
        if (!si || !si.dataset.sym) return;
        if (onPick) onPick(si.dataset.sym);
      });
    }
    const pager = document.getElementById('stripPage');
    if (pager) {
      pager.addEventListener('click', () => nextPage(true));
      pager.title = '点一下翻页';
    }
  }

  function setList(list) {
    items = (list || []).slice(0, MAX);
    if (page >= pageCount()) page = 0;
    render();
  }

  function setQuotes(arr) {
    let changed = false;
    (arr || []).forEach((q) => {
      if (!q || !q.symbol) return;
      quotes.set(q.symbol, q);
      changed = true;
    });
    if (changed) render();
  }

  function render() {
    if (!listEl) return;
    const total = pageCount();
    const start = page * PER_PAGE;
    let html = '';
    for (let i = 0; i < PER_PAGE; i++) {
      const it = items[start + i];
      if (!it) {
        html += '<div class="si off"><div class="l1"><span class="sn">—</span></div><span class="sp">—</span></div>';
        continue;
      }
      const q = quotes.get(it.symbol);
      const nm = it.name || (q && q.name) || baseOf(it.symbol);
      const last = q ? Fmt.price(q.last) : '—';
      const chg = q ? q.chgPct : null;
      const has = chg !== null && chg !== undefined && isFinite(chg);
      const up = (chg || 0) >= 0;
      html += '<div class="si" data-sym="' + esc(it.symbol) + '" title="' + esc(codeOf(it.symbol)) + '">' +
        '<div class="l1"><span class="sn">' + esc(String(nm).split('/')[0]) + '</span>' +
        '<span class="sc ' + (has ? (up ? 'up' : 'dn') : '') + '">' +
        (has ? (up ? '▲' : '▼') + Math.abs(chg).toFixed(2) + '%' : '') + '</span></div>' +
        '<span class="sp">' + last + '</span></div>';
    }
    listEl.innerHTML = html;
    if (pageEl) pageEl.textContent = (page + 1) + '/' + total;
  }

  function nextPage(manual) {
    if (pageCount() <= 1) return;
    page = (page + 1) % pageCount();
    if (manual) {
      // 手动翻页后重置自动翻页计时，避免刚点完就跳
      clearInterval(pageTimer);
      pageTimer = setInterval(() => {
        if (!document.hidden) nextPage();
      }, PAGE_MS);
    }
    render();
    if (listEl) {
      listEl.classList.add('flip');
      setTimeout(() => listEl.classList.remove('flip'), 320);
    }
  }

  async function refresh() {
    if (!running || !items.length) return;
    const my = ++seq;
    try {
      const r = await API.ticker(items.map((x) => x.symbol));
      if (my !== seq) return;
      setQuotes((r && r.items) || []);
    } catch (e) { /* 静默：下一轮再试 */ }
  }

  function start() {
    if (!listEl) init({});
    running = true;
    page = 0;
    render();
    refresh();
    clearInterval(pageTimer);
    clearInterval(pollTimer);
    pageTimer = setInterval(() => {
      if (!document.hidden) nextPage();
    }, PAGE_MS);
    pollTimer = setInterval(refresh, POLL_MS);
  }

  function stop() {
    running = false;
    clearInterval(pageTimer);
    clearInterval(pollTimer);
    pageTimer = null;
    pollTimer = null;
  }

  return { init, setList, setQuotes, refresh, nextPage, start, stop, isRunning: () => running };
})();
