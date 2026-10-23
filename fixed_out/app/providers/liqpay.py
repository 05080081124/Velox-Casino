"""
app/providers/liqpay.py — LiqPay провайдер (тонкий адаптер).

Основна логіка знаходиться в WithdrawalService / LiqPayProvider.
Цей файл — адаптер для сумісності зі старим інтерфейсом create_payment / handle_callback.

Документація: https://www.liqpay.ua/documentation/
Реєстрація:   https://www.liqpay.ua/

Налаштування (config/payments.json → id=liqpay):
  public_key  — публічний ключ з особистого кабінету LiqPay
  private_key — приватний ключ з особистого кабінету LiqPay
  callback_url — URL для отримання IPN-повідомлень від LiqPay
"""
import base64, hashlib, json, os, time

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '../../config/payments.json')


def _get_config() -> dict:
    try:
        cfg = json.load(open(_CONFIG_PATH, encoding='utf-8'))
        for s in cfg.get('systems', []):
            if s['id'] == 'liqpay':
                return s
    except Exception:
        pass
    return {}


def _sign(private_key: str, data_b64: str) -> str:
    raw = private_key + data_b64 + private_key
    return base64.b64encode(hashlib.sha1(raw.encode()).digest()).decode()


def create_payment(user_id: int, amount: float, currency: str, ptype: str = 'deposit'):
    """
    Створити платіж LiqPay.
    Повертає (checkout_url | None, order_id).
    """
    cfg = _get_config()
    public_key  = cfg.get('public_key', '')
    private_key = cfg.get('private_key', '')
    callback_url = cfg.get('callback_url', 'https://yourdomain.com/api/payments/callback/liqpay')

    order_id = f'liqpay_{"in" if ptype == "deposit" else "out"}_{user_id}_{int(time.time())}'

    if not public_key or not private_key:
        # STUB режим
        return None, order_id

    action = 'pay' if ptype == 'deposit' else 'p2pcredit'
    params = {
        'public_key' : public_key,
        'version'    : '3',
        'action'     : action,
        'amount'     : str(round(amount, 2)),
        'currency'   : currency,
        'description': f'{"Поповнення" if ptype == "deposit" else "Вивід"} VELOX Casino #{user_id}',
        'order_id'   : order_id,
        'server_url' : callback_url,
        'result_url' : 'https://yourdomain.com/payments',
    }
    data_b64  = base64.b64encode(json.dumps(params, ensure_ascii=False).encode()).decode()
    signature = _sign(private_key, data_b64)
    checkout_url = f'https://www.liqpay.ua/api/3/checkout?data={data_b64}&signature={signature}'
    return checkout_url, order_id


def handle_callback(request):
    """
    Обробити IPN-callback від LiqPay.
    Верифікує підпис та повертає стандартний словник результату.
    """
    cfg = _get_config()
    private_key = cfg.get('private_key', '')

    data_b64  = request.form.get('data', '')
    signature = request.form.get('signature', '')

    if not data_b64:
        raise ValueError('LiqPay callback: відсутній параметр data')

    # Перевірка підпису
    if private_key:
        expected = _sign(private_key, data_b64)
        if expected != signature:
            raise ValueError('LiqPay callback: невірний підпис')

    try:
        data = json.loads(base64.b64decode(data_b64).decode())
    except Exception:
        raise ValueError('LiqPay callback: не вдалося декодувати data')

    liqpay_status = data.get('status', '')
    # success, sandbox — успішно; failure, error, reversed — невдало
    ok_statuses = ('success', 'sandbox', '3ds_verify', 'otp_verify')
    status = 'success' if liqpay_status in ok_statuses else 'failed'

    return {
        'external_id': data.get('order_id', ''),
        'status'     : status,
        'amount'     : float(data.get('amount', 0)),
        'payment_id' : str(data.get('payment_id', '')),
        'currency'   : data.get('currency', ''),
    }
