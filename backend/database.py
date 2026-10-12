import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional


def default_data_dir() -> Path:
    return Path(os.getenv("FUTURE_APP_DATA_DIR", Path(__file__).parent / "data"))


def database_path(data_dir: Optional[str] = None, database_url: Optional[str] = None) -> Path:
    url = database_url or os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1)).expanduser()
    root = Path(data_dir) if data_dir else default_data_dir()
    return root / "future_app.sqlite3"


# ---------------------------------------------------------------------------
# Versioned migrations. Each entry: (version, name, list of SQL statements).
# Statements must be idempotent (IF NOT EXISTS) so re-runs are safe.
# ---------------------------------------------------------------------------
MIGRATIONS: list[tuple[int, str, tuple[str, ...]]] = [
    (
        1,
        "job infrastructure",
        (
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,             -- ok | error | skipped_locked
                detail TEXT,
                duration_ms INTEGER
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_job_runs_name_started
                ON job_runs(job_name, started_at DESC);
            """,
            """
            CREATE TABLE IF NOT EXISTS job_leases (
                job_name TEXT PRIMARY KEY,
                lease_until TEXT NOT NULL,        -- UTC ISO instant
                holder TEXT NOT NULL,             -- process/host token
                acquired_at TEXT NOT NULL
            );
            """,
        ),
    ),
    (
        2,
        "market core tables",
        (
            """
            CREATE TABLE IF NOT EXISTS companies (
                company_id TEXT PRIMARY KEY,               -- stable UUID
                company_name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                sector TEXT,
                listing_date TEXT,                          -- earliest known listing (ISO date)
                status TEXT NOT NULL DEFAULT 'active',      -- active | delisted | merged
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS listings (
                listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id TEXT NOT NULL REFERENCES companies(company_id),
                exchange TEXT NOT NULL CHECK (exchange IN ('NSE','BSE')),
                symbol TEXT NOT NULL,
                bse_code TEXT,
                isin TEXT,
                series TEXT,
                status TEXT NOT NULL DEFAULT 'listed',      -- listed | delisted | suspended
                listed_on TEXT,
                delisted_on TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(exchange, symbol)
            );
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_listings_isin ON listings(isin);
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_listings_company ON listings(company_id);
            """,
            """
            CREATE TABLE IF NOT EXISTS symbol_aliases (
                alias TEXT PRIMARY KEY,                     -- e.g. RELIANCE.NS, ^NSEI, old symbol
                exchange TEXT,
                symbol TEXT NOT NULL,                       -- canonical exchange symbol
                created_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS indices (
                index_id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,                -- canonical: NIFTY 50, SENSEX, NIFTY 500...
                name TEXT NOT NULL,
                exchange TEXT NOT NULL,
                universe INTEGER NOT NULL DEFAULT 0         -- 1 = gainers/losers universe (NIFTY 500)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS index_constituents (
                index_id INTEGER NOT NULL REFERENCES indices(index_id),
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT 'NSE',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (index_id, symbol, exchange)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS quotes_latest (
                symbol TEXT PRIMARY KEY,                    -- canonical exchange symbol
                exchange TEXT,
                last REAL,
                prev_close REAL,
                change REAL,
                change_pct REAL,
                open REAL,
                high REAL,
                low REAL,
                volume INTEGER,
                value REAL,
                as_of TEXT,                                  -- provider timestamp
                fetched_at TEXT NOT NULL,                    -- our fetch time (UTC ISO)
                provider TEXT NOT NULL,
                price_kind TEXT NOT NULL DEFAULT 'ltp',      -- ltp | indicative | close
                session TEXT,
                is_stale INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS price_history (
                symbol TEXT NOT NULL,
                exchange TEXT,
                interval TEXT NOT NULL,                      -- 1d | 1w | 1m ...
                ts INTEGER NOT NULL,                         -- epoch seconds (UTC)
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL,
                adjusted INTEGER NOT NULL DEFAULT 0,         -- 1 = adjusted for corp actions
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, ts)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS history_coverage (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                first_ts INTEGER,
                last_ts INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, interval)
            );
            """,
        ),
    ),
    (
        3,
        "ipo module",
        (
            """
            CREATE TABLE IF NOT EXISTS ipos (
                ipo_id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT UNIQUE,                      -- provider-independent identity
                company_id TEXT REFERENCES companies(company_id),
                name TEXT NOT NULL,
                ipo_type TEXT,                               -- Mainboard | SME
                status TEXT NOT NULL DEFAULT 'Upcoming',
                open_date TEXT,
                close_date TEXT,
                allotment_date TEXT,
                listing_date TEXT,
                price_band_low REAL,
                price_band_high REAL,
                issue_price REAL,
                lot_size INTEGER,
                issue_size_cr REAL,
                fresh_issue_cr REAL,
                ofs_cr REAL,
                exchanges TEXT,                              -- JSON list
                isin TEXT,
                listing_price_nse REAL,
                listing_price_bse REAL,
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ipo_subscription_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ipo_id INTEGER NOT NULL REFERENCES ipos(ipo_id),
                total_x REAL,
                qib_x REAL,
                nii_x REAL,
                retail_x REAL,
                as_of TEXT NOT NULL,
                source TEXT,
                UNIQUE(ipo_id, as_of, source)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ipo_gmp_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ipo_id INTEGER NOT NULL REFERENCES ipos(ipo_id),
                gmp_rupees REAL NOT NULL,
                gmp_pct REAL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE(ipo_id, as_of, source)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS ipo_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ipo_id INTEGER NOT NULL REFERENCES ipos(ipo_id),
                event_type TEXT NOT NULL,                    -- status transition or info event
                from_status TEXT,
                to_status TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            """,
        ),
    ),
    (
        4,
        "ops, registry, review, notifications",
        (
            """
            CREATE TABLE IF NOT EXISTS provider_status (
                provider TEXT PRIMARY KEY,
                last_success_at TEXT,
                last_failure_at TEXT,
                last_error TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                circuit_state TEXT NOT NULL DEFAULT 'closed'
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS interest_registry (
                symbol TEXT PRIMARY KEY,                     -- canonical exchange symbol
                exchange TEXT,
                added_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,                          -- duplicate_candidate | ipo_symbol_pending | ...
                payload TEXT NOT NULL,                       -- JSON
                score REAL,
                resolved INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS notification_prefs (
                user_key TEXT NOT NULL,                      -- user id or browser id
                event_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,          -- everything OFF by default
                email_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_key, event_type)
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT NOT NULL,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT,
                dedupe_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                read_at TEXT,
                emailed_at TEXT,
                UNIQUE(user_key, dedupe_key)
            );
            """,
        ),
    ),
    (
        5,
        "seed index definitions",
        (
            """
            INSERT OR IGNORE INTO indices (symbol, name, exchange, universe) VALUES
                ('NIFTY 50', 'NIFTY 50', 'NSE', 0),
                ('BANK NIFTY', 'NIFTY BANK', 'NSE', 0),
                ('FIN NIFTY', 'NIFTY FINANCIAL SERVICES', 'NSE', 0),
                ('MIDCPNIFTY', 'NIFTY MIDCAP SELECT', 'NSE', 0),
                ('NIFTY 500', 'NIFTY 500', 'NSE', 1),
                ('NIFTY MIDCAP 100', 'NIFTY MIDCAP 100', 'NSE', 0),
                ('NIFTY SMALLCAP 100', 'NIFTY SMALLCAP 100', 'NSE', 0),
                ('INDIA VIX', 'India VIX', 'NSE', 0),
                ('SENSEX', 'S&P BSE SENSEX', 'BSE', 0);
            """,
        ),
    ),
]


class Database:
    def __init__(self, data_dir: Optional[str] = None, database_url: Optional[str] = None):
        self.path = database_path(data_dir=data_dir, database_url=database_url)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._apply_pragmas()
        self.init_schema()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _apply_pragmas(self) -> None:
        """WAL lets background jobs and request handlers write concurrently."""
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payment_id TEXT,
                    order_id TEXT,
                    activated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    grace_until TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS payment_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    plan_id TEXT,
                    order_id TEXT,
                    payment_id TEXT,
                    event_type TEXT NOT NULL,
                    payload TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
                CREATE INDEX IF NOT EXISTS idx_payment_events_order ON payment_events(order_id);
                """
            )
            conn.commit()
        self.run_migrations()

    def run_migrations(self, logger: Optional[Callable[[str], None]] = None) -> list[int]:
        """Apply pending migrations in order; safe to call repeatedly."""
        applied: list[int] = []
        with self.connect() as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            done = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
            for version, name, statements in MIGRATIONS:
                if version in done:
                    continue
                for statement in statements:
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (version, name),
                )
                conn.commit()
                applied.append(version)
                if logger:
                    logger(f"migration {version} applied: {name}")
        return applied

    def migration_version(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            return int(row["v"] or 0)
