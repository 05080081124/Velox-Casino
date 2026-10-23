"""
app/games/crash/listener.py — Crash game loop.

Жизненный цикл раунда:
  waiting    → ждём хотя бы одну ставку (none_multiplayer) или отсчёт
  countdown  → обратный отсчёт перед стартом
  running    → запрашиваем приказ у velox_engine, летим, принимаем cashout
  crashed    → фиксируем проигрыши, пауза 4с

Интеграция с RNG:
  - new_round()  — каждый раунд получает свежий RNGCore + server_seed_hash
  - get_order()  — приказ с should_win, mult_cap, house_edge
  - crash_point()— точка краша с учётом order
  - close_round()— фиксируем bet/win в BankGuard + PlayerProfile

Бонусы:
  - free_spins: если > 0 и ставка <= min_bet — ставка бесплатная
  - bonus_deposit: мультипликатор × bonus_deposit_pct к выигрышу (если > 0)
"""
from __future__ import annotations

import json
import os
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
#  РАЗДЕЛЯЕМОЕ СОСТОЯНИЕ
# ══════════════════════════════════════════════════════════════════════════════

# players: uid_str → { username, icon_url, bet, cashed, mult, result, used_free_spin }
_state: Dict[str, Any] = {
    'status'    : 'waiting',
    'multiplier': 1.0,
    'crash_point': 1.0,
    'countdown' : 5,
    'round_id'  : 0,
    'seed_hash' : '',
    'players'   : {},
    'history'   : [],   # [{multiplier, color}] last 20
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
        self._q.append(m); self._ev.set()
    def get(self, timeout=25) -> list:
        self._ev.wait(timeout); self._ev.clear()
        items = self._q[:]; self._q.clear(); return items

_subs: List[_Q] = []
_sub_lock = threading.Lock()


def subscribe() -> _Q:
    q = _Q()
    with _sub_lock: _subs.append(q)
    return q


def unsubscribe(q: _Q):
    with _sub_lock:
        try: _subs.remove(q)
        except ValueError: pass


def _broadcast(event: str, data: dict):
    msg = f"event:{event}\ndata:{json.dumps(data, ensure_ascii=False)}\n\n"
    with _sub_lock:
        dead = []
        for q in _subs:
            try: q.put(msg)
            except Exception: dead.append(q)
        for q in dead:
            try: _subs.remove(q)
            except ValueError: pass

# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ ГЕТТЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

def get_state() -> dict:
    with _lock:
        return {
            'status'    : _state['status'],
            'multiplier': _state['multiplier'],
            'countdown' : _state['countdown'],
            'round_id'  : _state['round_id'],
            'seed_hash' : _state['seed_hash'],
            'history'   : list(_state['history'][:10]),
            'players'   : _pub_players(),
        }


def _pub_players() -> list:
    """Публичное представление игроков (без внутренних флагов)."""
    return [
        {
            'uid'     : uid,
            'username': p['username'],
            'icon_url': p['icon_url'],
            'bet'     : p['bet'],
            'cashed'  : p['cashed'],
            'mult'    : p['mult'],
            'result'  : p['result'],   # None | 'win' | 'loss'
        }
        for uid, p in _state['players'].items()
    ]

# ══════════════════════════════════════════════════════════════════════════════
#  ПУБЛИЧНЫЕ ДЕЙСТВИЯ (вызываются из crash_api.py)
# ══════════════════════════════════════════════════════════════════════════════

def place_bet(user: User, amount: float) -> dict:
    """Принять ставку игрока. Возвращает {'ok':True,...} или {'error':key}."""
    c = _cfg()
    uid = str(user.id)

    with _lock:
        if _state['status'] not in ('waiting', 'countdown'):
            return {'error': 'e_time'}
        if uid in _state['players']:
            return {'error': 'e_already'}

    # валидация суммы
    bet_cfg = c.get('bet', {})
    min_bet = float(bet_cfg.get('currency_min', {}).get(user.currency,
                    bet_cfg.get('min', 10)))
    max_bet = float(bet_cfg.get('max', 50000))

    # bonus: free_spins — используем FreeSpinService если в тегах есть 'fs'
    use_free_spin = False
    fs_bet_amount = amount

    tags = c.get('tags', [])
    if 'fs' in tags and 'slots' in tags:
        # Слот с FS — логика через FreeSpinService
        from app.services.freespin_service import FreeSpinService, get_fs_bet, check_user_fs
        if check_user_fs(user.id) > 0:
            use_free_spin = True
            fs_bet_amount = get_fs_bet(user.currency)
            amount = fs_bet_amount
    
    if not use_free_spin:
        if amount < min_bet:
            return {'error': 'e_min', 'min': min_bet}
        if amount > max_bet:
            return {'error': 'e_max', 'max': max_bet}

    # списываем деньги или free spin
    try:
        if use_free_spin:
            from app.services.freespin_service import FreeSpinService
            fs_result = FreeSpinService.consume_spin(user.id, 'crash', user.currency)
            new_balance = user.balance
            amount = fs_result.bet_amount
        else:
            new_balance = WalletService.place_bet(user.id, amount, 'crash')
    except Exception as e:
        msg = str(e).lower()
        return {'error': 'e_funds' if 'funds' in msg or 'balance' in msg else str(e)}

    with _lock:
        _state['players'][uid] = {
            'username'      : user.username,
            'icon_url'      : user.icon_url or '',
            'bet'           : amount,
            'cashed'        : False,
            'mult'          : None,
            'result'        : None,
            'used_free_spin': use_free_spin,
            'currency'      : user.currency,
        }

    _broadcast('players', {'players': _pub_players()})
    return {'ok': True, 'balance': new_balance, 'bet': amount,
            'free_spin': use_free_spin}

def cancel_bet(user: User) -> dict:
    """Отменить ставку до начала раунда."""
    uid = str(user.id)
    with _lock:
        if _state['status'] not in ('waiting', 'countdown'):
            return {'error': 'e_time'}
        p = _state['players'].get(uid)
        if not p:
            return {'error': 'no_bet'}
        
        amount = p['bet']
        is_fs = p.get('used_free_spin', False)
        del _state['players'][uid]

    new_balance = user.balance
    if not is_fs:
        try:
            # Возвращаем средства через WalletService
            new_balance = WalletService.pay_win(user.id, amount, 'crash', meta={'type': 'refund'})
        except Exception as e:
            return {'error': str(e)}

    _broadcast('players', {'players': _pub_players()})
    return {'ok': True, 'balance': new_balance}


def cashout(user: User) -> dict:
    """Кешаут. Возвращает {'ok':True, 'mult', 'win', 'balance'} или {'error':key}."""
    uid = str(user.id)

    with _lock:
        if _state['status'] != 'running':
            return {'error': 'not_running'}
        p = _state['players'].get(uid)
        if not p or p['cashed']:
            return {'error': 'no_bet'}
        mult    = round(_state['multiplier'], 2)
        win_raw = round(p['bet'] * mult, 2)
        # применяем бонус bonus_deposit как дополнительный % к выигрышу
        win     = _apply_bonus_deposit(user, win_raw)
        p['cashed'] = True
        p['mult']   = mult
        p['result'] = 'win'

    try:
        if p.get('used_free_spin'):
            from app.services.freespin_service import FreeSpinService
            new_balance = FreeSpinService.pay_fs_win(
                user.id, 'crash', win, p['bet'],
                meta={'multiplier': mult}
            )
        else:
            new_balance = WalletService.pay_win(user.id, win, 'crash',
                                                meta={'multiplier': mult})
    except Exception as e:
        return {'error': str(e)}

    GameRound.record(user.id, 'crash', p['bet'], multiplier=mult, win=win,
                     meta={'currency': user.currency, 'mode': 'freespin' if p.get('used_free_spin') else 'balance'})
    velox_engine.close_round('crash', user.id, p['bet'], win)

    _broadcast('players', {'players': _pub_players()})
    return {'ok': True, 'mult': mult, 'win': win, 'balance': new_balance}


def _apply_bonus_deposit(user: User, win: float) -> float:
    """bonus_deposit > 0 → добавляем 5% к выигрышу (бонус при депозите)."""
    try:
        if user.bonus_deposit > 0:
            return round(win * 1.05, 2)
    except Exception:
        pass
    return win

# ══════════════════════════════════════════════════════════════════════════════
#  ИГРОВОЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════════════════════

def _loop():
    while True:
        try:
            _run_round()
        except Exception as e:
            print(f'[crash.listener] error: {e}')
            time.sleep(3)


def _run_round():
    c       = _cfg()
    online  = c.get('online', {})
    none_mp = online.get('none_multiplayer', True)
    cd_secs = int(online.get('countdown_before_start', 5))

    # ── WAITING ──────────────────────────────────────────────────────────────
    with _lock:
        _state.update(status='waiting', players={}, multiplier=1.0,
                      countdown=cd_secs, seed_hash='')
        _state['round_id'] += 1
        rid = _state['round_id']

    _broadcast('state', {'status': 'waiting', 'countdown': cd_secs,
                         'multiplier': 1.0, 'players': [], 'round_id': rid})

    # Always wait for at least one bet before starting countdown
    # (none_mp=True means single-player mode — wait indefinitely)
    # (none_mp=False means multiplayer — still wait for min 1 bet)
    wait_for_bet = online.get('wait_for_bet', True)
    if wait_for_bet or none_mp:
        while True:
            time.sleep(0.15)
            with _lock:
                if _state['players']:
                    break

    # ── COUNTDOWN ─────────────────────────────────────────────────────────────
    with _lock:
        _state['status'] = 'countdown'
    _broadcast('state', {'status': 'countdown', 'countdown': cd_secs, 'round_id': rid})

    for i in range(cd_secs, 0, -1):
        with _lock:
            # Если все игроки ушли и это одиночный режим — сбрасываем раунд
            if not _state['players'] and none_mp:
                _state['status'] = 'waiting'
                _broadcast('state', {'status': 'waiting', 'round_id': rid})
                return

        with _lock: _state['countdown'] = i
        _broadcast('countdown', {'n': i})
        time.sleep(1)

        # Проверяем ещё раз ПОСЛЕ sleep — игрок мог отменить ставку во время паузы
        with _lock:
            if not _state['players'] and none_mp:
                _state['status'] = 'waiting'
                _broadcast('state', {'status': 'waiting', 'round_id': rid})
                return

    # ── RNG ЗАПРОС ────────────────────────────────────────────────────────────
    with _lock:
        uids   = [int(k) for k in _state['players']]
        main_uid = uids[0] if uids else 0

    max_mult    = float(c.get('multiplier', {}).get('max', 200.0))
    base_wp     = float(c.get('win_probability', 0.52))
    multiplayer = bool(c.get('online', {}).get('multiplayer', False))

    # Запрашиваем у gen-штаба: новый round
    rng_core, seed_hash = velox_engine.new_round('crash', main_uid)

    # Запрашиваем приказ — с историей этой игры
    order = velox_engine.get_order(
        game_id        = 'crash',
        user_id        = main_uid,
        rng            = rng_core,
        base_win_prob  = base_wp,
        max_mult       = max_mult,
        multiplayer    = multiplayer,
        active_players = uids if multiplayer else None,
    )

    # Профиль игрока для адаптивного поведения
    _profile = velox_engine._registry.get('crash', main_uid) if main_uid else None
    _consec  = _profile.consec_losses if _profile else 0

    # Получаем точку краша по приказу
    # none_mp=True → минимум 1.5×, mercy при серии проигрышей
    crash_at = velox_engine.crash_point(
        'crash', main_uid, rng_core, order,
        none_mp=none_mp,
        consec_losses=_consec,
    )

    with _lock:
        _state.update(status='running', crash_point=crash_at,
                      multiplier=1.0, seed_hash=seed_hash)

    _broadcast('state', {'status': 'running', 'multiplier': 1.0,
                         'seed_hash': seed_hash, 'round_id': rid})

    # ── RUNNING — рост множителя ──────────────────────────────────────────────
    t0 = time.time()
    while True:
        elapsed = time.time() - t0
        mult    = round(1.006 ** (elapsed * 20), 4)

        if mult >= crash_at:
            mult = crash_at
            with _lock: _state['multiplier'] = mult
            break

        with _lock: _state['multiplier'] = mult
        _broadcast('tick', {'m': mult})
        time.sleep(0.07)

    # ── CRASHED ───────────────────────────────────────────────────────────────
    with _lock:
        _state.update(status='crashed', multiplier=crash_at)
        _state['history'].insert(0, {
            'multiplier': crash_at,
            'color': 'green' if crash_at >= 2.0 else 'red',
        })
        _state['history'] = _state['history'][:20]
        snap = dict(_state['players'])

    _broadcast('crash', {
        'at'     : crash_at,
        'history': list(_state['history'][:10]),
        'players': _pub_players(),
    })

    # Фиксируем проигрыши всех кто не кешаутнулся
    for uid_str, p in snap.items():
        if p['cashed']:
            continue
        uid_int = int(uid_str)
        with _lock:
            if uid_str in _state['players']:
                _state['players'][uid_str]['result'] = 'loss'
                _state['players'][uid_str]['mult']   = crash_at
        try:
            GameRound.record(uid_int, 'crash', p['bet'],
                             multiplier=crash_at, win=0,
                             meta={'currency': p.get('currency', 'UAH')})
        except Exception:
            pass
        try:
            velox_engine.close_round('crash', uid_int, p['bet'], 0)
        except Exception:
            pass

    _broadcast('players', {'players': _pub_players()})
    time.sleep(4)

# ══════════════════════════════════════════════════════════════════════════════
#  ЗАПУСК
# ══════════════════════════════════════════════════════════════════════════════

_started = False
_start_lock = threading.Lock()


def start():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name='crash-listener').start()
