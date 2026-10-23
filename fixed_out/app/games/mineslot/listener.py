"""
app/games/mineslot/listener.py — MineSlot game logic.

POST /api/mineslot/spin      — spin
GET  /api/mineslot/config    — config
POST /api/mineslot/buy_bonus — buy bonus

Response format matches localSpin() on the frontend:
  ok, inventory, field_initial, field, pick_actions, tnt_actions,
  chest_results, enchanted_picks, ender_eyes, free_spins_gained,
  fs_multiplier, total_win, balance
"""
from __future__ import annotations
import json, os, random
from typing import Any, List, Optional

from app.models.rng_rtp_system import velox_engine
from app.models.game import GameRound
from app.models.user import User
from app.services.wallet_service import WalletService

_DIR     = os.path.dirname(__file__)
GAME_ID  = 'mineslot'
BLOCK_ORDER = ['dirt', 'dirt2', 'stone', 'ore', 'gold', 'diamond', 'obsidian']


# ── config ───────────────────────────────────────────────────────────────────

def _cfg() -> dict:
    try:
        with open(os.path.join(_DIR, 'config.json'), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ── RNG helpers ───────────────────────────────────────────────────────────────

def _wp(items: list, weights: list, f: float) -> Any:
    total = sum(weights)
    cum = 0.0
    for item, w in zip(items, weights):
        cum += w / total
        if f < cum:
            return item
    return items[-1]


def _row_count(cfg: dict, f: float) -> int:
    w = list(cfg.get('pick_count_weights', [0, 38, 32, 14, 10, 6]))
    return _wp(list(range(len(w))), w, f)


def _pick_type(cfg: dict, bet: float, f: float, bb_mode: bool = False) -> str:
    types   = list(cfg['pickaxes'].keys())
    weights = list(cfg.get('pick_weights', [50, 30, 15, 5]))
    if bb_mode:
        # block bonus: boost better picks
        for i in range(2, len(weights)):
            weights[i] *= 4
    else:
        mult = min(2.0, (bet / 10.0) ** 0.4)
        if mult > 1:
            for i in range(1, len(weights)):
                weights[i] *= 1 + (mult - 1) * 0.3
    return _wp(types, weights, f)


def _is_bonus(cfg: dict, bet: float, f: float, extra: bool) -> bool:
    base = cfg.get('bonus', {}).get('slot_chance_base', 0.06)
    mult = min(2.5, 1.0 + (bet / 50.0) * 0.5)
    return f < base * mult * (2.5 if extra else 1.0)


def _chest_mult(cfg: dict, f: float) -> float:
    mults   = cfg.get('chests', cfg.get('chest', {})).get('multipliers', [1.5, 2.0, 2.5, 3.0, 4.0, 5.0])
    weights = cfg.get('chests', cfg.get('chest', {})).get('weights',
              cfg.get('chests', cfg.get('chest', {})).get('mult_weights', [25, 22, 18, 14, 10, 6]))
    return _wp(mults, weights, f)


def _block_type_for_row(cfg: dict, row: int, f: float) -> str:
    bwr = cfg.get('block_weights_by_row', {})
    # support both dict (row_0..row_5) and list format
    # 7 block types: dirt, dirt2, stone, ore, gold, diamond, obsidian
    if isinstance(bwr, dict):
        key = f'row_{row}'
        weights = bwr.get(key, list(cfg.get('block_weights', [35, 30, 28, 18, 10, 6, 3])))
    elif isinstance(bwr, list) and row < len(bwr):
        weights = bwr[row]
    else:
        weights = list(cfg.get('block_weights', [35, 30, 28, 18, 10, 6, 3]))
    return _wp(BLOCK_ORDER, weights, f)


# ── Inventory generation ──────────────────────────────────────────────────────

def _generate_inventory(cfg: dict, rng, bet: float,
                         extra: bool = False, force_eyes: int = 0,
                         bb_mode: bool = False) -> tuple[list, int]:
    """Returns (slots[15], eye_count). slots[i] matches frontend format."""
    slots: list  = []
    eyes  = 0
    forced = force_eyes

    tnt_ratio  = cfg.get('bonus', {}).get('tnt_ratio',  0.20)
    book_ratio = cfg.get('bonus', {}).get('book_ratio', 0.50)

    for _row in range(3):
        count  = _row_count(cfg, rng.float01())
        avail  = list(range(5))
        pick_cols: set = set()
        for _ in range(min(count, 5)):
            if not avail:
                break
            idx = int(rng.float01() * len(avail))
            pick_cols.add(avail.pop(idx))

        for col in range(5):
            if col in pick_cols:
                if _is_bonus(cfg, bet, rng.float01(), extra):
                    r2 = rng.float01()
                    if r2 < tnt_ratio:
                        slots.append({'type': 'tnt', 'subtype': None, 'damage': 0})
                    elif r2 < tnt_ratio + book_ratio:
                        slots.append({'type': 'enchBook', 'subtype': None, 'damage': 0})
                    else:
                        eyes += 1
                        slots.append({'type': 'enderEye', 'subtype': None, 'damage': 0})
                else:
                    ptype = _pick_type(cfg, bet, rng.float01(), bb_mode)
                    dmg   = cfg['pickaxes'].get(ptype, {}).get('damage', 1)
                    slots.append({'type': 'pick', 'subtype': ptype, 'damage': dmg})
            elif forced > 0:
                forced -= 1
                eyes   += 1
                slots.append({'type': 'enderEye', 'subtype': None, 'damage': 0})
            else:
                slots.append(None)

    return slots, eyes


# ── Field generation ──────────────────────────────────────────────────────────

def _generate_field(cfg: dict, rng) -> list:
    field = []
    for row in range(6):
        r = []
        for _col in range(5):
            bt = _block_type_for_row(cfg, row, rng.float01())
            hp = cfg['blocks'][bt]['hp']
            r.append({'type': bt, 'hp': hp, 'maxHp': hp, 'crackStage': 0, 'cleared': False})
        field.append(r)
    return field


# ── Mining simulation → pick_actions (frontend format) ───────────────────────

def _simulate(cfg: dict, rng, slots: list, field: list, bet: float,
              bb_mode: bool = False, block_multipliers: dict = None) -> dict:
    """
    Simulates all picks & TNT, returns dicts matching localSpin() output:
      pick_actions, tnt_actions, chest_results, enchanted_picks
    Also returns modified `field` (cleared blocks).
    """
    COLS = 5
    ROWS = 6
    # Pre-mark columns that were ALREADY fully cleared before this spin
    # (from previous FS/BB spins) so we don't re-trigger chest rewards.
    col_cleared = [all(field[r][c]['cleared'] for r in range(ROWS)) for c in range(COLS)]

    # Chest multipliers per column
    chest_mults = [_chest_mult(cfg, rng.float01()) for _ in range(COLS)]

    pick_actions:     list = []
    tnt_actions:      list = []
    chest_results:    list = []
    enchanted_picks:  list = []

    # ── enchBook: the RTP engine (mineslot_resolve) already decided the upgrade.
    # Here we just need book_slot for the animation. If a book is present AND
    # mineslot_resolve decided an upgrade, link them. Otherwise book is decorative.
    book_slots = [i for i, s in enumerate(slots) if s and s['type'] == 'enchBook']
    # enchanted_picks is populated by do_spin after mineslot_resolve, passed in via
    # the slots array which was already mutated. We reconstruct it for animation here
    # by scanning for any pick that was upgraded (old_type tracking done in do_spin).

    def _col_chest_check(col: int):
        if not col_cleared[col] and all(field[r][col]['cleared'] for r in range(ROWS)):
            col_cleared[col] = True
            chest_results.append({
                'row':        5,
                'col':        col,
                'multiplier': chest_mults[col],
                'win':        round(bet * chest_mults[col], 2),
            })

    def _hit_block(row: int, col: int, dmg: int, slot_idx: int) -> dict:
        blk = field[row][col]
        blk['hp'] = max(0, blk['hp'] - dmg)
        cleared = blk['hp'] <= 0
        if cleared:
            blk['cleared']    = True
            blk['crackStage'] = 0
        else:
            ratio = blk['hp'] / blk['maxHp'] if blk['maxHp'] > 0 else 0
            blk['crackStage'] = 1 if ratio > 0.66 else (2 if ratio > 0.33 else 3)
        return {'row': row, 'dmg': dmg,
                'crack_stage': blk['crackStage'],
                'cleared': cleared, 'slot_idx': slot_idx}

    # ── Picks: bottom inventory row first (invRow 2→1→0) ────────────────────
    for inv_row in range(2, -1, -1):
        for col in range(COLS):
            si  = inv_row * COLS + col
            it  = slots[si]
            if not it or it['type'] != 'pick':
                continue

            tp  = it['subtype']
            dmg = cfg['pickaxes'].get(tp, {}).get('damage', 1)
            swings_left = dmg

            # find first non-cleared row
            row = next((r for r in range(ROWS) if not field[r][col]['cleared']), None)
            hits = []

            while swings_left > 0 and row is not None and row < ROWS:
                blk = field[row][col]
                if blk['cleared']:
                    row = next((r for r in range(row + 1, ROWS) if not field[r][col]['cleared']), None)
                    continue
                h = _hit_block(row, col, 1, si)
                hits.append(h)
                swings_left -= 1
                if h['cleared']:
                    _col_chest_check(col)
                    row = next((r for r in range(row + 1, ROWS) if not field[r][col]['cleared']), None)

            slots[si] = None  # Слот всегда очищается, если там была кирка
            if hits:
                pick_actions.append({'col': col, 'pick_type': tp,
                                     'damage': dmg, 'hits': hits, 'slot_idx': si})

    # ── TNT ──────────────────────────────────────────────────────────────────
    for si, it in enumerate(slots):
        if not it or it['type'] != 'tnt':
            continue
        col = si % COLS
        row = next((r for r in range(ROWS) if not field[r][col]['cleared']), None)
        if row is None:
            continue

        tnt_hits = []
        dmg_left  = 3
        r         = row
        while dmg_left > 0 and r is not None and r < ROWS:
            blk = field[r][col]
            if blk['cleared']:
                r = next((x for x in range(r + 1, ROWS) if not field[x][col]['cleared']), None)
                continue
            h = _hit_block(r, col, 1, si)
            tnt_hits.append(h)
            dmg_left -= 1
            if h['cleared']:
                _col_chest_check(col)
                r = next((x for x in range(r + 1, ROWS) if not field[x][col]['cleared']), None)

        # Splash: 30% each neighbour
        splash_hits = []
        for sc in [col - 1, col + 1]:
            if 0 <= sc < COLS and rng.float01() < 0.30:
                sr = next((x for x in range(ROWS) if not field[x][sc]['cleared']), None)
                if sr is not None:
                    sh = _hit_block(sr, sc, 1, si)
                    splash_hits.append({**sh, 'col': sc})
                    if sh['cleared']:
                        _col_chest_check(sc)

        slots[si] = None  # Слот всегда очищается после TNT
        if tnt_hits:
            tnt_actions.append({'col': col, 'slot_idx': si,
                                 'hits': tnt_hits, 'splash': splash_hits})

    # ── Block win ─────────────────────────────────────────────────────────────
    block_win = 0.0
    for r in range(ROWS):
        for c in range(COLS):
            blk = field[r][c]
            if blk['cleared']:
                if block_multipliers and f'{r}_{c}' in block_multipliers:
                    # RTP-given per-block multiplier (fraction of bet)
                    mult = block_multipliers[f'{r}_{c}']
                else:
                    # Fallback: use config value_mult or value
                    bcfg = cfg['blocks'].get(blk['type'], {})
                    if 'value_mult' in bcfg:
                        mult = bcfg['value_mult']
                    elif 'value' in bcfg:
                        mult = bcfg['value'] * 0.1
                    else:
                        mult = 0.05
                block_win += bet * mult
    chest_win = sum(cr['win'] for cr in chest_results)
    total_win = round(block_win + chest_win, 2)

    return {
        'pick_actions':    pick_actions,
        'tnt_actions':     tnt_actions,
        'chest_results':   chest_results,
        'enchanted_picks': enchanted_picks,
        'total_win':       total_win,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC: do_spin
# ══════════════════════════════════════════════════════════════════════════════

def do_spin(user: User, bet: float,
            extra_chance: bool = False,
            force_eyes:   int  = 0,
            free_spin:    bool = False,
            bb_mode:      bool = False,
            persisted_field: list = None) -> dict:

    cfg     = _cfg()
    bet_cfg = cfg.get('bet', {})

    # Bet validation — skip min check for free spins (FS bet is fixed by freespin_bets.json)
    cur_min = float(bet_cfg.get('currency_min', {}).get(user.currency,
                    bet_cfg.get('min', 1)))
    if not free_spin and bet < cur_min:
        return {'error': 'e_min', 'min': cur_min}
    if bet > float(bet_cfg.get('max', 10000)):
        return {'error': 'e_max'}
    if not free_spin and user.balance < bet:
        return {'error': 'e_funds'}

    # RNG
    rng, seed_hash = velox_engine.new_round(GAME_ID, user.id)
    order = velox_engine.get_order(GAME_ID, user.id, rng,
                                   base_win_prob=0.72,
                                   max_mult=12.0)

    # Deduct bet / consume free spin
    if not free_spin:
        try:
            WalletService.place_bet(user.id, bet, GAME_ID)
        except Exception as e:
            return {'error': str(e)}
    else:
        try:
            from app.services.freespin_service import FreeSpinService
            r = FreeSpinService.consume_spin(user.id, GAME_ID, user.currency)
            if not r.used:
                return {'error': 'no_free_spins'}
            bet = r.bet_amount  # use the official FS bet
        except Exception:
            pass

    # Generate inventory & field
    slots, eyes = _generate_inventory(cfg, rng, bet, extra_chance, force_eyes, bb_mode)
    initial_slots = json.loads(json.dumps(slots)) # Сохраняем копию до использования в симуляции

    # During FS or BB mode: use persisted field from frontend so server simulates
    # picks against the ACTUAL remaining blocks, not a freshly generated grid.
    # BB mode sends persisted_field too (isFree=False but bb_mode=True).
    if persisted_field and (free_spin or bb_mode):
        field = [[{
            'type':       b['type'],
            'hp':         int(b['hp']),
            'maxHp':      int(b.get('maxHp', b.get('max_hp', cfg['blocks'].get(b['type'],{}).get('hp',1)))),
            'crackStage': int(b.get('crackStage', b.get('crack_stage', 0))),
            'cleared':    bool(b.get('cleared', False)),
        } for b in row] for row in persisted_field]
    else:
        field = _generate_field(cfg, rng)

    field_initial = json.loads(json.dumps(field))  # snapshot before simulation

    # MineSlot RTP resolve: enchantment + block multipliers
    ms_resolve = velox_engine.mineslot_resolve(rng, order, slots, field, bet, bb_mode)

    # Apply enchantment ONLY if enchBook is in slots — RTP engine decides which pick
    pre_enchant_picks = []
    has_book = any(s and s['type'] == 'enchBook' for s in slots)
    if has_book and ms_resolve.get('enchanted_upgrade'):
        eu = ms_resolve['enchanted_upgrade']
        # Engine уже обновил тип в slots[eu['slot_idx']], берем данные из resolve
        book_si = next((i for i, s in enumerate(slots) if s and s['type'] == 'enchBook'), eu['slot_idx'])
        pre_enchant_picks.append({
            'slot_idx':      eu['slot_idx'],
            'book_slot_idx': book_si,
            'old_type':      eu['old_type'],
            'new_type':      eu['new_type'],
        })

    # Очищаем слоты бонусов и глаз, так как они считаются использованными в этом раунде
    for i, s in enumerate(slots):
        if s and s['type'] in ('enchBook', 'enderEye'):
            slots[i] = None

    # Simulate (pass block_multipliers for per-block win scaling)
    sim = _simulate(cfg, rng, slots, field, bet, bb_mode,
                    block_multipliers=ms_resolve['block_multipliers'])
    total_win = sim['total_win']

    # FS gained from ender eyes — support both config formats
    fs_e_new  = cfg.get('bonus', {}).get('ender_eye_freespins', {})  # {3:{count,multiplier}}
    fs_e_old  = cfg.get('bonus', {}).get('ender_eye_fs', {})         # {3: 3}
    fs_m_old  = cfg.get('bonus', {}).get('fs_multipliers', {})       # {3: 1}
    fs_gained = 0
    fs_mult   = 1
    if eyes >= 3:
        key = str(min(eyes, 5))
        if fs_e_new and key in fs_e_new:
            entry     = fs_e_new[key]
            fs_gained = int(entry.get('count', 0)) if isinstance(entry, dict) else int(entry)
            fs_mult   = int(entry.get('multiplier', 1)) if isinstance(entry, dict) else 1
        elif fs_e_old and key in fs_e_old:
            fs_gained = int(fs_e_old.get(key, 0))
            fs_mult   = int(fs_m_old.get(key, 1))

    if free_spin and fs_mult > 1:
        total_win = round(total_win * fs_mult, 2)

    # VIP win bonus (applied per spin)
    vip = _get_vip_benefits(user)
    win_bonus_pct = vip.get('win_bonus_percent', 0)
    if win_bonus_pct > 0 and total_win > 0:
        total_win = round(total_win * (1 + win_bonus_pct / 100), 2)

    # Pay win
    if total_win > 0:
        try:
            if free_spin:
                from app.services.freespin_service import FreeSpinService
                FreeSpinService.pay_fs_win(user.id, GAME_ID, total_win, bet)
            else:
                WalletService.pay_win(user.id, total_win, GAME_ID)
        except Exception as e:
            print(f'[mineslot] pay_win: {e}')

    # Add gained FS to user
    if fs_gained > 0:
        try:
            from app.db import get_db
            with get_db() as db:
                db.execute('UPDATE users SET free_spins = free_spins + %s WHERE id = %s',
                           (fs_gained, user.id))
        except Exception:
            pass

    # Record round
    try:
        velox_engine.close_round(GAME_ID, user.id, bet, total_win)
        GameRound.record(user.id, GAME_ID, bet,
                         multiplier=round(total_win / bet, 4) if bet > 0 and total_win > 0 else 0.0,
                         win=total_win,
                         meta={'currency': user.currency,
                               'mode': 'freespin' if free_spin else 'balance'})
    except Exception as e:
        print(f'[mineslot] record: {e}')

    # New balance & remaining FS
    try:
        new_bal = WalletService.get_balance(user.id)
    except Exception:
        new_bal = user.balance
    try:
        from app.db import get_db
        with get_db() as db:
            db.execute('SELECT free_spins FROM users WHERE id = %s', (user.id,))
            row = db.fetchone()
        fs_rem = int((row or {}).get('free_spins', 0))
    except Exception:
        fs_rem = 0

    # Return in localSpin() format so frontend works unchanged
    return {
        'ok':              True,
        'seed_hash':       seed_hash,
        'inventory':       slots,         # Финальное состояние (пустые слоты)
        'inventory_initial': initial_slots, # Состояние для начала анимации
        'field_initial':   field_initial,
        'field':           field,
        'pick_actions':    sim['pick_actions'],
        'tnt_actions':     sim['tnt_actions'],
        'chest_results':   sim['chest_results'],
        'enchanted_picks': pre_enchant_picks or sim['enchanted_picks'],
        'ender_eyes':      eyes,
        'free_spins_gained': fs_gained,
        'fs_multiplier':   fs_mult,
        'total_win':       total_win,
        'bet':             bet,
        'balance':         round(float(new_bal), 2),
        'free_spin_used':  free_spin,
        'free_spins_remaining': fs_rem,
        'block_multipliers': ms_resolve.get('block_multipliers', {}),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS: VIP benefits for mineslot
# ══════════════════════════════════════════════════════════════════════════════

def _get_vip_benefits(user: User) -> dict:
    """Return active VIP benefits dict, or empty dict if no active VIP."""
    if not user.vip_active:
        return {}
    import os, json as _json
    cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'vip.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            vip_cfg = _json.load(f)
        return vip_cfg.get('tiers', {}).get(user.vip_tier, {}).get('benefits', {})
    except Exception:
        return {}

def _bonus_base_cost_uah(bonus_type: str) -> float:
    """Fixed base prices in UAH."""
    return {'extra_chance': 485.0, 'block_bonus': 700.0}.get(bonus_type, 0.0)

def _vip_discount(tier: str) -> float:
    """Discount multiplier by VIP tier (1.0 = no discount)."""
    return {'bronze': 0.95, 'silver': 0.90, 'gold': 0.82, 'platinum': 0.70}.get(tier, 1.0)


# ══════════════════════════════════════════════════════════════════════════════
#  PUBLIC: buy_bonus
# ══════════════════════════════════════════════════════════════════════════════

def buy_bonus(user: User, bonus_type: str, bet: float) -> dict:
    if bonus_type not in ('extra_chance', 'block_bonus'):
        return {'error': 'unknown_bonus'}

    # Fixed price in UAH, convert to user currency
    base_uah = _bonus_base_cost_uah(bonus_type)
    try:
        from app.services.currency_service import convert
        cost = round(convert(base_uah, 'UAH', user.currency), 2)
    except Exception:
        cost = base_uah  # fallback: treat as UAH

    # VIP discount
    if user.vip_active:
        cost = round(cost * _vip_discount(user.vip_tier), 2)

    if user.balance < cost:
        return {'error': 'e_funds', 'cost': cost}

    try:
        WalletService.place_bet(user.id, cost, f'{GAME_ID}_bonus_{bonus_type}')
    except Exception as e:
        return {'error': str(e)}

    vip = _get_vip_benefits(user)

    if bonus_type == 'extra_chance':
        return {
            'ok':           True,
            'bonus_type':   bonus_type,
            'cost':         cost,
            'balance':      round(WalletService.get_balance(user.id), 2),
            'extra_chance': True,
            # VIP: win bonus % applied to session wins (frontend uses this)
            'vip_win_bonus_pct': vip.get('win_bonus_percent', 0),
        }
    else:  # block_bonus
        # VIP: extra bonus spins on top of base 4
        extra_spins = {'bronze': 0, 'silver': 1, 'gold': 2, 'platinum': 3}.get(user.vip_tier, 0)
        return {
            'ok':           True,
            'bonus_type':   bonus_type,
            'cost':         cost,
            'balance':      round(WalletService.get_balance(user.id), 2),
            'free_spins':   4 + extra_spins,
            'bb_mode':      True,   # high-tier picks + TNT boost
            # VIP perks
            'vip_win_bonus_pct': vip.get('win_bonus_percent', 0),
            'vip_extra_spins':   extra_spins,
        }