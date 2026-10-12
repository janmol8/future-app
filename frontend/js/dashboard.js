/**
 * Dashboard Module - Executive Analytics and Insights
 * Features: paper account summary, open positions, market strip, live IPOs, symbol trend / S&R,
 * plus the crypto-futures account widgets (collapsed).
 */
const Dashboard = (() => {
  let currentSymbol = '';
  let currentData = null;
  const BASE = '/api/futures';
  let fundingCountdownInterval = null;
  let nextFundingTime = 0;
  let lastAccountData = null;
  let lastPositionsData = [];

  function init() {
    console.log("Dashboard Module Initialized");
    startPolling();
    startFundingCountdown();
  }

  function startPolling() {
    setInterval(async () => {
      await refreshAccountMetrics();
    }, 2000);
  }

  function startFundingCountdown() {
    if (fundingCountdownInterval) clearInterval(fundingCountdownInterval);
    fundingCountdownInterval = setInterval(() => {
      updateFundingCountdown();
    }, 1000);
  }

  function updateFundingCountdown() {
    const el = document.getElementById('funding-countdown');
    if (!el || nextFundingTime <= 0) return;

    const now = Date.now();
    const diff = nextFundingTime - now;

    if (diff <= 0) {
      el.textContent = 'SETTLING...';
      el.style.color = 'var(--accent-gold)';
      return;
    }

    const hours = Math.floor(diff / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    const seconds = Math.floor((diff % 60000) / 1000);

    el.textContent = `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;

    // Color based on proximity
    if (diff < 300000) { // < 5 min
      el.style.color = 'var(--accent-gold)';
      el.style.textShadow = '0 0 8px rgba(255,215,0,0.4)';
    } else if (diff < 1800000) { // < 30 min
      el.style.color = 'var(--accent-blue)';
      el.style.textShadow = 'none';
    } else {
      el.style.color = 'var(--text-secondary)';
      el.style.textShadow = 'none';
    }
  }

  function calculateNextFundingTime() {
    // Binance futures funding: 00:00, 08:00, 16:00 UTC
    const now = new Date();
    const utcHours = now.getUTCHours();
    const utcMinutes = now.getUTCMinutes();
    const utcSeconds = now.getUTCSeconds();

    const fundingHours = [0, 8, 16];
    let nextHour = fundingHours.find(h => h > utcHours);
    if (nextHour === undefined) nextHour = fundingHours[0]; // wraps to next day

    const next = new Date(Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      nextHour <= utcHours ? now.getUTCDate() + 1 : now.getUTCDate(),
      nextHour, 0, 0
    ));

    return next.getTime();
  }

  async function refreshAccountMetrics() {
    try {
      const [accountResp, positionsResp] = await Promise.allSettled([
        fetch(`${BASE}/account`).then(r => r.json()),
        fetch(`${BASE}/positions`).then(r => r.json())
      ]);

      const account = accountResp.status === 'fulfilled' ? accountResp.value : null;
      const positions = positionsResp.status === 'fulfilled' ? positionsResp.value : [];

      if (account) {
        lastAccountData = account;
        renderAccountHealth(account, positions);
        renderLiquidationAlert(account, positions);
      }

      lastPositionsData = Array.isArray(positions) ? positions : [];

      // Fetch funding rate for current symbol
      if (currentSymbol) {
        const markResp = await fetch(`${BASE}/mark-price/${currentSymbol}`).then(r => r.json()).catch(() => null);
        if (markResp && !markResp.error) {
          if (markResp.next_funding_time) {
            nextFundingTime = markResp.next_funding_time;
          } else {
            nextFundingTime = calculateNextFundingTime();
          }
          renderFundingWidget(markResp);
          renderMarketSentiment(markResp);
        }
      }
    } catch (e) {
      console.warn('Dashboard refresh error:', e);
    }
  }

  // ── Liquidation Proximity Alert ──────────────────────────────────────────
  function renderLiquidationAlert(account, positions) {
    const existing = document.getElementById('liq-alert-banner');
    if (existing) existing.remove();

    if (!Array.isArray(positions) || positions.length === 0) return;

    // Find positions at risk (MR > 70%)
    const atRisk = positions.filter(p => {
      const mr = parseFloat(p.margin_ratio || 0);
      return mr > 70;
    });

    if (atRisk.length === 0) return;

    // Find the most at-risk position
    const worst = atRisk.reduce((a, b) => parseFloat(a.margin_ratio || 0) > parseFloat(b.margin_ratio || 0) ? a : b);
    const worstMr = parseFloat(worst.margin_ratio || 0);
    const isCritical = worstMr >= 90;

    const container = document.getElementById('account-health-container');
    if (!container) return;

    const banner = document.createElement('div');
    banner.id = 'liq-alert-banner';
    banner.style.cssText = `
      background: ${isCritical
        ? 'linear-gradient(135deg, rgba(255,23,68,0.15), rgba(255,23,68,0.05))'
        : 'linear-gradient(135deg, rgba(255,152,0,0.15), rgba(255,152,0,0.05))'};
      border: 1px solid ${isCritical ? 'rgba(255,23,68,0.4)' : 'rgba(255,152,0,0.4)'};
      border-radius: 12px;
      padding: 12px 16px;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      gap: 12px;
      animation: staggerFadeIn 0.3s ease forwards;
    `;

    const icon = isCritical ? '🚨' : '⚠️';
    const color = isCritical ? 'var(--accent-red)' : '#ff9800';
    const bgColor = isCritical ? 'rgba(255,23,68,0.1)' : 'rgba(255,152,0,0.1)';

    banner.innerHTML = `
      <div style="font-size: 24px; flex-shrink: 0;">${icon}</div>
      <div style="flex: 1;">
        <div style="font-size: 11px; font-weight: 800; color: ${color}; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">
          LIQUIDATION ${isCritical ? 'CRITICAL' : 'WARNING'}
        </div>
        <div style="font-size: 12px; color: var(--text-primary); line-height: 1.4;">
          ${atRisk.length} position${atRisk.length > 1 ? 's' : ''} at risk.
          Worst: <strong style="color: ${color}">${worst.symbol}</strong>
          MR: <strong style="color: ${color}">${worstMr.toFixed(1)}%</strong>
          ${worst.liquidation_price ? `• Liq: $${parseFloat(worst.liquidation_price).toLocaleString()}` : ''}
        </div>
      </div>
      <div style="text-align: right; flex-shrink: 0;">
        <div style="font-size: 18px; font-weight: 800; color: ${color};">${worstMr.toFixed(1)}%</div>
        <div style="font-size: 9px; color: var(--text-muted);">MARGIN RATIO</div>
      </div>
    `;

    container.insertBefore(banner, container.firstChild);
  }

  // ── Account Health Widget ────────────────────────────────────────────────
  function renderAccountHealth(m, positions) {
    const container = document.getElementById('account-health-container');
    if (!container) return;

    const balance = parseFloat(m.wallet_balance || m.balance || 0);
    const equity = parseFloat(m.total_equity || m.equity || balance);
    const marginRatio = parseFloat(m.margin_ratio || 0);
    const totalUpnl = parseFloat(m.total_unrealized_pnl || 0);
    const available = parseFloat(m.available_balance || 0);
    const positionsCount = Array.isArray(positions) ? positions.length : (m.positions_count || 0);

    // Margin ratio color
    let healthColor, healthLabel, healthGlow;
    if (marginRatio < 30) {
      healthColor = 'var(--accent-green)';
      healthLabel = 'HEALTHY';
      healthGlow = 'rgba(0,230,118,0.15)';
    } else if (marginRatio < 60) {
      healthColor = '#ff9800';
      healthLabel = 'MODERATE';
      healthGlow = 'rgba(255,152,0,0.15)';
    } else if (marginRatio < 80) {
      healthColor = 'var(--accent-red)';
      healthLabel = 'HIGH RISK';
      healthGlow = 'rgba(255,23,68,0.15)';
    } else {
      healthColor = '#ff1744';
      healthLabel = 'CRITICAL';
      healthGlow = 'rgba(255,23,68,0.3)';
    }

    // Circular gauge angle (0-270 degrees)
    const gaugeAngle = Math.min(marginRatio, 100) * 2.7;

    container.innerHTML = `
      <!-- Account Health Grid -->
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px;">
        <!-- Margin Ratio Gauge -->
        <div class="analytics-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); padding: 16px; border-radius: 12px; display: flex; flex-direction: column; align-items: center;">
          <div style="font-size: 9px; color: var(--text-muted); text-transform: uppercase; font-weight: 800; margin-bottom: 12px; letter-spacing: 0.5px;">Account Health</div>
          <div style="position: relative; width: 80px; height: 80px; margin-bottom: 8px;">
            <!-- Background ring -->
            <svg width="80" height="80" viewBox="0 0 80 80" style="transform: rotate(-135deg);">
              <circle cx="40" cy="40" r="34" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="6" stroke-dasharray="161" stroke-dashoffset="0" stroke-linecap="round"/>
              <circle cx="40" cy="40" r="34" fill="none" stroke="${healthColor}" stroke-width="6" stroke-dasharray="${gaugeAngle * 1.79} 161" stroke-dashoffset="0" stroke-linecap="round" style="filter: drop-shadow(0 0 6px ${healthGlow}); transition: stroke-dasharray 0.5s ease, stroke 0.3s ease;"/>
            </svg>
            <div style="position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center;">
              <div style="font-size: 18px; font-weight: 800; color: ${healthColor}; line-height: 1;">${marginRatio.toFixed(1)}</div>
              <div style="font-size: 8px; color: var(--text-muted); font-weight: 600;">% MR</div>
            </div>
          </div>
          <div style="font-size: 9px; font-weight: 700; color: ${healthColor}; text-transform: uppercase; letter-spacing: 1px;">${healthLabel}</div>
        </div>

        <!-- Balance & Equity -->
        <div class="analytics-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); padding: 16px; border-radius: 12px;">
          <div style="font-size: 9px; color: var(--text-muted); text-transform: uppercase; font-weight: 800; margin-bottom: 12px; letter-spacing: 0.5px;">Portfolio</div>

          <div style="margin-bottom: 10px;">
            <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 2px;">Total Equity</div>
            <div style="font-size: 16px; font-weight: 800; color: white;">$${formatCompact(equity)}</div>
          </div>

          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px;">
            <div>
              <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 2px;">Wallet</div>
              <div style="font-size: 12px; font-weight: 700; color: var(--text-secondary);">$${formatCompact(balance)}</div>
            </div>
            <div>
              <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 2px;">UPnL</div>
              <div style="font-size: 12px; font-weight: 700; color: ${totalUpnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'};">
                ${totalUpnl >= 0 ? '+' : ''}$${formatCompact(totalUpnl)}
              </div>
            </div>
          </div>

          <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.05);">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-size: 9px; color: var(--text-muted);">Available</span>
              <span style="font-size: 11px; font-weight: 700; color: var(--accent-blue);">$${formatCompact(available)}</span>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 4px;">
              <span style="font-size: 9px; color: var(--text-muted);">Positions</span>
              <span style="font-size: 11px; font-weight: 700; color: white;">${positionsCount}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Risk Exposure Bar Chart -->
      ${renderRiskExposureChart(positions)}
    `;
  }

  // ── Risk Exposure Bar Chart ──────────────────────────────────────────────
  function renderRiskExposureChart(positions) {
    if (!Array.isArray(positions) || positions.length === 0) return '';

    // Calculate notional exposure per symbol
    const exposureMap = {};
    let totalExposure = 0;

    positions.forEach(p => {
      const symbol = p.symbol || 'Unknown';
      const qty = parseFloat(p.quantity || 0);
      const price = parseFloat(p.entry_price || p.mark_price || 0);
      const notional = qty * price;
      exposureMap[symbol] = (exposureMap[symbol] || 0) + notional;
      totalExposure += notional;
    });

    if (totalExposure === 0) return '';

    // Sort by exposure descending
    const sorted = Object.entries(exposureMap).sort((a, b) => b[1] - a[1]);
    const maxExposure = sorted[0][1];

    const colors = ['var(--accent-blue)', 'var(--accent-green)', 'var(--accent-gold)', '#e040fb', 'var(--accent-red)', '#00bcd4'];

    return `
      <div class="analytics-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); padding: 16px; border-radius: 12px; margin-bottom: 16px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
          <span style="font-size: 9px; color: var(--text-muted); text-transform: uppercase; font-weight: 800; letter-spacing: 0.5px;">Risk Exposure</span>
          <span style="font-size: 10px; color: var(--text-secondary); font-weight: 700;">$${formatCompact(totalExposure)} Total</span>
        </div>

        <div style="display: flex; flex-direction: column; gap: 8px;">
          ${sorted.map(([symbol, exposure], i) => {
            const pct = (exposure / totalExposure * 100).toFixed(1);
            const barWidth = (exposure / maxExposure * 100).toFixed(1);
            const color = colors[i % colors.length];
            const posData = positions.find(p => p.symbol === symbol);
            const side = posData ? (posData.side || '').toUpperCase() : '';
            const sideColor = side === 'LONG' ? 'var(--accent-green)' : side === 'SHORT' ? 'var(--accent-red)' : 'var(--text-muted)';

            return `
              <div style="display: flex; align-items: center; gap: 10px;">
                <div style="width: 70px; flex-shrink: 0;">
                  <span style="font-size: 11px; font-weight: 700; color: white;">${symbol.replace('USDT', '')}</span>
                  <span style="font-size: 8px; font-weight: 600; color: ${sideColor}; margin-left: 3px;">${side ? side.substring(0, 1) : ''}</span>
                </div>
                <div style="flex: 1; height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden;">
                  <div style="width: ${barWidth}%; height: 100%; background: ${color}; border-radius: 3px; transition: width 0.5s ease; box-shadow: 0 0 6px ${color}40;"></div>
                </div>
                <div style="width: 80px; text-align: right; flex-shrink: 0;">
                  <span style="font-size: 10px; font-weight: 700; color: var(--text-secondary);">$${formatCompact(exposure)}</span>
                  <span style="font-size: 9px; color: var(--text-muted); margin-left: 4px;">${pct}%</span>
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;
  }

  // ── Funding Rate Widget ──────────────────────────────────────────────────
  function renderFundingWidget(m) {
    // Update or create funding widget in the account health area
    const container = document.getElementById('funding-widget-container');
    if (!container) {
      // Inject it into the dashboard after account health
      const healthContainer = document.getElementById('account-health-container');
      if (!healthContainer) return;

      const widget = document.createElement('div');
      widget.id = 'funding-widget-container';
      widget.style.cssText = 'margin-bottom: 16px;';
      healthContainer.parentNode.insertBefore(widget, healthContainer.nextSibling);
      return renderFundingWidget(m); // retry
    }

    const fundingRate = parseFloat(m.funding_rate || 0);
    const fundingPct = (fundingRate * 100).toFixed(4);
    const isPositive = fundingRate > 0;
    const rateColor = isPositive ? 'var(--accent-green)' : fundingRate < 0 ? 'var(--accent-red)' : 'var(--text-muted)';
    const rateIcon = isPositive ? '▲' : fundingRate < 0 ? '▼' : '—';

    // Estimate 8h cost for a $1000 long position
    const dailyCost = Math.abs(fundingRate * 3) * 1000; // 3 settlements per day
    const annualYield = Math.abs(fundingRate) * 3 * 365 * 100;

    container.innerHTML = `
      <div class="analytics-card" style="background: rgba(255,255,255,0.03); border: 1px solid var(--border-color); padding: 16px; border-radius: 12px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
          <span style="font-size: 9px; color: var(--text-muted); text-transform: uppercase; font-weight: 800; letter-spacing: 0.5px;">Funding Rate</span>
          <div style="display: flex; align-items: center; gap: 6px;">
            <span style="font-size: 9px; color: var(--text-muted);">Next:</span>
            <span id="funding-countdown" style="font-size: 11px; font-weight: 700; color: var(--text-secondary); font-variant-numeric: tabular-nums;">--:--:--</span>
          </div>
        </div>

        <div style="display: flex; align-items: flex-end; gap: 10px; margin-bottom: 12px;">
          <div style="font-size: 24px; font-weight: 800; color: ${rateColor}; line-height: 1; font-variant-numeric: tabular-nums;">
            ${rateIcon} ${Math.abs(fundingPct)}%
          </div>
          <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 4px;">per 8h</div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding-top: 10px; border-top: 1px solid rgba(255,255,255,0.05);">
          <div>
            <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 2px;">Direction Signal</div>
            <div style="font-size: 10px; font-weight: 700; color: ${isPositive ? 'var(--accent-green)' : 'var(--accent-red)'};">
              ${isPositive ? 'Longs Pay Shorts' : 'Shorts Pay Longs'}
            </div>
          </div>
          <div>
            <div style="font-size: 9px; color: var(--text-muted); margin-bottom: 2px;">Annualized</div>
            <div style="font-size: 10px; font-weight: 700; color: var(--text-secondary);">
              ${annualYield.toFixed(1)}% APR
            </div>
          </div>
        </div>
      </div>
    `;

    // Start countdown update
    if (m.next_funding_time) {
      nextFundingTime = m.next_funding_time;
    }
    updateFundingCountdown();
  }

  // ── Market Sentiment ─────────────────────────────────────────────────────
  function renderMarketSentiment(m) {
    const container = document.getElementById('market-sentiment-container');
    if (!container) return;

    const lsRatio = m.long_short_ratio || { ratio: '0', long_pct: '50', short_pct: '50' };
    const oiDelta = m.oi_delta || { delta: '0', percent: '0' };
    const deltaColor = parseFloat(oiDelta.delta) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
    const oi = parseFloat(m.open_interest || 0);

    container.innerHTML = `
      <div style="margin-bottom: 16px;">
        <span class="card-title" style="font-size: 9px; font-weight: 800; color: var(--text-muted); text-transform: uppercase; display: block; margin-bottom: 12px; letter-spacing: 0.5px;">Market Sentiment (${m.symbol})</span>

        <!-- Open Interest & Delta -->
        <div class="analytics-card" style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); padding: 12px; border-radius: 12px; margin-bottom: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-size: 10px; color: var(--text-muted);">Open Interest</span>
            <span style="font-size: 10px; color: ${deltaColor}; font-weight: 700;">
              ${parseFloat(oiDelta.delta) >= 0 ? '▲' : '▼'} ${Math.abs(parseFloat(oiDelta.percent || 0)).toFixed(2)}%
            </span>
          </div>
          <div style="font-size: 18px; font-weight: 800; color: white; font-variant-numeric: tabular-nums;">
            ${oi > 0 ? oi.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '--'}
          </div>
        </div>

        <!-- Long/Short Ratio -->
        <div class="analytics-card" style="background: rgba(255,255,255,0.02); border: 1px solid var(--border-color); padding: 12px; border-radius: 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
            <span style="font-size: 10px; color: var(--text-muted);">Long/Short Ratio</span>
            <span style="font-size: 12px; color: white; font-weight: 800;">${lsRatio.ratio || '--'}</span>
          </div>
          <div style="height: 6px; background: var(--bg-hover); border-radius: 3px; overflow: hidden; display: flex;">
            <div style="width: ${lsRatio.long_pct || 50}%; height: 100%; background: var(--accent-green); transition: width 0.3s ease;"></div>
            <div style="width: ${lsRatio.short_pct || 50}%; height: 100%; background: var(--accent-red); transition: width 0.3s ease;"></div>
          </div>
          <div style="display: flex; justify-content: space-between; margin-top: 6px; font-size: 9px; font-weight: 700;">
            <span style="color: var(--accent-green);">${lsRatio.long_pct || 50}% LONG</span>
            <span style="color: var(--accent-red);">${lsRatio.short_pct || 50}% SHORT</span>
          </div>
        </div>
      </div>
    `;
  }

  // ── Main Render ──────────────────────────────────────────────────────────
  // ── Overview (paper account, positions, market, IPOs) ───────────────────
  let overviewTimer = null;
  let overviewBusy = false;
  let lastOverviewAt = 0;

  function esc(v) {
    return String(v === null || v === undefined ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function inr(n, dp = 2) {
    const v = Number(n);
    if (!isFinite(v)) return 'N/A';
    return '\u20b9' + v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp });
  }
  function signed(n, dp = 2) {
    const v = Number(n) || 0;
    return (v > 0 ? '+' : v < 0 ? '-' : '') + inr(Math.abs(v), dp);
  }
  function pnlColor(n) {
    return Number(n) > 0 ? 'var(--accent-green)' : Number(n) < 0 ? 'var(--accent-red)' : 'var(--text-secondary)';
  }
  function goTab(tab) {
    document.querySelector('.sidebar-item[data-tab="' + tab + '"]')?.click();
  }
  function getJSON(url) {
    return fetch(url).then(r => (r.ok ? r.json() : null)).catch(() => null);
  }

  function ensureSkeleton(container) {
    if (container.querySelector('#dash-overview')) return;
    container.innerHTML = `
      <div id="dash-overview"><div class="dash-loading">Loading your dashboard\u2026</div></div>
      <details class="dash-futures">
        <summary>Crypto futures account</summary>
        <div id="account-health-container"></div>
        <div id="funding-widget-container"></div>
        <div id="market-sentiment-container"></div>
      </details>
      <div id="dash-insights"></div>`;
    if (!overviewTimer) overviewTimer = setInterval(() => refreshOverview(), 10000);
    refreshOverview(true);
  }

  async function refreshOverview(force = false) {
    const el = document.getElementById('dash-overview');
    if (!el || overviewBusy) return;
    if (!force && (el.offsetParent === null || Date.now() - lastOverviewAt < 8000)) return;
    overviewBusy = true;
    try {
      const [acc, pos, perf, mkt, ipos] = await Promise.all([
        getJSON('/api/paper/account'), getJSON('/api/paper/positions'),
        getJSON('/api/paper/performance'), getJSON('/api/market/overview'),
        getJSON('/api/market/ipos'),
      ]);
      lastOverviewAt = Date.now();
      el.innerHTML = renderMarketStrip(mkt) + renderPaperCard(acc, perf) +
        renderPositionsCard(pos) + renderIpoCard(ipos);
      el.querySelectorAll('[data-go]').forEach(b => { b.onclick = () => goTab(b.getAttribute('data-go')); });
    } catch (e) {
      console.warn('Dashboard overview error:', e);
    } finally {
      overviewBusy = false;
    }
  }

  function renderMarketStrip(mkt) {
    if (!mkt) return '';
    const ms = mkt.market_status || {};
    const session = ms.session || ms.status || ms.state || '';
    const idx = (mkt.indices || []).filter(i => i.quote && i.quote.last != null).slice(0, 4);
    const chips = idx.map(i => {
      const pct = Number(i.quote.change_pct);
      return `<div class="dash-idx"><span>${esc(i.name || i.symbol)}</span><b>${inr(i.quote.last)}</b>` +
        `<em style="color:${pnlColor(pct)}">${isFinite(pct) ? (pct > 0 ? '+' : '') + pct.toFixed(2) + '%' : ''}</em></div>`;
    }).join('');
    if (!session && !chips) return '';
    return `<div class="dash-card"><div class="dash-title">Market${session ? ` <span class="dash-pill">${esc(session)}</span>` : ''}</div>` +
      (chips ? `<div class="dash-idx-row">${chips}</div>` : '<div class="dash-muted">Index quotes unavailable right now.</div>') + '</div>';
  }

  function renderPaperCard(a, perf) {
    if (!a || a.error) {
      return '<div class="dash-card"><div class="dash-title">Paper trading</div><div class="dash-muted">Account unavailable.</div></div>';
    }
    const noTrades = !a.positions_count && !(perf && perf.total_trades);
    const hero = `<div class="dash-hero"><span>Portfolio value</span><b>${inr(a.portfolio_value)}</b>` +
      `<em style="color:${pnlColor(a.total_pnl)}">${signed(a.total_pnl)} (${Number(a.total_pnl_pct || 0).toFixed(2)}%)</em></div>`;
    const stat = (l, v, c) => `<div class="dash-stat"><span>${l}</span><b style="${c ? 'color:' + c : ''}">${v}</b></div>`;
    const wr = perf && perf.total_trades ? `${Number(perf.win_rate).toFixed(0)}% of ${perf.total_trades}` : 'N/A';
    return `<div class="dash-card"><div class="dash-title">Paper trading <button class="dash-link" data-go="papertrade">Open \u203a</button></div>${hero}` +
      `<div class="dash-grid">` +
      stat('Today (realised)', signed(a.realized_today), pnlColor(a.realized_today)) +
      stat('Unrealised', signed(a.unrealized_pnl), pnlColor(a.unrealized_pnl)) +
      stat('Free cash', inr(a.free_cash)) + stat('Invested', inr(a.invested_value)) +
      stat('Win rate', wr) + stat('Pending orders', String(a.pending_orders || 0)) + '</div>' +
      (noTrades ? `<div class="dash-empty">No trades yet \u2014 practise with ${inr(a.initial_balance, 0)} of virtual cash. ` +
        `<button class="dash-link" data-go="papertrade">Place your first trade \u203a</button></div>` : '') + '</div>';
  }

  function renderPositionsCard(pos) {
    if (!Array.isArray(pos) || !pos.length) return '';
    const rows = pos.slice(0, 5).map(p => {
      const pnl = Number(p.unrealized_pnl) || 0;
      return `<div class="dash-row"><div><b>${esc(p.display_name || p.symbol)}</b>` +
        `<span class="dash-muted">${esc(p.side)} ${Number(p.quantity)} @ ${inr(p.avg_price)}</span></div>` +
        `<div style="text-align:right"><b style="color:${pnlColor(pnl)}">${signed(pnl)}</b>` +
        `<span class="dash-muted" style="color:${pnlColor(pnl)}">${Number(p.unrealized_pnl_pct || 0).toFixed(2)}%</span></div></div>`;
    }).join('');
    return `<div class="dash-card"><div class="dash-title">Open positions (${pos.length}) ` +
      `<button class="dash-link" data-go="papertrade">Manage \u203a</button></div>${rows}</div>`;
  }

  function renderIpoCard(data) {
    if (!data || !Array.isArray(data.ipos)) return '';
    const live = data.ipos.filter(i => i.status === 'Open' || i.status === 'Upcoming').slice(0, 4);
    if (!live.length) {
      return '<div class="dash-card"><div class="dash-title">IPOs <button class="dash-link" data-go="ipos">Tracker \u203a</button></div>' +
        '<div class="dash-muted">No open or upcoming IPOs right now. New ones appear here automatically.</div></div>';
    }
    const rows = live.map(i => {
      const lo = i.price_band_low, hi = i.price_band_high;
      const band = hi == null ? 'Price band: N/A' :
        'Band ' + (lo == null || Number(lo) === Number(hi) ? inr(hi) : inr(lo) + ' \u2013 ' + inr(hi));
      const when = i.status === 'Open' ? (i.close_date ? 'closes ' + esc(i.close_date) : '') : (i.open_date ? 'opens ' + esc(i.open_date) : '');
      return `<div class="dash-row"><div><b>${esc(i.name)}</b><span class="dash-muted">${band}${i.lot_size ? ' \u00b7 lot ' + i.lot_size : ''}</span></div>` +
        `<div style="text-align:right"><span class="dash-pill ${i.status === 'Open' ? 'dash-pill-green' : ''}">${esc(i.status)}</span>` +
        `<span class="dash-muted">${when}</span></div></div>`;
    }).join('');
    return `<div class="dash-card"><div class="dash-title">IPOs <button class="dash-link" data-go="ipos">All \u203a</button></div>${rows}</div>`;
  }

  // ── Symbol insights (need support/resistance data) ───────────────────────
  function renderInsights(srData) {
    const el = document.getElementById('dash-insights');
    if (!el) return;
    const price = srData.current_price;
    const trendScore = calculateTrendScore(currentData);
    const momentum = calculateMomentum(currentData);
    el.innerHTML = `
      <div class="dash-card">
        <div class="dash-title">${esc(currentSymbol || 'Symbol')} \u2014 trend
          <span class="dash-pill ${trendScore > 60 ? 'dash-pill-green' : 'dash-pill-red'}">${trendScore > 60 ? 'BULLISH' : 'BEARISH'}</span></div>
        <div style="display:flex; align-items:center; gap:16px;">
          <div class="score-ring" style="width:50px; height:50px; border-radius:50%; border:4px solid var(--bg-hover); border-top-color:var(--accent-blue); display:flex; align-items:center; justify-content:center; font-weight:800; font-size:14px; color:white;">${trendScore}</div>
          <div style="flex:1;">
            <div class="dash-muted" style="margin-bottom:4px;">Trend strength</div>
            <div style="height:4px; background:var(--bg-hover); border-radius:2px; overflow:hidden;"><div style="width:${trendScore}%; height:100%; background:var(--accent-blue);"></div></div>
          </div>
        </div>
      </div>
      <div class="dash-card">
        <div class="dash-title">Support &amp; resistance</div>
        <div style="display:flex; flex-direction:column; gap:12px;">
          ${renderLevelInsight(srData.resistance?.[0], 'Resistance', 'var(--accent-red)', price)}
          ${renderLevelInsight(srData.support?.[0], 'Support', 'var(--accent-green)', price)}
        </div>
        <p class="dash-muted" style="margin:10px 0 0; line-height:1.6;">
          Price is ${price > (srData.support?.[0]?.price || 0) ? 'holding structural support' : 'under pressure'}.
          ${momentum > 0 ? 'Upward momentum' : 'Downward pressure'} with ${srData.resistance?.[0]?.touches || 0} past retests overhead. Educational only.
        </p>
      </div>`;
  }

  function update(symbol, data, srData) {
    currentSymbol = symbol;
    currentData = data;
    render(srData);
  }

  function render(srData) {
    const container = document.getElementById('analytics-content');
    if (!container) return;
    ensureSkeleton(container);           // always show the overview, even with no S/R data
    if (srData && srData.current_price) renderInsights(srData);
  }

  function renderLevelInsight(lvl, label, color, currentPrice) {
    if (!lvl) return `<div style="font-size:11px; color:var(--text-muted);">No major ${label} nearby</div>`;
    const dist = ((Math.abs(lvl.price - currentPrice) / currentPrice) * 100).toFixed(2);
    return `
      <div style="display:flex; justify-content:space-between; align-items:flex-end;">
        <div>
          <div style="font-size:12px; font-weight:700; color:${color};">${label} @ ${lvl.price.toFixed(2)}</div>
          <div style="font-size:10px; color:var(--text-muted);">${lvl.touches} Independent Retests</div>
        </div>
        <div style="text-align:right;">
          <div style="font-size:11px; font-weight:600; color:var(--text-primary);">${dist}% Gap</div>
          <div style="font-size:9px; color:var(--text-muted);">PROXIMITY</div>
        </div>
      </div>
    `;
  }

  function calculateTrendScore(data) {
    if (!data || data.length < 20) return 50;
    const last = data[data.length - 1].close;
    const prev = data[data.length - 20].close;
    const score = 50 + ((last - prev) / prev * 500);
    return Math.min(99, Math.max(1, Math.round(score)));
  }

  function calculateMomentum(data) {
    if (!data || data.length < 5) return 0;
    return data[data.length - 1].close - data[data.length - 5].close;
  }

  // ── Helpers ──────────────────────────────────────────────────────────────
  function formatCompact(num) {
    if (Math.abs(num) >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    if (Math.abs(num) >= 1e3) return (num / 1e3).toFixed(2) + 'K';
    return num.toFixed(2);
  }

  return { init, update };
})();

window.Dashboard = Dashboard;
