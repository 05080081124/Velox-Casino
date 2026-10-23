"""app/api/vip.py — VIP-клуб: покупка, статус, бонусы."""
from __future__ import annotations
import json, os
from datetime import datetime, timedelta, timezone as _tz


def _now_utc():
    return datetime.now(_tz.utc)

def _parse_dt(s):
    """Parse ISO datetime string, always return timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz.utc)
    return dt
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.wallet_service import WalletService, InsufficientFundsError, WalletError
from app.services.currency_service import convert

vip_bp = Blueprint('vip', __name__, url_prefix='/api/vip')

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'vip.json')


def _cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _cu() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


# ── GET /api/vip/config — публичный конфиг тиров ─────────────────────────────

@vip_bp.route('/config')
def vip_config():
    cfg = _cfg()
    return jsonify({
        'tiers':     cfg.get('tiers', {}),
        'durations': cfg.get('durations', []),
        'enabled':   cfg.get('enabled', True),
    })


# ── GET /api/vip/status — статус VIP текущего юзера ──────────────────────────

@vip_bp.route('/status')
def vip_status():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    tier    = user.vip_tier or 'none'
    expires = user.vip_expires_at

    active = False
    if tier != 'none' and expires:
        try:
            exp_dt = _parse_dt(expires)
            active = exp_dt > _now_utc()
        except Exception:
            pass

    cfg   = _cfg()
    tiers = cfg.get('tiers', {})
    benefits = tiers.get(tier, {}).get('benefits', {}) if active else {}

    return jsonify({
        'tier':       tier if active else 'none',
        'active':     active,
        'expires_at': expires if active else None,
        'benefits':   benefits,
    })


# ── POST /api/vip/purchase — купить VIP ──────────────────────────────────────

@vip_bp.route('/purchase', methods=['POST'])
def vip_purchase():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d       = request.get_json() or {}
    tier_id = (d.get('tier') or '').lower().strip()
    months  = int(d.get('months', 1))

    cfg   = _cfg()
    if not cfg.get('enabled', True):
        return jsonify({'error': 'VIP-клуб временно недоступен'}), 400

    tiers = cfg.get('tiers', {})
    if tier_id not in tiers:
        return jsonify({'error': 'Неверный тир VIP'}), 400

    valid_months = [d['months'] for d in cfg.get('durations', [])]
    if months not in valid_months:
        return jsonify({'error': 'Неверный срок подписки'}), 400

    # Цена в UAH
    price_uah = tiers[tier_id]['prices'].get(str(months))
    if price_uah is None:
        return jsonify({'error': 'Цена не найдена'}), 400

    # Конвертируем в валюту юзера
    user_currency = user.currency or 'UAH'
    price_in_cur  = round(convert(price_uah, 'UAH', user_currency), 2)

    # Списываем с баланса
    try:
        WalletService.withdraw(
            user_id=user.id,
            amount=price_in_cur,
            meta={
                'type':       'vip_purchase',
                'tier':       tier_id,
                'months':     months,
                'price_uah':  price_uah,
                'currency':   user_currency,
            }
        )
    except InsufficientFundsError:
        return jsonify({'error': 'insufficient_funds'}), 402
    except WalletError as e:
        return jsonify({'error': str(e)}), 400

    # Считаем дату окончания (продлеваем если уже есть активный)
    now = _now_utc()
    current_exp = user.vip_expires_at
    base_dt = now
    if current_exp:
        try:
            exp_dt = _parse_dt(current_exp)
            if exp_dt > now:
                base_dt = exp_dt   # продлеваем от текущей даты истечения
        except Exception:
            pass

    new_expires = (base_dt + timedelta(days=30 * months)).isoformat()

    # Начисляем бонусы за покупку
    benefits = tiers[tier_id].get('benefits', {})
    free_spins = benefits.get('free_spins_monthly', 0)
    if free_spins > 0:
        user.add_free_spins(free_spins)

    # Обновляем VIP поля
    user.set_vip(tier_id, new_expires)

    fresh = User.get_by_id(user.id)
    return jsonify({
        'success':    True,
        'tier':       tier_id,
        'expires_at': new_expires,
        'balance':    round(fresh.balance, 2),
        'free_spins_granted': free_spins,
    })


# ── POST /api/vip/cancel — отмена (не возвращает деньги, просто не продлевает) 

@vip_bp.route('/cancel', methods=['POST'])
def vip_cancel():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    user.set_vip('none', None)
    return jsonify({'success': True})