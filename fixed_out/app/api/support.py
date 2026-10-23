"""app/api/support.py — Blueprint тикетов поддержки (casino.db)."""
import os, secrets, string, sqlite3 as _sl, json
from flask import Blueprint, request, jsonify, session
from app.models.user import User

support_bp = Blueprint('support', __name__, url_prefix='/api/support')

_DB_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', '..', 'database', 'casino.db')
_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', '..', 'static', 'uploads', 'support')
_ALLOWED_EXT = {'jpg', 'jpeg', 'png'}


def _gen_uuid() -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))


def _user():
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


def _db():
    """Direct sqlite3 — avoids custom cursor wrapper issues."""
    conn = _sl.connect(_DB_PATH)
    conn.row_factory = _sl.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


@support_bp.route('/ticket', methods=['POST'])
def create_ticket():
    u = _user()

    if request.content_type and 'multipart' in request.content_type:
        subject = (request.form.get('subject') or '').strip()
        desc    = (request.form.get('description') or '').strip()
        contact = (request.form.get('contact') or '').strip()
    else:
        d       = request.get_json() or {}
        subject = (d.get('subject') or '').strip()
        desc    = (d.get('description') or '').strip()
        contact = (d.get('contact') or '').strip()

    if not subject or not desc:
        return jsonify({'error': 'Заполните все поля'}), 400

    uid   = u.id if u else None
    email = (u.email or contact) if u else contact

    # Generate unique 5-char UUID
    uuid = _gen_uuid()
    conn = _db()
    for _ in range(10):
        row = conn.execute("SELECT id FROM support_tickets WHERE ticket_uuid=?", (uuid,)).fetchone()
        if not row:
            break
        uuid = _gen_uuid()

    # Insert ticket (with ticket_uuid if column exists)
    try:
        conn.execute(
            "INSERT INTO support_tickets (user_id, contact, subject, description, ticket_uuid, created_at)"
            " VALUES (?,?,?,?,?,datetime('now'))",
            (uid, email, subject, desc, uuid)
        )
    except _sl.OperationalError:
        # ticket_uuid column not yet added (migration pending) — insert without it
        conn.execute(
            "INSERT INTO support_tickets (user_id, contact, subject, description, created_at)"
            " VALUES (?,?,?,?,datetime('now'))",
            (uid, email, subject, desc)
        )
    conn.commit()
    tid = conn.execute("SELECT last_insert_rowid() AS lid").fetchone()['lid']

    # Save optional screenshots
    files = request.files.getlist('uploads') if request.files else []
    saved = []
    for f in files:
        if not f or not f.filename:
            continue
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in _ALLOWED_EXT:
            continue
        ticket_dir = os.path.join(_UPLOADS_DIR, str(tid))
        os.makedirs(ticket_dir, exist_ok=True)
        fname = f'{secrets.token_hex(8)}.{ext}'
        f.save(os.path.join(ticket_dir, fname))
        saved.append(fname)

    if saved:
        conn.execute(
            "UPDATE support_tickets SET attachment_path=? WHERE id=?",
            (json.dumps(saved), tid)
        )
        conn.commit()

    conn.close()
    return jsonify({'success': True, 'message': 'Тикет создан! Ответим в ближайшее время.'})


@support_bp.route('/my_tickets')
def my_tickets():
    """Возвращает список тикетов текущего пользователя."""
    u = _user()
    if not u:
        return jsonify({'error': 'Требуется авторизация'}), 401

    conn = _db()
    rows = conn.execute(
        """SELECT id, ticket_uuid, subject, description, status, created_at
           FROM support_tickets
           WHERE user_id = ?
           ORDER BY created_at DESC
           LIMIT 50""",
        (u.id,)
    ).fetchall()
    conn.close()

    tickets = []
    for r in rows:
        tickets.append({
            'id':          r['id'],
            'uuid':        r['ticket_uuid'] or f'#{r["id"]}',
            'subject':     r['subject'],
            'description': r['description'],
            'status':      r['status'] or 'open',
            'created_at':  r['created_at'],
        })

    return jsonify({'tickets': tickets})


