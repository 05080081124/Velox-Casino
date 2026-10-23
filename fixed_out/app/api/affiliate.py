"""app/api/affiliate.py — Referral / Affiliate system.

Flow:
  1. User opens /api/affiliate/my_link  → gets their personal ref_code
  2. New user registers with ?ref=CODE  → referred_by is set + ref_code stored in session
  3. On first deposit → referrer gets REFERRAL_BONUS_UAH credited + email notification
  4. GET /api/affiliate/stats           → referred count, total bonuses earned

Config (config/site.json → referral):
  enabled: true
  bonus_per_referral: 100      # UAH credited to referrer on friend's first deposit
  min_deposit_to_trigger: 50   # friend must deposit at least this amount
"""
from __future__ import annotations
import os
from flask import Blueprint, request, jsonify, session
from app.models.user import User

affiliate_bp = Blueprint('affiliate', __name__, url_prefix='/api/affiliate')

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site.json')


def _load_referral_cfg() -> dict:
    try:
        import json
        with open(_CFG_PATH, encoding='utf-8') as f:
            return json.load(f).get('referral', {})
    except Exception:
        return {}


def _cu() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


@affiliate_bp.route('/my_link')
def my_link():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    code = user.ensure_ref_code()
    base = os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')
    link = f'{base}/?ref={code}'

    cfg  = _load_referral_cfg()
    stats = _get_stats(user.id)

    return jsonify({
        'ref_code':            code,
        'ref_url':             link,
        'bonus_per_referral':  cfg.get('bonus_per_referral', 100),
        'currency':            user.currency,
        'enabled':             cfg.get('enabled', True),
        **stats,
    })


@affiliate_bp.route('/stats')
def stats():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    return jsonify(_get_stats(user.id))


def _get_stats(user_id: int) -> dict:
    from app.db import get_db
    try:
        with get_db() as db:
            db.execute(
                'SELECT COUNT(*) as cnt FROM users WHERE referred_by = %s',
                (user_id,)
            )
            row = db.fetchone()
            referred_count = int(row['cnt']) if row else 0

            db.execute(
                """SELECT COALESCE(SUM(amount), 0) as total FROM transactions
                   WHERE user_id = %s AND type = 'referral_bonus'""",
                (user_id,)
            )
            row2 = db.fetchone()
            total_earned = float(row2['total']) if row2 else 0.0

        return {'referred_count': referred_count, 'total_bonus_earned': total_earned}
    except Exception:
        return {'referred_count': 0, 'total_bonus_earned': 0.0}


def process_referral_bonus(new_user: User, deposit_amount: float) -> bool:
    """
    Called after a referred user's FIRST deposit.
    Credits bonus to the referrer.
    Returns True if bonus was paid out.
    """
    if not new_user.referred_by:
        return False

    cfg = _load_referral_cfg()
    if not cfg.get('enabled', True):
        return False

    min_dep = float(cfg.get('min_deposit_to_trigger', 50))
    if deposit_amount < min_dep:
        return False

    # Check this is the first deposit
    from app.db import get_db
    try:
        with get_db() as db:
            db.execute(
                """SELECT COUNT(*) as cnt FROM transactions
                   WHERE user_id = %s AND type = 'deposit' AND status = 'completed'""",
                (new_user.id,)
            )
            row = db.fetchone()
            if row and int(row['cnt']) > 1:
                return False  # not first deposit

        referrer = User.get_by_id(new_user.referred_by)
        if not referrer:
            return False

        bonus = float(cfg.get('bonus_per_referral', 100))

        from app.services.wallet_service import WalletService
        WalletService.deposit(
            referrer.id, bonus,
            meta={'type': 'referral_bonus', 'referred_user_id': new_user.id,
                  'referred_username': new_user.username}
        )

        # Record as referral_bonus transaction type
        with get_db() as db:
            import json
            db.execute(
                """INSERT INTO transactions (user_id, type, amount, status, meta, created_at)
                   VALUES (%s, 'referral_bonus', %s, 'completed', %s, NOW())""",
                (referrer.id, bonus, json.dumps({
                    'referred_user_id': new_user.id,
                    'referred_username': new_user.username,
                }))
            )

        # Email notification to referrer
        if referrer.email:
            try:
                from app.services.email_service import send_referral_bonus
                send_referral_bonus(
                    referrer.email, referrer.username,
                    bonus, referrer.currency, new_user.username
                )
            except Exception:
                pass

        return True

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f'[affiliate] referral bonus error: {e}')
        return False
