"""app/api/sport.py — Blueprint спортивных ставок.

Полный цикл:
  POST /api/sport/bet          — принять ставку, списать баланс, записать в game_history
  POST /api/sport/settle       — расчитать результат (admin), выплатить выигрыш
  POST /api/sport/settle/bulk  — массовый расчёт по match_id
  GET  /api/sport/bets         — история ставок пользователя (из БД)
  GET  /api/sport/bet/<bet_id> — детали одной ставки
"""
import json, os, hashlib
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.wallet_service import WalletService, InsufficientFundsError, WalletError
from app.db import get_db, DB_ENGINE
from app.api.admin import _require_admin

sport_bp = Blueprint('sport', __name__, url_prefix='/api/sport')

_SPORTS_CFG = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'sports.json')
_BUKM_CFG   = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'bukmaking.json')


def _p():
    return '%s' if DB_ENGINE == 'postgresql' else '?'


def _current_user():
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _load_events():
    try:
        from app.services.sport_odds_feed import get_merged_events_list
        ev = get_merged_events_list()
        if ev:
            return ev
    except Exception:
        pass
    for path in (_BUKM_CFG, _SPORTS_CFG):
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            events = data.get('events', [])
            if events:
                return events
        except Exception:
            pass
    return []


def _parse_meta(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _load_limits_cfg():
    try:
        with open(_BUKM_CFG, encoding='utf-8') as f:
            cfg = json.load(f)
        limits = cfg.get('limits', {}) or {}
    except Exception:
        limits = {}
    return {
        'min_stake': float(limits.get('min_stake', 1.0)),
        'max_stake': float(limits.get('max_stake', 100000.0)),
        'max_picks': int(limits.get('max_picks', 20)),
        'max_total_coef': float(limits.get('max_total_coef', 10000.0)),
    }


def _idempotency_key(user_id, body):
    hdr = request.headers.get('X-Idempotency-Key', '').strip()
    if hdr:
        return hdr[:128]
    raw = json.dumps(body or {}, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f'{user_id}:{raw}'.encode('utf-8')).hexdigest()
    return digest[:64]


def _save_idem_response(user_id, idem_key, resp_obj):
    payload = json.dumps(resp_obj or {}, ensure_ascii=False)
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(
                "INSERT INTO sport_idempotency (user_id, idem_key, response, created_at)"
                " VALUES (%s, %s, %s::jsonb, NOW())"
                " ON CONFLICT (user_id, idem_key) DO UPDATE SET response=EXCLUDED.response",
                (user_id, idem_key, payload)
            )
    else:
        with get_db() as db:
            db.execute(
                "INSERT OR REPLACE INTO sport_idempotency (user_id, idem_key, response, created_at)"
                " VALUES (?, ?, ?, datetime('now'))",
                (user_id, idem_key, payload)
            )


def _get_idem_response(user_id, idem_key):
    p = _p()
    with get_db() as db:
        db.execute(
            f"SELECT response FROM sport_idempotency WHERE user_id={p} AND idem_key={p} ORDER BY id DESC LIMIT 1",
            (user_id, idem_key)
        )
        row = db.fetchone()
    if not row:
        return None
    return _parse_meta(row.get('response'))


def _audit(action, actor_user_id=None, entity_id='', payload=None):
    pl = json.dumps(payload or {}, ensure_ascii=False)
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(
                "INSERT INTO sport_audit_logs (actor_user_id, action, entity_id, payload, created_at)"
                " VALUES (%s, %s, %s, %s::jsonb, NOW())",
                (actor_user_id, action, str(entity_id or ''), pl)
            )
    else:
        with get_db() as db:
            db.execute(
                "INSERT INTO sport_audit_logs (actor_user_id, action, entity_id, payload, created_at)"
                " VALUES (?, ?, ?, ?, datetime('now'))",
                (actor_user_id, action, str(entity_id or ''), pl)
            )


def _insert_sport_bet(user_id, data):
    """Записывает ставку в game_history, возвращает id записи."""
    meta_json = json.dumps(data)
    stake     = data.get('stake', 0)
    coef      = data.get('coef', 1)
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(
                "INSERT INTO game_history (user_id, game_id, bet, multiplier, win, meta, created_at)"
                " VALUES (%s, 'sport_bet', %s, %s, 0, %s, NOW()) RETURNING id",
                (user_id, stake, coef, meta_json)
            )
            row = db.fetchone()
        return row['id']
    else:
        with get_db() as db:
            db.execute(
                "INSERT INTO game_history (user_id, game_id, bet, multiplier, win, meta, created_at)"
                " VALUES (?, 'sport_bet', ?, ?, 0, ?, datetime('now'))",
                (user_id, stake, coef, meta_json)
            )
        with get_db() as db:
            db.execute(
                "SELECT id FROM game_history WHERE user_id=? AND game_id='sport_bet'"
                " ORDER BY id DESC LIMIT 1", (user_id,)
            )
            row = db.fetchone()
        return row['id'] if row else 0


def _get_sport_bet(bet_id):
    p = _p()
    with get_db() as db:
        db.execute(
            f"SELECT * FROM game_history WHERE id={p} AND game_id='sport_bet'", (bet_id,)
        )
        row = db.fetchone()
    if not row:
        return None
    r = dict(row)
    r['meta'] = _parse_meta(r.get('meta'))
    return r


def _update_bet_result(bet_id, status, win_amount):
    """Обновляет результат ставки в game_history (SQLite + PostgreSQL)."""
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(
                "UPDATE game_history SET win=%s,"
                " meta = meta || %s::jsonb"
                " WHERE id=%s AND game_id='sport_bet'",
                (win_amount, json.dumps({
                    'result_status': status,
                    'settled_at': datetime.now(timezone.utc).isoformat()
                }), bet_id)
            )
    else:
        # SQLite — merge meta in Python
        with get_db() as db:
            db.execute(
                "SELECT meta FROM game_history WHERE id=? AND game_id='sport_bet'", (bet_id,)
            )
            row = db.fetchone()
        meta = _parse_meta(row['meta'] if row else '{}')
        meta['result_status'] = status
        meta['settled_at']    = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "UPDATE game_history SET win=?, meta=? WHERE id=?",
                (win_amount, json.dumps(meta), bet_id)
            )