@support_bp.route('/ticket/<int:ticket_id>/messages')
def ticket_messages(ticket_id):
    """Возвращает переписку по тикету (только для его владельца)."""
    u = _user()
    if not u:
        return jsonify({'error': 'Требуется авторизация'}), 401

    conn = _db()

    # Проверяем что тикет принадлежит этому пользователю
    row = conn.execute(
        'SELECT id, subject, description, status, created_at FROM support_tickets WHERE id=? AND user_id=?',
        (ticket_id, u.id)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({'error': 'Тикет не найден'}), 404

    # Сообщения: admin + user + системные о принятии тикета (assign)
    msgs = conn.execute(
        """SELECT sender, message, created_at
           FROM support_ticket_messages
           WHERE ticket_id=?
             AND (
               sender IN ('admin', 'user')
               OR (sender = 'system' AND (
                     message LIKE '%Ticket taken%'
                  OR message LIKE '%взят%'
                  OR message LIKE '%released%'
               ))
             )
           ORDER BY created_at ASC""",
        (ticket_id,)
    ).fetchall()
    conn.close()

    return jsonify({
        'ticket': {
            'id':          row['id'],
            'subject':     row['subject'],
            'description': row['description'],
            'status':      row['status'] or 'open',
            'created_at':  row['created_at'],
        },
        'messages': [
            {
                'sender':     m['sender'],
                'message':    m['message'],
                'created_at': m['created_at'],
            }
            for m in msgs
        ],
    })


@support_bp.route('/ticket/<int:ticket_id>/close', methods=['POST'])
def close_ticket(ticket_id):
    """Пользователь закрывает свой тикет."""
    u = _user()
    if not u:
        return jsonify({'error': 'Требуется авторизация'}), 401

    conn = _db()
    row = conn.execute(
        'SELECT id, status FROM support_tickets WHERE id=? AND user_id=?',
        (ticket_id, u.id)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({'error': 'Тикет не найден'}), 404

    if row['status'] == 'closed':
        conn.close()
        return jsonify({'error': 'Тикет уже закрыт'}), 400

    conn.execute(
        "UPDATE support_tickets SET status='closed' WHERE id=?",
        (ticket_id,)
    )
    conn.execute(
        """INSERT INTO support_ticket_messages (ticket_id, sender, message, created_at)
           VALUES (?, 'system', '✅ Тикет закрыт пользователем.', datetime('now'))""",
        (ticket_id,)
    )
    conn.commit()
    conn.close()

    return jsonify({'success': True})


@support_bp.route('/ticket/<int:ticket_id>/reply', methods=['POST'])
def reply_ticket(ticket_id):
    """Пользователь отвечает в своём тикете."""
    u = _user()
    if not u:
        return jsonify({'error': 'Требуется авторизация'}), 401

    d = request.get_json() or {}
    message = (d.get('message') or '').strip()
    if not message:
        return jsonify({'error': 'Сообщение не может быть пустым'}), 400
    if len(message) > 2000:
        return jsonify({'error': 'Сообщение слишком длинное (макс. 2000 символов)'}), 400

    conn = _db()
    row = conn.execute(
        'SELECT id, status FROM support_tickets WHERE id=? AND user_id=?',
        (ticket_id, u.id)
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({'error': 'Тикет не найден'}), 404

    if row['status'] == 'closed':
        conn.close()
        return jsonify({'error': 'Тикет закрыт — нельзя отправить сообщение'}), 400

    conn.execute(
        """INSERT INTO support_ticket_messages (ticket_id, sender, message, created_at)
           VALUES (?, 'user', ?, datetime('now'))""",
        (ticket_id, message)
    )
    # Если тикет был закрыт — переоткрываем при ответе пользователя
    if row['status'] != 'open':
        conn.execute(
            "UPDATE support_tickets SET status='open' WHERE id=?",
            (ticket_id,)
        )
    conn.commit()
    conn.close()

    return jsonify({'success': True, 'message': message})
