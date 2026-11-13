/* EMA：SMA 种子 + 递推，前 p-1 个为 null */
window.EMA = (function () {
  function compute(closes, p) {
    const out = new Array(closes.length).fill(null);
    if (!p || p < 1 || closes.length < p) return out;
    let sum = 0;
    for (let i = 0; i < p; i++) sum += closes[i];
    let prev = sum / p;
    out[p - 1] = prev;
    const k = 2 / (p + 1);
    for (let i = p; i < closes.length; i++) {
      prev = closes[i] * k + prev * (1 - k);
      out[i] = prev;
    }
    return out;
  }

  /** bars: [[t,o,h,l,c,v]...] -> [{time, value}...]（跳过 null） */
  function series(bars, p, toTime) {
    const closes = bars.map((b) => b[4]);
    const vals = compute(closes, p);
    const out = [];
    for (let i = 0; i < bars.length; i++) {
      if (vals[i] === null) continue;
      out.push({ time: toTime(bars[i][0]), value: vals[i] });
    }
    return out;
  }

  return { compute, series };
})();
