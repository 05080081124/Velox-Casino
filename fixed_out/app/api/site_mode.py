"""
app/api/site_mode.py — Middleware и Blueprint режима сайта.

Оффлайн-режим:
  - Все запросы кроме /api/mode и разрешённых IP → страница обслуживания
  - Статика и /static/* — работают (чтобы страница обслуживания выглядела нормально)
  - Разрешённые IP из allow_admin_ip видят сайт полностью

Онлайн-режим:
  - Сайт работает как обычно
  - Опционально показывает баннер об обслуживании в будущем

Переключение:
  Поменяй "mode" в config/site_mode.json → "offline" или "online"
  Изменения применяются сразу без перезапуска сервера.
"""
import os, json
from functools import lru_cache
from flask import Blueprint, request, jsonify, render_template_string, g

mode_bp = Blueprint('site_mode', __name__)

# Позволяет переопределить путь к конфигу через переменную окружения
_DEFAULT_CFG = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site_mode.json')
_CONFIG_PATH = os.getenv('VELOX_SITE_MODE_CONFIG', _DEFAULT_CFG)

# ── Чтение конфига (без кэша — чтобы менялся на лету) ───────────────────────

def load_mode_config() -> dict:
    try:
        with open(_CONFIG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'mode': 'online', 'offline': {}, 'online': {}}


def is_offline() -> bool:
    return load_mode_config().get('mode', 'online').lower() == 'offline'


def _client_ip() -> str:
    return (request.headers.get('X-Forwarded-For', '') or
            request.remote_addr or '127.0.0.1').split(',')[0].strip()


def _is_admin_ip() -> bool:
    cfg     = load_mode_config()
    allowed = cfg.get('offline', {}).get('allow_admin_ip', ['127.0.0.1'])
    return _client_ip() in allowed


# ── Страница обслуживания ─────────────────────────────────────────────────────

_OFFLINE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ title }}</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Exo+2:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{
    font-family:'Exo 2',sans-serif;
    background:{{ bg }};
    color:#eef0ff;
    min-height:100vh;
    display:flex;
    flex-direction:column;
    align-items:center;
    justify-content:center;
    text-align:center;
    padding:24px;
  }
  .logo{
    font-family:'Bebas Neue',sans-serif;
    font-size:52px;
    letter-spacing:8px;
    color:{{ accent }};
    margin-bottom:8px;
    text-shadow:0 0 40px {{ accent }}66;
  }
  .bolt{display:inline-block;margin-right:8px}
  .card{
    background:rgba(255,255,255,.04);
    border:1px solid rgba(255,255,255,.08);
    border-radius:20px;
    padding:40px 48px;
    max-width:520px;
    width:100%;
    margin-top:24px;
  }
  .icon{font-size:52px;margin-bottom:16px;display:block}
  h1{
    font-family:'Bebas Neue',sans-serif;
    font-size:32px;
    letter-spacing:3px;
    color:{{ accent }};
    margin-bottom:10px;
  }
  p{font-size:15px;color:#888;line-height:1.6;margin-bottom:0}
  .dots{display:flex;gap:8px;justify-content:center;margin-top:28px}
  .dot{
    width:10px;height:10px;border-radius:50%;
    background:{{ accent }};
    opacity:.3;
    animation:pulse 1.4s ease-in-out infinite;
  }
  .dot:nth-child(2){animation-delay:.2s}
  .dot:nth-child(3){animation-delay:.4s}
  @keyframes pulse{0%,100%{opacity:.3;transform:scale(1)}50%{opacity:1;transform:scale(1.3)}}
  .social{margin-top:32px;display:flex;gap:14px;justify-content:center}
  .s-btn{
    padding:9px 20px;border-radius:8px;font-size:13px;font-weight:600;
    background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1);
    color:#aaa;text-decoration:none;transition:.2s;
  }
  .s-btn:hover{color:#fff;border-color:{{ accent }};background:{{ accent }}22}
</style>
</head>
<body>
  <div class="logo">
    <span class="bolt">
      <svg width="36" height="36" viewBox="0 0 24 24" fill="{{ accent }}">
        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>
      </svg>
    </span>
    VELOX
  </div>

  <div class="card">
    <span class="icon">🛠️</span>
    <h1>{{ title }}</h1>
    <p>{{ message }}</p>
    <div class="dots">
      <div class="dot"></div>
      <div class="dot"></div>
      <div class="dot"></div>
    </div>
  </div>

  {% if show_social %}
  <div class="social">
    <a class="s-btn" href="#">Telegram</a>
    <a class="s-btn" href="#">Instagram</a>
    <a class="s-btn" href="#">Twitter/X</a>
  </div>
  {% endif %}
</body>
</html>"""


def offline_response():
    cfg    = load_mode_config()
    off    = cfg.get('offline', {})
    html   = render_template_string(
        _OFFLINE_HTML,
        title      = off.get('title',   'Технические работы'),
        message    = off.get('message', 'Скоро вернёмся!'),
        bg         = off.get('background_color', '#050a0f'),
        accent     = off.get('accent_color',     '#ff3c5a'),
        show_social= off.get('show_social', True),
    )
    return html, 503


# ── Middleware — вешается через before_request в main.py ─────────────────────

def offline_middleware():
    """
    Вызывается перед каждым запросом.
    Если сайт в offline-режиме — возвращает страницу обслуживания,
    кроме статики и /api/mode (чтобы можно было проверить статус).
    """
    if not is_offline():
        return  # онлайн — всё работает

    # Разрешённые IP видят сайт
    if _is_admin_ip():
        return

    path = request.path

    # Статику разрешаем (нужна для страницы обслуживания)
    if path.startswith('/static/'):
        return

    # API статуса режима разрешаем всегда
    if path in ('/api/mode', '/api/mode/status'):
        return

    # Auth, support и история игр работают всегда (залогиненные пользователи)
    if (path.startswith('/api/auth/') or path.startswith('/api/support/')
            or path == '/api/game/history'
            or path.startswith('/api/lottery/')
            or path == '/game/lottery_ticket'):
        return

    # API-запросы получают JSON-ответ
    if path.startswith('/api/'):
        return jsonify({
            'error':   'Сайт находится на техническом обслуживании',
            'offline': True,
        }), 503

    # Все остальные запросы — страница обслуживания
    return offline_response()


# ── API endpoints ─────────────────────────────────────────────────────────────

@mode_bp.route('/api/mode')
def get_mode():
    """Возвращает текущий режим сайта. Доступен всегда."""
    cfg  = load_mode_config()
    mode = cfg.get('mode', 'online')
    return jsonify({'mode': mode, 'offline': mode == 'offline'})


@mode_bp.route('/api/mode', methods=['POST'])
def set_mode():
    """
    Переключает режим сайта.
    Принимает: {"mode": "offline"} или {"mode": "online"}
    Доступен только для разрешённых IP.
    """
    if not _is_admin_ip():
        return jsonify({'error': 'Доступ запрещён'}), 403

    new_mode = (request.get_json() or {}).get('mode', '').lower()
    if new_mode not in ('offline', 'online'):
        return jsonify({'error': 'mode должен быть offline или online'}), 400

    try:
        cfg = load_mode_config()
        cfg['mode'] = new_mode
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return jsonify({'success': True, 'mode': new_mode})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
