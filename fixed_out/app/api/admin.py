"""app/api/admin.py — Admin API с поддержкой ролей.

Авторизация:
  - Заголовок X-Admin-Key: <ADMIN_KEY из .env>
  - Или session['is_admin'] == True
  - Дополнительно: role-based access через /config/perms.json

Эндпоинты:
  GET/POST  /api/admin/config/bukmaking   — матчи и настройки букмейкинга
  GET/POST  /api/admin/config/banners     — баннеры
  GET/POST  /api/admin/config/sidebar     — сайдбар
  POST      /api/admin/config/bukmaking/event        — добавить/обновить матч
  DELETE    /api/admin/config/bukmaking/event/<id>   — удалить матч
  POST      /api/admin/config/bukmaking/event/<id>/odds — обновить коэффициенты
  POST      /api/admin/config/banners/page           — добавить/обновить баннер
  DELETE    /api/admin/config/banners/page/<id>      — удалить баннер

  # Sport bets
  GET       /api/admin/sport/bets         — все ставки на спорт

  # Role-aware user management (delegates to roles.py for logic)
  POST      /api/admin/user/<uid>/balance  — скорректировать баланс
  POST      /api/admin/user/<uid>/ban      — заблокировать пользователя
  POST      /api/admin/user/<uid>/vip      — выдать VIP (CEO/Admin/Marketing)
  POST      /api/admin/user/<uid>/bonus    — выдать бонус (CEO/Admin/Marketing)
  POST      /api/admin/user/<uid>/freespins— выдать фриспины (CEO/Admin/Marketing)
"""
import os, json, copy
from flask import Blueprint, request, jsonify, session

admin_bp = Blueprint('admin', __name__, url_prefix='/api/admin')

_ADMIN_KEY   = os.environ.get('ADMIN_KEY', '')
_CONFIG_DIR  = os.path.join(os.path.dirname(__file__), '..', '..', 'config')


def _validate_pick_result_map(pick_result):
    if not isinstance(pick_result, dict):
        return False, 'pick_result должен быть JSON объектом'
    allowed = {'won', 'lost', 'void'}
    bad = [k for k, v in pick_result.items() if str(v).lower() not in allowed]
    if bad:
        return False, f'Некорректные исходы для ключей: {", ".join(map(str, bad[:10]))}'
    return True, ''


# ── Auth ───────────────────────────────────────────────────────────────────────

def _require_admin() -> bool:
    if session.get('is_admin'):
        return True
    key = request.headers.get('X-Admin-Key', '')
    if _ADMIN_KEY and key == _ADMIN_KEY:
        return True
    return False


def _auth_error():
    return jsonify({'error': 'Доступ запрещён'}), 403


def _load_perms() -> dict:
    perms_path = os.path.join(_CONFIG_DIR, 'perms.json')
    try:
        with open(perms_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'roles': {}}


def _get_admin_role() -> str:
    """Get role of current admin session user."""
    from app.models.user import User
    uid = session.get('user_id')
    if not uid:
        return ''
    user = User.get_by_id(uid)
    if not user:
        return ''
    return getattr(user, 'admin_role', '') or getattr(user, 'role', '') or ''


def _has_feature(feature: str) -> bool:
    """Check if current admin user has a specific feature enabled."""
    role_name = _get_admin_role()
    if not role_name:
        return True  # Legacy admin — full access
    perms = _load_perms()
    role = perms.get('roles', {}).get(role_name, {})
    if role.get('see_all_tabs') and feature.startswith('view_'):
        return True
    return bool(role.get('features', {}).get(feature, False))


def _feature_required(feature: str):
    """Returns 403 response if feature not allowed, else None."""
    if not _require_admin():
        return _auth_error()
    if not _has_feature(feature):
        role = _get_admin_role()
        return jsonify({'error': f'Роль {role!r} не имеет доступа к: {feature}'}), 403
    return None


# ── Config IO ──────────────────────────────────────────────────────────────────

def _cfg_path(name: str) -> str:
    return os.path.join(_CONFIG_DIR, f'{name}.json')


