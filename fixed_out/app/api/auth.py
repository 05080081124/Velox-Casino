"""app/api/auth.py — Blueprint авторизации + Email/SMS OTP верификация."""
import os
import re
import secrets
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.db import get_db
from app.services.wallet_service import WalletError
from app.security import rate_limit_login, record_login_failure, record_login_success
from app.services.email_service import (
    generate_otp, verify_otp, otp_remaining,
    send_otp_email, send_otp_sms,
    send_password_changed,
)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

try:
    import pyotp  # type: ignore
except Exception:
    pyotp = None


def _current_user() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _verify_totp(user: User, code: str) -> bool:
    if not user.two_fa:
        return True
    secret = (user.two_fa_pass or '').strip()
    if not secret or not code:
        return False
    if pyotp:
        try:
            return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
        except Exception:
            return False
    # Minimal fallback if pyotp is unavailable: reject to avoid false security.
    return False


def _build_totp_setup_payload(user: User, secret: str) -> dict:
    issuer = os.environ.get('TOTP_ISSUER', 'VELOX Casino')
    label = user.email or user.phone or f'user-{user.id}'
    if pyotp:
        try:
            uri = pyotp.TOTP(secret).provisioning_uri(name=label, issuer_name=issuer)
        except Exception:
            uri = f'otpauth://totp/{issuer}:{label}?secret={secret}&issuer={issuer}'
    else:
        uri = f'otpauth://totp/{issuer}:{label}?secret={secret}&issuer={issuer}'
    from urllib.parse import quote_plus
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={quote_plus(uri)}"
    return {
        'secret': secret,
        'otpauth_url': uri,
        'qr_url': qr_url,
        'issuer': issuer,
    }


def _verify_totp_secret(secret: str, code: str) -> bool:
    if not secret or not code or not pyotp:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception:
        return False


def _load_security_config() -> dict:
    import json, os
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'security.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


# ── REGISTRATION ──────────────────────────────────────────────────────────────

