"""app/api/auth.py — Admin auth: key check, register (OTP), login, settings."""
import os
from flask import Blueprint, request, jsonify, make_response
from app.models.admin import Admin
from app.services.email_service import (
    generate_otp, verify_otp, otp_remaining, send_otp_email
)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')
ADMIN_KEY = os.environ.get('ADMIN_REGISTER_KEY', 'VELOX_ADMIN_KEY_CHANGE_ME')
SUPPORT_TOKEN_COOKIE = os.environ.get('SUPPORT_TOKEN_COOKIE_NAME', 'velox_support_token')


def _smtp_enabled() -> bool:
    """Returns True if SMTP is enabled in otp_system.json."""
    import json as _json
    cfg_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'otp_system.json')
    try:
        with open(cfg_path, encoding='utf-8') as f:
            cfg = _json.load(f)
        return bool(cfg.get('provider', {}).get('email', {}).get('enabled', False))
    except Exception:
        return False


# ── Auth helper ───────────────────────────────────────────────────────────────

def _get_admin(req) -> Admin | None:
    token = req.cookies.get(SUPPORT_TOKEN_COOKIE) or req.headers.get('X-Support-Token')
    return Admin.get_by_token(token) if token else None


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = _get_admin(request)
        if not admin:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, admin=admin, **kwargs)
    return wrapper


def _set_cookie(resp, token: str):
    resp.set_cookie(
        SUPPORT_TOKEN_COOKIE, token,
        httponly=True, samesite='Lax',
        max_age=7 * 24 * 3600,
        secure=os.environ.get('HTTPS', 'false').lower() == 'true',
    )


# ── Stage 1: key ──────────────────────────────────────────────────────────────

@auth_bp.route('/check-key', methods=['POST'])
def check_key():
    d   = request.get_json() or {}
    key = (d.get('key') or '').strip()
    if key != ADMIN_KEY:
        return jsonify({'error': 'Invalid access key'}), 403
    return jsonify({'ok': True, 'has_admins': Admin.count() > 0})


# ── Stage 2: registration ─────────────────────────────────────────────────────

@auth_bp.route('/register/send_otp', methods=['POST'])
def register_send_otp():
    d     = request.get_json() or {}
    key   = (d.get('key') or '').strip()
    email = (d.get('email') or '').strip().lower()

    if key != ADMIN_KEY:
        return jsonify({'error': 'Invalid access key'}), 403
    if not email or '@' not in email:
        return jsonify({'error': 'Invalid email address'}), 400
    if Admin.get_by_email(email):
        return jsonify({'error': 'This email is already registered'}), 409

    remaining = otp_remaining(email)
    if remaining > 0:
        return jsonify({'error': f'Please wait {remaining}s', 'remaining': remaining}), 429

    code = generate_otp(email)
    smtp_on = _smtp_enabled()
    if smtp_on:
        send_otp_email(email, code)
    masked = email[:2] + '***@' + email.split('@')[1]
    resp = {'ok': True, 'masked': masked, 'ttl': 60}
    if not smtp_on:
        resp['code'] = code  # return code directly when SMTP is disabled
    return jsonify(resp)


@auth_bp.route('/register/resend_otp', methods=['POST'])
def register_resend_otp():
    d     = request.get_json() or {}
    key   = (d.get('key') or '').strip()
    email = (d.get('email') or '').strip().lower()

    if key != ADMIN_KEY:
        return jsonify({'error': 'Invalid access key'}), 403
    if not email:
        return jsonify({'error': 'Email required'}), 400

    remaining = otp_remaining(email)
    if remaining > 0:
        return jsonify({'error': f'Please wait {remaining}s', 'remaining': remaining}), 429

    code = generate_otp(email)
    smtp_on = _smtp_enabled()
    if smtp_on:
        send_otp_email(email, code)
    masked = email[:2] + '***@' + email.split('@')[1]
    resp = {'ok': True, 'masked': masked, 'ttl': 60}
    if not smtp_on:
        resp['code'] = code
    return jsonify(resp)


