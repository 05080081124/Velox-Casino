"""
app/services/freespin_service.py — Сервис управления Free Spins.

Механика:
  - Слоты с тегом 'fs' в config.json поддерживают фриспины.
  - Ставка фиксирована (берётся из config/freespin_bets.json по валюте юзера).
  - При спине: списывается 1 FS, ставка НЕ снимается с баланса.
  - Выигрыш начисляется на РЕАЛЬНЫЙ баланс.
  - Когда FS = 0 — игра переключается на обычный баланс.
  - Каждый спин логируется как транзакция типа 'fs_bet' / 'fs_win'.
"""
from __future__ import annotations
import json, os
from typing import Optional
from app.db import get_db

_FS_BETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'freespin_bets.json')


def _load_fs_bets() -> dict:
    """Загружает конфиг фиксированных ставок FS."""
    try:
        with open(_FS_BETS_PATH, encoding='utf-8') as f:
            return json.load(f).get('bets', {})
    except Exception:
        return {}


def get_fs_bet(currency: str) -> float:
    """Возвращает фиксированную ставку FS для валюты (по умолчанию UAH=1.0)."""
    bets = _load_fs_bets()
    return float(bets.get(currency.upper(), bets.get('UAH', 1.0)))


def game_supports_fs(game_config: dict) -> bool:
    """Проверяет, поддерживает ли игра фриспины (тег 'slots' + тег 'fs' в config)."""
    tags = game_config.get('tags', [])
    return 'slots' in tags and 'fs' in tags


def check_user_fs(user_id: int) -> int:
    """Возвращает количество оставшихся FS у пользователя."""
    with get_db() as db:
        db.execute('SELECT free_spins FROM users WHERE id = %s', (user_id,))
        row = db.fetchone()
    return int((row or {}).get('free_spins', 0))


class FreeSpinResult:
    """Результат применения одного фриспина."""
    def __init__(self, used: bool, bet_amount: float, remaining_fs: int,
                 mode: str, error: Optional[str] = None):
        self.used = used            # True — FS использован
        self.bet_amount = bet_amount  # Размер ставки (фиксированный)
        self.remaining_fs = remaining_fs  # Сколько FS осталось ПОСЛЕ
        self.mode = mode            # 'freespin' | 'balance'
        self.error = error

    def to_dict(self) -> dict:
        return {
            'used_freespin': self.used,
            'bet_amount': self.bet_amount,
            'remaining_fs': self.remaining_fs,
            'mode': self.mode,
            'error': self.error,
        }


