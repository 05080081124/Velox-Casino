"""
app/api/freespin.py — API для Free Spins.

Endpoints:
  GET  /api/freespin/status          — проверка FS для текущего юзера и игры
  POST /api/freespin/spin            — выполнить один фриспин (списать FS, не снимать деньги)
  POST /api/freespin/win             — начислить выигрыш от фриспина на баланс
  GET  /api/freespin/history         — история фриспинов
  GET  /api/freespin/stats           — статистика фриспинов
  GET  /api/freespin/bets            — конфиг фиксированных ставок по валютам

Флоу для слота с поддержкой FS:
  1. При входе в игру — GET /api/freespin/status?game_id=<id>
     → {mode: 'freespin'|'balance', fs_remaining, fs_bet}
  2. Если mode='freespin': перед спином — POST /api/freespin/spin {game_id}
     → {ok, fs_remaining, bet_amount, mode}  — ставка уже "сделана" (FS списан)
  3. Игра выполняет RNG, считает выигрыш
  4. POST /api/freespin/win {game_id, win_amount, bet_amount}
     → {ok, balance, win_amount}
  5. Если fs_remaining=0 → mode переключается на 'balance'
"""
import os, json
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.freespin_service import FreeSpinService, get_fs_bet, game_supports_fs
from app.services.game_service import game_service

freespin_bp = Blueprint('freespin', __name__)

_REGISTRY = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'games.json')
_FS_BETS_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'freespin_bets.json')


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _load_game_config(game_id: str) -> dict:
    """Загружает конфиг игры по ID."""
    games = game_service.get_all_games(_REGISTRY)
    return next((g for g in games if g['id'] == game_id), {})


# ── Статус FS для игры ────────────────────────────────────────────────────────

@freespin_bp.route('/api/freespin/status')
def fs_status():
    """
    Проверяет статус FS для пользователя и конкретной игры.
    Вызывается при входе в игру.
    ?game_id=slots_777
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    game_id = request.args.get('game_id', '')
    game_cfg = _load_game_config(game_id) if game_id else {}

    status = FreeSpinService.get_game_mode(user.id, game_cfg, user.currency)
    status['user_fs_total'] = user.free_spins

    return jsonify(status)


# ── Выполнить один фриспин ────────────────────────────────────────────────────

@freespin_bp.route('/api/freespin/spin', methods=['POST'])
def fs_spin():
    """
    Выполняет один фриспин:
    - Списывает 1 FS
    - НЕ снимает деньги с баланса
    - Возвращает bet_amount (фиксированная ставка) и оставшиеся FS
    
    Body: {game_id: str}
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    data    = request.get_json() or {}
    game_id = data.get('game_id', '')

    if not game_id:
        return jsonify({'error': 'game_id обязателен'}), 400

    # Проверяем что игра поддерживает FS
    game_cfg = _load_game_config(game_id)
    if not game_cfg:
        return jsonify({'error': 'Игра не найдена'}), 404

    if not game_supports_fs(game_cfg):
        return jsonify({'error': 'Игра не поддерживает фриспины', 'supports_fs': False}), 400

    # Выполняем фриспин
    result = FreeSpinService.consume_spin(user.id, game_id, user.currency)

    if not result.used:
        # FS кончились — переключаем на баланс
        return jsonify({
            'ok': True,
            'used_freespin': False,
            'mode': 'balance',
            'fs_remaining': 0,
            'bet_amount': result.bet_amount,
            'balance': user.balance,
            'message': 'Фриспины закончились. Переключение на баланс.',
        })

    return jsonify({
        'ok': True,
        'used_freespin': True,
        'mode': result.mode,
        'fs_remaining': result.remaining_fs,
        'bet_amount': result.bet_amount,
        'currency': user.currency,
        'balance': user.balance,  # Баланс НЕ изменился (FS не снимает деньги)
    })


# ── Начислить выигрыш от фриспина ─────────────────────────────────────────────

@freespin_bp.route('/api/freespin/win', methods=['POST'])
def fs_win():
    """
    Начисляет выигрыш от фриспина на реальный баланс.
    Вызывается после RNG слота.
    
    Body: {game_id: str, win_amount: float, bet_amount: float, meta?: dict}
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    data       = request.get_json() or {}
    game_id    = data.get('game_id', '')
    win_amount = float(data.get('win_amount', 0))
    bet_amount = float(data.get('bet_amount', get_fs_bet(user.currency)))
    extra_meta = data.get('meta', {})

    if not game_id:
        return jsonify({'error': 'game_id обязателен'}), 400

    if win_amount < 0:
        return jsonify({'error': 'win_amount не может быть отрицательным'}), 400

    new_balance = FreeSpinService.pay_fs_win(
        user_id=user.id,
        game_id=game_id,
        win_amount=win_amount,
        bet_amount=bet_amount,
        meta=extra_meta or None,
    )

    fs_remaining = FreeSpinService.check_user_fs(user.id)

    return jsonify({
        'ok': True,
        'win_amount': win_amount,
        'balance': new_balance,
        'fs_remaining': fs_remaining,
        'mode': 'freespin' if fs_remaining > 0 else 'balance',
    })


# ── История фриспинов ─────────────────────────────────────────────────────────

@freespin_bp.route('/api/freespin/history')
def fs_history():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    limit = min(int(request.args.get('limit', 20)), 100)
    history = FreeSpinService.get_fs_history(user.id, limit)
    return jsonify({'history': history, 'count': len(history)})


# ── Статистика фриспинов ──────────────────────────────────────────────────────

@freespin_bp.route('/api/freespin/stats')
def fs_stats():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    stats = FreeSpinService.get_fs_stats(user.id)
    return jsonify(stats)


# ── Конфиг ставок по валютам ──────────────────────────────────────────────────

@freespin_bp.route('/api/freespin/bets')
def fs_bets():
    """Публичный endpoint — возвращает таблицу фиксированных ставок FS."""
    try:
        with open(_FS_BETS_PATH, encoding='utf-8') as f:
            cfg = json.load(f)
        return jsonify({'bets': cfg.get('bets', {})})
    except Exception:
        return jsonify({'bets': {'UAH': 1.0, 'USD': 0.05, 'EUR': 0.05}}), 200
