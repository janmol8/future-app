/**
 * Main App Controller - ties together all modules.
 */
const App = (() => {
  // State
  let currentSymbol = localStorage.getItem('fa_current_symbol') || 'BTCUSDT';
  let currentName = localStorage.getItem('fa_current_name') || 'Bitcoin';
  let currentInterval = localStorage.getItem('fa_current_interval') || '1m';
  let liveStreamSource = null;
  let lastTickTime = Date.now();
  let marketSessionCache = null;
  let optionChainInitialized = false;
  const globalStockSymbols = new Set(['AAPL', 'TSLA', 'MSFT', 'GOOGL', 'NVDA', 'AMZN', 'META']);
  const logoCache = new Map();
  let watchlist = JSON.parse(localStorage.getItem('fa_watchlist')) || ['BTCUSDT', 'ETHUSDT', 'RELIANCE.NS', 'TCS.NS', '^NSEI', '^BSESN', 'GC=F'];

  // ⚡ Performance: track in-flight load so rapid timeframe clicks cancel the previous
  let _loadSymbolVersion = 0;
  let _pollingStarted = false;  // set true once startPricePolling() is actually launched

  /** Debounce helper — returns a function that delays execution by `ms` ms. */
  function _debounce(fn, ms) {
    let timer = null;
    return function (...args) {
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), ms);
    };
  }

  /**
   * Universal Toast Notification System
   */
  window.showNotification = (msg, type = 'info') => {
    const toast = document.createElement('div');
    toast.style.cssText = `
      position: fixed; bottom: 40px; right: 20px; z-index: 100000;
      padding: 12px 24px; border-radius: 12px; font-size: 13px; font-weight: 700;
      color: white; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      background: ${type === 'error' ? '#f23645' : (type === 'success' ? '#089981' : '#2962ff')};
      border: 1px solid rgba(255,255,255,0.1);
      display: flex; align-items: center; gap: 10px;
      animation: toastIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
    `;
    const icon = type === 'error' ? 'fa-triangle-exclamation' : (type === 'success' ? 'fa-check-circle' : 'fa-info-circle');
    const _iconEl = document.createElement("i");
    _iconEl.className = `fas ${icon}`;
    const _msgEl = document.createElement("span");
    _msgEl.textContent = msg;  // textContent prevents XSS
    toast.appendChild(_iconEl);
    toast.appendChild(document.createTextNode(" "));
    toast.appendChild(_msgEl);
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(20px)';
        toast.style.transition = 'all 0.4s ease';
        setTimeout(() => toast.remove(), 400);
    }, 4000);
  };
  window.showError = (msg) => window.showNotification(msg, 'error');

  async function init() {
    console.log("[App] Starting initial boot sequence...");
    
    try {
      // 1. Core Layout
      initLayoutInteractions();
      
      // 2. Chart Engine
      if (typeof ChartModule !== 'undefined') {
          const container = document.getElementById('chart-container');
          ChartModule.init(container);
          if (ChartModule.setActiveInterval) ChartModule.setActiveInterval(currentInterval);
          window.ChartModule = ChartModule;
      }
      
      // Initialize Drawing Engine
      if (typeof DrawingEngine !== 'undefined') {
          window.Drawing = new DrawingEngine('chart-container', ChartModule);
      }

      // 3. Modules (Defensive Load)
      const modules = [
          { name: 'SymbolSearch', ref: typeof SymbolSearch !== 'undefined' ? SymbolSearch : null },
          { name: 'Indicators', ref: typeof Indicators !== 'undefined' ? Indicators : null },
          { name: 'PaperTrade', ref: typeof PaperTrade !== 'undefined' ? PaperTrade : null },
          { name: 'Dashboard', ref: typeof Dashboard !== 'undefined' ? Dashboard : null },
          { name: 'LearningCenter', ref: typeof LearningCenter !== 'undefined' ? LearningCenter : null },
          { name: 'SubscriptionUI', ref: typeof SubscriptionUI !== 'undefined' ? SubscriptionUI : null }
      ];

      modules.forEach(m => {
          try {
              if (m.ref && m.ref.init) m.ref.init(onSymbolSelect);
          } catch (e) { console.error(`Module ${m.name} init failed:`, e); }
      });

      // 4. UI components
      initTimeframeButtons();
      renderWatchlist();
      initWatchlistSearch();
      if (typeof WatchlistBaskets !== 'undefined') WatchlistBaskets.init();
      initThemeToggle();
      
      // 5. Initial Data (historical chart — always OK to load)
      await loadSymbol(currentSymbol, currentName);

      // 6. Check session BEFORE starting any live polling
      updateExtendedHoursUI();  // sets badge + calls haltAllLiveActivity if needed

      // 7. Schedule the session check every 10s (cheap, no-op when halted)
      _pollingIds.push(setInterval(updateExtendedHoursUI, 10000));

      // Initialize Strategy Builder if exists
      if (typeof StrategyBuilder !== 'undefined' && StrategyBuilder.renderInsideContainer) {
          StrategyBuilder.renderInsideContainer('strategy-builder-container');
      }

    } catch (err) {
      console.error("[App] Critical boot error:", err);
    } finally {
        const ls = document.getElementById('loading-screen');
        if (ls) {
            ls.style.opacity = '0';
            setTimeout(() => { ls.style.display = 'none'; ls.classList.add('hidden'); }, 500);
        }
    }
  }

  function initLayoutInteractions() {
    const sidebar = document.getElementById('main-sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const inspector = document.getElementById('inspector-panel');

    if (sidebarToggle) {
      sidebarToggle.addEventListener('click', () => {
        sidebar.classList.toggle('expanded');
        setTimeout(() => ChartModule.resize(), 310);
      });
    }

    document.querySelectorAll('.sidebar-item[data-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        if (tab === 'chart') {
            closeInspector();
        } else {
            showInspectorView(`${tab}-view`);
            setOptionChainActive(tab === 'optionchain');
            if (tab === 'strategy' && typeof StrategyBuilder !== 'undefined') {
                StrategyBuilder.renderInsideContainer('strategy-builder-container');
            } else if (tab === 'patterns') {
                refreshPatterns();
            } else if (tab === 'dashboard' && typeof Dashboard !== 'undefined') {
                Dashboard.update(currentSymbol, null, null); 
            }
            // Market subsystem panels poll only while open.
            if (typeof MarketUI !== 'undefined') MarketUI.activateTab(tab);
        }
      });
    });

    window.addEventListener('resize', () => ChartModule.resize());
  }

  function closeInspector() {
    const inspector = document.getElementById('inspector-panel');
    inspector?.classList.add('collapsed');
    setOptionChainActive(false);
    // Market subsystem panels stop polling when nothing is watching.
    if (typeof MarketUI !== 'undefined') MarketUI.activateTab('chart');
    document.querySelectorAll('.sidebar-item').forEach(b => b.classList.remove('active'));
    document.querySelector('.sidebar-item[data-tab="chart"]')?.classList.add('active');
    setTimeout(() => ChartModule.resize(), 310);
  }

  function showInspectorView(viewId) {
    const inspector = document.getElementById('inspector-panel');
    if (!inspector) return;
    inspector.classList.remove('collapsed');
    document.querySelectorAll('.inspector-view').forEach(v => v.classList.remove('active'));
    document.getElementById(viewId)?.classList.add('active');
    
    const tabName = viewId.replace('-view', '');
    document.querySelectorAll('.sidebar-item').forEach(b => b.classList.toggle('active', b.dataset.tab === tabName));
    setTimeout(() => ChartModule.resize(), 310);
  }

  function getRangeForInterval(interval) {
    const ranges = {
      '1s': 1, '5s': 3, '15s': 5, '30s': 7,
      '1m': 5, '5m': 14, '15m': 30, '30m': 60,
      '1h': 90, '4h': 365, '1d': 3650, '1w': 3650, '1M': 3650
    };
    if (ranges[interval]) return ranges[interval];
    const sec = typeof Datafeed !== 'undefined' && Datafeed.parseIntervalToSeconds
      ? Datafeed.parseIntervalToSeconds(interval)
      : 86400;
    if (sec < 60) return 5;
    if (sec < 300) return 7;
    if (sec < 1800) return 60;
    if (sec < 3600) return 90;
    if (sec < 86400) return 730;
    return 3650;
  }

  async function loadSymbol(symbol, name = null) {
    // ⚡ Versioning: if a newer load starts before this one finishes, this one bails early
    const myVersion = ++_loadSymbolVersion;

    currentSymbol = symbol;
    currentName = name || symbol;
    window.currentSymbol = symbol;
    const loader = document.getElementById('chart-loading');
    if (loader) loader.classList.add('active');
    
    // Sync state with modules
    if (typeof Indicators !== 'undefined') {
        Indicators.setSymbol(currentSymbol);
        Indicators.setInterval(currentInterval);
    }

    if (optionChainInitialized && typeof OptionChain !== 'undefined' && isIndianSymbol(symbol)) {
        OptionChain.onSymbolChange(toOptionChainSymbol(symbol));
    }
    
    // Update Header Info
    const nameEl = document.getElementById('asi-name');
    if (nameEl) nameEl.textContent = currentName;
    const typeEl = document.getElementById('asi-type');
    if (typeEl) {
        if (symbol.startsWith('^') || symbol.endsWith('=F')) typeEl.textContent = 'INDEX';
        else if (symbol.endsWith('=X')) typeEl.textContent = 'FOREX';
        else if (symbol.endsWith('USDT')) typeEl.textContent = 'CRYPTO';
        else typeEl.textContent = 'STOCK';
    }

    const priceEl = document.getElementById('quote-price');
    const changeEl = document.getElementById('quote-change');
    if (priceEl) priceEl.textContent = '\u2014';
    if (changeEl) { changeEl.textContent = '\u2014'; changeEl.className = 'quote-change'; }
    if (ChartModule.resetLiveCandle) ChartModule.resetLiveCandle();
    if (ChartModule.stopCandleRefresh) ChartModule.stopCandleRefresh();

    try {
      const range = getRangeForInterval(currentInterval);
      // ⚡ Fetch OHLC + quote in parallel (was already parallel, kept)
      const [candles, quote] = await Promise.all([
          Datafeed.fetchOHLC(symbol, currentInterval, range),
          Datafeed.fetchQuote(symbol)
      ]);

      // A newer load has started — stop our timers and exit cleanly
      if (myVersion !== _loadSymbolVersion) {
        if (ChartModule.stopCandleRefresh) ChartModule.stopCandleRefresh();
        return;
      }

      if (candles && candles.length > 0) {
        ChartModule.setData(candles);
        
        // ⚡ Run indicator refresh, pattern reset, quote update CONCURRENTLY
        const tasks = [];

        if (typeof Indicators !== 'undefined' && Indicators.refreshActiveIndicators) {
            tasks.push(Indicators.refreshActiveIndicators());
        }
        if (typeof PatternEngine !== 'undefined' && PatternEngine.resetForChart) {
            PatternEngine.resetForChart(currentSymbol, currentInterval);
        }
        if (quote) {
            updateQuoteInfo(quote);
            if (quote.name && nameEl) nameEl.textContent = quote.name;
        }

        // Kick off live stream immediately; don't wait for indicator refresh
        startLiveStream();
        if (ChartModule.startCandleRefresh) ChartModule.startCandleRefresh(currentSymbol);
        window.dispatchEvent(new CustomEvent('app:chartReady', {
            detail: { symbol: currentSymbol, interval: currentInterval }
        }));

        // Await indicator tasks in background (loader stays on until done)
        if (tasks.length) await Promise.all(tasks);

      } else {
        window.showError(`No data for ${symbol}`);
      }
    } catch (e) { console.error('Load symbol error:', e); window.showError(`Failed to load ${symbol}`); }
    finally {
      // Always remove loader — even if this load was superseded by a newer one
      if (loader) loader.classList.remove('active');
    }
  }

  function updateQuoteInfo(quote) {
    if (!quote || quote.symbol !== currentSymbol) return;
    const price = Number(quote.price);
    const change = Number(quote.change || 0);
    const changePercent = Number(quote.changePercent || 0);
    if (!Number.isFinite(price)) return;

    const priceEl = document.getElementById('quote-price');
    const changeEl = document.getElementById('quote-change');
    
    if (priceEl) {
        priceEl.textContent = price.toLocaleString('en-IN', { minimumFractionDigits: 2 });
        priceEl.className = `quote-price ${change >= 0 ? 'positive' : 'negative'}`;
    }
    if (changeEl) {
        const sign = change >= 0 ? '+' : '';
        changeEl.textContent = `${sign}${change.toLocaleString('en-IN', { minimumFractionDigits: 2 })} (${sign}${changePercent.toFixed(2)}%)`;
        changeEl.className = `quote-change ${change >= 0 ? 'positive' : 'negative'}`;
    }
    window.dispatchEvent(new CustomEvent('wl:priceUpdate', {
        detail: { symbol: quote.symbol, price, change, changePct: changePercent }
    }));
    updateMarketStatus(quote);
  }

  let _sseReconnectTimer = null;
  let _sseRetries = 0;
  const _SSE_MAX_RETRIES = 12;

  function startLiveStream() {
    if (_sseReconnectTimer) { clearTimeout(_sseReconnectTimer); _sseReconnectTimer = null; }
    if (liveStreamSource) { liveStreamSource.close(); liveStreamSource = null; }
    _sseRetries = 0;
    _openSSE();
  }

  function _openSSE() {
    if (!currentSymbol) return;
    liveStreamSource = new EventSource(`/api/stream?symbol=${encodeURIComponent(currentSymbol)}&interval=${encodeURIComponent(currentInterval)}`);
    liveStreamSource.onopen = () => { _sseRetries = 0; };
    liveStreamSource.onerror = () => {
      if (liveStreamSource) { liveStreamSource.close(); liveStreamSource = null; }
      if (_sseRetries < _SSE_MAX_RETRIES) {
        const delay = Math.min(1000 * Math.pow(2, _sseRetries), 30000);
        _sseRetries++;
        _sseReconnectTimer = setTimeout(() => { if (currentSymbol) _openSSE(); }, delay);
      }
    };
    liveStreamSource.onmessage = (e) => {
      let data; try { data = JSON.parse(e.data); } catch { return; }
      if (data.type === 'tick') {
        if (data.symbol === currentSymbol) {
            ChartModule.updateLiveTick(data);
            updateQuoteFromTick(data);
        }
        if (window.PaperTrade) window.PaperTrade.updateLiveTick(data);
        if (typeof Dashboard !== 'undefined') Dashboard.update(currentSymbol, ChartModule.currentData, null);
      }
    };
  }

  function updateQuoteFromTick(tick) {
      const priceEl = document.getElementById('quote-price');
      const _tp = Number(tick.price);
      if (priceEl && Number.isFinite(_tp)) priceEl.textContent = _tp.toLocaleString('en-IN', { minimumFractionDigits: 2 });
      const price = Number(tick.price);
      const prevClose = Number(tick.prevClose || 0);
      const change = Number.isFinite(prevClose) && prevClose > 0 ? price - prevClose : Number(tick.change || 0);
      const changePct = prevClose > 0 ? (change / prevClose) * 100 : Number(tick.changePercent || tick.changePct || 0);
      if (Number.isFinite(price)) {
          window.dispatchEvent(new CustomEvent('wl:priceUpdate', {
              detail: { symbol: tick.symbol || currentSymbol, price, change, changePct }
          }));
      }
      if (tick.session || typeof tick.is_open !== 'undefined') {
          updateMarketStatus({
              symbol: tick.symbol || currentSymbol,
              isIndian: isIndianSymbol(tick.symbol || currentSymbol),
              session: tick.session || (tick.is_open ? 'REGULAR' : 'CLOSED')
          });
      }
  }

  function isIndianSymbol(symbol) {
      const s = (symbol || '').toUpperCase();
      if (!s) return false;
      if (s.endsWith('.NS') || s.endsWith('.BO') || s.startsWith('^NSE') || s.startsWith('^BSE') || s.startsWith('^CNX') || s.startsWith('^INDIA')) return true;
      if (s.startsWith('^') || s.endsWith('=F') || s.endsWith('=X')) return false;
      if (s.endsWith('USDT') || s.endsWith('USDC') || s.endsWith('BUSD') || s.endsWith('FDUSD')) return false;
      return !globalStockSymbols.has(s);
  }

  function toOptionChainSymbol(symbol) {
      const s = (symbol || '').toUpperCase().replace(/\.(NS|BO)$/i, '');
      const indexMap = {
          '^NSEI': 'NIFTY',
          '^NSEBANK': 'BANKNIFTY',
          '^CNXFIN': 'FINNIFTY',
          '^BSESN': 'SENSEX'
      };
      return indexMap[s] || s.replace(/^\^/, '');
  }

  function setOptionChainActive(active) {
      if (typeof OptionChain === 'undefined') return;
      if (active) {
          if (!optionChainInitialized) {
              OptionChain.init(onSymbolSelect);
              optionChainInitialized = true;
          } else if (OptionChain.toggleAutoRefresh) {
              OptionChain.toggleAutoRefresh(true);
          }
      } else if (optionChainInitialized && OptionChain.toggleAutoRefresh) {
          OptionChain.toggleAutoRefresh(false);
      }
  }

  async function fetchAuthoritativeMarketSession() {
      const now = Date.now();
      if (marketSessionCache && now - marketSessionCache.ts < 15000) {
          return marketSessionCache.data;
      }
      const data = await fetch('/api/market-session', { cache: 'no-store' }).then(r => r.json());
      marketSessionCache = { ts: now, data };
      return data;
  }

  async function updateMarketStatus(quote = {}) {
      const statusEl = document.getElementById('market-status');
      if (!statusEl) return;
      const isIndian = quote.isIndian === true || isIndianSymbol(quote.symbol || currentSymbol);
      if (!isIndian) {
          statusEl.style.display = 'none';
          statusEl.textContent = '';
          return;
      }
      let session = quote.session || (quote.is_open ? 'REGULAR' : 'CLOSED');
      let holidayName = quote.holidayName || null;
      try {
          const serverSession = await fetchAuthoritativeMarketSession();
          if (serverSession?.session) {
              session     = serverSession.session;
              holidayName = serverSession.holidayName || null;
              console.log('[MarketSession] authoritative IST session', serverSession);
          }
      } catch (e) {
          console.warn('[MarketSession] Could not refresh authoritative session; using quote fallback.', e);
          // Client-side holiday fallback — session_manager.js
          if (typeof SessionManager !== 'undefined') {
              const info = SessionManager.getSessionInfo();
              session     = info.session;
              holidayName = info.holidayName;
          }
      }

      // ── Session display config ──────────────────────────────────────────────
      const holidayLabel = holidayName ? `HOLIDAY · ${holidayName.toUpperCase()}` : 'HOLIDAY';
      const labels = {
          PRE_MARKET:  'PRE-MARKET',
          REGULAR:     'MARKET OPEN',
          POST_MARKET: 'POST-MARKET',
          HOLIDAY:     holidayLabel,
          CLOSED:      'MARKET CLOSED'
      };
      const colors = {
          PRE_MARKET:  ['rgba(245,158,11,0.16)',  '#f59e0b'],
          REGULAR:     ['rgba(0,230,118,0.14)',    '#00E676'],
          POST_MARKET: ['rgba(167,139,250,0.16)', '#a78bfa'],
          HOLIDAY:     ['rgba(251,146,60,0.18)',   '#fb923c'],   // warm orange — distinct from PRE-MKT amber
          CLOSED:      ['rgba(242,54,69,0.14)',    '#f23645']
      };
      const [bg, fg] = colors[session] || colors.CLOSED;
      statusEl.textContent = labels[session] || labels.CLOSED;
      statusEl.style.display  = 'inline-block';
      statusEl.style.background = bg;
      statusEl.style.color     = fg;
      statusEl.title = session === 'HOLIDAY'
          ? `NSE/BSE Market Holiday${holidayName ? ': ' + holidayName : ''} · No trading today`
          : '';
  }

  // --- WATCHLIST LOGIC ---
  function _esc(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function renderWatchlist() {
    const container = document.getElementById('watchlist-items');
    if (!container) return;
    container.innerHTML = watchlist.map(sym => `
      <div class="watchlist-item ${sym === currentSymbol ? 'active' : ''}" style="display:flex; justify-content:space-between; align-items:center;">
        <span style="cursor:pointer; flex:1;" onclick="App.onSymbolSelect({symbol:'${_esc(sym)}', name:'${_esc(sym)}'})">${_esc(sym)}</span>
        <div style="display:flex; gap:8px; align-items:center;">
          <span id="wl-prc-${sym.replace(/[^a-zA-Z0-9]/g, '')}"></span>
          <button onclick="App.removeFromWatchlist('${sym}')" style="background:none; border:none; color:var(--text-muted); cursor:pointer;"><i class="fas fa-trash-alt"></i></button>
        </div>
      </div>
    `).join('');
  }

  function addToWatchlist(symbol) {
      if (!watchlist.includes(symbol)) {
          watchlist.push(symbol);
          localStorage.setItem('fa_watchlist', JSON.stringify(watchlist));
          renderWatchlist();
          window.showNotification(`${symbol} added to watchlist`, 'success');
      }
  }

  function removeFromWatchlist(symbol) {
      watchlist = watchlist.filter(s => s !== symbol);
      localStorage.setItem('fa_watchlist', JSON.stringify(watchlist));
      renderWatchlist();
      window.showNotification(`${symbol} removed`, 'info');
  }

  // ─── Polling ID registry — stores ALL live interval IDs so we can kill them ──
  const _pollingIds = [];
  let _marketHalted = false;  // true when HOLIDAY or weekend/whole-day CLOSED

  /**
   * Stop ALL live activity — called when market is HOLIDAY or CLOSED for the day.
   * Safe to call multiple times (idempotent).
   */
  function haltAllLiveActivity(session, holidayName) {
    if (_marketHalted) return;  // already halted
    _marketHalted = true;
    console.log(`[MarketHalt] Stopping all live data. Session: ${session}${holidayName ? ' · ' + holidayName : ''}`);

    // 1. Kill every stored polling interval
    _pollingIds.forEach(id => clearInterval(id));
    _pollingIds.length = 0;

    // 2. Stop chart candle refresh + live close timer
    if (typeof ChartModule !== 'undefined') {
        if (ChartModule.stopCandleRefresh) ChartModule.stopCandleRefresh();
        if (ChartModule.resetLiveCandle)   ChartModule.resetLiveCandle();
        // Hide the price countdown widget
        const paw = document.getElementById('price-axis-widget');
        if (paw) paw.style.display = 'none';
    }

    // 3. Hide the LIVE dot badge
    const liveBadge = document.getElementById('live-badge');
    if (liveBadge) liveBadge.style.display = 'none';

    // 4. Show a prominent market-halt banner above the chart
    _showMarketHaltBanner(session, holidayName);

    console.log('[MarketHalt] All live activity stopped.');
  }

  /** Renders a non-intrusive banner just below the toolbar when market is inactive. */
  function _showMarketHaltBanner(session, holidayName) {
    // Remove any existing banner first
    const old = document.getElementById('market-halt-banner');
    if (old) old.remove();

    const isHoliday = session === 'HOLIDAY';
    const label = isHoliday
        ? `🏖 Market Holiday${holidayName ? ' · ' + holidayName : ''} — No trading today. Charts show historical data only.`
        : `🔴 Market Closed — No live data. Charts show historical data only.`;
    const bg    = isHoliday ? 'rgba(251,146,60,0.12)' : 'rgba(242,54,69,0.10)';
    const fg    = isHoliday ? '#fb923c'               : '#f23645';
    const bdr   = isHoliday ? 'rgba(251,146,60,0.30)' : 'rgba(242,54,69,0.25)';

    const banner = document.createElement('div');
    banner.id = 'market-halt-banner';
    banner.style.cssText = `
      width:100%; padding:7px 16px; font-size:12px; font-weight:700;
      background:${bg}; color:${fg}; border-bottom:1px solid ${bdr};
      display:flex; align-items:center; gap:8px; letter-spacing:0.3px;
      font-family:'Outfit',sans-serif; z-index:200; flex-shrink:0;
    `;
    banner.textContent = label;

    // Insert right above the chart stage
    const chartStage = document.querySelector('.chart-stage');
    if (chartStage) {
        chartStage.insertBefore(banner, chartStage.firstChild);
    }
  }

  function startPricePolling() {
    if (_marketHalted) {
        console.log('[PricePolling] Market halted — skipping all live polling.');
        return;
    }

    // Poll current-symbol quote every 5s
    _pollingIds.push(setInterval(async () => {
        if (_marketHalted) return;
        try {
            const quote = await Datafeed.fetchQuote(currentSymbol);
            if (quote) updateQuoteInfo(quote);
        } catch (_e) { /* silent */ }
    }, 2000));

    // Poll watchlist symbols one-by-one (staggered) to avoid server bursts
    let _wlIdx = 0;
    _pollingIds.push(setInterval(async () => {
        if (_marketHalted) return;
        const wl = [...watchlist];
        if (!wl.length) return;
        _wlIdx = (_wlIdx + 1) % wl.length;
        const sym = wl[_wlIdx];
        try {
            const q = await Datafeed.fetchQuote(sym);
            const el = document.getElementById(`wl-prc-${sym.replace(/[^a-zA-Z0-9]/g, '')}`);
            if (el && q) {
                el.textContent = Number(q.price).toLocaleString('en-IN', {minimumFractionDigits: 2});
                el.style.color = (q.change >= 0) ? 'var(--accent-green)' : 'var(--accent-red)';
            }
        } catch (_e) { /* silent */ }
    }, 1200)); // 1.2s per symbol keeps it smooth
  }

  function onSymbolSelect(item) {
      currentSymbol = item.symbol;
      currentName = item.name || item.symbol;
      localStorage.setItem('fa_current_symbol', currentSymbol);
      localStorage.setItem('fa_current_name', currentName);
      if (typeof SymbolSearch !== 'undefined') SymbolSearch.setValue(item.symbol);
      
      const nameEl = document.getElementById('asi-name');
      if (nameEl) nameEl.textContent = currentName;

      const typeEl = document.getElementById('asi-type');
      if (typeEl) {
        if (item.symbol.startsWith('^') || item.symbol.endsWith('=F')) typeEl.textContent = 'INDEX';
        else if (item.symbol.endsWith('=X')) typeEl.textContent = 'FOREX';
        else if (item.symbol.endsWith('USDT')) typeEl.textContent = 'CRYPTO';
        else typeEl.textContent = 'STOCK';
      }
      
      renderWatchlist(); // Update active state
      loadSymbol(item.symbol, item.name);
  }

  async function updateExtendedHoursUI() {
    if (!currentSymbol) return;
    const badgeEl = document.getElementById('ext-hours-badge');
    if (!isIndianSymbol(currentSymbol)) {
        if (badgeEl) badgeEl.innerHTML = '';
        updateMarketStatus({ isIndian: false });
        // Non-Indian (crypto/global) — start polling once if not already started
        if (!_marketHalted && !_pollingStarted) { _pollingStarted = true; startPricePolling(); }
        return;
    }
    try {
        const res = await fetch(`/api/extended-hours/${currentSymbol}`).then(r => r.json());
        updateMarketStatus({ symbol: currentSymbol, isIndian: true, session: res.session, holidayName: res.holidayName || null });
        if (!badgeEl) return;

        if (res.session === 'HOLIDAY' || res.session === 'CLOSED') {
            // ── HALT everything ────────────────────────────────────────────
            haltAllLiveActivity(res.session, res.holidayName || null);

            if (res.session === 'HOLIDAY') {
                const hName = res.holidayName ? ` · ${res.holidayName}` : '';
                badgeEl.innerHTML = `<div style="display:flex; align-items:center; gap:6px; margin-left:12px;">
                  <span style="padding:2px 8px; border-radius:4px;
                    background:rgba(251,146,60,0.18); color:#fb923c;
                    font-size:10px; font-weight:800; letter-spacing:0.5px;
                    border:1px solid rgba(251,146,60,0.3);">
                    🏖 HOLIDAY${hName}
                  </span>
                </div>`;
            } else {
                badgeEl.innerHTML = '';
            }
        } else if (res.session === 'PRE_MARKET' || res.session === 'POST_MARKET') {
            // Start polling if not yet started
            if (!_pollingStarted) { _pollingStarted = true; startPricePolling(); }
            const label = res.session === 'PRE_MARKET' ? 'PRE-MKT' : 'POST-MKT';
            const price = res.preMarketPrice || res.postMarketPrice;
            if (!Number.isFinite(Number(price)) || Number(price) <= 0) {
                badgeEl.innerHTML = '';
                return;
            }
            const priceHtml = Number.isFinite(Number(price)) ? `<span style="color:white; font-weight:700;">₹${Number(price).toLocaleString('en-IN')}</span>` : '';
            badgeEl.innerHTML = `<div style="display:flex; align-items:center; gap:8px; margin-left:12px;"><span style="padding:2px 6px; border-radius:4px; background:rgba(245,158,11,0.15); color:#f59e0b; font-size:10px; font-weight:700;">${label}</span>${priceHtml}</div>`;
        } else {
            // REGULAR — start polling if not yet started
            if (!_pollingStarted) { _pollingStarted = true; startPricePolling(); }
            badgeEl.innerHTML = '';
        }
    } catch (e) {
        // Client-side fallback: check SessionManager for holidays
        if (typeof SessionManager !== 'undefined') {
            const info = SessionManager.getSessionInfo();
            if (info.session === 'HOLIDAY' || info.session === 'CLOSED') {
                haltAllLiveActivity(info.session, info.holidayName);
            } else if (!_pollingStarted) {
                _pollingStarted = true;
                startPricePolling();
            }
            if (badgeEl && info.session === 'HOLIDAY') {
                const hName = info.holidayName ? ` · ${info.holidayName}` : '';
                badgeEl.innerHTML = `<div style="display:flex; align-items:center; gap:6px; margin-left:12px;">
                  <span style="padding:2px 8px; border-radius:4px;
                    background:rgba(251,146,60,0.18); color:#fb923c;
                    font-size:10px; font-weight:800; letter-spacing:0.5px;
                    border:1px solid rgba(251,146,60,0.3);">
                    🏖 HOLIDAY${hName}
                  </span>
                </div>`;
                updateMarketStatus({ symbol: currentSymbol, isIndian: true, session: 'HOLIDAY', holidayName: info.holidayName });
            }
        }
    }
  }

  function initTimeframeButtons() {
    const toggle = document.getElementById('tf-dropdown-toggle');
    const menu = document.getElementById('tf-dropdown-menu');
    const activeLabel = document.getElementById('active-tf-label');

    if (toggle && menu) {
        toggle.onclick = (e) => { e.stopPropagation(); menu.classList.toggle('active'); };
        document.addEventListener('click', (e) => {
            if (!menu.contains(e.target) && !toggle.contains(e.target)) {
                menu.classList.remove('active');
            }
        });
    }
    
    // ⚡ One shared debounced load — rapid timeframe clicks only fire the last one
    const _debouncedLoadSymbol = _debounce((sym) => loadSymbol(sym), 120);

    // Set initial active state from currentInterval
    document.querySelectorAll('.tf-menu-item[data-interval]').forEach(btn => {
        if (btn.dataset.interval === currentInterval) {
            btn.classList.add('active');
            if (activeLabel) activeLabel.textContent = btn.textContent.trim();
        } else {
            btn.classList.remove('active');
        }

        btn.onclick = () => {
            document.querySelectorAll('.tf-menu-item').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentInterval = btn.dataset.interval;
            localStorage.setItem('fa_current_interval', currentInterval);
            if (ChartModule.setActiveInterval) ChartModule.setActiveInterval(currentInterval);
            _debouncedLoadSymbol(currentSymbol);
            if (activeLabel) activeLabel.textContent = btn.textContent.trim();
            // Use optional chaining — menu may be null if toggle element missing
            menu?.classList.remove('active');
        };
    });

    // Handle initial state for custom interval if not in standard list
    const isStandard = Array.from(document.querySelectorAll('.tf-menu-item[data-interval]'))
                            .some(btn => btn.dataset.interval === currentInterval);
    if (!isStandard && activeLabel) {
        activeLabel.textContent = currentInterval;
    }

    // Custom Timeframe Logic
    const customTrigger = document.getElementById('btn-custom-tf-trigger');
    const customWrapper = document.getElementById('custom-tf-input-wrapper');
    const customInput = document.getElementById('custom-tf-input');
    const customApply = document.getElementById('btn-apply-custom-tf');

    if (customTrigger && customWrapper) {
        customTrigger.onclick = (e) => {
            e.stopPropagation();
            menu.classList.remove('active');
            customWrapper.style.display = 'flex';
            if (customInput) customInput.focus();
        };
    }

    if (customApply && customInput) {
        customApply.onclick = () => {
            const val = normalizeIntervalInput(customInput.value);
            if (!val) return;
            
            document.querySelectorAll('.tf-menu-item').forEach(b => b.classList.remove('active'));
            currentInterval = val;
            localStorage.setItem('fa_current_interval', currentInterval);
            if (ChartModule.setActiveInterval) ChartModule.setActiveInterval(currentInterval);
            loadSymbol(currentSymbol);
            
            const activeLabel = document.getElementById('active-tf-label');
            if (activeLabel) activeLabel.textContent = val;
            
            customWrapper.style.display = 'none';
      };
    }
  }

  function normalizeIntervalInput(value) {
      const raw = (value || '').trim();
      const match = raw.match(/^(\d+)\s*([smhdwM])$/);
      if (!match) {
          window.showError?.('Use a timeframe like 3m, 2h, 1d, 1w, or 1M');
          return '';
      }
      const unit = match[2] === 'M' ? 'M' : match[2].toLowerCase();
      return `${parseInt(match[1], 10)}${unit}`;
  }

  function initWatchlistSearch() {
      const addBtn = document.getElementById('btn-add-watchlist');
      const box = document.getElementById('watchlist-search-container');
      const input = document.getElementById('watchlist-search-input');
      const resultsDiv = document.getElementById('watchlist-search-results');
      
      if (addBtn && box && input) {
          addBtn.onclick = () => { box.style.display = box.style.display === 'none' ? 'block' : 'none'; if (box.style.display === 'block') input.focus(); };
          input.oninput = async (e) => {
              const query = e.target.value.trim();
              if (query.length < 2) { resultsDiv.innerHTML = ''; return; }
              try {
                  const results = await Datafeed.searchSymbols(query);
                  resultsDiv.innerHTML = results.slice(0, 5).map(r => `
                      <div style="padding:8px; border-bottom:1px solid var(--border-color); cursor:pointer;" onclick="App.addToWatchlist('${_esc(r.symbol)}'); document.getElementById('watchlist-search-container').style.display='none'; document.getElementById('watchlist-search-input').value=''; document.getElementById('watchlist-search-results').innerHTML='';">
                          <span style="color:white; font-weight:700;">${_esc(r.symbol)}</span> <span style="font-size:10px; color:var(--text-muted);">${_esc(r.name)}</span>
                      </div>
                  `).join('');
              } catch (err) {}
          };
      }
  }

  function initThemeToggle() {
      const btn = document.getElementById('btn-theme-toggle');
      const savedTheme = localStorage.getItem('fa_theme');
      if (savedTheme === 'light') {
          document.body.classList.remove('dark-theme');
          document.body.classList.add('light-theme');
      } else if (savedTheme === 'dark') {
          document.body.classList.remove('light-theme');
          document.body.classList.add('dark-theme');
      }
      if (typeof ChartModule !== 'undefined' && ChartModule.applyTheme) {
          ChartModule.applyTheme();
      }
      if (btn) btn.onclick = () => {
          const isLight = document.body.classList.contains('light-theme');
          if (isLight) {
              document.body.classList.remove('light-theme');
              document.body.classList.add('dark-theme');
              localStorage.setItem('fa_theme', 'dark');
          } else {
              document.body.classList.remove('dark-theme');
              document.body.classList.add('light-theme');
              localStorage.setItem('fa_theme', 'light');
          }
          if (typeof ChartModule !== 'undefined' && ChartModule.applyTheme) {
              ChartModule.applyTheme();
          }
      };
  }

  function setChartTypeUI(type) {
      ChartModule.setChartType(type);
      document.querySelectorAll('.chart-type-btn').forEach(btn => {
          btn.classList.toggle('active', btn.id === `chart-type-${type}`);
      });
  }

  async function refreshPatterns() {
      const container = document.getElementById('patterns-list');
      if (!container) return;
      container.innerHTML = '<div class="analytics-placeholder"><i class="fas fa-spinner fa-spin"></i> Scanning chart patterns...</div>';
      if (typeof PatternEngine !== 'undefined' && PatternEngine.loadAndDraw) {
          const s = PatternSettings?.get?.() || {};
          PatternEngine.loadAndDraw(currentSymbol, currentInterval, s.accountSize, s.riskPct, s.lotSize);
      }
  }

  return {
    init, onSymbolSelect, closeInspector, addToWatchlist, removeFromWatchlist,
    get currentSymbol() { return currentSymbol; },
    get currentInterval() { return currentInterval; },
    takeScreenshot: () => {
      const canvas = ChartModule.takeScreenshot();
      if (canvas) {
        const link = document.createElement('a');
        link.download = `chart_${currentSymbol}.png`;
        link.href = canvas.toDataURL();
        link.click();
      }
    },
    showSettings: () => alert("Future Chart v1.0\\nHigh Performance Engine Active"),
    refreshPatterns,
    setChartType: setChartTypeUI
  };
})();  window.App = App;
  document.addEventListener('DOMContentLoaded', () => {
    App.init();
    if (typeof MarketUI !== 'undefined') MarketUI.init(App.onSymbolSelect);
  });
