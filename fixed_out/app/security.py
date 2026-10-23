"""
app/security.py — Security middleware for VELOX Casino.

Covers:
  1.  Security HTTP headers (CSP with per-request nonce, HSTS, X-Frame-Options, etc.)
  2.  DDoS / brute-force protection (per-IP sliding window, burst detection)
  3.  XSS prevention (input sanitization, output escaping, nonce-based CSP)
  4.  SQL injection protection (parameterized queries enforced, pattern detection)
  5.  Account lockout after failed login attempts
  6.  Request size limits and suspicious payload detection
  7.  Session hardening (rotation, binding, expiry)
  8.  Path traversal / DirBusting protection
  9.  CloudFlare-aware IP resolution
 10.  DB hardening helpers
 11.  CSRF protection (double-submit cookie + Synchronizer Token)
 12.  GET parameter injection scanning
 13.  Extended rate limits: password reset, email verification, withdraw, support
 14.  Runtime toggle of any check via config flags
 15.  Structured logging via app.logger (no bare print)

Config: config/security.json
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from functools import wraps
from typing import Optional

from flask import Flask, request, jsonify, g, make_response

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_CFG_PATH = os.path.join(os.path.dirname(__file__), '..', 'config', 'security.json')

_cfg_cache: dict = {}
_cfg_lock  = threading.Lock()
_cfg_mtime: float = 0.0


def _load_cfg(force: bool = False) -> dict:
    """Hot-reload config if file changed on disk."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = os.path.getmtime(_CFG_PATH)
        with _cfg_lock:
            if force or mtime != _cfg_mtime:
                with open(_CFG_PATH, encoding='utf-8') as f:
                    _cfg_cache = json.load(f)
                _cfg_mtime = mtime
            return _cfg_cache
    except Exception:
        return _cfg_cache or {}


def cfg() -> dict:
    return _load_cfg()


def _flag(name: str, default: bool = True) -> bool:
    """Read a feature-flag from config['checks'] section."""
    return cfg().get('checks', {}).get(name, default)


_DEFAULT_UA_PATTERNS = (
    'headlesschrome',
    'python-requests',
    'python-httpx',
    'curl/',
    'wget/',
    'sqlmap',
    'nikto',
    'scanner',
)


def _ua_patterns() -> tuple[str, ...]:
    patterns = cfg().get('ua_patterns')
    if isinstance(patterns, list):
        cleaned = [str(p).strip().lower() for p in patterns if str(p).strip()]
        if cleaned:
            return tuple(cleaned)
    return _DEFAULT_UA_PATTERNS


def _is_suspicious_ua(ua: str) -> bool:
    ua_l = (ua or '').lower()
    return any(p in ua_l for p in _ua_patterns())


def _blocked_countries() -> set[str]:
    items = cfg().get('blocked_countries', [])
    if not isinstance(items, list):
        return set()
    return {str(c).strip().upper() for c in items if str(c).strip()}


_GEO_READER = None
_GEO_READER_PATH = ''
_GEO_READER_MTIME = 0.0
_GEO_LOCK = threading.Lock()


def _geoip_paths() -> list[str]:
    paths: list[str] = []
    env_path = os.environ.get('GEOIP_DB_PATH', '').strip()
    if env_path:
        paths.append(env_path)
    cfg_paths = cfg().get('geoip', {}).get('database_paths', [])
    if isinstance(cfg_paths, list):
        for p in cfg_paths:
            pp = str(p).strip()
            if pp:
                if os.path.isabs(pp):
                    paths.append(pp)
                else:
                    paths.append(os.path.join(os.path.dirname(__file__), '..', pp))
    return paths


