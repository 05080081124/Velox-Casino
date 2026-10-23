"""
app/api/crash_api.py

HTTP / SSE маршруты для Crash.
Вся логика, RNG, балансы — в app/games/crash/listener.py.

Экспортирует crash_bp и start_crash_loop() для совместимости с main.py.
"""
import json
from flask import Blueprint, request, jsonify, session, Response
from app.models.user import User
import app.games.crash.listener as _L

crash_bp = Blueprint('crash', __name__)


def _me() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


# ── SSE ────────────────────────────────────────────────────────────────────

@crash_bp.route('/api/crash/events')
def crash_events():
    q    = _L.subscribe()
    init = _L.get_state()

    def _gen():
        yield f"event:init\ndata:{json.dumps(init, ensure_ascii=False)}\n\n"
        try:
            while True:
                msgs = q.get(timeout=25)
                yield ''.join(msgs) if msgs else ': hb\n\n'
        except GeneratorExit:
            _L.unsubscribe(q)

    return Response(_gen(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


# ── REST ───────────────────────────────────────────────────────────────────

@crash_bp.route('/api/crash/state')
def crash_state():
    return jsonify(_L.get_state())


@crash_bp.route('/api/crash/bet', methods=['POST'])
def crash_bet():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    d      = request.get_json(silent=True) or {}
    amount = float(d.get('bet') or d.get('amount') or 0)
    res    = _L.place_bet(user, amount)
    return (jsonify(res), 400) if 'error' in res else jsonify(res)


@crash_bp.route('/api/crash/cancel', methods=['POST'])
def crash_cancel():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    res = _L.cancel_bet(user)
    return (jsonify(res), 400) if 'error' in res else jsonify(res)


@crash_bp.route('/api/crash/cashout', methods=['POST'])
def crash_cashout():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    res = _L.cashout(user)
    return (jsonify(res), 400) if 'error' in res else jsonify(res)


# ── Совместимость с main.py ─────────────────────────────────────────────────

def start_crash_loop():
    """Вызывается из main.py — делегирует в listener.start()."""
    _L.start()
