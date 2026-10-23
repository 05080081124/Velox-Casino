"""
app/api/roulette_api.py
HTTP / SSE маршруты для Roulette.
"""
import json
from flask import Blueprint, request, jsonify, session, Response
from app.models.user import User
import app.games.roulette.listener as _L

roulette_bp = Blueprint('roulette', __name__)


def _me() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


@roulette_bp.route('/api/roulette/events')
def roulette_events():
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
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@roulette_bp.route('/api/roulette/state')
def roulette_state():
    return jsonify(_L.get_state())


@roulette_bp.route('/api/roulette/bet', methods=['POST'])
def roulette_bet():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    d    = request.get_json(silent=True) or {}
    bets = d.get('bets', [])
    if not bets:
        return jsonify({'error': 'e_min'}), 400
    res = _L.place_bet(user, bets)
    return (jsonify(res), 400) if 'error' in res else jsonify(res)


@roulette_bp.route('/api/roulette/cancel', methods=['POST'])
def roulette_cancel():
    user = _me()
    if not user:
        return jsonify({'error': 'e_auth'}), 401
    res = _L.cancel_bet(user)
    return (jsonify(res), 400) if 'error' in res else jsonify(res)


def start_roulette_loop():
    _L.start()
