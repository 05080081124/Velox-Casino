"""
app/api/payments.py — Blueprint платежей.

Маршруты:
  POST /api/payments/create     → создать заявку на пополнение
  POST /api/payments/withdraw   → вывод средств
  POST /api/payments/callback/<provider> → webhook от провайдера
  GET  /api/payments/config     → публичная конфигурация
  GET  /api/payments/history    → история транзакций
"""
import json, os
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.wallet_service import WalletService, WalletError, InsufficientFundsError
from app.services.withdrawal_service import (
    WithdrawalService, WithdrawalError, WithdrawalLimitError,
    CurrencyNotSupportedError, ProviderError, get_provider,
)
from app.db import get_db

payments_bp = Blueprint('payments', __name__, url_prefix='/api/payments')

_PROVIDERS = ('fondy', 'wayforpay', 'yookassa', 'stripe', 'crypto', 'p2p',
              'paypal', 'googlepay', 'applepay', 'bank_transfer', 'liqpay')

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config/payments.json')


def _load_payments_config():
    try:
        return json.load(open(_CONFIG_PATH, encoding='utf-8'))
    except Exception:
        return {}


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


# ── ПОПОЛНЕНИЕ ────────────────────────────────────────────────────────────────

@payments_bp.route('/create', methods=['POST'])
def create_payment():
    """Создать платёжный ордер и вернуть URL для оплаты."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d        = request.get_json() or {}
    provider = d.get('provider', 'liqpay').lower()
    amount   = float(d.get('amount', 0))
    currency = d.get('currency', user.currency or 'UAH')
    meta     = d.get('meta', {})

    if amount <= 0:
        return jsonify({'error': 'Сумма должна быть положительной'}), 400

    # Новые провайдеры через WithdrawalService
    withdrawal_providers = ('paypal', 'googlepay', 'applepay', 'bank_transfer', 'liqpay')
    if provider in withdrawal_providers:
        try:
            result = WithdrawalService.deposit(
                user_id=user.id,
                amount=amount,
                currency=currency,
                provider=provider,
                meta=meta,
            )
            return jsonify({
                'success'         : True,
                'provider_tx_id'  : result['provider_tx_id'],
                'payment_url'     : result.get('payment_url'),
                'status'          : result['status'],
                'liqpay_data'     : result.get('liqpay_data'),
                'liqpay_signature': result.get('liqpay_signature'),
                'instructions'    : result.get('instructions'),
                'message'         : 'Перенаправляем на оплату' if result.get('payment_url') else 'Ордер создан',
            })
        except (WithdrawalError, WithdrawalLimitError, CurrencyNotSupportedError) as e:
            return jsonify({'error': str(e)}), 400
        except ProviderError as e:
            return jsonify({'error': f'Ошибка провайдера: {e}'}), 502

    if provider not in _PROVIDERS:
        return jsonify({'error': f'Неизвестный провайдер: {provider}'}), 400

    # Старые провайдеры — прежняя логика
    try:
        from importlib import import_module
        mod = import_module(f'app.providers.{provider}')
        payment_url, external_id = mod.create_payment(
            user_id=user.id, amount=amount,
            currency=currency, ptype='deposit',
        )
    except ImportError:
        payment_url  = None
        external_id  = f'mock_{provider}_{user.id}'

    with get_db() as db:
        db.execute(
            """INSERT INTO payment_orders
               (user_id, provider, external_id, amount, currency, type, status, meta, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s::jsonb, NOW(), NOW())
               RETURNING id""",
            (user.id, provider, external_id, amount, currency, 'deposit',
             json.dumps({'initiated_by': 'user'}))
        )
        order_id = db.fetchone()['id']

    return jsonify({
        'success'    : True,
        'order_id'   : order_id,
        'payment_url': payment_url,
        'message'    : 'Ордер создан' if not payment_url else 'Перенаправляем на оплату',
    })


# ── ВЫВОД ─────────────────────────────────────────────────────────────────────

@payments_bp.route('/withdraw', methods=['POST'])
def withdraw_payment():
    """
    Вывод средств через провайдера.

    Body JSON:
      provider  — 'liqpay' | 'paypal' | 'bank_transfer' | ...
      amount    — сумма
      currency  — 'UAH' | 'USD' | 'EUR'
      meta      — реквизиты (зависят от провайдера):
                  liqpay:       {'card': '4111...'}
                  paypal:       {'paypal_email': 'user@example.com'}
                  bank_transfer:{'iban': 'UA...', 'account_holder': '...'}
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d        = request.get_json() or {}
    provider = d.get('provider', 'liqpay').lower()
    amount   = float(d.get('amount', 0))
    currency = d.get('currency', user.currency or 'UAH')
    meta     = d.get('meta', {})

    if amount <= 0:
        return jsonify({'error': 'Сумма должна быть положительной'}), 400

    try:
        result = WithdrawalService.withdraw(
            user_id=user.id,
            amount=amount,
            currency=currency,
            provider=provider,
            meta=meta,
        )
        # ── Email receipt ─────────────────────────────────────────────────────
        if user.email:
            try:
                from app.services.email_service import send_withdrawal_receipt
                send_withdrawal_receipt(
                    email=user.email, username=user.username,
                    amount=amount, currency=currency,
                    tx_id=result['provider_tx_id'], method=provider,
                    status=result['status'],
                )
            except Exception as _e:
                print(f'[payments/withdraw] email error: {_e}')

        return jsonify({
            'success'       : True,
            'provider_tx_id': result['provider_tx_id'],
            'balance'       : result['balance'],
            'status'        : result['status'],
            'fee'           : result['fee'],
            'note'          : result.get('note', ''),
            'message'       : 'Запрос на вывод принят',
        })

    except InsufficientFundsError:
        return jsonify({'error': 'Недостаточно средств на балансе'}), 400
    except WithdrawalLimitError as e:
        return jsonify({'error': str(e)}), 400
    except CurrencyNotSupportedError as e:
        return jsonify({'error': str(e)}), 400
    except WithdrawalError as e:
        return jsonify({'error': str(e)}), 400
    except ProviderError as e:
        return jsonify({'error': f'Ошибка провайдера: {e}'}), 502


