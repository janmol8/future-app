/**
 * Datafeed module - fetches OHLC data from the backend API.
 */
const Datafeed = (() => {
  // Use relative paths for API calls
  const BASE_URL = '';

  /**
   * Fetch OHLC candles for a symbol.
   * @param {string} symbol - Stock or crypto symbol
   * @param {string} interval - 1m, 5m, 1h, 1d, etc.
   * @param {number} range - Number of days to fetch
   * @returns {Promise<Array>} List of candle objects
   */
  async function fetchOHLC(symbol, interval = '1d', range = 365) {
    const url = `${BASE_URL}/api/ohlc?symbol=${encodeURIComponent(symbol)}&interval=${interval}&range=${range}`;
    const resp = await fetch(url);
    const data = await resp.json();

    if (data.error) {
      throw new Error(data.error);
    }

    return data.candles || [];
  }

  /**
   * Search for symbols.
   * @param {string} query
   * @returns {Promise<Array>} List of matching symbols
   */
  async function searchSymbols(query) {
    const url = `${BASE_URL}/api/symbols?query=${encodeURIComponent(query)}`;
    const resp = await fetch(url);
    return resp.json();
  }

  /**
   * Get current quote for a symbol.
   * @param {string} symbol
   * @returns {Promise<Object>} Quote data
   */
  async function fetchQuote(symbol) {
    try {
      const url = `${BASE_URL}/api/quote?symbol=${encodeURIComponent(symbol)}`;
      const resp = await fetch(url);
      const data = await resp.json();
      if (!resp.ok || data.error || !Number.isFinite(Number(data.price))) {
        return null;
      }
      return data;
    } catch (e) {
      console.error("Quote fetch failed:", e);
      return null;
    }
  }

  async function fetchPreOpen(symbol, category = 'ALL') {
    try {
      const path = symbol
        ? `/api/market/preopen/${encodeURIComponent(symbol)}?category=${encodeURIComponent(category)}`
        : `/api/market/preopen?category=${encodeURIComponent(category)}`;
      const resp = await fetch(`${BASE_URL}${path}`);
      return await resp.json();
    } catch (e) {
      console.error("Pre-open fetch failed:", e);
      return { status: 'error', message: 'Pre-market data request failed', data: null };
    }
  }

  /**
   * Calculate a technical indicator.
   * @param {string} symbol
   * @param {string} interval
   * @param {string} type - sma, ema, rsi, macd, bollinger
   * @param {number} period - Period for the indicator
   * @returns {Promise<Object>} Indicator data
   */
  async function fetchIndicator(symbol, interval, type, period = 20) {
    const url = `${BASE_URL}/api/indicators?symbol=${encodeURIComponent(symbol)}&interval=${interval}&type=${type}&period=${period}`;
    const resp = await fetch(url);
    const data = await resp.json();

    if (data.error) {
      throw new Error(data.error);
    }

    return data;
  }

  /**
   * Fetch OHLC data with a custom interval.
   * @param {string} symbol
   * @param {string} interval - Custom interval string
   * @param {number} range - Days of history
   * @returns {Promise<Array>} Candles
   */
  async function fetchOHLCCustom(symbol, interval, range = 365) {
    // Standardizing to use the main ohlc endpoint as it now supports custom intervals
    return fetchOHLC(symbol, interval, range);
  }

  /**
   * Get list of all available markets.
   * @returns {Promise<Object>} {stocks: [...], crypto: [...]}
   */
  async function fetchMarkets() {
    const url = `${BASE_URL}/api/markets`;
    const resp = await fetch(url);
    return resp.json();
  }

  /**
   * Check if an interval string is a custom interval (not a standard one).
   * @param {string} interval
   * @returns {boolean}
   */
  function isCustomInterval(interval) {
    const standard = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1M'];
    return !standard.includes(interval);
  }

  /**
   * Parse a custom interval string to its numeric value in seconds.
   * @param {string} interval - e.g. '30s', '45m', '2h', '7d', '3w'
   * @returns {number} Interval in seconds
   */
  function parseIntervalToSeconds(interval) {
    const match = interval.match(/^(\d+)\s*([smhdwM])/);
    if (!match) return 86400; // default 1d
    const num = parseInt(match[1]);
    const unit = match[2].toLowerCase();
    const multipliers = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 };
    if (match[2] === 'M') return num * 2592000;
    return num * (multipliers[unit] || 86400);
  }

  /**
   * Fetch OHLC with an offset for loading more historical data.
   * @param {string} symbol
   * @param {string} interval
   * @param {number} range - Days of data to fetch
   * @param {number} offset - Offset in days from the present (for pagination)
   * @returns {Promise<Array>} Candles
   */
  async function fetchOHLCWithOffset(symbol, interval, range, offset) {
    const url = `${BASE_URL}/api/ohlc?symbol=${encodeURIComponent(symbol)}&interval=${interval}&range=${range}&offset=${offset}`;
    const resp = await fetch(url);
    const data = await resp.json();
    if (data.error) throw new Error(data.error);
    return data.candles || [];
  }

  return {
    fetchOHLC,
    fetchOHLCCustom,
    fetchOHLCWithOffset,
    searchSymbols,
    fetchQuote,
    fetchPreOpen,
    fetchIndicator,
    fetchMarkets,
    isCustomInterval,
    parseIntervalToSeconds,
  };
})();
