"""app/api/roles.py — Система ролей Velox Casino Admin Panel.

Роли и их доступы читаются из config/perms.json.
Endpoints:
  GET  /api/roles/config           — публичный список ролей (без секретов)
  GET  /api/roles/my               — роль и права текущего admin-пользователя
  GET  /api/roles/check/<feature>  — проверить доступ к фиче
  POST /api/roles/assign           — назначить роль пользователю (CEO only)
  GET  /api/roles/perms            — полный perms.json (CEO/Admin only)
  POST /api/roles/perms            — обновить perms.json (CEO only)
  GET  /api/roles/db_export/<uid>  — собрать полный лог по аккаунту (SOC/Admin/Helper)
"""
from __future__ import annotations
import os, json, hashlib
from datetime import datetime, timezone as _tz
from flask import Blueprint, request, jsonify, session
from app.models.user import User

roles_bp = Blueprint('roles', __name__, url_prefix='/api/roles')

_CONFIG_DIR  = os.path.join(os.path.dirname(__file__), '..', '..', 'config')
_PERMS_PATH  = os.path.join(_CONFIG_DIR, 'perms.json')
_ADMIN_KEY   = os.environ.get('ADMIN_KEY', '')


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_perms() -> dict:
    try:
        with open(_PERMS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'roles': {}, 'tabs': {}}


def _write_perms(data: dict):
    tmp = _PERMS_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _PERMS_PATH)


def _current_admin_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _is_admin_authed() -> bool:
    if session.get('is_admin'):
        return True
    key = request.headers.get('X-Admin-Key', '')
    return bool(_ADMIN_KEY and key == _ADMIN_KEY)


def _get_admin_role() -> str:
    """Get role of current admin session user from DB."""
    user = _current_admin_user()
    if not user:
        return ''
    # Check admin_role attribute, fall back to role field
    return getattr(user, 'admin_role', '') or getattr(user, 'role', '') or ''


def _has_feature(role_name: str, feature: str) -> bool:
    """Check if a role has a specific feature enabled."""
    perms = _load_perms()
    role = perms.get('roles', {}).get(role_name, {})
    # CEO with see_all_tabs sees everything
    if role.get('see_all_tabs') and feature.startswith('view_'):
        return True
    return bool(role.get('features', {}).get(feature, False))


def _can_see_tab(role_name: str, tab: str) -> bool:
    perms = _load_perms()
    role = perms.get('roles', {}).get(role_name, {})
    if role.get('see_all_tabs'):
        return True
    tabs = role.get('tabs', [])
    return '*' in tabs or tab in tabs


def _auth_error():
    return jsonify({'error': 'Доступ запрещён'}), 403


# ── Endpoints ──────────────────────────────────────────────────────────────────

@roles_bp.route('/config')
def get_roles_config():
    """Public list of roles with labels/colors (no sensitive data)."""
    perms = _load_perms()
    result = {}
    for role_name, role_data in perms.get('roles', {}).items():
        result[role_name] = {
            'label':       role_data.get('label', role_name),
            'color':       role_data.get('color', '#888'),
            'icon':        role_data.get('icon', ''),
            'description': role_data.get('description', ''),
        }
    return jsonify({'roles': result, 'tabs': perms.get('tabs', {})})


@roles_bp.route('/my')
def my_role():
    """Return current admin user's role and permissions."""
    if not _is_admin_authed():
        return _auth_error()

    role_name = _get_admin_role()
    perms = _load_perms()
    roles = perms.get('roles', {})

    if not role_name or role_name not in roles:
        # Default: show everything for backward compat (legacy admin)
        return jsonify({
            'role': 'CEO',
            'see_all_tabs': True,
            'tabs': list(perms.get('tabs', {}).keys()),
            'features': {k: True for k in roles.get('CEO', {}).get('features', {})},
        })

    role = roles[role_name]
    features = role.get('features', {})
    tabs = perms.get('tabs', {})

    # Build visible tabs list
    if role.get('see_all_tabs'):
        visible_tabs = list(tabs.keys())
    else:
        role_tabs = role.get('tabs', [])
        visible_tabs = [t for t in role_tabs if t in tabs or t == '*']

    return jsonify({
        'role':         role_name,
        'label':        role.get('label', role_name),
        'color':        role.get('color', '#888'),
        'icon':         role.get('icon', ''),
        'see_all_tabs': role.get('see_all_tabs', False),
        'tabs':         visible_tabs,
        'features':     features,
    })


@roles_bp.route('/check/<feature>')
def check_feature(feature: str):
    """Quick check if current admin has a specific feature enabled."""
    if not _is_admin_authed():
        return _auth_error()
    role_name = _get_admin_role()
    allowed = _has_feature(role_name, feature) if role_name else True
    return jsonify({'feature': feature, 'allowed': allowed, 'role': role_name})


