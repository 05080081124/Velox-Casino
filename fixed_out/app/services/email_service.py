"""app/services/email_service.py — Email / SMS notification service.

Handles:
  • OTP codes for email verification (9-digit, 60-second TTL)
  • Deposit / withdrawal receipts
  • Password-change notifications
  • SMS stub (replace body of _send_sms with real provider)

Config block in config/site.json:
  "email": {
    "smtp_host":     "smtp.gmail.com",
    "smtp_port":     587,
    "smtp_tls":      true,
    "smtp_user":     "no-reply@yourdomain.com",
    "smtp_password": "app_password_here",
    "from_name":     "VELOX Casino",
    "from_addr":     "no-reply@yourdomain.com",
    "enabled":       true
  }
"""

import json
import os
import random
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

# ── In-memory OTP store  {contact: {"code": str, "expires": float}} ──────────
_otp_store: dict[str, dict] = {}
_otp_lock = threading.Lock()

OTP_TTL = 60          # seconds
OTP_LENGTH = 9        # digits


# ── Config ────────────────────────────────────────────────────────────────────
#
# Приоритет загрузки настроек SMTP:
#   1. Переменные окружения (.env)  — пароли и ключи ВСЕГДА сюда
#   2. config/site.json             — только не-секретные поля (хост, порт, имена)
#
# Переменные в .env:
#   SMTP_HOST       smtp.gmail.com
#   SMTP_PORT       587
#   SMTP_TLS        true
#   SMTP_USER       no-reply@yourdomain.com
#   SMTP_PASSWORD   ваш_пароль_приложения     ← секрет, только здесь
#   SMTP_FROM_NAME  VELOX
#   SMTP_FROM_ADDR  no-reply@yourdomain.com
#   SMTP_ENABLED    true
#   KYC_NOTIFY_EMAIL  admin@yourdomain.com    ← секрет, только здесь

def _load_email_cfg() -> dict:
    """
    Собирает конфиг SMTP: сначала читает .env, потом перекрывает
    значениями из site.json (только если в .env не задано).
    Пароль берётся ИСКЛЮЧИТЕЛЬНО из .env.
    """
    # Базовые значения из site.json (не-секретные: хост, порт, имена)
    file_cfg: dict = {}
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site.json')
    try:
        with open(path, encoding='utf-8') as f:
            file_cfg = json.load(f).get('email', {})
    except Exception:
        pass

    # Env-переменные перекрывают всё из файла
    env = os.environ
    cfg = dict(file_cfg)                              # копия из файла как база

    if env.get('SMTP_HOST'):      cfg['smtp_host']     = env['SMTP_HOST']
    if env.get('SMTP_PORT'):      cfg['smtp_port']     = int(env['SMTP_PORT'])
    if env.get('SMTP_TLS'):       cfg['smtp_tls']      = env['SMTP_TLS'].lower() != 'false'
    if env.get('SMTP_USER'):      cfg['smtp_user']     = env['SMTP_USER']
    if env.get('SMTP_FROM_NAME'): cfg['from_name']     = env['SMTP_FROM_NAME']
    if env.get('SMTP_FROM_ADDR'): cfg['from_addr']     = env['SMTP_FROM_ADDR']
    if env.get('SMTP_ENABLED'):   cfg['enabled']       = env['SMTP_ENABLED'].lower() == 'true'

    # Пароль — ТОЛЬКО из .env, никогда из файла
    smtp_password = env.get('SMTP_PASSWORD', '').strip()
    if smtp_password:
        cfg['smtp_password'] = smtp_password
    else:
        # Если в .env пароля нет — стираем его даже если он был в site.json
        # (защита от случайного коммита site.json с паролем)
        cfg.pop('smtp_password', None)
        if cfg.get('enabled'):
            print('[EmailService] WARNING: SMTP_PASSWORD not set in .env — email disabled')
            cfg['enabled'] = False

    return cfg


def get_kyc_notify_email() -> str:
    """
    Возвращает email оператора для KYC-уведомлений.
    Приоритет: KYC_NOTIFY_EMAIL в .env → notify_email в site.json kyc-секции.
    """
    env_email = os.environ.get('KYC_NOTIFY_EMAIL', '').strip()
    if env_email:
        return env_email
    # Fallback: site.json → kyc.notify_email (не-секретный, но лучше в .env)
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('kyc', {}).get('notify_email', '')
    except Exception:
        return ''


