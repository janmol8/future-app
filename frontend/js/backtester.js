
/**
 * Backtest Results - UI for displaying performance metrics
 */
const BacktestResults = {
    show(results) {
        let panel = document.getElementById('backtest-results-panel');
        if (!panel) {
            panel = document.createElement('div');
            panel.id = 'backtest-results-panel';
            panel.className = 'results-panel';
            document.body.appendChild(panel);
        }
        panel.classList.add('active');

        const { summary, trades, equity_curve } = results;
        
        panel.innerHTML = `
            <div class="results-header">
                <h3><i class="fas fa-chart-bar"></i> Performance Summary</h3>
                <button class="btn-close-results" onclick="BacktestResults.hide()"><i class="fas fa-times"></i></button>
            </div>
            <div class="results-grid">
                <div class="metric-card">
                    <span class="label">Net Profit</span>
                    <span class="value ${summary.net_profit >= 0 ? 'pos' : 'neg'}">${summary.net_profit_pct}%</span>
                    <span class="sub-value">$${summary.net_profit.toLocaleString()}</span>
                </div>
                <div class="metric-card">
                    <span class="label">Win Rate</span>
                    <span class="value">${summary.win_rate}%</span>
                    <span class="sub-value">${summary.total_trades} Trades</span>
                </div>
                <div class="metric-card">
                    <span class="label">Profit Factor</span>
                    <span class="value">${summary.profit_factor}</span>
                </div>
                <div class="metric-card">
                    <span class="label">Max Drawdown</span>
                    <span class="value neg">${summary.max_drawdown}%</span>
                </div>
            </div>
            
            <div class="equity-chart-container">
                <div id="equity-chart"></div>
            </div>

            <div class="trades-table-container">
                <table class="trades-table">
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th>Entry</th>
                            <th>Exit</th>
                            <th>PnL (%)</th>
                            <th>Reason</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${trades.map(t => `
                            <tr>
                                <td class="buy">BUY</td>
                                <td>$${t.entry_price.toFixed(2)}</td>
                                <td>$${t.exit_price.toFixed(2)}</td>
                                <td class="${t.pnl_pct >= 0 ? 'pos' : 'neg'}">${t.pnl_pct.toFixed(2)}%</td>
                                <td>${t.exit_reason}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;

        this.renderEquityChart(equity_curve);
    },

    hide() {
        const panel = document.getElementById('backtest-results-panel');
        if (panel) panel.classList.remove('active');
    },

    renderEquityChart(data) {
        const container = document.getElementById('equity-chart');
        const chart = LightweightCharts.createChart(container, {
            width: container.clientWidth,
            height: 200,
            layout: { background: { color: 'transparent' }, textColor: '#d1d4dc' },
            grid: { vertLines: { color: '#1e222d' }, horzLines: { color: '#1e222d' } },
            timeScale: { visible: false }
        });

        const areaSeries = chart.addAreaSeries({
            lineColor: '#2962ff',
            topColor: 'rgba(41, 98, 255, 0.3)',
            bottomColor: 'rgba(41, 98, 255, 0)',
            lineWidth: 2,
        });

        areaSeries.setData(data.map(d => ({ time: d.time, value: d.value })));
        chart.timeScale().fitContent();
        
        window.addEventListener('resize', () => {
            chart.applyOptions({ width: container.clientWidth });
        });
    }
};