@auth_bp.route('/register/send_otp', methods=['POST'])
def register_send_otp():
    d        = request.get_json() or {}
    username = (d.get('username') or '').strip()
    email    = (d.get('email')    or '').strip().lower()
    phone    = (d.get('phone')    or '').strip()
    pw       = d.get('password', '')
    cpw      = d.get('confirm_password', '')

    if not username or len(username) < 3:
        return jsonify({'error': 'Имя: минимум 3 символа'}), 400
    if not email and not phone:
        return jsonify({'error': 'Укажите почту или телефон'}), 400
    if email and not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return jsonify({'error': 'Некорректный email'}), 400
    if pw != cpw:
        return jsonify({'error': 'Пароли не совпадают'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'}), 400

    sec = _load_security_config()
    if email:
        domain = email.split('@')[1]
        if domain in sec.get('blocked_email_domains', []):
            return jsonify({'error': 'Временные почты запрещены'}), 400

    if email and User.get_by_email(email):
        return jsonify({'error': 'Email уже используется'}), 400
    if phone and User.get_by_phone(phone):
        return jsonify({'error': 'Телефон уже используется'}), 400

    contact = email or phone
    code    = generate_otp(contact)

    # ── Check if SMTP/OTP is enabled ──────────────────────────────────────
    import json as _json, os as _os
    _site_path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'config', 'site.json')
    try:
        with open(_site_path, encoding='utf-8') as _f:
            _site = _json.load(_f)
        _smtp_enabled = _site.get('email', {}).get('enabled', True)
    except Exception:
        _smtp_enabled = True

    # Also check SMTP_ENABLED env var (set by email_service)
    if _os.environ.get('SMTP_ENABLED', '').lower() == 'false':
        _smtp_enabled = False

    if not _smtp_enabled:
        # OTP disabled — register directly without email verification
        return jsonify({'success': True, 'skip_otp': True, 'via': 'none'})

    if email:
        send_otp_email(email, code, username)
        masked = email[:2] + '***@' + email.split('@')[1]
    else:
        send_otp_sms(phone, code)
        masked = phone[:4] + '***' + phone[-2:]

    return jsonify({'success': True, 'masked': masked, 'via': 'email' if email else 'sms', 'ttl': 60})


@auth_bp.route('/register/verify_otp', methods=['POST'])
def register_verify_otp():
    d        = request.get_json() or {}
    code     = (d.get('code')     or '').strip()
    username = (d.get('username') or '').strip()
    email    = (d.get('email')    or '').strip().lower()
    phone    = (d.get('phone')    or '').strip()
    pw       = d.get('password', '')
    cpw      = d.get('confirm_password', '')

    if not code:
        return jsonify({'error': 'Введите код'}), 400

    contact = email or phone
    if not verify_otp(contact, code):
        return jsonify({'error': 'Неверный или устаревший код'}), 400

    if email and User.get_by_email(email):
        return jsonify({'error': 'Email уже используется'}), 400
    if phone and User.get_by_phone(phone):
        return jsonify({'error': 'Телефон уже используется'}), 400

    user = User.create(username, pw, email=email or None, phone=phone or None)

    # Apply referral code if stored in session
    ref_code = session.pop('pending_ref_code', None)
    if ref_code:
        referrer = User.get_by_ref_code(ref_code)
        if referrer and referrer.id != user.id:
            from app.db import get_db as _gdb
            with _gdb() as db:
                db.execute('UPDATE users SET referred_by = %s WHERE id = %s',
                           (referrer.id, user.id))

    # Ensure new user gets their own ref_code
    user.ensure_ref_code()

    return jsonify({'success': True, 'pending_user_id': user.id})


@auth_bp.route('/register/confirm_policy', methods=['POST'])
def register_confirm_policy():
    d   = request.get_json() or {}
    uid = d.get('pending_user_id')
    if not uid:
        return jsonify({'error': 'Неверный запрос'}), 400
    user = User.get_by_id(uid)
    if not user:
        return jsonify({'error': 'Пользователь не найден'}), 404
    session.permanent  = True
    session['user_id'] = user.id
    return jsonify({'success': True, 'user': user.to_dict()})


@auth_bp.route('/register/resend_otp', methods=['POST'])
def register_resend_otp():
    d        = request.get_json() or {}
    email    = (d.get('email')    or '').strip().lower()
    phone    = (d.get('phone')    or '').strip()
    username = (d.get('username') or '').strip()
    contact  = email or phone

    if not contact:
        return jsonify({'error': 'Укажите почту или телефон'}), 400

    remaining = otp_remaining(contact)
    if remaining > 0:
        return jsonify({'error': f'Подождите ещё {remaining} сек.', 'remaining': remaining}), 429

    code = generate_otp(contact)
    if email:
        send_otp_email(email, code, username)
        masked = email[:2] + '***@' + email.split('@')[1]
    else:
        send_otp_sms(phone, code)
        masked = phone[:4] + '***' + phone[-2:]

    return jsonify({'success': True, 'masked': masked, 'ttl': 60})




# ── LEGACY: /register kept for rate-limiter key + backwards compat ────────────
@auth_bp.route('/register', methods=['POST'])
def register():
    """Legacy endpoint — routes to send_otp flow."""
    return register_send_otp()


@auth_bp.route('/register/direct', methods=['POST'])
def register_direct():
    """
    Direct registration without OTP — used when SMTP is disabled.
    Creates user and immediately opens session (policy already accepted on frontend).
    """
    d        = request.get_json() or {}
    username = (d.get('username') or '').strip()
    email    = (d.get('email')    or '').strip().lower()
    phone    = (d.get('phone')    or '').strip()
    pw       = d.get('password', '')

    if not username or len(username) < 3:
        return jsonify({'error': 'Имя: минимум 3 символа'}), 400
    if not email and not phone:
        return jsonify({'error': 'Укажите почту или телефон'}), 400
    if pw and len(pw) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'}), 400

    if email and User.get_by_email(email):
        return jsonify({'error': 'Email уже используется'}), 400
    if phone and User.get_by_phone(phone):
        return jsonify({'error': 'Телефон уже используется'}), 400

    user = User.create(username, pw, email=email or None, phone=phone or None)

    ref_code = session.pop('pending_ref_code', None)
    if ref_code:
        referrer = User.get_by_ref_code(ref_code)
        if referrer and referrer.id != user.id:
            from app.db import get_db as _gdb
            with _gdb() as db:
                db.execute('UPDATE users SET referred_by = %s WHERE id = %s',
                           (referrer.id, user.id))

    user.ensure_ref_code()
    session.permanent  = True
    session['user_id'] = user.id
    return jsonify({'success': True, 'user': user.to_dict()})

@auth_bp.route('/otp_remaining', methods=['POST'])
def otp_remaining_route():
    d       = request.get_json() or {}
    contact = (d.get('email') or d.get('phone') or '').strip().lower()
    return jsonify({'remaining': otp_remaining(contact) if contact else 0})


# ── LOGIN ─────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['POST'])
def login():
    d         = request.get_json() or {}
    login_val = (d.get('login') or '').strip()
    pw        = d.get('password', '')
    twofa     = d.get('two_fa', '')

    if not login_val or not pw:
        return jsonify({'error': 'Заполните все поля'}), 400

    # ── Account lockout + IP rate limit check ─────────────────────────────
    allowed, err = rate_limit_login(login_val)
    if not allowed:
        return jsonify(err), 429

    user = (User.get_by_email(login_val.lower())
            if '@' in login_val else User.get_by_phone(login_val))

    if not user or not user.check_password(pw):
        remaining = record_login_failure(login_val)
        msg = 'Неверные данные'
        if remaining <= 3:
            msg += f' (осталось попыток: {remaining})'
        return jsonify({'error': msg}), 401

    if user.two_fa:
        if not twofa:
            return jsonify({'requires_2fa': True}), 200
        if not _verify_totp(user, twofa):
            return jsonify({'error': 'Неверный код 2FA'}), 401

    record_login_success(login_val)
    session.permanent  = True
    session['user_id'] = user.id
    return jsonify({'success': True, 'user': user.to_dict()})


# ── LOGOUT / ME ───────────────────────────────────────────────────────────────

@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})


