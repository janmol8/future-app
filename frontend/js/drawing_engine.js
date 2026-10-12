/**
 * TradeView Pro - Drawing Engine
 * Manages trendlines, shapes, and measurements via SVG overlay.
 */
class DrawingEngine {
    constructor(containerId, chartModule) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.chart = chartModule.chart;
        this.chartModule = chartModule;
        this.svg = null;
        this.drawings = [];
        this.activeTool = null;
        this.isDrawing = false;
        this.currentDrawing = null;
        
        this.initSVG();
        this.bindEvents();
        this.loadDrawings();
        this.initFibSettings();
    }

    initFibSettings() {
        this.fibLevels = [
            { level: 0, color: '#787b86', enabled: true },
            { level: 0.236, color: '#f23645', enabled: true },
            { level: 0.382, color: '#81c784', enabled: true },
            { level: 0.5, color: '#4caf50', enabled: true },
            { level: 0.618, color: '#009688', enabled: true },
            { level: 0.786, color: '#2196f3', enabled: true },
            { level: 1, color: '#787b86', enabled: true },
            { level: 1.618, color: '#2962ff', enabled: false },
            { level: 2.618, color: '#2962ff', enabled: false },
        ];
        this.fibShowDiagonal = true;
        this.fibExtendLines = true;
    }

    loadDrawings() {
        const saved = localStorage.getItem('tv_drawings');
        if (saved) {
            try {
                this.drawings = JSON.parse(saved);
                this.requestRedraw();
            } catch (e) { console.error("Failed to load drawings", e); }
        }
    }

    initSVG() {
        // Ensure no duplicate SVG
        const existing = this.container.querySelector('.drawing-svg-layer');
        if (existing) existing.remove();

        this.svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        this.svg.classList.add('drawing-svg-layer');
        this.svg.style.position = "absolute";
        this.svg.style.top = "0";
        this.svg.style.left = "0";
        this.svg.style.width = "100%";
        this.svg.style.height = "100%";
        this.svg.style.pointerEvents = "none";
        this.svg.style.zIndex = "50"; // Above chart, but below some UI
        this.container.appendChild(this.svg);
    }

    bindEvents() {
        // We listen on the container but only act if tool is active
        this.container.addEventListener('mousedown', (e) => this.handleMouseDown(e));
        this.container.addEventListener('mousemove', (e) => this.handleMouseMove(e));
        this.container.addEventListener('mouseup', (e) => this.handleMouseUp(e));
        
        // Update on chart scroll/zoom
        if (this.chart) {
            this.chart.timeScale().subscribeVisibleTimeRangeChange(() => this.requestRedraw());
        }

        // Toolbar Toggle
        const toggleBtn = document.getElementById('btn-toggle-drawing');
        const toolbar = document.querySelector('.drawing-toolbar');
        if (toggleBtn && toolbar) {
            toggleBtn.addEventListener('click', () => {
                const isHidden = toolbar.style.display === 'none';
                toolbar.style.display = isHidden ? 'flex' : 'none';
                toggleBtn.classList.toggle('active', !isHidden);
                window.showNotification(isHidden ? "Drawing Toolbar Visible" : "Drawing Toolbar Hidden");
            });
        }
    }

    setTool(tool) {
        if (this.activeTool === tool && tool === 'fib') {
            this.showFibSettings();
        }
        this.activeTool = tool;
        
        // UI Feedback
        document.querySelectorAll('.dt-item').forEach(btn => btn.classList.remove('active'));
        if (tool) {
            const btn = document.getElementById(`tool-${tool}`);
            if (btn) btn.classList.add('active');
            this.container.style.cursor = "crosshair";
            this.svg.style.pointerEvents = "auto";
        } else {
            const cursorBtn = document.getElementById('tool-cursor');
            if (cursorBtn) cursorBtn.classList.add('active');
            this.container.style.cursor = "default";
            this.svg.style.pointerEvents = "none";
        }

        console.log(`[Drawing] Active tool: ${tool || 'None'}`);
    }

    showFibSettings() {
        const dialog = document.getElementById('fib-settings-dialog');
        const container = document.getElementById('fib-levels-container');
        if (!dialog || !container) return;

        container.innerHTML = '';
        this.fibLevels.forEach((lvl, i) => {
            const div = document.createElement('div');
            div.style.cssText = `display:flex; align-items:center; gap:10px; margin-bottom:8px;`;
            div.innerHTML = `
                <input type="checkbox" ${lvl.enabled ? 'checked' : ''} onchange="Drawing.fibLevels[${i}].enabled = this.checked" />
                <span style="font-size:12px; width:40px;">${lvl.level}</span>
                <input type="color" value="${lvl.color}" onchange="Drawing.fibLevels[${i}].color = this.value" style="width:30px; height:20px; border:none; background:transparent;" />
            `;
            container.appendChild(div);
        });

        document.getElementById('fib-show-diagonal').checked = this.fibShowDiagonal;
        document.getElementById('fib-extend-lines').checked = this.fibExtendLines;
        dialog.style.display = 'block';
    }

    applyFibSettings() {
        this.fibShowDiagonal = document.getElementById('fib-show-diagonal').checked;
        this.fibExtendLines = document.getElementById('fib-extend-lines').checked;
        document.getElementById('fib-settings-dialog').style.display = 'none';
        this.requestRedraw();
        window.showNotification("Fibonacci Settings Applied");
    }

    handleMouseDown(e) {
        if (!this.activeTool) return;
        
        const rect = this.container.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        const time = this.chart.timeScale().coordinateToTime(x);
        const price = this.chartModule.currentSeries().coordinateToPrice(y);
        
        if (price === null || time === null) return;

        this.isDrawing = true;
        this.currentDrawing = {
            type: this.activeTool,
            points: [{ x, y, time, price }],
            color: '#2962ff',
            width: 2,
            settings: this.activeTool === 'fib' ? {
                levels: JSON.parse(JSON.stringify(this.fibLevels)),
                showDiagonal: this.fibShowDiagonal,
                extendLines: this.fibExtendLines
            } : null
        };

        e.preventDefault();
        e.stopPropagation();
    }

    handleMouseMove(e) {
        if (!this.isDrawing || !this.currentDrawing) return;
        
        const rect = this.container.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;
        
        const time = this.chart.timeScale().coordinateToTime(x);
        const price = this.chartModule.currentSeries().coordinateToPrice(y);
        
        if (price === null || time === null) return;

        if (this.currentDrawing.points.length === 1) {
            this.currentDrawing.points.push({ x, y, time, price });
        } else {
            this.currentDrawing.points[1] = { x, y, time, price };
        }
        
        this.requestRedraw();
    }

    handleMouseUp() {
        if (this.isDrawing && this.currentDrawing) {
            if (this.currentDrawing.points.length === 2) {
                this.drawings.push(this.currentDrawing);
                localStorage.setItem('tv_drawings', JSON.stringify(this.drawings));
            }
            this.isDrawing = false;
            this.currentDrawing = null;
        }
    }

    requestRedraw() {
        if (!this.svg) return;
        requestAnimationFrame(() => this.draw());
    }

    draw() {
        if (!this.svg) return;
        while (this.svg.firstChild) this.svg.removeChild(this.svg.firstChild);
        
        const allDrawings = [...this.drawings];
        if (this.isDrawing && this.currentDrawing) allDrawings.push(this.currentDrawing);
        
        allDrawings.forEach(d => {
            if (d.points.length < 2) return;
            
            const p1 = d.points[0];
            const p2 = d.points[1];
            
            // Re-calculate screen coordinates from time/price for persistence
            const x1 = this.chart.timeScale().timeToCoordinate(p1.time);
            const y1 = this.chartModule.currentSeries().priceToCoordinate(p1.price);
            const x2 = this.chart.timeScale().timeToCoordinate(p2.time);
            const y2 = this.chartModule.currentSeries().priceToCoordinate(p2.price);

            if (x1 === null || y1 === null || x2 === null || y2 === null) return;

            if (d.type === 'trendline') {
                const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
                line.setAttribute("x1", x1);
                line.setAttribute("y1", y1);
                line.setAttribute("x2", x2);
                line.setAttribute("y2", y2);
                line.setAttribute("stroke", d.color);
                line.setAttribute("stroke-width", d.width);
                this.svg.appendChild(line);
            } else if (d.type === 'fib') {
                this.drawFib(x1, y1, x2, y2, d.settings || { levels: this.fibLevels, showDiagonal: this.fibShowDiagonal, extendLines: this.fibExtendLines });
            }
        });
    }

    drawFib(x1, y1, x2, y2, settings) {
        const dy = y2 - y1;
        const width = this.container.clientWidth;

        if (settings.showDiagonal) {
            const diag = document.createElementNS("http://www.w3.org/2000/svg", "line");
            diag.setAttribute("x1", x1);
            diag.setAttribute("y1", y1);
            diag.setAttribute("x2", x2);
            diag.setAttribute("y2", y2);
            diag.setAttribute("stroke", "#787b86");
            diag.setAttribute("stroke-width", "1");
            diag.setAttribute("stroke-dasharray", "4");
            this.svg.appendChild(diag);
        }
        
        settings.levels.forEach(lvl => {
            if (!lvl.enabled) return;
            const currY = y1 + dy * lvl.level;
            
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", settings.extendLines ? 0 : Math.min(x1, x2));
            line.setAttribute("y1", currY);
            line.setAttribute("x2", settings.extendLines ? width : Math.max(x1, x2));
            line.setAttribute("y2", currY);
            line.setAttribute("stroke", lvl.color);
            line.setAttribute("stroke-width", "1");
            line.setAttribute("opacity", "0.8");
            this.svg.appendChild(line);
            
            const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
            text.setAttribute("x", (settings.extendLines ? width - 50 : Math.max(x1, x2) + 5));
            text.setAttribute("y", currY - 4);
            text.setAttribute("fill", lvl.color);
            text.setAttribute("font-size", "10px");
            text.setAttribute("font-weight", "bold");
            text.textContent = `${lvl.level} (${(lvl.level * 100).toFixed(1)}%)`;
            this.svg.appendChild(text);
        });
    }

    clear() {
        this.drawings = [];
        localStorage.removeItem('tv_drawings');
        this.requestRedraw();
    }
}

// Global accessibility
if (typeof window !== 'undefined') {
    window.DrawingEngine = DrawingEngine;
}
