"""Загрузка `fixed_out/app/services/sport_odds_feed.py` без конфликта с пакетом `app` velox_support.

Импорт вида `from app.services.sport_odds_feed` всегда берёт velox_support.app — модуля там нет.
Используем importlib по абсолютному пути к файлу казино.
"""
from __future__ import annotations

import importlib.util
import os

_CACHED = None


def _casino_fixed_out_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'fixed_out'))


def load_casino_sport_odds_feed():
    """Единственный экземпляр модуля; пути config внутри модуля привязаны к его __file__ в fixed_out."""
    global _CACHED
    if _CACHED is not None:
        return _CACHED
    root = _casino_fixed_out_root()
    path = os.path.join(root, 'app', 'services', 'sport_odds_feed.py')
    if not os.path.isfile(path):
        raise ImportError(f'sport_odds_feed not found: {path}')
    spec = importlib.util.spec_from_file_location('casino_sport_odds_feed_impl', path)
    if spec is None or spec.loader is None:
        raise ImportError('spec_from_file_location failed for sport_odds_feed')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _CACHED = mod
    return mod
