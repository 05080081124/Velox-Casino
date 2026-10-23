"""
app/api/mineslot_api.py — MineSlot API Blueprint.
"""
import json, os
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.freespin_service import FreeSpinService
from app.services.wallet_service import WalletService

mineslot_bp = Blueprint('mineslot', __name__)

def _me():
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None

def _cfg():
    cfg_path = os.path.join(os.path.dirname(__file__), '..', 'games', 'mineslot', 'config.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}

@mineslot_bp.route('/api/mineslot/config')
def mineslot_config():
    cfg_path = os.path.join(os.path.dirname(__file__), '..', 'games', 'mineslot', 'config.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': 'config_not_found', 'detail': str(e)}), 404

@mineslot_bp.route('/api/mineslot/init')
def mineslot_init():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth', 'authenticated': False}), 401

    cfg = _cfg()
    try:
        mode_info = FreeSpinService.get_game_mode(user.id, cfg, user.currency)
    except Exception:
        mode_info = {'mode': 'balance', 'fs_remaining': 0, 'fs_bet': 1.0,
                     'currency': user.currency, 'supports_fs': False}

    try:
        balance = WalletService.get_balance(user.id)
    except Exception:
        balance = user.balance

    # Определение мобильного устройства для адаптации UI
    ua = request.headers.get('User-Agent', '').lower()
    is_mobile = any(x in ua for x in ['mobile', 'android', 'iphone', 'ipad'])

    # Bonus prices in user currency with VIP discount
    try:
        from app.games.mineslot.listener import (
            _bonus_base_cost_uah, _vip_discount, _get_vip_benefits
        )
        from app.services.currency_service import convert

        def _price(bonus_type):
            base_uah = _bonus_base_cost_uah(bonus_type)
            try:    cost = round(convert(base_uah, 'UAH', user.currency), 2)
            except: cost = base_uah
            if user.vip_active:
                cost = round(cost * _vip_discount(user.vip_tier), 2)
            return cost

        bonus_prices = {'extra_chance': _price('extra_chance'), 'block_bonus': _price('block_bonus')}
        vip_benefits = _get_vip_benefits(user)
    except Exception:
        bonus_prices = {'extra_chance': 485.0, 'block_bonus': 700.0}
        vip_benefits = {}

    return jsonify({
        'ok':           True,
        'authenticated': True,
        'balance':      round(float(balance), 2),
        'currency':     user.currency,
        'username':     user.username,
        'mode':         mode_info.get('mode', 'balance'),
        'fs_active':    mode_info.get('mode') == 'freespin',
        'fs_remaining': mode_info.get('fs_remaining', 0),
        'fs_bet':       mode_info.get('fs_bet', 1.0),
        'supports_fs':  mode_info.get('supports_fs', False),
        'vip_tier':     user.vip_tier if user.vip_active else 'none',
        'vip_active':   user.vip_active,
        'vip_benefits': vip_benefits,
        'bonus_prices': bonus_prices,
        'is_mobile':    is_mobile,
        'ui_config': {
            'min_break_duration': 150,
            'upgrade_delay': 800,
            'finish_delay': 400,
            'fs_next_delay': 800,
            'keep_unbroken': True,      # Флаг для JS: не очищать неразрушенные блоки
            'fade_out_duration': 1200,  # Время на плавное растворение кирки в конце
            'mobile_scale': 0.85 if is_mobile else 1.0
        }
    })

@mineslot_bp.route('/api/mineslot/spin', methods=['POST'])
def mineslot_spin():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    d = request.get_json(silent=True) or {}
    try:
        import app.games.mineslot.listener as _L
        result = _L.do_spin(
            user,
            float(d.get('bet', 5)),
            bool(d.get('extra_chance', False)),
            int(d.get('force_eyes', 0)),
            bool(d.get('is_free_spin', False)),
            bool(d.get('block_bonus_mode', False)),
            persisted_field=d.get('persisted_field', None),
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f'[mineslot] SPIN 500:\n{tb}')
        return jsonify({'ok': False, 'error': str(e), 'traceback': tb}), 500
    return jsonify(result)

@mineslot_bp.route('/api/mineslot/buy_bonus', methods=['POST'])
def mineslot_buy_bonus():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    d = request.get_json(silent=True) or {}
    try:
        import app.games.mineslot.listener as _L
        result = _L.buy_bonus(user, str(d.get('bonus_type', '')), float(d.get('bet', 5)))
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return (jsonify(result), 400) if result.get('error') else jsonify(result)