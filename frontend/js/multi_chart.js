/**
 * TradeView Pro - Multi-Chart Layout Engine
 */
class MultiChartManager {
    constructor(overlayId) {
        this.overlayId = overlayId || 'multi-chart-overlay';
        this.overlay = document.getElementById(this.overlayId);
        this.charts = [];
        this.layout = '1x1';
        this.activeIndex = 0;
    }

    setLayout(layout) {
        if (this.layout === layout) return;
        this.layout = layout;
        this.activeIndex = 0; // Reset to first
        
        if (!this.overlay) this.overlay = document.getElementById(this.overlayId);
        const mainChart = document.getElementById('chart-container');

        if (layout === '1x1') {
            if (this.overlay) this.overlay.style.display = 'none';
            if (mainChart) {
                mainChart.style.visibility = 'visible';
                mainChart.style.pointerEvents = 'auto';
                if (typeof ChartModule !== 'undefined') ChartModule.resize();
            }
            window.showNotification("Main Chart Active");
        } else {
            if (mainChart) {
                mainChart.style.visibility = 'hidden';
                mainChart.style.pointerEvents = 'none';
            }
            if (this.overlay) {
                this.overlay.style.display = 'grid';
                this.setupGrid(layout);
                this.focusPane(0);
            }
        }
        
        this.updateActiveButton(layout);
    }

    focusPane(index) {
        this.activeIndex = index;
        document.querySelectorAll('.sub-chart-wrapper').forEach((w, i) => {
            w.classList.toggle('active', i === index);
            w.style.borderColor = i === index ? 'var(--accent-blue)' : 'var(--border-color)';
            w.style.boxShadow = i === index ? 'inset 0 0 0 1px var(--accent-blue)' : 'none';
        });
        
        const chartObj = this.charts.find(c => c.index === index);
        if (chartObj && window.App) {
            // Sync top bar label to active pane's symbol (optional but helpful)
            // window.App.updateActiveSymbolInfo(chartObj.symbol);
        }
    }

    getActiveChart() {
        if (this.layout === '1x1') return null;
        return this.charts.find(c => c.index === this.activeIndex);
    }

