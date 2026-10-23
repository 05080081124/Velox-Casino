"""app/api/admin_panel.py — CEO & Administrator panel APIs."""
import os, json, re
from flask import Blueprint, request, jsonify
from app.db import get_casino_db, get_admin_db
from app.api.auth import require_admin

admin_panel_bp = Blueprint('admin_panel', __name__, url_prefix='/api/admin')

_CASINO_DIR  = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fixed_out')
_SITE_JSON   = os.path.join(_CASINO_DIR, 'config', 'site_mode.json')
_SECURITY_JSON = os.path.join(_CASINO_DIR, 'config', 'security.json')
_BUKM_JSON   = os.path.join(_CASINO_DIR, 'config', 'bukmaking.json')
_SPORTS_JSON = os.path.join(_CASINO_DIR, 'config', 'sports.json')
_PROV_CFG    = os.path.join(_CASINO_DIR, 'app', 'games', 'provider_catalog', 'config.json')
_SPORT_MATCH_UPLOADS = os.path.join(_CASINO_DIR, 'static', 'uploads', 'bukmaking', 'matches')
_SPORT_ICON_UPLOADS = os.path.join(_CASINO_DIR, 'static', 'uploads', 'bukmaking', 'icons')
_VISIBILITY_JSON = os.path.join(_CASINO_DIR, 'config', 'sport_provider_visibility.json')
_PROV_OVERRIDES_JSON = os.path.join(_CASINO_DIR, 'config', 'sport_provider_overrides.json')


def _invalidate_casino_odds_cache():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_CASINO_DIR, '.env'))
        from app.services.casino_sport_odds import load_casino_sport_odds_feed
        load_casino_sport_odds_feed().invalidate_odds_feed_cache()
    except Exception:
        pass


def _is_provider_feed_event_id(eid: str) -> bool:
    s = str(eid or '')
    return s.startswith('oddsapiio_') or s.startswith('oddsapi_')


def _ceo_or_admin(admin):
    return admin.role in ('ceo', 'admin')


def _admin_only(admin):
    return admin.role in ('admin', 'ceo')


def _gb_role_ok(admin):
    """Game Builder: ceo, admin — full write; gm, dealer — read + toggle/rtp (no delete)."""
    return admin.role in ('ceo', 'admin', 'gm', 'dealer')

def _site_role_ok(admin):
    return admin.role in ('ceo', 'admin', 'developer', 'marketing')

def _tour_role_ok(admin):
    return admin.role in ('ceo', 'admin', 'marketing', 'gm', 'dealer')