@auth_bp.route('/register', methods=['POST'])
def register():
    d        = request.get_json() or {}
    key      = (d.get('key') or '').strip()
    email    = (d.get('email') or '').strip().lower()
    password = (d.get('password') or '').strip()
    nickname = (d.get('nickname') or '').strip()
    otp_code = (d.get('otp') or '').strip()
    role     = (d.get('role') or 'helper').strip().lower()

    if key != ADMIN_KEY:
        return jsonify({'error': 'Invalid access key'}), 403
    if not email or '@' not in email:
        return jsonify({'error': 'Invalid email address'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    if Admin.get_by_email(email):
        return jsonify({'error': 'This email is already registered'}), 409
    if _smtp_enabled() and not verify_otp(email, otp_code):
        return jsonify({'error': 'Invalid or expired verification code'}), 400

    admin = Admin.create(email=email, password=password, nickname=nickname, role=role)
    token = Admin.create_session(admin.id, ip=request.remote_addr)
    resp  = make_response(jsonify({'ok': True, 'admin': admin.to_dict()}))
    _set_cookie(resp, token)
    return resp


# ── Stage 3: login ────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['POST'])
def login():
    d        = request.get_json() or {}
    key      = (d.get('key') or '').strip()
    email    = (d.get('email') or '').strip().lower()
    password = (d.get('password') or '').strip()

    if key != ADMIN_KEY:
        return jsonify({'error': 'Invalid access key'}), 403
    if not email or not password:
        return jsonify({'error': 'Please fill in all fields'}), 400

    admin = Admin.get_by_email(email)
    if not admin or not admin.check_password(password):
        return jsonify({'error': 'Incorrect email or password'}), 401

    token = Admin.create_session(admin.id, ip=request.remote_addr)
    resp  = make_response(jsonify({'ok': True, 'admin': admin.to_dict()}))
    _set_cookie(resp, token)
    return resp


@auth_bp.route('/logout', methods=['POST'])
def logout():
    token = request.cookies.get(SUPPORT_TOKEN_COOKIE)
    if token:
        Admin.delete_session(token)
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(SUPPORT_TOKEN_COOKIE)
    return resp


@auth_bp.route('/me', methods=['GET'])
def me():
    admin = _get_admin(request)
    if not admin:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'admin': admin.to_dict()})


@auth_bp.route('/otp_remaining', methods=['POST'])
def otp_remaining_route():
    d     = request.get_json() or {}
    email = (d.get('email') or '').strip().lower()
    return jsonify({'remaining': otp_remaining(email) if email else 0})


# ── Settings: nickname ────────────────────────────────────────────────────────

@auth_bp.route('/settings/nickname', methods=['PUT'])
@require_admin
def settings_nickname(admin):
    d    = request.get_json() or {}
    nick = (d.get('nickname') or '').strip()
    if len(nick) < 2:
        return jsonify({'error': 'Nickname must be at least 2 characters'}), 400
    admin.update_nickname(nick)
    return jsonify({'ok': True, 'admin': admin.to_dict()})


# ── Settings: email change (OTP flow) ────────────────────────────────────────

@auth_bp.route('/settings/email/send_otp', methods=['POST'])
@require_admin
def settings_email_send_otp(admin):
    d        = request.get_json() or {}
    new_email = (d.get('email') or '').strip().lower()

    if not new_email or '@' not in new_email:
        return jsonify({'error': 'Invalid email address'}), 400
    if new_email == admin.email:
        return jsonify({'error': 'This is already your email'}), 400
    if Admin.get_by_email(new_email):
        return jsonify({'error': 'This email is already in use'}), 409

    # Use scoped OTP key: "email_change:new@email"
    otp_key   = f'email_change:{admin.id}:{new_email}'
    remaining = otp_remaining(otp_key)
    if remaining > 0:
        return jsonify({'error': f'Please wait {remaining}s', 'remaining': remaining}), 429

    code = generate_otp(otp_key)
    send_otp_email(new_email, code)
    masked = new_email[:2] + '***@' + new_email.split('@')[1]
    return jsonify({'ok': True, 'masked': masked, 'ttl': 60})


