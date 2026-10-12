/**
 * Native Session Detection Logic (IST only)
 * Includes NSE/BSE official holiday list for 2025 & 2026.
 */
const SessionManager = (() => {

  // ─── NSE Official Market Holidays ───────────────────────────────────────────
  // Source: NSE Circular NSE/CMTR/65587 (2025) & NSE/CMTR/71775 (2026)
  // Format: "YYYY-MM-DD" (IST date, weekday holidays only)
  const NSE_HOLIDAYS = new Set([
    // 2025
    "2025-02-26",  // Maha Shivaratri
    "2025-03-14",  // Holi
    "2025-03-31",  // Id-Ul-Fitr
    "2025-04-10",  // Shri Mahavir Jayanti
    "2025-04-14",  // Dr. Ambedkar Jayanti
    "2025-04-18",  // Good Friday
    "2025-05-01",  // Maharashtra Day
    "2025-06-27",  // Eid-Ul-Adha
    "2025-08-15",  // Independence Day
    "2025-08-27",  // Janmashtami
    "2025-10-02",  // Mahatma Gandhi Jayanti
    "2025-10-21",  // Diwali Laxmi Pujan
    "2025-10-22",  // Diwali Balipratipada
    "2025-11-05",  // Prakash Gurpurab Shri Guru Nanak Dev
    "2025-12-25",  // Christmas
    // 2026
    "2026-01-26",  // Republic Day
    "2026-02-17",  // Maha Shivaratri
    "2026-03-04",  // Holi
    "2026-03-20",  // Id-Ul-Fitr
    "2026-03-30",  // Shri Mahavir Jayanti
    "2026-04-02",  // Dr. Ambedkar Jayanti (observed)
    "2026-04-03",  // Good Friday
    "2026-05-01",  // Maharashtra Day
    "2026-06-17",  // Eid-Ul-Adha (tentative)
    "2026-06-26",  // Muharram
    "2026-08-26",  // Janmashtami
    "2026-10-02",  // Mahatma Gandhi Jayanti
    "2026-11-09",  // Diwali Balipratipada
    "2026-11-24",  // Prakash Gurpurab Shri Guru Nanak Dev
    "2026-12-25",  // Christmas
  ]);

  /**
   * Returns the current IST date-time components.
   */
  function getCurrentIST() {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Kolkata',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).formatToParts(new Date());
    const values = Object.fromEntries(parts.map(p => [p.type, p.value]));
    return {
      year:    Number(values.year),
      month:   Number(values.month),
      day:     Number(values.day),
      hour:    Number(values.hour),
      minute:  Number(values.minute),
      second:  Number(values.second),
      weekday: new Intl.DateTimeFormat('en-US', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short',
      }).format(new Date()),
      // ISO date string in IST (YYYY-MM-DD) for holiday lookup
      dateStr: `${values.year}-${values.month}-${values.day}`,
    };
  }

  /**
   * Returns true if today is an NSE market holiday (non-weekend declared holiday).
   * @param {string} dateStr - "YYYY-MM-DD"
   */
  function isHoliday(dateStr) {
    return NSE_HOLIDAYS.has(dateStr);
  }

  /**
   * Returns the holiday name for the given date (or null if not a holiday).
   * @param {string} dateStr - "YYYY-MM-DD"
   */
  function getHolidayName(dateStr) {
    const names = {
      "2025-02-26": "Maha Shivaratri",
      "2025-03-14": "Holi",
      "2025-03-31": "Id-Ul-Fitr",
      "2025-04-10": "Shri Mahavir Jayanti",
      "2025-04-14": "Dr. Ambedkar Jayanti",
      "2025-04-18": "Good Friday",
      "2025-05-01": "Maharashtra Day",
      "2025-06-27": "Eid-Ul-Adha",
      "2025-08-15": "Independence Day",
      "2025-08-27": "Janmashtami",
      "2025-10-02": "Gandhi Jayanti",
      "2025-10-21": "Diwali Laxmi Pujan",
      "2025-10-22": "Diwali Balipratipada",
      "2025-11-05": "Guru Nanak Jayanti",
      "2025-12-25": "Christmas",
      "2026-01-26": "Republic Day",
      "2026-02-17": "Maha Shivaratri",
      "2026-03-04": "Holi",
      "2026-03-20": "Id-Ul-Fitr",
      "2026-03-30": "Shri Mahavir Jayanti",
      "2026-04-02": "Dr. Ambedkar Jayanti",
      "2026-04-03": "Good Friday",
      "2026-05-01": "Maharashtra Day",
      "2026-06-17": "Eid-Ul-Adha",
      "2026-06-26": "Muharram",
      "2026-08-26": "Janmashtami",
      "2026-10-02": "Gandhi Jayanti",
      "2026-11-09": "Diwali Balipratipada",
      "2026-11-24": "Guru Nanak Jayanti",
      "2026-12-25": "Christmas",
    };
    return names[dateStr] || null;
  }

  /**
   * Returns the current NSE trading session type.
   * @returns {'PRE_MARKET'|'REGULAR'|'POST_MARKET'|'HOLIDAY'|'CLOSED'}
   */
  function getSession() {
    const ist = getCurrentIST();

    // ── 1. Weekend ──────────────────────────────────────────────────────────
    if (ist.weekday === 'Sun' || ist.weekday === 'Sat') return 'CLOSED';

    // ── 2. NSE Public Holiday ───────────────────────────────────────────────
    if (isHoliday(ist.dateStr)) return 'HOLIDAY';

    // ── 3. Time-based session (market hours, IST) ───────────────────────────
    const mins = ist.hour * 60 + ist.minute;
    if (mins >= 540 && mins <  555) return 'PRE_MARKET';   // 09:00–09:15
    if (mins >= 555 && mins <  930) return 'REGULAR';      // 09:15–15:30
    if (mins >= 930 && mins <  960) return 'POST_MARKET';  // 15:30–16:00
    return 'CLOSED';
  }

  /**
   * Returns a human-readable description of today's market status.
   * @returns {{ session: string, label: string, holidayName: string|null }}
   */
  function getSessionInfo() {
    const ist     = getCurrentIST();
    const session = getSession();
    const holidayName = (session === 'HOLIDAY') ? getHolidayName(ist.dateStr) : null;
    const labels  = {
      PRE_MARKET:  'Pre-Market',
      REGULAR:     'Market Open',
      POST_MARKET: 'Post-Market',
      HOLIDAY:     holidayName ? `Holiday · ${holidayName}` : 'Market Holiday',
      CLOSED:      'Market Closed',
    };
    return { session, label: labels[session] || 'Market Closed', holidayName };
  }

  return { getSession, getSessionInfo, isHoliday, getHolidayName };
})();
