const PaperTrade = (() => {
  const SPOT_BASE = '/api/paper';
  const FUTURES_BASE = '/api/futures';

  // ─── STATE ──────────────────────────────────────────────────────────
  let mode = 'FUTURES'; 
  let spotState = { account: null, positions: [], orders: [], history: [], performance: null };
  let futuresState = {
    account: null, positions: [], orders: [], history: [], fundingHistory: [],
    journal: [], patterns: [], markPrices: {}
  };
  let tradingState = {
    totalBalance: 0,
    availableBalance: 0,
    usedMargin: 0,
    openPositions: [],
    orderHistory: [],
    openOrders: [],
    realizedPnL: 0,
    unrealizedPnL: 0,
    portfolioValue: 0
  };
  let activeScreen = 'dashboard';
  let refreshTimer = null;
  let priceCache = {};

  // Order form state (Made public setters to avoid ReferenceErrors)
  let orderForm = {
    symbol: '', price: 0, name: '', side: 'BUY', qty: 1,
    type: 'MARKET', leverage: 10, marginMode: 'ISOLATED',
    triggerPrice: '', stopPrice: '', trailingCallback: 0.5,
    isExit: false, reduceOnly: false, postOnly: false,
    timeInForce: 'GTC',
    twapDuration: 10, twapSlices: 10, twapRandomize: false
  };

  // ─── INIT ───────────────────────────────────────────────────────────
  async function init() {
    console.log("[PaperTrade] Initializing module...");
    renderPanel();
    await refresh();
    startPolling();

    document.addEventListener('keydown', (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;
      const overlay = document.getElementById('pt-modal-overlay');
      if (e.key === 'b' || e.key === 'B') {
        if (!overlay && window.App) openOrderModal(App.currentSymbol, null, App.currentSymbol, 'BUY');
      } else if (e.key === 's' || e.key === 'S') {
        if (!overlay && window.App) openOrderModal(App.currentSymbol, null, App.currentSymbol, 'SELL');
      } else if (e.key === 'Escape') {
        if (overlay) closeModal();
      }
    });
  }

  function startPolling() {
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(async () => {
      await refresh();
    }, 5000);
  }

  // ─── DATA FETCHING ──────────────────────────────────────────────────
  async function refresh() {
    try {
      if (mode === 'FUTURES') await refreshFutures();
      else await refreshSpot();
    } catch (e) { console.error('Refresh error:', e); }
  }

  async function refreshFutures() {
    const [acc, pos, ord, hist] = await Promise.all([
      fetch(`${FUTURES_BASE}/account`).then(r => r.ok ? r.json() : null),
      fetch(`${FUTURES_BASE}/positions`).then(r => r.ok ? r.json() : []),
      fetch(`${FUTURES_BASE}/orders`).then(r => r.ok ? r.json() : []),
      fetch(`${FUTURES_BASE}/history`).then(r => r.ok ? r.json() : []),
    ]);
    futuresState.account = acc;
    futuresState.positions = Array.isArray(pos) ? pos : Object.values(pos || {});
    futuresState.orders = ord;
    futuresState.history = hist;

    futuresState.positions.forEach(p => {
        if (priceCache[p.symbol]) {
            p.mark_price = priceCache[p.symbol];
            p.current_price = priceCache[p.symbol];
        }
    });
    updateTradingState();
    renderCurrentScreen(true);
  }

  async function refreshSpot() {
    const [acc, pos, ord, hist] = await Promise.all([
      fetch(`${SPOT_BASE}/account`).then(r => r.ok ? r.json() : null),
      fetch(`${SPOT_BASE}/positions`).then(r => r.ok ? r.json() : []),
      fetch(`${SPOT_BASE}/orders`).then(r => r.ok ? r.json() : []),
      fetch(`${SPOT_BASE}/history`).then(r => r.ok ? r.json() : []),
    ]);
    spotState.account = acc;
    spotState.positions = pos;
    spotState.orders = ord;
    spotState.history = hist;

    spotState.positions.forEach(p => {
        if (priceCache[p.symbol]) p.current_price = priceCache[p.symbol];
    });
    updateTradingState();
    renderCurrentScreen(true);
  }

  function updateTradingState() {
    if (mode === 'FUTURES') {
      const account = futuresState.account || {};
      const positions = futuresState.positions || [];
      const wallet = Number(account.wallet_balance || 0);
      const usedMargin = positions.reduce((sum, p) => sum + Number(p.isolated_margin || p.allocated_margin || 0), 0);
      const unrealizedPnL = Number(account.unrealized_pnl || positions.reduce((sum, p) => sum + Number(p.unrealized_pnl || 0), 0));
      tradingState = {
        totalBalance: wallet,
        availableBalance: Number(account.available_balance ?? Math.max(0, wallet - usedMargin)),
        usedMargin,
        openPositions: positions,
        orderHistory: futuresState.history || [],
        openOrders: futuresState.orders || [],
        realizedPnL: (futuresState.history || []).reduce((sum, t) => sum + Number(t.realized_pnl || 0), 0),
        unrealizedPnL,
        portfolioValue: Number(account.margin_balance || wallet + unrealizedPnL)
      };
    } else {
      const account = spotState.account || {};
      tradingState = {
        totalBalance: Number(account.portfolio_value || 0),
        availableBalance: Number(account.free_cash || 0),
        usedMargin: Number(account.invested_value || 0),
        openPositions: spotState.positions || [],
        orderHistory: spotState.history || [],
        openOrders: spotState.orders || [],
        realizedPnL: 0,
        unrealizedPnL: Number(account.total_pnl || 0),
        portfolioValue: Number(account.portfolio_value || 0)
      };
    }
    console.log('[TradingState] updated', tradingState);
    return tradingState;
  }

  // ─── RENDERING ──────────────────────────────────────────────────────
  function renderPanel() {
    const root = document.getElementById('pt-root');
    if (!root) return;
    
    const isFutures = mode === 'FUTURES';
    
    // Split into smaller parts to avoid deep nesting parser issues
    const navButtons = isFutures ? `
      <button class="pt-nav-btn" data-screen="funding" onclick="PaperTrade.switchScreen('funding')" style="flex-shrink:0; padding:8px 10px; background:transparent; border:none; color:var(--text-muted); font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">FUNDING</button>
      <button class="pt-nav-btn" data-screen="journal" onclick="PaperTrade.switchScreen('journal')" style="flex-shrink:0; padding:8px 10px; background:transparent; border:none; color:var(--text-muted); font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">JOURNAL</button>
    ` : '';

    root.innerHTML = `
      <div class="pt-container" style="display:flex; flex-direction:column; height:100%; overflow:hidden; background:#0b0e11;">
        <div style="padding:12px 16px; border-bottom:1px solid rgba(255,255,255,0.06); display:flex; justify-content:space-between; align-items:center;">
          <div style="display:flex; align-items:center; gap:8px;">
            <div style="width:8px; height:8px; background:#00E676; border-radius:50%; box-shadow:0 0 8px #00E676;"></div>
            <span style="font-size:12px; font-weight:800; color:white; letter-spacing:0.5px;">PAPER TRADING</span>
          </div>
          <button onclick="PaperTrade.resetAccount()" style="background:transparent; border:none; color:var(--text-muted); cursor:pointer;"><i class="fas fa-redo-alt"></i></button>
        </div>
        <div style="padding:8px 16px; display:flex; gap:4px; background:rgba(0,0,0,0.3);">
          <button onclick="PaperTrade.setMode('SPOT')" id="pt-mode-spot" style="flex:1; height:32px; border:none; border-radius:6px; font-size:11px; font-weight:800; cursor:pointer; background:${mode === 'SPOT' ? 'rgba(41,121,255,0.2)' : 'transparent'}; color:${mode === 'SPOT' ? '#2979FF' : 'var(--text-muted)'};">SPOT</button>
          <button onclick="PaperTrade.setMode('FUTURES')" id="pt-mode-futures" style="flex:1; height:32px; border:none; border-radius:6px; font-size:11px; font-weight:800; cursor:pointer; background:${mode === 'FUTURES' ? 'rgba(255,214,0,0.15)' : 'transparent'}; color:${mode === 'FUTURES' ? '#FFD600' : 'var(--text-muted)'};">FUTURES</button>
        </div>
        <div style="display:flex; background:rgba(0,0,0,0.2); padding:4px 8px; gap:2px;">
          <button class="pt-nav-btn active" data-screen="dashboard" onclick="PaperTrade.switchScreen('dashboard')" style="flex-shrink:0; padding:8px 10px; background:rgba(255,255,255,0.05); border:none; color:white; font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">DASH</button>
          <button class="pt-nav-btn" data-screen="positions" onclick="PaperTrade.switchScreen('positions')" style="flex-shrink:0; padding:8px 10px; background:transparent; border:none; color:var(--text-muted); font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">POSITIONS</button>
          <button class="pt-nav-btn" data-screen="orders" onclick="PaperTrade.switchScreen('orders')" style="flex-shrink:0; padding:8px 10px; background:transparent; border:none; color:var(--text-muted); font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">ORDERS</button>
          <button class="pt-nav-btn" data-screen="history" onclick="PaperTrade.switchScreen('history')" style="flex-shrink:0; padding:8px 10px; background:transparent; border:none; color:var(--text-muted); font-size:9px; font-weight:800; cursor:pointer; border-radius:6px;">HISTORY</button>
          ${navButtons}
        </div>
        <div id="pt-screen-container" style="flex:1; overflow-y:auto; padding:12px;"></div>
      </div>
    `;
    renderCurrentScreen(true);
    refresh();
  }

  function switchScreen(screen) {
    activeScreen = screen;
    document.querySelectorAll('.pt-nav-btn').forEach(b => {
      const isActive = b.dataset.screen === screen;
      b.classList.toggle('active', isActive);
      b.style.background = isActive ? 'rgba(255,255,255,0.05)' : 'transparent';
      b.style.color = isActive ? 'white' : 'var(--text-muted)';
    });
    renderCurrentScreen();
  }

  function setMode(newMode) {
    mode = newMode;
    renderPanel();
    refresh();
  }

  function renderCurrentScreen(force = false) {
    const container = document.getElementById('pt-screen-container');
    if (!container) return;
    updateTradingState();
    if (force || container.dataset.screen !== activeScreen || container.innerHTML === '' || container.innerHTML.includes('Loading...')) {
        container.dataset.screen = activeScreen;
        switch (activeScreen) {
          case 'dashboard': renderDashboard(container); break;
          case 'positions': renderPositions(container); break;
          case 'orders': renderOrders(container); break;
          case 'history': renderHistory(container); break;
          case 'funding': renderFunding(container); break;
          case 'journal': renderJournal(container); break;
        }
    } else {
        updateLiveValues();
    }
  }

  // ─── DASHBOARD ──────────────────────────────────────────────────────
  function renderDashboard(container) {
    if (mode === 'FUTURES') {
      const a = futuresState.account;
      if (!a) { container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">Loading...</div>'; return; }
      const equity = tradingState.portfolioValue;
      const upnl = tradingState.unrealizedPnL;
      const available = tradingState.availableBalance;
      const usedMargin = tradingState.usedMargin;
      const marginRatio = a.margin_ratio || 0;
      const mrColor = marginRatio < 50 ? '#00E676' : marginRatio < 80 ? '#FFD600' : '#FF1744';

      container.innerHTML = `
        <div style="display:flex; flex-direction:column; gap:12px;">
          <div style="padding:20px; background:linear-gradient(135deg, rgba(255,214,0,0.08), rgba(255,255,255,0.02)); border:1px solid rgba(255,214,0,0.15); border-radius:14px;">
            <div style="font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800; letter-spacing:1px;">Total Equity</div>
            <div id="pt-equity" style="font-size:28px; font-weight:800; color:white; margin:6px 0;">${cur()}${formatNum(equity)}</div>
            <div style="display:flex; gap:16px; margin-top:8px;">
              <div><span style="font-size:9px; color:var(--text-muted);">UPnL</span><div id="pt-upnl" style="font-size:14px; font-weight:800; color:${upnl >= 0 ? '#00E676' : '#FF1744'};">${upnl >= 0 ? '+' : ''}${cur()}${formatNum(upnl)}</div></div>
              <div><span style="font-size:9px; color:var(--text-muted);">AVAILABLE</span><div id="pt-available" style="font-size:14px; font-weight:800; color:#00E676;">${cur()}${formatNum(available)}</div></div>
            </div>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
            <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
              <div style="font-size:9px; color:var(--text-muted); font-weight:800;">MARGIN RATIO</div>
              <div id="pt-mr-val" style="font-size:18px; font-weight:800; color:${mrColor}; margin-top:4px;">${marginRatio.toFixed(1)}%</div>
              <div style="height:3px; background:rgba(255,255,255,0.05); border-radius:2px; margin-top:6px; overflow:hidden;">
                <div id="pt-mr-bar" style="width:${Math.min(marginRatio, 100)}%; height:100%; background:${mrColor}; border-radius:2px; transition:width 0.3s;"></div>
              </div>
            </div>
            <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
              <div style="font-size:9px; color:var(--text-muted); font-weight:800;">POSITIONS</div>
              <div style="font-size:18px; font-weight:800; color:white; margin-top:4px;">${futuresState.positions.length}</div>
            </div>
            <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
              <div style="font-size:9px; color:var(--text-muted); font-weight:800;">USED MARGIN</div>
              <div id="pt-used-margin" style="font-size:18px; font-weight:800; color:#FFD600; margin-top:4px;">${cur()}${formatNum(usedMargin)}</div>
            </div>
          </div>
          <button onclick="PaperTrade.openOrderModal(window.App?.currentSymbol || 'BTCUSDT', null, window.App?.currentSymbol || 'BTCUSDT', 'BUY')" style="width:100%; height:44px; background:linear-gradient(135deg, #1B5E20, #26a69a); border:none; border-radius:12px; color:white; font-weight:800; font-size:12px; cursor:pointer;">💹 QUICK TRADE</button>
          <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
            <div style="font-size:9px; color:var(--text-muted); font-weight:800; margin-bottom:8px;">OPEN POSITION SUMMARY</div>
            ${renderPositionSummary()}
          </div>
        </div>
      `;
    } else {
      const a = spotState.account;
      if (!a) { container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">Loading...</div>'; return; }
      container.innerHTML = `
        <div style="display:flex; flex-direction:column; gap:12px;">
          <div style="padding:20px; background:linear-gradient(135deg, rgba(41,121,255,0.08), rgba(255,255,255,0.02)); border:1px solid rgba(41,121,255,0.15); border-radius:14px;">
            <div style="font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800; letter-spacing:1px;">Portfolio Value</div>
            <div id="pt-equity-spot" style="font-size:28px; font-weight:800; color:white; margin:6px 0;">${cur()}${formatNum(a.portfolio_value)}</div>
            <div id="pt-total-pnl-spot" style="font-size:13px; font-weight:800; color:${a.total_pnl >= 0 ? '#00E676' : '#FF1744'};">${a.total_pnl >= 0 ? '+' : ''}${cur()}${formatNum(a.total_pnl)} (${a.total_pnl_pct.toFixed(2)}%)</div>
          </div>
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px;">
            <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
              <div style="font-size:9px; color:var(--text-muted); font-weight:800;">INVESTED</div>
              <div id="pt-invested-spot" style="font-size:16px; font-weight:800; color:white; margin-top:4px;">${cur()}${formatNum(a.invested_value)}</div>
            </div>
            <div style="padding:12px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
              <div style="font-size:9px; color:var(--text-muted); font-weight:800;">AVAILABLE</div>
              <div id="pt-available-spot" style="font-size:16px; font-weight:800; color:#00E676; margin-top:4px;">${cur()}${formatNum(a.free_cash)}</div>
            </div>
          </div>
          <div style="text-align:center; padding:12px; color:var(--text-muted); font-size:11px;">Order history is available in the History tab.</div>
        </div>
      `;
    }
  }

  function renderPositionSummary() {
    if (!tradingState.openPositions.length) return '<div style="color:var(--text-muted); font-size:11px;">No open positions</div>';
    return tradingState.openPositions.map(p => {
      const upnl = Number(p.unrealized_pnl || 0);
      return `<div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-top:1px solid rgba(255,255,255,0.04);">
        <span style="font-size:11px; color:white; font-weight:800;">${p.symbol} ${p.side || ''}</span>
        <span style="font-size:11px; color:${upnl >= 0 ? '#00E676' : '#FF1744'}; font-weight:800;">${upnl >= 0 ? '+' : ''}${cur()}${formatNum(upnl)}</span>
      </div>`;
    }).join('');
  }

  function renderRecentTrades() {
    const trades = mode === 'FUTURES' ? futuresState.history.slice(0, 5) : spotState.history.slice(0, 5);
    if (!trades.length) return '<div style="text-align:center; padding:20px; color:var(--text-muted); font-size:11px;">No trades yet</div>';
    return trades.map(t => {
      const pnl = parseFloat(t.realized_pnl || t.pnl || 0);
      const isProfit = pnl > 0;
      return `<div style="padding:10px; margin-bottom:6px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.04); border-radius:8px; display:flex; justify-content:space-between; align-items:center;">
        <div>
          <div style="font-weight:800; font-size:12px; color:white;">${t.symbol} <span style="font-size:9px; color:${t.side === 'BUY' ? '#00E676' : '#FF1744'};">${t.side}</span></div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:13px; font-weight:800; color:${isProfit ? '#00E676' : '#FF1744'};">${isProfit ? '+' : ''}${cur()}${formatNum(pnl)}</div>
        </div>
      </div>`;
    }).join('');
  }

  // ─── POSITIONS ──────────────────────────────────────────────────────
  function renderPositions(container) {
    const positions = tradingState.openPositions;
    console.log('[PaperTrade] rendering positions', positions.length);
    if (!positions.length) {
      container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">No open positions</div>';
      return;
    }
    
    let html = `<button onclick="PaperTrade.closeAllPositions()" style="width:100%; height:32px; margin-bottom:12px; background:rgba(242, 54, 69, 0.15); color:#f23645; border:1px solid rgba(242, 54, 69, 0.3); border-radius:8px; font-size:10px; font-weight:800; cursor:pointer;">🔥 EXIT ALL POSITIONS</button>`;
    
    positions.forEach(p => {
        const upnl = parseFloat(p.unrealized_pnl || 0);
        const upnlPct = parseFloat(p.unrealized_pnl_pct || 0);
        const isProfit = upnl > 0;
        const pnlColor = isProfit ? '#00E676' : (upnl < 0 ? '#FF1744' : 'var(--text-primary)');
        const entry = parseFloat(p.avg_price || p.entry_price || 0);
        const mark = parseFloat(p.mark_price || p.current_price || entry);

        if (mode === 'FUTURES') {
            html += `
              <div style="padding:14px; margin-bottom:8px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px; border-left:3px solid ${p.side === 'LONG' ? '#00E676' : '#FF1744'};">
                <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                  <div>
                    <div style="font-weight:800; font-size:14px; color:white;">${p.symbol}</div>
                    <div style="font-size:9px; font-weight:800; color:var(--text-muted);">${p.side} ${p.leverage}x</div>
                  </div>
                  <div style="text-align:right;" id="pos-upnl-${p.symbol}-${p.side}">
                    <div style="font-size:16px; font-weight:800; color:${pnlColor};">${isProfit ? '+' : ''}${cur()}${formatNum(upnl)}</div>
                    <div style="font-size:10px; color:var(--text-muted);">${upnlPct.toFixed(2)}%</div>
                  </div>
                </div>
                <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; font-size:10px;">
                  <div><span style="color:var(--text-muted);">Entry</span> <span style="color:white; font-weight:700;">${cur()}${formatNum(entry)}</span></div>
                  <div id="pos-mark-${p.symbol}-${p.side}"><span style="color:var(--text-muted);">Mark</span> <span style="color:white; font-weight:700;">${cur()}${formatNum(mark)}</span></div>
                </div>
                <div style="display:flex; gap:6px; margin-top:10px;">
                  <button onclick="PaperTrade.closePosition('${p.symbol}', '${p.side}', 100)" style="flex:1; height:28px; background:rgba(255,23,68,0.15); color:#FF1744; border:1px solid rgba(255,23,68,0.3); border-radius:6px; font-size:9px; font-weight:800; cursor:pointer;">CLOSE</button>
                </div>
              </div>
            `;
        } else {
            html += `
              <div style="padding:14px; margin-bottom:8px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px; border-left:3px solid ${isProfit ? '#00E676' : '#FF1744'};">
                <div style="display:flex; justify-content:space-between;">
                  <div style="font-weight:800; font-size:14px; color:white;">${p.symbol}</div>
                  <div style="text-align:right;" id="pos-upnl-${p.symbol}-${p.side}">
                    <div style="font-size:16px; font-weight:800; color:${pnlColor};">${isProfit ? '+' : ''}${cur()}${formatNum(upnl)}</div>
                  </div>
                </div>
                <div id="pos-mark-${p.symbol}-${p.side}" style="margin-top:8px; font-size:10px; color:var(--text-muted);">Avg: ${cur()}${formatNum(entry)} | LTP: ${cur()}${formatNum(mark)}</div>
                <button onclick="PaperTrade.openOrderModal('${p.symbol}', ${mark}, '${p.symbol}', 'SELL', true)" style="width:100%; height:30px; margin-top:10px; background:rgba(255,255,255,0.05); color:white; border:1px solid rgba(255,255,255,0.1); border-radius:6px; font-size:10px; font-weight:800; cursor:pointer;">EXIT</button>
              </div>
            `;
        }
    });
    container.innerHTML = html;
  }

  function renderOrders(container) {
    const orders = tradingState.openOrders || [];
    if (!orders.length) {
      container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">No open orders</div>';
      return;
    }
    container.innerHTML = orders.map(o => `<div style="padding:12px; margin-bottom:8px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
      <div style="display:flex; justify-content:space-between; gap:8px;">
        <div style="font-size:12px; font-weight:800; color:white;">${o.symbol}</div>
        <div style="font-size:10px; font-weight:800; color:${o.side === 'BUY' ? '#00E676' : '#FF1744'};">${o.side} ${o.status || ''}</div>
      </div>
      <div style="margin-top:6px; font-size:10px; color:var(--text-muted);">Qty ${formatNum(o.quantity, 6)} | ${o.order_type || o.type || 'ORDER'} | Price ${o.price ? cur() + formatNum(o.price) : 'MARKET'}</div>
    </div>`).join('');
  }

  function renderHistory(container) {
    const history = tradingState.orderHistory || [];
    if (!history.length) {
      container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">No history</div>';
      return;
    }
    console.log('[PaperTrade] history updated', history.length);
    container.innerHTML = history.map(t => {
      const pnl = Number(t.realized_pnl || t.pnl || 0);
      const fee = Number(t.fee || 0);
      const ts = t.timestamp ? new Date(t.timestamp).toLocaleString() : '';
      return `<div style="padding:12px; margin-bottom:8px; background:rgba(255,255,255,0.02); border:1px solid rgba(255,255,255,0.05); border-radius:10px;">
        <div style="display:flex; justify-content:space-between; gap:8px;">
          <div>
            <div style="font-size:12px; font-weight:800; color:white;">${t.symbol}</div>
            <div style="font-size:9px; color:var(--text-muted);">${ts}</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:10px; font-weight:800; color:${t.side === 'BUY' ? '#00E676' : '#FF1744'};">${t.side}</div>
            <div style="font-size:12px; font-weight:800; color:${pnl >= 0 ? '#00E676' : '#FF1744'};">${pnl >= 0 ? '+' : ''}${cur()}${formatNum(pnl)}</div>
          </div>
        </div>
        <div style="margin-top:6px; font-size:10px; color:var(--text-muted);">Qty ${formatNum(t.quantity, 6)} | Fill ${cur()}${formatNum(t.price)} | Fee ${cur()}${formatNum(fee)}</div>
      </div>`;
    }).join('');
  }
  function renderFunding(container) { container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">No funding data</div>'; }
  function renderJournal(container) { container.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted);">No entries</div>'; }

  // ─── ORDER MODAL ────────────────────────────────────────────────────
  async function openOrderModal(symbol, price, name, side = 'BUY', isExit = false) {
    if (!symbol) return;
    
    // Set form state
    orderForm.symbol = symbol;
    orderForm.name = name || symbol;
    orderForm.side = side;
    orderForm.isExit = isExit;
    orderForm.price = price || priceCache[symbol] || 0;
    orderForm.qty = (symbol.includes('.NS') || symbol.includes('.BO') || symbol.startsWith('^')) ? 1 : 0.001;
    orderForm.type = 'MARKET';

    const overlay = document.createElement('div');
    overlay.id = 'pt-modal-overlay';
    overlay.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.85); backdrop-filter:blur(10px); z-index:100000; display:flex; align-items:center; justify-content:center;';
    
    overlay.innerHTML = `
      <div style="width:380px; padding:24px; border-radius:16px; background:#1e222d; border:1px solid rgba(255,255,255,0.1);" id="pt-modal-inner">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
          <div style="font-weight:800; font-size:20px; color:white;">${symbol}</div>
          <button onclick="PaperTrade.closeModal()" style="background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:18px;">✕</button>
        </div>
        
        <div style="display:flex; gap:4px; margin-bottom:15px; background:rgba(0,0,0,0.3); padding:4px; border-radius:10px;">
          <button onclick="PaperTrade.setSide('BUY')" id="pt-btn-buy" style="flex:1; height:38px; border:none; border-radius:8px; font-weight:800; background:${side === 'BUY' ? '#00E676' : 'transparent'}; color:${side === 'BUY' ? '#0b0e11' : 'var(--text-muted)'};">${mode === 'FUTURES' ? 'LONG' : 'BUY'}</button>
          <button onclick="PaperTrade.setSide('SELL')" id="pt-btn-sell" style="flex:1; height:38px; border:none; border-radius:8px; font-weight:800; background:${side === 'SELL' ? '#FF1744' : 'transparent'}; color:${side === 'SELL' ? '#0b0e11' : 'var(--text-muted)'};">${mode === 'FUTURES' ? 'SHORT' : 'SELL'}</button>
        </div>

        <div style="margin-bottom:15px;">
          <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
            <label style="font-size:10px; font-weight:800; color:var(--text-muted);">QUANTITY</label>
            <div style="font-size:10px; font-weight:800; color:#00E676;" id="pt-avail-header">Available: $0.00</div>
          </div>
          <input type="number" id="pt-qty-input" value="${orderForm.qty}" oninput="PaperTrade.setQty(this.value)" style="width:100%; height:44px; text-align:center; background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.1); color:white; font-size:18px; font-weight:800; border-radius:10px; outline:none;">
        </div>

        <div id="pt-margin-info" style="margin-bottom:20px; padding:15px; background:rgba(0,0,0,0.2); border-radius:12px;">
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:10px;">
            <div>
              <div style="font-size:8px; color:var(--text-muted); font-weight:800;">REQUIRED</div>
              <div id="pt-req-margin" style="font-size:14px; font-weight:800; color:white;">$0.00</div>
            </div>
            <div>
              <div style="font-size:8px; color:var(--text-muted); font-weight:800;">REMAINING</div>
              <div id="pt-rem-margin" style="font-size:14px; font-weight:800; color:#00E676;">$0.00</div>
            </div>
          </div>
        </div>

        <button onclick="PaperTrade.submitOrder()" id="pt-submit-btn" style="width:100%; height:50px; border:none; border-radius:12px; font-size:16px; font-weight:800; cursor:pointer; color:#0b0e11; background:linear-gradient(135deg, ${side === 'BUY' ? '#00E676, #00897B' : '#FF1744, #c62828'});">CONFIRM ORDER</button>
      </div>
    `;
    document.body.appendChild(overlay);
    updateMarginRequirement();
  }

  function closeModal() {
    const overlay = document.getElementById('pt-modal-overlay');
    if (overlay) overlay.remove();
  }

  // ─── STATE SETTERS ──────────────────────────────────────────────────
  function setSide(side) {
    orderForm.side = side;
    const buyBtn = document.getElementById('pt-btn-buy');
    const sellBtn = document.getElementById('pt-btn-sell');
    const submitBtn = document.getElementById('pt-submit-btn');
    if (buyBtn) { buyBtn.style.background = side === 'BUY' ? '#00E676' : 'transparent'; buyBtn.style.color = side === 'BUY' ? '#0b0e11' : 'var(--text-muted)'; }
    if (sellBtn) { sellBtn.style.background = side === 'SELL' ? '#FF1744' : 'transparent'; sellBtn.style.color = side === 'SELL' ? '#0b0e11' : 'var(--text-muted)'; }
    if (submitBtn) submitBtn.style.background = `linear-gradient(135deg, ${side === 'BUY' ? '#00E676, #00897B' : '#FF1744, #c62828'})`;
    updateMarginRequirement();
  }

  function setQty(val) { orderForm.qty = parseFloat(val) || 0; updateMarginRequirement(); }

  function updateMarginRequirement() {
    const reqEl = document.getElementById('pt-req-margin');
    const remEl = document.getElementById('pt-rem-margin');
    const availHeader = document.getElementById('pt-avail-header');
    if (!reqEl || !remEl) return;

    const price = priceCache[orderForm.symbol] || orderForm.price || 0;
    const notional = price * orderForm.qty;
    let req = 0; let available = 0;

    if (mode === 'FUTURES') {
      req = notional / (orderForm.leverage || 10);
      available = tradingState.availableBalance;
    } else {
      req = orderForm.side === 'BUY' ? notional : 0;
      available = parseFloat(spotState.account?.free_cash || 0);
    }

    const remaining = available - req;
    console.log('[PaperTrade] margin required', { symbol: orderForm.symbol, notional, requiredMargin: req, available });
    reqEl.innerText = `${cur()}${formatNum(req)}`;
    remEl.innerText = `${cur()}${formatNum(remaining)}`;
    remEl.style.color = remaining < 0 ? '#FF1744' : '#00E676';
    if (availHeader) availHeader.innerText = `Available: ${cur()}${formatNum(available)}`;
  }

  // ─── ACTIONS ────────────────────────────────────────────────────────
  async function submitOrder() {
    try {
      const base = mode === 'FUTURES' ? FUTURES_BASE : SPOT_BASE;
      const payload = {
        symbol: orderForm.symbol,
        side: orderForm.side,
        order_type: 'MARKET',
        quantity: orderForm.qty,
        leverage: orderForm.leverage,
        current_price: orderForm.price
      };
      console.log('[PaperTrade] order placed request', { payload, available: tradingState.availableBalance });
      const res = await fetch(`${base}/order`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }).then(r => r.json());
      if (res.success) {
        console.log('[PaperTrade] order placed response', res);
        showToast('Order placed', 'success');
        closeModal();
        await refresh();
      } else {
        console.warn('[PaperTrade] order rejected', res);
        showError(res.error || res.message || 'Order rejected');
      }
    } catch (e) { showError('Network error'); }
  }

  async function closePosition(symbol, side, pct = 100) {
      try {
        await fetch(`${FUTURES_BASE}/order/close`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol, position_side: side, pct }) });
        showToast('Position closed', 'success');
        await refresh();
      } catch (e) { showError('Close failed'); }
  }

  async function closeAllPositions() {
      const positions = mode === 'FUTURES' ? futuresState.positions : spotState.positions;
      if (!positions.length || !confirm('Close ALL?')) return;
      for (const p of positions) {
          if (mode === 'FUTURES') await closePosition(p.symbol, p.side, 100);
          else await fetch(`${SPOT_BASE}/order`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol: p.symbol, side: 'SELL', quantity: p.quantity, order_type: 'MARKET', current_price: p.current_price }) });
      }
      showToast('All closed', 'success');
      await refresh();
  }

  async function resetAccount() {
      if (!confirm('Reset Account?')) return;
      const base = mode === 'FUTURES' ? FUTURES_BASE : SPOT_BASE;
      const resp = await fetch(`${base}/reset`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || data.error || data.success === false) {
          showError(data.error || 'Reset failed');
          return;
      }
      if (mode === 'FUTURES') {
          futuresState = { account: null, positions: [], orders: [], history: [], fundingHistory: [], journal: [], patterns: [], markPrices: {} };
      } else {
          spotState = { account: null, positions: [], orders: [], history: [], performance: null };
      }
      renderPanel();
      await refresh();
      showToast('Account reset', 'success');
  }

  // ─── HELPERS ────────────────────────────────────────────────────────
  function cur() { return mode === 'FUTURES' ? '$' : '₹'; }
  function formatNum(n, digits = 2) { return Number(n || 0).toLocaleString(mode === 'FUTURES' ? 'en-US' : 'en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
  function showToast(msg, type) { if (window.showNotification) window.showNotification(msg, type); }
  function showError(msg) { if (window.showError) window.showError(msg); }

  function updateLiveValues() {
    if (mode === 'FUTURES' && futuresState.account) {
        let totalUpnl = 0; futuresState.positions.forEach(p => totalUpnl += parseFloat(p.unrealized_pnl || 0));
        const equity = parseFloat(futuresState.account.wallet_balance || 0) + totalUpnl;
        const eqEl = document.getElementById('pt-equity');
        if (eqEl) eqEl.innerText = `${cur()}${formatNum(equity)}`;
        const upnlEl = document.getElementById('pt-upnl');
        if (upnlEl) {
            upnlEl.innerText = `${totalUpnl >= 0 ? '+' : ''}${cur()}${formatNum(totalUpnl)}`;
            upnlEl.style.color = totalUpnl >= 0 ? '#00E676' : '#FF1744';
        }
        const availableEl = document.getElementById('pt-available');
        if (availableEl) availableEl.innerText = `${cur()}${formatNum(tradingState.availableBalance)}`;
        const usedEl = document.getElementById('pt-used-margin');
        if (usedEl) usedEl.innerText = `${cur()}${formatNum(tradingState.usedMargin)}`;
    } else if (mode === 'SPOT' && spotState.account) {
        const eqEl = document.getElementById('pt-equity-spot');
        if (eqEl) eqEl.innerText = `${cur()}${formatNum(spotState.account.portfolio_value)}`;
    }

    const positions = mode === 'FUTURES' ? futuresState.positions : spotState.positions;
    positions.forEach(p => {
        const side = p.side || '';
        const upnl = parseFloat(p.unrealized_pnl || 0);
        const qty = parseFloat(p.quantity || 0);
        const entry = parseFloat(p.avg_price || p.entry_price || 0);
        const mark = parseFloat(p.mark_price || p.current_price || entry);
        const pct = entry > 0 && qty > 0 ? (upnl / (entry * qty)) * 100 : parseFloat(p.unrealized_pnl_pct || 0);
        const color = upnl > 0 ? '#00E676' : (upnl < 0 ? '#FF1744' : 'var(--text-primary)');
        const upnlEl = document.getElementById(`pos-upnl-${p.symbol}-${side}`);
        if (upnlEl) {
            upnlEl.innerHTML = `<div style="font-size:16px; font-weight:800; color:${color};">${upnl > 0 ? '+' : ''}${cur()}${formatNum(upnl)}</div>${mode === 'FUTURES' ? `<div style="font-size:10px; color:var(--text-muted);">${pct.toFixed(2)}%</div>` : ''}`;
        }
        const markEl = document.getElementById(`pos-mark-${p.symbol}-${side}`);
        if (markEl) {
            if (mode === 'FUTURES') {
                markEl.innerHTML = `<span style="color:var(--text-muted);">Mark</span> <span style="color:white; font-weight:700;">${cur()}${formatNum(mark)}</span>`;
            } else {
                markEl.innerHTML = `Avg: ${cur()}${formatNum(entry)} | LTP: ${cur()}${formatNum(mark)}`;
            }
        }
    });
  }

  function updateLiveTick(tick) {
      if (!tick) return;
      priceCache[tick.symbol] = tick.price;
      const positions = mode === 'FUTURES' ? futuresState.positions : spotState.positions;
      positions.forEach(p => {
          if (p.symbol === tick.symbol) {
              p.current_price = tick.price;
              if (mode === 'FUTURES') {
                  const isLong = p.side === 'LONG';
                  p.unrealized_pnl = isLong ? (tick.price - p.entry_price) * p.quantity : (p.entry_price - tick.price) * p.quantity;
                  p.mark_price = tick.price;
              } else {
                  p.unrealized_pnl = (tick.price - (p.avg_price || p.entry_price)) * p.quantity;
              }
          }
      });
      updateLiveValues();
  }

  return {
    init, setMode, switchScreen, openOrderModal, closeModal, setSide, setQty,
    submitOrder, closePosition, closeAllPositions, resetAccount, updateLiveTick, updateMarginRequirement
  };
})();

window.PaperTrade = PaperTrade;
