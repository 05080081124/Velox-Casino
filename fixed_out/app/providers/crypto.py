"""
NOWPayments — криптовалютный процессинг (USDT, BTC, ETH и др.)

Документация: https://documenter.getpostman.com/view/7907941/S1a32n38
Регистрация:  https://nowpayments.io/

Настройка:
  1. Зарегистрируйтесь на nowpayments.io
  2. Получите API key в Settings → API Keys
  3. Настройте IPN (webhook): https://your-domain.com/api/payments/callback/crypto
  4. Укажите api_key и ipn_secret в config/payments.json → systems[id=crypto]

STUB: Замените реализацией после получения API-ключей.
"""
import time

def create_payment(user_id, amount, currency, ptype='deposit'):
    mock_id = f'np_stub_{user_id}_{int(time.time())}'
    # TODO: POST https://api.nowpayments.io/v1/payment
    # headers = {'x-api-key': api_key}
    # body = {'price_amount': amount, 'price_currency': currency.lower(), ...}
    raise NotImplementedError('NOWPayments: укажите api_key в config/payments.json → systems[id=crypto]')

def handle_callback(request):
    data = request.get_json() or {}
    return {
        'external_id': data.get('order_id', ''),
        'status': 'success' if data.get('payment_status') == 'finished' else 'failed',
        'amount': float(data.get('price_amount', 0)),
    }
