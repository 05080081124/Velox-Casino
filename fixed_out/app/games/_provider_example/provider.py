"""
app/games/_provider_example/provider.py — Провайдерский модуль.

Скопируй и переименуй под своего провайдера.
Реализуй generate_token() для аутентификации игрока у провайдера.

Этот файл ОПЦИОНАЛЕН — если его нет, {token} в game_url будет пустой строкой.
"""
import os
import json
import time
import hmac
import hashlib


# ── Настройки провайдера (из env или config) ──────────────────────────────────

def _get_config() -> dict:
    """Читает config.json этого провайдера."""
    cfg_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ── Генерация токена ──────────────────────────────────────────────────────────

def generate_token(user, game_info: dict) -> str:
    """
    Генерирует токен для аутентификации пользователя у провайдера.

    Args:
        user:      объект User (user.id, user.username, user.currency, ...)
        game_info: dict с данными игры из config.json

    Returns:
        str — токен (JWT, HMAC, или просто user_id для простых провайдеров)

    Примеры реализации:
    ─────────────────────────────────────────────────────────────────────────

    1. ПРОСТОЙ (user_id как токен):
        return str(user.id)

    2. HMAC подпись (большинство провайдеров):
        secret = os.environ.get('PROVIDER_SECRET', '')
        payload = f"{user.id}:{int(time.time())}:{game_info['id']}"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{sig}"

    3. JWT (если провайдер требует JWT):
        import jwt
        secret = os.environ.get('PROVIDER_JWT_SECRET', '')
        payload = {
            'sub': str(user.id),
            'name': user.username,
            'cur': user.currency,
            'game': game_info['id'],
            'iat': int(time.time()),
            'exp': int(time.time()) + 3600,
        }
        return jwt.encode(payload, secret, algorithm='HS256')

    4. API-запрос к провайдеру (сессионный токен):
        import requests
        r = requests.post(
            'https://provider.com/api/session',
            json={'user_id': user.id, 'currency': user.currency},
            headers={'Authorization': f'Bearer {API_KEY}'},
            timeout=5,
        )
        return r.json()['session_token']
    ─────────────────────────────────────────────────────────────────────────
    """
    # STUB — замени на реальную логику
    return str(user.id)