@auth_bp.route('/settings/email', methods=['PUT'])
@require_admin
def settings_email(admin):
    d         = request.get_json() or {}
    new_email = (d.get('email') or '').strip().lower()
    otp_code  = (d.get('otp') or '').strip()

    if not new_email or '@' not in new_email:
        return jsonify({'error': 'Invalid email address'}), 400
    if new_email == admin.email:
        return jsonify({'error': 'This is already your email'}), 400
    if Admin.get_by_email(new_email):
        return jsonify({'error': 'This email is already in use'}), 409

    otp_key = f'email_change:{admin.id}:{new_email}'
    if not verify_otp(otp_key, otp_code):
        return jsonify({'error': 'Invalid or expired verification code'}), 400

    admin.update_email(new_email)
    return jsonify({'ok': True, 'admin': admin.to_dict()})


# ── Settings: password change (OTP + notification) ────────────────────────────

@auth_bp.route('/settings/password/send_otp', methods=['POST'])
@require_admin
def settings_password_send_otp(admin):
    otp_key   = f'pw_change:{admin.id}'
    remaining = otp_remaining(otp_key)
    if remaining > 0:
        return jsonify({'error': f'Please wait {remaining}s', 'remaining': remaining}), 429

    code = generate_otp(otp_key)
    send_otp_email(admin.email, code)
    masked = admin.email[:2] + '***@' + admin.email.split('@')[1]
    return jsonify({'ok': True, 'masked': masked, 'ttl': 60})


@auth_bp.route('/settings/password', methods=['PUT'])
@require_admin
def settings_password(admin):
    d        = request.get_json() or {}
    cur_pw   = (d.get('current_password') or '').strip()
    new_pw   = (d.get('new_password') or '').strip()
    otp_code = (d.get('otp') or '').strip()

    if not admin.check_password(cur_pw):
        return jsonify({'error': 'Current password is incorrect'}), 400
    if len(new_pw) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    otp_key = f'pw_change:{admin.id}'
    if not verify_otp(otp_key, otp_code):
        return jsonify({'error': 'Invalid or expired verification code'}), 400

    admin.update_password(new_pw)
    # Invalidate all sessions — force re-login
    Admin.delete_all_sessions(admin.id)
    # Send notification email
    from app.services.email_service import send_password_changed
    send_password_changed(admin.email, admin.nickname)

    resp = make_response(jsonify({'ok': True, 'message': 'Password changed. Please sign in again.'}))
    resp.delete_cookie(SUPPORT_TOKEN_COOKIE)
    return resp


# ── Team management ───────────────────────────────────────────────────────────

@auth_bp.route('/team', methods=['GET'])
@require_admin
def get_team(admin):
    """List all admins (visible to ceo, op, admin roles)."""
    allowed = {'ceo', 'op', 'admin'}
    if admin.role not in allowed:
        return jsonify({'error': 'Access denied'}), 403
    members = Admin.get_all()
    return jsonify({'members': [dict(m) for m in members]})


@auth_bp.route('/team/<int:mid>/role', methods=['PUT'])
@require_admin
def set_role(mid, admin):
    """Change another admin's role (CEO / OP only)."""
    if admin.role not in ('ceo', 'op'):
        return jsonify({'error': 'Only CEO or OP can change roles'}), 403
    d    = request.get_json() or {}
    role = (d.get('role') or '').strip().lower()
    target = Admin.get_by_id(mid)
    if not target:
        return jsonify({'error': 'Admin not found'}), 404
    target.update_role(role)
    return jsonify({'ok': True})
