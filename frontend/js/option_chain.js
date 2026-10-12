/**
 * Option Chain Module
 * Live NSE option chain with persistent updates and terminal-style cell patching.
 */
const OptionChain = (() => {
  const INDEX_SYMBOLS = [
    { symbol: 'NIFTY', label: 'Nifty 50' },
    { symbol: 'BANKNIFTY', label: 'Bank Nifty' },
    { symbol: 'FINNIFTY', label: 'Fin Nifty' },
    { symbol: 'MIDCPNIFTY', label: 'Midcap Nifty' },
    { symbol: 'NIFTYNXT50', label: 'Nifty Next 50' },
    { symbol: 'SENSEX', label: 'Sensex (BSE)' },
  ];

  const CELL_FIELDS = [
    { id: 'call-oi', side: 'call', field: 'call.oi', type: 'oi', label: 'OI' },
    { id: 'call-oi-change', side: 'call', field: 'call.oi_change', type: 'oiChange', label: 'OI Chg' },
    { id: 'call-volume', side: 'call', field: 'call.volume', type: 'volume', label: 'Vol' },
    { id: 'call-ltp', side: 'call', field: 'call.ltp', type: 'price', label: 'LTP' },
    { id: 'strike', side: 'center', field: 'strike', type: 'strike', label: 'Strike', required: true },
    { id: 'put-ltp', side: 'put', field: 'put.ltp', type: 'price', label: 'LTP' },
    { id: 'put-volume', side: 'put', field: 'put.volume', type: 'volume', label: 'Vol' },
    { id: 'put-oi-change', side: 'put', field: 'put.oi_change', type: 'oiChange', label: 'OI Chg' },
    { id: 'put-oi', side: 'put', field: 'put.oi', type: 'oi', label: 'OI' },
  ];
  const COLUMN_PREF_KEY = 'fa_option_chain_columns';
  const COMPACT_COLUMN_IDS = ['call-oi', 'call-ltp', 'strike', 'put-ltp', 'put-oi'];

  let currentSymbol = 'NIFTY';
  let currentExpiry = null;
  let currentStrikes = 50;
  let streamSource = null;
  let pollTimer = null;
  let pollInFlight = false;
  let reconnectTimer = null;
  let lastData = null;
  let autoRefresh = true;
  let filterMode = 'all';
  let histOIChart = null;
  let onSymbolSelect = null;
  let stockSearchTimeout = null;
  let stockSearchResults = [];
  let rowCache = new Map();
  let renderedKeys = new Set();
  let hasStarted = false;
  let reconnectAttempt = 0;
  let statusText = 'Connecting';
  let visibleColumns = loadColumnPrefs();
  const LIVE_REFRESH_MS = 2000;

  function init(symbolSelectCallback) {
    onSymbolSelect = symbolSelectCallback;
    buildPanel();
    start();
  }

  function start() {
    if (hasStarted) return;
    hasStarted = true;
    loadChain(currentSymbol, currentExpiry);
  }

  function buildPanel() {
    const view = document.getElementById('optionchain-view');
    if (!view) return;

    // Reset caches and load preferences
    rowCache.clear();
    renderedKeys.clear();
    lastData = null;
    visibleColumns = loadColumnPrefs();

    view.innerHTML = `
      <div class="oc-panel-header" id="oc-panel-header">
        <div class="oc-header-sticky">
          <div class="oc-header-left">
            <button class="btn-icon btn-close-panel" title="Close" onclick="App.closeInspector()">
              <i class="fas fa-arrow-left"></i>
            </button>
            <div class="oc-symbol-info">
              <span class="oc-current-symbol" id="oc-sticky-symbol">${currentSymbol}</span>
              <span class="oc-current-price" id="oc-sticky-price">-</span>
              <span class="oc-current-change" id="oc-sticky-change"></span>
            </div>
          </div>
          <div class="oc-header-right">
            <button class="btn-icon" id="oc-settings-trigger" title="Column Settings">
              <i class="fas fa-cog"></i>
            </button>
            <div class="oc-live-badge" id="oc-live-badge">
              <span class="oc-live-dot"></span><span class="oc-live-label">CONNECTING</span>
            </div>
          </div>
        </div>

        <div class="oc-collapsible-content" id="oc-collapsible-content">
          <div class="oc-controls">
            <div class="oc-control-group">
              <label>Index / Stock</label>
              <div class="oc-symbol-row">
                <select id="oc-symbol-select">
                  ${INDEX_SYMBOLS.map(i => `<option value="${i.symbol}">${i.label}</option>`).join('')}
                </select>
                <div class="oc-stock-search">
                  <input type="text" id="oc-stock-input" placeholder="Search NSE stock" class="oc-stock-input" autocomplete="off" />
                  <div id="oc-stock-results" class="oc-stock-results"></div>
                </div>
              </div>
            </div>

            <div class="oc-control-group">
              <label>Expiry</label>
              <select id="oc-expiry-select"><option>Loading...</option></select>
            </div>

            <div class="oc-control-group">
              <label>Strikes <span id="oc-strikes-val">+/-${currentStrikes}</span></label>
              <input type="range" id="oc-strikes-range" min="5" max="50" step="5" value="${currentStrikes}" />
            </div>
          </div>

          <div class="oc-stats-bar" id="oc-stats-bar">
            <div class="oc-stat"><span class="oc-stat-label">Spot</span><span class="oc-stat-value" id="oc-spot">-</span></div>
            <div class="oc-stat"><span class="oc-stat-label">ATM</span><span class="oc-stat-value" id="oc-atm">-</span></div>
            <div class="oc-stat"><span class="oc-stat-label">PCR</span><span class="oc-stat-value" id="oc-pcr">-</span><span class="oc-pcr-badge" id="oc-pcr-badge">Neutral</span></div>
            <div class="oc-stat"><span class="oc-stat-label">Max Pain</span><span class="oc-stat-value" id="oc-maxpain">-</span></div>
            <div class="oc-stat"><span class="oc-stat-label">Call OI</span><span class="oc-stat-value oc-call" id="oc-total-call-oi">-</span></div>
            <div class="oc-stat"><span class="oc-stat-label">Put OI</span><span class="oc-stat-value oc-put" id="oc-total-put-oi">-</span></div>
          </div>

          <div class="oc-tab-row">
            <div class="oc-filter-tabs">
              <button class="oc-tab active" data-filter="all" onclick="OptionChain.setFilter('all')">All</button>
              <button class="oc-tab" data-filter="itm" onclick="OptionChain.setFilter('itm')">ITM</button>
              <button class="oc-tab" data-filter="otm" onclick="OptionChain.setFilter('otm')">OTM</button>
              <button class="oc-tab" data-filter="atm" onclick="OptionChain.setFilter('atm')">ATM +/-5</button>
            </div>
            <div class="oc-connection-line">
              <span id="oc-connection-status">Connecting</span>
              <span id="oc-timestamp">Last update: -</span>
            </div>
          </div>
        </div>
      </div>

      <div class="oc-settings-overlay" id="oc-settings-overlay">
        <div class="oc-settings-panel">
          <div class="oc-settings-header">
            <h4>Column Settings</h4>
            <button class="btn-icon" onclick="OptionChain.toggleSettings(false)"><i class="fas fa-times"></i></button>
          </div>
          <div class="oc-column-toolbar" id="oc-column-toolbar">
            <div class="oc-column-presets">
              <button type="button" class="oc-preset-btn" data-column-preset="all">All</button>
              <button type="button" class="oc-preset-btn" data-column-preset="compact">Compact</button>
              <button type="button" class="oc-preset-btn" data-column-preset="reset">Reset</button>
            </div>
            <div class="oc-column-chips">
              ${renderColumnToggleButtons()}
            </div>
          </div>
        </div>
      </div>

      <div class="oc-loading" id="oc-loading">
        <div class="oc-spinner"></div>
        <span>Fetching live option chain...</span>
      </div>

      <div class="oc-error" id="oc-error" style="display:none">
        <i class="fas fa-exclamation-triangle"></i>
        <span id="oc-error-msg">Connection issue. Showing previous data.</span>
        <button onclick="OptionChain.refresh()">Reconnect</button>
      </div>

      <div class="oc-table-wrap" id="oc-table-wrap" style="display:none">
        <table class="oc-table" id="oc-table">
          ${renderTableHeader()}
          <tbody id="oc-tbody"></tbody>
        </table>
      </div>

      <div class="oc-footer" id="oc-footer" style="display:none">
        <span><i class="fas fa-signal"></i> Streaming while this app is open</span>
        <button class="oc-refresh-btn" onclick="OptionChain.refresh()">
          <i class="fas fa-sync-alt"></i> Reconnect
        </button>
      </div>
    `;

    bindControls();
    document.getElementById('oc-settings-trigger')?.addEventListener('click', () => toggleSettings(true));
    applyColumnVisibility(); // Apply to the newly built header
    initScrollBehavior();
  }

  function toggleSettings(show) {
    const overlay = document.getElementById('oc-settings-overlay');
    if (overlay) overlay.classList.toggle('active', show);
  }

  function initScrollBehavior() {
    const tableWrap = document.getElementById('oc-table-wrap');
    const panelHeader = document.getElementById('oc-panel-header');
    if (!tableWrap || !panelHeader) return;

    let lastScrollTop = 0;
    tableWrap.addEventListener('scroll', () => {
      const st = tableWrap.scrollTop;
      if (st > 30) {
        panelHeader.classList.add('collapsed');
      } else if (st < 10) {
        panelHeader.classList.remove('collapsed');
      }
      lastScrollTop = st;
    }, { passive: true });
  }

  function bindControls() {
    document.getElementById('oc-symbol-select')?.addEventListener('change', e => selectSymbol(e.target.value));

    const stockInput = document.getElementById('oc-stock-input');
    stockInput?.addEventListener('input', onStockSearchInput);
    stockInput?.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        const val = normalizeOptionSymbol(e.target.value);
        if (val) loadChain(val, null);
        hideStockResults();
      } else if (e.key === 'Escape') {
        hideStockResults();
      }
    });

    document.addEventListener('click', e => {
      const wrap = document.querySelector('.oc-stock-search');
      if (wrap && !wrap.contains(e.target)) hideStockResults();
    });

    document.getElementById('oc-expiry-select')?.addEventListener('change', e => {
      currentExpiry = e.target.value;
      loadChain(currentSymbol, currentExpiry);
    });

    const strikesRange = document.getElementById('oc-strikes-range');
    strikesRange?.addEventListener('input', e => {
      currentStrikes = parseInt(e.target.value, 10);
      document.getElementById('oc-strikes-val').textContent = `+/-${currentStrikes}`;
    });
    strikesRange?.addEventListener('change', () => loadChain(currentSymbol, currentExpiry));
    bindColumnControls();
  }

  // ⚡ Client-side expiry cache: {symbol: {ts, expiries[]}}
  const _expiryCache = new Map();
  const _EXPIRY_CACHE_MS = 5 * 60 * 1000; // 5 minutes

  async function loadChain(symbol, expiry) {
    const nextSymbol = normalizeOptionSymbol(symbol || currentSymbol);
    const symbolChanged = nextSymbol !== currentSymbol;
    currentSymbol = nextSymbol;
    currentExpiry = expiry || null;
    reconnectAttempt = 0;
    if (symbolChanged) {
      lastData = null;
      rowCache.clear();
      renderedKeys.clear();
      const tbody = document.getElementById('oc-tbody');
      if (tbody) tbody.innerHTML = '';
    }
    syncSymbolControls(currentSymbol);
    setLoading(!lastData);
    clearReconnect();
    stopPolling(false);
    stopStream(false);
    setConnection('connecting', 'Connecting');

    try {
      if (!currentExpiry) {
        // ⚡ Check client-side expiry cache first
        const cached = _expiryCache.get(currentSymbol);
        if (cached && Date.now() - cached.ts < _EXPIRY_CACHE_MS) {
          populateExpiries(cached.expiries);
          currentExpiry = cached.expiries[0] || null;
        }

        // ⚡ Fetch expiries + snapshot in PARALLEL (was sequential await-then-await)
        const expiryPromise = currentExpiry
          ? Promise.resolve(null)  // already have expiry from cache, skip fetch
          : fetch(`/api/option-chain/expiries?symbol=${encodeURIComponent(currentSymbol)}`, { cache: 'no-store' })
              .then(r => r.json())
              .catch(err => { console.warn('Failed to load expiries:', err); return null; });

        // Start snapshot fetch right away using whatever expiry we have (may be null → backend picks first)
        const snapshotPromise = fetchSnapshot();

        const [expiryData, data] = await Promise.all([expiryPromise, snapshotPromise]);

        if (expiryData && expiryData.expiry_dates) {
          populateExpiries(expiryData.expiry_dates);
          currentExpiry = currentExpiry || expiryData.expiry_dates[0] || null;
          // Store in client cache
          _expiryCache.set(currentSymbol, { ts: Date.now(), expiries: expiryData.expiry_dates });
        }

        patchChain(data);
      } else {
        // Expiry already known, just fetch snapshot
        const data = await fetchSnapshot();
        patchChain(data);
      }

      if (currentSymbol === 'SENSEX') {
        console.log('[OC] Starting BSE Sensex option polling');
        startPolling();
      } else {
        console.log('[OC] Starting NSE option stream for:', currentSymbol);
        startStream();
      }
    } catch (e) {
      handleFailure('Network error: ' + e.message);
      scheduleReconnect();
    }
  }

  // Chart symbol (Yahoo-style) for an option-chain symbol.
  const CHART_INDEX_MAP = { NIFTY: '^NSEI', BANKNIFTY: '^NSEBANK', FINNIFTY: '^CNXFIN', SENSEX: '^BSESN' };
  function toChartSymbol(symbol) {
    if (CHART_INDEX_MAP[symbol]) return CHART_INDEX_MAP[symbol];
    if (INDEX_SYMBOLS.some(i => i.symbol === symbol)) return null; // no known chart symbol
    return `${symbol}.NS`;
  }

  // User picked a symbol in the option chain: open it in the chart too.
  // The chart's loadSymbol() calls OptionChain.onSymbolChange(), which loads
  // the chain, so we only call loadChain directly when there is no chart link.
  function selectSymbol(symbol) {
    const next = normalizeOptionSymbol(symbol);
    const chartSymbol = toChartSymbol(next);
    if (typeof onSymbolSelect === 'function' && chartSymbol) {
      onSymbolSelect({ symbol: chartSymbol, name: next });
      // Safety net: if the chart did not route back (e.g. non-Indian guard), load directly.
      setTimeout(() => { if (currentSymbol !== next) loadChain(next, null); }, 0);
    } else {
      loadChain(next, null);
    }
  }

  async function fetchSnapshot() {
    const start = Date.now();
    const url = `/api/option-chain?symbol=${encodeURIComponent(currentSymbol)}`
      + `&expiry=${encodeURIComponent(currentExpiry || '')}`
      + `&strikes=${currentStrikes}`
      + `&refresh=1`
      + `&_=${Date.now()}`;
    const resp = await fetch(url, { cache: 'no-store' });
    const text = await resp.text();
    try {
      const data = JSON.parse(text);
      const elapsed = Date.now() - start;
      if (resp.ok && !data.error) {
        console.log(`[OC] FETCH: ${currentSymbol} | Network: ${elapsed}ms | ServerAge: ${Math.round(Date.now()/1000 - data.timestamp)}s | Spot: ${data.spot}`);
      }
      if (!resp.ok || data.error) throw new Error(data.error || `HTTP ${resp.status}`);
      return data;
    } catch(e) {
      console.error('[OC] Raw server response:', text.substring(0, 300));
      throw new Error('Server returned non-JSON: ' + text.substring(0, 100));
    }
  }

  function patchChain(data) {
    if (!data || data.error) {
      handleFailure(data?.error || 'Option chain feed error');
      return;
    }
    console.log(`[OC] PATCH: Spot ${data.spot} at ${new Date().toLocaleTimeString()}.${new Date().getMilliseconds()}`);
    
    if (data.unavailable) {
      handleUnavailable(data.message || 'NSE option-chain data is temporarily unavailable. Retrying...');
      return;
    }

    const previousData = lastData;
    lastData = data;

    // Force visibility of the table wrap and footer
    const wrap = document.getElementById('oc-table-wrap');
    const footer = document.getElementById('oc-footer');
    if (wrap) {
      wrap.style.display = 'block';
      wrap.style.visibility = 'visible';
    }
    if (footer) footer.style.display = 'flex';

    setLoading(false);
    updateStats(data);
    updateTimestamp(data.timestamp);
    setConnection('live', 'Live');
    
    // Select strikes and patch the rows
    const visibleStrikes = selectVisibleStrikes(data);
    patchRows(visibleStrikes, previousData);
    
    flashLiveBadge();

    // Update sticky header elements
    const stickySymbol = document.getElementById('oc-sticky-symbol');
    const stickyPrice = document.getElementById('oc-sticky-price');
    if (stickySymbol) stickySymbol.textContent = currentSymbol;
    if (stickyPrice) stickyPrice.textContent = fmtNum(data.spot);
  }

  function updateStats(data) {
    if (!data) return;

    setText('oc-spot', fmtNum(data.spot));
    setText('oc-sticky-price', fmtNum(data.spot));

    const change = data.spot_change || 0;
    const pChange = data.spot_pchange || 0;
    const sign = change >= 0 ? '+' : '';
    const changeText = `${sign}${fmtNum(change)} (${sign}${pChange.toFixed(2)}%)`;
    
    const changeEl = document.getElementById('oc-sticky-change');
    if (changeEl) {
      changeEl.textContent = changeText;
      changeEl.className = `oc-current-change ${change >= 0 ? 'positive' : 'negative'}`;
    }
    const priceEl = document.getElementById('oc-sticky-price');
    if (priceEl) {
      priceEl.className = `oc-current-price ${change >= 0 ? 'positive' : 'negative'}`;
    }

    setText('oc-atm', data.atm_strike ? fmtNum(data.atm_strike) : '-');
    setText('oc-pcr', data.pcr ? Number(data.pcr).toFixed(2) : '-');
    setText('oc-maxpain', data.max_pain ? fmtNum(data.max_pain) : '-');
    setText('oc-total-call-oi', data.total_call_oi ? fmtOI(data.total_call_oi, false) : '-');
    setText('oc-total-put-oi', data.total_put_oi ? fmtOI(data.total_put_oi, false) : '-');

    const pcrEl = document.getElementById('oc-pcr');
    const pcrBadge = document.getElementById('oc-pcr-badge');
    if ((pcrEl || pcrBadge) && data.pcr !== undefined) {
      const pcrClass = data.pcr > 1.2 ? 'oc-bullish' : data.pcr < 0.8 ? 'oc-bearish' : 'oc-neutral';
      const pcrText = data.pcr > 1.2 ? 'Bullish Bias' : data.pcr < 0.8 ? 'Bearish Bias' : 'Neutral';
      if (pcrEl) pcrEl.className = 'oc-stat-value ' + (pcrClass === 'oc-neutral' ? '' : pcrClass);
      if (pcrBadge) {
        pcrBadge.textContent = pcrText;
        pcrBadge.className = `oc-pcr-badge ${pcrClass}`;
      }
    }
  }

  function selectVisibleStrikes(data) {
    let strikes = Array.isArray(data.strikes) ? data.strikes.slice() : [];
    if (filterMode === 'itm') {
      strikes = strikes.filter(s => Number(s.strike) < Number(data.spot));
    } else if (filterMode === 'otm') {
      strikes = strikes.filter(s => Number(s.strike) > Number(data.spot));
    } else if (filterMode === 'atm') {
      const atmIdx = Math.max(0, strikes.findIndex(s => s.is_atm));
      strikes = strikes.slice(Math.max(0, atmIdx - 5), Math.min(strikes.length, atmIdx + 6));
    }
    return strikes;
  }

  function patchRows(strikes, previousData) {
    const tbody = document.getElementById('oc-tbody');
    if (!tbody) return;

    if (!strikes || strikes.length === 0) {
      if (currentSymbol === 'SENSEX') {
         tbody.innerHTML = `<tr><td colspan="3" style="padding:40px; text-align:center; color:var(--text-muted); font-size:12px;">${lastData?.message || 'Data unavailable'}</td></tr>`;
      } else {
         tbody.innerHTML = `<tr><td colspan="3" style="padding:40px; text-align:center; color:var(--text-muted); font-size:12px;">No strike data found.</td></tr>`;
      }
      renderedKeys = new Set();
      return;
    }

    const spot = Number(lastData?.spot || 0);
    const spotChange = Number(lastData?.spot_change || 0);
    const spotPChange = Number(lastData?.spot_pchange || 0);

    // If strikes set changed significantly (different expiry or count), full re-render
    const currentKeys = new Set(strikes.map(s => String(s.strike)));
    if (!sameKeySet(renderedKeys, currentKeys)) {
      fullRender(strikes, spot, spotChange, spotPChange);
      return;
    }

    // Surgical updates
    const prevStrikes = previousData?.strikes || [];
    const prevMap = new Map(prevStrikes.map(s => [s.strike, s]));

    // Update Spot Row
    const spotEl = tbody.querySelector('.oc-spot-indicator');
    if (spotEl) {
      const sign = spotChange >= 0 ? '+' : '';
      spotEl.innerHTML = `SPOT: ₹${fmtNum(spot)} <span class="${spotChange >= 0 ? 'positive' : 'negative'}">${sign}${fmtNum(spotChange)} (${sign}${spotPChange.toFixed(2)}%)</span>`;
    }

    // Re-calculate max OI for bar scaling updates
    let maxCallOI = 0; 
    let maxPutOI = 0;
    strikes.forEach(s => {
      maxCallOI = Math.max(maxCallOI, Number(s.call?.oi || 0));
      maxPutOI = Math.max(maxPutOI, Number(s.put?.oi || 0));
    });
    maxCallOI = Math.max(maxCallOI, 1);
    maxPutOI = Math.max(maxPutOI, 1);

    strikes.forEach(s => {
      const row = tbody.querySelector(`tr[data-strike="${s.strike}"]`);
      if (!row) return;

      const prev = prevMap.get(s.strike);
      
      // Update Call side
      updateSideCell(row.querySelector('.oc-call-side'), s.call, prev?.call, 'call', maxCallOI);
      // Update Put side
      updateSideCell(row.querySelector('.oc-put-side'), s.put, prev?.put, 'put', maxPutOI);
    });
  }

  function fullRender(strikes, spot, spotChange, spotPChange) {
    const tbody = document.getElementById('oc-tbody');
    const fragment = document.createDocumentFragment();

    if (!strikes || strikes.length === 0) {
        if (currentSymbol === 'SENSEX') {
            tbody.innerHTML = `<tr><td colspan="3" style="padding:40px; text-align:center; color:var(--text-muted); font-size:12px;">${lastData?.message || 'Data unavailable'}</td></tr>`;
        } else {
            tbody.innerHTML = `<tr><td colspan="3" style="padding:40px; text-align:center; color:var(--text-muted); font-size:12px;">No strike data found.</td></tr>`;
        }
        renderedKeys = new Set();
        return;
    }

    let maxCallOI = 0, maxPutOI = 0;
    strikes.forEach(s => {
      maxCallOI = Math.max(maxCallOI, Number(s.call?.oi || 0));
      maxPutOI = Math.max(maxPutOI, Number(s.put?.oi || 0));
    });
    maxCallOI = Math.max(maxCallOI, 1);
    maxPutOI = Math.max(maxPutOI, 1);

    let spotInserted = false;
    strikes.forEach((s) => {
      if (!spotInserted && Number(s.strike) > spot) {
        fragment.appendChild(createSpotRow(spot, spotChange, spotPChange));
        spotInserted = true;
      }
      fragment.appendChild(createGrowwRow(s, spot, maxCallOI, maxPutOI));
    });
    if (!spotInserted) fragment.appendChild(createSpotRow(spot, spotChange, spotPChange));

    tbody.innerHTML = '';
    tbody.appendChild(fragment);
    renderedKeys = new Set(strikes.map(s => String(s.strike)));
  }

  function updateSideCell(cell, current, prev, side, maxOI) {
    if (!cell || !current) return;
    
    const price = displayPremium(current);
    const prevPrice = displayPremium(prev);
    const oi = Number(current.oi || 0);
    const prevOI = Number(prev?.oi || 0);
    const oiChg = Number(current.oi_change || 0);
    const chg = optionPercentChange(current);

    // Update Price and Flash
    const priceEl = cell.querySelector('.oc-cell-price');
    if (priceEl) {
      const formatted = `₹${price.toFixed(2)}`;
      if (priceEl.textContent !== formatted) {
        const diff = price - prevPrice;
        priceEl.textContent = formatted;
        if (prevPrice > 0 && Math.abs(diff) > 0.001) flashCell(priceEl, diff, 'price');
      }
    }

    // Update PChg
    const chgEl = cell.querySelector('.oc-cell-change');
    if (chgEl) {
      chgEl.textContent = `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%`;
      chgEl.className = `oc-cell-change ${chg >= 0 ? 'positive' : 'negative'}`;
    }

    // Update OI info
    const oiValEl = cell.querySelector('.oc-oi-val');
    if (oiValEl) {
      const formatted = fmtOI(oi, false);
      if (oiValEl.textContent !== formatted) {
        const diff = oi - prevOI;
        oiValEl.textContent = formatted;
        if (prevOI > 0 && diff !== 0) flashCell(oiValEl, diff, 'oi');
      }
    }

    const oiChgEl = cell.querySelector('.oc-oi-change');
    if (oiChgEl) {
      oiChgEl.textContent = fmtOI(oiChg, true);
      oiChgEl.className = `oc-oi-change ${oiChg >= 0 ? 'positive' : 'negative'}`;
    }

    // Update OI Bar
    const bar = cell.querySelector(`.oc-oi-bar-${side}`);
    if (bar) {
      bar.style.width = `${(oi / maxOI) * 100}%`;
    }
  }

  function createGrowwRow(s, spot, maxCallOI, maxPutOI) {
    const tr = document.createElement('tr');
    tr.className = 'oc-row';
    tr.dataset.strike = s.strike;
    if (s.is_atm) tr.classList.add('oc-atm-row');

    const c = s.call || {};
    const p = s.put || {};
    
    const callPrice = displayPremium(c);
    const putPrice = displayPremium(p);
    const callChg = optionPercentChange(c);
    const putChg = optionPercentChange(p);
    const callOI = Number(c.oi || 0);
    const putOI = Number(p.oi || 0);
    const callOIChg = Number(c.oi_change || 0);
    const putOIChg = Number(p.oi_change || 0);

    const callOIWidth = (callOI / maxCallOI) * 100;
    const putOIWidth = (putOI / maxPutOI) * 100;

    tr.innerHTML = `
      <td class="oc-call-side ${s.strike < spot ? 'oc-itm' : ''}">
        <div class="oc-side-content">
          <div class="oc-cell-price">₹${callPrice.toFixed(2)}</div>
          <div class="oc-cell-change ${callChg >= 0 ? 'positive' : 'negative'}">
            ${callChg >= 0 ? '+' : ''}${callChg.toFixed(2)}%
          </div>
          <div class="oc-oi-info">
            <span>OI:</span> <span class="oc-oi-val">${fmtOI(callOI, false)}</span>
            <span class="oc-oi-change ${callOIChg >= 0 ? 'positive' : 'negative'}">${fmtOI(callOIChg, true)}</span>
          </div>
          <div class="oc-oi-bar-wrap">
            <div class="oc-oi-bar-call" style="width: ${callOIWidth}%"></div>
          </div>
        </div>
      </td>
      <td class="oc-center-cell">
        <div class="oc-strike-val">${fmtNum(s.strike)}</div>
      </td>
      <td class="oc-put-side ${s.strike > spot ? 'oc-itm' : ''}">
        <div class="oc-side-content">
          <div class="oc-cell-price">₹${putPrice.toFixed(2)}</div>
          <div class="oc-cell-change ${putChg >= 0 ? 'positive' : 'negative'}">
            ${putChg >= 0 ? '+' : ''}${putChg.toFixed(2)}%
          </div>
          <div class="oc-oi-info">
            <span>OI:</span> <span class="oc-oi-val">${fmtOI(putOI, false)}</span>
            <span class="oc-oi-change ${putOIChg >= 0 ? 'positive' : 'negative'}">${fmtOI(putOIChg, true)}</span>
          </div>
          <div class="oc-oi-bar-wrap">
            <div class="oc-oi-bar-put" style="width: ${putOIWidth}%"></div>
          </div>
        </div>
      </td>
    `;

    tr.onclick = () => onStrikeClick(s.strike, lastData?.expiry);
    return tr;
  }

  function createSpotRow(price, change, pChange) {
    const tr = document.createElement('tr');
    tr.className = 'oc-spot-row';
    const sign = change >= 0 ? '+' : '';
    tr.innerHTML = `
      <td colspan="3">
        <div class="oc-spot-indicator">
          SPOT: ₹${fmtNum(price)}
          <span class="${change >= 0 ? 'positive' : 'negative'}" style="text-shadow: none; color: #fff !important; opacity: 0.9;">
            ${sign}${fmtNum(change)} (${sign}${pChange.toFixed(2)}%)
          </span>
        </div>
      </td>
    `;
    return tr;
  }

  function renderTableHeader() {
    return `
      <thead>
        <tr>
          <th class="oc-call-head">CALL SIDE</th>
          <th class="oc-strike-head">STRIKE / OI</th>
          <th class="oc-put-head">PUT SIDE</th>
        </tr>
      </thead>
    `;
  }

  function renderColumnToggleButtons() {
    return CELL_FIELDS
      .filter(col => !col.required)
      .map(col => `
        <button type="button" class="oc-col-chip" data-col-toggle="${col.id}">
          <span class="oc-col-side ${col.side === 'call' ? 'oc-call' : 'oc-put'}">${col.side === 'call' ? 'C' : 'P'}</span>
          ${col.label}
        </button>
      `).join('');
  }

  function bindColumnControls() {
    document.getElementById('oc-column-toolbar')?.addEventListener('click', e => {
      const colBtn = e.target.closest('[data-col-toggle]');
      const presetBtn = e.target.closest('[data-column-preset]');
      if (colBtn) {
        toggleColumn(colBtn.dataset.colToggle);
      } else if (presetBtn) {
        setColumnPreset(presetBtn.dataset.columnPreset);
      }
    });
  }

  function loadColumnPrefs() {
    const allIds = CELL_FIELDS.map(col => col.id);
    const defaults = new Set(allIds);
    try {
      const saved = JSON.parse(localStorage.getItem(COLUMN_PREF_KEY) || '[]');
      if (Array.isArray(saved) && saved.length > 0) {
        const valid = saved.filter(id => allIds.includes(id));
        if (valid.length > 0) {
          const prefSet = new Set(valid);
          prefSet.add('strike');
          return prefSet;
        }
      }
    } catch (_) {}
    return defaults;
  }

  function saveColumnPrefs() {
    localStorage.setItem(COLUMN_PREF_KEY, JSON.stringify([...visibleColumns]));
  }

  function toggleColumn(id) {
    if (id === 'strike') return;
    if (visibleColumns.has(id)) {
      visibleColumns.delete(id);
    } else {
      visibleColumns.add(id);
    }
    visibleColumns.add('strike');
    saveColumnPrefs();
    applyColumnVisibility();
  }

  function setColumnPreset(preset) {
    if (preset === 'reset') {
      localStorage.removeItem(COLUMN_PREF_KEY);
      visibleColumns = new Set(CELL_FIELDS.map(col => col.id));
    } else {
      visibleColumns = preset === 'compact'
        ? new Set(COMPACT_COLUMN_IDS)
        : new Set(CELL_FIELDS.map(col => col.id));
    }
    visibleColumns.add('strike');
    saveColumnPrefs();
    applyColumnVisibility();
  }

  function applyColumnVisibility(scope = document) {
    // Safety: ensure strike is always in visibleColumns
    if (!visibleColumns || visibleColumns.size === 0) {
      visibleColumns = new Set(CELL_FIELDS.map(c => c.id));
    }
    visibleColumns.add('strike');
    
    scope.querySelectorAll?.('[data-col]').forEach(el => {
      const colId = el.dataset.col;
      const hidden = !visibleColumns.has(colId);
      el.style.display = hidden ? 'none' : (el.tagName === 'TH' || el.tagName === 'TD' ? 'table-cell' : '');
    });
    
    // Update active state of column chips
    document.querySelectorAll('.oc-col-chip').forEach(btn => {
      btn.classList.toggle('active', visibleColumns.has(btn.dataset.colToggle));
    });

    // Update colspans for the side headers
    const callVisible = CELL_FIELDS.filter(col => col.side === 'call' && visibleColumns.has(col.id)).length;
    const putVisible = CELL_FIELDS.filter(col => col.side === 'put' && visibleColumns.has(col.id)).length;
    
    const callSpan = document.getElementById('oc-call-span');
    const putSpan = document.getElementById('oc-put-span');
    if (callSpan) {
      callSpan.colSpan = Math.max(callVisible, 1);
      callSpan.style.display = callVisible === 0 ? 'none' : 'table-cell';
    }
    if (putSpan) {
      putSpan.colSpan = Math.max(putVisible, 1);
      putSpan.style.display = putVisible === 0 ? 'none' : 'table-cell';
    }
  }

  function flashCell(cell, diff, type) {
    cell.classList.remove('oc-flash-up', 'oc-flash-down', 'oc-oi-pulse');
    void cell.offsetWidth;
    if (type === 'oi' || type === 'oiChange' || type === 'volume') {
      cell.classList.add('oc-oi-pulse');
    } else {
      cell.classList.add(diff >= 0 ? 'oc-flash-up' : 'oc-flash-down');
    }
    setTimeout(() => cell.classList.remove('oc-flash-up', 'oc-flash-down', 'oc-oi-pulse'), 700);
  }

  function startStream() {
    stopStream(false);
    if (!autoRefresh || !currentExpiry) return;
    if (currentSymbol === 'SENSEX') {
      startPolling();
      return;
    }

    const url = `/api/option-chain/stream?symbol=${encodeURIComponent(currentSymbol)}`
      + `&expiry=${encodeURIComponent(currentExpiry || '')}`
      + `&strikes=${currentStrikes}`;

    console.log('[SSE] Connecting to:', url);
    streamSource = new EventSource(url);
    setConnection('connecting', 'Connecting');

    streamSource.onopen = () => {
      console.log('[SSE] Connection opened');
      reconnectAttempt = 0;
      setConnection('live', 'Live');
    };

    streamSource.onmessage = e => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'option_chain' && !msg.error) {
          console.log('[LIVE]', new Date().toLocaleTimeString(), 'spot:', msg.spot);
          patchChain(msg);
        } else if (msg.type === 'heartbeat') {
          updateTimestamp(msg.ts);
        } else if (msg.type === 'market_closed') {
          setConnection('closed', 'Market closed');
        } else if (msg.type === 'error' || msg.error) {
          handleFailure(msg.message || msg.error || 'Feed error');
        }
      } catch (err) {
        console.error('[OC] Bad SSE message:', e.data ? e.data.substring(0, 200) : 'undefined');
        handleFailure('Bad feed message');
      }
    };

    streamSource.onerror = () => {
      console.warn('[SSE] Stream error, retrying stream');
      setConnection('reconnecting', 'Polling Live');
      stopStream(false);
      startPolling();
    };
  }

  function startPolling() {
    stopStream(false);
    stopPolling(false);
    if (!autoRefresh || !currentExpiry) return;

    const run = async () => {
      if (!autoRefresh || pollInFlight) return;
      pollInFlight = true;
      try {
        const data = await fetchSnapshot();
        reconnectAttempt = 0;
        patchChain(data);
      } catch (err) {
        handleFailure('Polling error: ' + err.message);
      } finally {
        pollInFlight = false;
      }
    };

    setConnection('live', 'Live');
    run();
    pollTimer = setInterval(run, LIVE_REFRESH_MS);
  }

  function stopPolling(updateStatus = true) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    pollInFlight = false;
    if (updateStatus) setConnection('closed', 'Stopped');
  }

  function scheduleReconnect() {
    if (!autoRefresh || reconnectTimer) return;
    reconnectAttempt += 1;
    const delay = Math.min(15000, 1000 * Math.max(1, reconnectAttempt));
    setConnection('reconnecting', `Reconnecting in ${Math.round(delay / 1000)}s`);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (lastData) startPolling();
      else loadChain(currentSymbol, currentExpiry);
    }, delay);
  }

  function stopStream(updateStatus = true) {
    if (streamSource) {
      streamSource.close();
      streamSource = null;
    }
    if (updateStatus) setConnection('closed', 'Stopped');
  }

  function clearReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  function handleFailure(message) {
    setLoading(false);
    setConnection('error', message || 'Connection issue');
    const error = document.getElementById('oc-error');
    const errMsg = document.getElementById('oc-error-msg');
    if (lastData) {
      if (error) error.style.display = 'flex';
      if (errMsg) errMsg.textContent = `${message}. Showing previous data.`;
      document.getElementById('oc-table-wrap').style.display = 'block';
      document.getElementById('oc-footer').style.display = 'flex';
    } else {
      if (error) {
        error.style.display = 'flex';
        error.innerHTML = `
          <i class="fas fa-exclamation-triangle"></i>
          <span id="oc-error-msg">${message || 'NSE data is currently unavailable.'}</span>
          <div style="display:flex; gap:8px; margin-top:10px;">
            <button onclick="OptionChain.refresh()">Retry</button>
            <button onclick="OptionChain.refresh()" style="background:var(--bg-elevated); color:var(--text-primary);">Reset Session</button>
          </div>
        `;
      }
    }
  }

  function handleUnavailable(message) {
    setLoading(false);
    setConnection('reconnecting', 'Retrying');
    const error = document.getElementById('oc-error');
    const errMsg = document.getElementById('oc-error-msg');
    if (error) error.style.display = 'flex';
    if (errMsg) errMsg.textContent = message;

    const wrap = document.getElementById('oc-table-wrap');
    const footer = document.getElementById('oc-footer');
    if (lastData) {
      if (wrap) wrap.style.display = 'block';
      if (footer) footer.style.display = 'flex';
      return;
    }

    if (wrap) {
      wrap.style.display = 'block';
      const tbody = document.getElementById('oc-tbody');
      if (tbody) {
        tbody.innerHTML = `
          <tr class="oc-row">
            <td colspan="3" class="oc-empty-state">${escapeHtml(message)}</td>
          </tr>
        `;
      }
    }
    if (footer) footer.style.display = 'flex';
  }

  function onStrikeClick(strike, expiry) {
    if (!lastData) return;
    const row = lastData.strikes.find(s => Number(s.strike) === Number(strike));
    if (row) showStrikeDetail(strike, expiry, row);
  }

  function showStrikeDetail(strike, expiry, row) {
    document.getElementById('oc-detail-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'oc-detail-modal';
    modal.className = 'oc-modal';
    modal.innerHTML = `
      <div class="oc-modal-content">
        <div class="oc-modal-header">
          <span>${currentSymbol} ${strike} | Expiry: ${expiry}</span>
          <div class="oc-modal-actions">
            <button onclick="OptionChain.copyStrike(${strike}, 'CE')" title="Copy Strike"><i class="fas fa-copy"></i></button>
            <button onclick="document.getElementById('oc-detail-modal').remove()" title="Close"><i class="fas fa-times"></i></button>
          </div>
        </div>
        <div class="oc-modal-body">
          <div class="oc-greeks-grid">
            <div class="oc-greeks-col">
              <h4 class="oc-call-head">CALL</h4>
              ${detailTable(row.call)}
              <button class="oc-trade-btn oc-buy-btn" onclick="OptionChain.placeOrder('BUY','CE',${strike},'${expiry}',${row.call.ltp})">BUY CE</button>
              <button class="oc-trade-btn oc-sell-btn" onclick="OptionChain.placeOrder('SELL','CE',${strike},'${expiry}',${row.call.ltp})">SELL CE</button>
            </div>
            <div class="oc-greeks-col">
              <h4 class="oc-put-head">PUT</h4>
              ${detailTable(row.put)}
              <button class="oc-trade-btn oc-buy-btn" onclick="OptionChain.placeOrder('BUY','PE',${strike},'${expiry}',${row.put.ltp})">BUY PE</button>
              <button class="oc-trade-btn oc-sell-btn" onclick="OptionChain.placeOrder('SELL','PE',${strike},'${expiry}',${row.put.ltp})">SELL PE</button>
            </div>
          </div>
          <div id="oc-hist-oi-container">
            <h4>Historical OI</h4>
            <button onclick="OptionChain.loadHistOI(${strike},'${expiry}')" class="oc-load-hist-btn">Load Historical OI</button>
            <div id="oc-hist-oi-chart" style="height:160px; margin-top:8px;"></div>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  }

  function detailTable(side) {
    const rows = [
      ['Mark', Number(side.mark ?? side.premium ?? side.ltp ?? 0).toFixed(2)],
      ['LTP', Number(side.ltp || 0).toFixed(2)],
      ['Change', Number(side.change || 0).toFixed(2)],
      ['OI', fmtOI(side.oi, false)],
      ['OI Chg', fmtOI(side.oi_change)],
      ['Volume', fmtOI(side.volume, false)],
      ['Bid', Number(side.bid || 0).toFixed(2)],
      ['Ask', Number(side.ask || 0).toFixed(2)],
    ];
    return `<table class="oc-detail-table">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join('')}</table>`;
  }

  async function loadHistOI(strike, expiry) {
    const container = document.getElementById('oc-hist-oi-chart');
    if (!container) return;
    container.innerHTML = '<div class="oc-spinner"></div>';
    try {
      const resp = await fetch(`/api/option-chain/historical-oi?symbol=${currentSymbol}&expiry=${encodeURIComponent(expiry)}&strike=${strike}`);
      const data = await resp.json();
      if (data.status !== 'ok' || !data.data.length) {
        container.innerHTML = '<span class="oc-muted">No historical OI data</span>';
        return;
      }
      renderHistOIChart(container, data.data);
    } catch (_) {
      container.innerHTML = '<span class="oc-muted">Error loading OI history</span>';
    }
  }

  function renderHistOIChart(container, histData) {
    container.innerHTML = '';
    if (typeof LightweightCharts === 'undefined') return;
    if (histOIChart && histOIChart.remove) histOIChart.remove();
    histOIChart = LightweightCharts.createChart(container, {
      width: container.clientWidth || 400,
      height: 160,
      layout: { background: { color: '#1e222d' }, textColor: '#d1d4dc' },
      grid: { vertLines: { color: '#363c4e' }, horzLines: { color: '#363c4e' } },
      timeScale: { visible: true },
    });
    const series = histOIChart.addHistogramSeries({ color: '#2962ff', priceFormat: { type: 'volume' } });
    const chartData = histData.map(d => ({
      time: Math.floor(new Date(d.FH_TIMESTAMP || d.date || d.TIMESTAMP).getTime() / 1000),
      value: parseFloat(d.FH_OPEN_INT || d.openInterest || 0),
    })).filter(d => d.time && d.value).sort((a, b) => a.time - b.time);
    series.setData(chartData);
  }

  function placeOrder(side, optionType, strike, expiry, ltp) {
    if (typeof PaperTrade !== 'undefined' && typeof PaperTrade.openOrderModal === 'function') {
      const symbol = `${currentSymbol}${strike}${optionType}`;
      PaperTrade.openOrderModal(symbol, ltp, `${currentSymbol} ${strike} ${optionType}`, side);
    } else {
      window.showNotification(`${side} ${currentSymbol} ${strike}${optionType} @ ${ltp}`, side === 'BUY' ? 'success' : 'info');
    }
  }

  async function copyStrike(strike, optionType = 'CE') {
    const text = `${currentSymbol} ${strike} ${optionType}`;
    try {
      await navigator.clipboard.writeText(text);
      window.showNotification(`Copied ${text}`, 'success');
    } catch (_) {
      const tmp = document.createElement('textarea');
      tmp.value = text;
      document.body.appendChild(tmp);
      tmp.select();
      document.execCommand('copy');
      tmp.remove();
      window.showNotification(`Copied ${text}`, 'success');
    }
  }

  function setFilter(mode) {
    filterMode = mode;
    document.querySelectorAll('.oc-tab').forEach(t => t.classList.toggle('active', t.dataset.filter === mode));
    if (lastData) patchRows(selectVisibleStrikes(lastData), lastData);
  }

  function toggleAutoRefresh(enabled) {
    autoRefresh = enabled;
    clearReconnect();
    if (enabled) {
      reconnectAttempt = 0;
      if (lastData) {
        console.log('[OC] Auto-refresh enabled, starting NSE option stream...');
        startStream();
      } else {
        loadChain(currentSymbol, currentExpiry);
      }
    } else {
      stopPolling(false);
      stopStream(true);
    }
  }

  function refresh() {
    reconnectAttempt = 0;
    loadChain(currentSymbol, currentExpiry);
  }

  function populateExpiries(expiries) {
    const sel = document.getElementById('oc-expiry-select');
    if (!sel) return;
    sel.innerHTML = expiries.length
      ? expiries.map((e, i) => `<option value="${e}" ${i === 0 ? 'selected' : ''}>${e}</option>`).join('')
      : '<option value="">No expiries</option>';
  }

  function syncSymbolControls(symbol) {
    const select = document.getElementById('oc-symbol-select');
    const input = document.getElementById('oc-stock-input');
    if (select && INDEX_SYMBOLS.some(i => i.symbol === symbol)) select.value = symbol;
    if (input) input.value = INDEX_SYMBOLS.some(i => i.symbol === symbol) ? '' : symbol;
  }

  function normalizeOptionSymbol(symbol) {
    return (symbol || '').trim().toUpperCase().replace(/\.(NS|BO)$/i, '').replace(/^\^NSEI$/, 'NIFTY').replace(/^\^NSEBANK$/, 'BANKNIFTY');
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function onStockSearchInput(e) {
    clearTimeout(stockSearchTimeout);
    const query = e.target.value.trim();
    if (query.length < 1) {
      hideStockResults();
      return;
    }
    stockSearchTimeout = setTimeout(async () => {
      try {
        const results = await Datafeed.searchSymbols(query);
        stockSearchResults = (results || [])
          .filter(r => (r.exchange || '').toUpperCase() === 'NSE' && (r.type || 'stock') === 'stock')
          .slice(0, 8);
        renderStockResults();
      } catch (err) {
        hideStockResults();
      }
    }, 150);
  }

  function renderStockResults() {
    const box = document.getElementById('oc-stock-results');
    if (!box) return;
    if (!stockSearchResults.length) {
      box.innerHTML = '<div class="oc-stock-empty">No NSE matches</div>';
      box.classList.add('active');
      return;
    }
    box.innerHTML = stockSearchResults.map((r, idx) => `
      <button type="button" class="oc-stock-result" data-idx="${idx}">
        <span>${escapeHtml(normalizeOptionSymbol(r.symbol))}</span>
        <small>${escapeHtml(r.name || r.symbol)}</small>
      </button>
    `).join('');
    box.querySelectorAll('.oc-stock-result').forEach(btn => {
      btn.addEventListener('click', () => {
        const item = stockSearchResults[Number(btn.dataset.idx)];
        const symbol = normalizeOptionSymbol(item?.symbol);
        if (symbol) selectSymbol(symbol);
        hideStockResults();
      });
    });
    box.classList.add('active');
  }

  function hideStockResults() {
    const box = document.getElementById('oc-stock-results');
    if (box) {
      box.classList.remove('active');
      box.innerHTML = '';
    }
  }

  function setLoading(on) {
    const loading = document.getElementById('oc-loading');
    const wrap = document.getElementById('oc-table-wrap');
    const footer = document.getElementById('oc-footer');
    const error = document.getElementById('oc-error');
    if (loading) loading.style.display = on ? 'flex' : 'none';
    if (wrap) wrap.style.display = on ? 'none' : (lastData ? 'block' : 'none');
    if (footer) footer.style.display = on ? 'none' : (lastData ? 'flex' : 'none');
    if (error && !on) error.style.display = 'none';
  }

  function showError(msg) {
    setLoading(false);
    const wrap = document.getElementById('oc-table-wrap');
    const footer = document.getElementById('oc-footer');
    if (wrap) wrap.style.display = lastData ? 'block' : 'none';
    if (footer) footer.style.display = lastData ? 'flex' : 'none';
    const error = document.getElementById('oc-error');
    const errMsg = document.getElementById('oc-error-msg');
    if (error) error.style.display = 'flex';
    if (errMsg) errMsg.textContent = msg;
    if (!lastData) window.showNotification('Option chain: ' + msg, 'error');
  }

  function setConnection(state, text) {
    statusText = text;
    const status = document.getElementById('oc-connection-status');
    if (status) {
      status.textContent = text;
      status.className = `oc-conn-${state}`;
    }
    const badge = document.getElementById('oc-live-badge');
    if (badge) {
      badge.className = `oc-live-badge oc-live-${state}`;
      badge.innerHTML = `<span class="oc-live-dot"></span><span class="oc-live-label">${text.toUpperCase()}</span>`;
    }
  }

  function flashLiveBadge() {
    const badge = document.getElementById('oc-live-badge');
    if (!badge) return;
    badge.classList.add('oc-flash');
    setTimeout(() => badge.classList.remove('oc-flash'), 500);
  }

  function updateTimestamp(timestamp) {
    const ts = Number(timestamp || Date.now() / 1000);
    const el = document.getElementById('oc-timestamp');
    if (!el) return;
    const date = new Date(ts * 1000);
    el.textContent = 'Last update: ' + date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el && el.textContent !== text) el.textContent = text;
  }

  function getField(row, path) {
    return path.split('.').reduce((obj, key) => obj == null ? undefined : obj[key], row);
  }

  function formatCell(value, type, field, row) {
    if (field === 'strike') return fmtNum(row.strike);
    if (type === 'price' || type === 'change') return Number(value || 0).toFixed(2);
    if (type === 'oi' || type === 'oiChange' || type === 'volume') return fmtOI(Number(value || 0), type === 'oiChange');
    return String(value ?? '-');
  }

  function sameKeySet(a, b) {
    if (a.size !== b.size) return false;
    for (const key of a) if (!b.has(key)) return false;
    return true;
  }

  function fmtNum(n) {
    if (n == null || isNaN(n)) return '-';
    return new Intl.NumberFormat('en-IN').format(n);
  }

  function fmtMoney(n) {
    if (n == null || isNaN(n)) return '-';
    return '₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(Number(n));
  }

  function fmtOI(n, showSign = true) {
    if (n == null || isNaN(n)) return '-';
    const abs = Math.abs(Number(n));
    const sign = showSign ? (n < 0 ? '-' : (n > 0 ? '+' : '')) : (n < 0 ? '-' : '');
    if (abs >= 1e7) return sign + (abs / 1e7).toFixed(1) + 'Cr';
    if (abs >= 1e5) return sign + (abs / 1e5).toFixed(1) + 'L';
    if (abs >= 1e3) return sign + (abs / 1e3).toFixed(1) + 'K';
    return sign + abs.toString();
  }

  function optionPercentChange(side) {
    const pChange = Number(side?.pChange ?? side?.pchange ?? side?.percentChange ?? 0);
    if (Number.isFinite(pChange) && pChange !== 0) return pChange;

    const change = Number(side?.change ?? side?.priceChange ?? 0);
    const ltp = Number(side?.ltp ?? side?.lastPrice ?? 0);
    const previousClose = Number(side?.previousClose ?? side?.prevClose ?? side?.closePrice ?? 0);
    const base = previousClose || (ltp && change && ltp !== change ? ltp - change : 0);
    if (base) return (change / base) * 100;
    return 0;
  }

  function displayPremium(side) {
    return Number(side?.premium ?? side?.ltp ?? 0);
  }

  return {
    init,
    loadChain,
    refresh,
    setFilter,
    toggleAutoRefresh,
    onStrikeClick,
    placeOrder,
    loadHistOI,
    copyStrike,
    toggleSettings,
    onSymbolChange: sym => loadChain(sym, null),
    _status: () => ({ currentSymbol, currentExpiry, currentStrikes, autoRefresh, statusText }),
  };
})();
