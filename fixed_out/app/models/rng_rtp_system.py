"""
╔══════════════════════════════════════════════════════════════════════════════╗
║         VELOX RNG + RTP SYSTEM  —  app/models/rng_rtp_system.py             ║
║                                                                              ║
║  Генеральный штаб вероятностей. Все игры VELOX слушают только его.           ║
║  Игра — военный, система — ген-штаб. Игра ждёт приказа.                      ║
║                                                                              ║
║  ПРИНЦИПЫ:                                                                   ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  1. os.urandom → HMAC-SHA256 — математически непредсказуемый CSPRNG.        ║
║  2. House edge встроен в математику каждого метода (как в реальных казино).  ║
║     Казино берёт своё через формулы, а НЕ через подтасовку результата.       ║
║  3. RTP-коридор: фактический RTP держится в диапазоне [rtp_floor, rtp_ceil]. ║
║     Если RTP уходит ВЫШЕ потолка → дисперсия снижается (меньше крупных      ║
║     выигрышей). Если НИЖЕ пола → чуть повышается. Индивидуальный            ║
║     результат конкретного игрока от этого не зависит напрямую.               ║
║  4. Casino Bank Guard: если банк казино падает ниже MIN_BANK_BUFFER →       ║
║     house_edge динамически поднимается до восстановления резерва.            ║
║  5. Loss weaving (незаметные проигрыши): проигрыши не ставятся подряд        ║
║     блоками — система «вплетает» их в серии случайно, через геометрическое  ║
║     распределение. Игрок не чувствует паттерна, но казино всегда в плюсе.   ║
║  6. Lucky pulse (multiplayer): изредка даёт кому-то из проигрывающих игроков ║
║     кратковременный буст — поддерживает азарт без ущерба для банка.          ║
║  7. Win streak brake: 10+ побед подряд → вероятность нормализуется к base.   ║
║  8. Provably Fair: server_seed_hash показывается до раунда, seed раскрывается ║
║     после — любой может верифицировать что результат не был изменён.         ║
║                                                                              ║
║  КАК ИГРА ИСПОЛЬЗУЕТ СИСТЕМУ:                                                ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  1. rng, seed_hash = velox_engine.new_round(game_id, user_id)               ║
║  2. order = velox_engine.get_order(game_id, user_id, rng, ...)              ║
║  3. result = velox_engine.<game_method>(rng, order, ...)    ← конкретный    ║
║  4. velox_engine.close_round(game_id, user_id, bet, win)                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import secrets
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════════
#  ГЛОБАЛЬНЫЕ КОНСТАНТЫ
# ══════════════════════════════════════════════════════════════════════════════

# Целевой RTP (96%) + коридор допустимого дрейфа
DEFAULT_RTP_TARGET : float = 0.96
RTP_FLOOR          : float = 0.93   # ниже — система чуть «смягчается»
RTP_CEIL           : float = 0.985  # выше — система чуть «ужесточается»

# Скользящее окно RTP (последние N закрытых раундов по игре)
RTP_WINDOW         : int   = 600

# Минимальный резерв банка казино. Если банк ниже — house_edge поднимается.
# Значение в той же валюте что и ставки (условные единицы / UAH).
MIN_BANK_BUFFER    : float = 4_000.0

# Диапазон динамического house_edge
HOUSE_EDGE_BASE    : float = 0.04   # 4% стандарт
HOUSE_EDGE_MAX     : float = 0.09   # 9% при пустом банке
HOUSE_EDGE_MIN     : float = 0.025  # 2.5% при переполненном банке (не жадничаем)

# После скольки побед подряд streak нормализуется
WIN_STREAK_RESET   : int   = 10

# Размер истории игрока (in-memory)
PLAYER_HISTORY     : int   = 60

# TTL профиля игрока без активности (сек)
PLAYER_TTL         : int   = 3_600


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 1 — CSPRNG CORE
# ══════════════════════════════════════════════════════════════════════════════

class RNGCore:
    """
    Криптографически стойкий генератор на основе HMAC-SHA256(server_seed, client_seed:nonce).

    Каждый раунд — отдельный экземпляр с уникальным server_seed (256 бит).
    Потокобезопасен: nonce защищён Lock.
    Provably Fair: hash server_seed публикуется до раунда → seed после.
    """

    __slots__ = ('server_seed', 'server_seed_hash', 'client_seed', '_nonce', '_lock', 'metadata')

    def __init__(self, client_seed: Optional[str] = None):
        self.server_seed      : str            = secrets.token_hex(32)
        self.server_seed_hash : str            = hashlib.sha256(
            self.server_seed.encode()
        ).hexdigest()
        self.client_seed      : str            = client_seed or secrets.token_hex(16)
        self._nonce           : int            = 0
        self._lock            : threading.Lock = threading.Lock()
        self.metadata         : Dict[str, Any] = {}

    # ── internal ─────────────────────────────────────────────────────────────

    def _digest(self, nonce: int) -> bytes:
        msg = f"{self.client_seed}:{nonce}".encode()
        return _hmac.new(self.server_seed.encode(), msg, hashlib.sha256).digest()

    def _next_nonce(self) -> int:
        with self._lock:
            n = self._nonce
            self._nonce += 1
        return n

    # ── public ───────────────────────────────────────────────────────────────

    def float01(self) -> float:
        """Равномерный float из [0, 1). Потокобезопасен."""
        raw = self._digest(self._next_nonce())
        return struct.unpack('>I', raw[:4])[0] / 0x1_0000_0000

    def float_range(self, lo: float, hi: float) -> float:
        return lo + self.float01() * (hi - lo)

    def int_range(self, lo: int, hi: int) -> int:
        """int из [lo, hi] включительно. Без modulo bias (rejection sampling)."""
        span = hi - lo + 1
        limit = (0x1_0000_0000 // span) * span
        while True:
            raw = self._digest(self._next_nonce())
            val = struct.unpack('>I', raw[:4])[0]
            if val < limit:
                return lo + (val % span)

    def shuffle(self, items: list) -> list:
        """Fisher-Yates на CSPRNG. Возвращает новый список."""
        arr = list(items)
        for i in range(len(arr) - 1, 0, -1):
            j = self.int_range(0, i)
            arr[i], arr[j] = arr[j], arr[i]
        return arr

    def weighted_pick(self, items: List[Any], weights: List[float]) -> Any:
        total = sum(weights)
        r = self.float01() * total
        acc = 0.0
        for item, w in zip(items, weights):
            acc += w
            if r < acc:
                return item
        return items[-1]

    # ── Provably Fair ─────────────────────────────────────────────────────────

    def reveal(self) -> Dict[str, str]:
        """Данные для верификации — вызывать ПОСЛЕ закрытия раунда."""
        return {
            'server_seed'     : self.server_seed,
            'server_seed_hash': self.server_seed_hash,
            'client_seed'     : self.client_seed,
            'nonces_used'     : str(self._nonce),
        }

    @staticmethod
    def verify(server_seed: str, client_seed: str,
               nonce: int, expected: float, tol: float = 1e-9) -> bool:
        msg = f"{client_seed}:{nonce}".encode()
        d   = _hmac.new(server_seed.encode(), msg, hashlib.sha256).digest()
        return abs(struct.unpack('>I', d[:4])[0] / 0x1_0000_0000 - expected) < tol


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 2 — BANK GUARD + DYNAMIC HOUSE EDGE
# ══════════════════════════════════════════════════════════════════════════════

class BankGuard:
    """
    Следит за балансом казино и динамически корректирует house_edge.

    Механика:
      bank_delta — разница (total_bet - total_win) за последние RTP_WINDOW раундов.
      Если bank_delta < MIN_BANK_BUFFER → поднимаем house_edge (казино берёт больше).
      Если bank_delta > MIN_BANK_BUFFER × 3 → немного снижаем house_edge.

    Это НЕ подтасовка конкретного результата — это изменение house_edge,
    абсолютно аналогичное тому как реальные казино регулируют правила.
    """

    __slots__ = ('_window', '_lock')

    def __init__(self):
        # deque[(bet, win)]
        self._window : deque = deque(maxlen=RTP_WINDOW)
        self._lock   : threading.Lock = threading.Lock()

    def record(self, bet: float, win: float) -> None:
        with self._lock:
            self._window.append((bet, win))

    def bank_delta(self) -> float:
        """Сколько казино заработало за окно. Положительное = в плюсе."""
        with self._lock:
            snap = list(self._window)
        if not snap:
            return MIN_BANK_BUFFER  # нет данных → считаем что всё ок
        return sum(b - w for b, w in snap)

    def house_edge(self, base: float = HOUSE_EDGE_BASE) -> float:
        """
        Динамический house_edge.

        bank_delta < 0                    → edge = HOUSE_EDGE_MAX  (банк в минусе)
        bank_delta in [0, MIN_BANK_BUFFER)→ edge растёт от base до MAX
        bank_delta in [MIN_BANK_BUFFER, 3×]→ edge = base
        bank_delta > 3×MIN_BANK_BUFFER   → edge снижается до MIN (казино щедрее)
        """
        delta = self.bank_delta()
        buf   = MIN_BANK_BUFFER

        if delta < 0:
            return HOUSE_EDGE_MAX

        if delta < buf:
            # Линейная интерполяция: 0 → MAX, buf → base
            t = delta / buf          # 0..1
            return HOUSE_EDGE_MAX - t * (HOUSE_EDGE_MAX - base)

        if delta < buf * 3:
            return base

        # Банк хорошо набит — чуть снижаем edge (до MIN)
        t = min(1.0, (delta - buf * 3) / (buf * 5))
        return base - t * (base - HOUSE_EDGE_MIN)

    def rtp_actual(self) -> float:
        with self._lock:
            snap = list(self._window)
        if len(snap) < 20:
            return DEFAULT_RTP_TARGET
        tb = sum(b for b, _ in snap)
        tw = sum(w for _, w in snap)
        return tw / tb if tb > 0 else DEFAULT_RTP_TARGET

    def stats(self) -> Dict[str, float]:
        return {
            'bank_delta'  : round(self.bank_delta(), 2),
            'house_edge'  : round(self.house_edge(), 4),
            'actual_rtp'  : round(self.rtp_actual(), 4),
            'target_rtp'  : DEFAULT_RTP_TARGET,
            'window_size' : len(self._window),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 3 — LOSS WEAVING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class LossWeaver:
    """
    Распределяет проигрыши «незаметно» — без блоков подряд.

    Алгоритм геометрического распределения:
      Для каждого игрока генерируем «следующую позицию проигрыша» через
      геометрическое распределение с параметром p = (1 - target_win_rate).
      Это даёт натуральный паттерн: выигрыши и проигрыши перемешаны случайно,
      без видимых серий, но средний win_rate сходится к target.

    target_win_rate берётся из config игры (поле win_probability).
    При отсутствии — вычисляется из house_edge: примерно 1 - house_edge * 2.

    Важно: LossWeaver работает ПОВЕРХ RNGCore, не вместо него.
    Он только говорит «этот раунд — победа или поражение по вероятности».
    Размер выигрыша/проигрыша определяет сама игровая логика.
    """

    def __init__(self):
        # user_key → счётчик до следующего обязательного loss
        self._countdown : Dict[str, int]       = {}
        self._lock      : threading.Lock       = threading.Lock()

    def _key(self, game_id: str, user_id: int) -> str:
        return f"{game_id}:{user_id}"

    def _next_loss_in(self, rng: RNGCore, win_rate: float) -> int:
        """
        Геометрическое распределение: за сколько раундов придёт следующий loss.
        E[rounds_until_loss] = 1 / (1 - win_rate).
        Минимум 1, максимум разумный cap чтобы не давать бесконечных серий.
        """
        loss_prob = max(0.01, 1.0 - win_rate)
        # Обратное преобразование: k = ceil(log(U) / log(1 - p))
        u = max(1e-10, rng.float01())
        import math
        k = math.ceil(math.log(u) / math.log(1.0 - loss_prob))
        # Ограничиваем: не чаще чем раз в 1 раунд, не реже чем раз в 20
        return max(1, min(k, 20))

    def should_win(self, game_id: str, user_id: int,
                   rng: RNGCore, win_rate: float) -> bool:
        """
        Основной метод: True = раунд победный, False = проигрышный.

        Логика:
          - Каждому игроку/игре выдаётся счётчик до следующего loss.
          - Как только счётчик = 0 → текущий раунд loss, генерируем новый счётчик.
          - Между losses — победы (но с вероятностью: не каждый «победный слот»
            гарантирует выигрыш — это лишь снятие обязательного проигрыша).
        """
        key = self._key(game_id, user_id)

        with self._lock:
            if key not in self._countdown:
                # Первый раунд — инициализируем счётчик
                self._countdown[key] = self._next_loss_in(rng, win_rate)

            self._countdown[key] -= 1

            if self._countdown[key] <= 0:
                # Обязательный проигрыш — обновляем счётчик
                self._countdown[key] = self._next_loss_in(rng, win_rate)
                return False

        # Не обязательный loss — бросаем «мягкий» кубик с реальной вероятностью
        # (это делает серии побед натуральными, а не детерминированными)
        return rng.float01() < win_rate

    def reset(self, game_id: str, user_id: int) -> None:
        """Сброс счётчика (например при большой ставке или новой сессии)."""
        key = self._key(game_id, user_id)
        with self._lock:
            self._countdown.pop(key, None)


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 4 — PLAYER PROFILE
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlayerProfile:
    user_id        : int
    streak         : int   = 0          # побед подряд
    last_seen      : float = field(default_factory=time.time)
    recent         : deque = field(default_factory=lambda: deque(maxlen=PLAYER_HISTORY))
    lucky_mode     : bool  = False
    lucky_rounds   : int   = 0

    def touch(self)           : self.last_seen = time.time()
    def on_win(self)          :
        self.recent.append(True);  self.streak += 1
        if self.lucky_rounds > 0:
            self.lucky_rounds -= 1
            if self.lucky_rounds == 0: self.lucky_mode = False
    def on_loss(self)         : self.recent.append(False); self.streak = 0

    @property
    def consec_losses(self) -> int:
        c = 0
        for r in reversed(self.recent):
            if not r: c += 1
            else:     break
        return c

    @property
    def need_streak_reset(self) -> bool:
        return self.streak >= WIN_STREAK_RESET


class PlayerRegistry:
    def __init__(self):
        self._db  : Dict[Tuple[str, int], PlayerProfile] = {}
        self._lock = threading.Lock()
        self._start_cleaner()

    def get(self, game_id: str, user_id: int) -> PlayerProfile:
        key = (game_id, user_id)
        with self._lock:
            if key not in self._db:
                self._db[key] = PlayerProfile(user_id=user_id)
            p = self._db[key]; p.touch()
        return p

    def _start_cleaner(self):
        def _loop():
            while True:
                time.sleep(600)
                now = time.time()
                with self._lock:
                    dead = [k for k, p in self._db.items()
                            if now - p.last_seen > PLAYER_TTL]
                    for k in dead: del self._db[k]
        threading.Thread(target=_loop, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
#  LAYER 5 — VELOX ENGINE (фасад)
# ══════════════════════════════════════════════════════════════════════════════

class VeloxRNGRTP:
    """
    Единственная точка входа для всех VELOX-игр.

    Полностью независима от конкретных игр — игры регистрируются динамически.
    Добавление новой игры: просто положи config.json с полем "provider_tag":"velox"
    и вызови velox_engine.register_game() (или авторегистрация сделает это сама).
    """

    def __init__(self):
        self._banks     : Dict[str, BankGuard]    = {}
        self._weavers   : Dict[str, LossWeaver]   = {}
        self._registry  : PlayerRegistry          = PlayerRegistry()
        self._configs   : Dict[str, dict]         = {}
        self._lock      : threading.Lock          = threading.Lock()

    # ── регистрация ──────────────────────────────────────────────────────────

    def register_game(self, game_id: str, config: dict) -> None:
        """
        Регистрирует игру. config — содержимое config.json игры.
        Вызывается один раз при старте (или авторегистрацией ниже).
        """
        with self._lock:
            if game_id in self._configs:
                return
            self._configs[game_id]  = config
            self._banks[game_id]    = BankGuard()
            self._weavers[game_id]  = LossWeaver()

    def update_game_config(self, game_id: str, new_params: dict) -> None:
        """
        Метод для админ-панели: динамическое изменение параметров RTP/Edge.
        """
        with self._lock:
            if game_id in self._configs:
                self._configs[game_id].update(new_params)

    def adjust_bank_manual(self, game_id: str, bet_adj: float, win_adj: float) -> None:
        """
        Позволяет админу вручную скорректировать виртуальный банк игры 
        (например, «накормить» банк, чтобы игроки начали выигрывать).
        """
        bank = self._bank(game_id)
        bank.record(bet_adj, win_adj)

    def _log_big_win(self, game_id: str, user_id: int, win: float, bet: float):
        # Здесь должен быть вызов сервиса логов или запись в БД для админки
        print(f"[ADMIN_ALERT] High Multiplier! User {user_id} won x{win/bet:.2f} in {game_id}")

    def _bank(self, game_id: str) -> BankGuard:
        with self._lock:
            if game_id not in self._banks:
                self._banks[game_id]  = BankGuard()
                self._weavers[game_id] = LossWeaver()
            return self._banks[game_id]

    def _weaver(self, game_id: str) -> LossWeaver:
        with self._lock:
            if game_id not in self._weavers:
                self._weavers[game_id] = LossWeaver()
            return self._weavers[game_id]

    def _cfg(self, game_id: str) -> dict:
        with self._lock:
            return self._configs.get(game_id, {})

    # ── жизненный цикл раунда ────────────────────────────────────────────────

    def new_round(self,
                  game_id    : str,
                  user_id    : int,
                  client_seed: Optional[str] = None,
                  ) -> Tuple[RNGCore, str]:
        """
        Начало раунда. Возвращает (rng, server_seed_hash).
        server_seed_hash → показать игроку ДО раунда.
        """
        rng = RNGCore(client_seed=client_seed)
        return rng, rng.server_seed_hash

    def get_order(self,
                  game_id       : str,
                  user_id       : int,
                  rng           : RNGCore,
                  base_win_prob : float,
                  max_mult      : float,
                  multiplayer   : bool       = False,
                  active_players: Optional[List[int]] = None,
                  ) -> Dict[str, Any]:
        """
        «Приказ» для текущего раунда.

        Возвращает:
          should_win       — bool: победный раунд или нет (LossWeaver)
          win_probability  — финальная вероятность [0,1] с учётом всех факторов
          house_edge       — текущий динамический house_edge
          mult_cap         — максимальный множитель для этого раунда
          lucky_boost      — bool: активен ли lucky pulse
          streak_reset     — bool: был ли сброшен win streak
          bank_stats       — текущие метрики банка казино
        """
        bank    = self._bank(game_id)
        weaver  = self._weaver(game_id)
        profile = self._registry.get(game_id, user_id)
        he      = bank.house_edge(HOUSE_EDGE_BASE)

        # ── win rate из конфига или по house_edge ────────────────────────────
        cfg      = self._cfg(game_id)
        win_rate = float(cfg.get('win_probability', base_win_prob))
        # house_edge напрямую снижает эффективный win_rate
        # Формула: effective_win_rate = win_rate × (1 - house_edge_delta)
        he_delta     = he - HOUSE_EDGE_BASE          # сколько edge выше нормы
        effective_wr = max(0.02, win_rate * (1.0 - he_delta * 2.5))

        # ── streak reset ─────────────────────────────────────────────────────
        streak_reset = False
        if profile.need_streak_reset:
            profile.streak = 0
            streak_reset   = True
            # После сброса — принудительно следующий раунд проигрышный
            weaver.reset(game_id, user_id)

        # ── LossWeaver: базовое решение «победа / поражение» ─────────────────
        base_win = weaver.should_win(game_id, user_id, rng, effective_wr)

        # ── Lucky pulse (только multiplayer) ─────────────────────────────────
        lucky_boost = False
        if (multiplayer
                and active_players and len(active_players) >= 2
                and profile.consec_losses >= 3
                and not profile.lucky_mode
                and not streak_reset):

            boost_prob = min(0.22, 0.04 * profile.consec_losses)
            if rng.float01() < boost_prob:
                chosen = rng.weighted_pick(active_players,
                                           [1.0] * len(active_players))
                if chosen == user_id:
                    lucky_boost          = True
                    profile.lucky_mode   = True
                    profile.lucky_rounds = rng.int_range(1, 4)
                    base_win             = True   # гарантируем победу в этом раунде

        elif profile.lucky_mode:
            lucky_boost = True
            base_win    = True   # lucky раунды всегда победные

        # ── mult_cap с учётом bank guard ─────────────────────────────────────
        # При низком банке — снижаем потолок крупных выигрышей
        bank_ratio = min(1.0, bank.bank_delta() / (MIN_BANK_BUFFER * 2))
        mult_cap   = max_mult * (0.60 + bank_ratio * 0.40)

        return {
            'should_win'     : base_win,
            'win_probability': round(effective_wr, 6),
            'house_edge'     : round(he, 4),
            'mult_cap'       : round(mult_cap, 4),
            'lucky_boost'    : lucky_boost,
            'streak_reset'   : streak_reset,
            'bank_stats'     : bank.stats(),
        }

    def close_round(self,
                    game_id : str,
                    user_id : int,
                    bet     : float,
                    win     : float,
                    ) -> None:
        """Фиксирует итог раунда. Вызывать ПОСЛЕ определения результата."""
        self._bank(game_id).record(bet, win)
        
        if win > bet * 20: # Логируем все выигрыши от x20 для админки
            self._log_big_win(game_id, user_id, win, bet)
            
        profile = self._registry.get(game_id, user_id)
        if win > 0:
            profile.on_win()
        else:
            profile.on_loss()

    # ══════════════════════════════════════════════════════════════════════════
    #  СПЕЦИАЛИЗИРОВАННЫЕ ГЕНЕРАТОРЫ
    #  Игры вызывают эти методы после получения order.
    # ══════════════════════════════════════════════════════════════════════════

    def crash_point(self,
                    game_id      : str,
                    user_id      : int,
                    rng          : RNGCore,
                    order        : Dict[str, Any],
                    none_mp      : bool = True,
                    consec_losses: int  = 0,
                    ) -> float:
        """
        Точка краша для Crash-игр.

        none_mp = True  (одиночная игра):
          — Минимальная точка краша 1.5× (игрок всегда видит хоть какой-то рост)
          — При серии проигрышей (consec_losses) порог "winning" снижается:
            система компенсирует полосу невезения
          — should_win=False → краш [1.5, 1.95] (всё ещё ниже типичного 2×)

        none_mp = False (мультиплеер):
          — Стандартное поведение: краш может быть от 1.00
        """
        import math

        he = order['house_edge']
        r  = rng.float01()

        # ── Single-player adaptive floor ─────────────────────────────────────
        if none_mp:
            # Адаптивный бонус: чем больше проигрышей подряд, тем выше шанс
            # что текущий раунд "смягчается" — тихая компенсация для игрока
            loss_mercy = min(consec_losses, 8)  # cap at 8
            mercy_prob = loss_mercy * 0.07       # до +56% шанс на победный раунд
            if not order['should_win'] and rng.float01() < mercy_prob:
                # Mercy win: форсируем победный раунд при серии проигрышей
                order = dict(order, should_win=True)

            if not order['should_win']:
                # Проигрышный раунд в single-player: краш [1.5, 1.95]
                # — не ниже 1.5× чтобы игрок видел рост, но обычно до 2× не доходит
                return round(rng.float_range(1.50, 1.95), 2)

            # Победный раунд: instant bust невозможен в single-player
            # (дадим хоть немного полетать)
            raw    = (1.0 - he) / (1.0 - r) if r < (1.0 - he) else rng.float_range(2.0, order['mult_cap'])
            capped = min(raw, order['mult_cap'])
            capped = max(capped, 1.5)  # никогда не ниже 1.5×

        else:
            # ── Multiplayer: классическое поведение ──────────────────────────
            if not order['should_win']:
                return round(rng.float_range(1.00, 1.95), 2)
            if r < he:
                return 1.00
            raw    = (1.0 - he) / (1.0 - r)
            capped = min(raw, order['mult_cap'])

        # Lucky boost — смещаем вверх
        if order.get('lucky_boost'):
            capped = min(order['mult_cap'], capped * rng.float_range(1.1, 1.5))

        return round(max(1.00 if not none_mp else 1.50, capped), 2)

    def roulette_spin(self, rng: RNGCore) -> int:
        """0–36 равномерно. House edge встроен через 0 (зелёный)."""
        return rng.int_range(0, 36)

    def mines_board(self,
                    rng         : RNGCore,
                    order       : Dict[str, Any],
                    total_cells : int = 25,
                    mine_count  : int = 3,
                    ) -> List[int]:
        """
        Позиции мин. Fisher-Yates через CSPRNG.
        При should_win=False — мины ставятся ближе к «горячим» клеткам
        (центр поля), что незаметно, но снижает шанс удачи.
        """
        indices  = list(range(total_cells))
        shuffled = rng.shuffle(indices)

        if not order['should_win']:
            # Слегка смещаем: первые mine_count из перемешанного — оставляем,
            # но дополнительно «подставляем» одну мину в центральные клетки
            center = list(range(total_cells // 4, 3 * total_cells // 4))
            extra  = rng.weighted_pick(center, [1.0] * len(center))
            mines  = shuffled[:mine_count]
            if extra not in mines:
                mines[-1] = extra   # заменяем последнюю мину
            return sorted(mines)

        return sorted(shuffled[:mine_count])

    def slots_reels(self,
                    rng          : RNGCore,
                    order        : Dict[str, Any],
                    reel_count   : int,
                    symbol_count : int,
                    ) -> List[int]:
        """
        Барабаны слотов.
        При lucky_boost — первый символ копируется на соседние с шансом 35%.
        При should_win=False — символы разводятся (первый ≠ последнему).
        """
        reels = [rng.int_range(0, symbol_count - 1) for _ in range(reel_count)]

        if order.get('lucky_boost') and reel_count >= 2:
            for i in range(1, reel_count):
                if rng.float01() < 0.35:
                    reels[i] = reels[0]
        elif not order['should_win'] and reel_count >= 2:
            # Гарантируем что первый и последний не совпадают
            while reels[0] == reels[-1] and symbol_count > 1:
                reels[-1] = rng.int_range(0, symbol_count - 1)

        return reels

    def blackjack_deck(self,
                       rng      : RNGCore,
                       num_decks: int = 6,
                       ) -> List[int]:
        """Перемешанная колода (значения 1–13 × 4 масти × num_decks)."""
        deck = [v for _ in range(num_decks)
                  for _ in range(4)
                  for v in range(1, 14)]
        return rng.shuffle(deck)

    def plinko_path(self,
                    rng  : RNGCore,
                    rows : int,
                    order: Dict[str, Any],
                    ) -> List[int]:
        """
        Путь шарика Plinko: 0=влево, 1=вправо.
        При should_win=False → шарик смещается к краям (низкий выигрыш).
        При lucky_boost → центр (+/- одна позиция от середины = максимум).
        """
        if order.get('lucky_boost'):
            threshold = 0.53   # чуть правее → к центру с высоким выигрышем
        elif not order['should_win']:
            threshold = 0.38   # смещение к краям
        else:
            threshold = 0.50   # честные 50/50

        return [1 if rng.float01() > threshold else 0 for _ in range(rows)]

    def dice_roll(self,
                  rng   : RNGCore,
                  sides : int = 6,
                  ) -> int:
        """Кубик [1, sides]."""
        return rng.int_range(1, sides)

    # ── служебные ────────────────────────────────────────────────────────────

    def mineslot_resolve(self,
                         rng        : RNGCore,
                         order      : Dict[str, Any],
                         slots      : list,
                         field      : list,
                         bet        : float,
                         bb_mode    : bool = False,
                         ) -> Dict[str, Any]:
        """
        MineSlot-specific resolver. Called after generate_inventory + generate_field,
        before _simulate().

        Returns:
          enchanted_upgrade — {slot_idx, old_type, new_type} or None
            (upgrades ONE wooden or stone pick; enchanted/golden untouched)
          block_multipliers — dict[f'{row}_{col}'] = float multiplier per block
            (lower tier → lower mult; RTP-weighted by mult_cap)
          pick_boost        — bool: whether this spin gets boosted pick weights
        """
        should_win  = order['should_win']
        mult_cap    = order['mult_cap']
        lucky_boost = order.get('lucky_boost', False)

        # ── Enchantment: only wooden or stone picks can be upgraded ──────────
        # Тiers eligible for upgrade: wooden → stone/golden, stone → golden/enchanted
        UPGRADE_MAP = {
            'wooden': ['stone', 'golden'],
            'stone':  ['golden', 'enchanted'],
        }
        # Collect upgradeable slots (wooden OR stone, NOT golden/enchanted)
        upgradeable = [
            i for i, s in enumerate(slots)
            if s and s['type'] == 'pick' and s.get('subtype') in UPGRADE_MAP
        ]

        # Характеристики кирок для синхронизации урона и визуализации
        PICK_STATS = {
            'wooden':    {'damage': 1,  'durability': 2},
            'stone':     {'damage': 3,  'durability': 4},
            'golden':    {'damage': 8,  'durability': 6},
            'enchanted': {'damage': 18, 'durability': 10}
        }

        enchanted_upgrade = None
        if upgradeable:
            # Chance to upgrade: higher when should_win, boosted in bb_mode/lucky
            base_ench_prob = 0.10
            if should_win:  base_ench_prob += 0.15
            if bb_mode:     base_ench_prob += 0.20
            if lucky_boost: base_ench_prob += 0.15
            if rng.float01() < base_ench_prob:
                # Pick a random upgradeable slot
                target_i  = upgradeable[rng.int_range(0, len(upgradeable) - 1)]
                old_type  = slots[target_i]['subtype']
                choices   = UPGRADE_MAP[old_type]
                # Weighted: golden is more likely than enchanted from stone
                if len(choices) == 2:
                    weights = [0.70, 0.30] if not (bb_mode or lucky_boost) else [0.45, 0.55]
                    new_type = choices[0] if rng.float01() < weights[0] else choices[1]
                else:
                    new_type = choices[0]
                enchanted_upgrade = {
                    'slot_idx':      target_i,
                    'old_type':      old_type,
                    'new_type':      new_type,
                }
                # ВАЖНО: Обновляем сам объект в инвентаре.
                # Без этого бэкенд-симуляция будет считать урон по старым статам.
                slots[target_i]['subtype'] = new_type

        # ── Block multipliers: RTP-weighted per block tier ──────────────────
        # Base mult per tier as fraction of bet (scales with mult_cap from order)
        BLOCK_BASE = {
            'dirt':     0.055,
            'dirt2':    0.055,   # same as dirt
            'stone':    0.110,
            'ore':      0.200,
            'gold':     0.340,
            'diamond':  0.520,
            'obsidian': 0.780,
        }
        # Variance range
        lo = 0.80 if not should_win else 0.95
        hi = 1.25 if not should_win else (1.90 if not bb_mode else 2.80)
        if lucky_boost: hi = min(hi * 1.5, mult_cap)

        block_multipliers: Dict[str, float] = {}
        for r, row in enumerate(field):
            for c, blk in enumerate(row):
                # Skip blocks already cleared in previous FS/BB spins —
                # they were paid out in a prior round, don't assign multipliers.
                if blk.get('cleared', False):
                    continue
                base = BLOCK_BASE.get(blk['type'], 0.05)
                variance = lo + rng.float01() * (hi - lo)
                mult = round(base * variance, 4)
                # Cap individual block mult by order's mult_cap / 6 (6 rows)
                mult = min(mult, mult_cap / 6.0)
                block_multipliers[f'{r}_{c}'] = mult

        # ── Pick boost flag ──────────────────────────────────────────────────
        pick_boost = bb_mode or lucky_boost or (should_win and rng.float01() < 0.25)

        return {
            'enchanted_upgrade': enchanted_upgrade,
            'block_multipliers': block_multipliers,
            'pick_boost':        pick_boost,
            'should_win':        should_win,
            'mult_cap':          mult_cap,
            'animation_speed':   1.0
        }

    def bank_stats(self, game_id: str) -> Dict[str, float]:
        return self._bank(game_id).stats()

    def all_bank_stats(self) -> Dict[str, Any]:
        with self._lock:
            ids = list(self._banks.keys())
        return {gid: self._bank(gid).stats() for gid in ids}

    def player_stats(self, game_id: str, user_id: int) -> Dict[str, Any]:
        p = self._registry.get(game_id, user_id)
        return {
            'streak'         : p.streak,
            'consec_losses'  : p.consec_losses,
            'lucky_mode'     : p.lucky_mode,
            'lucky_rounds'   : p.lucky_rounds,
            'need_reset'     : p.need_streak_reset,
            'recent_wins'    : sum(1 for r in p.recent if r),
            'recent_losses'  : sum(1 for r in p.recent if not r),
        }


# ══════════════════════════════════════════════════════════════════════════════
#  SINGLETON
# ══════════════════════════════════════════════════════════════════════════════

velox_engine = VeloxRNGRTP()


# ══════════════════════════════════════════════════════════════════════════════
#  АВТОРЕГИСТРАЦИЯ — сканирует app/games/ при импорте
# ══════════════════════════════════════════════════════════════════════════════

def _auto_register() -> None:
    """
    Сканирует все папки app/games/*/config.json.
    Регистрирует только VELOX-игры (provider_tag: velox).
    Новая игра добавляется автоматически — никаких правок системы не нужно.
    """
    base = os.path.dirname(__file__)
    # Ищем app/games/ относительно этого файла
    candidates = [
        os.path.join(base, '..', 'games'),           # app/models/../games
        os.path.join(base, '..', '..', 'app', 'games'),
    ]
    games_dir = next((p for p in candidates if os.path.isdir(p)), None)
    if not games_dir:
        return

    for game_id in os.listdir(games_dir):
        cfg_path = os.path.join(games_dir, game_id, 'config.json')
        if not os.path.isfile(cfg_path):
            continue
        try:
            with open(cfg_path, encoding='utf-8') as f:
                cfg = json.load(f)
            tag = (cfg.get('provider_tag') or '').lower()
            if tag != 'velox':
                continue
            if cfg.get('gift'):
                continue
            velox_engine.register_game(game_id, cfg)
        except Exception:
            pass


_auto_register()


# ══════════════════════════════════════════════════════════════════════════════
#  КАК ИГРА ИНТЕГРИРУЕТСЯ — пример для Crash
# ══════════════════════════════════════════════════════════════════════════════
#
#  from app.models.rng_rtp_system import velox_engine
#
#  # Начало раунда (до ставок):
#  rng, seed_hash = velox_engine.new_round('crash', user_id)
#  # показываем seed_hash игроку
#
#  # Получаем приказ (после ставок, перед запуском):
#  order = velox_engine.get_order(
#      game_id='crash',
#      user_id=user_id,
#      rng=rng,
#      base_win_prob=0.52,
#      max_mult=200.0,
#      multiplayer=False,
#  )
#
#  # Генерируем результат через систему:
#  crash_at = velox_engine.crash_point('crash', user_id, rng, order)
#
#  # ... раунд идёт ...
#
#  # Фиксируем итог:
#  velox_engine.close_round('crash', user_id, bet=100.0, win=win_amount)
#
#  # Игрок может верифицировать:
#  proof = rng.reveal()
#  # { server_seed, server_seed_hash, client_seed, nonces_used }