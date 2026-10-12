/**
 * Symbol search module - handles the search input and dropdown.
 * Features: grouped results (stocks / indices / crypto), text highlighting, keyboard nav.
 */
const SymbolSearch = (() => {
  let searchTimeout = null;
  let selectedIndex = -1;
  let currentResults = [];

  const searchInput = () => document.getElementById('symbol-search');
  const searchDropdown = () => document.getElementById('search-dropdown');

  /**
   * Initialize the search functionality.
   * @param {Function} onSelect - Callback when a symbol is selected
   */
  function init(onSelect) {
    const input = searchInput();
    const dropdown = searchDropdown();

    if (!input || !dropdown) return;

    // Input event with debounce
    input.addEventListener('input', () => {
      clearTimeout(searchTimeout);
      const query = input.value.trim();

      if (query.length < 1) {
        dropdown.classList.remove('active');
        return;
      }

      searchTimeout = setTimeout(async () => {
        try {
          const results = await Datafeed.searchSymbols(query);
          currentResults = results;
          renderDropdown(results, query, onSelect);
        } catch (e) {
          console.error('Search error:', e);
          if (window.showError) {
            window.showError('Search failed. Check backend connection.');
          }
        }
      }, 150);
    });

    // Keyboard navigation
    input.addEventListener('keydown', (e) => {
      const items = dropdown.querySelectorAll('.search-item');

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex = Math.min(selectedIndex + 1, items.length - 1);
        updateSelection(items);
        scrollToSelected(items);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex = Math.max(selectedIndex - 1, 0);
        updateSelection(items);
        scrollToSelected(items);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < currentResults.length) {
          onSelect(currentResults[selectedIndex]);
          closeDropdown();
        }
      } else if (e.key === 'Escape') {
        closeDropdown();
        input.blur();
      }
    });

    // Close on click outside
    document.addEventListener('click', (e) => {
      const wrapper = document.querySelector('.symbol-search-wrapper');
      if (wrapper && !wrapper.contains(e.target)) {
        closeDropdown();
      }
    });

    // Focus input on "/" key
    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && !e.ctrlKey && !e.metaKey) {
        const activeEl = document.activeElement;
        if (activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA')) return;
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  /**
   * Highlight matching text in a string.
   */
  function highlightText(text, query) {
    if (!query) return text;
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(`(${escaped})`, 'gi');
    return text.replace(regex, '<mark style="background:rgba(41,98,255,0.3);color:var(--text-primary);border-radius:2px;padding:0 1px;">$1</mark>');
  }

  /**
   * Render the search dropdown with grouped results.
   */
  function renderDropdown(results, query, onSelect) {
    const dropdown = searchDropdown();
    if (!dropdown) return;

    if (results.length === 0) {
      dropdown.innerHTML = `
        <div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">
          <i class="fas fa-search" style="font-size:18px;margin-bottom:8px;display:block;opacity:0.5;"></i>
          No results found for "${query}"
        </div>
      `;
      dropdown.classList.add('active');
      return;
    }

    dropdown.innerHTML = '';
    selectedIndex = -1;

    // Group results by type
    const groups = {
      index: { label: 'Indices', icon: 'fas fa-chart-line', class: 'sym-icon-index', items: [] },
      stock: { label: 'Stocks', icon: 'fas fa-building', class: 'sym-icon-stock', items: [] },
      forex: { label: 'Forex/Commodities', icon: 'fas fa-exchange-alt', class: 'sym-icon-forex', items: [] },
      commodity: { label: 'Forex/Commodities', icon: 'fas fa-exchange-alt', class: 'sym-icon-forex', items: [] },
      crypto: { label: 'Crypto', icon: 'fas fa-bitcoin-sign', class: 'sym-icon-crypto', items: [] },
    };
    const order = ['index', 'stock', 'forex', 'crypto'];
    let globalIdx = 0;

    results.forEach((item) => {
      const type = item.type || 'stock';
      if (groups[type]) {
        groups[type].items.push({ ...item, _idx: globalIdx++ });
      } else {
        groups['stock'].items.push({ ...item, _idx: globalIdx++ });
      }
    });

    let hasContent = false;
    order.forEach(type => {
      const group = groups[type];
      if (!group.items.length) return;
      hasContent = true;

      // Section header
      const header = document.createElement('div');
      header.className = 'search-section-header';
      header.innerHTML = `<i class="${group.icon} ${group.class}" style="font-size:10px;width:14px;"></i> ${group.label}`;
      dropdown.appendChild(header);

      // Items
      group.items.forEach((item) => {
        const div = document.createElement('div');
        div.className = 'search-item';
        div.dataset.idx = item._idx;

        const symHtml = highlightText(item.symbol, query);
        const nameHtml = highlightText(item.name || item.symbol, query);

        let badgeClass = 'stock';
        let badgeText = `${item.exchange || 'NSE'}`;
        if (type === 'crypto') {
          badgeClass = 'crypto';
          badgeText = 'CRYPTO';
        } else if (type === 'index') {
          badgeClass = 'index';
          badgeText = 'INDEX';
        } else if (type === 'forex' || type === 'commodity') {
          badgeClass = 'forex';
          badgeText = item.type.toUpperCase();
        }

        const cat = App.getSymbolCategoryInfo ? App.getSymbolCategoryInfo(item.symbol) : null;
        const logoHtml = (cat && cat.logo) ? 
            `<img src="${cat.logo}" class="sym-logo-small" style="width:20px; height:20px; margin-right:4px;" onerror="this.style.display='none'; this.nextElementSibling.style.display='block'">
             <i class="fas ${group.icon} ${group.class}" style="display:none; font-size:12px; width:20px; text-align:center; margin-right:4px;"></i>` :
            `<i class="fas ${group.icon} ${group.class}" style="font-size:12px; width:20px; text-align:center; margin-right:4px;"></i>`;

        div.innerHTML = `
          <div class="si-left">
            <div class="si-symbol">
              ${logoHtml}
              ${symHtml}
            </div>
            <div class="si-name">${nameHtml}</div>
          </div>
          <span class="si-badge ${badgeClass}">${badgeText}</span>
        `;

        div.addEventListener('click', () => {
          onSelect(item);
          closeDropdown();
        });
        div.addEventListener('mouseenter', () => {
          selectedIndex = item._idx;
          updateSelection(dropdown.querySelectorAll('.search-item'));
        });

        dropdown.appendChild(div);
      });
    });

    if (!hasContent) {
      dropdown.innerHTML = `
        <div style="padding:16px;text-align:center;color:var(--text-muted);font-size:12px;">
          No results found
        </div>
      `;
    }

    dropdown.classList.add('active');
  }

  function updateSelection(items) {
    items.forEach((item) => {
      const idx = parseInt(item.dataset.idx);
      if (idx === selectedIndex) {
        item.style.background = 'var(--bg-active)';
        // Add a subtle left border indicator
        item.style.borderLeft = '3px solid var(--accent-blue)';
      } else {
        item.style.background = '';
        item.style.borderLeft = '3px solid transparent';
      }
    });
  }

  function scrollToSelected(items) {
    for (const item of items) {
      const idx = parseInt(item.dataset.idx);
      if (idx === selectedIndex) {
        item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        break;
      }
    }
  }

  function closeDropdown() {
    const dropdown = searchDropdown();
    if (dropdown) {
      dropdown.classList.remove('active');
      dropdown.innerHTML = '';
    }
    selectedIndex = -1;
  }

  function setValue(val) {
    const input = searchInput();
    if (input) {
      input.value = val;
    }
  }

  function focus() {
    const input = searchInput();
    if (input) {
      input.focus();
      input.select();
    }
  }

  return {
    init,
    setValue,
    focus,
    closeDropdown,
  };
})();
