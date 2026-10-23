"""
app/games/roulette/listener.py — Roulette game loop.

Жизненный цикл раунда:
  waiting   → ждём хотя бы одну ставку
  betting   → открыты ставки, обратный отсчёт 10с
  spinning  → колесо крутится (анимация на клиенте), тикаем прогресс
  result    → показываем результат 4с, выплачиваем
  → repeat

Типы ставок:
  number  — конкретное число (3 числа за раунд), ×5
  sector  — один из трёх секторов (1-12/13-24/25-36), ×2.5
  color   — red/black, ×1.7
  evenodd — even/odd, ×1.4
  dozen   — 1-12/13-24/25-36 (аналог sector, тот же расчёт), ×1.2
  zero    — 0 или 00, ×10-20 (RNG выбирает)

RNG-интеграция:
  - new_round()         → свежий RNGCore + seed_hash
  - get_order()         → приказ (should_win, mult_cap, house_edge …)
  - roulette_spin()     → число 0-36 (+ специальная обработка 00)
  - close_round()       → фиксируем bet/win в BankGuard + PlayerProfile
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any, Dict, List, Optional

from app.models.rng_rtp_system import velox_engine
from app.models.game import GameRound
from app.models.user import User
from app.services.wallet_service import WalletService

# ── config ────────────────────────────────────────────────────────────────────
_DIR = os.path.dirname(__file__)


def _cfg() -> dict:
    try:
        with open(os.path.join(_DIR, 'config.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  КОНСТАНТЫ РУЛЕТКИ
# ══════════════════════════════════════════════════════════════════════════════

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

# Стандартный порядок чисел на колесе (европейская + 00)
WHEEL_ORDER = [0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36,
               11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9,
               22, 18, 29, 7, 28, 12, 35, 3, 26]

# Специальный слот для 00 (double zero) — вставляем между 0 и 32
WHEEL_WITH_00 = [0, '00', 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36,
                 11, 30, 8, 23, 10, 5, 24, 16, 33, 1, 20, 14, 31, 9,
                 22, 18, 29, 7, 28, 12, 35, 3, 26]


def _num_color(n) -> str:
    """Возвращает 'red', 'black', или 'green' (0/00)."""
    if n == 0 or n == '00':
        return 'green'
    return 'red' if int(n) in RED_NUMBERS else 'black'


def _sector(n) -> Optional[int]:
    """1=1-12, 2=13-24, 3=25-36, None для 0/00."""
    if n == 0 or n == '00':
        return None
    n = int(n)
    if 1 <= n <= 12:
        return 1
    if 13 <= n <= 24:
        return 2
    if 25 <= n <= 36:
        return 3
    return None


def _check_bet_wins(bet: dict, result_num, rng_core, cfg: dict) -> float:
    """
    Проверяет попадание ставки. Возвращает множитель выигрыша (0 = проигрыш).

    bet = {
      'type': 'number'|'sector'|'color'|'evenodd'|'dozen'|'zero',
      'value': ...,   # число/сектор/цвет/even-odd/dozen/zero
      'amount': float
    }
    result_num: int или '00'
    """
    payouts = cfg.get('payouts', {})
    btype = bet.get('type')
    bval  = bet.get('value')

    if btype == 'zero':
        # Ставка на 0 или 00
        if str(result_num) == str(bval):
            # RNG выбирает множитель 10-20
            lo, hi = payouts.get('zero', [10, 20])
            mult = rng_core.float_range(float(lo), float(hi))
            return round(mult, 2)
        return 0.0

    if btype == 'number':
        # Ставка на конкретное число (1-36)
        if str(result_num) == str(bval):
            return float(payouts.get('number', 5.0))
        return 0.0

    if btype == 'sector':
        # Ставка на сектор (1/2/3)
        if _sector(result_num) == int(bval):
            return float(payouts.get('sector', 2.5))
        return 0.0

    if btype == 'color':
        # Ставка на цвет (red/black)
        if _num_color(result_num) == bval:
            return float(payouts.get('color', 1.7))
        return 0.0

    if btype == 'evenodd':
        if result_num == 0 or result_num == '00':
            return 0.0
        is_even = int(result_num) % 2 == 0
        if (bval == 'even' and is_even) or (bval == 'odd' and not is_even):
            return float(payouts.get('evenodd', 1.4))
        return 0.0

    if btype == 'dozen':
        # Дюжина: 1-12/13-24/25-36
        sec = _sector(result_num)
        if sec is not None and str(sec) == str(bval):
            return float(payouts.get('dozen', 1.2))
        return 0.0

    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
#  РАЗДЕЛЯЕМОЕ СОСТОЯНИЕ
# ══════════════════════════════════════════════════════════════════════════════

_state: Dict[str, Any] = {
    'status'      : 'waiting',   # waiting | betting | spinning | result
    'countdown'   : 10,
    'round_id'    : 0,
    'seed_hash'   : '',
    'result_num'  : None,        # итоговое число раунда
    'result_color': None,
    'result_sector': None,
    'players'     : {},          # uid → player_dict
    'history'     : [],          # [{num, color, sector}] last 20
}
_lock = threading.RLock()


# ══════════════════════════════════════════════════════════════════════════════
#  SSE
# ══════════════════════════════════════════════════════════════════════════════

class _Q:
    def __init__(self):
        self._q: list = []
        self._ev = threading.Event()

    def put(self, m: str):
        self._q.append(m)
        self._ev.set()

    def get(self, timeout=25) -> list:
        self._ev.wait(timeout)
        self._ev.clear()
        items = self._q[:]
        self._q.clear()
        return items


_subs: List[_Q] = []
_sub_lock = threading.Lock()


def subscribe() -> _Q:
    q = _Q()
    with _sub_lock:
        _subs.append(q)
    return q


def unsubscribe(q: _Q):
    with _sub_lock:
        try:
            _subs.remove(q)
        except ValueError:
            pass


def _broadcast(event: str, data: dict):
    msg = f"event:{event}\ndata:{json.dumps(data, ensure_ascii=False)}\n\n"
    with _sub_lock:
        dead = []
        for q in _subs:
            try:
                q.put(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            try:
                _subs.remove(q)
            except ValueError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ ГЕТТЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

def get_state() -> dict:
    with _lock:
        return {
            'status'       : _state['status'],
            'countdown'    : _state['countdown'],
            'round_id'     : _state['round_id'],
            'seed_hash'    : _state['seed_hash'],
            'result_num'   : _state['result_num'],
            'result_color' : _state['result_color'],
            'result_sector': _state['result_sector'],
            'history'      : list(_state['history'][:20]),
            'players'      : _pub_players(),
        }


def _pub_players() -> list:
    return [
        {
            'uid'     : uid,
            'username': p['username'],
            'icon_url': p['icon_url'],
            'bets'    : p['bets'],      # список ставок [{type,value,amount}]
            'total_bet': p['total_bet'],
            'win'     : p['win'],
            'result'  : p['result'],    # None | 'win' | 'loss'
        }
        for uid, p in _state['players'].items()
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ ДЕЙСТВИЯ
# ══════════════════════════════════════════════════════════════════════════════

def place_bet(user: User, bets: list) -> dict:
    """
    Принять ставки игрока.
    bets = [{type, value, amount}, ...]
    Возвращает {'ok':True, 'balance', 'total_bet'} или {'error': key}
    """
    c = _cfg()
    uid = str(user.id)

    with _lock:
        if _state['status'] not in ('waiting', 'betting'):
            return {'error': 'e_time'}

    # Валидация ставок
    bet_cfg = c.get('bet', {})
    min_bet = float(bet_cfg.get('currency_min', {}).get(
        user.currency, bet_cfg.get('min', 10)))
    max_bet = float(bet_cfg.get('max', 50000))

    total = sum(float(b.get('amount', 0)) for b in bets)
    if total < min_bet:
        return {'error': 'e_min', 'min': min_bet}
    if total > max_bet:
        return {'error': 'e_max', 'max': max_bet}

    # Списываем деньги
    try:
        new_balance = WalletService.place_bet(user.id, total, 'roulette')
    except Exception as e:
        msg = str(e).lower()
        return {'error': 'e_funds' if 'funds' in msg or 'balance' in msg else str(e)}

    with _lock:
        if uid in _state['players']:
            # Добавляем к существующим ставкам
            _state['players'][uid]['bets'].extend(bets)
            _state['players'][uid]['total_bet'] += total
        else:
            _state['players'][uid] = {
                'username' : user.username,
                'icon_url' : user.icon_url or '',
                'bets'     : list(bets),
                'total_bet': total,
                'win'      : 0.0,
                'result'   : None,
                'currency' : user.currency,
            }

    _broadcast('players', {'players': _pub_players()})
    return {'ok': True, 'balance': new_balance, 'total_bet': total}


def cancel_bet(user: User) -> dict:
    """
    Отменить ставку игрока и вернуть деньги на баланс.
    Возможно только в статусе waiting/betting (до начала вращения).
    """
    uid = str(user.id)

    with _lock:
        if _state['status'] not in ('waiting', 'betting'):
            return {'error': 'e_time'}

        player = _state['players'].get(uid)
        if not player:
            return {'error': 'e_no_bet'}

        total_bet = player['total_bet']
        # Удаляем игрока из раунда
        del _state['players'][uid]

    # Возвращаем деньги
    try:
        new_balance = WalletService.cancel_bet(user.id, total_bet, 'roulette')
    except Exception as e:
        # Если cancel_bet не реализован, делаем через pay_win
        try:
            new_balance = WalletService.pay_win(user.id, total_bet, 'roulette',
                                                meta={'reason': 'cancel'})
        except Exception as e2:
            # Восстанавливаем игрока обратно если возврат не удался
            with _lock:
                _state['players'][uid] = player
            return {'error': str(e2)}

    _broadcast('players', {'players': _pub_players()})
    return {'ok': True, 'balance': new_balance, 'refunded': total_bet}


# ══════════════════════════════════════════════════════════════════════════════
#  ИГРОВОЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════════════════════

def _loop():
    while True:
        try:
            _run_round()
        except Exception as e:
            print(f'[roulette.listener] error: {e}')
            time.sleep(3)


def _run_round():
    c        = _cfg()
    online   = c.get('online', {})
    bet_time = int(online.get('betting_time', 10))
    spin_sec = int(online.get('spin_time', 6))
    res_sec  = int(online.get('result_time', 4))

    # ── WAITING ──────────────────────────────────────────────────────────────
    with _lock:
        _state.update(
            status='waiting', players={},
            countdown=bet_time, seed_hash='',
            result_num=None, result_color=None, result_sector=None,
        )
        _state['round_id'] += 1
        rid = _state['round_id']

    _broadcast('state', {
        'status': 'waiting', 'countdown': bet_time,
        'players': [], 'round_id': rid,
        'result_num': None, 'result_color': None,
    })

    # Ждём первую ставку
    while True:
        time.sleep(0.15)
        with _lock:
            if _state['players']:
                break

    # ── BETTING — обратный отсчёт ─────────────────────────────────────────────
    with _lock:
        _state['status'] = 'betting'
        _state['countdown'] = bet_time

    _broadcast('state', {
        'status': 'betting', 'countdown': bet_time, 'round_id': rid,
    })

    for i in range(bet_time, 0, -1):
        with _lock:
            _state['countdown'] = i
        _broadcast('countdown', {'n': i})
        time.sleep(1)

    # ── RNG — определяем результат ────────────────────────────────────────────
    with _lock:
        uids     = [int(k) for k in _state['players']]
        main_uid = uids[0] if uids else 0

    base_wp  = float(c.get('win_probability', 0.486))
    max_mult = 20.0

    rng_core, seed_hash = velox_engine.new_round('roulette', main_uid)

    order = velox_engine.get_order(
        game_id       ='roulette',
        user_id       = main_uid,
        rng           = rng_core,
        base_win_prob = base_wp,
        max_mult      = max_mult,
        multiplayer   = False,
    )

    # Генерируем выпавшее число через roulette_spin (0-36)
    # Отдельно обрабатываем 00: если should_win=False → шанс на 0/00 выше
    raw_num = velox_engine.roulette_spin(rng_core)  # 0-36

    # Добавляем шанс выпадения 00 (double zero)
    # should_win=False → чуть выше шанс зеро (невыгодно для большинства ставок)
    if not order['should_win'] and rng_core.float01() < 0.06:
        result_num = '00'
    elif raw_num == 0 and rng_core.float01() < 0.5:
        result_num = '00'  # случайно: 0 или 00
    else:
        result_num = raw_num

    result_color  = _num_color(result_num)
    result_sector = _sector(result_num)

    with _lock:
        _state.update(
            status='spinning', seed_hash=seed_hash,
            result_num=result_num, result_color=result_color,
            result_sector=result_sector,
        )

    _broadcast('state', {
        'status': 'spinning', 'seed_hash': seed_hash,
        'round_id': rid,
        # Результат скрыт во время вращения — клиент покажет после spin_sec
    })

    # ── SPINNING — ждём анимацию колеса ──────────────────────────────────────
    for i in range(spin_sec * 10):
        _broadcast('spin_progress', {'progress': i / (spin_sec * 10)})
        time.sleep(0.1)

    # ── RESULT ────────────────────────────────────────────────────────────────
    with _lock:
        _state['status'] = 'result'
        snap = dict(_state['players'])

    _broadcast('result', {
        'status'        : 'result',
        'result_num'    : result_num,
        'result_color'  : result_color,
        'result_sector' : result_sector,
        'round_id'      : rid,
        'seed_hash'     : seed_hash,
    })

    # Выплачиваем выигрыши
    for uid_str, p in snap.items():
        uid_int   = int(uid_str)
        total_bet = p['total_bet']
        total_win = 0.0

        for bet in p['bets']:
            mult = _check_bet_wins(bet, result_num, rng_core, c)
            if mult > 0:
                total_win += round(bet['amount'] * mult, 2)

        with _lock:
            if uid_str in _state['players']:
                _state['players'][uid_str]['win']    = total_win
                _state['players'][uid_str]['result'] = 'win' if total_win > 0 else 'loss'

        if total_win > 0:
            try:
                WalletService.pay_win(uid_int, total_win, 'roulette',
                                      meta={'result': str(result_num)})
            except Exception:
                pass
            try:
                GameRound.record(uid_int, 'roulette', total_bet,
                                 multiplier=round(total_win / total_bet, 4),
                                 win=total_win,
                                 meta={'currency': p.get('currency', 'UAH'),
                                       'result': str(result_num)})
            except Exception:
                pass
            velox_engine.close_round('roulette', uid_int, total_bet, total_win)
        else:
            try:
                GameRound.record(uid_int, 'roulette', total_bet,
                                 multiplier=0, win=0,
                                 meta={'currency': p.get('currency', 'UAH')})
            except Exception:
                pass
            velox_engine.close_round('roulette', uid_int, total_bet, 0)

    # Обновляем историю
    with _lock:
        _state['history'].insert(0, {
            'num'   : result_num,
            'color' : result_color,
            'sector': result_sector,
        })
        _state['history'] = _state['history'][:20]

    _broadcast('players', {'players': _pub_players()})
    _broadcast('history', {'history': list(_state['history'][:20])})

    time.sleep(res_sec)


# ══════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

_started    = False
_start_lock = threading.Lock()


def start():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True,
                     name='roulette-listener').start()