    setupGrid(layout) {
        if (!this.overlay) return;
        this.overlay.innerHTML = '';
        this.charts = [];
        
        const [cols, rows] = layout.split('x').map(Number);
        this.overlay.style.gridTemplateColumns = `repeat(${cols}, 1fr)`;
        this.overlay.style.gridTemplateRows = `repeat(${rows}, 1fr)`;
        this.overlay.style.gap = '8px';
        this.overlay.style.padding = '8px';
        this.overlay.style.background = '#0a0b0e';

        const count = cols * rows;
        const currentSymbol = typeof App !== 'undefined' ? App.currentSymbol : 'BTCUSDT';
        const currentInterval = typeof App !== 'undefined' ? App.currentInterval : '1h';

        for (let i = 0; i < count; i++) {
            const chartWrapper = document.createElement('div');
            chartWrapper.className = 'sub-chart-wrapper';
            chartWrapper.style.cssText = `
                position: relative; border: 1px solid var(--border-color);
                border-radius: 8px; overflow: hidden; display: flex;
                flex-direction: column; background: #131722; cursor: pointer;
                transition: border-color 0.2s;
            `;
            chartWrapper.onclick = () => this.focusPane(i);
            
            const header = document.createElement('div');
            header.style.cssText = `
                height: 32px; background: #1c202b; 
                display: flex; align-items: center; padding: 0 10px;
                border-bottom: 1px solid var(--border-color);
                justify-content: space-between; z-index: 10;
            `;
            header.innerHTML = `
                <div style="display:flex; align-items:center; gap:8px;">
                    <div style="cursor:pointer; display:flex; align-items:center; gap:4px;" onclick="event.stopPropagation(); MultiChart.openSearch(${i})">
                        <span id="pane-sym-${i}" style="font-size:11px; font-weight:800; color:var(--accent-blue);">${currentSymbol}</span>
                        <i class="fas fa-search" style="font-size:9px; opacity:0.6; margin-left:2px;"></i>
                    </div>
                    <div class="pane-tf-selector" style="position:relative; cursor:pointer;" onclick="event.stopPropagation(); MultiChart.toggleTfMenu(${i})">
                        <span id="pane-int-${i}" style="font-size:9px; color:var(--text-muted); background:rgba(255,255,255,0.05); padding:2px 6px; border-radius:4px; border:1px solid rgba(255,255,255,0.1);">${currentInterval.toUpperCase()}</span>
                        <div id="pane-tf-menu-${i}" class="pane-tf-dropdown" style="display:none; position:absolute; top:100%; left:0; background:#1c202b; border:1px solid var(--border-color); border-radius:4px; z-index:1000; min-width:70px; box-shadow:0 4px 12px rgba(0,0,0,0.5); margin-top:4px; overflow:hidden;">
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '1m')">1m</div>
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '5m')">5m</div>
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '15m')">15m</div>
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '1h')">1H</div>
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '4h')">4H</div>
                            <div class="pane-tf-item" onclick="event.stopPropagation(); MultiChart.changePaneInterval(${i}, '1d')">1D</div>
                        </div>
                    </div>
                </div>
                <div style="display:flex; gap:4px;">
                    <button class="btn-icon" style="width:20px; height:20px;" onclick="event.stopPropagation(); MultiChart.openSearch(${i})" title="Change Symbol">
                        <i class="fas fa-exchange-alt" style="font-size:10px; opacity:0.7;"></i>
                    </button>
                </div>
            `;
            
            const chartDiv = document.createElement('div');
            chartDiv.id = `chart-sub-${i}`;
            chartDiv.style.flex = '1';
            chartDiv.style.width = '100%';
            
            const searchOverlay = document.createElement('div');
            searchOverlay.id = `pane-search-${i}`;
            searchOverlay.style.cssText = `
                position: absolute; inset: 0; background: rgba(10,11,14,0.98);
                display: none; flex-direction: column; padding: 15px; z-index: 2000;
                backdrop-filter: blur(15px);
            `;
            searchOverlay.innerHTML = `
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <span style="font-size:11px; font-weight:700; letter-spacing:0.5px; color:var(--text-muted);">SELECT SYMBOL</span>
                    <button class="btn-icon" onclick="event.stopPropagation(); MultiChart.closeSearch(${i})"><i class="fas fa-times"></i></button>
                </div>
                <div style="position:relative;">
                    <input type="text" placeholder="Search markets (e.g. RELIANCE, BTC)..." 
                        oninput="MultiChart.handleSearch(event, ${i})"
                        onclick="event.stopPropagation()"
                        style="width:100%; padding:10px 12px; background:var(--bg-secondary); border:1px solid var(--accent-blue); border-radius:6px; color:white; font-size:13px; outline:none; margin-bottom:10px;">
                    <i class="fas fa-search" style="position:absolute; right:12px; top:12px; color:var(--accent-blue); font-size:12px;"></i>
                </div>
                <div id="pane-results-${i}" style="flex:1; overflow-y:auto; margin-top:5px; border-top:1px solid rgba(255,255,255,0.05);"></div>
            `;
            
            chartWrapper.appendChild(header);
            chartWrapper.appendChild(chartDiv);
            chartWrapper.appendChild(searchOverlay);
            this.overlay.appendChild(chartWrapper);
            
            this.initSubChart(chartDiv, i, currentSymbol, currentInterval);
        }
        
        // Add styles for the new dropdown
        if (!document.getElementById('multi-chart-extra-styles')) {
            const style = document.createElement('style');
            style.id = 'multi-chart-extra-styles';
            style.textContent = `
                .pane-tf-item {
                    padding: 8px 12px; font-size: 11px; color: #d1d4dc; cursor: pointer;
                    transition: background 0.2s; border-bottom: 1px solid rgba(255,255,255,0.03);
                }
                .pane-tf-item:hover { background: var(--accent-blue); color: white; }
                .pane-tf-item:last-child { border-bottom: none; }
                .sub-chart-wrapper.active { border-color: var(--accent-blue) !important; box-shadow: 0 0 0 1px var(--accent-blue) !important; }
            `;
            document.head.appendChild(style);
        }

        window.showNotification(`Switched to ${layout} layout`);
    }