class FreeSpinService:

    @staticmethod
    def get_game_mode(user_id: int, game_config: dict, currency: str) -> dict:
        """
        Определяет режим игры для слота при входе:
          - 'freespin': у юзера есть FS и игра поддерживает FS
          - 'balance': обычный баланс
        Возвращает словарь с режимом, ставкой и кол-вом FS.
        """
        supports = game_supports_fs(game_config)
        fs_count = check_user_fs(user_id) if supports else 0

        if supports and fs_count > 0:
            return {
                'mode': 'freespin',
                'fs_remaining': fs_count,
                'fs_bet': get_fs_bet(currency),
                'currency': currency,
                'supports_fs': True,
            }
        return {
            'mode': 'balance',
            'fs_remaining': fs_count,
            'fs_bet': get_fs_bet(currency),
            'currency': currency,
            'supports_fs': supports,
        }

    @staticmethod
    def consume_spin(user_id: int, game_id: str, currency: str) -> FreeSpinResult:
        """
        Выполняет один фриспин:
          1. Проверяет что у юзера есть FS
          2. Определяет фиксированную ставку по валюте
          3. Списывает 1 FS (НЕ снимает деньги с баланса)
          4. Логирует транзакцию типа 'fs_bet'
          5. Возвращает результат

        Если FS нет — возвращает mode='balance', используй обычный place_bet.
        """
        fs_count = check_user_fs(user_id)

        if fs_count <= 0:
            return FreeSpinResult(
                used=False,
                bet_amount=get_fs_bet(currency),
                remaining_fs=0,
                mode='balance',
            )

        bet_amount = get_fs_bet(currency)

        with get_db() as db:
            # Атомарно уменьшаем free_spins
            db.execute(
                'UPDATE users SET free_spins = free_spins - 1 WHERE id = %s AND free_spins > 0',
                (user_id,)
            )
            db.execute('SELECT free_spins FROM users WHERE id = %s', (user_id,))
            row = db.fetchone()
            remaining = int((row or {}).get('free_spins', 0))

            # Логируем как транзакцию
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'fs_bet', %s, 'completed', %s, NOW())",
                (user_id, bet_amount, json.dumps({
                    'game_id': game_id,
                    'fs_remaining_after': remaining,
                    'source': 'freespin',
                }))
            )

        return FreeSpinResult(
            used=True,
            bet_amount=bet_amount,
            remaining_fs=remaining,
            mode='freespin' if remaining > 0 else 'balance',
        )

    @staticmethod
    def pay_fs_win(user_id: int, game_id: str, win_amount: float,
                   bet_amount: float, meta: Optional[dict] = None) -> float:
        """
        Начисляет выигрыш от фриспина на РЕАЛЬНЫЙ баланс.
        Возвращает новый баланс.
        """
        if win_amount <= 0:
            # Проигрыш — просто возвращаем баланс
            with get_db() as db:
                db.execute('SELECT balance FROM users WHERE id = %s', (user_id,))
                row = db.fetchone()
            return round(float((row or {}).get('balance', 0)), 2)

        with get_db() as db:
            db.execute(
                'UPDATE users SET balance = balance + %s WHERE id = %s',
                (win_amount, user_id)
            )
            db.execute('SELECT balance FROM users WHERE id = %s', (user_id,))
            row = db.fetchone()
            new_balance = float((row or {}).get('balance', 0))

            payload = {
                'game_id': game_id,
                'source': 'freespin',
                'fs_bet': bet_amount,
                **(meta or {}),
            }
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'fs_win', %s, 'completed', %s, NOW())",
                (user_id, win_amount, json.dumps(payload))
            )

        return round(new_balance, 2)

    @staticmethod
    def get_fs_history(user_id: int, limit: int = 20) -> list:
        """Возвращает историю фриспинов (ставки + выигрыши)."""
        with get_db() as db:
            db.execute(
                "SELECT * FROM transactions"
                " WHERE user_id = %s AND type IN ('fs_bet', 'fs_win')"
                " ORDER BY created_at DESC LIMIT %s",
                (user_id, limit)
            )
            rows = db.fetchall()
        result = []
        for r in rows:
            r = dict(r)
            try:
                r['meta'] = json.loads(r.get('meta') or '{}')
            except Exception:
                r['meta'] = {}
            result.append(r)
        return result

    @staticmethod
    def check_user_fs(user_id: int) -> int:
        """Алиас для удобства — возвращает кол-во FS у пользователя."""
        return check_user_fs(user_id)

    @staticmethod
    def get_fs_stats(user_id: int) -> dict:
        """Статистика фриспинов: сколько было использовано, суммарный выигрыш."""
        with get_db() as db:
            db.execute(
                "SELECT COUNT(*) as spins_used, COALESCE(SUM(amount),0) as total_bet"
                " FROM transactions WHERE user_id = %s AND type = 'fs_bet'",
                (user_id,)
            )
            bet_row = db.fetchone() or {}

            db.execute(
                "SELECT COALESCE(SUM(amount),0) as total_win"
                " FROM transactions WHERE user_id = %s AND type = 'fs_win'",
                (user_id,)
            )
            win_row = db.fetchone() or {}

        spins_used = int(bet_row.get('spins_used', 0))
        total_win  = round(float(win_row.get('total_win', 0)), 2)
        total_bet  = round(float(bet_row.get('total_bet', 0)), 2)

        return {
            'spins_used': spins_used,
            'total_win':  total_win,
            'total_bet':  total_bet,
            'net_profit': round(total_win - total_bet, 2),
            'fs_remaining': check_user_fs(user_id),
        }


# Глобальный экземпляр
freespin_service = FreeSpinService()
