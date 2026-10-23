"""app/models/admin.py — Admin model using get_admin_db."""
from __future__ import annotations
import os, secrets, string
from datetime import datetime, timedelta
from typing import Optional
from app.db import get_admin_db

try:
    import bcrypt as _bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

DEFAULT_NICKNAME = os.environ.get('DEFAULT_ADMIN_NICKNAME', 'VELOX SUPPORT')


def _hash_pw(pw: str) -> str:
    if _BCRYPT_OK:
        return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(rounds=12)).decode()
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()


def _check_pw(pw: str, hashed: str) -> bool:
    if _BCRYPT_OK and hashed.startswith('$2'):
        try:
            return _bcrypt.checkpw(pw.encode(), hashed.encode())
        except Exception:
            return False
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest() == hashed


def _gen_public_id() -> str:
    """Generate short human-readable ID: ADM-XXXX (uppercase alphanum)."""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(6))
    return f'ADM-{suffix}'


class Admin:
    def __init__(self, row: dict):
        self._r = row

    @property
    def id(self) -> int:            return self._r['id']
    @property
    def public_id(self) -> str:     return self._r.get('public_id') or ''
    @property
    def email(self) -> str:         return self._r['email']
    @property
    def nickname(self) -> str:      return self._r.get('nickname') or DEFAULT_NICKNAME
    @property
    def role(self) -> str:          return self._r.get('role', 'helper')
    @property
    def status(self) -> str:        return self._r.get('status', 'active')
    @property
    def created_at(self) -> str:
        v = self._r.get('created_at')
        return v.isoformat() if isinstance(v, datetime) else str(v or '')

    def check_password(self, pw: str) -> bool:
        return _check_pw(pw, self._r['password'])

    def to_dict(self) -> dict:
        return {
            'id':         self.id,
            'public_id':  self.public_id,
            'email':      self.email,
            'nickname':   self.nickname,
            'role':       self.role,
            'status':     self.status,
            'created_at': self.created_at,
        }

    # ── Queries ───────────────────────────────────────────────────────────────

    @staticmethod
    def count() -> int:
        with get_admin_db() as db:
            db.execute('SELECT COUNT(*) as cnt FROM support_admins')
            row = db.fetchone()
        return row['cnt'] if row else 0

    @staticmethod
    def get_by_id(aid: int) -> Optional['Admin']:
        with get_admin_db() as db:
            db.execute('SELECT * FROM support_admins WHERE id = %s', (aid,))
            row = db.fetchone()
        return Admin(row) if row else None

    @staticmethod
    def get_by_email(email: str) -> Optional['Admin']:
        with get_admin_db() as db:
            db.execute('SELECT * FROM support_admins WHERE email = %s', (email.lower(),))
            row = db.fetchone()
        return Admin(row) if row else None

    @staticmethod
    def create(email: str, password: str, nickname: str = '', role: str = 'helper') -> 'Admin':
        nick = nickname.strip() or DEFAULT_NICKNAME
        role = role.strip().lower() if role else 'helper'
        valid_roles = {'helper','admin','soc','dsgn','devops','op','fm','ceo','developer'}
        if role not in valid_roles:
            role = 'helper'
        # Generate unique public_id
        for _ in range(10):
            pid = _gen_public_id()
            with get_admin_db() as db:
                db.execute('SELECT id FROM support_admins WHERE public_id = %s', (pid,))
                if not db.fetchone():
                    break

        with get_admin_db() as db:
            db.execute(
                "INSERT INTO support_admins (public_id, email, password, nickname, role) "
                "VALUES (%s, %s, %s, %s, %s)",
                (pid, email.lower(), _hash_pw(password), nick, role)
            )
            db.execute('SELECT * FROM support_admins WHERE email = %s', (email.lower(),))
            row = db.fetchone()
        if not row:
            raise RuntimeError('Failed to create admin')
        return Admin(row)

    def update_role(self, role: str) -> None:
        valid = {'helper','admin','soc','dsgn','devops','op','fm','ceo','developer'}
        if role not in valid:
            return
        with get_admin_db() as db:
            db.execute('UPDATE support_admins SET role = %s WHERE id = %s', (role, self.id))
        self._r['role'] = role

    @staticmethod
    def get_all() -> list:
        with get_admin_db() as db:
            db.execute('SELECT id, public_id, email, nickname, role, status, created_at FROM support_admins ORDER BY created_at ASC')
            return db.fetchall()

    # ── Profile update ────────────────────────────────────────────────────────

    def update_nickname(self, nickname: str) -> None:
        nick = nickname.strip() or DEFAULT_NICKNAME
        with get_admin_db() as db:
            db.execute('UPDATE support_admins SET nickname = %s WHERE id = %s', (nick, self.id))
        self._r['nickname'] = nick

    def update_email(self, email: str) -> None:
        with get_admin_db() as db:
            db.execute('UPDATE support_admins SET email = %s WHERE id = %s', (email.lower(), self.id))
        self._r['email'] = email.lower()

    def update_password(self, new_pw: str) -> None:
        with get_admin_db() as db:
            db.execute('UPDATE support_admins SET password = %s WHERE id = %s', (_hash_pw(new_pw), self.id))

    # ── Sessions ──────────────────────────────────────────────────────────────

    @staticmethod
    def create_session(admin_id: int, ip: str = '') -> str:
        token   = secrets.token_hex(32)
        expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
        with get_admin_db() as db:
            db.execute(
                "INSERT INTO support_sessions (admin_id, token, ip, expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (admin_id, token, ip, expires)
            )
        return token

    @staticmethod
    def get_by_token(token: str) -> Optional['Admin']:
        with get_admin_db() as db:
            db.execute(
                "SELECT a.* FROM support_admins a "
                "JOIN support_sessions s ON s.admin_id = a.id "
                "WHERE s.token = %s AND s.expires_at > datetime('now')",
                (token,)
            )
            row = db.fetchone()
        return Admin(row) if row else None

    @staticmethod
    def delete_session(token: str) -> None:
        with get_admin_db() as db:
            db.execute("DELETE FROM support_sessions WHERE token = %s", (token,))

    @staticmethod
    def delete_all_sessions(admin_id: int) -> None:
        """Invalidate all sessions for this admin (used after password change)."""
        with get_admin_db() as db:
            db.execute("DELETE FROM support_sessions WHERE admin_id = %s", (admin_id,))
