"""app/api/tickets.py — Tickets, users, stats, KYC actions, assign/unpin."""
import os, json
from flask import Blueprint, request, jsonify
from app.db import get_casino_db
from app.api.auth import require_admin

tickets_bp = Blueprint('tickets', __name__, url_prefix='/api/tickets')


# ── Stats ─────────────────────────────────────────────────────────────────────

@tickets_bp.route('/stats', methods=['GET'])
@require_admin
def stats(admin):
    def q(db, sql, p=None):
        db.execute(sql, p) if p else db.execute(sql)
        return (db.fetchone() or {}).get('cnt', 0)

    with get_casino_db() as db:
        open_cnt     = q(db, "SELECT COUNT(*) as cnt FROM support_tickets WHERE status='open'")
        progress_cnt = q(db, "SELECT COUNT(*) as cnt FROM support_tickets WHERE status='in_progress'")
        closed_cnt   = q(db, "SELECT COUNT(*) as cnt FROM support_tickets WHERE status='closed'")
        users_cnt    = q(db, "SELECT COUNT(*) as cnt FROM users")
        pending_pay  = q(db, "SELECT COUNT(*) as cnt FROM payment_orders WHERE status='pending'")
        kyc_pending  = q(db, "SELECT COUNT(*) as cnt FROM users WHERE kyc_status='pending'")

    return jsonify({
        'tickets': {'open': open_cnt, 'in_progress': progress_cnt, 'closed': closed_cnt},
        'users':            users_cnt,
        'payments_pending': pending_pay,
        'kyc_pending':      kyc_pending,
    })


# ── Tickets list ──────────────────────────────────────────────────────────────