# ── WEBHOOKS ──────────────────────────────────────────────────────────────────

@payments_bp.route('/callback/<provider>', methods=['POST'])
def payment_callback(provider: str):
    """Webhook от платёжного провайдера."""

    # LiqPay callback
    if provider == 'liqpay':
        return _liqpay_callback()

    # PayPal callback
    if provider == 'paypal':
        return _paypal_callback()

    # Старые провайдеры
    if provider not in _PROVIDERS:
        return jsonify({'error': 'Unknown provider'}), 400

    try:
        from importlib import import_module
        mod    = import_module(f'app.providers.{provider}')
        result = mod.handle_callback(request)
    except ImportError:
        return jsonify({'error': 'Provider not implemented'}), 501
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    external_id = result.get('external_id')
    status      = result.get('status')
    amount      = float(result.get('amount', 0))

    with get_db() as db:
        db.execute(
            'SELECT * FROM payment_orders WHERE external_id = %s', (external_id,)
        )
        order = db.fetchone()
        if not order:
            return jsonify({'error': 'Order not found'}), 404
        order = dict(order)
        if order['status'] != 'pending':
            return jsonify({'ok': True})
        db.execute(
            'UPDATE payment_orders SET status = %s, updated_at = NOW() WHERE external_id = %s',
            (status, external_id)
        )

    if status == 'success' and order['type'] == 'deposit':
        try:
            WalletService.deposit(
                order['user_id'], amount,
                meta={'provider': provider, 'external_id': external_id}
            )
        except WalletError:
            pass

    return jsonify({'ok': True})


def _liqpay_callback():
    """Обработать LiqPay IPN webhook (server_url)."""
    import base64, hashlib

    data_b64  = request.form.get('data', '')
    signature = request.form.get('signature', '')

    if not data_b64:
        return jsonify({'error': 'No data'}), 400

    cfg = _load_payments_config()
    private_key = next(
        (s.get('private_key', '') for s in cfg.get('systems', []) if s['id'] == 'liqpay'), ''
    )
    if private_key:
        expected = base64.b64encode(
            hashlib.sha1((private_key + data_b64 + private_key).encode()).digest()
        ).decode()
        if expected != signature:
            return jsonify({'error': 'Invalid signature'}), 403

    try:
        data = json.loads(base64.b64decode(data_b64).decode())
    except Exception:
        return jsonify({'error': 'Invalid data'}), 400

    status   = data.get('status', '')
    order_id = data.get('order_id', '')
    amount   = float(data.get('amount', 0))
    action   = data.get('action', '')

    # success + sandbox = успешная оплата
    if status in ('success', 'sandbox') and action == 'pay':
        WithdrawalService.confirm_deposit(order_id, amount, 'liqpay')

    return jsonify({'ok': True})


@payments_bp.route('/liqpay/return', methods=['GET', 'POST'])
def liqpay_return():
    """
    Страница возврата после LiqPay оплаты (result_url).

    LiqPay POST'ит сюда data+signature — зачисляем деньги как второй
    гарантированный путь (первый — server_url webhook выше).
    Затем редиректим на главную с ?liqpay=ok — фронт покажет тост.
    """
    from flask import redirect
    import base64, hashlib

    data_b64  = request.form.get('data', '')
    signature = request.form.get('signature', '')

    if data_b64:
        cfg = _load_payments_config()
        private_key = next(
            (s.get('private_key', '') for s in cfg.get('systems', []) if s['id'] == 'liqpay'), ''
        )
        sig_ok = True
        if private_key:
            expected = base64.b64encode(
                hashlib.sha1((private_key + data_b64 + private_key).encode()).digest()
            ).decode()
            sig_ok = (expected == signature)

        if sig_ok:
            try:
                data   = json.loads(base64.b64decode(data_b64).decode())
                status = data.get('status', '')
                action = data.get('action', '')
                if status in ('success', 'sandbox') and action == 'pay':
                    order_id = data.get('order_id', '')
                    amount   = float(data.get('amount', 0))
                    # confirm_deposit идемпотентен — повторный вызов не задвоит деньги
                    WithdrawalService.confirm_deposit(order_id, amount, 'liqpay')
            except Exception:
                pass

    return redirect('/?liqpay=ok')


