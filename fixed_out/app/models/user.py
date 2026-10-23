"""app/models/user.py — Модель пользователя (таблица users)."""
from __future__ import annotations
import secrets, json
from datetime import datetime
from typing import Optional
from app.db import get_db, DB_ENGINE

try:
    import bcrypt as _bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False
    import hashlib


def _hash_pw(pw: str) -> str:
    """Хэшируем пароль. bcrypt если установлен, fallback на SHA-256 (для совместимости со старыми хешами)."""
    if _BCRYPT_OK:
        return _bcrypt.hashpw(pw.encode('utf-8'), _bcrypt.gensalt(rounds=12)).decode('utf-8')
    # fallback — только если bcrypt не установлен
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()


def _check_pw(pw: str, hashed: str) -> bool:
    """Проверяем пароль. Автоматически определяет bcrypt или legacy SHA-256."""
    if _BCRYPT_OK and hashed.startswith('$2'):
        # bcrypt хеш
        try:
            return _bcrypt.checkpw(pw.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False
    # legacy SHA-256 (старые аккаунты до миграции)
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest() == hashed


class User:
    def __init__(self, row: dict):
        self._r = row

    @property
    def id(self) -> int:                     return self._r['id']
    @property
    def uuid(self) -> str:                   return str(self._r['uuid_user'])
    @property
    def username(self) -> str:               return self._r['user_name']
    @property
    def email(self) -> Optional[str]:        return self._r.get('email')
    @property
    def phone(self) -> Optional[str]:        return self._r.get('phone')
    @property
    def balance(self) -> float:              return float(self._r['balance'])
    @property
    def currency(self) -> str:               return self._r.get('currency', 'UAH')
    @property
    def verified(self) -> bool:              return bool(self._r.get('verified', False))
    @property
    def two_fa(self) -> bool:                return bool(self._r.get('two_fa', False))
    @property
    def two_fa_pass(self) -> Optional[str]:  return self._r.get('two_fa_pass')
    @property
    def image(self) -> Optional[str]:        return self._r.get('image')
    @property
    def icon_url(self) -> Optional[str]:     return self._r.get('icon_url')
    @property
    def ticket_used(self) -> bool:           return bool(self._r.get('ticket_used', False))
    @property
    def free_spins(self) -> int:             return int(self._r.get('free_spins', 0))
    @property
    def bonus_deposit(self) -> float:        return float(self._r.get('bonus_deposit', 0.0))
    @property
    def used_promocodes(self) -> list:
        raw = self._r.get('used_promocodes', '[]')
        if isinstance(raw, list): return raw
        try:    return json.loads(raw)
        except: return []

    @property
    def created_at(self) -> str:
        v = self._r.get('created_at')
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @property
    def kyc_status(self) -> str:
        """none | pending | verified | rejected"""
        return self._r.get('kyc_status', 'none') or 'none'
    @property
    def kyc_dob(self) -> Optional[str]:        return self._r.get('kyc_dob')
    @property
    def kyc_doc_path(self) -> Optional[str]:   return self._r.get('kyc_doc_path')
    @property
    def kyc_rejection_reason(self) -> Optional[str]: return self._r.get('kyc_rejection_reason')
    @property
    def kyc_submitted_at(self) -> Optional[str]:     return self._r.get('kyc_submitted_at')

    @property
    def favorite_games(self) -> list:
        raw = self._r.get('favorite_games', '[]')
        if isinstance(raw, list): return raw
        try:    return json.loads(raw)
        except: return []

    def check_password(self, pw: str) -> bool:
        return _check_pw(pw, self._r['password'])

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'username': self.username,
            'email': self.email, 'phone': self.phone,
            'balance': round(self.balance, 2), 'currency': self.currency,
            'verified': self.verified, 'image': self.image,
            'icon_url': self.icon_url,
            'two_fa': self.two_fa, 'created_at': self.created_at,
            'favorite_games': self.favorite_games,
            'ticket_used': self.ticket_used,
            'kyc_status': self.kyc_status,
            'kyc_dob': self.kyc_dob,
            'kyc_submitted_at': self.kyc_submitted_at,
            'free_spins': self.free_spins,
            'bonus_deposit': round(self.bonus_deposit, 2),
            'used_promocodes': self.used_promocodes,
            'vip_tier':       self.vip_tier,
            'vip_expires_at': self.vip_expires_at,
            'vip_active':     self.vip_active,
        }

    # ── CRUD ────────────────────────────────────────────────────────────────
    @staticmethod
    def get_by_id(uid: int) -> Optional['User']:
        with get_db() as db:
            db.execute('SELECT * FROM users WHERE id = %s', (uid,))
            row = db.fetchone()
        return User(dict(row)) if row else None

    @staticmethod
    def get_by_email(email: str) -> Optional['User']:
        with get_db() as db:
            db.execute('SELECT * FROM users WHERE email = %s', (email.lower(),))
            row = db.fetchone()
        return User(dict(row)) if row else None

    @staticmethod
    def get_by_phone(phone: str) -> Optional['User']:
        with get_db() as db:
            db.execute('SELECT * FROM users WHERE phone = %s', (phone,))
            row = db.fetchone()
        return User(dict(row)) if row else None

    @staticmethod
    def create(username: str, password: str,
               email: Optional[str] = None, phone: Optional[str] = None) -> 'User':
        with get_db() as db:
            db.execute(
                "INSERT INTO users (user_name, email, phone, password, created_at)"
                " VALUES (%s, %s, %s, %s, NOW())",
                (username, email, phone, _hash_pw(password))
            )
            # Универсально — берём по lastrowid (SQLite) или по уникальному полю (PG)
            if DB_ENGINE == 'postgresql':
                db.execute(
                    'SELECT * FROM users WHERE email = %s OR phone = %s ORDER BY id DESC LIMIT 1',
                    (email, phone)
                )
            else:
                db.execute('SELECT * FROM users WHERE id = last_insert_rowid()')
            row = db.fetchone()
        if not row:
            raise RuntimeError('Не удалось получить созданного пользователя')
        return User(dict(row))

    # ── Обновление ──────────────────────────────────────────────────────────
    def update_profile(self, username: Optional[str] = None,
                       currency: Optional[str] = None,
                       icon_url: Optional[str] = None) -> 'User':
        parts, vals = [], []
        if username and len(username) >= 3:
            parts.append('user_name = %s'); vals.append(username)
        if currency and currency.upper() in ('UAH', 'RUB', 'USD', 'EUR', 'USDT'):
            parts.append('currency = %s'); vals.append(currency.upper())
        if icon_url is not None:
            parts.append('icon_url = %s'); vals.append(icon_url or None)
        if not parts: return self
        vals.append(self.id)
        with get_db() as db:
            db.execute(f"UPDATE users SET {', '.join(parts)} WHERE id = %s", vals)
            db.execute('SELECT * FROM users WHERE id = %s', (self.id,))
            self._r = dict(db.fetchone())
        return self

    def mark_ticket_used(self) -> None:
        from app.db import DB_ENGINE
        p = '%s' if DB_ENGINE == 'postgresql' else '?'
        with get_db() as db:
            db.execute(f'UPDATE users SET ticket_used = 1 WHERE id = {p}', (self.id,))
        self._r['ticket_used'] = True

    def add_free_spins(self, amount: int) -> None:
        with get_db() as db:
            db.execute('UPDATE users SET free_spins = free_spins + %s WHERE id = %s', (amount, self.id))
            db.execute('SELECT free_spins FROM users WHERE id = %s', (self.id,))
            row = db.fetchone()
        self._r['free_spins'] = row['free_spins'] if row else self.free_spins + amount

    def add_bonus_deposit(self, amount: float) -> None:
        with get_db() as db:
            db.execute('UPDATE users SET bonus_deposit = bonus_deposit + %s WHERE id = %s', (amount, self.id))
            db.execute('SELECT bonus_deposit FROM users WHERE id = %s', (self.id,))
            row = db.fetchone()
        self._r['bonus_deposit'] = row['bonus_deposit'] if row else self.bonus_deposit + amount

    def add_used_promo(self, code: str) -> None:
        codes = self.used_promocodes
        if code not in codes:
            codes.append(code)
            with get_db() as db:
                db.execute('UPDATE users SET used_promocodes = %s WHERE id = %s',
                           (json.dumps(codes), self.id))
            self._r['used_promocodes'] = codes

    def change_password(self, new_pw: str) -> None:
        with get_db() as db:
            db.execute('UPDATE users SET password = %s WHERE id = %s',
                       (_hash_pw(new_pw), self.id))

    def enable_2fa(self) -> str:
        """Enable TOTP-based 2FA and return new shared secret."""
        try:
            import pyotp  # type: ignore
            secret = pyotp.random_base32()
        except Exception:
            # Fallback for environments where pyotp is not installed yet.
            secret = secrets.token_urlsafe(32).replace('-', '').replace('_', '')[:32].upper()
        with get_db() as db:
            db.execute('UPDATE users SET two_fa = 1, two_fa_pass = %s WHERE id = %s',
                       (secret, self.id))
        self._r['two_fa'] = True
        self._r['two_fa_pass'] = secret
        return secret

    def disable_2fa(self) -> None:
        with get_db() as db:
            db.execute('UPDATE users SET two_fa = 0, two_fa_pass = NULL WHERE id = %s',
                       (self.id,))
        self._r['two_fa'] = False; self._r['two_fa_pass'] = None

    def set_kyc_status(self, status: str, reason: str = '') -> None:
        with get_db() as db:
            db.execute(
                'UPDATE users SET kyc_status = %s, kyc_rejection_reason = %s WHERE id = %s',
                (status, reason or None, self.id)
            )
        self._r['kyc_status'] = status
        self._r['kyc_rejection_reason'] = reason or None

    def submit_kyc(self, dob: str, doc_path: str) -> None:
        from datetime import datetime as _dt
        now = _dt.now().isoformat()
        with get_db() as db:
            db.execute(
                'UPDATE users SET kyc_status = %s, kyc_dob = %s, kyc_doc_path = %s, kyc_submitted_at = %s WHERE id = %s',
                ('pending', dob, doc_path, now, self.id)
            )
        self._r.update({'kyc_status': 'pending', 'kyc_dob': dob,
                        'kyc_doc_path': doc_path, 'kyc_submitted_at': now})

    def set_favorites(self, games: list) -> None:
        with get_db() as db:
            db.execute('UPDATE users SET favorite_games = %s WHERE id = %s',
                       (json.dumps(games), self.id))
        self._r['favorite_games'] = games

    # ── Password Reset ───────────────────────────────────────────────────────
    def set_reset_token(self) -> str:
        """Generate a reset token valid for 30 minutes. Returns the raw token."""
        from datetime import datetime as _dt, timedelta
        token = secrets.token_urlsafe(32)
        expires = (_dt.utcnow() + timedelta(minutes=30)).isoformat()
        with get_db() as db:
            db.execute(
                'UPDATE users SET reset_token = %s, reset_token_expires = %s WHERE id = %s',
                (token, expires, self.id)
            )
        self._r['reset_token'] = token
        self._r['reset_token_expires'] = expires
        return token

    def clear_reset_token(self) -> None:
        with get_db() as db:
            db.execute(
                'UPDATE users SET reset_token = NULL, reset_token_expires = NULL WHERE id = %s',
                (self.id,)
            )
        self._r['reset_token'] = None
        self._r['reset_token_expires'] = None

    @staticmethod
    def get_by_reset_token(token: str) -> Optional['User']:
        from datetime import datetime as _dt
        with get_db() as db:
            db.execute('SELECT * FROM users WHERE reset_token = %s', (token,))
            row = db.fetchone()
        if not row:
            return None
        user = User(dict(row))
        # Check expiry
        exp = user._r.get('reset_token_expires')
        if not exp:
            return None
        try:
            exp_dt = _dt.fromisoformat(str(exp).replace('Z',''))
            if _dt.utcnow() > exp_dt:
                return None
        except Exception:
            return None
        return user

    # ── Referral / Affiliate ─────────────────────────────────────────────────
    @property
    def ref_code(self) -> str:
        """Unique referral code for this user."""
        return self._r.get('ref_code') or ''

    @property
    def referred_by(self) -> Optional[int]:
        """ID of user who referred this account (or None)."""
        v = self._r.get('referred_by')
        return int(v) if v else None

    @staticmethod
    def get_by_ref_code(code: str) -> Optional['User']:
        with get_db() as db:
            db.execute('SELECT * FROM users WHERE ref_code = %s', (code.upper(),))
            row = db.fetchone()
        return User(dict(row)) if row else None

    def ensure_ref_code(self) -> str:
        """Generate ref_code if not set yet. Returns it."""
        if self.ref_code:
            return self.ref_code
        code = (self.username[:4].upper() + secrets.token_hex(2).upper())[:8]
        with get_db() as db:
            db.execute('UPDATE users SET ref_code = %s WHERE id = %s', (code, self.id))
        self._r['ref_code'] = code
        return code

    # ── Responsible Gambling ─────────────────────────────────────────────────
    @property
    def rg_deposit_daily_limit(self) -> Optional[float]:
        v = self._r.get('rg_deposit_daily_limit')
        return float(v) if v is not None else None

    @property
    def rg_deposit_weekly_limit(self) -> Optional[float]:
        v = self._r.get('rg_deposit_weekly_limit')
        return float(v) if v is not None else None

    @property
    def rg_self_excluded_until(self) -> Optional[str]:
        return self._r.get('rg_self_excluded_until')

    @property
    def rg_is_excluded(self) -> bool:
        """Returns True if player is currently self-excluded."""
        exp = self.rg_self_excluded_until
        if not exp:
            return False
        from datetime import datetime as _dt
        try:
            exp_dt = _dt.fromisoformat(str(exp).replace('Z',''))
            return _dt.utcnow() < exp_dt
        except Exception:
            return False

    def set_rg_deposit_limit(self, daily: Optional[float], weekly: Optional[float]) -> None:
        parts, vals = [], []
        if daily is not None:
            parts.append('rg_deposit_daily_limit = %s'); vals.append(daily if daily > 0 else None)
        if weekly is not None:
            parts.append('rg_deposit_weekly_limit = %s'); vals.append(weekly if weekly > 0 else None)
        if not parts: return
        vals.append(self.id)
        with get_db() as db:
            db.execute(f'UPDATE users SET {", ".join(parts)} WHERE id = %s', vals)
        if daily is not None:  self._r['rg_deposit_daily_limit'] = daily or None
        if weekly is not None: self._r['rg_deposit_weekly_limit'] = weekly or None

    def self_exclude(self, days: int) -> str:
        """Exclude player for N days. Returns exclusion end date."""
        from datetime import datetime as _dt, timedelta
        until = (_dt.utcnow() + timedelta(days=days)).isoformat()
        with get_db() as db:
            db.execute(
                'UPDATE users SET rg_self_excluded_until = %s WHERE id = %s',
                (until, self.id)
            )
        self._r['rg_self_excluded_until'] = until
        return until

    # ── VIP ──────────────────────────────────────────────────────────────────

    @property
    def vip_tier(self) -> str:
        return self._r.get('vip_tier') or 'none'

    @property
    def vip_expires_at(self) -> Optional[str]:
        return self._r.get('vip_expires_at')

    @property
    def vip_active(self) -> bool:
        """True если VIP подписка активна прямо сейчас."""
        if self.vip_tier == 'none':
            return False
        exp = self.vip_expires_at
        if not exp:
            return False
        from datetime import datetime as _dt, timezone as _tz
        try:
            exp_dt = _dt.fromisoformat(str(exp))
            # Make both timezone-aware (UTC) to handle PostgreSQL +00:00 strings
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=_tz.utc)
            return exp_dt > _dt.now(_tz.utc)
        except Exception:
            return False

    def set_vip(self, tier: str, expires_at: Optional[str]) -> None:
        with get_db() as db:
            db.execute(
                'UPDATE users SET vip_tier = %s, vip_expires_at = %s WHERE id = %s',
                (tier if tier != 'none' else None, expires_at, self.id)
            )
        self._r['vip_tier']       = tier
        self._r['vip_expires_at'] = expires_at