def _set_bet_pending(bet_id):
    """Возвращает ставку в pending (для rollback)."""
    if DB_ENGINE == 'postgresql':
        with get_db() as db:
            db.execute(
                "SELECT meta FROM game_history WHERE id=%s AND game_id='sport_bet'",
                (bet_id,)
            )
            row = db.fetchone()
        meta = _parse_meta(row.get('meta') if row else {})
        meta['result_status'] = 'pending'
        meta['rolled_back_at'] = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "UPDATE game_history SET win=%s, meta=%s::jsonb WHERE id=%s AND game_id='sport_bet'",
                (0, json.dumps(meta), bet_id)
            )
    else:
        with get_db() as db:
            db.execute(
                "SELECT meta FROM game_history WHERE id=? AND game_id='sport_bet'",
                (bet_id,)
            )
            row = db.fetchone()
        meta = _parse_meta(row['meta'] if row else '{}')
        meta['result_status'] = 'pending'
        meta['rolled_back_at'] = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.execute(
                "UPDATE game_history SET win=?, meta=? WHERE id=? AND game_id='sport_bet'",
                (0, json.dumps(meta), bet_id)
            )


def _debit_balance(user_id, amount, reason='sport_settle_rollback', meta=None):
    """Списывает средства для rollback расчёта, с логом в transactions."""
    if amount <= 0:
        return WalletService.get_balance(user_id)
    p = _p()
    tx_meta = json.dumps({'reason': reason, **(meta or {})}, ensure_ascii=False)
    with get_db() as db:
        db.execute(f"SELECT balance FROM users WHERE id={p}", (user_id,))
        row = db.fetchone()
        if not row:
            raise WalletError('Пользователь не найден')
        bal = float(row.get('balance', 0))
        if bal < amount:
            raise InsufficientFundsError('Недостаточно средств для rollback')
        db.execute(f"UPDATE users SET balance = balance - {p} WHERE id = {p}", (amount, user_id))
        if DB_ENGINE == 'postgresql':
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (%s, 'rollback', %s, 'completed', %s::jsonb, NOW())",
                (user_id, amount, tx_meta)
            )
        else:
            db.execute(
                "INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                " VALUES (?, 'rollback', ?, 'completed', ?, datetime('now'))",
                (user_id, amount, tx_meta)
            )
        db.execute(f"SELECT balance FROM users WHERE id={p}", (user_id,))
        row2 = db.fetchone()
    return round(float(row2.get('balance', 0)), 2)