# ── OTP helpers ───────────────────────────────────────────────────────────────

def generate_otp(contact: str) -> str:
    """Generate a new 9-digit OTP, store it and return the code."""
    code = ''.join([str(random.randint(0, 9)) for _ in range(OTP_LENGTH)])
    with _otp_lock:
        _otp_store[contact] = {'code': code, 'expires': time.time() + OTP_TTL}
    return code


def verify_otp(contact: str, code: str) -> bool:
    """Return True if code matches and is not expired; consume the entry."""
    with _otp_lock:
        entry = _otp_store.get(contact)
        if not entry:
            return False
        if time.time() > entry['expires']:
            del _otp_store[contact]
            return False
        if entry['code'] != str(code).strip():
            return False
        del _otp_store[contact]
        return True


def otp_remaining(contact: str) -> int:
    """Return seconds remaining for an active OTP, or 0 if none/expired."""
    with _otp_lock:
        entry = _otp_store.get(contact)
        if not entry:
            return 0
        remaining = int(entry['expires'] - time.time())
        return max(0, remaining)


# ── SMTP sender ───────────────────────────────────────────────────────────────

def _send_email(to_addr: str, subject: str, html_body: str) -> bool:
    cfg = _load_email_cfg()
    if not cfg.get('enabled', False):
        print(f'[EmailService] disabled — would send to {to_addr}: {subject}')
        return True  # silently succeed in dev mode

    try:
        msg = MIMEMultipart('alternative')
        from_name = cfg.get('from_name', 'Casino')
        from_addr = cfg.get('from_addr', cfg.get('smtp_user', ''))
        msg['From'] = f'{from_name} <{from_addr}>'
        msg['To'] = to_addr
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        host = cfg.get('smtp_host', 'smtp.gmail.com')
        port = int(cfg.get('smtp_port', 587))
        use_tls = cfg.get('smtp_tls', True)
        user = cfg.get('smtp_user', '')
        password = cfg.get('smtp_password', '')

        if use_tls:
            server = smtplib.SMTP(host, port, timeout=10)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(host, port, timeout=10)

        if user and password:
            server.login(user, password)

        server.sendmail(from_addr, to_addr, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f'[EmailService] send error: {e}')
        return False


def _send_async(to_addr: str, subject: str, html_body: str):
    """Fire-and-forget email in background thread."""
    t = threading.Thread(target=_send_email, args=(to_addr, subject, html_body), daemon=True)
    t.start()


# ── SMS stub ──────────────────────────────────────────────────────────────────

def _send_sms(phone: str, text: str) -> bool:
    """
    SMS stub — replace this body with your SMS provider SDK.
    Examples: Twilio, Vonage (Nexmo), MessageBird, SMSCenter, etc.

    Twilio example:
        from twilio.rest import Client
        client = Client(ACCOUNT_SID, AUTH_TOKEN)
        client.messages.create(body=text, from_=TWILIO_FROM, to=phone)
    """
    print(f'[SMSStub] TO={phone}  MSG={text}')
    return True  # stub always succeeds


# ── Template builder ──────────────────────────────────────────────────────────

def _casino_name() -> str:
    path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'site.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('casino_name', 'Casino')
    except Exception:
        return 'Casino'


def _html_wrapper(title: str, body_html: str) -> str:
    name = _casino_name()
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#0d0d0d;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d0d;padding:32px 0">
<tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0"
  style="background:#1a1a2e;border-radius:16px;overflow:hidden;
         border:1px solid #2a2a4a;max-width:480px;width:100%">
  <!-- header -->
  <tr><td style="background:linear-gradient(135deg,#6c3ff5,#00c8ff);
                 padding:24px 32px;text-align:center">
    <span style="color:#fff;font-size:22px;font-weight:700;
                 letter-spacing:2px;font-family:'Segoe UI',Arial,sans-serif">
      {name}
    </span>
  </td></tr>
  <!-- body -->
  <tr><td style="padding:28px 32px 24px;color:#e0e0e0;font-size:14px;line-height:1.7">
    {body_html}
  </td></tr>
  <!-- footer -->
  <tr><td style="padding:16px 32px;border-top:1px solid #2a2a4a;
                 color:#555;font-size:11px;text-align:center">
    &copy; {name} · Do not reply to this email
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


