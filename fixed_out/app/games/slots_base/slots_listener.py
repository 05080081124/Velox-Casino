"""
app/games/slots_base/slots_listener.py — Базовая логика для слотов с поддержкой Free Spins.

Этот модуль импортируется конкретными слотами (slots_777, и др.)
Каждый слот вызывает spin() из своего listener.py, передавая game_id и RNG-функцию.

Флоу одного спина:
  1. Клиент запрашивает GET /api/freespin/status?game_id=<id>
     → {mode, fs_remaining, fs_bet}
  2. Клиент делает POST /api/slots/<game_id>/spin  {bet_amount?, use_fs?}
  3. Этот модуль:
     a. Если use_fs=True и fs_remaining > 0 → consume_spin() (списать FS)
     b. Иначе → WalletService.place_bet() (снять с баланса)
     c. Вызывает rng_func(bet) → {win_amount, symbols, ...}
     d. Если fs → FreeSpinService.pay_fs_win()
        Иначе  → WalletService.pay_win()
     e. Возвращает результат + новый баланс + fs_remaining

Формат возврата:
  {
    ok: true,
    mode: 'freespin' | 'balance',
    bet_amount: float,
    win_amount: float,
    balance: float,
    fs_remaining: int,
    symbols: [...],   # зависит от слота
    meta: {...}       # доп. данные от RNG
  }
"""
from __future__ import annotations
from typing import Callable, Optional
from app.models.user import User
from app.services.wallet_service import WalletService, InsufficientFundsError
from app.services.freespin_service import (
    FreeSpinService, get_fs_bet, game_supports_fs, check_user_fs
)
from app.models.game import GameRound
from app.models.rng_rtp_system import velox_engine


def spin(
    user: User,
    game_id: str,
    game_config: dict,
    rng_func: Callable[[float], dict],
    bet_amount: Optional[float] = None,
    force_balance: bool = False,
) -> dict:
    """
    Универсальный обработчик одного спина слота.

    Args:
        user:         объект User
        game_id:      ID игры (например 'slots_777')
        game_config:  конфиг игры (из config.json)
        rng_func:     функция(bet_amount) → {win_amount, symbols, ...}
                      win_amount = 0 означает проигрыш
        bet_amount:   ставка (если None и режим balance → берётся min_bet)
        force_balance: принудительно использовать баланс (игнорировать FS)

    Returns:
        dict с результатом спина
    """
    supports_fs = game_supports_fs(game_config)
    fs_count    = check_user_fs(user.id) if supports_fs and not force_balance else 0
    use_fs      = supports_fs and fs_count > 0 and not force_balance

    # ── Определяем ставку ─────────────────────────────────────────────────────
    if use_fs:
        # Фиксированная ставка из конфига FS
        actual_bet = get_fs_bet(user.currency)
    else:
        # Обычная ставка — либо переданная, либо min_bet
        bet_cfg = game_config.get('bet', {})
        min_bet = float(
            bet_cfg.get('currency_min', {}).get(user.currency, bet_cfg.get('min', 1))
        )
        max_bet = float(bet_cfg.get('max', 50000))

        if bet_amount is None:
            actual_bet = min_bet
        else:
            actual_bet = float(bet_amount)

        if actual_bet < min_bet:
            return {'error': 'e_min', 'min': min_bet}
        if actual_bet > max_bet:
            return {'error': 'e_max', 'max': max_bet}

    # ── Списываем FS или деньги ───────────────────────────────────────────────
    fs_result = None
    if use_fs:
        fs_result = FreeSpinService.consume_spin(user.id, game_id, user.currency)
        if not fs_result.used:
            # Гонка: FS уже кончились между проверкой и consume → fallback на баланс
            use_fs     = False
            actual_bet = bet_amount or get_fs_bet(user.currency)
            try:
                WalletService.place_bet(user.id, actual_bet, game_id)
            except InsufficientFundsError:
                return {'error': 'e_funds'}
            except Exception as e:
                return {'error': str(e)}
        actual_bet = fs_result.bet_amount
    else:
        try:
            WalletService.place_bet(user.id, actual_bet, game_id)
        except InsufficientFundsError:
            return {'error': 'e_funds'}
        except Exception as e:
            return {'error': str(e)}

    # ── Запускаем RNG ─────────────────────────────────────────────────────────
    try:
        rng_result = rng_func(actual_bet)
    except Exception as e:
        # RNG упал — возвращаем деньги / FS обратно
        if use_fs and fs_result and fs_result.used:
            # Восстанавливаем FS
            from app.models.user import User as _User
            u = _User.get_by_id(user.id)
            if u:
                u.add_free_spins(1)
        else:
            WalletService.cancel_bet(user.id, actual_bet, game_id)
        return {'error': f'RNG error: {e}'}

    win_amount = float(rng_result.get('win_amount', 0))

    # ── Начисляем выигрыш ─────────────────────────────────────────────────────
    if use_fs:
        new_balance = FreeSpinService.pay_fs_win(
            user_id=user.id,
            game_id=game_id,
            win_amount=win_amount,
            bet_amount=actual_bet,
            meta=rng_result.get('meta'),
        )
        fs_remaining = fs_result.remaining_fs if fs_result else 0
    else:
        if win_amount > 0:
            new_balance = WalletService.pay_win(
                user.id, win_amount, game_id,
                meta=rng_result.get('meta')
            )
        else:
            new_balance = WalletService.get_balance(user.id)
        fs_remaining = check_user_fs(user.id)

    # ── Записываем раунд в историю ────────────────────────────────────────────
    try:
        GameRound.record(
            user.id, game_id,
            actual_bet,
            multiplier=round(win_amount / actual_bet, 4) if actual_bet > 0 and win_amount > 0 else 0.0,
            win=win_amount,
            meta={
                'currency': user.currency,
                'mode': 'freespin' if use_fs else 'balance',
                'symbols': rng_result.get('symbols', []),
            }
        )
        velox_engine.close_round(game_id, user.id, actual_bet, win_amount)
    except Exception as e:
        print(f'[slots_listener] record error: {e}')

    # ── Финальный ответ ───────────────────────────────────────────────────────
    mode_now = ('freespin' if (fs_remaining > 0 and supports_fs) else 'balance')

    return {
        'ok':          True,
        'mode':        'freespin' if use_fs else 'balance',
        'mode_next':   mode_now,          # режим для СЛЕДУЮЩЕГО спина
        'bet_amount':  actual_bet,
        'win_amount':  win_amount,
        'balance':     new_balance,
        'fs_remaining': fs_remaining,
        'symbols':     rng_result.get('symbols', []),
        'win_lines':   rng_result.get('win_lines', []),
        'multiplier':  rng_result.get('multiplier', 0),
        'meta':        rng_result.get('meta', {}),
    }