# ── POST /api/sport/bet ────────────────────────────────────────────────────────

@sport_bp.route('/bet', methods=['POST'])
def place_bet():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    body   = request.get_json(silent=True) or {}
    amount = float(body.get('amount', 0))
    picks  = body.get('picks', [])
    mode   = body.get('mode', 'single')
    limits = _load_limits_cfg()

    if amount <= 0:
        return jsonify({'error': 'Сумма ставки должна быть больше нуля'}), 400
    if amount < limits['min_stake']:
        return jsonify({'error': f"Минимальная ставка: {limits['min_stake']:.2f}"}), 400
    if amount > limits['max_stake']:
        return jsonify({'error': f"Максимальная ставка: {limits['max_stake']:.2f}"}), 400
    if not picks:
        return jsonify({'error': 'Купон пуст'}), 400
    if len(picks) > limits['max_picks']:
        return jsonify({'error': f"Слишком много исходов в купоне (max {limits['max_picks']})"}), 400

    idem_key = _idempotency_key(user.id, body)
    idem_saved = _get_idem_response(user.id, idem_key)
    if idem_saved:
        idem_saved['idempotent_replay'] = True
        return jsonify(idem_saved)

    events = {str(e['id']): e for e in _load_events() if e.get('enabled', True)}
    for pick in picks:
        mid = str(pick.get('matchId', ''))
        if mid and mid not in events:
            return jsonify({'error': f'Событие {mid} не найдено или недоступно'}), 400

    total_coef = 1.0
    for pick in picks:
        try:
            total_coef *= float(pick.get('odd', 1))
        except (TypeError, ValueError):
            pass
    total_coef    = round(total_coef, 4)
    if total_coef > limits['max_total_coef']:
        return jsonify({'error': f"Слишком большой коэффициент (max {limits['max_total_coef']})"}), 400
    potential_win = round(amount * total_coef, 2)

    # Enrich picks with event data snapshot for history
    enriched_picks = []
    for pick in picks:
        mid = str(pick.get('matchId', ''))
        ev  = events.get(mid, {})
        enriched_picks.append({
            'matchId':      mid,
            'market':       pick.get('market', ''),
            'title':        pick.get('title', ''),
            'odd':          float(pick.get('odd', 1)),
            'home_team':    ev.get('home_team', ''),
            'away_team':    ev.get('away_team', ''),
            'league':       ev.get('league', ''),
            'match_status': ev.get('match_status', 'planned'),
            'starts_at':    ev.get('starts_at', ''),
            'match_td_utc': ev.get('match_td_utc', ''),
            'video_url':    ev.get('match_center', {}).get('video_embed_url', ''),
            'result_status': 'pending',
        })

    bet_meta = {
        'type':          'sport_bet',
        'mode':          mode,
        'picks':         enriched_picks,
        'coef':          total_coef,
        'stake':         amount,
        'potential_win': potential_win,
        'currency':      user.currency,
        'result_status': 'pending',
    }

    try:
        new_balance = WalletService.place_bet(user.id, amount, game_id='sport_bet')
    except InsufficientFundsError:
        return jsonify({'error': 'Недостаточно средств на балансе'}), 400
    except WalletError as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

    bet_id = 0
    try:
        bet_id = _insert_sport_bet(user.id, bet_meta)
    except Exception:
        pass

    resp = {
        'success':       True,
        'bet_id':        bet_id,
        'balance':       new_balance,
        'amount':        amount,
        'coef':          total_coef,
        'potential_win': potential_win,
        'currency':      user.currency,
        'result_status': 'pending',
    }
    _save_idem_response(user.id, idem_key, resp)
    _audit('sport_bet_placed', actor_user_id=user.id, entity_id=bet_id, payload={
        'amount': amount, 'coef': total_coef, 'picks': len(picks), 'mode': mode
    })
    return jsonify(resp)


# ── GET /api/sport/bets ────────────────────────────────────────────────────────