# ── Public API ────────────────────────────────────────────────────────────────

def send_otp_email(email: str, code: str, username: str = '') -> bool:
    """Send OTP verification code to email."""
    name = _casino_name()
    body = f"""
<p style="color:#aaa;margin:0 0 18px">Hi <strong style="color:#fff">{username or email}</strong>,</p>
<p style="margin:0 0 20px">Your verification code for <strong>{name}</strong>:</p>
<div style="background:#0d0d0d;border:1px solid #6c3ff5;border-radius:12px;
            padding:20px 0;text-align:center;margin:0 0 20px">
  <span style="font-size:36px;font-weight:700;letter-spacing:6px;
               color:#a78bfa;font-family:monospace">{code}</span>
</div>
<p style="color:#888;font-size:12px;margin:0">
  This code is valid for <strong style="color:#a78bfa">60 seconds</strong>.<br>
  If you did not request this, please ignore the message.
</p>"""
    html = _html_wrapper(f'Verification Code — {_casino_name()}', body)
    return _send_email(email, f'Your {_casino_name()} verification code', html)


def send_otp_sms(phone: str, code: str) -> bool:
    """Send OTP via SMS (stub)."""
    return _send_sms(phone, f'Your {_casino_name()} code: {code}. Valid 60 seconds.')


def send_deposit_receipt(email: str, username: str, amount: float,
                         currency: str, tx_id: str, method: str) -> bool:
    """Send deposit confirmation receipt."""
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 18px">Your deposit has been <strong style="color:#4ade80">confirmed</strong>.</p>
<table width="100%" cellpadding="0" cellspacing="0"
  style="background:#0d0d0d;border-radius:10px;border:1px solid #2a2a4a;margin-bottom:20px">
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Amount</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;
                 color:#4ade80;font-weight:700;font-size:18px">{amount} {currency}</td></tr>
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Method</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;color:#e0e0e0">{method}</td></tr>
  <tr><td style="padding:10px 16px;color:#888;font-size:12px">Transaction ID</td>
      <td style="padding:10px 16px;text-align:right;color:#6c9fff;font-size:12px;font-family:monospace">{tx_id}</td></tr>
</table>
<p style="color:#666;font-size:12px;margin:0">Funds are now available in your account.</p>"""
    html = _html_wrapper('Deposit Confirmed', body)
    _send_async(email, f'{_casino_name()} — Deposit {amount} {currency}', html)
    return True


def send_withdrawal_receipt(email: str, username: str, amount: float,
                             currency: str, tx_id: str, method: str,
                             status: str = 'Processing') -> bool:
    """Send withdrawal receipt."""
    status_color = '#4ade80' if status.lower() == 'completed' else '#facc15'
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 18px">Your withdrawal request has been received.</p>
<table width="100%" cellpadding="0" cellspacing="0"
  style="background:#0d0d0d;border-radius:10px;border:1px solid #2a2a4a;margin-bottom:20px">
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Amount</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;
                 color:#f97316;font-weight:700;font-size:18px">-{amount} {currency}</td></tr>
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Method</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;color:#e0e0e0">{method}</td></tr>
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Status</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;
                 color:{status_color};font-weight:600">{status}</td></tr>
  <tr><td style="padding:10px 16px;color:#888;font-size:12px">Transaction ID</td>
      <td style="padding:10px 16px;text-align:right;color:#6c9fff;font-size:12px;font-family:monospace">{tx_id}</td></tr>
</table>
<p style="color:#666;font-size:12px;margin:0">Processing usually takes 1–3 business days.</p>"""
    html = _html_wrapper('Withdrawal Request', body)
    _send_async(email, f'{_casino_name()} — Withdrawal {amount} {currency}', html)
    return True


def send_password_changed(email: str, username: str) -> bool:
    """Notify user that their password was changed."""
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 16px">
  Your <strong>{_casino_name()}</strong> account password was
  <strong style="color:#facc15">successfully changed</strong>.
