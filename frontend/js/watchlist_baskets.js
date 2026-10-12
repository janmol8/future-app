/**
 * Watchlist baskets module.
 * Adds multi-basket watchlists without changing the legacy watchlist/search code.
 */
const WatchlistBaskets = (() => {
  const STORAGE_KEY = 'fa_watchlist_baskets';
  const LEGACY_KEY = 'fa_watchlist';
  const DEFAULT_ID = 'default';
  const ACTIVE_PRICE_THROTTLE_MS = 75;
  const BACKGROUND_PRICE_THROTTLE_MS = 250;
  const BASKET_REFRESH_MS = 750;
  const MAX_QUOTES_PER_CYCLE = 3;
  const DEFAULT_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'RELIANCE.NS', 'TCS.NS', '^NSEI', '^BSESN', 'GC=F'];

  let state = null;
  let initialized = false;
  let legacyAddToWatchlist = null;
  let originalFetchQuote = null;
  let restoringList = false;
  let refreshTimer = null;
  let refreshStartTimer = null;
  let refreshCursor = 0;
  const priceCache = new Map();
  const lastUpdateAt = new Map();
  const inFlightQuotes = new Set();

  function init() {
    if (initialized) return;
    initialized = true;

    migrateLegacyWatchlist();
    state = loadState();
    normalizeState();
    saveState();

    replaceWatchlistHeader();
    bindSearchToggle();
    wrapLegacyAddToWatchlist();
    wrapFetchQuote();
    bindPriceEvents();
    bindChartReady();
    observeLegacyRepaints();
    render();
  }

  function migrateLegacyWatchlist() {
    if (localStorage.getItem(STORAGE_KEY)) return;
    const legacy = readJson(LEGACY_KEY, DEFAULT_SYMBOLS);
    const symbols = Array.isArray(legacy) ? legacy.filter(Boolean) : [];
    const next = {
      activeBasketId: DEFAULT_ID,
      baskets: [{ id: DEFAULT_ID, name: 'Main Watchlist', symbols }],
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  function readJson(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (_) {
      return fallback;
    }
  }

  function loadState() {
    return readJson(STORAGE_KEY, {
      activeBasketId: DEFAULT_ID,
      baskets: [{ id: DEFAULT_ID, name: 'Main Watchlist', symbols: [] }],
    });
  }

  function normalizeState() {
    if (!state || !Array.isArray(state.baskets)) {
      state = { activeBasketId: DEFAULT_ID, baskets: [] };
    }
    if (state.baskets.length === 0) {
      state.baskets.push({ id: DEFAULT_ID, name: 'Main Watchlist', symbols: [] });
    }
    state.baskets = state.baskets.map((basket, idx) => ({
      id: String(basket.id || (idx === 0 ? DEFAULT_ID : Date.now() + idx)),
      name: String(basket.name || `Watchlist ${idx + 1}`).trim() || `Watchlist ${idx + 1}`,
      symbols: uniqueSymbols(Array.isArray(basket.symbols) ? basket.symbols : []),
    }));
    if (!getActiveBasket()) {
      state.activeBasketId = state.baskets[0].id;
    }
    hydrateEmptyDefaultFromLegacy();
    hydrateEmptyActiveFromLegacy();
  }

  function hydrateEmptyDefaultFromLegacy() {
    const hasAnySymbols = state.baskets.some(basket => Array.isArray(basket.symbols) && basket.symbols.length > 0);
    if (hasAnySymbols) return;

    const legacy = readJson(LEGACY_KEY, DEFAULT_SYMBOLS);
    const symbols = Array.isArray(legacy) ? uniqueSymbols(legacy) : [];
    if (!symbols.length) return;

    const defaultBasket = state.baskets.find(basket => basket.id === DEFAULT_ID) || state.baskets[0];
    defaultBasket.name = defaultBasket.name || 'Main Watchlist';
    defaultBasket.symbols = symbols;
    state.activeBasketId = defaultBasket.id;
  }

  function hydrateEmptyActiveFromLegacy() {
    const active = getActiveBasket();
    if (!active || active.symbols.length > 0) return;

    const legacy = readJson(LEGACY_KEY, DEFAULT_SYMBOLS);
    const symbols = Array.isArray(legacy) ? uniqueSymbols(legacy) : [];
    if (!symbols.length) return;

    active.symbols = symbols;
  }

  function uniqueSymbols(symbols) {
    const seen = new Set();
    const out = [];
    symbols.forEach(sym => {
      const normalized = normalizeSymbol(sym);
      if (!normalized || seen.has(normalized)) return;
      seen.add(normalized);
      out.push(normalized);
    });
    return out;
  }

  function saveState() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function getActiveBasket() {
    return state?.baskets?.find(b => b.id === state.activeBasketId) || null;
  }

  function replaceWatchlistHeader() {
    const view = document.getElementById('watchlist-view');
    const header = view?.querySelector('.inspector-header');
    if (!view || !header) return;

    view.classList.add('wlb-view');
    header.classList.add('wlb-header');
    header.innerHTML = `
      <div class="wlb-topline">
        <select class="wlb-select" id="wlb-basket-select" aria-label="Select watchlist basket"></select>
        <button class="wlb-btn wlb-new-btn" id="wlb-new-basket" type="button">+ New</button>
        <button class="btn-icon btn-close-panel" title="Close Watchlist" type="button" onclick="App.closeInspector()">
          <i class="fas fa-times"></i>
        </button>
      </div>
      <div class="wlb-search-row">
        <button id="btn-add-watchlist" class="wlb-search-btn" type="button">
          <i class="fas fa-search"></i>
          <span>Search symbol...</span>
        </button>
      </div>
      <div class="wlb-meta-row">
        <span id="wlb-count">0 symbols</span>
        <span class="wlb-dot">.</span>
        <button class="wlb-link" id="wlb-rename-basket" type="button">Rename</button>
        <button class="wlb-link danger" id="wlb-delete-basket" type="button">Delete</button>
      </div>
    `;

    header.querySelector('#wlb-basket-select')?.addEventListener('change', event => {
      state.activeBasketId = event.target.value;
      saveState();
      render({ fade: true });
    });
    header.querySelector('#wlb-new-basket')?.addEventListener('click', createBasket);
    header.querySelector('#wlb-rename-basket')?.addEventListener('click', renameBasket);
    header.querySelector('#wlb-delete-basket')?.addEventListener('click', deleteBasket);
  }

  function bindSearchToggle() {
    const button = document.getElementById('btn-add-watchlist');
    const box = document.getElementById('watchlist-search-container');
    const input = document.getElementById('watchlist-search-input');
    if (!button || !box || !input) return;

    button.addEventListener('click', () => {
      const isHidden = box.style.display === 'none' || !box.style.display;
      box.style.display = isHidden ? 'block' : 'none';
      if (isHidden) input.focus();
    });
  }

  function wrapLegacyAddToWatchlist() {
    if (!window.App || typeof window.App.addToWatchlist !== 'function') return;
    legacyAddToWatchlist = window.App.addToWatchlist.bind(window.App);
    window.App.addToWatchlist = (symbol, displayName) => addSymbol(symbol, displayName);
  }

  function wrapFetchQuote() {
    if (typeof Datafeed === 'undefined' || typeof Datafeed.fetchQuote !== 'function') return;
    if (Datafeed.fetchQuote.__wlbWrapped) return;

    originalFetchQuote = Datafeed.fetchQuote.bind(Datafeed);
    const wrapped = async (symbol) => {
      const quote = await originalFetchQuote(symbol);
      if (quote && Number.isFinite(Number(quote.price))) {
        window.dispatchEvent(new CustomEvent('wl:priceUpdate', {
          detail: {
            symbol: normalizeSymbol(symbol),
            quoteSymbol: normalizeSymbol(quote.symbol || symbol),
            price: Number(quote.price),
            change: Number(quote.change || 0),
            changePct: Number(quote.changePercent || quote.changePct || 0),
          },
        }));
      }
      return quote;
    };
    wrapped.__wlbWrapped = true;
    Datafeed.fetchQuote = wrapped;
  }

  function bindPriceEvents() {
    window.addEventListener('wl:priceUpdate', event => {
      const detail = event.detail || {};
      const symbol = normalizeSymbol(detail.symbol || detail.quoteSymbol);
      if (!symbol) return;

      const now = Date.now();
      const last = lastUpdateAt.get(symbol) || 0;
      const isActiveChartSymbol = symbol === normalizeSymbol(window.App?.currentSymbol || window.currentSymbol);
      const throttleMs = isActiveChartSymbol ? ACTIVE_PRICE_THROTTLE_MS : BACKGROUND_PRICE_THROTTLE_MS;
      if (now - last < throttleMs) return;
      lastUpdateAt.set(symbol, now);

      const previous = priceCache.get(symbol);
      const next = {
        price: Number(detail.price),
        change: Number(detail.change || 0),
        changePct: Number(detail.changePct || 0),
      };
      priceCache.set(symbol, next);
      updateRowPrice(symbol, next, previous);
    });
  }

  function observeLegacyRepaints() {
    const container = document.getElementById('watchlist-items');
    if (!container) return;
    const observer = new MutationObserver(() => {
      if (restoringList) return;
      if (container.querySelector('.wlb-row, .wlb-empty')) return;
      restoringList = true;
      window.requestAnimationFrame(() => {
        renderListOnly();
        restoringList = false;
      });
    });
    observer.observe(container, { childList: true });
  }

  function createBasket() {
    const name = window.prompt('Basket name');
    const clean = (name || '').trim();
    if (!clean) return;
    const id = String(Date.now());
    state.baskets.push({ id, name: clean, symbols: [] });
    state.activeBasketId = id;
    saveState();
    render({ fade: true });
  }

  function renameBasket() {
    const basket = getActiveBasket();
    if (!basket) return;
    const next = window.prompt('Rename basket', basket.name);
    const clean = (next || '').trim();
    if (!clean) return;
    basket.name = clean;
    saveState();
    render();
  }

  function deleteBasket() {
    const basket = getActiveBasket();
    if (!basket || state.baskets.length <= 1) {
      window.showNotification?.('You need at least one watchlist basket.', 'info');
      return;
    }
    if (!window.confirm(`Delete "${basket.name}"?`)) return;
    state.baskets = state.baskets.filter(b => b.id !== basket.id);
    state.activeBasketId = state.baskets[0].id;
    saveState();
    render({ fade: true });
  }

  function addSymbol(symbol, displayName) {
    const normalized = normalizeSymbol(symbol);
    if (!normalized) return;
    const basket = getActiveBasket();
    if (!basket) return;

    if (basket.symbols.includes(normalized)) {
      window.showNotification?.(`${normalized} is already in this basket.`, 'info');
      return;
    }

    basket.symbols.push(normalized);
    saveState();

    if (legacyAddToWatchlist) {
      legacyAddToWatchlist(normalized);
    }

    renderListOnly();
    window.showNotification?.(`${normalized} added to ${basket.name}`, 'success');

    if (displayName) {
      priceCache.set(normalized, priceCache.get(normalized) || {});
    }
  }

  function removeSymbol(symbol) {
    const normalized = normalizeSymbol(symbol);
    const basket = getActiveBasket();
    if (!basket) return;
    basket.symbols = basket.symbols.filter(sym => sym !== normalized);
    saveState();
    renderListOnly();
    window.showNotification?.(`${normalized} removed from ${basket.name}`, 'info');
  }

  function render(options = {}) {
    renderSelector();
    renderMeta();
    renderListOnly(options);
  }

  function renderSelector() {
    const select = document.getElementById('wlb-basket-select');
    if (!select) return;
    select.innerHTML = state.baskets.map(basket => (
      `<option value="${escapeAttr(basket.id)}"${basket.id === state.activeBasketId ? ' selected' : ''}>${escapeHtml(basket.name)}</option>`
    )).join('');
  }

  function renderMeta() {
    const basket = getActiveBasket();
    const count = basket?.symbols?.length || 0;
    const countEl = document.getElementById('wlb-count');
    if (countEl) countEl.textContent = `${count} symbol${count === 1 ? '' : 's'}`;
  }

  function renderListOnly(options = {}) {
    const container = document.getElementById('watchlist-items');
    const basket = getActiveBasket();
    if (!container || !basket) return;

    const write = () => {
      restoringList = true;
      container.classList.add('wlb-list');
      container.innerHTML = basket.symbols.length
        ? basket.symbols.map(renderRow).join('')
        : `<div class="wlb-empty">No symbols in this basket yet.</div>`;
      renderMeta();
      basket.symbols.forEach(symbol => {
        const cached = priceCache.get(symbol);
        if (cached) updateRowPrice(symbol, cached, null);
      });
      window.requestAnimationFrame(() => {
        restoringList = false;
      });
    };

    if (!options.fade) {
      write();
      return;
    }

    container.classList.add('is-switching');
    window.setTimeout(() => {
      write();
      container.classList.remove('is-switching');
    }, 150);
  }

  function requestPrice(symbol, attempt = 0) {
    if (!originalFetchQuote) return;
    const normalized = normalizeSymbol(symbol);
    if (!normalized || inFlightQuotes.has(normalized)) return;
    inFlightQuotes.add(normalized);
    originalFetchQuote(symbol)
      .then(quote => {
        if (!quote || !Number.isFinite(Number(quote.price))) {
          schedulePriceRetry(symbol, attempt);
          return;
        }
        window.dispatchEvent(new CustomEvent('wl:priceUpdate', {
          detail: {
            symbol: normalizeSymbol(symbol),
            quoteSymbol: normalizeSymbol(quote.symbol || symbol),
            price: Number(quote.price),
            change: Number(quote.change || 0),
            changePct: Number(quote.changePercent || quote.changePct || 0),
          },
        }));
      })
      .catch(() => schedulePriceRetry(symbol, attempt))
      .finally(() => {
        inFlightQuotes.delete(normalized);
      });
  }

  function schedulePriceRetry(symbol, attempt) {
    if (attempt >= 4) {
      markPriceUnavailable(symbol);
      return;
    }
    window.setTimeout(() => requestPrice(symbol, attempt + 1), 2500 + attempt * 1500);
  }

  function markPriceUnavailable(symbol) {
    const row = findRow(symbol);
    if (!row) return;
    const priceEl = row.querySelector('[data-price]');
    const changeEl = row.querySelector('[data-change]');
    if (priceEl && priceEl.textContent === 'Loading...') priceEl.textContent = '-';
    if (changeEl && changeEl.textContent === '-') changeEl.textContent = 'No quote';
  }

  function startBasketPriceRefresh() {
    if (!window.App?.currentSymbol) return;
    if (refreshTimer) window.clearInterval(refreshTimer);
    primeActiveBasketPrices();
    refreshTimer = window.setInterval(refreshActiveBasketPrices, BASKET_REFRESH_MS);
  }

  function bindChartReady() {
    window.addEventListener('app:chartReady', () => {
      if (refreshStartTimer) window.clearTimeout(refreshStartTimer);
      refreshStartTimer = window.setTimeout(() => {
        startBasketPriceRefresh();
      }, 250);
    });
  }

  function refreshActiveBasketPrices() {
    const basket = getActiveBasket();
    if (!basket || !Array.isArray(basket.symbols) || !basket.symbols.length) return;
    const current = normalizeSymbol(window.App?.currentSymbol);
    const symbols = basket.symbols.filter(symbol => {
      const normalized = normalizeSymbol(symbol);
      return normalized && (normalized !== current || !priceCache.has(normalized));
    });
    if (!symbols.length) return;

    for (let i = 0; i < Math.min(MAX_QUOTES_PER_CYCLE, symbols.length); i += 1) {
      const symbol = symbols[refreshCursor % symbols.length];
      refreshCursor += 1;
      window.setTimeout(() => requestPrice(symbol), i * 120);
    }
  }

  function primeActiveBasketPrices() {
    const basket = getActiveBasket();
    if (!basket || !Array.isArray(basket.symbols) || !basket.symbols.length) return;
    const current = normalizeSymbol(window.App?.currentSymbol || window.currentSymbol);
    basket.symbols
      .filter(symbol => {
        const normalized = normalizeSymbol(symbol);
        return normalized && (normalized !== current || !priceCache.has(normalized));
      })
      .forEach((symbol, index) => {
        window.setTimeout(() => requestPrice(symbol), index * 80);
      });
  }

  function renderRow(symbol) {
    const active = window.App?.currentSymbol === symbol ? ' active' : '';
    const cache = priceCache.get(symbol);
    const label = instrumentLabel(symbol);
    const hue = symbolHue(symbol);
    return `
      <div class="wlb-row${active}" data-symbol="${escapeAttr(symbol)}" onclick="WatchlistBaskets.selectSymbol('${escapeJs(symbol)}')">
        <div class="wlb-badge" style="--wlb-badge-hue:${hue};">${escapeHtml(abbrev(symbol))}</div>
        <div class="wlb-main">
          <div class="wlb-symbol">${escapeHtml(cleanDisplaySymbol(symbol))}</div>
          <div class="wlb-subtitle">${escapeHtml(label)}</div>
        </div>
        <div class="wlb-quote" aria-live="polite">
          <div class="wlb-price" data-price>${Number.isFinite(Number(cache?.price)) ? formatPrice(cache.price) : 'Loading...'}</div>
          <div class="wlb-change ${Number(cache?.change || 0) >= 0 ? 'positive' : 'negative'}" data-change>${formatChange(cache)}</div>
        </div>
        <button class="wlb-remove" type="button" title="Remove" onclick="event.stopPropagation(); WatchlistBaskets.removeSymbol('${escapeJs(symbol)}')">&times;</button>
      </div>
    `;
  }

  function selectSymbol(symbol) {
    if (!window.App?.onSymbolSelect) return;
    window.App.onSymbolSelect({ symbol, name: cleanDisplaySymbol(symbol) });
    renderListOnly();
  }

  function updateRowPrice(symbol, quote, previous) {
    const row = findRow(symbol);
    if (!row) return;
    const priceEl = row.querySelector('[data-price]');
    const changeEl = row.querySelector('[data-change]');
    if (priceEl) priceEl.textContent = formatPrice(quote.price);
    if (changeEl) {
      changeEl.textContent = formatChange(quote);
      changeEl.classList.toggle('positive', Number(quote.change || 0) >= 0);
      changeEl.classList.toggle('negative', Number(quote.change || 0) < 0);
    }

    if (previous && Number.isFinite(previous.price) && Number.isFinite(quote.price)) {
      row.classList.remove('flash-up', 'flash-down');
      void row.offsetWidth;
      if (quote.price > previous.price) row.classList.add('flash-up');
      if (quote.price < previous.price) row.classList.add('flash-down');
    }
  }

  function findRow(symbol) {
    const normalized = normalizeSymbol(symbol);
    return Array.from(document.querySelectorAll('.wlb-row'))
      .find(row => normalizeSymbol(row.dataset.symbol) === normalized) || null;
  }

  function normalizeSymbol(symbol) {
    return String(symbol || '').trim().toUpperCase();
  }

  function cleanDisplaySymbol(symbol) {
    return normalizeSymbol(symbol).replace(/\.(NS|BO)$/i, '');
  }

  function abbrev(symbol) {
    const cleaned = cleanDisplaySymbol(symbol).replace(/[^A-Z0-9]/g, '');
    return (cleaned.slice(0, 2) || '--').padEnd(2, '-');
  }

  function symbolHue(symbol) {
    return normalizeSymbol(symbol).split('').reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 360;
  }

  function instrumentLabel(symbol) {
    const s = normalizeSymbol(symbol);
    if (s.endsWith('.NS')) return 'NSE · Equity';
    if (s.endsWith('.BO')) return 'BSE · Equity';
    if (s.endsWith('USDT') || s.endsWith('USDC')) return 'Crypto';
    if (s.endsWith('=F')) return 'Futures';
    if (s.startsWith('^')) return 'Index';
    return 'Global';
  }

  function formatPrice(price) {
    const n = Number(price);
    if (!Number.isFinite(n)) return '-';
    return n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function formatChange(quote) {
    if (!quote || !Number.isFinite(Number(quote.price))) return '-';
    const change = Number(quote.change || 0);
    const pct = Number(quote.changePct || 0);
    const arrow = change >= 0 ? '▲' : '▼';
    const sign = change >= 0 ? '+' : '';
    return `${arrow} ${sign}${change.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, ch => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[ch]));
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function escapeJs(value) {
    return String(value).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  }

  return {
    init,
    addSymbol,
    removeSymbol,
    selectSymbol,
    _state: () => state,
  };
})();

window.WatchlistBaskets = WatchlistBaskets;