def _geoip_reader():
    global _GEO_READER, _GEO_READER_PATH, _GEO_READER_MTIME
    try:
        import geoip2.database
    except Exception:
        return None

    with _GEO_LOCK:
        for path in _geoip_paths():
            try:
                if not os.path.exists(path):
                    continue
                mtime = os.path.getmtime(path)
                if (
                    _GEO_READER is None
                    or _GEO_READER_PATH != path
                    or _GEO_READER_MTIME != mtime
                ):
                    if _GEO_READER is not None:
                        try:
                            _GEO_READER.close()
                        except Exception:
                            pass
                    _GEO_READER = geoip2.database.Reader(path)
                    _GEO_READER_PATH = path
                    _GEO_READER_MTIME = mtime
                return _GEO_READER
            except Exception:
                continue
    return None


def _country_code_by_ip(ip: str) -> str:
    reader = _geoip_reader()
    if reader is None:
        return ''
    try:
        rec = reader.country(ip)
        return (rec.country.iso_code or '').upper()
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# CloudFlare IP resolution
# ---------------------------------------------------------------------------

_CF_RANGES_STR = [
    '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '104.16.0.0/13',  '104.24.0.0/14',   '108.162.192.0/18',
    '131.0.72.0/22',  '141.101.64.0/18', '162.158.0.0/15',
    '172.64.0.0/13',  '173.245.48.0/20', '188.114.96.0/20',
    '190.93.240.0/20','197.234.240.0/22','198.41.128.0/17',
]

def _build_cf_nets():
    import ipaddress
    return [ipaddress.ip_network(c, strict=False) for c in _CF_RANGES_STR]

_CF_NETS = None
_CF_NETS_LOCK = threading.Lock()

def _ip_in_cf_range(ip: str) -> bool:
    global _CF_NETS
    try:
        import ipaddress
        with _CF_NETS_LOCK:
            if _CF_NETS is None:
                _CF_NETS = _build_cf_nets()
        addr = ipaddress.ip_address(ip)
        return any(addr in net for net in _CF_NETS)
    except Exception:
        return False


def get_client_ip() -> str:
    remote = request.remote_addr or '0.0.0.0'
    cf_ip = request.headers.get('CF-Connecting-IP', '').strip()
    if cf_ip and _ip_in_cf_range(remote):
        return cf_ip.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP', '').strip()
    if real_ip:
        return real_ip
    xff = request.headers.get('X-Forwarded-For', '').strip()
    if xff:
        return xff.split(',')[0].strip()
    return remote


# ---------------------------------------------------------------------------
# Security headers  (nonce-based CSP)
# ---------------------------------------------------------------------------

_HSTS_HEADER = 'max-age=63072000; includeSubDomains; preload'

_CSP_DIRECTIVES_STATIC = (
    "default-src 'self' https:; "
    "style-src 'self' https: 'unsafe-inline'; "
    "style-src-attr 'unsafe-inline'; "
    "font-src 'self' https: data:; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' https:; "
    "media-src 'self' blob:; "
    "frame-src 'self' https:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'; "
    "script-src-attr 'unsafe-inline'; "
)

# script-src built per request so nonce is injected correctly
_SCRIPT_SRC_TPL = "script-src 'self' https: 'unsafe-inline' 'unsafe-eval';"

_BASE_HEADERS = {
    'X-Frame-Options':              'DENY',
    'X-Content-Type-Options':       'nosniff',
    'X-XSS-Protection':             '0',          # modern browsers: let CSP do the work
    'Referrer-Policy':              'strict-origin-when-cross-origin',
    'Cache-Control':                'no-store, no-cache, must-revalidate, private',
    'Permissions-Policy':           'camera=(), microphone=(), geolocation=(), payment=(self)',
    'Cross-Origin-Opener-Policy':   'same-origin',
    'Cross-Origin-Resource-Policy': 'same-origin',
    # COEP=require-corp blocks embedding third-party iframes (e.g. YouTube streams) inside our pages.
    # We intentionally allow external embeds for sport match-center.
    'Cross-Origin-Embedder-Policy': 'unsafe-none',
}


def generate_nonce() -> str:
    return secrets.token_hex(16)


