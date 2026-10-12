const SubscriptionUI = (() => {
  let authState = { authenticated: false, user: null, subscription: null };
  let plans = [];

  async function init() {
    await refresh();
    render();
  }

  async function refresh() {
    try {
      const [me, planRes] = await Promise.all([
        fetch('/api/auth/me').then(r => r.json()),
        fetch('/api/subscription/plans').then(r => r.json())
      ]);
      authState = me;
      plans = planRes.plans || [];
    } catch (e) {
      console.warn('Subscription state unavailable', e);
    }
  }

  function render() {
    const root = document.getElementById('subscription-root');
    if (!root) return;
    const sub = authState.subscription || { tier: 'free', features: [] };
    root.innerHTML = `
      <div class="account-card">
        <div class="learning-kicker">Account</div>
        <h4>${authState.authenticated ? authState.user.email : 'Guest student'}</h4>
        <p>${authState.authenticated ? `Current tier: ${sub.tier}` : 'Login is required for subscriptions and progress sync.'}</p>
        ${authState.authenticated ? `
          <button class="btn-secondary" onclick="SubscriptionUI.logout()">Logout</button>
        ` : renderAuthForm()}
      </div>
      <div class="pricing-list">
        ${plans.map(plan => renderPlan(plan, sub.tier)).join('')}
      </div>
      <div class="account-disclaimer">
        Payments unlock simulator lessons and educational features only. This is not investment advice and does not provide real-money trading recommendations.
      </div>
    `;
  }

  function renderAuthForm() {
    return `
      <div class="auth-form">
        <input id="auth-email" type="email" placeholder="Email" autocomplete="email">
        <input id="auth-password" type="password" placeholder="Password (min 8 chars)" autocomplete="current-password">
        <div class="auth-actions">
          <button class="btn-primary" onclick="SubscriptionUI.login()">Login</button>
          <button class="btn-secondary" onclick="SubscriptionUI.register()">Register</button>
        </div>
      </div>
    `;
  }

  function renderPlan(plan, currentTier) {
    const active = plan.id === currentTier;
    const paid = Number(plan.price_inr || 0) > 0;
    return `
      <div class="pricing-card ${active ? 'active' : ''}">
        <div class="lesson-topline"><span>${active ? 'Current' : 'Plan'}</span><strong>${paid ? 'Premium' : 'Free'}</strong></div>
        <h4>${plan.name}</h4>
        <div class="plan-price">${paid ? `₹${plan.price_inr}/month` : 'Free'}</div>
        <ul>${(plan.features || []).map(f => `<li>${featureLabel(f)}</li>`).join('')}</ul>
        ${paid ? `<button class="btn-primary" ${active ? 'disabled' : ''} onclick="SubscriptionUI.upgrade('${plan.id}')">${active ? 'Active' : 'Upgrade with UPI'}</button>` : ''}
      </div>
    `;
  }

  function featureLabel(feature) {
    return feature.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  }

  function credentials() {
    return {
      email: document.getElementById('auth-email')?.value || '',
      password: document.getElementById('auth-password')?.value || ''
    };
  }

  async function register() {
    await authPost('/api/auth/register', credentials(), 'Registered');
  }

  async function login() {
    await authPost('/api/auth/login', credentials(), 'Logged in');
  }

  async function logout() {
    await fetch('/api/auth/logout', { method: 'POST' });
    await refresh();
    render();
  }

  async function authPost(url, body, successText) {
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Auth failed');
      window.showNotification?.(successText, 'success');
      await refresh();
      render();
    } catch (e) {
      window.showError?.(e.message);
    }
  }

  async function upgrade(planId) {
    if (!authState.authenticated) {
      window.showError?.('Login before upgrading.');
      return;
    }
    window.location.href = `/checkout?plan=${encodeURIComponent(planId)}`;
  }

  return { init, refresh, render, register, login, logout, upgrade };
})();

window.SubscriptionUI = SubscriptionUI;
