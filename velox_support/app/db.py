"""
app/db.py — Два независимых коннектора к БД:

  get_admin_db()  → БД администраторов (localA_db / A_DB_*)
                    таблицы: support_admins, support_sessions

  get_casino_db() → БД казино (localC_db / C_DB_*)
                    таблицы: users, support_tickets, transactions,
                             payment_orders, game_history, support_ticket_messages

.env (DB_ENGINE=sqlite):
  localA_db = путь к файлу БД администраторов
  localC_db = путь к файлу БД казино

.env (DB_ENGINE=postgresql):
  C_DB_HOST, C_DB_PORT, C_DB_NAME, C_DB_USER, C_DB_PASSWORD  → казино
  A_DB_HOST, A_DB_PORT, A_DB_NAME, A_DB_USER, A_DB_PASSWORD  → администраторы
"""

import os, sqlite3, threading, re
from contextlib import contextmanager

# ── Engine ────────────────────────────────────────────────────────────────────
DB_ENGINE = 'sqlite'
_env = os.environ.get('DB_ENGINE', '').lower().strip()
if _env in ('postgresql', 'postgres', 'pg', 'supabase'):
    DB_ENGINE = 'postgresql'
elif _env == 'sqlite':
    DB_ENGINE = 'sqlite'

# ── SQLite paths ───────────────────────────────────────────────────────────────
# _BASE = папка, где лежит db.py (velox_support/app/)
_BASE = os.path.dirname(os.path.abspath(__file__))
# _ROOT = корень проекта velox_support/
_ROOT = os.path.dirname(_BASE)

def _resolve_path(env_key: str, default_relative: str) -> str:
    """
    1. Берём значение из os.environ (уже загружено dotenv в main.py).
    2. Если путь относительный — разворачиваем относительно _ROOT.
    3. Нормализуем через os.path.abspath.
    """
    raw = os.environ.get(env_key, '').strip()
    if not raw:
        raw = default_relative
    if not os.path.isabs(raw):
        raw = os.path.join(_ROOT, raw)
    return os.path.normpath(raw)

# Пути подставляются ПОСЛЕ загрузки dotenv (main.py вызывает load_dotenv перед импортом app)
# Поэтому используем функцию-ленивую инициализацию
_ADMIN_SQLITE_RAW  = None
_CASINO_SQLITE_RAW = None

def _admin_path() -> str:
    global _ADMIN_SQLITE_RAW
    if _ADMIN_SQLITE_RAW is None:
        _ADMIN_SQLITE_RAW = _resolve_path('localA_db', '../support_db/support.db')
    return _ADMIN_SQLITE_RAW

def _casino_path() -> str:
    global _CASINO_SQLITE_RAW
    if _CASINO_SQLITE_RAW is None:
        _CASINO_SQLITE_RAW = _resolve_path('localC_db', '../database/casino.db')
    return _CASINO_SQLITE_RAW

_admin_lock  = threading.Lock()
_casino_lock = threading.Lock()


# ══════════════════════════════════════════════════════════════════════════════
#  SQLite cursor wrapper
# ══════════════════════════════════════════════════════════════════════════════