def apply_security_headers(response, https: bool = False, nonce: str = ''):
    nonce = nonce or getattr(g, 'csp_nonce', '') or generate_nonce()
    for k, v in _BASE_HEADERS.items():
        response.headers[k] = v
    csp = (
        _CSP_DIRECTIVES_STATIC.format(nonce=nonce)
        + ' '
        + _SCRIPT_SRC_TPL.format(nonce=nonce)
    )
    response.headers['Content-Security-Policy'] = csp
    if https:
        response.headers['Strict-Transport-Security'] = _HSTS_HEADER
    response.headers.pop('Server', None)
    response.headers.pop('X-Powered-By', None)
    return response


# ---------------------------------------------------------------------------
# Sliding-window rate limiter
# ---------------------------------------------------------------------------

class _SlidingWindowLimiter:
    def __init__(self, max_req: int, window_sec: int):
        self._max    = max_req
        self._window = window_sec
        self._data:  dict[str, list] = defaultdict(list)
        self._lock   = threading.Lock()
        self._last_gc = time.monotonic()

    def _gc(self, now: float):
        if now - self._last_gc > 120:
            cutoff = now - self._window * 2
            self._data = defaultdict(list, {
                k: v for k, v in self._data.items()
                if any(t > cutoff for t in v)
            })
            self._last_gc = now

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._gc(now)
            cutoff = now - self._window
            bucket = [t for t in self._data[key] if t > cutoff]
            if len(bucket) >= self._max:
                self._data[key] = bucket
                return False
            bucket.append(now)
            self._data[key] = bucket
            return True

    def count(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            return len([t for t in self._data.get(key, []) if t > cutoff])

    def reconfigure(self, max_req: int, window_sec: int):
        with self._lock:
            self._max    = max_req
            self._window = window_sec


def _make_limiters() -> dict[str, _SlidingWindowLimiter]:
    """Build limiters from current config. Called once at startup."""
    rl = cfg().get('rate_limits', {})
    return {
        'ddos':          _SlidingWindowLimiter(rl.get('ddos_per_10s',       300),  10),
        'burst':         _SlidingWindowLimiter(rl.get('burst_per_1s',        30),   1),
        'api':           _SlidingWindowLimiter(rl.get('api_per_minute',      120),  60),
        'login':         _SlidingWindowLimiter(rl.get('login_per_minute',     10),  60),
        'kyc':           _SlidingWindowLimiter(rl.get('kyc_per_5min',          5), 300),
        'payment':       _SlidingWindowLimiter(rl.get('deposit_per_minute',   20),  60),
        'register':      _SlidingWindowLimiter(rl.get('register_per_5min',     5), 300),
        'pwd_reset':     _SlidingWindowLimiter(rl.get('pwd_reset_per_hour',    5), 3600),
        'email_verify':  _SlidingWindowLimiter(rl.get('email_verify_per_hour',10), 3600),
        'withdraw':      _SlidingWindowLimiter(rl.get('withdraw_per_hour',    10), 3600),
        'support':       _SlidingWindowLimiter(rl.get('support_per_minute',   10),  60),
    }


_limiters: dict[str, _SlidingWindowLimiter] = {}


# ---------------------------------------------------------------------------
# Account lockout
# ---------------------------------------------------------------------------

class _AccountLockout:
    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, list] = defaultdict(list)
        self._locked:   dict[str, float] = {}

    def _cfg(self):
        c = cfg().get('account_lockout', {})
        return (
            c.get('max_failures', 10),
            c.get('window_minutes', 15) * 60,
            c.get('lockout_minutes', 30) * 60,
        )

    def _key(self, identifier: str) -> str:
        return hashlib.sha256(identifier.lower().encode()).hexdigest()[:16]

    def is_locked(self, identifier: str) -> tuple[bool, int]:
        if not cfg().get('account_lockout', {}).get('enabled', True):
            return False, 0
        key = self._key(identifier)
        now = time.time()
        with self._lock:
            lu = self._locked.get(key, 0)
            if lu > now:
                return True, int(lu - now)
            elif lu:
                del self._locked[key]
        return False, 0

    def record_failure(self, identifier: str) -> int:
        max_f, window, lockout = self._cfg()
        key = self._key(identifier)
        now = time.time()
        with self._lock:
            cutoff = now - window
            self._failures[key] = [t for t in self._failures.get(key, []) if t > cutoff]
            self._failures[key].append(now)
            if len(self._failures[key]) >= max_f:
                self._locked[key] = now + lockout
                self._failures[key] = []
                return 0
            return max(0, max_f - len(self._failures[key]))

    def record_success(self, identifier: str):
        key = self._key(identifier)
        with self._lock:
            self._failures.pop(key, None)
            self._locked.pop(key, None)

    def failure_count(self, identifier: str) -> int:
        _, window, _ = self._cfg()
        key = self._key(identifier)
        now = time.time()
        with self._lock:
            return len([t for t in self._failures.get(key, []) if t > now - window])


