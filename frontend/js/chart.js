/* 图表封装：白底、无红绿空心蜡烛、四条 EMA、成交量副图、十字光标图例 */
window.ChartView = (function () {
  const SLOT_COLORS = ['#7f93b0', '#b39b7a', '#9a8fb0', '#b9bfc9'];
  const VOL_UP = 'rgba(198,203,211,0.55)';
  const VOL_DN = 'rgba(166,173,183,0.65)';

  let chart = null;
  let candle = null;
  let volSeries = null;
  let container = null;
  let slots = [];
  let bars = [];
  let intraday = true;
  let volOn = true;
  let onCrosshair = null;
  let onRange = null;
  let ro = null;
  let curPeriod = '15m';
  // setData / 重设视口期间会触发 visibleLogicalRangeChange，先压住，
  // 否则会被误判成「用户拖到最左」而自动加载更早的历史
  let suppressRange = false;
  let rangeToken = 0;

  function toTime(t, isIntraday) {
    if (!isIntraday) {
      const d = new Date(t * 1000);
      const y = d.getUTCFullYear();
      const m = String(d.getUTCMonth() + 1).padStart(2, '0');
      const day = String(d.getUTCDate()).padStart(2, '0');
      return y + '-' + m + '-' + day;
    }
    return t;
  }

  function precisionFor(v) {
    const a = Math.abs(v || 0);
    if (a >= 1000) return 2;
    if (a >= 1) return 3;
    if (a >= 0.01) return 5;
    if (a >= 0.0001) return 7;
    return 9;
  }

  function priceFormatter(v) {
    if (v === null || v === undefined) return '';
    if (v < 0) return '';          // 价格轴在成交量区域会生成负刻度，直接隐藏
    const a = Math.abs(v);
    let d = 2;
    if (a < 0.01) d = 8;
    else if (a < 1) d = 6;
    else if (a < 100) d = 3;
    return v.toFixed(d).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');
  }

  function applyVolScale() {
    if (!chart) return;
    chart.priceScale('right').applyOptions({
      scaleMargins: { top: 0.08, bottom: volOn ? 0.24 : 0.08 },
    });
    if (volSeries) {
      volSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
        visible: false,
        borderVisible: false,
      });
    }
  }

  function init(el, cb, rangeCb) {
    container = el;
    onCrosshair = cb;
    onRange = rangeCb || null;

    chart = LightweightCharts.createChart(el, {
      autoSize: false,
      width: el.clientWidth || 480,
      height: el.clientHeight || 260,
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#8a8f98',
        fontSize: 10.5,
        attributionLogo: false,
        fontFamily: '"Segoe UI","Microsoft YaHei",system-ui,sans-serif',
      },
      grid: {
        vertLines: { color: '#f4f5f7' },
        horzLines: { color: '#f4f5f7' },
      },
      rightPriceScale: {
        borderColor: '#eceef2',
        borderVisible: true,
        entireTextOnly: true,
        scaleMargins: { top: 0.08, bottom: 0.08 },
      },
      timeScale: {
        borderColor: '#eceef2',
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 3,
        barSpacing: 6,
        minBarSpacing: 0.5,
        fixLeftEdge: false,
        lockVisibleTimeRangeOnResize: true,
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: {
          color: '#d3d8e0',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#a7aeb9',
        },
        horzLine: {
          color: '#d3d8e0',
          width: 1,
          style: LightweightCharts.LineStyle.Dashed,
          labelBackgroundColor: '#a7aeb9',
        },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      localization: { priceFormatter },
    });

    candle = chart.addCandlestickSeries({
      upColor: '#ffffff',
      downColor: '#dfe3e9',
      borderUpColor: '#b4bac4',
      borderDownColor: '#c3c8d1',
      borderVisible: true,
      wickUpColor: '#aab1bb',
      wickDownColor: '#9aa1ac',
      priceLineVisible: false,
      lastValueVisible: false,
      priceLineStyle: LightweightCharts.LineStyle.Dotted,
    });

    volSeries = chart.addHistogramSeries({
      priceScaleId: '',
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });

    slots = SLOT_COLORS.map((color) => {
      const s = chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        crosshairMarkerRadius: 0,
      });
      return { color, series: s, p: 0, on: false, data: [] };
    });

    applyVolScale();

    chart.subscribeCrosshairMove((param) => {
      if (!onCrosshair) return;
      if (!param || !param.time) {
        onCrosshair({ bar: null, emas: [], last: true });
        return;
      }
      const idx = indexOfTime(param.time);
      if (idx < 0) {
        onCrosshair({ bar: null, emas: [], last: true });
        return;
      }
      const b = bars[idx];
      const emas = slots
        .filter((s) => s.on && s.p > 0)
        .map((s) => ({ p: s.p, color: s.color, value: s.data[idx] === undefined ? null : s.data[idx] }));
      onCrosshair({
        bar: b,
        emas,
        last: false,
      });
    });

    ro = new ResizeObserver(() => resize());
    ro.observe(el);
    if (onRange) {
      chart.timeScale().subscribeVisibleLogicalRangeChange((r) => {
        if (r && !suppressRange) onRange(r);
      });
    }
    return chart;
  }

  let timeIndex = new Map();

  function indexOfTime(t) {
    const key = typeof t === 'string' ? t : Number(t);
    const v = timeIndex.get(String(key));
    return v === undefined ? -1 : v;
  }

  function resize() {
    if (!chart || !container) return;
    const w = Math.floor(container.clientWidth || 0);
    const h = Math.floor(container.clientHeight || 0);
    if (w < 10 || h < 10) return;
    chart.applyOptions({ width: w, height: h });
  }

  // 默认视口：按窗口宽度算根数，而不是固定 160 根。
  // 窄窗口 340px 时 160 根只有 2px 一根，蜡烛会挤成一条线。
  function targetBars(period) {
    const w = (container && container.clientWidth) || 480;
    const px = (period === '1d' || period === '1w') ? 6.4 : 5.0;
    return Math.max(48, Math.min(170, Math.round(w / px)));
  }

  function applyRange(n, period) {
    if (!chart || !n) return;
    const target = targetBars(period);
    try {
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, n - target),
        to: n + 3,
      });
    } catch (e) { /* ignore */ }
  }

  function setBars(rawBars, period, resetView) {
    bars = [];
    intraday = !(period === '1d' || period === '1w');
    curPeriod = period || '15m';
    timeIndex = new Map();
    let last = -Infinity;
    const data = [];
    const volData = [];
    for (const b of rawBars) {
      if (!b || b.length < 5) continue;
      if (b[0] <= last) continue;
      last = b[0];
      bars.push(b);
      const t = toTime(b[0], intraday);
      data.push({ time: t, open: b[1], high: b[2], low: b[3], close: b[4] });
      volData.push({
        time: t,
        value: b[5] || 0,
        color: b[4] >= b[1] ? VOL_UP : VOL_DN,
      });
    }
    bars.forEach((b, i) => timeIndex.set(String(toTime(b[0], intraday)), i));

    const lastClose = bars.length ? bars[bars.length - 1][4] : 1;
    const prec = precisionFor(lastClose);
    candle.applyOptions({ priceFormat: { type: 'price', precision: prec, minMove: Math.pow(10, -prec) } });
    candle.setData(data);
    if (volSeries) volSeries.setData(volData);
    chart.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } });

    const n = data.length;
    const token = ++rangeToken;
    suppressRange = true;
    if (n > 0 && resetView !== false) applyRange(n, period);
    // setData 后图表会先自动 fit 一次，等布局稳定再校正，避免视口被它覆盖
    requestAnimationFrame(() => {
      if (token !== rangeToken) return;
      if (n > 0 && resetView !== false) applyRange(n, period);
      requestAnimationFrame(() => {
        if (token === rangeToken) suppressRange = false;
      });
    });
  }

  function applyEma(cfg) {
    if (!slots.length) return;
    cfg = cfg || [];
    slots.forEach((slot, i) => {
      const c = cfg[i] || { p: 0, on: false };
      slot.p = Number(c.p) || 0;
      slot.on = !!c.on && slot.p > 0;
      slot.series.applyOptions({ visible: slot.on });
      if (!slot.on || !bars.length) {
        slot.data = [];
        slot.series.setData([]);
        return;
      }
      const pts = EMA.series(bars, slot.p, (t) => toTime(t, intraday));
      const arr = new Array(bars.length).fill(null);
      pts.forEach((pt) => {
        const idx = timeIndex.get(String(pt.time));
        if (idx !== undefined) arr[idx] = pt.value;
      });
      slot.data = arr;
      slot.series.setData(pts);
    });
  }

  function setVolume(on) {
    volOn = !!on;
    if (volSeries) volSeries.applyOptions({ visible: volOn });
    applyVolScale();
  }

  function lastBar() {
    if (!bars.length) return null;
    return bars[bars.length - 1];
  }

  function logicalRange() {
    if (!chart) return null;
    try {
      return chart.timeScale().getVisibleLogicalRange();
    } catch (e) {
      return null;
    }
  }

  function setLogicalRange(r) {
    if (!chart || !r) return;
    try {
      chart.timeScale().setVisibleLogicalRange(r);
    } catch (e) { /* ignore */ }
  }

  function resetView() {
    if (!chart) return;
    const n = bars.length;
    if (!n) return;
    const token = ++rangeToken;
    suppressRange = true;
    applyRange(n, curPeriod);
    requestAnimationFrame(() => {
      if (token !== rangeToken) return;
      applyRange(n, curPeriod);
      requestAnimationFrame(() => {
        if (token === rangeToken) suppressRange = false;
      });
    });
  }

  function emaAt(idx) {
    return slots
      .filter((s) => s.on && s.p > 0)
      .map((s) => ({ p: s.p, color: s.color, value: s.data[idx] }));
  }

  function destroy() {
    if (ro) ro.disconnect();
    if (chart) chart.remove();
    chart = null;
  }

  return {
    init, setBars, applyEma, setVolume, resize, lastBar, emaAt, count: () => bars.length,
    logicalRange, setLogicalRange, resetView, destroy,
  };
})();
