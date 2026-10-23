"""app/api/kyc.py — KYC API endpoints."""
import os
import secrets
from flask import Blueprint, request, jsonify, session
from app.models.user import User
from app.services.kyc_service import (
    load_cfg, get_mode, get_active_provider,
    get_or_refresh_word, create_kyc_ticket, create_scan_session,
    check_card_deposit,
)

kyc_bp = Blueprint('kyc', __name__, url_prefix='/api/kyc')

_BASE_DIR = os.path.join(os.path.dirname(__file__), '..', '..')


def _cu() -> User | None:
    uid = session.get('user_id')
    return User.get_by_id(uid) if uid else None


# ── STATUS ─────────────────────────────────────────────────────────────────────

@kyc_bp.route('/status')
def kyc_status():
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401

    cfg  = load_cfg()
    mode = get_mode()
    prov = get_active_provider()

    return jsonify({
        'mode':               mode,
        'active_provider':    prov,
        'status':             user.kyc_status,
        'dob':                user.kyc_dob,
        'submitted_at':       user.kyc_submitted_at,
        'rejection_reason':   user.kyc_rejection_reason,
        'min_age':            18,
        'card_verification':  cfg.get('card_verification', {}),
    })


# ── LIVENESS WORD (workflow mode) ─────────────────────────────────────────────

@kyc_bp.route('/word')
def kyc_word():
    """Get or refresh the liveness word for this user."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    return jsonify(get_or_refresh_word(user.id))


# ── WORKFLOW SUBMIT (video + word) ─────────────────────────────────────────────

@kyc_bp.route('/workflow/submit', methods=['POST'])
def workflow_submit():
    """Submit video verification. Creates support ticket for operator review."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    if user.kyc_status == 'verified':
        return jsonify({'error': 'Вы уже верифицированы'}), 400

    cfg    = load_cfg()
    dob    = (request.form.get('dob') or '').strip()
    word   = (request.form.get('word') or '').strip().upper()
    video  = request.files.get('video')

    if not dob:
        return jsonify({'error': 'Укажите дату рождения'}), 400

    # Age check
    from app.services.kyc_service import _calc_age
    age = _calc_age(dob)
    if age is None:
        return jsonify({'error': 'Некорректная дата (YYYY-MM-DD)'}), 400
    if age < 18:
        return jsonify({'error': f'Доступно только для лиц от 18 лет'}), 403

    # Video required for workflow
    if not video or not video.filename:
        return jsonify({'error': 'Прикрепите видео-запись'}), 400

    # Validate extension
    ext = video.filename.rsplit('.', 1)[-1].lower() if '.' in video.filename else ''
    allowed = cfg.get('allowed_video_extensions', ['mp4', 'webm', 'mov'])
    if ext not in allowed:
        return jsonify({'error': f'Допустимые форматы видео: {", ".join(allowed)}'}), 400

    # Validate size
    video.seek(0, 2); size_mb = video.tell() / (1024*1024); video.seek(0)
    if size_mb > float(cfg.get('max_file_mb', 50)):
        return jsonify({'error': f'Файл слишком большой. Максимум {cfg.get("max_file_mb",50)} МБ'}), 400

    # Save video
    upload_dir = os.path.join(_BASE_DIR, cfg.get('upload_dir', 'static/uploads/kyc'))
    os.makedirs(upload_dir, exist_ok=True)
    fname = f'kyc_video_{user.id}_{secrets.token_hex(8)}.{ext}'
    full_path = os.path.join(upload_dir, fname)
    video.save(full_path)
    video_path = f'/{cfg.get("upload_dir","static/uploads/kyc")}/{fname}'

    # Create support ticket
    ticket_id = create_kyc_ticket(user, video_path, word, dob)

    # Update user status
    user.submit_kyc(dob, video_path)

    return jsonify({
        'success':   True,
        'status':    'pending',
        'ticket_id': ticket_id,
        'message':   'Видео отправлено на проверку. Обычно занимает до 24 часов.',
    })


# ── SCAN SESSION (scan / both mode) ───────────────────────────────────────────

