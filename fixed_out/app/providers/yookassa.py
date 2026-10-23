"""
ЮКасса (YooKassa) — российский платёжный провайдер.

Документация: https://yookassa.ru/developers/
Регистрация:  https://yookassa.ru/

Настройка:
  1. Зарегистрируйтесь на yookassa.ru (нужен ИП или ООО)
  2. В настройках магазина получите shop_id и secret_key
  3. Укажите их в config/payments.json → systems[id=card_ru]
  4. Webhook URL: https://your-domain.com/api/payments/callback/yookassa

STUB: Замените реализацией после получения API-ключей.
"""
import time

def create_payment(user_id, amount, currency, ptype='deposit'):
    mock_id = f'yk_stub_{user_id}_{int(time.time())}'
    # TODO: реализовать через yookassa SDK
    # pip install yookassa
    # from yookassa import Configuration, Payment
    # Configuration.account_id = shop_id
    # Configuration.secret_key = secret_key
    # payment = Payment.create({...})
    raise NotImplementedError('YooKassa: заполните shop_id и secret_key в config/payments.json')

def handle_callback(request):
    data = request.get_json() or {}
    obj = data.get('object', {})
    return {
        'external_id': obj.get('id', ''),
        'status': 'success' if obj.get('status') == 'succeeded' else 'failed',
        'amount': float(obj.get('amount', {}).get('value', 0)),
    }
