"""app/api/currency.py — Currency rates API + background auto-refresh.

Endpoints:
  GET  /api/currency/rates          — current rates + age info (public)
  POST /api/currency/change         — change user currency + convert balance
  POST /api/currency/refresh        — force refresh rates from NBU (admin/internal)

Auto-refresh:
  Runs in a background thread on startup (every 30 min).
  Falls back to cached rates if NBU is unreachable.
"""
from __future__ import annotations
import os
import threading
import time
import logging

from flask import Blueprint, jsonify, request, session
from app.models.user import User
from app.services.currency_service import (
    get_rates, get_rates_info, update_rates, convert, from_uah, to_uah
)

currency_bp = Blueprint('currency', __name__, url_prefix='/api/currency')
logger = logging.getLogger(__name__)

_SUPPORTED = ('UAH', 'USD', 'EUR', 'USDT')

# ── Background auto-refresh ───────────────────────────────────────────────────

_refresh_thread: threading.Thread | None = None
_REFRESH_INTERVAL = 30 * 60  # 30 minutes


def _refresh_loop():
    """Runs in background, updates rates every 30 minutes."""
    # Initial update at startup
    time.sleep(5)
    while True:
        try:
            ok = update_rates()
            logger.info(f'[currency] Auto-refresh: {"OK" if ok else "failed, using cache"}')
        except Exception as e:
            logger.error(f'[currency] Auto-refresh error: {e}')
        time.sleep(_REFRESH_INTERVAL)


def start_currency_refresh():
    """Start background refresh thread. Call once from create_app()."""
    global _refresh_thread
    if _refresh_thread and _refresh_thread.is_alive():
        return
    _refresh_thread = threading.Thread(
        target=_refresh_loop,
        daemon=True,
        name='currency-refresh'
    )
    _refresh_thread.start()
    logger.info('[currency] Background refresh started (every 30 min, source: NBU)')


# ── API ───────────────────────────────────────────────────────────────────────

@currency_bp.route('/list')
def currency_list():
    """
    Returns list of currencies enabled by the casino operator in site.json.
    Frontend uses this to build the currency selector.
    """
    import json as _json
    _site_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site.json')
    try:
        site = _json.load(open(_site_path, encoding='utf-8'))
    except Exception:
        site = {}

    currencies = site.get('currencies', [])

    # Support both old format (list of strings) and new format (list of dicts)
    _SYMBOLS = {'UAH': '₴', 'USD': '$', 'EUR': '€', 'USDT': '₮', 'RUB': '₽'}
    result = []
    for c in currencies:
        if isinstance(c, str):
            result.append({'code': c, 'symbol': _SYMBOLS.get(c, c), 'name': c, 'default': c == 'UAH'})
        else:
            result.append(c)

    default = site.get('default_currency', result[0]['code'] if result else 'UAH')

    return jsonify({'currencies': result, 'default': default})


@currency_bp.route('/rates')
def rates():
    """Public endpoint — returns current rates and freshness info."""
    info = get_rates_info()
    rates_to_uah = info.get('rates', {})

    # Build a display-friendly table: each currency → how much UAH
    # and also inverse (1 UAH = X currency)
    display = {}
    for cur, rate_to_uah in rates_to_uah.items():
        if cur == 'UAH':
            continue
        display[cur] = {
            'rate_to_uah':   round(float(rate_to_uah), 4),
            'rate_from_uah': round(1 / float(rate_to_uah), 6) if rate_to_uah else 0,
        }

    updated_at = info.get('updated_at', 0)
    age_minutes = int((time.time() - float(updated_at)) / 60) if updated_at else None

    return jsonify({
        'rates':       display,
        'base':        'UAH',
        'source':      info.get('source', 'fallback'),
        'fresh':       info.get('fresh', False),
        'updated_at':  updated_at,
        'age_minutes': age_minutes,
        'supported':   list(_SUPPORTED),
    })


@currency_bp.route('/change', methods=['POST'])
def change_currency():
    """
    Change authenticated user's currency and convert their balance.

    Body: { "currency": "USD" }

    Returns: { "success": true, "balance": 12.34, "currency": "USD",
               "old_balance": 500.0, "old_currency": "UAH",
               "rate": 41.5 }
    """
    uid = session.get('user_id')
    if not uid:
        return jsonify({'error': 'Требуется авторизация'}), 401

    user = User.get_by_id(uid)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    d       = request.get_json() or {}
    new_cur = (d.get('currency') or '').strip().upper()

    if new_cur not in _SUPPORTED:
        return jsonify({'error': f'Поддерживаемые валюты: {", ".join(_SUPPORTED)}'}), 400

    old_cur     = user.currency
    old_balance = user.balance

    if old_cur == new_cur:
        return jsonify({
            'success':      True,
            'balance':      round(old_balance, 2),
            'currency':     new_cur,
            'old_balance':  round(old_balance, 2),
            'old_currency': old_cur,
            'rate':         1.0,
            'converted':    False,
        })

    # Convert balance: old_currency → UAH → new_currency
    new_balance = convert(old_balance, old_cur, new_cur)

    # Calculate display rate (1 unit of old → N units of new)
    rate = round(convert(1.0, old_cur, new_cur), 6)

    # Persist: update balance + currency in DB atomically
    from app.db import get_db
    with get_db() as db:
        db.execute(
            'UPDATE users SET balance = %s, currency = %s WHERE id = %s',
            (new_balance, new_cur, user.id)
        )

    logger.info(
        f'[currency] User {user.id}: {old_balance} {old_cur} → {new_balance} {new_cur} '
        f'(rate: {rate})'
    )

    return jsonify({
        'success':      True,
        'balance':      round(new_balance, 2),
        'currency':     new_cur,
        'old_balance':  round(old_balance, 2),
        'old_currency': old_cur,
        'rate':         rate,
        'converted':    True,
    })


@currency_bp.route('/refresh', methods=['POST'])
def force_refresh():
    """Manually trigger NBU rate refresh. Returns new rates."""
    ok = update_rates()
    info = get_rates_info()
    return jsonify({
        'success':    ok,
        'updated_at': info.get('updated_at'),
        'source':     info.get('source'),
        'rates':      info.get('rates', {}),
    })