</p>
<div style="background:#0d0d0d;border-left:3px solid #f97316;
            border-radius:6px;padding:14px 18px;margin-bottom:20px">
  <p style="margin:0;color:#f97316;font-size:13px">
    ⚠️ If you did NOT make this change, contact support immediately.
  </p>
</div>
<p style="color:#666;font-size:12px;margin:0">
  For security, we recommend enabling 2FA in your account settings.
</p>"""
    html = _html_wrapper('Password Changed', body)
    _send_async(email, f'{_casino_name()} — Password Changed', html)
    return True


def send_reset_link(email: str, username: str, reset_url: str) -> bool:
    """Send password reset link."""
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 18px">
  We received a request to reset the password for your <strong>{_casino_name()}</strong> account.
</p>
<div style="text-align:center;margin:24px 0">
  <a href="{reset_url}"
     style="display:inline-block;background:linear-gradient(135deg,#6c3ff5,#00c8ff);
            color:#fff;font-weight:700;font-size:15px;padding:13px 32px;
            border-radius:8px;text-decoration:none;letter-spacing:.03em">
    Reset Password
  </a>
</div>
<p style="color:#888;font-size:13px;text-align:center;word-break:break-all;margin:0 0 18px">
  Or copy this link: <br>
  <span style="color:#6c9fff">{reset_url}</span>
</p>
<div style="background:#0d0d0d;border-left:3px solid #facc15;
            border-radius:6px;padding:14px 18px;margin-bottom:16px">
  <p style="margin:0;color:#facc15;font-size:13px">
    ⚠️ Link expires in 30 minutes. If you did not request this, ignore this email.
  </p>
</div>
<p style="color:#666;font-size:12px;margin:0">
  For security, never share this link with anyone.
</p>"""
    html = _html_wrapper('Reset Your Password', body)
    _send_async(email, f'{_casino_name()} — Reset Your Password', html)
    return True


def send_referral_bonus(email: str, username: str, bonus: float, currency: str,
                        referred_username: str) -> bool:
    """Notify referrer that their invited friend registered and bonus was credited."""
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 18px">
  Your friend <strong style="color:#6c9fff">{referred_username}</strong> just joined
  <strong>{_casino_name()}</strong> using your referral link!
</p>
<div style="background:#0d0d0d;border-radius:10px;border:1px solid #2a2a4a;
            padding:20px;text-align:center;margin-bottom:20px">
  <p style="color:#888;font-size:12px;margin:0 0 6px">Referral bonus credited</p>
  <p style="color:#4ade80;font-weight:700;font-size:28px;margin:0">
    +{bonus} {currency}
  </p>
</div>
<p style="color:#666;font-size:12px;margin:0">
  Keep sharing your link to earn more bonuses!
</p>"""
    html = _html_wrapper('Referral Bonus Credited!', body)
    _send_async(email, f'{_casino_name()} — Referral Bonus +{bonus} {currency}', html)
    return True


def send_self_exclusion(email: str, username: str, until: str) -> bool:
    """Confirm self-exclusion to the player."""
    body = f"""
<p style="color:#aaa;margin:0 0 16px">Hi <strong style="color:#fff">{username}</strong>,</p>
<p style="margin:0 0 18px">
  Your self-exclusion request has been processed. Your account access is restricted until:
</p>
<div style="background:#0d0d0d;border-radius:10px;border:1px solid #2a2a4a;
            padding:20px;text-align:center;margin-bottom:20px">
  <p style="color:#f97316;font-weight:700;font-size:20px;margin:0">{until}</p>
</div>
<p style="margin:0 0 14px">
  During this period you will not be able to log in or make deposits.
  If you need help, please contact our support team.
</p>
<div style="background:#0d0d0d;border-left:3px solid #4ade80;
            border-radius:6px;padding:14px 18px">
  <p style="margin:0;color:#4ade80;font-size:13px">
    🛡 We support responsible gambling. Help is available at any time.
  </p>
</div>"""
    html = _html_wrapper('Self-Exclusion Confirmed', body)
    _send_async(email, f'{_casino_name()} — Self-Exclusion Confirmed', html)
    return True
