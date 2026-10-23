"""
velox_support/main.py — Точка входа VELOX Support Panel.

Запуск:
    python main.py

Продакшн:
    gunicorn "main:create_app()" -w 2 -k gevent --bind 0.0.0.0:5001
"""
import os, secrets, json
from flask import Flask, render_template, redirect, request, jsonify
from dotenv import load_dotenv


load_dotenv()


def create_app() -> Flask:
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    # ── Secret key ────────────────────────────────────────────────────────
    _sk = os.environ.get('SECRET_KEY')
    if not _sk:
        _sk_path = os.path.join(os.path.dirname(__file__), '.env')
        _sk = secrets.token_hex(32)
        with open(_sk_path, 'a') as f:
            f.write(f'\nSECRET_KEY={_sk}\n')
        print(f'  [KEY] Secret key generated -> .env')
    app.secret_key = _sk

    from datetime import timedelta
    app.config.update(
        SESSION_COOKIE_SAMESITE    = 'Lax',
        SESSION_COOKIE_HTTPONLY    = True,
        SESSION_COOKIE_SECURE      = os.environ.get('HTTPS', 'false').lower() == 'true',
        SESSION_COOKIE_NAME        = 'velox_support_sess',
        PERMANENT_SESSION_LIFETIME = timedelta(days=7),
        JSON_ENSURE_ASCII          = False,
    )

    # ── DB ────────────────────────────────────────────────────────────────
    from app.db import init_support_db, DB_ENGINE
    init_support_db()

    # ── Blueprints ────────────────────────────────────────────────────────
    from app.api.auth    import auth_bp
    from app.api.admin_panel import admin_panel_bp
    from app.api.tickets import tickets_bp
    from app.api.chat    import chat_bp
    from app.api.soc     import soc_bp
    from app.api.admin   import admin_bp          # ← добавить
    app.register_blueprint(auth_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(soc_bp)
    app.register_blueprint(admin_panel_bp)
    app.register_blueprint(admin_bp)   

    # ── Static lang files (serves config/languages/*.json) ───────────────
    @app.route('/static/langs/<lang>.json')
    def serve_lang(lang):
        allowed = {'ru', 'ua', 'en'}
        if lang.lower() not in allowed:
            return jsonify({}), 404
        lang_path = os.path.join(
            os.path.dirname(__file__), 'config', 'languages', f'{lang.lower()}.json'
        )
        if not os.path.exists(lang_path):
            return jsonify({}), 404
        with open(lang_path, encoding='utf-8') as f:
            return app.response_class(f.read(), mimetype='application/json')

    # ── Panel routes (auth guard in JS; server renders template) ─────────
    @app.route('/')
    def index():
        return render_template('auth.html')

    @app.route('/panel')
    def panel_dashboard():
        return render_template('dashboard.html')

    @app.route('/panel/tickets')
    def panel_tickets():
        return render_template('tickets.html')

    @app.route('/panel/ticket/<int:tid>')
    def panel_ticket_detail(tid):
        return render_template('ticket_detail.html', ticket_id=tid)

    @app.route('/panel/users')
    def panel_users():
        return render_template('users.html')

    @app.route('/panel/user/<int:uid>')
    def panel_user_detail(uid):
        return render_template('user_detail.html', user_id=uid)

    @app.route('/panel/settings')
    def panel_settings():
        return render_template('settings.html')

    @app.route('/panel/reports')
    def panel_reports():
        return render_template('reports.html')

    @app.route('/panel/chat')
    def panel_chat():
        return render_template('chat.html')

    @app.route('/panel/analytics')
    def panel_analytics():
        return render_template('analytics.html')

    @app.route('/panel/payments-queue')
    def panel_payments():
        return render_template('payments_queue.html')

    @app.route('/panel/team')
    def panel_team():
        return render_template('team.html')

    @app.route('/panel/bans')
    def panel_bans():
        return render_template('bans.html')

    @app.route('/panel/broadcast')
    def panel_broadcast():
        return render_template('broadcast.html')

    @app.route('/panel/game-builder')
    def panel_game_builder():
        return render_template('game_builder.html')

    @app.route('/panel/soc')
    def panel_soc():
        return render_template('soc.html')

    @app.route('/panel/banners')
    def panel_banners():
        return render_template('banners.html')

    @app.route('/panel/campaigns')
    def panel_campaigns():
        return render_template('campaigns.html')

    @app.route('/panel/site')
    def panel_site():
        return render_template('site.html')

    @app.route('/panel/tournament-builder')
    def panel_tournament_builder():
        return render_template('tournament_builder.html')

    @app.route('/config/perms.json')
    def serve_perms():
        import os
        p = os.path.join(os.path.dirname(__file__), 'config', 'perms.json')
        if not os.path.exists(p):
            return jsonify({}), 404
        with open(p, encoding='utf-8') as f:
            return app.response_class(f.read(), mimetype='application/json')

    @app.route('/support_uploads/banners/<path:filename>')
    def serve_banner_upload(filename):
        import os
        from flask import send_from_directory
        upload_dir = os.path.join(os.path.dirname(__file__), 'support_uploads', 'banners')
        return send_from_directory(upload_dir, filename)

    # ── Health check ──────────────────────────────────────────────────────
    @app.route('/health')
    def health():
        return jsonify({'ok': True, 'service': 'velox-support'})

    # ── Startup log ───────────────────────────────────────────────────────
    port    = int(os.environ.get('PORT', 5001))
    key_set = os.environ.get('ADMIN_REGISTER_KEY', '') not in ('', 'замени_на_случайный_ключ')

    try:
        import bcrypt; pw_algo = 'bcrypt'
    except ImportError:
        pw_algo = 'SHA-256 (pip install bcrypt)'

    print(f'\n  VELOX Support Panel')
    print(f'  [DB]   {DB_ENGINE.upper()}')
    print(f'  [SEC]  Пароли: {pw_algo}')
    print(f'  [KEY]  ADMIN_REGISTER_KEY: {"set" if key_set else "NOT SET (configure .env)"}')
    print(f'  ->  http://localhost:{port}\n')

    return app


if __name__ == '__main__':
    application = create_app()
    application.run(
        host     = '0.0.0.0',
        port     = int(os.environ.get('PORT', 5001)),
        debug    = os.environ.get('DEBUG', 'false').lower() == 'true',
        threaded = True,
    )