def _read_cfg(name: str) -> dict:
    with open(_cfg_path(name), encoding='utf-8') as f:
        return json.load(f)


def _write_cfg(name: str, data: dict):
    path = _cfg_path(name)
    tmp  = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── Generic config endpoints ───────────────────────────────────────────────────

_ALLOWED_CONFIGS = {
    'bukmaking', 'banners', 'sidebar', 'site', 'sports', 'promotions',
    'raffles', 'vip', 'promo_settings', 'site_mode', 'partners'
}

# Config → required feature mapping
_CONFIG_FEATURE_MAP = {
    'bukmaking':      'manage_sport',
    'banners':        'manage_banners',
    'sidebar':        'manage_roles',
    'promotions':     'manage_promo_codes',
    'vip':            'give_vip',
    'promo_settings': 'manage_promo_codes',
    'site_mode':      'site_maintenance',
}


@admin_bp.route('/config/<name>', methods=['GET'])
def get_config(name):
    if not _require_admin():
        return _auth_error()
    if name not in _ALLOWED_CONFIGS:
        return jsonify({'error': 'Конфиг не найден'}), 404

    # Feature check for sensitive configs
    feat = _CONFIG_FEATURE_MAP.get(name)
    if feat and not _has_feature(feat) and not _has_feature('view_' + name):
        return jsonify({'error': 'Недостаточно прав для просмотра конфига'}), 403

    try:
        return jsonify(_read_cfg(name))
    except FileNotFoundError:
        return jsonify({'error': f'{name}.json не найден'}), 404