def _paypal_callback():
    """Обработать PayPal Webhook."""
    data = request.get_json() or {}
    event_type = data.get('event_type', '')

    if event_type == 'PAYMENT.CAPTURE.COMPLETED':
        resource = data.get('resource', {})
        order_id = resource.get('supplementary_data', {}).get('related_ids', {}).get('order_id', '')
        amount   = float(resource.get('amount', {}).get('value', 0))
        if order_id:
            WithdrawalService.confirm_deposit(order_id, amount, 'paypal')

    return jsonify({'ok': True})


# ── ПУБЛИЧНЫЙ КОНФИГ ──────────────────────────────────────────────────────────

def _provider_is_configured(s: dict) -> bool:
    """Return True if the provider has real API keys filled in (not empty strings)."""
    pid = s.get('id', '')
    keys_map = {
        'liqpay':        ['public_key', 'private_key'],
        'wayforpay':     ['merchant_account', 'merchant_key'],
        'fondy':         ['merchant_id', 'secret_key'],
        'paypal':        ['client_id', 'client_secret'],
        'stripe':        ['secret_key'],
        'googlepay':     ['gateway_merchant_id', 'merchant_id'],
        'applepay':      ['merchant_id', 'gateway_merchant_id'],
        'crypto':        ['api_key'],
        'p2p':           ['card_number'],
        'bank_transfer': ['iban'],
        'yookassa':      ['shop_id', 'secret_key'],
    }
    required = keys_map.get(pid, [])
    if not required:
        return True
    return all(bool(str(s.get(k, '')).strip()) for k in required)


@payments_bp.route('/config')
def payments_config():
    """Публичная конфигурация платёжных систем (без секретов)."""
    cfg = _load_payments_config()
    systems = cfg.get('systems', [])
    glb = cfg.get('global', {})
    POPULAR_IDS = ['liqpay', 'wayforpay', 'googlepay', 'applepay', 'paypal', 'crypto', 'p2p']
    public = []
    for s in systems:
        # Include all systems — enabled: false ones are shown as "coming soon"
        ptype = s.get('type', 'card')
        configured = _provider_is_configured(s)
        entry = {
            'id'                  : s['id'],
            'label'               : s.get('label', s['id']),
            'type'                : ptype,
            'provider'            : s.get('provider', ''),
            'enabled'             : s.get('enabled', False),
            'configured'          : configured,
            'available'           : s.get('enabled', False) and configured,
            'supported_currencies': s.get('supported_currencies', []),
            'docs'                : s.get('_docs', ''),
            'registration'        : s.get('_registration', ''),
            'popular'             : s['id'] in POPULAR_IDS,
            'logo_url'            : s.get('logo_url', ''),
            'p2p_card'            : s.get('card_number', '') if ptype == 'p2p' else '',
            'p2p_holder'          : s.get('card_holder', '') if ptype == 'p2p' else '',
            'p2p_bank'            : s.get('bank_name', '') if ptype in ('p2p', 'bank') else '',
            'bank_iban'           : s.get('iban', '') if ptype == 'bank' else '',
            'bank_swift'          : s.get('swift_bic', '') if ptype == 'bank' else '',
            'bank_beneficiary'    : s.get('beneficiary_name', '') if ptype == 'bank' else '',
        }
        public.append(entry)
    test_mode = glb.get('test_mode', True)
    allow_direct = glb.get('allow_direct_deposit', False)
    return jsonify({
        'systems'              : public,
        'min_deposit'          : glb.get('min_deposit_uah', 10),
        'test_mode'            : test_mode,
        'allow_direct_deposit' : allow_direct and test_mode,  # only when test_mode ON
    })


@payments_bp.route('/providers')
def payment_providers():
    """Alias for /config — returns enabled providers list."""
    return payments_config()


# ── ИСТОРИЯ ───────────────────────────────────────────────────────────────────

@payments_bp.route('/history')
def payment_history():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    with get_db() as db:
        db.execute(
            'SELECT id, provider, amount, currency, type, status, created_at'
            ' FROM payment_orders WHERE user_id = %s ORDER BY created_at DESC LIMIT 20',
            (user.id,)
        )
        rows = db.fetchall()
    return jsonify({'payments': [dict(r) for r in rows]})

