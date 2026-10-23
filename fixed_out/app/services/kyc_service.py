"""
app/services/kyc_service.py — KYC verification service.

Supports 4 modes (set in config/kyc_system.json → "mode"):

  workflow — Video liveness: user records video holding passport, says random word.
             Ticket created → operator reviews manually.

  scan     — Automated via Onfido / Sumsub / Jumio. Returns instant result.

  both     — Tries scan provider first; falls back to workflow if not configured.

  manual   — Classic document upload only. Operator reviews file.

Liveness word:
  - Generated per-user, stored in DB (kyc_word / kyc_word_expires_at)
  - Valid for word_ttl_minutes (default 120)
  - New word generated on expiry or on-demand
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import random
import secrets
import time
from datetime import datetime, timedelta
from typing import Optional

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'kyc_system.json')


# ── Age helper ───────────────────────────────────────────────────────────────

def _calc_age(dob_str: str):
    """Return age in years from YYYY-MM-DD string, or None if invalid."""
    from datetime import date
    try:
        dob = date.fromisoformat(dob_str)
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


# ── Word bank ──────────────────────────────────────────────────────────────────
# Simple common words easy to say clearly on camera
_WORD_BANK = [
    'CASINO', 'VELOX', 'ORANGE', 'TABLE', 'VERIFY', 'SECURE',
    'BRIDGE', 'PLANET', 'FOREST', 'CAMERA', 'SILVER', 'MONKEY',
    'ROCKET', 'GARDEN', 'MIRROR', 'BUCKET', 'CANDLE', 'PILLOW',
    'DRAGON', 'WINDOW', 'LAPTOP', 'COFFEE', 'BOTTLE', 'GUITAR',
    'MARKET', 'PUZZLE', 'CASTLE', 'WINTER', 'SUMMER', 'PARROT',
]


# ── Config ────────────────────────────────────────────────────────────────────

def load_cfg() -> dict:
    try:
        with open(_CFG_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_mode() -> str:
    return load_cfg().get('mode', 'workflow')


def get_active_provider() -> Optional[str]:
    """Returns name of first enabled scan provider, or None."""
    cfg = load_cfg()
    for name in ('sumsub', 'onfido', 'jumio'):
        p = cfg.get('providers', {}).get(name, {})
        if p.get('enabled'):
            return name
    return None


# ── Liveness word ─────────────────────────────────────────────────────────────

def generate_word() -> str:
    return random.choice(_WORD_BANK)


def get_or_refresh_word(user_id: int) -> dict:
    """
    Returns current word for user. Generates new one if expired or not set.
    Returns: {'word': str, 'expires_at': ISO str, 'remaining_seconds': int}
    """
    from app.db import get_db
    cfg = load_cfg()
    ttl_min = int(cfg.get('word_ttl_minutes', 120))

    with get_db() as db:
        db.execute('SELECT kyc_word, kyc_word_expires_at FROM users WHERE id = %s', (user_id,))
        row = db.fetchone()

    now = datetime.utcnow()
    word = None
    expires_at = None

    if row:
        word       = row.get('kyc_word')
        expires_str = row.get('kyc_word_expires_at')
        if expires_str and word:
            try:
                exp = datetime.fromisoformat(str(expires_str).replace('Z', ''))
                if exp > now:
                    expires_at = exp
                else:
                    word = None  # expired
            except Exception:
                word = None

    if not word:
        word       = generate_word()
        expires_at = now + timedelta(minutes=ttl_min)
        with get_db() as db:
            db.execute(
                'UPDATE users SET kyc_word = %s, kyc_word_expires_at = %s WHERE id = %s',
                (word, expires_at.isoformat(), user_id)
            )

    remaining = max(0, int((expires_at - now).total_seconds())) if expires_at else 0
    return {
        'word':             word,
        'expires_at':       expires_at.isoformat() if expires_at else '',
        'remaining_seconds': remaining,
        'ttl_minutes':      ttl_min,
    }


# ── Ticket creation (workflow mode) ───────────────────────────────────────────

def create_kyc_ticket(user, video_path: str, word: str, dob: str) -> int:
    """Creates a support ticket tagged as KYC for operator review."""
    import secrets as _sec, string as _str
    from app.db import get_db
    cfg = load_cfg()

    # Generate unique 6-char UUID (same format as support.py)
    _chars = _str.ascii_letters + _str.digits
    ticket_uuid = ''.join(_sec.choice(_chars) for _ in range(6))

    subject = f'[KYC] Верификация — {user.username} (ID {user.id})'
    desc = (
        f'Пользователь: {user.username} (ID {user.id})\n'
        f'Email: {user.email or "—"}\n'
        f'Дата рождения: {dob}\n'
        f'Контрольное слово: {word}\n\n'
        f'Инструкция: Проверьте что пользователь:\n'
        f'  1. Держит паспорт/удостоверение рядом с лицом\n'
        f'  2. Чётко произносит слово «{word}»\n'
        f'  3. Лицо и документ хорошо видны'
    )

    ticket_id = 0
    with get_db() as db:
        db.execute(
            'INSERT INTO support_tickets (user_id, contact, subject, description, status, ticket_uuid, created_at)'
            ' VALUES (%s, %s, %s, %s, %s, %s, NOW())',
            (user.id, user.email or user.phone or str(user.id), subject, desc, 'open', ticket_uuid)
        )
        row = db.fetchone()
        if row:
            ticket_id = row.get('id', 0)

    # Email notification
    from app.services.email_service import get_kyc_notify_email
    _notify_operator(user, word, video_path, get_kyc_notify_email())
    return ticket_id


def _notify_operator(user, word: str, video_path: str, email: str):
    if not email:
        return
    try:
        from app.services.email_service import _send_async, _html_wrapper
        body = f"""
