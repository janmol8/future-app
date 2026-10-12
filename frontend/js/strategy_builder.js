
/**
 * Strategy Builder - UI for creating backtest conditions
 */
const StrategyBuilder = {
    entryConditions: [],
    exitConditions: [],

    init() {
        // No longer auto-rendering a floating panel
    },

    renderUI(containerId) {
        if (!containerId) return;
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="strategy-body">
                <div class="strategy-section">
                    <h4 style="color:var(--text-secondary); font-size:11px; margin-bottom:10px; text-transform:uppercase;">Entry Conditions (Long)</h4>
                    <div id="entry-conditions-list"></div>
                    <button class="btn-add-cond" onclick="StrategyBuilder.addCondition('entry')" style="width:100%; padding:8px; background:transparent; border:1px dashed var(--border-color); color:var(--text-secondary); cursor:pointer; border-radius:6px; font-size:11px;">
                        <i class="fas fa-plus"></i> Add Entry Condition
                    </button>
                </div>
                <div class="strategy-section" style="margin-top:20px;">
                    <h4 style="color:var(--text-secondary); font-size:11px; margin-bottom:10px; text-transform:uppercase;">Exit Conditions</h4>
                    <div id="exit-conditions-list"></div>
                    <button class="btn-add-cond" onclick="StrategyBuilder.addCondition('exit')" style="width:100%; padding:8px; background:transparent; border:1px dashed var(--border-color); color:var(--text-secondary); cursor:pointer; border-radius:6px; font-size:11px;">
                        <i class="fas fa-plus"></i> Add Exit Condition
                    </button>
                </div>
                <div class="strategy-settings" style="margin-top:20px; background:var(--bg-tertiary); padding:15px; border-radius:8px;">
                    <div class="setting-row" style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <label style="font-size:12px;">Stop Loss (%)</label>
                        <input type="number" id="strategy-sl" value="2.0" step="0.1" style="width:60px; background:var(--bg-primary); border:1px solid var(--border-color); color:white; border-radius:4px; text-align:center;">
                    </div>
                    <div class="setting-row" style="display:flex; justify-content:space-between; align-items:center;">
                        <label style="font-size:12px;">Take Profit (%)</label>
                        <input type="number" id="strategy-tp" value="5.0" step="0.1" style="width:60px; background:var(--bg-primary); border:1px solid var(--border-color); color:white; border-radius:4px; text-align:center;">
                    </div>
                </div>
                <button class="btn-run-backtest" onclick="StrategyBuilder.runBacktest()" style="width:100%; padding:12px; background:var(--accent-blue); color:white; border:none; border-radius:8px; font-weight:bold; cursor:pointer; margin-top:20px;">
                    <i class="fas fa-play"></i> RUN BACKTEST
                </button>
            </div>
        `;

        this.addCondition('entry');
        this.addCondition('exit');
    },

    renderInsideContainer(containerId) {
        // Only render if container is empty to avoid re-initializing on every click
        const container = document.getElementById(containerId);
        if (container && container.innerHTML.trim() === '') {
            this.renderUI(containerId);
        }
    },

    reset() {
        const container = document.getElementById('strategy-builder-container');
        if (container) {
            container.innerHTML = '';
            this.renderUI('strategy-builder-container');
            if (window.showNotification) window.showNotification("Strategy Builder Reset");
        }
    },

    toggle() {
        document.getElementById('strategy-builder-panel').classList.toggle('collapsed');
    },

    addCondition(type) {
        const container = document.getElementById(`${type}-conditions-list`);
        const condId = Date.now() + Math.random();
        const div = document.createElement('div');
        div.className = 'condition-row';
        div.id = `cond-${condId}`;
        div.innerHTML = `
            <select class="cond-indicator">
                <option value="price">Price (Close)</option>
                <option value="sma">SMA</option>
                <option value="ema">EMA</option>
                <option value="rsi">RSI</option>
            </select>
            <input type="number" class="cond-period" value="20" placeholder="Period">
            <select class="cond-operator">
                <option value=">">></option>
                <option value="<"><</option>
                <option value="crosses_above">Crosses Above</option>
                <option value="crosses_below">Crosses Below</option>
            </select>
            <select class="cond-value-type">
                <option value="close">Price (Close)</option>
                <option value="value">Value</option>
            </select>
            <input type="number" class="cond-value" value="0" style="display:none">
            <button class="btn-remove-cond" onclick="document.getElementById('cond-${condId}').remove()"><i class="fas fa-trash"></i></button>
        `;
        
        div.querySelector('.cond-value-type').onchange = (e) => {
            div.querySelector('.cond-value').style.display = e.target.value === 'value' ? 'block' : 'none';
        };

        container.appendChild(div);
    },

    getStrategyConfig() {
        const parseConditions = (type) => {
            const rows = document.querySelectorAll(`#${type}-conditions-list .condition-row`);
            return Array.from(rows).map(row => ({
                indicator: row.querySelector('.cond-indicator').value,
                period: row.querySelector('.cond-period').value,
                operator: row.querySelector('.cond-operator').value,
                value: row.querySelector('.cond-value-type').value === 'close' ? 'close' : row.querySelector('.cond-value').value
            }));
        };

        return {
            entry_conditions: parseConditions('entry'),
            exit_conditions: parseConditions('exit'),
            stop_loss: parseFloat(document.getElementById('strategy-sl').value),
            take_profit: parseFloat(document.getElementById('strategy-tp').value)
        };
    },

    async runBacktest() {
        const config = this.getStrategyConfig();
        const activeSymbol = App.currentSymbol;
        const activeInterval = App.currentInterval;

        const btn = document.querySelector('.btn-run-backtest');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> RUNNING...';
        btn.disabled = true;

        try {
            const response = await fetch(`${App.API_BASE}/backtest`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    symbol: activeSymbol,
                    interval: activeInterval,
                    strategy: config
                })
            });
            const results = await response.json();
            if (results.error) throw new Error(results.error);
            
            BacktestResults.show(results);
            // Close inspector if it was a floating success, but here we just stay open or let user switch
            // App.showInspectorView('chart-view'); // Optional
        } catch (err) {
            alert("Backtest Failed: " + err.message);
        } finally {
            btn.innerHTML = '<i class="fas fa-play"></i> RUN BACKTEST';
            btn.disabled = false;
        }
    }
};