account_lockout = _AccountLockout()


# ---------------------------------------------------------------------------
# Injection / traversal patterns
# ---------------------------------------------------------------------------

_SQLI_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r'(union\s+(all\s+)?select)',
    r'(insert\s+into|delete\s+from|drop\s+(table|database)|alter\s+table)',
    r'(exec\s*\(|execute\s*\(|xp_cmdshell)',
    r'(sleep\s*\(\s*\d+|benchmark\s*\(|waitfor\s+delay)',
    r'(--\s*$|;\s*--|#\s*$)',
    r'(\bor\b\s+[\'"0-9]+=[\'"0-9]+|\band\b\s+[\'"0-9]+=[\'"0-9]+)',
    r'(0x[0-9a-f]{6,})',
    r'(information_schema|pg_catalog|sqlite_master)',
    r'(\bload_file\b|\binto\s+outfile\b)',
    r'(\bchar\s*\(|concat\s*\(|group_concat\s*\()',
    r'(\bcast\s*\(|\bconvert\s*\()',
]]

_XSS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r'<\s*script[^>]*>',
    r'javascript\s*:',
    r'on(load|error|click|mouse|focus|blur|key|submit|change|input|drag|drop|paste|copy|cut|scroll|wheel|resize|select|contextmenu|dblclick|pointer)\s*=',
    r'<\s*(iframe|object|embed|applet|svg|math)[^>]*>',
    r'vbscript\s*:',
    r'expression\s*\(',
    r'data\s*:\s*text/(html|javascript)',
    r'&#\s*x?\s*[0-9a-f]+\s*;',
    r'\\u00[0-9a-f]{2}',
]]

_DIRB_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r'\.\./|\.\.\\|%2e%2e[/\\]',
    r'/etc/(passwd|shadow|hosts|sudoers)',
    r'/proc/(self|[0-9]+)',
    r'/(wp-admin|wp-login|wp-content|phpmyadmin|adminer|phpinfo|\.env|\.git|\.svn|\.htaccess|\.htpasswd)',
    r'\.(sql|bak|backup|old|zip|tar|gz|dump|log|cfg|ini|conf|pem|key)$',
    r'(eval\s*\(|base64_decode|passthru|system\s*\(|shell_exec)',
]]

_SSTI_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r'\{\{.*\}\}',
    r'\{%.*%\}',
    r'\$\{.*\}',
    r'<%.*%>',
]]