class _SQLiteCursor:
    def __init__(self, cursor: sqlite3.Cursor, conn: sqlite3.Connection):
        self._c   = cursor
        self._conn = conn

    def execute(self, sql: str, params=None):
        sql = sql.replace('%s', '?')
        sql = sql.replace('::jsonb', '').replace('::JSONB', '')
        sql = sql.replace("NOW()", "datetime('now')")

        # RETURNING * after INSERT
        ret_insert_tbl = None
        if re.search(r'RETURNING\s+\*', sql, re.IGNORECASE):
            m = re.search(r'INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)', sql, re.IGNORECASE)
            if m:
                ret_insert_tbl = m.group(1)
            sql = re.sub(r'RETURNING\s+\*', '', sql, flags=re.IGNORECASE)

        # RETURNING col after UPDATE
        ret_upd_col = ret_upd_tbl = None
        m2 = re.search(r'RETURNING\s+(\w+)', sql, re.IGNORECASE)
        if m2:
            ret_upd_col = m2.group(1)
            m3 = re.search(r'UPDATE\s+(\w+)', sql, re.IGNORECASE)
            if m3:
                ret_upd_tbl = m3.group(1)
            sql = re.sub(r'RETURNING\s+\w+', '', sql, flags=re.IGNORECASE)

        self._c.execute(sql, params) if params else self._c.execute(sql)

        if ret_insert_tbl and self._c.lastrowid:
            self._c.execute(f'SELECT * FROM {ret_insert_tbl} WHERE id = ?', (self._c.lastrowid,))
        elif ret_upd_tbl and ret_upd_col:
            self._c.execute(f'SELECT {ret_upd_col} FROM {ret_upd_tbl} WHERE rowid = ?', (self._c.lastrowid or 1,))

        return self

    def fetchone(self):
        row = self._c.fetchone()
        if row is None: return None
        if hasattr(row, 'keys'): return dict(row)
        if self._c.description:
            return dict(zip([d[0] for d in self._c.description], row))
        return None

    def fetchall(self):
        rows = self._c.fetchall()
        if not rows: return []
        if hasattr(rows[0], 'keys'): return [dict(r) for r in rows]
        if self._c.description:
            cols = [d[0] for d in self._c.description]
            return [dict(zip(cols, r)) for r in rows]
        return []

    def __iter__(self):
        for row in self._c:
            if hasattr(row, 'keys'): yield dict(row)
            elif self._c.description:
                yield dict(zip([d[0] for d in self._c.description], row))


def _make_sqlite_ctx(path_fn, lock: threading.Lock):
    """Factory: returns a context-manager function for a given SQLite path."""
    @contextmanager
    def _ctx():
        path = path_fn()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        cur = _SQLiteCursor(conn.cursor(), conn)
        try:
            with lock:
                yield cur
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return _ctx


_sqlite_admin_db  = _make_sqlite_ctx(_admin_path,  _admin_lock)
_sqlite_casino_db = _make_sqlite_ctx(_casino_path, _casino_lock)


# ══════════════════════════════════════════════════════════════════════════════
#  PostgreSQL pools
# ══════════════════════════════════════════════════════════════════════════════

_pg_casino_pool = None
_pg_admin_pool  = None


def _make_pg_pool(prefix: str):
    from psycopg2 import pool as pgp
    return pgp.ThreadedConnectionPool(
        minconn  = int(os.environ.get(f'{prefix}DB_MIN_CONN', '1')),
        maxconn  = int(os.environ.get(f'{prefix}DB_MAX_CONN', '10')),
        host     = os.environ.get(f'{prefix}DB_HOST', 'localhost'),
        port     = int(os.environ.get(f'{prefix}DB_PORT', '5432')),
        dbname   = os.environ.get(f'{prefix}DB_NAME', 'velox'),
        user     = os.environ.get(f'{prefix}DB_USER', 'velox'),
        password = os.environ.get(f'{prefix}DB_PASSWORD', ''),
    )


def _get_casino_pool():
    global _pg_casino_pool
    if _pg_casino_pool is None or _pg_casino_pool.closed:
        _pg_casino_pool = _make_pg_pool('C_')
    return _pg_casino_pool


def _get_admin_pool():
    global _pg_admin_pool
    if _pg_admin_pool is None or _pg_admin_pool.closed:
        _pg_admin_pool = _make_pg_pool('A_')
    return _pg_admin_pool


def _make_pg_ctx(pool_getter):
    @contextmanager
    def _ctx():
        from psycopg2.extras import RealDictCursor
        conn = pool_getter().getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pool_getter().putconn(conn)
    return _ctx


