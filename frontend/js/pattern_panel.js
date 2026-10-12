const PatternPanel = (() => {
  const stateOrder = ['confirmed', 'emerging', 'forming', 'stale', 'invalidated'];

  function renderIdle(symbol, interval) {
    const container = document.getElementById('patterns-list');
    if (!container) return;
    container.innerHTML = `<div class="analytics-placeholder">Patterns are not scanned automatically. Open this panel and press refresh when you want to scan ${symbol || ''} ${interval || ''}.</div>`;
  }

  function renderLoading(symbol, interval) {
    const container = document.getElementById('patterns-list');
    if (!container) return;
    container.innerHTML = `<div class="analytics-placeholder"><i class="fas fa-spinner fa-spin"></i> Scanning ${symbol || ''} ${interval || ''}...</div>`;
  }

  function render(patterns = [], meta = {}) {
    const container = document.getElementById('patterns-list');
    if (!container) return;
    if (!patterns.length) {
      container.innerHTML = '<div class="analytics-placeholder">No chart patterns match current filters.</div>';
      return;
    }
    const grouped = stateOrder.reduce((acc, state) => {
      acc[state] = patterns.filter(p => p.state === state);
      return acc;
    }, {});
    container.innerHTML = `
      <div style="padding:10px 12px; border-bottom:1px solid var(--border-color); display:flex; align-items:center; justify-content:space-between; gap:8px;">
        <div style="font-size:11px; color:var(--text-secondary);">${meta.interval || ''} | ${patterns.length} shown${scanText(meta.scanWindow)}${meta.cached ? ' | cached' : ''} | ${meta.appliedPatternId ? '1 applied' : 'none applied'}</div>
        <div style="display:flex; gap:6px;">
          <button class="btn-icon" title="Clear applied pattern" onclick="PatternEngine.clearApplied()"><i class="fas fa-eraser"></i></button>
          <button class="btn-icon" title="Pattern settings" onclick="PatternSettings.open()"><i class="fas fa-cog"></i></button>
        </div>
      </div>
      ${stateOrder.map(state => section(state, grouped[state])).join('')}
    `;
  }

  function section(state, rows) {
    if (!rows?.length) return '';
    return `<div style="padding:10px 12px;">
      <div style="font-size:10px; font-weight:900; color:var(--text-muted); margin-bottom:8px;">${state.toUpperCase()} (${rows.length})</div>
      ${rows.map(card).join('')}
    </div>`;
  }

  function card(p) {
    const color = p.bias === 'bull' ? '#089981' : p.bias === 'bear' ? '#F23645' : '#2962FF';
    const risk = p.risk || {};
    const rr = risk.rr_classical == null ? 'n/a' : `${risk.rr_classical}:1`;
    return `<div style="padding:11px; margin-bottom:8px; border:1px solid rgba(255,255,255,.08); border-left:3px solid ${color}; border-radius:8px; background:rgba(255,255,255,.025);">
      <div style="display:flex; justify-content:space-between; gap:8px; align-items:center;">
        <div style="font-size:12px; font-weight:900; color:var(--text-primary);">${escapeHtml(p.name)}</div>
        <div style="font-size:11px; font-weight:900; color:${confidenceColor(p.confidence)};">${p.confidence}%</div>
      </div>
      <div style="margin-top:4px; font-size:10px; color:var(--text-secondary);">${String(p.bias).toUpperCase()} | ${p.state}${p.conflicted ? ' | CONFLICT' : ''}</div>
      <div style="margin-top:8px; font-size:11px; line-height:1.65; color:var(--text-secondary);">
        Entry: ${fmt(risk.entry_price || p.breakout_price)} | RR: ${rr}<br>
        SL: ${fmt(p.stop_loss)}<br>
        T1: ${fmt(p.targets?.classical)} | T2: ${fmt(p.targets?.bulkowski)}<br>
        TC: ${fmt(p.targets?.conservative)}<br>
        Position: ${fmt(risk.position_size_units, 0)} units | ${fmt(risk.position_size_value)} | Max loss ${fmt(risk.max_loss_inr)}
      </div>
      <div style="margin-top:8px; display:flex; gap:6px;">
        <button class="pattern-action" onclick="PatternEngine.apply('${p.id}')">Apply</button>
        <button class="pattern-action" onclick="PatternEngine.focus('${p.id}')">Focus</button>
        <button class="pattern-action" onclick="PatternPanel.trade('${p.id}')">Trade</button>
        <button class="pattern-action" onclick="PatternPanel.alert('${p.id}')">Alert</button>
      </div>
    </div>`;
  }

  function confidenceColor(score) {
    if (score >= 80) return '#089981';
    if (score >= 60) return '#F7A600';
    if (score >= 40) return '#2962FF';
    return '#888';
  }

  function scanText(scanWindow) {
    if (!scanWindow) return '';
    return ` | bars ${scanWindow.start_index}-${scanWindow.end_index} | lookback ${scanWindow.lookback}`;
  }

  function fmt(value, digits = 2) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 'n/a';
    return n.toLocaleString('en-IN', { maximumFractionDigits: digits, minimumFractionDigits: digits });
  }

  function escapeHtml(value) {
    return String(value || '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  }

  function trade(id) {
    const p = window.PatternEngine?.getActive?.().find(x => x.id === id);
    if (!p) return;
    window.PaperTrade?.openOrderModal?.(window.App?.currentSymbol || '', p.breakout_price || p.risk?.entry_price || null, p.name, p.bias === 'bear' ? 'SELL' : 'BUY');
  }

  function alert(id) {
    const p = window.PatternEngine?.getActive?.().find(x => x.id === id);
    if (!p) return;
    window.alertsEngine?.showCreateAlertModal?.();
    setTimeout(() => {
      const input = document.getElementById('alert-price');
      if (input) input.value = Number(p.breakout_price || p.targets?.classical || p.stop_loss).toFixed(2);
    }, 50);
  }

  return { render, renderIdle, renderLoading, trade, alert };
})();

window.PatternPanel = PatternPanel;
