/**
 * Future Chart - Educational Model Lab
 */
const AIAnalyst = (() => {
    let currentData = null;

    async function analyze(symbol, interval) {
        const container = document.getElementById('ai-analysis-container');
        if (!container) return;

        container.innerHTML = `
            <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding:40px 20px; color:var(--text-muted);">
                <i class="fas fa-brain fa-spin" style="font-size:32px; margin-bottom:15px; color:var(--accent-blue);"></i>
                <div style="font-size:13px; font-weight:700; letter-spacing:1px;">MODEL SCORES LOADING...</div>
            </div>
        `;

        try {
            const url = `/api/ai/analyze?symbol=${encodeURIComponent(symbol)}&interval=${interval}`;
            const resp = await fetch(url);
            const data = await resp.json();
            if (!resp.ok || data.error || !data.market_regime) {
                throw new Error(data.error || 'Model response missing market regime');
            }
            currentData = data;
            render(data);
        } catch (err) {
            console.error("Model analysis failed:", err);
            container.innerHTML = `<div class="error-msg">Model Lab Unavailable</div>`;
        }
    }

    function render(data) {
        const container = document.getElementById('ai-analysis-container');
        if (!container) return;

        const getProbColor = (p) => {
            if (p > 0.75) return '#089981';
            if (p > 0.6) return '#2962ff';
            if (p < 0.4) return '#f23645';
            return '#d1d4dc';
        };

        container.innerHTML = `
            <div class="ai-results" style="padding:15px; display:flex; flex-direction:column; gap:20px;">
                
                <!-- Market Regime Badge -->
                <div style="background:var(--bg-tertiary); padding:15px; border-radius:12px; border-left:4px solid var(--accent-blue);">
                    <div style="font-size:10px; color:var(--text-muted); text-transform:uppercase; font-weight:800; margin-bottom:5px;">Current Market Regime</div>
                    <div style="font-size:16px; font-weight:900; color:white; display:flex; align-items:center; gap:8px;">
                        <i class="fas fa-microchip" style="color:var(--accent-blue);"></i>
                        ${data.market_regime.replace('_', ' ')}
                    </div>
                </div>

                <!-- Probabilities -->
                <div style="display:flex; flex-direction:column; gap:15px;">
                    ${renderProgressBar("Support Hold", data.support_hold_probability)}
                    ${renderProgressBar("Resistance Break", data.resistance_break_probability)}
                    ${renderProgressBar("Trend Continuation", data.trend_continuation_probability)}
                    ${renderProgressBar("Reversal Risk", data.reversal_probability)}
                </div>

                <!-- Confidence Score -->
                <div style="margin-top:10px; padding:15px; background:rgba(41, 98, 255, 0.05); border-radius:12px; text-align:center; border:1px solid rgba(41, 98, 255, 0.1);">
                    <div style="font-size:11px; font-weight:800; color:var(--accent-blue); margin-bottom:5px;">MODEL SCORE CLARITY</div>
                    <div style="font-size:24px; font-weight:900; color:white;">${(data.confidence_score * 100).toFixed(0)}%</div>
                    <div style="font-size:9px; color:var(--text-muted); margin-top:5px;">Educational feature score, not a trade signal</div>
                </div>

                <div style="font-size:10px; color:var(--text-muted); line-height:1.5; font-style:italic;">
                    * These model probabilities are for classroom-style review only. They are not investment advice, not recommendations, and not independently validated as predictive.
                </div>
                <div style="font-size:10px; color:var(--text-muted); line-height:1.5;">
                    ${data.disclaimer || 'Educational simulation only. AI/model output may be wrong.'}
                </div>
            </div>
        `;
    }

    function renderProgressBar(label, prob) {
        const percent = (prob * 100).toFixed(0);
        let color = '#2962ff';
        if (prob > 0.7) color = '#089981';
        if (prob < 0.3) color = '#f23645';

        return `
            <div>
                <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-size:11px; font-weight:700;">
                    <span>${label}</span>
                    <span style="color:${color}">${percent}%</span>
                </div>
                <div style="height:6px; background:rgba(255,255,255,0.05); border-radius:3px; overflow:hidden;">
                    <div style="height:100%; width:${percent}%; background:${color}; transition:width 1s cubic-bezier(0.4, 0, 0.2, 1);"></div>
                </div>
            </div>
        `;
    }

    function refresh() {
        if (window.App) {
            analyze(window.App.currentSymbol, window.App.currentInterval);
        }
    }

    return {
        analyze,
        refresh
    };
})();
