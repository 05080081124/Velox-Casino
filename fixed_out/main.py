"""
main.py — Точка входа VELOX Casino v3.

Запуск (разработка):
    python main.py

Запуск (продакшн):
    gunicorn "main:create_app()" -w 4 -k gevent --bind 0.0.0.0:5000

Режим сайта:
    config/site_mode.json → "mode": "offline" | "online"
    Меняется на лету без перезапуска сервера.
"""
import os, secrets, json, threading, time
from flask import Flask, render_template, send_from_directory, jsonify, request, session
from dotenv import load_dotenv

load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__, template_folder='templates', static_folder='static')

    # ── Конфигурация ─────────────────────────────────────────────────────────
    _sk = os.environ.get('SECRET_KEY')
    if not _sk:
        _sk_path = os.path.join(os.path.dirname(__file__), '.env')
        _sk_content = open(_sk_path).read() if os.path.exists(_sk_path) else ''
        if 'SECRET_KEY=' not in _sk_content:
            _sk = secrets.token_hex(32)
            with open(_sk_path, 'a') as _f:
                _f.write(f'\nSECRET_KEY={_sk}\n')
            print(f'  [KEY] Сгенерирован SECRET_KEY → .env')
        else:
            for _line in _sk_content.splitlines():
                if _line.startswith('SECRET_KEY='):
                    _sk = _line.split('=', 1)[1].strip()
    app.secret_key = _sk

    from datetime import timedelta
    app.config.update(
        SESSION_COOKIE_SAMESITE    = 'Lax',
        SESSION_COOKIE_HTTPONLY    = True,
        SESSION_COOKIE_SECURE      = os.environ.get('HTTPS', 'false').lower() == 'true',
        SESSION_COOKIE_NAME        = 'velox_sess',
        PERMANENT_SESSION_LIFETIME = timedelta(days=30),
        JSON_ENSURE_ASCII          = False,
        MAX_CONTENT_LENGTH         = 60 * 1024 * 1024,  # 60MB для KYC видео
        RATELIMIT_DEFAULT_LIMITS   = [],
    )

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Redis URL из .env (REDIS_URL=redis://localhost:6379/0)
    # Если Redis не задан — fallback на memory:// (для dev)
    _redis_url = os.environ.get('REDIS_URL', '').strip()

    # CloudFlare передаёт реальный IP в CF-Connecting-IP
    # Nginx передаёт в X-Real-IP или X-Forwarded-For
    def _real_ip():
        for header in ('CF-Connecting-IP', 'X-Real-IP', 'X-Forwarded-For'):
            val = request.headers.get(header, '').split(',')[0].strip()
            if val:
                return val
        return request.remote_addr or '0.0.0.0'

    try:
        from flask_limiter import Limiter

        _storage = _redis_url if _redis_url else 'memory://'
        try:
            limiter = Limiter(
                app=app,
                key_func=_real_ip,
                default_limits=[],
                storage_uri=_storage,
                strategy='fixed-window',
            )
            _backend = 'Redis' if _redis_url else 'memory (dev)'
            print(f'  [SEC]  Rate limiter: ON ({_backend})')
        except Exception as e:
            # If Redis backend is configured but redis dependency/server is unavailable,
            # fallback to in-memory storage so the app can still boot in dev.
            if _redis_url:
                limiter = Limiter(
                    app=app,
                    key_func=_real_ip,
                    default_limits=[],
                    storage_uri='memory://',
                    strategy='fixed-window',
                )
                print(f'  [SEC]  Rate limiter: Redis unavailable, fallback to memory:// ({e})')
            else:
                raise
        app.extensions['limiter'] = limiter
    except ImportError:
        limiter = None
        print('  [SEC]  Rate limiter: OFF (pip install flask-limiter redis)')

    # ── БД ───────────────────────────────────────────────────────────────────
    from app.db import init_db, close_pool, DB_ENGINE
    init_db()

    import atexit
    atexit.register(close_pool)

    # ── Режим сайта (offline/online) — ДО всех blueprint'ов ─────────────────
    from app.api.site_mode import mode_bp, offline_middleware
    app.register_blueprint(mode_bp)
    app.before_request(offline_middleware)

    # ── Blueprints ────────────────────────────────────────────────────────────
    from app.api.auth      import auth_bp
    from app.api.games     import games_bp
    from app.api.wallet    import wallet_bp
    from app.api.payments  import payments_bp
    from app.api.support   import support_bp
    from app.api.kyc       import kyc_bp
    from app.api.responsible_gambling import rg_bp
    from app.api.affiliate import affiliate_bp
    from app.api.vip       import vip_bp
    from app.api.currency  import currency_bp, start_currency_refresh
    from app.api.crash_api    import crash_bp, start_crash_loop
    from app.api.roulette_api import roulette_bp, start_roulette_loop
    from app.api.freespin     import freespin_bp
    from app.api.slots_api    import slots_bp
    from app.api.mineslot_api import mineslot_bp
    from app.api.sport        import sport_bp
    from app.api.admin        import admin_bp

    for bp in (auth_bp, games_bp, wallet_bp, payments_bp, support_bp,
               kyc_bp, rg_bp, affiliate_bp, currency_bp, vip_bp, crash_bp, roulette_bp,
               freespin_bp, slots_bp, mineslot_bp, sport_bp, admin_bp):
        app.register_blueprint(bp)

    # ── Применяем лимиты к чувствительным эндпоинтам ─────────────────────────
    if limiter:
        # Логин: 10 попыток в минуту с одного IP
        limiter.limit('10 per minute')(
            app.view_functions['auth.login']
        )
        # Регистрация: 5 в минуту
        for ep in ('auth.register', 'auth.register_send_otp'):
            try:
                limiter.limit('5 per minute')(app.view_functions[ep])
            except KeyError:
                pass
        # Депозит/вывод: 20 в минуту
        for ep in ('wallet.deposit', 'payments.create_payment', 'payments.withdraw_payment'):
            try:
                limiter.limit('20 per minute')(app.view_functions[ep])
            except KeyError:
                pass

        # Обработчик 429
        @app.errorhandler(429)
        def ratelimit_handler(e):
            return jsonify({'error': 'Слишком много запросов. Попробуйте через минуту.'}), 429

    # ── Маршруты ─────────────────────────────────────────────────────────────
    @app.route('/')
    def index():
        # Capture referral code from URL → store in session for registration
        ref = request.args.get('ref', '').strip().upper()
        if ref:
            session['pending_ref_code'] = ref
        return render_template('index.html')

    @app.route('/sport-settle')
    def sport_settle_page():
        """Admin-панель расчёта спортивных ставок."""
        from flask import session as _sess
        if not _sess.get('is_admin'):
            from flask import abort
            abort(403)
        return render_template('sport_settle.html')

    @app.route('/reset_password')
    def reset_password_page():
        """Password reset page — token validated client-side via API."""
        return render_template('index.html')

    @app.route('/static/uploads/<path:filename>')
    def uploaded_file(filename):
        return send_from_directory(
            os.path.join(app.root_path, 'static', 'uploads'), filename
        )

    # ── Игровые петли ────────────────────────────────────────────────────────
    # ── Security middleware ──────────────────────────────────────────────────
    from app.security import init_security
    import json as _json
    _sec_cfg = {}
    try:
        with open(os.path.join(os.path.dirname(__file__), 'config', 'security.json')) as _f:
            _sec_cfg = _json.load(_f)
    except Exception:
        pass
    _https = _sec_cfg.get('https', False) or os.environ.get('HTTPS','').lower() == 'true'
    init_security(app, https=_https)

    # Block manually banned IPs
    _blocked_ips = set(_sec_cfg.get('blocked_ips', []))
    if _blocked_ips:
        from flask import request as _req
        from app.security import get_client_ip as _gcip
        @app.before_request
        def _check_blocked():
            if _gcip() in _blocked_ips:
                from flask import jsonify as _j
                return _j({'error': 'Access denied.'}), 403

    start_crash_loop()
    start_roulette_loop()
    start_currency_refresh()

    # ── Sport auto-settle scheduler (optional) ───────────────────────────────
    def _start_sport_auto_settle_scheduler():
        cfg_path = os.path.join(os.path.dirname(__file__), 'config', 'bukmaking.json')
        try:
            with open(cfg_path, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
        auto = cfg.get('auto_settle', {}) or {}
        enabled = bool(auto.get('enabled', False))
        interval_sec = int(auto.get('interval_sec', 120))
        interval_sec = max(30, interval_sec)
        if not enabled:
            print('  [SPORT] Auto-settle scheduler: OFF')
            return

        def _loop():
            from app.api.sport import run_auto_settle
            while True:
                try:
                    with app.app_context():
                        res = run_auto_settle(actor_user_id=None)
                        if res.get('settled'):
                            print(f"  [SPORT] Auto-settle: {len(res.get('settled', []))} settled")
                except Exception as e:
                    print(f'  [SPORT] Auto-settle error: {e}')
                time.sleep(interval_sec)

        th = threading.Thread(target=_loop, name='sport-auto-settle', daemon=True)
        th.start()
        print(f'  [SPORT] Auto-settle scheduler: ON ({interval_sec}s)')

    _start_sport_auto_settle_scheduler()

    # ── Статус при запуске ───────────────────────────────────────────────────
    from app.api.site_mode import load_mode_config
    mode = load_mode_config().get('mode', 'online')
    mode_label = '[OFFLINE] maintenance' if mode == 'offline' else '[ONLINE]'
    try:
        import bcrypt  # noqa
        pw_algo = 'bcrypt OK'
    except ImportError:
        pw_algo = 'SHA-256 (install bcrypt)'

    print('\n  VELOX Casino v3')
    print(f'  [DB]   {DB_ENGINE.upper()}')
    print(f'  [SEC]  Passwords: {pw_algo}')
    print(f'  [MODE] {mode_label}')
    print(f'  ->  http://localhost:{os.environ.get("PORT", 5000)}\n')

    return app


if __name__ == '__main__':
    application = create_app()
    application.run(
        host     = '0.0.0.0',
        port     = int(os.environ.get('PORT', 5000)),
        debug    = os.environ.get('DEBUG', 'false').lower() == 'true',
        threaded = True,
    )