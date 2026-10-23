"""app/services/wallet_service.py — Операции с балансом. Совместим с SQLite и PostgreSQL."""
from __future__ import annotations
import json, os
from typing import Optional
from app.db import get_db


class InsufficientFundsError(Exception):
    pass

class WalletError(Exception):
    pass


# ── VIP benefit helpers ───────────────────────────────────────────────────────

_VIP_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'vip.json')

def _vip_benefits(user_id: int) -> dict:
    """Return active VIP benefits dict for user, or {} if no active VIP."""
    try:
        from app.models.user import User
        from datetime import datetime
        user = User.get_by_id(user_id)
        if not user or not user.vip_active:
            return {}
        tier = user.vip_tier
        if not tier or tier == 'none':
            return {}
        with open(_VIP_CFG_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        return cfg.get('tiers', {}).get(tier, {}).get('benefits', {})
    except Exception:
        return {}



class WalletService:

    @staticmethod
    def _get_balance(db, user_id: int) -> float:
        """Читает баланс внутри уже открытой транзакции."""
        db.execute('SELECT balance FROM users WHERE id = %s', (user_id,))
        row = db.fetchone()
        if not row:
            raise WalletError('Пользователь не найден')
        return float(row['balance'])

    @staticmethod
    def get_balance(user_id: int) -> float:
        with get_db() as db:
            return WalletService._get_balance(db, user_id)

    @staticmethod
    def deposit(user_id: int, amount: float, meta: Optional[dict] = None) -> float:
        if amount <= 0:
            raise WalletError('Сумма пополнения должна быть положительной')

        # ── VIP deposit bonus ─────────────────────────────────────────────────
        vip_b = _vip_benefits(user_id)
        deposit_bonus_pct = vip_b.get('deposit_bonus_percent', 0)
        bonus_amount = round(amount * deposit_bonus_pct / 100, 2) if deposit_bonus_pct > 0 else 0
        total_credit = round(amount + bonus_amount, 2)
        # ─────────────────────────────────────────────────────────────────────

        with get_db() as db:
            db.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (total_credit, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'deposit', %s, 'completed', %s, NOW())",
                (user_id, amount, json.dumps(meta or {}))
            )
            # Log VIP bonus as separate transaction so it's visible in history
            if bonus_amount > 0:
                db.execute(
                    "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                    " VALUES (%s, 'deposit', %s, 'completed', %s, NOW())",
                    (user_id, bonus_amount, json.dumps({
                        'type': 'vip_deposit_bonus',
                        'bonus_pct': deposit_bonus_pct,
                        'base_deposit': amount,
                    }))
                )
        return round(new_balance, 2)

    @staticmethod
    def withdraw(user_id: int, amount: float, meta: Optional[dict] = None) -> float:
        if amount <= 0:
            raise WalletError('Сумма вывода должна быть положительной')

        # ── Wager check ──────────────────────────────────────────────────────
        import os as _os
        _promo_cfg_path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'config', 'promo_settings.json')
        try:
            with open(_promo_cfg_path, encoding='utf-8') as _f:
                _promo_cfg = json.load(_f)
            _multiplier = float(_promo_cfg.get('rules', {}).get('wagering_multiplier', 20))
        except Exception:
            _multiplier = 20.0

        with get_db() as db:
            db.execute('SELECT bonus_deposit FROM users WHERE id = %s', (user_id,))
            _row = db.fetchone()
            _bonus = float((_row or {}).get('bonus_deposit', 0) or 0)

        if _bonus > 0:
            # Count total bets since last bonus
            with get_db() as db:
                db.execute(
                    "SELECT COALESCE(SUM(amount),0) as total FROM transactions"
                    " WHERE user_id=%s AND type='bet' AND status='completed'",
                    (user_id,)
                )
                _bets_row = db.fetchone()
            _total_bets = float((_bets_row or {}).get('total', 0) or 0)
            _required   = round(_bonus * _multiplier, 2)
            if _total_bets < _required:
                _remaining = round(_required - _total_bets, 2)
                raise WalletError(
                    f'Необходимо отыграть бонус: поставьте ещё {_remaining} {chr(164)}'
                    f' (бонус {_bonus} × {int(_multiplier)})'
                )
        # ─────────────────────────────────────────────────────────────────────
        # Бонус отыгран — обнуляем
        elif _bonus > 0 and _total_bets >= _required:
            with get_db() as db:
                db.execute('UPDATE users SET bonus_deposit = 0 WHERE id = %s', (user_id,))

        with get_db() as db:
            bal = WalletService._get_balance(db, user_id)
            if bal < amount:
                raise InsufficientFundsError('Недостаточно средств')
            db.execute('UPDATE users SET balance = balance - %s WHERE id = %s', (amount, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'withdraw', %s, 'completed', %s, NOW())",
                (user_id, amount, json.dumps(meta or {}))
            )
        return round(new_balance, 2)

    @staticmethod
    def place_bet(user_id: int, amount: float, game_id: str) -> float:
        if amount <= 0:
            raise WalletError('Ставка должна быть положительной')
        with get_db() as db:
            bal = WalletService._get_balance(db, user_id)
            if bal < amount:
                raise InsufficientFundsError('Недостаточно средств')
            db.execute('UPDATE users SET balance = balance - %s WHERE id = %s', (amount, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'bet', %s, 'completed', %s, NOW())",
                (user_id, amount, json.dumps({'game_id': game_id}))
            )
        return round(new_balance, 2)

    @staticmethod
    def cancel_bet(user_id: int, amount: float, game_id: str) -> float:
        """Возвращает сумму ставки на баланс (отмена до начала игры)."""
        if amount <= 0:
            return WalletService.get_balance(user_id)
        with get_db() as db:
            db.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (amount, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'refund', %s, 'completed', %s, NOW())",
                (user_id, amount, json.dumps({'game_id': game_id, 'reason': 'cancel'}))
            )
        return round(new_balance, 2)

    @staticmethod
    def pay_win(user_id: int, amount: float, game_id: str,
                meta: Optional[dict] = None) -> float:
        if amount <= 0:
            return WalletService.get_balance(user_id)

        # ── VIP win bonus ─────────────────────────────────────────────────────
        vip_b = _vip_benefits(user_id)
        win_bonus_pct = vip_b.get('win_bonus_percent', 0)
        bonus_amount  = round(amount * win_bonus_pct / 100, 2) if win_bonus_pct > 0 else 0
        total_credit  = round(amount + bonus_amount, 2)
        # ─────────────────────────────────────────────────────────────────────

        with get_db() as db:
            db.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (total_credit, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            payload = {'game_id': game_id, **(meta or {})}
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'win', %s, 'completed', %s, NOW())",
                (user_id, amount, json.dumps(payload))
            )
            if bonus_amount > 0:
                db.execute(
                    "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                    " VALUES (%s, 'win', %s, 'completed', %s, NOW())",
                    (user_id, bonus_amount, json.dumps({
                        'type':      'vip_win_bonus',
                        'game_id':   game_id,
                        'bonus_pct': win_bonus_pct,
                        'base_win':  amount,
                    }))
                )
        return round(new_balance, 2)

    @staticmethod
    def apply_promo(user_id: int, code: str) -> tuple[float, float]:
        from datetime import datetime, timezone
        with get_db() as db:
            db.execute(
                'SELECT * FROM promo_codes WHERE code = %s AND active = 1',
                (code.upper(),)
            )
            promo = db.fetchone()
            if not promo:
                raise WalletError('Промокод не найден или неактивен')
            promo = dict(promo)

            if promo.get('expires_at'):
                try:
                    exp = datetime.fromisoformat(str(promo['expires_at']))
                    if exp.tzinfo:
                        now = datetime.now(timezone.utc)
                    else:
                        now = datetime.now()
                    if exp < now:
                        raise WalletError('Срок действия промокода истёк')
                except (ValueError, TypeError):
                    pass

            if promo['uses'] >= promo['max_uses']:
                raise WalletError('Промокод исчерпан')

            if promo['one_per_user']:
                db.execute(
                    'SELECT 1 FROM promo_uses WHERE promo_id = %s AND user_id = %s',
                    (promo['id'], user_id)
                )
                if db.fetchone():
                    raise WalletError('Вы уже использовали этот промокод')

            bonus = float(promo['bonus_value'])
            db.execute('UPDATE users SET balance = balance + %s WHERE id = %s', (bonus, user_id))
            new_balance = WalletService._get_balance(db, user_id)
            db.execute('UPDATE promo_codes SET uses = uses + 1 WHERE id = %s', (promo['id'],))
            db.execute(
                'INSERT INTO promo_uses (promo_id, user_id) VALUES (%s, %s)',
                (promo['id'], user_id)
            )
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'promo', %s, 'completed', %s, NOW())",
                (user_id, bonus, json.dumps({'code': code.upper(), 'promo_id': promo['id']}))
            )
        return round(bonus, 2), round(new_balance, 2)


wallet = WalletService()
