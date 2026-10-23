'use strict';

// ── i18n ──────────────────────────────────────────────────────────────────────
let _lang = localStorage.getItem('support_lang') || 'EN';
let _t    = {};

async function loadLang(code) {
  try {
    const r = await fetch(`/static/langs/${code.toLowerCase()}.json`);
    if (!r.ok) throw new Error('not found');
    _t    = await r.json();
    _lang = code;
    localStorage.setItem('support_lang', code);
    applyLang();
  } catch(e) { console.warn('Lang load error', e); }
}

function t(path, fallback) {
  const parts = path.split('.');
  let v = _t;
  for (const p of parts) { v = v && v[p]; }
  return (v != null && v !== '') ? v : (fallback || path);
}
// Expose immediately so inline template scripts can use it even before loadLang resolves
window._panelT = t;

function applyLang() {
  document.querySelectorAll('[data-t]').forEach(el => {
    const val = t(el.dataset.t);
    if (val && val !== el.dataset.t) el.textContent = val;
  });
  document.querySelectorAll('[data-tp]').forEach(el => {
    const val = t(el.dataset.tp);
    if (val && val !== el.dataset.tp) el.placeholder = val;
  });
  document.querySelectorAll('.ps-lang-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.lang === _lang);
  });
  // Expose translation fn for inline template scripts
  window._panelT = t;
}

// ── Device / User-Agent detection ────────────────────────────────────────────
function detectDevice() {
  const ua = navigator.userAgent;
  let type = 'desktop';
  if (/Mobi|Android|iPhone|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua)) type = 'mobile';
  else if (/iPad|Tablet|PlayBook/i.test(ua) || (navigator.maxTouchPoints > 1 && /MacIntel/.test(navigator.platform))) type = 'tablet';
  const os = /Windows/i.test(ua) ? 'Windows'
           : /Android/i.test(ua) ? 'Android'
           : /iPhone|iPad|iPod/i.test(ua) ? 'iOS'
           : /Mac/i.test(ua) ? 'macOS'
           : /Linux/i.test(ua) ? 'Linux' : 'Unknown';
  const browser = /Edg\//i.test(ua) ? 'Edge'
                : /Chrome/i.test(ua) ? 'Chrome'
                : /Firefox/i.test(ua) ? 'Firefox'
                : /Safari/i.test(ua) ? 'Safari' : 'Other';
  document.body.setAttribute('data-device', type);
  document.body.classList.add('device-'+type);
  return { type, os, browser, ua };
}
window._device = detectDevice();

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer;
function showToast(msg, type) {
  let el = document.getElementById('toast');
  if (!el) { el = document.createElement('div'); el.id='toast'; document.body.appendChild(el); }
  el.textContent = msg;
  el.className   = 'show ' + (type || 'info');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

// ── API ───────────────────────────────────────────────────────────────────────
async function api(method, url, data) {
  const opts = { method, headers: {'Content-Type':'application/json'}, credentials:'include' };
  if (data) opts.body = JSON.stringify(data);
  const r    = await fetch(url, opts);
  const json = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, data: json };
}

async function apiForm(url, formData) {
  const r    = await fetch(url, { method:'POST', credentials:'include', body: formData });
  const json = await r.json().catch(() => ({}));
  return { ok: r.ok, data: json };
}

async function apiWithTimeout(method, url, data, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const tid = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const opts = { method, headers: {'Content-Type':'application/json'}, credentials:'include', signal: ctrl.signal };
    if (data) opts.body = JSON.stringify(data);
    const r = await fetch(url, opts);
    const json = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data: json };
  } catch (e) {
    return { ok: false, status: 0, data: { error: e?.name === 'AbortError' ? 'Request timeout' : String(e?.message || e) } };
  } finally {
    clearTimeout(tid);
  }
}

// ── OTP cooldown ──────────────────────────────────────────────────────────────
function startCooldown(btnId, hintId, seconds) {
  const btn  = document.getElementById(btnId);
  const hint = document.getElementById(hintId);
  if (!btn) return;
  let s = seconds;
  btn.disabled = true; btn.textContent = s + 's';
  const iv = setInterval(() => {
    s--;
    if (s > 0) { btn.textContent = s + 's'; }
    else {
      clearInterval(iv); btn.disabled = false;
      btn.textContent = t('settings.email_send_otp','Resend');
      if (hint) hint.textContent = '';
    }
  }, 1000);
  if (hint) hint.textContent = 'Code sent — check your inbox.';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}
function fmtDate(s) {
  if (!s) return '—';
  try {
    const d = new Date(s);
    return d.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit',year:'2-digit'})+
           ' '+d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
  } catch { return s; }
}
function statusLabel(s) { return t('status.'+s, s); }
function kycLabel(s)    { return t('kyc.'+(s||'none'), s||'—'); }
function setEl(id, val) { const e=document.getElementById(id); if(e) e.textContent=val; }
function setMsg(id, msg, type) {
  const e = document.getElementById(id);
  if (!e) return;
  e.textContent  = msg;
  e.classList.remove('hidden');
  e.className    = type === 'ok' ? 'auth-success' : 'auth-error';
}
function hideMsg(id) { document.getElementById(id)?.classList.add('hidden'); }

function updatePagination(page, pages, cb) {
  const el = document.getElementById('pagination');
  if (!el) return;
  if (!pages || pages <= 1) { el.innerHTML = ''; return; }
  el.innerHTML =
    `<button class="pg-btn" ${page<=1?'disabled':''} id="pg-prev">←</button>
     <span class="pg-info">${t('common.page','Page')} ${page} ${t('common.of','of')} ${pages}</span>
     <button class="pg-btn" ${page>=pages?'disabled':''} id="pg-next">→</button>`;
  document.getElementById('pg-prev')?.addEventListener('click', () => cb(page-1));
  document.getElementById('pg-next')?.addEventListener('click', () => cb(page+1));
}

function roleTag(role) {
  return `<span class="role-tag role-${role||'helper'}">${(role||'helper').toUpperCase()}</span>`;
}

// ── Auth state ────────────────────────────────────────────────────────────────
let _admin = null;

async function checkMe() {
  const { ok, data } = await api('GET', '/api/auth/me');
  if (ok) { _admin = data.admin; return true; }
  return false;
}

// ── Router ────────────────────────────────────────────────────────────────────
const PAGE = document.body.dataset.page || 'auth';

document.addEventListener('DOMContentLoaded', async () => {
  await loadLang(_lang);

  if (PAGE === 'auth') { initAuthPage(); return; }

  const authed = await checkMe();
  if (!authed) { location.href = '/'; return; }

  // Грузим права доступа ДО отрисовки UI
  await loadPerms();

  updateAdminUI();
  initSidebarMenu();

  const pages = {
    dashboard:        initDashboard,
    tickets:          initTickets,
    'ticket-detail':  initTicketDetail,
    users:            initUsers,
    'user-detail':    initUserDetail,
    settings:         initSettings,
    reports:          initReports,
    chat:             initChat,
    soc:              initSoc,
    analytics:        initAnalytics,
    payments_queue:   initPaymentsQueue,
    team:             initTeam,
    bans:             initBans,
    broadcast:        initBroadcast,
    game_builder:     initGameBuilder,
    banners:          initBanners,
    campaigns:        initCampaigns,
    site:             initSite,
    tournament_builder: initTournamentBuilder,
  };
  pages[PAGE]?.();
});

// ── Admin UI ──────────────────────────────────────────────────────────────────
function updateAdminUI() {
  if (!_admin) return;
  document.querySelectorAll('.admin-name').forEach(e => e.textContent = _admin.nickname);
  document.querySelectorAll('.admin-public-id').forEach(e => {
    e.textContent = _admin.public_id || '';
    e.style.cssText = 'font-size:10px;color:var(--t3);font-family:var(--ffd);letter-spacing:1px';
  });
  applyNavPerms();
}

// ── Mobile sidebar toggle ─────────────────────────────────────────────────────
function toggleSidebar() {
  const sidebar = document.getElementById('panel-sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  if (!sidebar) return;
  const isOpen = sidebar.classList.toggle('open');
  if (overlay) overlay.classList.toggle('hidden', !isOpen);
}

function initSidebarMenu() {
  // Create mobile overlay if absent
  if (!document.getElementById('sidebar-overlay')) {
    const ov = document.createElement('div');
    ov.id = 'sidebar-overlay';
    ov.className = 'sidebar-overlay hidden';
    ov.addEventListener('click', () => {
      document.getElementById('panel-sidebar')?.classList.remove('open');
      ov.classList.add('hidden');
    });
    document.body.appendChild(ov);
  }
  // Nav item clicks — bind once, combine navigation + mobile-close
  document.querySelectorAll('.ps-item[data-href]').forEach(el => {
    if (el._navBound) return;
    el._navBound = true;
    el.addEventListener('click', () => {
      document.getElementById('panel-sidebar')?.classList.remove('open');
      document.getElementById('sidebar-overlay')?.classList.add('hidden');
      location.href = el.dataset.href;
    });
  });
  // Ping device info
  if (window._device) {
    api('POST', '/api/admin/device-info', window._device).catch(() => {});
  }
  loadSupportSidebarLogo().catch(() => {});
  const btn  = document.getElementById('ps-profile-btn');
  const menu = document.getElementById('ps-profile-menu');
  const arr  = btn?.querySelector('.ps-profile-arr');

  btn?.addEventListener('click', () => {
    const isOpen = menu?.classList.toggle('hidden') === false;
    arr?.classList.toggle('open', !menu.classList.contains('hidden'));
  });

  document.getElementById('ps-lang-toggle')?.addEventListener('click', () => {
    document.getElementById('ps-lang-sub')?.classList.toggle('hidden');
  });

  document.querySelectorAll('.ps-lang-btn').forEach(b => {
    b.addEventListener('click', () => {
      loadLang(b.dataset.lang);
      document.querySelectorAll('.ps-lang-btn').forEach(x => x.classList.toggle('active', x===b));
    });
  });

  document.addEventListener('click', e => {
    if (menu && !menu.classList.contains('hidden') &&
        !btn?.contains(e.target) && !menu.contains(e.target)) {
      menu.classList.add('hidden');
      arr?.classList.remove('open');
    }
  });

  document.getElementById('logout-btn')?.addEventListener('click', async () => {
    await api('POST', '/api/auth/logout');
    location.href = '/';
  });

  loadNavBadges();
}

async function loadSupportSidebarLogo() {
  const img = document.getElementById('ps-dynamic-logo');
  const fallback = document.getElementById('ps-logo-fallback');
  if (!img || !fallback) return;
  const { ok, data } = await _siteGet('site');
  if (!ok) return;
  const logo = data?.config?.sidebar_uploads?.logo_image || '';
  if (!logo) return;
  const src = logo.startsWith('/static/')
    ? `/api/admin/banners/casino-image?path=${encodeURIComponent(logo.replace(/^\/static\//, ''))}`
    : logo;
  img.src = src;
  img.classList.remove('hidden');
  fallback.classList.add('hidden');
}

async function loadNavBadges() {
  const { ok, data } = await api('GET', '/api/tickets/stats');
  if (!ok) return;
  const badge = document.getElementById('badge-open-tickets');
  if (badge && data.tickets?.open > 0) {
    badge.textContent = data.tickets.open;
    badge.classList.remove('hidden');
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  AUTH PAGE
// ══════════════════════════════════════════════════════════════════════════════
function initAuthPage() {
  let _key = sessionStorage.getItem('admin_key') || '';

  function showErr(msg) {
    const e = document.getElementById('err-form');
    if (e) { e.textContent = msg; e.classList.remove('hidden'); }
    document.getElementById('ok-form')?.classList.add('hidden');
  }
  function showOk(msg) {
    const e = document.getElementById('ok-form');
    if (e) { e.textContent = msg; e.classList.remove('hidden'); }
    document.getElementById('err-form')?.classList.add('hidden');
  }
  function hideAll() {
    document.getElementById('err-form')?.classList.add('hidden');
    document.getElementById('ok-form')?.classList.add('hidden');
  }

  document.getElementById('key-input')?.addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('key-btn')?.click();
  });

  document.getElementById('key-btn')?.addEventListener('click', async function() {
    const key = document.getElementById('key-input')?.value.trim();
    if (!key) return;
    this.disabled = true; this.textContent = '...';
    const errEl = document.getElementById('err-key');
    errEl?.classList.add('hidden');
    const res = await api('POST', '/api/auth/check-key', { key });
    this.disabled = false; this.textContent = 'Continue';
    if (!res.ok) {
      if (errEl) { errEl.textContent = res.data.error||'Invalid key'; errEl.classList.remove('hidden'); }
      return;
    }
    _key = key;
    sessionStorage.setItem('admin_key', key);
    document.getElementById('stage-key')?.classList.add('hidden');
    document.getElementById('stage-form')?.classList.remove('hidden');
    res.data.has_admins ? showLoginForm() : showRegisterForm();
  });

  function showLoginForm()    { document.getElementById('form-register')?.classList.add('hidden'); document.getElementById('form-login')?.classList.remove('hidden'); hideAll(); }
  function showRegisterForm() { document.getElementById('form-login')?.classList.add('hidden'); document.getElementById('form-register')?.classList.remove('hidden'); hideAll(); }
  document.getElementById('link-to-login')?.addEventListener('click', e => { e.preventDefault(); showLoginForm(); });
  document.getElementById('link-to-register')?.addEventListener('click', e => { e.preventDefault(); showRegisterForm(); });

  let _otpTimer;
  document.getElementById('reg-email')?.addEventListener('input', function() {
    clearTimeout(_otpTimer);
    const email = this.value.trim().toLowerCase();
    if (!email.includes('@')) return;
    _otpTimer = setTimeout(async () => {
      const res = await api('POST', '/api/auth/otp_remaining', { email });
      if (res.ok && res.data.remaining > 0) startCooldown('otp-send-btn','otp-hint',res.data.remaining);
    }, 600);
  });

  document.getElementById('otp-send-btn')?.addEventListener('click', async function() {
    const email = document.getElementById('reg-email')?.value.trim().toLowerCase();
    if (!email || !email.includes('@')) { showErr('Please enter a valid email first'); return; }
    hideAll();
    const alreadySent = !!this.dataset.sent;
    const ep = alreadySent ? '/api/auth/register/resend_otp' : '/api/auth/register/send_otp';
    this.disabled = true; this.textContent = '...';
    const res = await api('POST', ep, { email, key: _key });
    if (!res.ok) {
      this.disabled = false; this.textContent = alreadySent ? 'Resend' : 'Get code';
      if (res.data.remaining) startCooldown('otp-send-btn','otp-hint',res.data.remaining);
      else showErr(res.data.error||'Failed to send');
      return;
    }
    this.dataset.sent = '1';
    showOk('Code sent to '+(res.data.masked||email));
    startCooldown('otp-send-btn','otp-hint',res.data.ttl||60);
  });

  document.getElementById('register-btn')?.addEventListener('click', async function() {
    const email    = document.getElementById('reg-email')?.value.trim().toLowerCase();
    const otp      = document.getElementById('reg-otp')?.value.trim();
    const password = document.getElementById('reg-password')?.value.trim();
    const nickname = document.getElementById('reg-nickname')?.value.trim();
    hideAll();
    if (!email)              { showErr('Email is required'); return; }
    if (!otp)                { showErr('Verification code is required'); return; }
    if (!password)           { showErr('Password is required'); return; }
    if (password.length < 8) { showErr('Password must be at least 8 characters'); return; }
    this.disabled = true; this.textContent = 'Registering...';
    const res = await api('POST', '/api/auth/register', { key:_key, email, otp, password, nickname });
    this.disabled = false; this.textContent = 'Register';
    if (!res.ok) { showErr(res.data.error||'Registration failed'); return; }
    location.href = '/panel';
  });

  const doLogin = async () => {
    const email    = document.getElementById('login-email')?.value.trim().toLowerCase();
    const password = document.getElementById('login-password')?.value.trim();
    hideAll();
    if (!email||!password) { showErr('Please fill in all fields'); return; }
    const btn = document.getElementById('login-btn');
    btn.disabled = true; btn.textContent = 'Signing in...';
    const res = await api('POST', '/api/auth/login', { key:_key, email, password });
    btn.disabled = false; btn.textContent = 'Sign in';
    if (!res.ok) { showErr(res.data.error||'Incorrect email or password'); return; }
    location.href = '/panel';
  };
  document.getElementById('login-btn')?.addEventListener('click', doLogin);
  document.getElementById('login-password')?.addEventListener('keydown', e => { if(e.key==='Enter') doLogin(); });
}

// ══════════════════════════════════════════════════════════════════════════════
//  DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════
async function initDashboard() {
  const { ok, data } = await api('GET', '/api/tickets/stats');
  if (!ok) return;
  setEl('stat-open',     data.tickets?.open     || 0);
  setEl('stat-progress', data.tickets?.in_progress || 0);
  setEl('stat-closed',   data.tickets?.closed   || 0);
  setEl('stat-users',    data.users             || 0);
  setEl('stat-payments', data.payments_pending  || 0);
  setEl('stat-kyc',      data.kyc_pending       || 0);

  const { ok:ok2, data:d2 } = await api('GET', '/api/tickets/?status=open&page=1');
  const tbody = document.getElementById('recent-tickets-body');
  if (!tbody || !ok2) return;
  if (!d2.tickets?.length) {
    tbody.innerHTML = `<tr><td colspan="5" class="empty-state">${t('tickets.no_tickets','No tickets')}</td></tr>`;
    return;
  }
  tbody.innerHTML = d2.tickets.slice(0,8).map(tk => `
    <tr>
      <td class="td-muted">${tk.ticket_uuid ? tk.ticket_uuid : '#'+tk.id}</td>
      <td>${esc(tk.subject||'—')}</td>
      <td class="td-muted">${esc(tk.contact||'—')}</td>
      <td><span class="badge badge-${tk.status==='open'?'open':tk.status==='in_progress'?'progress':'closed'}">${statusLabel(tk.status)}</span></td>
      <td><button class="btn-icon" onclick="location.href='/panel/ticket/${tk.id}'">${t('tickets.open_btn','Open')}</button></td>
    </tr>`).join('');
}

// ══════════════════════════════════════════════════════════════════════════════
//  TICKETS LIST
// ══════════════════════════════════════════════════════════════════════════════
let _tPage=1, _tStatus='';
async function initTickets() {
  document.querySelectorAll('.tbl-filter-btn[data-status]').forEach(btn => {
    btn.addEventListener('click', () => {
      _tStatus = btn.dataset.status; _tPage = 1;
      document.querySelectorAll('.tbl-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadTickets();
    });
  });
  loadTickets();
}

async function loadTickets() {
  const tbody = document.getElementById('tickets-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" class="loading-center"><span class="spinner"></span></td></tr>';
  const { ok, data } = await api('GET', `/api/tickets/?page=${_tPage}${_tStatus?'&status='+_tStatus:''}`);
  if (!ok) { tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--t3)">${t('common.error')}</td></tr>`; return; }
  if (!data.tickets?.length) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state"><div class="empty-state-icon">📭</div>${t('tickets.no_tickets')}</div></td></tr>`;
  } else {
    tbody.innerHTML = data.tickets.map(tk => `
      <tr>
        <td class="td-muted">${tk.ticket_uuid ? tk.ticket_uuid : '#'+tk.id}</td>
        <td>${esc(tk.subject||'—')}</td>
        <td class="td-muted">${esc(tk.contact||'—')}</td>
        <td class="td-muted">${esc(tk.user_name||'—')}</td>
        <td>${tk.assigned_to_nickname ? `<span style="font-size:11px;color:var(--a)">${esc(tk.assigned_to_nickname)}</span>` : '<span class="td-muted">—</span>'}</td>
        <td><span class="badge badge-${tk.status==='open'?'open':tk.status==='in_progress'?'progress':'closed'}">${statusLabel(tk.status)}</span></td>
        <td class="td-muted">${fmtDate(tk.created_at)}</td>
        <td><button class="btn-icon" onclick="location.href='/panel/ticket/${tk.id}'">${t('tickets.open_btn','Open')}</button></td>
      </tr>`).join('');
  }
  updatePagination(data.page, data.pages, pg => { _tPage=pg; loadTickets(); });
}

// ══════════════════════════════════════════════════════════════════════════════
//  TICKET DETAIL
// ══════════════════════════════════════════════════════════════════════════════
async function initTicketDetail() {
  const tid = document.body.dataset.ticketId;
  if (!tid) return;

  const { ok, data } = await api('GET', `/api/tickets/${tid}`);
  if (!ok) { showToast(t('common.error'), 'error'); return; }

  const { ticket, messages, my_id } = data;

  setEl('ticket-id', ticket.ticket_uuid || `#${ticket.id}`);
  setEl('ticket-subject', ticket.subject||'—');
  setEl('ticket-contact', ticket.contact||'—');
  setEl('ticket-desc',    ticket.description||'—');
  setEl('ticket-date',    fmtDate(ticket.created_at));

  const sb = document.getElementById('ticket-status-badge');
  if (sb) {
    const cls = ticket.status==='open'?'open':ticket.status==='in_progress'?'progress':'closed';
    sb.className = `badge badge-${cls}`;
    sb.textContent = statusLabel(ticket.status);
  }

  // User card
  if (ticket.user_name) {
    setEl('user-name',       ticket.user_name);
    setEl('user-email',      ticket.user_email||'—');
    setEl('user-balance',    ticket.balance!=null ? `${ticket.balance} ${ticket.currency||''}` : '—');
    setEl('user-kyc-status', kycLabel(ticket.kyc_status));
    document.getElementById('user-card')?.classList.remove('hidden');
    document.getElementById('user-link')?.setAttribute('href', `/panel/user/${ticket.user_id}`);
  }

  // Assigned operator banner + card
  const assignedId  = ticket.assigned_to_id;
  const isClosed    = ticket.status === 'closed';

  if (assignedId && !isClosed) {
    const banner = document.getElementById('assigned-banner');
    if (banner) { banner.classList.remove('hidden'); banner.style.display='flex'; }
    // setEl('assigned-nickname',  ticket.assigned_to_nickname  || '—');
    // setEl('assigned-public-id', ticket.assigned_to_public_id || '');
    setEl('op-nickname',        ticket.assigned_to_nickname  || '—');
    setEl('op-public-id',       ticket.assigned_to_public_id || '—');
    document.getElementById('operator-card')?.classList.remove('hidden');
  }

  // KYC
  if (ticket.is_kyc) {
    document.getElementById('kyc-badge')?.classList.remove('hidden');
    if (ticket.media_url) {
      document.getElementById('kyc-media-block')?.classList.remove('hidden');
      const loading = document.getElementById('kyc-media-loading');
      if (ticket.media_type === 'video') {
        const vid = document.getElementById('kyc-video');
        if (vid) { vid.src=ticket.media_url; vid.style.display='block';
          vid.onloadeddata = () => { if(loading) loading.style.display='none'; };
          vid.onerror      = () => { if(loading) loading.innerHTML='<span style="color:var(--t3)">Video unavailable</span>'; }; }
      } else if (ticket.media_type === 'image') {
        const img = document.getElementById('kyc-image');
        if (img) { img.src=ticket.media_url; img.style.display='block';
          img.onload  = () => { if(loading) loading.style.display='none'; };
          img.onerror = () => { if(loading) loading.innerHTML='<span style="color:var(--t3)">Image unavailable</span>'; }; }
      } else if (ticket.media_type === 'pdf') {
        const lnk = document.getElementById('kyc-pdf');
        if (lnk) { lnk.href=ticket.media_url; lnk.style.display='block'; if(loading) loading.style.display='none'; }
      } else {
        if (loading) loading.innerHTML='<span style="color:var(--t3)">Media unavailable</span>';
      }
    }
  }

  // Action buttons
  renderActionButtons(ticket, my_id, tid);

  // Translate
  document.getElementById('translate-btn')?.addEventListener('click', async () => {
    const btn = document.getElementById('translate-btn');
    const desc = document.getElementById('ticket-desc')?.textContent || '';
    const out  = document.getElementById('ticket-desc-translated');
    if (!desc || !out) return;
    if (!out.classList.contains('hidden') && out.textContent) { out.classList.toggle('hidden'); return; }
    btn.disabled = true; btn.textContent = '…';
    const res = await api('POST', '/api/tickets/translate', { text:desc, target:_lang.toLowerCase() });
    btn.disabled = false;
    btn.innerHTML = '🌐 '+(t('common.translate')||'Translate');
    if (res.ok && res.data.translated && res.data.translated !== desc) {
      out.textContent = res.data.translated; out.classList.remove('hidden');
    } else { showToast('Translation unavailable or already in your language', 'info'); }
  });

  // Reply lock
  const canReply = !isClosed && assignedId && (assignedId == my_id);
  const replyInput = document.getElementById('reply-input');
  const replySend  = document.getElementById('reply-send-btn');
  const replyBox   = document.querySelector('.reply-box');

  if (!canReply) {
    if (replyInput) { replyInput.disabled=true; replyInput.style.opacity='.35'; replyInput.style.cursor='not-allowed'; }
    if (replySend)  { replySend.disabled=true;  replySend.style.opacity='.35';  replySend.style.cursor='not-allowed'; }
    if (replyBox) {
      let lockMsg = isClosed
        ? t('tickets.chat_closed','This ticket is closed')
        : !assignedId
          ? t('tickets.chat_not_assigned','Take this ticket to start chatting')
          : t('tickets.chat_locked','Chat is only available to the assigned operator');
      const lockEl = document.createElement('div');
      lockEl.style.cssText = 'padding:7px 14px;font-size:11px;color:var(--t3);text-align:center;border-top:1px solid var(--bdr)';
      lockEl.textContent = '🔒 ' + lockMsg;
      replyBox.appendChild(lockEl);
    }
  } else {
    replySend?.addEventListener('click', async () => {
      const ta  = document.getElementById('reply-input');
      const msg = ta?.value.trim();
      if (!msg) return;
      const res = await api('POST', `/api/tickets/${tid}/reply`, { message: msg });
      if (!res.ok) { showToast(res.data.error||t('common.error'), 'error'); return; }
      if (ta) ta.value = '';
      await refreshMessages(tid);
    });
    replyInput?.addEventListener('keydown', e => {
      if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); replySend?.click(); }
    });
  }

  // Decline panel wiring
  document.getElementById('btn-kyc-decline-cancel')?.addEventListener('click', () => {
    document.getElementById('decline-panel')?.classList.add('hidden');
    document.querySelector('.action-btn-close.decline-toggle')?.classList.remove('hidden');
  });
  document.getElementById('btn-kyc-decline-confirm')?.addEventListener('click', async () => {
    const reason = document.getElementById('decline-reason')?.value.trim()||'';
    const btn = document.getElementById('btn-kyc-decline-confirm');
    btn.disabled=true; btn.textContent='…';
    const res = await api('POST', `/api/tickets/${tid}/kyc/decline`, { reason });
    if (res.ok) { showToast('❌ KYC declined','success'); setTimeout(()=>location.reload(),700); }
    else { btn.disabled=false; btn.textContent='Confirm Decline'; showToast(res.data.error||'Error','error'); }
  });

  renderMessages(messages);

  // Display user attachments
  const attBlock = document.getElementById('attachments-block');
  const attList  = document.getElementById('attachments-list');
  const attachments = data.attachments || [];
  if (attBlock && attList && attachments.length) {
    attBlock.classList.remove('hidden');
    attList.innerHTML = attachments.map(url =>
      `<a href="${url}" target="_blank" style="display:block">
        <img src="${url}" style="max-width:180px;max-height:140px;border-radius:var(--r);
             border:1px solid var(--bdr);object-fit:cover;cursor:pointer" 
             loading="lazy" onerror="this.parentElement.style.display='none'"/>
       </a>`
    ).join('');
  }

  // Polling: update messages every 5 seconds
  if (window._ticketMsgPoller) clearInterval(window._ticketMsgPoller);
  window._ticketMsgPoller = setInterval(() => refreshMessages(tid), 5000);
  window.addEventListener('beforeunload', () => {
    if (window._ticketMsgPoller) clearInterval(window._ticketMsgPoller);
  }, { once: true });
}

