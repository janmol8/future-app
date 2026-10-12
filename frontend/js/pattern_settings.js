const PatternSettings = (() => {
  const KEY = 'future_chart_pattern_settings';
  const defaults = {
    accountSize: 100000,
    riskPct: 1.0,
    lotSize: 1,
    lookback: 120,
    anchorMode: 'current',
    anchorDate: '',
    minConfidence: 40,
    families: { classic: true, candlestick: true, event: true, volatility: true },
    states: { confirmed: true, emerging: true, forming: false, stale: false, invalidated: false },
  };

  function get() {
    try {
      return { ...defaults, ...(JSON.parse(localStorage.getItem(KEY) || '{}')) };
    } catch (e) {
      return { ...defaults };
    }
  }

  function save(settings) {
    localStorage.setItem(KEY, JSON.stringify(settings));
    return settings;
  }

  function open() {
    const current = get();
    document.getElementById('pattern-settings-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'pattern-settings-modal';
    modal.style.cssText = 'position:fixed; inset:0; z-index:9999; background:rgba(0,0,0,.55); display:flex; align-items:center; justify-content:center;';
    modal.innerHTML = `
      <div style="width:360px; background:var(--bg-secondary,#111827); border:1px solid var(--border-color,#273244); border-radius:8px; padding:16px; color:var(--text-primary,#fff); box-shadow:0 18px 60px rgba(0,0,0,.35);">
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:14px;">
          <h3 style="margin:0; font-size:14px;">Pattern Settings</h3>
          <button id="pattern-settings-close" class="btn-icon"><i class="fas fa-times"></i></button>
        </div>
        ${numberRow('Account Size', 'pattern-account-size', current.accountSize)}
        ${numberRow('Risk Per Trade %', 'pattern-risk-pct', current.riskPct, '0.1')}
        ${numberRow('Lot Size', 'pattern-lot-size', current.lotSize)}
        ${numberRow('Lookback Bars', 'pattern-lookback', current.lookback)}
        <label style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin:8px 0; font-size:12px;">
          <span>Anchor Mode</span>
          <select id="pattern-anchor-mode" style="width:140px; height:30px; background:var(--bg-primary,#0b0f18); border:1px solid var(--border-color,#273244); color:inherit; border-radius:6px; padding:0 8px;">
            <option value="current" ${current.anchorMode !== 'date' ? 'selected' : ''}>Current</option>
            <option value="date" ${current.anchorMode === 'date' ? 'selected' : ''}>Date</option>
          </select>
        </label>
        <label style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin:8px 0; font-size:12px;">
          <span>Anchor Date</span>
          <input type="date" id="pattern-anchor-date" value="${current.anchorDate || ''}" style="width:140px; height:30px; background:var(--bg-primary,#0b0f18); border:1px solid var(--border-color,#273244); color:inherit; border-radius:6px; padding:0 8px;">
        </label>
        ${numberRow('Min Confidence', 'pattern-min-confidence', current.minConfidence)}
        <div style="margin:12px 0 6px; font-size:10px; color:var(--text-muted,#8a94a6); font-weight:800;">FAMILIES</div>
        ${checkRow('Classic', 'family-classic', current.families?.classic)}
        ${checkRow('Candlestick', 'family-candlestick', current.families?.candlestick)}
        ${checkRow('Bar/Event', 'family-event', current.families?.event)}
        ${checkRow('Volatility', 'family-volatility', current.families?.volatility)}
        <div style="margin:12px 0 6px; font-size:10px; color:var(--text-muted,#8a94a6); font-weight:800;">STATES</div>
        ${checkRow('Confirmed', 'state-confirmed', current.states?.confirmed)}
        ${checkRow('Emerging', 'state-emerging', current.states?.emerging)}
        ${checkRow('Forming', 'state-forming', current.states?.forming)}
        <button id="pattern-settings-save" style="width:100%; margin-top:14px; height:36px; border:none; border-radius:6px; background:#2962FF; color:#fff; font-weight:800; cursor:pointer;">Save</button>
      </div>`;
    document.body.appendChild(modal);
    document.getElementById('pattern-settings-close').onclick = () => modal.remove();
    modal.onclick = e => { if (e.target === modal) modal.remove(); };
    document.getElementById('pattern-settings-save').onclick = () => {
      save({
        accountSize: numberValue('pattern-account-size', defaults.accountSize),
        riskPct: numberValue('pattern-risk-pct', defaults.riskPct),
        lotSize: numberValue('pattern-lot-size', defaults.lotSize),
        lookback: clamp(numberValue('pattern-lookback', defaults.lookback), 60, 3000),
        anchorMode: document.getElementById('pattern-anchor-mode')?.value === 'date' ? 'date' : 'current',
        anchorDate: document.getElementById('pattern-anchor-date')?.value || '',
        minConfidence: numberValue('pattern-min-confidence', defaults.minConfidence),
        families: {
          classic: checked('family-classic'),
          candlestick: checked('family-candlestick'),
          event: checked('family-event'),
          volatility: checked('family-volatility'),
        },
        states: {
          confirmed: checked('state-confirmed'),
          emerging: checked('state-emerging'),
          forming: checked('state-forming'),
          stale: false,
          invalidated: false,
        },
      });
      modal.remove();
      window.PatternEngine?.reload?.();
    };
  }

  function numberRow(label, id, value, step = '1') {
    return `<label style="display:flex; align-items:center; justify-content:space-between; gap:12px; margin:8px 0; font-size:12px;"><span>${label}</span><input type="number" id="${id}" value="${value}" step="${step}" style="width:140px; height:30px; background:var(--bg-primary,#0b0f18); border:1px solid var(--border-color,#273244); color:inherit; border-radius:6px; padding:0 8px;"></label>`;
  }

  function checkRow(label, id, value) {
    return `<label style="display:flex; align-items:center; gap:8px; margin:6px 0; font-size:12px;"><input type="checkbox" id="${id}" ${value ? 'checked' : ''}>${label}</label>`;
  }

  function numberValue(id, fallback) {
    const n = Number(document.getElementById(id)?.value);
    return Number.isFinite(n) ? n : fallback;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function checked(id) {
    return !!document.getElementById(id)?.checked;
  }

  return { get, save, open };
})();

window.PatternSettings = PatternSettings;