    toggleTfMenu(index) {
        const menu = document.getElementById(`pane-tf-menu-${index}`);
        if (!menu) return;
        
        // Close others
        document.querySelectorAll('.pane-tf-dropdown').forEach(m => {
            if (m.id !== `pane-tf-menu-${index}`) m.style.display = 'none';
        });
        
        menu.style.display = menu.style.display === 'none' ? 'block' : 'none';
        
        // Close on outside click
        const closeMenu = (e) => {
            if (!menu.contains(e.target)) {
                menu.style.display = 'none';
                document.removeEventListener('click', closeMenu);
            }
        };
        if (menu.style.display === 'block') {
            setTimeout(() => document.addEventListener('click', closeMenu), 10);
        }
    }

    async initSubChart(container, index, symbol, interval) {
        if (typeof LightweightCharts === 'undefined') return;

        const chart = LightweightCharts.createChart(container, {
            layout: { background: { color: '#131722' }, textColor: '#d1d4dc' },
            grid: { vertLines: { visible: false }, horzLines: { color: 'rgba(42, 46, 57, 0.05)' } },
            timeScale: { borderColor: '#2a2e39', timeVisible: true },
            rightPriceScale: { borderColor: '#2a2e39' }
        });

        const series = chart.addCandlestickSeries({
            upColor: '#089981', downColor: '#f23645',
            wickUpColor: '#089981', wickDownColor: '#f23645',
        });

        const chartObj = { chart, series, index, symbol, interval, indicators: {} };
        this.charts.push(chartObj);

        Datafeed.fetchOHLC(symbol, interval, 100).then(data => {
            if (data && data.length > 0) {
                series.setData(data);
                chart.timeScale().fitContent();
            }
        });

        const ro = new ResizeObserver(() => {
            if (container.clientWidth > 0) chart.resize(container.clientWidth, container.clientHeight);
        });
        ro.observe(container);
    }

    openSearch(index) {
        const overlay = document.getElementById(`pane-search-${index}`);
        if (overlay) {
            overlay.style.display = 'flex';
            overlay.querySelector('input').focus();
        }
    }

    closeSearch(index) {
        const overlay = document.getElementById(`pane-search-${index}`);
        if (overlay) overlay.style.display = 'none';
    }

    async handleSearch(event, index) {
        const query = event.target.value.trim();
        const resultsDiv = document.getElementById(`pane-results-${index}`);
        if (!query || query.length < 2) { resultsDiv.innerHTML = ''; return; }

        try {
            const data = await Datafeed.searchSymbols(query);
            resultsDiv.innerHTML = '';
            if (data && data.length > 0) {
                data.forEach(item => {
                    const div = document.createElement('div');
                    div.style.cssText = `padding: 6px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); cursor: pointer;`;
                    div.innerHTML = `<div style="font-size:11px; font-weight:700;">${item.symbol}</div><div style="font-size:9px; color:#8b8f9a;">${item.name}</div>`;
                    div.onclick = () => this.changePaneSymbol(index, item.symbol);
                    resultsDiv.appendChild(div);
                });
            }
        } catch (e) { console.error(e); }
    }

    async changePaneSymbol(index, symbol) {
        const chartObj = this.charts.find(c => c.index === index);
        if (!chartObj) return;

        chartObj.symbol = symbol;
        const label = document.getElementById(`pane-sym-${index}`);
        if (label) label.textContent = symbol;

        try {
            const data = await Datafeed.fetchOHLC(symbol, chartObj.interval, 100);
            if (data && data.length > 0) {
                chartObj.series.setData(data);
                chartObj.chart.timeScale().fitContent();
            }
            this.closeSearch(index);
        } catch (e) { window.showError(`Load failed: ${symbol}`); }
    }

    async changePaneInterval(index, interval) {
        const chartObj = this.charts.find(c => c.index === index);
        if (!chartObj) return;

        chartObj.interval = interval;
        const label = document.getElementById(`pane-int-${index}`);
        if (label) label.textContent = interval.toUpperCase();

        try {
            const data = await Datafeed.fetchOHLC(chartObj.symbol, interval, 100);
            if (data && data.length > 0) {
                chartObj.series.setData(data);
                chartObj.chart.timeScale().fitContent();
            }
        } catch (e) { window.showError(`Interval change failed: ${interval}`); }
    }