@auth_bp.route('/me')
def me():
    user = _current_user()
    return jsonify({'user': user.to_dict() if user else None})


# ── PROFILE ───────────────────────────────────────────────────────────────────

@auth_bp.route('/profile', methods=['PUT'])
def update_profile():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    d = request.get_json() or {}
    user.update_profile(username=d.get('username'), currency=d.get('currency'), icon_url=d.get('icon_url'))
    return jsonify({'success': True, 'user': user.to_dict()})


@auth_bp.route('/profile/phone', methods=['PUT'])
def update_profile_phone():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d = request.get_json() or {}
    raw_phone = (d.get('phone') or '').strip()
    phone = re.sub(r'[^\d+]', '', raw_phone)
    if not phone:
        return jsonify({'error': 'Укажите номер телефона'}), 400
    if len(re.sub(r'\D', '', phone)) < 8:
        return jsonify({'error': 'Некорректный номер телефона'}), 400

    exists = User.get_by_phone(phone)
    if exists and exists.id != user.id:
        return jsonify({'error': 'Телефон уже используется'}), 400

    with get_db() as db:
        db.execute('UPDATE users SET phone = %s WHERE id = %s', (phone, user.id))

    fresh = User.get_by_id(user.id) or user
    return jsonify({'success': True, 'user': fresh.to_dict()})


