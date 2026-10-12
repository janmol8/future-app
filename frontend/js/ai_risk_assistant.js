/**
 * Educational Risk Coach Module
 * Simulated risk review and example position sizing.
 */
const AIRiskAssistant = (() => {
    let container = null;
    const BASE = '/api/futures';

    function init() {
        console.log("Educational Risk Coach Initialized");
        createFloatingPanel();
        startPolling();
    }

    function createFloatingPanel() {
        container = document.createElement('div');
        container.id = 'ai-risk-assistant';
        container.style.cssText = `
            position: fixed; bottom: 80px; left: 20px; width: 280px;
            background: var(--glass-bg); backdrop-filter: blur(var(--glass-blur));
            border: 1px solid var(--border-light); border-radius: 16px;
            box-shadow: var(--shadow-panel); z-index: 1000;
            display: flex; flex-direction: column; overflow: hidden;
            transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1);
        `;

        container.innerHTML = `
            <div style="padding: 12px 16px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.2);">
                <div style="display: flex; align-items: center; gap: 8px;">
                    <i class="fas fa-graduation-cap" style="color: var(--accent-gold);"></i>
                    <span style="font-size: 11px; font-weight: 800; letter-spacing: 1px; color: var(--accent-gold);">SIM RISK COACH</span>
                </div>
                <button onclick="AIRiskAssistant.toggle()" style="background: transparent; border: none; color: var(--text-muted); cursor: pointer;"><i class="fas fa-chevron-down"></i></button>
            </div>
            <div id="ai-risk-body" style="padding: 16px; display: flex; flex-direction: column; gap: 16px;">
                <!-- Portfolio Risk -->
                <div id="ai-portfolio-risk">
                    <div style="font-size: 10px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 8px;">Portfolio Exposure</div>
                    <div style="height: 6px; background: rgba(255,255,255,0.05); border-radius: 3px; overflow: hidden;">
                        <div id="risk-bar" style="width: 0%; height: 100%; background: var(--accent-green); transition: width 0.5s, background 0.5s;"></div>
                    </div>
                    <div id="risk-status" style="font-size: 11px; font-weight: 700; margin-top: 6px; color: var(--accent-green);">LOW RISK</div>
                </div>

                <!-- Educational Actions -->
                <button onclick="AIRiskAssistant.checkSize()" class="btn-primary" style="height: 36px; font-size: 11px; background: linear-gradient(135deg, var(--accent-gold), #F57F17); color: black;">
                    <i class="fas fa-calculator" style="margin-right: 6px;"></i> SIM SIZE EXAMPLE
                </button>

                <!-- Market Summary -->
                <div id="ai-market-summary" style="font-size: 11px; line-height: 1.5; color: var(--text-secondary); background: rgba(255,255,255,0.02); padding: 10px; border-radius: 8px; border: 1px solid var(--border-color);">
                    Reviewing simulated risk context. Educational only, not advice.
                </div>
            </div>
        `;

        document.body.appendChild(container);
    }

    function toggle() {
        const body = document.getElementById('ai-risk-body');
        const icon = container.querySelector('.fa-chevron-down, .fa-chevron-up');
        if (body.style.display === 'none') {
            body.style.display = 'flex';
            icon.className = 'fas fa-chevron-down';
        } else {
            body.style.display = 'none';
            icon.className = 'fas fa-chevron-up';
        }
    }

    function startPolling() {
        setInterval(async () => {
            try {
                const risk = await fetch(`${BASE}/risk-status`).then(r => r.json());
                updateRiskUI(risk);
            } catch (e) {}
        }, 5000);
    }

    function updateRiskUI(data) {
        const bar = document.getElementById('risk-bar');
        const status = document.getElementById('risk-status');
        const summary = document.getElementById('ai-market-summary');

        if (!bar || !status || !data) return;

        const exposure = data.exposure_pct || 0;
        bar.style.width = `${exposure}%`;
        
        if (exposure > 80) {
            bar.style.background = 'var(--accent-red)';
            status.textContent = 'CRITICAL RISK';
            status.style.color = 'var(--accent-red)';
        } else if (exposure > 50) {
            bar.style.background = 'var(--accent-gold)';
            status.textContent = 'MODERATE RISK';
            status.style.color = 'var(--accent-gold)';
        } else {
            bar.style.background = 'var(--accent-green)';
            status.textContent = 'LOW RISK';
            status.style.color = 'var(--accent-green)';
        }

        if (data.summary) summary.textContent = data.summary;
    }

    async function checkSize() {
        if (!window.App || !App.currentSymbol) return;
        
        const btn = container.querySelector('.btn-primary');
        const originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> CALCULATING...';
        btn.disabled = true;

        try {
            const res = await fetch(`${BASE}/size-check?symbol=${App.currentSymbol}`).then(r => r.json());
            if (res.success) {
                showRecommendation(res.recommendation);
            }
        } catch (e) {
            console.error("Size check failed", e);
        } finally {
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    }

    function showRecommendation(rec) {
        const detail = typeof rec === 'object' && rec !== null
            ? `<div style="font-size:22px; font-weight:900; color:white; margin-bottom:10px;">${rec.example_size ?? rec.recommended_size ?? '-'}</div>
               <div>${rec.reasoning || ''}</div>
               ${(rec.warnings || []).map(w => `<div style="margin-top:8px; color:var(--accent-gold);">${w}</div>`).join('')}
               <div style="margin-top:12px; font-size:11px; color:var(--text-muted);">${rec.disclaimer || 'Educational simulation only. Not investment advice.'}</div>`
            : `${rec}<div style="margin-top:12px; font-size:11px; color:var(--text-muted);">Educational simulation only. Not investment advice.</div>`;
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed; inset: 0; background: rgba(0,0,0,0.8);
            display: flex; align-items: center; justify-content: center; z-index: 2000;
        `;
        overlay.innerHTML = `
            <div class="pt-card custom-dialog" style="width: 320px; padding: 24px; text-align: center;">
                <i class="fas fa-graduation-cap" style="font-size: 40px; color: var(--accent-gold); margin-bottom: 16px;"></i>
                <h3 style="margin-bottom: 8px; color: white;">Simulated Size Example</h3>
                <div style="font-size: 14px; color: var(--text-secondary); margin-bottom: 20px; line-height: 1.6;">
                    ${detail}
                </div>
                <button onclick="this.parentElement.parentElement.remove()" class="btn-primary" style="width: 100%; height: 44px;">GOT IT</button>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    function showPostTradeReview(trade) {
        const overlay = document.createElement('div');
        overlay.style.cssText = `
            position: fixed; inset: 0; background: rgba(0,0,0,0.8);
            display: flex; align-items: center; justify-content: center; z-index: 2000;
        `;
        overlay.innerHTML = `
            <div class="pt-card custom-dialog" style="width: 360px; padding: 24px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h3 style="margin: 0; color: var(--accent-gold);">Trade Review</h3>
                    <button onclick="this.parentElement.parentElement.parentElement.remove()" style="background: transparent; border: none; color: var(--text-muted); cursor: pointer;"><i class="fas fa-times"></i></button>
                </div>
                <div style="display: flex; flex-direction: column; gap: 16px;">
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: var(--text-muted);">Symbol</span>
                        <span style="font-weight: 800;">${trade.symbol}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: var(--text-muted);">PnL</span>
                        <span style="font-weight: 800; color: ${trade.pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'};">₹${trade.pnl.toFixed(2)}</span>
                    </div>
                    <div style="background: rgba(255,255,255,0.03); padding: 12px; border-radius: 8px; border: 1px solid var(--border-color);">
                        <div style="font-size: 10px; color: var(--accent-gold); text-transform: uppercase; margin-bottom: 6px; font-weight: 800;">Educational Review</div>
                        <div style="font-size: 12px; line-height: 1.5; color: var(--text-primary);">
                            ${trade.ai_review || "Review whether this simulated trade followed your written risk plan. This is educational only, not advice."}
                        </div>
                    </div>
                    <div style="display: flex; gap: 8px;">
                        <button class="btn-secondary" style="flex: 1; height: 36px; font-size: 11px;">SAVE TO JOURNAL</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    return { init, toggle, checkSize, showPostTradeReview };
})();

window.AIRiskAssistant = AIRiskAssistant;