    addIndicator(index, id, name, data, color, type = 'line', subType = 'overlay') {
        const chartObj = this.charts.find(c => c.index === index);
        if (!chartObj) return;

        const chart = chartObj.chart;
        const subSeries = [];

        try {
            switch (type) {
                case 'macd':
                    const m = chart.addLineSeries({ color: '#2962FF', lineWidth: 2, priceScaleId: 'macd', lastValueVisible: true, scaleMargins: { top: 0.75, bottom: 0 } });
                    const s = chart.addLineSeries({ color: '#FF9800', lineWidth: 1.5, priceScaleId: 'macd', lastValueVisible: true, scaleMargins: { top: 0.75, bottom: 0 } });
                    const h = chart.addHistogramSeries({ priceScaleId: 'macd', lastValueVisible: false, scaleMargins: { top: 0.75, bottom: 0 } });
                    m.setData(data.macd || []); s.setData(data.signal || []);
                    h.setData((data.histogram || []).map(i => ({ time: i.time, value: i.value, color: i.value >= 0 ? 'rgba(38, 166, 154, 0.6)' : 'rgba(239, 83, 80, 0.6)' })));
                    subSeries.push(m, s, h);
                    break;

                case 'bollinger':
                case 'keltner':
                case 'donchian':
                    const mainColor = color || '#9B59B6';
                    const u = chart.addLineSeries({ color: mainColor, lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                    const mid = chart.addLineSeries({ color: mainColor, lineWidth: 2, lastValueVisible: true, priceLineVisible: false });
                    const l = chart.addLineSeries({ color: mainColor, lineWidth: 1, lastValueVisible: false, priceLineVisible: false });
                    u.setData(data.upper || []); mid.setData(data.middle || []); l.setData(data.lower || []);
                    subSeries.push(u, mid, l);
                    break;

                case 'psar':
                    const psar = chart.addLineSeries({ color: '#2962FF', lineWidth: 0, lastValueVisible: false });
                    psar.setData((data.sar || []).map(i => ({ time: i.time, value: i.value })));
                    psar.setMarkers((data.sar || []).map(i => ({ time: i.time, position: 'inBar', color: '#2962FF', shape: 'circle', size: 0.5 })));
                    subSeries.push(psar);
                    break;

                case 'ichimoku':
                    const conv = chart.addLineSeries({ color: '#089981', lineWidth: 1.5, lastValueVisible: false });
                    const base = chart.addLineSeries({ color: '#F23645', lineWidth: 1.5, lastValueVisible: false });
                    const sa = chart.addLineSeries({ color: 'rgba(8, 153, 129, 0.3)', lineWidth: 1, lastValueVisible: false });
                    const sb = chart.addLineSeries({ color: 'rgba(242, 54, 69, 0.3)', lineWidth: 1, lastValueVisible: false });
                    conv.setData(data.conversion || []); base.setData(data.base || []);
                    sa.setData(data.spanA || []); sb.setData(data.spanB || []);
                    subSeries.push(conv, base, sa, sb);
                    break;

                case 'stoch':
                case 'stochrsi':
                    const scaleId = `stoch_${id}`;
                    chart.priceScale(scaleId).applyOptions({ autoScale: true, scaleMargins: { top: 0.8, bottom: 0.02 }, visible: true });
                    
                    const k = chart.addLineSeries({ color: '#2962FF', lineWidth: 1.5, priceScaleId: scaleId });
                    const d_s = chart.addLineSeries({ color: '#FF9800', lineWidth: 1.5, priceScaleId: scaleId });
                    k.setData(data.k || []); d_s.setData(data.d || []);
                    
                    // Add background band (80-20)
                    const bg = chart.addHistogramSeries({ priceScaleId: scaleId, lastValueVisible: false, color: 'rgba(41,98,255,0.05)' });
                    bg.setData((data.k || []).map(i => ({ time: i.time, value: 100, color: 'rgba(41,98,255,0.03)' })));
                    
                    k.createPriceLine({ price: 80, color: 'rgba(120, 123, 134, 0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
                    k.createPriceLine({ price: 20, color: 'rgba(120, 123, 134, 0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: true });
                    
                    subSeries.push(k, d_s, bg);
                    break;

                case 'adx':
                    const adxScale = `adx_${id}`;
                    const adxLine = chart.addLineSeries({ color: '#FF9800', lineWidth: 2, priceScaleId: adxScale, scaleMargins: { top: 0.75, bottom: 0 } });
                    const pdi = chart.addLineSeries({ color: '#089981', lineWidth: 1, priceScaleId: adxScale, scaleMargins: { top: 0.75, bottom: 0 } });
                    const mdi = chart.addLineSeries({ color: '#F23645', lineWidth: 1, priceScaleId: adxScale, scaleMargins: { top: 0.75, bottom: 0 } });
                    adxLine.setData(data.adx || []); pdi.setData(data.plus_di || []); mdi.setData(data.minus_di || []);
                    subSeries.push(adxLine, pdi, mdi);
                    break;

                case 'supertrend':
                    const stLine = chart.addLineSeries({ lineWidth: 3, lastValueVisible: true, priceLineVisible: false });
                    stLine.setData(data.map(i => ({ time: i.time, value: i.value, color: i.dir === 1 ? '#089981' : '#F23645' })));
                    subSeries.push(stLine);
                    break;

                case 'sr_levels':
                    // Simplified S&R for multi-chart: just the primary levels as price lines
                    const levels = [...(data.support || []), ...(data.resistance || [])];
                    const priceLines = [];
                    levels.forEach(lvl => {
                        const price = Number(lvl?.price);
                        if (!Number.isFinite(price) || price <= 0) return;
                        const pl = chartObj.series.createPriceLine({
                            price,
                            color: lvl.type === 'resistance' ? '#00FF44' : '#FF0000',
                            lineWidth: 2,
                            lineStyle: 2, // Dashed
                            axisLabelVisible: true,
                            title: String(lvl.type || 'SR').toUpperCase().slice(0,3)
                        });
                        priceLines.push(pl);
                    });
                    chartObj.indicators[id] = { type: 'sr_levels', priceLines };
                    return;

                default:
                    // Standard Line Indicator
                    const isSubpane = subType === 'subpane' || subType === 'multi_subpane';
                    const seriesData = Array.isArray(data) ? data : (data.data || []);
                    const line = chart.addLineSeries({
                        color: color || '#2962ff',
                        lineWidth: 2,
                        title: name,
                        priceScaleId: isSubpane ? `scale_${id}` : 'right',
                        scaleMargins: isSubpane ? { top: 0.75, bottom: 0 } : undefined
                    });
                    line.setData(seriesData);
                    subSeries.push(line);
            }

            chartObj.indicators[id] = { type, subSeries };

        } catch (e) {
            console.error(`[MultiChart] Error adding indicator ${type}:`, e);
        }
    }

    removeIndicator(index, id) {
        const chartObj = this.charts.find(c => c.index === index);
        if (!chartObj || !chartObj.indicators[id]) return;

        const ind = chartObj.indicators[id];
        if (ind.subSeries) {
            ind.subSeries.forEach(s => chartObj.chart.removeSeries(s));
        }
        if (ind.priceLines) {
            ind.priceLines.forEach(pl => chartObj.series.removePriceLine(pl));
        }
        delete chartObj.indicators[id];
    }

    updateLiveTick(tick) {
        if (this.layout === '1x1') return;
        this.charts.forEach(c => {
            if (c.symbol === tick.symbol) {
                try {
                    c.series.update({
                        time: tick.timeRounded || tick.timestamp,
                        open: tick.price, high: tick.price, low: tick.price, close: tick.price
                    });
                } catch(e) {}
            }
        });
    }

    updateActiveButton(layout) {
        document.querySelectorAll('.layout-btn').forEach(btn => {
            const isMatch = btn.getAttribute('onclick')?.includes(layout);
            btn.classList.toggle('active', isMatch);
        });
    }

    syncAll(type) {
        if (this.layout === '1x1' || this.charts.length === 0) return;
        
        const activeChart = this.charts.find(c => c.index === this.activeIndex);
        if (!activeChart) return;

        if (type === 'symbol') {
            const symbol = activeChart.symbol;
            this.charts.forEach(c => {
                if (c.index !== this.activeIndex) this.changePaneSymbol(c.index, symbol);
            });
            window.showNotification(`All charts synced to ${symbol}`);
        } else if (type === 'interval') {
            const interval = activeChart.interval;
            this.charts.forEach(c => {
                if (c.index !== this.activeIndex) this.changePaneInterval(c.index, interval);
            });
            window.showNotification(`All charts synced to ${interval.toUpperCase()}`);
        }
    }
}

if (typeof window !== 'undefined') {
    window.MultiChart = new MultiChartManager('multi-chart-overlay');
}
