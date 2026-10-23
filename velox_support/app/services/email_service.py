"""app/services/email_service.py — OTP + email notifications for VELOX Support.

Config: config/otp_system.json
  - provider settings (SMTP / SMS stub)
  - OTP length & TTL
  - multilingual templates (RU / UA / EN)
"""

import json
import os
import random
import smtplib
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# In-memory OTP store  {contact: {"code": str, "expires": float}}
_otp_store: dict = {}
_otp_lock = threading.Lock()

# Defaults (overridden by otp_system.json)
_DEFAULT_TTL    = 60
_DEFAULT_LENGTH = 9


# ── Config loader ─────────────────────────────────────────────────────────────

def _cfg_path() -> str:
    return os.path.join(
        os.path.dirname(__file__), '..', '..', 'config', 'otp_system.json'
    )


def _load_cfg() -> dict:
    try:
        with open(_cfg_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'  [OTP] Cannot read otp_system.json: {e}')
        return {}


def _get_otp_settings():
    """Returns (length, ttl_seconds)."""
    cfg = _load_cfg()
    otp = cfg.get('otp', {})
    return (
        int(otp.get('length', _DEFAULT_LENGTH)),
        int(otp.get('ttl_seconds', _DEFAULT_TTL)),
    )


def _get_email_cfg() -> dict:
    cfg = _load_cfg()
    return cfg.get('provider', {}).get('email', {})


def _get_sms_cfg() -> dict:
    cfg = _load_cfg()
    return cfg.get('provider', {}).get('sms', {})


def _get_template(lang=None) -> dict:
    cfg       = _load_cfg()
    default   = cfg.get('default_language', 'EN').upper()
    lang      = (lang or default).upper()
    templates = cfg.get('templates', {})
    return (
        templates.get(lang)
        or templates.get(default)
        or templates.get('EN')
        or {}
    )


def _provider_type() -> str:
    return _load_cfg().get('provider', {}).get('type', 'email').lower()


# ── OTP core ──────────────────────────────────────────────────────────────────

def generate_otp(contact: str) -> str:
    """Generate OTP, store it and return the code."""
    length, ttl = _get_otp_settings()
    code = ''.join([str(random.randint(0, 9)) for _ in range(length)])
    with _otp_lock:
        _otp_store[contact.lower()] = {
            'code':    code,
            'expires': time.time() + ttl,
        }
    return code


def verify_otp(contact: str, code: str) -> bool:
    """Return True if code matches and not expired; consume the entry."""
    with _otp_lock:
        entry = _otp_store.get(contact.lower())
        if not entry:
            return False
        if time.time() > entry['expires']:
            del _otp_store[contact.lower()]
            return False
        if entry['code'] != str(code).strip():
            return False
        del _otp_store[contact.lower()]
        return True


def otp_remaining(contact: str) -> int:
    """Return seconds remaining for an active OTP, or 0."""
    with _otp_lock:
        entry = _otp_store.get(contact.lower())
        if not entry:
            return 0
        return max(0, int(entry['expires'] - time.time()))


# ── SMTP sender ───────────────────────────────────────────────────────────────

def _send_email(to_addr: str, subject: str, html_body: str) -> bool:
    cfg = _get_email_cfg()
    if not cfg.get('enabled', False):
        print(f'  [Support/OTP] Email disabled — would send to {to_addr}: {subject}')
        return True

    try:
        msg = MIMEMultipart('alternative')
        from_name = cfg.get('from_name', 'VELOX Support')
        from_addr = cfg.get('from_addr', cfg.get('smtp_user', ''))
        msg['From']    = f'{from_name} <{from_addr}>'
        msg['To']      = to_addr
        msg['Subject'] = subject
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))

        host     = cfg.get('smtp_host', 'smtp.gmail.com')
        port     = int(cfg.get('smtp_port', 587))
        use_tls  = cfg.get('smtp_tls', True)
        user     = cfg.get('smtp_user', '')
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
        print(f'  [Support/OTP] Sent to {to_addr}')
        return True

    except Exception as e:
        print(f'  [Support/OTP] Send error: {e}')
        return False


def _send_async(to_addr: str, subject: str, html_body: str):
    """Non-blocking: sends email in a background thread."""
    threading.Thread(
        target=_send_email,
        args=(to_addr, subject, html_body),
        daemon=True,
    ).start()


# ── SMS stub ──────────────────────────────────────────────────────────────────

def _send_sms(phone: str, text: str) -> bool:
    """
    SMS stub — reads config from otp_system.json → provider.sms.
    To activate a real provider: set enabled=true, fill credentials,
    then replace the body below with your SDK calls.

    Twilio example:
        from twilio.rest import Client
        cfg = _get_sms_cfg()
        Client(cfg['account_sid'], cfg['auth_token']).messages.create(
            body=text, from_=cfg['from_number'], to=phone
        )
    """
    cfg = _get_sms_cfg()
    if not cfg.get('enabled', False):
        print(f'  [Support/SMS] Stub — TO={phone}  MSG={text}')
        return True
    print(f'  [Support/SMS] Provider={cfg.get("provider_name")} TO={phone}  MSG={text}')
    return True