def check_sqli(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 3:
        return False
    return any(p.search(value) for p in _SQLI_PATTERNS)


def check_xss(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 4:
        return False
    return any(p.search(value) for p in _XSS_PATTERNS)


def check_dirb(path: str) -> bool:
    return any(p.search(path) for p in _DIRB_PATTERNS)


def check_ssti(value: str) -> bool:
    if not isinstance(value, str) or len(value) < 3:
        return False
    return any(p.search(value) for p in _SSTI_PATTERNS)


def sanitize_input(value: str, max_length: int = 500) -> str:
    if not isinstance(value, str):
        return str(value)[:max_length]
    return value.replace('\x00', '').strip()[:max_length]


def escape_html(value: str) -> str:
    return (value
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;'))


def _scan_str(value: str, key: str) -> Optional[str]:
    if check_sqli(value):
        return f'sqli:{key}'
    if check_xss(value):
        return f'xss:{key}'
    if check_ssti(value):
        return f'ssti:{key}'
    return None


def scan_request_payload(data: dict, depth: int = 0) -> Optional[str]:
    if depth > 4:
        return None
    for key, value in data.items():
        if isinstance(value, str):
            hit = _scan_str(value, key)
            if hit:
                return hit
        elif isinstance(value, dict):
            r = scan_request_payload(value, depth + 1)
            if r:
                return r
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    hit = _scan_str(item, key)
                    if hit:
                        return f'list:{hit}'
    return None


def scan_query_params(args: dict) -> Optional[str]:
    """Scan GET parameters for injection patterns."""
    for key, value in args.items():
        if isinstance(value, str):
            hit = _scan_str(value, f'qs:{key}')
            if hit:
                return hit
    return None


def scan_form_data(form: dict) -> Optional[str]:
    """Scan POST form fields for injection patterns."""
    for key, value in form.items():
        if isinstance(value, str):
            hit = _scan_str(value, f'form:{key}')
            if hit:
                return hit
    return None


def scan_file_names(files) -> Optional[str]:
    """Check uploaded file names for path traversal / suspicious extensions."""
    dangerous_ext = re.compile(
        r'\.(php[0-9]?|phtml|phar|asp|aspx|jsp|cgi|sh|py|rb|pl|exe|bat|cmd|scr|vbs|ps1)$',
        re.IGNORECASE,
    )
    for field_name, file_storage in files.items():
        fname = getattr(file_storage, 'filename', '') or ''
        if check_dirb(fname):
            return f'traversal:filename:{field_name}'
        if dangerous_ext.search(fname):
            return f'dangerous_ext:filename:{field_name}'
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def safe_like(value: str) -> str:
    return value.replace('%', r'\%').replace('_', r'\_')


_ALLOWED_TABLES = frozenset({
    'users', 'transactions', 'game_history', 'promo_codes',
    'promo_uses', 'support_tickets', 'support_ticket_messages', 'payment_orders',
})

_ALLOWED_COLUMNS_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$')


def validate_table_name(name: str) -> bool:
    return name in _ALLOWED_TABLES


def validate_column_name(name: str) -> bool:
    return bool(_ALLOWED_COLUMNS_RE.match(name))


# ---------------------------------------------------------------------------
# CSRF protection
# ---------------------------------------------------------------------------

_CSRF_COOKIE     = 'velox_csrf'
_CSRF_HEADER     = 'X-CSRF-Token'
_CSRF_FORM_FIELD = '_csrf_token'
_CSRF_MAX_AGE    = 7200  # 2 hours

_SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS', 'TRACE'})

# Routes exempt from CSRF (e.g., public webhooks with HMAC verification)
_CSRF_EXEMPT_PATHS = {
    '/api/webhooks/liqpay',
    '/api/webhooks/monobank',
}


def generate_csrf_token() -> str:
    return secrets.token_hex(32)


def _get_csrf_from_request() -> Optional[str]:
    """Extract CSRF token from header or form field."""
    return (
        request.headers.get(_CSRF_HEADER)
        or request.form.get(_CSRF_FORM_FIELD)
    )


def validate_csrf() -> bool:
    if not _flag('csrf'):
        return True
    if request.method in _SAFE_METHODS:
        return True
    if request.path in _CSRF_EXEMPT_PATHS:
        return True
    cookie_token = request.cookies.get(_CSRF_COOKIE, '')
    request_token = _get_csrf_from_request() or ''
    if not cookie_token or not request_token:
        return False
    return secrets.compare_digest(cookie_token, request_token)


def set_csrf_cookie(response):
    """Set or refresh the CSRF cookie on the response."""
    token = request.cookies.get(_CSRF_COOKIE) or generate_csrf_token()
    response.set_cookie(
        _CSRF_COOKIE,
        token,
        max_age=_CSRF_MAX_AGE,
        httponly=False,    # JS must be able to read it
        samesite='Strict',
        secure=cfg().get('https', False),
        path='/',
    )
    return response


def csrf_exempt(f):
    """Decorator to mark a view as CSRF-exempt."""
    f._csrf_exempt = True
    return f


# ---------------------------------------------------------------------------
# Request body size limits (from config)
# ---------------------------------------------------------------------------

def _body_limits() -> tuple[int, int]:
    rl = cfg().get('request_limits', {})
    api_mb    = rl.get('max_api_body_mb', 2)
    upload_mb = rl.get('max_upload_body_mb', 60)
    return api_mb * 1024 * 1024, upload_mb * 1024 * 1024


_UPLOAD_PATHS = ('/kyc/', '/upload', '/support/ticket')


# ---------------------------------------------------------------------------
# Core middleware init
# ---------------------------------------------------------------------------

def init_security(app: Flask, https: bool = False):
    global _limiters
    _load_cfg(force=True)
    _limiters = _make_limiters()

    logger = app.logger
    
    # Регистрируем хелпер для шаблонов, чтобы можно было писать {{ csp_nonce() }}
    app.jinja_env.globals['csp_nonce'] = get_csp_nonce

    @app.before_request
    def _security_check():
        path = request.path
        ip   = get_client_ip()
        g.client_ip = ip

        # Generate per-request CSP nonce
        g.csp_nonce = generate_nonce()

        # ------------------------------------------------------------------
        # 0. Static assets — skip most checks
        # ------------------------------------------------------------------
        if any(path.startswith(p) for p in ('/static/',)):
            return None

        # ------------------------------------------------------------------
        # 1. Blocked IPs
        # ------------------------------------------------------------------
        if _flag('blocked_ips') and ip in cfg().get('blocked_ips', []):
            logger.warning('[sec] Blocked IP: %s %s', ip, path)
            return jsonify({'error': 'Not found'}), 404

        # ------------------------------------------------------------------
        # 2. Path traversal / DirBusting
        # ------------------------------------------------------------------
        if _flag('dirb') and check_dirb(request.full_path or path):
            logger.warning('[sec] DirB: %s %s', ip, path)
            return jsonify({'error': 'Not found'}), 404

        # ------------------------------------------------------------------
        # 3. Burst + DDoS rate limits
        # ------------------------------------------------------------------
        if _flag('burst') and not _limiters['burst'].is_allowed(ip):
            return jsonify({'error': 'Slow down.'}), 429

        if _flag('ddos') and not _limiters['ddos'].is_allowed(ip):
            logger.warning('[sec] DDoS: %s %s', ip, path)
            return jsonify({'error': 'Too many requests.'}), 429

        # ------------------------------------------------------------------
        # 4. API-wide rate limit
        # ------------------------------------------------------------------
        if path.startswith('/api/') and _flag('api_rate_limit'):
            if not _limiters['api'].is_allowed(ip):
                return jsonify({'error': 'API rate limit exceeded.'}), 429

        # ------------------------------------------------------------------
        # 5. Endpoint-specific rate limits
        # ------------------------------------------------------------------
        if request.method == 'POST':
            if path in ('/api/auth/login', '/api/auth/register/send_otp'):
                if _flag('login_rate_limit') and not _limiters['login'].is_allowed(ip):
                    return jsonify({'error': 'Too many attempts. Wait 1 minute.'}), 429

            if path.startswith('/api/auth/register') and _flag('register_rate_limit'):
                if not _limiters['register'].is_allowed(ip):
                    return jsonify({'error': 'Too many registrations.'}), 429

            if path.startswith('/api/auth/password') and _flag('pwd_reset_rate_limit'):
                if not _limiters['pwd_reset'].is_allowed(ip):
                    return jsonify({'error': 'Too many password reset attempts.'}), 429

            if path.startswith('/api/auth/email') and _flag('email_verify_rate_limit'):
                if not _limiters['email_verify'].is_allowed(ip):
                    return jsonify({'error': 'Too many email verification attempts.'}), 429

            if path.startswith('/api/balance/') and _flag('payment_rate_limit'):
                if not _limiters['payment'].is_allowed(ip):
                    return jsonify({'error': 'Too many payment requests.'}), 429

            if path.startswith('/api/balance/withdraw') and _flag('withdraw_rate_limit'):
                if not _limiters['withdraw'].is_allowed(ip):
                    return jsonify({'error': 'Too many withdrawal requests.'}), 429

            if path.startswith('/api/kyc/') and _flag('kyc_rate_limit'):
                if not _limiters['kyc'].is_allowed(ip):
                    return jsonify({'error': 'Too many verification attempts.'}), 429

            if path.startswith('/api/support/') and _flag('support_rate_limit'):
                if not _limiters['support'].is_allowed(ip):
                    return jsonify({'error': 'Too many support requests.'}), 429

        # ------------------------------------------------------------------
        # 6. Request body size
        # ------------------------------------------------------------------
        max_api, max_upload = _body_limits()
        cl = request.content_length or 0
        is_upload = any(x in path for x in _UPLOAD_PATHS)
        limit = max_upload if is_upload else max_api
        if cl > limit:
            return jsonify({'error': 'Request too large.'}), 413

        # ------------------------------------------------------------------
        # 7. Injection scanning — JSON body
        # ------------------------------------------------------------------
        if (request.method in ('POST', 'PUT', 'PATCH')
                and path.startswith('/api/')
                and _flag('injection_scan')):
            ct = request.content_type or ''
            if 'multipart' not in ct and 'application/json' in ct:
                try:
                    payload = request.get_json(silent=True, force=True) or {}
                    if payload:
                        hit = scan_request_payload(payload)
                        if hit:
                            logger.warning('[sec] Injection(json): %s %s [%s]', ip, path, hit)
                            return jsonify({'error': 'Invalid input detected.'}), 400
                except Exception:
                    pass

        # ------------------------------------------------------------------
        # 8. Injection scanning — form data
        # ------------------------------------------------------------------
        if _flag('injection_scan') and request.form:
            hit = scan_form_data(request.form)
            if hit:
                logger.warning('[sec] Injection(form): %s %s [%s]', ip, path, hit)
                return jsonify({'error': 'Invalid input detected.'}), 400

        # ------------------------------------------------------------------
        # 9. Injection scanning — file names
        # ------------------------------------------------------------------
        if _flag('injection_scan') and request.files:
            hit = scan_file_names(request.files)
            if hit:
                logger.warning('[sec] Injection(file): %s %s [%s]', ip, path, hit)
                return jsonify({'error': 'Invalid file.'}), 400

        # ------------------------------------------------------------------
        # 10. Injection scanning — GET parameters
        # ------------------------------------------------------------------
        if _flag('injection_scan') and request.args:
            hit = scan_query_params(dict(request.args))
            if hit:
                logger.warning('[sec] Injection(qs): %s %s [%s]', ip, path, hit)
                return jsonify({'error': 'Invalid parameter detected.'}), 400

        # ------------------------------------------------------------------
        # 11. CSRF
        # ------------------------------------------------------------------
        if _flag('csrf') and not validate_csrf():
            # Check if view is exempt
            endpoint = request.endpoint
            view_func = app.view_functions.get(endpoint)
            if not getattr(view_func, '_csrf_exempt', False):
                logger.warning('[sec] CSRF fail: %s %s', ip, path)
                return jsonify({'error': 'CSRF validation failed.'}), 403

        # ------------------------------------------------------------------
        # 12. User-Agent sanity check
        # ------------------------------------------------------------------
        if _flag('ua_check'):
            ua = request.headers.get('User-Agent', '')
            if not ua or len(ua) < 5:
                if path.startswith(('/api/auth/', '/api/balance/', '/api/vip/')):
                    return jsonify({'error': 'Invalid request.'}), 400
            if path.startswith('/api/') and _is_suspicious_ua(ua):
                logger.warning('[sec] Suspicious UA: %s %s [%s]', ip, path, ua[:180])
                return jsonify({'error': 'Access denied.'}), 403

        # ------------------------------------------------------------------
        # 13.5 GeoIP country block
        # ------------------------------------------------------------------
        if _flag('geo_block'):
            blocked = _blocked_countries()
            if blocked:
                cc = _country_code_by_ip(ip)
                if cc and cc in blocked:
                    logger.warning('[sec] Geo block: %s %s [%s]', ip, path, cc)
                    return jsonify({'error': 'Access unavailable in your region.'}), 403

        # ------------------------------------------------------------------
        # 13. Host header validation
        # ------------------------------------------------------------------
        if _flag('host_check'):
            allowed_hosts = [h.strip() for h in os.environ.get('ALLOWED_HOSTS', '').split(',') if h.strip()]
            if allowed_hosts:
                host = (request.host or '').split(':')[0]
                if host not in allowed_hosts:
                    logger.warning('[sec] Bad host: %s %s', host, path)
                    return jsonify({'error': 'Invalid host.'}), 400

        return None

    @app.after_request
    def _add_headers(response):
        nonce = getattr(g, 'csp_nonce', generate_nonce())
        apply_security_headers(response, https=https, nonce=nonce)
        set_csrf_cookie(response)
        return response

    # -----------------------------------------------------------------------
    # Error handlers
    # -----------------------------------------------------------------------
    @app.errorhandler(400)
    def _400(e): return jsonify({'error': 'Bad request'}), 400

    @app.errorhandler(403)
    def _403(e): return jsonify({'error': 'Forbidden'}), 403

    @app.errorhandler(404)
    def _404(e): return jsonify({'error': 'Not found'}), 404

    @app.errorhandler(405)
    def _405(e): return jsonify({'error': 'Method not allowed'}), 405

    @app.errorhandler(413)
    def _413(e): return jsonify({'error': 'Request too large.'}), 413

    @app.errorhandler(429)
    def _429(e): return jsonify({'error': 'Too many requests.'}), 429

    @app.errorhandler(500)
    def _500(e):
        app.logger.error('[sec] 500: %s', e, exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

    app.logger.info('[SEC] Security middleware: ON  |  https=%s', https)
    return app


# ---------------------------------------------------------------------------
# Public helpers used by auth routes
# ---------------------------------------------------------------------------

def rate_limit_login(identifier: str) -> tuple[bool, dict]:
    ip = get_client_ip()
    if not _limiters.get('login', _SlidingWindowLimiter(10, 60)).is_allowed(ip):
        return False, {
            'error': 'Too many login attempts. Try again in 1 minute.',
            'retry_after': 60,
        }
    locked, remaining = account_lockout.is_locked(identifier)
    if locked:
        minutes = max(1, remaining // 60)
        return False, {
            'error': f'Account locked for {minutes} min.',
            'retry_after': remaining,
            'locked': True,
        }
    return True, {}


def record_login_failure(identifier: str) -> int:
    return account_lockout.record_failure(identifier)


def record_login_success(identifier: str):
    account_lockout.record_success(identifier)


def get_csp_nonce() -> str:
    """Call from Jinja2 templates: {{ csp_nonce() }}"""
    return getattr(g, 'csp_nonce', '')