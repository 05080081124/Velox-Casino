"""app/api/games.py — Blueprint игр (список, избранное, история, страница)."""
import os, json
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session, Response
from app.services.game_service import game_service
from app.models.user import User
from app.models.game import GameRound
from app.models.tournament import Tournament
from app.db import get_db, DB_ENGINE

games_bp = Blueprint('games', __name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config')
_REGISTRY    = os.path.join(_CONFIG_PATH, 'games.json')


def _p() -> str:
    return '%s' if DB_ENGINE == 'postgresql' else '?'


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


# ── Список игр ───────────────────────────────────────────────────────────────

@games_bp.route('/api/games')
def get_games():
    return jsonify({'games': game_service.get_all_games(_REGISTRY)})


# ── Конфиг лотерейного билета ─────────────────────────────────────────────────
# ВАЖНО: этот маршрут должен быть ДО /game/<game_id> чтобы не конфликтовать

@games_bp.route('/api/lottery/config')
def lottery_config():
    cfg = game_service.load_config('lottery_ticket')
    if not cfg:
        return jsonify({'error': 'Config not found'}), 404
    return jsonify(cfg)


# ── Страница игры ─────────────────────────────────────────────────────────────

@games_bp.route('/game/<game_id>')
def game_page(game_id):
    try:
        with open(_REGISTRY, encoding='utf-8') as f:
            registry = json.load(f)
        entry = next((g for g in registry.get('games', []) if g['id'] == game_id), None)
        # Редиректим ТОЛЬКО если явно active: false
        if entry is not None and entry.get('active', True) is False:
            from flask import render_template
            return render_template('index.html')
    except Exception as e:
        print(f'[game_page] registry error: {e}')

    html_path = game_service.get_html_path(game_id)
    if html_path:
        try:
            with open(html_path, encoding='utf-8') as f:
                return Response(f.read(), mimetype='text/html; charset=utf-8')
        except Exception as e:
            print(f'[game_page] read error: {e}')
            return f'Ошибка чтения игры: {e}', 500

    # Fallback: templates/game_<id>.html
    from flask import render_template
    tpl = f'game_{game_id}.html'
    tpl_path = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', tpl)
    if os.path.exists(tpl_path):
        return render_template(tpl)

    return f'Игра "{game_id}" не найдена', 404



# ── URL внешней игры (для провайдерских игр с iframe) ─────────────────────────

@games_bp.route('/api/games/<game_id>/url')
def game_url(game_id):
    """Вернуть URL для загрузки провайдерской игры (с токеном)."""
    user = _current_user()
    lang = request.args.get('lang', 'ru')
    url = game_service.get_game_url(game_id, user, lang)
    if not url:
        return jsonify({'error': 'Game URL not configured for this provider'}), 404
    return jsonify({'url': url, 'game_id': game_id})


# ── Список всех игр (включая авто-обнаруженные провайдерские) ─────────────────

# ── Избранное ─────────────────────────────────────────────────────────────────

@games_bp.route('/api/favorites')
def get_favs():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    return jsonify({'favorites': user.favorite_games})


@games_bp.route('/api/favorites', methods=['POST'])
def set_favs():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    favs = (request.get_json() or {}).get('favorites', [])
    user.set_favorites(favs)
    return jsonify({'success': True})


# ── История раундов ───────────────────────────────────────────────────────────

@games_bp.route('/api/game/history')
def game_history():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    game_id = request.args.get('game')
    limit   = min(int(request.args.get('limit', 20)), 100)
    rounds  = GameRound.list_for_user(user.id, game_id=game_id, limit=limit)
    count   = GameRound.count_for_user(user.id, game_id) if game_id else None
    return jsonify({
        'history': [r.to_dict() for r in rounds],
        'count':   count,
    })


@games_bp.route('/api/tournaments/leaderboard')
def tournaments_leaderboard():
    """Leaderboard for active tournament season."""
    period = (request.args.get('period') or 'week').strip().lower()

    try:
        limit = min(max(int(request.args.get('limit', 20)), 1), 100)
    except Exception:
        limit = 20

    tournament, rows = Tournament.leaderboard(period, limit)

    leaders = []
    for idx, row in enumerate(rows, start=1):
        leaders.append({
            'rank': idx,
            'user_id': row.get('user_id'),
            'username': row.get('username') or '—',
            'icon_url': row.get('icon_url') or '',
            'total_win': float(row.get('total_win') or 0),
            'total_bet': float(row.get('total_bet') or 0),
            'total_profit': float(row.get('total_profit') or 0),
            'rounds': int(row.get('rounds') or 0),
        })

    tour_payload = {}
    if tournament:
        def _parse_dt(raw):
            if not raw:
                return None
            txt = str(raw)
            if txt.endswith('Z'):
                txt = txt[:-1] + '+00:00'
            try:
                dt = datetime.fromisoformat(txt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None

        now = datetime.now(timezone.utc)
        st = _parse_dt(tournament.get('starts_at'))
        en = _parse_dt(tournament.get('ends_at'))
        has_started = bool(st and now >= st)
        is_finished = bool(en and now >= en)
        seconds_to_start = int((st - now).total_seconds()) if st and now < st else 0
        seconds_to_end = int((en - now).total_seconds()) if en and now < en else 0
        tour_payload = {
            'id': tournament.get('id'),
            'slug': tournament.get('slug') or '',
            'title': tournament.get('title') or '',
            'description': tournament.get('description') or '',
            'period': tournament.get('period') or period,
            'prize_pool': float(tournament.get('prize_pool') or 0),
            'starts_at': str(tournament.get('starts_at') or ''),
            'ends_at': str(tournament.get('ends_at') or ''),
            'status': tournament.get('status') or 'active',
            'has_started': has_started and not is_finished,
            'is_finished': is_finished,
            'is_upcoming': bool(st and now < st),
            'seconds_to_start': max(seconds_to_start, 0),
            'seconds_to_end': max(seconds_to_end, 0),
            'game_ids': list(tournament.get('game_ids') or []),
            'glow_color': str(tournament.get('glow_color') or '#f5c451'),
        }

    # Top "big wins" panel (single rounds with highest wins)
    high_wins = []
    all_time_big_winners = []
    try:
        p = _p()
        panel_limit = 200
        with get_db() as db:
            db.execute(
                f"""
                SELECT
                    gh.id,
                    gh.game_id,
                    gh.win,
                    gh.bet,
                    gh.created_at,
                    u.user_name AS username,
                    u.icon_url AS icon_url
                FROM game_history gh
                JOIN users u ON u.id = gh.user_id
                WHERE gh.win > 0
                ORDER BY gh.win DESC, gh.created_at DESC
                LIMIT {p}
                """,
                (60,),
            )
            rows_hw = db.fetchall() or []
        high_wins = [
            {
                'id': int(r.get('id') or 0),
                'username': r.get('username') or '—',
                'icon_url': r.get('icon_url') or '',
                'game_id': r.get('game_id') or '',
                'win': float(r.get('win') or 0),
                'bet': float(r.get('bet') or 0),
                'created_at': str(r.get('created_at') or ''),
                'source': 'big_win',
            }
            for r in rows_hw
        ]

        with get_db() as db:
            db.execute(
                f"""
                SELECT
                    u.id AS user_id,
                    u.user_name AS username,
                    u.icon_url AS icon_url,
                    MAX(gh.win) AS max_win,
                    COUNT(gh.id) AS wins_count
                FROM users u
                JOIN game_history gh ON gh.user_id = u.id
                WHERE gh.win > 0
                GROUP BY u.id, u.user_name, u.icon_url
                ORDER BY MAX(gh.win) DESC, COUNT(gh.id) DESC, u.id ASC
                LIMIT {p}
                """,
                (panel_limit,),
            )
            rows_all_time = db.fetchall() or []

        all_time_big_winners = [
            {
                'id': int(r.get('user_id') or 0),
                'username': r.get('username') or '—',
                'icon_url': r.get('icon_url') or '',
                'max_win': float(r.get('max_win') or 0),
                'wins_count': int(r.get('wins_count') or 0),
                'source': 'all_time_big_win',
            }
            for r in rows_all_time
        ]

        if not all_time_big_winners:
            with get_db() as db:
                db.execute(
                    f"""
                    SELECT
                        u.id AS user_id,
                        u.user_name AS username,
                        u.icon_url AS icon_url,
                        COALESCE(SUM(gh.win), 0) AS sum_win,
                        COALESCE(SUM(gh.bet), 0) AS sum_bet
                    FROM users u
                    JOIN game_history gh ON gh.user_id = u.id
                    GROUP BY u.id, u.user_name, u.icon_url
                    HAVING COALESCE(SUM(gh.bet), 0) > 0
                    ORDER BY (COALESCE(SUM(gh.win), 0) / NULLIF(COALESCE(SUM(gh.bet), 0), 0)) DESC,
                             COALESCE(SUM(gh.win), 0) DESC
                    LIMIT {p}
                    """,
                    (panel_limit,),
                )
                rows_rtp = db.fetchall() or []
            all_time_big_winners = [
                {
                    'id': int(r.get('user_id') or 0),
                    'username': r.get('username') or '—',
                    'icon_url': r.get('icon_url') or '',
                    'max_win': float(r.get('sum_win') or 0),
                    'wins_count': 0,
                    'source': 'rtp_fallback',
                    'win_rtp': (float(r.get('sum_win') or 0) / float(r.get('sum_bet') or 1)) if float(r.get('sum_bet') or 0) > 0 else 0.0,
                }
                for r in rows_rtp
            ]
    except Exception:
        high_wins = []
        all_time_big_winners = []

    return jsonify({
        'period': period,
        'tournament': tour_payload,
        'leaders': leaders,
        'high_wins': high_wins,
        'all_time_big_winners': all_time_big_winners,
    })


# ── Конфиги ───────────────────────────────────────────────────────────────────

def _load_cfg(name: str) -> dict:
    with open(os.path.join(_CONFIG_PATH, f'{name}.json'), encoding='utf-8') as f:
        return json.load(f)

@games_bp.route('/api/config/site')
def cfg_site():
    cfg = _load_cfg('site')
    # Expose feature flags to frontend
    cfg['kyc_enabled']  = cfg.get('kyc', {}).get('enabled', True)
    cfg['smtp_enabled'] = cfg.get('email', {}).get('enabled', True)
    return jsonify(cfg)

@games_bp.route('/api/config/banners')
def cfg_banners():        return jsonify(_load_cfg('banners'))

@games_bp.route('/api/config/promotions')
def cfg_promos():         return jsonify(_load_cfg('promotions'))

@games_bp.route('/api/config/raffles')
def cfg_raffles():        return jsonify(_load_cfg('raffles'))

@games_bp.route('/api/config/faq')
def cfg_faq():            return jsonify(_load_cfg('faq'))

@games_bp.route('/api/config/promo_settings')
def cfg_promo_settings(): return jsonify(_load_cfg('promo_settings'))

@games_bp.route('/api/config/sidebar')
def cfg_sidebar():        return jsonify(_load_cfg('sidebar'))

@games_bp.route('/api/config/sports')
def cfg_sports():         return jsonify(_load_cfg('sports'))

@games_bp.route('/api/config/bukmaking')
def cfg_bukmaking():
    try:
        from app.services.sport_odds_feed import load_merged_bukmaking
        return jsonify(load_merged_bukmaking())
    except Exception:
        try:
            return jsonify(_load_cfg('bukmaking'))
        except Exception:
            return jsonify(_load_cfg('sports'))

@games_bp.route('/api/config/partners')
def cfg_partners():       return jsonify(_load_cfg('partners'))

@games_bp.route('/api/config/btm')
def cfg_btm():            return jsonify(_load_cfg('btm_cfg'))

# ── i18n: язык по коду ───────────────────────────────────────────────────────
_LANG_MAP = {'RU': 'ru', 'UA': 'ua', 'US': 'us', 'EN': 'us'}

@games_bp.route('/api/lang/<code>')
def get_lang(code):
    code = code.upper()
    filename = _LANG_MAP.get(code)
    if not filename:
        return jsonify({'error': 'Unknown language'}), 404
    lang_path = os.path.join(_CONFIG_PATH, 'languages', f'{filename}.json')
    if not os.path.exists(lang_path):
        return jsonify({'error': 'Language file not found'}), 404
    with open(lang_path, encoding='utf-8') as f:
        data = json.load(f)
    return jsonify(data)

@games_bp.route('/api/lang/list')
def get_lang_list():
    """Возвращает доступные языки из site.json + мета-данные (флаг, название)."""
    site = _load_cfg('site')
    raw = site.get('site_language', 'RU')
    codes = [c.strip().upper() for c in raw.split(',') if c.strip()]
    result = []
    for code in codes:
        filename = _LANG_MAP.get(code)
        if not filename:
            continue
        lang_path = os.path.join(_CONFIG_PATH, 'languages', f'{filename}.json')
        if not os.path.exists(lang_path):
            continue
        with open(lang_path, encoding='utf-8') as f:
            meta = json.load(f)
        result.append({
            'code': code,
            'label': meta.get('lang_label', code),
            'flag':  meta.get('lang_flag', ''),
        })
    default = site.get('default_language', codes[0] if codes else 'RU').upper()
    return jsonify({'languages': result, 'default': default})

@games_bp.route('/api/config/ticket_lang')
def cfg_ticket_lang():
    lang_path = os.path.join(_CONFIG_PATH, 'languages', 'ticket_lang.json')
    if not os.path.exists(lang_path):
        return jsonify({}), 404
    with open(lang_path, encoding='utf-8') as f:
        return jsonify(json.load(f))

@games_bp.route('/api/games/by_tag/<tag>')
def games_by_tag(tag):
    all_games = game_service.get_all_games(_REGISTRY)
    filtered = [g for g in all_games if tag in (g.get('tags') or []) or g.get('category') == tag]
    return jsonify({'games': filtered, 'tag': tag})


# ── Аватарка ─────────────────────────────────────────────────────────────────

@games_bp.route('/api/auth/avatar', methods=['POST'])
def upload_avatar():
    uid = session.get('user_id')
    user = User.get_by_id(uid) if uid else None
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    d = request.get_json() or {}
    icon_url = (d.get('icon_url') or '').strip()
    if not icon_url:
        return jsonify({'error': 'Данные не переданы'}), 400
    if not (icon_url.startswith('data:image') or icon_url.startswith('http')):
        return jsonify({'error': 'Некорректный формат'}), 400
    if len(icon_url) > 3_000_000:
        return jsonify({'error': 'Изображение слишком большое (макс. 2МБ)'}), 400
    user.update_profile(icon_url=icon_url)
    return jsonify({'success': True, 'user': user.to_dict()})