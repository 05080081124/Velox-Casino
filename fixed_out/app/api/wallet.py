"""app/api/wallet.py — Blueprint кошелька (баланс, депозит, промокоды, история).

Логика пополнения:
  - POST /api/balance/deposit без provider → прямое зачисление (тест/промо)
  - POST /api/balance/deposit с provider   → через WithdrawalService (реальный провайдер)
"""
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.wallet_service import WalletService, InsufficientFundsError, WalletError
from app.services.withdrawal_service import (
    WithdrawalService, WithdrawalError, WithdrawalLimitError,
    CurrencyNotSupportedError, ProviderError,
)

wallet_bp = Blueprint('wallet', __name__, url_prefix='/api')

# Минимальные суммы депозита по валютам
_MIN_DEPOSIT = {'UAH': 10, 'RUB': 50, 'USD': 1.0, 'EUR': 1.0, 'USDT': 1.0}


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


@wallet_bp.route('/balance')
def get_balance():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    return jsonify({'balance': round(user.balance, 2), 'currency': user.currency})


@wallet_bp.route('/balance/deposit', methods=['POST'])
def deposit():
    """
    Пополнение баланса.

    Без provider (или provider='direct') — прямое зачисление в тестовом режиме.
    С provider='liqpay'|'paypal'|... — инициирует платёж через WithdrawalService,
    возвращает payment_url для перенаправления.
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d        = request.get_json() or {}
    amount   = float(d.get('amount', 0))
    provider = d.get('provider', 'direct').lower()
    currency = d.get('currency', user.currency or 'UAH')
    meta     = d.get('meta', {})
    mn       = _MIN_DEPOSIT.get(currency, 50)

    if amount <= 0:
        return jsonify({'error': 'Сумма должна быть положительной'}), 400
    if amount < mn:
        return jsonify({'error': f'Минимум {mn} {currency}'}), 400

    # ── Responsible Gambling check ────────────────────────────────────────────
    try:
        from app.api.responsible_gambling import check_rg_deposit_allowed
        allowed, rg_reason = check_rg_deposit_allowed(user, amount)
        if not allowed:
            return jsonify({'error': rg_reason, 'rg_blocked': True}), 403
    except Exception:
        pass

    # ── Реальный провайдер через WithdrawalService ────────────────────────────
    real_providers = ('liqpay', 'paypal', 'googlepay', 'applepay', 'bank_transfer')
    if provider in real_providers:
        try:
            result = WithdrawalService.deposit(
                user_id=user.id,
                amount=amount,
                currency=currency,
                provider=provider,
                meta=meta,
            )
            return jsonify({
                'success'          : True,
                'provider'         : provider,
                'provider_tx_id'   : result['provider_tx_id'],
                'status'           : result['status'],
                'payment_url'      : result.get('payment_url'),
                'liqpay_data'      : result.get('liqpay_data'),
                'liqpay_signature' : result.get('liqpay_signature'),
                'instructions'     : result.get('instructions'),
                'message'          : (
                    'Перенаправляем на оплату' if result.get('payment_url')
                    else 'Запрос создан — ожидайте подтверждения'
                ),
            })
        except (WithdrawalLimitError, CurrencyNotSupportedError, WithdrawalError) as e:
            return jsonify({'error': str(e)}), 400
        except ProviderError as e:
            return jsonify({'error': f'Ошибка провайдера: {e}'}), 502

    # ── Прямое зачисление (только когда allow_direct_deposit:true + test_mode:true) ──
    # Защита от бесплатного пополнения на продакшне
    try:
        import json as _json, os as _os
        _pcfg_path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'config', 'payments.json')
        _pcfg = _json.load(open(_pcfg_path, encoding='utf-8'))
        _glb = _pcfg.get('global', {})
        _allow_direct = _glb.get('allow_direct_deposit', False)
        _test_mode = _glb.get('test_mode', True)
    except Exception:
        _allow_direct = False
        _test_mode = True

    if not (_allow_direct and _test_mode):
        return jsonify({'error': 'Прямое зачисление отключено. Используйте платёжную систему.'}), 403

    try:
        new_balance = WalletService.deposit(
            user.id, amount,
            meta={'currency': currency, 'source': 'manual_direct'}
        )
    except WalletError as e:
        return jsonify({'error': str(e)}), 400

    # ── Referral bonus (first deposit) ────────────────────────────────────────
    try:
        from app.api.affiliate import process_referral_bonus
        process_referral_bonus(user, amount)
    except Exception:
        pass

    # ── Email receipt ─────────────────────────────────────────────────────────
    if user.email:
        try:
            from app.services.email_service import send_deposit_receipt
            import secrets as _sec
            send_deposit_receipt(
                email=user.email, username=user.username,
                amount=amount, currency=currency,
                tx_id=_sec.token_hex(8).upper(), method='Direct',
            )
        except Exception as _e:
            print(f'[wallet/deposit] email error: {_e}')

    return jsonify({'success': True, 'balance': new_balance, 'status': 'completed'})


@wallet_bp.route('/promo/redeem', methods=['POST'])
def redeem_promo():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    code = (request.get_json() or {}).get('code', '').strip().upper()
    if not code:
        return jsonify({'error': 'Укажите промокод'}), 400

    try:
        bonus, new_balance = WalletService.apply_promo(user.id, code)
    except WalletError as e:
        return jsonify({'error': str(e)}), 400

    return jsonify({'success': True, 'bonus': bonus, 'balance': new_balance})


@wallet_bp.route('/transactions')
def transactions():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    from app.models.transaction import Transaction
    txs = Transaction.list_for_user(
        user.id, limit=50,
        types=['deposit', 'withdraw', 'bonus', 'refund']
    )
    return jsonify({'transactions': [t.to_dict() for t in txs]})