@tickets_bp.route('/', methods=['GET'])
@require_admin
def list_tickets(admin):
    status = request.args.get('status', '').strip()
    page   = max(1, int(request.args.get('page', 1)))
    limit  = 20
    offset = (page - 1) * limit

    with get_casino_db() as db:
        if status:
            db.execute(
                "SELECT t.*, u.user_name, u.email as user_email "
                "FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.status = %s ORDER BY t.created_at DESC LIMIT %s OFFSET %s",
                (status, limit, offset)
            )
        else:
            db.execute(
                "SELECT t.*, u.user_name, u.email as user_email "
                "FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
                "ORDER BY t.created_at DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
        tickets = db.fetchall()

        where = f" WHERE status = '{status}'" if status else ''
        db.execute(f"SELECT COUNT(*) as cnt FROM support_tickets{where}")
        total = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({
        'tickets': [dict(t) for t in tickets],
        'total':   total,
        'page':    page,
        'pages':   max(1, (total + limit - 1) // limit),
    })


# ── Ticket detail ─────────────────────────────────────────────────────────────

@tickets_bp.route('/<int:tid>', methods=['GET'])
@require_admin
def get_ticket(tid, admin):
    with get_casino_db() as db:
        db.execute(
            "SELECT t.*, u.user_name, u.email as user_email, "
            "u.balance, u.currency, u.kyc_status, u.verified "
            "FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
            "WHERE t.id = %s",
            (tid,)
        )
        ticket = db.fetchone()
    if not ticket:
        return jsonify({'error': 'Ticket not found'}), 404

    ticket = dict(ticket)

    # ── KYC detection ─────────────────────────────────────────────────────────
    ticket['is_kyc']     = False
    ticket['media_url']  = None
    ticket['media_type'] = None

    subject = (ticket.get('subject') or '').upper()
    desc    = ticket.get('description') or ''

    if 'KYC_VERIFICATION' in subject or '[KYC' in subject:
        ticket['is_kyc'] = True
        for line in desc.splitlines():
            line = line.strip()
            if line.lower().startswith('видео:') or line.lower().startswith('video:'):
                path = line.split(':', 1)[1].strip()
                if path:
                    ticket['media_url']  = _resolve_media_url(path)
                    ticket['media_type'] = _detect_media_type(path)
                break
        if not ticket['media_url'] and ticket.get('user_id'):
            with get_casino_db() as db:
                db.execute('SELECT kyc_doc_path FROM users WHERE id = %s', (ticket['user_id'],))
                row = db.fetchone()
            if row and row.get('kyc_doc_path'):
                path = row['kyc_doc_path']
                ticket['media_url']  = _resolve_media_url(path)
                ticket['media_type'] = _detect_media_type(path)

    # ── Messages ──────────────────────────────────────────────────────────────
    with get_casino_db() as db:
        db.execute(
            "SELECT * FROM support_ticket_messages "
            "WHERE ticket_id = %s ORDER BY created_at ASC",
            (tid,)
        )
        messages = db.fetchall()

    # Who is assigned right now
    ticket['assigned_to_me'] = (
        ticket.get('assigned_to_id') == admin.id
    )

    # Parse attachment paths
    import json as _json
    raw_att = ticket.get('attachment_path')
    attachments = []
    if raw_att:
        try:
            names = _json.loads(raw_att) if raw_att.startswith('[') else [raw_att]
            # Use velox_support proxy route (can't serve casino's /static/ directly)
            attachments = [f'/api/tickets/support-file/{tid}/{n}' for n in names]
        except Exception:
            pass

    return jsonify({
        'ticket':      ticket,
        'messages':    [dict(m) for m in messages],
        'my_id':       admin.id,
        'attachments': attachments,
    })


def _resolve_media_url(path: str) -> str:
    return f'/api/tickets/media/{path.lstrip("/")}'


def _detect_media_type(path: str) -> str:
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    if ext in ('mp4', 'webm', 'mov', 'avi'):  return 'video'
    if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'): return 'image'
    if ext == 'pdf': return 'pdf'
    return 'unknown'


# ── Media proxy ───────────────────────────────────────────────────────────────

@tickets_bp.route('/media/<path:filepath>', methods=['GET'])
@require_admin
def serve_media(filepath, admin):
    from flask import send_file, abort

    casino_root = os.environ.get('CASINO_ROOT', '').strip()
    candidates  = []
    if casino_root:
        candidates.append(os.path.join(casino_root, filepath))

    _here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(5):
        _here = os.path.dirname(_here)
        for name in ('fixed_out', 'casino', 'velox_casino'):
            candidates.append(os.path.join(_here, name, filepath))
        candidates.append(os.path.join(_here, filepath))

    for full_path in candidates:
        full_path = os.path.normpath(full_path)
        if os.path.isfile(full_path):
            return send_file(full_path)
    abort(404)


# ── Serve user support screenshots ──────────────────────────────────────────

@tickets_bp.route('/support-file/<int:tid>/<filename>', methods=['GET'])
@require_admin
def serve_support_file(tid, filename, admin):
    from flask import send_file, abort
    import os as _os
    filename = _os.path.basename(filename)
    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _ in range(6):
        _here = _os.path.dirname(_here)
        for name in ('fixed_out', 'casino', 'velox_casino'):
            p = _os.path.normpath(
                _os.path.join(_here, name, 'static', 'uploads', 'support', str(tid), filename)
            )
            if _os.path.isfile(p):
                return send_file(p)
    abort(404)


# ── Assign (take / set in progress) ──────────────────────────────────────────

@tickets_bp.route('/<int:tid>/assign', methods=['POST'])
@require_admin
def assign_ticket(tid, admin):
    """Take ownership: set status=in_progress, store operator info."""
    with get_casino_db() as db:
        db.execute("SELECT status, assigned_to_id FROM support_tickets WHERE id = %s", (tid,))
        row = db.fetchone()

    if not row:
        return jsonify({'error': 'Ticket not found'}), 404
    if row.get('assigned_to_id') and row['assigned_to_id'] != admin.id:
        return jsonify({'error': 'Already taken by another operator'}), 409

    with get_casino_db() as db:
        db.execute(
            "UPDATE support_tickets SET status='in_progress', "
            "assigned_to_id=%s, assigned_to_nickname=%s, assigned_to_public_id=%s "
            "WHERE id = %s",
            (admin.id, admin.nickname, admin.public_id, tid)
        )
        db.execute(
            "INSERT INTO support_ticket_messages (ticket_id, admin_id, sender, message) "
            "VALUES (%s, %s, %s, %s)",
            (tid, admin.id, 'system',
             f'🔔 Ticket taken by {admin.nickname} ({admin.public_id})')
        )

    return jsonify({'ok': True, 'assigned_to': {
        'id': admin.id, 'nickname': admin.nickname, 'public_id': admin.public_id
    }})


# ── Unpin (release ownership) ─────────────────────────────────────────────────

@tickets_bp.route('/<int:tid>/unpin', methods=['POST'])
@require_admin
def unpin_ticket(tid, admin):
    """Release ownership: clear assigned_to, set back to open."""
    with get_casino_db() as db:
        db.execute("SELECT assigned_to_id FROM support_tickets WHERE id = %s", (tid,))
        row = db.fetchone()

    if not row:
        return jsonify({'error': 'Ticket not found'}), 404
    if row.get('assigned_to_id') and row['assigned_to_id'] != admin.id:
        return jsonify({'error': 'You are not the assigned operator'}), 403

    with get_casino_db() as db:
        db.execute(
            "UPDATE support_tickets SET status='open', "
            "assigned_to_id=NULL, assigned_to_nickname=NULL, assigned_to_public_id=NULL "
            "WHERE id = %s",
            (tid,)
        )
        db.execute(
            "INSERT INTO support_ticket_messages (ticket_id, admin_id, sender, message) "
            "VALUES (%s, %s, %s, %s)",
            (tid, admin.id, 'system',
             f'↩️ {admin.nickname} ({admin.public_id}) released this ticket')
        )

    return jsonify({'ok': True})


# ── Update status (close only now — assign/unpin handle the rest) ─────────────

@tickets_bp.route('/<int:tid>/status', methods=['PUT'])
@require_admin
def update_status(tid, admin):
    d      = request.get_json() or {}
    status = (d.get('status') or '').strip()
    if status not in ('open', 'in_progress', 'closed'):
        return jsonify({'error': 'Invalid status'}), 400
    with get_casino_db() as db:
        db.execute("UPDATE support_tickets SET status = %s WHERE id = %s", (status, tid))
    return jsonify({'ok': True})


# ── Messages only — lightweight endpoint for polling ─────────────────────────

@tickets_bp.route('/<int:tid>/messages', methods=['GET'])
@require_admin
def get_messages(tid, admin):
    with get_casino_db() as db:
        db.execute(
            "SELECT * FROM support_ticket_messages "
            "WHERE ticket_id = %s ORDER BY created_at ASC",
            (tid,)
        )
        messages = db.fetchall()
    return jsonify({'messages': [dict(m) for m in messages]})


# ── Reply — locked to assigned operator only ──────────────────────────────────

@tickets_bp.route('/<int:tid>/reply', methods=['POST'])
@require_admin
def reply(tid, admin):
    d   = request.get_json() or {}
    msg = (d.get('message') or '').strip()
    if not msg:
        return jsonify({'error': 'Message cannot be empty'}), 400

    with get_casino_db() as db:
        db.execute(
            "SELECT status, assigned_to_id FROM support_tickets WHERE id = %s", (tid,)
        )
        row = db.fetchone()

    if not row:
        return jsonify({'error': 'Ticket not found'}), 404

    status      = row.get('status', 'open')
    assigned_id = row.get('assigned_to_id')

    # Closed — nobody can reply
    if status == 'closed':
        return jsonify({'error': 'This ticket is closed'}), 403

    # Not yet assigned — nobody can reply (must take first)
    if not assigned_id:
        return jsonify({'error': 'Take this ticket first before replying'}), 403

    # Assigned to someone else — blocked
    if assigned_id != admin.id:
        return jsonify({'error': 'You are not the assigned operator for this ticket'}), 403

    with get_casino_db() as db:
        db.execute(
            "INSERT INTO support_ticket_messages (ticket_id, admin_id, sender, message) "
            "VALUES (%s, %s, %s, %s)",
            (tid, admin.id, 'admin', msg)
        )

    # Email notification to user with language detection
    try:
        with get_casino_db() as db:
            db.execute(
                "SELECT t.subject, t.contact, t.ticket_uuid, u.email, u.user_name, u.currency "
                "FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
                "WHERE t.id = %s", (tid,)
            )
            trow = db.fetchone()
        if trow:
            to_email = trow.get('email') or trow.get('contact')
            username = trow.get('user_name') or to_email or 'User'
            cur_lang_map = {'USD':'en','UAH':'ua','RUB':'ru','EUR':'en','USDT':'en'}
            lang = cur_lang_map.get((trow.get('currency') or 'UAH').upper(), 'ru')
            ticket_display_id = trow.get('ticket_uuid') or str(tid)
            if to_email and '@' in to_email:
                from app.services.email_service import send_support_reply
                send_support_reply(
                    to_email      = to_email,
                    username      = username,
                    subject       = trow.get('subject', ''),
                    admin_message = msg,
                    ticket_id     = ticket_display_id,
                    lang          = lang,
                )
    except Exception as e:
        print(f'  [reply] email error: {e}')

    return jsonify({'ok': True})


# ── KYC: verify ───────────────────────────────────────────────────────────────

@tickets_bp.route('/<int:tid>/kyc/verify', methods=['POST'])
@require_admin
def kyc_verify(tid, admin):
    with get_casino_db() as db:
        db.execute("SELECT user_id FROM support_tickets WHERE id = %s", (tid,))
        row = db.fetchone()
    if not row or not row.get('user_id'):
        return jsonify({'error': 'Ticket not found or no linked user'}), 404

    uid = row['user_id']
    with get_casino_db() as db:
        db.execute(
            "UPDATE users SET kyc_status='verified', verified=1 WHERE id=%s", (uid,)
        )
        db.execute(
            "UPDATE support_tickets SET status='closed', "
            "assigned_to_id=%s, assigned_to_nickname=%s, assigned_to_public_id=%s "
            "WHERE id=%s",
            (admin.id, admin.nickname, admin.public_id, tid)
        )
        db.execute(
            "INSERT INTO support_ticket_messages (ticket_id, admin_id, sender, message) "
            "VALUES (%s, %s, %s, %s)",
            (tid, admin.id, 'system',
             f'✅ KYC verified by {admin.nickname} ({admin.public_id}). Account approved.')
        )

    return jsonify({'ok': True, 'kyc_status': 'verified'})


# ── KYC: decline ─────────────────────────────────────────────────────────────

@tickets_bp.route('/<int:tid>/kyc/decline', methods=['POST'])
@require_admin
def kyc_decline(tid, admin):
    d      = request.get_json() or {}
    reason = (d.get('reason') or 'Verification declined by operator').strip()

    with get_casino_db() as db:
        db.execute("SELECT user_id FROM support_tickets WHERE id = %s", (tid,))
        row = db.fetchone()
    if not row or not row.get('user_id'):
        return jsonify({'error': 'Ticket not found or no linked user'}), 404

    uid = row['user_id']
    with get_casino_db() as db:
        db.execute(
            "UPDATE users SET kyc_status='rejected', kyc_rejection_reason=%s WHERE id=%s",
            (reason, uid)
        )
        db.execute(
            "UPDATE support_tickets SET status='closed' WHERE id=%s", (tid,)
        )
        db.execute(
            "INSERT INTO support_ticket_messages (ticket_id, admin_id, sender, message) "
            "VALUES (%s, %s, %s, %s)",
            (tid, admin.id, 'system',
             f'❌ KYC declined by {admin.nickname} ({admin.public_id}). Reason: {reason}')
        )

    return jsonify({'ok': True, 'kyc_status': 'rejected'})


# ── Translate ─────────────────────────────────────────────────────────────────

@tickets_bp.route('/translate', methods=['POST'])
@require_admin
def translate_text(admin):
    d      = request.get_json() or {}
    text   = (d.get('text') or '').strip()
    target = (d.get('target') or 'en').lower()

    if not text:
        return jsonify({'error': 'No text provided'}), 400
    if len(text) > 5000:
        text = text[:5000]

    # Map support panel lang codes to ISO-639-1
    lang_map = {'en': 'en', 'ru': 'ru', 'ua': 'uk', 'uk': 'uk'}
    target_code = lang_map.get(target, 'en')

    # Detect source language (never send 'auto' to MyMemory)
    source_code = _detect_lang(text)

    translated = _translate(text, source_code, target_code)
    return jsonify({'ok': True, 'translated': translated,
                    'source': source_code, 'target': target_code})


def _detect_lang(text: str) -> str:
    """
    Simple heuristic: count Cyrillic vs Latin chars.
    Returns ISO-639-1 code: 'ru', 'uk', or 'en'.
    For the support panel use-case this is enough —
    casino users write in RU/UA/EN predominantly.
    """
    sample = text[:200]
    cyrillic = sum(1 for c in sample if '\u0400' <= c <= '\u04FF')
    latin    = sum(1 for c in sample if c.isalpha() and c.isascii())
    total    = cyrillic + latin or 1

    if cyrillic / total > 0.4:
        # Distinguish UA (Ukrainian) by presence of unique Ukrainian chars
        ua_chars = set('іїєґІЇЄҐ')
        if any(c in ua_chars for c in sample):
            return 'uk'
        return 'ru'
    return 'en'


def _translate(text: str, source: str, target: str) -> str:
    """
    Translate using MyMemory free API.
    langpair must be explicit ISO codes, e.g. 'ru|en' (NOT 'auto|en').
    """
    import urllib.request, urllib.parse

    if source == target:
        return text

    try:
        params = urllib.parse.urlencode({
            'q':        text,
            'langpair': f'{source}|{target}',
        })
        url = f'https://api.mymemory.translated.net/get?{params}'
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'VeloxSupport/1.0'}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())

        # Check response status
        resp_status = data.get('responseStatus', 0)
        result = data.get('responseData', {}).get('translatedText', '')

        if resp_status == 200 and result and result != text:
            return result

        # MyMemory sometimes returns matches array as fallback
        matches = data.get('matches', [])
        if matches:
            best = max(matches, key=lambda m: float(m.get('quality', 0)), default=None)
            if best and best.get('translation'):
                return best['translation']

    except Exception as e:
        print(f'  [Translate] Error: {e}')

    return text  # return original if failed