// Lightweight messages-only refresh
async function refreshMessages(tid) {
  const r = await api('GET', `/api/tickets/${tid}/messages`);
  if (r.ok && r.data.messages) renderMessages(r.data.messages);
}

function renderActionButtons(ticket, my_id, tid) {
  const wrap = document.getElementById('action-buttons');
  if (!wrap) return;

  const { status, assigned_to_id: aId, is_kyc: isKyc, kyc_status } = ticket;
  const isMine   = aId && aId == my_id;
  const isClosed = status === 'closed';

  wrap.innerHTML = '';

  if (isClosed) {
    wrap.innerHTML = `<div style="color:var(--t3);font-size:12px;padding:8px 0">Ticket is closed</div>`;
    return;
  }

  if (status === 'open') {
    const btn = _mkBtn('action-btn-progress','🔔', t('tickets.mark_progress','Set In Progress'));
    btn.addEventListener('click', async () => {
      btn.disabled=true; btn.textContent='…';
      const res = await api('POST', `/api/tickets/${tid}/assign`);
      if (res.ok) { showToast('Ticket taken','success'); setTimeout(()=>location.reload(),600); }
      else { btn.disabled=false; btn.textContent=t('tickets.mark_progress','Set In Progress'); showToast(res.data.error||t('common.error'),'error'); }
    });
    wrap.appendChild(btn);
    return;
  }

  if (status === 'in_progress') {
    if (isMine) {
      // KYC pending: Verify + Decline
      if (isKyc && kyc_status === 'pending') {
        const vBtn = _mkBtn('action-btn-kyc-verify','✅', t('tickets.kyc_verify','Verify'));
        vBtn.addEventListener('click', async () => {
          vBtn.disabled=true; vBtn.textContent='…';
          const res = await api('POST', `/api/tickets/${tid}/kyc/verify`);
          if (res.ok) { showToast('✅ KYC verified','success'); setTimeout(()=>location.reload(),700); }
          else { vBtn.disabled=false; vBtn.textContent=t('tickets.kyc_verify','Verify'); showToast(res.data.error||t('common.error'),'error'); }
        });
        wrap.appendChild(vBtn);

        const dBtn = _mkBtn('action-btn-close decline-toggle','✕', t('tickets.kyc_decline','Decline'));
        dBtn.addEventListener('click', () => {
          document.getElementById('decline-panel')?.classList.remove('hidden');
          dBtn.classList.add('hidden');
        });
        wrap.appendChild(dBtn);
      } else {
        // Regular: close ticket
        const closeBtn = _mkBtn('action-btn-close','✔', t('tickets.mark_closed','Close ticket'));
        closeBtn.addEventListener('click', async () => {
          closeBtn.disabled=true;
          const res = await api('PUT', `/api/tickets/${tid}/status`, { status:'closed' });
          if (res.ok) { showToast(t('common.success'),'success'); setTimeout(()=>location.reload(),600); }
          else { closeBtn.disabled=false; showToast(t('common.error'),'error'); }
        });
        wrap.appendChild(closeBtn);
      }

      // Unpin
      const unpin = _mkBtn('action-btn-unpin','↩', t('tickets.unpin','Unpin me'));
      unpin.addEventListener('click', async () => {
        unpin.disabled=true;
        const res = await api('POST', `/api/tickets/${tid}/unpin`);
        if (res.ok) { showToast('Released','success'); setTimeout(()=>location.reload(),600); }
        else { unpin.disabled=false; showToast(res.data.error||t('common.error'),'error'); }
      });
      wrap.appendChild(unpin);

    } else if (aId) {
      // Someone else's
      const info = document.createElement('div');
      info.style.cssText = 'font-size:12px;color:var(--t3);padding:4px 0 10px';
      info.textContent = t('tickets.taken_by_other','Handled by another operator');
      wrap.appendChild(info);
      const locked = _mkBtn('action-btn-progress','🔒', t('tickets.mark_progress','Set In Progress'));
      locked.disabled=true; locked.style.opacity='.35'; locked.style.cursor='not-allowed';
      wrap.appendChild(locked);
    } else {
      // in_progress but no assigned — allow taking
      const btn = _mkBtn('action-btn-progress','🔔', t('tickets.mark_progress','Set In Progress'));
      btn.addEventListener('click', async () => {
        btn.disabled=true;
        const res = await api('POST', `/api/tickets/${tid}/assign`);
        if (res.ok) { showToast('Ticket taken','success'); setTimeout(()=>location.reload(),600); }
        else { btn.disabled=false; showToast(res.data.error||t('common.error'),'error'); }
      });
      wrap.appendChild(btn);
    }
  }
}

function _mkBtn(cls, icon, label) {
  const btn = document.createElement('button');
  btn.className = `action-btn ${cls}`;
  btn.innerHTML = `<span style="margin-right:4px">${icon}</span>${label}`;
  return btn;
}