@auth_bp.route('/profile/email/send_otp', methods=['POST'])
def profile_send_email_otp():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d = request.get_json() or {}
    email = (d.get('email') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Укажите email'}), 400
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return jsonify({'error': 'Некорректный email'}), 400

    exists = User.get_by_email(email)
    if exists and exists.id != user.id:
        return jsonify({'error': 'Email уже используется'}), 400
    if user.email and user.email.lower() == email:
        return jsonify({'error': 'Это уже ваш текущий email'}), 400

    remaining = otp_remaining(email)
    if remaining > 0:
        return jsonify({'error': f'Подождите ещё {remaining} сек.', 'remaining': remaining}), 429

    code = generate_otp(email)
    send_otp_email(email, code, user.username)
    session['pending_email_change'] = email
    masked = email[:2] + '***@' + email.split('@')[1]
    return jsonify({'success': True, 'masked': masked, 'ttl': 60})


@auth_bp.route('/profile/email/verify_otp', methods=['POST'])
def profile_verify_email_otp():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d = request.get_json() or {}
    code = (d.get('code') or '').strip()
    email = (session.get('pending_email_change') or '').strip().lower()
    if not email:
        return jsonify({'error': 'Сначала запросите код подтверждения'}), 400
    if not code:
        return jsonify({'error': 'Введите код подтверждения'}), 400
    if not verify_otp(email, code):
        return jsonify({'error': 'Неверный или устаревший код'}), 400

    exists = User.get_by_email(email)
    if exists and exists.id != user.id:
        session.pop('pending_email_change', None)
        return jsonify({'error': 'Email уже используется'}), 400

    with get_db() as db:
        db.execute('UPDATE users SET email = %s WHERE id = %s', (email, user.id))
    session.pop('pending_email_change', None)

    fresh = User.get_by_id(user.id) or user
    return jsonify({'success': True, 'user': fresh.to_dict()})


# ── PASSWORD ──────────────────────────────────────────────────────────────────

@auth_bp.route('/password', methods=['PUT'])
def change_password():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    d   = request.get_json() or {}
    cur = d.get('current_password', '')
    nw  = d.get('new_password', '')
    cnf = d.get('confirm_password', '')
    tfa = d.get('two_fa', '')

    if not user.check_password(cur):
        return jsonify({'error': 'Неверный текущий пароль'}), 400
    if nw != cnf:
        return jsonify({'error': 'Пароли не совпадают'}), 400
    if len(nw) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'}), 400
    if user.two_fa and not _verify_totp(user, tfa):
        return jsonify({'error': 'Неверный код 2FA'}), 400

    user.change_password(nw)
    if user.email:
        send_password_changed(user.email, user.username)
    return jsonify({'success': True})


# ── 2FA ───────────────────────────────────────────────────────────────────────

@auth_bp.route('/2fa', methods=['PUT'])
def toggle_2fa():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    d      = request.get_json() or {}
    action = d.get('action')
    pw     = d.get('password', '')
    tfa    = d.get('two_fa', '')

    if not user.check_password(pw):
        return jsonify({'error': 'Неверный пароль'}), 400
    if action == 'enable' and not user.two_fa:
        pending_secret = session.get('pending_2fa_secret', '')
        code = (tfa or '').strip()
        if code and pending_secret:
            if not _verify_totp_secret(pending_secret, code):
                return jsonify({'error': 'Неверный код 2FA'}), 400
            with get_db() as db:
                db.execute(
                    'UPDATE users SET two_fa = 1, two_fa_pass = %s WHERE id = %s',
                    (pending_secret, user.id),
                )
            session.pop('pending_2fa_secret', None)
            return jsonify({'success': True})
        if not pending_secret:
            if pyotp:
                pending_secret = pyotp.random_base32()
            else:
                return jsonify({'error': 'pyotp не установлен на сервере'}), 500
            session['pending_2fa_secret'] = pending_secret
        return jsonify({'success': True, 'two_fa_setup': _build_totp_setup_payload(user, pending_secret)})
    elif action == 'disable' and user.two_fa:
        if not _verify_totp(user, tfa):
            return jsonify({'error': 'Неверный код 2FA'}), 400
        user.disable_2fa()
        return jsonify({'success': True})
    return jsonify({'error': 'Некорректное действие'}), 400


# ── TICKET ────────────────────────────────────────────────────────────────────

@auth_bp.route('/ticket/use', methods=['POST'])
def use_ticket():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    if user.ticket_used:
        return jsonify({'error': 'Билет уже использован'}), 400

    d            = request.get_json() or {}
    reward_type  = d.get('type', '')
    reward_val   = d.get('value', 0)

    try:
        val = float(reward_val)
    except (TypeError, ValueError):
        val = 0.0

    from app.db import get_db
    try:
        if reward_type == 'balance' and val > 0:
            from app.db import DB_ENGINE
            p = '%s' if DB_ENGINE == 'postgresql' else '?'
            with get_db() as db:
                db.execute(f'UPDATE users SET balance = balance + {p} WHERE id = {p}', (val, user.id))
        elif reward_type == 'freespin' and val > 0:
            user.add_free_spins(int(val))
        elif reward_type == 'deposit_mult' and val > 0:
            user.add_bonus_deposit(val)
    except Exception as e:
        print(f'[ticket/use] reward error: {e}')

    user.mark_ticket_used()
    fresh = User.get_by_id(user.id) or user
    return jsonify({'success': True, 'user': fresh.to_dict()})


@auth_bp.route('/ticket/status')
def ticket_status():
    user = _current_user()
    if not user:
        return jsonify({'logged_in': False, 'ticket_used': False}), 200
    return jsonify({'logged_in': True, 'ticket_used': user.ticket_used})


# ── KYC ───────────────────────────────────────────────────────────────────────

@auth_bp.route('/verify', methods=['POST'])
def submit_verify():
    user = _current_user()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    return jsonify({'success': True, 'message': 'Документы отправлены на проверку'})


# ── FORGOT PASSWORD ───────────────────────────────────────────────────────────

@auth_bp.route('/forgot_password', methods=['POST'])
def forgot_password():
    """Send password reset link to email."""
    d       = request.get_json() or {}
    contact = (d.get('contact') or '').strip()
    twofa   = (d.get('two_fa') or '').strip()

    if not contact:
        return jsonify({'error': 'Укажите почту или телефон'}), 400

    user = (User.get_by_email(contact.lower())
            if '@' in contact else User.get_by_phone(contact))

    # Always return success to prevent user enumeration
    if not user:
        return jsonify({'success': True}), 200

    if user.two_fa:
        if not twofa:
            return jsonify({'requires_2fa': True}), 200
        if not _verify_totp(user, twofa):
            return jsonify({'error': 'Неверный код 2FA'}), 401

    if not user.email:
        # Phone-only accounts — fallback to support ticket
        from app.db import get_db as _gdb
        with _gdb() as db:
            db.execute(
                "INSERT INTO support_tickets (user_id, contact, subject, description, created_at)"
                " VALUES (%s, %s, %s, %s, NOW())",
                (user.id, contact, '[Забыт пароль]',
                 f'Восстановление пароля: {user.username} (ID {user.id})')
            )
        return jsonify({'success': True, 'method': 'ticket'}), 200

    token   = user.set_reset_token()
    base    = os.environ.get('BASE_URL', 'http://localhost:5000').rstrip('/')
    link    = f'{base}/reset_password?token={token}'

    from app.services.email_service import send_reset_link
    send_reset_link(user.email, user.username, link)

    return jsonify({'success': True, 'method': 'email'}), 200


@auth_bp.route('/reset_password', methods=['POST'])
def reset_password():
    """Confirm password reset using token."""
    d     = request.get_json() or {}
    token = (d.get('token') or '').strip()
    pw    = d.get('new_password', '')
    cpw   = d.get('confirm_password', pw)

    if not token:
        return jsonify({'error': 'Токен не указан'}), 400
    if pw != cpw:
        return jsonify({'error': 'Пароли не совпадают'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Пароль минимум 6 символов'}), 400

    user = User.get_by_reset_token(token)
    if not user:
        return jsonify({'error': 'Ссылка недействительна или истекла'}), 400

    user.change_password(pw)
    user.clear_reset_token()

    if user.email:
        from app.services.email_service import send_password_changed
        send_password_changed(user.email, user.username)

    return jsonify({'success': True})


@auth_bp.route('/reset_password/validate', methods=['POST'])
def validate_reset_token():
    """Check if reset token is still valid (GET-friendly POST)."""
    token = (request.get_json() or {}).get('token', '').strip()
    user  = User.get_by_reset_token(token) if token else None
    return jsonify({'valid': bool(user), 'username': user.username if user else None})