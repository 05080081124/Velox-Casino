"""Слияние статического bukmaking.json с линией внешнего API.

Поддерживаются:
  - The Odds API v4 (api.the-odds-api.com) — provider the_odds_api
  - Odds-API.io v3 (api.odds-api.io) — provider odds_api_io, линия ML через /odds/multi

Ключи: переменные окружения (env_key) и/или config/sport_odds_credentials.json.
Несколько линий: sport_odds_api.json → sources[] (разные env_key / провайдеры).
The Odds API: auto_discover_sports + sport_keys [\"auto\"] — подбор активных видов через GET /v4/sports.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_CONFIG_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'config')
_BUKMAKING_PATH = os.path.join(_CONFIG_DIR, 'bukmaking.json')
_SPORTS_PATH = os.path.join(_CONFIG_DIR, 'sports.json')
_FEED_CFG_PATH = os.path.join(_CONFIG_DIR, 'sport_odds_api.json')
_CREDENTIALS_PATH = os.path.join(_CONFIG_DIR, 'sport_odds_credentials.json')
_PROV_OVERRIDES_PATH = os.path.join(_CONFIG_DIR, 'sport_provider_overrides.json')
_VISIBILITY_PATH = os.path.join(_CONFIG_DIR, 'sport_provider_visibility.json')

_cache: dict[str, Any] = {'ts': 0.0, 'merged': None}
_cred_cache: dict[str, Any] = {'mtime': 0.0, 'flat': {}}
_auto_sports_cache: dict[str, Any] = {'key': '', 'keys': []}


def _read_json(path: str, default: dict) -> dict:
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return deepcopy(default)


def _credentials_flat() -> dict[str, str]:
    """Плоский словарь имён переменных → ключ API (файл sport_odds_credentials.json)."""
    global _cred_cache
    try:
        st = os.stat(_CREDENTIALS_PATH)
        mtime = float(st.st_mtime)
    except OSError:
        mtime = -1.0
    if _cred_cache.get('mtime') == mtime and isinstance(_cred_cache.get('flat'), dict):
        return _cred_cache['flat']
    raw = _read_json(_CREDENTIALS_PATH, {})
    flat: dict[str, str] = {}
    if isinstance(raw.get('keys'), dict):
        for k, v in raw['keys'].items():
            if v is not None and str(v).strip():
                flat[str(k)] = str(v).strip()
    for k, v in raw.items():
        if k.startswith('_') or k == 'keys':
            continue
        if isinstance(v, str) and v.strip():
            flat[str(k)] = v.strip()
    _cred_cache = {'mtime': mtime, 'flat': flat}
    return flat


def _resolve_env_key(name: str) -> str:
    n = str(name or '').strip()
    if not n:
        return ''
    v = (os.environ.get(n) or '').strip()
    if v:
        return v
    return (_credentials_flat().get(n) or '').strip()


def _api_key() -> str:
    return _resolve_env_key('SPORT_ODDS_API_KEY') or _resolve_env_key('THE_ODDS_API_KEY')


def _infer_tag(sport_key: str, tag_map: dict) -> str:
    sk = str(sport_key or '').strip()
    if sk in tag_map:
        return str(tag_map[sk])
    low = sk.lower()
    if 'soccer' in low or 'football' in low:
        return 'football'
    if 'basketball' in low:
        return 'basketball'
    if 'tennis' in low:
        return 'tennis'
    if 'mma' in low or 'ufc' in low:
        return 'mma'
    if 'boxing' in low:
        return 'boxing'
    if 'baseball' in low:
        return 'baseball'
    if 'ice_hockey' in low or 'hockey' in low:
        return 'hockey'
    if 'esport' in low:
        return 'esports'
    return 'football'


_GROUP_ORDER = (
    'Soccer',
    'Basketball',
    'Tennis',
    'Ice Hockey',
    'American Football',
    'Baseball',
    'Mixed Martial Arts',
    'Boxing',
    'Golf',
    'Esports',
    'E-Sports',
)


def _sport_group_sort_key(row: dict) -> tuple[int, str]:
    g = str(row.get('group') or '')
    try:
        idx = _GROUP_ORDER.index(g)
    except ValueError:
        idx = 40
    return (idx, g)


def _fetch_the_odds_sport_keys_active(
    base_url: str,
    api_key: str,
    timeout: int,
    max_sports: int,
    group_allowlist: Optional[list],
) -> list[str]:
    """Активные ключи видов спорта The Odds API v4 (/sports)."""
    if not api_key:
        return []
    global _auto_sports_cache
    allow_tuple = tuple(str(x) for x in (group_allowlist or []) if str(x).strip())
    ck = hashlib.sha256(f'{base_url}|{api_key}|{max_sports}|{allow_tuple}'.encode()).hexdigest()[:24]
    if _auto_sports_cache.get('key') == ck and _auto_sports_cache.get('keys'):
        return list(_auto_sports_cache['keys'])
    q = urllib.parse.urlencode({'apiKey': api_key})
    url = f'{base_url.rstrip("/")}/sports?{q}'
    try:
        data = _http_json(url, timeout)
    except Exception:
        return []
    rows = [r for r in (data if isinstance(data, list) else []) if isinstance(r, dict)]
    rows = [r for r in rows if r.get('active')]
    rows.sort(key=_sport_group_sort_key)
    if allow_tuple:
        allow = set(allow_tuple)
        rows = [r for r in rows if str(r.get('group') or '') in allow] + [
            r for r in rows if str(r.get('group') or '') not in allow
        ]
    out: list[str] = []
    for r in rows:
        key = str(r.get('key') or '').strip()
        if not key:
            continue
        if key not in out:
            out.append(key)
        if len(out) >= max(1, min(int(max_sports or 14), 48)):
            break
    _auto_sports_cache = {'key': ck, 'keys': out}
    return out


_GRAD_BY_TAG: dict[str, tuple[str, str, str]] = {
    'football': ('#047857', '#022c22', '#0d9488'),
    'basketball': ('#c2410c', '#431407', '#fb923c'),
    'tennis': ('#166534', '#052e16', '#4ade80'),
    'hockey': ('#1d4ed8', '#0f172a', '#38bdf8'),
    'mma': ('#991b1b', '#1c0a0a', '#f87171'),
    'boxing': ('#a16207', '#1a1004', '#eab308'),
    'kickboxing': ('#a16207', '#1c1917', '#fbbf24'),
    'baseball': ('#b91c1c', '#1c0a0a', '#fca5a5'),
    'esports': ('#5b21b6', '#1e103c', '#a78bfa'),
}


def _hash_int(s: str) -> int:
    h = 0
    for ch in str(s):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h


def _decorate_auto_event_visual(ev: dict) -> None:
    """Градиент шапки карточки и насыщенные цвета формы для mode=auto (без своих cover/background)."""
    if str(ev.get('mode') or '') != 'auto':
        return
    tag = str(ev.get('tag') or 'sport').lower()
    trip = _GRAD_BY_TAG.get(tag, ('#1e3a8a', '#0b1128', '#4338ca'))
    salt = f"{ev.get('home_team')}|{ev.get('league')}"
    n = _hash_int(salt) % 14
    ev['card_gradient'] = (
        f'linear-gradient(135deg, {trip[0]} 0%, {trip[1]} {38 + n}%, {trip[2]} 100%)'
    )
    # Optional: background templates by tag from sports.json
    if not ev.get('background') and not ev.get('cover'):
        sp = _read_json(_SPORTS_PATH, {})
        mb = sp.get('match_backgrounds') if isinstance(sp.get('match_backgrounds'), dict) else {}
        bt = mb.get('by_tag') if isinstance(mb.get('by_tag'), dict) else {}
        arr = bt.get(tag)
        if isinstance(arr, list) and arr:
            idx = _hash_int(f"{ev.get('home_team')}|{ev.get('away_team')}|{ev.get('starts_at')}") % max(1, len(arr))
            bg = str(arr[idx] or '').strip()
            if bg:
                ev['background'] = bg
    if not ev.get('home_kit_primary'):
        h = _hash_int(str(ev.get('home_team') or 'h') + tag) % 360
        ev['home_kit_primary'] = f'hsl({h}, 84%, 46%)'
        ev['home_kit_secondary'] = f'hsl({(h + 24) % 360}, 42%, 91%)'
    if not ev.get('away_kit_primary'):
        h2 = _hash_int(str(ev.get('away_team') or 'a') + tag) % 360
        ev['away_kit_primary'] = f'hsl({h2}, 84%, 44%)'
        ev['away_kit_secondary'] = f'hsl({(h2 + 31) % 360}, 44%, 89%)'


def _category_for_bukmaking_ui(tag: str) -> str:
    """Совпадает с sports_menu[].title в bukmaking.json — иначе фронт отфильтровывает API-матчи."""
    t = str(tag or '').lower()
    if t == 'football':
        return 'Football'
    if t == 'basketball':
        return 'Basketball'
    if 'esport' in t:
        return 'E-Sport'
    if t == 'tennis':
        return 'Tennis'
    if t == 'mma':
        return 'MMA'
    if t == 'boxing':
        return 'Boxing'
    if t == 'kickboxing':
        return 'Kickboxing'
    if t == 'hockey':
        return 'Hockey'
    return str(tag or 'Sport').title()


def _sport_type_by_tag(tag: str) -> str:
    t = str(tag or '').lower()
    return 'esport' if 'esport' in t else 'sport'


def _country_hint_from_league(league_name: str) -> str:
    """Первый сегмент до « - » из названия лиги Odds-API.io (Spain - La Liga → Spain)."""
    s = str(league_name or '').strip()
    if ' - ' not in s:
        return ''
    head = s.split(' - ', 1)[0].strip()
    if len(head) > 48 or '/' in head:
        return ''
    return head


def _h2h_odds(home_team: str, away_team: str, outcomes: list) -> dict:
    oh = od = oa = None
    home_team = str(home_team or '')
    away_team = str(away_team or '')
    for o in outcomes or []:
        try:
            name = str(o.get('name', ''))
            price = float(o.get('price'))
        except (TypeError, ValueError):
            continue
        if name == home_team:
            oh = price
        elif name == away_team:
            oa = price
        elif name.lower() in ('draw', 'tie'):
            od = price
    return {'home': oh, 'draw': od, 'away': oa}


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(str(v).replace(',', '').strip())
        # Decimal odds should be > 1.00. Some APIs can return 0/1 placeholders.
        if f <= 1.0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _match_status_odds_api_io(api_status: str, commence_iso: str) -> str:
    s = str(api_status or '').lower()
    if s == 'live':
        return 'live'
    if s in ('settled', 'cancelled', 'completed', 'ended'):
        return 'finished'
    # Don't infer LIVE from commence time for odds-api.io. Their status should drive live/finished.
    return 'planned'


def _prefer_odds_api_event_row(a: dict, b: dict) -> dict:
    """При объединении pending+live одного id оставляем строку live."""
    sa = str(a.get('status') or '').lower()
    sb = str(b.get('status') or '').lower()
    if sb == 'live' and sa != 'live':
        return b
    if sa == 'live' and sb != 'live':
        return a
    return b


def _odds_api_io_fetch_statuses(src: dict, feed_cfg: dict) -> list[str]:
    """Статусы Odds-API.io /events: как минимум pending+live — иначе матч пропадает из выборки после начала."""
    raw = src.get('events_statuses')
    if isinstance(raw, list) and raw:
        out: list[str] = []
        for x in raw:
            xs = str(x).strip().lower()
            if xs and xs not in out:
                out.append(xs)
        return out if out else ['pending', 'live']
    legacy = str(
        src.get('events_status')
        or feed_cfg.get('odds_api_io_events_status')
        or ''
    ).strip().lower()
    if legacy == 'pending':
        return ['pending', 'live']
    if legacy:
        return [legacy]
    return ['pending', 'live']


def _score_from_odds_api_io_row(row: dict) -> dict:
    """Пытаемся вытащить счёт из ответа API (поля могут различаться по видам спорта)."""
    z = {'home': 0, 'away': 0}
    scores = row.get('scores')
    # Common format: scores: [{name, score}, {name, score}]
    if isinstance(scores, list) and scores:
        by_name: dict[str, int] = {}
        for it in scores:
            if not isinstance(it, dict):
                continue
            nm = str(it.get('name') or '').strip()
            sc = it.get('score')
            try:
                if nm and sc is not None:
                    by_name[nm] = int(sc)
            except (TypeError, ValueError):
                continue
        if by_name:
            home = str(row.get('home') or row.get('home_team') or '').strip()
            away = str(row.get('away') or row.get('away_team') or '').strip()
            if home in by_name and away in by_name:
                return {'home': int(by_name[home]), 'away': int(by_name[away])}
    sc = row.get('score')
    if isinstance(sc, dict):
        try:
            h = sc.get('home')
            a = sc.get('away')
            if h is not None and a is not None:
                return {'home': int(h), 'away': int(a)}
        except (TypeError, ValueError):
            pass
    g = row.get('goals')
    if isinstance(g, (list, tuple)) and len(g) >= 2:
        try:
            return {'home': int(g[0]), 'away': int(g[1])}
        except (TypeError, ValueError):
            pass
    ft = row.get('fulltime') if isinstance(row.get('fulltime'), dict) else None
    if ft:
        try:
            h, a = ft.get('home'), ft.get('away')
            if h is not None and a is not None:
                return {'home': int(h), 'away': int(a)}
        except (TypeError, ValueError):
            pass
    return z


def _ml_odds_from_odds_api_io(bookmakers: Any) -> dict:
    oh = od = oa = None
    if not isinstance(bookmakers, dict):
        return {'home': oh, 'draw': od, 'away': oa}
    for bm_name, markets in bookmakers.items():
        if '(no latency)' in str(bm_name).lower():
            continue
        if not isinstance(markets, list):
            continue
        for mkt in markets:
            if not isinstance(mkt, dict):
                continue
            if str(mkt.get('name')) != 'ML':
                continue
            rows = mkt.get('odds') or []
            if not rows or not isinstance(rows[0], dict):
                continue
            row = rows[0]
            oh = _float_or_none(row.get('home'))
            od = _float_or_none(row.get('draw'))
            oa = _float_or_none(row.get('away'))
            if oh is not None or oa is not None:
                return {'home': oh, 'draw': od, 'away': oa}
    return {'home': oh, 'draw': od, 'away': oa}


def _http_json(url: str, timeout: int) -> Any:
    req = urllib.request.Request(url, headers={'User-Agent': 'VeloxSportOddsFeed/1.1'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _sportsrc_json(base_url: str, api_key: str, params: dict, timeout: int) -> Any:
    q = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None and str(v) != ''})
    url = f'{base_url.rstrip("/")}/?{q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'VeloxSportOddsFeed/1.1', 'X-API-KEY': api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _sportsrc_pick(d: dict, keys: list[str], default: Any = '') -> Any:
    for k in keys:
        if k in d and d.get(k) not in (None, ''):
            return d.get(k)
    return default


def _sportsrc_norm_status(raw: Any) -> str:
    s = str(raw or '').strip().lower()
    if s in ('inprogress', 'live', 'playing', '1h', '2h', 'ot'):
        return 'live'
    if s in ('finished', 'completed', 'ft', 'ended', 'settled'):
        return 'finished'
    return 'planned'


def _sportsrc_extract_stream(detail: dict) -> str:
    # Be tolerant: API may return url, embed, iframe, stream, streams, etc.
    if not isinstance(detail, dict):
        return ''
    for k in ('embed', 'embed_url', 'stream', 'stream_url', 'iframe', 'iframe_url'):
        v = detail.get(k)
        if isinstance(v, str) and v.strip().startswith('http'):
            return v.strip()
        if isinstance(v, dict):
            u = v.get('url') or v.get('embed') or v.get('iframe')
            if isinstance(u, str) and u.strip().startswith('http'):
                return u.strip()
    streams = detail.get('streams')
    if isinstance(streams, list) and streams:
        for it in streams:
            if isinstance(it, dict):
                u = it.get('url') or it.get('embed') or it.get('iframe')
                if isinstance(u, str) and u.strip().startswith('http'):
                    return u.strip()
            if isinstance(it, str) and it.strip().startswith('http'):
                return it.strip()
    return ''


def _sportsrc_extract_score(detail: dict, home: str, away: str) -> dict:
    z = {'home': 0, 'away': 0}
    if not isinstance(detail, dict):
        return z
    # Common pattern: scores: [{name, score}]
    sc = detail.get('scores')
    if isinstance(sc, list) and sc:
        by_name: dict[str, int] = {}
        for it in sc:
            if not isinstance(it, dict):
                continue
            nm = str(it.get('name') or it.get('team') or '').strip()
            val = it.get('score')
            try:
                if nm and val is not None:
                    by_name[nm] = int(val)
            except (TypeError, ValueError):
                continue
        if by_name and home in by_name and away in by_name:
            return {'home': int(by_name[home]), 'away': int(by_name[away])}
    # Alternative: score: {home, away}
    sc2 = detail.get('score')
    if isinstance(sc2, dict):
        try:
            h, a = sc2.get('home'), sc2.get('away')
            if h is not None and a is not None:
                return {'home': int(h), 'away': int(a)}
        except (TypeError, ValueError):
            pass
    return z


def _fetch_sportsrc_matches(base_url: str, api_key: str, sport: str, status: str, date_ymd: str, timeout: int) -> list[dict]:
    data = _sportsrc_json(base_url, api_key, {'type': 'matches', 'sport': sport, 'status': status, 'date': date_ymd}, timeout)
    if isinstance(data, dict) and isinstance(data.get('data'), list):
        return [x for x in data['data'] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _fetch_sportsrc_detail(base_url: str, api_key: str, match_id: str, timeout: int) -> dict:
    data = _sportsrc_json(base_url, api_key, {'type': 'detail', 'id': match_id}, timeout)
    if isinstance(data, dict) and isinstance(data.get('data'), dict):
        return data['data']
    return data if isinstance(data, dict) else {}


def _normalize_sportsrc_rows(rows: list[dict], sport: str, tag: str, source_slot: str) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        mid = str(_sportsrc_pick(row, ['id', 'match_id', 'matchId', 'slug'], '') or '')[:120].strip()
        if not mid:
            continue
        home = str(_sportsrc_pick(row, ['home_team', 'home', 'homeTeam', 'team_home', 'team1'], '') or '').strip()
        away = str(_sportsrc_pick(row, ['away_team', 'away', 'awayTeam', 'team_away', 'team2'], '') or '').strip()
        league = str(_sportsrc_pick(row, ['league', 'tournament', 'competition', 'sport_title', 'sportTitle'], sport) or '').strip()
        country = str(_sportsrc_pick(row, ['country', 'region'], '') or '').strip()
        commence = str(_sportsrc_pick(row, ['commence_time', 'starts_at', 'start_time', 'date', 'time'], '') or '').strip()
        st = _sportsrc_norm_status(_sportsrc_pick(row, ['status', 'match_status', 'state'], ''))
        ev = {
            'id': f'sportsrc_{mid}',
            'enabled': True,
            'sport_type': _sport_type_by_tag(tag),
            'match_status': st,
            'tag': tag,
            'category': _category_for_bukmaking_ui(tag),
            'country': country,
            'league': league,
            'home_team': home,
            'away_team': away,
            'starts_at': commence,
            'match_td_utc': commence,
            'background': '',
            'cover': '',
            'icon_png': '',
            'is_live_stream': False,
            'match_today': False,
            'mode': 'auto',
            'automation': {
                'provider': 'sportsrc',
                'sport': sport,
                'source_id': mid,
                'source_slot': str(source_slot or ''),
            },
            'odds': {'home': None, 'draw': None, 'away': None},
            'match_center': {'video_enabled': False},
            'markets': {},
            'score': {'home': 0, 'away': 0},
            'period_scores': [],
        }
        _decorate_auto_event_visual(ev)
        out.append(ev)
    return out


def _fetch_odds_api_io_events(
    base_url: str, api_key: str, sport_slug: str, status: str, limit: int, timeout: int,
) -> list:
    q = urllib.parse.urlencode({
        'apiKey': api_key,
        'sport': sport_slug,
        'status': status,
        'limit': max(1, min(limit, 100)),
    })
    url = f'{base_url.rstrip("/")}/events?{q}'
    data = _http_json(url, timeout)
    return data if isinstance(data, list) else []


def _fetch_odds_api_io_multi(
    base_url: str, api_key: str, event_ids: list[int], bookmakers: str, timeout: int,
) -> list:
    if not event_ids:
        return []
    q = urllib.parse.urlencode({
        'apiKey': api_key,
        'eventIds': ','.join(str(i) for i in event_ids),
        'bookmakers': bookmakers,
    })
    url = f'{base_url.rstrip("/")}/odds/multi?{q}'
    data = _http_json(url, timeout)
    return data if isinstance(data, list) else []


def _normalize_odds_api_io_events(
    event_rows: list,
    odds_by_id: dict[int, dict],
    sport_slug: str,
    tag: str,
    source_slot: str,
) -> list:
    out = []
    for row in event_rows:
        if not isinstance(row, dict):
            continue
        try:
            eid_num = int(row.get('id'))
        except (TypeError, ValueError):
            continue
        oid = f'oddsapiio_{eid_num}'
        home = str(row.get('home') or '')
        away = str(row.get('away') or '')
        lg = row.get('league') if isinstance(row.get('league'), dict) else {}
        league = str(lg.get('name') or sport_slug)
        commence = str(row.get('date') or '')
        api_status = str(row.get('status') or '')
        block = odds_by_id.get(eid_num) or {}
        bmk = block.get('bookmakers') if isinstance(block.get('bookmakers'), dict) else row.get('bookmakers')
        odds = _ml_odds_from_odds_api_io(bmk)
        st_l = api_status.strip().lower()
        if odds.get('home') is None and odds.get('away') is None:
            if st_l != 'live':
                continue
            odds = {'home': None, 'draw': None, 'away': None}
        score = _score_from_odds_api_io_row(row)
        row_ev = {
            'id': oid,
            'enabled': True,
            'sport_type': _sport_type_by_tag(tag),
            'match_status': _match_status_odds_api_io(api_status, commence),
            'tag': tag,
            'category': _category_for_bukmaking_ui(tag),
            'country': _country_hint_from_league(league),
            'league': league,
            'home_team': home,
            'away_team': away,
            'starts_at': commence,
            'match_td_utc': commence,
            'background': '',
            'cover': '',
            'icon_png': '',
            'is_live_stream': api_status.lower() == 'live',
            'match_today': False,
            'mode': 'auto',
            'automation': {
                'provider': 'odds_api_io',
                'sport_slug': sport_slug,
                'source_id': str(eid_num),
                'source_slot': str(source_slot or ''),
            },
            'odds': odds,
            'match_center': {'video_enabled': False},
            'markets': {},
            'score': score,
            'period_scores': [],
        }
        _decorate_auto_event_visual(row_ev)
        out.append(row_ev)
    return out


def _fetch_merge_odds_api_io(feed_cfg: dict, src: dict) -> list:
    base = str(
        src.get('base_url')
        or feed_cfg.get('odds_api_io_base_url')
        or 'https://api.odds-api.io/v3'
    ).strip()
    api_key = str(src.get('api_key') or '')
    if not api_key:
        return []
    timeout = int(feed_cfg.get('request_timeout_sec') or 25)
    slugs = src.get('sport_slugs') or []
    if isinstance(slugs, str):
        slugs = [slugs]
    tag_map = src.get('tag_by_sport_slug') if isinstance(src.get('tag_by_sport_slug'), dict) else {}
    slot = str(src.get('source_slot') or '')
    limit = int(src.get('events_limit') or feed_cfg.get('odds_api_io_events_limit') or 25)
    status_list = _odds_api_io_fetch_statuses(src, feed_cfg)
    bookmakers = str(src.get('bookmakers') or 'Bet365')

    merged: list = []
    for slug in slugs:
        slug = str(slug).strip()
        if not slug:
            continue
        combined_rows: dict[int, dict] = {}
        for status_f in status_list:
            try:
                part = _fetch_odds_api_io_events(base, api_key, slug, status_f, limit, timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                continue
            for r in part:
                if not isinstance(r, dict) or r.get('id') is None:
                    continue
                try:
                    eid_num = int(r['id'])
                except (TypeError, ValueError):
                    continue
                old = combined_rows.get(eid_num)
                combined_rows[eid_num] = r if old is None else _prefer_odds_api_event_row(old, r)
        ev_rows = list(combined_rows.values())
        if not ev_rows:
            continue
        ids: list[int] = []
        for r in ev_rows:
            if isinstance(r, dict) and r.get('id') is not None:
                try:
                    ids.append(int(r['id']))
                except (TypeError, ValueError):
                    pass
        odds_by_id: dict[int, dict] = {}
        for i in range(0, len(ids), 10):
            chunk = ids[i:i + 10]
            try:
                multi = _fetch_odds_api_io_multi(base, api_key, chunk, bookmakers, timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                continue
            if not isinstance(multi, list):
                continue
            for block in multi:
                if isinstance(block, dict) and block.get('id') is not None:
                    try:
                        odds_by_id[int(block['id'])] = block
                    except (TypeError, ValueError):
                        pass
        tag = _infer_tag(slug, tag_map)
        merged.extend(_normalize_odds_api_io_events(ev_rows, odds_by_id, slug, tag, slot))
    return merged


def _status_from_commence(commence_iso: str) -> str:
    try:
        raw = str(commence_iso).replace('Z', '+00:00')
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        # Heuristic: if event is far in the past, treat as finished (auto-clean).
        if dt.__le__(now - timedelta(hours=12)):
            return 'finished'
        if dt <= now:
            return 'live'
    except Exception:
        pass
    return 'planned'


def _fetch_the_odds_scores(
    base_url: str,
    api_key: str,
    sport_key: str,
    event_ids: list[str],
    timeout: int,
    days_from: Optional[int],
) -> list[dict]:
    """The Odds API v4 scores: /v4/sports/{sport}/scores (id matches odds id)."""
    if not api_key or not sport_key:
        return []
    params: dict[str, str] = {'apiKey': api_key, 'dateFormat': 'iso'}
    if days_from is not None:
        try:
            df = int(days_from)
            if 1 <= df <= 3:
                params['daysFrom'] = str(df)
        except (TypeError, ValueError):
            pass
    if event_ids:
        # Limit payload/quota to the events we already have.
        params['eventIds'] = ','.join(str(x) for x in event_ids[:80] if str(x))
    q = urllib.parse.urlencode(params)
    url = f'{base_url.rstrip("/")}/sports/{urllib.parse.quote(sport_key, safe="")}/scores?{q}'
    data = _http_json(url, timeout)
    return data if isinstance(data, list) else []


def _apply_scores_to_the_odds_events(events: list[dict], scores_rows: list[dict]) -> None:
    by_id: dict[str, dict] = {}
    for r in scores_rows or []:
        if not isinstance(r, dict):
            continue
        rid = str(r.get('id') or '')[:80]
        if rid:
            by_id[rid] = r
    if not by_id:
        return
    for ev in events:
        if not isinstance(ev, dict):
            continue
        rid = str(ev.get('automation', {}).get('source_id') or '')
        if not rid:
            # ev.id is prefixed oddsapi_{id}
            rid = str(ev.get('id') or '').replace('oddsapi_', '', 1)
        row = by_id.get(rid)
        if not row:
            continue
        completed = bool(row.get('completed'))
        scores = row.get('scores')
        if isinstance(scores, list) and scores:
            by_name: dict[str, int] = {}
            for it in scores:
                if not isinstance(it, dict):
                    continue
                nm = str(it.get('name') or '').strip()
                sc = it.get('score')
                try:
                    if nm and sc is not None:
                        by_name[nm] = int(sc)
                except (TypeError, ValueError):
                    continue
            if by_name:
                h = str(ev.get('home_team') or '').strip()
                a = str(ev.get('away_team') or '').strip()
                if h in by_name and a in by_name:
                    ev['score'] = {'home': int(by_name[h]), 'away': int(by_name[a])}
        ev['match_status'] = 'finished' if completed else 'live'


def _fetch_the_odds_for_sport(
    base_url: str,
    api_key: str,
    sport_key: str,
    regions: str,
    markets: str,
    timeout: int,
) -> list:
    q = urllib.parse.urlencode({
        'apiKey': api_key,
        'regions': regions,
        'markets': markets,
    })
    url = f'{base_url.rstrip("/")}/sports/{urllib.parse.quote(sport_key, safe="")}/odds?{q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'VeloxSportOddsFeed/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data if isinstance(data, list) else []


def _normalize_the_odds_rows(rows: list, sport_key: str, tag: str, source_slot: str = '') -> list:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        api_id = str(row.get('id') or '')[:80]
        if not api_id:
            continue
        home = str(row.get('home_team') or '')
        away = str(row.get('away_team') or '')
        league = str(row.get('sport_title') or row.get('sport_nice') or sport_key)
        commence = str(row.get('commence_time') or '')
        bookmakers = row.get('bookmakers') or []
        odds = {'home': None, 'draw': None, 'away': None}
        for bm in bookmakers:
            if not isinstance(bm, dict):
                continue
            for mkt in bm.get('markets') or []:
                if not isinstance(mkt, dict):
                    continue
                if str(mkt.get('key')) != 'h2h':
                    continue
                odds = _h2h_odds(home, away, mkt.get('outcomes') or [])
                break
            if odds.get('home') is not None or odds.get('away') is not None:
                break
        eid = f'oddsapi_{api_id}'
        row_ev = {
            'id': eid,
            'enabled': True,
            'sport_type': _sport_type_by_tag(tag),
            'match_status': _status_from_commence(commence),
            'tag': tag,
            'category': _category_for_bukmaking_ui(tag),
            'country': '',
            'league': league,
            'home_team': home,
            'away_team': away,
            'starts_at': commence,
            'match_td_utc': commence,
            'background': '',
            'cover': '',
            'icon_png': '',
            'is_live_stream': False,
            'match_today': False,
            'mode': 'auto',
            'automation': {
                'provider': 'the_odds_api',
                'sport_key': sport_key,
                'source_id': api_id,
                'source_slot': str(source_slot or ''),
            },
            'odds': odds,
            'match_center': {'video_enabled': False},
            'markets': {},
            'score': {'home': 0, 'away': 0},
            'period_scores': [],
        }
        _decorate_auto_event_visual(row_ev)
        out.append(row_ev)
    return out


def _resolve_the_odds_sport_keys(s: dict, feed_cfg: dict, api_key: str) -> list[str]:
    """sport_keys или auto_discover_sports → список ключей The Odds API."""
    timeout = int(feed_cfg.get('request_timeout_sec') or 25)
    base_url = str(feed_cfg.get('base_url') or 'https://api.the-odds-api.com/v4')
    raw_sks = s.get('sport_keys')
    auto_flag = s.get('auto_discover_sports') is True
    if isinstance(raw_sks, str) and raw_sks.strip().lower() == 'auto':
        auto_flag = True
        raw_sks = None
    if isinstance(raw_sks, list) and raw_sks and str(raw_sks[0]).strip().lower() == 'auto':
        auto_flag = True
    if auto_flag:
        max_n = int(s.get('auto_discover_max') or s.get('auto_discover_max_sports') or feed_cfg.get('auto_discover_max_sports') or 14)
        grp = s.get('auto_discover_groups')
        if isinstance(grp, str):
            grp = [x.strip() for x in grp.split(',') if x.strip()]
        gl = grp if isinstance(grp, list) else None
        return _fetch_the_odds_sport_keys_active(base_url, api_key, timeout, max_n, gl)
    if isinstance(raw_sks, str):
        raw_sks = [raw_sks]
    sks = list(raw_sks or [])
    return [str(x).strip() for x in sks if str(x).strip()]


def _load_base_bukmaking() -> dict:
    data = _read_json(_BUKMAKING_PATH, {'events': []})
    if not data.get('events'):
        alt = _read_json(_SPORTS_PATH, {'events': []})
        if alt.get('events'):
            base = deepcopy(alt)
            extra = {k: v for k, v in data.items() if k != 'events'}
            base.update(extra)
            return base
    return data


def _iter_feed_sources(feed_cfg: dict) -> list[dict]:
    """Список конфигов по одному на каждый активный источник в sources[]."""
    raw = feed_cfg.get('sources')
    built: list[dict] = []
    if isinstance(raw, list):
        for i, s in enumerate(raw):
            if not isinstance(s, dict):
                continue
            if s.get('enabled') is False:
                continue
            ek = str(s.get('env_key') or '').strip()
            p = str(s.get('provider') or 'the_odds_api').lower()
            key = _resolve_env_key(ek) if ek else ''
            if not key and p == 'the_odds_api':
                key = _api_key()
            if not key and p == 'odds_api_io':
                key = _resolve_env_key('ODDS_API_IO_KEY')
            if not key:
                continue
            slot = str(s.get('id') or s.get('label') or f'source_{i}')
            if p == 'odds_api_io':
                slugs = s.get('sport_slugs') or s.get('sport_keys') or []
                if isinstance(slugs, str):
                    slugs = [slugs]
                if not isinstance(slugs, list) or not slugs:
                    continue
                tm = s.get('tag_by_sport_slug') if isinstance(s.get('tag_by_sport_slug'), dict) else {}
                built.append({
                    'provider': 'odds_api_io',
                    'api_key': key,
                    'sport_slugs': slugs,
                    'tag_by_sport_slug': tm,
                    'source_slot': slot,
                    'events_limit': int(s.get('events_limit') or 25),
                    'events_status': str(s.get('events_status') or 'pending'),
                    'events_statuses': s.get('events_statuses'),
                    'bookmakers': str(s.get('bookmakers') or 'Bet365'),
                    'base_url': str(s.get('base_url') or '').strip(),
                })
            elif p == 'the_odds_api':
                sks = _resolve_the_odds_sport_keys(s, feed_cfg, key)
                if not sks:
                    continue
                tm = s.get('tag_by_sport_key') if isinstance(s.get('tag_by_sport_key'), dict) else {}
                built.append({
                    'provider': 'the_odds_api',
                    'api_key': key,
                    'sport_keys': sks,
                    'tag_by_sport_key': tm,
                    'source_slot': slot,
                })
            elif p == 'sportsrc':
                sp = s.get('sports') or s.get('sport') or []
                if isinstance(sp, str):
                    sp = [x.strip() for x in sp.split(',') if x.strip()]
                if not isinstance(sp, list) or not sp:
                    continue
                tm = s.get('tag_by_sport') if isinstance(s.get('tag_by_sport'), dict) else {}
                built.append({
                    'provider': 'sportsrc',
                    'api_key': key,
                    'sports': [str(x).strip() for x in sp if str(x).strip()],
                    'tag_by_sport': tm,
                    'source_slot': slot,
                    'base_url': str(s.get('base_url') or 'https://api.sportsrc.org/v2').strip(),
                    'live_only_detail': bool(s.get('live_only_detail', True)),
                    'per_sport_limit': int(s.get('per_sport_limit') or 20),
                })
    if built:
        return built
    # Legacy: один ключ + sport_keys на верхнем уровне
    key = _api_key()
    if not key:
        return []
    legacy_src = {
        'sport_keys': feed_cfg.get('sport_keys'),
        'auto_discover_sports': feed_cfg.get('auto_discover_sports'),
        'auto_discover_max': feed_cfg.get('auto_discover_max_sports'),
        'auto_discover_max_sports': feed_cfg.get('auto_discover_max_sports'),
        'auto_discover_groups': feed_cfg.get('auto_discover_groups'),
    }
    sks = _resolve_the_odds_sport_keys(legacy_src, feed_cfg, key)
    if not sks:
        return []
    tm = feed_cfg.get('tag_by_sport_key') if isinstance(feed_cfg.get('tag_by_sport_key'), dict) else {}
    return [{
        'provider': str(feed_cfg.get('provider') or 'the_odds_api').lower(),
        'api_key': key,
        'sport_keys': sks,
        'tag_by_sport_key': tm,
        'source_slot': 'default',
    }]


def fetch_api_events(feed_cfg: dict) -> list:
    base_url = str(feed_cfg.get('base_url') or 'https://api.the-odds-api.com/v4')
    regions = str(feed_cfg.get('regions') or 'eu')
    markets = str(feed_cfg.get('markets') or 'h2h')
    timeout = int(feed_cfg.get('request_timeout_sec') or 25)
    scores_enabled = feed_cfg.get('scores_enabled', True) is not False
    try:
        scores_days_from = int(feed_cfg.get('scores_days_from') or 1)
    except (TypeError, ValueError):
        scores_days_from = 1

    merged_api: list = []
    seen_ids: set[str] = set()

    for src in _iter_feed_sources(feed_cfg):
        prov = str(src.get('provider') or '')
        if prov == 'odds_api_io':
            for ev in _fetch_merge_odds_api_io(feed_cfg, src):
                eid = str(ev.get('id') or '')
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                merged_api.append(ev)
            continue
        if prov == 'sportsrc':
            api_key = str(src.get('api_key') or '')
            if not api_key:
                continue
            base = str(src.get('base_url') or 'https://api.sportsrc.org/v2').strip()
            slot = str(src.get('source_slot') or '')
            tag_map = src.get('tag_by_sport') if isinstance(src.get('tag_by_sport'), dict) else {}
            per_limit = int(src.get('per_sport_limit') or 20)
            live_only_detail = bool(src.get('live_only_detail', True))
            # SportSRC requires explicit sport + status + date
            date_ymd = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            sports = src.get('sports') or []
            if isinstance(sports, str):
                sports = [sports]
            timeout2 = int(feed_cfg.get('request_timeout_sec') or 25)
            for sp in sports:
                sp = str(sp).strip()
                if not sp:
                    continue
                tag = str(tag_map.get(sp) or _infer_tag(sp, {}) or 'football')
                # Get both live and upcoming for today
                for st in ('inprogress', 'upcoming'):
                    try:
                        rows = _fetch_sportsrc_matches(base, api_key, sp, st, date_ymd, timeout2)
                    except Exception:
                        continue
                    if per_limit and len(rows) > per_limit:
                        rows = rows[:per_limit]
                    norm = _normalize_sportsrc_rows(rows, sp, tag, source_slot=slot)
                    # For live matches: enrich with detail (stream + score)
                    for ev in norm:
                        eid = str(ev.get('id') or '')
                        if not eid:
                            continue
                        if eid in seen_ids:
                            continue
                        is_live = str(ev.get('match_status') or '').lower() == 'live'
                        if (not live_only_detail) or is_live:
                            try:
                                det = _fetch_sportsrc_detail(base, api_key, str(ev.get('automation', {}).get('source_id') or ''), timeout2)
                                s = _sportsrc_extract_stream(det)
                                if s:
                                    mc = ev.get('match_center') if isinstance(ev.get('match_center'), dict) else {}
                                    mc = dict(mc)
                                    mc['video_enabled'] = True
                                    mc['video_embed_url'] = s
                                    ev['match_center'] = mc
                                ev['score'] = _sportsrc_extract_score(det, str(ev.get('home_team') or ''), str(ev.get('away_team') or ''))
                                # Use detail status if present
                                ev['match_status'] = _sportsrc_norm_status(_sportsrc_pick(det if isinstance(det, dict) else {}, ['status', 'state', 'match_status'], ev.get('match_status')))
                            except Exception:
                                pass
                        if str(ev.get('match_status') or '').lower() == 'finished':
                            continue
                        seen_ids.add(eid)
                        merged_api.append(ev)
            continue
        if prov != 'the_odds_api':
            continue
        api_key = str(src.get('api_key') or '')
        if not api_key:
            continue
        tag_map = src.get('tag_by_sport_key') or {}
        slot = str(src.get('source_slot') or '')
        for sk in src.get('sport_keys') or []:
            sk = str(sk).strip()
            if not sk:
                continue
            try:
                rows = _fetch_the_odds_for_sport(base_url, api_key, sk, regions, markets, timeout)
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
                continue
            tag = _infer_tag(sk, tag_map)
            norm = _normalize_the_odds_rows(rows, sk, tag, source_slot=slot)
            if scores_enabled and norm:
                try:
                    ids = [str(e.get('automation', {}).get('source_id') or '') for e in norm]
                    ids = [x for x in ids if x]
                    srows = _fetch_the_odds_scores(base_url, api_key, sk, ids, timeout, days_from=scores_days_from)
                    _apply_scores_to_the_odds_events(norm, srows)
                except Exception:
                    pass
            for ev in norm:
                # Auto-remove completed events (provider feed)
                if str(ev.get('match_status') or '').lower() == 'finished':
                    continue
                eid = str(ev.get('id') or '')
                if eid and eid in seen_ids:
                    continue
                if eid:
                    seen_ids.add(eid)
                merged_api.append(ev)
    return merged_api


def _is_provider_feed_event_id(eid: str) -> bool:
    s = str(eid or '')
    return s.startswith('oddsapiio_') or s.startswith('oddsapi_')


def _apply_provider_visibility(events: list) -> list:
    """В режиме listed на сайте только разрешённые id из внешней линии."""
    vis = _read_json(_VISIBILITY_PATH, {'mode': 'all', 'allowed_ids': []})
    mode = str(vis.get('mode') or 'all').lower()
    if mode != 'listed':
        return events
    allowed = {str(x) for x in (vis.get('allowed_ids') or [])}
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get('id') or '')
        if _is_provider_feed_event_id(eid):
            if eid in allowed:
                out.append(ev)
            continue
        out.append(ev)
    return out


def _apply_provider_overrides(events: list) -> list:
    """Ручные оверрайды для провайдерской линии (например видео-стрим)."""
    cfg = _read_json(_PROV_OVERRIDES_PATH, {'streams': {}})
    streams = cfg.get('streams') if isinstance(cfg.get('streams'), dict) else {}
    if not streams:
        return events
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get('id') or '')
        if not _is_provider_feed_event_id(eid):
            out.append(ev)
            continue
        url = str(streams.get(eid) or '').strip()
        # If provider already supplies video_embed_url — keep it and ignore manual override.
        mc_existing = ev.get('match_center') if isinstance(ev.get('match_center'), dict) else {}
        if str(mc_existing.get('video_embed_url') or '').strip():
            out.append(ev)
            continue
        if url:
            mc = ev.get('match_center') if isinstance(ev.get('match_center'), dict) else {}
            mc = dict(mc)
            mc['video_enabled'] = True
            mc['video_embed_url'] = url
            ev = dict(ev)
            ev['match_center'] = mc
        out.append(ev)
    return out


def _merge_api_events_into(base: dict, feed_cfg: dict) -> None:
    api_events = fetch_api_events(feed_cfg)
    if not api_events:
        return
    static_events = list(base.get('events') or [])
    if feed_cfg.get('merge_with_file', True):
        static_ids = {str(e.get('id')) for e in static_events if isinstance(e, dict)}
        for ev in api_events:
            if str(ev.get('id')) not in static_ids:
                static_events.append(ev)
        base['events'] = static_events
    else:
        base['events'] = api_events


def invalidate_odds_feed_cache() -> None:
    global _cache, _auto_sports_cache
    _cache = {'ts': 0.0, 'merged': None}
    _auto_sports_cache = {'key': '', 'keys': []}


def get_merged_provider_events_preview() -> list:
    """Тот же merge что на сайте, но без фильтра sport_provider_visibility — для админ-каталога."""
    feed_cfg = _read_json(_FEED_CFG_PATH, {'enabled': False})
    base = deepcopy(_load_base_bukmaking())
    if feed_cfg.get('enabled'):
        _merge_api_events_into(base, feed_cfg)
    return list(base.get('events') or [])


def load_merged_bukmaking() -> dict:
    """Полный конфиг букмекинга: файл + (опционально) события из API + видимость провайдера."""
    global _cache
    feed_cfg = _read_json(_FEED_CFG_PATH, {'enabled': False})
    ttl = float(feed_cfg.get('cache_ttl_sec') or 120)
    now = time.monotonic()
    if _cache.get('merged') is not None and (now - float(_cache.get('ts') or 0)) < ttl:
        return deepcopy(_cache['merged'])

    base = _load_base_bukmaking()
    if feed_cfg.get('enabled'):
        _merge_api_events_into(base, feed_cfg)
    base['events'] = _apply_provider_overrides(_apply_provider_visibility(list(base.get('events') or [])))

    _cache = {'ts': now, 'merged': deepcopy(base)}
    return deepcopy(base)


def get_merged_events_list() -> list:
    return list(load_merged_bukmaking().get('events') or [])
