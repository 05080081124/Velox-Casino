"""app/api/chat.py — Internal admin team chat with file attachments."""
import os, secrets, mimetypes
from flask import Blueprint, request, jsonify, send_file
from app.db import get_admin_db
from app.api.auth import require_admin

chat_bp = Blueprint('chat', __name__, url_prefix='/api/chat')

_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'support_uploads', 'chat')
_MAX_MB     = 50
_ALLOWED    = {'jpg','jpeg','png','gif','webp','mp4','webm','mov','mp3','ogg','wav','pdf','txt','zip'}

_MSG_COLS = (
    "SELECT m.id, m.admin_id, m.type, m.content, m.file_url, m.file_name, m.created_at, "
    "a.nickname, a.public_id, a.role "
    "FROM support_chat_messages m "
    "JOIN support_admins a ON a.id = m.admin_id "
)


def _last_message(db, admin_id: int) -> dict:
    """Fetch the most recently inserted message for this admin."""
    db.execute(
        _MSG_COLS +
        "WHERE m.admin_id = %s ORDER BY m.id DESC LIMIT 1",
        (admin_id,)
    )
    row = db.fetchone()
    return dict(row) if row else {}


# ── Messages list ─────────────────────────────────────────────────────────────

@chat_bp.route('/messages', methods=['GET'])
@require_admin
def get_messages(admin):
    limit  = min(int(request.args.get('limit', 50)), 100)
    before = request.args.get('before')

    with get_admin_db() as db:
        if before:
            db.execute(
                _MSG_COLS + "WHERE m.id < %s ORDER BY m.id DESC LIMIT %s",
                (int(before), limit)
            )
        else:
            db.execute(_MSG_COLS + "ORDER BY m.id DESC LIMIT %s", (limit,))
        rows = db.fetchall()

    rows.reverse()
    return jsonify({'messages': [dict(r) for r in rows]})


# ── Send text ─────────────────────────────────────────────────────────────────

@chat_bp.route('/send', methods=['POST'])
@require_admin
def send_message(admin):
    d    = request.get_json() or {}
    text = (d.get('content') or '').strip()
    if not text:
        return jsonify({'error': 'Empty message'}), 400
    if len(text) > 4000:
        return jsonify({'error': 'Message too long (max 4000 chars)'}), 400

    with get_admin_db() as db:
        db.execute(
            "INSERT INTO support_chat_messages (admin_id, type, content) "
            "VALUES (%s, %s, %s)",
            (admin.id, 'text', text)
        )
        msg = _last_message(db, admin.id)

    return jsonify({'ok': True, 'message': msg})


# ── Upload file ───────────────────────────────────────────────────────────────

@chat_bp.route('/upload', methods=['POST'])
@require_admin
def upload_file(admin):
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file provided'}), 400

    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in _ALLOWED:
        return jsonify({'error': f'File type not allowed'}), 400

    f.seek(0, 2); size_mb = f.tell() / (1024*1024); f.seek(0)
    if size_mb > _MAX_MB:
        return jsonify({'error': f'File too large (max {_MAX_MB} MB)'}), 400

    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    fname    = f'chat_{admin.id}_{secrets.token_hex(10)}.{ext}'
    filepath = os.path.join(_UPLOAD_DIR, fname)
    f.save(filepath)

    mime = mimetypes.guess_type(fname)[0] or ''
    if   mime.startswith('image'): ftype = 'image'
    elif mime.startswith('video'): ftype = 'video'
    elif mime.startswith('audio'): ftype = 'audio'
    elif ext == 'pdf':             ftype = 'pdf'
    else:                          ftype = 'file'

    file_url  = f'/api/chat/file/{fname}'
    orig_name = f.filename

    with get_admin_db() as db:
        db.execute(
            "INSERT INTO support_chat_messages "
            "(admin_id, type, content, file_url, file_name) VALUES (%s, %s, %s, %s, %s)",
            (admin.id, ftype, orig_name, file_url, orig_name)
        )
        msg = _last_message(db, admin.id)

    return jsonify({'ok': True, 'message': msg})


# ── Serve file ────────────────────────────────────────────────────────────────

@chat_bp.route('/file/<filename>', methods=['GET'])
@require_admin
def serve_file(filename, admin):
    filename = os.path.basename(filename)
    filepath = os.path.join(_UPLOAD_DIR, filename)
    if not os.path.isfile(filepath):
        from flask import abort
        abort(404)
    return send_file(filepath)


# ── Poll for new messages ─────────────────────────────────────────────────────

@chat_bp.route('/poll', methods=['GET'])
@require_admin
def poll(admin):
    after = int(request.args.get('after', 0))
    with get_admin_db() as db:
        db.execute(
            _MSG_COLS + "WHERE m.id > %s ORDER BY m.id ASC LIMIT 50",
            (after,)
        )
        rows = db.fetchall()
    return jsonify({'messages': [dict(r) for r in rows]})