@admin_bp.route('/config/<name>', methods=['POST'])
def set_config(name):
    """Полная замена конфига. Используй осторожно."""
    if not _require_admin():
        return _auth_error()
    if name not in _ALLOWED_CONFIGS:
        return jsonify({'error': 'Конфиг не найден'}), 404

    feat = _CONFIG_FEATURE_MAP.get(name)
    if feat and not _has_feature(feat):
        return jsonify({'error': f'Роль {_get_admin_role()!r} не может изменять {name}.json'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидается JSON объект'}), 400
    _write_cfg(name, data)
    return jsonify({'success': True, 'config': name})


# ── Bukmaking events ───────────────────────────────────────────────────────────

@admin_bp.route('/config/bukmaking/event', methods=['POST'])
def upsert_event():
    err = _feature_required('manage_sport')
    if err: return err

    event = request.get_json(silent=True)
    if not isinstance(event, dict):
        return jsonify({'error': 'Ожидается JSON объект матча'}), 400

    cfg    = _read_cfg('bukmaking')
    events = cfg.get('events', [])

    event_id = str(event.get('id', ''))
    if not event_id:
        existing_ids = [str(e.get('id', '')) for e in events]
        n = len(events) + 1
        while f'match_{n}' in existing_ids:
            n += 1
        event_id    = f'match_{n}'
        event['id'] = event_id

    idx = next((i for i, e in enumerate(events) if str(e.get('id')) == event_id), None)
    if idx is not None:
        events[idx] = event
        action = 'updated'
    else:
        event.setdefault('enabled', True)
        event.setdefault('sport_type', 'sport')
        event.setdefault('match_status', 'planned')
        event.setdefault('score', {'home': 0, 'away': 0})
        event.setdefault('period_scores', [])
        event.setdefault('markets', {})
        events.append(event)
        action = 'created'

    cfg['events'] = events
    _write_cfg('bukmaking', cfg)
    return jsonify({'success': True, 'action': action, 'id': event_id, 'event': event})


@admin_bp.route('/config/bukmaking/event/<event_id>', methods=['DELETE'])
def delete_event(event_id):
    err = _feature_required('manage_sport')
    if err: return err

    cfg    = _read_cfg('bukmaking')
    events = cfg.get('events', [])
    before = len(events)
    cfg['events'] = [e for e in events if str(e.get('id')) != str(event_id)]

    if len(cfg['events']) == before:
        return jsonify({'error': f'Матч {event_id} не найден'}), 404

    _write_cfg('bukmaking', cfg)
    return jsonify({'success': True, 'deleted': event_id})


@admin_bp.route('/config/bukmaking/event/<event_id>/toggle', methods=['POST'])
def toggle_event(event_id):
    err = _feature_required('manage_sport')
    if err: return err

    body    = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled', True))
    cfg     = _read_cfg('bukmaking')
    events  = cfg.get('events', [])

    for e in events:
        if str(e.get('id')) == str(event_id):
            e['enabled'] = enabled
            _write_cfg('bukmaking', cfg)
            return jsonify({'success': True, 'id': event_id, 'enabled': enabled})

    return jsonify({'error': f'Матч {event_id} не найден'}), 404


@admin_bp.route('/config/bukmaking/event/<event_id>/odds', methods=['POST'])
def update_odds(event_id):
    err = _feature_required('manage_sport')
    if err: return err

    body = request.get_json(silent=True) or {}
    cfg  = _read_cfg('bukmaking')

    for e in cfg.get('events', []):
        if str(e.get('id')) == str(event_id):
            if 'odds' in body or any(k in body for k in ('home','draw','away','over','under')):
                odds_patch = body.get('odds', {})
                for k in ('home','draw','away','over','under','handicap_home','handicap_away'):
                    if k in body:
                        odds_patch[k] = body[k]
                e.setdefault('odds', {}).update(odds_patch)
            if 'markets'      in body: e.setdefault('markets', {}).update(body['markets'])
            if 'match_status' in body: e['match_status'] = body['match_status']
            if 'live_minutes' in body: e['live_minutes']  = body['live_minutes']
            if 'score'        in body: e['score']         = body['score']
            _write_cfg('bukmaking', cfg)
            return jsonify({'success': True, 'id': event_id, 'event': e})

    return jsonify({'error': f'Матч {event_id} не найден'}), 404


@admin_bp.route('/config/bukmaking/event/<event_id>/status', methods=['POST'])
def update_match_status(event_id):
    err = _feature_required('manage_sport')
    if err: return err

    body = request.get_json(silent=True) or {}
    cfg  = _read_cfg('bukmaking')

    for e in cfg.get('events', []):
        if str(e.get('id')) == str(event_id):
            if 'status' in body:
                e['match_status'] = body['status']
                if body['status'] == 'live':
                    e['match_today'] = True
            if 'live_minutes'  in body: e['live_minutes']  = body['live_minutes']
            if 'score'         in body: e['score']         = body['score']
            if 'period_scores' in body: e['period_scores'] = body['period_scores']
            _write_cfg('bukmaking', cfg)
            return jsonify({'success': True, 'id': event_id})

    return jsonify({'error': f'Матч {event_id} не найден'}), 404


@admin_bp.route('/config/bukmaking/event/<event_id>/pick-result', methods=['POST'])
def set_pick_result_map(event_id):
    err = _feature_required('manage_sport')
    if err: return err

    body = request.get_json(silent=True) or {}
    pick_result = body.get('pick_result', {})
    ok, msg = _validate_pick_result_map(pick_result)
    if not ok:
        return jsonify({'error': msg}), 400

    cfg = _read_cfg('bukmaking')
    for e in cfg.get('events', []):
        if str(e.get('id')) == str(event_id):
            mc = e.setdefault('match_center', {})
            mc['pick_result'] = {str(k): str(v).lower() for k, v in pick_result.items()}
            _write_cfg('bukmaking', cfg)
            return jsonify({'success': True, 'id': event_id, 'pick_result': mc['pick_result']})
    return jsonify({'error': f'Матч {event_id} не найден'}), 404


# ── Banners (Marketing + Admin + CEO) ─────────────────────────────────────────

_BANNER_DEFAULTS = {
    'invisible':            False,
    'image_ad':             '',
    'modal_window':         False,
    'modal_text':           '',
    'modal_video':          False,
    'modal_mp4':            '',
    'modal_telegramButton': '',
    'modal_tgButtonIcon':   '',
    'modal_youtubeButton':  '',
    'modal_YTButtonIcon':   '',
    'timer_change':         5,
}


@admin_bp.route('/config/banners/page', methods=['POST'])
def upsert_banner():
    err = _feature_required('manage_banners')
    if err: return err

    banner = request.get_json(silent=True)
    if not isinstance(banner, dict):
        return jsonify({'error': 'Ожидается JSON объект баннера'}), 400

    cfg     = _read_cfg('banners')
    banners = cfg.get('page_banners', [])

    bid = str(banner.get('id', ''))
    if not bid:
        existing_ids = {str(b.get('id', '')) for b in banners}
        n = len(banners) + 1
        while f'Ad_{n}' in existing_ids:
            n += 1
        bid          = f'Ad_{n}'
        banner['id'] = bid

    merged = copy.copy(_BANNER_DEFAULTS)
    merged.update(banner)

    idx = next((i for i, b in enumerate(banners) if str(b.get('id')) == bid), None)
    if idx is not None:
        banners[idx] = merged
        action = 'updated'
    else:
        banners.append(merged)
        action = 'created'

    cfg['page_banners'] = banners
    _write_cfg('banners', cfg)
    return jsonify({'success': True, 'action': action, 'id': bid, 'banner': merged})


@admin_bp.route('/config/banners/page/<banner_id>', methods=['DELETE'])
def delete_banner(banner_id):
    err = _feature_required('manage_banners')
    if err: return err

    cfg     = _read_cfg('banners')
    banners = cfg.get('page_banners', [])
    before  = len(banners)
    cfg['page_banners'] = [b for b in banners if str(b.get('id')) != str(banner_id)]

    if len(cfg['page_banners']) == before:
        return jsonify({'error': f'Баннер {banner_id} не найден'}), 404

    _write_cfg('banners', cfg)
    return jsonify({'success': True, 'deleted': banner_id})


@admin_bp.route('/config/banners/page/<banner_id>/toggle', methods=['POST'])
def toggle_banner(banner_id):
    err = _feature_required('manage_banners')
    if err: return err

    body      = request.get_json(silent=True) or {}
    invisible = bool(body.get('invisible', False))
    cfg       = _read_cfg('banners')

    for b in cfg.get('page_banners', []):
        if str(b.get('id')) == str(banner_id):
            b['invisible'] = invisible
            _write_cfg('banners', cfg)
            return jsonify({'success': True, 'id': banner_id, 'invisible': invisible})

    sb = cfg.get('sidebar_banner', {})
    if str(sb.get('id')) == str(banner_id):
        sb['invisible'] = invisible
        cfg['sidebar_banner'] = sb
        _write_cfg('banners', cfg)
        return jsonify({'success': True, 'id': banner_id, 'invisible': invisible})

    return jsonify({'error': f'Баннер {banner_id} не найден'}), 404


@admin_bp.route('/config/banners/sidebar', methods=['POST'])
def update_sidebar_banner():
    err = _feature_required('manage_banners')
    if err: return err

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидается JSON объект'}), 400

    cfg = _read_cfg('banners')
    cfg.setdefault('sidebar_banner', {}).update(data)
    _write_cfg('banners', cfg)
    return jsonify({'success': True, 'sidebar_banner': cfg['sidebar_banner']})


# ── Sidebar items ──────────────────────────────────────────────────────────────

@admin_bp.route('/config/sidebar/item/<item_id>/toggle', methods=['POST'])
def toggle_sidebar_item(item_id):
    if not _require_admin():
        return _auth_error()

    body    = request.get_json(silent=True) or {}
    enabled = bool(body.get('enabled', True))
    cfg     = _read_cfg('sidebar')

    def _toggle_in(items):
        for item in items:
            if str(item.get('id')) == str(item_id):
                item['enabled'] = enabled
                return True
            for child in item.get('children', []):
                if str(child.get('id')) == str(item_id):
                    child['enabled'] = enabled
                    return True
        return False

    if not _toggle_in(cfg.get('items', [])):
        return jsonify({'error': f'Элемент {item_id} не найден'}), 404

    _write_cfg('sidebar', cfg)
    return jsonify({'success': True, 'id': item_id, 'enabled': enabled})


@admin_bp.route('/config/sidebar/item', methods=['POST'])
def add_sidebar_item():
    if not _require_admin():
        return _auth_error()

    item = request.get_json(silent=True)
    if not isinstance(item, dict):
        return jsonify({'error': 'Ожидается JSON объект'}), 400
    if not item.get('id') or not item.get('type'):
        return jsonify({'error': 'id и type обязательны'}), 400

    item.setdefault('enabled', True)
    position = item.pop('position', None)

    cfg   = _read_cfg('sidebar')
    items = cfg.get('items', [])

    if any(str(i.get('id')) == str(item['id']) for i in items):
        return jsonify({'error': f'Элемент с id={item["id"]} уже существует'}), 409

    if position is not None and 0 <= int(position) <= len(items):
        items.insert(int(position), item)
    else:
        items.append(item)

    cfg['items'] = items
    _write_cfg('sidebar', cfg)
    return jsonify({'success': True, 'action': 'created', 'item': item})


@admin_bp.route('/config/sidebar/item/<item_id>', methods=['DELETE'])
def delete_sidebar_item(item_id):
    if not _require_admin():
        return _auth_error()

    cfg    = _read_cfg('sidebar')
    items  = cfg.get('items', [])
    before = len(items)
    cfg['items'] = [i for i in items if str(i.get('id')) != str(item_id)]

    if len(cfg['items']) == before:
        return jsonify({'error': f'Элемент {item_id} не найден'}), 404

    _write_cfg('sidebar', cfg)
    return jsonify({'success': True, 'deleted': item_id})


# ── User management (role-aware) ───────────────────────────────────────────────

@admin_bp.route('/users/<int:user_id>/balance', methods=['POST'])
def admin_adjust_balance(user_id):
    err = _feature_required('adjust_balance')
    if err: return err

    body   = request.get_json(silent=True) or {}
    amount = float(body.get('amount', 0))
    op     = body.get('operation', 'add')  # 'add' | 'subtract' | 'set'

    from app.models.user import User
    from app.services.wallet_service import WalletService

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        if op == 'set':
            WalletService.set_balance(user, amount)
        elif op == 'subtract':
            WalletService.debit(user, amount, reason='admin_adjust')
        else:
            WalletService.credit(user, amount, reason='admin_adjust')

        return jsonify({'success': True, 'new_balance': round(user.balance, 2)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@admin_bp.route('/users/<int:user_id>/ban', methods=['POST'])
def admin_ban_user(user_id):
    """Ban or unban a user."""
    if not _require_admin():
        return _auth_error()

    body     = request.get_json(silent=True) or {}
    banned   = bool(body.get('banned', True))
    reason   = body.get('reason', '')

    feat = 'ban_users' if banned else 'unban_users'
    if not _has_feature(feat):
        return jsonify({'error': f'Недостаточно прав: {feat}'}), 403

    from app.models.user import User
    from app.db import get_db

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET is_banned = ?, ban_reason = ? WHERE id = ?",
                (1 if banned else 0, reason if banned else None, user_id)
            )
        return jsonify({'success': True, 'user_id': user_id, 'banned': banned})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/users/<int:user_id>/vip', methods=['POST'])
def admin_give_vip(user_id):
    """Give / extend VIP for a user — CEO, Admin, Marketing only."""
    err = _feature_required('give_vip')
    if err: return err

    body     = request.get_json(silent=True) or {}
    tier     = body.get('tier', 'gold')
    days     = int(body.get('days', 30))

    from app.models.user import User
    from app.db import get_db
    from datetime import datetime, timedelta, timezone as _tz

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    expires = (datetime.now(_tz.utc) + timedelta(days=days)).isoformat()
    try:
        with get_db() as db:
            db.execute(
                "UPDATE users SET vip_tier = %s, vip_expires_at = %s WHERE id = %s",
                (tier, expires, user_id)
            )
        return jsonify({'success': True, 'tier': tier, 'expires_at': expires})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/users/<int:user_id>/bonus', methods=['POST'])
def admin_give_bonus(user_id):
    """Give a one-time bonus — CEO, Admin, Marketing only."""
    err = _feature_required('give_bonus')
    if err: return err

    body   = request.get_json(silent=True) or {}
    amount = float(body.get('amount', 0))
    reason = body.get('reason', 'admin_bonus')

    if amount <= 0:
        return jsonify({'error': 'Сумма должна быть положительной'}), 400

    from app.models.user import User
    from app.services.wallet_service import WalletService

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        WalletService.credit(user, amount, reason=reason, tx_type='bonus')
        return jsonify({'success': True, 'amount': amount, 'new_balance': round(user.balance, 2)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@admin_bp.route('/users/<int:user_id>/freespins', methods=['POST'])
def admin_give_freespins(user_id):
    """Give freespins — CEO, Admin, Marketing only."""
    err = _feature_required('give_freespins')
    if err: return err

    body    = request.get_json(silent=True) or {}
    count   = int(body.get('count', 10))
    game_id = body.get('game_id', 'any')

    from app.models.user import User
    from app.db import get_db
    from datetime import datetime, timezone as _tz

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        with get_db() as db:
            db.execute(
                """INSERT INTO freespins (user_id, count, game_id, created_at, source)
                   VALUES (?, ?, ?, ?, 'admin')""",
                (user_id, count, game_id, datetime.now(_tz.utc).isoformat())
            )
        return jsonify({'success': True, 'count': count, 'game_id': game_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/users/<int:user_id>/deposit_bonus', methods=['POST'])
def admin_deposit_bonus(user_id):
    """Set one-time deposit bonus % — CEO, Admin, Marketing only."""
    err = _feature_required('give_deposit_bonus')
    if err: return err

    body    = request.get_json(silent=True) or {}
    percent = float(body.get('percent', 50))
    hours   = int(body.get('valid_hours', 24))

    from app.models.user import User
    from app.db import get_db
    from datetime import datetime, timedelta, timezone as _tz

    user = User.get_by_id(user_id)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        user.add_bonus_deposit(percent)
        return jsonify({'success': True, 'percent': percent, 'new_bonus_deposit': round(user.bonus_deposit, 2)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Sport bet management ───────────────────────────────────────────────────────

@admin_bp.route('/sport/bets', methods=['GET'])
def admin_get_bets():
    err = _feature_required('view_sport')
    if err: return err

    from app.db import get_db
    status = request.args.get('status', '')
    limit  = min(int(request.args.get('limit', 100)), 500)

    with get_db() as db:
        db.execute(
            f"SELECT gh.*, u.username, u.currency as user_currency"
            f" FROM game_history gh"
            f" LEFT JOIN users u ON u.id = gh.user_id"
            f" WHERE gh.game_id='sport_bet'"
            f" ORDER BY gh.created_at DESC LIMIT {int(limit)}"
        )
        rows = db.fetchall()

    import json as _json

    def _parse(raw):
        if isinstance(raw, dict): return raw
        try: return _json.loads(raw) if raw else {}
        except: return {}

    bets = []
    for row in rows:
        r    = dict(row)
        meta = _parse(r.get('meta'))
        rs   = meta.get('result_status', 'pending')
        if status and rs != status:
            continue
        stake = float(r.get('bet', 0))
        coef  = float(r.get('multiplier') or 1)
        bets.append({
            'id':            r['id'],
            'user_id':       r.get('user_id'),
            'username':      r.get('username', ''),
            'stake':         stake,
            'coef':          coef,
            'potential_win': round(stake * coef, 2),
            'win':           float(r.get('win', 0)),
            'currency':      meta.get('currency', r.get('user_currency', 'UAH')),
            'mode':          meta.get('mode', 'single'),
            'picks':         meta.get('picks', []),
            'result_status': rs,
            'created_at':    str(r.get('created_at', '')),
        })

    return jsonify({'bets': bets, 'total': len(bets)})