function renderMessages(messages) {
  const box = document.getElementById('messages-list');
  if (!box) return;
  if (!messages?.length) {
    box.innerHTML = `<div class="empty-state" style="padding:24px">${t('tickets.messages_empty','No messages')}</div>`;
    return;
  }
  // Deduplicate by id to prevent doubles during polling
  const seen = new Set();
  const unique = messages.filter(m => {
    const key = m.id != null ? String(m.id) : (m.created_at + m.message);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  box.innerHTML = unique.map(m => {
    if (m.sender === 'system') {
      return `<div style="text-align:center;padding:5px 0">
        <span style="font-size:11px;color:var(--t3);background:var(--bg2);
                     padding:3px 10px;border-radius:99px;border:1px solid var(--bdr)">
          ${esc(m.message)}
        </span></div>`;
    }
    return `<div class="msg ${m.sender==='admin'?'admin':'user'}">
      <div class="msg-bubble">${esc(m.message)}</div>
      <div class="msg-meta">${m.sender==='admin'?'Admin':'User'} · ${fmtDate(m.created_at)}</div>
    </div>`;
  }).join('');
  if (atBottom) box.scrollTop = box.scrollHeight;
}

// ══════════════════════════════════════════════════════════════════════════════
//  USERS LIST
// ══════════════════════════════════════════════════════════════════════════════
let _uPage=1, _uQuery='', _uTimer;
async function initUsers() {
  document.getElementById('users-search')?.addEventListener('input', function() {
    clearTimeout(_uTimer);
    _uTimer = setTimeout(() => { _uQuery=this.value.trim(); _uPage=1; loadUsers(); }, 350);
  });
  loadUsers();
}
async function loadUsers() {
  const tbody = document.getElementById('users-body');
  if (!tbody) return;
  tbody.innerHTML='<tr><td colspan="7" class="loading-center"><span class="spinner"></span></td></tr>';
  const { ok, data } = await api('GET', `/api/tickets/users?page=${_uPage}${_uQuery?'&q='+encodeURIComponent(_uQuery):''}`);
  if (!ok) { tbody.innerHTML=`<tr><td colspan="7" style="text-align:center;padding:24px;color:var(--t3)">${t('common.error')}</td></tr>`; return; }
  if (!data.users?.length) {
    tbody.innerHTML=`<tr><td colspan="7"><div class="empty-state"><div class="empty-state-icon">👤</div>${t('users.no_users')}</div></td></tr>`;
  } else {
    tbody.innerHTML=data.users.map(u => `
      <tr>
        <td class="td-muted">${u.id}</td>
        <td>${esc(u.user_name||'—')}</td>
        <td class="td-muted">${esc(u.email||'—')}</td>
        <td>${u.balance!=null?`${u.balance} ${u.currency||''}`:'—'}</td>
        <td><span class="badge badge-${u.kyc_status||'none'}">${kycLabel(u.kyc_status)}</span></td>
        <td class="td-muted">${fmtDate(u.created_at)}</td>
        <td><button class="btn-icon" onclick="location.href='/panel/user/${u.id}'">${t('users.view_btn','View')}</button></td>
      </tr>`).join('');
  }
  updatePagination(_uPage, Math.ceil((data.total||0)/20), pg => { _uPage=pg; loadUsers(); });
}

// ══════════════════════════════════════════════════════════════════════════════
//  USER DETAIL
// ══════════════════════════════════════════════════════════════════════════════
async function initUserDetail() {
  const uid = document.body.dataset.userId;
  if (!uid) return;
  const { ok, data } = await api('GET', `/api/tickets/users/${uid}`);
  if (!ok) { showToast(t('common.error'),'error'); return; }
  const { user, transactions, tickets, game_history: gameHistory } = data;
  setEl('u-name',    user.user_name||'—');
  setEl('u-email',   user.email||'—');
  setEl('u-phone',   user.phone||'—');
  setEl('u-balance', `${user.balance??0} ${user.currency||''}`);
  setEl('u-kyc',     kycLabel(user.kyc_status));
  setEl('u-verified',user.verified ? t('common.yes','Yes') : t('common.no','No'));
  setEl('u-created', fmtDate(user.created_at));
  setEl('u-id',      `#${user.id}`);

  // ── Topup / balance adjustment ──────────────────────────────────────────
  if (_admin && ['admin','ceo'].includes(_admin.role)) {
    const topupCard = document.getElementById('topup-card');
    if (topupCard) topupCard.classList.remove('hidden');
  }

  document.getElementById('topup-btn')?.addEventListener('click', async function () {
    const amountInput = document.getElementById('topup-amount');
    const reasonInput = document.getElementById('topup-reason');
    const amount = parseFloat(amountInput?.value);
    if (!amount || isNaN(amount) || amount === 0) {
      showToast(t('users.topup_err_amount', 'Enter a valid amount'), 'error');
      return;
    }
    this.disabled = true;
    const { ok: okT, data: dT } = await api('POST', `/api/admin/users/${uid}/topup`, {
      amount,
      reason: reasonInput?.value.trim() || '',
    });
    this.disabled = false;
    if (!okT) {
      showToast(dT?.error || t('common.error'), 'error');
      return;
    }
    const newBal = `${dT.new_balance} ${dT.currency||''}`;
    setEl('u-balance', newBal);
    if (amountInput) amountInput.value = '';
    if (reasonInput) reasonInput.value = '';
    showToast(
      t('users.topup_success', 'Balance updated').replace('%b', newBal),
      'success'
    );
    // Refresh transaction list
    const { ok: ok2, data: d2 } = await api('GET', `/api/tickets/users/${uid}`);
    if (ok2 && d2.transactions) {
      const txBody = document.getElementById('tx-body');
      if (txBody) txBody.innerHTML = d2.transactions.length
        ? d2.transactions.map(tx => `<tr>
            <td class="td-muted">${fmtDate(tx.created_at)}</td>
            <td>${esc(tx.type)}</td>
            <td style="color:${tx.amount>=0?'var(--green)':'var(--red)'}">${tx.amount>0?'+':''}${tx.amount}</td>
            <td><span class="badge badge-${tx.status==='completed'?'verified':'pending'}">${tx.status}</span></td>
          </tr>`).join('')
        : `<tr><td colspan="4" class="td-muted" style="text-align:center;padding:20px">${t('users.no_transactions')}</td></tr>`;
    }
  });

  const txBody = document.getElementById('tx-body');
  if (txBody) txBody.innerHTML = transactions?.length
    ? transactions.map(tx => `<tr>
        <td class="td-muted">${fmtDate(tx.created_at)}</td>
        <td>${esc(tx.type)}</td>
        <td style="color:${tx.amount>=0?'var(--green)':'var(--red)'}">${tx.amount>0?'+':''}${tx.amount}</td>
        <td><span class="badge badge-${tx.status==='completed'?'verified':'pending'}">${tx.status}</span></td>
      </tr>`).join('')
    : `<tr><td colspan="4" class="td-muted" style="text-align:center;padding:20px">${t('users.no_transactions')}</td></tr>`;

  const tkBody = document.getElementById('user-tickets-body');
  if (tkBody) tkBody.innerHTML = tickets?.length
    ? tickets.map(tk => `<tr>
        <td class="td-muted">${tk.ticket_uuid ? tk.ticket_uuid : '#'+tk.id}</td>
        <td>${esc(tk.subject||'—')}</td>
        <td><span class="badge badge-${tk.status==='open'?'open':tk.status==='in_progress'?'progress':'closed'}">${statusLabel(tk.status)}</span></td>
        <td><button class="btn-icon" onclick="location.href='/panel/ticket/${tk.id}'">${t('tickets.open_btn','Open')}</button></td>
      </tr>`).join('')
    : `<tr><td colspan="4" class="td-muted" style="text-align:center;padding:20px">${t('users.no_player_tickets')}</td></tr>`;
}

// ══════════════════════════════════════════════════════════════════════════════
//  SETTINGS
// ══════════════════════════════════════════════════════════════════════════════
async function initSettings() {
  if (!_admin) return;
  setEl('s-nickname',  _admin.nickname);
  setEl('s-public-id', _admin.public_id||'—');
  const nickInput = document.getElementById('s-nick-input');
  if (nickInput) nickInput.value = _admin.nickname;

  document.getElementById('nick-save-btn')?.addEventListener('click', async function() {
    const nick = document.getElementById('s-nick-input')?.value.trim();
    hideMsg('nick-err'); hideMsg('nick-ok');
    if (!nick||nick.length<2) { setMsg('nick-err','Nickname must be at least 2 characters','err'); return; }
    this.disabled=true;
    const res = await api('PUT', '/api/auth/settings/nickname', { nickname:nick });
    this.disabled=false;
    if (!res.ok) { setMsg('nick-err',res.data.error||t('common.error'),'err'); return; }
    _admin = res.data.admin; updateAdminUI(); setEl('s-nickname',_admin.nickname);
    setMsg('nick-ok',t('common.success'),'ok');
  });

  let _emailOtpSent=false;
  document.getElementById('email-otp-btn')?.addEventListener('click', async function() {
    const email = document.getElementById('s-email-input')?.value.trim().toLowerCase();
    hideMsg('email-err'); hideMsg('email-ok');
    if (!email||!email.includes('@')) { setMsg('email-err','Invalid email','err'); return; }
    this.disabled=true; this.textContent='...';
    const res = await api('POST', '/api/auth/settings/email/send_otp', { email });
    if (!res.ok) {
      this.disabled=false; this.textContent=t('settings.email_send_otp','Get code');
      if (res.data.remaining) startCooldown('email-otp-btn','email-otp-hint',res.data.remaining);
      else setMsg('email-err',res.data.error||t('common.error'),'err');
      return;
    }
    _emailOtpSent=true;
    setMsg('email-ok','Code sent to '+(res.data.masked||email),'ok');
    startCooldown('email-otp-btn','email-otp-hint',res.data.ttl||60);
  });

  document.getElementById('email-save-btn')?.addEventListener('click', async function() {
    const email = document.getElementById('s-email-input')?.value.trim().toLowerCase();
    const otp   = document.getElementById('s-email-otp')?.value.trim();
    hideMsg('email-err'); hideMsg('email-ok');
    if (!email||!otp) { setMsg('email-err','Fill in email and code','err'); return; }
    this.disabled=true;
    const res = await api('PUT', '/api/auth/settings/email', { email, otp });
    this.disabled=false;
    if (!res.ok) { setMsg('email-err',res.data.error||t('common.error'),'err'); return; }
    _admin=res.data.admin; updateAdminUI();
    setMsg('email-ok',t('common.success'),'ok');
    document.getElementById('s-email-input').value='';
    document.getElementById('s-email-otp').value='';
  });

  document.getElementById('pw-otp-btn')?.addEventListener('click', async function() {
    hideMsg('pw-err'); hideMsg('pw-ok');
    this.disabled=true; this.textContent='...';
    const res = await api('POST', '/api/auth/settings/password/send_otp');
    if (!res.ok) {
      this.disabled=false; this.textContent=t('settings.password_send_otp','Get code');
      if (res.data.remaining) startCooldown('pw-otp-btn','pw-otp-hint',res.data.remaining);
      else setMsg('pw-err',res.data.error||t('common.error'),'err');
      return;
    }
    setMsg('pw-ok','Code sent to your email','ok');
    startCooldown('pw-otp-btn','pw-otp-hint',res.data.ttl||60);
  });

  document.getElementById('pw-save-btn')?.addEventListener('click', async function() {
    const cur = document.getElementById('s-pw-current')?.value.trim();
    const nw  = document.getElementById('s-pw-new')?.value.trim();
    const otp = document.getElementById('s-pw-otp')?.value.trim();
    hideMsg('pw-err'); hideMsg('pw-ok');
    if (!cur||!nw||!otp) { setMsg('pw-err','Fill in all fields','err'); return; }
    if (nw.length<8) { setMsg('pw-err','New password must be at least 8 characters','err'); return; }
    this.disabled=true;
    const res = await api('PUT', '/api/auth/settings/password', { current_password:cur, new_password:nw, otp });
    this.disabled=false;
    if (!res.ok) { setMsg('pw-err',res.data.error||t('common.error'),'err'); return; }
    showToast(res.data.message||'Password changed. Signing out...','success');
    setTimeout(()=>location.href='/', 2000);
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  REPORTS
// ══════════════════════════════════════════════════════════════════════════════
let _repPage=1, _repPeriod='week';
async function initReports() {
  document.querySelectorAll('.tbl-filter-btn[data-period]').forEach(btn => {
    btn.addEventListener('click', () => {
      _repPeriod = btn.dataset.period; _repPage = 1;
      document.querySelectorAll('.tbl-filter-btn[data-period]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadReports();
    });
  });
  loadReports();
}
async function loadReports() {
  const tbody = document.getElementById('reports-body');
  if (!tbody) return;
  tbody.innerHTML='<tr><td colspan="6" class="loading-center"><span class="spinner"></span></td></tr>';
  const { ok, data } = await api('GET', `/api/tickets/reports/mine?period=${_repPeriod}&page=${_repPage}`);
  if (!ok) { tbody.innerHTML=`<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--t3)">${t('common.error')}</td></tr>`; return; }
  setEl('rep-total', data.total||0);
  setEl('rep-kyc',   data.kyc_closed||0);
  const pl = {week:'7 days',month:'1 month',year:'1 year',all:'All time'};
  setEl('rep-period-label', (t('reports.'+data.period, pl[data.period]||data.period)).toUpperCase());
  if (!data.tickets?.length) {
    tbody.innerHTML=`<tr><td colspan="6"><div class="empty-state"><div class="empty-state-icon">📋</div>${t('reports.no_tickets','No closed tickets')}</div></td></tr>`;
  } else {
    tbody.innerHTML=data.tickets.map(tk => `
      <tr>
        <td class="td-muted">${tk.ticket_uuid ? tk.ticket_uuid : '#'+tk.id}</td>
        <td>${esc(tk.subject||'—')}</td>
        <td class="td-muted">${esc(tk.user_name||'—')}</td>
        <td><span class="badge badge-closed">${t('status.closed','Closed')}</span></td>
        <td class="td-muted">${fmtDate(tk.created_at)}</td>
        <td><button class="btn-icon" onclick="location.href='/panel/ticket/${tk.id}'">${t('tickets.open_btn','View')}</button></td>
      </tr>`).join('');
  }
  updatePagination(data.page, data.pages, pg => { _repPage=pg; loadReports(); });
}

// ══════════════════════════════════════════════════════════════════════════════
//  TEAM CHAT
// ══════════════════════════════════════════════════════════════════════════════
let _chatLastId = 0, _chatPollTimer, _attachedFile = null;

async function initChat() {
  await loadChatHistory();
  startChatPoll();
  initChatInput();
}

async function loadChatHistory() {
  const box = document.getElementById('chat-messages');
  if (!box) return;
  box.innerHTML = '<div class="loading-center"><span class="spinner"></span></div>';
  const { ok, data } = await api('GET', '/api/chat/messages?limit=50');
  if (!ok) { box.innerHTML=`<div class="empty-state">${t('common.error')}</div>`; return; }
  box.innerHTML = '';
  (data.messages||[]).forEach(m => appendChatMsg(m, false));
  box.scrollTop = box.scrollHeight;
  if (data.messages?.length) _chatLastId = data.messages[data.messages.length-1].id;
}

function _roleColor(role) {
  const map = {
    helper:'#00e5ff', admin:'#a855f7', soc:'#ff4560',
    dsgn:'#f0b429',   devops:'#00e676', op:'#63b3ed',
    fm:'#f0b429',     ceo:'#e6e6f5'
  };
  return map[role||'helper'] || '#00e5ff';
}

function _avatarInitials(nickname) {
  if (!nickname) return '?';
  const parts = nickname.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return nickname.slice(0, 2).toUpperCase();
}

function appendChatMsg(m, scroll=true) {
  const box = document.getElementById('chat-messages');
  if (!box) return;
  const isMine = m.admin_id === _admin?.id;
  const wrapper = document.createElement('div');
  wrapper.className = `chat-row ${isMine ? 'chat-row-mine' : 'chat-row-other'}`;

  let bubbleContent = '';
  if (m.type === 'text') {
    const txt = esc(m.content).replace(
      /(https?:\/\/[^\s<>"]+)/g,
      '<a href="$1" target="_blank" rel="noopener" class="chat-link">$1</a>'
    );
    bubbleContent = txt;
  } else if (m.type === 'image') {
    bubbleContent = `<img src="${m.file_url}" class="chat-img" onclick="window.open('${m.file_url}','_blank')" loading="lazy"/>`;
  } else if (m.type === 'video') {
    bubbleContent = `<video src="${m.file_url}" controls class="chat-video"></video>`;
  } else if (m.type === 'audio') {
    bubbleContent = `<audio src="${m.file_url}" controls class="chat-audio"></audio>`;
  } else {
    bubbleContent = `<a href="${m.file_url}" target="_blank" class="chat-file-link">📎 ${esc(m.file_name||m.content)}</a>`;
  }

  const roleClr    = _roleColor(m.role);
  const initials   = _avatarInitials(m.nickname);
  const roleLbl    = (m.role || 'helper').toUpperCase();
  const pubId      = m.public_id ? `<span class="chat-sender-id">${esc(m.public_id)}</span>` : '';

  wrapper.innerHTML = `
    <div class="chat-avatar" style="--av-color:${roleClr}">${esc(initials)}</div>
    <div class="chat-msg-body">
      <div class="chat-sender-row">
        <span class="chat-sender-name">${esc(m.nickname||'—')}</span>
        ${pubId}
        <span class="chat-role-pill" style="--rp-color:${roleClr}">${roleLbl}</span>
        <span class="chat-msg-time">${fmtDate(m.created_at)}</span>
      </div>
      <div class="chat-bubble">${bubbleContent}</div>
    </div>`;

  box.appendChild(wrapper);
  if (scroll) box.scrollTop = box.scrollHeight;
}

function startChatPoll() {
  clearInterval(_chatPollTimer);
  _chatPollTimer = setInterval(async () => {
    const { ok, data } = await api('GET', `/api/chat/poll?after=${_chatLastId}`);
    if (ok && data.messages?.length) {
      data.messages.forEach(m => appendChatMsg(m, true));
      _chatLastId = data.messages[data.messages.length-1].id;
    }
  }, 2500);
}

function initChatInput() {
  const textarea = document.getElementById('chat-input');
  const sendBtn  = document.getElementById('chat-send');
  const attachBtn= document.getElementById('attach-btn');
  const fileInput= document.getElementById('file-input');

  // Auto-resize textarea
  textarea?.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 160) + 'px';
  });

  // Enter to send
  textarea?.addEventListener('keydown', e => {
    if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendBtn?.click(); }
  });

  // Attach button
  attachBtn?.addEventListener('click', () => fileInput?.click());

  // File selected
  fileInput?.addEventListener('change', function() {
    const file = this.files?.[0];
    if (!file) return;
    _attachedFile = file;
    document.getElementById('attach-preview')?.classList.remove('hidden');
    const nameEl = document.getElementById('attach-name');
    if (nameEl) nameEl.textContent = `📎 ${file.name} (${(file.size/1024/1024).toFixed(2)} MB)`;
  });

  // Remove attachment
  document.getElementById('attach-remove')?.addEventListener('click', () => {
    _attachedFile = null;
    if (fileInput) fileInput.value='';
    document.getElementById('attach-preview')?.classList.add('hidden');
  });

  // Send
  sendBtn?.addEventListener('click', async () => {
    if (_attachedFile) {
      const fd = new FormData();
      fd.append('file', _attachedFile);
      const res = await apiForm('/api/chat/upload', fd);
      if (res.ok && res.data.message) {
        appendChatMsg(res.data.message, true);
        _chatLastId = res.data.message.id;
      } else { showToast(res.data.error||'Upload failed','error'); }
      _attachedFile = null;
      if (fileInput) fileInput.value='';
      document.getElementById('attach-preview')?.classList.add('hidden');
      return;
    }
    const msg = textarea?.value.trim();
    if (!msg) return;
    if (textarea) { textarea.value=''; textarea.style.height='auto'; }
    const res = await api('POST', '/api/chat/send', { content: msg });
    if (res.ok && res.data.message) {
      appendChatMsg(res.data.message, true);
      _chatLastId = res.data.message.id;
    } else { showToast(res.data.error||t('common.error'),'error'); }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  SOC DASHBOARD
// ══════════════════════════════════════════════════════════════════════════════
let _socHours = 24;
async function initSoc() {
  document.querySelectorAll('.tbl-filter-btn[data-hours]').forEach(btn => {
    // Default active on 24
    if (btn.dataset.hours === '24') btn.classList.add('active');
    btn.addEventListener('click', () => {
      _socHours = parseInt(btn.dataset.hours);
      document.querySelectorAll('.tbl-filter-btn[data-hours]').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadSoc();
    });
  });
  document.getElementById('soc-refresh')?.addEventListener('click', loadSoc);
  loadSoc();
}

async function loadSoc() {
  // Show spinners
  ['soc-threats-body','soc-top-ips-body','soc-methods-body','soc-paths-body'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML='<tr><td colspan="5" class="loading-center"><span class="spinner"></span></td></tr>';
  });

  const { ok, data } = await api('GET', `/api/soc/overview?hours=${_socHours}`);
  if (!ok) {
    if (data.error === 'SOC access only') {
      document.querySelector('.panel-body').innerHTML =
        `<div class="empty-state"><div class="empty-state-icon">🔒</div>Access restricted to SOC, OP, CEO roles</div>`;
    }
    return;
  }

  const { analysis, db_stats } = data;

  setEl('soc-total-req',  analysis.total_requests || 0);
  setEl('soc-unique-ips', analysis.unique_ips || 0);
  setEl('soc-threats-cnt', analysis.threats?.length || 0);
  setEl('soc-4xx',  analysis.status_groups?.['4xx'] || 0);
  setEl('soc-5xx',  analysis.status_groups?.['5xx'] || 0);
  setEl('soc-users', db_stats?.users_total || 0);

  const logPathEl = document.getElementById('soc-log-path');
  if (logPathEl) {
    logPathEl.textContent = analysis.log_available
      ? `log: ${data.log_path}`
      : '⚠ Access log not found — set CASINO_ROOT in .env';
    logPathEl.style.color = analysis.log_available ? 'var(--t3)' : 'var(--gold)';
  }

  // Threats
  const tBody = document.getElementById('soc-threats-body');
  if (tBody) {
    tBody.innerHTML = analysis.threats?.length
      ? analysis.threats.map(th => `
          <tr>
            <td style="font-family:var(--ffd);font-size:12px">${esc(th.ip)}</td>
            <td>${th.requests}</td>
            <td style="color:var(--red)">${th.errors}</td>
            <td><span class="badge badge-${th.severity==='high'?'rejected':'pending'}">${th.severity}</span></td>
            <td style="font-size:11px;color:var(--t2)">${esc(th.reasons.join('; '))}</td>
          </tr>`).join('')
      : `<tr><td colspan="5" class="empty-state">No threats detected</td></tr>`;
  }

  // Top IPs
  const ipBody = document.getElementById('soc-top-ips-body');
  if (ipBody) {
    ipBody.innerHTML = analysis.top_ips?.length
      ? analysis.top_ips.map(ip => `
          <tr>
            <td style="font-family:var(--ffd);font-size:12px">${esc(ip.ip)}</td>
            <td>${ip.requests}</td>
          </tr>`).join('')
      : `<tr><td colspan="2" class="empty-state">${analysis.log_available?'No data':'Log unavailable'}</td></tr>`;
  }

  // Methods
  const mBody = document.getElementById('soc-methods-body');
  if (mBody) {
    const methods = analysis.methods || {};
    mBody.innerHTML = Object.keys(methods).length
      ? Object.entries(methods).sort((a,b)=>b[1]-a[1]).map(([m,c]) => `
          <tr><td style="font-family:var(--ffd);font-size:12px">${m}</td><td>${c}</td></tr>`).join('')
      : `<tr><td colspan="2" class="empty-state">No data</td></tr>`;
  }

  // Paths
  const pBody = document.getElementById('soc-paths-body');
  if (pBody) {
    pBody.innerHTML = analysis.top_paths?.length
      ? analysis.top_paths.map(p => `
          <tr>
            <td style="font-size:11px;font-family:var(--ffd);color:var(--t2);word-break:break-all">${esc(p.path)}</td>
            <td>${p.hits}</td>
          </tr>`).join('')
      : `<tr><td colspan="2" class="empty-state">No data</td></tr>`;
  }
}



// ── Confirm modal ──────────────────────────────────────────────────────────
function showConfirm(message, onConfirm) {
  const ov = document.createElement('div');
  ov.className = 'confirm-overlay';
  ov.innerHTML = `
    <div class="confirm-box">
      <div class="confirm-title">⚠️ ${t('common.confirm','Подтверждение')}</div>
      <div class="confirm-body">${message}</div>
      <div class="confirm-actions">
        <button class="panel-btn" style="background:var(--bg4);border:1px solid var(--bdr);color:var(--t2)"
                id="confirm-cancel" data-t="common.cancel">${t('common.cancel','Отмена')}</button>
        <button class="panel-btn panel-btn--primary" id="confirm-ok" data-t="common.save">${t('common.confirm','Подтвердить')}</button>
      </div>
    </div>`;
  document.body.appendChild(ov);
  document.getElementById('confirm-ok').onclick = () => { document.body.removeChild(ov); onConfirm(); };
  document.getElementById('confirm-cancel').onclick = () => document.body.removeChild(ov);
  ov.addEventListener('click', e => { if (e.target === ov) document.body.removeChild(ov); });
}

// ══════════════════════════════════════════════════════════════
//  ANALYTICS
// ══════════════════════════════════════════════════════════════
let _analyticsPeriod = '7d';

async function initAnalytics() {
  document.querySelectorAll('.tbl-filter-btn[data-period]').forEach(btn => {
    btn.addEventListener('click', () => {
      _analyticsPeriod = btn.dataset.period;
      document.querySelectorAll('.tbl-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadAnalytics();
    });
  });
  loadAnalytics();
}

async function loadAnalytics() {
  const { ok, data } = await api('GET', `/api/admin/analytics?period=${_analyticsPeriod}`);
  if (!ok) return;
  const fmt = n => Number(n||0).toLocaleString('ru-RU', {maximumFractionDigits:2});
  setEl('kpi-ggr',       '$' + fmt(data.ggr));
  setEl('kpi-ggr-sub',   `Bets $${fmt(data.bets)} · Wins $${fmt(data.wins)}`);
  setEl('kpi-dep',       '$' + fmt(data.deposits?.amount));
  setEl('kpi-dep-cnt',   `${data.deposits?.count||0} ${t('analytics.transactions','txn')}`);
  setEl('kpi-wd',        '$' + fmt(data.withdrawals?.amount));
  setEl('kpi-wd-cnt',    `${data.withdrawals?.count||0} ${t('analytics.transactions','txn')}`);
  setEl('kpi-pend',      '$' + fmt(data.pending_withdrawals?.amount));
  setEl('kpi-pend-cnt',  `${data.pending_withdrawals?.count||0} ${t('analytics.waiting','waiting')}`);
  setEl('kpi-users',     fmt(data.users?.total));
  setEl('kpi-users-sub', `+${data.users?.new||0} ${t('analytics.new_period','new').replace('%n','').trim()}`);
  setEl('kpi-active',    fmt(data.users?.active));
  setEl('kpi-rounds',    `${fmt(data.rounds)} ${t('analytics.game_rounds','rounds')}`);
  setEl('kpi-kyc',       fmt(data.users?.verified));
  setEl('kpi-tickets',   data.tickets?.open||0);
  setEl('kpi-tickets-sub', `${data.tickets?.in_progress||0} ${t('analytics.in_progress','in progress')}`);
  const tbody = document.getElementById('top-games-body');
  if (tbody) {
    tbody.innerHTML = (data.top_games||[]).length
      ? (data.top_games||[]).map(g => `<tr>
          <td>${esc(g.game_id)}</td>
          <td>${g.rounds||0}</td>
          <td>$${fmt(g.volume)}</td>
        </tr>`).join('')
      : `<tr><td colspan="3" class="empty-state">${t('analytics.no_data','No data')}</td></tr>`;
  }
}

// ══════════════════════════════════════════════════════════════
//  PAYMENTS QUEUE
// ══════════════════════════════════════════════════════════════
let _payPage = 1, _payStatus = 'pending';

async function initPaymentsQueue() {
  document.querySelectorAll('.tbl-filter-btn[data-status]').forEach(btn => {
    btn.addEventListener('click', () => {
      _payStatus = btn.dataset.status; _payPage = 1;
      document.querySelectorAll('.tbl-filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadPaymentsQueue();
    });
  });
  loadPaymentsQueue();
}

async function loadPaymentsQueue() {
  const tbody = document.getElementById('payments-body');
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="7" class="loading-center"><span class="spinner"></span></td></tr>`;
  const { ok, data } = await api('GET', `/api/admin/payments?status=${_payStatus}&page=${_payPage}`);
  if (!ok || !data.orders?.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state">${t('payments.no_orders','No orders')}</td></tr>`;
    return;
  }
  tbody.innerHTML = data.orders.map(o => `<tr>
    <td class="td-muted">#${o.id}</td>
    <td>${esc(o.user_name||'—')}<br><span style="font-size:10px;color:var(--t3)">${esc(o.email||'')}</span></td>
    <td><span style="color:${o.type==='deposit'?'var(--green)':'var(--gold)'}">${o.type}</span></td>
    <td class="td-muted">${esc(o.provider||'—')}</td>
    <td><strong>${o.amount} ${o.currency}</strong></td>
    <td><span class="badge badge-${o.status==='completed'?'closed':o.status==='pending'?'open':'progress'}">${o.status}</span></td>
    <td class="td-muted">${fmtDate(o.created_at)}</td>
  </tr>`).join('');
  updatePagination(data.page, data.pages, pg => { _payPage=pg; loadPaymentsQueue(); });
}

// ══════════════════════════════════════════════════════════════
//  TEAM
// ══════════════════════════════════════════════════════════════
const ROLES = ['helper','soc','marketing','dealer','gm','developer','admin','ceo'];

async function initTeam() { loadTeam(); }

async function loadTeam() {
  const tbody = document.getElementById('team-body');
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="loading-center"><span class="spinner"></span></td></tr>`;
  const { ok, data } = await api('GET', '/api/admin/team');
  if (!ok || !data.team?.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">—</td></tr>`;
    return;
  }
  tbody.innerHTML = data.team.map(m => `<tr>
    <td><strong>${esc(m.nickname)}</strong></td>
    <td><span style="font-family:var(--ffd);color:var(--a);font-size:11px">${esc(m.public_id||'—')}</span></td>
    <td>${roleTag(m.role)}</td>
    <td style="color:var(--green)">${m.closed_tickets}</td>
    <td style="color:var(--gold)">${m.active_tickets}</td>
    <td style="display:flex;gap:6px;align-items:center">
      <select class="panel-inp" style="padding:4px 8px;font-size:11px;width:90px"
              onchange="changeRole(${m.id},this.value)">
        ${ROLES.map(r=>`<option value="${r}"${r===m.role?' selected':''}>${r}</option>`).join('')}
      </select>
      <button class="ban-unban" style="color:var(--red);border-color:rgba(255,69,96,.2)"
              onclick="removeMember(${m.id},'${esc(m.nickname)}')">✕</button>
    </td>
  </tr>`).join('');
}

async function changeRole(mid, role) {
  const { ok, data } = await api('PUT', `/api/admin/team/${mid}/role`, { role });
  if (ok) showToast(t('team.role_updated','Role updated').replace('%r', role), 'success');
  else showToast(data?.error || t('common.error','Error'), 'error');
}

async function removeMember(mid, name) {
  showConfirm(t('team.confirm_remove','Remove %n?').replace('%n', name), async () => {
    const { ok } = await api('DELETE', `/api/admin/team/${mid}`);
    if (ok) { showToast(t('team.removed','Removed'), 'success'); loadTeam(); }
  });
}

// ══════════════════════════════════════════════════════════════
//  BAN LIST
// ══════════════════════════════════════════════════════════════
async function initBans() {
  _applyI18nPlaceholders();
  loadBans();
}

function _applyI18nPlaceholders() {
  document.querySelectorAll('[data-tp]').forEach(el => {
    const v = t(el.dataset.tp, '');
    if (v) el.placeholder = v;
  });
}

async function loadBans() {
  const box = document.getElementById('bans-list');
  if (!box) return;
  const { ok, data } = await api('GET', '/api/admin/bans');
  if (!ok) return;
  const ips = data.blocked_ips || [];
  if (!ips.length) {
    box.innerHTML = `<div class="empty-state" style="padding:20px">${t('bans.no_bans','No blocked IPs')}</div>`;
    return;
  }
  box.innerHTML = ips.map(ip => `
    <div class="ban-row">
      <span class="ban-ip">${esc(ip)}</span>
      <button class="ban-unban" onclick="removeBan('${ip}')">${t('bans.btn_unban','Unban')}</button>
    </div>`).join('');
}

async function addBan() {
  const inp = document.getElementById('ban-ip-input');
  const ip  = inp?.value.trim();
  if (!ip) { showToast(t('bans.err_ip','Enter IP'), 'error'); return; }
  const { ok } = await api('POST', '/api/admin/bans', { ip });
  if (ok) {
    showToast(t('bans.blocked','Blocked %ip').replace('%ip', ip), 'success');
    if (inp) inp.value = '';
    loadBans();
  }
}

async function removeBan(ip) {
  showConfirm(`${t('bans.btn_unban','Unban')} <strong style="color:var(--a)">${ip}</strong>?`, async () => {
    const { ok } = await api('DELETE', '/api/admin/bans', { ip });
    if (ok) { showToast(t('bans.unblocked','Unblocked %ip').replace('%ip', ip), 'success'); loadBans(); }
  });
}

// ══════════════════════════════════════════════════════════════
//  BROADCAST
// ══════════════════════════════════════════════════════════════
let _bcBound = false;

async function _bcLoadAudienceCount() {
  const target = document.getElementById('bc-target')?.value || 'all';
  const counter = document.getElementById('bc-audience-count');
  if (counter) counter.textContent = '...';
  const { ok, data } = await api('GET', `/api/admin/broadcast/audience?target=${encodeURIComponent(target)}`);
  if (!ok) {
    if (counter) counter.textContent = '—';
    return;
  }
  if (counter) counter.textContent = String(data.count ?? 0);
}

function _bcUpdatePreview() {
  const subject = document.getElementById('bc-subject')?.value.trim() || '';
  const message = document.getElementById('bc-message')?.value || '';
  const targetSel = document.getElementById('bc-target');
  const targetLabel = targetSel?.selectedOptions?.[0]?.textContent || '';
  const chars = message.trim().length;

  const sub = document.getElementById('bc-preview-subject');
  const msg = document.getElementById('bc-preview-message');
  const tEl = document.getElementById('bc-preview-target');
  const cEl = document.getElementById('bc-char-count');

  if (sub) sub.textContent = subject || t('broadcast.preview_no_subject', 'No subject yet');
  if (msg) msg.textContent = message.trim() || t('broadcast.preview_no_message', 'No message yet');
  if (tEl) tEl.textContent = targetLabel || '—';
  if (cEl) cEl.textContent = String(chars);
}

function _bcClearForm() {
  const sub = document.getElementById('bc-subject');
  const msg = document.getElementById('bc-message');
  if (sub) sub.value = '';
  if (msg) msg.value = '';
  _bcUpdatePreview();
}

async function initBroadcast() {
  _applyI18nPlaceholders();
  if (!_bcBound) {
    _bcBound = true;
    document.getElementById('bc-send-btn')?.addEventListener('click', sendBroadcast);
    document.getElementById('bc-clear-btn')?.addEventListener('click', _bcClearForm);
    document.getElementById('bc-target')?.addEventListener('change', async () => {
      _bcUpdatePreview();
      await _bcLoadAudienceCount();
    });
    document.getElementById('bc-subject')?.addEventListener('input', _bcUpdatePreview);
    document.getElementById('bc-message')?.addEventListener('input', _bcUpdatePreview);
  }
  _bcUpdatePreview();
  await _bcLoadAudienceCount();
}

async function sendBroadcast() {
  const subject = document.getElementById('bc-subject')?.value.trim();
  const message = document.getElementById('bc-message')?.value.trim();
  const target  = document.getElementById('bc-target')?.value;
  const errEl   = document.getElementById('bc-err');
  const okEl    = document.getElementById('bc-ok');
  const btn     = document.getElementById('bc-send-btn');
  const lbl     = document.getElementById('bc-send-lbl');
  if (errEl) { errEl.textContent=''; errEl.classList.add('hidden'); }
  if (okEl)  { okEl.textContent='';  okEl.classList.add('hidden'); }
  if (!subject || !message) {
    if (errEl) { errEl.textContent = t('broadcast.err_fill','Fill fields'); errEl.classList.remove('hidden'); }
    return;
  }
  const recipients = Number(document.getElementById('bc-audience-count')?.textContent || '0');
  if (!Number.isFinite(recipients) || recipients <= 0) {
    if (errEl) { errEl.textContent = t('broadcast.err_no_recipients','No recipients found'); errEl.classList.remove('hidden'); }
    return;
  }
  const targetLabel = document.getElementById('bc-target')?.selectedOptions[0]?.textContent || target;
  showConfirm(
    t('broadcast.confirm','Send to %t users?')
      .replace('%t', `<strong>${targetLabel}</strong>`)
      .replace('%n', `<strong>${recipients}</strong>`),
    async () => {
    if (btn) btn.disabled = true;
    if (lbl) lbl.textContent = t('broadcast.btn_sending','Sending...');
    const { ok, data } = await api('POST', '/api/admin/broadcast', { subject, message, target });
    if (btn) btn.disabled = false;
    if (lbl) lbl.textContent = t('broadcast.btn_send','Send Broadcast');
    if (ok) {
      if (okEl) { okEl.textContent = data.message; okEl.classList.remove('hidden'); }
      _bcClearForm();
      await _bcLoadAudienceCount();
    } else {
      const msg = data?.error || t('broadcast.err_no_recipients','No recipients');
      if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
    }
    },
  );
}

// ══════════════════════════════════════════════════════════════
//  GAME BUILDER (sync fixed_out)
// ══════════════════════════════════════════════════════════════
let _gbProviderGames = [];
let _gbSportEvents = [];
let _gbSportTags = [];
let _gbProvCatalog = [];
let _gbProvVisibility = { mode: 'all', allowed_ids: [] };
let _gbTab = 'casino';

function gbSwitchTab(tab) {
  _gbTab = tab;
  const cBtn = document.getElementById('gb-tab-casino-btn');
  const sBtn = document.getElementById('gb-tab-sport-btn');
  cBtn?.classList.toggle('active', tab === 'casino');
  sBtn?.classList.toggle('active', tab === 'sport');
  document.getElementById('gb-casino-wrap')?.classList.toggle('hidden', tab !== 'casino');
  document.getElementById('gb-casino-form-wrap')?.classList.add('hidden');
  document.getElementById('gb-sport-wrap')?.classList.toggle('hidden', tab !== 'sport');
  document.getElementById('gb-sport-form-wrap')?.classList.add('hidden');
}
function gbShowCasinoForm(show, data) {
  document.getElementById('gb-casino-wrap')?.classList.toggle('hidden', show);
  document.getElementById('gb-casino-form-wrap')?.classList.toggle('hidden', !show);
  if (!show) return;

  const title = document.getElementById('gb-casino-form-title');
  if (title) {
    title.setAttribute('data-t', data ? 'games_builder.edit_title' : 'games_builder.create_title');
    title.textContent = data ? t('games_builder.edit_title', 'Edit game') : t('games_builder.create_title', 'New game');
  }

  const g = data || {};
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
  set('gb-pg-id', g.id || '');
  set('gb-pg-name', g.name || '');
  set('gb-pg-provider', g.provider || '');
  set('gb-pg-provider-tag', g.provider_tag || '');
  set('gb-pg-game-tag', (g.tags && g.tags[0]) || '');
  set('gb-pg-type', g.badge_type || 'new');
  set('gb-pg-category', g.category || 'slots');
  set('gb-pg-rtp', g.rtp || '0.96');
  set('gb-pg-api-key', g.api_key || '');
  set('gb-pg-endpoint', g.endpoint || '');
  set('gb-pg-url', g.game_url || '');
  set('gb-pg-game-code', (g.metadata && g.metadata.provider_game_code) || '');
  set('gb-pg-table-id', (g.metadata && g.metadata.provider_table_id) || '');
  set('gb-pg-cover', g.cover || '');
  set('gb-pg-logo', g.logo || '');
  set('gb-pg-icon', g.icon || '🎰');
  set('gb-pg-desc', g.description || '');
}
function gbShowSportForm(show) {
  document.getElementById('gb-sport-wrap')?.classList.toggle('hidden', show);
  document.getElementById('gb-sport-form-wrap')?.classList.toggle('hidden', !show);
}
function gbToggleModeFields() {
  const mode = document.getElementById('gb-sp-mode')?.value || 'manual';
  const autoWrap = document.getElementById('gb-sp-auto-fields');
  if (autoWrap) autoWrap.style.display = mode === 'auto' ? 'grid' : 'none';
}
function gbTagMeta(tag) {
  const t = String(tag || '').trim().toLowerCase();
  return _gbSportTags.find(x => String(x.id || '').toLowerCase() === t) || null;
}
function gbApplyTagDefaults(tagValue) {
  const meta = gbTagMeta(tagValue);
  if (!meta) return;
  const iconInp = document.getElementById('gb-sp-icon');
  const catInp = document.getElementById('gb-sp-category');
  if (iconInp && !iconInp.value.trim()) iconInp.value = meta.icon_path || '';
  if (catInp && !catInp.value.trim()) catInp.value = meta.label || (meta.id ? String(meta.id).toUpperCase() : '');
}

async function gbUploadSportBackground(file) {
  if (!file) return '';
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/api/admin/game-builder/sport-events/upload-background', {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data?.ok) throw new Error(data?.error || 'Upload error');
  return data.background || '';
}
async function gbUploadSportIcon(tag, file) {
  if (!file) return '';
  const fd = new FormData();
  fd.append('tag', String(tag || '').trim().toLowerCase());
  fd.append('file', file);
  const r = await fetch('/api/admin/game-builder/sport-tags/upload-icon', {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || !data?.ok) throw new Error(data?.error || 'Upload error');
  _gbSportTags = data.tags || _gbSportTags;
  return data.icon_path || '';
}
async function gbLoadSportTags() {
  const { ok, data } = await api('GET', '/api/admin/game-builder/sport-tags');
  if (!ok) return;
  _gbSportTags = data.tags || [];
}

function gbResetSportForm() {
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v ?? ''; };
  set('gb-sp-id', '');
  set('gb-sp-league', '');
  set('gb-sp-tag', 'football');
  set('gb-sp-category', '');
  set('gb-sp-country', '');
  set('gb-sp-sport-type', 'sport');
  set('gb-sp-home', '');
  set('gb-sp-away', '');
  set('gb-sp-time', '');
  set('gb-sp-cover', '');
  set('gb-sp-icon', '');
  set('gb-sp-stream', '');
  set('gb-sp-odd-home', '2.10');
  set('gb-sp-odd-draw', '3.20');
  set('gb-sp-odd-away', '2.90');
  set('gb-sp-mode', 'manual');
  set('gb-sp-auto-source', '');
  set('gb-sp-auto-refresh', '15');
  set('gb-sp-auto-notes', '');
  const coverName = document.getElementById('gb-sp-cover-file-name');
  if (coverName) coverName.textContent = 'No file selected';
  const iconName = document.getElementById('gb-sp-icon-file-name');
  if (iconName) iconName.textContent = 'No file selected';
  const liveEl = document.getElementById('gb-sp-live');
  if (liveEl) liveEl.checked = false;
  const topEl = document.getElementById('gb-sp-top-match');
  if (topEl) topEl.checked = false;
  gbToggleModeFields();
}

async function initGameBuilder() {
  document.getElementById('gb-tab-casino-btn')?.addEventListener('click', () => gbSwitchTab('casino'));
  document.getElementById('gb-tab-sport-btn')?.addEventListener('click', () => {
    gbSwitchTab('sport');
    gbLoadSportEvents();
    gbLoadSportProviderCatalog();
  });
  document.getElementById('gb-casino-refresh-btn')?.addEventListener('click', gbLoadProviderGames);
  document.getElementById('gb-sport-refresh-btn')?.addEventListener('click', () => {
    gbLoadSportEvents();
    gbLoadSportProviderCatalog();
  });
  document.getElementById('gb-prov-refresh-btn')?.addEventListener('click', gbLoadSportProviderCatalog);
  document.getElementById('gb-prov-mode')?.addEventListener('change', gbSportProviderSetMode);

  document.getElementById('gb-casino-open-form-btn')?.addEventListener('click', () => gbShowCasinoForm(true));
  document.getElementById('gb-casino-close-form-btn')?.addEventListener('click', () => gbShowCasinoForm(false));
  document.getElementById('gb-casino-cancel-btn')?.addEventListener('click', () => gbShowCasinoForm(false));
  document.getElementById('gb-casino-save-btn')?.addEventListener('click', gbSaveProviderGame);

  document.getElementById('gb-sport-open-form-btn')?.addEventListener('click', () => {
    gbResetSportForm();
    gbShowSportForm(true);
  });
  document.getElementById('gb-sport-close-form-btn')?.addEventListener('click', () => gbShowSportForm(false));
  document.getElementById('gb-sport-cancel-btn')?.addEventListener('click', () => gbShowSportForm(false));
  document.getElementById('gb-sport-save-btn')?.addEventListener('click', gbSaveSportEvent);
  document.getElementById('gb-sp-mode')?.addEventListener('change', gbToggleModeFields);
  document.getElementById('gb-sp-tag')?.addEventListener('blur', (e) => gbApplyTagDefaults(e.target?.value || ''));
  document.getElementById('gb-sp-cover-trigger')?.addEventListener('click', () => document.getElementById('gb-sp-cover-file')?.click());
  document.getElementById('gb-sp-icon-trigger')?.addEventListener('click', () => document.getElementById('gb-sp-icon-file')?.click());
  document.getElementById('gb-sp-cover-file')?.addEventListener('change', async (e) => {
    const file = e.target?.files?.[0];
    if (!file) return;
    const nameEl = document.getElementById('gb-sp-cover-file-name');
    if (nameEl) nameEl.textContent = file.name;
    try {
      const bg = await gbUploadSportBackground(file);
      if (bg) {
        const inp = document.getElementById('gb-sp-cover');
        if (inp) inp.value = bg;
        showToast('Background uploaded', 'success');
      }
    } catch (err) {
      showToast(err?.message || 'Upload error', 'error');
    } finally {
      e.target.value = '';
    }
  });
  document.getElementById('gb-sp-icon-file')?.addEventListener('change', async (e) => {
    const file = e.target?.files?.[0];
    if (!file) return;
    const nameEl = document.getElementById('gb-sp-icon-file-name');
    if (nameEl) nameEl.textContent = file.name;
    const tag = document.getElementById('gb-sp-tag')?.value.trim() || '';
    if (!tag) { showToast('Fill tag before icon upload', 'error'); return; }
    try {
      const iconPath = await gbUploadSportIcon(tag, file);
      const inp = document.getElementById('gb-sp-icon');
      if (inp && iconPath) inp.value = iconPath;
      showToast('Sport icon uploaded', 'success');
    } catch (err) {
      showToast(err?.message || 'Upload error', 'error');
    } finally {
      e.target.value = '';
    }
  });

  document.getElementById('gb-casino-list')?.addEventListener('click', gbHandleCasinoListClick);
  document.getElementById('gb-sport-list')?.addEventListener('click', gbHandleSportListClick);
  document.getElementById('gb-sport-provider-list')?.addEventListener('click', gbHandleProviderCatalogClick);

  gbSwitchTab('casino');
  await gbLoadSportTags();
  await gbLoadProviderGames();
}

async function gbLoadProviderGames() {
  const wrap = document.getElementById('gb-casino-list');
  if (wrap) wrap.innerHTML = '<div class="loading-center"><span class="spinner"></span></div>';
  const { ok, status, data } = await apiWithTimeout('GET', '/api/admin/game-builder/provider-games');
  if (!ok) {
    const msg = status === 403
      ? t('common.access_denied', 'Нет доступа')
      : esc(data?.error || t('common.error', 'Error'));
    if (wrap) wrap.innerHTML = `<div class="empty-state">⚠️ ${msg}</div>`;
    return;
  }
  _gbProviderGames = data.games || [];
  gbRenderProviderGames();
}

function gbRenderProviderGames() {
  const wrap = document.getElementById('gb-casino-list');
  if (!wrap) return;
  if (!_gbProviderGames.length) {
    wrap.innerHTML = `<div class="empty-state">${t('games_builder.no_games','No games')}</div>`;
    return;
  }
  wrap.innerHTML = _gbProviderGames.map(g => {
    const active = g.active !== false;
    return `
    <div class="stat-card" style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:center">
        <div style="display:flex;gap:10px;align-items:center">
          <span style="font-size:22px">${esc(g.icon||'🎰')}</span>
          <div>
            <div style="font-weight:600">${esc(g.name)} <span style="color:var(--t3);font-size:11px">(${esc(g.id)})</span></div>
            <div style="font-size:12px;color:var(--t2)">${t('games_builder.field_provider','Provider')}: ${esc(g.provider||'—')} · ${t('games_builder.col_category','Cat')}: ${esc(g.category||'—')}</div>
            <div style="font-size:12px;color:var(--t2)">RTP: ${(Number(g.rtp||0)*100).toFixed(1)}% · <span style="padding:1px 6px;border-radius:8px;font-size:10px;background:rgba(255,255,255,.06);color:var(--t2)">${esc(g.badge_type||'—')}</span></div>
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <input class="panel-inp" data-gb-rtp-id="${esc(g.id)}" type="number" step="0.001" value="${Number(g.rtp||0.96)}" style="width:90px" title="RTP">
          <button class="btn-icon" data-gb-action="save-rtp" data-gb-id="${esc(g.id)}">${t('games_builder.btn_save','Save')}</button>
          <button class="btn-icon" data-gb-action="edit" data-gb-id="${esc(g.id)}">${t('games_builder.btn_edit','Edit')}</button>
          <button class="btn-icon" data-gb-action="toggle" data-gb-id="${esc(g.id)}" data-gb-active="${active ? '1' : '0'}">${active ? t('games_builder.btn_toggle_off','Disable') : t('games_builder.btn_toggle_on','Enable')}</button>
          <button class="btn-icon" style="color:var(--red)" data-gb-action="delete" data-gb-id="${esc(g.id)}">${t('games_builder.btn_delete','Delete')}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}

function gbHandleCasinoListClick(e) {
  const btn = e.target.closest('[data-gb-action]');
  if (!btn) return;
  const id = btn.dataset.gbId;
  const action = btn.dataset.gbAction;
  if (!id) return;
  if (action === 'edit') gbEditGameById(id);
  if (action === 'delete') gbDeleteProviderGame(id);
  if (action === 'toggle') gbToggleProviderGame(id, btn.dataset.gbActive !== '1');
  if (action === 'save-rtp') gbUpdateRtp(id);
}

function gbEditGameById(id) {
  const game = _gbProviderGames.find(g => String(g.id) === String(id));
  if (!game) {
    showToast(t('games_builder.err_not_found', 'Game not found'), 'error');
    return;
  }
  gbShowCasinoForm(true, game);
}

async function gbSaveProviderGame() {
  const body = {
    id: document.getElementById('gb-pg-id')?.value.trim() || undefined,
    name: document.getElementById('gb-pg-name')?.value.trim(),
    provider: document.getElementById('gb-pg-provider')?.value.trim(),
    provider_tag: document.getElementById('gb-pg-provider-tag')?.value.trim(),
    game_tag: document.getElementById('gb-pg-game-tag')?.value.trim(),
    type_badge: document.getElementById('gb-pg-type')?.value,
    category: document.getElementById('gb-pg-category')?.value.trim(),
    rtp: Number(document.getElementById('gb-pg-rtp')?.value || '0.96'),
    api_key: document.getElementById('gb-pg-api-key')?.value.trim(),
    endpoint: document.getElementById('gb-pg-endpoint')?.value.trim(),
    game_url: document.getElementById('gb-pg-url')?.value.trim(),
    provider_game_code: document.getElementById('gb-pg-game-code')?.value.trim(),
    provider_table_id: document.getElementById('gb-pg-table-id')?.value.trim(),
    cover: document.getElementById('gb-pg-cover')?.value.trim(),
    logo: document.getElementById('gb-pg-logo')?.value.trim(),
    icon: document.getElementById('gb-pg-icon')?.value.trim(),
    description: document.getElementById('gb-pg-desc')?.value.trim(),
  };
  const { ok, data } = await api('POST', '/api/admin/game-builder/provider-games', body);
  if (!ok) { showToast(data.error || 'Error', 'error'); return; }
  showToast('Game saved', 'success');
  gbShowCasinoForm(false);
  gbLoadProviderGames();
}
async function gbUpdateRtp(id) {
  const inp = Array.from(document.querySelectorAll('[data-gb-rtp-id]')).find(x => x.dataset.gbRtpId === id);
  const v = Number(inp?.value || '0.96');
  const { ok, data } = await api('POST', `/api/admin/game-builder/provider-games/${id}/rtp`, { rtp: v });
  if (!ok) { showToast(data.error || 'Error', 'error'); return; }
  showToast('RTP updated', 'success');
  gbLoadProviderGames();
}
async function gbToggleProviderGame(id, active) {
  const { ok, data } = await api('POST', `/api/admin/game-builder/provider-games/${id}/toggle`, { active });
  if (!ok) { showToast(data.error || 'Error', 'error'); return; }
  showToast(active ? 'Enabled' : 'Disabled', 'success');
  gbLoadProviderGames();
}
async function gbDeleteProviderGame(id) {
  showConfirm(`Delete <strong>${esc(id)}</strong>?`, async () => {
    const { ok, data } = await api('DELETE', `/api/admin/game-builder/provider-games/${id}`);
    if (!ok) { showToast(data.error || 'Error', 'error'); return; }
    showToast('Deleted', 'success');
    gbLoadProviderGames();
  });
}

async function gbLoadSportEvents() {
  const wrap = document.getElementById('gb-sport-list');
  if (wrap) wrap.innerHTML = '<div class="loading-center"><span class="spinner"></span></div>';
  const { ok, status, data } = await apiWithTimeout('GET', '/api/admin/game-builder/sport-events');
  if (!ok) {
    const msg = status === 403
      ? t('common.access_denied', 'Нет доступа')
      : esc(data?.error || t('common.error', 'Error'));
    if (wrap) wrap.innerHTML = `<div class="empty-state">⚠️ ${msg}</div>`;
    return;
  }
  _gbSportEvents = data.events || [];
  gbRenderSportEvents();
}

async function gbLoadSportProviderCatalog() {
  const wrap = document.getElementById('gb-sport-provider-list');
  if (wrap) wrap.innerHTML = '<div class="loading-center"><span class="spinner"></span></div>';
  const { ok, data } = await apiWithTimeout('GET', '/api/admin/game-builder/sport-provider/catalog');
  if (!ok) {
    if (wrap) wrap.innerHTML = `<div class="empty-state">${esc(data?.error || t('common.error', 'Error'))}</div>`;
    return;
  }
  _gbProvCatalog = data.events || [];
  _gbProvVisibility = data.visibility || { mode: 'all', allowed_ids: [] };
  const sel = document.getElementById('gb-prov-mode');
  if (sel) sel.value = (_gbProvVisibility.mode === 'listed') ? 'listed' : 'all';
  gbRenderSportProviderCatalog();
}

function gbProvFormatStart(iso) {
  if (!iso) return { day: '—', time: '', raw: '' };
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return { day: '—', time: '', raw: String(iso) };
  return {
    day: d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }),
    time: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    raw: iso,
  };
}

function gbRenderSportProviderCatalog() {
  const wrap = document.getElementById('gb-sport-provider-list');
  if (!wrap) return;
  const onlyProv = _gbProvCatalog.filter(e => e.is_provider);
  if (!onlyProv.length) {
    wrap.innerHTML = `<div class="empty-state">${t('games_builder.provider_catalog_empty', 'Нет событий провайдера — проверьте sport_odds_api.json и ключ API')}</div>`;
    return;
  }
  wrap.innerHTML = onlyProv.map((row) => {
    const { day, time } = gbProvFormatStart(row.starts_at);
    const vis = row.visible_on_site;
    const btn = vis
      ? `<button type="button" class="btn-icon" data-gbp-action="hide" data-gbp-id="${esc(row.id)}">${t('games_builder.provider_hide', 'Скрыть')}</button>`
      : `<button type="button" class="panel-btn panel-btn--primary" data-gbp-action="show" data-gbp-id="${esc(row.id)}">${t('games_builder.provider_show', 'Показать')}</button>`;
    const odds = row.odds || {};
    const oc = `H ${odds.home ?? '—'} · X ${odds.draw ?? '—'} · A ${odds.away ?? '—'}`;
    const st = String(row.match_status || '');
    const visLbl = vis ? t('games_builder.provider_visible_yes', 'На сайте') : t('games_builder.provider_visible_no', 'Скрыто');
    const stream = String(row.stream_url || '').trim();
    const source = String(row.stream_source || '').trim();
    const streamRow = source === 'provider'
      ? `<div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
          <div style="font-size:11px;color:var(--t2)">${t('games_builder.provider_stream', 'Stream')} · <span style="color:var(--a)">${t('games_builder.provider_stream_auto','Auto')}</span></div>
          ${stream ? `<a href="${esc(stream)}" target="_blank" style="font-size:12px;color:var(--t1);word-break:break-all">${esc(stream)}</a>` : ''}
        </div>`
      : `<div style="margin-top:8px;display:flex;flex-direction:column;gap:6px">
          <div style="font-size:11px;color:var(--t2)">${t('games_builder.provider_stream', 'Stream')}</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <input class="panel-inp" data-gbp-stream-id="${esc(row.id)}" placeholder="YouTube URL or embed URL" value="${esc(stream)}" style="min-width:240px;flex:1">
            <button type="button" class="tbl-filter-btn" data-gbp-action="save_stream" data-gbp-id="${esc(row.id)}">${t('games_builder.btn_save', 'Save')}</button>
            ${stream ? `<button type="button" class="btn-icon" data-gbp-action="clear_stream" data-gbp-id="${esc(row.id)}">${t('common.remove', 'Remove')}</button>` : ''}
          </div>
        </div>`;
    return `
    <div class="stat-card gb-prov-event-card">
      <div class="gb-prov-event-grid">
        <div class="gb-prov-when">
          <div class="gb-prov-when-label">${t('games_builder.provider_when_col', 'Когда')}</div>
          <div class="gb-prov-when-day">${esc(day)}</div>
          <div class="gb-prov-when-time">${esc(time)}</div>
        </div>
        <div class="gb-prov-main">
          <div class="gb-prov-teams">${esc(row.home_team)} <span class="gb-prov-vs">vs</span> ${esc(row.away_team)}</div>
          <div class="gb-prov-meta">${esc(row.league || '')}${row.country ? ' · ' + esc(row.country) : ''}</div>
          <div class="gb-prov-sub">${esc(row.id)} · ${esc(st)} · tag: ${esc(row.tag || '')} · <span class="${vis ? 'gb-prov-vis-on' : 'gb-prov-vis-off'}">${visLbl}</span></div>
          <div class="gb-prov-odds">${esc(oc)}</div>
          ${streamRow}
        </div>
        <div class="gb-prov-actions">${btn}</div>
      </div>
    </div>`;
  }).join('');
}

async function gbSportProviderToggle(id, show) {
  const { ok, data } = await api('POST', '/api/admin/game-builder/sport-provider/toggle', { event_id: id, visible: show });
  if (!ok) { showToast(data?.error || 'Error', 'error'); return; }
  showToast(t('games_builder.provider_saved', 'Сохранено'), 'success');
  gbLoadSportProviderCatalog();
}

async function gbSportProviderSetMode() {
  const sel = document.getElementById('gb-prov-mode');
  const mode = sel && sel.value === 'listed' ? 'listed' : 'all';
  const { ok, data } = await api('POST', '/api/admin/game-builder/sport-provider/visibility', { mode });
  if (!ok) { showToast(data?.error || 'Error', 'error'); return; }
  showToast(t('games_builder.provider_saved', 'Сохранено'), 'success');
  gbLoadSportProviderCatalog();
}
function gbRenderSportEvents() {
  const wrap = document.getElementById('gb-sport-list');
  if (!wrap) return;
  if (!_gbSportEvents.length) {
    wrap.innerHTML = `<div class="empty-state">${t('games_builder.no_sport_events','No sport events')}</div>`;
    return;
  }
  wrap.innerHTML = _gbSportEvents.map(e => {
    const rawLogo = e.background || e.cover || '';
    const logo = rawLogo.startsWith('/static/')
      ? `/api/admin/banners/casino-image?path=${encodeURIComponent(rawLogo.replace(/^\/static\//, ''))}`
      : rawLogo;
    const logoHtml = logo
      ? `<img src="${esc(logo)}" style="width:64px;height:64px;border-radius:10px;object-fit:cover;border:1px solid var(--bdr);background:var(--bg2)">`
      : '';
    const liveBadge = e.match_status === 'live'
      ? `<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;background:rgba(255,69,96,.12);border:1px solid rgba(255,69,96,.25);color:var(--red);font-size:11px">● LIVE</span>`
      : `<span style="display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:999px;background:rgba(255,255,255,.05);border:1px solid var(--bdr);color:var(--t2);font-size:11px">PLANNED</span>`;
    return `
    <div class="stat-card" style="margin-bottom:10px;padding:12px">
      <div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap">
        <div style="display:flex;gap:12px;min-width:320px;flex:1">
          <div style="flex-shrink:0">${logoHtml || `<div style="width:64px;height:64px;border-radius:10px;border:1px dashed var(--bdr);display:flex;align-items:center;justify-content:center;color:var(--t3)">🏟️</div>`}</div>
          <div style="min-width:220px">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:2px">
              <div style="font-weight:700">${esc(e.home_team)} vs ${esc(e.away_team)}</div>
              ${liveBadge}
            </div>
            <div style="font-size:12px;color:var(--t2)">${esc(e.league)} | ${esc(e.starts_at || '')}</div>
            <div style="font-size:12px;color:var(--t2)">🏷 ${esc(e.tag || '-')} | 📂 ${esc(e.category || '-')} | 🌍 ${esc(e.country || '-')} | type: ${esc(e.sport_type || 'sport')}</div>
            <div style="font-size:12px;color:var(--t2)">Odds: H ${e.odds?.home ?? '-'} | D ${e.odds?.draw ?? '-'} | A ${e.odds?.away ?? '-'}</div>
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <button class="btn-icon" data-gbs-action="edit" data-gbs-id="${esc(e.id)}">${t('games_builder.btn_edit','Edit')}</button>
          <button class="btn-icon" data-gbs-action="toggle" data-gbs-id="${esc(e.id)}" data-gbs-enabled="${e.enabled ? '1' : '0'}">${e.enabled ? t('games_builder.btn_toggle_off','Disable') : t('games_builder.btn_toggle_on','Enable')}</button>
          <button class="btn-icon" style="color:var(--red)" data-gbs-action="delete" data-gbs-id="${esc(e.id)}">${t('games_builder.btn_delete','Delete')}</button>
        </div>
      </div>
    </div>
  `;
  }).join('');
}

function gbHandleSportListClick(e) {
  const btn = e.target.closest('[data-gbs-action]');
  if (!btn) return;
  const id = btn.dataset.gbsId;
  if (!id) return;
  if (btn.dataset.gbsAction === 'edit') {
    const ev = _gbSportEvents.find(x => String(x.id) === String(id));
    if (ev) {
      gbShowSportForm(true);
      const set = (eid, v) => { const el = document.getElementById(eid); if (el) el.value = v ?? ''; };
      set('gb-sp-id', ev.id || '');
      set('gb-sp-league', ev.league || '');
      set('gb-sp-tag', ev.tag || 'football');
      gbApplyTagDefaults(ev.tag || 'football');
      set('gb-sp-category', ev.category || '');
      set('gb-sp-country', ev.country || '');
      set('gb-sp-sport-type', ev.sport_type || 'sport');
      set('gb-sp-home', ev.home_team || '');
      set('gb-sp-away', ev.away_team || '');
      const dt = ev.starts_at ? new Date(ev.starts_at) : null;
      if (dt && !Number.isNaN(dt.getTime())) {
        const local = new Date(dt.getTime() - dt.getTimezoneOffset() * 60000).toISOString().slice(0,16);
        set('gb-sp-time', local);
      } else {
        set('gb-sp-time', '');
      }
      set('gb-sp-cover', ev.background || ev.cover || '');
      set('gb-sp-icon', ev.icon_png || '');
      set('gb-sp-stream', (ev.match_center && ev.match_center.video_embed_url) || '');
      const liveEl = document.getElementById('gb-sp-live');
      if (liveEl) liveEl.checked = Boolean(ev.is_live_stream || ev.match_status === 'live');
      const topEl = document.getElementById('gb-sp-top-match');
      if (topEl) topEl.checked = Boolean(ev.match_today);
      set('gb-sp-mode', ev.mode || 'manual');
      set('gb-sp-auto-source', ev.automation?.source_url || '');
      set('gb-sp-auto-refresh', ev.automation?.refresh_sec ?? '15');
      set('gb-sp-auto-notes', ev.automation?.notes || '');
      const coverName = document.getElementById('gb-sp-cover-file-name');
      if (coverName) coverName.textContent = 'No file selected';
      const iconName = document.getElementById('gb-sp-icon-file-name');
      if (iconName) iconName.textContent = 'No file selected';
      gbToggleModeFields();
      set('gb-sp-odd-home', ev.odds?.home ?? '0');
      set('gb-sp-odd-draw', ev.odds?.draw ?? '0');
      set('gb-sp-odd-away', ev.odds?.away ?? '0');
    }
  }
  if (btn.dataset.gbsAction === 'toggle') gbToggleSportEvent(id, btn.dataset.gbsEnabled !== '1');
  if (btn.dataset.gbsAction === 'delete') gbDeleteSportEvent(id);
}

function gbHandleProviderCatalogClick(e) {
  const btn = e.target.closest('[data-gbp-action]');
  if (!btn) return;
  const id = btn.dataset.gbpId;
  const act = btn.dataset.gbpAction;
  if (!id || !act) return;
  if (act === 'show') gbSportProviderToggle(id, true);
  if (act === 'hide') gbSportProviderToggle(id, false);
  if (act === 'save_stream') {
    const inp = document.querySelector(`[data-gbp-stream-id="${CSS.escape(id)}"]`);
    const raw = (inp?.value || '').trim();
    gbSportProviderSaveStream(id, raw);
  }
  if (act === 'clear_stream') {
    gbSportProviderSaveStream(id, '');
  }
}

async function gbSportProviderSaveStream(id, url) {
  const { ok, data } = await api('POST', '/api/admin/game-builder/sport-provider/stream', { event_id: id, video_embed_url: url });
  if (!ok) { showToast(data?.error || 'Error', 'error'); return; }
  showToast(t('games_builder.provider_saved', 'Сохранено'), 'success');
  gbLoadSportProviderCatalog();
}

async function gbSaveSportEvent() {
  const dt = document.getElementById('gb-sp-time')?.value;
  const iso = dt ? (Number.isNaN(new Date(dt).getTime()) ? '' : new Date(dt).toISOString()) : '';
  const body = {
    id: document.getElementById('gb-sp-id')?.value.trim() || undefined,
    league: document.getElementById('gb-sp-league')?.value.trim(),
    tag: document.getElementById('gb-sp-tag')?.value.trim() || 'football',
    category: document.getElementById('gb-sp-category')?.value.trim(),
    country: document.getElementById('gb-sp-country')?.value.trim(),
    sport_type: document.getElementById('gb-sp-sport-type')?.value.trim() || 'sport',
    home_team: document.getElementById('gb-sp-home')?.value.trim(),
    away_team: document.getElementById('gb-sp-away')?.value.trim(),
    starts_at: iso,
    match_td_utc: iso,
    background: document.getElementById('gb-sp-cover')?.value.trim(),
    cover: document.getElementById('gb-sp-cover')?.value.trim(),
    icon_png: document.getElementById('gb-sp-icon')?.value.trim(),
    is_live_stream: Boolean(document.getElementById('gb-sp-live')?.checked),
    match_today: Boolean(document.getElementById('gb-sp-top-match')?.checked),
    mode: document.getElementById('gb-sp-mode')?.value || 'manual',
    match_status: document.getElementById('gb-sp-live')?.checked ? 'live' : 'planned',
    automation: {
      source_url: document.getElementById('gb-sp-auto-source')?.value.trim(),
      refresh_sec: Number(document.getElementById('gb-sp-auto-refresh')?.value || '15'),
      notes: document.getElementById('gb-sp-auto-notes')?.value.trim(),
    },
    odds: {
      home: Number(document.getElementById('gb-sp-odd-home')?.value || '0'),
      draw: Number(document.getElementById('gb-sp-odd-draw')?.value || '0'),
      away: Number(document.getElementById('gb-sp-odd-away')?.value || '0'),
    },
    match_center: {
      video_embed_url: document.getElementById('gb-sp-stream')?.value.trim(),
      video_enabled: Boolean(document.getElementById('gb-sp-live')?.checked),
    },
  };
  const { ok, data } = await api('POST', '/api/admin/game-builder/sport-events', body);
  if (!ok) { showToast(data.error || 'Error', 'error'); return; }
  showToast('Event saved', 'success');
  gbShowSportForm(false);
  gbLoadSportEvents();
}
async function gbToggleSportEvent(id, enabled) {
  const { ok, data } = await api('POST', `/api/admin/game-builder/sport-events/${id}/toggle`, { enabled });
  if (!ok) { showToast(data.error || 'Error', 'error'); return; }
  showToast(enabled ? 'Enabled' : 'Disabled', 'success');
  gbLoadSportEvents();
}
async function gbDeleteSportEvent(id) {
  showConfirm(`Delete <strong>${esc(id)}</strong>?`, async () => {
    const { ok, data } = await api('DELETE', `/api/admin/game-builder/sport-events/${id}`);
    if (!ok) { showToast(data.error || 'Error', 'error'); return; }
    showToast('Deleted', 'success');
    gbLoadSportEvents();
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  PERMISSIONS SYSTEM  (/config/perms.json)
// ══════════════════════════════════════════════════════════════════════════════
let _perms = null;

async function loadPerms() {
  try {
    const ctrl = new AbortController();
    const tid  = setTimeout(() => ctrl.abort(), 4000);
    const r = await fetch('/config/perms.json', { signal: ctrl.signal });
    clearTimeout(tid);
    if (r.ok) _perms = await r.json();
  } catch(e) { console.warn('[perms] load error (non-fatal):', e.message || e); }
}

function _rolePerms(role) {
  if (!_perms || !role) return null;
  // Поиск без учёта регистра
  const key = Object.keys(_perms.roles || {}).find(k => k.toLowerCase() === role.toLowerCase());
  return key ? _perms.roles[key] : null;
}

function canDo(feature) {
  if (!_admin) return false;
  const rp = _rolePerms(_admin.role);
  if (!rp) return false;
  if (rp.tabs_all) return true;
  return !!(rp.features && rp.features[feature]);
}

function hasTab(tab) {
  if (!_admin) return false;
  const rp = _rolePerms(_admin.role);
  if (!rp) return false;
  if (rp.tabs_all) return true;
  return Array.isArray(rp.tabs) && rp.tabs.includes(tab);
}

// Всегда видимые вкладки (независимо от роли)
const _ALWAYS_TABS = ['dashboard', 'chat', 'reports'];

function applyNavPerms() {
  if (!_admin) return;
  // If perms failed to load, don't hide everything — show at least current page + always-tabs
  const permsLoaded = !!_perms;
  document.querySelectorAll('#ps-nav .ps-item[data-tab]').forEach(el => {
    const tab = el.dataset.tab;
    if (_ALWAYS_TABS.includes(tab)) { el.classList.remove('hidden'); return; }
    // If current page tab — always show regardless of perms load failure
    if (tab === PAGE) { el.classList.remove('hidden'); return; }
    if (!permsLoaded) {
      // perms.json failed to load: keep existing visibility (don't nuke sidebar)
      return;
    }
    if (hasTab(tab)) el.classList.remove('hidden');
    else el.classList.add('hidden');
  });
  // Role badge
  const badge = document.getElementById('ps-role-badge');
  if (badge && _admin.role) {
    badge.textContent = _admin.role.toUpperCase();
    badge.className = `ps-role-badge role-tag role-${_admin.role}`;
    badge.classList.remove('hidden');
  }
}

// ── Патч updateAdminUI уже определён выше (заменили оригинал)



// ══════════════════════════════════════════════════════════════════════════════
//  USER DETAIL — переопределяем initUserDetail
// ══════════════════════════════════════════════════════════════════════════════
const _origInitUserDetail = initUserDetail;
async function initUserDetail() {
  const uid = document.body.dataset.userId;
  if (!uid) return;
  const { ok, data } = await api('GET', `/api/tickets/users/${uid}`);
  if (!ok) { showToast(t('common.error'),'error'); return; }
  const { user, transactions, tickets } = data;

  setEl('u-name',    user.user_name || '—');
  setEl('u-email',   user.email || '—');
  setEl('u-phone',   user.phone || t('player_profile.not_set','—'));
  setEl('u-balance', `${user.balance ?? 0} ${user.currency || ''}`);
  setEl('u-kyc',     kycLabel(user.kyc_status));
  setEl('u-country', user.country || '—');
  setEl('u-currency', user.currency || '—');
  setEl('u-verified', user.verified ? `✓ ${t('common.yes','Yes')}` : `✗ ${t('common.no','No')}`);
  setEl('u-created', fmtDate(user.created_at));
  setEl('u-id',      `#${user.id}`);
  // VIP fields
  const vipTier = (user.vip_tier || '').trim();
  const vipExp = user.vip_expires_at || user.vip_expires || null;
  const vipStatus = !!vipTier && vipTier.toLowerCase() !== 'none';
  setEl('u-vip-tier', vipStatus ? vipTier.toUpperCase() : t('player_profile.no_vip','—'));
  setEl('u-verified', vipStatus ? `♛ ${vipTier.toUpperCase()}` : t('player_profile.no_vip','None'));
  setEl('u-vip-exp', vipExp ? fmtDate(vipExp) : (vipStatus ? '∞' : '—'));

  // Avatar initials
  const av = document.getElementById('ud-avatar');
  if (av && user.user_name) {
    const ini = user.user_name.slice(0,2).toUpperCase();
    av.innerHTML = `<span style="font-family:var(--ffd);font-size:17px;font-weight:700;color:var(--a)">${ini}</span>`;
  }
  // VIP badge
  if (vipStatus) {
    document.getElementById('ud-vip-tag')?.classList.remove('hidden');
  }

  // Action bar
  const bar = document.getElementById('ud-action-bar');
  if (bar) bar.style.display = 'flex';

  // GET-DB
  if (canDo('get_db')) {
    const b = document.getElementById('btn-get-db');
    b?.classList.remove('hidden');
    b?.addEventListener('click', () => openGetDbModal(uid, user));
  }
  // Give VIP
  if (canDo('give_vip')) {
    const b = document.getElementById('btn-give-vip');
    b?.classList.remove('hidden');
    b?.addEventListener('click', () => document.getElementById('modal-vip')?.classList.remove('hidden'));
    _initVipModal(uid);
  }
  // One-time bonus
  if (canDo('one_time_bonus')) {
    const b = document.getElementById('btn-give-bonus');
    b?.classList.remove('hidden');
    b?.addEventListener('click', () => document.getElementById('modal-bonus')?.classList.remove('hidden'));
    _initBonusModal(uid);
  }

  // Balance topup
  if (canDo('balance_topup')) {
    document.getElementById('topup-card')?.classList.remove('hidden');
    _initTopupCard(uid);
  }

  // Transactions
  const txBody = document.getElementById('tx-body');
  if (txBody) txBody.innerHTML = transactions?.length
    ? transactions.map(tx => `<tr>
        <td class="td-muted">${fmtDate(tx.created_at)}</td>
        <td>${esc(tx.type)}</td>
        <td style="color:${tx.amount>=0?'var(--green)':'var(--red)'}">${tx.amount>0?'+':''}${tx.amount}</td>
        <td><span class="badge badge-${tx.status==='completed'?'verified':'pending'}">${tx.status}</span></td>
      </tr>`).join('')
    : `<tr><td colspan="4" class="td-muted" style="text-align:center;padding:20px">${t('users.no_transactions','Нет транзакций')}</td></tr>`;

  // Game history
  const ghBody = document.getElementById('game-history-body');
  if (ghBody) ghBody.innerHTML = gameHistory?.length
    ? gameHistory.map(row => {
        const bet = Number(row.bet || 0);
        const win = Number(row.win || 0);
        const isWin = win > bet;
        const resultKey = isWin ? 'player_profile.result_win' : 'player_profile.result_loss';
        return `<tr>
          <td class="td-muted">${fmtDate(row.created_at)}</td>
          <td>${esc(row.game_name || row.game_id || '—')}</td>
          <td>${bet}</td>
          <td style="color:${win > 0 ? 'var(--green)' : 'var(--t2)'}">${win}</td>
          <td><span class="badge badge-${isWin ? 'verified' : 'closed'}">${t(resultKey, isWin ? 'Win' : 'Loss')}</span></td>
        </tr>`;
      }).join('')
    : `<tr><td colspan="5" class="td-muted" style="text-align:center;padding:20px">${t('player_profile.no_game_history','No game history')}</td></tr>`;

  // Tickets
  const tkBody = document.getElementById('user-tickets-body');
  if (tkBody) tkBody.innerHTML = tickets?.length
    ? tickets.map(tk => `<tr>
        <td class="td-muted">${tk.ticket_uuid || '#'+tk.id}</td>
        <td>${esc(tk.subject||'—')}</td>
        <td><span class="badge badge-${tk.status==='open'?'open':tk.status==='in_progress'?'progress':'closed'}">${statusLabel(tk.status)}</span></td>
        <td><button class="btn-icon" onclick="location.href='/panel/ticket/${tk.id}'">${t('tickets.open_btn','Открыть')}</button></td>
      </tr>`).join('')
    : `<tr><td colspan="4" class="td-muted" style="text-align:center;padding:20px">${t('users.no_player_tickets','Нет тикетов')}</td></tr>`;
}

function _initTopupCard(uid) {
  // Quick buttons
  document.querySelectorAll('.ud-topup-qbtn').forEach(btn => {
    btn.addEventListener('click', () => {
      const inp = document.getElementById('topup-amount');
      if (inp) inp.value = btn.dataset.val;
    });
  });
  // Sign toggle
  document.querySelectorAll('.ud-topup-sign').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ud-topup-sign').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
  // Apply
  document.getElementById('topup-btn')?.addEventListener('click', async function() {
    const rawVal = document.getElementById('topup-amount')?.value.trim();
    const reason = document.getElementById('topup-reason')?.value.trim() || '';
    const sign   = document.querySelector('.ud-topup-sign.active')?.dataset.sign;
    let amount   = parseFloat(rawVal);
    if (!amount || isNaN(amount) || amount <= 0) { showToast('Введи корректную сумму', 'error'); return; }
    if (sign === 'minus') amount = -amount;
    const lbl = document.getElementById('topup-btn-lbl');
    this.disabled = true;
    if (lbl) lbl.textContent = '...';
    const { ok: okT, data: dT } = await api('POST', `/api/admin/users/${uid}/topup`, { amount, reason });
    this.disabled = false;
    if (lbl) lbl.textContent = t('users.topup_btn', 'Применить');
    if (!okT) { showToast(dT?.error || t('common.error'), 'error'); return; }
    const newBal = `${dT.new_balance} ${dT.currency || ''}`;
    setEl('u-balance', newBal);
    document.getElementById('topup-amount').value = '';
    document.getElementById('topup-reason').value = '';
    document.querySelectorAll('.ud-topup-sign').forEach(b => b.classList.remove('active'));
    document.getElementById('topup-sign-p')?.classList.add('active');
    showToast(`✓ Баланс обновлён: ${newBal}`, 'success');
  });
}

function _initVipModal(uid) {
  // VIP tier definitions matching vip.json (icon_url not available here, use SVG badges)
  const VIP_TIERS = {
    bronze:   { color: '#cd7f32', icon: '/api/admin/banners/casino-image?path=uploads/vip/bronze.png' },
    silver:   { color: '#a8a9ad', icon: '/api/admin/banners/casino-image?path=uploads/vip/silver.png' },
    gold:     { color: '#f5c518', icon: '/api/admin/banners/casino-image?path=uploads/vip/gold.png' },
    platinum: { color: '#00e5ff', icon: '/api/admin/banners/casino-image?path=uploads/vip/platinum.png' },
    diamond:  { color: '#b388ff', icon: '/api/admin/banners/casino-image?path=uploads/vip/diamond.png' },
  };
  // Re-render VIP tier buttons with proper icons + colours
  const tiersWrap = document.getElementById('vip-tiers');
  if (tiersWrap) {
    tiersWrap.innerHTML = Object.entries(VIP_TIERS).map(([tier, cfg]) => `
      <button class="ud-vip-tier${tier==='platinum'?' active':''}" data-tier="${tier}" style="border-color:${cfg.color}40;--tier-color:${cfg.color}">
        <span class="ud-tier-ico" style="color:${cfg.color}"><img src="${cfg.icon}" alt="${tier}" style="width:18px;height:18px;object-fit:contain" onerror="this.style.display='none'"></span>
        <span style="font-size:11px;color:${cfg.color}">${t('vip_modal.tier_'+tier, tier.charAt(0).toUpperCase()+tier.slice(1))}</span>
      </button>`).join('');
    tiersWrap.querySelectorAll('.ud-vip-tier').forEach(btn => {
      btn.addEventListener('click', () => {
        tiersWrap.querySelectorAll('.ud-vip-tier').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const cfg = VIP_TIERS[btn.dataset.tier];
        if (cfg) {
          const applyBtn = document.getElementById('vip-apply-btn');
          if (applyBtn) applyBtn.style.setProperty('--tier-color', cfg.color);
        }
      });
    });
  }

  document.querySelectorAll('.ud-vip-dur').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.ud-vip-dur').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  document.getElementById('vip-apply-btn')?.addEventListener('click', async function() {
    const tier = document.querySelector('.ud-vip-tier.active')?.dataset.tier || 'platinum';
    const days = parseInt(document.querySelector('.ud-vip-dur.active')?.dataset.days ?? '30');
    const origText = this.textContent;
    this.disabled = true; this.textContent = '...';
    const { ok, data } = await api('POST', `/api/admin/users/${uid}/vip`, { tier, days });
    this.disabled = false;
    this.textContent = t('vip_modal.btn_give', 'Grant VIP');
    if (!ok) { showToast(data?.error || t('common.error','Error'), 'error'); return; }
    const tierLabel = t('vip_modal.tier_'+tier, tier.toUpperCase());
    showToast(`♛ VIP ${tierLabel} — ${t('vip_modal.success','Granted')}`, 'success');
    document.getElementById('modal-vip')?.classList.add('hidden');
    document.getElementById('ud-vip-tag')?.classList.remove('hidden');
    // Update profile fields
    const vipTag = document.getElementById('ud-vip-tag');
    if (vipTag) vipTag.textContent = `♛ ${tierLabel}`;
    setEl('u-vip-tier', tierLabel);
    if (days === 0) {
      setEl('u-vip-exp', '∞');
    } else {
      const exp = new Date(Date.now() + days * 86400000);
      setEl('u-vip-exp', fmtDate(exp.toISOString()));
    }
    const resultEl = document.getElementById('vip-result');
    if (resultEl) {
      resultEl.textContent = `✓ VIP ${tierLabel}`;
      resultEl.style.color = 'var(--green)';
      resultEl.classList.remove('hidden');
      setTimeout(() => resultEl.classList.add('hidden'), 3000);
    }
  });
}

function _initBonusModal(uid) {
  document.querySelectorAll('.ud-bonus-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.ud-bonus-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.ud-bonus-panel').forEach(p => p.classList.add('hidden'));
      tab.classList.add('active');
      document.getElementById(`bonus-panel-${tab.dataset.type}`)?.classList.remove('hidden');
    });
  });
  document.getElementById('bonus-apply-btn')?.addEventListener('click', async function() {
    const type = document.querySelector('.ud-bonus-tab.active')?.dataset.type || 'cash';
    let payload = { type };
    if (type === 'cash') {
      payload.amount = parseFloat(document.getElementById('bonus-cash-amount')?.value || '0');
      payload.note = document.getElementById('bonus-cash-note')?.value.trim() || '';
      if (!payload.amount || isNaN(payload.amount)) { showToast('Введи сумму', 'error'); return; }
    } else if (type === 'freespins') {
      payload.count = parseInt(document.getElementById('bonus-fs-count')?.value || '0');
      payload.game  = document.getElementById('bonus-fs-game')?.value.trim() || '';
      if (!payload.count) { showToast('Введи количество фриспинов', 'error'); return; }
    } else if (type === 'deposit_boost') {
      payload.percent = parseInt(document.getElementById('bonus-dep-pct')?.value || '0');
      payload.hours   = parseInt(document.getElementById('bonus-dep-hours')?.value || '24');
      if (!payload.percent) { showToast('Введи процент', 'error'); return; }
    } else if (type === 'extend_vip') {
      payload.days = parseInt(document.getElementById('bonus-vip-days')?.value || '0');
      if (!payload.days) { showToast('Введи количество дней', 'error'); return; }
    }
    this.disabled = true; this.textContent = '...';
    const { ok, data } = await api('POST', `/api/admin/users/${uid}/bonus`, payload);
    this.disabled = false; this.textContent = t('bonus_modal.btn_give', 'Give Bonus');
    if (!ok) { showToast(data?.error || t('common.error','Error'), 'error'); return; }
    const typeLabel = t('bonus_modal.type_'+type, type);
    showToast(`✓ ${t('bonus_modal.success','Bonus granted')}: ${typeLabel}`, 'success');
    document.getElementById('modal-bonus')?.classList.add('hidden');
    const resultEl = document.getElementById('bonus-result');
    if (resultEl) {
      resultEl.textContent = `✓ ${typeLabel}`;
      resultEl.style.color = 'var(--green)';
      resultEl.classList.remove('hidden');
      setTimeout(() => resultEl.classList.add('hidden'), 3000);
    }
    // Refresh balance if cash bonus
    if (type === 'cash' && data.new_balance !== undefined) {
      const { ok: ok2, data: d2 } = await api('GET', `/api/tickets/users/${uid}`);
      if (ok2) setEl('u-balance', `${d2.user?.balance ?? data.new_balance} ${d2.user?.currency || ''}`);
    }
  });
}

// ── GET-DB Modal ─────────────────────────────────────────────────────────────
let _dbReportText = '';

function openGetDbModal(uid, user) {
  const modal  = document.getElementById('modal-getdb');
  const output = document.getElementById('db-output');
  const copyRow = document.getElementById('db-copy-row');
  const prog   = document.getElementById('db-progress');
  if (!modal) return;

  setEl('db-uid-lbl', `UID #${uid} — ${user?.user_name || '...'}`);
  output?.classList.add('hidden');
  copyRow?.classList.add('hidden');
  if (prog) prog.style.display = 'flex';

  // Reset steps
  for (let i=1; i<=4; i++) {
    const s = document.getElementById(`db-step-${i}`);
    if (s) { s.classList.remove('active','done'); if(i===1) s.classList.add('active'); }
  }
  modal.classList.remove('hidden');

  // Scan btn
  const btn = document.getElementById('btn-get-db');
  btn?.classList.add('scanning');

  let step = 1;
  const iv = setInterval(() => {
    document.getElementById(`db-step-${step}`)?.classList.replace('active','done');
    step++;
    if (step <= 4) {
      document.getElementById(`db-step-${step}`)?.classList.add('active');
    } else {
      clearInterval(iv);
      btn?.classList.remove('scanning');
      if (prog) prog.style.display = 'none';
      _buildDbReport(uid, user, output, copyRow);
    }
  }, 550);
}

function _buildDbReport(uid, user, outputEl, copyRowEl) {
  if (!outputEl) return;
  const now = new Date().toISOString();
  const pwHash = user?.password_hash || `$2b$12$VX${uid}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`.slice(0,60);
  const lines = [
    ['━━━ ACCOUNT PROFILE ━━━', [
      ['ID',            `#${uid}`],
      ['Username',      user?.user_name || '—'],
      ['Email',         user?.email || '—'],
      ['Phone',         user?.phone || 'not set'],
      ['Registered',    fmtDate(user?.created_at)],
      ['Last Login',    user?.last_login ? fmtDate(user.last_login) : 'unknown'],
      ['Country',       user?.country || '—'],
      ['Language',      user?.lang || 'ru'],
    ]],
    ['━━━ VERIFICATION ━━━', [
      ['KYC Status',    kycLabel(user?.kyc_status)],
      ['Email Verified',user?.verified ? 'YES' : 'NO'],
      ['Phone Verified',user?.phone_verified ? 'YES' : 'NO'],
      ['2FA',           user?.totp_enabled ? 'ENABLED' : 'disabled'],
    ]],
    ['━━━ FINANCIAL ━━━', [
      ['Balance',       `${user?.balance ?? 0} ${user?.currency || 'USD'}`],
      ['Total Deposited',user?.total_deposits ?? '—'],
      ['Total Withdrawn',user?.total_withdrawals ?? '—'],
      ['Total Bonuses', user?.total_bonuses ?? '—'],
      ['VIP Status',    user?.vip_status || 'none'],
      ['VIP Tier',      user?.vip_tier || '—'],
      ['VIP Expires',   user?.vip_expires ? fmtDate(user.vip_expires) : '—'],
    ]],
    ['━━━ BETTING ACTIVITY ━━━', [
      ['Total Bets',    user?.total_bets ?? '—'],
      ['Bet Volume',    user?.total_bet_amount ?? '—'],
      ['Total Won',     user?.total_won ?? '—'],
      ['Biggest Win',   user?.biggest_win ?? '—'],
      ['Slots Bets',    user?.slots_count ?? '—'],
      ['Crash Bets',    user?.crash_count ?? '—'],
      ['Cards Bets',    user?.cards_count ?? '—'],
      ['Blackjack',     user?.blackjack_count ?? '—'],
      ['Sport Bets',    user?.sport_count ?? '—'],
      ['Live Casino',   user?.live_count ?? '—'],
    ]],
    ['━━━ SECURITY ━━━', [
      ['Status',        user?.is_banned ? '⛔ BANNED' : user?.is_locked ? '🔒 LOCKED' : '✓ ACTIVE'],
      ['Ban Reason',    user?.ban_reason || '—'],
      ['Reports',       user?.reports_count ?? 0],
      ['Chargebacks',   user?.chargebacks ?? 0],
      ['Password Hash', pwHash, 'hash'],
    ]],
    ['━━━ GENERATED ━━━', [
      ['Timestamp',     now],
      ['Requested by',  `${_admin?.nickname || 'unknown'} [${(_admin?.role || '?').toUpperCase()}]`],
    ]],
  ];

  let html = '';
  let text = '';
  lines.forEach(([title, rows]) => {
    html += `<span class="ud-db-section">${title}</span>`;
    text += `\n${title}\n`;
    rows.forEach(([k, v, type]) => {
      const kPad = (k + ':').padEnd(18);
      const vEsc = esc(String(v));
      if (type === 'hash') {
        html += `  <span class="ud-db-hash">${kPad} ${vEsc}</span>\n`;
      } else {
        html += `  ${kPad} ${vEsc}\n`;
      }
      text += `  ${kPad} ${v}\n`;
    });
  });
  _dbReportText = text;
  outputEl.innerHTML = html;
  outputEl.classList.remove('hidden');
  copyRowEl?.classList.remove('hidden');
}

function copyDbReport() {
  navigator.clipboard.writeText(_dbReportText).then(() => showToast('Отчёт скопирован', 'success'));
}

function closeGetDb() {
  document.getElementById('modal-getdb')?.classList.add('hidden');
  document.getElementById('btn-get-db')?.classList.remove('scanning');
}

// ══════════════════════════════════════════════════════════════════════════════
//  BANNERS PAGE
// ══════════════════════════════════════════════════════════════════════════════
let _bnFile = null;
let _bnBanners = [];
let _bnEditId = null;
let _bnDragLinkId = null;
let _bnLinkModalState = null;
const _BN_LINKS = [
  { id: 'telegram',  field: 'modal_telegramButton',  label: 'Telegram',  icon: '✈️', color: '#29b6f6' },
  { id: 'youtube',   field: 'modal_youtubeButton',   label: 'YouTube',   icon: '▶️', color: '#f44336' },
  { id: 'viber',     field: 'modal_viberButton',     label: 'Viber',     icon: '📞', color: '#7c4dff' },
  { id: 'whatsapp',  field: 'modal_whatsappButton',  label: 'WhatsApp',  icon: '💬', color: '#25d366' },
  { id: 'instagram', field: 'modal_instagramButton', label: 'Instagram', icon: '📸', color: '#ff4081' },
  { id: 'twitter',   field: 'modal_twitterButton',   label: 'Twitter',   icon: '🐦', color: '#4fc3f7' },
  { id: 'vk',        field: 'modal_vkButton',        label: 'VK',        icon: 'VK', color: '#4c75a3' },
  { id: 'url',       field: 'modal_urlButton',       label: 'URL',       icon: '🔗', color: '#9ca3af' },
];

function _bnGetLinkMetaById(id) { return _BN_LINKS.find(x => x.id === id); }
function _bnGetLinkMetaByField(field) { return _BN_LINKS.find(x => x.field === field); }
function _bnLinkButtonHtml(meta, href) {
  return `<a href="${esc(href)}" target="_blank" rel="noopener" style="display:flex;align-items:center;justify-content:center;gap:7px;padding:9px 0;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none;width:100%;background:${meta.color};color:#fff">${meta.icon} ${meta.label}</a>`;
}
function _bnImageUrl(b) {
  const rawImage = b.image_url || b.image_ad || '';
  return rawImage.startsWith('/static/')
    ? `/api/admin/banners/casino-image?path=${encodeURIComponent(rawImage.replace(/^\/static\//, ''))}`
    : rawImage;
}
function bnEnableSwipe(node) {
  if (!node || node.dataset.dragReady === '1') return;
  node.dataset.dragReady = '1';
  let isDown = false;
  let moved = false;
  let startX = 0;
  let startLeft = 0;
  let activeCard = null;
  let pointerId = null;
  let rafId = 0;
  let pendingLeft = 0;
  const zoomScale = () => Math.max(0.5, Math.min(2, window.visualViewport?.scale || 1));
  const moveAt = (clientX) => {
    const dx = (clientX - startX) / zoomScale();
    if (Math.abs(dx) > 4) moved = true;
    pendingLeft = startLeft - dx;
    if (!rafId) {
      rafId = requestAnimationFrame(() => {
        node.scrollLeft = pendingLeft;
        rafId = 0;
      });
    }
  };
  const endDrag = () => {
    if (!isDown) return;
    isDown = false;
    node.classList.remove('drag-active');
    if (moved && activeCard) activeCard.dataset.bnSkipClick = '1';
    if (pointerId !== null) {
      try { node.releasePointerCapture(pointerId); } catch {}
    }
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    pointerId = null;
  };
  node.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    if (e.target?.closest?.('button, input, a, textarea')) return;
    isDown = true;
    moved = false;
    startX = e.clientX || 0;
    startLeft = node.scrollLeft;
    activeCard = e.target?.closest?.('[data-bn-preview]') || null;
    pointerId = e.pointerId;
    node.classList.add('drag-active');
    try { node.setPointerCapture(pointerId); } catch {}
  });
  node.addEventListener('pointermove', (e) => {
    if (!isDown) return;
    if (pointerId !== null && e.pointerId !== pointerId) return;
    moveAt(e.clientX || 0);
    if (moved) e.preventDefault();
  }, { passive: false });
  node.addEventListener('pointerup', endDrag);
  node.addEventListener('pointercancel', endDrag);
  node.addEventListener('dragstart', (e) => e.preventDefault());
}

async function initBanners() {
  const editImageEl = document.getElementById('bn-edit-image');
  if (editImageEl) editImageEl.setAttribute('data-edit-hint', t('banners.edit_image_hint', 'Tap to change image'));
  document.getElementById('bn-file-trigger')?.addEventListener('click', () => {
    document.getElementById('bn-file')?.click();
  });
  document.getElementById('bn-file')?.addEventListener('change', (e) => {
    const f = e.target.files?.[0];
    _bnFile = f || null;
    bnRenderUploadCard(f || null);
  });
  document.getElementById('bn-save-btn')?.addEventListener('click', bnSaveBanner);
  document.getElementById('bn-refresh-btn')?.addEventListener('click', loadBanners);
  document.getElementById('bn-grid')?.addEventListener('click', bnHandleGridClick);
  document.getElementById('bn-edit-close')?.addEventListener('click', () => document.getElementById('bn-edit-modal')?.classList.remove('on'));
  document.getElementById('bn-view-close')?.addEventListener('click', () => document.getElementById('bn-view-modal')?.classList.remove('on'));
  document.getElementById('bn-edit-save')?.addEventListener('click', bnSaveEditBanner);
  document.getElementById('bn-edit-image')?.addEventListener('click', () => document.getElementById('bn-edit-file')?.click());
  document.getElementById('bn-edit-file')?.addEventListener('change', bnEditUploadImage);
  document.getElementById('bn-link-modal-cancel')?.addEventListener('click', bnCloseLinkModal);
  document.getElementById('bn-link-modal-save')?.addEventListener('click', bnConfirmLinkModal);
  document.getElementById('bn-link-modal-remove')?.addEventListener('click', bnRemoveFromLinkModal);
  const uploadZone = document.getElementById('bn-upload-zone');
  uploadZone?.addEventListener('click', () => document.getElementById('bn-file')?.click());
  uploadZone?.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadZone.classList.add('drag-over');
  });
  uploadZone?.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
  uploadZone?.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadZone.classList.remove('drag-over');
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    const input = document.getElementById('bn-file');
    if (input) {
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
    }
    _bnFile = file;
    bnRenderUploadCard(file);
  });
  bnBindLinkBoardDrop('bn-add-link-board');
  bnBindLinkBoardDrop('bn-link-board');
  bnRenderLinkBank('bn-add-link-bank');
  bnRenderUploadCard(null);
  loadBanners();
}

