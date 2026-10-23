"""
app/db.py — Универсальное подключение к БД (SQLite / PostgreSQL).

ПЕРЕКЛЮЧАТЕЛЬ — одна строка в коде:
    DB_ENGINE = 'sqlite'      ← тест/разработка (без установки)
    DB_ENGINE = 'postgresql'  ← продакшн

Или через переменную окружения / .env:
    DB_ENGINE=postgresql
"""
import os, sqlite3, threading, re
from contextlib import contextmanager

# ▼▼▼ ПЕРЕКЛЮЧАТЕЛЬ — меняй здесь ▼▼▼
DB_ENGINE = 'sqlite'
# ▲▲▲ или 'postgresql' для продакшна ▲▲▲

# Переменная окружения перезаписывает захардкоженное значение
_env = os.environ.get('DB_ENGINE', '').lower().strip()
if _env in ('postgresql', 'postgres', 'pg'):
    DB_ENGINE = 'postgresql'
elif _env == 'sqlite':
    DB_ENGINE = 'sqlite'

# ══════════════════════════════════════════════════════════════════════════════
#  SQLite
# ══════════════════════════════════════════════════════════════════════════════

_SQLITE_PATH = os.environ.get(
    'SQLITE_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'database', 'casino.db')
)
_sqlite_lock = threading.Lock()


class _SQLiteCursor:
    """
    Обёртка над sqlite3.Cursor.
    Эмулирует psycopg2 RealDictCursor:
      - параметры %s  → ?
      - fetchone/fetchall → dict
      - RETURNING *   → SELECT по lastrowid после INSERT
      - RETURNING col → SELECT после UPDATE
      - ::jsonb, ::JSONB → убираем
      - NOW()         → datetime('now')
    """
    def __init__(self, cursor: sqlite3.Cursor, conn: sqlite3.Connection):
        self._c    = cursor
        self._conn = conn

    def execute(self, sql: str, params=None):
        sql = sql.replace('%s', '?')
        sql = sql.replace('::jsonb', '').replace('::JSONB', '')
        sql = sql.replace("NOW()", "datetime('now')")

        # ── RETURNING * после INSERT ──────────────────────────────────────────
        returning_insert_table = None
        if re.search(r'RETURNING\s+\*', sql, re.IGNORECASE):
            m = re.search(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)', sql, re.IGNORECASE)
            if m:
                returning_insert_table = m.group(1)
            sql = re.sub(r'RETURNING\s+\*', '', sql, flags=re.IGNORECASE)

        # ── RETURNING <col> после UPDATE ──────────────────────────────────────
        returning_update_table = None
        returning_update_col   = None
        m_ret = re.search(r'RETURNING\s+(\w+)', sql, re.IGNORECASE)
        if m_ret:
            returning_update_col = m_ret.group(1)
            m_tbl = re.search(r'UPDATE\s+(\w+)', sql, re.IGNORECASE)
            if m_tbl:
                returning_update_table = m_tbl.group(1)
            sql = re.sub(r'RETURNING\s+\w+', '', sql, flags=re.IGNORECASE)

        # ── Выполняем ─────────────────────────────────────────────────────────
        if params:
            self._c.execute(sql, params)
        else:
            self._c.execute(sql)

        # ── Эмулируем RETURNING * — читаем вставленную строку ─────────────────
        if returning_insert_table and self._c.lastrowid:
            self._c.execute(
                f'SELECT * FROM {returning_insert_table} WHERE id = ?',
                (self._c.lastrowid,)
            )

        # ── Эмулируем RETURNING col — ищем WHERE id=? в оригинальном SQL ─────
        elif returning_update_table and returning_update_col:
            # Вытаскиваем id из WHERE id = ? (первый int-параметр в условии)
            uid = None
            if params:
                # Ищем первый параметр после WHERE id =
                m_id = re.search(r'WHERE\s+id\s*=\s*\?', sql, re.IGNORECASE)
                if m_id:
                    # считаем сколько ? до этого id
                    before = sql[:m_id.start()].count('?')
                    if before < len(params):
                        uid = params[before]
            if uid is not None:
                self._c.execute(
                    f'SELECT {returning_update_col} FROM {returning_update_table} WHERE id = ?',
                    (uid,)
                )
            else:
                # Fallback — SELECT изменённые строки (rowcount=1 обычно)
                self._c.execute(
                    f'SELECT {returning_update_col} FROM {returning_update_table} WHERE rowid = ?',
                    (self._c.lastrowid or 1,)
                )

        return self

    def fetchone(self):
        row = self._c.fetchone()
        if row is None:
            return None
        if hasattr(row, 'keys'):       # sqlite3.Row
            return dict(row)
        if self._c.description:
            cols = [d[0] for d in self._c.description]
            return dict(zip(cols, row))
        return None

    def fetchall(self):
        rows = self._c.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], 'keys'):
            return [dict(r) for r in rows]
        if self._c.description:
            cols = [d[0] for d in self._c.description]
            return [dict(zip(cols, r)) for r in rows]
        return []

    def __iter__(self):
        for row in self._c:
            if hasattr(row, 'keys'):
                yield dict(row)
            elif self._c.description:
                cols = [d[0] for d in self._c.description]
                yield dict(zip(cols, row))


