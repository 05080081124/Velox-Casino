"""
app/api/provider_wallet.py — Wallet API для провайдерских игр.

Провайдер (Pragmatic Play, Evolution, BGaming и др.) вызывает эти эндпоинты
когда нужно управлять балансом игрока на нашей стороне.

  POST /api/provider/wallet/balance   — получить текущий баланс игрока
  POST /api/provider/wallet/debit     — списать ставку
  POST /api/provider/wallet/credit    — зачислить выигрыш
  POST /api/provider/wallet/rollback  — отменить транзакцию (тех. сбой)

─── Аутентификация ──────────────────────────────────────────────────────────────
  Заголовок  X-Provider-ID : <provider_id>  (например "pragmatic")
  Заголовок  X-Signature   : HMAC-SHA256(request_body_bytes, provider_secret)

  secret берётся из config/providers.json → providers[id].secret
  Если у провайдера нет подписи — установи "wallet_auth": false в его конфиге.

─── Идемпотентность ─────────────────────────────────────────────────────────────
  Каждый debit/credit имеет transaction_id от провайдера.
  Повторный запрос с тем же transaction_id → возвращаем баланс без двойного списания.

─── Конвертация валют ───────────────────────────────────────────────────────────
  Провайдер может работать в USD, а игрок — в UAH.
  Конвертация происходит автоматически через currency_service.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from typing import Optional

from flask import Blueprint, request, jsonify

from app.db import get_db
from app.models.user import User
from app.services.currency_service import convert
from app.services.wallet_service import (
    WalletService, InsufficientFundsError, WalletError
)

provider_wallet_bp = Blueprint(
    'provider_wallet', __name__, url_prefix='/api/provider/wallet'
)

_PROVIDERS_CFG = os.path.join(
    os.path.dirname(__file__), '..', '..', 'config', 'providers.json'
)


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_provider(provider_id: str) -> Optional[dict]:
    """Читает конфиг провайдера из providers.json. None если не найден / disabled."""
    try:
        with open(_PROVIDERS_CFG, encoding='utf-8') as f:
            cfg = json.load(f)
        for p in cfg.get('providers', []):
            if p.get('id') == provider_id and p.get('enabled', False):
                return p
    except Exception as e:
        print(f'[provider_wallet] providers.json read error: {e}')
    return None


def _verify_signature(provider: dict, body: bytes, sig: str) -> bool:
    """
    Проверяет HMAC-SHA256 подпись запроса.
    secret = providers.json → secret (или api_key как fallback).
    """
    secret = (provider.get('secret') or provider.get('api_key') or '').strip()
    if not secret:
        return False
    expected = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.lower())


def _user_from_token(token: str) -> Optional[User]:
    """
    Находит пользователя по токену который мы передали провайдеру.

    По умолчанию токен = str(user.id) (из _provider_example/provider.py).
    Если generate_token() формирует "user_id:timestamp:sig" — берём первую часть.
    """
    if not token:
        return None
    try:
        uid = int(str(token).split(':')[0])
        return User.get_by_id(uid)
    except (ValueError, TypeError):
        return None


def _is_duplicate_tx(provider_tx_id: str) -> bool:
    """
    Проверяет что транзакция с таким provider_tx_id уже обработана.
    Идемпотентность: повторный запрос не должен дублировать операции.
    """
    from app.db import DB_ENGINE
    p = '%s' if DB_ENGINE == 'postgresql' else '?'
    try:
        with get_db() as db:
            db.execute(
                f"SELECT id FROM transactions WHERE meta LIKE {p} LIMIT 1",
                (f'%"provider_tx_id": "{provider_tx_id}"%',)
            )
            return db.fetchone() is not None
    except Exception:
        return False


def _ok_response(user: User, provider_currency: str, tx_id: str = '') -> dict:
    """Формирует успешный ответ с балансом в валюте провайдера."""
    u = User.get_by_id(user.id)  # свежий баланс после операции
    bal = convert(u.balance, u.currency, provider_currency)
    resp = {
        'status':    'ok',
        'balance':   round(bal, 4),
        'currency':  provider_currency,
        'player_id': str(u.id),
    }
    if tx_id:
        resp['tx_id'] = tx_id
    return resp


# ══════════════════════════════════════════════════════════════════════════════
#  /balance — получить баланс игрока
# ══════════════════════════════════════════════════════════════════════════════

@provider_wallet_bp.route('/balance', methods=['POST'])
def get_balance():
    """
    Провайдер вызывает при открытии игры, чтобы показать баланс.

    Request body (пример Pragmatic Play):
        { "playerId": "42", "currency": "USD", "gameId": "vs20goldrush" }

    Response:
        { "status": "ok", "balance": 25.30, "currency": "USD", "player_id": "42" }
    """
    provider_id = request.headers.get('X-Provider-ID', '').strip()
    signature   = request.headers.get('X-Signature',   '').strip()
    body        = request.get_data()

    provider = _load_provider(provider_id)
    if not provider:
        return jsonify({'error': 'Unknown provider', 'code': 'PROVIDER_NOT_FOUND'}), 403

    if provider.get('wallet_auth', True) and not _verify_signature(provider, body, signature):
        return jsonify({'error': 'Invalid signature', 'code': 'AUTH_FAILED'}), 403

    data  = request.get_json(silent=True) or {}

    # Поддерживаем разные имена полей у разных провайдеров
    token = str(
        data.get('player_token') or data.get('token') or
        data.get('playerId')     or data.get('userId') or ''
    )
    provider_cur = (
        data.get('currency') or provider.get('currency', 'UAH')
    ).upper()

    user = _user_from_token(token)
    if not user:
        return jsonify({'error': 'Player not found', 'code': 'PLAYER_NOT_FOUND'}), 404

    bal = convert(user.balance, user.currency, provider_cur)
    return jsonify({
        'status':    'ok',
        'balance':   round(bal, 4),
        'currency':  provider_cur,
        'player_id': str(user.id),
    })


# ══════════════════════════════════════════════════════════════════════════════
#  /debit — списание ставки
# ══════════════════════════════════════════════════════════════════════════════

@provider_wallet_bp.route('/debit', methods=['POST'])
def debit():
    """
    Провайдер вызывает когда игрок делает ставку.
    Списывает amount из баланса игрока (с конвертацией если валюты отличаются).

    Request body:
        {
            "player_token":    "42",
            "amount":          10.00,
            "currency":        "USD",
            "transaction_id":  "prag_tx_abc123",
            "game_id":         "vs20goldrush",
            "round_id":        "round_789"
        }
    """
    provider_id = request.headers.get('X-Provider-ID', '').strip()
    signature   = request.headers.get('X-Signature',   '').strip()
    body        = request.get_data()

    provider = _load_provider(provider_id)
    if not provider:
        return jsonify({'error': 'Unknown provider', 'code': 'PROVIDER_NOT_FOUND'}), 403
    if provider.get('wallet_auth', True) and not _verify_signature(provider, body, signature):
        return jsonify({'error': 'Invalid signature', 'code': 'AUTH_FAILED'}), 403

    data = request.get_json(silent=True) or {}

    token        = str(data.get('player_token') or data.get('token') or data.get('playerId') or '')
    amount_raw   = float(data.get('amount', 0))
    provider_cur = (data.get('currency') or provider.get('currency', 'UAH')).upper()
    tx_id        = str(data.get('transaction_id') or data.get('txId') or secrets.token_hex(8))
    game_id      = str(data.get('game_id') or data.get('gameId', ''))
    round_id     = str(data.get('round_id') or data.get('roundId', ''))

    if amount_raw <= 0:
        return jsonify({'error': 'Invalid amount', 'code': 'INVALID_AMOUNT'}), 400

    user = _user_from_token(token)
    if not user:
        return jsonify({'error': 'Player not found', 'code': 'PLAYER_NOT_FOUND'}), 404

    # ── Идемпотентность ───────────────────────────────────────────────────────
    if _is_duplicate_tx(tx_id):
        return jsonify(_ok_response(user, provider_cur, f'velox_{tx_id}'))

    # ── Конвертируем из валюты провайдера в валюту игрока ─────────────────────
    amount_user_cur = convert(amount_raw, provider_cur, user.currency)

    # ── Списание через WalletService (он пишет транзакцию сам) ───────────────
    try:
        WalletService.place_bet(
            user_id=user.id,
            amount=amount_user_cur,
            game_id=game_id,
        )
    except InsufficientFundsError:
        return jsonify({'error': 'Insufficient funds', 'code': 'INSUFFICIENT_FUNDS'}), 402
    except WalletError as e:
        return jsonify({'error': str(e), 'code': 'WALLET_ERROR'}), 400

    # ── Обновляем meta транзакции с provider-деталями ─────────────────────────
    # WalletService записал транзакцию, добавляем provider-поля через UPDATE
    try:
        from app.db import DB_ENGINE
        p = '%s' if DB_ENGINE == 'postgresql' else '?'
        with get_db() as db:
            db.execute(
                f"UPDATE transactions SET meta = {p}::jsonb"
                f" WHERE user_id = {p} AND type = 'bet' ORDER BY id DESC LIMIT 1",
                (json.dumps({
                    'provider_id':       provider_id,
                    'provider_tx_id':    tx_id,
                    'provider_amount':   amount_raw,
                    'provider_currency': provider_cur,
                    'game_id':           game_id,
                    'round_id':          round_id,
                }), user.id)
            )
    except Exception as e:
        print(f'[provider_wallet/debit] meta update error: {e}')

    return jsonify(_ok_response(user, provider_cur, f'velox_{tx_id}'))


# ══════════════════════════════════════════════════════════════════════════════
#  /credit — зачисление выигрыша
# ══════════════════════════════════════════════════════════════════════════════

@provider_wallet_bp.route('/credit', methods=['POST'])
def credit():
    """
    Провайдер вызывает когда раунд завершён и игрок выиграл.
    amount = 0 — означает проигрыш без выплаты (тоже валидный кейс).

    Request body:
        {
            "player_token":    "42",
            "amount":          25.00,
            "currency":        "USD",
            "transaction_id":  "prag_tx_abc124",
            "game_id":         "vs20goldrush",
            "round_id":        "round_789"
        }
    """
    provider_id = request.headers.get('X-Provider-ID', '').strip()
    signature   = request.headers.get('X-Signature',   '').strip()
    body        = request.get_data()

    provider = _load_provider(provider_id)
    if not provider:
        return jsonify({'error': 'Unknown provider', 'code': 'PROVIDER_NOT_FOUND'}), 403
    if provider.get('wallet_auth', True) and not _verify_signature(provider, body, signature):
        return jsonify({'error': 'Invalid signature', 'code': 'AUTH_FAILED'}), 403

    data = request.get_json(silent=True) or {}

    token        = str(data.get('player_token') or data.get('token') or data.get('playerId') or '')
    amount_raw   = float(data.get('amount', 0))
    provider_cur = (data.get('currency') or provider.get('currency', 'UAH')).upper()
    tx_id        = str(data.get('transaction_id') or data.get('txId') or secrets.token_hex(8))
    game_id      = str(data.get('game_id') or data.get('gameId', ''))
    round_id     = str(data.get('round_id') or data.get('roundId', ''))

    if amount_raw < 0:
        return jsonify({'error': 'Invalid amount', 'code': 'INVALID_AMOUNT'}), 400

    user = _user_from_token(token)
    if not user:
        return jsonify({'error': 'Player not found', 'code': 'PLAYER_NOT_FOUND'}), 404

    # ── Идемпотентность ───────────────────────────────────────────────────────
    if _is_duplicate_tx(tx_id):
        return jsonify(_ok_response(user, provider_cur, f'velox_{tx_id}'))

    # amount = 0 → проигрыш, баланс не меняется, но транзакцию всё равно пишем
    if amount_raw > 0:
        amount_user_cur = convert(amount_raw, provider_cur, user.currency)
        try:
            WalletService.deposit(
                user_id=user.id,
                amount=amount_user_cur,
                meta={
                    'type':              'win',
                    'provider_id':       provider_id,
                    'provider_tx_id':    tx_id,
                    'provider_amount':   amount_raw,
                    'provider_currency': provider_cur,
                    'game_id':           game_id,
                    'round_id':          round_id,
                }
            )
        except WalletError as e:
            return jsonify({'error': str(e), 'code': 'WALLET_ERROR'}), 400
    else:
        # Проигрыш: пишем нулевую транзакцию для истории
        try:
            from app.db import DB_ENGINE
            p = '%s' if DB_ENGINE == 'postgresql' else '?'
            with get_db() as db:
                db.execute(
                    f"INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                    f" VALUES ({p}, 'lose', 0, 'completed', {p}::jsonb, NOW())",
                    (user.id, json.dumps({
                        'provider_id':       provider_id,
                        'provider_tx_id':    tx_id,
                        'game_id':           game_id,
                        'round_id':          round_id,
                    }))
                )
        except Exception as e:
            print(f'[provider_wallet/credit] lose tx write error: {e}')

    return jsonify(_ok_response(user, provider_cur, f'velox_{tx_id}'))


# ══════════════════════════════════════════════════════════════════════════════
#  /rollback — отмена транзакции
# ══════════════════════════════════════════════════════════════════════════════

@provider_wallet_bp.route('/rollback', methods=['POST'])
def rollback():
    """
    Провайдер вызывает при техническом сбое — раунд не засчитан,
    нужно вернуть ставку игроку.

    Request body:
        {
            "player_token":      "42",
            "original_tx_id":    "prag_tx_abc123",
            "currency":          "USD"
        }
    """
    provider_id = request.headers.get('X-Provider-ID', '').strip()
    signature   = request.headers.get('X-Signature',   '').strip()
    body        = request.get_data()

    provider = _load_provider(provider_id)
    if not provider:
        return jsonify({'error': 'Unknown provider', 'code': 'PROVIDER_NOT_FOUND'}), 403
    if provider.get('wallet_auth', True) and not _verify_signature(provider, body, signature):
        return jsonify({'error': 'Invalid signature', 'code': 'AUTH_FAILED'}), 403

    data         = request.get_json(silent=True) or {}
    token        = str(data.get('player_token') or data.get('token') or data.get('playerId') or '')
    orig_tx_id   = str(data.get('original_tx_id') or data.get('txId') or '')
    provider_cur = (data.get('currency') or provider.get('currency', 'UAH')).upper()
    new_tx_id    = secrets.token_hex(8)

    user = _user_from_token(token)
    if not user:
        return jsonify({'error': 'Player not found', 'code': 'PLAYER_NOT_FOUND'}), 404

    # ── Ищем оригинальную ставку ──────────────────────────────────────────────
    from app.db import DB_ENGINE
    p = '%s' if DB_ENGINE == 'postgresql' else '?'
    try:
        with get_db() as db:
            db.execute(
                f"SELECT id, amount FROM transactions"
                f" WHERE user_id = {p} AND type = 'bet' AND meta LIKE {p} LIMIT 1",
                (user.id, f'%"provider_tx_id": "{orig_tx_id}"%')
            )
            row = db.fetchone()
    except Exception as e:
        print(f'[provider_wallet/rollback] find tx error: {e}')
        row = None

    if not row:
        # Транзакции нет — либо уже отменена, либо никогда не была
        return jsonify({
            **_ok_response(user, provider_cur),
            'message': 'Transaction not found, may already be rolled back',
        })

    # ── Проверяем не было ли уже rollback ─────────────────────────────────────
    if _is_duplicate_tx(f'rollback_{orig_tx_id}'):
        return jsonify(_ok_response(user, provider_cur, f'velox_rb_{orig_tx_id}'))

    refund_amount = abs(float(dict(row).get('amount', 0)))
    if refund_amount <= 0:
        return jsonify(_ok_response(user, provider_cur))

    # ── Возвращаем ставку через WalletService ─────────────────────────────────
    try:
        WalletService.deposit(
            user_id=user.id,
            amount=refund_amount,
            meta={
                'type':            'rollback',
                'provider_id':     provider_id,
                'provider_tx_id':  f'rollback_{orig_tx_id}',
                'original_tx_id':  orig_tx_id,
                'new_tx_id':       new_tx_id,
            }
        )
    except WalletError as e:
        return jsonify({'error': str(e), 'code': 'WALLET_ERROR'}), 400

    return jsonify(_ok_response(user, provider_cur, f'velox_rb_{orig_tx_id}'))


# ══════════════════════════════════════════════════════════════════════════════
#  /rates — текущие курсы валют (для провайдера, если нужно)
# ══════════════════════════════════════════════════════════════════════════════

@provider_wallet_bp.route('/rates', methods=['GET'])
def exchange_rates():
    """Возвращает актуальные курсы валют к UAH."""
    from app.services.currency_service import get_rates_info
    info = get_rates_info()
    return jsonify({
        'status': 'ok',
        'rates':  info['rates'],
        'source': info['source'],
        'fresh':  info['fresh'],
    })