def _read_json(path, fallback=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {} if fallback is None else fallback


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _slugify(v: str, fallback='item') -> str:
    out = re.sub(r'[^a-z0-9]+', '_', (v or '').lower()).strip('_')
    return out or fallback


def _normalize_hex_color(raw: str, default: str = '#f5c451') -> str:
    val = str(raw or '').strip()
    if re.fullmatch(r'#[0-9a-fA-F]{6}', val):
        return val.lower()
    return default


def _sport_cfg_path() -> str:
    return _BUKM_JSON if os.path.exists(_BUKM_JSON) else _SPORTS_JSON


def _read_provider_catalog():
    return _read_json(_PROV_CFG, {
        'provider': 'Provider Catalog',
        'provider_tag': 'provider_catalog',
        'games': [],
    })


def _write_provider_catalog(data):
    _write_json(_PROV_CFG, data)


def _collect_local_games():
    games_dir = os.path.join(_CASINO_DIR, 'app', 'games')
    out = []
    if not os.path.isdir(games_dir):
        return out
    for entry in os.listdir(games_dir):
        full = os.path.join(games_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.startswith('_') or entry == 'provider_catalog':
            continue
        cfg_path = os.path.join(full, 'config.json')
        if not os.path.exists(cfg_path):
            continue
        cfg = _read_json(cfg_path, {})
        gid = str(cfg.get('id') or entry).strip()
        if not gid:
            continue
        i18n = cfg.get('i18n') or {}
        i18n_ru = (i18n.get('ru') or {})
        i18n_us = (i18n.get('us') or {})
        i18n_ua = (i18n.get('ua') or {})
        name = (
            str(i18n_ru.get('name') or '').strip()
            or str(i18n_us.get('name') or '').strip()
            or str(i18n_ua.get('name') or '').strip()
            or str(cfg.get('name') or gid).strip()
        )
        out.append({
            'id': gid,
            'name': name,
            'route': str(cfg.get('route') or f'/game/{gid}'),
            'active': bool(cfg.get('active', True)),
            'provider': str(cfg.get('provider') or 'VELOX Games'),
            'provider_tag': str(cfg.get('provider_tag') or 'velox'),
            'category': str(cfg.get('category') or 'slots'),
            'source': 'local',
            'icon': str(cfg.get('icon') or '🎮'),
        })
    return out


def _collect_games_for_campaign_picker():
    provider_cfg = _read_provider_catalog()
    provider_games = provider_cfg.get('games', []) if isinstance(provider_cfg, dict) else []
    merged = {}
    for g in _collect_local_games():
        merged[str(g.get('id'))] = g
    for g in provider_games:
        gid = str(g.get('id') or '').strip()
        if not gid:
            continue
        merged[gid] = {
            'id': gid,
            'name': str(g.get('name') or gid),
            'route': str(g.get('route') or f'/game/{gid}'),
            'active': bool(g.get('active', True)),
            'provider': str(g.get('provider') or 'Provider'),
            'provider_tag': str(g.get('provider_tag') or ''),
            'category': str(g.get('category') or 'slots'),
            'source': 'provider',
            'icon': str(g.get('icon') or '🎰'),
        }
    return sorted(merged.values(), key=lambda x: str(x.get('name') or x.get('id') or '').lower())


def _ensure_tournament_tables():
    with get_casino_db() as db:
        if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql':
            db.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id BIGSERIAL PRIMARY KEY,
                    slug VARCHAR(128) UNIQUE,
                    title VARCHAR(256) NOT NULL,
                    period VARCHAR(16) NOT NULL,
                    prize_pool NUMERIC(18,2) NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT '',
                    game_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    glow_color VARCHAR(16) NOT NULL DEFAULT '#f5c451',
                    starts_at TIMESTAMPTZ NOT NULL,
                    ends_at TIMESTAMPTZ NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_period_status ON tournaments(period, status, starts_at, ends_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tournament_entries (
                    id BIGSERIAL PRIMARY KEY,
                    tournament_id BIGINT NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    total_bet NUMERIC(18,2) NOT NULL DEFAULT 0,
                    total_win NUMERIC(18,2) NOT NULL DEFAULT 0,
                    total_profit NUMERIC(18,2) NOT NULL DEFAULT 0,
                    rounds INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(tournament_id, user_id)
                )
            """)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT UNIQUE,
                    title TEXT NOT NULL,
                    period TEXT NOT NULL,
                    prize_pool REAL NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT '',
                    game_ids TEXT NOT NULL DEFAULT '[]',
                    glow_color TEXT NOT NULL DEFAULT '#f5c451',
                    starts_at TEXT NOT NULL,
                    ends_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_tournaments_period_status ON tournaments(period, status, starts_at, ends_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS tournament_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tournament_id INTEGER NOT NULL REFERENCES tournaments(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    total_bet REAL NOT NULL DEFAULT 0,
                    total_win REAL NOT NULL DEFAULT 0,
                    total_profit REAL NOT NULL DEFAULT 0,
                    rounds INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                    UNIQUE(tournament_id, user_id)
                )
            """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_tour_entries_tour ON tournament_entries(tournament_id, total_win DESC)")
        for alter in (
            "ALTER TABLE tournaments ADD COLUMN description TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE tournaments ADD COLUMN game_ids TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE tournaments ADD COLUMN glow_color TEXT NOT NULL DEFAULT '#f5c451'",
        ):
            try:
                db.execute(alter)
            except Exception:
                pass



# ═══════════════════════════════════════════════════════════════
# CEO — ANALYTICS
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/tournaments/seasons', methods=['GET'])
@require_admin
def tournaments_list(admin):
    if not _tour_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    _ensure_tournament_tables()
    with get_casino_db() as db:
        db.execute("""
            SELECT
                t.*,
                COUNT(te.id) AS entries
            FROM tournaments t
            LEFT JOIN tournament_entries te ON te.tournament_id = t.id
            GROUP BY t.id
            ORDER BY t.created_at DESC, t.id DESC
            LIMIT 100
        """)
        rows = [dict(r) for r in db.fetchall()]
    return jsonify({'seasons': rows})


@admin_panel_bp.route('/tournaments/seasons', methods=['POST'])
@require_admin
def tournaments_create(admin):
    if not _tour_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    _ensure_tournament_tables()
    d = request.get_json() or {}
    title = str(d.get('title') or '').strip()
    description = str(d.get('description') or '').strip()
    period = str(d.get('period') or 'custom').strip().lower()
    starts_at = str(d.get('starts_at') or '').strip()
    ends_at = str(d.get('ends_at') or '').strip()
    if not title:
        return jsonify({'error': 'Title required'}), 400
    if not period:
        period = 'custom'
    if not starts_at or not ends_at:
        return jsonify({'error': 'Start/end required'}), 400
    try:
        prize_pool = round(float(d.get('prize_pool') or 0), 2)
    except Exception:
        return jsonify({'error': 'Invalid prize_pool'}), 400
    if prize_pool < 0:
        return jsonify({'error': 'prize_pool must be >= 0'}), 400
    game_ids_raw = d.get('game_ids') if isinstance(d.get('game_ids'), list) else []
    game_ids = sorted({str(x).strip() for x in game_ids_raw if str(x).strip()})
    glow_color = _normalize_hex_color(d.get('glow_color'))

    slug = f"{_slugify(title, 'season')}_{int(__import__('time').time())}"
    p = '%s' if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql' else '?'
    game_ids_expr = f"{p}::jsonb" if p == "%s" else p
    now_fn = "NOW()" if p == "%s" else "datetime('now')"
    with get_casino_db() as db:
        db.execute(
            f"""INSERT INTO tournaments
                (slug, title, description, period, prize_pool, game_ids, glow_color, starts_at, ends_at, status, created_at, updated_at)
                VALUES ({p},{p},{p},{p},{p},{game_ids_expr},{p},{p},{p},'active',{now_fn},{now_fn})
                RETURNING *""",
            (slug, title, description, period, prize_pool, json.dumps(game_ids, ensure_ascii=False), glow_color, starts_at, ends_at)
        )
        row = db.fetchone()
    return jsonify({'ok': True, 'season': dict(row) if row else {}})


@admin_panel_bp.route('/tournaments/seasons/<int:sid>/status', methods=['POST'])
@require_admin
def tournaments_set_status(admin, sid):
    if not _tour_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    _ensure_tournament_tables()
    status = str((request.get_json() or {}).get('status') or '').strip().lower()
    if status not in ('active', 'completed', 'draft'):
        return jsonify({'error': 'Invalid status'}), 400
    p = '%s' if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql' else '?'
    now_fn = "NOW()" if p == "%s" else "datetime('now')"
    with get_casino_db() as db:
        db.execute(f"UPDATE tournaments SET status={p}, updated_at={now_fn} WHERE id={p}", (status, sid))
        db.execute(f"SELECT * FROM tournaments WHERE id={p}", (sid,))
        row = db.fetchone()
    if not row:
        return jsonify({'error': 'Season not found'}), 404
    return jsonify({'ok': True, 'season': dict(row)})


@admin_panel_bp.route('/tournaments/seasons/<int:sid>', methods=['DELETE'])
@require_admin
def tournaments_delete(admin, sid):
    if not _tour_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    _ensure_tournament_tables()
    p = '%s' if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql' else '?'
    with get_casino_db() as db:
        db.execute(f"DELETE FROM tournaments WHERE id={p}", (sid,))
    return jsonify({'ok': True, 'deleted': sid})


@admin_panel_bp.route('/analytics', methods=['GET'])
@require_admin
def analytics(admin):
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403

    period = request.args.get('period', '7d')
    period_map = {
        '24h':  "datetime('now','-1 day')",
        '7d':   "datetime('now','-7 days')",
        '30d':  "datetime('now','-1 month')",
        'all':  "datetime('2000-01-01')",
    }
    since = period_map.get(period, period_map['7d'])

    with get_casino_db() as db:
        # Users
        db.execute("SELECT COUNT(*) as cnt FROM users")
        total_users = (db.fetchone() or {}).get('cnt', 0)

        db.execute(f"SELECT COUNT(*) as cnt FROM users WHERE created_at >= {since}")
        new_users = (db.fetchone() or {}).get('cnt', 0)

        db.execute("SELECT COUNT(*) as cnt FROM users WHERE kyc_status='verified'")
        verified_users = (db.fetchone() or {}).get('cnt', 0)

        # Deposits (payment_orders type=deposit completed)
        db.execute(f"""SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as total
                       FROM payment_orders
                       WHERE type='deposit' AND status='completed'
                       AND created_at >= {since}""")
        dep = db.fetchone() or {}

        # Withdrawals
        db.execute(f"""SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as total
                       FROM payment_orders
                       WHERE type='withdraw' AND status='completed'
                       AND created_at >= {since}""")
        wd = db.fetchone() or {}

        # Pending withdrawals
        db.execute("""SELECT COUNT(*) as cnt, COALESCE(SUM(amount),0) as total
                      FROM payment_orders WHERE type='withdraw' AND status='pending'""")
        pending_wd = db.fetchone() or {}

        # GGR (bets - wins from game_history)
        db.execute(f"""SELECT COALESCE(SUM(bet),0) as bets, COALESCE(SUM(win),0) as wins
                       FROM game_history WHERE created_at >= {since}""")
        gg = db.fetchone() or {}
        bets = float(gg.get('bets') or 0)
        wins = float(gg.get('wins') or 0)
        ggr  = round(bets - wins, 2)

        # Game rounds
        db.execute(f"SELECT COUNT(*) as cnt FROM game_history WHERE created_at >= {since}")
        rounds = (db.fetchone() or {}).get('cnt', 0)

        # Active users (placed bets in period)
        db.execute(f"SELECT COUNT(DISTINCT user_id) as cnt FROM game_history WHERE created_at >= {since}")
        active_users = (db.fetchone() or {}).get('cnt', 0)

        # Top games
        db.execute(f"""SELECT game_id, COUNT(*) as rounds, COALESCE(SUM(bet),0) as volume
                       FROM game_history WHERE created_at >= {since}
                       GROUP BY game_id ORDER BY volume DESC LIMIT 5""")
        top_games = [dict(r) for r in db.fetchall()]

        # Tickets stats
        db.execute("SELECT COUNT(*) as cnt FROM support_tickets WHERE status='open'")
        open_tickets = (db.fetchone() or {}).get('cnt', 0)

        db.execute("SELECT COUNT(*) as cnt FROM support_tickets WHERE status='in_progress'")
        progress_tickets = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({
        'period': period,
        'users': {
            'total': total_users, 'new': new_users,
            'verified': verified_users, 'active': active_users,
        },
        'deposits':  {'count': dep.get('cnt',0),        'amount': round(float(dep.get('total') or 0), 2)},
        'withdrawals':{'count': wd.get('cnt',0),         'amount': round(float(wd.get('total') or 0), 2)},
        'pending_withdrawals': {'count': pending_wd.get('cnt',0), 'amount': round(float(pending_wd.get('total') or 0), 2)},
        'ggr':   ggr,
        'bets':  round(bets, 2),
        'wins':  round(wins, 2),
        'rounds': rounds,
        'top_games': top_games,
        'tickets': {'open': open_tickets, 'in_progress': progress_tickets},
    })


# ═══════════════════════════════════════════════════════════════
# CEO — PAYMENTS QUEUE
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/payments', methods=['GET'])
@require_admin
def payments_queue(admin):
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403

    status = request.args.get('status', 'pending')
    page   = max(1, int(request.args.get('page', 1)))
    limit  = 25
    offset = (page - 1) * limit

    with get_casino_db() as db:
        db.execute(
            "SELECT po.*, u.user_name, u.email "
            "FROM payment_orders po LEFT JOIN users u ON u.id = po.user_id "
            "WHERE po.status=%s ORDER BY po.created_at DESC LIMIT %s OFFSET %s",
            (status, limit, offset)
        )
        orders = [dict(r) for r in db.fetchall()]
        db.execute("SELECT COUNT(*) as cnt FROM payment_orders WHERE status=%s", (status,))
        total = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({'orders': orders, 'total': total, 'page': page,
                    'pages': max(1, (total + limit - 1) // limit)})


# ═══════════════════════════════════════════════════════════════
# CEO — SITE MODE TOGGLE
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/site-mode', methods=['GET'])
@require_admin
def get_site_mode(admin):
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403
    try:
        with open(_SITE_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        return jsonify({'mode': cfg.get('mode', 'online')})
    except Exception:
        return jsonify({'mode': 'unknown'})


@admin_panel_bp.route('/site-mode', methods=['POST'])
@require_admin
def set_site_mode(admin):
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403
    mode = (request.get_json() or {}).get('mode', 'online')
    if mode not in ('online', 'offline'):
        return jsonify({'error': 'Invalid mode'}), 400
    try:
        with open(_SITE_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        cfg['mode'] = mode
        with open(_SITE_JSON, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True, 'mode': mode})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# CEO — TEAM OVERVIEW
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/team', methods=['GET'])
@require_admin
def team_overview(admin):
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403

    with get_admin_db() as db:
        db.execute("SELECT id, nickname, public_id, role, email, created_at FROM support_admins ORDER BY created_at ASC")
        members = [dict(m) for m in db.fetchall()]

    # Enrich with ticket stats from casino db
    with get_casino_db() as db:
        for m in members:
            db.execute(
                "SELECT COUNT(*) as cnt FROM support_tickets WHERE assigned_to_id=%s AND status='closed'",
                (m['id'],)
            )
            m['closed_tickets'] = (db.fetchone() or {}).get('cnt', 0)
            db.execute(
                "SELECT COUNT(*) as cnt FROM support_tickets WHERE assigned_to_id=%s AND status='in_progress'",
                (m['id'],)
            )
            m['active_tickets'] = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({'team': members})


# ═══════════════════════════════════════════════════════════════
# ADMINISTRATOR — TEAM MANAGEMENT
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/team/<int:mid>/role', methods=['PUT'])
@require_admin
def change_role(mid, admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403
    role = (request.get_json() or {}).get('role', '').lower()
    valid = ('helper', 'op', 'admin', 'ceo', 'soc', 'fm', 'dsgn', 'devops', 'developer')
    if role not in valid:
        return jsonify({'error': f'Invalid role. Valid: {", ".join(valid)}'}), 400
    with get_admin_db() as db:
        db.execute("UPDATE support_admins SET role=%s WHERE id=%s", (role, mid))
    return jsonify({'ok': True})


@admin_panel_bp.route('/team/<int:mid>', methods=['DELETE'])
@require_admin
def remove_member(mid, admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403
    if mid == admin.id:
        return jsonify({'error': 'Cannot remove yourself'}), 400
    with get_admin_db() as db:
        db.execute("DELETE FROM support_admins WHERE id=%s", (mid,))
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════
# ADMINISTRATOR — BAN LIST
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/bans', methods=['GET'])
@require_admin
def get_bans(admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403
    try:
        with open(_SECURITY_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        return jsonify({'blocked_ips': cfg.get('blocked_ips', [])})
    except Exception:
        return jsonify({'blocked_ips': []})


@admin_panel_bp.route('/bans', methods=['POST'])
@require_admin
def add_ban(admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403
    ip = (request.get_json() or {}).get('ip', '').strip()
    if not ip:
        return jsonify({'error': 'IP required'}), 400
    try:
        with open(_SECURITY_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        blocked = cfg.get('blocked_ips', [])
        if ip not in blocked:
            blocked.append(ip)
            cfg['blocked_ips'] = blocked
            with open(_SECURITY_JSON, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True, 'blocked_ips': blocked})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/bans', methods=['DELETE'])
@require_admin
def remove_ban(admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403
    ip = (request.get_json() or {}).get('ip', '').strip()
    try:
        with open(_SECURITY_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        blocked = [b for b in cfg.get('blocked_ips', []) if b != ip]
        cfg['blocked_ips'] = blocked
        with open(_SECURITY_JSON, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify({'ok': True, 'blocked_ips': blocked})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ═══════════════════════════════════════════════════════════════
# ADMINISTRATOR — BROADCAST EMAIL
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/broadcast', methods=['POST'])
@require_admin
def broadcast(admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403

    d       = request.get_json() or {}
    subject = (d.get('subject') or '').strip()
    message = (d.get('message') or '').strip()
    target  = d.get('target', 'all')  # all | verified | depositors

    if not subject or not message:
        return jsonify({'error': 'Subject and message required'}), 400

    with get_casino_db() as db:
        if target == 'verified':
            db.execute("SELECT email FROM users WHERE email IS NOT NULL AND verified=1")
        elif target == 'depositors':
            db.execute(
                "SELECT DISTINCT u.email FROM users u "
                "JOIN payment_orders po ON po.user_id=u.id "
                "WHERE u.email IS NOT NULL AND po.type='deposit' AND po.status='completed'"
            )
        else:
            db.execute("SELECT email FROM users WHERE email IS NOT NULL")
        emails = [r['email'] for r in db.fetchall() if r.get('email')]

    if not emails:
        return jsonify({'error': 'No recipients found'}), 400

    # Send async to avoid timeout
    import threading
    def _send_all():
        from app.services.email_service import _send_email
        from_name = 'VELOX Casino'
        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#07071a;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:560px;margin:32px auto;background:#0e0e22;border-radius:14px;
            border:1px solid rgba(255,255,255,.07);overflow:hidden">
  <div style="padding:22px 28px 16px;border-bottom:1px solid rgba(255,255,255,.06)">
    <span style="font-size:18px;font-weight:700;color:#e6e6f5">⚡ VELOX</span>
  </div>
  <div style="padding:26px 28px;font-size:14px;color:#c8c8e0;line-height:1.7;white-space:pre-wrap">{message}</div>
  <div style="padding:14px 28px;border-top:1px solid rgba(255,255,255,.06);
              text-align:center;font-size:11px;color:#44445a">{from_name}</div>
</div></body></html>"""
        sent = 0
        for email in emails:
            try:
                _send_email(email, subject, html)
                sent += 1
            except Exception:
                pass
        print(f'  [broadcast] Sent {sent}/{len(emails)}')

    threading.Thread(target=_send_all, daemon=True).start()
    return jsonify({'ok': True, 'recipients': len(emails),
                    'message': f'Sending to {len(emails)} users in background'})


@admin_panel_bp.route('/broadcast/audience', methods=['GET'])
@require_admin
def broadcast_audience(admin):
    if not _admin_only(admin):
        return jsonify({'error': 'Access denied'}), 403

    target = request.args.get('target', 'all')
    with get_casino_db() as db:
        if target == 'verified':
            db.execute("SELECT COUNT(*) as cnt FROM users WHERE email IS NOT NULL AND verified=1")
        elif target == 'depositors':
            db.execute(
                "SELECT COUNT(DISTINCT u.id) as cnt FROM users u "
                "JOIN payment_orders po ON po.user_id=u.id "
                "WHERE u.email IS NOT NULL AND po.type='deposit' AND po.status='completed'"
            )
        else:
            db.execute("SELECT COUNT(*) as cnt FROM users WHERE email IS NOT NULL")
        row = db.fetchone() or {}
    return jsonify({'ok': True, 'target': target, 'count': int(row.get('cnt') or 0)})


# ═══════════════════════════════════════════════════════════════
# ADMINISTRATOR/CEO — GAME BUILDER (syncs fixed_out)
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/game-builder/provider-games', methods=['GET'])
@require_admin
def gb_list_provider_games(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    cfg = _read_provider_catalog()
    return jsonify({'games': cfg.get('games', []), 'total': len(cfg.get('games', []))})


@admin_panel_bp.route('/game-builder/all-games', methods=['GET'])
@require_admin
def gb_list_all_games(admin):
    if not _gb_role_ok(admin) and not _site_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    games = _collect_games_for_campaign_picker()
    return jsonify({'games': games, 'total': len(games)})


@admin_panel_bp.route('/game-builder/provider-games', methods=['POST'])
@require_admin
def gb_upsert_provider_game(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    d = request.get_json() or {}
    required = ['name', 'provider', 'provider_tag', 'game_tag', 'type_badge', 'api_key', 'endpoint', 'game_url']
    missing = [k for k in required if not str(d.get(k, '')).strip()]
    if missing:
        return jsonify({'error': f'Missing fields: {", ".join(missing)}'}), 400

    cfg = _read_provider_catalog()
    games = cfg.setdefault('games', [])
    prov_tag = _slugify(str(d.get('provider_tag')))
    game_id = str(d.get('id', '')).strip() or f"{prov_tag}_{_slugify(str(d.get('name')), 'game')}"

    badge = str(d.get('type_badge') or 'new').lower()
    game = {
        'id': game_id,
        'name': str(d.get('name')).strip(),
        'provider': str(d.get('provider')).strip(),
        'provider_tag': prov_tag,
        'category': str(d.get('category') or 'slots').strip().lower(),
        'tags': [str(d.get('game_tag')).strip().lower()],
        'badge_type': badge,
        'api_key': str(d.get('api_key')).strip(),
        'endpoint': str(d.get('endpoint')).strip(),
        'game_url': str(d.get('game_url')).strip(),
        'active': bool(d.get('active', True)),
        'hot': badge in ('hot', 'hit', 'top'),
        'new': badge in ('new', 'fresh'),
        'rtp': float(d.get('rtp', 0.96) or 0.96),
        'icon': str(d.get('icon') or '🎰'),
        'logo': str(d.get('logo') or ''),
        'cover': str(d.get('cover') or ''),
        'description': str(d.get('description') or ''),
        'metadata': {
            'provider_game_code': str(d.get('provider_game_code') or ''),
            'provider_table_id': str(d.get('provider_table_id') or ''),
        },
    }
    i = next((idx for idx, g in enumerate(games) if str(g.get('id')) == game_id), None)
    action = 'created'
    if i is None:
        games.append(game)
    else:
        games[i].update(game)
        action = 'updated'

    cfg['provider'] = 'Provider Catalog'
    cfg['provider_tag'] = 'provider_catalog'
    cfg['games'] = games
    _write_provider_catalog(cfg)
    return jsonify({'ok': True, 'action': action, 'game': game})


@admin_panel_bp.route('/game-builder/provider-games/<game_id>/rtp', methods=['POST'])
@require_admin
def gb_provider_rtp(admin, game_id):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    d = request.get_json() or {}
    try:
        rtp = float(d.get('rtp'))
    except Exception:
        return jsonify({'error': 'rtp must be number'}), 400
    if rtp <= 0 or rtp > 1.2:
        return jsonify({'error': 'rtp must be in (0, 1.2]'}), 400
    cfg = _read_provider_catalog()
    for g in cfg.get('games', []):
        if str(g.get('id')) == str(game_id):
            g['rtp'] = rtp
            _write_provider_catalog(cfg)
            return jsonify({'ok': True, 'id': game_id, 'rtp': rtp})
    return jsonify({'error': 'Game not found'}), 404


@admin_panel_bp.route('/game-builder/provider-games/<game_id>/toggle', methods=['POST'])
@require_admin
def gb_provider_toggle(admin, game_id):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    active = bool((request.get_json() or {}).get('active', True))
    cfg = _read_provider_catalog()
    for g in cfg.get('games', []):
        if str(g.get('id')) == str(game_id):
            g['active'] = active
            _write_provider_catalog(cfg)
            return jsonify({'ok': True, 'id': game_id, 'active': active})
    return jsonify({'error': 'Game not found'}), 404


@admin_panel_bp.route('/game-builder/provider-games/<game_id>', methods=['DELETE'])
@require_admin
def gb_provider_delete(admin, game_id):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    cfg = _read_provider_catalog()
    before = len(cfg.get('games', []))
    cfg['games'] = [g for g in cfg.get('games', []) if str(g.get('id')) != str(game_id)]
    if len(cfg['games']) == before:
        return jsonify({'error': 'Game not found'}), 404
    _write_provider_catalog(cfg)
    return jsonify({'ok': True, 'deleted': game_id})


@admin_panel_bp.route('/game-builder/sport-events', methods=['GET'])
@require_admin
def gb_sport_list(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    cfg = _read_json(_sport_cfg_path(), {'events': []})
    return jsonify({'events': cfg.get('events', []), 'total': len(cfg.get('events', []))})


@admin_panel_bp.route('/game-builder/sport-tags', methods=['GET'])
@require_admin
def gb_sport_tags(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    cfg = _read_json(_sport_cfg_path(), {'tags': []})
    return jsonify({'tags': cfg.get('tags', [])})


@admin_panel_bp.route('/game-builder/sport-events', methods=['POST'])
@require_admin
def gb_sport_upsert(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    d = request.get_json() or {}
    must = ['league', 'home_team', 'away_team', 'starts_at']
    miss = [k for k in must if not str(d.get(k, '')).strip()]
    if miss:
        return jsonify({'error': f'Missing fields: {", ".join(miss)}'}), 400

    path = _sport_cfg_path()
    cfg = _read_json(path, {'events': []})
    events = cfg.setdefault('events', [])
    event_id = str(d.get('id', '')).strip()
    if not event_id:
        n = len(events) + 1
        ids = {str(e.get('id', '')) for e in events}
        while f'match_{n}' in ids:
            n += 1
        event_id = f'match_{n}'

    is_live_stream = bool(d.get('is_live_stream', False))
    match_status = str(d.get('match_status') or ('live' if is_live_stream else 'planned')).strip().lower()
    tag = str(d.get('tag') or 'football').strip().lower()
    background = str(d.get('background') or d.get('cover') or '').strip()
    sport_type = str(d.get('sport_type') or 'sport').strip().lower() or 'sport'

    cfg_tags = cfg.get('tags', []) if isinstance(cfg.get('tags', []), list) else []
    tag_icon = next((str(t.get('icon_path') or '').strip() for t in cfg_tags if str(t.get('id', '')).strip().lower() == tag), '')
    icon_png = str(d.get('icon_png') or tag_icon or '').strip()
    is_top_match = bool(d.get('match_today', False))
    mode = str(d.get('mode') or 'manual').strip().lower()

    ev = {
        'id': event_id,
        'enabled': bool(d.get('enabled', True)),
        'sport_type': sport_type,
        'match_status': match_status,
        'tag': tag,
        'category': str(d.get('category') or tag.title()).strip(),
        'country': str(d.get('country') or '').strip(),
        'league': str(d.get('league')).strip(),
        'home_team': str(d.get('home_team')).strip(),
        'away_team': str(d.get('away_team')).strip(),
        'starts_at': str(d.get('starts_at')).strip(),
        'match_td_utc': str(d.get('match_td_utc') or d.get('starts_at')).strip(),
        'background': background,
        'cover': background,
        'icon_png': icon_png,
        'is_live_stream': is_live_stream,
        'match_today': is_top_match,
        'mode': mode,
        'automation': d.get('automation') or {},
        'odds': d.get('odds') or {},
        'match_center': {
            **(d.get('match_center') or {}),
            'video_enabled': is_live_stream,
        },
        'markets': d.get('markets') or {},
        'score': d.get('score') or {'home': 0, 'away': 0},
        'period_scores': d.get('period_scores') or [],
    }
    idx = next((i for i, x in enumerate(events) if str(x.get('id')) == event_id), None)
    action = 'created'
    if idx is None:
        events.append(ev)
    else:
        events[idx].update(ev)
        action = 'updated'
    cfg['events'] = events
    _write_json(path, cfg)
    return jsonify({'ok': True, 'action': action, 'event': ev})


@admin_panel_bp.route('/game-builder/sport-events/upload-background', methods=['POST'])
@require_admin
def gb_sport_upload_background(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'File required'}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return jsonify({'error': 'Invalid file type'}), 400

    import uuid as _uuid
    os.makedirs(_SPORT_MATCH_UPLOADS, exist_ok=True)
    fname = f'{_uuid.uuid4().hex}{ext}'
    full_path = os.path.join(_SPORT_MATCH_UPLOADS, fname)
    f.save(full_path)
    rel = f'/static/uploads/bukmaking/matches/{fname}'
    return jsonify({'ok': True, 'background': rel})


@admin_panel_bp.route('/game-builder/sport-tags/upload-icon', methods=['POST'])
@require_admin
def gb_sport_upload_icon(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403

    tag = (request.form.get('tag') or '').strip().lower()
    if not tag:
        return jsonify({'error': 'tag required'}), 400

    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'File required'}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.svg'):
        return jsonify({'error': 'Invalid file type'}), 400

    import uuid as _uuid
    os.makedirs(_SPORT_ICON_UPLOADS, exist_ok=True)
    fname = f'{tag}_{_uuid.uuid4().hex[:8]}{ext}'
    full_path = os.path.join(_SPORT_ICON_UPLOADS, fname)
    f.save(full_path)
    rel = f'/static/uploads/bukmaking/icons/{fname}'

    path = _sport_cfg_path()
    cfg = _read_json(path, {'tags': [], 'events': []})
    tags = cfg.setdefault('tags', [])
    idx = next((i for i, t in enumerate(tags) if str(t.get('id', '')).strip().lower() == tag), None)
    if idx is None:
        tags.append({'id': tag, 'label': tag.title(), 'icon_path': rel})
    else:
        tags[idx]['icon_path'] = rel
    cfg['tags'] = tags
    _write_json(path, cfg)
    return jsonify({'ok': True, 'tag': tag, 'icon_path': rel, 'tags': tags})


@admin_panel_bp.route('/game-builder/sport-events/<event_id>/toggle', methods=['POST'])
@require_admin
def gb_sport_toggle(admin, event_id):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    enabled = bool((request.get_json() or {}).get('enabled', True))
    path = _sport_cfg_path()
    cfg = _read_json(path, {'events': []})
    for e in cfg.get('events', []):
        if str(e.get('id')) == str(event_id):
            e['enabled'] = enabled
            _write_json(path, cfg)
            return jsonify({'ok': True, 'id': event_id, 'enabled': enabled})
    return jsonify({'error': 'Event not found'}), 404


@admin_panel_bp.route('/users/<int:uid>/topup', methods=['POST'])
@require_admin
def user_topup(admin, uid):
    """Manually adjust a player's balance (deposit or deduction)."""
    if not _ceo_or_admin(admin):
        return jsonify({'error': 'Access denied'}), 403

    body   = request.get_json(force=True) or {}
    amount = body.get('amount')
    reason = (body.get('reason') or '').strip()[:200]

    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid amount'}), 400

    if amount == 0:
        return jsonify({'error': 'Amount must not be zero'}), 400

    p = '%s' if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql' else '?'

    with get_casino_db() as db:
        db.execute(f'SELECT id, balance, currency FROM users WHERE id = {p}', (uid,))
        user = db.fetchone()

    if not user:
        return jsonify({'error': 'User not found'}), 404

    user = dict(user)
    new_balance = round(float(user['balance'] or 0) + amount, 2)

    if new_balance < 0:
        return jsonify({'error': 'Balance cannot go below zero'}), 400

    import json as _json
    from datetime import datetime as _dt

    meta_json = _json.dumps({
        'reason':   reason or 'manual adjustment',
        'admin_id': admin.id,
        'admin':    admin.nickname,
    })
    tx_type = 'admin_topup' if amount > 0 else 'admin_deduct'

    if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql':
        with get_casino_db() as db:
            db.execute(
                f'UPDATE users SET balance = {p} WHERE id = {p}',
                (new_balance, uid)
            )
            db.execute(
                f"INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                f" VALUES ({p},{p},{p},'completed',{p}::jsonb, NOW())",
                (uid, tx_type, amount, meta_json)
            )
    else:
        with get_casino_db() as db:
            db.execute(
                f"UPDATE users SET balance = {p} WHERE id = {p}",
                (new_balance, uid)
            )
            db.execute(
                f"INSERT INTO transactions (user_id, type, amount, status, meta, created_at)"
                f" VALUES ({p},{p},{p},'completed',{p}, datetime('now'))",
                (uid, tx_type, amount, meta_json)
            )

    return jsonify({
        'ok':          True,
        'new_balance': new_balance,
        'currency':    user.get('currency', ''),
        'amount':      amount,
    })


@admin_panel_bp.route('/game-builder/sport-events/<event_id>', methods=['DELETE'])
@require_admin
def gb_sport_delete(admin, event_id):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    path = _sport_cfg_path()
    cfg = _read_json(path, {'events': []})
    before = len(cfg.get('events', []))
    cfg['events'] = [e for e in cfg.get('events', []) if str(e.get('id')) != str(event_id)]
    if len(cfg['events']) == before:
        return jsonify({'error': 'Event not found'}), 404
    _write_json(path, cfg)
    return jsonify({'ok': True, 'deleted': event_id})


@admin_panel_bp.route('/game-builder/sport-provider/catalog', methods=['GET'])
@require_admin
def gb_sport_provider_catalog(admin):
    """Сводка линии провайдера (fixed_out merge без фильтра видимости) + текущий режим показа."""
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_CASINO_DIR, '.env'))
        from app.services.casino_sport_odds import load_casino_sport_odds_feed
        raw = load_casino_sport_odds_feed().get_merged_provider_events_preview()
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500

    vis = _read_json(_VISIBILITY_JSON, {'mode': 'all', 'allowed_ids': []})
    ov = _read_json(_PROV_OVERRIDES_JSON, {'streams': {}})
    streams = ov.get('streams') if isinstance(ov.get('streams'), dict) else {}
    allowed = {str(x) for x in (vis.get('allowed_ids') or [])}
    mode = str(vis.get('mode') or 'all').lower()
    rows = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        eid = str(e.get('id') or '')
        is_prov = _is_provider_feed_event_id(eid)
        if mode == 'listed':
            vis_site = (not is_prov) or (eid in allowed)
        else:
            vis_site = True
        auto = e.get('automation') if isinstance(e.get('automation'), dict) else {}
        mc = e.get('match_center') if isinstance(e.get('match_center'), dict) else {}
        provider_stream = str(mc.get('video_embed_url') or '').strip()
        manual_stream = str(streams.get(eid) or '').strip() if is_prov else ''
        effective_stream = manual_stream or provider_stream
        if provider_stream:
            stream_source = 'provider'
        elif manual_stream:
            stream_source = 'manual'
        else:
            stream_source = ''
        rows.append({
            'id': eid,
            'home_team': e.get('home_team'),
            'away_team': e.get('away_team'),
            'league': e.get('league'),
            'country': e.get('country'),
            'category': e.get('category'),
            'tag': e.get('tag'),
            'starts_at': e.get('starts_at') or e.get('match_td_utc'),
            'match_status': e.get('match_status'),
            'odds': e.get('odds'),
            'mode': e.get('mode'),
            'provider': auto.get('provider'),
            'is_provider': is_prov,
            'visible_on_site': vis_site,
            'stream_url': (effective_stream if is_prov else ''),
            'stream_source': (stream_source if is_prov else ''),
        })
    return jsonify({'ok': True, 'visibility': vis, 'events': rows})


@admin_panel_bp.route('/game-builder/sport-provider/stream', methods=['POST'])
@require_admin
def gb_sport_provider_stream(admin):
    """Сохранить/очистить стрим для матча провайдерской линии."""
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    body = request.get_json(silent=True) or {}
    eid = str(body.get('event_id') or '').strip()
    url = str(body.get('video_embed_url') or '').strip()
    if not eid or not _is_provider_feed_event_id(eid):
        return jsonify({'error': 'event_id must be a provider line id'}), 400
    def _normalize_embed(u: str) -> str:
        raw = str(u or '').strip()
        if not raw:
            return ''
        try:
            import urllib.parse
            pu = urllib.parse.urlparse(raw)
            host = (pu.hostname or '').lower().replace('www.', '')
            if host in ('youtube.com', 'm.youtube.com'):
                qs = urllib.parse.parse_qs(pu.query or '')
                vid = (qs.get('v') or [''])[0]
                if vid:
                    return f'https://www.youtube.com/embed/{vid}'
            if host == 'youtu.be':
                seg = [x for x in (pu.path or '').split('/') if x]
                if seg:
                    return f'https://www.youtube.com/embed/{seg[0]}'
            if host.endswith('youtube.com') and (pu.path or '').startswith('/embed/'):
                return raw
        except Exception:
            return raw
        return raw

    url = _normalize_embed(url)
    cfg = _read_json(_PROV_OVERRIDES_JSON, {'streams': {}, 'updated_at': ''})
    if not isinstance(cfg.get('streams'), dict):
        cfg['streams'] = {}
    if url:
        cfg['streams'][eid] = url
    else:
        cfg['streams'].pop(eid, None)
    try:
        from datetime import datetime
        cfg['updated_at'] = datetime.utcnow().isoformat()
    except Exception:
        pass
    _write_json(_PROV_OVERRIDES_JSON, cfg)
    _invalidate_casino_odds_cache()
    return jsonify({'ok': True})


@admin_panel_bp.route('/game-builder/sport-provider/visibility', methods=['POST'])
@require_admin
def gb_sport_provider_visibility(admin):
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    body = request.get_json(silent=True) or {}
    mode = str(body.get('mode') or 'all').lower()
    if mode not in ('all', 'listed'):
        mode = 'all'
    vis = _read_json(_VISIBILITY_JSON, {'mode': 'all', 'allowed_ids': []})
    vis['mode'] = mode
    if isinstance(body.get('allowed_ids'), list):
        vis['allowed_ids'] = [str(x) for x in body['allowed_ids']]
    _write_json(_VISIBILITY_JSON, vis)
    _invalidate_casino_odds_cache()
    return jsonify({'ok': True, 'visibility': vis})


@admin_panel_bp.route('/game-builder/sport-provider/toggle', methods=['POST'])
@require_admin
def gb_sport_provider_toggle(admin):
    """Показать/скрыть один матч линии на сайте (переключает mode в listed при показе)."""
    if not _gb_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    body = request.get_json(silent=True) or {}
    eid = str(body.get('event_id') or '').strip()
    visible = bool(body.get('visible'))
    if not eid or not _is_provider_feed_event_id(eid):
        return jsonify({'error': 'event_id must be a provider line id'}), 400
    vis = _read_json(_VISIBILITY_JSON, {'mode': 'all', 'allowed_ids': []})
    ids = [str(x) for x in (vis.get('allowed_ids') or [])]
    if visible:
        if eid not in ids:
            ids.append(eid)
        vis['mode'] = 'listed'
        vis['allowed_ids'] = ids
    else:
        vis['allowed_ids'] = [x for x in ids if x != eid]
    _write_json(_VISIBILITY_JSON, vis)
    _invalidate_casino_odds_cache()
    return jsonify({'ok': True, 'visibility': vis})


# ═══════════════════════════════════════════════════════════════
# VIP — выдача статуса игроку
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/users/<int:uid>/vip', methods=['POST'])
@require_admin
def user_give_vip(admin, uid):
    allowed = ('ceo', 'admin', 'marketing')
    if admin.role not in allowed:
        return jsonify({'error': 'Access denied'}), 403
    d    = request.get_json(force=True) or {}
    tier = str(d.get('tier', 'platinum')).lower()
    days = int(d.get('days', 30))
    valid_tiers = ('bronze', 'silver', 'gold', 'platinum', 'diamond')
    if tier not in valid_tiers:
        return jsonify({'error': f'Invalid tier. Valid: {", ".join(valid_tiers)}'}), 400
    from datetime import datetime, timedelta
    if days == 0:
        expires = None  # вечный
    else:
        expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    p = '%s' if os.environ.get('DB_ENGINE', 'sqlite').lower() == 'postgresql' else '?'
    with get_casino_db() as db:
        db.execute(f'SELECT id FROM users WHERE id={p}', (uid,))
        if not db.fetchone():
            return jsonify({'error': 'User not found'}), 404
    with get_casino_db() as db:
        db.execute(
            f"UPDATE users SET vip_tier={p}, vip_expires_at={p} WHERE id={p}",
            (tier, expires, uid)
        )
    return jsonify({'ok': True, 'tier': tier, 'days': days, 'expires_at': expires})


# ═══════════════════════════════════════════════════════════════
# BONUS — единоразовые бонусы
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/users/<int:uid>/bonus', methods=['POST'])
@require_admin
def user_give_bonus(admin, uid):
    allowed = ('ceo', 'admin', 'marketing')
    if admin.role not in allowed:
        return jsonify({'error': 'Access denied'}), 403
    d = request.get_json(force=True) or {}
    bonus_type = d.get('type', 'cash')
    p = '%s' if os.environ.get('DB_ENGINE','sqlite').lower()=='postgresql' else '?'

    with get_casino_db() as db:
        db.execute(f'SELECT id, balance, currency FROM users WHERE id={p}', (uid,))
        user = db.fetchone()
    if not user:
        return jsonify({'error': 'User not found'}), 404
    user = dict(user)

    if bonus_type == 'cash':
        amount = round(float(d.get('amount', 0)), 2)
        if amount <= 0:
            return jsonify({'error': 'Amount must be > 0'}), 400
        new_balance = round(float(user['balance'] or 0) + amount, 2)
        import json as _json
        meta = _json.dumps({'type': 'one_time_cash_bonus', 'reason': d.get('note',''), 'admin': admin.nickname})
        with get_casino_db() as db:
            db.execute(f'UPDATE users SET balance={p} WHERE id={p}', (new_balance, uid))
            now_fn = "NOW()" if p == "%s" else "datetime('now')"
            db.execute(f"INSERT INTO transactions (user_id,type,amount,status,meta,created_at) VALUES ({p},{p},{p},'completed',{p},{now_fn})",
                       (uid, 'bonus', amount, meta))
        return jsonify({'ok': True, 'type': 'cash', 'amount': amount, 'new_balance': new_balance})

    elif bonus_type == 'freespins':
        count = int(d.get('count', 0))
        game  = str(d.get('game', '')).strip()
        if count <= 0:
            return jsonify({'error': 'Count must be > 0'}), 400
        import json as _json
        meta = _json.dumps({
            'type': 'freespins_bonus',
            'game': game or 'any',
            'admin': admin.nickname
        })
        with get_casino_db() as db:
            db.execute(
                f"UPDATE users SET free_spins = COALESCE(free_spins, 0) + {p} WHERE id={p}",
                (count, uid)
            )
            now_fn = "NOW()" if p == "%s" else "datetime('now')"
            db.execute(
                f"INSERT INTO transactions (user_id,type,amount,status,meta,created_at) "
                f"VALUES ({p},{p},{p},'completed',{p},{now_fn})",
                (uid, 'freespins_bonus', count, meta)
            )
        # Read updated free_spins value
        with get_casino_db() as db:
            db.execute(f"SELECT free_spins FROM users WHERE id={p}", (uid,))
            row = db.fetchone()
        new_fs = dict(row)['free_spins'] if row else count
        return jsonify({'ok': True, 'type': 'freespins', 'count': count, 'game': game, 'new_free_spins': new_fs})

    elif bonus_type == 'deposit_boost':
        percent = int(d.get('percent', 0))
        hours   = int(d.get('hours', 24))
        if percent <= 0:
            return jsonify({'error': 'Percent must be > 0'}), 400
        import json as _json
        from datetime import datetime, timedelta
        expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        meta = _json.dumps({
            'type': 'deposit_boost',
            'percent': percent,
            'hours': hours,
            'expires_at': expires_at,
            'admin': admin.nickname
        })
        with get_casino_db() as db:
            now_fn = "NOW()" if p == "%s" else "datetime('now')"
            db.execute(
                f"INSERT INTO transactions (user_id,type,amount,status,meta,created_at) "
                f"VALUES ({p},{p},{p},'pending',{p},{now_fn})",
                (uid, 'deposit_boost', percent, meta)
            )
            db.execute(
                f"UPDATE users SET bonus_deposit = COALESCE(bonus_deposit, 0) + {p} WHERE id={p}",
                (percent, uid)
            )
        return jsonify({'ok': True, 'type': 'deposit_boost', 'percent': percent, 'hours': hours, 'expires_at': expires_at})

    elif bonus_type == 'extend_vip':
        days = int(d.get('days', 0))
        if days <= 0:
            return jsonify({'error': 'Days must be > 0'}), 400
        from datetime import datetime, timedelta
        with get_casino_db() as db:
            db.execute(f'SELECT vip_expires_at FROM users WHERE id={p}', (uid,))
            row = db.fetchone()
        exp = None
        if row and row['vip_expires_at']:
            try:
                exp = datetime.fromisoformat(str(row['vip_expires_at'])) + timedelta(days=days)
            except Exception:
                exp = datetime.utcnow() + timedelta(days=days)
        else:
            exp = datetime.utcnow() + timedelta(days=days)
        with get_casino_db() as db:
            db.execute(f"UPDATE users SET vip_expires_at={p} WHERE id={p}", (exp.isoformat(), uid))
        return jsonify({'ok': True, 'type': 'extend_vip', 'days': days, 'new_expires_at': exp.isoformat()})

    return jsonify({'error': f'Unknown bonus type: {bonus_type}'}), 400


# ═══════════════════════════════════════════════════════════════
# BANNERS — управление рекламными баннерами
# ═══════════════════════════════════════════════════════════════

_BANNERS_JSON   = os.path.join(_CASINO_DIR, 'config', 'banners.json')
_BANNER_UPLOADS = os.path.join(_CASINO_DIR, 'static', 'uploads', 'banners')
_SITE_CONFIG_DIR = os.path.join(_CASINO_DIR, 'config')
_SUPPORT_CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'config')


def _load_banners():
    try:
        cfg = _load_banners_cfg()
        banners = cfg.get('page_banners', [])
        # Normalise title: support panel stores it as _title, casino has no title field
        for b in banners:
            if not b.get('title'):
                b['title'] = b.get('_title') or b.get('id', '—')
        return banners
    except Exception:
        return []


def _load_banners_cfg():
    try:
        with open(_BANNERS_JSON, encoding='utf-8') as f:
            cfg = json.load(f)
        changed = False
        for b in cfg.get('page_banners', []):
            for key in ('image_url', 'image_ad'):
                v = str(b.get(key) or '').strip()
                if v.startswith('/support_uploads/banners/'):
                    fname = os.path.basename(v)
                    casino_candidate = f'/static/uploads/banners/{fname}'
                    if os.path.isfile(os.path.join(_CASINO_DIR, 'static', 'uploads', 'banners', fname)):
                        b[key] = casino_candidate
                        changed = True
        if changed:
            _write_json(_BANNERS_JSON, cfg)
        return cfg
    except Exception:
        return {'page_banners': [], 'sidebar_banner': {}}


def _save_banners(banners_list):
    """Save page_banners list back into casino banners.json preserving other keys."""
    cfg = _load_banners_cfg()
    cfg['page_banners'] = banners_list
    os.makedirs(os.path.dirname(_BANNERS_JSON), exist_ok=True)
    tmp = _BANNERS_JSON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _BANNERS_JSON)


def _save_banner_upload(file_obj):
    import uuid as _uuid
    ext = os.path.splitext(file_obj.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return '', '', 'Invalid file type'
    support_banners = os.path.join(
        os.path.dirname(__file__), '..', '..', 'support_uploads', 'banners'
    )
    os.makedirs(support_banners, exist_ok=True)
    fname = f'{_uuid.uuid4().hex}{ext}'
    support_path = os.path.join(support_banners, fname)
    file_obj.save(support_path)
    support_rel = f'/support_uploads/banners/{fname}'
    casino_rel = ''
    try:
        import shutil
        casino_banners = os.path.join(_CASINO_DIR, 'static', 'uploads', 'banners')
        os.makedirs(casino_banners, exist_ok=True)
        shutil.copy2(support_path, os.path.join(casino_banners, fname))
        casino_rel = f'/static/uploads/banners/{fname}'
    except Exception:
        pass
    return support_rel, casino_rel, ''


def _save_campaign_upload(file_obj):
    import uuid as _uuid
    ext = os.path.splitext(file_obj.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return '', '', 'Invalid file type'
    support_dir = os.path.join(
        os.path.dirname(__file__), '..', '..', 'support_uploads', 'campaigns'
    )
    os.makedirs(support_dir, exist_ok=True)
    fname = f'{_uuid.uuid4().hex}{ext}'
    support_path = os.path.join(support_dir, fname)
    file_obj.save(support_path)
    support_rel = f'/support_uploads/campaigns/{fname}'
    casino_rel = ''
    try:
        import shutil
        casino_dir = os.path.join(_CASINO_DIR, 'static', 'uploads', 'campaigns')
        os.makedirs(casino_dir, exist_ok=True)
        shutil.copy2(support_path, os.path.join(casino_dir, fname))
        casino_rel = f'/static/uploads/campaigns/{fname}'
    except Exception:
        pass
    return support_rel, casino_rel, ''


def _banner_role_ok(admin):
    return admin.role in ('ceo', 'admin', 'marketing')

def _site_cfg_path(name: str) -> str:
    safe = (name or '').strip().lower()
    if safe == 'roles_perms':
        return os.path.join(_SUPPORT_CONFIG_DIR, 'perms.json')
    allowed = {
        'site', 'sidebar', 'providers', 'payments', 'faq', 'site_mode',
        'promotions', 'raffles', 'vip', 'promo_settings', 'promo_codes',
    }
    if safe not in allowed:
        return ''
    return os.path.join(_SITE_CONFIG_DIR, f'{safe}.json')


@admin_panel_bp.route('/banners', methods=['GET'])
@require_admin
def banners_list(admin):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    banners = _load_banners()
    # Rewrite casino-relative image paths so the support panel can display them
    for b in banners:
        for key in ('image_url', 'image_ad'):
            v = b.get(key, '')
            if v and not v.startswith((
                'http://', 'https://',
                '/support_uploads/',
                '/api/admin/banners/casino-image',
            )):
                b[key] = '/api/admin/banners/casino-image?path=' + v.lstrip('/')
    return jsonify({'banners': banners})


@admin_panel_bp.route('/banners', methods=['POST'])
@require_admin
def banners_create(admin):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403

    from flask import request as req
    from datetime import datetime
    import uuid as _uuid

    title    = (req.form.get('title') or '').strip()
    position = (req.form.get('position') or 'main').strip()

    if not title:
        return jsonify({'error': 'Title required'}), 400

    # ── Image upload — save to support_uploads/banners/ + fixed_out/static/uploads/banners
    image_url = ''
    casino_rel = ''
    if 'file' in req.files:
        f = req.files['file']
        if f and f.filename:
            image_url, casino_rel, err = _save_banner_upload(f)
            if err:
                return jsonify({'error': err}), 400

    modal_text = (req.form.get('modal_text') or title).strip()
    timer      = int(req.form.get('timer_change') or 5)
    modal_win  = req.form.get('modal_window', 'true').lower() != 'false'

    # All supported link fields
    link_fields = {
        'modal_telegramButton': (req.form.get('modal_telegramButton') or '').strip(),
        'modal_youtubeButton':  (req.form.get('modal_youtubeButton')  or '').strip(),
        'modal_viberButton':    (req.form.get('modal_viberButton')    or '').strip(),
        'modal_whatsappButton': (req.form.get('modal_whatsappButton') or '').strip(),
        'modal_vkButton':       (req.form.get('modal_vkButton')       or '').strip(),
        'modal_instagramButton':(req.form.get('modal_instagramButton')or '').strip(),
        'modal_twitterButton':  (req.form.get('modal_twitterButton')  or '').strip(),
        'modal_urlButton':      (req.form.get('modal_urlButton')      or '').strip(),
    }
    link_order_raw = req.form.get('link_order', '[]')
    try:
        link_order = json.loads(link_order_raw)
        if not isinstance(link_order, list):
            link_order = []
    except Exception:
        link_order = []

    banners = _load_banners()
    new_id  = str(_uuid.uuid4().hex[:12])
    banner  = {
        'id':           new_id,
        'invisible':    False,
        'active':       True,
        'image_ad':     casino_rel or image_url,      # casino compat key
        'image_url':    casino_rel or image_url,      # support panel display key
        '_image_ad_size': '1280x340px',
        'modal_window': modal_win,
        'modal_text':   modal_text,
        'modal_video':  False,
        'modal_mp4':    '',
        'timer_change': timer,
        'link_order': link_order,
        # Link buttons
        **link_fields,
        # Support panel meta
        '_title':      title,
        '_position':   position,
        '_created_by': admin.nickname,
        '_created_at': datetime.utcnow().isoformat(),
    }
    banners.append(banner)
    _save_banners(banners)
    return jsonify({'ok': True, 'banner': banner})


@admin_panel_bp.route('/banners/<bid>', methods=['PUT'])
@require_admin
def banners_update(admin, bid):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    from flask import request as req
    body = req.get_json(silent=True) or {}

    banners = _load_banners()
    idx = next((i for i, b in enumerate(banners) if b.get('id') == bid), None)
    if idx is None:
        return jsonify({'error': 'Not found'}), 404

    b = banners[idx]
    if 'title' in body:
        b['_title'] = str(body.get('title') or '').strip() or b.get('_title') or b.get('title') or b.get('id')
        b['title'] = b['_title']
    if 'modal_text' in body:
        b['modal_text'] = str(body.get('modal_text') or '').strip()
    if 'timer_change' in body:
        try:
            b['timer_change'] = max(1, min(120, int(body.get('timer_change') or 5)))
        except Exception:
            pass
    if 'modal_window' in body:
        b['modal_window'] = bool(body.get('modal_window'))
    if 'position' in body:
        b['_position'] = str(body.get('position') or 'main').strip() or 'main'
    if 'link_order' in body and isinstance(body.get('link_order'), list):
        b['link_order'] = body.get('link_order')

    link_keys = (
        'modal_telegramButton', 'modal_youtubeButton', 'modal_viberButton',
        'modal_whatsappButton', 'modal_vkButton', 'modal_instagramButton',
        'modal_twitterButton', 'modal_urlButton'
    )
    for k in link_keys:
        if k in body:
            b[k] = str(body.get(k) or '').strip()

    banners[idx] = b
    _save_banners(banners)
    return jsonify({'ok': True, 'banner': b})


@admin_panel_bp.route('/banners/<bid>/image', methods=['POST'])
@require_admin
def banners_update_image(admin, bid):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    from flask import request as req
    f = req.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'File required'}), 400
    support_rel, casino_rel, err = _save_banner_upload(f)
    if err:
        return jsonify({'error': err}), 400

    banners = _load_banners()
    idx = next((i for i, b in enumerate(banners) if b.get('id') == bid), None)
    if idx is None:
        return jsonify({'error': 'Not found'}), 404
    rel = casino_rel or support_rel
    banners[idx]['image_ad'] = rel
    banners[idx]['image_url'] = rel
    _save_banners(banners)
    return jsonify({'ok': True, 'image': rel, 'banner': banners[idx]})


@admin_panel_bp.route('/banners/casino-image', methods=['GET'])
@require_admin
def banners_casino_image_proxy(admin):
    """Proxy casino static images so the support panel can display them."""
    from flask import request as req, send_file, abort as fl_abort
    import mimetypes
    rel = req.args.get('path', '').lstrip('/')
    if not rel or '..' in rel:
        fl_abort(400)
    full = os.path.normpath(os.path.join(_CASINO_DIR, 'static', rel.replace('static/', '', 1)))
    # Security: must stay inside _CASINO_DIR
    if not full.startswith(os.path.normpath(_CASINO_DIR)):
        fl_abort(403)
    if not os.path.isfile(full):
        fl_abort(404)
    mime = mimetypes.guess_type(full)[0] or 'application/octet-stream'
    return send_file(full, mimetype=mime)


@admin_panel_bp.route('/banners/<bid>/toggle', methods=['POST'])
@require_admin
def banners_toggle(admin, bid):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    active  = bool((request.get_json() or {}).get('active', True))
    banners = _load_banners()
    for b in banners:
        if b.get('id') == bid:
            b['invisible'] = not active  # casino uses invisible=true to hide
            b['active'] = active         # support panel compat field
            _save_banners(banners)
            return jsonify({'ok': True, 'active': active, 'invisible': not active})
    return jsonify({'error': 'Not found'}), 404


@admin_panel_bp.route('/banners/<bid>', methods=['DELETE'])
@require_admin
def banners_delete(admin, bid):
    if not _banner_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    banners = _load_banners()
    new_banners = [b for b in banners if b.get('id') != bid]
    if len(new_banners) == len(banners):
        return jsonify({'error': 'Not found'}), 404
    _save_banners(new_banners)
    return jsonify({'ok': True})

@admin_panel_bp.route('/site/config/<name>', methods=['GET'])
@require_admin
def site_config_get(admin, name):
    if not _site_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    path = _site_cfg_path(name)
    if not path:
        return jsonify({'error': 'Invalid config'}), 400
    try:
        if not os.path.exists(path):
            default_cfg = {'codes': []} if str(name).strip().lower() == 'promo_codes' else {}
            return jsonify({'ok': True, 'name': name, 'config': default_cfg})
        with open(path, encoding='utf-8') as f:
            return jsonify({'ok': True, 'name': name, 'config': json.load(f)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@admin_panel_bp.route('/site/config/<name>', methods=['POST'])
@require_admin
def site_config_set(admin, name):
    if not _site_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    path = _site_cfg_path(name)
    if not path:
        return jsonify({'error': 'Invalid config'}), 400
    body = request.get_json(silent=True) or {}
    data = body.get('config')
    if not isinstance(data, (dict, list)):
        return jsonify({'error': 'Config must be object or list'}), 400
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return jsonify({'ok': True, 'name': name})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_panel_bp.route('/site/config/campaigns-image', methods=['POST'])
@require_admin
def campaigns_upload_image(admin):
    if not _site_role_ok(admin):
        return jsonify({'error': 'Access denied'}), 403
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'File required'}), 400
    support_rel, casino_rel, err = _save_campaign_upload(f)
    if err:
        return jsonify({'error': err}), 400
    return jsonify({'ok': True, 'image': casino_rel or support_rel, 'support_image': support_rel})


# ═══════════════════════════════════════════════════════════════
# USER-AGENT / DEVICE TRACKING
# ═══════════════════════════════════════════════════════════════

@admin_panel_bp.route('/device-info', methods=['POST'])
@require_admin
def device_info(admin):
    """Store/retrieve client device info for the current admin session."""
    d = request.get_json(force=True) or {}
    device_type = d.get('type', 'unknown')
    os_name     = d.get('os', 'unknown')
    browser     = d.get('browser', 'unknown')
    ua_raw      = request.headers.get('User-Agent', '')
    return jsonify({
        'ok': True,
        'device': {
            'type': device_type,
            'os': os_name,
            'browser': browser,
            'ua': ua_raw[:200],
            'admin': admin.nickname
        }
    })


@admin_panel_bp.route('/device-info', methods=['GET'])
@require_admin
def device_info_get(admin):
    """Return server-side parsed device info from User-Agent header."""
    ua = request.headers.get('User-Agent', '')
    import re
    if re.search(r'Mobi|Android|iPhone|iPod', ua, re.I):
        dev_type = 'mobile'
    elif re.search(r'iPad|Tablet|PlayBook', ua, re.I):
        dev_type = 'tablet'
    else:
        dev_type = 'desktop'

    if re.search(r'Windows', ua, re.I):   os_name = 'Windows'
    elif re.search(r'Android', ua, re.I): os_name = 'Android'
    elif re.search(r'iPhone|iPad|iPod', ua, re.I): os_name = 'iOS'
    elif re.search(r'Mac OS', ua, re.I):  os_name = 'macOS'
    elif re.search(r'Linux', ua, re.I):   os_name = 'Linux'
    else:                                  os_name = 'Unknown'

    if re.search(r'Edg/', ua, re.I):      browser = 'Edge'
    elif re.search(r'Chrome/', ua, re.I): browser = 'Chrome'
    elif re.search(r'Firefox/', ua, re.I):browser = 'Firefox'
    elif re.search(r'Safari/', ua, re.I): browser = 'Safari'
    else:                                  browser = 'Other'

    return jsonify({'ok': True, 'device': {'type': dev_type, 'os': os_name, 'browser': browser}})