@contextmanager
def _sqlite_get_db():
    os.makedirs(os.path.dirname(os.path.abspath(_SQLITE_PATH)), exist_ok=True)
    conn = sqlite3.connect(_SQLITE_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    cur = _SQLiteCursor(conn.cursor(), conn)
    try:
        with _sqlite_lock:
            yield cur
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sqlite_close():
    pass


# ══════════════════════════════════════════════════════════════════════════════
#  PostgreSQL
# ══════════════════════════════════════════════════════════════════════════════

_pg_pool = None

def _pg_get_pool():
    global _pg_pool
    if _pg_pool is None or _pg_pool.closed:
        from psycopg2 import pool as pg_pool_mod
        _pg_pool = pg_pool_mod.ThreadedConnectionPool(
            minconn  = int(os.environ.get('DB_MIN_CONN', '2')),
            maxconn  = int(os.environ.get('DB_MAX_CONN', '20')),
            host     = os.environ.get('DB_HOST',     'localhost'),
            port     = int(os.environ.get('DB_PORT', '5432')),
            dbname   = os.environ.get('DB_NAME',     'velox_casino'),
            user     = os.environ.get('DB_USER',     'velox'),
            password = os.environ.get('DB_PASSWORD', 'velox_secret'),
        )
    return _pg_pool


@contextmanager
def _pg_get_db():
    from psycopg2.extras import RealDictCursor
    conn = _pg_get_pool().getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pg_get_pool().putconn(conn)


def _pg_close():
    global _pg_pool
    if _pg_pool and not _pg_pool.closed:
        _pg_pool.closeall()
        _pg_pool = None


# ══════════════════════════════════════════════════════════════════════════════
#  Публичный интерфейс
# ══════════════════════════════════════════════════════════════════════════════

if DB_ENGINE == 'postgresql':
    get_db     = _pg_get_db
    close_pool = _pg_close
    print(f'  [DB] PostgreSQL -> {os.environ.get("DB_HOST","localhost")}:{os.environ.get("DB_PORT","5432")}/{os.environ.get("DB_NAME","velox_casino")}')
else:
    get_db     = _sqlite_get_db
    close_pool = _sqlite_close
    print(f'  [DB] SQLite -> {os.path.abspath(_SQLITE_PATH)}')


# ══════════════════════════════════════════════════════════════════════════════
#  DDL — схемы таблиц
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
#  SCHEMAS — PostgreSQL / Supabase
# ══════════════════════════════════════════════════════════════════════════════

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    uuid_user       UUID          NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    user_name       VARCHAR(64)   NOT NULL,
    email           VARCHAR(256)  UNIQUE,
    phone           VARCHAR(32)   UNIQUE,
    password        VARCHAR(128)  NOT NULL,
    two_fa          BOOLEAN       NOT NULL DEFAULT FALSE,
    two_fa_pass     VARCHAR(64),
    image           TEXT,
    icon_url        TEXT,
    balance         NUMERIC(18,2) NOT NULL DEFAULT 0.00,
    currency        VARCHAR(8)    NOT NULL DEFAULT 'UAH',
    verified        BOOLEAN       NOT NULL DEFAULT FALSE,
    ticket_used     BOOLEAN       NOT NULL DEFAULT FALSE,
    free_spins      INTEGER       NOT NULL DEFAULT 0,
    bonus_deposit   NUMERIC(18,2) NOT NULL DEFAULT 0.00,
    wagered_amount  NUMERIC(18,2) NOT NULL DEFAULT 0.00,
    used_promocodes JSONB         NOT NULL DEFAULT '[]'::jsonb,
    favorite_games  JSONB         NOT NULL DEFAULT '[]'::jsonb,
    kyc_status      VARCHAR(16)   NOT NULL DEFAULT 'none',
    kyc_word        TEXT,
    kyc_word_expires_at TIMESTAMPTZ,
    kyc_full_name   TEXT,
    kyc_dob         DATE,
    kyc_doc_path    TEXT,
    kyc_rejection_reason TEXT,
    kyc_submitted_at TIMESTAMPTZ,
    reset_token         VARCHAR(128),
    reset_token_expires TIMESTAMPTZ,
    ref_code            VARCHAR(16)   UNIQUE,
    referred_by         INTEGER       REFERENCES users(id),
    rg_deposit_daily_limit  NUMERIC(18,2),
    rg_deposit_weekly_limit NUMERIC(18,2),
    rg_self_excluded_until  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);

