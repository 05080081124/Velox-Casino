"""app/api/soc.py — SOC dashboard: suspicious IPs, requests, threats."""
import os, json, re, sqlite3
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify
from app.api.auth import require_admin

soc_bp = Blueprint('soc', __name__, url_prefix='/api/soc')

# Roles that can access SOC
SOC_ROLES = {'soc', 'op', 'ceo'}


def _soc_required(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        admin = kwargs.get('admin')
        if not admin or admin.role not in SOC_ROLES:
            return jsonify({'error': 'SOC access only'}), 403
        return f(*args, **kwargs)
    return wrapper


# ── Parse casino access log ───────────────────────────────────────────────────

def _get_log_path() -> str:
    casino_root = os.environ.get('CASINO_ROOT', '').strip()
    candidates  = []
    if casino_root:
        candidates += [
            os.path.join(casino_root, 'logs', 'access.log'),
            os.path.join(casino_root, 'access.log'),
        ]
    _here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        _here = os.path.dirname(_here)
        for name in ('fixed_out', 'casino', 'velox_casino', ''):
            candidates.append(os.path.join(_here, name, 'logs', 'access.log'))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return ''


def _parse_log_lines(path: str, hours: int = 24) -> list[dict]:
    """Parse combined log format entries from the last N hours."""
    if not path:
        return []
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    entries = []
    # Combined log: 127.0.0.1 - - [14/Mar/2026:12:00:00 +0000] "GET /path HTTP/1.1" 200 512
    pattern = re.compile(
        r'(?P<ip>\S+) \S+ \S+ \[(?P<dt>[^\]]+)\] "(?P<method>\w+) (?P<path>\S+)[^"]*" (?P<status>\d+) (?P<size>\S+)'
    )
    try:
        with open(path, 'r', errors='replace') as f:
            for line in f:
                m = pattern.match(line)
                if not m:
                    continue
                try:
                    dt = datetime.strptime(m.group('dt')[:20], '%d/%b/%Y:%H:%M:%S')
                    if dt < cutoff:
                        continue
                except Exception:
                    continue
                entries.append({
                    'ip':     m.group('ip'),
                    'dt':     m.group('dt'),
                    'method': m.group('method'),
                    'path':   m.group('path'),
                    'status': int(m.group('status')),
                    'size':   m.group('size'),
                })
    except Exception:
        pass
    return entries


# ── Threat detection ──────────────────────────────────────────────────────────

_SUSPICIOUS_PATHS = [
    '/admin', '/wp-admin', '/.env', '/config', '/etc/passwd',
    '/phpmyadmin', '/shell', '/cmd', '/exec', '/eval',
    '../', '..\\', 'SELECT ', 'UNION ', '<script',
    '/api/auth/login',  # brute-force indicator
]

_SCAN_UAS = [
    'sqlmap', 'nikto', 'nmap', 'masscan', 'zgrab', 'curl/7',
    'python-requests', 'go-http-client', 'dirbuster', 'hydra',
]


def _analyze(entries: list[dict]) -> dict:
    ip_counts   = Counter(e['ip'] for e in entries)
    status_cnt  = Counter(e['status'] for e in entries)
    method_cnt  = Counter(e['method'] for e in entries)
    path_cnt    = Counter(e['path'] for e in entries)

    # IPs with many 4xx / 5xx
    ip_errors   = defaultdict(int)
    ip_paths    = defaultdict(set)
    for e in entries:
        if e['status'] >= 400:
            ip_errors[e['ip']] += 1
        ip_paths[e['ip']].add(e['path'])

    # Suspicious: >100 requests OR >20 errors OR scanning known paths
    threats = []
    for ip, cnt in ip_counts.most_common(100):
        reasons = []
        if cnt > 200:
            reasons.append(f'High volume: {cnt} requests')
        if ip_errors[ip] > 30:
            reasons.append(f'{ip_errors[ip]} error responses')
        paths_hit = ip_paths[ip]
        sus_paths = [p for p in paths_hit
                     if any(s.lower() in p.lower() for s in _SUSPICIOUS_PATHS)]
        if sus_paths:
            reasons.append(f'Suspicious paths: {", ".join(sus_paths[:3])}')
        if len(paths_hit) > 100:
            reasons.append(f'Scanning: {len(paths_hit)} unique paths')
        if reasons:
            threats.append({
                'ip':       ip,
                'requests': cnt,
                'errors':   ip_errors[ip],
                'reasons':  reasons,
                'severity': 'high' if len(reasons) >= 2 else 'medium',
            })

    # Top IPs
    top_ips = [{'ip': ip, 'requests': cnt}
               for ip, cnt in ip_counts.most_common(15)]

    # Top paths
    top_paths = [{'path': p, 'hits': c}
                 for p, c in path_cnt.most_common(10)]

    # Status breakdown
    status_groups = {'2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0}
    for s, c in status_cnt.items():
        key = f'{s // 100}xx'
        if key in status_groups:
            status_groups[key] += c

    return {
        'total_requests': len(entries),
        'unique_ips':     len(ip_counts),
        'threats':        threats,
        'top_ips':        top_ips,
        'top_paths':      top_paths,
        'status_groups':  status_groups,
        'methods':        dict(method_cnt.most_common()),
        'log_available':  True,
    }


def _db_stats() -> dict:
    """Pull stats from casino DB."""
    from app.db import get_casino_db, DB_ENGINE
    try:
        with get_casino_db() as db:
            db.execute("SELECT COUNT(*) as cnt FROM users")
            users = (db.fetchone() or {}).get('cnt', 0)
            db.execute("SELECT COUNT(*) as cnt FROM transactions WHERE created_at >= datetime('now', '-1 day')")
            tx_day = (db.fetchone() or {}).get('cnt', 0)
            db.execute("SELECT COUNT(*) as cnt FROM payment_orders WHERE status='pending'")
            pay_pending = (db.fetchone() or {}).get('cnt', 0)
            db.execute("SELECT COUNT(*) as cnt FROM users WHERE kyc_status='pending'")
            kyc_pending = (db.fetchone() or {}).get('cnt', 0)
            db.execute("SELECT COUNT(*) as cnt FROM support_tickets WHERE status='open'")
            tickets_open = (db.fetchone() or {}).get('cnt', 0)
        return {
            'users_total':   users,
            'tx_last_24h':   tx_day,
            'pay_pending':   pay_pending,
            'kyc_pending':   kyc_pending,
            'tickets_open':  tickets_open,
        }
    except Exception as e:
        return {'error': str(e)}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@soc_bp.route('/overview', methods=['GET'])
@require_admin
@_soc_required
def overview(admin):
    hours   = int(request.args.get('hours', 24))
    log_path = _get_log_path()
    entries  = _parse_log_lines(log_path, hours)

    if entries:
        analysis = _analyze(entries)
    else:
        analysis = {
            'total_requests': 0,
            'unique_ips':     0,
            'threats':        [],
            'top_ips':        [],
            'top_paths':      [],
            'status_groups':  {'2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0},
            'methods':        {},
            'log_available':  False,
        }

    db_stats = _db_stats()

    return jsonify({
        'analysis':   analysis,
        'db_stats':   db_stats,
        'hours':      hours,
        'log_path':   log_path or 'Not found',
    })


@soc_bp.route('/threats', methods=['GET'])
@require_admin
@_soc_required
def threats(admin):
    hours    = int(request.args.get('hours', 24))
    log_path = _get_log_path()
    entries  = _parse_log_lines(log_path, hours)
    analysis = _analyze(entries) if entries else {'threats': [], 'log_available': False}
    return jsonify({'threats': analysis.get('threats', []),
                    'log_available': analysis.get('log_available', False)})