# ── HTML template builder ─────────────────────────────────────────────────────

def _build_html(code: str, lang=None):
    """
    Returns (subject, html_body) built from otp_system.json templates.
    Falls back to EN if requested lang is not found.
    """
    _, ttl = _get_otp_settings()
    tpl    = _get_template(lang)

    subject  = tpl.get('email_subject', 'VELOX Support — Verification Code')
    greeting = tpl.get('email_greeting', 'Hello,')
    body_txt = tpl.get('email_body', 'Your verification code:')
    expiry   = tpl.get('email_expiry', f'Valid for <strong>{ttl} seconds</strong>.')
    expiry   = expiry.replace('{ttl}', str(ttl)).replace('{code}', code)
    warning  = tpl.get('email_warning', 'If you did not request this, ignore the message.')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#05050e;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#05050e;padding:40px 0">
  <tr><td align="center">
  <table width="460" cellpadding="0" cellspacing="0"
         style="background:#09091a;border-radius:14px;overflow:hidden;
                border:1px solid rgba(0,229,255,0.12);max-width:460px;width:100%">

    <!-- header -->
    <tr>
      <td style="padding:22px 32px;border-bottom:1px solid rgba(255,255,255,0.06)">
        <span style="font-family:monospace;font-size:20px;font-weight:700;
                     letter-spacing:5px;color:#00e5ff">VELOX</span>
        <span style="display:block;font-size:10px;letter-spacing:3px;
                     color:#44445a;margin-top:3px;font-family:monospace">SUPPORT PANEL</span>
      </td>
    </tr>

    <!-- body -->
    <tr>
      <td style="padding:28px 32px 8px;color:#e6e6f5;font-size:14px;line-height:1.7">
        <p style="color:#8888aa;margin:0 0 16px">{greeting}</p>
        <p style="margin:0 0 22px">{body_txt}</p>

        <div style="background:#05050e;border:1px solid rgba(0,229,255,0.22);
                    border-radius:10px;padding:22px 0;text-align:center;margin:0 0 22px">
          <span style="font-size:36px;font-weight:700;letter-spacing:10px;
                       color:#00e5ff;font-family:monospace;
                       text-shadow:0 0 20px rgba(0,229,255,0.4)">{code}</span>
        </div>

        <p style="color:#8888aa;font-size:12px;margin:0 0 10px">{expiry}</p>
        <p style="color:#44445a;font-size:11px;margin:0 0 24px">{warning}</p>
      </td>
    </tr>

    <!-- footer -->
    <tr>
      <td style="padding:14px 32px;border-top:1px solid rgba(255,255,255,0.06);
                 color:#44445a;font-size:11px;text-align:center">
        VELOX Support &middot; Do not reply to this email
      </td>
    </tr>
  </table>
  </td></tr>
</table>
</body>
</html>"""

    return subject, html


# ── Public API ────────────────────────────────────────────────────────────────

def send_otp_email(email: str, code: str, lang=None) -> bool:
    """
    Send OTP to admin email.
    lang: 'RU' | 'UA' | 'EN' | None  (None = use default_language from otp_system.json)
    Non-blocking — fires in background thread.
    """
    subject, html = _build_html(code, lang)
    _send_async(email, subject, html)
    return True


def send_otp_sms(phone: str, code: str, lang=None) -> bool:
    """Send OTP via SMS (stub by default)."""
    _, ttl = _get_otp_settings()
    tpl    = _get_template(lang)
    text   = (
        tpl.get('sms_text', 'VELOX Support: your code is {code}. Valid for {ttl}s.')
        .replace('{code}', code)
        .replace('{ttl}', str(ttl))
    )
    return _send_sms(phone, text)


def send_password_changed(email: str, nickname: str) -> bool:
    """Notify admin that their password was changed."""
    cfg = _load_cfg()
    default_lang = cfg.get('default_language', 'EN').upper()

    subjects = {'RU': 'VELOX Support — Пароль изменён',
                'UA': 'VELOX Support — Пароль змінено',
                'EN': 'VELOX Support — Password Changed'}
    subject = subjects.get(default_lang, subjects['EN'])

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#05050e;font-family:'Segoe UI',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#05050e;padding:40px 0">
  <tr><td align="center">
  <table width="460" cellpadding="0" cellspacing="0"
         style="background:#09091a;border-radius:14px;overflow:hidden;
                border:1px solid rgba(0,229,255,0.12);max-width:460px;width:100%">
    <tr><td style="padding:22px 32px;border-bottom:1px solid rgba(255,255,255,0.06)">
      <span style="font-family:monospace;font-size:20px;font-weight:700;
                   letter-spacing:5px;color:#00e5ff">VELOX</span>
      <span style="display:block;font-size:10px;letter-spacing:3px;
                   color:#44445a;margin-top:3px;font-family:monospace">SUPPORT PANEL</span>
    </td></tr>
    <tr><td style="padding:28px 32px 8px;color:#e6e6f5;font-size:14px;line-height:1.7">
      <p style="color:#8888aa;margin:0 0 16px">Hello, <strong style="color:#e6e6f5">{nickname}</strong></p>
      <p style="margin:0 0 18px">Your VELOX Support Panel password has been
         <strong style="color:#00e5ff">successfully changed</strong>.</p>
      <div style="background:#05050e;border-left:3px solid #ff4560;
                  border-radius:6px;padding:14px 18px;margin-bottom:20px">
        <p style="margin:0;color:#ff4560;font-size:13px">
          ⚠ If you did NOT make this change, contact your system administrator immediately.
        </p>
      </div>
      <p style="color:#44445a;font-size:11px;margin:0 0 24px">
        All active sessions have been invalidated. Please sign in again.
      </p>
    </td></tr>
    <tr><td style="padding:14px 32px;border-top:1px solid rgba(255,255,255,0.06);
               color:#44445a;font-size:11px;text-align:center">
      VELOX Support &middot; Do not reply to this email
    </td></tr>
  </table>
  </td></tr>
</table>
</body></html>"""

    _send_async(email, subject, html)
    return True