CREATE TABLE IF NOT EXISTS transactions (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       VARCHAR(32)   NOT NULL,
    amount     NUMERIC(18,2) NOT NULL,
    status     VARCHAR(16)   NOT NULL DEFAULT 'completed',
    meta       JSONB,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tx_user   ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_tx_type   ON transactions(type);
CREATE INDEX IF NOT EXISTS idx_tx_date   ON transactions(created_at);

CREATE TABLE IF NOT EXISTS game_history (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    game_id    VARCHAR(64)   NOT NULL,
    bet        NUMERIC(18,2) NOT NULL,
    multiplier NUMERIC(12,4),
    win        NUMERIC(18,2) NOT NULL DEFAULT 0,
    profit     NUMERIC(18,2) GENERATED ALWAYS AS (win - bet) STORED,
    meta       JSONB,
    created_at TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_gh_user ON game_history(user_id);
CREATE INDEX IF NOT EXISTS idx_gh_game ON game_history(game_id);
CREATE INDEX IF NOT EXISTS idx_gh_date ON game_history(created_at);

CREATE TABLE IF NOT EXISTS promo_codes (
    id           SERIAL PRIMARY KEY,
    code         VARCHAR(64)   NOT NULL UNIQUE,
    type         VARCHAR(32)   NOT NULL DEFAULT 'balance',
    bonus_value  NUMERIC(18,2) NOT NULL,
    currency     VARCHAR(8),
    min_deposit  NUMERIC(18,2) DEFAULT 0,
    max_uses     INTEGER       NOT NULL DEFAULT 1000,
    uses         INTEGER       NOT NULL DEFAULT 0,
    one_per_user BOOLEAN       NOT NULL DEFAULT TRUE,
    active       BOOLEAN       NOT NULL DEFAULT TRUE,
    expires_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS promo_uses (
    id       BIGSERIAL PRIMARY KEY,
    promo_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    used_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(promo_id, user_id)
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id                   SERIAL PRIMARY KEY,
    ticket_uuid          VARCHAR(8)   UNIQUE,
    user_id              INTEGER REFERENCES users(id) ON DELETE SET NULL,
    contact              VARCHAR(256),
    subject              VARCHAR(256),
    description          TEXT,
    status               VARCHAR(16)  NOT NULL DEFAULT 'open',
    tag                  TEXT,
    attachment_path      TEXT,
    assigned_to_id       INTEGER,
    assigned_to_nickname VARCHAR(128),
    assigned_to_public_id VARCHAR(32),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_st_user   ON support_tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_st_status ON support_tickets(status);

CREATE TABLE IF NOT EXISTS support_ticket_messages (
    id         BIGSERIAL    PRIMARY KEY,
    ticket_id  INTEGER      NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
    admin_id   INTEGER,
    sender     VARCHAR(16)  NOT NULL DEFAULT 'user',
    message    TEXT         NOT NULL,
    created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_stm_ticket ON support_ticket_messages(ticket_id);

CREATE TABLE IF NOT EXISTS payment_orders (
    id          BIGSERIAL    PRIMARY KEY,
    user_id     INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider    VARCHAR(32)  NOT NULL,
    external_id VARCHAR(256) UNIQUE,
    amount      NUMERIC(18,2) NOT NULL,
    currency    VARCHAR(8)   NOT NULL,
    type        VARCHAR(16)  NOT NULL,
    status      VARCHAR(32)  NOT NULL DEFAULT 'pending',
    meta        JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_po_user   ON payment_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_po_status ON payment_orders(status);
CREATE INDEX IF NOT EXISTS idx_po_type   ON payment_orders(type);

CREATE TABLE IF NOT EXISTS tournaments (
    id         BIGSERIAL PRIMARY KEY,
    slug       VARCHAR(64) UNIQUE,
    title      VARCHAR(128) NOT NULL,
    period     VARCHAR(16)  NOT NULL,
    prize_pool NUMERIC(18,2) NOT NULL DEFAULT 0,
    description TEXT         NOT NULL DEFAULT '',
    game_ids   JSONB         NOT NULL DEFAULT '[]'::jsonb,
    glow_color VARCHAR(16)   NOT NULL DEFAULT '#f5c451',
    starts_at  TIMESTAMPTZ NOT NULL,
    ends_at    TIMESTAMPTZ NOT NULL,
    status     VARCHAR(16)  NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tournaments_period_status ON tournaments(period, status, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS tournament_entries (
    id            BIGSERIAL PRIMARY KEY,
    tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    total_bet     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_win     NUMERIC(18,2) NOT NULL DEFAULT 0,
    total_profit  NUMERIC(18,2) NOT NULL DEFAULT 0,
    rounds        INTEGER NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tournament_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_tournament_entries_tour ON tournament_entries(tournament_id, total_win DESC);

CREATE TABLE IF NOT EXISTS sport_idempotency (
    id         BIGSERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    idem_key   VARCHAR(128) NOT NULL,
    response   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, idem_key)
);
CREATE INDEX IF NOT EXISTS idx_sport_idem_created ON sport_idempotency(created_at);

CREATE TABLE IF NOT EXISTS sport_audit_logs (
    id         BIGSERIAL PRIMARY KEY,
    actor_user_id INTEGER,
    action     VARCHAR(64) NOT NULL,
    entity_id  VARCHAR(128),
    payload    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sport_audit_created ON sport_audit_logs(created_at);
"""

_PG_SEED = """
INSERT INTO promo_codes (code, type, bonus_value, max_uses)
VALUES
    ('WELCOME100', 'balance', 100, 10000),
    ('BONUS50',    'balance',  50, 10000),
    ('VELOX200',   'balance', 200,  5000)
ON CONFLICT (code) DO NOTHING;
"""

# ══════════════════════════════════════════════════════════════════════════════
#  SCHEMAS — SQLite (dev / small deploy)
# ══════════════════════════════════════════════════════════════════════════════

_SQLITE_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid_user       TEXT    NOT NULL DEFAULT (lower(hex(randomblob(16)))) UNIQUE,
        user_name       TEXT    NOT NULL,
        email           TEXT    UNIQUE,
        phone           TEXT    UNIQUE,
        password        TEXT    NOT NULL,
        two_fa          INTEGER NOT NULL DEFAULT 0,
        two_fa_pass     TEXT,
        image           TEXT,
        icon_url        TEXT,
        balance         REAL    NOT NULL DEFAULT 0.0,
        currency        TEXT    NOT NULL DEFAULT 'UAH',
        verified        INTEGER NOT NULL DEFAULT 0,
        ticket_used     INTEGER NOT NULL DEFAULT 0,
        free_spins      INTEGER NOT NULL DEFAULT 0,
        bonus_deposit   REAL    NOT NULL DEFAULT 0.0,
        wagered_amount  REAL    NOT NULL DEFAULT 0.0,
        used_promocodes TEXT    NOT NULL DEFAULT '[]',
        favorite_games  TEXT    NOT NULL DEFAULT '[]',
        kyc_status      TEXT    NOT NULL DEFAULT 'none',
        kyc_word        TEXT,
        kyc_word_expires_at TEXT,
        kyc_full_name   TEXT,
        kyc_dob         TEXT,
        kyc_doc_path    TEXT,
        kyc_rejection_reason TEXT,
        kyc_submitted_at TEXT,
        reset_token         TEXT,
        reset_token_expires TEXT,
        ref_code            TEXT UNIQUE,
        referred_by         INTEGER,
        rg_deposit_daily_limit  REAL,
        rg_deposit_weekly_limit REAL,
        rg_self_excluded_until  TEXT,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)",
    "CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)",

    """CREATE TABLE IF NOT EXISTS transactions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        type       TEXT    NOT NULL,
        amount     REAL    NOT NULL,
        status     TEXT    NOT NULL DEFAULT 'completed',
        meta       TEXT,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type)",
    "CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(created_at)",

    """CREATE TABLE IF NOT EXISTS game_history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        game_id    TEXT    NOT NULL,
        bet        REAL    NOT NULL,
        multiplier REAL,
        win        REAL    NOT NULL DEFAULT 0,
        meta       TEXT,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_gh_user ON game_history(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_gh_game ON game_history(game_id)",
    "CREATE INDEX IF NOT EXISTS idx_gh_date ON game_history(created_at)",

    """CREATE TABLE IF NOT EXISTS promo_codes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        code         TEXT    NOT NULL UNIQUE,
        type         TEXT    NOT NULL DEFAULT 'balance',
        bonus_value  REAL    NOT NULL,
        currency     TEXT,
        min_deposit  REAL    DEFAULT 0,
        max_uses     INTEGER NOT NULL DEFAULT 1000,
        uses         INTEGER NOT NULL DEFAULT 0,
        one_per_user INTEGER NOT NULL DEFAULT 1,
        active       INTEGER NOT NULL DEFAULT 1,
        expires_at   TEXT,
        created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",

    """CREATE TABLE IF NOT EXISTS promo_uses (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        promo_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
        user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        used_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(promo_id, user_id)
    )""",

    """CREATE TABLE IF NOT EXISTS support_tickets (
        id                    INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_uuid           TEXT    UNIQUE,
        user_id               INTEGER REFERENCES users(id) ON DELETE SET NULL,
        contact               TEXT,
        subject               TEXT,
        description           TEXT,
        status                TEXT    NOT NULL DEFAULT 'open',
        tag                   TEXT,
        attachment_path       TEXT,
        assigned_to_id        INTEGER,
        assigned_to_nickname  TEXT,
        assigned_to_public_id TEXT,
        created_at            TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_st_user   ON support_tickets(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_st_status ON support_tickets(status)",

    """CREATE TABLE IF NOT EXISTS support_ticket_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id  INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
        admin_id   INTEGER,
        sender     TEXT    NOT NULL DEFAULT 'user',
        message    TEXT    NOT NULL,
        created_at TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_stm_ticket ON support_ticket_messages(ticket_id)",

    """CREATE TABLE IF NOT EXISTS payment_orders (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        provider    TEXT    NOT NULL,
        external_id TEXT    UNIQUE,
        amount      REAL    NOT NULL,
        currency    TEXT    NOT NULL,
        type        TEXT    NOT NULL,
        status      TEXT    NOT NULL DEFAULT 'pending',
        meta        TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_po_user   ON payment_orders(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_po_status ON payment_orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_po_type   ON payment_orders(type)",
    """CREATE TABLE IF NOT EXISTS tournaments (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        slug        TEXT UNIQUE,
        title       TEXT NOT NULL,
        period      TEXT NOT NULL,
        prize_pool  REAL NOT NULL DEFAULT 0,
        description TEXT NOT NULL DEFAULT '',
        game_ids    TEXT NOT NULL DEFAULT '[]',
        glow_color  TEXT NOT NULL DEFAULT '#f5c451',
        starts_at   TEXT NOT NULL,
        ends_at     TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'active',
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tournaments_period_status ON tournaments(period, status, starts_at, ends_at)",
    """CREATE TABLE IF NOT EXISTS tournament_entries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
        user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        total_bet     REAL NOT NULL DEFAULT 0,
        total_win     REAL NOT NULL DEFAULT 0,
        total_profit  REAL NOT NULL DEFAULT 0,
        rounds        INTEGER NOT NULL DEFAULT 0,
        updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(tournament_id, user_id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tournament_entries_tour ON tournament_entries(tournament_id, total_win DESC)",
    """CREATE TABLE IF NOT EXISTS sport_idempotency (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        idem_key   TEXT NOT NULL,
        response   TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(user_id, idem_key)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sport_idem_created ON sport_idempotency(created_at)",
    """CREATE TABLE IF NOT EXISTS sport_audit_logs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_user_id INTEGER,
        action     TEXT NOT NULL,
        entity_id  TEXT,
        payload    TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_sport_audit_created ON sport_audit_logs(created_at)",
]

_SQLITE_SEED = [
    ("INSERT OR IGNORE INTO promo_codes (code,type,bonus_value,max_uses) VALUES (?,?,?,?)",
     ('WELCOME100','balance',100,10000)),
    ("INSERT OR IGNORE INTO promo_codes (code,type,bonus_value,max_uses) VALUES (?,?,?,?)",
     ('BONUS50',   'balance', 50,10000)),
    ("INSERT OR IGNORE INTO promo_codes (code,type,bonus_value,max_uses) VALUES (?,?,?,?)",
     ('VELOX200',  'balance',10000, 5000)),
]

# ── Migrations for existing databases ─────────────────────────────────────────
# These run safely on old DBs — errors are silently ignored (column already exists)
_SQLITE_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN icon_url TEXT",
    "ALTER TABLE users ADD COLUMN ticket_used INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN free_spins INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN bonus_deposit REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE users ADD COLUMN wagered_amount REAL NOT NULL DEFAULT 0.0",
    "ALTER TABLE users ADD COLUMN used_promocodes TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE users ADD COLUMN favorite_games TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE users ADD COLUMN kyc_status TEXT NOT NULL DEFAULT 'none'",
    "ALTER TABLE users ADD COLUMN kyc_word TEXT",
    "ALTER TABLE users ADD COLUMN kyc_word_expires_at TEXT",
    "ALTER TABLE users ADD COLUMN kyc_full_name TEXT",
    "ALTER TABLE users ADD COLUMN kyc_dob TEXT",
    "ALTER TABLE users ADD COLUMN kyc_doc_path TEXT",
    "ALTER TABLE users ADD COLUMN kyc_rejection_reason TEXT",
    "ALTER TABLE users ADD COLUMN kyc_submitted_at TEXT",
    "ALTER TABLE support_tickets ADD COLUMN ticket_uuid TEXT",
    "ALTER TABLE support_tickets ADD COLUMN tag TEXT",
    "ALTER TABLE support_tickets ADD COLUMN attachment_path TEXT",
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_id INTEGER",
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_nickname TEXT",
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_public_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_gh_date ON game_history(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_po_type ON payment_orders(type)",
    # v3 — password reset, referral, responsible gambling
    "ALTER TABLE users ADD COLUMN reset_token TEXT",
    "ALTER TABLE users ADD COLUMN reset_token_expires TEXT",
    "ALTER TABLE users ADD COLUMN ref_code TEXT",
    "ALTER TABLE users ADD COLUMN referred_by INTEGER",
    "ALTER TABLE users ADD COLUMN rg_deposit_daily_limit REAL",
    "ALTER TABLE users ADD COLUMN rg_deposit_weekly_limit REAL",
    "ALTER TABLE users ADD COLUMN rg_self_excluded_until TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_ref_code ON users(ref_code)",
    # v4 — VIP club
    "ALTER TABLE users ADD COLUMN vip_tier TEXT",
    "ALTER TABLE users ADD COLUMN vip_expires_at TEXT",
    # v5 — tournaments
    "ALTER TABLE tournaments ADD COLUMN slug TEXT",
    "ALTER TABLE tournaments ADD COLUMN period TEXT",
    "ALTER TABLE tournaments ADD COLUMN prize_pool REAL NOT NULL DEFAULT 0",
    "ALTER TABLE tournaments ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE tournaments ADD COLUMN game_ids TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE tournaments ADD COLUMN glow_color TEXT NOT NULL DEFAULT '#f5c451'",
    "ALTER TABLE tournaments ADD COLUMN starts_at TEXT",
    "ALTER TABLE tournaments ADD COLUMN ends_at TEXT",
    "ALTER TABLE tournaments ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE tournaments ADD COLUMN created_at TEXT NOT NULL DEFAULT (datetime('now'))",
    "ALTER TABLE tournaments ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))",
    "ALTER TABLE tournament_entries ADD COLUMN total_profit REAL NOT NULL DEFAULT 0",
    "ALTER TABLE tournament_entries ADD COLUMN updated_at TEXT NOT NULL DEFAULT (datetime('now'))",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tournaments_slug ON tournaments(slug)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tour_entries_uq ON tournament_entries(tournament_id, user_id)",
    "CREATE TABLE IF NOT EXISTS sport_idempotency (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, idem_key TEXT NOT NULL, response TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(user_id, idem_key))",
    "CREATE INDEX IF NOT EXISTS idx_sport_idem_created ON sport_idempotency(created_at)",
    "CREATE TABLE IF NOT EXISTS sport_audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER, action TEXT NOT NULL, entity_id TEXT, payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')))",
    "CREATE INDEX IF NOT EXISTS idx_sport_audit_created ON sport_audit_logs(created_at)",
]


def init_db():
    """Создаёт таблицы и начальные данные. Вызывать при старте."""
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(_PG_SCHEMA)
            db.execute(_PG_SEED)
    else:
        import sqlite3 as _sl
        os.makedirs(os.path.dirname(os.path.abspath(_SQLITE_PATH)), exist_ok=True)
        conn = _sl.connect(_SQLITE_PATH)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        for stmt in _SQLITE_STATEMENTS:
            conn.execute(stmt)
        for migration in _SQLITE_MIGRATIONS:
            try:
                conn.execute(migration)
            except Exception:
                pass  # Column/index already exists
        for sql, params in _SQLITE_SEED:
            conn.execute(sql, params)
        conn.commit()
        conn.close()
        print(f'  [DB]   SQLITE -> {os.path.abspath(_SQLITE_PATH)}')
