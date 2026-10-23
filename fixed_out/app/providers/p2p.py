"""
P2P — ручное подтверждение переводов (Monobank, ПриватБанк, Tinkoff, СБП)

Это НЕ автоматический процессинг. Логика:
  1. Пользователь видит реквизиты карты/телефона
  2. Делает перевод самостоятельно
  3. Оператор вручную подтверждает получение → update payment_orders SET status='success'
  
  Либо: настройте webhook от банка (если банк поддерживает) для auto_confirm.

Настройка:
  - card_number: номер карты для приёма
  - sbp_phone: телефон для СБП (Россия)
  - bank: monobank | privatbank | tinkoff
  Всё в config/payments.json → systems[id=p2p_ua] / systems[id=p2p_ru]

STUB: P2P работает в ручном режиме без дополнительной настройки.
"""
import time, json, os

def create_payment(user_id, amount, currency, ptype='deposit'):
    order_id = f'p2p_{user_id}_{int(time.time())}'
    # P2P не требует внешнего URL — показываем реквизиты на фронте
    # payment_url=None → фронт должен показать номер карты из config/payments.json
    return None, order_id

def handle_callback(request):
    # P2P callback вызывается вручную оператором или банком
    data = request.get_json() or {}
    return {
        'external_id': data.get('order_id', ''),
        'status': data.get('status', 'failed'),
        'amount': float(data.get('amount', 0)),
    }