# ── Support reply notification ────────────────────────────────────────────────

def send_support_reply(to_email: str, username: str, subject: str,
                       admin_message: str, ticket_id, lang: str = 'ru') -> bool:
    cfg       = _get_email_cfg()
    from_name = cfg.get('from_name', 'VELOX Support')

    _i18n = {
        'ru': {
            'greeting': 'Привет,', 'replied': 'Администратор ответил на твоё обращение:',
            'topic': 'Тема', 'response': 'Ответ поддержки',
            'footer_text': 'Если есть вопросы — создай новое обращение на сайте.',
            'ticket_label': 'Тикет',
        },
        'ua': {
            'greeting': 'Привіт,', 'replied': 'Адміністратор відповів на твоє звернення:',
            'topic': 'Тема', 'response': 'Відповідь підтримки',
            'footer_text': 'Якщо є питання — створи нове звернення на сайті.',
            'ticket_label': 'Тікет',
        },
        'en': {
            'greeting': 'Hello,', 'replied': 'An administrator has replied to your request:',
            'topic': 'Subject', 'response': 'Support Reply',
            'footer_text': 'If you have more questions, please create a new request on the site.',
            'ticket_label': 'Ticket',
        },
    }
    t = _i18n.get(lang, _i18n['ru'])
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#07071a;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:520px;margin:32px auto;background:#0e0e22;border-radius:14px;border:1px solid rgba(255,255,255,.07);overflow:hidden">
  <div style="padding:22px 28px 16px;border-bottom:1px solid rgba(255,255,255,.06)">
    <span style="font-size:18px;font-weight:700;color:#e6e6f5">⚡ VELOX</span>
    <span style="font-size:12px;color:#44445a;margin-left:8px">Support</span>
  </div>
  <div style="padding:24px 28px">
    <p style="margin:0 0 6px;font-size:13px;color:#8888aa">{t['greeting']} <strong style="color:#e6e6f5">{username}</strong>!</p>
    <p style="margin:0 0 20px;font-size:13px;color:#8888aa">{t['replied']}</p>
    <div style="padding:10px 14px;background:#131328;border-radius:8px;border:1px solid rgba(255,255,255,.06);margin-bottom:14px">
      <div style="font-size:10px;color:#44445a;text-transform:uppercase;letter-spacing:1px">{t['topic']}</div>
      <div style="font-size:13px;font-weight:600;color:#e6e6f5;margin-top:4px">{subject}</div>
    </div>
    <div style="padding:14px;background:#131328;border-radius:8px;border-left:3px solid #00e5ff;margin-bottom:22px">
      <div style="font-size:10px;color:#00e5ff;text-transform:uppercase;letter-spacing:1px;font-weight:700">{t['response']}</div>
      <div style="font-size:14px;color:#e6e6f5;margin-top:8px;line-height:1.6;white-space:pre-wrap">{admin_message}</div>
    </div>
    <p style="margin:0;font-size:12px;color:#44445a;line-height:1.6">{t['footer_text']}</p>
  </div>
  <div style="padding:14px 28px;border-top:1px solid rgba(255,255,255,.06);text-align:center;font-size:11px;color:#44445a">
    {from_name} &middot; {t['ticket_label']} #{ticket_id}
  </div>
</div></body></html>"""
    return _send_email(to_email, f'[VELOX] Ответ на обращение #{ticket_id}: {subject}', html)