<p style="color:#aaa;margin:0 0 16px">Новая заявка KYC видео-верификации.</p>
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d0d;border-radius:10px;border:1px solid #2a2a4a;margin-bottom:20px">
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Пользователь</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;color:#e0e0e0">{user.username} (ID {user.id})</td></tr>
  <tr><td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;color:#888;font-size:12px">Контрольное слово</td>
      <td style="padding:10px 16px;border-bottom:1px solid #2a2a4a;text-align:right;color:#a78bfa;font-size:18px;font-weight:700;font-family:monospace">{word}</td></tr>
  <tr><td style="padding:10px 16px;color:#888;font-size:12px">Видео</td>
      <td style="padding:10px 16px;text-align:right;color:#6c9fff;font-size:11px;font-family:monospace">{video_path}</td></tr>
</table>
<p style="color:#666;font-size:12px">Откройте тикет поддержки для просмотра видео и принятия решения.</p>"""
        html = _html_wrapper(f'KYC: {user.username} (ID {user.id})', body)
        _send_async(email, f'KYC верификация — {user.username}', html)
    except Exception as e:
        print(f'[kyc_service] notify error: {e}')


# ── Scan providers ─────────────────────────────────────────────────────────────

def create_scan_session(user, provider: str) -> dict:
    """
    Create a verification session with external provider.
    Returns: {'url': str, 'session_id': str, 'provider': str} or {'error': str}
    """
    cfg = load_cfg()
    pcfg = cfg.get('providers', {}).get(provider, {})

    if provider == 'sumsub':
        return _sumsub_create_session(user, pcfg)
    elif provider == 'onfido':
        return _onfido_create_session(user, pcfg)
    elif provider == 'jumio':
        return _jumio_create_session(user, pcfg)
    return {'error': f'Unknown provider: {provider}'}


def _sumsub_create_session(user, pcfg: dict) -> dict:
    """Create Sumsub applicant and generate SDK token."""
    app_token  = pcfg.get('app_token', '')
    secret_key = pcfg.get('secret_key', '')
    level      = pcfg.get('level_name', 'basic-kyc-level')

    if not app_token or not secret_key:
        return {'error': 'Sumsub: app_token and secret_key not configured'}

    try:
        import requests
        base = 'https://api.sumsub.com'

        def _sign(method: str, url: str, body: bytes = b'') -> dict:
            ts  = str(int(time.time()))
            msg = (ts + method.upper() + url + body.decode('utf-8', errors='')).encode()
            sig = hmac.new(secret_key.encode(), msg, hashlib.sha256).hexdigest()
            return {
                'X-App-Token':   app_token,
                'X-App-Access-Ts': ts,
                'X-App-Access-Sig': sig,
                'Content-Type': 'application/json',
            }

        # Create applicant
        ep   = f'/resources/applicants?levelName={level}'
        body = json.dumps({'externalUserId': f'velox_{user.id}',
                           'email': user.email or '', 'lang': 'ru'}).encode()
        hdrs = _sign('POST', ep, body)
        r = requests.post(base + ep, headers=hdrs, data=body, timeout=10)
        r.raise_for_status()
        applicant_id = r.json().get('id', '')

        # Generate SDK token
        ep2  = f'/resources/accessTokens?userId=velox_{user.id}&levelName={level}'
        hdrs2 = _sign('POST', ep2)
        r2 = requests.post(base + ep2, headers=hdrs2, timeout=10)
        r2.raise_for_status()
        sdk_token = r2.json().get('token', '')

        return {
            'provider':    'sumsub',
            'session_id':  applicant_id,
            'sdk_token':   sdk_token,
            'url':         None,  # Sumsub uses JS SDK, not redirect URL
        }
    except Exception as e:
        return {'error': f'Sumsub error: {e}'}


def _onfido_create_session(user, pcfg: dict) -> dict:
    """Create Onfido applicant and SDK token."""
    api_token = pcfg.get('api_token', '')
    region    = pcfg.get('region', 'eu')
    workflow_id = pcfg.get('workflow_run_id', '')

    if not api_token:
        return {'error': 'Onfido: api_token not configured'}

    base_map = {'eu': 'https://api.eu.onfido.com/v3.6',
                'us': 'https://api.us.onfido.com/v3.6',
                'ca': 'https://api.ca.onfido.com/v3.6'}
    base = base_map.get(region, base_map['eu'])
    hdrs = {'Authorization': f'Token token={api_token}',
            'Content-Type': 'application/json'}

    try:
        import requests
        # Create applicant
        r = requests.post(f'{base}/applicants', headers=hdrs,
                          json={'first_name': user.username, 'last_name': ''},
                          timeout=10)
        r.raise_for_status()
        applicant_id = r.json().get('id', '')

        # Generate SDK token
        r2 = requests.post(f'{base}/sdk_token', headers=hdrs,
                           json={'applicant_id': applicant_id,
                                 'referrer': '*://*/*'},
                           timeout=10)
        r2.raise_for_status()
        sdk_token = r2.json().get('token', '')

        return {
            'provider':    'onfido',
            'session_id':  applicant_id,
            'sdk_token':   sdk_token,
            'workflow_id': workflow_id,
            'url':         None,
        }
    except Exception as e:
        return {'error': f'Onfido error: {e}'}


def _jumio_create_session(user, pcfg: dict) -> dict:
    """Create Jumio transaction and return web URL."""
    api_token  = pcfg.get('api_token', '')
    api_secret = pcfg.get('api_secret', '')
    dc         = pcfg.get('datacenter', 'EU').upper()

    if not api_token or not api_secret:
        return {'error': 'Jumio: api_token and api_secret not configured'}

    base_map = {'EU': 'https://netverify.com', 'US': 'https://netverify.com',
                'SGP': 'https://lon.netverify.com'}
    base = base_map.get(dc, base_map['EU'])

    try:
        import requests
        from requests.auth import HTTPBasicAuth
        r = requests.post(
            f'{base}/api/v4/initiate',
            auth=HTTPBasicAuth(api_token, api_secret),
            json={
                'customerInternalReference': f'velox_{user.id}_{int(time.time())}',
                'userReference': f'velox_{user.id}',
                'successUrl': 'https://yourdomain.com/api/kyc/jumio/callback?status=success',
                'errorUrl':   'https://yourdomain.com/api/kyc/jumio/callback?status=error',
            },
            headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        return {
            'provider':   'jumio',
            'session_id': data.get('transactionReference', ''),
            'url':        data.get('redirectUrl', ''),
            'sdk_token':  None,
        }
    except Exception as e:
        return {'error': f'Jumio error: {e}'}


# ── Card holder verification ───────────────────────────────────────────────────

def check_card_deposit(user, amount: float, card_holder_name: str = '') -> dict:
    """
    Check if a deposit should require KYC or name match.
    Returns: {'allowed': bool, 'reason': str}
    """
    cfg     = load_cfg()
    card_cfg = cfg.get('card_verification', {})

    if not card_cfg.get('enabled', False):
        return {'allowed': True, 'reason': ''}

    # Require verified for large deposits
    min_req = float(card_cfg.get('min_deposit_require_kyc', 10000))
    if card_cfg.get('require_verified_for_deposit') or amount >= min_req:
        if user.kyc_status != 'verified':
            return {
                'allowed': False,
                'reason':  'kyc_required',
                'message': f'Для депозита свыше {min_req} требуется верификация личности (KYC).',
            }

    # Name match check (only if scan provider verified the name)
    if card_cfg.get('match_card_holder_name') and card_holder_name:
        kyc_name = getattr(user, 'kyc_full_name', '') or ''
        if kyc_name and card_holder_name.upper().strip() != kyc_name.upper().strip():
            return {
                'allowed': False,
                'reason':  'name_mismatch',
                'message': 'Имя держателя карты не совпадает с верифицированным документом.',
            }

    return {'allowed': True, 'reason': ''}
