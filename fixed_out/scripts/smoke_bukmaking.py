"""Smoke test: merged bukmaking (static + optional The Odds API v4). From fixed_out: python scripts/smoke_bukmaking.py"""
import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_root, '.env'))
except Exception:
    pass

from app.services.sport_odds_feed import load_merged_bukmaking

has_the_odds = bool((os.environ.get('SPORT_ODDS_API_KEY') or os.environ.get('THE_ODDS_API_KEY') or '').strip())
has_odds_io = bool(os.environ.get('ODDS_API_IO_KEY', '').strip())
print('the_odds_api_key:', has_the_odds, '| odds_api_io_key:', has_odds_io)

d = load_merged_bukmaking()
ev = d.get('events') or []
auto_v4 = [e for e in ev if str(e.get('id', '')).startswith('oddsapi_')]
auto_io = [e for e in ev if str(e.get('id', '')).startswith('oddsapiio_')]
print('total_events:', len(ev))
print('auto_the_odds_api:', len(auto_v4), '| auto_odds_api_io:', len(auto_io))
for e in ev[:5]:
    print(
        ' ',
        e.get('id'),
        '|',
        e.get('home_team'),
        'vs',
        e.get('away_team'),
        '| mode=',
        e.get('mode'),
    )
