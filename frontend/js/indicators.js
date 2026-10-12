/**
 * Indicators module - TradingView-style indicator catalog and chart binding.
 */
const Indicators = (() => {
  let currentSymbol = '';
  let currentInterval = '1d';
  let activeIndicators = {};

  const INDICATOR_DEFS = {
    trend: {
      label: 'Trend Indicators',
      icon: 'fa-chart-line',
      items: [
        { id: 'sma', name: 'Moving Average (SMA)', subType: 'overlay', defaultPeriod: 20 },
        { id: 'ema', name: 'Moving Average (EMA)', subType: 'overlay', defaultPeriod: 20 },
        { id: 'wma', name: 'Weighted Moving Average', subType: 'overlay', defaultPeriod: 20 },
        { id: 'hma', name: 'Hull Moving Average', subType: 'overlay', defaultPeriod: 20 },
        { id: 'linearregression', name: 'Linear Regression', subType: 'overlay', defaultPeriod: 20 },
        { id: 'vwap', name: 'VWAP', subType: 'overlay', defaultPeriod: 20 },
        { id: 'bollinger', name: 'Bollinger Bands', subType: 'multi_overlay', defaultPeriod: 20 },
        { id: 'keltner', name: 'Keltner Channels', subType: 'multi_overlay', defaultPeriod: 20 },
        { id: 'donchian', name: 'Donchian Channels', subType: 'multi_overlay', defaultPeriod: 20 },
        { id: 'supertrend', name: 'SuperTrend', subType: 'overlay', defaultPeriod: 10 },
        { id: 'psar', name: 'Parabolic SAR', subType: 'psar', defaultPeriod: 20 },
        { id: 'ichimoku', name: 'Ichimoku Cloud', subType: 'ichimoku', defaultPeriod: 20 },
      ],
    },
    oscillator: {
      label: 'Oscillators',
      icon: 'fa-wave-square',
      items: [
        { id: 'rsi', name: 'Relative Strength Index (RSI)', subType: 'subpane', defaultPeriod: 14 },
        { id: 'macd', name: 'MACD', subType: 'macd', defaultPeriod: 12 },
        { id: 'stoch', name: 'Stochastic Oscillator', subType: 'multi_subpane', defaultPeriod: 14 },
        { id: 'stochrsi', name: 'Stochastic RSI', subType: 'multi_subpane', defaultPeriod: 14 },
        { id: 'adx', name: 'Average Directional Index', subType: 'adx', defaultPeriod: 14 },
        { id: 'cci', name: 'Commodity Channel Index', subType: 'subpane', defaultPeriod: 20 },
        { id: 'williamsr', name: 'Williams %R', subType: 'subpane', defaultPeriod: 14 },
        { id: 'roc', name: 'Rate of Change', subType: 'subpane', defaultPeriod: 12 },
        { id: 'momentum', name: 'Momentum', subType: 'subpane', defaultPeriod: 10 },
        { id: 'trix', name: 'TRIX', subType: 'subpane', defaultPeriod: 15 },
      ],
    },
    volume: {
      label: 'Volume Indicators',
      icon: 'fa-chart-column',
      items: [
        { id: 'obv', name: 'On-Balance Volume', subType: 'subpane', defaultPeriod: 20 },
        { id: 'mfi', name: 'Money Flow Index', subType: 'subpane', defaultPeriod: 14 },
        { id: 'cmf', name: 'Chaikin Money Flow', subType: 'subpane', defaultPeriod: 20 },
        { id: 'ao', name: 'Awesome Oscillator', subType: 'histogram_subpane', defaultPeriod: 34 },
      ],
    },
    volatility: {
      label: 'Volatility',
      icon: 'fa-bolt',
      items: [
        { id: 'atr', name: 'Average True Range', subType: 'subpane', defaultPeriod: 14 },
      ],
    },
    other: {
      label: 'Other',
      icon: 'fa-ellipsis',
      items: [
        { id: 'pivotpoints', name: 'Pivot Points (Standard)', subType: 'pivot_points', defaultPeriod: 1 },
        { id: 'sr_levels', name: 'Support & Resistance (Auto)', subType: 'sr_levels', defaultPeriod: 400 },
      ],
    },
  };

  const INDICATOR_COLORS = ['#2962FF', '#089981', '#F23645', '#FF9800', '#9B59B6'];
  let colorIndex = 0;
  let indicatorSearchQuery = '';

  function nextColor() { return INDICATOR_COLORS[colorIndex++ % INDICATOR_COLORS.length]; }

  function init() {
    renderIndicatorList();
    const searchInput = document.getElementById('indicator-search');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        indicatorSearchQuery = (e.target.value || '').trim().toLowerCase();
        renderIndicatorList();
      });
      searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          searchInput.value = '';
          indicatorSearchQuery = '';
          renderIndicatorList();
        }
      });
    }
    const applyBtn = document.getElementById('ind-apply-btn');
    if (applyBtn) applyBtn.onclick = applyIndicatorSettings;
  }

  function getIndicatorDef(type) {
    return Object.values(INDICATOR_DEFS).flatMap(g => g.items).find(i => i.id === type) || null;
  }

  function renderIndicatorList() {
    const container = document.getElementById('indicator-list');
    if (!container) return;
    container.innerHTML = '';
    let renderedCount = 0;

    Object.values(INDICATOR_DEFS).forEach(group => {
      const filteredItems = group.items.filter(ind => {
        if (!indicatorSearchQuery) return true;
        const haystack = `${ind.id} ${ind.name} ${ind.subType} ${group.label}`.toLowerCase();
        return haystack.includes(indicatorSearchQuery);
      });
      if (!filteredItems.length) return;

      const gDiv = document.createElement('div');
      gDiv.className = 'indicator-group';
      gDiv.innerHTML = `<div class="indicator-group-title"><i class="fas ${group.icon}"></i> ${group.label}</div>`;

      const grid = document.createElement('div');
      grid.className = 'indicator-grid';

      filteredItems.forEach(ind => {
        const item = document.createElement('div');
        item.className = 'indicator-item';
        item.innerHTML = `
          <span class="ii-name">${ind.name}<small>${ind.id.toUpperCase()}</small></span>
          <span class="ii-badge">${ind.subType.toUpperCase().slice(0, 4)}</span>
        `;
        item.onclick = () => showSettings(ind);
        grid.appendChild(item);
        renderedCount += 1;
      });
      gDiv.appendChild(grid);
      container.appendChild(gDiv);
    });

    if (!renderedCount) {
      container.innerHTML = `
        <div class="indicator-empty">
          <i class="fas fa-search"></i>
          <span>No indicators found</span>
        </div>
      `;
    }
  }

  let pendingInd = null;
  let pendingEditId = null;

  function showSettings(ind, editId = null) {
    pendingInd = ind;
    pendingEditId = editId;
    const dialog = document.getElementById('ind-settings-dialog');
    const title = document.getElementById('ind-settings-title');
    const container = document.getElementById('ind-settings-container');
    const existing = editId ? activeIndicators[editId] : null;

    if (title) title.textContent = existing ? `Edit ${ind.name}` : ind.name;
    
    // Clear and build dynamic inputs
    container.innerHTML = '';
    
    const params = getParamsForIndicator(ind.id, existing?.params);
    
    Object.entries(params).forEach(([key, val]) => {
        const group = document.createElement('div');
        group.style.marginBottom = '15px';
        
        const label = document.createElement('label');
        label.style.display = 'block';
        label.style.fontSize = '11px';
        label.style.color = 'var(--text-muted)';
        label.style.textTransform = 'uppercase';
        label.style.marginBottom = '6px';
        label.textContent = key.replace(/_/g, ' ');
        
        const input = document.createElement('input');
        input.type = typeof val === 'number' ? 'number' : 'text';
        input.dataset.key = key;
        input.value = val;
        input.className = 'ind-param-input';
        input.style.cssText = 'width:100%; height:34px; background:var(--bg-primary); border:1px solid var(--border-color); color:white; border-radius:4px; padding:0 10px; outline:none; font-size:13px;';
        
        group.appendChild(label);
        group.appendChild(input);
        container.appendChild(group);
    });

    if (dialog) dialog.style.display = 'block';
  }

  function getParamsForIndicator(type, saved = null) {
      if (type === 'stochrsi') {
          return saved || { rsi_period: 14, stoch_period: 14, k_smooth: 3, d_smooth: 3 };
      }
      if (type === 'stoch') {
          return saved || { k_period: 14, smooth: 3, d_period: 3 };
      }
      if (type === 'macd') {
          return saved || { fast: 12, slow: 26, sig: 9 };
      }
      if (type === 'bollinger') {
          return saved || { period: 20, std_dev: 2 };
      }
      return saved || { period: getIndicatorDef(type)?.defaultPeriod || 20 };
  }

  function showSettingsForActive(id) {
    const saved = activeIndicators[id];
    if (!saved) return;
    const def = getIndicatorDef(saved.type);
    if (def) showSettings(def, id);
  }

  async function applyIndicatorSettings() {
    if (!pendingInd || !currentSymbol) return;
    
    const inputs = document.querySelectorAll('.ind-param-input');
    const params = {};
    inputs.forEach(input => {
        const val = input.value;
        params[input.dataset.key] = input.type === 'number' ? parseFloat(val) : val;
    });

    const id = pendingEditId || `${pendingInd.id}_${Date.now()}`;

    try {
      const loading = document.getElementById('chart-loading');
      if (loading) loading.classList.add('active');

      await fetchAndRenderIndicator(pendingInd, params, id);
      activeIndicators[id] = { id, name: pendingInd.name, type: pendingInd.id, params };
      renderActiveIndicators();
      document.getElementById('ind-settings-dialog').style.display = 'none';
      pendingEditId = null;
    } catch (e) {
      console.error(e);
      window.showError?.(e.message || 'Indicator failed');
    } finally {
      document.getElementById('chart-loading')?.classList.remove('active');
    }
  }

  async function fetchAndRenderIndicator(ind, params, id) {
    if (ChartModule && ChartModule.removeIndicatorSeries) {
      ChartModule.removeIndicatorSeries(id, true);
    }

    const candles = ChartModule?.getCurrentData ? ChartModule.getCurrentData() : [];
    if (!candles.length) throw new Error('No chart candles available for indicator calculation');

    const firstCandleTime = candles[0]?.time;
    const lastCandleTime = candles[candles.length - 1]?.time;
    console.log(`[${ind.id}] using chart candles`, {
      firstCandleTime,
      lastCandleTime,
      candleCount: candles.length,
    });

    const endpoint = ind.id === 'sr_levels' ? '/api/sr_levels' : '/api/indicator';
    const payload = ind.id === 'sr_levels'
      ? { candles, interval: currentInterval, params }
      : { indicator: ind.id, candles, params };
    const data = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).then(r => r.json());
    if (!data || data.error || data.status === 'error') throw new Error(data?.error || data?.message || `Failed to load ${ind.name}`);

    const resData = data.data || data;
    if (ind.id === 'stochrsi') {
      const firstStochRsiTime = resData?.k?.[0]?.time;
      const lastStochRsiTime = resData?.k?.[resData.k.length - 1]?.time;
      console.log('[Stoch RSI] time range', {
        firstCandleTime,
        lastCandleTime,
        firstStochRsiTime,
        lastStochRsiTime,
      });
    }
    const color = nextColor();

    if (ind.id === 'sr_levels') ChartModule.addSRLevelsSeries(id, ind.name, resData);
    else if (ind.id === 'macd') ChartModule.addMACDSeries(id, ind.name, resData, params);
    else if (ind.id === 'bollinger') ChartModule.addBollingerSeries(id, ind.name, resData, params);
    else if (ind.id === 'keltner' || ind.id === 'donchian') ChartModule.addMultiSeries(id, ind.name, resData, ind.id, params);
    else if (ind.id === 'stoch' || ind.id === 'stochrsi') ChartModule.addStochSeries(id, ind.name, resData, ind.id === 'stochrsi' ? 'Stoch RSI' : 'Stoch', params);
    else if (ind.id === 'pivotpoints') ChartModule.addPivotPointsSeries(id, ind.name, resData);
    else if (ind.id === 'supertrend') ChartModule.addSuperTrendSeries(id, ind.name, resData);
    else if (ind.id === 'psar') ChartModule.addPSARSeries(id, ind.name, resData);
    else if (ind.id === 'ichimoku') ChartModule.addIchimokuSeries(id, ind.name, resData);
    else if (ind.id === 'adx') ChartModule.addADXSeries(id, ind.name, resData);
    else if (ind.id === 'ao') ChartModule.addAOSeries(id, ind.name, resData);
    else ChartModule.addIndicatorSeries(id, ind.name, ind.subType, resData, { color, ...params, indicatorId: ind.id });
  }

  function renderActiveIndicators() {
    const container = document.getElementById('active-indicators-list');
    if (!container) return;
    container.innerHTML = Object.values(activeIndicators).map(ind => `
      <div class="active-indicator">
        <span>&bull; ${ind.name}</span>
        <span class="active-indicator-actions">
          <button type="button" title="Settings" onclick="Indicators.showSettingsForActive('${ind.id}')"><i class="fas fa-cog"></i></button>
          <button type="button" title="Remove" onclick="Indicators.remove('${ind.id}')">&times;</button>
        </span>
      </div>
    `).join('');
  }

  function remove(id) {
    if (activeIndicators[id]) ChartModule.removeIndicatorSeries(id);
    delete activeIndicators[id];
    renderActiveIndicators();
  }

  function removeActiveIndicator(id) {
    delete activeIndicators[id];
    renderActiveIndicators();
  }

  async function refreshActiveIndicators() {
    const entries = Object.entries(activeIndicators);
    if (!entries.length || !currentSymbol) return;
    const loading = document.getElementById('chart-loading');
    if (loading) loading.classList.add('active');

    // Pre-remove all indicator series so the chart is clean before parallel fetch
    entries.forEach(([id]) => {
      if (ChartModule && ChartModule.removeIndicatorSeries) {
        ChartModule.removeIndicatorSeries(id, true);
      }
    });

    try {
      // ⚡ Fetch + render all indicators IN PARALLEL (was sequential for-await)
      await Promise.all(
        entries.map(([id, saved]) => {
          const def = getIndicatorDef(saved.type);
          if (!def) return Promise.resolve();
          return fetchAndRenderIndicator(def, saved.params || { period: saved.period }, id)
            .catch(e => console.warn(`[Indicators] ${saved.type} refresh failed:`, e.message));
        })
      );
      renderActiveIndicators();
    } catch (e) {
      console.error('Indicator refresh failed:', e);
      window.showError?.(e.message || 'Failed to refresh indicators');
    } finally {
      if (loading) loading.classList.remove('active');
    }
  }

  return {
    init,
    setSymbol: s => currentSymbol = s,
    setInterval: i => currentInterval = i,
    remove,
    removeActiveIndicator,
    showSettings,
    showSettingsForActive,
    refreshActiveIndicators,
    INDICATOR_DEFS,
  };
})();
