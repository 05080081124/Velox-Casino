"""
app/games/slots_777/listener.py — Слот «777».

Три барабана, 9 символов, RTP ~96.5%.
Поддерживает Free Spins через тег 'fs' в config.json.

Таблица символов (вес → вероятность выпадения):
  WILD   — вес 1   (замена любого символа)
  777    — вес 2
  BAR3   — вес 4
  BAR2   — вес 6
  BAR    — вес 10
  CHERRY — вес 14
  LEMON  — вес 18
  ORANGE — вес 22
  BELL   — вес 23

Таблица выплат (ставка × коэффициент):
  WILD  WILD  WILD  → ×50
  777   777   777   → ×30
  BAR3  BAR3  BAR3  → ×20
  BAR2  BAR2  BAR2  → ×12
  BAR   BAR   BAR   → ×8
  BAR*  BAR*  BAR*  → ×4   (любые BAR)
  CHERRY CHERRY CHERRY → ×6
  CHERRY CHERRY  *  → ×3   (2 вишни)
  CHERRY *       *  → ×1.5 (1 вишня слева)
  LEMON  LEMON  LEMON → ×3
  ORANGE ORANGE ORANGE → ×2.5
  BELL   BELL   BELL  → ×2
"""
from __future__ import annotations
import json, os, random
from typing import Optional
from app.models.user import User
from app.services.game_service import game_service
from app.games.slots_base.slots_listener import spin as _base_spin

_DIR     = os.path.dirname(__file__)
_REGISTRY = os.path.join(_DIR, '..', '..', '..', 'config', 'games.json')

# ── Символы ───────────────────────────────────────────────────────────────────

SYMBOLS = [
    {'id': 'WILD',   'label': '★',  'weight': 1 },
    {'id': '777',    'label': '7',  'weight': 2 },
    {'id': 'BAR3',   'label': '▓▓▓','weight': 4 },
    {'id': 'BAR2',   'label': '▓▓', 'weight': 6 },
    {'id': 'BAR',    'label': '▓',  'weight': 10},
    {'id': 'CHERRY', 'label': '🍒', 'weight': 14},
    {'id': 'LEMON',  'label': '🍋', 'weight': 18},
    {'id': 'ORANGE', 'label': '🍊', 'weight': 22},
    {'id': 'BELL',   'label': '🔔', 'weight': 23},
]

_SYM_IDS     = [s['id']     for s in SYMBOLS]
_SYM_WEIGHTS = [s['weight'] for s in SYMBOLS]
_SYM_MAP     = {s['id']: s for s in SYMBOLS}

# ── Таблица выплат ────────────────────────────────────────────────────────────

def _calc_win(reels: list[str], bet: float) -> tuple[float, list[str]]:
    """
    Считает выигрыш для трёх барабанов.
    Возвращает (win_amount, win_lines_labels).
    """
    r0, r1, r2 = reels

    def _is_bar(s: str) -> bool:
        return s in ('BAR', 'BAR2', 'BAR3')

    def _eff(s: str) -> str:
        """WILD ведёт себя как любой символ — возвращаем 'eff' для матчинга."""
        return 'WILD' if s == 'WILD' else s

    e0, e1, e2 = _eff(r0), _eff(r1), _eff(r2)

    # Определяем реальный символ для WILD: берём символ от соседей
    def _resolve(pos: int, reels: list) -> str:
        if reels[pos] != 'WILD':
            return reels[pos]
        for s in reels:
            if s != 'WILD':
                return s
        return 'WILD'

    # ── Три одинаковых (с учётом WILD) ───────────────────────────────────────
    # Все три WILD
    if r0 == r1 == r2 == 'WILD':
        return round(bet * 107, 2), ['★ ★ ★ → ×107']

    # Проверяем три одинаковых с WILD
    resolved = (_resolve(0, reels), _resolve(1, reels), _resolve(2, reels))
    sym = resolved[0] if resolved[0] == resolved[1] == resolved[2] else None

    if sym == '777':
        return round(bet * 64,  2), ['7 7 7 → ×64']
    if sym == 'BAR3':
        return round(bet * 43,  2), ['▓▓▓ ▓▓▓ ▓▓▓ → ×43']
    if sym == 'BAR2':
        return round(bet * 26,  2), ['▓▓ ▓▓ ▓▓ → ×26']
    if sym == 'BAR':
        return round(bet * 17,  2), ['▓ ▓ ▓ → ×17']
    if sym == 'CHERRY':
        return round(bet * 13,  2), ['🍒 🍒 🍒 → ×13']
    if sym == 'LEMON':
        return round(bet * 6.4, 2), ['🍋 🍋 🍋 → ×6.4']
    if sym == 'ORANGE':
        return round(bet * 5.3, 2), ['🍊 🍊 🍊 → ×5.3']
    if sym == 'BELL':
        return round(bet * 4.3, 2), ['🔔 🔔 🔔 → ×4.3']

    # ── Любые три BAR (с WILD) ────────────────────────────────────────────────
    effective_bars = [
        _is_bar(r) or r == 'WILD'
        for r in reels
    ]
    if all(effective_bars):
        return round(bet * 8.5, 2), ['BAR BAR BAR (mix) → ×8.5']

    # ── Вишни (только реальные, не WILD) ─────────────────────────────────────
    cherries = [i for i, r in enumerate(reels) if r == 'CHERRY']
    if len(cherries) == 2:
        return round(bet * 6.4, 2), ['🍒 🍒 _ → ×6.4']
    if len(cherries) == 1 and cherries[0] == 0:   # только левый барабан
        return round(bet * 3.2, 2), ['🍒 _ _ → ×3.2']

    return 0.0, []


# ── RNG барабанов ─────────────────────────────────────────────────────────────

def _spin_reels() -> list[str]:
    """Выбирает случайные символы для трёх барабанов."""
    return random.choices(_SYM_IDS, weights=_SYM_WEIGHTS, k=3)


def _rng(bet: float) -> dict:
    """
    RNG-функция одного спина.
    Возвращает структуру, понятную slots_listener.spin().
    """
    reels    = _spin_reels()
    win_amount, win_lines = _calc_win(reels, bet)

    symbols_data = [
        {'reel': i, 'id': s, 'label': _SYM_MAP[s]['label']}
        for i, s in enumerate(reels)
    ]

    return {
        'win_amount': win_amount,
        'symbols':    symbols_data,
        'win_lines':  win_lines,
        'multiplier': round(win_amount / bet, 4) if bet > 0 and win_amount > 0 else 0,
        'meta': {
            'reels': reels,
        },
    }


# ── Публичный метод ───────────────────────────────────────────────────────────

def do_spin(user: User, bet_amount: Optional[float] = None,
            force_balance: bool = False) -> dict:
    """
    Публичный метод спина для slots_777.
    Вызывается из slots_api.py.

    Автоматически:
      - Использует FS если есть и игра поддерживает
      - Начисляет выигрыш на реальный баланс
      - Переключается на баланс когда FS = 0
    """
    cfg = game_service.load_config('slots_777') or {}
    return _base_spin(
        user=user,
        game_id='slots_777',
        game_config=cfg,
        rng_func=_rng,
        bet_amount=bet_amount,
        force_balance=force_balance,
    )