@roles_bp.route('/perms', methods=['GET'])
def get_perms():
    """Full perms.json — CEO and Admin only."""
    if not _is_admin_authed():
        return _auth_error()
    role_name = _get_admin_role()
    perms = _load_perms()
    if role_name and role_name not in ('CEO', 'Admin') and not session.get('is_superadmin'):
        return _auth_error()
    return jsonify(perms)


@roles_bp.route('/perms', methods=['POST'])
def update_perms():
    """Update perms.json — CEO only."""
    if not _is_admin_authed():
        return _auth_error()
    role_name = _get_admin_role()
    if role_name and role_name != 'CEO' and not session.get('is_superadmin'):
        return jsonify({'error': 'Только CEO может изменять права доступа'}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Ожидается JSON объект'}), 400

    # Safety: preserve _comment and _version
    existing = _load_perms()
    data.setdefault('_comment', existing.get('_comment', ''))
    data.setdefault('_version', existing.get('_version', '2.0.0'))

    _write_perms(data)
    return jsonify({'success': True})


@roles_bp.route('/assign', methods=['POST'])
def assign_role():
    """Assign admin role to a user — CEO only."""
    if not _is_admin_authed():
        return _auth_error()
    role_name = _get_admin_role()
    if role_name and role_name != 'CEO' and not session.get('is_superadmin'):
        return jsonify({'error': 'Только CEO может назначать роли'}), 403

    body = request.get_json(silent=True) or {}
    target_uid = body.get('user_id')
    new_role    = body.get('role', '')

    if not target_uid:
        return jsonify({'error': 'user_id обязателен'}), 400

    perms = _load_perms()
    valid_roles = list(perms.get('roles', {}).keys()) + ['']
    if new_role not in valid_roles:
        return jsonify({'error': f'Роль "{new_role}" не существует'}), 400

    target = User.get_by_id(target_uid)
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404

    # Update role in DB
    try:
        from app.db import get_db
        with get_db() as db:
            db.execute(
                "UPDATE users SET admin_role = ? WHERE id = ?",
                (new_role or None, target_uid)
            )
        return jsonify({'success': True, 'user_id': target_uid, 'role': new_role})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@roles_bp.route('/db_export/<int:user_id>', methods=['GET'])
def db_export(user_id: int):
    """
    Полный лог по аккаунту — только SOC, Admin, Helper, CEO.
    Собирает всю информацию об аккаунте: профиль, ставки, транзакции,
    бонусы, верификация, VIP статус и т.д.
    """
    if not _is_admin_authed():
        return _auth_error()

    role_name = _get_admin_role()
    perms = _load_perms()
    allowed = _has_feature(role_name, 'get_db_export') if role_name else True

    if not allowed:
        return jsonify({'error': 'Нет доступа к экспорту данных'}), 403

    target = User.get_by_id(user_id)
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404

    try:
        from app.db import get_db
        import sqlite3

        with get_db() as db:
            # ── Game history ──────────────────────────────────────────────────
            db.execute(
                "SELECT * FROM game_history WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            game_rows = [dict(r) for r in db.fetchall()]

            # ── Transactions ──────────────────────────────────────────────────
            db.execute(
                "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            tx_rows = [dict(r) for r in db.fetchall()]

            # ── Support tickets ───────────────────────────────────────────────
            db.execute(
                "SELECT * FROM support_tickets WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            tickets = [dict(r) for r in db.fetchall()]

            # ── Freespins ─────────────────────────────────────────────────────
            db.execute(
                "SELECT * FROM freespins WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            freespins = [dict(r) for r in db.fetchall()]

        # ── Categorize bets ───────────────────────────────────────────────────
        def _parse_meta(raw):
            if isinstance(raw, dict):
                return raw
            try:
                return json.loads(raw) if raw else {}
            except Exception:
                return {}

        sport_bets  = []
        slot_bets   = []
        crash_bets  = []
        card_bets   = []
        other_bets  = []

        total_wagered = 0.0
        total_won     = 0.0
        big_bets      = []
        small_bets    = []
        BIG_THRESHOLD = 500  # UAH

        for row in game_rows:
            meta     = _parse_meta(row.get('meta'))
            game_id  = str(row.get('game_id', ''))
            bet_amt  = float(row.get('bet', 0))
            win_amt  = float(row.get('win', 0))

            total_wagered += bet_amt
            total_won     += win_amt

            entry = {
                'id':         row.get('id'),
                'game_id':    game_id,
                'bet':        bet_amt,
                'win':        win_amt,
                'multiplier': row.get('multiplier'),
                'created_at': str(row.get('created_at', '')),
                'meta':       meta,
            }

            if 'sport' in game_id:
                sport_bets.append(entry)
            elif any(k in game_id for k in ('crash', 'rocket')):
                crash_bets.append(entry)
            elif any(k in game_id for k in ('card', 'blackjack', 'poker', 'baccarat')):
                card_bets.append(entry)
            elif any(k in game_id for k in ('slot', 'mine', 'roulette', 'lottery')):
                slot_bets.append(entry)
            else:
                other_bets.append(entry)

            if bet_amt >= BIG_THRESHOLD:
                big_bets.append(entry)
            elif bet_amt > 0:
                small_bets.append(entry)

        # ── Deposits / Withdrawals ─────────────────────────────────────────────
        deposits     = [t for t in tx_rows if t.get('type') == 'deposit']
        withdrawals  = [t for t in tx_rows if t.get('type') == 'withdrawal']
        bonuses      = [t for t in tx_rows if t.get('type') in ('bonus', 'freespin', 'cashback')]

        total_deposited  = sum(float(t.get('amount', 0)) for t in deposits)
        total_withdrawn  = sum(float(t.get('amount', 0)) for t in withdrawals)

        # ── Password hash (masked) ─────────────────────────────────────────────
        raw_hash = getattr(target, 'password_hash', '') or ''
        masked_hash = raw_hash[:8] + '...' + raw_hash[-4:] if len(raw_hash) > 12 else raw_hash

        # ── Build report ──────────────────────────────────────────────────────
        now_utc = datetime.now(_tz.utc).isoformat()

        report = {
            '_meta': {
                'export_type':      'FULL_ACCOUNT_DB_EXPORT',
                'generated_at':     now_utc,
                'generated_by_role': role_name or 'Admin',
                'target_user_id':   user_id,
                'velox_version':    '2.0.0',
            },

            'profile': {
                'id':               target.id,
                'username':         target.username,
                'email':            target.email or None,
                'phone':            getattr(target, 'phone', None),
                'currency':         target.currency,
                'registered_at':    str(getattr(target, 'created_at', '')),
                'last_login':       str(getattr(target, 'last_login', '')),
                'is_banned':        bool(getattr(target, 'is_banned', False)),
                'ban_reason':       getattr(target, 'ban_reason', None),
                'password_hash':    masked_hash,
                'balance':          round(float(target.balance or 0), 2),
                'country':          getattr(target, 'country', None),
                'language':         getattr(target, 'language', 'ru'),
                'admin_role':       getattr(target, 'admin_role', None),
            },

            'verification': {
                'kyc_status':       getattr(target, 'kyc_status', 'unverified'),
                'kyc_level':        getattr(target, 'kyc_level', 0),
                'email_verified':   bool(getattr(target, 'email_verified', False)),
                'phone_verified':   bool(getattr(target, 'phone_verified', False)),
                'documents':        getattr(target, 'kyc_documents', []),
            },

            'vip': {
                'tier':             getattr(target, 'vip_tier', 'none'),
                'expires_at':       str(getattr(target, 'vip_expires_at', '')),
                'is_active':        bool(getattr(target, 'vip_tier', None) and getattr(target, 'vip_expires_at', None)),
            },

            'financial_summary': {
                'total_deposited':  round(total_deposited, 2),
                'total_withdrawn':  round(total_withdrawn, 2),
                'total_wagered':    round(total_wagered, 2),
                'total_won':        round(total_won, 2),
                'net_profit':       round(total_deposited - total_withdrawn, 2),
                'ggr':              round(total_wagered - total_won, 2),
                'deposit_count':    len(deposits),
                'withdrawal_count': len(withdrawals),
            },

            'transactions': {
                'deposits':     deposits,
                'withdrawals':  withdrawals,
                'bonuses':      bonuses,
                'all':          tx_rows,
            },

            'bets': {
                'summary': {
                    'total_bets':   len(game_rows),
                    'sport_count':  len(sport_bets),
                    'slots_count':  len(slot_bets),
                    'crash_count':  len(crash_bets),
                    'cards_count':  len(card_bets),
                    'other_count':  len(other_bets),
                    'big_bets_count':   len(big_bets),
                    'small_bets_count': len(small_bets),
                },
                'sport':   sport_bets,
                'slots':   slot_bets,
                'crash':   crash_bets,
                'cards':   card_bets,
                'other':   other_bets,
                'big_bets':   big_bets[:50],
                'small_bets': small_bets[:100],
                'all':     game_rows,
            },

            'support_tickets': tickets,
            'freespins':        freespins,
        }

        return jsonify(report)

    except Exception as e:
        return jsonify({'error': f'Ошибка экспорта: {e}'}), 500
