/**
 * Futures Analytics Module — Enhanced
 * Equity curve with drawdown overlay, PnL per-trade bars,
 * consecutive streaks, fee analysis, win/loss donut, emotion heatmap.
 */
const FuturesAnalytics = (() => {
    let equityChart = null;
    let equitySeries = null;
    let drawdownSeries = null;

    const BASE = '/api/futures';

    /* ── INIT ─────────────────────────────────────────────── */
    async function init() {
        console.log('[Analytics] Initialized');
        const container = document.getElementById('analytics-content');
        if (!container) return;
        renderLayout(container);
        await refresh();
    }

    async function refresh() {
        try {
            const data = await fetch(`${BASE}/analytics`).then(r => r.json());
            if (!data) return;
            updateMetrics(data.metrics, data.summary);
            updateEquityCurve(data.equity_curve);
            updateDrawdown(data.equity_curve);
            updatePnLBarChart(data.trade_pnl || []);
            updateDistribution(data.distribution);
            updateConsecutive(data.consecutive);
            updateFeeAnalysis(data.summary);
            updateEmotionChart(data.emotion_data);
        } catch (e) {
            console.error('[Analytics] Refresh failed:', e);
        }
    }

    /* ── LAYOUT ───────────────────────────────────────────── */
    function renderLayout(container) {
        container.innerHTML = `
        <div class="analytics-scroll-container" style="padding:12px; display:flex; flex-direction:column; gap:16px; overflow-y:auto; height:100%;">

            <!-- Metrics Grid 2×4 -->
            <div id="analytics-metrics-grid" style="display:grid; grid-template-columns:repeat(2,1fr); gap:8px;"></div>

            <!-- Equity Curve + Drawdown -->
            <div>
                <h4 class="analytics-section-title">Equity Curve</h4>
                <div id="equity-curve-container" style="height:200px; background:rgba(0,0,0,0.2); border-radius:10px; border:1px solid var(--border-color); overflow:hidden;"></div>
            </div>

            <!-- Drawdown Chart -->
            <div>
                <h4 class="analytics-section-title">Drawdown</h4>
                <div id="drawdown-container" style="height:100px; background:rgba(0,0,0,0.2); border-radius:10px; border:1px solid var(--border-color); overflow:hidden;"></div>
            </div>

            <!-- PnL Per-Trade Bar Chart -->
            <div>
                <h4 class="analytics-section-title">PnL Per Trade</h4>
                <div id="pnl-bar-container" style="height:140px; background:rgba(0,0,0,0.2); border-radius:10px; border:1px solid var(--border-color); padding:8px; display:flex; align-items:flex-end; gap:3px; overflow-x:auto;"></div>
            </div>

            <!-- Win/Loss Donut + Consecutive Runs -->
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
                <div>
                    <h4 class="analytics-section-title">Win / Loss</h4>
                    <div id="distribution-container" style="display:flex; align-items:center; justify-content:center; height:140px;"></div>
                </div>
                <div>
                    <h4 class="analytics-section-title">Streaks</h4>
                    <div id="consecutive-container" style="display:flex; flex-direction:column; justify-content:center; height:140px; gap:10px; padding:0 12px;"></div>
                </div>
            </div>

            <!-- Fee Analysis -->
            <div>
                <h4 class="analytics-section-title">Fee Analysis</h4>
                <div id="fee-analysis-container" style="display:grid; grid-template-columns:repeat(3,1fr); gap:8px;"></div>
            </div>

            <!-- Emotion vs PnL -->
            <div>
                <h4 class="analytics-section-title">Emotion vs PnL</h4>
                <div id="emotion-chart-container" style="height:160px; background:rgba(0,0,0,0.2); border-radius:10px; border:1px solid var(--border-color); padding:8px;">
                    <div id="emotion-bars" style="display:flex; align-items:flex-end; justify-content:space-around; height:100%; gap:6px;"></div>
                </div>
            </div>
        </div>

        <style>
            .analytics-section-title {
                font-size:10px; color:var(--text-muted); text-transform:uppercase;
                letter-spacing:1px; margin-bottom:8px; font-weight:800;
            }
            .analytics-metric-card {
                background:rgba(255,255,255,0.03); border:1px solid var(--border-color);
                padding:10px; border-radius:10px; text-align:center;
            }
            .analytics-metric-card .label { font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800; margin-bottom:3px; }
            .analytics-metric-card .value { font-size:15px; font-weight:800; }
        </style>
        `;
        initEquityChart();
        initDrawdownChart();
    }

    /* ── EQUITY CHART (Lightweight Charts) ─────────────────── */
    function initEquityChart() {
        const el = document.getElementById('equity-curve-container');
        if (!el || typeof LightweightCharts === 'undefined') return;
        equityChart = LightweightCharts.createChart(el, {
            layout: { background: { color: 'transparent' }, textColor: 'rgba(255,255,255,0.5)', fontSize: 10 },
            grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
            rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.15, bottom: 0.15 } },
            timeScale: { borderVisible: false },
            handleScroll: false, handleScale: false,
        });
        equitySeries = equityChart.addAreaSeries({
            lineColor: '#2979FF', topColor: 'rgba(41,121,255,0.25)',
            bottomColor: 'rgba(41,121,255,0)', lineWidth: 2,
        });
        window.addEventListener('resize', () => {
            if (equityChart) equityChart.resize(el.clientWidth, el.clientHeight);
        });
    }

    function updateEquityCurve(data) {
        if (!equitySeries || !data || !data.length) return;
        equitySeries.setData(data.map(d => ({
            time: d.timestamp || d.time || 0,
            value: parseFloat(d.balance || d.equity || 0),
        })));
        equityChart.timeScale().fitContent();
    }

    /* ── DRAWDOWN CHART ───────────────────────────────────── */
    let ddChart = null, ddSeries = null;

    function initDrawdownChart() {
        const el = document.getElementById('drawdown-container');
        if (!el || typeof LightweightCharts === 'undefined') return;
        ddChart = LightweightCharts.createChart(el, {
            layout: { background: { color: 'transparent' }, textColor: 'rgba(255,255,255,0.5)', fontSize: 10 },
            grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
            rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: 0.1 } },
            timeScale: { borderVisible: false },
            handleScroll: false, handleScale: false,
        });
        ddSeries = ddChart.addHistogramSeries({
            color: '#FF1744', priceFormat: { type: 'percent' },
        });
        window.addEventListener('resize', () => {
            if (ddChart) ddChart.resize(el.clientWidth, el.clientHeight);
        });
    }

    function updateDrawdown(data) {
        if (!ddSeries || !data || !data.length) return;
        const equities = data.map(d => parseFloat(d.balance || d.equity || 0));
        let peak = equities[0];
        const ddData = data.map((d, i) => {
            if (equities[i] > peak) peak = equities[i];
            const dd = peak > 0 ? ((equities[i] - peak) / peak) * 100 : 0;
            return { time: d.timestamp || d.time || 0, value: dd, color: dd < 0 ? 'rgba(255,23,68,0.6)' : 'rgba(0,230,118,0.3)' };
        });
        ddSeries.setData(ddData);
        ddChart.timeScale().fitContent();
    }

    /* ── METRICS GRID ─────────────────────────────────────── */
    function updateMetrics(metrics, summary) {
        const grid = document.getElementById('analytics-metrics-grid');
        if (!grid || !metrics) return;

        const items = [
            { label: 'Win Rate', value: `${(metrics.win_rate * 100).toFixed(1)}%`, color: metrics.win_rate >= 0.5 ? 'var(--accent-green)' : 'var(--accent-red)' },
            { label: 'Profit Factor', value: metrics.profit_factor >= 99 ? '∞' : metrics.profit_factor.toFixed(2), color: metrics.profit_factor >= 1 ? 'var(--accent-green)' : 'var(--accent-red)' },
            { label: 'Sharpe', value: metrics.sharpe.toFixed(2), color: metrics.sharpe >= 1 ? 'var(--accent-green)' : 'var(--accent-blue)' },
            { label: 'Sortino', value: metrics.sortino.toFixed(2), color: metrics.sortino >= 1 ? 'var(--accent-green)' : 'var(--accent-blue)' },
            { label: 'Max DD', value: `${(metrics.max_dd * 100).toFixed(1)}%`, color: 'var(--accent-red)' },
            { label: 'Calmar', value: metrics.calmar.toFixed(2), color: 'var(--text-primary)' },
            { label: 'Avg R:R', value: `1:${metrics.avg_rr.toFixed(1)}`, color: 'var(--text-primary)' },
            { label: 'Expectancy', value: `$${metrics.expectancy.toFixed(2)}`, color: metrics.expectancy >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' },
        ];

        grid.innerHTML = items.map(it => `
            <div class="analytics-metric-card">
                <div class="label">${it.label}</div>
                <div class="value" style="color:${it.color}">${it.value}</div>
            </div>
        `).join('');
    }

    /* ── PnL BAR CHART ────────────────────────────────────── */
    function updatePnLBarChart(pnlList) {
        const el = document.getElementById('pnl-bar-container');
        if (!el || !pnlList.length) {
            if (el) el.innerHTML = '<div style="margin:auto; color:var(--text-muted); font-size:11px;">No trades yet</div>';
            return;
        }
        const maxAbs = Math.max(...pnlList.map(p => Math.abs(p)), 1);
        el.innerHTML = pnlList.map(p => {
            const h = Math.max(2, (Math.abs(p) / maxAbs) * 100);
            const color = p >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            return `<div style="flex:0 0 auto; width:8px; height:${h}%; background:${color}; border-radius:2px 2px 0 0; opacity:0.85; transition:height 0.3s;" title="$${p.toFixed(2)}"></div>`;
        }).join('');
    }

    /* ── WIN/LOSS DONUT ───────────────────────────────────── */
    function updateDistribution(data) {
        const el = document.getElementById('distribution-container');
        if (!el || !data) return;
        const total = (data.wins || 0) + (data.losses || 0);
        const winPct = total > 0 ? (data.wins / total) * 100 : 50;

        el.innerHTML = `
            <div style="position:relative; width:90px; height:90px; border-radius:50%; background:conic-gradient(var(--accent-green) ${winPct}%, var(--accent-red) 0); box-shadow:0 0 16px rgba(0,0,0,0.4);">
                <div style="position:absolute; inset:14px; background:var(--bg-secondary); border-radius:50%; display:flex; flex-direction:column; align-items:center; justify-content:center;">
                    <span style="font-size:14px; font-weight:800; color:white;">${winPct.toFixed(0)}%</span>
                    <span style="font-size:7px; color:var(--text-muted);">WIN</span>
                </div>
            </div>
            <div style="margin-left:20px; display:flex; flex-direction:column; gap:6px;">
                <div style="display:flex; align-items:center; gap:6px; font-size:10px;">
                    <div style="width:7px; height:7px; border-radius:2px; background:var(--accent-green);"></div>
                    <span style="color:var(--text-secondary);">${data.wins || 0} Wins</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px; font-size:10px;">
                    <div style="width:7px; height:7px; border-radius:2px; background:var(--accent-red);"></div>
                    <span style="color:var(--text-secondary);">${data.losses || 0} Losses</span>
                </div>
                <div style="font-size:9px; color:var(--text-muted);">Total: ${total}</div>
            </div>
        `;
    }

    /* ── CONSECUTIVE STREAKS ───────────────────────────────── */
    function updateConsecutive(data) {
        const el = document.getElementById('consecutive-container');
        if (!el || !data) return;
        const streakColor = (v) => v > 0 ? 'var(--accent-green)' : v < 0 ? 'var(--accent-red)' : 'var(--text-muted)';
        const streakLabel = (v) => v > 0 ? `${v}W` : v < 0 ? `${Math.abs(v)}L` : '—';

        el.innerHTML = `
            <div style="display:flex; justify-content:space-between; padding:0 4px;">
                <div style="text-align:center;">
                    <div style="font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800;">Max Win Streak</div>
                    <div style="font-size:22px; font-weight:800; color:var(--accent-green); margin-top:2px;">${data.max_consecutive_wins || 0}</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800;">Max Loss Streak</div>
                    <div style="font-size:22px; font-weight:800; color:var(--accent-red); margin-top:2px;">${data.max_consecutive_losses || 0}</div>
                </div>
            </div>
            <div style="text-align:center; padding-top:8px; border-top:1px solid var(--border-color);">
                <div style="font-size:9px; color:var(--text-muted); text-transform:uppercase; font-weight:800;">Current Streak</div>
                <div style="font-size:18px; font-weight:800; color:${streakColor(data.current_streak || 0)}; margin-top:2px;">${streakLabel(data.current_streak || 0)}</div>
            </div>
        `;
    }

    /* ── FEE ANALYSIS ─────────────────────────────────────── */
    function updateFeeAnalysis(summary) {
        const el = document.getElementById('fee-analysis-container');
        if (!el || !summary) return;
        const items = [
            { label: 'Total Fees', value: `$${(summary.total_fees || 0).toFixed(2)}`, color: 'var(--accent-red)' },
            { label: 'Total Funding', value: `$${(summary.total_funding || 0).toFixed(2)}`, color: 'var(--accent-gold)' },
            { label: 'ROI', value: `${(summary.roi_pct || 0).toFixed(2)}%`, color: (summary.roi_pct || 0) >= 0 ? 'var(--accent-green)' : 'var(--accent-red)' },
        ];
        el.innerHTML = items.map(it => `
            <div class="analytics-metric-card">
                <div class="label">${it.label}</div>
                <div class="value" style="color:${it.color}">${it.value}</div>
            </div>
        `).join('');
    }

    /* ── EMOTION BAR CHART ────────────────────────────────── */
    function updateEmotionChart(data) {
        const el = document.getElementById('emotion-bars');
        if (!el) return;
        if (!data || Object.keys(data).length === 0) {
            el.innerHTML = '<div style="margin:auto; color:var(--text-muted); font-size:11px;">No emotion data</div>';
            return;
        }
        const maxVal = Math.max(...Object.values(data).map(v => Math.abs(v)), 1);
        el.innerHTML = Object.entries(data).map(([emotion, pnl]) => {
            const h = Math.max(4, (Math.abs(pnl) / maxVal) * 80);
            const color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
            return `
                <div style="display:flex; flex-direction:column; align-items:center; gap:4px; flex:1;">
                    <div style="font-size:8px; color:${color}; font-weight:700;">$${pnl.toFixed(0)}</div>
                    <div style="width:100%; height:${h}%; min-height:4px; background:${color}; border-radius:3px 3px 0 0; opacity:0.8; transition:height 0.4s;"></div>
                    <div style="font-size:7px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.5px;">${emotion}</div>
                </div>
            `;
        }).join('');
    }

    return { init, refresh };
})();

window.FuturesAnalytics = FuturesAnalytics;