@sport_bp.route('/bets', methods=['GET'])
def get_bets():
    """История ставок текущего пользователя из БД."""
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    limit  = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    p      = _p()

    with get_db() as db:
        db.execute(
            f"SELECT * FROM game_history WHERE user_id={p} AND game_id='sport_bet'"
            f" ORDER BY created_at DESC LIMIT {int(limit)} OFFSET {int(offset)}",
            (user.id,)
        )
        rows = db.fetchall()

    bets = []
    for row in rows:
        r    = dict(row)
        meta = _parse_meta(r.get('meta'))
        stake = float(r.get('bet', 0))
        coef  = float(r.get('multiplier') or 1)
        bets.append({
            'id':            r['id'],
            'bet_id':        r['id'],
            'stake':         stake,
            'coef':          coef,
            'potential_win': round(stake * coef, 2),
            'win':           float(r.get('win', 0)),
            'currency':      meta.get('currency', user.currency),
            'mode':          meta.get('mode', 'single'),
            'picks':         meta.get('picks', []),
            'result_status': meta.get('result_status', 'pending'),
            'created_at':    str(r.get('created_at', '')),
        })

    return jsonify({'bets': bets, 'total': len(bets)})


# ── GET /api/sport/bet/<bet_id> ────────────────────────────────────────────────

@sport_bp.route('/bet/<int:bet_id>', methods=['GET'])
def get_bet(bet_id):
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    row = _get_sport_bet(bet_id)
    if not row or row.get('user_id') != user.id:
        return jsonify({'error': 'Ставка не найдена'}), 404

    meta  = row.get('meta', {})
    stake = float(row.get('bet', 0))
    coef  = float(row.get('multiplier') or 1)
    return jsonify({
        'id':            row['id'],
        'stake':         stake,
        'coef':          coef,
        'potential_win': round(stake * coef, 2),
        'win':           float(row.get('win', 0)),
        'currency':      meta.get('currency', user.currency),
        'mode':          meta.get('mode', 'single'),
        'picks':         meta.get('picks', []),
        'result_status': meta.get('result_status', 'pending'),
        'created_at':    str(row.get('created_at', '')),
    })


# ── POST /api/sport/settle  (admin) ───────────────────────────────────────────

@sport_bp.route('/settle', methods=['POST'])
def settle_bet():
    """
    Расчёт результата одной ставки.
    Требует X-Admin-Key заголовок или session['is_admin'].

    Body: { "bet_id": 123, "result": "won"|"lost"|"void", "win_amount": 250.00 }
      won  → выплачивает win_amount (или stake*coef если не передан)
      lost → деньги не возвращаются
      void → возвращает stake (отмена/форс-мажор)
    """
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403

    body       = request.get_json(silent=True) or {}
    bet_id     = int(body.get('bet_id', 0))
    result     = body.get('result', '').lower()
    win_amount = body.get('win_amount')

    if not bet_id:
        return jsonify({'error': 'bet_id обязателен'}), 400
    if result not in ('won', 'lost', 'void'):
        return jsonify({'error': 'result должен быть won | lost | void'}), 400

    row = _get_sport_bet(bet_id)
    if not row:
        return jsonify({'error': 'Ставка не найдена'}), 404

    meta = row.get('meta', {})
    if meta.get('result_status') not in ('pending', None, ''):
        return jsonify({'error': f'Ставка уже расчитана: {meta.get("result_status")}'}), 409

    user_id = row['user_id']
    stake   = float(row.get('bet', 0))
    coef    = float(row.get('multiplier') or 1)

    if result == 'won':
        payout = round(float(win_amount), 2) if win_amount is not None else round(stake * coef, 2)
        try:
            new_balance = WalletService.pay_win(
                user_id, payout, 'sport_bet',
                meta={'bet_id': bet_id, 'coef': coef, 'type': 'sport_win'}
            )
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        credited = payout

    elif result == 'void':
        try:
            new_balance = WalletService.cancel_bet(user_id, stake, 'sport_bet')
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        credited = stake

    else:  # lost
        new_balance = WalletService.get_balance(user_id)
        credited    = 0.0

    try:
        _update_bet_result(bet_id, result, credited)
    except Exception:
        pass

    resp = {
        'success':     True,
        'bet_id':      bet_id,
        'result':      result,
        'credited':    credited,
        'new_balance': new_balance,
    }
    _audit('sport_bet_settled_manual', actor_user_id=session.get('user_id'), entity_id=bet_id, payload=resp)
    return jsonify(resp)


