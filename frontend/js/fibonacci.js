/**
 * TradeView Pro - Fibonacci Tools
 * Specialized logic for Fibonacci Retracements and Extensions.
 */
const FibonacciModule = {
    levels: [
        { val: 0, color: '#787b86' },
        { val: 0.236, color: '#f23645' },
        { val: 0.382, color: '#ff9800' },
        { val: 0.5, color: '#00b973' },
        { val: 0.618, color: '#00b973' },
        { val: 0.786, color: '#2962ff' },
        { val: 1, color: '#787b86' }
    ],

    drawRetracement(ctx, x1, y1, x2, y2, svg) {
        const dy = y2 - y1;
        const width = Math.abs(x2 - x1);
        const startX = Math.min(x1, x2);
        const endX = Math.max(x1, x2);

        this.levels.forEach(lvl => {
            const currY = y1 + dy * lvl.val;
            
            const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
            line.setAttribute("x1", startX);
            line.setAttribute("y1", currY);
            line.setAttribute("x2", endX);
            line.setAttribute("y2", currY);
            line.setAttribute("stroke", lvl.color);
            line.setAttribute("stroke-width", "1");
            line.setAttribute("stroke-dasharray", "2");
            svg.appendChild(line);

            const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
            label.setAttribute("x", endX + 5);
            label.setAttribute("y", currY + 4);
            label.setAttribute("fill", lvl.color);
            label.setAttribute("font-size", "10px");
            label.setAttribute("font-family", "monospace");
            label.textContent = `${lvl.val.toFixed(3)} (${(lvl.val * 100).toFixed(1)}%)`;
            svg.appendChild(label);
            
            // Add a light background fill between levels
            // (Optional: can implement if requested, keeping it simple for now)
        });
    }
};
