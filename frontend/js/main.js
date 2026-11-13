/* 主逻辑：请求状态机、周期切换、轮询、近期/收藏、窗口控制 */
(function () {
  const PERIODS = ['15m', '1h', '4h', '8h', '1d', '1w'];
  const PERIOD_LABEL = { '15m': '15M', '1h': '1H', '4h': '4H', '8h': '8H', '1d': '1D', '1w': '1W' };
  const RECENT_MAX = 10;
  const WATCH_MAX = 30;
  const STRIP_MAX = 20;

  const S = {
    symbol: 'gf:BTC_USDT',
    period: '15m',
    name: '',
    tag: '',
    periods: PERIODS,
    data: null,
    quote: null,
    ema: [
      { p: 20, on: true },
      { p: 50, on: true },
      { p: 100, on: true },
      { p: 200, on: true },
    ],
    recent: [],
    watch: [],
    strip: [],
    mode: 'full',
    stripDir: 'h',
    exitAsk: true,
    exitAction: 'tray',
    exitShown: false,
    exitView: 'ask',
    chartReady: false,
    more: { loading: false, hasMore: true },
    lastOlderAt: 0,
    rangeArmedAt: 0,
    pin: true,
    alpha: 0.95,
    vol: true,
    visible: true,
    loading: false,
    reqSeq: 0,
    ctrl: null,
    quoteSeq: 0,
    timer: null,
    quoteTimer: null,
    progTimer: null,
    guardTimer: null,
    tickTimer: null,
    progStart: 0,
    lastStatus: '',
    viewKey: '',
  };

  const $ = (id) => document.getElementById(id);
  const el = {
    symName: $('symName'),
    symTag: $('symTag'),
    last: $('last'),
    chg: $('chg'),
    legend: $('legend'),
    statusL: $('stLeft'),
    statusR: $('stRight'),
    hint: $('hint'),
    chart: $('chart'),
    tf: $('tf'),
    btnFav: $('btnFav'),
    favPrev: $('favPrev'),
    favNext: $('favNext'),
    btnPin: $('btnPin'),
    btnEma: $('btnEma'),
    emaPanel: $('emaPanel'),
    emaRows: $('emaRows'),
    menu: $('menu'),
    prog: $('prog'),
    progText: $('progText'),
    progSub: $('progSub'),
    progElapsed: $('progElapsed'),
    older: $('older'),
    olderText: $('olderText'),
  };

  function normList(v, limit) {
    const out = [];
    const seen = new Set();
    (Array.isArray(v) ? v : []).forEach((it) => {
      let symbol = '';
      let name = '';
      let label = '';
      let tag = '';
      if (typeof it === 'string') {
        symbol = it.trim();
      } else if (it && typeof it === 'object') {
        symbol = String(it.symbol || '').trim();
        name = String(it.name || '');
        label = String(it.label || it.marketLabel || '');
        tag = String(it.tag || '');
      }
      if (!symbol || seen.has(symbol)) return;
      seen.add(symbol);
      out.push({ symbol, name, label, tag });
    });
    return out.slice(0, limit);
  }

  /* ---------------- 启动 ---------------- */
  async function boot() {
    let p = {};
    try { p = (await API.prefs()) || {}; } catch (e) { p = {}; }

    S.symbol = p.symbol || S.symbol;
    S.period = PERIODS.indexOf(p.period) >= 0 ? p.period : '15m';

    // 调试/直达：?s=gf:NVDA_USDT&p=1d&mode=strip
    const usp = new URLSearchParams(location.search);
    if (usp.get('s')) S.symbol = usp.get('s');
    if (usp.get('p') && PERIODS.indexOf(usp.get('p')) >= 0) S.period = usp.get('p');

    S.recent = normList(p.recent, RECENT_MAX);
    S.watch = normList(p.watch, WATCH_MAX);
    S.strip = normList(p.strip, STRIP_MAX);
    S.mode = p.mode === 'strip' ? 'strip' : 'full';
    if (usp.get('mode') === 'strip' || usp.get('mode') === 'full') S.mode = usp.get('mode');
    S.pin = p.on_top !== false;
    S.alpha = typeof p.alpha === 'number' ? p.alpha : 0.95;
    S.vol = p.vol !== false;
    S.stripDir = p.strip_dir === 'v' ? 'v' : 'h';
    S.exitAsk = p.exit_ask !== false;
    S.exitAction = p.exit_action === 'quit' ? 'quit' : 'tray';
    document.body.classList.toggle('vstrip', S.stripDir === 'v');

    if (p.ema) {
      S.ema.forEach((e) => {
        const v = p.ema[String(e.p)] !== undefined ? p.ema[String(e.p)] : p.ema[e.p];
        if (v !== undefined) e.on = !!v;
      });
    }

    applyAlpha(S.alpha);
    el.btnPin.classList.toggle('on', S.pin);

    // 窗口模式：nativeAlpha 时由系统整窗调透明度，页面保持纯白
    try {
      const st = await API.win.state();
      S.nativeAlpha = !!st.nativeAlpha;
      S.hotkeyOk = !!st.hotkeyOk;
      document.body.classList.toggle('opaque', S.nativeAlpha);
      if (S.nativeAlpha) {
        document.documentElement.style.setProperty('--alpha', '1');
      }
    } catch (e) { /* 忽略 */ }

    Search.init((it) => setSymbol(it.symbol, {}), onToggleFav);
    Search.setState({ recent: S.recent, watch: S.watch });
    bindUI();
    buildEmaPanel();
    renderTf();
    renderFav();
    syncVolSwitch();
    updateStripDirMenu();
    updateCloseMenu();
    Strip.init({ onPick: expandTo });

    if (S.mode === 'strip') {
      enterStripUI();
    } else {
      ensureChart();
      await setSymbol(S.symbol, { silent: true, noRecent: true });
    }
    if (usp.get('open') === 'search') {
      const openQ = usp.get('q') || '';
      setTimeout(() => Search.open(openQ), 700);
    }
    document.addEventListener('visibilitychange', () => {
      S.visible = !document.hidden;
      if (S.visible) {
        if (S.mode === 'full') {
          setTimeout(() => ChartView.resize(), 200);
          refreshQuote();
        } else {
          Strip.refresh();
        }
      }
    });
    setTimeout(() => ChartView.resize(), 260);
    setInterval(syncWin, 800);
  }

  // 托盘 / 菜单在 Python 侧切模式、以及点 X 触发的关闭问询，都靠轮询跟上
  // （避免从 HTTP 线程跨线程调用 evaluate_js）
  let syncing = false;
  async function syncWin() {
    if (syncing || document.hidden) return;
    syncing = true;
    try {
      const st = await API.win.state();
      if (!st) return;
      if (st.stripDir && st.stripDir !== S.stripDir) applyStripDir(st.stripDir, false);
      // 先跟上窗口模式：点 X 时若原本是价位条，后端会临时展开成完整窗口，
      // 页面去掉 mini 后再弹问询卡，否则 .layer 会被 mini 样式隐藏
      if (Date.now() >= (S.modeIgnoreUntil || 0) &&
          (st.mode === 'strip' || st.mode === 'full') && st.mode !== S.mode) {
        await setMode(st.mode, { external: true });
      }
      if (st.exitPending && !S.exitShown) openExit('ask');
    } catch (e) { /* 忽略 */ }
    syncing = false;
  }

  // 图表是惰性创建的：收起模式下不需要，展开时才建，省内存
  function ensureChart() {
    if (S.chartReady) return;
    ChartView.init(el.chart, onCrosshair, onRange);
    ChartView.setVolume(S.vol);
    S.chartReady = true;
  }

  /* ---------------- 数据加载 ---------------- */
  async function fetchKline(symbol, period, opts) {
    opts = opts || {};
    const seq = ++S.reqSeq;
    if (S.ctrl) {
      try { S.ctrl.abort(); } catch (e) { /* ignore */ }
    }
    const ctrl = new AbortController();
    S.ctrl = ctrl;
    S.loading = true;
    if (!opts.quiet) {
      el.chart.classList.add('busy');
      showProg(symbol, period);
    }

    try {
      const d = await API.kline(symbol, period, 800, !!opts.force, {
        signal: ctrl.signal,
        timeout: 20000,
      });
      if (seq !== S.reqSeq) return null;
      if (!d || d.symbol !== symbol || d.period !== period) return null;
      applyData(d);
      return d;
    } catch (e) {
      if (e.code === 'ABORTED' || seq !== S.reqSeq) return null;
      throw e;
    } finally {
      if (seq === S.reqSeq) {
        S.loading = false;
        S.ctrl = null;
        hideProg();
        el.chart.classList.remove('busy');
      }
    }
  }

  async function setSymbol(symbol, opts) {
    opts = opts || {};
    if (!symbol) return;
    S.symbol = symbol;
    clearTimeout(S.timer);
    setStatus('加载中…');
    try {
      const d = await fetchKline(symbol, S.period, { force: false });
      if (!d) return;
      if (!opts.silent && !opts.noRecent) pushRecent(d);
      API.save({ symbol, period: S.period });
      renderFav();
      schedule();
    } catch (e) {
      if (e.code === 'ABORTED') return;
      if (e.code === 'PERIOD_UNSUPPORTED') {
        const want = S.period;
        if (S.period !== '1d') {
          S.period = '1d';
          renderTf();
          API.save({ period: '1d' });
        }
        try {
          const d2 = await fetchKline(symbol, '1d', { force: false });
          if (d2) {
            if (!opts.silent && !opts.noRecent) pushRecent(d2);
            API.save({ symbol, period: '1d' });
            renderFav();
            if (!opts.silent) await offerSwitch(want);
          }
        } catch (e2) {
          if (e2.code !== 'ABORTED') failHard(e2);
        }
        schedule();
        return;
      }
      failHard(e);
      schedule();
    }
  }

  function mergeBars(oldBars, newBars) {
    const map = new Map();
    (oldBars || []).forEach((b) => { if (b && b.length >= 5) map.set(b[0], b); });
    (newBars || []).forEach((b) => { if (b && b.length >= 5) map.set(b[0], b); });
    return [...map.values()].sort((a, b) => a[0] - b[0]).slice(-6000);
  }

  function applyData(d) {
    if (!d || d.symbol !== S.symbol || d.period !== S.period) return false;

    // 刷新只带来最新 800 根；如果用户已经往回翻过，保留更早的部分，避免视图被截断
    const prev = S.data;
    const periodChanged = !!(prev && prev.period && prev.period !== d.period);
    if (prev && prev.symbol === d.symbol && prev.period === d.period &&
        prev.bars && prev.bars.length > ((d.bars && d.bars.length) || 0)) {
      d.bars = mergeBars(prev.bars, d.bars || []);
      d.count = d.bars.length;
    }

    S.data = d;
    S.name = d.name || '';
    S.tag = d.sourceLabel || '';
    S.periods = Array.isArray(d.periods) && d.periods.length ? d.periods : PERIODS;
    el.symName.textContent = d.name && d.name !== 'undefined' ? d.name : shortCode(d.symbol);
    el.symTag.textContent = S.tag;
    el.symTag.className = 'tag' + (d.provider === 'tx' ? ' tx' : '');

    if (S.mode === 'strip') {
      renderTf();
      renderFav();
      return true;
    }
    document.title = (d.name || '') + ' · 摸鱼看盘';

    const key = d.symbol + '|' + d.period;
    // 切周期一定要重置视口，别把上个周期的缩放/滚动位置带过来
    const resetView = periodChanged || key !== S.viewKey;
    S.viewKey = key;
    // setData 自身会触发区间回调，必须在渲染前武装保护，否则会被当成用户拖到最左
    S.rangeArmedAt = Date.now() + 1400;
    ChartView.setBars(d.bars || [], d.period, resetView);
    ChartView.applyEma(S.ema);
    S.more = { loading: false, hasMore: d.hasMore !== false };
    renderLegend(null);
    hideHint();

    const secs = Math.max(1, Math.round((Date.now() / 1000) - (d.fetchedAt || 0)));
    const staleMark = d.stale ? '<span class="dot warn"></span>' : '<span class="dot"></span>';
    const via = d.servedFrom === 'local' ? ' · 本地' : d.servedFrom === 'cache' ? ' · 缓存' : '';
    const deep = (d.bars && d.bars.length > 1000) ? ' · ' + d.bars.length + ' 根' : '';
    setStatus(staleMark + (d.sourceLabel || '') + via + deep + ' · ' + Fmt.ago(secs) + (d.stale ? ' · 离线' : ''));
    renderFav();
    refreshQuote();
    return true;
  }

  /* ---------------- 向左滚动：加载更早 ---------------- */
  function onRange(r) {
    if (!r || S.mode !== 'full') return;
    if (!S.data || !S.data.bars || S.data.bars.length < 120) return;
    if (S.more.loading || !S.more.hasMore) return;
    if (Date.now() < (S.rangeArmedAt || 0)) return;   // 数据刚更新时忽略自动触发的区间变化
    // 负区间是向右拖出来的空白，不是历史边界；视口整个在数据外也不加载
    if (r.from > 1.5 || r.from < -1) return;
    if (r.to < 2) return;
    const now = Date.now();
    if (now - S.lastOlderAt < 700) return;
    S.lastOlderAt = now;
    loadEarlier();
  }

  function showOlder(on, text) {
    if (!el.older) return;
    if (text && el.olderText) el.olderText.textContent = text;
    el.older.classList.toggle('hidden', !on);
  }

  async function loadEarlier() {
    const d0 = S.data;
    if (!d0 || !d0.bars || !d0.bars.length) return;
    if (S.more.loading || !S.more.hasMore) return;
    const sym = S.symbol;
    const per = S.period;
    const oldest = d0.bars[0][0];
    S.more.loading = true;
    showOlder(true, '加载更早…');
    try {
      const r = await API.kline(sym, per, 400, false, { before: oldest, timeout: 20000 });
      if (sym !== S.symbol || per !== S.period) return;
      const page = (r && r.bars) || [];
      const prevCount = ChartView.count();
      const prevRange = ChartView.logicalRange();
      if (page.length) {
        d0.bars = mergeBars(page, d0.bars);
        d0.count = d0.bars.length;
      }
      d0.hasMore = !!(r && r.hasMore);
      S.more.hasMore = !!(r && r.hasMore);
      if (page.length) {
        ChartView.setBars(d0.bars, per, false);
        ChartView.applyEma(S.ema);
        const added = ChartView.count() - prevCount;
        if (prevRange && added > 0 && prevRange.from >= 0) {
          ChartView.setLogicalRange({ from: prevRange.from + added, to: prevRange.to + added });
        } else if (prevRange) {
          // 之前停在空白/异常区间：加载完直接回最新，别把视口留在历史里
          ChartView.resetView();
        }
        renderLegend(null);
      }
      if (!S.more.hasMore) showHint('已经到最早的一根 K 线', null, null, 2200);
      const depth = ChartView.count();
      setStatus('<span class="dot"></span>已加载 ' + depth + ' 根 · 更早 ' + (S.more.hasMore ? '可继续' : '到头了'));
    } catch (e) {
      if (e.code !== 'ABORTED') {
        showHint('加载更早的 K 线失败：' + (e.message || ''), '重试', () => loadEarlier(), 6000);
      }
    } finally {
      S.more.loading = false;
      S.rangeArmedAt = Date.now() + 900;
      showOlder(false);
    }
  }

  function failHard(e) {
    const msg = e && e.code === 'TIMEOUT' ? '行情响应超时（20s），可点重试' : ((e && e.message) || '加载失败');
    setStatus('<span class="dot warn"></span>' + escapeHtml(msg));
    showHint(msg, '重试', () => load(true));
  }

  function schedule() {
    clearTimeout(S.timer);
    if (S.mode === 'strip') return;
    const ttl = (S.data && S.data.ttl) || 60;
    S.timer = setTimeout(() => {
      if (!S.visible || S.loading) { schedule(); return; }
      load(false, true);
    }, Math.max(15, ttl) * 1000);
  }

  async function load(force, quiet) {
    clearTimeout(S.timer);
    if (S.mode === 'strip') return;
    try {
      await fetchKline(S.symbol, S.period, { force: !!force, quiet: !!quiet });
    } catch (e) {
      if (e.code !== 'ABORTED') failHard(e);
    } finally {
      schedule();
    }
  }

  async function refreshQuote() {
    clearTimeout(S.quoteTimer);
    const my = ++S.quoteSeq;
    const sym = S.symbol;
    try {
      const r = await API.quote([sym]);
      if (my !== S.quoteSeq || sym !== S.symbol) return;
      const q = r && r.items && r.items[0];
      if (q && (!q.symbol || q.symbol === sym)) {
        S.quote = q;
        renderHeader(q);
      }
    } catch (e) { /* 静默 */ }
    if (my === S.quoteSeq) {
      S.quoteTimer = setTimeout(() => {
        if (S.visible) refreshQuote();
      }, 6000);
    }
  }

  function renderHeader(q) {
    if (!q) return;
    if (S.tag === '真实盘' && q.name) {
      el.symName.textContent = q.name;
      document.title = q.name + ' · 摸鱼看盘';
    }
    el.last.textContent = Fmt.price(q.last);
    const up = q.chgPct >= 0;
    el.chg.textContent = (up ? '▲' : '▼') + Fmt.pct(Math.abs(q.chgPct)).replace('+', '');
    el.chg.className = 'chg ' + (up ? 'up' : 'dn');
    const bits = [];
    if (q.source === 'gatef') bits.push('资金费率 ' + (q.funding === null || q.funding === undefined ? '—' : (q.funding * 100).toFixed(4) + '%'));
    if (q.vol) bits.push('24h ' + Fmt.vol(q.vol));
    el.statusR.textContent = bits.join(' · ');
  }

  /* ---------------- 图例 ---------------- */
  function renderLegend(info) {
    let bar = info && info.bar;
    let emas = info && info.emas;
    if (!bar) {
      bar = ChartView.lastBar();
      if (!bar) { el.legend.innerHTML = ''; return; }
      emas = ChartView.emaAt(ChartView.count() - 1);
    }
    const up = bar[4] >= bar[1];
    const pct = bar[1] ? ((bar[4] - bar[1]) / bar[1]) * 100 : 0;
    const volBit = S.vol
      ? '<span class="k">V</span><b>' + (bar[5] ? Fmt.vol(bar[5]) : '—') + '</b>'
      : '';
    let row1 =
      '<span class="k">O</span><b>' + Fmt.price(bar[1]) + '</b>' +
      '<span class="k">H</span><b>' + Fmt.price(bar[2]) + '</b>' +
      '<span class="k">L</span><b>' + Fmt.price(bar[3]) + '</b>' +
      '<span class="k">C</span><b>' + Fmt.price(bar[4]) + '</b>' +
      volBit +
      '<span class="chg ' + (up ? 'up' : 'dn') + '">' + (up ? '▲' : '▼') + Math.abs(pct).toFixed(2) + '%</span>';
    let row2 = '';
    (emas || []).forEach((e) => {
      if (!e || !e.p) return;
      row2 += '<span class="ema" style="--c:' + e.color + '"><i></i><span class="ek">EMA</span><span class="ep">' + e.p + '</span><b>' +
        (e.value ? Fmt.compact(e.value) : '—') + '</b></span>';
    });
    el.legend.innerHTML = '<div class="lg-row">' + row1 + '</div>' +
      (row2 ? '<div class="lg-row">' + row2 + '</div>' : '');
  }

  function onCrosshair(info) {
    renderLegend(info);
  }

  /* ---------------- 周期 ---------------- */
  function renderTf() {
    [...el.tf.querySelectorAll('button')].forEach((b) => {
      const p = b.dataset.p;
      b.classList.toggle('active', p === S.period);
      const ok = S.periods.indexOf(p) >= 0;
      b.classList.toggle('dim', !ok);
      b.title = ok ? '' : '该标的无此周期数据';
    });
  }

  function switchPeriod(p) {
    if (p === S.period) return;
    const ok = S.periods.indexOf(p) >= 0;
    if (!ok) {
      offerSwitch(p);
      return;
    }
    S.period = p;
    renderTf();
    API.save({ period: p });
    load(false);
  }

  function contractTwin(symbol) {
    if (!symbol || symbol.indexOf('tx:') !== 0) return '';
    const code = symbol.slice(3);
    if (!code.toLowerCase().startsWith('us')) return '';
    const base = code.slice(2).split('.')[0].toUpperCase();
    if (!base) return '';
    return 'gf:' + base + '_USDT';
  }

  async function offerSwitch(period) {
    const twin = contractTwin(S.symbol);
    if (!twin) {
      showHint('该标的只有 1D / 1W 数据');
      return;
    }
    try {
      const r = await API.search(twin.slice(3).replace('_USDT', ''));
      const hit = (r.items || []).find((i) => i.symbol === twin);
      if (hit) {
        showHint('真实盘只有 1D / 1W，合约版支持 ' + (period ? PERIOD_LABEL[period] || '' : '全周期'), '切到合约版', () => {
          setSymbol(twin);
        });
      } else {
        showHint('该标的只有 1D / 1W 数据');
      }
    } catch (e) {
      showHint('该标的只有 1D / 1W 数据');
    }
  }

  /* ---------------- 进度卡 ---------------- */
  function showProg(symbol, period) {
    S.progStart = Date.now();
    if (el.progSub) el.progSub.textContent = shortCode(symbol) + ' · ' + (PERIOD_LABEL[period] || period);
    if (el.progText) el.progText.textContent = '连接行情…';
    if (el.progElapsed) el.progElapsed.textContent = '0.0s';
    clearTimeout(S.progTimer);
    clearInterval(S.tickTimer);
    // 刚开始加载的 560ms 内卡片不接收点击：双击标的时的第二下会穿透到图表，
    // 不会误触「取消」把刚发起的请求打断（也覆盖上一次卡片还没消失的情况）
    if (el.prog) el.prog.classList.add('guard');
    clearTimeout(S.guardTimer);
    S.guardTimer = setTimeout(() => {
      if (el.prog) el.prog.classList.remove('guard');
    }, 560);
    S.progTimer = setTimeout(() => {
      if (!el.prog) return;
      el.prog.classList.remove('hidden');
      S.tickTimer = setInterval(() => {
        const s = (Date.now() - S.progStart) / 1000;
        if (el.progElapsed) el.progElapsed.textContent = s.toFixed(1) + 's';
        if (el.progText) {
          el.progText.textContent =
            s < 0.9 ? '连接行情…' :
            s < 3.0 ? '拉取 K 线…' :
            s < 7.0 ? '尝试备用数据源…' : '网络不稳定，仍在等待…';
        }
      }, 100);
    }, 80);
  }

  function hideProg() {
    clearTimeout(S.progTimer);
    clearTimeout(S.guardTimer);
    clearInterval(S.tickTimer);
    if (el.prog) {
      el.prog.classList.add('hidden');
      el.prog.classList.remove('guard');
    }
  }

  function cancelLoad() {
    S.reqSeq++;
    if (S.ctrl) {
      try { S.ctrl.abort(); } catch (e) { /* ignore */ }
    }
    S.ctrl = null;
    S.loading = false;
    hideProg();
    if (el.chart) el.chart.classList.remove('busy');

    // 取消后必须回到「真正显示在图上」的那份数据，否则 S.symbol 已经是新标的、
    // 图表却还是旧的，下一次报价轮询会把顶栏价格串到别的品种上
    const d = S.data;
    if (d && d.symbol) {
      S.symbol = d.symbol;
      S.period = d.period;
      S.name = d.name || '';
      S.tag = d.sourceLabel || '';
      S.periods = Array.isArray(d.periods) && d.periods.length ? d.periods : PERIODS;
      el.symName.textContent = d.name && d.name !== 'undefined' ? d.name : shortCode(d.symbol);
      el.symTag.textContent = S.tag;
      el.symTag.className = 'tag' + (d.provider === 'tx' ? ' tx' : '');
      renderTf();
      renderFav();
      renderLegend(null);
      refreshQuote();
      API.save({ symbol: S.symbol, period: S.period });
    }
    setStatus('<span class="dot"></span>已取消');
    schedule();
  }

  /* ---------------- 提示条 ---------------- */
  let hintTimer = null;
  function showHint(msg, actionLabel, action, ms) {
    el.hint.classList.remove('hidden');
    el.hint.innerHTML = '<span>' + escapeHtml(msg) + '</span>';
    if (actionLabel) {
      const b = document.createElement('button');
      b.textContent = actionLabel;
      b.addEventListener('click', () => { hideHint(); action && action(); });
      el.hint.appendChild(b);
    }
    clearTimeout(hintTimer);
    hintTimer = setTimeout(hideHint, ms || 9000);
  }
  function hideHint() { el.hint.classList.add('hidden'); }

  /* ---------------- 近期 / 收藏 ---------------- */
  function pushRecent(d) {
    if (!d || !d.symbol) return;
    const it = { symbol: d.symbol, name: d.name || '', label: d.marketLabel || '', tag: d.sourceLabel || '' };
    S.recent = [it].concat(S.recent.filter((x) => x.symbol !== it.symbol)).slice(0, RECENT_MAX);
    API.save({ recent: S.recent });
    Search.setState({ recent: S.recent, watch: S.watch });
  }

  function stepRecent(dir) {
    if (!S.recent.length) return;
    const i = S.recent.findIndex((x) => x.symbol === S.symbol);
    const n = S.recent.length;
    const next = S.recent[((i < 0 ? 0 : i) + dir + n) % n];
    if (next && next.symbol !== S.symbol) setSymbol(next.symbol, { noRecent: true });
  }

  function isFav(symbol) {
    return S.watch.some((x) => x.symbol === symbol);
  }

  function currentItem() {
    const d = S.data || {};
    return {
      symbol: S.symbol,
      name: S.name || d.name || '',
      marketLabel: d.marketLabel || '',
      tag: S.tag || '',
    };
  }

  function onToggleFav(it) {
    toggleFav(it);
  }

  function toggleFav(it) {
    if (!it || !it.symbol) return;
    const i = S.watch.findIndex((x) => x.symbol === it.symbol);
    let added = false;
    if (i >= 0) {
      S.watch.splice(i, 1);
    } else {
      S.watch.unshift({
        symbol: it.symbol,
        name: it.name || (it.symbol === S.symbol ? S.name : '') || '',
        label: it.marketLabel || it.label || '',
        tag: it.tag || '',
      });
      S.watch = S.watch.slice(0, WATCH_MAX);
      added = true;
    }
    API.save({ watch: S.watch });
    Search.setState({ recent: S.recent, watch: S.watch });
    renderFav();
    if (added) {
      API.warm(it.symbol);
      showHint('已收藏 ' + (it.name || shortCode(it.symbol)) + '，正在后台缓存全周期', null, null, 2600);
    } else {
      showHint('已取消收藏 ' + (it.name || shortCode(it.symbol)), null, null, 2600);
    }
  }

  function stepWatch(dir) {
    if (!S.watch.length) {
      showHint('还没有收藏：搜索结果里点 ☆ 即可', null, null, 3000);
      return;
    }
    const i = S.watch.findIndex((x) => x.symbol === S.symbol);
    const n = S.watch.length;
    const next = S.watch[i < 0 ? (dir > 0 ? 0 : n - 1) : ((i + dir + n) % n)];
    if (next && next.symbol !== S.symbol) setSymbol(next.symbol, { noRecent: true });
  }

  function renderFav() {
    const on = isFav(S.symbol);
    if (el.btnFav) {
      el.btnFav.classList.toggle('on', on);
      el.btnFav.title = on ? '取消收藏 (F)' : '收藏当前标的 (F)';
      el.btnFav.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    const empty = !S.watch.length;
    if (el.favPrev) el.favPrev.disabled = empty;
    if (el.favNext) el.favNext.disabled = empty;
  }

  /* ---------------- 收起模式（滚动条） ---------------- */
  function stripList() {
    return S.strip.length ? S.strip : S.watch.slice(0, STRIP_MAX);
  }

  function enterStripUI() {
    document.body.classList.add('mini');
    showOlder(false);
    hideProg();
    if (el.chart) el.chart.classList.remove('busy');
    clearTimeout(S.timer);
    clearTimeout(S.quoteTimer);
    S.loading = false;
    Strip.setList(stripList());
    Strip.start();
  }

  async function setMode(mode, opts) {
    opts = opts || {};
    mode = mode === 'strip' ? 'strip' : 'full';
    if (mode === S.mode) {
      if (mode === 'strip') Strip.start();
      return;
    }
    S.mode = mode;
    S.modeIgnoreUntil = Date.now() + 2500;
    document.body.classList.toggle('mini', mode === 'strip');
    if (!opts.external) API.save({ mode });
    if (mode === 'strip') {
      enterStripUI();
      if (!opts.external) {
        try { await API.win.mode('strip'); } catch (e) { /* ignore */ }
      }
      return;
    }
    Strip.stop();
    ensureChart();
    if (!opts.external) {
      try { await API.win.mode('full'); } catch (e) { /* ignore */ }
    }
    setTimeout(async () => {
      ChartView.resize();
      try {
        await setSymbol(S.symbol, { noRecent: true });
        ChartView.resetView();
      } catch (e) { /* setSymbol 内部已处理 */ }
    }, 260);
  }

  async function expandTo(sym) {
    if (sym && sym !== S.symbol) {
      S.symbol = sym;
      S.data = null;
    }
    await setMode('full');
  }

  function openStripPanel() {
    const box = $('stripLayer');
    const list = $('stripList');
    if (!box || !list) return;
    const cands = [];
    const push = (x) => {
      if (!x || !x.symbol) return;
      if (cands.some((c) => c.symbol === x.symbol)) return;
      cands.push({ symbol: x.symbol, name: x.name || '', label: x.label || '' });
    };
    push(currentItem());
    S.watch.forEach(push);
    S.recent.forEach(push);

    const cur = stripList().map((x) => x.symbol);
    if (!cands.length) {
      list.innerHTML = '<div class="si-empty">还没有收藏或近期标的。<br>先在搜索里点 ☆ 收藏几个，再回来勾选要放进滚动条的。</div>';
    } else {
      list.innerHTML = cands.map((c) => {
        const on = cur.indexOf(c.symbol) >= 0;
        return '<div class="si-row' + (on ? ' on' : '') + '" data-sym="' + escapeHtml(c.symbol) + '">' +
          '<span class="cb">' + (on ? '✓' : '') + '</span>' +
          '<span class="nm">' + escapeHtml(c.name || c.symbol) + '</span>' +
          '<span class="cd">' + escapeHtml(shortCode(c.symbol)) + '</span></div>';
      }).join('');
    }
    if ($('miStripCount')) $('miStripCount').textContent = stripList().length + '/' + STRIP_MAX;
    box.classList.remove('hidden');
  }

  function toggleStripSymbol(sym) {
    if (!sym) return;
    const cur = stripList().map((x) => ({ symbol: x.symbol, name: x.name || '', label: x.label || '', tag: x.tag || '' }));
    const i = cur.findIndex((x) => x.symbol === sym);
    if (i >= 0) {
      cur.splice(i, 1);
    } else {
      if (cur.length >= STRIP_MAX) {
        showHint('滚动条最多放 ' + STRIP_MAX + ' 个标的', null, null, 2600);
        return;
      }
      const src = S.watch.concat(S.recent).find((x) => x.symbol === sym) || { symbol: sym, name: '', label: '' };
      cur.push({ symbol: sym, name: src.name || '', label: src.label || '', tag: src.tag || '' });
    }
    S.strip = cur;
    API.save({ strip: S.strip });
    Strip.setList(stripList());
    openStripPanel();
  }

  /* ---------------- 关闭行为 / 价位条方向 ---------------- */
  function updateCloseMenu() {
    const v = $('miCloseVal');
    if (v) v.textContent = S.exitAsk ? '每次询问' : (S.exitAction === 'quit' ? '直接退出' : '保留价位条');
  }

  function updateStripDirMenu() {
    const v = $('miStripDirVal');
    if (v) v.textContent = S.stripDir === 'v' ? '竖向' : '横向';
  }

  function applyStripDir(d, notify) {
    S.stripDir = d === 'v' ? 'v' : 'h';
    document.body.classList.toggle('vstrip', S.stripDir === 'v');
    updateStripDirMenu();
    if (notify) {
      API.save({ strip_dir: S.stripDir });
      API.win.stripDir(S.stripDir);
      if (S.mode === 'strip') Strip.render();
    }
  }

  function openExit(view) {
    S.exitShown = true;
    S.exitView = view === 'settings' ? 'settings' : 'ask';
    const settings = S.exitView === 'settings';
    $('exitTitle').textContent = settings ? '关闭行为' : '关闭窗口';
    $('exitDesc').textContent = settings
      ? '设置点击关闭按钮或窗口 X 时的默认动作。托盘右键始终可以直接退出。'
      : '要直接退出，还是隐藏窗口、只在通知区保留实时价位条？';
    $('exitAsk').classList.toggle('hidden', settings);
    $('exitSettings').classList.toggle('hidden', !settings);
    $('exitCancel').textContent = settings ? '关闭' : '取消';
    if (!settings) {
      $('exitRemember').checked = false;
    } else {
      const cur = S.exitAsk ? 'ask' : S.exitAction;
      const r = document.querySelector('input[name="exitMode"][value="' + cur + '"]');
      if (r) r.checked = true;
    }
    el.menu.classList.add('hidden');
    $('exitLayer').classList.remove('hidden');
  }

  function closeExit() {
    S.exitShown = false;
    const box = $('exitLayer');
    if (box) box.classList.add('hidden');
  }

  async function answerExit(action, remember) {
    const ask = S.exitView === 'ask';
    closeExit();
    try {
      await API.win.close(action, remember && ask);
    } catch (e) { /* 选择退出时应用可能已关闭 */ }
    if (ask && remember && (action === 'quit' || action === 'tray')) {
      S.exitAsk = false;
      S.exitAction = action;
      updateCloseMenu();
    }
  }

  function requestClose() {
    el.menu.classList.add('hidden');
    if (!S.exitAsk) { answerExit(S.exitAction, false); return; }
    openExit('ask');
  }

  function dismissExit() {
    const ask = S.exitView === 'ask';
    closeExit();
    // 问询来自点 X 时要通知后端恢复窗口；设置面板直接关即可
    if (ask) API.win.close('cancel', false).catch(() => {});
  }

  async function saveExitSettings() {
    const r = document.querySelector('input[name="exitMode"]:checked');
    const v = r ? r.value : 'ask';
    S.exitAsk = v === 'ask';
    S.exitAction = v === 'quit' ? 'quit' : 'tray';
    await API.save({ exit_ask: S.exitAsk, exit_action: S.exitAction });
    updateCloseMenu();
    closeExit();
    showHint('关闭行为已保存', null, null, 2200);
  }

  /* ---------------- 窗口 ---------------- */
  function applyAlpha(v) {
    if (S.nativeAlpha) {
      document.documentElement.style.setProperty('--alpha', '1');
    } else {
      document.documentElement.style.setProperty('--alpha', String(v));
    }
    const t = $('alphaVal');
    if (t) t.textContent = Math.round(v * 100) + '%';
    const r = $('alphaRange');
    if (r) r.value = String(Math.round(v * 100));
  }

  function syncVolSwitch() {
    const sw = $('swVol');
    if (sw) sw.classList.toggle('on', S.vol);
  }

  function toggleVol() {
    S.vol = !S.vol;
    ChartView.setVolume(S.vol);
    syncVolSwitch();
    API.save({ vol: S.vol });
    renderLegend(null);
  }

  function bindUI() {
    // 周期
    el.tf.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (b) switchPeriod(b.dataset.p);
    });

    // 拖动：顶栏空白处
    const bar = $('bar');
    bar.addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      if (e.target.closest('button, input')) return;
      API.win.drag();
    });

    // 缩放热区
    document.querySelectorAll('.rz').forEach((h) => {
      h.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        API.win.resize(h.dataset.edge);
      });
    });

    // 搜索
    $('btnSearch').addEventListener('click', () => Search.open());
    $('symBtn').addEventListener('click', () => Search.open());

    // 收藏
    if (el.btnFav) el.btnFav.addEventListener('click', () => toggleFav(currentItem()));
    if (el.favPrev) el.favPrev.addEventListener('click', () => stepWatch(-1));
    if (el.favNext) el.favNext.addEventListener('click', () => stepWatch(1));
    if ($('progCancel')) $('progCancel').addEventListener('click', cancelLoad);

    // 收起 / 展开
    if ($('btnCollapse')) $('btnCollapse').addEventListener('click', () => setMode('strip'));
    if ($('stripExpand')) $('stripExpand').addEventListener('click', () => setMode('full'));
    if ($('stripGrip')) $('stripGrip').addEventListener('mousedown', (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      API.win.drag();
    });
    const stripEl = $('strip');
    if (stripEl) {
      stripEl.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('.si, button')) return;
        API.win.drag();
      });
    }

    // 滚动条标的配置
    if ($('miStrip')) {
      $('miStrip').addEventListener('click', () => {
        el.menu.classList.add('hidden');
        openStripPanel();
      });
    }
    if ($('miStripDir')) {
      $('miStripDir').addEventListener('click', () => {
        el.menu.classList.add('hidden');
        applyStripDir(S.stripDir === 'h' ? 'v' : 'h', true);
      });
    }
    if ($('miClose')) {
      $('miClose').addEventListener('click', () => openExit('settings'));
    }
    if ($('stripClose')) $('stripClose').addEventListener('click', () => $('stripLayer').classList.add('hidden'));
    if ($('stripAll')) {
      $('stripAll').addEventListener('click', () => {
        S.strip = S.watch.slice(0, STRIP_MAX);
        API.save({ strip: S.strip });
        Strip.setList(stripList());
        openStripPanel();
      });
    }
    if ($('stripList')) {
      $('stripList').addEventListener('click', (e) => {
        const row = e.target.closest('.si-row');
        if (row) toggleStripSymbol(row.dataset.sym);
      });
    }

    // 置顶
    el.btnPin.addEventListener('click', () => {
      S.pin = !S.pin;
      el.btnPin.classList.toggle('on', S.pin);
      API.win.topmost(S.pin);
      const sw = $('swTop');
      if (sw) sw.classList.toggle('on', S.pin);
    });

    // 透明度快捷按钮：循环三档
    $('btnAlpha').addEventListener('click', () => {
      const steps = [1.0, 0.9, 0.72];
      const cur = steps.reduce((best, v) => (Math.abs(v - S.alpha) < Math.abs(best - S.alpha) ? v : best), steps[0]);
      const i = steps.indexOf(cur);
      const v = steps[(i + 1) % steps.length];
      S.alpha = v;
      applyAlpha(v);
      API.win.opacity(v);
    });

    // 刷新
    $('btnRefresh').addEventListener('click', () => load(true));

    // EMA 面板
    el.btnEma.addEventListener('click', (e) => {
      e.stopPropagation();
      el.emaPanel.classList.toggle('hidden');
      el.menu.classList.add('hidden');
    });

    // 菜单
    $('btnMenu').addEventListener('click', (e) => {
      e.stopPropagation();
      el.menu.classList.toggle('hidden');
      el.emaPanel.classList.add('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('#menu') && !e.target.closest('#btnMenu')) el.menu.classList.add('hidden');
      if (!e.target.closest('#emaPanel') && !e.target.closest('#btnEma')) el.emaPanel.classList.add('hidden');
    });

    $('miTop').addEventListener('click', () => el.btnPin.click());
    $('miHide').addEventListener('click', () => API.win.hide());
    $('miQuit').addEventListener('click', requestClose);
    if ($('btnClose')) $('btnClose').addEventListener('click', requestClose);
    $('miDiag').addEventListener('click', openDiag);

    // 关闭行为弹层
    $('exitX').addEventListener('click', dismissExit);
    $('exitCancel').addEventListener('click', dismissExit);
    $('exitSave').addEventListener('click', saveExitSettings);
    $('exitAsk').addEventListener('click', (e) => {
      const b = e.target.closest('[data-act]');
      if (b) answerExit(b.dataset.act, $('exitRemember').checked);
    });
    $('diagClose').addEventListener('click', () => $('diag').classList.add('hidden'));
    if ($('miVol')) $('miVol').addEventListener('click', toggleVol);

    const range = $('alphaRange');
    range.addEventListener('input', () => {
      const v = Number(range.value) / 100;
      S.alpha = v;
      applyAlpha(v);
    });
    range.addEventListener('change', () => API.win.opacity(S.alpha));

    // 快捷键
    document.addEventListener('keydown', (e) => {
      if (e.isComposing) return;
      if (S.exitShown) {
        if (e.key === 'Escape') { e.preventDefault(); dismissExit(); }
        return;
      }
      if (e.altKey && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'm') {
        e.preventDefault();
        setMode(S.mode === 'strip' ? 'full' : 'strip');
        return;
      }
      if (S.mode === 'strip') return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        Search.open();
        return;
      }
      if (Search.isOpen()) return;
      if (e.key === 'Escape') {
        el.menu.classList.add('hidden');
        el.emaPanel.classList.add('hidden');
        return;
      }
      const tag = ((document.activeElement && document.activeElement.tagName) || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea') return;
      if (e.key === 'ArrowUp') { e.preventDefault(); stepWatch(-1); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); stepWatch(1); }
      else if (e.key === '[') { e.preventDefault(); stepRecent(-1); }
      else if (e.key === ']') { e.preventDefault(); stepRecent(1); }
      else if (e.key.toLowerCase() === 'r' && !e.ctrlKey && !e.metaKey && !e.altKey) { load(true); }
      else if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) { toggleFav(currentItem()); }
    });
  }

  /* ---------------- EMA 面板 ---------------- */
  function buildEmaPanel() {
    const COLORS = ['#7f93b0', '#b39b7a', '#9a8fb0', '#b9bfc9'];
    el.emaRows.innerHTML = '';
    S.ema.forEach((item, i) => {
      const row = document.createElement('div');
      row.className = 'row';
      row.innerHTML =
        '<span class="sw' + (item.on ? ' on' : '') + '"><i></i></span>' +
        '<span class="lb">EMA</span>' +
        '<input type="number" min="2" max="400" step="1" value="' + item.p + '">' +
        '<span class="cl" style="--c:' + COLORS[i] + '"></span>';
      const sw = row.querySelector('.sw');
      sw.addEventListener('click', () => {
        item.on = !item.on;
        sw.classList.toggle('on', item.on);
        ChartView.applyEma(S.ema);
        saveEma();
        renderLegend(null);
      });
      const inp = row.querySelector('input');
      inp.addEventListener('change', () => {
        let v = parseInt(inp.value, 10);
        if (!v || v < 2) v = 2;
        if (v > 400) v = 400;
        inp.value = v;
        item.p = v;
        ChartView.applyEma(S.ema);
        saveEma();
        renderLegend(null);
      });
      el.emaRows.appendChild(row);
    });
  }

  function saveEma() {
    const bySlot = {};
    ['20', '50', '100', '200'].forEach((k, i) => { bySlot[k] = S.ema[i].on; });
    API.save({ ema: bySlot });
  }

  /* ---------------- 诊断 ---------------- */
  async function openDiag() {
    const box = $('diag');
    const body = $('diagBody');
    box.classList.remove('hidden');
    body.textContent = '加载中…';
    try {
      const d = await API.diag();
      body.textContent = JSON.stringify(d, null, 2);
    } catch (e) {
      body.textContent = '诊断失败：' + e.message;
    }
  }

  /* ---------------- 工具 ---------------- */
  function setStatus(html) {
    el.statusL.innerHTML = html;
    if (html.indexOf('加载中') < 0) S.lastStatus = html;
  }
  function shortCode(symbol) { return (symbol || '').split(':')[1] || symbol; }
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
