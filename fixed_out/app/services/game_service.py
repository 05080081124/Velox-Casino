"""
app/services/game_service.py — Загрузка и авто-обнаружение игр.

Поддерживает два вида игр:

1. In-house игры (Crash, Roulette, Lottery и т.д.)
   app/games/<id>/config.json  — конфиг одной игры, "id" на верхнем уровне
   app/games/<id>/index.html   — страница игры
   app/games/<id>/listener.py  — серверная логика (SSE, раунды)

2. Провайдерские игры (авто-обнаружение)
   app/games/<provider>/config.json  — конфиг провайдера с массивом "games": [...]
   app/games/<provider>/index.html   — iframe-обёртка (опционально)
   app/games/<provider>/provider.py  — логика токенов/сессий (опционально)

   Структура провайдерского config.json:
   {
     "provider": "EvoPlay",
     "provider_tag": "evoplay",
     "games": [
       {
         "id": "evo_slots_1",      // опционально — авто: <provider>_<index>
         "name": "Magic Slots",    // или i18n: {"ru":{"name":"..."},"us":{"name":"..."}}
         "category": "slots",
         "icon": "🎰",
         "bg_gradient": "linear-gradient(...)",
         "logo": "/static/uploads/...",
         "game_url": "https://provider.com/game?token={token}&lang={lang}",
         "active": true,
         "hot": false,
         "new": true,
         "rtp": 0.96,
         "tags": ["slots", "bonus"],
         "bet": {"min": 10, "max": 50000}
       }
     ]
   }

   game_url поддерживает плейсхолдеры:
     {token}    — JWT/сессионный токен (генерируется provider.py если есть)
     {user_id}  — ID пользователя
     {lang}     — текущий язык (ru/ua/us)
     {currency} — валюта пользователя
"""
from __future__ import annotations
import os, json, importlib.util
from typing import Optional, Any

_GAMES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'games'))
_module_cache: dict[str, Any] = {}


