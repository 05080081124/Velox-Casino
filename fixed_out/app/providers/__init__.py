"""
app/providers/ — Модули платёжных провайдеров.

Каждый файл реализует два метода:
  create_payment(user_id, amount, currency, ptype) → (payment_url, external_id)
  handle_callback(request) → {'external_id': ..., 'status': ..., 'amount': ...}

Настройки API-ключей — в config/payments.json
"""