# ── POST /api/sport/settle/bulk  (admin) ──────────────────────────────────────

@sport_bp.route('/settle/bulk', methods=['POST'])
def settle_bulk():
    """
    Массовый расчёт всех pending-ставок по match_id.

    Body:
    {
      "match_id": "match_1",
      "pick_result": {
        "winner/Real Madrid": "won",
        "winner/Man City":    "lost",
        "total/over":         "void"
      }
    }
    """
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403

    body        = request.get_json(silent=True) or {}
    match_id    = str(body.get('match_id', ''))
    pick_result = body.get('pick_result', {})

    if not match_id:
        return jsonify({'error': 'match_id обязателен'}), 400
    if not pick_result:
        return jsonify({'error': 'pick_result обязателен'}), 400

    with get_db() as db:
        db.execute(
            "SELECT * FROM game_history WHERE game_id='sport_bet'"
            " ORDER BY created_at DESC LIMIT 1000"
        )
        rows = db.fetchall()

    settled = []
    errors  = []

    for row in rows:
        r    = dict(row)
        meta = _parse_meta(r.get('meta'))

        if meta.get('result_status') not in ('pending', None, ''):
            continue

        picks    = meta.get('picks', [])
        relevant = [pk for pk in picks if str(pk.get('matchId')) == match_id]
        if not relevant:
            continue

        bet_id  = r['id']
        user_id = r['user_id']
        stake   = float(r.get('bet', 0))
        coef    = float(r.get('multiplier') or 1)

        # Resolve outcome per pick
        pick_outcomes = []
        for pk in picks:
            key = f"{pk.get('market','')}/{pk.get('title','')}"
            if str(pk.get('matchId')) == match_id:
                pick_outcomes.append(pick_result.get(key, 'pending'))
            else:
                pick_outcomes.append('pending')

        if 'pending' in pick_outcomes:
            continue  # other picks not yet settled

        # Determine final result
        if all(o == 'void' for o in pick_outcomes):
            final_result = 'void'
            payout       = stake
        elif 'lost' in pick_outcomes:
            final_result = 'lost'
            payout       = 0.0
        else:
            # Won (possibly with some void picks — recalc coef)
            active = [pk for pk, o in zip(picks, pick_outcomes) if o == 'won']
            if not active:
                final_result = 'void'
                payout       = stake
            else:
                new_coef = 1.0
                for pk in active:
                    new_coef *= float(pk.get('odd', 1))
                payout       = round(stake * new_coef, 2)
                final_result = 'won'

        try:
            if final_result == 'won':
                WalletService.pay_win(user_id, payout, 'sport_bet',
                                      meta={'bet_id': bet_id, 'type': 'sport_win'})
            elif final_result == 'void':
                WalletService.cancel_bet(user_id, stake, 'sport_bet')
            _update_bet_result(bet_id, final_result, payout if final_result != 'lost' else 0)
            settled.append({'bet_id': bet_id, 'result': final_result, 'payout': payout})
        except Exception as e:
            errors.append({'bet_id': bet_id, 'error': str(e)})

    resp = {
        'success':  True,
        'settled':  settled,
        'errors':   errors,
        'match_id': match_id,
    }
    _audit('sport_bet_settled_bulk', actor_user_id=session.get('user_id'), entity_id=match_id, payload={
        'settled_count': len(settled), 'error_count': len(errors)
    })
    return jsonify(resp)


