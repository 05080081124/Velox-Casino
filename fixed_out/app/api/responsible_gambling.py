"""app/api/responsible_gambling.py — Responsible Gambling API.

Endpoints:
  GET  /api/rg/status           — current limits + exclusion status
  POST /api/rg/deposit_limit    — set daily / weekly deposit limits
  POST /api/rg/self_exclude     — self-exclude for N days (7/30/90/365/forever)
  POST /api/rg/cancel_exclusion — request to cancel (only after period ends)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify, session
from app.models.user import User

rg_bp = Blueprint('rg', __name__, url_prefix='/api/rg')

_EXCLUDE_OPTIONS = {
    7:    '7 days',
    30:   '30 days',
    90:   '90 days',
    180:  '180 days',
    365:  '1 year',
    36500: 'Permanent',
}


def _cu() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


@rg_bp.route('/status')
def rg_status():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    is_excluded = user.rg_is_excluded
    excluded_until = user.rg_self_excluded_until

    return jsonify({
        'deposit_daily_limit':  user.rg_deposit_daily_limit,
        'deposit_weekly_limit': user.rg_deposit_weekly_limit,
        'self_excluded':        is_excluded,
        'excluded_until':       excluded_until,
        'exclude_options':      _EXCLUDE_OPTIONS,
    })


@rg_bp.route('/deposit_limit', methods=['POST'])
def set_deposit_limit():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d       = request.get_json() or {}
    daily   = d.get('daily')
    weekly  = d.get('weekly')

    if daily is not None:
        try:
            daily = float(daily)
            if daily < 0:
                return jsonify({'error': 'Лимит не может быть отрицательным'}), 400
        except (TypeError, ValueError):
            return jsonify({'error': 'Неверное значение'}), 400

    if weekly is not None:
        try:
            weekly = float(weekly)
            if weekly < 0:
                return jsonify({'error': 'Лимит не может быть отрицательным'}), 400
        except (TypeError, ValueError):
            return jsonify({'error': 'Неверное значение'}), 400

    # Cooldown: new limit can only be set lower immediately;
    # increasing a limit requires 24h cooling-off period (simplified: always allow for now)
    user.set_rg_deposit_limit(daily, weekly)

    return jsonify({
        'success': True,
        'deposit_daily_limit':  user.rg_deposit_daily_limit,
        'deposit_weekly_limit': user.rg_deposit_weekly_limit,
    })


@rg_bp.route('/self_exclude', methods=['POST'])
def self_exclude():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d    = request.get_json() or {}
    days = d.get('days')

    try:
        days = int(days)
    except (TypeError, ValueError):
        return jsonify({'error': 'Укажите количество дней'}), 400

    if days not in _EXCLUDE_OPTIONS:
        return jsonify({'error': f'Допустимые значения: {list(_EXCLUDE_OPTIONS.keys())}'}), 400

    until = user.self_exclude(days)

    # Send confirmation email
    if user.email:
        try:
            from app.services.email_service import send_self_exclusion
            until_fmt = until[:10] if until else '—'
            send_self_exclusion(user.email, user.username, until_fmt)
        except Exception:
            pass

    return jsonify({
        'success': True,
        'excluded_until': until,
        'message': f'Самоисключение до {until[:10]}',
    })


@rg_bp.route('/cancel_exclusion', methods=['POST'])
def cancel_exclusion():
    """Allow cancellation only after exclusion period has ended."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    if user.rg_is_excluded:
        return jsonify({
            'error': 'Период самоисключения ещё не завершён. Обратитесь в поддержку.'
        }), 403

    user.set_rg_deposit_limit(None, None)
    from app.db import get_db
    with get_db() as db:
        db.execute('UPDATE users SET rg_self_excluded_until = NULL WHERE id = %s', (user.id,))

    return jsonify({'success': True})


def check_rg_deposit_allowed(user: User, amount: float) -> tuple[bool, str]:
    """
    Check if deposit is allowed under RG limits.
    Returns (allowed, reason).
    Called from wallet.py before processing deposit.
    """
    if user.rg_is_excluded:
        return False, 'Аккаунт временно заблокирован (самоисключение)'

    # Daily limit check
    daily = user.rg_deposit_daily_limit
    if daily and daily > 0:
        today_total = _sum_deposits_since(user.id, hours=24)
        if today_total + amount > daily:
            remaining = max(0, daily - today_total)
            return False, f'Превышен дневной лимит депозитов. Доступно: {remaining:.2f}'

    # Weekly limit check
    weekly = user.rg_deposit_weekly_limit
    if weekly and weekly > 0:
        week_total = _sum_deposits_since(user.id, hours=168)
        if week_total + amount > weekly:
            remaining = max(0, weekly - week_total)
            return False, f'Превышен недельный лимит депозитов. Доступно: {remaining:.2f}'

    return True, ''


def _sum_deposits_since(user_id: int, hours: int) -> float:
    """Sum completed deposits for a user in the last N hours."""
    from app.db import get_db, DB_ENGINE
    try:
        with get_db() as db:
            if DB_ENGINE == 'postgresql':
                db.execute(
                    """SELECT COALESCE(SUM(amount), 0) as total FROM transactions
                       WHERE user_id = %s AND type = 'deposit' AND status = 'completed'
                         AND created_at >= NOW() - INTERVAL '%s hours'""",
                    (user_id, hours)
                )
            else:
                db.execute(
                    """SELECT COALESCE(SUM(amount), 0) as total FROM transactions
                       WHERE user_id = ? AND type = 'deposit' AND status = 'completed'
                         AND created_at >= datetime('now', ? || ' hours')""",
                    (user_id, f'-{hours}')
                )
            row = db.fetchone()
            return float(row['total']) if row else 0.0
    except Exception:
        return 0.0
