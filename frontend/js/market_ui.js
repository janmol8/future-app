/* Market subsystem UI (Phase 4): market overview, IPO tracker, notification
   bell. Education-first rules enforced here:
   - Missing data renders as N/A — never 0, never a dash that looks like 0.
   - Every panel shows its data source and a "delayed · unofficial · for
     education only" note. GMP, when present, is explicitly labelled
     unofficial.
   - No polling loop starts unless the view is actually open (no provider
     load when nobody is watching). */
(function () {
  'use strict';

  var POLL_MS = 15000;          // overview/IPO refresh while the panel is open
  var BELL_POLL_MS = 60000;     // notification inbox check
  var _overviewTimer = null;
  var _ipoTimer = null;
  var _bellTimer = null;
  var _userKey = null;

  function userKey() {
    if (_userKey) return _userKey;
    _userKey = localStorage.getItem('fa_user_key');
    if (!_userKey) {
      _userKey = 'u_' + Math.random().toString(36).slice(2, 10);
      localStorage.setItem('fa_user_key', _userKey);
    }
    return _userKey;
  }

  // ── formatting: missing is N/A, never 0 ──
  function na(v) { return (v === null || v === undefined || v === '') ? 'N/A' : v; }

  function fmtNum(v, dp) {
    if (v === null || v === undefined || isNaN(Number(v))) return 'N/A';
    return Number(v).toLocaleString('en-IN', {
      minimumFractionDigits: dp === undefined ? 2 : dp,
      maximumFractionDigits: dp === undefined ? 2 : dp,
    });
  }

  function fmtPct(v) {
    if (v === null || v === undefined || isNaN(Number(v))) return 'N/A';
    var n = Number(v);
    return (n >= 0 ? '+' : '') + n.toFixed(2) + '%';
  }

  function pctClass(v) {
    if (v === null || v === undefined || isNaN(Number(v))) return '';
    return Number(v) >= 0 ? 'mu-up' : 'mu-down';
  }

  function timeAgo(iso) {
    if (!iso) return '';
    var then = new Date(iso).getTime();
    if (isNaN(then)) return '';
    var s = Math.max(0, Math.floor((Date.now() - then) / 1000));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── data source / education labels ──
  var EDUCATION_NOTE =
    '<span class="mu-source-note">' +
    '<i class="fas fa-circle-info"></i> Delayed, unofficial market data · ' +
    'for education only · not investment advice</span>';

  function renderSourceNote(sources) {
    var chain = Array.isArray(sources) && sources.length
      ? 'Sources: ' + sources.join(' → ') : '';
    return '<div class="mu-meta">' + EDUCATION_NOTE +
      (chain ? '<span class="mu-chain">' + esc(chain) + '</span>' : '') + '</div>';
  }

  // ── API ──
  function apiGet(url) {
    return fetch(url).then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  // ── market overview ──
  function renderOverview(data) {
    var el = document.getElementById('mu-overview-body');
    if (!el) return;
    if (!data) {
      el.innerHTML = '<div class="mu-error">Market data unavailable right now.' +
        ' No fallback prices are shown by design.</div>';
      return;
    }
    var status = data.market_status || {};
    var state = status.state || 'UNKNOWN';
    var rows = (data.indices || []).map(function (idx) {
      var q = idx.quote || {};
      var stale = q.is_stale || (!q.last && q.last !== 0);
      return '<tr class="mu-row" data-symbol="' + esc(idx.symbol) + '">' +
        '<td class="mu-sym"><span class="mu-name">' + esc(idx.name) + '</span>' +
        (stale && q.last !== null && q.last !== undefined
          ? ' <span class="mu-stale" title="Last update ' + esc(q.as_of || '') + '">stale</span>'
          : '') + '</td>' +
        '<td class="mu-last">' + fmtNum(q.last) + '</td>' +
        '<td class="' + pctClass(q.change_pct) + '">' + fmtPct(q.change_pct) + '</td>' +
        '</tr>';
    }).join('');

    el.innerHTML =
      '<div class="mu-status-line">' +
      '<span class="mu-state mu-state-' + esc(state.toLowerCase()) + '">' + esc(state) + '</span>' +
      (status.holiday_name ? '<span class="mu-holiday">' + esc(status.holiday_name) + '</span>' : '') +
      '<span class="mu-asof">' + (data.last_updated ? timeAgo(data.last_updated) : '') + '</span>' +
      '</div>' +
      '<table class="mu-table"><tbody>' + (rows || '') + '</tbody></table>' +
      '<div class="mu-section-title">New listings (last 30 days)</div>' +
      renderNewListings(data.new_listings || []) +
      renderSourceNote(data.sources);
  }

  function renderNewListings(items) {
    if (!items.length) {
      return '<div class="mu-empty">No recent listings tracked yet — the ' +
        'discovery job updates this list daily.</div>';
    }
    return '<table class="mu-table"><tbody>' + items.map(function (n) {
      return '<tr class="mu-row" data-symbol="' + esc(n.symbol) + '">' +
        '<td class="mu-sym"><span class="mu-name">' + esc(n.company_name || n.symbol) + '</span>' +
        '<span class="mu-sub">' + esc(n.exchange || '') + '</span></td>' +
        '<td class="mu-last">' + esc(n.listed_on || 'N/A') + '</td>' +
        '</tr>';
    }).join('') + '</tbody></table>';
  }

  // ── IPO tracker ──
  var _ipoData = null;
  var _ipoFilter = 'All';

  function ipoDayDiff(iso) {
    if (!iso) return null;
    var d = new Date(iso + 'T00:00:00');
    if (isNaN(d.getTime())) return null;
    var t = new Date(); t.setHours(0, 0, 0, 0);
    return Math.round((d - t) / 86400000);
  }

  function ipoCountdown(ipo) {
    var o = ipoDayDiff(ipo.open_date), c = ipoDayDiff(ipo.close_date);
    function days(n) { return n === 1 ? '1 day' : n + ' days'; }
    if (ipo.status === 'Upcoming' && o !== null) {
      return o > 0 ? 'Opens in ' + days(o) : 'Opens today';
    }
    if (ipo.status === 'Open' && c !== null) {
      return c > 0 ? 'Closes in ' + days(c) : (c === 0 ? 'Closes today' : '');
    }
    if (ipo.status === 'Closed' && ipo.allotment_date) {
      var a = ipoDayDiff(ipo.allotment_date);
      if (a !== null && a >= 0) return a === 0 ? 'Allotment today' : 'Allotment in ' + days(a);
    }
    if (ipo.status === 'Allotment' && ipo.listing_date) {
      var l = ipoDayDiff(ipo.listing_date);
      if (l !== null && l >= 0) return l === 0 ? 'Lists today' : 'Lists in ' + days(l);
    }
    return '';
  }

  function ipoIsNew(ipo) {
    var t = Date.parse(ipo.created_at || '');
    return !isNaN(t) && (Date.now() - t) < 3 * 86400000 && ipo.status !== 'Listed';
  }

  function ipoBand(ipo) {
    var lo = ipo.price_band_low, hi = ipo.price_band_high;
    if (hi === null || hi === undefined) {
      if (ipo.issue_price) return '\u20b9' + fmtNum(ipo.issue_price);
      return null;
    }
    if (lo === null || lo === undefined || Number(lo) === Number(hi)) return '\u20b9' + fmtNum(hi);
    return '\u20b9' + fmtNum(lo) + ' \u2013 \u20b9' + fmtNum(hi);
  }

  function ipoStat(label, value) {
    return '<div class="mu-ipo-stat"><span>' + esc(label) + '</span><b>' +
      (value === null || value === undefined || value === '' ? 'N/A' : value) + '</b></div>';
  }

  function ipoCard(ipo) {
    var band = ipoBand(ipo);
    var hi = ipo.price_band_high || ipo.issue_price;
    var minInv = (ipo.lot_size && hi) ? '\u20b9' + fmtNum(ipo.lot_size * hi, 0) : null;
    var cd = ipoCountdown(ipo);
    var statusCls = 'st-' + String(ipo.status || '').toLowerCase().replace(/[^a-z]+/g, '-');
    var gmp = '';
    if (ipo.gmp && ipo.gmp.gmp_rupees !== null && ipo.gmp.gmp_rupees !== undefined) {
      gmp = ipoStat('GMP*', '\u20b9' + fmtNum(ipo.gmp.gmp_rupees));
    }
    return '<div class="mu-ipo-card" data-ipo="' + esc(ipo.name) + '">' +
      '<div class="mu-ipo-top"><div><span class="mu-name">' + esc(ipo.name) + '</span>' +
      '<span class="mu-sub">' + esc(ipo.ipo_type || '') + '</span></div>' +
      '<div class="mu-ipo-badges">' + (ipoIsNew(ipo) ? '<span class="mu-ipo-new">NEW</span>' : '') +
      '<span class="mu-ipo-status ' + esc(statusCls) + '">' + esc(ipo.status || 'N/A') + '</span></div></div>' +
      '<div class="mu-ipo-band"><span>Price band</span><b>' +
      (band || '<em>Not announced yet</em>') + '</b>' +
      (cd ? '<span class="mu-ipo-cd">' + esc(cd) + '</span>' : '') + '</div>' +
      '<div class="mu-ipo-grid">' +
      ipoStat('Lot size', ipo.lot_size ? fmtNum(ipo.lot_size, 0) + ' shares' : null) +
      ipoStat('Min. investment', minInv) +
      ipoStat('Issue size', ipo.issue_size_cr ? '\u20b9' + fmtNum(ipo.issue_size_cr, 0) + ' Cr' : null) +
      ipoStat('Opens', ipo.open_date) + ipoStat('Closes', ipo.close_date) +
      ipoStat('Allotment', ipo.allotment_date) + ipoStat('Listing', ipo.listing_date) + gmp +
      '</div></div>';
  }

  function renderIpos(data) {
    var el = document.getElementById('mu-ipo-body');
    if (!el) return;
    if (!data || !data.ipos) {
      el.innerHTML = '<div class="mu-error">IPO data unavailable right now.</div>';
      return;
    }
    _ipoData = data;
    if (!data.ipos.length) {
      el.innerHTML = '<div class="mu-empty">No IPOs found yet. New IPOs are detected automatically ' +
        'every few minutes from NSE. If the NSE feed is unreachable, add entries to ' +
        'backend/data/ipos_manual.csv (see docs/DATA_SOURCES.md).</div>';
      return;
    }
    var counts = { All: data.ipos.length };
    data.ipos.forEach(function (i) { counts[i.status] = (counts[i.status] || 0) + 1; });
    var chips = ['All', 'Open', 'Upcoming', 'Closed', 'Allotment', 'Listed'].filter(function (k) {
      return k === 'All' || counts[k];
    }).map(function (k) {
      return '<button class="mu-ipo-chip' + (k === _ipoFilter ? ' active' : '') +
        '" data-filter="' + k + '">' + k + ' (' + (counts[k] || 0) + ')</button>';
    }).join('');
    var list = data.ipos.filter(function (i) { return _ipoFilter === 'All' || i.status === _ipoFilter; });
    el.innerHTML = '<div class="mu-ipo-chips">' + chips + '</div>' +
      (list.length ? list.map(ipoCard).join('') : '<div class="mu-empty">No IPOs with this status.</div>') +
      '<div class="mu-footnote">Price band, lot size and dates come from the exchange feed; ' +
      'anything not published shows N/A. *GMP (grey market premium) is an unofficial, ' +
      'unregulated figure shown only as entered. Education only \u2014 not investment advice.</div>';
    Array.prototype.forEach.call(el.querySelectorAll('.mu-ipo-chip'), function (btn) {
      btn.onclick = function () { _ipoFilter = btn.getAttribute('data-filter'); renderIpos(_ipoData); };
    });
  }

  // ── notification bell ──
  function renderBell(n) {
    var dot = document.getElementById('mu-bell-dot');
    if (!dot) return;
    var count = (n && n.unread) || 0;
    dot.style.display = count > 0 ? 'flex' : 'none';
    dot.textContent = count > 99 ? '99+' : String(count);
  }

  function renderInbox(items) {
    var el = document.getElementById('mu-inbox');
    if (!el) return;
    if (!items || !items.length) {
      el.innerHTML = '<div class="mu-empty">No notifications. Enable event ' +
        'types below — everything is off until you opt in.</div>';
      return;
    }
    el.innerHTML = items.map(function (ntf) {
      return '<div class="mu-notif' + (ntf.read_at ? '' : ' unread') + '">' +
        '<div class="mu-notif-title">' + esc(ntf.title) + '</div>' +
        (ntf.body ? '<div class="mu-notif-body">' + esc(ntf.body) + '</div>' : '') +
        '<div class="mu-notif-time">' + timeAgo(ntf.created_at) + '</div>' +
        '</div>';
    }).join('');
  }

  function loadBell() {
    apiGet('/api/market/notifications?user_key=' + encodeURIComponent(userKey()))
      .then(function (n) { renderBell(n); });
  }

  function loadInbox() {
    apiGet('/api/market/notifications?user_key=' + encodeURIComponent(userKey()))
      .then(function (n) { renderInbox(n ? n.notifications : null); renderBell(n); });
  }

  function loadPrefs() {
    apiGet('/api/market/notifications/prefs?user_key=' + encodeURIComponent(userKey()))
      .then(function (data) {
        var prefs = (data && data.prefs) || {};
        var map = {
          new_stock_added: 'New listings',
          new_ipo_announced: 'New IPO announced',
          ipo_open: 'IPO opens for bidding',
          ipo_listed: 'IPO listed',
          ipo_closing_soon: 'IPO closing soon',
        };
        var el = document.getElementById('mu-prefs');
        if (!el) return;
        el.innerHTML = Object.keys(map).map(function (k) {
          return '<label class="mu-pref"><input type="checkbox" data-pref="' + k + '"' +
            (prefs[k] ? ' checked' : '') + '> ' + map[k] + '</label>';
        }).join('');
        el.querySelectorAll('input[data-pref]').forEach(function (box) {
          box.addEventListener('change', function () {
            var body = { user_key: userKey(), prefs: {} };
            body.prefs[box.dataset.pref] = box.checked;
            fetch('/api/market/notifications/prefs?user_key=' + encodeURIComponent(userKey()), {
              method: 'PUT', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            }).then(function () {
              if (window.showNotification) window.showNotification('Notification preference saved');
            });
          });
        });
      });
  }

  function markAllRead() {
    fetch('/api/market/notifications/read', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_key: userKey() }),
    }).then(loadInbox);
  }

  // ── panel wiring ──
  function startOverviewPolling() {
    stopOverviewPolling();
    var tick = function () {
      apiGet('/api/market/overview').then(renderOverview);
    };
    tick();
    _overviewTimer = setInterval(tick, POLL_MS);
  }

  function stopOverviewPolling() {
    if (_overviewTimer) { clearInterval(_overviewTimer); _overviewTimer = null; }
  }

  function startIpoPolling() {
    stopIpoPolling();
    var tick = function () { apiGet('/api/market/ipos').then(renderIpos); };
    tick();
    _ipoTimer = setInterval(tick, POLL_MS);
  }

  function stopIpoPolling() {
    if (_ipoTimer) { clearInterval(_ipoTimer); _ipoTimer = null; }
  }

  function activateTab(tab) {
    if (tab === 'market') startOverviewPolling();
    else stopOverviewPolling();
    if (tab === 'ipos') startIpoPolling();
    else stopIpoPolling();
  }

  function init(onSymbolSelect) {
    _onSymbolSelect = onSymbolSelect || function () {};

    // Bell button (injected next to the theme toggle).
    var actions = document.querySelector('.top-bar-actions');
    if (actions && !document.getElementById('mu-bell-btn')) {
      var btn = document.createElement('button');
      btn.className = 'top-bar-btn';
      btn.id = 'mu-bell-btn';
      btn.title = 'Notifications';
      btn.innerHTML = '<i class="fas fa-bell"></i>' +
        '<span id="mu-bell-dot" class="mu-bell-dot" style="display:none"></span>';
      btn.addEventListener('click', toggleInbox);
      actions.insertBefore(btn, actions.firstChild);
    }

    // Row click -> open symbol on chart.
    document.addEventListener('click', function (ev) {
      var row = ev.target.closest ? ev.target.closest('.mu-row') : null;
      if (row && row.dataset.symbol && _onSymbolSelect) {
        _onSymbolSelect({ symbol: row.dataset.symbol, name: row.dataset.symbol });
      }
    });

    loadBell();
    if (!_bellTimer) _bellTimer = setInterval(loadBell, BELL_POLL_MS);
  }

  var _onSymbolSelect = function () {};
  var _inboxOpen = false;

  function toggleInbox() {
    var el = document.getElementById('mu-inbox-panel');
    if (!el) return;
    _inboxOpen = !_inboxOpen;
    el.style.display = _inboxOpen ? 'block' : 'none';
    if (_inboxOpen) {
      loadInbox();
      loadPrefs();
      fetch('/api/market/notifications/read', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_key: userKey() }),
      }).then(loadBell);
    }
  }

  function activateInbox(open) { _inboxOpen = open; }

  window.MarketUI = {
    init: init,
    activateTab: activateTab,
    refreshOverview: function () {
      apiGet('/api/market/overview').then(renderOverview);
    },
    refreshIpos: function () {
      fetch('/api/market/ipos/refresh', { method: 'POST' }).catch(function () {})
        .then(function () { return apiGet('/api/market/ipos'); }).then(renderIpos);
    },
    renderOverview: renderOverview,
    renderIpos: renderIpos,
    loadBell: loadBell,
    loadInbox: loadInbox,
    loadPrefs: loadPrefs,
    markAllRead: markAllRead,
    na: na,
    fmtNum: fmtNum,
    fmtPct: fmtPct,
  };
})();
