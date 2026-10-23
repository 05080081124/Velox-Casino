"""
WayForPay — украинский платёжный провайдер.

Документация: https://wayforpay.com/doc
Регистрация:  https://wayforpay.com/

Настройка:
  1. Зарегистрируйтесь на wayforpay.com
  2. В личном кабинете получите merchant_account и merchant_key
  3. Укажите их в config/payments.json → systems[id=card_ua]
  4. Убедитесь что webhook URL настроен: https://your-domain.com/api/payments/callback/wayforpay

STUB: Возвращает mock-ответ. Замените реализацией после получения API-ключей.
"""
import json, hashlib, hmac, os, time

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config/payments.json')

def _get_config():
    try:
        cfg = json.load(open(_CONFIG_PATH, encoding='utf-8'))
        for s in cfg.get('systems', []):
            if s['id'] == 'card_ua':
                return s
    except Exception:
        pass
    return {}

def create_payment(user_id, amount, currency, ptype='deposit'):
    cfg = _get_config()
    merchant_account = cfg.get('merchant_account', '')
    merchant_key = cfg.get('merchant_key', '')

    if not merchant_account or not merchant_key:
        # STUB MODE — имитируем успешный ордер
        mock_id = f'wfp_stub_{user_id}_{int(time.time())}'
        return None, mock_id  # payment_url=None → покажем ошибку на фронте

    # TODO: реализовать реальный запрос к WayForPay API
    # Пример:
    # order_id = f'velox_{user_id}_{int(time.time())}'
    # payload = {
    #     'transactionType': 'CREATE_INVOICE',
    #     'merchantAccount': merchant_account,
    #     'orderReference': order_id,
    #     'amount': amount,
    #     'currency': currency,
    #     ...
    # }
    raise NotImplementedError('WayForPay: заполните merchant_account и merchant_key в config/payments.json')

def handle_callback(request):
    data = request.get_json() or {}
    # TODO: верифицировать подпись от WayForPay
    return {
        'external_id': data.get('orderReference', ''),
        'status': 'success' if data.get('transactionStatus') == 'Approved' else 'failed',
        'amount': float(data.get('amount', 0)),
    }