async function loadBanners() {
  const grid = document.getElementById('bn-grid');
  if (!grid) return;
  grid.innerHTML = '<div class="loading-center"><span class="spinner"></span></div>';
  const { ok, status, data } = await apiWithTimeout('GET', '/api/admin/banners');
  if (!ok) {
    const msg = status === 403
      ? t('common.access_denied', 'Нет доступа')
      : (data?.error || t('common.error', 'Ошибка'));
    grid.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠️</div>${msg}</div>`;
    return;
  }
  _bnBanners = data.banners || [];
  bnRenderPreviewTrack();
  if (!_bnBanners.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🖼</div>${t('banners.no_banners','Баннеров пока нет')}</div>`;
    return;
  }
  grid.innerHTML = _bnBanners.map(b => {
    const active   = b.active !== false && b.invisible !== true;
    const imageUrl = _bnImageUrl(b);
    const title    = esc(b._title || b.title || '—');
    return `
    <div class="banner-card">
      <div class="bn-card-top">
        ${imageUrl ? `<img src="${imageUrl}" alt="${title}">` : `<div class="bn-preview-fallback">🖼</div>`}
      </div>
      <div class="banner-card-body">
        <div class="bn-card-title">${title}</div>
        <div class="banner-card-meta">
          <span class="badge badge-${active ? 'open' : 'closed'}" data-t="${active ? 'banners.status_active' : 'banners.status_hidden'}">${active ? t('banners.status_active','Active') : t('banners.status_hidden','Hidden')}</span>
          <span style="font-size:10px;color:var(--t3)">${esc(b._position || 'main')}</span>
        </div>
        ${b.modal_text ? `<div style="font-size:12px;color:var(--t2);margin-top:4px">${esc(b.modal_text.slice(0,80))}${b.modal_text.length>80?'…':''}</div>` : ''}
      </div>
      <div class="bn-card-actions">
        <button class="btn-icon" data-bn-action="toggle" data-bn-id="${esc(b.id)}" data-bn-active="${active ? '1' : '0'}">${active ? t('banners.btn_toggle_off','Hide') : t('banners.btn_toggle_on','Show')}</button>
        <button class="btn-icon" data-bn-action="edit" data-bn-id="${esc(b.id)}">Edit</button>
        <button class="btn-icon" style="color:var(--red)" data-bn-action="delete" data-bn-id="${esc(b.id)}">${t('banners.btn_delete','Delete')}</button>
      </div>
    </div>`;
  }).join('');
}
function bnRenderPreviewTrack() {
  const track = document.getElementById('bn-preview-track');
  if (!track) return;
  bnEnableSwipe(track);
  if (!_bnBanners.length) {
    track.innerHTML = '<div class="empty-state">No banners yet</div>';
    return;
  }
  track.innerHTML = _bnBanners.map(b => {
    const imageUrl = _bnImageUrl(b);
    const title = esc(b._title || b.title || '—');
    return `<div class="bn-preview-card" data-bn-preview="${esc(b.id)}">
      ${imageUrl ? `<img src="${imageUrl}" alt="${title}" draggable="false">` : `<div class="bn-preview-fallback">${title}</div>`}
      <div class="bn-preview-overlay">${title}</div>
    </div>`;
  }).join('');
  track.querySelectorAll('[data-bn-preview]').forEach(elm => {
    elm.addEventListener('click', () => {
      if (elm.dataset.bnSkipClick === '1') {
        elm.dataset.bnSkipClick = '0';
        return;
      }
      bnOpenPreview(elm.dataset.bnPreview);
    });
    elm.addEventListener('dblclick', () => {
      if (elm.dataset.bnSkipClick === '1') {
        elm.dataset.bnSkipClick = '0';
        return;
      }
      bnOpenPreview(elm.dataset.bnPreview);
    });
  });
}
function bnOpenPreview(id) {
  const b = _bnBanners.find(x => String(x.id) === String(id));
  if (!b) return;
  const imageWrap = document.getElementById('bn-view-image');
  const textEl = document.getElementById('bn-view-text');
  const linksEl = document.getElementById('bn-view-links');
  const imageUrl = _bnImageUrl(b);
  if (imageWrap) imageWrap.innerHTML = imageUrl ? `<img src="${imageUrl}" style="width:100%;height:100%;object-fit:cover">` : '<div class="bn-preview-fallback">No image</div>';
  if (textEl) textEl.textContent = b.modal_text || '';
  if (linksEl) {
    const ordered = Array.isArray(b.link_order) && b.link_order.length
      ? b.link_order.map(id2 => _bnGetLinkMetaById(id2)).filter(Boolean)
      : _BN_LINKS.slice();
    linksEl.innerHTML = ordered
      .map(meta => ({ meta, href: b[meta.field] || '' }))
      .filter(x => x.href)
      .map(x => _bnLinkButtonHtml(x.meta, x.href))
      .join('') || '<div style="color:var(--t3);font-size:12px">No links</div>';
  }
  document.getElementById('bn-view-modal')?.classList.add('on');
}

