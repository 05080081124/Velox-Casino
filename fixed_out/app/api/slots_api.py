"""
app/api/slots_api.py — Универсальный API для слотов.

Поддерживает любой слот с тегом 'slots' в config.json.
Слоты с тегом 'fs' автоматически получают поддержку фриспинов.

Endpoints:
  GET  /api/slots/<game_id>/status  — получить статус (баланс, FS, режим)
  POST /api/slots/<game_id>/spin    — выполнить спин
"""
import os
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.game_service import game_service
from app.services.freespin_service import FreeSpinService, game_supports_fs

slots_bp = Blueprint('slots', __name__)

_REGISTRY = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'games.json')


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _load_game(game_id: str) -> dict | None:
    games = game_service.get_all_games(_REGISTRY)
    return next((g for g in games if g['id'] == game_id), None)


def _load_listener(game_id: str):
    """Загружает listener.py для слота."""
    mod = game_service.load_module(game_id)
    return mod


# ── Статус слота ──────────────────────────────────────────────────────────────

@slots_bp.route('/api/slots/<game_id>/status')
def slot_status(game_id: str):
    """
    Возвращает текущее состояние игры для пользователя:
      - mode: 'freespin' | 'balance'
      - fs_remaining: кол-во оставшихся FS
      - fs_bet: фиксированная ставка FS
      - balance: текущий баланс
      - supports_fs: поддерживает ли игра FS
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    game_cfg = _load_game(game_id)
    if not game_cfg:
        return jsonify({'error': 'Игра не найдена'}), 404

    # Проверяем что это слот
    if 'slots' not in (game_cfg.get('tags') or []) and game_cfg.get('category') != 'slots':
        return jsonify({'error': 'Это не слот'}), 400

    mode_info = FreeSpinService.get_game_mode(user.id, game_cfg, user.currency)

    return jsonify({
        **mode_info,
        'balance':  round(user.balance, 2),
        'currency': user.currency,
        'game_id':  game_id,
    })


# ── Спин ─────────────────────────────────────────────────────────────────────

@slots_bp.route('/api/slots/<game_id>/spin', methods=['POST'])
def slot_spin(game_id: str):
    """
    Выполняет один спин слота.

    Body (JSON, все поля опциональны):
      {
        "bet_amount": 10.0,      // ставка (игнорируется если mode=freespin)
        "force_balance": false   // принудительно использовать баланс
      }

    Ответ:
      {
        "ok": true,
        "mode": "freespin",        // режим ЭТОГО спина
        "mode_next": "freespin",   // режим СЛЕДУЮЩЕГО спина
        "bet_amount": 1.0,
        "win_amount": 5.0,
        "balance": 150.0,
        "fs_remaining": 9,
        "symbols": [...],
        "win_lines": [...],
        "multiplier": 5.0
      }
    """
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    game_cfg = _load_game(game_id)
    if not game_cfg:
        return jsonify({'error': 'Игра не найдена'}), 404

    if not game_cfg.get('active', True):
        return jsonify({'error': 'Игра недоступна'}), 403

    data          = request.get_json() or {}
    bet_amount    = data.get('bet_amount')
    force_balance = bool(data.get('force_balance', False))

    if bet_amount is not None:
        try:
            bet_amount = float(bet_amount)
        except (TypeError, ValueError):
            return jsonify({'error': 'Некорректная ставка'}), 400

    # Загружаем listener конкретного слота
    mod = _load_listener(game_id)

    if mod and hasattr(mod, 'do_spin'):
        # Слот имеет собственный listener с do_spin()
        result = mod.do_spin(user, bet_amount=bet_amount, force_balance=force_balance)
    elif mod and hasattr(mod, 'spin'):
        # Устаревший интерфейс
        result = mod.spin(user, bet_amount or 0)
    else:
        # Фолбек: запускаем через базовый slots_listener с дефолтным RNG
        try:
            from app.games.slots_base.slots_listener import spin as base_spin
            import random

            def _default_rng(bet: float) -> dict:
                win = round(bet * random.uniform(0, 3), 2) if random.random() < 0.35 else 0.0
                return {'win_amount': win, 'symbols': [], 'win_lines': [], 'meta': {}}

            result = base_spin(
                user=user,
                game_id=game_id,
                game_config=game_cfg,
                rng_func=_default_rng,
                bet_amount=bet_amount,
                force_balance=force_balance,
            )
        except Exception as e:
            return jsonify({'error': f'Ошибка игры: {e}'}), 500

    if 'error' in result:
        # Маппинг кодов ошибок
        error_map = {
            'e_funds': 'Недостаточно средств',
            'e_min':   f"Минимальная ставка {result.get('min', '')}",
            'e_max':   f"Максимальная ставка {result.get('max', '')}",
        }
        msg = error_map.get(result['error'], result['error'])
        return jsonify({'error': msg, 'code': result['error']}), 400

    return jsonify(result)
