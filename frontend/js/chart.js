/**
 * Chart module - manages the TradingView Lightweight Charts instance.
 */
const ChartModule = (() => {
  let chart = null;
  let mainSeries = null;
  let candleSeries = null;
  let lineSeries = null;
  let chartType = 'candles';
  let volumeSeries = null;
  let indicatorSeries = {};
  let extHoursPriceLine = null;
  let currentData = [];
  let container = null;
  let activeInterval = '1d';
  let onVisibleRangeChanged = null;
  let countdownEnabled = true;
  let countdownInterval = null;
  let lastTickTimestamp = null;
  let lastTickPrice = null;

  const INDICATOR_COLORS = ['#2962FF', '#089981', '#F23645', '#FF9800', '#9B59B6', '#F1C40F'];
  let colorIdx = 0;

  let indicatorMetadata = {};
  let subPanes = {}; // Tracks { id: { container, chart, legend } }
  let paneOrder = [];
  let syncingLogicalRange = false;
  const RIGHT_OFFSET_BARS = 3;
  const MIN_PRICE_SCALE_WIDTH = 54;
  const MAX_PRICE_SCALE_WIDTH = 88;
  const PRICE_SCALE_LABEL_PADDING = 18;

  function nextColor() {
    return INDICATOR_COLORS[colorIdx++ % INDICATOR_COLORS.length];
  }

  function baseTimeScaleOptions(visible = true) {
    return {
      visible,
      borderColor: getChartThemeOptions().borderColor,
      timeVisible: true,
      secondsVisible: true,
      barSpacing: 15,
      minBarSpacing: 1,
      rightOffset: RIGHT_OFFSET_BARS,
      rightBarStaysOnScroll: true,
    };
  }

  function applyCompactRightOffset(targetChart = chart) {
    if (!targetChart) return;
    targetChart.applyOptions({
      timeScale: {
        rightOffset: RIGHT_OFFSET_BARS,
        rightBarStaysOnScroll: true,
      }
    });
  }

  function applyCompactRightOffsetToAll() {
    applyCompactRightOffset(chart);
    Object.values(subPanes).forEach(pane => applyCompactRightOffset(pane.chart));
  }

  function formatPriceLabel(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return '';
    return n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function measureLabelWidth(labels) {
    if (typeof document === 'undefined') return MIN_PRICE_SCALE_WIDTH;
    const canvas = measureLabelWidth.canvas || (measureLabelWidth.canvas = document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    if (!ctx) return MIN_PRICE_SCALE_WIDTH;
    ctx.font = '12px Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif';
    return labels.reduce((max, label) => Math.max(max, ctx.measureText(String(label)).width), 0);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function getMainPriceScaleWidth() {
    if (!currentData.length) return MIN_PRICE_SCALE_WIDTH;
    const last = liveCandle || currentData[currentData.length - 1];
    const visibleSample = currentData.slice(-160);
    let minPrice = Number.POSITIVE_INFINITY;
    let maxPrice = Number.NEGATIVE_INFINITY;
    visibleSample.forEach(c => {
      const low = finiteNumber(c.low ?? c.value ?? c.close);
      const high = finiteNumber(c.high ?? c.value ?? c.close);
      if (low !== null) minPrice = Math.min(minPrice, low);
      if (high !== null) maxPrice = Math.max(maxPrice, high);
    });
    const labels = [minPrice, maxPrice, last?.open, last?.high, last?.low, last?.close ?? last?.value]
      .map(formatPriceLabel)
      .filter(Boolean);
    return clamp(Math.ceil(measureLabelWidth(labels) + PRICE_SCALE_LABEL_PADDING), MIN_PRICE_SCALE_WIDTH, MAX_PRICE_SCALE_WIDTH);
  }

  function getOscillatorPriceScaleWidth() {
    return clamp(Math.ceil(measureLabelWidth(['100.00', '80.00', '50.00', '20.00', '0.00']) + PRICE_SCALE_LABEL_PADDING), MIN_PRICE_SCALE_WIDTH, MAX_PRICE_SCALE_WIDTH);
  }

  function applyRightPriceScaleWidth() {
    const alignedWidth = Math.max(getMainPriceScaleWidth(), paneOrder.length ? getOscillatorPriceScaleWidth() : MIN_PRICE_SCALE_WIDTH);
    if (chart) chart.priceScale('right').applyOptions({ minimumWidth: alignedWidth });
    Object.values(subPanes).forEach(pane => {
      if (pane.chart) pane.chart.priceScale('right').applyOptions({ minimumWidth: alignedWidth });
    });
    return alignedWidth;
  }

  function getChartThemeOptions() {
    const isLight = document.body.classList.contains('light-theme');
    return isLight ? {
      background: '#ffffff',
      textColor: '#131722',
      borderColor: '#d6d9e0',
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0.08)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.08)' },
      },
      layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#131722', fontSize: 12 },
    } : {
      background: '#131722',
      textColor: '#d1d4dc',
      borderColor: '#2a2e39',
      grid: {
        vertLines: { color: 'rgba(42, 46, 57, 0.1)' },
        horzLines: { color: 'rgba(42, 46, 57, 0.1)' },
      },
      layout: { background: { type: 'solid', color: '#131722' }, textColor: '#d1d4dc', fontSize: 12 },
    };
  }

  function applyThemeToChart(targetChart) {
    if (!targetChart) return;
    const theme = getChartThemeOptions();
    targetChart.applyOptions({
      layout: theme.layout,
      grid: theme.grid,
      rightPriceScale: { borderColor: theme.borderColor },
      timeScale: { borderColor: theme.borderColor },
      crosshair: {
        vertLine: { color: theme.textColor },
        horzLine: { color: theme.textColor },
      },
    });
  }

  function applyTheme() {
    const theme = getChartThemeOptions();
    const mainWrapper = document.getElementById('main-chart-wrapper');
    if (mainWrapper) mainWrapper.style.background = theme.background;
    if (container) container.style.background = theme.background;
    applyThemeToChart(chart);
    Object.values(subPanes).forEach(pane => {
      if (pane.container) pane.container.style.background = theme.background;
      applyThemeToChart(pane.chart);
    });
    applyCompactRightOffsetToAll();
    applyRightPriceScaleWidth();
  }

  function sameLogicalRange(a, b) {
    return !!a && !!b && Math.abs(a.from - b.from) < 0.0001 && Math.abs(a.to - b.to) < 0.0001;
  }

  function setLogicalRange(targetChart, range) {
    if (!targetChart || !range) return;
    const currentRange = targetChart.timeScale().getVisibleLogicalRange();
    if (!sameLogicalRange(currentRange, range)) {
      targetChart.timeScale().setVisibleLogicalRange(range);
    }
  }

  function syncSubPaneRanges(range) {
    if (!range || syncingLogicalRange) return;
    syncingLogicalRange = true;
    try {
      Object.values(subPanes).forEach(pane => setLogicalRange(pane.chart, range));
    } finally {
      syncingLogicalRange = false;
    }
  }

  function ensurePriceAxisWidget(parent) {
    let widget = document.getElementById('price-axis-widget');
    if (widget) widget.remove();

    widget = document.createElement('div');
    widget.id = 'price-axis-widget';
    widget.className = 'price-axis-widget';
    widget.innerHTML = `
      <span id="paw-price" class="paw-price">-</span>
      <span id="paw-timer" class="paw-timer">00:00</span>
    `;
    parent.appendChild(widget);
    return widget;
  }

  function refreshPaneLayout() {
      const hasSubPanes = paneOrder.length > 0;
      
      // 1. Toggle main chart time scale
      if (chart) {
          chart.applyOptions({
              timeScale: baseTimeScaleOptions(!hasSubPanes)
          });
      }
      
      // 2. Toggle subpane time scales - only the last one should show it
      paneOrder.forEach((id, index) => {
          const isLast = index === paneOrder.length - 1;
          const pane = subPanes[id];
          if (pane && pane.chart) {
              pane.chart.applyOptions({
                  timeScale: baseTimeScaleOptions(isLast)
              });
          }
      });
      applyRightPriceScaleWidth();
  }

  function finiteNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function finiteTime(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function sanitizeCandles(rows = []) {
    return rows
      .map(i => {
        const time = finiteTime(i?.time);
        const open = finiteNumber(i?.open);
        const high = finiteNumber(i?.high);
        const low = finiteNumber(i?.low);
        const close = finiteNumber(i?.close);
        const volume = finiteNumber(i?.volume) ?? 0;
        if (time === null || open === null || high === null || low === null || close === null) return null;
        return { time, open, high, low, close, value: close, volume };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
  }

  function sanitizeLineData(rows = []) {
    return rows
      .map(i => {
        const time = finiteTime(i?.time);
        const value = finiteNumber(i?.value ?? i?.close);
        if (time === null || value === null) return null;
        return i?.color ? { time, value, color: i.color } : { time, value };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
  }

  function sanitizeHistogramData(rows = []) {
    return sanitizeLineData(rows);
  }

  function init(containerEl) {
    if (!containerEl) return null;
    container = containerEl;
    
    // Clear and set flex
    container.innerHTML = '';
    container.style.display = 'flex';
    container.style.flexDirection = 'column';
    container.style.overflow = 'hidden';

    // 1. Main Chart Wrapper
    let mainWrapper = document.createElement('div');
    mainWrapper.id = 'main-chart-wrapper';
    mainWrapper.style.flex = '1';
    mainWrapper.style.position = 'relative';
    mainWrapper.style.minHeight = '200px';
    mainWrapper.style.background = getChartThemeOptions().background;
    container.appendChild(mainWrapper);

    // OHLC Legend
    const ohlc = document.createElement('div');
    ohlc.id = 'ohlc-legend';
    ohlc.className = 'ohlc-legend';
    mainWrapper.appendChild(ohlc);

    // Indicator Legend (for overlays)
    const indLeg = document.createElement('div');
    indLeg.id = 'indicator-legend';
    indLeg.className = 'indicator-legend';
    mainWrapper.appendChild(indLeg);

    // Crosshair info element
    const chi = document.createElement('div');
    chi.id = 'crosshair-info';
    chi.className = 'crosshair-info';
    mainWrapper.appendChild(chi);

    if (typeof LightweightCharts === 'undefined') {
      console.error("LightweightCharts missing!");
      return null;
    }

    try {
      chart = LightweightCharts.createChart(mainWrapper, {
        layout: getChartThemeOptions().layout,
        grid: getChartThemeOptions().grid,
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: getChartThemeOptions().borderColor, scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: MIN_PRICE_SCALE_WIDTH },
        leftPriceScale: { visible: false },
        timeScale: { 
          borderColor: getChartThemeOptions().borderColor, 
          timeVisible: true, 
          secondsVisible: true, 
          barSpacing: 15, 
          minBarSpacing: 1, 
          rightOffset: RIGHT_OFFSET_BARS,
          rightBarStaysOnScroll: true,
          tickMarkFormatter: (time, tickMarkType, locale) => {
            const date = new Date(time * 1000);
            const isDailyOrHigher = ['1d', '1w', '1M'].includes(activeInterval);
            const options = { timeZone: 'Asia/Kolkata', hour12: false };
            if (tickMarkType === 0) options.year = 'numeric';
            else if (tickMarkType === 1) options.month = 'short';
            else if (tickMarkType === 2) { 
                if (isDailyOrHigher) { options.day = 'numeric'; options.month = 'short'; }
                else { options.hour = '2-digit'; options.minute = '2-digit'; }
            }
            else { 
                if (isDailyOrHigher) { options.day = 'numeric'; options.month = 'short'; }
                else { options.hour = '2-digit'; options.minute = '2-digit'; }
            }
            return new Intl.DateTimeFormat('en-IN', options).format(date);
          }
        },
        localization: {
          locale: 'en-IN',
          timeFormatter: (timestamp) => {
            const date = new Date(timestamp * 1000);
            const isDailyOrHigher = ['1d', '1w', '1M'].includes(activeInterval);
            return new Intl.DateTimeFormat('en-IN', { 
              timeZone: 'Asia/Kolkata', year: 'numeric', month: 'short', day: 'numeric',
              hour: isDailyOrHigher ? undefined : '2-digit', minute: isDailyOrHigher ? undefined : '2-digit',
              second: (!isDailyOrHigher && activeInterval.includes('s')) ? '2-digit' : undefined, hour12: false 
            }).format(date);
          },
          priceFormatter: (price) => price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
        },
        handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
        handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      });
    } catch (e) { console.error("Failed to create chart:", e); throw e; }

    ensurePriceAxisWidget(mainWrapper);

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => { 
      syncSubPaneRanges(range);
    });

    chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
        if (onVisibleRangeChanged) onVisibleRangeChanged(range);
    });

    createSeries();
    initCountdown();
    applyTheme();

    const ro = new ResizeObserver(() => { 
      if (mainWrapper && chart) chart.applyOptions({ width: mainWrapper.clientWidth, height: mainWrapper.clientHeight });
      Object.values(subPanes).forEach(pane => {
          if (pane.container && pane.chart) pane.chart.applyOptions({ width: pane.container.clientWidth, height: pane.container.clientHeight });
      });
      applyCompactRightOffsetToAll();
      applyRightPriceScaleWidth();
    });
    ro.observe(containerEl);

    chart.subscribeCrosshairMove((p) => {
        if (!p.point || !p.time) {
            updateLegend(null);
            Object.values(subPanes).forEach(pane => pane.chart.clearCrosshairPosition());
        } else {
            updateLegend(p);
            Object.values(subPanes).forEach(pane => {
                if (pane.series) pane.chart.setCrosshairPosition(0, p.time, pane.series);
            });
        }
    });

    refreshPaneLayout();
    return chart;
  }

  function createSubPane(id) {
    if (subPanes[id]) return subPanes[id].chart;
    
    const wrapper = document.createElement('div');
    wrapper.id = `subpane-wrapper-${id}`;
    wrapper.style.height = '180px'; 
    wrapper.style.minHeight = '120px';
    wrapper.style.width = '100%';
    wrapper.style.borderTop = '1px solid var(--border-color)';
    wrapper.style.position = 'relative';
    wrapper.style.flexShrink = '0';
    wrapper.style.background = getChartThemeOptions().background;
    
    container.appendChild(wrapper);

    // Create Legend for this subpane
    const leg = document.createElement('div');
    leg.id = `subpane-legend-${id}`;
    leg.className = 'indicator-legend';
    leg.style.position = 'absolute';
    leg.style.top = '10px';
    leg.style.left = '10px';
    leg.style.zIndex = '70';
    leg.style.pointerEvents = 'none';
    wrapper.appendChild(leg);

    const subChart = LightweightCharts.createChart(wrapper, {
      layout: getChartThemeOptions().layout,
      grid: getChartThemeOptions().grid,
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
      timeScale: baseTimeScaleOptions(false),
      rightPriceScale: { borderColor: getChartThemeOptions().borderColor, scaleMargins: { top: 0.1, bottom: 0.1 }, minimumWidth: MIN_PRICE_SCALE_WIDTH },
      leftPriceScale: { visible: false },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
    });

    subChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range || syncingLogicalRange) return;
        syncingLogicalRange = true;
        try {
            setLogicalRange(chart, range);
            Object.values(subPanes).forEach(pane => {
                if (pane.chart !== subChart) setLogicalRange(pane.chart, range);
            });
        } finally {
            syncingLogicalRange = false;
        }
    });

    subChart.subscribeCrosshairMove((p) => {
        if (!p.point || !p.time) {
            chart.clearCrosshairPosition();
            Object.values(subPanes).forEach(pane => pane.chart.clearCrosshairPosition());
            updateLegend(null);
        } else {
            let mainPrice = 0;
            const dataPoint = currentData.find(d => d.time === p.time);
            if (dataPoint) mainPrice = dataPoint.close;
            
            chart.setCrosshairPosition(mainPrice, p.time, mainSeries);
            Object.values(subPanes).forEach(pane => {
                if(pane.chart !== subChart && pane.series) pane.chart.setCrosshairPosition(0, p.time, pane.series);
            });
            updateLegend({ time: p.time, seriesData: new Map(), fromSubPane: true });
        }
    });

    const range = chart.timeScale().getVisibleLogicalRange();
    if (range) subChart.timeScale().setVisibleLogicalRange(range);
    applyCompactRightOffset(subChart);

    subPanes[id] = { container: wrapper, chart: subChart, legend: leg };
    paneOrder.push(id);
    refreshPaneLayout();
    applyTheme();
    return subChart;
  }

  function addTimeScaleAnchor(subChart) {
    const anchor = subChart.addLineSeries({
      color: 'rgba(0,0,0,0)',
      lineVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    anchor.setData(currentData.map(c => ({ time: c.time, value: 50 })));
    return anchor;
  }

  function validateIndicatorTimes(label, primaryData) {
    if (!currentData.length || !primaryData.length) {
      console.warn(`[${label}] Missing candle or indicator data; not plotting.`, {
        candleCount: currentData.length,
        indicatorCount: primaryData.length,
      });
      return false;
    }
    const firstCandleTime = currentData[0].time;
    const lastCandleTime = currentData[currentData.length - 1].time;
    const firstIndicatorTime = primaryData[0].time;
    const lastIndicatorTime = primaryData[primaryData.length - 1].time;
    console.log(`[${label}] candle/indicator time validation`, {
      firstCandleTime,
      lastCandleTime,
      firstIndicatorTime,
      lastIndicatorTime,
    });
    if (lastIndicatorTime !== lastCandleTime) {
      console.warn(`[${label}] Last indicator timestamp does not match last candle timestamp; not plotting.`, {
        lastCandleTime,
        lastIndicatorTime,
      });
      return false;
    }
    return true;
  }

  function removeSubPane(id) {
      if (subPanes[id]) {
          subPanes[id].chart.remove();
          subPanes[id].container.remove();
          delete subPanes[id];
          paneOrder = paneOrder.filter(pid => pid !== id);
          refreshPaneLayout();
      }
  }

  function createSeries() {
    if (!chart) return;
    if (mainSeries) { chart.removeSeries(mainSeries); mainSeries = null; }
    if (volumeSeries) { volumeSeries = null; }

    const isLight = document.body.classList.contains('light-theme');
    const upColor = '#089981', downColor = '#f23645';

    if (chartType === 'candles') {
      mainSeries = chart.addCandlestickSeries({
        upColor, downColor, borderVisible: false, wickUpColor: upColor, wickDownColor: downColor,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
      });
      candleSeries = mainSeries;
      lineSeries = null;
    } else {
      mainSeries = chart.addLineSeries({
        color: '#2962ff', lineWidth: 2,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 }
      });
      lineSeries = mainSeries;
      candleSeries = null;
    }
    
    // Volume overlay on main chart
    volumeSeries = chart.addHistogramSeries({
        color: '#26a69a', priceFormat: { type: 'volume' },
        priceScaleId: '', // Overlay
    });
    volumeSeries.priceScale().applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
    });
  }

  function initCountdown() {
    if (countdownInterval) cancelAnimationFrame(countdownInterval);
    const tick = () => {
      updateCountdown();
      countdownInterval = requestAnimationFrame(tick);
    };
    countdownInterval = requestAnimationFrame(tick);
  }

  function formatCountdown(remMs, intervalSec) {
    const safeMs = Math.max(0, remMs);
    const totalSeconds = Math.ceil(safeMs / 1000);
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.floor((totalSeconds % 3600) / 60);
    const s = totalSeconds % 60;
    return `${h > 0 ? String(h).padStart(2, '0') + ':' : ''}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  function updateCountdown() {
    const el = document.getElementById('price-axis-widget');
    const priceSpan = document.getElementById('paw-price');
    const timerSpan = document.getElementById('paw-timer');

    if (!el || !countdownEnabled || !chart || !mainSeries || currentData.length === 0) { 
        if(el) el.style.display = 'none'; 
        return; 
    }

    const intervalSec = parseIntervalToSeconds(activeInterval);
    const nowMs = Date.now();
    let remMs = 0;

    const lastCandle = currentData[currentData.length - 1];
    const activeCandleTime = finiteTime(liveCandleTime) ?? finiteTime(liveCandle?.time) ?? finiteTime(lastCandle?.time);
    if (activeCandleTime !== null && Number.isFinite(intervalSec) && intervalSec > 0) {
        remMs = ((activeCandleTime + intervalSec) * 1000) - nowMs;
    }
    
    if (timerSpan) {
        timerSpan.textContent = formatCountdown(remMs, intervalSec);
    }
    el.style.display = 'flex';

    const currentPrice = liveCandle ? liveCandle.close : lastCandle.close;
    const openPrice = liveCandle ? liveCandle.open : lastCandle.open;
    
    if (priceSpan) {
        priceSpan.textContent = currentPrice.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        if (currentPrice >= openPrice) {
            priceSpan.style.background = 'var(--accent-green)';
        } else {
            priceSpan.style.background = 'var(--accent-red)';
        }
        priceSpan.style.color = '#fff';
    }

    const y = mainSeries.priceToCoordinate(currentPrice);
    if (y !== null && !isNaN(y)) {
        el.style.top = `${y}px`;
        el.style.display = 'flex';
    } else {
        el.style.display = 'none';
    }
  }

  function parseIntervalToSeconds(i) {
    if (!i) return 60;
    const match = i.match(/^(\d+)([smhdwM])/);
    if (!match) return 86400;
    const num = parseInt(match[1]);
    const unit = match[2];
    const multipliers = { s: 1, m: 60, h: 3600, d: 86400, w: 604800, M: 2592000 };
    return num * multipliers[unit];
  }

  function setChartType(t) {
    if (chartType === t) return;
    chartType = t;
    const r = chart.timeScale().getVisibleLogicalRange();
    createSeries();
    if (currentData.length > 0) {
      const d = sanitizeCandles(currentData);
      mainSeries.setData(d);
      if (r) chart.timeScale().setVisibleLogicalRange(r);
      applyCompactRightOffsetToAll();
      applyRightPriceScaleWidth();
      applyTheme();
    }
  }

  function formatVolume(v) {
    if (!v) return '0';
    if (v >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (v >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (v >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toFixed(0);
  }

  function updateLegend(p) {
    const ohlcEl = document.getElementById('ohlc-legend');
    const indEl = document.getElementById('indicator-legend');
    if (!ohlcEl || !indEl || !mainSeries) return;

    // 1. OHLC Legend Update
    const ohlcData = p ? p.seriesData.get(mainSeries) : (currentData.length > 0 ? currentData[currentData.length - 1] : null);
    if (ohlcData) {
        const price = ohlcData.close !== undefined ? ohlcData.close : (ohlcData.value || 0);
        ohlcEl.innerHTML = `
            <span>O <span style="color:var(--text-primary)">${ohlcData.open?.toFixed(2) || price.toFixed(2)}</span></span>
            <span>H <span style="color:var(--text-primary)">${ohlcData.high?.toFixed(2) || price.toFixed(2)}</span></span>
            <span>L <span style="color:var(--text-primary)">${ohlcData.low?.toFixed(2) || price.toFixed(2)}</span></span>
            <span>C <span style="color:var(--text-primary)">${price.toFixed(2)}</span></span>
            ${ohlcData.volume !== undefined ? `<span>Vol <span style="color:var(--text-primary)">${formatVolume(ohlcData.volume)}</span></span>` : ''}
        `;
    }

    // 2. Indicators Legend Routing
    const mainHtml = [];
    const subPaneHtmls = {};

    Object.entries(indicatorMetadata).forEach(([id, meta]) => {
        const ind = indicatorSeries[id];
        if (!ind) return;

        let mainVal = '-';
        let subVals = [];

        let dataPoint = null;
        if (p) {
            dataPoint = p.seriesData.get(ind.series);
            if(!dataPoint && ind.data && p.time) {
                dataPoint = ind.data.find(d => d.time === p.time) || null;
            }
        } else {
            dataPoint = ind.data ? ind.data[ind.data.length - 1] : null;
        }
        
        if (dataPoint) {
            const v = dataPoint.value !== undefined ? dataPoint.value : (dataPoint.close || 0);
            mainVal = typeof v === 'number' ? v.toFixed(2) : '-';
        }

        if (ind.subSeries) {
            Object.entries(ind.subSeries).forEach(([key, s]) => {
                if (key === 'ribbon') return; // Don't show ribbon in legend
                let sp = p ? p.seriesData.get(s) : null;
                if (!sp && ind.data) {
                    const lastD = p ? ind.data.find(d => d.time === p.time) : ind.data[ind.data.length - 1];
                    if (lastD && lastD[key] !== undefined) {
                        sp = { value: lastD[key] };
                    }
                }
                if (sp) {
                    const sv = sp.value !== undefined ? sp.value : (sp.close || 0);
                    subVals.push({ label: key.toUpperCase(), value: typeof sv === 'number' ? sv.toFixed(2) : sv });
                }
            });
        }

        const itemHtml = `
            <div class="legend-item" id="leg-${id}" onmouseenter="ChartModule.highlightIndicator('${id}', true)" onmouseleave="ChartModule.highlightIndicator('${id}', false)">
                <div class="legend-main">
                    <span style="color:${ind.color || '#2962ff'}">●</span>
                    <span class="legend-title">${meta.name}</span>
                    <span class="legend-params">${meta.params ? '(' + meta.params + ')' : ''}</span>
                </div>
                <div class="legend-values">
                    <div class="legend-val-grp">
                        <span class="legend-value" style="color:${ind.color}">${mainVal}</span>
                    </div>
                    ${subVals.map(sv => `
                        <div class="legend-val-grp">
                            <span class="legend-label">${sv.label}:</span>
                            <span class="legend-value">${sv.value}</span>
                        </div>
                    `).join('')}
                </div>
                <div class="legend-actions">
                    <div class="legend-btn" onclick="ChartModule.toggleIndicatorVisibility('${id}')" title="Hide/Show"><i class="far ${meta.visible ? 'fa-eye' : 'fa-eye-slash'}"></i></div>
                    <div class="legend-btn" onclick="ChartModule.showIndicatorSettings('${id}')" title="Settings"><i class="fas fa-cog"></i></div>
                    <div class="legend-btn remove" onclick="ChartModule.removeIndicatorSeries('${id}')" title="Remove"><i class="fas fa-times"></i></div>
                </div>
            </div>
        `;

        if (subPanes[id]) {
            if (!subPaneHtmls[id]) subPaneHtmls[id] = [];
            subPaneHtmls[id].push(itemHtml);
        } else {
            mainHtml.push(itemHtml);
        }
    });

    indEl.innerHTML = mainHtml.join('');
    Object.entries(subPaneHtmls).forEach(([pid, items]) => {
        const subLegend = document.getElementById(`subpane-legend-${pid}`);
        if (subLegend) subLegend.innerHTML = items.join('');
    });
  }

  function highlightIndicator(id, active) {
    const ind = indicatorSeries[id];
    if (!ind || !ind.series || !ind.series.applyOptions) return;
    const width = active ? 4 : (ind.options?.lineWidth || 2);
    ind.series.applyOptions({ lineWidth: width });
    if (ind.subSeries) {
        Object.values(ind.subSeries).forEach(s => {
            if (s.applyOptions) s.applyOptions({ lineWidth: width });
        });
    }
  }

  function toggleIndicatorVisibility(id) {
    const ind = indicatorSeries[id];
    const meta = indicatorMetadata[id];
    if (!ind || !meta) return;
    
    meta.visible = !meta.visible;
    if (ind.series && ind.series.applyOptions) ind.series.applyOptions({ visible: meta.visible });
    if (ind.subSeries) {
        Object.values(ind.subSeries).forEach(s => {
            if (s.applyOptions) s.applyOptions({ visible: meta.visible });
        });
    }
    updateLegend();
  }

  function showIndicatorSettings(id) {
    const meta = indicatorMetadata[id];
    if (meta && window.Indicators) {
        if (typeof window.Indicators.showSettingsForActive === 'function') {
            window.Indicators.showSettingsForActive(id);
            return;
        }
    }
  }

  function setData(c, autoFit = true) {
    if (!mainSeries || !chart) return;
    const d = sanitizeCandles(c);
    currentData = d;
    if (d.length) {
      console.log('[Candles] sorted data range', {
        firstCandleTime: d[0].time,
        lastCandleTime: d[d.length - 1].time,
        count: d.length,
      });
    }
    mainSeries.applyOptions({ priceFormat: { type: 'price', precision: 2, minMove: 0.01 } });
    mainSeries.setData(d);
    applyRightPriceScaleWidth();
    if (d.length > 0 && autoFit) {
      chart.timeScale().fitContent();
      applyCompactRightOffsetToAll();
    }
    clearIndicatorSeries(true);
    resetLiveCandle();
    updateLegend();
    updateCountdown();
  }

  let liveCandle = null, liveCandleTime = null;
  let currentSymbol = null;
  let candleRefreshTimer = null;
  function resetLiveCandle() {
    liveCandle = null;
    liveCandleTime = null;
    lastTickTimestamp = null;
    lastTickPrice = null;
  }
  function getLiveCandle() { return liveCandle; }
  // ⚡ FAST path: update only the closing price from live quote cache (<10ms response)
  async function refreshLiveClose() {
    if (!currentSymbol || !mainSeries || currentData.length === 0) return;
    try {
      const resp = await fetch(`/api/live-candle?symbol=${encodeURIComponent(currentSymbol)}&interval=${activeInterval}`);
      if (!resp.ok) return;
      const live = await resp.json();
      if (!live || !live.close) return;

      const p = live.close;
      const roundedTime = live.time;
      const lastDataCandle = currentData[currentData.length - 1];

      // Update or create the live candle with the fresh close
      if (!liveCandle || roundedTime !== liveCandleTime) {
        if (roundedTime === lastDataCandle.time) {
          liveCandle = { ...lastDataCandle, close: p, high: Math.max(lastDataCandle.high, p), low: Math.min(lastDataCandle.low, p) };
        } else if (roundedTime > lastDataCandle.time) {
          const openPrice = liveCandle ? liveCandle.close : lastDataCandle.close;
          liveCandle = { time: roundedTime, open: openPrice, high: Math.max(openPrice, p), low: Math.min(openPrice, p), close: p, volume: 0 };
          currentData.push(liveCandle);
        } else return;
        liveCandleTime = roundedTime;
      } else {
        liveCandle.close = p;
        liveCandle.high  = Math.max(liveCandle.high, p);
        liveCandle.low   = Math.min(liveCandle.low,  p);
      }

      // For daily intervals the live-candle endpoint also has open/high/low
      if (!live.intraday_only && live.open && live.high && live.low) {
        liveCandle.open = live.open;
        liveCandle.high = live.high;
        liveCandle.low  = live.low;
      }

      mainSeries.update(liveCandle);
      lastTickPrice = p;
      updateLegend(null);
      updateCountdown();
    } catch (e) { /* silent — SSE tick will cover if this fails */ }
  }

  // SLOW path: correct open/high/low from yfinance OHLC (catches any drift)
  async function refreshLastCandle() {
    if (!currentSymbol || !mainSeries || currentData.length === 0) return;
    try {
      const range = activeInterval === '1d' ? 5 : (activeInterval === '1w' ? 4 : 30);
      const candles = await Datafeed.fetchOHLC(currentSymbol, activeInterval, range);
      if (!candles || candles.length === 0) return;
      const cleanFetched = sanitizeCandles(candles);
      if (!cleanFetched.length) return;
      const lastCurrentCandle = currentData[currentData.length - 1];
      const serverLast = cleanFetched[cleanFetched.length - 1];

      if (serverLast.time === lastCurrentCandle.time) {
        lastCurrentCandle.open   = serverLast.open;
        lastCurrentCandle.volume = serverLast.volume;
        if (liveCandle && liveCandle.time === lastCurrentCandle.time) {
          lastCurrentCandle.high  = Math.max(serverLast.high, liveCandle.high);
          lastCurrentCandle.low   = Math.min(serverLast.low,  liveCandle.low);
          lastCurrentCandle.close = liveCandle.close;
          liveCandle.open = lastCurrentCandle.open;
          liveCandle.high = lastCurrentCandle.high;
          liveCandle.low  = lastCurrentCandle.low;
          mainSeries.update(liveCandle);
        } else {
          lastCurrentCandle.high  = serverLast.high;
          lastCurrentCandle.low   = serverLast.low;
          lastCurrentCandle.close = serverLast.close;
          const clean = sanitizeCandles([lastCurrentCandle])[0];
          if (clean) mainSeries.update(clean);
        }
        applyRightPriceScaleWidth();
        updateLegend(null);
      } else if (serverLast.time > lastCurrentCandle.time) {
        currentData.push(serverLast);
        const cleanNew = sanitizeCandles([serverLast])[0];
        if (cleanNew) mainSeries.update(cleanNew);
        liveCandle = null;
        liveCandleTime = null;
        applyRightPriceScaleWidth();
        updateLegend(null);
      }
    } catch (e) { console.warn('[CandleRefresh] Failed:', e.message); }
  }

  let liveCloseTimer = null;
  function startCandleRefresh(symbol) {
    stopCandleRefresh();
    currentSymbol = symbol;
    const intervalSec = Math.max(parseIntervalToSeconds(activeInterval), 1);

    // Fast timer: refresh live close price every 2s (from cache, no yfinance)
    liveCloseTimer = setInterval(refreshLiveClose, 2000);

    // Slow timer: correct open/high/low from yfinance, matched to backend cache TTL
    let refreshMs;
    if      (intervalSec <=    60) refreshMs =  5000;
    else if (intervalSec <=   300) refreshMs =  8000;
    else if (intervalSec <=   900) refreshMs = 10000;
    else if (intervalSec <=  1800) refreshMs = 15000;
    else if (intervalSec <=  3600) refreshMs = 30000;
    else                           refreshMs = 60000;
    candleRefreshTimer = setInterval(refreshLastCandle, refreshMs);
  }

  function stopCandleRefresh() {
    if (candleRefreshTimer) { clearInterval(candleRefreshTimer); candleRefreshTimer = null; }
    if (liveCloseTimer) { clearInterval(liveCloseTimer); liveCloseTimer = null; }
  }

  function updateLiveTick(t) {
    if (!mainSeries || !chart || !currentData || currentData.length === 0) return;
    const p = finiteNumber(t.price);
    if (p === null) return;

    // Use backend-computed timeRounded (bucket-aligned, IST-aware for Indian stocks)
    const intervalSec = Math.max(parseIntervalToSeconds(activeInterval), 1);
    let roundedTime;
    if (t.timeRounded && Number.isFinite(Number(t.timeRounded))) {
      roundedTime = Number(t.timeRounded);
    } else {
      const ts = finiteTime(t.timestamp) ?? Math.floor(Date.now() / 1000);
      roundedTime = Math.floor(ts / intervalSec) * intervalSec;
    }

    const rawTs = finiteTime(t.timestamp) ?? Math.floor(Date.now() / 1000);
    if (lastTickTimestamp !== null) {
      if (rawTs < lastTickTimestamp) return;
      if (rawTs === lastTickTimestamp && p === lastTickPrice) return;
    }

    const lastDataCandle = currentData[currentData.length - 1];
    if (roundedTime < lastDataCandle.time) return;

    lastTickTimestamp = rawTs;
    lastTickPrice = p;

    // NSE quote open/high/low = ENTIRE DAY range — correct only for 1d candles.
    // For intraday (1m/5m/etc.), we must track price extremes from ticks.
    const isDaily = intervalSec >= 86400;
    const hasRealOHLC = isDaily &&
                        Number.isFinite(finiteNumber(t.open)) &&
                        Number.isFinite(finiteNumber(t.high)) &&
                        Number.isFinite(finiteNumber(t.low));

    if (!liveCandle || roundedTime !== liveCandleTime) {
      if (roundedTime === lastDataCandle.time) {
        liveCandle = hasRealOHLC
          ? { time: roundedTime, open: t.open, high: t.high, low: t.low, close: p, volume: t.volume || 0 }
          : { ...lastDataCandle, close: p, high: Math.max(lastDataCandle.high, p), low: Math.min(lastDataCandle.low, p) };
      } else {
        const openPrice = liveCandle ? liveCandle.close : lastDataCandle.close;
        liveCandle = hasRealOHLC
          ? { time: roundedTime, open: t.open, high: t.high, low: t.low, close: p, volume: t.volume || 0 }
          : { time: roundedTime, open: openPrice, high: Math.max(openPrice, p), low: Math.min(openPrice, p), close: p, volume: 0 };
        currentData.push(liveCandle);
      }
      liveCandleTime = roundedTime;
    } else {
      liveCandle.close = p;
      if (hasRealOHLC) {
        liveCandle.high   = t.high;
        liveCandle.low    = t.low;
        liveCandle.open   = t.open;
        liveCandle.volume = t.volume || liveCandle.volume;
      } else {
        liveCandle.high = Math.max(liveCandle.high, p);
        liveCandle.low  = Math.min(liveCandle.low,  p);
      }
    }

    mainSeries.update(liveCandle);
    applyRightPriceScaleWidth();
    updateLegend(null);
    updateCountdown();
  }

  function addIndicatorSeries(id, name, type, data, options = {}) {
    if (!chart) return;
    if (indicatorSeries[id]) { removeIndicatorSeries(id, true); }
    const isSubPane = type === 'subpane' || type === 'macd' || type.includes('subpane');
    const targetChart = isSubPane ? createSubPane(id) : chart;
    
    if (isSubPane) {
        applyRightPriceScaleWidth();
    }
    const c = options.color || nextColor();
    const isOscillator = options.indicatorId === 'rsi' || options.indicatorId === 'mfi' || type.includes('subpane');
    if (isOscillator) {
        options.autoscaleInfoProvider = () => ({ priceRange: { minValue: 0, maxValue: 100 } });
    }
    const s = targetChart.addLineSeries({ 
        color: c, lineWidth: 2, lastValueVisible: true, priceLineVisible: false, title: name, ...options 
    });
    const cleanData = sanitizeLineData(data);
    s.setData(cleanData);
    let ribbon = null;
    if (options.indicatorId === 'rsi' || options.indicatorId === 'mfi') {
        ribbon = targetChart.addAreaSeries({ topColor: 'rgba(41, 121, 255, 0.06)', bottomColor: 'rgba(41, 121, 255, 0.06)', lineVisible: false, lastValueVisible: false, priceLineVisible: false, baseValue: { type: 'price', price: 30 }, autoscaleInfoProvider: options.autoscaleInfoProvider });
        ribbon.setData(cleanData.map(p => ({ time: p.time, value: 70 })));
        s.createPriceLine({ price: 70, color: 'rgba(120, 123, 134, 0.4)', lineWidth: 1, lineStyle: 2 });
        s.createPriceLine({ price: 30, color: 'rgba(120, 123, 134, 0.4)', lineWidth: 1, lineStyle: 2 });
    }
    indicatorSeries[id] = { series: s, name, type, data: cleanData, color: c, options, subSeries: ribbon ? { ribbon } : null };
    indicatorMetadata[id] = { name, type: options.indicatorId || type, params: options.paramStr || options.period || '', visible: true };
    if (isSubPane && subPanes[id]) { subPanes[id].series = s; }
    updateLegend();
    return s;
  }

  function addMACDSeries(id, name, d, params = {}) {
    if (!chart || !d) return;
    const subChart = createSubPane(id);
    const anchor = addTimeScaleAnchor(subChart);
    const m = subChart.addLineSeries({ color: '#2962FF', lineWidth: 2, lastValueVisible: true, priceLineVisible: false });
    const s = subChart.addLineSeries({ color: '#FF9800', lineWidth: 1.5, lastValueVisible: true, priceLineVisible: false });
    const h = subChart.addHistogramSeries({ lastValueVisible: false, priceLineVisible: false });
    const macdData = sanitizeLineData(d.macd || []);
    const signalData = sanitizeLineData(d.signal || []);
    const histogramData = sanitizeHistogramData((d.histogram || []).map(i => ({ time: i.time, value: i.value, color: i.value >= 0 ? 'rgba(38, 166, 154, 0.6)' : 'rgba(239, 83, 80, 0.6)' })));
    if (!validateIndicatorTimes(name, macdData)) { subChart.removeSeries(anchor); removeSubPane(id); return; }
    m.setData(macdData); s.setData(signalData); h.setData(histogramData);
    const paramStr = `${params.fast || 12}, ${params.slow || 26}, ${params.sig || 9}`;
    indicatorSeries[id] = { series: m, name, subSeries: { signal: s, hist: h, anchor }, data: macdData, color: '#2962FF', options: {} };
    indicatorMetadata[id] = { name, type: 'macd', params: paramStr, visible: true };
    if (subPanes[id]) subPanes[id].series = m;
    updateLegend();
  }

  function addStochSeries(id, name, d, title = 'Stoch', params = {}) {
    if (!chart || !d) return;
    const subChart = createSubPane(id);
    const anchor = addTimeScaleAnchor(subChart);
    const autoscaleInfo = () => ({ priceRange: { minValue: 0, maxValue: 100 } });
    const k = subChart.addLineSeries({ color: '#2962FF', lineWidth: 2, lastValueVisible: true, priceLineVisible: false, autoscaleInfoProvider: autoscaleInfo });
    const d_ser = subChart.addLineSeries({ color: '#FF9800', lineWidth: 2, lastValueVisible: true, priceLineVisible: false, autoscaleInfoProvider: autoscaleInfo });
    const ribbon = subChart.addAreaSeries({ topColor: 'rgba(41, 121, 255, 0.06)', bottomColor: 'rgba(41, 121, 255, 0.06)', lineVisible: false, lastValueVisible: false, priceLineVisible: false, baseValue: { type: 'price', price: 20 }, autoscaleInfoProvider: autoscaleInfo });
    const kData = sanitizeLineData(d.k || []);
    const dData = sanitizeLineData(d.d || []);
    if (!validateIndicatorTimes(title, kData)) { subChart.removeSeries(anchor); removeSubPane(id); return; }
    k.setData(kData); d_ser.setData(dData);
    if (currentData.length > 0) ribbon.setData(currentData.map(p => ({ time: p.time, value: 80 })));
    k.createPriceLine({ price: 80, color: 'rgba(120, 123, 134, 0.5)', lineWidth: 1, lineStyle: 2 });
    k.createPriceLine({ price: 50, color: 'rgba(120, 123, 134, 0.25)', lineWidth: 1, lineStyle: 2 });
    k.createPriceLine({ price: 20, color: 'rgba(120, 123, 134, 0.5)', lineWidth: 1, lineStyle: 2 });
    
    let paramStr = "";
    if (title === 'Stoch RSI') {
        paramStr = `${params.k_smooth || 3}, ${params.d_smooth || 3}, ${params.rsi_period || 14}, ${params.stoch_period || 14}`;
    } else {
        paramStr = `${params.k_period || 14}, ${params.smooth || 3}, ${params.d_period || 3}`;
    }

    indicatorSeries[id] = { series: k, name, subSeries: { d: d_ser, ribbon, anchor }, data: kData, color: '#2962FF', options: {} };
    indicatorMetadata[id] = { name, type: title === 'Stoch RSI' ? 'stochrsi' : 'stoch', params: paramStr, visible: true };
    if (subPanes[id]) subPanes[id].series = k;
    updateLegend();
  }

  function addADXSeries(id, name, d) {
    if (!chart || !d) return;
    const subChart = createSubPane(id);
    const a = subChart.addLineSeries({ color: '#FF9800', lineWidth: 2, lastValueVisible: true, priceLineVisible: false });
    const p = subChart.addLineSeries({ color: '#089981', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
    const m = subChart.addLineSeries({ color: '#F23645', lineWidth: 1.5, lastValueVisible: false, priceLineVisible: false });
    const adxData = sanitizeLineData(d.adx || []);
    a.setData(adxData); p.setData(sanitizeLineData(d.plus_di || [])); m.setData(sanitizeLineData(d.minus_di || []));
    indicatorSeries[id] = { series: a, name, subSeries: { plus_di: p, minus_di: m }, data: adxData, color: '#FF9800', options: {} };
    indicatorMetadata[id] = { name, type: 'adx', params: '14', visible: true };
    if (subPanes[id]) subPanes[id].series = a;
    updateLegend();
  }

  function addAOSeries(id, name, d) {
    if (!chart || !d) return;
    const subChart = createSubPane(id);
    const s = subChart.addHistogramSeries({ lastValueVisible: true, priceLineVisible: false });
    const data = sanitizeHistogramData(d.map((v, i) => {
        const prev = i > 0 ? d[i-1].value : v.value;
        const color = v.value >= prev ? '#089981' : '#f23645';
        return { time: v.time, value: v.value, color };
    }));
    s.setData(data);
    indicatorSeries[id] = { series: s, name, data, options: {} };
    indicatorMetadata[id] = { name, type: 'ao', params: '34, 5', visible: true };
    if (subPanes[id]) subPanes[id].series = s;
    updateLegend();
  }

  function removeIndicatorSeries(id, silent = false) {
    const ind = indicatorSeries[id]; if (!ind) return;
    if (subPanes[id]) { removeSubPane(id); } 
    else {
        if (ind.subSeries) Object.values(ind.subSeries).forEach(s => chart.removeSeries(s));
        if (ind.series) chart.removeSeries(ind.series);
    }
    delete indicatorSeries[id]; delete indicatorMetadata[id];
    updateLegend();
    if (!silent && window.Indicators && typeof window.Indicators.removeActiveIndicator === 'function') {
        window.Indicators.removeActiveIndicator(id);
    }
  }

  function clearIndicatorSeries(silent = false) { Object.keys(indicatorSeries).forEach(id => removeIndicatorSeries(id, silent)); colorIdx = 0; }
  function resize() {
    if (chart && container) {
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
      Object.values(subPanes).forEach(pane => {
        if (pane.container && pane.chart) {
          pane.chart.applyOptions({ width: pane.container.clientWidth, height: pane.container.clientHeight });
        }
      });
      applyCompactRightOffsetToAll();
      applyRightPriceScaleWidth();
    }
  }
  function scrollToRecent() { if (chart) { applyCompactRightOffsetToAll(); chart.timeScale().scrollToRealTime(); } }

  return {
    init, setData, updateLiveTick, addIndicatorSeries, addMACDSeries, addStochSeries, addADXSeries, addAOSeries,
    removeIndicatorSeries, clearIndicatorSeries, resize, scrollToRecent, highlightIndicator, toggleIndicatorVisibility,
    showIndicatorSettings, startCandleRefresh, stopCandleRefresh, resetLiveCandle, setChartType, applyTheme, setActiveInterval: (i) => activeInterval = i,
    getChart: () => chart,
    getCandleSeries: () => candleSeries || mainSeries || lineSeries,
    getActiveInterval: () => activeInterval,
    clearPriceLines: () => {},
    getCurrentData: () => currentData.slice(),
    getLiveCandle,
    getContainer: () => container,
    getVisibleLogicalRange: () => chart?.timeScale()?.getVisibleLogicalRange?.() || null,
    takeScreenshot: () => chart?.takeScreenshot(), setCountdownEnabled: (v) => { countdownEnabled = v; }
  };
})();

window.ChartModule = ChartModule;