# ── My reports ───────────────────────────────────────────────────────────────

@tickets_bp.route('/reports/mine', methods=['GET'])
@require_admin
def my_reports(admin):
    """
    Returns tickets closed/assigned by this admin.
    period: week | month | year | all
    """
    period = request.args.get('period', 'month').strip()
    page   = max(1, int(request.args.get('page', 1)))
    limit  = 25
    offset = (page - 1) * limit

    period_sql = {
        'week':  "AND t.created_at >= datetime('now', '-7 days')",
        'month': "AND t.created_at >= datetime('now', '-1 month')",
        'year':  "AND t.created_at >= datetime('now', '-1 year')",
        'all':   '',
    }.get(period, '')

    # PG-compatible version (datetime() → NOW() - INTERVAL)
    from app.db import DB_ENGINE
    if DB_ENGINE == 'postgresql':
        period_sql = {
            'week':  "AND t.created_at >= NOW() - INTERVAL '7 days'",
            'month': "AND t.created_at >= NOW() - INTERVAL '1 month'",
            'year':  "AND t.created_at >= NOW() - INTERVAL '1 year'",
            'all':   '',
        }.get(period, '')

    base_where = f"WHERE t.assigned_to_id = %s AND t.status = 'closed' {period_sql}"

    with get_casino_db() as db:
        db.execute(
            f"SELECT t.*, u.user_name, u.email as user_email "
            f"FROM support_tickets t LEFT JOIN users u ON u.id = t.user_id "
            f"{base_where} ORDER BY t.created_at DESC LIMIT %s OFFSET %s",
            (admin.id, limit, offset)
        )
        tickets = db.fetchall()

        db.execute(
            f"SELECT COUNT(*) as cnt FROM support_tickets t {base_where}",
            (admin.id,)
        )
        total = (db.fetchone() or {}).get('cnt', 0)

        # KYC verified count
        db.execute(
            f"SELECT COUNT(*) as cnt FROM support_tickets t "
            f"WHERE t.assigned_to_id = %s AND t.status = 'closed' "
            f"AND (t.subject LIKE '%KYC%' OR t.subject LIKE '%kyc%') {period_sql}",
            (admin.id,)
        )
        kyc_cnt = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({
        'tickets': [dict(t) for t in tickets],
        'total':   total,
        'kyc_closed': kyc_cnt,
        'page':    page,
        'pages':   max(1, (total + limit - 1) // limit),
        'period':  period,
    })


# ── Reply — with lock check ───────────────────────────────────────────────────

# ── Users list ────────────────────────────────────────────────────────────────

@tickets_bp.route('/users', methods=['GET'])
@require_admin
def list_users(admin):
    q      = (request.args.get('q') or '').strip()
    page   = max(1, int(request.args.get('page', 1)))
    limit  = 20
    offset = (page - 1) * limit

    with get_casino_db() as db:
        if q:
            like = f'%{q}%'
            db.execute(
                "SELECT id, user_name, email, balance, currency, kyc_status, created_at "
                "FROM users WHERE user_name LIKE %s OR email LIKE %s "
                "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (like, like, limit, offset)
            )
        else:
            db.execute(
                "SELECT id, user_name, email, balance, currency, kyc_status, created_at "
                "FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (limit, offset)
            )
        users = db.fetchall()
        db.execute("SELECT COUNT(*) as cnt FROM users")
        total = (db.fetchone() or {}).get('cnt', 0)

    return jsonify({'users': [dict(u) for u in users], 'total': total})


@tickets_bp.route('/users/<int:uid>', methods=['GET'])
@require_admin
def get_user(uid, admin):
    with get_casino_db() as db:
        db.execute("SELECT * FROM users WHERE id = %s", (uid,))
        user = db.fetchone()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    with get_casino_db() as db:
        db.execute(
            "SELECT * FROM transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT 20",
            (uid,)
        )
        transactions = db.fetchall()

    with get_casino_db() as db:
        db.execute(
            "SELECT * FROM support_tickets WHERE user_id = %s ORDER BY created_at DESC LIMIT 10",
            (uid,)
        )
        user_tickets = db.fetchall()

    game_history = []
    try:
        with get_casino_db() as db:
            db.execute(
                "SELECT game_id, game_name, bet, win, created_at "
                "FROM game_history WHERE user_id = %s ORDER BY created_at DESC LIMIT 20",
                (uid,)
            )
            game_history = db.fetchall()
    except Exception:
        game_history = []

    safe = {k: v for k, v in dict(user).items() if k != 'password'}
    return jsonify({
        'user':         safe,
        'transactions': [dict(t) for t in transactions],
        'tickets':      [dict(t) for t in user_tickets],
        'game_history': [dict(g) for g in game_history],
    })