class GameService:

    # ── Загрузка Python-модуля игры ───────────────────────────────────────────

    @staticmethod
    def load_module(game_id: str) -> Optional[Any]:
        """
        Ищет listener.py, provider.py или game_mainSett.py в папке игры.
        Возвращает загруженный модуль или None.
        """
        if game_id in _module_cache:
            return _module_cache[game_id]

        candidates = ['listener.py', 'provider.py', 'game_mainSett.py']
        game_dir = os.path.join(_GAMES_DIR, game_id)

        # Для провайдерских игр — ищем в папке провайдера
        if not os.path.isdir(game_dir):
            # game_id может быть evo_slots_1 → ищем папку evoplay
            provider = game_id.rsplit('_', 1)[0] if '_' in game_id else None
            if provider:
                game_dir = os.path.join(_GAMES_DIR, provider)

        for fname in candidates:
            path = os.path.join(game_dir, fname)
            if os.path.exists(path):
                try:
                    spec = importlib.util.spec_from_file_location(f'game_mod_{game_id}', path)
                    mod  = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _module_cache[game_id] = mod
                    return mod
                except Exception as e:
                    print(f'[game_service] load_module {game_id}: {e}')
        return None

    @staticmethod
    def invalidate_cache(game_id: Optional[str] = None):
        if game_id:
            _module_cache.pop(game_id, None)
        else:
            _module_cache.clear()

    # ── Конфиг игры ───────────────────────────────────────────────────────────

    @staticmethod
    def load_config(game_id: str) -> Optional[dict]:
        """Читает app/games/<game_id>/config.json."""
        path = os.path.join(_GAMES_DIR, game_id, 'config.json')
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    # ── Авто-обнаружение всех игр ─────────────────────────────────────────────

    @staticmethod
    def discover_games() -> list[dict]:
        """
        Сканирует app/games/* и возвращает все игры.
        Поддерживает как одиночные игры (id на верхнем уровне),
        так и провайдерские пакеты (массив "games").
        """
        result = {}

        if not os.path.isdir(_GAMES_DIR):
            return []

        for folder in sorted(os.listdir(_GAMES_DIR)):
            # Skip template/example folders (prefixed with _)
            if folder.startswith('_'):
                continue
            folder_path = os.path.join(_GAMES_DIR, folder)
            if not os.path.isdir(folder_path):
                continue
            cfg_path = os.path.join(folder_path, 'config.json')
            if not os.path.exists(cfg_path):
                continue

            try:
                with open(cfg_path, encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception as e:
                print(f'[game_service] discover {folder}: {e}')
                continue

            # ── Провайдерский пакет (массив "games") ─────────────────────────
            if 'games' in cfg and isinstance(cfg['games'], list):
                provider_name = cfg.get('provider', folder)
                provider_tag  = cfg.get('provider_tag', folder.lower())
                for idx, game in enumerate(cfg['games']):
                    if not isinstance(game, dict):
                        continue
                    # Авто-генерируем id если не задан
                    gid = game.get('id') or f'{provider_tag}_{idx+1}'
                    g = dict(game)
                    g['id']           = gid
                    g['provider']     = provider_name
                    g['provider_tag'] = provider_tag
                    g['_source_dir']  = folder
                    g['_is_provider'] = True
                    # URL iframe-обёртки: /game/<id>
                    if not g.get('route'):
                        g['route'] = f'/game/{gid}'
                    g = GameService._normalize_game(g)
                    result[gid] = g

            # ── Одиночная игра (id на верхнем уровне или = имени папки) ───────
            else:
                gid = cfg.get('id') or folder
                g = dict(cfg)
                g['id']           = gid
                g['_source_dir']  = folder
                g['_is_provider'] = False
                if not g.get('route'):
                    g['route'] = f'/game/{gid}'
                g = GameService._normalize_game(g)
                result[gid] = g

        return list(result.values())

    @staticmethod
    def set_game_status(game_id: str, active: bool):
        """
        Позволяет админке выключать игры (например, на тех-обслуживание) 
        без правки JSON-файлов.
        """
        config = GameService.load_config(game_id)
        if config:
            # Здесь должна быть логика сохранения статуса в БД
            pass

    @staticmethod
    def _normalize_game(cfg: dict) -> dict:
        """Нормализует поля игры: bet limits, i18n name/description."""
        bet  = cfg.get('bet', {})
        mult = cfg.get('multiplier', {})
        cfg['min_bet']     = bet.get('min', 0)
        cfg['max_bet']     = bet.get('max', 50000)
        cfg['currency_min']= bet.get('currency_min', {})
        cfg['min_multiplier'] = mult.get('min', 1.0)
        cfg['max_multiplier'] = mult.get('max', 100.0)
        online = cfg.get('online', {})
        cfg['none_multiplayer']        = online.get('none_multiplayer', True)
        cfg['countdown_before_start']  = online.get('countdown_before_start', 4)

        # Извлекаем name/description из i18n если не заданы на верхнем уровне
        if not cfg.get('name'):
            i18n = cfg.get('i18n', {})
            for lang in ('us', 'ru', 'ua'):
                n = i18n.get(lang, {}).get('name')
                if n:
                    cfg['name'] = n
                    break
        if not cfg.get('description'):
            i18n = cfg.get('i18n', {})
            for lang in ('us', 'ru', 'ua'):
                d = i18n.get(lang, {}).get('description')
                if d:
                    cfg['description'] = d
                    break
        if not cfg.get('name'):
            cfg['name'] = cfg.get('id', '').replace('_', ' ').title()

        cfg.setdefault('active', True)
        cfg.setdefault('hot',    False)
        cfg.setdefault('new',    False)
        cfg.setdefault('featured', False)
        cfg.setdefault('icon',   '🎮')
        cfg.setdefault('tags',   [])
        cfg.setdefault('rtp',    0.96)
        return cfg

    # ── get_all_games — основной метод для /api/games ─────────────────────────

    @staticmethod
    def get_all_games(registry_path: str) -> list[dict]:
        """
        Объединяет:
          1. Игры из config/games.json (ручной реестр)
          2. Авто-обнаруженные игры из app/games/*/config.json
        Дедупликация по id — реестр имеет приоритет.
        """
        manual: dict[str, dict] = {}
        try:
            with open(registry_path, encoding='utf-8') as f:
                registry = json.load(f)
            base = os.path.normpath(os.path.join(os.path.dirname(registry_path), '..'))
            for entry in registry.get('games', []):
                gid = entry['id']
                # Пробуем загрузить полный конфиг из app/games/<id>/config.json
                new_cfg = os.path.join(_GAMES_DIR, gid, 'config.json')
                old_cfg = os.path.join(base, entry.get('config_path', ''))
                cfg_path = new_cfg if os.path.exists(new_cfg) else old_cfg
                try:
                    with open(cfg_path, encoding='utf-8') as f:
                        cfg = json.load(f)
                    cfg['active'] = entry.get('active', cfg.get('active', False))
                    cfg = GameService._normalize_game(cfg)
                    manual[gid] = cfg
                except Exception:
                    pass
        except Exception:
            pass

        # Авто-обнаружение
        discovered = {g['id']: g for g in GameService.discover_games()}

        # Merge: manual overrides discovered
        merged = {**discovered, **manual}
        return list(merged.values())

    # ── Валидация ставки ──────────────────────────────────────────────────────

    @staticmethod
    def validate_bet(game_id: str, amount: float, currency: str = 'UAH') -> tuple[bool, str]:
        mod = GameService.load_module(game_id)
        if mod and hasattr(mod, 'validate_bet'):
            return mod.validate_bet(amount, currency)
        cfg = GameService.load_config(game_id) or {}
        bet_cfg = cfg.get('bet', {})
        cur_min = bet_cfg.get('currency_min', {}).get(currency)
        min_bet = cur_min if cur_min is not None else bet_cfg.get('min', 1)
        max_bet = bet_cfg.get('max', 1_000_000)
        if amount <= 0:    return False, 'Ставка должна быть больше нуля'
        if amount < min_bet: return False, f'Минимальная ставка {min_bet} {currency}'
        if amount > max_bet: return False, f'Максимальная ставка {max_bet} {currency}'
        return True, ''

    # ── HTML страница игры ────────────────────────────────────────────────────

    @staticmethod
    def get_html_path(game_id: str) -> Optional[str]:
        """
        Ищет index.html игры. Для провайдерских игр — в папке провайдера.
        Если не найден — возвращает путь к generic_provider.html (iframe wrapper).
        """
        # Прямой путь: app/games/<game_id>/index.html
        direct = os.path.join(_GAMES_DIR, game_id, 'index.html')
        if os.path.exists(direct):
            return direct

        # Для провайдерской игры вида <provider_tag>_<N> — ищем папку провайдера
        games = GameService.discover_games()
        game_info = next((g for g in games if g['id'] == game_id), None)
        if game_info and game_info.get('_is_provider'):
            src_dir = game_info.get('_source_dir', '')
            prov_html = os.path.join(_GAMES_DIR, src_dir, 'index.html')
            if os.path.exists(prov_html):
                return prov_html
            # Используем generic iframe wrapper
            generic = os.path.join(
                os.path.dirname(__file__), '..', '..', 'templates', 'provider_game.html'
            )
            return os.path.normpath(generic) if os.path.exists(generic) else None

        return None

    @staticmethod
    def get_game_url(game_id: str, user=None, lang: str = 'ru') -> Optional[str]:
        """
        Возвращает URL внешней игры с подставленными плейсхолдерами.
        Если у провайдера есть provider.py с generate_token() — вызывает его.
        """
        games = GameService.discover_games()
        game_info = next((g for g in games if g['id'] == game_id), None)
        if not game_info:
            return None

        url = game_info.get('game_url', '')
        if not url:
            return None

        token = ''
        if user:
            # Пробуем вызвать provider.py generate_token()
            mod = GameService.load_module(game_id)
            if mod and hasattr(mod, 'generate_token'):
                try:
                    token = mod.generate_token(user, game_info) or ''
                except Exception as e:
                    print(f'[game_service] generate_token {game_id}: {e}')

        currency = getattr(user, 'currency', 'UAH') if user else 'UAH'
        user_id  = str(getattr(user, 'id', 0)) if user else '0'

        url = url.replace('{token}',    token)
        url = url.replace('{user_id}',  user_id)
        url = url.replace('{lang}',     lang)
        url = url.replace('{currency}', currency)
        return url


# Глобальный экземпляр
game_service = GameService()