def run_auto_settle(actor_user_id=None):
    """Внутренний авто-расчёт pending ставок по завершённым матчам."""
    events = {str(e.get('id')): e for e in _load_events() if e.get('enabled', True)}

    with get_db() as db:
        db.execute(
            "SELECT * FROM game_history WHERE game_id='sport_bet'"
            " ORDER BY created_at DESC LIMIT 2000"
        )
        rows = db.fetchall()

    settled = []
    skipped = 0
    errors = []
    for row in rows:
        r = dict(row)
        meta = _parse_meta(r.get('meta'))
        if meta.get('result_status') not in ('pending', None, ''):
            continue
        picks = meta.get('picks', []) or []
        if not picks:
            skipped += 1
            continue

        pick_outcomes = []
        ready = True
        for pk in picks:
            mid = str(pk.get('matchId', ''))
            ev = events.get(mid) or {}
            st = str(ev.get('match_status', '')).lower()
            if st not in ('finished', 'ended', 'settled', 'cancelled'):
                ready = False
                break
            key = f"{pk.get('market','')}/{pk.get('title','')}"
            out_map = _parse_meta(ev.get('match_center', {}).get('pick_result', {}))
            out = str(out_map.get(key, 'pending')).lower()
            if out not in ('won', 'lost', 'void'):
                ready = False
                break
            pick_outcomes.append(out)
        if not ready or 'pending' in pick_outcomes:
            skipped += 1
            continue

        bet_id = r['id']
        user_id = r['user_id']
        stake = float(r.get('bet', 0))
        coef = float(r.get('multiplier') or 1)

        if all(o == 'void' for o in pick_outcomes):
            final_result = 'void'
            payout = stake
        elif 'lost' in pick_outcomes:
            final_result = 'lost'
            payout = 0.0
        else:
            active = [pk for pk, o in zip(picks, pick_outcomes) if o == 'won']
            if not active:
                final_result = 'void'
                payout = stake
            else:
                new_coef = 1.0
                for pk in active:
                    new_coef *= float(pk.get('odd', 1))
                payout = round(stake * new_coef, 2)
                final_result = 'won'

        try:
            if final_result == 'won':
                WalletService.pay_win(user_id, payout, 'sport_bet',
                                      meta={'bet_id': bet_id, 'coef': coef, 'type': 'sport_win_auto'})
            elif final_result == 'void':
                WalletService.cancel_bet(user_id, stake, 'sport_bet')
            _update_bet_result(bet_id, final_result, payout if final_result != 'lost' else 0)
            settled.append({'bet_id': bet_id, 'result': final_result, 'payout': payout})
        except Exception as e:
            errors.append({'bet_id': bet_id, 'error': str(e)})

    _audit('sport_bet_settled_auto', actor_user_id=actor_user_id, entity_id='auto', payload={
        'settled_count': len(settled), 'skipped_count': skipped, 'error_count': len(errors)
    })
    return {
        'success': True,
        'settled': settled,
        'skipped': skipped,
        'errors': errors,
    }


@sport_bp.route('/settle/auto', methods=['POST'])
def settle_auto():
    """
    Авто-расчёт pending ставок по завершённым матчам.
    Ожидает в event.match_center.pick_result словарь:
      {"market/title":"won|lost|void"}
    """
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403
    return jsonify(run_auto_settle(actor_user_id=session.get('user_id')))


# ── GET /api/sport/pending  (admin) ───────────────────────────────────────────

@sport_bp.route('/pending', methods=['GET'])
def pending_bets():
    """
    Возвращает все ставки со статусом pending, сгруппированные по match_id.
    Используется admin-панелью для settle UI.
    """
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403

    with get_db() as db:
        db.execute(
            "SELECT * FROM game_history WHERE game_id='sport_bet'"
            " ORDER BY created_at DESC LIMIT 2000"
        )
        rows = db.fetchall()

    by_match = {}
    for row in rows:
        r    = dict(row)
        meta = _parse_meta(r.get('meta'))
        if meta.get('result_status') not in ('pending', None, ''):
            continue
        picks = meta.get('picks', [])
        for pk in picks:
            mid = str(pk.get('matchId', ''))
            if not mid:
                continue
            if mid not in by_match:
                by_match[mid] = {
                    'match_id':  mid,
                    'home_team': pk.get('home_team', ''),
                    'away_team': pk.get('away_team', ''),
                    'league':    pk.get('league', ''),
                    'bets_count': 0,
                    'total_stake': 0.0,
                    'markets': set(),
                    'picks_sample': []
                }
            by_match[mid]['bets_count']  += 1
            by_match[mid]['total_stake'] += float(r.get('bet', 0))
            market_key = f"{pk.get('market','')}/{pk.get('title','')}"
            by_match[mid]['markets'].add(market_key)
            if len(by_match[mid]['picks_sample']) < 10:
                by_match[mid]['picks_sample'].append({
                    'bet_id': r['id'],
                    'market': pk.get('market', ''),
                    'title':  pk.get('title', ''),
                    'odd':    pk.get('odd', 1),
                    'stake':  float(r.get('bet', 0)),
                    'potential_win': float(r.get('bet', 0)) * float(r.get('multiplier') or 1)
                })

    result = []
    for item in by_match.values():
        item['markets']      = sorted(item['markets'])
        item['total_stake']  = round(item['total_stake'], 2)
        result.append(item)

    result.sort(key=lambda x: x['bets_count'], reverse=True)
    return jsonify({'success': True, 'matches': result})


