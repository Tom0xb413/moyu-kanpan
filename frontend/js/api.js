/* 本地 API 封装（同源 + token） */
window.API = (function () {
  const meta = document.querySelector('meta[name="app-token"]');
  const fromUrl = new URLSearchParams(location.search).get('k');
  const token = fromUrl || (meta ? meta.content : '') || '';

  function qs(params) {
    const p = new URLSearchParams(params || {});
    if (token) p.set('k', token);
    return p.toString();
  }

  async function request(path, params, opts) {
    opts = opts || {};
    const ctrl = new AbortController();
    let why = '';
    const ext = opts.signal;
    const onAbort = () => {
      why = why || 'user';
      try { ctrl.abort(); } catch (e) { /* ignore */ }
    };
    if (ext) {
      if (ext.aborted) onAbort();
      else ext.addEventListener('abort', onAbort, { once: true });
    }
    const timeout = opts.timeout || 15000;
    const timer = setTimeout(() => {
      why = 'timeout';
      try { ctrl.abort(); } catch (e) { /* ignore */ }
    }, timeout);

    try {
      const url = path + (path.indexOf('?') >= 0 ? '&' : '?') + qs(params);
      const init = {
        method: opts.method || 'GET',
        signal: ctrl.signal,
        cache: 'no-store',
      };
      if (opts.headers) init.headers = opts.headers;
      if (opts.body !== undefined) init.body = opts.body;
      const res = await fetch(url, init);
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
      if (!res.ok) {
        const err = (data && data.error) || { code: 'HTTP_' + res.status, message: text.slice(0, 120) };
        const e = new Error(err.message || '请求失败');
        e.code = err.code;
        e.status = res.status;
        throw e;
      }
      return data;
    } catch (err) {
      if (err && err.name === 'AbortError') {
        const e = new Error(why === 'timeout' ? '请求超时' : '已取消');
        e.code = why === 'timeout' ? 'TIMEOUT' : 'ABORTED';
        throw e;
      }
      throw err;
    } finally {
      clearTimeout(timer);
      if (ext) ext.removeEventListener('abort', onAbort);
    }
  }

  const get = (path, params, opts) => request(path, params, opts);
  const post = (path, params, opts) =>
    request(path, params, Object.assign({ method: 'POST' }, opts || {}));

  return {
    token,
    get,
    post,
    kline: (symbol, period, limit, refresh, opts) => {
      const o = opts || {};
      const params = { symbol, period, limit: limit || 800, refresh: refresh ? 1 : 0 };
      if (o.before) params.before = o.before;
      return get('/api/kline', params, o);
    },
    quote: (symbols) => get('/api/quote', { symbols: symbols.join(',') }),
    ticker: (symbols, maxAge) =>
      get('/api/ticker', { symbols: symbols.join(','), maxAge: maxAge || 2.5 }, { timeout: 9000 }),
    search: (q) => get('/api/search', { q: q || '', limit: 16 }),
    diag: () => get('/api/diag'),
    prefs: () => get('/api/prefs'),
    save: (patch) => post('/api/prefs', {}, {
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
      timeout: 8000,
    }).catch(() => null),
    warm: (symbol) => post('/api/warm', symbol ? { symbol } : {}, { timeout: 8000 }).catch(() => null),
    win: {
      drag: () => post('/api/win/drag'),
      resize: (edge) => post('/api/win/resize', { edge }),
      topmost: (on) => post('/api/win/topmost', { on: on ? 1 : 0 }),
      opacity: (v) => post('/api/win/opacity', { v }),
      hide: () => post('/api/win/hide'),
      show: () => post('/api/win/show'),
      mode: (mode) => post('/api/win/mode', { mode }),
      quit: () => post('/api/win/quit'),
      close: (action, remember) => post('/api/win/close', { action, remember: remember ? 1 : 0 }),
      stripDir: (dir) => post('/api/win/strip-dir', { dir }),
      state: () => get('/api/win/state'),
    },
  };
})();
