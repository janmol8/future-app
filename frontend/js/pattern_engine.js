const PatternEngine = (() => {
  const BIAS_COLORS = { bull: '#089981', bear: '#F23645', neutral: '#2962FF' };
  let activeSeries = [];
  let activePriceLines = [];
  let activeDomOverlays = [];
  let activePatterns = [];
  let isVisible = true;
  let lastArgs = null;
  let appliedPatternId = null;
  let currentRequest = null;
  let redrawOverlayHandler = null;
  let lastOverlayPatterns = [];
  const cache = new Map();
  const MAX_RENDERED_PATTERNS = 80;

  async function loadAndDraw(symbol, interval, accountSize, riskPct, lotSize) {
    clearAll();
    appliedPatternId = null;
    if (!isVisible || !symbol) return;
    const settings = PatternSettings.get();
    const account = accountSize ?? settings.accountSize;
    const risk = riskPct ?? settings.riskPct;
    const lot = lotSize ?? settings.lotSize;
    const visibleScan = getVisibleScanConfig();
    lastArgs = { symbol, interval, accountSize: account, riskPct: risk, lotSize: lot };
    const cacheKey = `${symbol}|${interval}|${account}|${risk}|${lot}|${settings.minConfidence}|${visibleScan.lookback}|${visibleScan.anchorMode}|${visibleScan.anchorDate || ''}`;
    const cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.ts < 120000) {
      activePatterns = cached.patterns;
      PatternPanel.render(activePatterns, { interval, symbol, appliedPatternId, cached: true, scanWindow: cached.scanWindow });
      return;
    }
    if (currentRequest) currentRequest.abort();
    currentRequest = new AbortController();
    try {
      PatternPanel.renderLoading?.(symbol, interval);
      const params = new URLSearchParams({
        symbol,
        interval,
        account_size: String(account),
        risk_pct: String(risk),
        lot_size: String(lot),
        max_patterns: String(MAX_RENDERED_PATTERNS),
        lookback: String(visibleScan.lookback),
        min_confidence: String(settings.minConfidence || 40),
        anchor_mode: visibleScan.anchorMode,
      });
      if (visibleScan.anchorMode === 'date' && visibleScan.anchorDate) params.set('anchor_date', visibleScan.anchorDate);
      const res = await fetch(`/api/patterns?${params.toString()}`, {
        signal: currentRequest.signal,
      });
      const data = await res.json();
      activePatterns = (data.patterns || []).filter(p => passesSettings(p, settings)).slice(0, MAX_RENDERED_PATTERNS);
      cache.set(cacheKey, { ts: Date.now(), patterns: activePatterns, scanWindow: data.scan_window });
      PatternPanel.render(activePatterns, { interval: data.timeframe || interval, symbol, appliedPatternId, scanWindow: data.scan_window });
    } catch (e) {
      if (e.name === 'AbortError') return;
      console.warn('Pattern load failed:', e);
      PatternPanel.render([], { interval, symbol });
    } finally {
      currentRequest = null;
    }
  }

  function passesSettings(p, settings) {
    return p.confidence >= settings.minConfidence && !!settings.families?.[p.family] && !!settings.states?.[p.state];
  }

  function getVisibleScanConfig() {
    const settings = PatternSettings.get();
    const data = ChartModule.getCurrentData?.() || [];
    const range = ChartModule.getVisibleLogicalRange?.();
    if (!data.length || !range) {
      return {
        lookback: Math.max(1, Number(settings.lookback || 120)),
        anchorMode: settings.anchorMode || 'current',
        anchorDate: settings.anchorMode === 'date' ? settings.anchorDate : '',
      };
    }
    const start = Math.min(data.length - 1, Math.max(0, Math.floor(range.from)));
    const end = Math.min(data.length - 1, Math.max(0, Math.ceil(range.to)));
    if (end <= start) {
      return { lookback: Math.max(1, Number(settings.lookback || 120)), anchorMode: 'current', anchorDate: '' };
    }
    return {
      lookback: Math.max(1, end - start + 1),
      anchorMode: 'date',
      anchorDate: new Date(Number(data[end].time) * 1000).toISOString(),
    };
  }

  function reload() {
    if (lastArgs) loadAndDraw(lastArgs.symbol, lastArgs.interval, lastArgs.accountSize, lastArgs.riskPct, lastArgs.lotSize);
  }

  function renderAll(patterns) {
    const chart = ChartModule.getChart?.();
    const series = ChartModule.getCandleSeries?.();
    if (!chart || !series) return;
    clearAll();
    const markers = [];
    patterns.forEach(p => {
      const color = BIAS_COLORS[p.bias] || '#888';
      if (p.state !== 'confirmed') return;
      (p.draw?.lines || []).forEach(line => drawLineOrPrice(chart, series, line));
      (p.draw?.labels || []).forEach(l => markers.push({
        time: l.time,
        position: p.bias === 'bear' ? 'aboveBar' : 'belowBar',
        color: l.color || color,
        shape: 'circle',
        text: l.text,
        size: 1,
      }));
      (p.draw?.arrows || []).forEach(a => {
        markers.push({
          time: a.time,
          position: a.direction === 'down' ? 'aboveBar' : 'belowBar',
          color: a.color || color,
          shape: a.direction === 'down' ? 'arrowDown' : 'arrowUp',
          text: a.text,
          size: 2,
        });
      });
    });
    series.setMarkers?.(markers);
    drawPatternOverlays(patterns.filter(p => p.state === 'confirmed'));
  }

  function drawLineOrPrice(chart, series, lineSpec) {
    if (lineSpec.price != null) {
      // Native price lines extend infinitely. SL/targets are bounded by the DOM overlay instead.
      return;
    }
    if (!lineSpec.p1 || !lineSpec.p2) return;
    const s = chart.addLineSeries({ color: lineSpec.color || '#888', lineWidth: lineSpec.type === 'neckline' ? 2 : 1, lineStyle: 0, priceLineVisible: false, lastValueVisible: false });
    s.setData([{ time: lineSpec.p1.time, value: lineSpec.p1.price }, { time: lineSpec.p2.time, value: lineSpec.p2.price }]);
    activeSeries.push(s);
  }

  function labelForLine(line) {
    if (line.type === 'stop') return `SL: ${Number(line.price).toFixed(2)}`;
    if (line.type === 'target_classical') return `T1: ${Number(line.price).toFixed(2)}`;
    if (line.type === 'target_bulkowski') return `T2: ${Number(line.price).toFixed(2)}`;
    if (line.type === 'target_conservative') return `TC: ${Number(line.price).toFixed(2)}`;
    return '';
  }

  function drawHorizontalPriceLine(series, price, color, title, style = 'dashed') {
    const styleMap = { solid: 0, dotted: 1, dashed: 2 };
    const line = series.createPriceLine?.({ price, color, lineWidth: 1, lineStyle: styleMap[style] ?? 2, axisLabelVisible: !!title, title });
    if (line) activePriceLines.push(line);
  }

  function drawZone(series, zone, color) {
    // Pattern zones are drawn by the DOM overlay renderer because Lightweight Charts
    // does not provide native rectangle primitives in this build.
  }

  function drawPatternOverlays(patterns) {
    lastOverlayPatterns = patterns;
    renderDomOverlays(patterns);
    const chart = ChartModule.getChart?.();
    if (!chart || redrawOverlayHandler) return;
    redrawOverlayHandler = () => renderDomOverlays(lastOverlayPatterns);
    chart.timeScale?.().subscribeVisibleTimeRangeChange?.(redrawOverlayHandler);
  }

  function renderDomOverlays(patterns) {
    clearDomOverlays();
    const chart = ChartModule.getChart?.();
    const series = ChartModule.getCandleSeries?.();
    const data = ChartModule.getCurrentData?.() || [];
    const container = ChartModule.getContainer?.() || document.getElementById('chart-container');
    if (!chart || !series || !container || !data.length) return;
    container.style.position = container.style.position || 'relative';
    const layer = document.createElement('div');
    layer.className = 'pattern-overlay-layer';
    layer.style.cssText = 'position:absolute; inset:0; z-index:86; pointer-events:none; overflow:hidden;';
    container.appendChild(layer);
    activeDomOverlays.push(layer);
    patterns.forEach(pattern => drawPattern(pattern, { chart, series, data, layer }));
  }

  function drawPattern(pattern, ctx) {
    const overlay = pattern.draw?.overlay;
    if (!overlay) return;
    drawPatternZone(overlay, ctx);
    drawHorizontalOverlayLine(overlay.stopLoss?.price, overlay.stopLoss?.color || '#c0392b', 'SL', overlay.entrySignal?.bar, overlay.rangeEndBar, ctx);
    (overlay.targets || []).forEach(t => drawHorizontalOverlayLine(t.price, t.color, t.label, overlay.entrySignal?.bar, overlay.rangeEndBar, ctx));
    drawConfidenceBadge(overlay, ctx);
  }

  function drawPatternZone(overlay, { chart, series, data, layer }) {
    const zone = overlay.patternZone;
    if (!zone) return;
    const start = clampIndex(zone.startBar, data.length);
    const end = clampIndex(zone.endBar, data.length);
    if (start == null || end == null || end < start) return;
    const x1 = chart.timeScale().timeToCoordinate(data[start].time);
    const x2 = chart.timeScale().timeToCoordinate(data[end].time);
    if (x1 == null || x2 == null) return;
    const range = data.slice(start, end + 1);
    const high = Math.max(...range.map(c => Number(c.high)));
    const low = Math.min(...range.map(c => Number(c.low)));
    const yTop = series.priceToCoordinate(high);
    const yBottom = series.priceToCoordinate(low);
    if (yTop == null || yBottom == null) return;
    const rect = document.createElement('div');
    rect.style.cssText = [
      'position:absolute',
      `left:${Math.min(x1, x2)}px`,
      `top:${Math.min(yTop, yBottom)}px`,
      `width:${Math.max(4, Math.abs(x2 - x1))}px`,
      `height:${Math.max(12, Math.abs(yBottom - yTop))}px`,
      `background:${zone.color || 'rgba(127,119,221,0.12)'}`,
      'border:1px solid rgba(255,255,255,.18)',
      'border-radius:4px',
      'box-sizing:border-box',
    ].join(';');
    layer.appendChild(rect);
  }

  function drawHorizontalOverlayLine(price, color, label, startBar, endBar, { chart, series, data, layer }) {
    const n = Number(price);
    const start = clampIndex(startBar, data.length);
    const end = clampIndex(endBar ?? startBar, data.length);
    if (!Number.isFinite(n) || start == null || end == null) return;
    const x = chart.timeScale().timeToCoordinate(data[start].time);
    const xEnd = chart.timeScale().timeToCoordinate(data[end].time);
    const y = series.priceToCoordinate(n);
    if (x == null || xEnd == null || y == null) return;
    const line = document.createElement('div');
    line.style.cssText = [
      'position:absolute',
      `left:${x}px`,
      `top:${y}px`,
      `width:${Math.max(6, xEnd - x)}px`,
      `border-top:1px dashed ${color || '#888'}`,
    ].join(';');
    const tag = document.createElement('div');
    tag.textContent = `${label}: ${n.toFixed(2)}`;
    tag.style.cssText = [
      'position:absolute',
      `left:${Math.min(layer.clientWidth - 96, x + 6)}px`,
      `top:${y - 16}px`,
      `color:${color || '#888'}`,
      'font-size:10px',
      'font-weight:800',
      'background:rgba(0,0,0,.55)',
      'padding:1px 4px',
      'border-radius:3px',
    ].join(';');
    layer.appendChild(line);
    layer.appendChild(tag);
  }

  function drawConfidenceBadge(overlay, { chart, series, data, layer }) {
    const badge = overlay.confidenceBadge;
    const zone = overlay.patternZone;
    if (!badge || !zone) return;
    const start = clampIndex(zone.startBar, data.length);
    const end = clampIndex(zone.endBar, data.length);
    if (start == null || end == null || end < start) return;
    const x = chart.timeScale().timeToCoordinate(data[end].time);
    const high = Math.max(...data.slice(start, end + 1).map(c => Number(c.high)));
    const y = series.priceToCoordinate(high);
    if (x == null || y == null) return;
    const el = document.createElement('div');
    el.textContent = `${Math.round(Number(badge.value) || 0)}%`;
    el.style.cssText = [
      'position:absolute',
      `left:${x + 4}px`,
      `top:${Math.max(4, y - 22)}px`,
      'background:rgba(17,24,39,.9)',
      'border:1px solid rgba(255,255,255,.2)',
      'color:#fff',
      'font-size:10px',
      'font-weight:900',
      'padding:2px 5px',
      'border-radius:4px',
    ].join(';');
    layer.appendChild(el);
  }

  function clampIndex(index, length) {
    const n = Number(index);
    if (!Number.isInteger(n) || n < 0 || n >= length) return null;
    return n;
  }

  function clearDomOverlays() {
    activeDomOverlays.forEach(el => { try { el.remove(); } catch (e) {} });
    activeDomOverlays = [];
  }

  function clearAll() {
    const chart = ChartModule.getChart?.();
    const series = ChartModule.getCandleSeries?.();
    activeSeries.forEach(s => { try { chart?.removeSeries(s); } catch (e) {} });
    activePriceLines.forEach(l => { try { series?.removePriceLine?.(l); } catch (e) {} });
    series?.setMarkers?.([]);
    clearDomOverlays();
    lastOverlayPatterns = [];
    activeSeries = [];
    activePriceLines = [];
  }

  function apply(id) {
    const pattern = activePatterns.find(x => x.id === id);
    if (!pattern) return;
    clearAll();
    appliedPatternId = id;
    renderAll([pattern]);
    focus(id);
    PatternPanel.render(activePatterns, {
      interval: lastArgs?.interval || ChartModule.getActiveInterval?.(),
      symbol: lastArgs?.symbol || window.App?.currentSymbol,
      appliedPatternId,
    });
  }

  function clearApplied() {
    clearAll();
    appliedPatternId = null;
    PatternPanel.render(activePatterns, {
      interval: lastArgs?.interval || ChartModule.getActiveInterval?.(),
      symbol: lastArgs?.symbol || window.App?.currentSymbol,
      appliedPatternId,
    });
  }

  function resetForChart(symbol, interval) {
    if (currentRequest) currentRequest.abort();
    clearAll();
    appliedPatternId = null;
    activePatterns = [];
    lastOverlayPatterns = [];
    lastArgs = { symbol, interval };
    PatternPanel.renderIdle?.(symbol, interval);
  }

  function focus(id) {
    const chart = ChartModule.getChart?.();
    const data = ChartModule.getCurrentData?.() || [];
    const p = activePatterns.find(x => x.id === id);
    if (!chart || !p || !data.length) return;
    chart.timeScale().setVisibleRange({ from: data[Math.max(0, p.start_index - 5)]?.time, to: data[Math.min(data.length - 1, p.end_index + 8)]?.time });
  }

  function toggle() {
    isVisible = !isVisible;
    if (isVisible) reload();
    else clearAll();
  }

  return { loadAndDraw, reload, toggle, clearAll, clearApplied, resetForChart, apply, focus, getActive: () => activePatterns };
})();

window.PatternEngine = PatternEngine;
