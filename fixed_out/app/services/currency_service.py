"""
app/services/currency_service.py — Конвертация валют для VELOX Casino.

Курсы хранятся в config/currency_rates.json и обновляются с НБУ API.
Fallback — захардкоженные курсы (обновляй раз в неделю вручную).

Авто-обновление через cron (добавь в crontab):
  */30 * * * * cd /path/to/velox && python -c \
    "from app.services.currency_service import update_rates; update_rates()"
"""
from __future__ import annotations
import json, os, time, urllib.request
from typing import Optional

_RATES_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'config', 'currency_rates.json'
)

# Fallback курсы (UAH как базовая валюта)
# Обновляй этот словарь вручную если API недоступен
_FALLBACK: dict[str, float] = {
    'UAH':  1.0,
    'USD':  41.5,
    'EUR':  45.2,
    'RUB':  0.46,
    'USDT': 41.5,
}

_SUPPORTED = set(_FALLBACK.keys())


# ── Загрузка курсов ───────────────────────────────────────────────────────────

def _load_rates() -> dict[str, float]:
    """
    Читает курсы из файла. Если файл старше 24ч или не существует — возвращает fallback.
    """
    try:
        with open(_RATES_PATH, encoding='utf-8') as f:
            data = json.load(f)
        age = time.time() - float(data.get('updated_at', 0))
        if age < 86_400:                        # свежее 24 часов
            rates = data.get('rates_to_uah', {})
            if rates:
                return {k.upper(): float(v) for k, v in rates.items()}
    except Exception:
        pass
    return dict(_FALLBACK)


# ── Публичное API ─────────────────────────────────────────────────────────────

def convert(amount: float, from_cur: str, to_cur: str) -> float:
    """
    Конвертирует amount из from_cur в to_cur через UAH.

    Пример:
        convert(10.0, 'USD', 'UAH') → 415.0
        convert(100.0, 'UAH', 'USD') → 2.41
        convert(10.0, 'USD', 'EUR')  → 9.18  (USD→UAH→EUR)
    """
    from_cur = from_cur.upper()
    to_cur   = to_cur.upper()
    if from_cur == to_cur:
        return round(amount, 2)
    rates = _load_rates()
    rate_from = rates.get(from_cur, 1.0)
    rate_to   = rates.get(to_cur,   1.0)
    if rate_to == 0:
        return 0.0
    in_uah = amount * rate_from          # → UAH
    result = in_uah / rate_to            # → target currency
    return round(result, 4)


def to_uah(amount: float, currency: str) -> float:
    """Конвертирует amount из currency в UAH."""
    rates = _load_rates()
    return round(amount * rates.get(currency.upper(), 1.0), 2)


def from_uah(amount_uah: float, currency: str) -> float:
    """Конвертирует amount из UAH в currency."""
    rates = _load_rates()
    rate  = rates.get(currency.upper(), 1.0)
    if rate == 0:
        return 0.0
    return round(amount_uah / rate, 4)


def get_rates() -> dict[str, float]:
    """Возвращает текущие курсы к UAH."""
    return _load_rates()


def get_rates_info() -> dict:
    """Возвращает курсы + метаданные (время обновления, источник)."""
    try:
        with open(_RATES_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return {
            'rates':      data.get('rates_to_uah', _FALLBACK),
            'updated_at': data.get('updated_at', 0),
            'source':     data.get('source', 'fallback'),
            'fresh':      (time.time() - float(data.get('updated_at', 0))) < 86_400,
        }
    except Exception:
        return {'rates': _FALLBACK, 'updated_at': 0, 'source': 'fallback', 'fresh': False}


# ── Авто-обновление курсов с НБУ ─────────────────────────────────────────────

def update_rates() -> bool:
    """
    Загружает актуальные курсы с API Национального банка Украины.
    Сохраняет в config/currency_rates.json.

    Returns:
        True если успешно, False при ошибке (fallback-курсы остаются рабочими).
    """
    try:
        url = 'https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?json'
        req = urllib.request.Request(url, headers={'User-Agent': 'VELOX-Casino/3.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            nbu_data = json.loads(r.read().decode())

        rates: dict[str, float] = {'UAH': 1.0}

        # Ищем нужные валюты в ответе НБУ
        _target = {'USD', 'EUR', 'RUB'}
        for item in nbu_data:
            cc = item.get('cc', '').upper()
            rate = float(item.get('rate', 0))
            if cc in _target and rate > 0:
                rates[cc] = rate

        # USDT ≈ USD (стейблкоин)
        rates['USDT'] = rates.get('USD', _FALLBACK['USDT'])

        # Убедимся что все нужные валюты присутствуют
        for cur, fallback_rate in _FALLBACK.items():
            if cur not in rates:
                rates[cur] = fallback_rate

        # Сохраняем
        os.makedirs(os.path.dirname(_RATES_PATH), exist_ok=True)
        with open(_RATES_PATH, 'w', encoding='utf-8') as f:
            json.dump({
                'rates_to_uah': rates,
                'updated_at':   time.time(),
                'source':       'nbu',
            }, f, indent=2, ensure_ascii=False)

        print(f'[currency] Курсы обновлены: USD={rates.get("USD")}, EUR={rates.get("EUR")}')
        return True

    except Exception as e:
        print(f'[currency] Ошибка обновления курсов: {e}. Используются fallback-значения.')
        return False


# ── Форматирование для отображения ────────────────────────────────────────────

_SYMBOLS = {'UAH': '₴', 'RUB': '₽', 'USD': '$', 'EUR': '€', 'USDT': '₮'}


def format_amount(amount: float, currency: str) -> str:
    """Форматирует сумму с символом валюты."""
    sym = _SYMBOLS.get(currency.upper(), currency)
    if amount == int(amount):
        return f'{sym}{int(amount):,}'
    return f'{sym}{amount:,.2f}'


def min_bet_in_currency(min_uah: float, currency: str) -> float:
    """Конвертирует минимальную ставку из UAH в нужную валюту."""
    return max(0.5, from_uah(min_uah, currency))