_pg_admin_db  = _make_pg_ctx(_get_admin_pool)
_pg_casino_db = _make_pg_ctx(_get_casino_pool)


# ══════════════════════════════════════════════════════════════════════════════
#  Public context managers
# ══════════════════════════════════════════════════════════════════════════════

if DB_ENGINE == 'postgresql':
    get_admin_db  = _pg_admin_db
    get_casino_db = _pg_casino_db

    def close_pools():
        global _pg_casino_pool, _pg_admin_pool
        for p in (_pg_casino_pool, _pg_admin_pool):
            if p and not p.closed:
                p.closeall()
        _pg_casino_pool = _pg_admin_pool = None
else:
    get_admin_db  = _sqlite_admin_db
    get_casino_db = _sqlite_casino_db

    def close_pools():
        pass

# Legacy alias
get_db = get_admin_db


# ══════════════════════════════════════════════════════════════════════════════
#  DDL — Admin DB tables
# ══════════════════════════════════════════════════════════════════════════════

_ADMIN_SQLITE_DDL = [
    """CREATE TABLE IF NOT EXISTS support_admins (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        public_id   TEXT    NOT NULL DEFAULT '',
        email       TEXT    NOT NULL UNIQUE,
        password    TEXT    NOT NULL,
        nickname    TEXT    NOT NULL DEFAULT 'VELOX SUPPORT',
        role        TEXT    NOT NULL DEFAULT 'helper',
        status      TEXT    NOT NULL DEFAULT 'active',
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS support_sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id    INTEGER NOT NULL REFERENCES support_admins(id) ON DELETE CASCADE,
        token       TEXT    NOT NULL UNIQUE,
        ip          TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        expires_at  TEXT    NOT NULL
    )""",
]

_ADMIN_PG_DDL = """
CREATE TABLE IF NOT EXISTS support_admins (
    id          SERIAL       PRIMARY KEY,
    public_id   VARCHAR(12)  NOT NULL DEFAULT '',
    email       VARCHAR(256) NOT NULL UNIQUE,
    password    VARCHAR(128) NOT NULL,
    nickname    VARCHAR(64)  NOT NULL DEFAULT 'VELOX SUPPORT',
    role        VARCHAR(16)  NOT NULL DEFAULT 'helper',
    status      VARCHAR(16)  NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS support_sessions (
    id          BIGSERIAL    PRIMARY KEY,
    admin_id    INTEGER      NOT NULL REFERENCES support_admins(id) ON DELETE CASCADE,
    token       VARCHAR(128) NOT NULL UNIQUE,
    ip          VARCHAR(64),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ  NOT NULL
);
"""

_ADMIN_MIGRATIONS = [
    "ALTER TABLE support_admins ADD COLUMN public_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE support_admins ADD COLUMN role TEXT NOT NULL DEFAULT 'helper'",
]

# ── Casino DB — messages table only (other tables already exist in casino) ────

_CASINO_SQLITE_MESSAGES = """CREATE TABLE IF NOT EXISTS support_ticket_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    admin_id    INTEGER,
    sender      TEXT    NOT NULL DEFAULT 'user',
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
)"""

