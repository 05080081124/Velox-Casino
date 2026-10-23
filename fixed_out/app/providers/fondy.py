"""
app/providers/fondy.py — Провайдер Fondy (заглушка).

Реализуй create_payment() и handle_callback() по документации Fondy:
https://docs.fondy.eu/

Переменные окружения:
    FONDY_MERCHANT_ID
    FONDY_SECRET_KEY
    FONDY_CALLBACK_URL
"""
import os, hashlib, json, time
from flask import Request

MERCHANT_ID  = os.environ.get('FONDY_MERCHANT_ID', '')
SECRET_KEY   = os.environ.get('FONDY_SECRET_KEY', '')
CALLBACK_URL = os.environ.get('FONDY_CALLBACK_URL', 'https://yourdomain.com/api/payments/callback/fondy')


def _signature(*args) -> str:
    """Формирует подпись Fondy: SHA1 от SECRET_KEY|param1|param2|..."""
    parts = [SECRET_KEY] + [str(a) for a in args]
    raw   = '|'.join(parts)
    return hashlib.sha1(raw.encode()).hexdigest()


def create_payment(user_id: int, amount: float, currency: str, ptype: str) -> tuple[str | None, str]:
    """
    Создаёт платёж через Fondy API.
    Возвращает (checkout_url, order_id).
    """
    if ptype == 'withdraw':
        # Вывод через Fondy реализуется отдельно (P2P endpoint)
        return None, f'fondy_withdraw_{user_id}_{int(time.time())}'

    order_id  = f'velox_{user_id}_{int(time.time())}'
    amount_kopecks = int(amount * 100)

    # TODO: реальный запрос к Fondy API
    # import requests
    # payload = {
    #     'request': {
    #         'order_id': order_id,
    #         'merchant_id': MERCHANT_ID,
    #         'amount': amount_kopecks,
    #         'currency': currency,
    #         'order_desc': 'VELOX Casino deposit',
    #         'response_url': CALLBACK_URL,
    #     }
    # }
    # payload['request']['signature'] = _signature(...)
    # r = requests.post('https://pay.fondy.eu/api/checkout/url/', json=payload)
    # checkout_url = r.json()['response']['checkout_url']

    checkout_url = None  # Заглушка
    return checkout_url, order_id


def handle_callback(request: Request) -> dict:
    """
    Обрабатывает POST-вебхук от Fondy.
    Возвращает {'external_id': ..., 'status': 'success'|'failed', 'amount': ...}
    """
    data = request.get_json() or request.form.to_dict()

    # Проверка подписи
    # signature = data.get('signature')
    # expected  = _signature(data.get('order_id'), data.get('amount'))
    # if signature != expected:
    #     raise ValueError('Неверная подпись Fondy')

    order_status = data.get('order_status', '')
    return {
        'external_id': data.get('order_id', ''),
        'status':      'success' if order_status == 'approved' else 'failed',
        'amount':      float(data.get('amount', 0)) / 100,
    }