@kyc_bp.route('/scan/start', methods=['POST'])
def scan_start():
    """Initiate automated KYC scan with external provider."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    if user.kyc_status == 'verified':
        return jsonify({'error': 'Вы уже верифицированы'}), 400

    provider = get_active_provider()
    if not provider:
        return jsonify({'error': 'Scan provider not configured', 'fallback': 'workflow'}), 503

    result = create_scan_session(user, provider)
    if 'error' in result:
        return jsonify({'error': result['error'], 'fallback': 'workflow'}), 502

    # Mark as pending
    user.submit_kyc(user.kyc_dob or '', user.kyc_doc_path or '')
    return jsonify({**result, 'success': True})


# ── SCAN WEBHOOK CALLBACKS ─────────────────────────────────────────────────────

@kyc_bp.route('/callback/sumsub', methods=['POST'])
def sumsub_callback():
    """Sumsub webhook callback."""
    cfg    = load_cfg()
    secret = cfg.get('providers', {}).get('sumsub', {}).get('secret_key', '')
    import hmac as _hmac, hashlib as _hashlib
    if secret:
        sig  = request.headers.get('X-Payload-Digest', '')
        body = request.get_data()
        expected = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected, sig):
            return jsonify({'error': 'Invalid signature'}), 401

    data      = request.get_json() or {}
    review    = data.get('reviewResult', {})
    answer    = review.get('reviewAnswer', '')
    ext_id    = data.get('externalUserId', '')

    if ext_id.startswith('velox_'):
        try:
            uid = int(ext_id.split('_')[1])
            u   = User.get_by_id(uid)
            if u:
                if answer == 'GREEN':
                    u.set_kyc_status('verified')
                elif answer == 'RED':
                    reason = review.get('rejectLabels', [''])
                    u.set_kyc_status('rejected', str(reason))
        except Exception as e:
            print(f'[kyc/sumsub_cb] {e}')

    return jsonify({'ok': True})


@kyc_bp.route('/callback/onfido', methods=['POST'])
def onfido_callback():
    """Onfido webhook callback."""
    data        = request.get_json() or {}
    payload     = data.get('payload', {})
    resource_id = payload.get('resource_id', '')
    action      = payload.get('action', '')
    # onfido uses applicant_id — we stored as session_id; lookup by kyc_doc_path or add field
    # For now log and return OK; operator can manually verify
    print(f'[kyc/onfido_cb] action={action} resource={resource_id}')
    return jsonify({'ok': True})


@kyc_bp.route('/callback/jumio', methods=['GET', 'POST'])
def jumio_callback():
    """Jumio redirect / callback."""
    status = request.args.get('status') or (request.get_json() or {}).get('identityVerification', {}).get('validity')
    print(f'[kyc/jumio_cb] status={status}')
    return jsonify({'ok': True})


# ── MANUAL SUBMIT (document only) ─────────────────────────────────────────────

@kyc_bp.route('/manual/submit', methods=['POST'])
def manual_submit():
    """Classic document upload (manual / fallback)."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    if user.kyc_status == 'verified':
        return jsonify({'error': 'Вы уже верифицированы'}), 400

    cfg     = load_cfg()
    dob     = (request.form.get('dob') or '').strip()
    file_obj = request.files.get('doc')

    if not dob:
        return jsonify({'error': 'Укажите дату рождения'}), 400
    from app.services.kyc_service import _calc_age
    age = _calc_age(dob)
    if not age or age < 18:
        return jsonify({'error': 'Доступно только для лиц от 18 лет'}), 403

    doc_path = ''
    if file_obj and file_obj.filename:
        ext     = file_obj.filename.rsplit('.', 1)[-1].lower()
        allowed = cfg.get('allowed_doc_extensions', ['jpg','jpeg','png','pdf'])
        if ext not in allowed:
            return jsonify({'error': f'Допустимо: {", ".join(allowed)}'}), 400
        upload_dir = os.path.join(_BASE_DIR, cfg.get('upload_dir', 'static/uploads/kyc'))
        os.makedirs(upload_dir, exist_ok=True)
        fname = f'kyc_doc_{user.id}_{secrets.token_hex(8)}.{ext}'
        file_obj.save(os.path.join(upload_dir, fname))
        doc_path = f'/{cfg.get("upload_dir","static/uploads/kyc")}/{fname}'

    user.submit_kyc(dob, doc_path)
    return jsonify({'success': True, 'status': 'pending',
                    'message': 'Документы отправлены на проверку.'})


# ── CARD DEPOSIT CHECK ─────────────────────────────────────────────────────────

@kyc_bp.route('/check_deposit', methods=['POST'])
def check_deposit():
    """Check if deposit is allowed (KYC + card holder name)."""
    user = _cu()
    if not user:
        return jsonify({'error': 'Требуется авторизация'}), 401
    d = request.get_json() or {}
    result = check_card_deposit(user, float(d.get('amount', 0)),
                                d.get('card_holder', ''))
    return jsonify(result)


# ── ADMIN VERIFY / REJECT ──────────────────────────────────────────────────────

@kyc_bp.route('/admin/verify', methods=['POST'])
def admin_verify():
    from app.api.admin import _require_admin
    if not _require_admin():
        return jsonify({'error': 'Нет прав доступа'}), 403
    d      = request.get_json() or {}
    uid    = d.get('user_id')
    action = d.get('action')
    reason = d.get('reason', '')
    if not uid or action not in ('verify', 'reject'):
        return jsonify({'error': 'Неверные параметры'}), 400
    target = User.get_by_id(int(uid))
    if not target:
        return jsonify({'error': 'Пользователь не найден'}), 404
    target.set_kyc_status('verified' if action == 'verify' else 'rejected', reason)
    return jsonify({'success': True, 'status': target.kyc_status})