async function bnSaveBanner() {
  const btn   = document.getElementById('bn-save-btn');
  const errEl = document.getElementById('bn-error');
  const okEl  = document.getElementById('bn-success');
  const title = document.getElementById('bn-title')?.value.trim();
  const modalText = document.getElementById('bn-modal-text')?.value.trim() || '';
  const timer     = document.getElementById('bn-timer')?.value || '5';
  const modalWin  = document.getElementById('bn-modal-window')?.checked !== false;
  if (errEl) { errEl.textContent=''; errEl.classList.add('hidden'); }
  if (okEl)  { okEl.classList.add('hidden'); }
  if (!title) {
    if (errEl) { errEl.textContent = t('banners.err_title','Enter a title'); errEl.classList.remove('hidden'); }
    return;
  }
  const fd = new FormData();
  fd.append('title', title);
  fd.append('modal_text', modalText || title);
  fd.append('timer_change', timer);
  fd.append('modal_window', modalWin ? 'true' : 'false');
  fd.append('position', 'main');
  const { links, order } = bnCollectLinksFromBoard('bn-add-link-board');
  Object.entries(links).forEach(([k, v]) => { if (v) fd.append(k, v); });
  if (order.length) fd.append('link_order', JSON.stringify(order));
  if (_bnFile) fd.append('file', _bnFile);
  if (btn) { btn.disabled=true; btn.textContent = t('common.loading','Saving...'); }
  const r = await apiForm('/api/admin/banners', fd);
  if (btn) { btn.disabled=false; btn.textContent = t('banners.btn_save','Save banner'); }
  if (!r.ok) {
    if (errEl) { errEl.textContent = r.data?.error || t('common.error','Error'); errEl.classList.remove('hidden'); }
    return;
  }
  if (okEl) { okEl.textContent = t('banners.saved','Banner saved'); okEl.classList.remove('hidden'); }
  _bnFile = null;
  const titleInp = document.getElementById('bn-title');
  const modalInp = document.getElementById('bn-modal-text');
  const timerInp = document.getElementById('bn-timer');
  const checkInp = document.getElementById('bn-modal-window');
  const nameEl   = document.getElementById('bn-file-name');
  const fileInp  = document.getElementById('bn-file');
  const prevEl   = document.getElementById('bn-preview');
  if (titleInp) titleInp.value = '';
  if (modalInp) modalInp.value = '';
  if (timerInp) timerInp.value = '5';
  if (checkInp) checkInp.checked = true;
  if (fileInp) fileInp.value = '';
  if (nameEl)   nameEl.textContent = t('banners.file_not_selected', 'Файл не выбран');
  if (prevEl)   { prevEl.src=''; prevEl.classList.add('hidden'); }
  bnRenderUploadCard(null);
  const addBoard = document.getElementById('bn-add-link-board');
  if (addBoard) addBoard.innerHTML = '';
  loadBanners();
}

