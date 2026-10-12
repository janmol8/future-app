/**
 * TradeView Pro - Screener Engine
 */
const Screener = (() => {
    let filters = {
        min_price: '',
        max_price: '',
        rsi_below: '',
        rsi_above: '',
    };
    let results = [];

    function init() {
        const runBtn = document.getElementById('btn-run-screener');
        if (runBtn) {
            runBtn.addEventListener('click', runScreener);
        }
    }

    async function runScreener() {
        const btn = document.getElementById('btn-run-screener');
        if (!btn) return;

        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> SCANNING...';
        
        filters.min_price = document.getElementById('scr-min-price')?.value || '';
        filters.max_price = document.getElementById('scr-max-price')?.value || '';
        filters.rsi_below = document.getElementById('scr-rsi-below')?.value || '';
        filters.rsi_above = document.getElementById('scr-rsi-above')?.value || '';

        try {
            const response = await fetch('/api/screener', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(filters)
            });
            results = await response.json();
            renderResults();
        } catch (err) {
            console.error('Screener failed:', err);
            window.showError("Screener failed to fetch results");
        } finally {
            btn.disabled = false;
            btn.innerText = 'RUN SCAN';
        }
    }

    function renderResults() {
        const container = document.getElementById('screener-results');
        if (!container) return;

        if (results.length === 0) {
            container.innerHTML = '<tr><td colspan="3" class="empty-msg">No matches found.</td></tr>';
            return;
        }

        container.innerHTML = '';
        results.forEach(r => {
            const tr = document.createElement('tr');
            tr.className = 'screener-row';
            tr.style.cursor = 'pointer';
            tr.innerHTML = `
                <td style="padding:10px 0; font-weight:700;">${r.symbol}</td>
                <td style="padding:10px 0;">${r.price.toFixed(2)}</td>
                <td style="padding:10px 0;" class="${r.change >= 0 ? 'positive' : 'negative'}">${r.change >= 0 ? '+' : ''}${r.change.toFixed(2)}%</td>
            `;
            tr.onclick = () => {
                if (window.App && window.App.onSymbolSelect) {
                    window.App.onSymbolSelect({ symbol: r.symbol, name: r.symbol });
                }
            };
            container.appendChild(tr);
        });
    }

    function refresh() {
        runScreener();
    }

    return {
        init,
        runScreener,
        refresh
    };
})();

document.addEventListener('DOMContentLoaded', () => Screener.init());
