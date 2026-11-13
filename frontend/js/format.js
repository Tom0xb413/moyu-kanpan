/* 数值/时间格式化 */
window.Fmt = (function () {
  function price(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    if (v === 0) return '0';
    const a = Math.abs(v);
    let s;
    if (a >= 0.000001 && a < 1e15) {
      s = String(Number(v.toPrecision(6)));
      if (s.indexOf('e') >= 0) s = v.toFixed(12).replace(/0+$/, '').replace(/\.$/, '');
    } else {
      s = v.toExponential(3);
    }
    const parts = s.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  function pct(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    const sign = v > 0 ? '+' : '';
    return sign + v.toFixed(2) + '%';
  }

  function signed(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return (v > 0 ? '+' : '') + price(v);
  }

  function vol(v) {
    if (!v) return '—';
    const a = Math.abs(v);
    if (a >= 1e8) return (v / 1e8).toFixed(2) + '亿';
    if (a >= 1e4) return (v / 1e4).toFixed(1) + '万';
    return Number(v.toPrecision(4)).toString();
  }

  function pad(n) {
    return n < 10 ? '0' + n : '' + n;
  }

  /** 图表时间：intraday 用本地时间 HH:mm / MM/DD HH:mm；日线为业务日字符串 */
  function axisTime(t, withDate) {
    if (typeof t === 'string') return t.slice(5);
    const d = new Date(t * 1000);
    const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (!withDate) return hm;
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + hm;
  }

  function fullTime(t) {
    if (typeof t === 'string') return t;
    const d = new Date(t * 1000);
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function ago(sec) {
    if (sec < 2) return '刚刚';
    if (sec < 60) return Math.floor(sec) + 's 前';
    if (sec < 3600) return Math.floor(sec / 60) + 'm 前';
    return Math.floor(sec / 3600) + 'h 前';
  }

  function compact(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    const a = Math.abs(v);
    const d = a >= 1000 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6;
    const s = v.toFixed(d);
    const parts = s.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  return { price, pct, signed, vol, compact, axisTime, fullTime, ago };
})();