_CASINO_PG_MESSAGES = """
CREATE TABLE IF NOT EXISTS support_ticket_messages (
    id          BIGSERIAL   PRIMARY KEY,
    ticket_id   INTEGER     NOT NULL,
    admin_id    INTEGER,
    sender      VARCHAR(16) NOT NULL DEFAULT 'user',
    message     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# ── Chat tables (in admin DB) ─────────────────────────────────────────────────

_CHAT_SQLITE_DDL = [
    """CREATE TABLE IF NOT EXISTS support_chat_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id    INTEGER NOT NULL REFERENCES support_admins(id) ON DELETE CASCADE,
        type        TEXT    NOT NULL DEFAULT 'text',
        content     TEXT    NOT NULL,
        file_url    TEXT,
        file_name   TEXT,
        created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
    )""",
]

_CHAT_PG_DDL = """
CREATE TABLE IF NOT EXISTS support_chat_messages (
    id          BIGSERIAL   PRIMARY KEY,
    admin_id    INTEGER     NOT NULL REFERENCES support_admins(id) ON DELETE CASCADE,
    type        VARCHAR(16) NOT NULL DEFAULT 'text',
    content     TEXT        NOT NULL,
    file_url    TEXT,
    file_name   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

# Migrations applied to casino DB on startup
_CASINO_SQLITE_MIGRATIONS = [
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_id INTEGER",
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_nickname TEXT",
    "ALTER TABLE support_tickets ADD COLUMN assigned_to_public_id TEXT",
]

_CASINO_PG_MIGRATIONS = [
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS assigned_to_id INTEGER",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS assigned_to_nickname VARCHAR(64)",
    "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS assigned_to_public_id VARCHAR(16)",
]


def init_support_db():
    """
    Creates support tables in both databases.
    Called once at startup from main.py (after load_dotenv).
    """
    # ── 1. Admin DB ───────────────────────────────────────────────────────────
    if DB_ENGINE == 'postgresql':
        with get_admin_db() as db:
            db.execute(_ADMIN_PG_DDL)
        print(f'  [DB] Admin  DB: PostgreSQL -> {os.environ.get("A_DB_HOST","?")}/'
              f'{os.environ.get("A_DB_NAME","?")}')
    else:
        import sqlite3 as _sl
        path = _admin_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = _sl.connect(path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        for stmt in _ADMIN_SQLITE_DDL:
            try:
                conn.execute(stmt)
            except Exception:
                pass
        for mig in _ADMIN_MIGRATIONS:
            try:
                conn.execute(mig)
            except Exception:
                pass  # column already exists
        conn.commit()
        conn.close()
        print(f'  [DB] Admin  DB: SQLite -> {path}')

    # ── 2. Casino DB — verify it exists and add messages table ───────────────
    if DB_ENGINE == 'postgresql':
        with get_casino_db() as db:
            db.execute(_CASINO_PG_MESSAGES)
            for mig in _CASINO_PG_MIGRATIONS:
                try:
                    db.execute(mig)
                except Exception:
                    pass
        print(f'  [DB] Casino DB: PostgreSQL -> {os.environ.get("C_DB_HOST","?")}/'
              f'{os.environ.get("C_DB_NAME","?")}')
    else:
        import sqlite3 as _sl
        path = _casino_path()
        if not os.path.exists(path):
            print(f'  [DB] [WARN] Casino DB NOT FOUND: {path}')
            print(f'  [DB]   Set localC_db= in .env to correct path of casino.db')
            return
        conn = _sl.connect(path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=OFF')  # casino may have FK issues
        try:
            conn.execute(_CASINO_SQLITE_MESSAGES)
            for mig in _CASINO_SQLITE_MIGRATIONS:
                try:
                    conn.execute(mig)
                except Exception:
                    pass  # column already exists
            conn.commit()
        except Exception as e:
            print(f'  [DB]   messages table note: {e}')
        conn.close()
        print(f'  [DB] Casino DB: SQLite -> {path}')

    # ── 3. Chat tables (in admin DB) ──────────────────────────────────────────
    if DB_ENGINE == 'postgresql':
        with get_admin_db() as db:
            db.execute(_CHAT_PG_DDL)
    else:
        import sqlite3 as _sl3
        apath = _admin_path()
        os.makedirs(os.path.dirname(apath), exist_ok=True)
        conn3 = _sl3.connect(apath)
        conn3.execute('PRAGMA journal_mode=WAL')
        for stmt in _CHAT_SQLITE_DDL:
            try:
                conn3.execute(stmt)
            except Exception:
                pass
        conn3.commit()
        conn3.close()
        print(f'  [DB] Chat    DB: SQLite -> {apath} (support_chat_messages)')