@sport_bp.route('/audit', methods=['GET'])
def sport_audit():
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403
    limit = min(int(request.args.get('limit', 200)), 1000)
    action = request.args.get('action', '').strip().lower()
    q = request.args.get('q', '').strip().lower()
    with get_db() as db:
        db.execute(
            f"SELECT * FROM sport_audit_logs ORDER BY created_at DESC LIMIT {int(max(limit, 500))}"
        )
        rows = db.fetchall()
    logs = [dict(r) for r in rows]
    if action:
        logs = [x for x in logs if action in str(x.get('action', '')).lower()]
    if q:
        logs = [x for x in logs if q in (f"{x.get('action','')} {x.get('entity_id','')} {x.get('payload','')}").lower()]
    return jsonify({'success': True, 'logs': logs[:limit], 'total': len(logs)})


@sport_bp.route('/limits', methods=['GET'])
def get_limits():
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403
    return jsonify({'success': True, 'limits': _load_limits_cfg()})


@sport_bp.route('/limits', methods=['POST'])
def set_limits():
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403
    body = request.get_json(silent=True) or {}
    limits = body.get('limits', body)
    if not isinstance(limits, dict):
        return jsonify({'error': 'Ожидается JSON объект limits'}), 400
    try:
        with open(_BUKM_CFG, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg['limits'] = {
        'min_stake': float(limits.get('min_stake', 1.0)),
        'max_stake': float(limits.get('max_stake', 100000.0)),
        'max_picks': int(limits.get('max_picks', 20)),
        'max_total_coef': float(limits.get('max_total_coef', 10000.0)),
    }
    tmp = _BUKM_CFG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _BUKM_CFG)
    _audit('sport_limits_updated', actor_user_id=session.get('user_id'), entity_id='limits', payload=cfg['limits'])
    return jsonify({'success': True, 'limits': cfg['limits']})


@sport_bp.route('/settle/rollback', methods=['POST'])
def settle_rollback():
    """
    Откат расчёта одной ставки обратно в pending.
    Body: { "bet_id": 123 }
    """
    if not _require_admin():
        return jsonify({'error': 'Доступ запрещён'}), 403
    body = request.get_json(silent=True) or {}
    bet_id = int(body.get('bet_id', 0))
    if not bet_id:
        return jsonify({'error': 'bet_id обязателен'}), 400

    row = _get_sport_bet(bet_id)
    if not row:
        return jsonify({'error': 'Ставка не найдена'}), 404
    meta = row.get('meta', {})
    cur_status = str(meta.get('result_status', 'pending')).lower()
    if cur_status in ('pending', '', 'none'):
        return jsonify({'error': 'Ставка уже pending'}), 409
    if meta.get('rollback_locked') is True:
        return jsonify({'error': 'Rollback запрещён для этой ставки'}), 409

    user_id = row['user_id']
    stake = float(row.get('bet', 0))
    credited = float(row.get('win', 0))
    to_debit = credited if cur_status == 'won' else (stake if cur_status == 'void' else 0.0)
    try:
        new_balance = _debit_balance(user_id, to_debit, reason='sport_settle_rollback', meta={
            'bet_id': bet_id,
            'prev_status': cur_status
        }) if to_debit > 0 else WalletService.get_balance(user_id)
        _set_bet_pending(bet_id)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    _audit('sport_bet_settle_rollback', actor_user_id=session.get('user_id'), entity_id=bet_id, payload={
        'prev_status': cur_status, 'debited': to_debit, 'new_balance': new_balance
    })
    return jsonify({
        'success': True,
        'bet_id': bet_id,
        'prev_status': cur_status,
        'debited': to_debit,
        'new_balance': new_balance,
        'result_status': 'pending',
    })