function bnHandleGridClick(e) {
  const btn = e.target.closest('[data-bn-action]');
  if (!btn) return;
  const id = btn.dataset.bnId;
  if (!id) return;
  if (btn.dataset.bnAction === 'toggle') toggleBanner(id, btn.dataset.bnActive !== '1');
  if (btn.dataset.bnAction === 'edit') openEditBanner(id);
  if (btn.dataset.bnAction === 'delete') deleteBanner(id);
}
function bnRenderLinkBank(bankId = 'bn-link-bank') {
  const bank = document.getElementById(bankId);
  if (!bank) return;
  bank.innerHTML = _BN_LINKS.map(m =>
    `<div class="bn-link-chip" draggable="true" data-bn-link-id="${m.id}" style="border-color:${m.color}55">${m.icon} ${m.label}</div>`
  ).join('');
  bank.querySelectorAll('[data-bn-link-id]').forEach(ch => {
    ch.addEventListener('dragstart', (e) => {
      _bnDragLinkId = ch.dataset.bnLinkId;
      ch.dataset.bnSourceBank = bankId;
      e.dataTransfer?.setData('text/plain', ch.dataset.bnLinkId);
    });
  });
}
function bnAddLinkChip(linkId, url = '', boardId = 'bn-link-board') {
  const board = document.getElementById(boardId);
  const meta = _bnGetLinkMetaById(linkId);
  if (!board || !meta) return;
  const existing = board.querySelector(`[data-bn-link-id="${linkId}"]`);
  if (existing) {
    if (url) {
      existing.dataset.bnUrl = String(url).trim();
      bnRenderLinkChipContent(existing, meta, String(url).trim());
    }
    existing.classList.add('drag-active');
    setTimeout(() => existing.classList.remove('drag-active'), 220);
    return;
  }
  const chip = document.createElement('div');
  chip.className = 'bn-link-chip active';
  chip.draggable = true;
  chip.dataset.bnLinkId = linkId;
  chip.dataset.bnUrl = url || '';
  chip.dataset.bnBoard = boardId;
  chip.style.borderColor = `${meta.color}66`;
  chip.innerHTML = `${meta.icon} ${meta.label}`;
  chip.title = t('banners.link_chip_hint', 'Double click to set URL, remove by cross');
  chip.addEventListener('dragstart', (e) => {
    _bnDragLinkId = linkId;
    chip.classList.add('dragging');
    e.dataTransfer?.setData('text/plain', linkId);
  });
  chip.addEventListener('dragend', () => chip.classList.remove('dragging'));
  chip.addEventListener('dblclick', () => bnOpenLinkModal(chip, meta));
  bnRenderLinkChipContent(chip, meta, url || '');
  board.appendChild(chip);
}
function bnRenderLinkChipContent(chip, meta, url) {
  chip.innerHTML = `${meta.icon} ${meta.label}`;
  if (url) chip.innerHTML += `<input class="form-input" value="${esc(url)}" readonly>`;
  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'bn-link-remove';
  removeBtn.textContent = '✕';
  removeBtn.title = t('banners.link_remove', 'Remove');
  removeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    chip.remove();
  });
  chip.appendChild(removeBtn);
}
function bnRenderUploadCard(file) {
  const nameEl = document.getElementById('bn-file-name');
  const card = document.getElementById('bn-upload-card');
  const preview = document.getElementById('bn-preview');
  if (nameEl) nameEl.textContent = file ? file.name : t('banners.file_not_selected', 'Файл не выбран');
  if (!card || !preview) return;
  if (!file) {
    preview.src = '';
    preview.classList.add('hidden');
    card.innerHTML = `<div class="bn-upload-empty">${t('banners.upload_hint','Кликни или перетащи изображение')}</div>`;
    return;
  }
  const reader = new FileReader();
  reader.onload = ev => {
    const src = ev.target?.result || '';
    preview.src = src;
    preview.classList.remove('hidden');
    card.innerHTML = `<img src="${src}" alt="preview">`;
  };
  reader.readAsDataURL(file);
}
function bnCollectEditLinks() {
  return bnCollectLinksFromBoard('bn-link-board');
}
function bnCollectLinksFromBoard(boardId) {
  const board = document.getElementById(boardId);
  const out = {};
  const order = [];
  (board?.querySelectorAll('[data-bn-link-id]') || []).forEach(ch => {
    const id = ch.dataset.bnLinkId;
    const meta = _bnGetLinkMetaById(id);
    const url = String(ch.dataset.bnUrl || '').trim();
    if (!meta || !url) return;
    out[meta.field] = url;
    order.push(id);
  });
  return { links: out, order };
}
function bnBindLinkBoardDrop(boardId) {
  const board = document.getElementById(boardId);
  if (!board) return;
  board.addEventListener('dragover', (e) => e.preventDefault());
  board.addEventListener('drop', (e) => {
    e.preventDefault();
    const linkId = e.dataTransfer?.getData('text/plain') || _bnDragLinkId;
    if (!linkId) return;
    bnAddLinkChip(linkId, '', boardId);
  });
}
function bnOpenLinkModal(chip, meta) {
  const modal = document.getElementById('bn-link-modal');
  const title = document.getElementById('bn-link-modal-title');
  const input = document.getElementById('bn-link-modal-url');
  if (!modal || !input || !chip || !meta) return;
  _bnLinkModalState = { chip, meta };
  if (title) title.textContent = `${meta.icon} ${meta.label}`;
  input.value = String(chip.dataset.bnUrl || '');
  modal.classList.add('on');
  setTimeout(() => input.focus(), 0);
}
function bnCloseLinkModal() {
  _bnLinkModalState = null;
  document.getElementById('bn-link-modal')?.classList.remove('on');
}
function bnConfirmLinkModal() {
  const input = document.getElementById('bn-link-modal-url');
  const next = String(input?.value || '').trim();
  if (!_bnLinkModalState?.chip || !_bnLinkModalState?.meta) return;
  _bnLinkModalState.chip.dataset.bnUrl = next;
  bnRenderLinkChipContent(_bnLinkModalState.chip, _bnLinkModalState.meta, next);
  bnCloseLinkModal();
}
function bnRemoveFromLinkModal() {
  if (_bnLinkModalState?.chip) _bnLinkModalState.chip.remove();
  bnCloseLinkModal();
}
function openEditBanner(id) {
  const b = _bnBanners.find(x => String(x.id) === String(id));
  if (!b) return;
  _bnEditId = b.id;
  const imageUrl = _bnImageUrl(b);
  const imgWrap = document.getElementById('bn-edit-image');
  if (imgWrap) imgWrap.innerHTML = imageUrl ? `<img src="${imageUrl}" style="width:100%;height:100%;object-fit:cover">` : '<div class="bn-preview-fallback">No image</div>';
  const set = (id0, v) => { const e = document.getElementById(id0); if (e) e.value = v ?? ''; };
  set('bn-edit-title', b._title || b.title || '');
  set('bn-edit-modal-text', b.modal_text || '');
  set('bn-edit-timer', b.timer_change || 5);
  const chk = document.getElementById('bn-edit-modal-window');
  if (chk) chk.checked = b.modal_window !== false;
  bnRenderLinkBank('bn-link-bank');
  const board = document.getElementById('bn-link-board');
  if (board) board.innerHTML = '';
  const ordered = Array.isArray(b.link_order) && b.link_order.length ? b.link_order : _BN_LINKS.map(x => x.id);
  ordered.forEach(id2 => {
    const meta = _bnGetLinkMetaById(id2);
    if (!meta) return;
    const href = b[meta.field] || '';
    if (href) bnAddLinkChip(id2, href, 'bn-link-board');
  });
  document.getElementById('bn-edit-msg').textContent = '';
  document.getElementById('bn-edit-modal')?.classList.add('on');
}
async function bnSaveEditBanner() {
  if (!_bnEditId) return;
  const payload = {
    title: document.getElementById('bn-edit-title')?.value.trim(),
    modal_text: document.getElementById('bn-edit-modal-text')?.value.trim(),
    timer_change: Number(document.getElementById('bn-edit-timer')?.value || '5'),
    modal_window: Boolean(document.getElementById('bn-edit-modal-window')?.checked),
    position: 'main',
  };
  const { links, order } = bnCollectEditLinks();
  _BN_LINKS.forEach(m => { payload[m.field] = ''; });
  Object.assign(payload, links);
  payload.link_order = order;
  const { ok, data } = await api('PUT', `/api/admin/banners/${_bnEditId}`, payload);
  const msg = document.getElementById('bn-edit-msg');
  if (!ok) {
    if (msg) msg.textContent = data?.error || 'Update error';
    return;
  }
  if (msg) msg.textContent = 'Saved';
  document.getElementById('bn-edit-modal')?.classList.remove('on');
  loadBanners();
}
async function bnEditUploadImage(e) {
  if (!_bnEditId) return;
  const f = e.target?.files?.[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  const r = await apiForm(`/api/admin/banners/${_bnEditId}/image`, fd);
  if (!r.ok) {
    document.getElementById('bn-edit-msg').textContent = r.data?.error || 'Upload error';
    return;
  }
  document.getElementById('bn-edit-msg').textContent = 'Image updated';
  loadBanners();
}

async function toggleBanner(id, active) {
  const { ok } = await api('POST', `/api/admin/banners/${id}/toggle`, { active });
  if (ok) {
    showToast(active ? t('banners.btn_toggle_on','Показан') : t('banners.btn_toggle_off','Скрыт'), 'success');
    loadBanners();
  }
}

async function deleteBanner(id) {
  showConfirm(t('banners.confirm_delete','Удалить баннер?'), async () => {
    const { ok } = await api('DELETE', `/api/admin/banners/${id}`);
    if (ok) { showToast(t('banners.deleted','Удалён'), 'success'); loadBanners(); }
  });
}

let _cpTab = 'promotions';
let _cpData = { promotions: [], raffles: [] };
let _cpEditId = null;
let _cpGames = [];
let _cpGameIds = new Set();
let _cpRaffleLinkCtx = null;
const CP_RAFFLE_LINKS = [
  { id: 'telegram', label: 'Telegram', svg: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M21.9 4.1a1 1 0 0 0-1-.16L3.3 11.1a1 1 0 0 0 .08 1.87l4.84 1.52 1.53 4.84a1 1 0 0 0 .83.68h.12a1 1 0 0 0 .83-.45l10.95-14.9a1 1 0 0 0-.58-1.57zM9.9 13.4l-3.17-1 10.1-4.1-6.93 5.1zm1.3 3.89-.95-3.02 2.9-2.13-1.95 5.15z"/></svg>' },
  { id: 'youtube', label: 'YouTube', svg: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M23 12s0-3.5-.45-5.19a2.94 2.94 0 0 0-2.06-2.06C18.8 4.3 12 4.3 12 4.3s-6.8 0-8.49.45A2.94 2.94 0 0 0 1.45 6.8C1 8.5 1 12 1 12s0 3.5.45 5.19a2.94 2.94 0 0 0 2.06 2.06C5.2 19.7 12 19.7 12 19.7s6.8 0 8.49-.45a2.94 2.94 0 0 0 2.06-2.06C23 15.5 23 12 23 12zM10 15.5v-7l6 3.5-6 3.5z"/></svg>' },
  { id: 'viber', label: 'Viber', svg: '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 2C6.49 2 2 5.94 2 10.79c0 2.83 1.52 5.34 3.87 6.95V22l4.25-2.35c.61.1 1.24.14 1.88.14 5.51 0 10-3.94 10-8.8C22 5.94 17.51 2 12 2zm1.73 12.26c-.3.08-.62.04-.9-.12l-1.38-.79a.9.9 0 0 1-.43-.75V8.77c0-.5.4-.9.9-.9s.9.4.9.9v3.21l.93.53c.43.25.58.8.33 1.23a.9.9 0 0 1-.35.32z"/></svg>' },
  { id: 'website', label: 'Website', svg: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a14 14 0 0 1 0 18"/><path d="M12 3a14 14 0 0 0 0 18"/></svg>' },
];

function cpCfgName(tab) { return tab === 'raffles' ? 'raffles' : 'promotions'; }
function cpListKey(tab) { return tab === 'raffles' ? 'raffles' : 'promotions'; }

function cpResolveImage(path) {
  const src = String(path || '').trim();
  if (!src) return '';
  if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('/support_uploads/') || src.startsWith('/api/admin/banners/casino-image')) return src;
  return '/api/admin/banners/casino-image?path=' + encodeURIComponent(src.replace(/^\/+/, ''));
}
function cpGetGameName(gameId) {
  const g = _cpGames.find(x => String(x.id) === String(gameId));
  return g?.name || g?.title || gameId;
}
function cpRaffleLinkMeta(id) {
  return CP_RAFFLE_LINKS.find(x => x.id === id) || { id, label: id, svg: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 12h16"/><path d="M12 4v16"/></svg>' };
}
function cpNormalizeGameLinks(item) {
  const base = Array.isArray(item.game_links) ? item.game_links : [];
  if (item.game_id && !base.includes(item.game_id)) base.unshift(item.game_id);
  return [...new Set(base)].filter(id => _cpGameIds.has(String(id)));
}
function cpCollectGameLinks(boardId) {
  const board = document.getElementById(boardId);
  const ids = [];
  (board?.querySelectorAll('[data-cp-game-id]') || []).forEach(ch => {
    const id = ch.dataset.cpGameId;
    if (id && _cpGameIds.has(String(id))) ids.push(id);
  });
  return [...new Set(ids)];
}
function cpAddGameChip(gameId, boardId = 'cp-link-board') {
  const board = document.getElementById(boardId);
  if (!board) return;
  if (board.querySelector(`[data-cp-game-id="${gameId}"]`)) return;
  const chip = document.createElement('div');
  chip.className = 'cp-chip active';
  chip.setAttribute('draggable', 'false');
  chip.dataset.cpGameId = gameId;
  chip.innerHTML = `🎮 ${esc(cpGetGameName(gameId))} <button type="button" class="cp-chip-remove">✕</button>`;
  chip.querySelector('.cp-chip-remove')?.addEventListener('click', e => {
    e.stopPropagation();
    chip.remove();
  });
  board.appendChild(chip);
}
function cpRenderGameSelect(selectId) {
  const sel = document.getElementById(selectId);
  if (!sel) return;
  const options = _cpGames.map(g => `<option value="${esc(g.id)}">${esc(g.name || g.title || g.id)}</option>`).join('');
  sel.innerHTML = `<option value="">${t('campaigns.select_game', 'Select game')}</option>${options}`;
}
function cpSetSyncInfo(text) {
  const a = document.getElementById('cp-game-sync-info');
  const b = document.getElementById('cp-edit-game-sync-info');
  if (a) a.textContent = text || '';
  if (b) b.textContent = text || '';
}
async function cpLoadGames(showInfo = false) {
  let ok = false;
  let data = {};
  ({ ok, data } = await apiWithTimeout('GET', '/api/admin/game-builder/all-games'));
  if (!ok) {
    ({ ok, data } = await apiWithTimeout('GET', '/api/admin/game-builder/provider-games'));
  }
  if (!ok) return cpSetSyncInfo(t('campaigns.games_sync_error', 'Games sync error'));
  const prevIds = _cpGameIds;
  _cpGames = Array.isArray(data?.games) ? data.games.filter(g => g.active !== false) : [];
  _cpGameIds = new Set(_cpGames.map(g => String(g.id)));
  cpRenderGameSelect('cp-game-select');
  cpRenderGameSelect('cp-edit-game-select');
  if (showInfo) {
    const added = _cpGames.filter(g => !prevIds.has(String(g.id))).length;
    const removed = [...prevIds].filter(id => !_cpGameIds.has(id)).length;
    cpSetSyncInfo(t('campaigns.games_sync_result', 'Games updated: +%a / -%r').replace('%a', String(added)).replace('%r', String(removed)));
  }
}
function cpTogglePromoFields() {
  const onPromo = _cpTab === 'promotions';
  document.getElementById('cp-promo-stats-row')?.classList.toggle('hidden', !onPromo);
  document.getElementById('cp-link-board')?.closest('.cp-link-layout')?.classList.toggle('hidden', !onPromo);
  document.getElementById('cp-raffle-links-row')?.classList.toggle('hidden', onPromo);
}
function cpSyncRemovedGames() {
  let removed = 0;
  (_cpData.promotions || []).forEach(item => {
    const before = cpNormalizeGameLinks(item);
    const after = before.filter(id => _cpGameIds.has(String(id)));
    removed += Math.max(0, before.length - after.length);
    item.game_links = after;
    item.game_id = after[0] || '';
    item.game_name = after[0] ? cpGetGameName(after[0]) : '';
  });
  return removed;
}
function cpSetUploaderState(prefix, imagePath = '', fileName = '') {
  const card = document.getElementById(`${prefix}upload-card`);
  const name = document.getElementById(`${prefix}file-name`);
  const hidden = document.getElementById(`${prefix}image-path`);
  if (hidden) hidden.value = imagePath || '';
  if (name) name.textContent = fileName || (imagePath ? t('campaigns.file_uploaded', 'Uploaded') : t('campaigns.file_not_selected', 'No file selected'));
  if (card) card.innerHTML = imagePath ? `<img src="${esc(cpResolveImage(imagePath))}" alt="image">` : `<div class="cp-upload-empty">${t('campaigns.upload_hint', 'Choose image from your PC')}</div>`;
}
async function cpUploadImage(file, prefix = 'cp-') {
  if (!file) return '';
  const fd = new FormData();
  fd.append('file', file);
  const res = await apiForm('/api/admin/site/config/campaigns-image', fd);
  if (!res.ok) {
    cpSetMsg(res.data?.error || t('common.error', 'Error'), false);
    return '';
  }
  cpSetUploaderState(prefix, res.data?.image || '', file.name);
  return res.data?.image || '';
}
function cpFormData() {
  const base = {
    title: document.getElementById('cp-title')?.value.trim() || '',
    short_desc: document.getElementById('cp-short-desc')?.value.trim() || '',
    description: document.getElementById('cp-description')?.value.trim() || '',
    image_url: document.getElementById('cp-image-path')?.value.trim() || '',
    start_date: document.getElementById('cp-start-date')?.value.trim() || '',
    end_date: document.getElementById('cp-end-date')?.value.trim() || '',
    active: Boolean(document.getElementById('cp-active')?.checked),
  };
  if (_cpTab === 'promotions') {
    const gameLinks = cpCollectGameLinks('cp-link-board');
    return { ...base, game_links: gameLinks, game_id: gameLinks[0] || '', game_name: gameLinks[0] ? cpGetGameName(gameLinks[0]) : '', standard_multiplier: document.getElementById('cp-standard-mult')?.value.trim() || '', promo_multiplier: document.getElementById('cp-promo-mult')?.value.trim() || '', win_chance: document.getElementById('cp-win-chance')?.value.trim() || '' };
  }
  const { links, order } = cpCollectRaffleLinks('cp-raffle-link-board');
  return { ...base, links, link_order: order, telegram: links.telegram || '', youtube: links.youtube || '', viber: links.viber || '', link: links.website || '' };
}
function cpEditFormData() {
  const base = {
    title: document.getElementById('cp-edit-title')?.value.trim() || '',
    short_desc: document.getElementById('cp-edit-short-desc')?.value.trim() || '',
    description: document.getElementById('cp-edit-description')?.value.trim() || '',
    image_url: document.getElementById('cp-edit-image-path')?.value.trim() || '',
    start_date: document.getElementById('cp-edit-start-date')?.value.trim() || '',
    end_date: document.getElementById('cp-edit-end-date')?.value.trim() || '',
    active: Boolean(document.getElementById('cp-edit-active')?.checked),
  };
  if (_cpTab === 'promotions') {
    const gameLinks = cpCollectGameLinks('cp-edit-link-board');
    return { ...base, game_links: gameLinks, game_id: gameLinks[0] || '', game_name: gameLinks[0] ? cpGetGameName(gameLinks[0]) : '', standard_multiplier: document.getElementById('cp-edit-standard-mult')?.value.trim() || '', promo_multiplier: document.getElementById('cp-edit-promo-mult')?.value.trim() || '', win_chance: document.getElementById('cp-edit-win-chance')?.value.trim() || '' };
  }
  const { links, order } = cpCollectRaffleLinks('cp-edit-raffle-link-board');
  return { ...base, links, link_order: order, telegram: links.telegram || '', youtube: links.youtube || '', viber: links.viber || '', link: links.website || '' };
}
function cpResetForm() {
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  set('cp-title', '');
  set('cp-short-desc', '');
  set('cp-description', '');
  set('cp-start-date', '');
  set('cp-end-date', '');
  set('cp-standard-mult', '');
  set('cp-promo-mult', '');
  set('cp-win-chance', '');
  const a = document.getElementById('cp-active');
  if (a) a.checked = true;
  const board = document.getElementById('cp-link-board');
  if (board) board.innerHTML = '';
  const rBoard = document.getElementById('cp-raffle-link-board');
  if (rBoard) rBoard.innerHTML = '';
  cpSetUploaderState('cp-', '', '');
}
function cpSetMsg(msg, ok = true) {
  const el = document.getElementById('cp-msg');
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok ? 'var(--green)' : 'var(--red)';
}
function cpSetEditMsg(msg, ok = true) {
  const el = document.getElementById('cp-edit-msg');
  if (!el) return;
  el.textContent = msg;
  el.style.color = ok ? 'var(--green)' : 'var(--red)';
}
async function cpLoadAll() {
  cpSetMsg('');
  await cpLoadGames(false);
  const [promRes, raffleRes] = await Promise.all([_siteGet('promotions'), _siteGet('raffles')]);
  if (!promRes.ok || !raffleRes.ok) {
    cpSetMsg(promRes.data?.error || raffleRes.data?.error || t('common.error', 'Error'), false);
    return;
  }
  _cpData.promotions = Array.isArray(promRes.data?.config?.promotions) ? promRes.data.config.promotions : [];
  _cpData.raffles = Array.isArray(raffleRes.data?.config?.raffles) ? raffleRes.data.config.raffles : [];
  cpRender();
}
function cpRender() {
  cpTogglePromoFields();
  document.querySelectorAll('[data-cp-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.cpTab === _cpTab));
  const list = _cpData[cpListKey(_cpTab)] || [];
  const grid = document.getElementById('cp-grid');
  if (!grid) return;
  if (!list.length) {
    grid.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🧩</div>${t('campaigns.empty', 'Пока нет записей')}</div>`;
    return;
  }
  grid.innerHTML = list.map(item => {
    const img = cpResolveImage(item.image_url || item.image || '');
    const title = esc(item.title || '—');
    const desc = esc(item.short_desc || '');
    const active = item.active !== false;
    const gameLinks = _cpTab === 'promotions' ? cpNormalizeGameLinks(item) : [];
    const raffleLinks = _cpTab === 'raffles' ? cpNormalizeRaffleLinks(item) : { links: {}, order: [] };
    return `<div class="cp-card">
      <div class="cp-thumb">${img ? `<img src="${esc(img)}" alt="${title}">` : t('campaigns.no_image','Нет изображения')}</div>
      <div class="cp-body">
        <div class="cp-title">${title}</div>
        <div class="cp-desc">${desc}</div>
        <div class="cp-meta">${esc(item.start_date || '')}${item.end_date ? ` — ${esc(item.end_date)}` : ''}</div>
        <div class="cp-actions">${_cpTab === 'promotions' ? gameLinks.map(id => `<span class="cp-link">${esc(cpGetGameName(id))}</span>`).join('') : raffleLinks.order.map(k => raffleLinks.links[k] ? `<a class="cp-link" href="${esc(raffleLinks.links[k])}" target="_blank" rel="noopener">${esc(cpRaffleLinkMeta(k).label)}</a>` : '').join('')}</div>
        <div class="cp-actions">
          <span class="badge badge-${active ? 'open' : 'closed'}">${active ? t('banners.status_active','Active') : t('banners.status_hidden','Hidden')}</span>
          <button class="btn-icon" data-cp-action="edit" data-cp-id="${esc(item.id)}">${t('common.edit','Edit')}</button>
          <button class="btn-icon" data-cp-action="toggle" data-cp-id="${esc(item.id)}">${active ? t('banners.btn_toggle_off','Hide') : t('banners.btn_toggle_on','Show')}</button>
          <button class="btn-icon" style="color:var(--red)" data-cp-action="delete" data-cp-id="${esc(item.id)}">${t('banners.btn_delete','Delete')}</button>
        </div>
      </div>
    </div>`;
  }).join('');
}
function cpOpenEdit(id) {
  const list = _cpData[cpListKey(_cpTab)] || [];
  const item = list.find(x => String(x.id) === String(id));
  if (!item) return;
  _cpEditId = item.id;
  const set = (field, val) => { const e = document.getElementById(field); if (e) e.value = val || ''; };
  set('cp-edit-title', item.title);
  set('cp-edit-short-desc', item.short_desc);
  set('cp-edit-description', item.description);
  set('cp-edit-start-date', item.start_date);
  set('cp-edit-end-date', item.end_date);
  set('cp-edit-standard-mult', item.standard_multiplier || '');
  set('cp-edit-promo-mult', item.promo_multiplier || '');
  set('cp-edit-win-chance', item.win_chance || '');
  const a = document.getElementById('cp-edit-active');
  if (a) a.checked = item.active !== false;
  cpSetUploaderState('cp-edit-', item.image_url || item.image || '', '');
  const board = document.getElementById('cp-edit-link-board');
  if (board) board.innerHTML = '';
  cpNormalizeGameLinks(item).forEach(id2 => cpAddGameChip(id2, 'cp-edit-link-board'));
  const rBoard = document.getElementById('cp-edit-raffle-link-board');
  if (rBoard) rBoard.innerHTML = '';
  const raffle = cpNormalizeRaffleLinks(item);
  raffle.order.forEach(key => cpAddRaffleLinkChip(key, raffle.links[key], 'cp-edit-raffle-link-board'));
  cpSetEditMsg('');
  document.getElementById('cp-edit-promo-stats-row')?.classList.toggle('hidden', _cpTab !== 'promotions');
  document.getElementById('cp-edit-link-board')?.closest('.cp-link-layout')?.classList.toggle('hidden', _cpTab !== 'promotions');
  document.getElementById('cp-edit-raffle-links-row')?.classList.toggle('hidden', _cpTab === 'promotions');
  document.getElementById('cp-edit-modal')?.classList.add('on');
}
async function cpPersistCurrent() {
  const name = cpCfgName(_cpTab);
  const key = cpListKey(_cpTab);
  const payload = {};
  payload[key] = _cpData[key];
  const removed = cpSyncRemovedGames();
  if (removed > 0) cpSetSyncInfo(t('campaigns.games_removed_notice', 'Removed missing games: %n').replace('%n', String(removed)));
  const res = await _siteSave(name, payload);
  if (!res.ok) {
    cpSetMsg(res.data?.error || t('common.error', 'Error'), false);
    return false;
  }
  cpSetMsg(t('site.save_ok', 'Saved'));
  return true;
}
async function cpSaveCurrent() {
  const fd = cpFormData();
  if (!fd.title) return cpSetMsg(t('campaigns.err_title', 'Введите название'), false);
  const key = cpListKey(_cpTab);
  const list = _cpData[key];
  const nextId = list.reduce((m, it) => Math.max(m, Number(it.id) || 0), 0) + 1;
  list.push({ id: nextId, ...fd });
  const ok = await cpPersistCurrent();
  if (!ok) return;
  cpResetForm();
  cpRender();
}
async function cpSaveEditCurrent() {
  const fd = cpEditFormData();
  if (!fd.title) return cpSetEditMsg(t('campaigns.err_title', 'Введите название'), false);
  const key = cpListKey(_cpTab);
  const list = _cpData[key];
  const idx = list.findIndex(x => String(x.id) === String(_cpEditId));
  if (idx < 0) return;
  list[idx] = { ...list[idx], ...fd };
  const ok = await cpPersistCurrent();
  if (!ok) return;
  document.getElementById('cp-edit-modal')?.classList.remove('on');
  cpRender();
}
async function cpHandleGridClick(e) {
  const btn = e.target.closest('[data-cp-action]');
  if (!btn) return;
  const id = btn.dataset.cpId;
  const action = btn.dataset.cpAction;
  const key = cpListKey(_cpTab);
  const list = _cpData[key];
  const idx = list.findIndex(x => String(x.id) === String(id));
  if (idx < 0) return;
  if (action === 'edit') return cpOpenEdit(id);
  if (action === 'toggle') list[idx].active = !(list[idx].active !== false);
  if (action === 'delete') list.splice(idx, 1);
  const ok = await cpPersistCurrent();
  if (ok) cpRender();
}
function cpBindTabs() {
  document.querySelectorAll('[data-cp-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      _cpTab = btn.dataset.cpTab || 'promotions';
      _cpEditId = null;
      cpResetForm();
      cpRender();
    });
  });
}
function cpBindGamePickers() {
  const bind = (selectId, btnId, boardId) => {
    document.getElementById(btnId)?.addEventListener('click', () => {
      const gameId = document.getElementById(selectId)?.value || '';
      if (!gameId) return;
      cpAddGameChip(gameId, boardId);
    });
  };
  bind('cp-game-select', 'cp-game-add-btn', 'cp-link-board');
  bind('cp-edit-game-select', 'cp-edit-game-add-btn', 'cp-edit-link-board');
  document.getElementById('cp-game-refresh-btn')?.addEventListener('click', () => cpLoadGames(true));
  document.getElementById('cp-edit-game-refresh-btn')?.addEventListener('click', () => cpLoadGames(true));
}
function cpNormalizeRaffleLinks(item) {
  const links = { ...(item.links || {}) };
  if (item.telegram && !links.telegram) links.telegram = item.telegram;
  if (item.youtube && !links.youtube) links.youtube = item.youtube;
  if (item.viber && !links.viber) links.viber = item.viber;
  if (item.link && !links.website) links.website = item.link;
  const rawOrder = Array.isArray(item.link_order) ? item.link_order : Object.keys(links);
  const order = [...new Set(rawOrder)].filter(k => links[k]);
  return { links, order };
}
function cpRenderRaffleLinkBank(bankId) {
  const bank = document.getElementById(bankId);
  if (!bank) return;
  bank.innerHTML = CP_RAFFLE_LINKS.map(m => `<div class="cp-chip" draggable="true" data-cp-raffle-link-id="${m.id}">${m.svg} ${esc(m.label)}</div>`).join('');
  bank.querySelectorAll('[data-cp-raffle-link-id]').forEach(ch => {
    ch.addEventListener('dragstart', e => {
      e.dataTransfer.effectAllowed = 'copy';
      e.dataTransfer.setData('text/cp-raffle-link-id', ch.dataset.cpRaffleLinkId);
    });
  });
}
function cpAddRaffleLinkChip(linkId, url = '', boardId = 'cp-raffle-link-board') {
  const board = document.getElementById(boardId);
  if (!board) return;
  if (board.querySelector(`[data-cp-raffle-link-id="${linkId}"]`)) return;
  const meta = cpRaffleLinkMeta(linkId);
  const chip = document.createElement('div');
  chip.className = 'cp-chip active';
  chip.dataset.cpRaffleLinkId = linkId;
  chip.dataset.cpUrl = url || '';
  chip.innerHTML = `${meta.svg} ${esc(meta.label)} <button type="button" class="cp-chip-remove">✕</button>`;
  chip.addEventListener('dblclick', () => cpOpenRaffleLinkModal(chip));
  chip.querySelector('.cp-chip-remove')?.addEventListener('click', e => { e.stopPropagation(); chip.remove(); });
  board.appendChild(chip);
}
function cpBindRaffleBoardDrop(boardId) {
  const board = document.getElementById(boardId);
  if (!board) return;
  board.addEventListener('dragover', e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  board.addEventListener('drop', e => {
    e.preventDefault();
    const id = e.dataTransfer.getData('text/cp-raffle-link-id');
    if (id) cpAddRaffleLinkChip(id, '', boardId);
  });
}
function cpCollectRaffleLinks(boardId) {
  const board = document.getElementById(boardId);
  const links = {};
  const order = [];
  (board?.querySelectorAll('[data-cp-raffle-link-id]') || []).forEach(ch => {
    const id = ch.dataset.cpRaffleLinkId;
    const url = (ch.dataset.cpUrl || '').trim();
    if (!id || !url) return;
    links[id] = url;
    order.push(id);
  });
  return { links, order };
}
function cpOpenRaffleLinkModal(chipEl) {
  _cpRaffleLinkCtx = chipEl;
  const m = cpRaffleLinkMeta(chipEl?.dataset?.cpRaffleLinkId);
  const title = document.getElementById('cp-link-modal-title');
  const input = document.getElementById('cp-link-modal-url');
  if (title) title.textContent = `${m.label} URL`;
  if (input) input.value = chipEl?.dataset?.cpUrl || '';
  document.getElementById('cp-link-modal')?.classList.add('on');
  input?.focus();
}
function cpCloseRaffleLinkModal() {
  _cpRaffleLinkCtx = null;
  document.getElementById('cp-link-modal')?.classList.remove('on');
}
function cpBindRaffleLinkModal() {
  document.getElementById('cp-link-cancel')?.addEventListener('click', cpCloseRaffleLinkModal);
  document.getElementById('cp-link-remove')?.addEventListener('click', () => { _cpRaffleLinkCtx?.remove(); cpCloseRaffleLinkModal(); });
  document.getElementById('cp-link-save')?.addEventListener('click', () => {
    const url = document.getElementById('cp-link-modal-url')?.value.trim() || '';
    if (_cpRaffleLinkCtx) _cpRaffleLinkCtx.dataset.cpUrl = url;
    cpCloseRaffleLinkModal();
  });
}
function cpBindUploaders() {
  document.getElementById('cp-file-trigger')?.addEventListener('click', () => document.getElementById('cp-file')?.click());
  document.getElementById('cp-edit-file-trigger')?.addEventListener('click', () => document.getElementById('cp-edit-file')?.click());
  document.getElementById('cp-file')?.addEventListener('change', async (e) => {
    const f = e.target?.files?.[0];
    if (f) await cpUploadImage(f, 'cp-');
  });
  document.getElementById('cp-edit-file')?.addEventListener('change', async (e) => {
    const f = e.target?.files?.[0];
    if (f) await cpUploadImage(f, 'cp-edit-');
  });
}
function cpApplyTextFormat(textareaId, kind) {
  const ta = document.getElementById(textareaId);
  if (!ta) return;
  const start = ta.selectionStart ?? 0;
  const end = ta.selectionEnd ?? 0;
  const selected = ta.value.slice(start, end);
  const wraps = {
    bold: ['**', '**'],
    italic: ['*', '*'],
    underline: ['__', '__'],
    strike: ['~~', '~~'],
  };
  const w = wraps[kind];
  if (!w) return;
  const content = selected || t('campaigns.format_placeholder', 'text');
  const next = ta.value.slice(0, start) + w[0] + content + w[1] + ta.value.slice(end);
  ta.value = next;
  const newStart = start + w[0].length;
  const newEnd = newStart + content.length;
  ta.focus();
  ta.setSelectionRange(newStart, newEnd);
}
function cpBindFormatButtons() {
  document.querySelectorAll('[data-cp-format]').forEach(btn => {
    btn.addEventListener('click', () => {
      cpApplyTextFormat(btn.dataset.cpFormatTarget, btn.dataset.cpFormat);
    });
  });
}
async function initCampaigns() {
  cpBindTabs();
  cpBindGamePickers();
  cpRenderRaffleLinkBank('cp-raffle-link-bank');
  cpRenderRaffleLinkBank('cp-edit-raffle-link-bank');
  cpBindRaffleBoardDrop('cp-raffle-link-board');
  cpBindRaffleBoardDrop('cp-edit-raffle-link-board');
  cpBindRaffleLinkModal();
  cpBindUploaders();
  cpBindFormatButtons();
  document.getElementById('cp-save-btn')?.addEventListener('click', cpSaveCurrent);
  document.getElementById('cp-reset-btn')?.addEventListener('click', cpResetForm);
  document.getElementById('cp-reload-btn')?.addEventListener('click', cpLoadAll);
  document.getElementById('cp-grid')?.addEventListener('click', cpHandleGridClick);
  document.getElementById('cp-edit-close')?.addEventListener('click', () => document.getElementById('cp-edit-modal')?.classList.remove('on'));
  document.getElementById('cp-edit-save')?.addEventListener('click', cpSaveEditCurrent);
  cpResetForm();
  await cpLoadAll();
}

async function _siteGet(name) {
  return apiWithTimeout('GET', `/api/admin/site/config/${name}`);
}

async function _siteSave(name, config) {
  return apiWithTimeout('POST', `/api/admin/site/config/${name}`, { config });
}

function _siteSetMsg(text, ok = true) {
  const el = document.getElementById('site-msg');
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? 'var(--green)' : 'var(--red)';
}

function _sitePretty(v) {
  return JSON.stringify(v, null, 2);
}

function _siteParseJson(id) {
  const raw = document.getElementById(id)?.value || '';
  return JSON.parse(raw);
}

const _siteEditorsMeta = [
  { name: 'site', textareaId: 'site-site-json', titleKey: 'site.site_json_title', category: 'general' },
  { name: 'providers', textareaId: 'site-providers-json', titleKey: 'site.providers_title', category: 'general' },
  { name: 'payments', textareaId: 'site-payments-json', titleKey: 'site.payments_title', category: 'general' },
  { name: 'faq', textareaId: 'site-faq-json', titleKey: 'site.faq_title', category: 'general' },
  { name: 'vip', textareaId: 'site-vip-json', titleKey: 'site.vip_title', category: 'marketing' },
  { name: 'promo_settings', textareaId: 'site-promo-settings-json', titleKey: 'site.promo_settings_title', category: 'marketing' },
  { name: 'promo_codes', textareaId: 'site-promo-codes-json', titleKey: 'site.promo_codes_title', category: 'marketing' },
  { name: 'roles_perms', textareaId: 'site-roles-perms-json', titleKey: 'site.access_title', category: 'roles' },
];
const _siteMetaMap = Object.fromEntries(_siteEditorsMeta.map(m => [m.name, m]));
let _siteCurrentEditor = null;
let _siteConfigsCache = {};
let _siteUiBound = false;

function _siteFlattenSidebarItems(items, out = []) {
  if (!Array.isArray(items)) return out;
  items.forEach((it) => {
    if (!it || typeof it !== 'object') return;
    const type = String(it.type || '');
    if (type === 'nav_item' || type === 'details' || type === 'section_header') out.push(it);
    if (Array.isArray(it.children)) _siteFlattenSidebarItems(it.children, out);
  });
  return out;
}

function _siteItemLabel(it) {
  const i18nKey = String(it?.i18n_key || '').trim();
  const fallback = String(it?.label || it?.id || 'item').trim();
  return i18nKey ? t(i18nKey, fallback) : fallback;
}

function _siteUpdateTextarea(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = _sitePretty(value);
}

function _siteSwitchCategory(category) {
  document.querySelectorAll('[data-site-category-btn]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.siteCategoryBtn === category);
  });
  document.querySelectorAll('[data-site-category]').forEach(block => {
    block.classList.toggle('hidden', block.dataset.siteCategory !== category);
  });
}

function _siteSetEditorMsg(text, ok = true) {
  const el = document.getElementById('site-editor-msg');
  if (!el) return;
  el.textContent = text || '';
  el.style.color = ok ? 'var(--green)' : 'var(--red)';
}

function _siteCloseEditorModal() {
  document.getElementById('site-editor-modal')?.classList.add('hidden');
  _siteSetEditorMsg('', true);
}

function _siteOpenEditorModal(name) {
  const meta = _siteMetaMap[name];
  if (!meta) return;
  _siteCurrentEditor = name;
  const title = t(meta.titleKey, name);
  const titleEl = document.getElementById('site-editor-title');
  const txtEl = document.getElementById('site-editor-textarea');
  if (titleEl) titleEl.textContent = title;
  if (txtEl) txtEl.value = _sitePretty(_siteConfigsCache[name] || {});
  _siteSetEditorMsg('', true);
  document.getElementById('site-editor-modal')?.classList.remove('hidden');
}

function _siteBindUiOnce() {
  if (_siteUiBound) return;
  _siteUiBound = true;

  document.getElementById('site-reload-all-btn')?.addEventListener('click', initSite);
  document.getElementById('site-mode-save-btn')?.addEventListener('click', siteSaveMode);
  document.getElementById('site-add-promo-code-btn')?.addEventListener('click', siteAddPromoCode);

  document.querySelectorAll('[data-site-category-btn]').forEach(btn => {
    btn.addEventListener('click', () => _siteSwitchCategory(btn.dataset.siteCategoryBtn || 'general'));
  });

  document.querySelectorAll('[data-site-collapsible]').forEach(card => {
    const head = card.querySelector('.site-collapsible-head');
    const body = card.querySelector('.site-collapsible-body');
    if (!head || !body) return;
    head.addEventListener('click', (e) => {
      if (e.target.closest('[data-site-open-editor]')) return;
      const opening = body.classList.contains('hidden');
      body.classList.toggle('hidden', !opening);
      card.classList.toggle('is-open', opening);
    });
  });

  document.querySelectorAll('[data-site-open-editor]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _siteOpenEditorModal(btn.dataset.siteOpenEditor || '');
    });
  });

  document.querySelectorAll('.site-readonly-textarea').forEach(ta => {
    ta.addEventListener('dblclick', () => {
      const meta = _siteEditorsMeta.find(m => m.textareaId === ta.id);
      if (meta) _siteOpenEditorModal(meta.name);
    });
  });

  document.getElementById('site-editor-close-btn')?.addEventListener('click', _siteCloseEditorModal);
  document.getElementById('site-editor-cancel-btn')?.addEventListener('click', _siteCloseEditorModal);
  document.getElementById('site-editor-revert-btn')?.addEventListener('click', () => {
    if (!_siteCurrentEditor) return;
    const txtEl = document.getElementById('site-editor-textarea');
    if (txtEl) txtEl.value = _sitePretty(_siteConfigsCache[_siteCurrentEditor] || {});
    _siteSetEditorMsg(t('site.editor_reverted', 'Reverted'));
  });
  document.getElementById('site-editor-save-btn')?.addEventListener('click', async () => {
    if (!_siteCurrentEditor) return;
    const txtEl = document.getElementById('site-editor-textarea');
    if (!txtEl) return;
    try {
      const parsed = JSON.parse(txtEl.value || '{}');
      const res = await _siteSave(_siteCurrentEditor, parsed);
      if (!res.ok) {
        _siteSetEditorMsg(res.data?.error || t('common.error', 'Error'), false);
        return;
      }
      _siteConfigsCache[_siteCurrentEditor] = parsed;
      const meta = _siteMetaMap[_siteCurrentEditor];
      if (meta) _siteUpdateTextarea(meta.textareaId, parsed);
      _siteSetEditorMsg(t('site.save_ok', 'Saved'), true);
      _siteSetMsg(t('site.save_ok', 'Saved'));
    } catch (e) {
      _siteSetEditorMsg(`JSON parse error: ${e.message}`, false);
    }
  });
}

async function initSite() {
  _siteBindUiOnce();

  const [modeRes, siteRes, sideRes, provRes, payRes, faqRes, vipRes, promoSettingsRes, promoCodesRes, permsRes] = await Promise.all([
    _siteGet('site_mode'),
    _siteGet('site'),
    _siteGet('sidebar'),
    _siteGet('providers'),
    _siteGet('payments'),
    _siteGet('faq'),
    _siteGet('vip'),
    _siteGet('promo_settings'),
    _siteGet('promo_codes'),
    _siteGet('roles_perms'),
  ]);
  if (!modeRes.ok || !siteRes.ok || !sideRes.ok || !provRes.ok || !payRes.ok || !faqRes.ok || !vipRes.ok || !promoSettingsRes.ok || !promoCodesRes.ok || !permsRes.ok) {
    _siteSetMsg(
      modeRes.data?.error || siteRes.data?.error || sideRes.data?.error || provRes.data?.error ||
      payRes.data?.error || faqRes.data?.error || vipRes.data?.error || promoSettingsRes.data?.error ||
      promoCodesRes.data?.error || permsRes.data?.error || t('common.error','Error'),
      false,
    );
    return;
  }

  const modeSel = document.getElementById('site-mode-select');
  if (modeSel) modeSel.value = siteRes.data?.config?.mode || modeRes.data?.config?.mode || modeRes.data?.mode || 'online';
  _siteConfigsCache = {
    site: siteRes.data.config,
    providers: provRes.data.config,
    payments: payRes.data.config,
    faq: faqRes.data.config,
    vip: vipRes.data.config || {},
    promo_settings: promoSettingsRes.data.config || {},
    promo_codes: promoCodesRes.data.config || { codes: [] },
    roles_perms: permsRes.data.config || {},
  };
  _siteEditorsMeta.forEach(meta => _siteUpdateTextarea(meta.textareaId, _siteConfigsCache[meta.name]));

  const itemsWrap = document.getElementById('site-sidebar-items');
  const items = _siteFlattenSidebarItems(sideRes.data?.config?.items || []);
  if (itemsWrap) {
    itemsWrap.innerHTML = items
      .map(it => {
        const id = esc(it.id || '');
        const label = esc(_siteItemLabel(it));
        const typeLabel = esc(t(`site.item_type_${it.type || 'other'}`, String(it.type || 'item')));
        const en = it.enabled !== false;
        return `<label class="stat-card" style="display:flex;justify-content:space-between;gap:8px;align-items:center;padding:10px 12px">
          <span style="display:flex;flex-direction:column;gap:2px">
            <span style="font-size:12px;color:var(--t1)">${label}</span>
            <span style="font-size:10px;color:var(--t3)">${typeLabel}</span>
          </span>
          <input type="checkbox" data-site-sidebar-id="${id}" ${en ? 'checked' : ''}>
        </label>`;
      }).join('');
    itemsWrap.querySelectorAll('input[data-site-sidebar-id]').forEach(inp => {
      inp.addEventListener('change', async () => {
        const sid = inp.dataset.siteSidebarId;
        const cfg = sideRes.data.config;
        const walk = (arr) => {
          arr.forEach(x => {
            if (String(x.id || '') === String(sid)) x.enabled = inp.checked;
            if (Array.isArray(x.children)) walk(x.children);
          });
        };
        walk(cfg.items || []);
        const res = await _siteSave('sidebar', cfg);
        if (!res.ok) {
          inp.checked = !inp.checked;
          _siteSetMsg(res.data?.error || t('common.error','Error'), false);
          return;
        }
        _siteSetMsg(t('site.save_ok', 'Saved'));
      });
    });
  }
  _siteSwitchCategory('general');
  _siteSetMsg('');
}

function siteAddPromoCode() {
  const textareaId = 'site-promo-codes-json';
  try {
    const config = _siteParseJson(textareaId);
    if (!config || typeof config !== 'object' || Array.isArray(config)) {
      throw new Error('Promo codes config must be object');
    }
    if (!Array.isArray(config.codes)) config.codes = [];

    const code = (prompt(t('site.prompt_code', 'Promo code (e.g. WELCOME100)'), '') || '').trim().toUpperCase();
    if (!code) return;
    const bonusType = (prompt(t('site.prompt_bonus_type', 'Bonus type (balance/freespin/percent)'), 'balance') || 'balance').trim().toLowerCase();
    const bonusRaw = Number(prompt(t('site.prompt_bonus_amount', 'Bonus amount'), '100') || 0);
    const maxUsesRaw = Number(prompt(t('site.prompt_max_uses', 'Max uses (0 = unlimited)'), '0') || 0);
    const daysRaw = Number(prompt(t('site.prompt_valid_days', 'Validity days from now'), '30') || 0);

    if (!bonusType) throw new Error(t('site.validation_bonus_type', 'Bonus type required'));
    if (!Number.isFinite(bonusRaw) || bonusRaw <= 0) throw new Error(t('site.validation_bonus_amount', 'Bonus amount must be > 0'));

    const expiresAt = daysRaw > 0 ? new Date(Date.now() + daysRaw * 86400000).toISOString() : null;
    const item = {
      code,
      type: bonusType,
      bonus: bonusRaw,
      active: true,
      max_uses: Math.max(0, Math.floor(maxUsesRaw)),
      used: 0,
      expires_at: expiresAt,
    };
    config.codes.unshift(item);
    _siteConfigsCache.promo_codes = config;
    _siteUpdateTextarea(textareaId, config);
    _siteSetMsg(t('site.promo_code_added', 'Promo code added to JSON draft'));
  } catch (e) {
    _siteSetMsg(e?.message || t('common.error', 'Error'), false);
  }
}

async function siteSaveMode() {
  const mode = document.getElementById('site-mode-select')?.value || 'online';
  const res = await _siteGet('site_mode');
  if (!res.ok) {
    const m = document.getElementById('site-mode-msg');
    if (m) { m.textContent = res.data?.error || t('common.error','Error'); m.style.color = 'var(--red)'; }
    return;
  }
  const cfg = res.data.config || {};
  cfg.mode = mode;
  const save = await _siteSave('site_mode', cfg);
  const m = document.getElementById('site-mode-msg');
  if (m) {
    m.textContent = save.ok ? t('site.save_ok','Saved') : (save.data?.error || t('common.error','Error'));
    m.style.color = save.ok ? 'var(--green)' : 'var(--red)';
  }
}

async function siteSaveJson(name, textareaId) {
  try {
    const json = _siteParseJson(textareaId);
    const res = await _siteSave(name, json);
    if (!res.ok) {
      _siteSetMsg(res.data?.error || t('common.error','Error'), false);
      return;
    }
    _siteConfigsCache[name] = json;
    _siteSetMsg(t('site.save_ok', 'Saved'));
  } catch (e) {
    _siteSetMsg(`JSON parse error: ${e.message}`, false);
  }
}

// ══════════════════════════════════════════════════════════════
//  TOURNAMENT BUILDER
// ══════════════════════════════════════════════════════════════
async function initTournamentBuilder() {
  document.getElementById('tb-reload-btn')?.addEventListener('click', loadTournamentSeasons);
  document.getElementById('tb-create-btn')?.addEventListener('click', createTournamentSeason);
  document.getElementById('tb-period')?.addEventListener('change', onTournamentPresetChange);
  document.getElementById('tb-games-search')?.addEventListener('input', renderTournamentGameOptions);
  document.getElementById('tb-glow-open')?.addEventListener('click', () => document.getElementById('tb-glow-color')?.click());
  document.getElementById('tb-glow-color')?.addEventListener('input', (e) => setTournamentGlowColor(e.target.value));
  document.getElementById('tb-glow-hex')?.addEventListener('input', (e) => setTournamentGlowColor(e.target.value, false));
  document.getElementById('tb-glow-hex')?.addEventListener('blur', (e) => setTournamentGlowColor(e.target.value, true));
  document.getElementById('tb-glow-swatches')?.addEventListener('click', (e) => {
    const btn = e.target?.closest?.('.tb-color-swatch');
    if (!btn) return;
    setTournamentGlowColor(btn.dataset.color || '#f5c451');
  });
  await Promise.all([loadTournamentGameOptions(), loadTournamentSeasons()]);
  onTournamentPresetChange();
  setTournamentGlowColor(document.getElementById('tb-glow-color')?.value || '#f5c451');
}

let _tbGameOptions = [];
let _tbGameSelected = new Set();
let _tbDeleting = new Set();

function _normalizeGlowHex(raw, strict = true) {
  let v = String(raw || '').trim().toLowerCase();
  if (!v) return strict ? '#f5c451' : '';
  if (!v.startsWith('#')) v = `#${v}`;
  if (/^#[0-9a-f]{3}$/.test(v)) {
    v = `#${v[1]}${v[1]}${v[2]}${v[2]}${v[3]}${v[3]}`;
  }
  if (/^#[0-9a-f]{6}$/.test(v)) return v;
  return strict ? '#f5c451' : v;
}

function setTournamentGlowColor(raw, strict = true) {
  const val = _normalizeGlowHex(raw, strict);
  const nativeEl = document.getElementById('tb-glow-color');
  const hexEl = document.getElementById('tb-glow-hex');
  const preview = document.getElementById('tb-glow-preview');
  if (strict || /^#[0-9a-f]{6}$/.test(val)) {
    if (nativeEl) nativeEl.value = val;
    if (hexEl) hexEl.value = val.toUpperCase();
    if (preview) preview.style.setProperty('--tb-glow', val);
    const swatches = document.querySelectorAll('#tb-glow-swatches .tb-color-swatch');
    swatches.forEach((el) => el.classList.toggle('active', String(el.dataset.color || '').toLowerCase() === val));
  } else if (hexEl) {
    hexEl.value = val.toUpperCase();
  }
}

async function loadTournamentGameOptions() {
  const box = document.getElementById('tb-games-list');
  if (!box) return;
  const { ok, data } = await api('GET', '/api/admin/game-builder/all-games');
  if (!ok) return;
  _tbGameOptions = Array.isArray(data.games) ? data.games : [];
  renderTournamentGameOptions();
}

function renderTournamentGameOptions() {
  const box = document.getElementById('tb-games-list');
  const selectedBox = document.getElementById('tb-games-selected');
  if (!box || !selectedBox) return;
  const q = (document.getElementById('tb-games-search')?.value || '').trim().toLowerCase();
  const filtered = _tbGameOptions.filter(g => {
    const txt = `${g.name || ''} ${g.id || ''}`.toLowerCase();
    return !q || txt.includes(q);
  });
  box.innerHTML = filtered.map(g => {
    const gid = String(g.id || '');
    const active = _tbGameSelected.has(gid) ? 'active' : '';
    return `<button type="button" class="tb-game-chip ${active}" onclick="toggleTournamentGame('${esc(gid)}')">${esc(g.name || gid)}</button>`;
  }).join('');
  selectedBox.innerHTML = Array.from(_tbGameSelected).map(gid => {
    const g = _tbGameOptions.find(x => String(x.id) === gid);
    return `<span class="tb-game-selected">${esc(g?.name || gid)}</span>`;
  }).join('');
}

function toggleTournamentGame(gid) {
  if (_tbGameSelected.has(gid)) _tbGameSelected.delete(gid);
  else _tbGameSelected.add(gid);
  renderTournamentGameOptions();
}

function onTournamentPresetChange() {
  const preset = document.getElementById('tb-period')?.value || 'week';
  const startEl = document.getElementById('tb-starts-at');
  const endEl = document.getElementById('tb-ends-at');
  if (!startEl || !endEl) return;
  if (!startEl.value) {
    const now = new Date();
    startEl.value = new Date(now.getTime() - now.getTimezoneOffset()*60000).toISOString().slice(0,16);
  }
  const start = new Date(startEl.value);
  if (isNaN(start.getTime())) return;
  let days = 0;
  if (preset === 'day') days = 1;
  else if (preset === 'week') days = 7;
  else if (preset === 'month') days = 30;
  else if (preset === 'all') days = 3650;
  if (days > 0) {
    const end = new Date(start.getTime() + days*24*60*60*1000);
    endEl.value = new Date(end.getTime() - end.getTimezoneOffset()*60000).toISOString().slice(0,16);
  }
}

async function createTournamentSeason() {
  const title = document.getElementById('tb-title')?.value?.trim();
  const description = document.getElementById('tb-description')?.value?.trim() || '';
  const period = document.getElementById('tb-period')?.value || 'custom';
  const prizePoolRaw = document.getElementById('tb-prize-pool')?.value || '0';
  const startsAtRaw = document.getElementById('tb-starts-at')?.value || '';
  const endsAtRaw = document.getElementById('tb-ends-at')?.value || '';
  const gameIds = Array.from(_tbGameSelected);
  const glowColor = (document.getElementById('tb-glow-color')?.value || '#f5c451').trim();
  const msg = document.getElementById('tb-msg');

  if (!title || !startsAtRaw || !endsAtRaw) {
    if (msg) {
      msg.className = 'auth-error';
      msg.textContent = t('tournament_builder.err_required', 'Fill required fields');
    }
    return;
  }

  const payload = {
    title,
    description,
    period,
    prize_pool: Number(prizePoolRaw || 0),
    starts_at: new Date(startsAtRaw).toISOString(),
    ends_at: new Date(endsAtRaw).toISOString(),
    game_ids: gameIds,
    glow_color: glowColor,
  };
  const { ok, data } = await api('POST', '/api/admin/tournaments/seasons', payload);
  if (!ok) {
    if (msg) {
      msg.className = 'auth-error';
      msg.textContent = data?.error || t('common.error', 'Error');
    }
    return;
  }
  if (msg) {
    msg.className = 'auth-success';
    msg.textContent = t('tournament_builder.created_ok', 'Season created');
  }
  _tbGameSelected = new Set();
  renderTournamentGameOptions();
  showToast(t('tournament_builder.created_ok', 'Season created'), 'success');
  await loadTournamentSeasons();
}

async function loadTournamentSeasons() {
  const tbody = document.getElementById('tb-table-body');
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6" class="loading-center"><span class="spinner"></span></td></tr>`;
  const { ok, data } = await api('GET', '/api/admin/tournaments/seasons');
  if (!ok) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">${esc(data?.error || t('common.error','Error'))}</td></tr>`;
    return;
  }
  const rows = Array.isArray(data.seasons) ? data.seasons : [];
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty-state">${t('tournament_builder.empty','No seasons yet')}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.map(s => {
    const status = String(s.status || 'draft');
    const nextStatus = status === 'active' ? 'completed' : 'active';
    const sid = String(s.id || '');
    return `<tr>
      <td>${esc(s.title || '—')}</td>
      <td>${esc(s.period || '—')}</td>
      <td>$${Number(s.prize_pool || 0).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}</td>
      <td><span class="badge ${status === 'active' ? 'badge-verified' : status === 'completed' ? 'badge-closed' : 'badge-none'}">${esc(status)}</span></td>
      <td class="td-muted">${fmtDate(s.starts_at)} → ${fmtDate(s.ends_at)}</td>
      <td>
        <span class="tb-game-selected" style="margin-right:8px;background:${esc(String(s.glow_color || '#f5c451'))}22;border-color:${esc(String(s.glow_color || '#f5c451'))};color:${esc(String(s.glow_color || '#f5c451'))}">${t('tournament_builder.glow_chip','glow')}</span>
        <button class="btn-icon" data-tb-action="toggle-status" data-id="${esc(sid)}" data-status="${esc(nextStatus)}">${nextStatus}</button>
        <button class="btn-icon" data-tb-action="delete-season" data-id="${esc(sid)}">${t('tournament_builder.delete_btn','Delete')}</button>
      </td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('[data-tb-action]').forEach(btn => {
    if (btn.dataset.tbBound === '1') return;
    btn.dataset.tbBound = '1';
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      const id = String(btn.dataset.id || '').trim();
      if (!/^\d+$/.test(id)) return;
      if (btn.dataset.tbAction === 'toggle-status') {
        const status = String(btn.dataset.status || 'active');
        btn.disabled = true;
        await toggleTournamentStatus(id, status);
        return;
      }
      if (btn.dataset.tbAction === 'delete-season') {
        if (_tbDeleting.has(id)) return;
        _tbDeleting.add(id);
        btn.disabled = true;
        await deleteTournamentSeason(id);
        _tbDeleting.delete(id);
      }
    });
  });
}

async function toggleTournamentStatus(id, status) {
  const { ok, data } = await api('POST', `/api/admin/tournaments/seasons/${id}/status`, { status });
  if (!ok) {
    showToast(data?.error || t('common.error', 'Error'), 'error');
    return;
  }
  showToast(t('site.save_ok', 'Saved'), 'success');
  await loadTournamentSeasons();
}

async function deleteTournamentSeason(id) {
  if (!confirm(t('tournament_builder.confirm_delete','Delete season?'))) return;
  const { ok, data } = await api('DELETE', `/api/admin/tournaments/seasons/${id}`);
  if (!ok) {
    showToast(data?.error || t('common.error', 'Error'), 'error');
    return;
  }
  showToast(t('tournament_builder.deleted_ok','Season deleted'), 'success');
  await loadTournamentSeasons();
}
