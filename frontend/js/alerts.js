/**
 * TradeView Pro - Alerts Engine
 * Handles alert creation, polling, and notifications.
 */

class AlertsEngine {
    constructor() {
        this.alerts = [];
        this.checkInterval = 10000; // 10 seconds
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.startPolling();
        this.requestNotificationPermission();
    }

    setupEventListeners() {
        document.addEventListener('click', (e) => {
            if (e.target.closest('#btn-create-alert')) {
                this.showCreateAlertModal();
            }
            
            const deleteBtn = e.target.closest('.btn-delete-alert');
            if (deleteBtn) {
                const alertId = deleteBtn.dataset.id;
                this.deleteAlert(alertId);
            }
        });
    }

    requestNotificationPermission() {
        if ('Notification' in window) {
            Notification.requestPermission();
        }
    }

    showCreateAlertModal() {
        const symbol = window.currentSymbol || 'RELIANCE';
        const price = window.currentPrice || 0;
        const savedEmail = localStorage.getItem('alert_email') || '';
        
        const modalHtml = `
            <div id="alert-modal" class="modal-overlay">
                <div class="modal-content">
                    <h3>Create Alert for ${symbol}</h3>
                    <div class="form-group">
                        <label>Condition</label>
                        <select id="alert-condition">
                            <option value="crossing">Crossing</option>
                            <option value="greater_than">Greater Than</option>
                            <option value="less_than">Less Than</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Value</label>
                        <input type="number" id="alert-value" value="${price}" step="0.01">
                    </div>
                    <div class="form-group">
                        <label>Email Notification (Optional)</label>
                        <input type="email" id="alert-email" value="${savedEmail}" placeholder="your@email.com">
                        <small style="color:var(--text-secondary); display:block; margin-top:4px;">Get notified when alert triggers</small>
                    </div>
                    <div class="modal-actions">
                        <button id="btn-save-alert" class="btn-primary">Create</button>
                        <button id="btn-cancel-alert" class="btn-secondary">Cancel</button>
                    </div>
                </div>
            </div>
        `;
        
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        document.getElementById('btn-save-alert').onclick = () => this.saveAlert(symbol);
        document.getElementById('btn-cancel-alert').onclick = () => document.getElementById('alert-modal').remove();
    }

    async saveAlert(symbol) {
        const condition = document.getElementById('alert-condition').value;
        const value = document.getElementById('alert-value').value;
        const email = document.getElementById('alert-email').value.trim();
        
        if (email) {
            localStorage.setItem('alert_email', email);
        }
        
        const alertData = {
            symbol: symbol,
            condition: condition,
            value: parseFloat(value),
            email: email,
            triggered: false
        };
        
        try {
            const response = await fetch('/api/alerts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(alertData)
            });
            const result = await response.json();
            if (result.success) {
                console.log('Alert created:', result.alert);
                document.getElementById('alert-modal').remove();
                this.updateAlertsList();
                if (window.showNotification) window.showNotification("Alert created successfully");
            }
        } catch (err) {
            console.error('Failed to create alert:', err);
            if (window.showError) window.showError("Failed to create alert");
        }
    }

    async deleteAlert(alertId) {
        const id = parseInt(alertId);
        
        // Hand-to-hand (Optimistic) UI Update
        this.alerts = this.alerts.filter(a => a.id !== id);
        this.updateAlertsUI();
        
        // Visual confirmation
        if (window.showNotification) {
            window.showNotification("Alert deleted successfully");
        }

        try {
            const response = await fetch(`/api/alerts/${id}`, {
                method: 'DELETE'
            });
            // Final sync with server
            this.updateAlertsList();
        } catch (err) {
            console.error('Failed to delete alert:', err);
            if (window.showError) window.showError("Sync error. Please refresh.");
        }
    }

    startPolling() {
        setInterval(() => this.checkAlerts(), this.checkInterval);
    }

    async checkAlerts() {
        try {
            const response = await fetch('/api/check-alerts');
            const data = await response.json();
            
            if (data.triggered && data.triggered.length > 0) {
                data.triggered.forEach(alert => {
                    this.notifyUser(alert);
                });
            }
            this.alerts = data.alerts;
            this.updateAlertsUI();
        } catch (err) {
            console.error('Error checking alerts:', err);
        }
    }

    notifyUser(alert) {
        const title = `Alert Triggered: ${alert.symbol}`;
        const message = `${alert.symbol} ${alert.condition} ${alert.value}`;
        
        // Browser Notification
        if ('Notification' in window && Notification.permission === 'granted') {
            new Notification(title, { body: message });
        }
        
        // In-app Notification
        this.showToast(title, message);
    }

    showToast(title, message) {
        const toast = document.createElement('div');
        toast.className = 'alert-toast';
        toast.innerHTML = `<strong>${title}</strong><p>${message}</p>`;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }

    updateAlertsUI() {
        const container = document.getElementById('alerts-container');
        if (!container) return;
        
        if (this.alerts.length === 0) {
            container.innerHTML = '<p class="empty-msg">No alerts set.</p>';
            return;
        }
        
        container.innerHTML = this.alerts.map(a => `
            <div class="alert-item ${a.triggered ? 'triggered' : ''}" style="position:relative;">
                <div class="alert-info">
                    <span class="symbol">${a.symbol}</span>
                    <span class="condition">${a.condition} ${a.value}</span>
                    ${a.email ? `<div style="font-size:10px; opacity:0.6;"><i class="fas fa-envelope"></i> ${a.email}</div>` : ''}
                </div>
                <div style="display:flex; align-items:center; gap:10px;">
                    <div class="alert-status">${a.triggered ? 'Triggered' : 'Active'}</div>
                    <button class="btn-delete-alert" data-id="${a.id}" 
                            style="background:none; border:none; color:var(--text-secondary); cursor:pointer; padding:4px;">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </div>
            </div>
        `).join('');
    }

    updateAlertsList() {
        this.checkAlerts();
    }
}

window.alertsEngine = new AlertsEngine();
