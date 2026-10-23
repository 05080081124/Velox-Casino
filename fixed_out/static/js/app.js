/* VELOX CASINO — app.js v3.1 */
const V = (() => {
  const VIEW_STORAGE_KEY = 'velox_view';
  let S = {
    user:null, cfg:null, games:[], banners:[],
    view:'home', bannerIdx:0, bannerTimer:null,
    detOpen:false, authType:'email', regType:'email',
    tfa_action:null, tfa_pending_cb:null,
    favSel:new Set(), pendingReg:null, pendingUserId:null,
    payMode:'deposit', payCat:'popular', payProviderId:null, payProviders:[], payConfig:null,
    raffles:[], promos:[], tournaments:[], tournamentPeriod:'week', currentTournament:null, currentTournamentKey:'', tournamentRefreshTimer:null, forgotNeedsCode:false,
    sidebarCfg: null, partnersCfg: null, activeTag: null,
    rates: {},   // { USD: { rate_to_uah: 41.5 }, EUR: {...}, ... }
    bannerShowAll: { home: false, games: false, sport: false },
    tournamentModalGameRoute: '',
  };

  // ── INIT ────────────────────────────────────────────────────────────────────
  async function init() {
    // Каждый загрузчик изолирован — сбой одного не роняет весь init
    await loadCfg().catch(e => console.warn('loadCfg', e));
    await Promise.allSettled([
      loadGames(), loadSidebarCfg(), loadBtmCfg(), loadI18n(), loadBanners(),
      _loadAvailableCurrencies(), loadSportCfg(), loadPartnersCfg()
    ]);
    // checkMe ВСЕГДА выполняется — даже если что-то выше упало
    await checkMe().catch(e => console.warn('checkMe', e));
    try { sessionStorage.removeItem('velox_from_game'); } catch {}
    // Применяем переводы ПОСЛЕ checkMe чтобы setUser уже знал валюту
    if (_T) _applyLang();

    buildSidebarLogo();
    buildSidebar();
    buildSidebarBanner();
    _buildLangPanel(); // Гарантируем что панель языков всегда построена
    _buildCurPanel();  // Панель выбора валюты
    // Новая главная страница
    initMiniBanners();
    buildSportSection();
    buildHighRtpSection();
    _lobbyTab = 'velox'; _lobbyPage = 0;
    _renderLobbyGames(true);
    buildProvidersRow();
    _applyLobbyI18n();
    renderGrid('all-grid', S.games);
    buildPhoneCodes();
    fillLicenses();
    setupSidebarHover();
    _bindBlockImageDrag();
    initBanner();
    buildBtmBar();
    _applyFooter();
    // Вешаем outsideClick только один раз через { once:false } но с флагом
    if (!V._outsideClickBound) {
      document.addEventListener('click', outsideClick);
      V._outsideClickBound = true;
    }

    // bfcache fix: при возврате с игровой страницы через кнопку "Назад"
    // DOMContentLoaded НЕ стреляет — нужен pageshow с persisted:true
    if (!V._pagesShowBound) {
      // bfcache: при нажатии "Назад" DOMContentLoaded не стреляет
      window.addEventListener('pageshow', async function(e) {
        if (e.persisted) {
          // Гарантируем что i18n загружен прежде чем вызывать checkMe/setUser
          if (!_T) await loadI18n();
          await checkMe();
          buildSidebar();
          _buildLangPanel();
        }
      });
      // Переключение вкладок — перепроверяем сессию
      document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'visible' && !S.user) checkMe();
      });
      // Возврат фокуса на окно
      window.addEventListener('focus', function() {
        if (!S.user) checkMe();
      });
      V._pagesShowBound = true;
    }

    // Обработка возврата от LiqPay — ?liqpay=ok в URL
    _handleLiqpayReturn();
    _checkResetToken();
    // Загружаем курсы сразу при старте (не ждём открытия настроек)
    _loadCurrencyRates();
    await refreshTournamentContext(false);
    if (!S.tournamentRefreshTimer) {
      S.tournamentRefreshTimer = setInterval(() => refreshTournamentContext(false), 15000);
    }
    _restoreViewAfterReload();
  }

  function _hasViewNode(view) {
    return !!(view && el('view-' + view));
  }

  function _persistView(view) {
    if (!_hasViewNode(view)) return;
    try { localStorage.setItem(VIEW_STORAGE_KEY, view); } catch {}
  }

  function _restoreViewAfterReload() {
    let saved = '';
    try { saved = localStorage.getItem(VIEW_STORAGE_KEY) || ''; } catch {}
    if (!saved || !_hasViewNode(saved)) return;
    if (saved === S.view && el('view-' + saved)?.classList.contains('active')) return;
    go(saved, { persist: false });
  }

  async function loadCfg() {
    try { S.cfg = await api('/api/config/site'); } catch {}
    if (S.cfg?.casino_policy_text) txt('set-pol-text', S.cfg.casino_policy_text);
    _applyFeatureFlags();
  }

  function _handleLiqpayReturn() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('liqpay') !== 'ok') return;

    // Чистим URL чтобы при F5 не мигал тост повторно
    history.replaceState({}, '', window.location.pathname);

    // Обновляем баланс с сервера и показываем тост
    setTimeout(async () => {
      try {
        const r = await api('/api/balance');
        if (r.balance != null && S.user) {
          S.user.balance = r.balance;
          const sym = csym(S.user.currency);
          txt('tb-bal-num', r.balance.toFixed(2)); txt('tb-bal-sym', sym);
          txt('bal-big-num', r.balance.toFixed(2)); txt('bal-big-sym', sym);
        }
      } catch {}
      const msg = (_t('payment.deposited') || 'Пополнено') + ' ✓';
      toast(msg, 'ok');
    }, 600); // небольшая задержка чтобы i18n успел загрузиться
  }

  // ── I18N ─────────────────────────────────────────────────────────────────────
  // Система переводов: отдельные файлы config/languages/{ru,ua,us}.json
  // API: GET /api/lang/{code}  — загрузка языка
  //      GET /api/lang/list    — список доступных языков + default из site.json
  //
  // Правило default_language:
  //   - При ПЕРВОМ заходе (нет 'velox_lang' в localStorage) — берём default из site.json
  //   - После ручного выбора языка пользователем — сохраняем в localStorage
  //   - localStorage НЕ перебивает default при сбросе (очистке storage)

  let _lang = 'RU';        // текущий активный код
  let _T    = null;        // объект переводов текущего языка
  let _langCache = {};     // кэш загруженных языков { 'RU': {...}, 'UA': {...} }
  let _availableLangs = []; // [{ code, label, flag }, ...]
  let _defaultLang = 'RU'; // из site.json

  // Нормализация кода: US → US, EN → US, uk → UA и т.д.
  function _normCode(v) {
    const m = { RU:'RU', UA:'UA', US:'US', EN:'US', UK:'UA',
                ru:'RU', ua:'UA', us:'US', en:'US', uk:'UA' };
    return m[String(v).trim()] || 'RU';
  }

  // Загрузка одного языкового файла с сервера (с кэшем)
  async function _fetchLang(code) {
    if (_langCache[code]) return _langCache[code];
    try {
      const data = await api(`/api/lang/${code}`);
      if (data && data.lang_label) {
        _langCache[code] = data;
        return data;
      }
    } catch {}
    return null;
  }

  // Главная инициализация i18n — вызывается из init() после loadCfg()
  async function loadI18n() {
    // 1. Загружаем список доступных языков и default
    try {
      const list = await api('/api/lang/list');
      if (list && list.languages) {
        _availableLangs = list.languages;
        _defaultLang    = _normCode(list.default || 'RU');
      }
    } catch {}

    // 2. Определяем язык при старте:
    //    - если пользователь ранее выбирал вручную — берём из localStorage
    //    - иначе — default из site.json
    const saved = localStorage.getItem('velox_lang');
    const savedValid = saved && _availableLangs.some(l => l.code === saved);
    const startLang = savedValid ? saved : _defaultLang;

    // 3. Загружаем стартовый язык
    const data = await _fetchLang(startLang);
    if (data) {
      _lang = startLang;
      _T    = data;
    } else {
      // Fallback: пробуем первый доступный
      for (const l of _availableLangs) {
        const d = await _fetchLang(l.code);
        if (d) { _lang = l.code; _T = d; break; }
      }
    }

    // 4. Применяем язык к DOM
    _applyLang();

    // 5. Строим панель выбора языка
    _buildLangPanel();
  }

  // Получить перевод по пути 'section.key'
  function _t(path) {
    if (!_T) return path;
    const parts = path.split('.');
    let node = _T;
    for (const p of parts) {
      if (node == null) return path;
      node = node[p];
    }
    return (node && typeof node === 'string') ? node : path;
  }
  // _tv: like _t() but returns fallback when key not translated (avoids truthy-key bug)
  function _tv(path, fallback) {
    const v = _t(path);
    return (v && v !== path) ? v : (fallback || path);
  }

  // Публичная функция смены языка (вызывается из кнопок в сайдбаре)
  async function setLang(code) {
    code = _normCode(code);
    if (code === _lang && _T) return; // уже активен
    const langMap = {US:'en-GB', RU:'ru', UA:'uk'};
    document.documentElement.lang = langMap[code] || 'en-GB';

    const data = await _fetchLang(code);
    if (!data) { toast(_t('toasts.error_generic'), 'error'); return; }

    _lang = code;
    _T    = data;
    localStorage.setItem('velox_lang', code);

    // Язык больше НЕ меняет валюту — валюта управляется отдельно через setCurrency()
    const curInput = el('set-cur');
    if (curInput && S.user) curInput.value = S.user.currency || 'UAH';

    // Применяем переводы и перестраиваем грид
    _applyLang();
    _applyLobbyI18n();
    _renderLobbyGames(true);
    buildSportSection();
    renderBukmakingPage();
    renderGrid('all-grid', S.games);
    _buildLangPanel();
    _buildCurPanel();
    _loadCurrencyRates();

    // Обновляем trigger-кнопку языка
    const langs = _availableLangs.length ? _availableLangs : [
      {code:'RU', label:'Русский', flag:'🇷🇺'},
      {code:'UA', label:'Українська', flag:'🇺🇦'},
      {code:'US', label:'English', flag:'🇬🇧'},
    ];
    const cur = langs.find(l => l.code === code) || langs[0];
    const fl = el('sb-lang-cur-flag');  if (fl) fl.textContent = cur.flag;
    const lb = el('sb-lang-cur-label'); if (lb) lb.textContent = cur.label;
  }

  // ── CURRENCY SELECTOR ─────────────────────────────────────────────────────────

  let _availableCurrencies = [];  // loaded from /api/currency/list

  function _readCookie(name){
    const m = document.cookie.match(new RegExp('(?:^|; )' + name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  function _csrfToken(){ return _readCookie('velox_csrf'); }
  function _secureFetch(input, init = {}) {
    const cfg = {...init};
    const method = String(cfg.method || 'GET').toUpperCase();
    cfg.headers = new Headers(cfg.headers || {});
    if (!cfg.credentials) cfg.credentials = 'include';
    const csrf = _csrfToken();
    if (csrf && method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS' && method !== 'TRACE') {
      cfg.headers.set('X-CSRF-Token', csrf);
    }
    return fetch(input, cfg);
  }

  async function _loadAvailableCurrencies() {
    try {
      const r = await api('/api/currency/list', 'GET');
      _availableCurrencies = r.currencies || [];
    } catch {
      _availableCurrencies = [
        {code:'UAH', symbol:'₴', name:'Гривня'},
        {code:'USD', symbol:'$', name:'US Dollar'},
        {code:'EUR', symbol:'€', name:'Euro'},
        {code:'USDT', symbol:'₮', name:'USDT'},
      ];
    }
  }

  function _buildCurPanel() {
    const panel = el('sb-cur-panel');
    if (!panel || !_availableCurrencies.length) return;

    const userCur = S.user?.currency || localStorage.getItem('velox_currency') || 'UAH';
    const cur = _availableCurrencies.find(c => c.code === userCur) || _availableCurrencies[0];
    const sym = cur?.symbol || '₴';

    panel.innerHTML = `
      <div class="sb-lang-wrap" id="sb-cur-wrap">
        <button class="sb-lang-trigger" id="sb-cur-trigger" onclick="V.toggleCurMenu(event)">
          <span class="sb-lang-cur-flag" id="sb-cur-sym">${sym}</span>
          <span class="sb-lang-cur-label" id="sb-cur-code">${cur?.code || userCur}</span>
          <span class="sb-lang-chevron" id="sb-cur-chevron">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
        </button>
        <div class="sb-lang-menu" id="sb-cur-menu">
          ${_availableCurrencies.map(c => `
            <button class="sb-lang-opt${c.code === userCur ? ' active' : ''}" onclick="event.stopPropagation();V.setCurrency('${c.code}')">
              <span class="sb-lang-opt-flag">${c.symbol}</span>
              <span class="sb-lang-opt-label">${c.code}</span>
              <span style="font-size:11px;opacity:.55">${c.name}</span>
              ${c.code === userCur ? '<span class="sb-lang-opt-check"><svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>' : ''}
            </button>
          `).join('')}
        </div>
      </div>`;

    panel.style.display = '';
  }

  function toggleCurMenu(event) {
    if (event) event.stopPropagation();
    const menu    = el('sb-cur-menu');
    const chevron = el('sb-cur-chevron');
    const wrap    = el('sb-cur-wrap');
    if (!menu) return;
    const isOpen = menu.classList.contains('open');
    if (isOpen) {
      menu.classList.remove('open');
      chevron?.classList.remove('open');
      wrap?.classList.remove('open');
    } else {
      menu.classList.add('open');
      chevron?.classList.add('open');
      wrap?.classList.add('open');
    }
  }

  async function setCurrency(code) {
    code = code.toUpperCase();
    // Закрываем меню
    const menu    = el('sb-cur-menu');
    const chevron = el('sb-cur-chevron');
    const wrap    = el('sb-cur-wrap');
    if (menu)    menu.classList.remove('open');
    if (chevron) chevron.classList.remove('open');
    if (wrap)    wrap.classList.remove('open');

    // Update trigger display
    const cur = _availableCurrencies.find(c => c.code === code);
    const sym = cur?.symbol || csym(code);
    const curSymEl  = el('sb-cur-sym');  if (curSymEl)  curSymEl.textContent  = sym;
    const curCodeEl = el('sb-cur-code'); if (curCodeEl) curCodeEl.textContent = code;

    localStorage.setItem('velox_currency', code);

    if (!S.user || S.user.currency === code) {
      _buildCurPanel(); return;
    }

    try {
      const r = await api('/api/currency/change', 'POST', { currency: code });
      if (r.success) {
        S.user.balance  = r.balance;
        S.user.currency = r.currency;
        const s = csym(r.currency);
        txt('tb-bal-num',  r.balance.toFixed(2)); txt('tb-bal-sym',  s);
        txt('bal-big-num', r.balance.toFixed(2)); txt('bal-big-sym', s);
        txt('set-stat-bal', r.balance.toFixed(2) + ' ' + s);
        const curInput = el('set-cur'); if (curInput) curInput.value = code;
        _loadCurrencyRates().then(() => {
          // Re-render VIP limits table if modal is open
          if (el('modal-vip')?.classList.contains('on')) {
            _vipRenderLimitsTable();
            if (_vipSelectedTier) _vipRenderBenefits(_vipSelectedTier);
          }
        });
      }
    } catch {
      S.user.currency = code;
    }
    _buildCurPanel(); // Перестроить чтобы галочка переехала
  }

  // ── Применение переводов к DOM ───────────────────────────────────────────────
  function _applyLang() {
    if (!_T) return;
    // Символ валюты: если авторизован — из профиля юзера, иначе дефолт из конфига
    const userCur  = S.user?.currency || localStorage.getItem('velox_currency') || 'UAH';
    const activeSym = csym(userCur);
    const sym       = activeSym;

    // Синхронизируем hidden input set-cur с реальной валютой юзера (не языковой)
    const setCurEl = el('set-cur'); if (setCurEl) setCurEl.value = userCur;

    document.querySelectorAll('[data-cur-sym]').forEach(e => e.textContent = activeSym);
    ['pay-cur-sym','bal-big-sym','tb-bal-sym'].forEach(id => {
      const e = el(id); if (e) e.textContent = activeSym;
    });
    const feeEl = el('pay-fee-val');  if (feeEl) feeEl.textContent = '0.00 ' + activeSym;
    const totEl = el('pay-total-val');if (totEl) totEl.textContent = '— ' + activeSym;

    // Кнопка входа
    const loginBtn = el('tb-login-btn');
    if (loginBtn) loginBtn.textContent = _t('topbar.login');

    // Кнопка пополнения
    const _submitLbl = el('pay-submit-lbl');
    if (_submitLbl) _submitLbl.textContent = (S.payMode === 'withdraw' ? _t('payment.withdraw_btn') : _t('payment.deposit_btn')) || 'Deposit';

    // Deposit / Withdraw mode tabs (top of payment page)
    const _depTabLbl = el('pay-tab-dep-lbl');
    const _wdTabLbl  = el('pay-tab-wd-lbl');
    if (_depTabLbl) _depTabLbl.textContent = _t('payment.deposit_btn')  || 'Deposit';
    if (_wdTabLbl)  _wdTabLbl.textContent  = _t('payment.withdraw_btn') || 'Withdraw';

    // Все элементы с data-i18n атрибутом
    document.querySelectorAll('[data-i18n]').forEach(e => {
      const v = _t(e.dataset.i18n);
      if (v !== e.dataset.i18n) e.textContent = v;
    });

    // Плейсхолдеры
    document.querySelectorAll('[data-i18n-ph]').forEach(e => {
      const v = _t(e.dataset.i18nPh);
      if (v !== e.dataset.i18nPh) e.placeholder = v;
    });

    // Блоки по секциям
    _applyTopbar();
    _applyDropdown();
    _applySidebar();
    _applyAuthModal();
    _applyPaymentSection(sym);
    _applySettingsPage();
    _applyModals();
    _applyFooter();
    _applyPageTitles();
    _applyRgModal();
    _applyAffiliateModal();
    _applyResetModal();
    _applyVipModal();

    // Активная кнопка языка
    document.querySelectorAll('.sb-lang-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.lang === _lang)
    );

    // Перестройка сайдбара (использует _t внутри)
    buildSidebar();

    // Авто-выбор страны по языку (только если поле пустое)
    const countrySel = el('inp-country');
    if (countrySel && !countrySel.value) {
      const map = { RU:'RU', UA:'UA', US:'US' };
      const country = map[_lang];
      if (country) { countrySel.value = country; onCountryChange(); }
    }
  }

  function _applyTopbar() {
    // Уже через loginBtn выше
  }

  function _applyDropdown() {
    const map = {
      'dd-text-balance':  'dropdown.balance',
      'dd-text-settings': 'dropdown.settings',
      'dd-text-logout':   'dropdown.logout',
      'dd-text-promo':    'dropdown.promo',
    };
    for (const [id, key] of Object.entries(map)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }
  }

  function _applySidebar() {
    const lbl = el('sb-lang-label');
    if (lbl) lbl.textContent = _t('sidebar.lang_label');
  }

  function _applyAuthModal() {
    // Табы входа
    const tabsLogin = { 'lat-e':'auth.tab_email', 'lat-p':'auth.tab_phone' };
    const tabsReg   = { 'rat-e':'auth.tab_email', 'rat-p':'auth.tab_phone' };
    for (const [id, key] of Object.entries({...tabsLogin, ...tabsReg})) {
      const e = el(id); if (e) e.textContent = _t(key);
    }
    // Ссылки переключения (span)
    const noAcc = el('auth-no-account');   if (noAcc) noAcc.textContent = _t('auth.no_account');
    const hasAcc = el('auth-has-account'); if (hasAcc) hasAcc.textContent = _t('auth.have_account');
    const forgot = el('auth-forgot');      if (forgot) forgot.textContent = _t('auth.forgot_password');
    const pol    = el('auth-policy-text'); if (pol)    pol.textContent = _t('auth.policy_agree');
    // Ссылки-переключатели форм (a href)
    const swToReg   = el('auth-switch-to-reg');   if (swToReg)   swToReg.textContent   = _t('auth.register_btn');
    const swToLogin = el('auth-switch-to-login'); if (swToLogin) swToLogin.textContent = _t('auth.login_btn');
    // Кнопки submit
    const loginBtn2 = el('auth-login-btn');    if (loginBtn2) loginBtn2.textContent = _t('auth.login_btn');
    const regBtn    = el('auth-register-btn'); if (regBtn)    regBtn.textContent    = _t('auth.register_btn');
    // Плейсхолдеры
    const phMap = {
      'l-email':   'auth.email_placeholder',
      'l-pw':      'auth.password_placeholder',
      'l-phone':   'auth.phone_placeholder',
      'l-2fa':     'auth.tfa_code_ph',
      'r-email':   'auth.email_placeholder',
      'r-pw':      'auth.password_placeholder',
      'r-cpw':     'auth.confirm_password_placeholder',
      'r-uname':   'auth.username_placeholder',
      'r-phone':   'auth.phone_placeholder',
    };
    for (const [id, key] of Object.entries(phMap)) {
      const e = el(id); if (e) e.placeholder = _t(key);
    }
  }

  function _applyPaymentSection(sym) {
    // Лейблы полей карты
    const labels = {
      'pay-label-cardnum':  'payment.card_number',
      'pay-label-expiry':   'payment.expiry',
      'pay-label-holder':   'payment.holder',
      'pay-label-cvv':      'payment.cvv',
      'pay-label-address':  'payment.address_title',
      'pay-label-country':  'payment.country',
      'pay-label-zip':      'payment.zip',
      'pay-label-region':   'payment.region',
      'pay-label-city':     'payment.city',
      'pay-label-street':   'payment.street',
      'pay-label-house':    'payment.house',
      'pay-label-apt':      'payment.apt',
      'pay-label-balance':  'payment.balance',
      'pay-label-amount':   'payment.amount_label',
      'pay-fee-label':      'payment.fee',
      'pay-total-label':    'payment.total',
    };
    for (const [id, key] of Object.entries(labels)) {
      const e = el(id); if (!e) continue;
      // Если внутри есть SVG — обновляем только текстовый узел
      const svg = e.querySelector('svg');
      if (svg) {
        e.childNodes.forEach(n => {
          if (n.nodeType === 3 && n.textContent.trim())
            n.textContent = ' ' + _t(key) + '\n            ';
        });
      } else if (e.querySelector('span.pay-opt-label')) {
        // house/apt с дочерним спаном
        e.childNodes.forEach(n => { if (n.nodeType === 3) n.textContent = _t(key) + ' '; });
      } else {
        e.textContent = _t(key);
      }
    }
    // (необяз.) спаны
    document.querySelectorAll('.pay-opt-label').forEach(e => e.textContent = _t('payment.optional'));

    // Надписи на карте (лицевая сторона)
    const holderLbl = el('pc-label-holder'); if (holderLbl) holderLbl.textContent = _t('payment.card_holder_label');
    const expiryLbl = el('pc-label-expiry'); if (expiryLbl) expiryLbl.textContent = _t('payment.card_expiry_label');

    // Country select options
    const optNone = el('inp-country-none'); if (optNone) optNone.textContent = _t('payment.select_country');
    const optUA   = el('inp-country-ua');   if (optUA)   optUA.textContent   = _t('payment.country_ua');
    const optRU   = el('inp-country-ru');   if (optRU)   optRU.textContent   = _t('payment.country_ru');
    const optUS   = el('inp-country-us');   if (optUS)   optUS.textContent   = _t('payment.country_us');

    // Region select default option
    const regNone = el('inp-region-none');
    if (regNone) {
      const sel = el('inp-country');
      regNone.textContent = sel && sel.value
        ? _t('payment.select_region')
        : _t('payment.no_region');
    }

    // Плейсхолдеры
    const phMap = {
      'inp-zip':    'payment.zip_ph',
      'inp-city':   'payment.city_ph',
      'inp-street': 'payment.street_ph',
      'inp-house':  'payment.house_ph',
      'inp-apt':    'payment.apt_ph',
      'inp-cexp':   'payment.expiry_ph',
    };
    for (const [id, key] of Object.entries(phMap)) {
      const e = el(id); if (e) e.placeholder = _t(key);
    }

    // Alt methods title, policy link, security
    const altTitle = el('pay-alt-title');   if (altTitle) altTitle.textContent = _t('payment.alt_methods');
    const polLink  = el('pay-policy-link'); if (polLink)  polLink.textContent  = _t('payment.policy_link');
    const sslBadge = el('pay-ssl-label');   if (sslBadge) sslBadge.textContent = _t('payment.ssl');

    // Кнопка "Вывести"
    const wdBtn = el('pay-withdraw-btn');
    if (wdBtn) {
      wdBtn.childNodes.forEach(n => {
        if (n.nodeType === 3 && n.textContent.trim())
          n.textContent = ' ' + _t('payment.withdraw_btn');
      });
    }
  }

  function _applySettingsPage() {
    // Заголовок страницы
    const title = el('settings-page-title'); if (title) title.textContent = _t('settings.title');

    // Табы
    // Табы — текст в span с отдельным id (не текстовым узлом)
    const tabLblMap = {
      'stab-profile-lbl':  'settings.tab_profile',
      'stab-security-lbl': 'settings.tab_security',
      'stab-policy-lbl':   'settings.tab_policy',
      'stab-tickets-lbl':  'tickets.tab_tickets',
      'stab-history-lbl':  'settings.tab_history',
    };
    for (const [id, key] of Object.entries(tabLblMap)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }

    // Статы
    const statMap = {
      'set-stat-lbl-balance':  'settings.stat_balance',
      'set-stat-lbl-verified': 'settings.stat_verified',
      'set-stat-lbl-tfa':      'settings.stat_tfa',
      'set-stat-lbl-date':     'settings.stat_date',
    };
    for (const [id, key] of Object.entries(statMap)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }

    // Блок профиля
    const editTitle    = el('set-edit-title');    if (editTitle)    editTitle.textContent    = _t('settings.edit_profile');
    const contactTitle = el('set-contact-title'); if (contactTitle) contactTitle.textContent = _t('settings.contact_info');
    const emailLbl     = el('set-email-lbl');     if (emailLbl)     emailLbl.textContent     = _t('settings.email_label');
    const phoneLbl     = el('set-phone-lbl');     if (phoneLbl)     phoneLbl.textContent     = _t('settings.phone_label');
    const emailNewLbl  = el('set-email-new-lbl'); if (emailNewLbl)  emailNewLbl.textContent  = _t('settings.new_email_label') || 'New email';
    const phoneNewLbl  = el('set-phone-new-lbl'); if (phoneNewLbl)  phoneNewLbl.textContent  = _t('settings.new_phone_label') || 'Phone number';
    const emailSendBtn = el('set-email-send-btn'); if (emailSendBtn) emailSendBtn.textContent = _t('settings.send_email_code_btn') || 'Send code';
    const emailVerifyBtn = el('set-email-verify-btn'); if (emailVerifyBtn) emailVerifyBtn.textContent = _t('settings.verify_email_btn') || 'Verify email';
    const phoneSaveBtn = el('set-phone-save-btn'); if (phoneSaveBtn) phoneSaveBtn.textContent = _t('settings.save_phone_btn') || 'Save phone';
    const verLbl       = el('set-ver-lbl');       if (verLbl)       verLbl.textContent       = _t('settings.stat_verified');
    const unameLbl     = el('set-uname-lbl');     if (unameLbl)     unameLbl.textContent     = _t('settings.username_label');
    const saveBtn      = el('set-save-btn');      if (saveBtn) { const sp = el('set-save-btn-text'); if (sp) sp.textContent = _t('settings.save_btn'); }

    // Placeholder username
    const unameInp = el('set-uname-inp'); if (unameInp) unameInp.placeholder = _t('settings.username_ph');
    const emailInp = el('set-email-new'); if (emailInp) emailInp.placeholder = _t('settings.new_email_ph') || 'name@example.com';
    const emailCodeInp = el('set-email-code'); if (emailCodeInp) emailCodeInp.placeholder = _t('settings.email_code_ph') || 'Code from email';
    const phoneInp = el('set-phone-new'); if (phoneInp) phoneInp.placeholder = _t('settings.new_phone_ph') || '+380501234567';

    // Блок безопасности
    const pwTitle   = el('set-pw-title');   if (pwTitle)   pwTitle.textContent   = _t('settings.change_password');
    const secTitle  = el('set-sec-title');  if (secTitle)  secTitle.textContent  = _t('settings.account_security');
    const tfaName   = el('set-tfa-name');   if (tfaName)   tfaName.textContent   = _t('settings.tfa_label');
    const tfaDesc   = el('set-tfa-desc');   if (tfaDesc)   tfaDesc.textContent   = _t('settings.tfa_desc');
    const docVerN   = el('set-doc-ver-name'); if (docVerN) docVerN.textContent   = _t('settings.doc_verification');
    const docVerD   = el('set-doc-ver-desc'); if (docVerD) docVerD.textContent   = _t('settings.doc_verification_sub');
    const rgName    = el('set-rg-name');  if (rgName)  rgName.textContent  = _t('settings.set-rg-name')  || _t('rg.title')          || rgName.textContent;
    const rgDesc    = el('set-rg-desc');  if (rgDesc)  rgDesc.textContent  = _t('settings.set-rg-desc')  || _t('rg.sub')            || rgDesc.textContent;
    const affName   = el('set-aff-name'); if (affName) affName.textContent = _t('settings.set-aff-name') || _t('affiliate.title')   || affName.textContent;
    const affDesc   = el('set-aff-desc'); if (affDesc) affDesc.textContent = _t('settings.set-aff-desc') || _t('affiliate.sub')     || affDesc.textContent;
    const changePwBtn = el('set-changepw-btn'); if (changePwBtn) changePwBtn.textContent = _t('settings.change_btn');

    // Лейблы и плейсхолдеры полей пароля
    const pwCurLbl = el('set-pw-cur-lbl'); if (pwCurLbl) pwCurLbl.textContent = _t('settings.old_password_label');
    const pwNewLbl = el('set-pw-new-lbl'); if (pwNewLbl) pwNewLbl.textContent = _t('settings.new_password_label');
    const pwCnfLbl = el('set-pw-cnf-lbl'); if (pwCnfLbl) pwCnfLbl.textContent = _t('settings.confirm_password_label');
    const pwCur = el('pw-cur'); if (pwCur) pwCur.placeholder = _t('settings.old_password_ph');
    const pwNew = el('pw-new'); if (pwNew) pwNew.placeholder = _t('settings.new_password_ph');
    const pwCnf = el('pw-cnf'); if (pwCnf) pwCnf.placeholder = _t('settings.confirm_password_ph');

    // Промокод
    const promoTitle = el('set-promo-title'); if (promoTitle) promoTitle.textContent = _t('settings.promo_title');
    const promoInp   = el('set-promo-inp');   if (promoInp)   promoInp.placeholder   = _t('settings.promo_ph');
    const promoApply = el('set-promo-apply'); if (promoApply) promoApply.textContent  = _t('settings.promo_apply');

    // Кнопка выхода
    const logoutBtn = el('set-logout-btn'); if (logoutBtn) logoutBtn.textContent = _t('settings.logout_btn');

    // Политика казино
    const polTitle = el('set-pol-title'); if (polTitle) polTitle.textContent = _t('settings.policy_title');
    const histTitle = el('set-hist-title'); if (histTitle) histTitle.textContent = _t('settings.history_title') || 'История игр';
    const tktTitle = el('set-tickets-title'); if (tktTitle) tktTitle.textContent = _t('tickets.tickets_title');


    // Опции валют
    const curMap = {
      'cur-opt-uah': 'settings.cur_uah',
      'cur-opt-rub': 'settings.cur_rub',
      'cur-opt-usd': 'settings.cur_usd',
      'cur-opt-eur': 'settings.cur_eur',
      'cur-opt-usdt': 'settings.cur_usdt',
    };
    for (const [id, key] of Object.entries(curMap)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }

    // Статус верификации (динамически, обновляем всегда)
    const verBadge = el('set-ver');
    if (verBadge) {
      const u = S.user;
      if (u) {
        verBadge.textContent = u.verified ? _t('settings.verified') : _t('settings.not_verified');
      } else {
        // Не залогинен — показываем fallback из текущего языка
        verBadge.textContent = _t('settings.not_verified');
      }
    }
  }

  function _applyModals() {
    // Табы правил
    const tabs = {
      'modal-tab-general':   'modals.tab_general',
      'modal-tab-deposits':  'modals.tab_deposits',
      'modal-tab-withdraw':  'modals.tab_withdraw',
      'modal-tab-bonuses':   'modals.tab_bonuses',
    };
    for (const [id, key] of Object.entries(tabs)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }

    // Заголовки bottom-bar модалок
    const btmTitles = {
      'btm-rules-title':  'modals.rules_title',
      'btm-lic-title':    'modals.licenses_title',
      'btm-prov-title':   'modals.providers_title',
      'btm-pay-title':    'modals.payments_title',
      'btm-ct-title':     'modals.contacts_title',
      'btm-ct-desc':      'modals.contacts_desc',
    };
    for (const [id, key] of Object.entries(btmTitles)) {
      const e = el(id); if (e) e.textContent = _t(key);
    }

    // Лицензии табы
    const licTabs = document.querySelectorAll('.ltab');
    const licKeys = ['modals.lic_tab_games','modals.lic_tab_deposits','modals.lic_tab_casino'];
    licTabs.forEach((e, i) => { if (licKeys[i]) e.textContent = _t(licKeys[i]); });

    // Политика казино
    const polTitle = el('modal-pol-title');   if (polTitle)  polTitle.textContent  = _t('modals.policy_title');
    const polHint  = el('modal-pol-hint');    if (polHint)   polHint.childNodes.forEach(n => { if (n.nodeType === 3 && n.textContent.trim()) n.textContent = ' ' + _t('modals.policy_scroll_hint'); });
    const polBtn   = el('pol-btn');           if (polBtn)    polBtn.textContent    = _t('modals.policy_accept');

    // Политика пополнения
    const depPolT  = el('modal-dep-pol-title'); if (depPolT) depPolT.textContent = _t('modals.dep_policy_title');

    // Поддержка
    const supTitle = el('modal-sup-title-text'); if (supTitle) supTitle.textContent = _t('modals.support_title');
    const supCL    = el('sup-contact-lbl');   if (supCL)     supCL.textContent  = _t('modals.support_contact_label');
    const supSL    = el('sup-subject-lbl');   if (supSL)     supSL.textContent  = _t('modals.support_subject_label');
    const supDL    = el('sup-desc-lbl');      if (supDL)     supDL.textContent  = _t('modals.support_desc_label');
    const supC     = el('sup-contact');       if (supC)      supC.placeholder   = _t('modals.support_contact_ph');
    const supS     = el('sup-subj');          if (supS)      supS.placeholder   = _t('modals.support_subject_ph');
    const supD     = el('sup-desc');          if (supD)      supD.placeholder   = _t('modals.support_desc_ph');
    const supSend  = el('sup-send-btn');      if (supSend)   supSend.textContent = _t('modals.support_send');

    // FAQ
    const faqTitle = el('modal-faq-title-text'); if (faqTitle) faqTitle.textContent = _t('modals.faq_title');

    // Смарт-поиск
    const ssTitle  = el('modal-ss-title');    if (ssTitle)   ssTitle.childNodes.forEach(n => { if (n.nodeType === 3 && n.textContent.trim()) n.textContent = '🔍 ' + _t('modals.smart_search_title'); });
    const ssInp    = el('smart-search-inp');  if (ssInp)     ssInp.placeholder  = _t('modals.smart_search_ph');

    // Промокод модалка
    const promoMT  = el('modal-promo-title-text'); if (promoMT) promoMT.textContent = _t('modals.promo_title');
    const promoMI  = el('promo-inp');         if (promoMI)   promoMI.placeholder = _t('modals.promo_ph');
    const promoMA  = el('promo-apply-btn');   if (promoMA)   promoMA.textContent  = _t('modals.promo_apply');

    // Игровая модалка — кнопка играть
    const gmPlay   = el('gm-play-btn');
    if (gmPlay) gmPlay.querySelector('span').innerHTML =
      `<svg width="15" height="15" viewBox="0 0 20 20" fill="currentColor"><path d="M5 3l12 7-12 7V3z"/></svg>${_t('modals.game_play')}`;
    txt('tev-subtitle', _t('tournaments.hero_sub') || 'Compete. Win. Take the prize.');
    txt('tev-minbet-label', _t('tournaments.min_bet') || 'MIN BET');
    txt('tev-game-label', _t('tournaments.game') || 'Game');
    txt('tev-players-title', _t('tournaments.event_players_title') || 'Event leaderboard');
    txt('tev-open-game-btn', _t('tournaments.open_game') || 'Open game');

    // Избранное модалка
    const favMT    = el('modal-fav-title-text'); if (favMT) favMT.textContent = _t('modals.fav_title');
    const favMSrch = el('fav-search');        if (favMSrch)  favMSrch.placeholder = _t('modals.fav_search_ph');
    const favCntL  = el('fav-cnt-label');     if (favCntL)   favCntL.textContent  = _t('modals.fav_selected') + ':';
    const favAddB  = el('fav-add-btn');       if (favAddB)   favAddB.childNodes.forEach(n => { if (n.nodeType === 3 && n.textContent.trim()) n.textContent = ' ' + _t('modals.fav_add'); });

    // Forgot password
    const forgotT  = el('modal-forgot-title-text'); if (forgotT) forgotT.textContent = _t('modals.forgot_title');
    const forgotS  = el('modal-forgot-sub');   if (forgotS)  forgotS.textContent  = _t('modals.forgot_sub');
    const forgotCL = el('forgot-contact-lbl'); if (forgotCL) forgotCL.textContent = _t('modals.forgot_contact_label');
    const forgotCI = el('forgot-contact');     if (forgotCI) forgotCI.placeholder = _t('modals.forgot_contact_ph');
    const forgotFL = el('forgot-2fa-lbl');     if (forgotFL) forgotFL.textContent = _t('modals.forgot_2fa_label');
    const forgotFH = el('forgot-2fa-hint');    if (forgotFH) forgotFH.textContent = _t('modals.forgot_2fa_hint');
    const forgotB  = el('forgot-btn');         if (forgotB)  forgotB.textContent  = _t('modals.forgot_send');

    // Аватарка
    const avTitle  = el('modal-av-title');    if (avTitle)   avTitle.textContent  = _t('settings.avatar_title');
    const avUpload = el('av-upload-text');    if (avUpload)  avUpload.textContent = _t('settings.avatar_upload');
    const avHint   = el('av-upload-hint');    if (avHint)    avHint.textContent   = _t('settings.avatar_hint');
    const avSave   = el('av-save-btn');       if (avSave)    avSave.textContent   = _t('settings.avatar_save');

    // Верификация
    const verT     = el('modal-ver-title-text'); if (verT) verT.textContent = _t('modals.verify_title');
    const verUpload= el('ver-upload-text');   if (verUpload) verUpload.textContent = _t('modals.verify_upload');
    const verHint  = el('ver-upload-hint');   if (verHint)   verHint.textContent  = _t('modals.verify_hint');
    const verSend  = el('ver-send-btn');      if (verSend)   verSend.textContent  = _t('modals.verify_send');

    // 2FA модалка
    const tfaMSub  = el('tfa-sub');           if (tfaMSub)   tfaMSub.textContent  = _t('auth.tfa_desc');
    const tfaMPw   = el('tfa-pw');            if (tfaMPw)    tfaMPw.placeholder   = _t('auth.tfa_password_ph');
    const tfaMCode = el('tfa-code');          if (tfaMCode)  tfaMCode.placeholder = _t('auth.tfa_code_ph');
    const tfaMBtn  = el('tfa-btn');           if (tfaMBtn)   tfaMBtn.textContent  = _t('auth.tfa_btn');
    const tfaSH    = el('tfa-code-hint');     if (tfaSH)     tfaSH.textContent    = _t('auth.tfa_save_hint');
    const tfaYC    = el('tfa-code-lbl');      if (tfaYC)     tfaYC.textContent    = _t('auth.tfa_your_code');
  }

  function _applyFooter() {
    const map = {
      'about':'footer.about','faq':'footer.faq','rules':'footer.rules',
      'licenses':'footer.licenses','providers':'footer.providers',
      'payments':'footer.payments','contacts':'footer.contacts',
      'support':'footer.support','all_games':'footer.all_games',
      'crash':'footer.crash','roulette':'footer.roulette','slots':'footer.slots',
      'jump':'footer.jump','jackpots':'footer.jackpots','blackjack':'footer.blackjack',
      'table':'footer.table'
    };
    document.querySelectorAll('[data-btm-id]').forEach(e => {
      const k = map[e.dataset.btmId]; if (k) e.textContent = _t(k);
    });
    // Copyright
    const copy = el('btm-copy');
    if (copy) copy.textContent = `© 2026 VELOX Casino. ${_t('footer.rights')}. 18+`;
  }

  function _applyPageTitles() {
    const popTitle = el('home-popular-title'); if (popTitle) popTitle.textContent = _t('home.popular_title');
    const allTitle = el('all-sec-title');      if (allTitle) allTitle.textContent = _t('home.all_games_title');
    const allLink  = el('home-all-link');      if (allLink)  allLink.textContent  = _t('home.all_games_title');
    txt('home-sport-all', _t('home.see_all') || 'All →');
    txt('home-rtp-all', _t('home.see_all') || 'All →');
    txt('home-prov-all', _t('home.see_all') || 'All →');
    const favTitle = el('fav-section-title');  if (favTitle) favTitle.textContent = _t('favorites.title');
    const favEmpty = el('fav-empty-msg');      if (favEmpty) favEmpty.textContent = _t('favorites.empty');
    const favEmptySub = el('fav-empty-sub');   if (favEmptySub) favEmptySub.textContent = _t('favorites.empty_sub');
    const favAddPage  = el('fav-add-page-btn');
    if (favAddPage) favAddPage.childNodes.forEach(n => { if (n.nodeType === 3 && n.textContent.trim()) n.textContent = ' ' + _t('favorites.add_btn'); });
    const raffTitle = el('raffles-section-title');   if (raffTitle) raffTitle.textContent = _t('raffles.title');
    const tourTitle = el('tournaments-section-title');if (tourTitle) tourTitle.textContent = _t('tournaments.title');
    txt('tournaments-hero-sub', _t('tournaments.hero_sub') || 'Compete. Win. Take the prize.');
    txt('tournaments-highwins-title', _t('tournaments.high_wins_title') || 'Top wins');
    txt('tournaments-alltime-title', _t('tournaments.all_time_title') || 'All-time big wins');
    const promTitle = el('promotions-section-title');if (promTitle) promTitle.textContent = _t('promotions.title');
  }

  function _applyRgModal() {
    const m = {
      'rg-modal-title':   'rg.title',
      'rg-modal-sub':     'rg.sub',
      'rg-limits-title':  'rg.deposit_limits_title',
      'rg-daily-lbl':     'rg.daily_limit_label',
      'rg-weekly-lbl':    'rg.weekly_limit_label',
      'rg-save-lbl':      'rg.save_limits_btn',
      'rg-excl-title':    'rg.self_exclusion_title',
      'rg-excl-sub':      'rg.self_exclusion_sub',
      'rg-excl-btn-lbl':  'rg.exclude_btn',
      'rg-excl-confirm-title': 'rg.exclude_confirm_title',
      'rg-excl-confirm-text': 'rg.exclude_confirm_text',
      'rg-excl-confirm-cancel': 'rg.exclude_cancel_btn',
      'rg-excl-confirm-apply': 'rg.exclude_apply_btn',
      'rg-ex-7':          'rg.exclude_7',
      'rg-ex-30':         'rg.exclude_30',
      'rg-ex-90':         'rg.exclude_90',
      'rg-ex-180':        'rg.exclude_180',
      'rg-ex-365':        'rg.exclude_365',
      'rg-ex-forever':    'rg.exclude_forever',
    };
    for(const [id,key] of Object.entries(m)){
      const e=el(id); if(e) e.textContent=_t(key)||e.textContent;
    }
    const inp=el('rg-daily-inp');  if(inp) inp.placeholder=_t('rg.limit_placeholder')||inp.placeholder;
    const inp2=el('rg-weekly-inp');if(inp2) inp2.placeholder=_t('rg.limit_placeholder')||inp2.placeholder;
  }

  function _applyAffiliateModal() {
    const m = {
      'aff-modal-title': 'affiliate.title',
      'aff-modal-sub':   'affiliate.sub',
      'aff-link-lbl':    'affiliate.your_link',
      'aff-copy-lbl':    'affiliate.copy_link',
      'aff-inv-lbl':     'affiliate.invited_count',
      'aff-earn-lbl':    'affiliate.total_earned',
      'aff-how-lbl':     'affiliate.how_it_works',
      'aff-step1':       'affiliate.step1',
      'aff-step2':       'affiliate.step2',
      'aff-step3':       'affiliate.step3',
      'aff-bonus-lbl':   'affiliate.bonus_per_friend',
    };
    for(const [id,key] of Object.entries(m)){
      const e=el(id); if(e) e.textContent=_t(key)||e.textContent;
    }
  }

  function _applyResetModal() {
    const m = {
      'reset-modal-title':  'reset_password.title',
      'reset-checking-lbl': 'reset_password.checking_token',
      'reset-pw-lbl':       'reset_password.new_password_label',
      'reset-cpw-lbl':      'reset_password.confirm_password_label',
      'reset-submit-lbl':   'reset_password.reset_btn',
    };
    for(const [id,key] of Object.entries(m)){
      const e=el(id); if(e) e.textContent=_t(key)||e.textContent;
    }
  }

  function _applyVipModal() {
    const e1 = el('vip-modal-title'); if (e1) e1.textContent = _tv('vip.vip_modal_title','VIP-Клуб');
    const e2 = el('vip-modal-sub');   if (e2) e2.textContent = _tv('vip.vip_modal_sub','Эксклюзивные привилегии');
    const e3 = el('vip-select-tier-lbl');   if (e3) e3.textContent = _tv('vip.vip_select_tier','Выбери статус');
    const e4 = el('vip-select-period-lbl'); if (e4) e4.textContent = _tv('vip.vip_select_period','Срок подписки');
    const e5 = el('vip-perks-title');       if (e5) e5.textContent = _tv('vip.vip_perks_title','Привилегии:');
    const e6 = el('vip-buy-btn');           if (e6 && e6.disabled) e6.textContent = _tv('vip.vip_buy_btn','Приобрести');
  }

  // Строим панель выбора языка в сайдбаре
  function _buildLangPanel() {
    const panel = el('sb-lang-panel');
    if (!panel) return;

    // Fallback: if API failed and no langs loaded, use defaults from site config
    const langs = _availableLangs.length ? _availableLangs : [
      {code:'RU', label:'Русский',     flag:'🇷🇺'},
      {code:'UA', label:'Українська',  flag:'🇺🇦'},
      {code:'US', label:'English',     flag:'🇬🇧'},
    ];

    const cur = langs.find(l => l.code === _lang) || langs[0];

    panel.innerHTML = `
      <div class="sb-lang-wrap" id="sb-lang-wrap">
        <button class="sb-lang-trigger" id="sb-lang-trigger" onclick="V.toggleLangMenu(false,event)">
          <span class="sb-lang-cur-flag">${cur.flag}</span>
          <span class="sb-lang-cur-label">${cur.label}</span>
          <span class="sb-lang-chevron" id="sb-lang-chevron">
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 3.5L5 6.5L8 3.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
        </button>
        <div class="sb-lang-menu" id="sb-lang-menu">
          ${langs.map(l => `
            <button class="sb-lang-opt${_lang === l.code ? ' active' : ''}" onclick="event.stopPropagation();V.setLang('${l.code}');V.toggleLangMenu(true)">
              <span class="sb-lang-opt-flag">${l.flag}</span>
              <span class="sb-lang-opt-label">${l.label}</span>
              ${_lang === l.code ? '<span class="sb-lang-opt-check"><svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg></span>' : ''}
            </button>
          `).join('')}
        </div>
      </div>`;

    panel.style.display = '';
  }

  function toggleLangMenu(forceClose, event) {
    // Останавливаем всплытие — иначе outsideClick закроет меню сразу после открытия
    if (event) event.stopPropagation();
    const menu    = el('sb-lang-menu');
    const chevron = el('sb-lang-chevron');
    const wrap    = el('sb-lang-wrap');
    if (!menu) return;
    const isOpen = menu.classList.contains('open');
    if (forceClose === true || isOpen) {
      menu.classList.remove('open');
      chevron?.classList.remove('open');
      wrap?.classList.remove('open');
    } else {
      menu.classList.add('open');
      chevron?.classList.add('open');
      wrap?.classList.add('open');
    }
  }

  async function loadGames() {
    try { const r = await api('/api/games'); S.games = (r.games||[]).filter(g => g.show_in_grid !== false && !g.gift); } catch {}
  }

  // Локализованное имя игры: берём из i18n по текущему языку, fallback → g.name → g.id
  function _gname(g) {
    const key = {RU:'ru',UA:'ua',US:'us'}[_lang] || 'ru';
    return (g.i18n?.[key]?.name) || g.name || g.id || '';
  }
  function _gdesc(g) {
    const key = {RU:'ru',UA:'ua',US:'us'}[_lang] || 'ru';
    return (g.i18n?.[key]?.description) || g.description || '';
  }

  // Получить локализованное название игры по текущему языку
  function _gameName(g) {
    if (!g) return '';
    const lmap = { RU:'ru', UA:'ua', US:'us' };
    const key  = lmap[_lang] || 'ru';
    return (g.i18n && g.i18n[key] && g.i18n[key].name) || g.name || g.id || '';
  }

  // Получить локализованное описание игры
  function _gameDesc(g) {
    if (!g) return '';
    const lmap = { RU:'ru', UA:'ua', US:'us' };
    const key  = lmap[_lang] || 'ru';
    return (g.i18n && g.i18n[key] && g.i18n[key].description) || g.description || '';
  }
  async function loadSidebarCfg() {
    try { S.sidebarCfg = await api('/api/config/sidebar'); } catch {}
  }
  async function checkMe() {
    // Мгновенно скрываем "Войти" если знаем что залогинен — ноль flash
    const ge = el('tb-guest');
    try {
      if (localStorage.getItem('velox_authed') === '1' && ge) {
        ge.style.visibility = 'hidden'; ge.style.opacity = '0';
      }
    } catch(e) {}
    try {
      const r = await api('/api/auth/me');
      if (r.user) {
        setUser(r.user);
      } else {
        // Сессия истекла — чистим флаг, возвращаем гостя
        try { localStorage.removeItem('velox_authed'); } catch(e) {}
        if (ge) { ge.style.visibility = ''; ge.style.opacity = ''; }
      }
    } catch {
      if (ge) { ge.style.visibility = ''; ge.style.opacity = ''; }
    }
  }

  // ── BANNERS ─────────────────────────────────────────────────────────────────
  async function loadBanners() {
    const normalizeBannerImage = (b) => {
      const out = { ...(b || {}) };
      const v = String(out.image_ad || '').trim();
      if (v.startsWith('/support_uploads/banners/')) {
        const fname = v.split('/').pop();
        out.image_ad = `/static/uploads/banners/${fname}`;
      }
      return out;
    };
    try {
      const r = await api('/api/config/banners');
      S._sidebarBanner = r.sidebar_banner ? normalizeBannerImage(r.sidebar_banner) : null;
      S._pageBanners   = (r.page_banners || []).map(normalizeBannerImage).filter(b => !b.invisible);
    } catch {
      S._sidebarBanner = null; S._pageBanners = [];
    }
  }

  function initBanner() {
    S.banners = (S._pageBanners||[]).filter(b => b.invisible !== true);
    // Всегда показываем обёртку — fallback-градиент рендерится если баннеров нет
    ['bwrap-home','bwrap-games'].forEach(id => { const e=el(id); if(e) e.style.display=''; });
    if (!S.banners.length) {
      // Fallback: красивый градиент без контента
      ['home','games'].forEach(k => {
        const wrap = el('bbi-wrap-'+k); if (!wrap) return;
        const fb = document.createElement('div');
        fb.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#0a0a2a 0%,#0d0a2e 40%,#0a1628 100%);position:relative;overflow:hidden';
        fb.innerHTML = '<div style="position:absolute;inset:0;background:radial-gradient(ellipse 60% 80% at 30% 50%,rgba(0,229,255,.07),transparent 70%),radial-gradient(ellipse 50% 60% at 70% 40%,rgba(168,85,247,.06),transparent 70%)"></div>';
        wrap.innerHTML = ''; wrap.appendChild(fb);
      });
      return;
    }
    S.bannerIdx = 0;
    _renderBanner();
    _bannerDots('bdots-home'); _bannerDots('bdots-games');
    _scheduleBanner();
  }

  function _renderBanner() {
    const b = S.banners[S.bannerIdx]; if (!b) return;
    ['home','games'].forEach(k => {
      const wrap = el('bbi-wrap-'+k); if (!wrap) return;
      wrap.innerHTML = '';
      if (!b.image_ad?.trim()) {
        // Fallback gradient banner
        const fb = document.createElement('div');
        fb.style.cssText = 'width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,#0a0a2a 0%,#0d0a2e 40%,#0a1628 100%);position:relative;overflow:hidden';
        fb.innerHTML = `<div style="position:absolute;inset:0;background:radial-gradient(ellipse 60% 80% at 30% 50%,rgba(0,229,255,.07),transparent 70%),radial-gradient(ellipse 50% 60% at 70% 40%,rgba(168,85,247,.06),transparent 70%)"></div>
          <div style="position:relative;text-align:center;padding:0 24px">
            <div style="font-size:22px;font-weight:700;font-family:var(--ffd);color:var(--t1);letter-spacing:1px;text-shadow:0 0 30px rgba(0,229,255,.3)">${b.modal_text||''}</div>
          </div>`;
        wrap.appendChild(fb);
        return;
      }
      const ext = (b.image_ad||'').split('.').pop().toLowerCase();
      let media;
      if (ext === 'mp4' || ext === 'webm') {
        media = document.createElement('video');
        media.autoplay = true; media.loop = true; media.muted = true; media.playsInline = true;
      } else {
        media = document.createElement('img');
      }
      media.src = b.image_ad;
      media.style.cssText = 'width:100%;height:100%;object-fit:cover;object-position:center;display:block;opacity:0;transition:opacity .35s;image-rendering:-webkit-optimize-contrast;image-rendering:high-quality;transform:translateZ(0)';
      const onLoad = () => { media.style.opacity = '1'; };
      media.tagName === 'VIDEO' ? media.addEventListener('canplay', onLoad) : (media.onload = onLoad);
      media.onerror = () => {
        wrap.innerHTML = '';
        const fb2 = document.createElement('div');
        fb2.style.cssText = 'width:100%;height:100%;background:linear-gradient(135deg,#0a0a2a,#0d0a2e)';
        wrap.appendChild(fb2);
      };
      wrap.appendChild(media);
    });
    ['bdots-home','bdots-games'].forEach(id => {
      el(id)?.querySelectorAll('.b-dot').forEach((d,i) => d.classList.toggle('on', i===S.bannerIdx));
    });
  }

  function _bannerDots(cid) {
    const c = el(cid); if (!c) return;
    c.innerHTML = S.banners.map((_,i) =>
      `<div class="b-dot${i===0?' on':''}" onclick="event.stopPropagation();V.bannerGoto(${i})"></div>`
    ).join('');
  }

  function _scheduleBanner() {
    clearTimeout(S.bannerTimer);
    const ms = ((S.banners[S.bannerIdx]?.timer_change)||5)*1000;
    S.bannerTimer = setTimeout(() => bannerStep(1), ms);
  }
  function bannerStep(dir) {
    if (!S.banners.length) return;
    S.bannerIdx = (S.bannerIdx + dir + S.banners.length) % S.banners.length;
    _renderBanner(); _scheduleBanner();
  }
  function bannerGoto(i) { S.bannerIdx=i; _renderBanner(); _scheduleBanner(); }

  function bannerClick() {
    const b = S.banners[S.bannerIdx]; if (!b) return;
    const vw=el('bni-video-wrap'), vid=el('bni-video');
    if (b.modal_video && b.modal_mp4) {
      if(vid) vid.src = b.modal_mp4;
      if(vw)  vw.style.display = 'block';
    } else {
      if(vw)  vw.style.display = 'none';
      if(vid) { vid.pause(); vid.src=''; }
    }
    el('bni-text').textContent = b.modal_text||'';
    el('bni-btns').innerHTML   = _socialBtns(b);
    modal('banner-info');
  }

  function buildSidebarBanner() {
    const wrap=el('sb-banner'); if(!wrap) return;
    const sb=S._sidebarBanner;
    if (!sb || sb.invisible) { wrap.style.display='none'; return; }
    if (!sb.image_ad?.trim()) {
      // Show gradient fallback
      el('sb-banner-img').style.display='none';
      wrap.style.background='linear-gradient(135deg,#0d0a2e 0%,#0a1628 100%)';
      wrap.style.display='block';
      return;
    }
    const img=el('sb-banner-img');
    if (img) {
      img.onload  = () => wrap.style.display='block';
      img.onerror = () => { img.style.display='none'; wrap.style.background='linear-gradient(135deg,#0d0a2e,#0a1628)'; wrap.style.display='block'; };
      img.src = sb.image_ad;
    }
  }
  function sidebarBannerClick() {
    const sb=S._sidebarBanner; if(!sb) return;
    el('sbb-text').textContent = sb.modal_text||'';
    el('sbb-btns').innerHTML   = _socialBtns(sb);
    modal('sb-banner');
  }

  const _TG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.562 8.248-1.97 9.289c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.932z"/></svg>';
  const _YT = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31.2 31.2 0 0 0 0 12a31.2 31.2 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31.2 31.2 0 0 0 24 12a31.2 31.2 0 0 0-.5-5.8zM9.7 15.5V8.5l6.3 3.5-6.3 3.5z"/></svg>';
  const _VI = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M19.4 4H4.6A2.6 2.6 0 0 0 2 6.6v10.8A2.6 2.6 0 0 0 4.6 20h2.8v3.4l3.7-3.4h8.3a2.6 2.6 0 0 0 2.6-2.6V6.6A2.6 2.6 0 0 0 19.4 4z"/></svg>';
  const _IG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5zm5 5.5A4.5 4.5 0 1 0 16.5 12 4.5 4.5 0 0 0 12 7.5zm0 7.4A2.9 2.9 0 1 1 14.9 12 2.9 2.9 0 0 1 12 14.9zm5.4-8.9a1 1 0 1 1-1-1 1 1 0 0 1 1 1z"/></svg>';
  const _TW = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M18.9 2H22l-6.8 7.8L23 22h-6.1l-4.8-6.4L6.5 22H3.4l7.3-8.4L1 2h6.2l4.3 5.8L18.9 2z"/></svg>';
  const _WA = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.7 15l-1.3 5 5.1-1.3A10 10 0 1 0 12 2zm5.2 14.4c-.2.7-1.2 1.3-1.8 1.4-.5.1-1.2.2-3.8-.9-3.4-1.5-5.6-5.1-5.8-5.3-.2-.3-1.4-1.8-1.4-3.4 0-1.6.8-2.4 1.1-2.8.3-.3.7-.4.9-.4h.7c.2 0 .5 0 .7.5.2.6.8 2 .9 2.2.1.2.1.4 0 .7-.1.2-.2.4-.4.6-.2.2-.3.4-.5.6-.2.2-.4.4-.2.8.2.4 1 1.7 2.1 2.8 1.5 1.4 2.8 1.8 3.2 2 .3.1.6.1.8-.1.2-.3.9-1 1.2-1.3.3-.3.5-.3.8-.2.3.1 2 .9 2.3 1 .3.2.5.2.6.4.1.1.1.8-.1 1.4z"/></svg>';
  const _VK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M3 7.2c.1 6.8 3.6 10.8 9.7 10.8h.3v-3.9c2.2.2 3.9 1.8 4.6 3.9H21c-.9-3.2-3.2-5-4.6-5.7 1.4-.9 3.4-3 3.9-5.1h-3c-.7 2.3-2.5 4.4-4.3 4.6V7.2h-3v8.2C8.1 15 5.9 12.7 5.8 7.2H3z"/></svg>';
  function _socialLinkDefs(b) {
    const defs = {
      telegram:  { href: b.modal_telegramButton, label: 'Telegram',  icon: _TG, color: '#229ED9' },
      youtube:   { href: b.modal_youtubeButton,  label: 'YouTube',   icon: _YT, color: '#c00' },
      viber:     { href: b.modal_viberButton,    label: 'Viber',     icon: _VI, color: '#7c4dff' },
      whatsapp:  { href: b.modal_whatsappButton, label: 'WhatsApp',  icon: _WA, color: '#25d366' },
      instagram: { href: b.modal_instagramButton,label: 'Instagram', icon: _IG, color: '#ff4081' },
      twitter:   { href: b.modal_twitterButton,  label: 'Twitter',   icon: _TW, color: '#4fc3f7' },
      vk:        { href: b.modal_vkButton,       label: 'VK',        icon: _VK, color: '#4c75a3' },
      url:       { href: b.modal_urlButton,      label: 'Open link', icon: '🔗', color: 'rgba(255,255,255,.12)', textColor: '#fff', border: '1px solid rgba(255,255,255,.2)' },
    };
    const order = Array.isArray(b.link_order) && b.link_order.length ? b.link_order : ['telegram','youtube','viber','whatsapp','instagram','twitter','vk','url'];
    return order.map(id => defs[id]).filter(Boolean).filter(x => x.href);
  }
  function _socialBtns(b) {
    const s='display:flex;align-items:center;justify-content:center;gap:7px;padding:10px 0;border-radius:var(--r);font-size:13px;font-weight:600;text-decoration:none;width:100%;margin-top:6px';
    return _socialLinkDefs(b).map(x =>
      `<a href="${x.href}" target="_blank" rel="noopener" style="${s};background:${x.color};color:${x.textColor || '#fff'};${x.border || ''}">${x.icon} ${x.label}</a>`
    ).join('');
  }

  // ── SIDEBAR LOGO ─────────────────────────────────────────────────────────────
  function buildSidebarLogo() {
    const wrap=el('sb-logo-wrap'); if(!wrap) return;
    const src=S.cfg?.sidebar_uploads?.logo_image||'';
    if (!src) { wrap.innerHTML='<span class="sb-logo-fallback">⚡ VELOX</span>'; return; }
    const img=new Image();
    img.onload  = () => wrap.innerHTML=`<img src="${src}" class="sb-logo-img" alt="Logo">`;
    img.onerror = () => wrap.innerHTML='<span class="sb-logo-fallback">⚡ VELOX</span>';
    img.src=src;
  }

  // ── SIDEBAR SVG ICONS ────────────────────────────────────────────────────────
  const ICONS = {
    ticket:     `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8a2 2 0 0 0 0 4v2a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-2a2 2 0 0 0 0-4V6a1 1 0 0 0-1-1H3a1 1 0 0 0-1 1v2z"/><path d="M8 7v8M11 9l1 2-1 2M14 9l1 2-1 2" stroke-width="1.2"/></svg>`,
    slots:      `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="16" height="14" rx="2"/><path d="M8 4v14M14 4v14M3 11h16"/></svg>`,
    home:       `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5L11 3l8 7.5V19a1.5 1.5 0 0 1-1.5 1.5H14v-7H8v7H4.5A1.5 1.5 0 0 1 3 19V10.5z"/></svg>`,
    search:     `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="9.5" cy="9.5" r="5.5"/><path d="M19 19l-4-4"/><path d="M7.5 7.5c.5-1 1.5-1.5 2.5-1.5" stroke-width="1.2"/></svg>`,
    spin:       `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M11 4V2M11 20v-2M4 11H2M20 11h-2"/><circle cx="11" cy="11" r="2.5" fill="currentColor" stroke="none"/><path d="M11 11l4-4"/></svg>`,
    promotions: `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12.5l-8.5 8.5a2 2 0 0 1-2.83 0l-7.17-7.17a2 2 0 0 1 0-2.83L10 2.5h8a2 2 0 0 1 2 2v8z"/><circle cx="15" cy="7" r="1.5" fill="currentColor" stroke="none"/><path d="M8 14l6-6"/></svg>`,
    raffles:    `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 19h6M11 15v4"/><path d="M5 3h12v8a6 6 0 0 1-12 0V3z"/><path d="M5 6H3a2 2 0 0 0 0 4h2M17 6h2a2 2 0 0 1 0 4h-2"/></svg>`,
    tournament: `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h14v3a5 5 0 0 1-5 5h-4a5 5 0 0 1-5-5V4z"/><path d="M8 18h6M11 12v6"/><path d="M4 6H2a2 2 0 0 0 0 4h2M18 6h2a2 2 0 0 1 0 4h-2"/></svg>`,
    live:       `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="11" cy="11" r="3" fill="currentColor" stroke="none"/><path d="M4.2 4.2a9.5 9.5 0 0 0 0 13.6M17.8 4.2a9.5 9.5 0 0 1 0 13.6"/><path d="M7 7a6 6 0 0 0 0 8M15 7a6 6 0 0 1 0 8"/></svg>`,
    blackjack:  `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="2" width="10" height="14" rx="2"/><rect x="8" y="6" width="10" height="14" rx="2"/><path d="M9 9l2 4 2-4" stroke-width="1.2"/></svg>`,
    roulette:   `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><circle cx="11" cy="11" r="3"/><path d="M11 3v2M11 17v2M3 11h2M17 11h2M5.6 5.6l1.4 1.4M15 15l1.4 1.4"/></svg>`,
    crash:      `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16L9 8l3 4 3-5 4 9"/><circle cx="18" cy="4" r="2" fill="currentColor" stroke="none"/></svg>`,
    show:       `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="5" width="18" height="13" rx="2"/><path d="M7 2l-2 3M15 2l2 3M9 10l5 3-5 3v-6z" fill="currentColor" stroke="none"/><path d="M9 10l5 3-5 3v-6z"/></svg>`,
    more:       `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="5" cy="11" r="1.5" fill="currentColor" stroke="none"/><circle cx="11" cy="11" r="1.5" fill="currentColor" stroke="none"/><circle cx="17" cy="11" r="1.5" fill="currentColor" stroke="none"/></svg>`,
    games:      `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="18" height="11" rx="4"/><path d="M7 11v4M5 13h4"/><circle cx="15" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="17" cy="14" r="1" fill="currentColor" stroke="none"/><path d="M10 4c0-1.1.9-2 2-2h0a2 2 0 0 1 2 2v3H10V4z"/></svg>`,
    favorites:  `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11 2l2.47 5.01L19 8.18l-4 3.9.94 5.5L11 15l-4.94 2.58.94-5.5L3 8.18l5.53-.17L11 2z"/></svg>`,
    details:    `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="16" height="16" rx="3"/><path d="M7 8h8M7 11h8M7 14h5"/></svg>`,
    partners:   `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="8" r="2.5"/><circle cx="15" cy="8" r="2.5"/><path d="M3.5 17c.8-2.4 2.6-3.6 4.9-3.6s4.1 1.2 4.9 3.6"/><path d="M10.6 17c.8-2.2 2.4-3.2 4.4-3.2 1.5 0 2.9.6 3.9 1.8"/></svg>`,
    vip:        `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7l3 8h4l3-8 3 8h1"/><path d="M18 7l-4 4"/><circle cx="11" cy="3" r="1.5" fill="currentColor" stroke="none"/></svg>`,
    sport:      `<svg viewBox="0 0 22 22" fill="none" width="17" height="17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="M11 3c0 0 3 4 3 8s-3 8-3 8M11 3c0 0-3 4-3 8s3 8 3 8M3 11h16"/><path d="M4.5 7h13M4.5 15h13"/></svg>`,
  };
  function _icon(name) {
    return `<span class="sb-icon-wrap">${ICONS[name]||ICONS.games}</span>`;
  }

  // ── BUILD SIDEBAR FROM JSON CONFIG ───────────────────────────────────────────
  function buildSidebar() {
    const nav = el('sb-nav'); if (!nav) return;
    const items = S.sidebarCfg?.items || _defaultSidebarItems();
    nav.innerHTML = '';

    nav.innerHTML += `<button class="sb-item" id="sb-fav" data-view="favorites" onclick="V.go('favorites')" style="display:none">${_icon('favorites')}${_t('sidebar.favorites')||_t('nav.favorites')}</button>`;

    items.forEach(item => {
      if (!item.enabled && item.enabled !== undefined) return;
      nav.innerHTML += _renderSidebarItem(item);
    });
  }

  // Sidebar label translation map
  function _sbLabel(item) {
    const keyMap = {
      'lucky-ticket':'sidebar.lucky_ticket','slots':'sidebar.slots',
      'smart-search':'sidebar.smart_search','daily-spin':'sidebar.daily_spin',
      'promotions':'sidebar.promotions','raffles':'sidebar.raffles',
      'live-games-header':'sidebar.games','blackjack':'sidebar.blackjack',
      'roulette':'sidebar.roulette','crash-games':'sidebar.crash_games',
      'game-shows':'sidebar.game_shows','more-games':'sidebar.more',
      'details':'sidebar.details','vip':'sidebar.vip_club',
      'sport':'sidebar.sport', 'games-all':'sidebar.games',
      'live-game':'sidebar.live_game', 'partners':'sidebar.partners',
      'home':'sidebar.home', 'tournaments':'sidebar.tournaments',
    };
    const key = keyMap[item.id] || item.i18n_key;
    const translated = key ? _t(key) : null;
    return (translated && translated !== key) ? translated : (item.label || '');
  }

  function _renderSidebarItem(item) {
    switch (item.type) {
      case 'separator':
        return `<div class="sb-sep"></div>`;

      case 'lucky_ticket':
        return `<button class="sb-item sb-lucky" id="sb-lottery" style="display:none" onclick="V.openLuckyTicket()">${_icon('ticket')}${_sbLabel(item)}<span class="sb-badge-new">1×</span></button>`;

      case 'nav_item': {
        const action = _sidebarAction(item);
        return `<button class="sb-item" data-id="${item.id}" data-view="${item.view||''}" data-tag="${item.tag||''}" onclick="${action}">${_icon(item.icon)}${_sbLabel(item)}</button>`;
      }

      case 'section_header': {
        const children = (item.children||[]).filter(c => c.enabled !== false);
        const childrenHtml = children.map(c => {
          const action = _sidebarAction(c);
          return `<button class="sb-sub-btn sb-item-child" data-id="${c.id}" data-tag="${c.tag||''}" onclick="${action}">${_icon(c.icon)}${_sbLabel(c)}</button>`;
        }).join('');
        return `
          <button class="sb-det-btn" onclick="V.toggleSection('sec-${item.id}','arr-${item.id}')">
            ${_icon(item.icon)}<span>${_sbLabel(item)}</span>
            <span class="sb-det-arr" id="arr-${item.id}">▸</span>
          </button>
          <div class="sb-det-sub" id="sec-${item.id}">${childrenHtml}</div>`;
      }

      case 'details':
        return `
          <button class="sb-det-btn" onclick="V.toggleDet()">
            ${_icon('details')}${_t('sidebar.details')}
            <span class="sb-det-arr" id="sb-det-arr">▸</span>
          </button>
          <div class="sb-det-sub" id="sb-det-sub">
            <button class="sb-sub-btn" onclick="V.modal('licenses')">${_icon('details')} ${_t('sidebar.licenses')}</button>
            <button class="sb-sub-btn" onclick="V.modal('faq')">${_icon('search')} ${_t('sidebar.faq')}</button>
            <button class="sb-sub-btn" onclick="V.modal('support')">${_icon('more')} ${_t('sidebar.support')}</button>
          </div>`;

      case 'partners': {
        const partners = _getPartnersList();
        const count = partners.length;
        const listHtml = partners.map(p => {
          const icon = p.icon_path
            ? `<img class="sb-sub-item-icon" src="${p.icon_path}" alt="${p.name || p.id}" draggable="false" onerror="this.style.display='none'">`
            : '';
          return `<div class="sb-sub-item-static">${icon}<span class="sb-sub-item-name">${p.name || p.id}</span></div>`;
        }).join('') || `<div class="sb-sub-item-static">—</div>`;
        return `
          <button type="button" class="sb-det-btn sb-partners-btn" onclick="V.toggleSection('sec-${item.id}','arr-${item.id}')">
            ${_icon('partners')}<span class="sb-partners-label">${_sbLabel(item)}</span>
            <span class="sb-partners-count">${count}</span>
            <span class="sb-det-arr" id="arr-${item.id}">▸</span>
          </button>
          <div class="sb-det-sub" id="sec-${item.id}">${listHtml}</div>`;
      }

      default: return '';
    }
  }

  function _sidebarAction(item) {
    if (item.action === 'filter_tag')  return `V.filterByTag('${item.tag}')`;
    if (item.action === 'view')        return `V.go('${item.view}')`;
    if (item.action === 'smart_search') return `V.openSmartSearch()`;
    if (item.action === 'daily_spin')   return `V.openDailySpin()`;
    if (item.action === 'modal_vip')    return `V.vipOpen()`;
    if (item.action === 'modal_faq')    return `V.modal('faq')`;
    if (item.action === 'modal_support') return `V.modal('support')`;
    return '';
  }

  function _defaultSidebarItems() {
    return [
      { type:'lucky_ticket', label:'Лаки-тикет', icon:'ticket', enabled:true },
      { type:'separator' },
      { type:'nav_item', id:'home',           label:'Главная',      icon:'home',       action:'view', view:'home',         enabled:true },
      { type:'nav_item', id:'smart-search', label:'Смарт-поиск',  icon:'search',     action:'smart_search', enabled:true },
      { type:'nav_item', id:'daily-spin',   label:'Дейли-крут',   icon:'spin',       action:'daily_spin',   enabled:false },
      { type:'nav_item', id:'vip',          label:'VIP-Клуб',     icon:'vip',        action:'modal_vip',    enabled:true },
      { type:'separator' },
      { type:'section_header', id:'live-games-header', label:'Игры', icon:'live', enabled:true, children:[
        { type:'nav_item', id:'live-game',  label:'Live Game',    icon:'live',       action:'filter_tag', tag:'live',      enabled:true },
        { type:'nav_item', id:'sport',      label:'Спорт',        icon:'sport',      action:'view', view:'sport',          enabled:true },
        { type:'nav_item', id:'slots',      label:'Слоты',        icon:'slots',      action:'filter_tag', tag:'slots',    enabled:true },
        { type:'nav_item', id:'blackjack',  label:'Блек-джек',    icon:'blackjack',  action:'filter_tag', tag:'blackjack', enabled:true },
        { type:'nav_item', id:'roulette',   label:'Рулетки',      icon:'roulette',   action:'filter_tag', tag:'roulette',  enabled:true },
        { type:'nav_item', id:'crash-games',label:'Краш игры',    icon:'crash',      action:'filter_tag', tag:'crash',     enabled:true },
        { type:'nav_item', id:'game-shows', label:'Игровые Шоу',  icon:'show',       action:'filter_tag', tag:'show',      enabled:true },
        { type:'nav_item', id:'more-games', label:'Ещё...',       icon:'more',       action:'view', view:'games',        enabled:true },
      ]},
      { type:'separator' },
      { type:'partners', id:'partners',     label:'Partners',     icon:'partners',                                   enabled:true },
      { type:'nav_item', id:'promotions',   label:'Акции',        icon:'promotions', action:'view', view:'promotions', enabled:true },
      { type:'nav_item', id:'raffles',      label:'Розыгрыши',    icon:'raffles',    action:'view', view:'raffles',    enabled:true },
      { type:'nav_item', id:'tournaments',  label:'Турниры',      icon:'tournament', action:'view', view:'tournaments',enabled:true },
      { type:'details',  id:'details',      label:'Детали',       icon:'details',                                    enabled:true },
    ];
  }

  async function loadPartnersCfg() {
    try { S.partnersCfg = await api('/api/config/partners'); } catch { S.partnersCfg = null; }
  }

  function _getPartnersList() {
    const arr = Array.isArray(S.partnersCfg?.partners) ? S.partnersCfg.partners : [];
    const seen = new Set();
    return arr.filter(p => {
      if (!p || p.enabled === false || !p.id) return false;
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });
  }

  /** Блокирует drag картинок/видео (кроме аватаров профиля) — надёжнее, чем только CSS */
  function _bindBlockImageDrag() {
    if (_bindBlockImageDrag._done) return;
    _bindBlockImageDrag._done = true;
    const allowEl = (node) => node && node.closest && node.closest('#set-hero-av, #tb-av, #dd-av, #av-modal-prev');
    document.addEventListener('dragstart', (e) => {
      const t = e.target;
      if (t instanceof HTMLImageElement || t instanceof HTMLVideoElement) {
        if (!allowEl(t)) e.preventDefault();
      }
    }, true);
  }

  function setupSidebarHover() {
    const sb=document.getElementById('sidebar');
    const tr=document.getElementById('sb-trigger');
    if (!sb||!tr) return;
    let timer=null;
    const show = () => { clearTimeout(timer); sb.classList.add('open'); };
    const hide = () => { timer=setTimeout(()=>sb.classList.remove('open'),220); };
    tr.addEventListener('mouseenter',show); tr.addEventListener('mouseleave',hide);
    sb.addEventListener('mouseenter',show); sb.addEventListener('mouseleave',hide);
    document.addEventListener('touchstart', e => {
      if (!sb.contains(e.target)&&!tr.contains(e.target)) sb.classList.remove('open');
    }, {passive:true});
  }

  function toggleSection(secId, arrId) {
    el(secId)?.classList.toggle('open');
    el(arrId)?.classList.toggle('open');
  }

  // ── TAG FILTER ───────────────────────────────────────────────────────────────
  function filterByTag(tag) {
    S.activeTag = tag;
    // Переходим на вид games
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    el('view-games')?.classList.add('active');
    S.view = 'games';
    _persistView('games');
    // Подсвечиваем активный элемент в сайдбаре
    document.querySelectorAll('.sb-item,.sb-item-child').forEach(b => {
      b.classList.toggle('active', b.dataset.tag===tag);
    });

    const filtered = S.games.filter(g => (g.tags||[]).includes(tag) || g.category===tag || g.provider_tag===tag || (g.provider||'').toLowerCase()===tag.toLowerCase());
    renderGrid('all-grid', filtered);
    // Обновляем заголовок
    const secTitle = el('all-sec-title');
    if (secTitle) {
      const tagLabels = {
        slots: _t('sidebar.slots'), blackjack: _t('sidebar.blackjack'),
        roulette: _t('sidebar.roulette'), crash: _t('sidebar.crash_games'),
        show: _t('sidebar.game_shows'), live: _t('sidebar.live_games')
      };
      secTitle.textContent = tagLabels[tag] || tag;
    }
    closeDD();
  }

  function openSmartSearch() {
    modal('smart-search');
  }
  function openDailySpin() {
    toast(_t('home.daily_spin_toast'),'inf');
  }  function openLuckyTicket() {
    if (!S.user) { modal('auth'); return; }
    if (S.user.ticket_used) { toast(_t('home.lucky_ticket_used'),'err'); return; }
    _openGiftOverlay();
  }

  // ── Gift ticket overlay (inline iframe, no redirect) ─────────────────────
  function _openGiftOverlay() {
    if (el('gift-overlay-frame')) return;
    const wrapper = document.createElement('div');
    wrapper.id = 'gift-overlay-frame';
    wrapper.style.cssText = 'position:fixed;inset:0;z-index:8000;pointer-events:all;';
    // Close button — always visible as escape hatch
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'position:absolute;top:14px;right:16px;z-index:1;'
      + 'background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);'
      + 'color:#fff;width:34px;height:34px;border-radius:50%;font-size:16px;'
      + 'cursor:pointer;display:flex;align-items:center;justify-content:center;';
    closeBtn.onclick = closeGiftTicket;
    const iframe = document.createElement('iframe');
    iframe.src = '/game/lottery_ticket';
    iframe.style.cssText = 'width:100%;height:100%;border:none;background:transparent;';
    iframe.setAttribute('allowtransparency','true');
    wrapper.appendChild(closeBtn);
    wrapper.appendChild(iframe);
    document.body.appendChild(wrapper);
    _giftFrameGuard();
  }

  // Слушаем postMessage от iframe с билетом
  window.addEventListener('message', function(e) {
    if (e.data === 'velox:gift:close') {
      closeGiftTicket();
    }
  });

  function closeGiftTicket() {
    const frame = el('gift-overlay-frame');
    if (frame) {
      frame.style.transition = 'opacity 0.5s ease';
      frame.style.opacity = '0';
      frame.style.pointerEvents = 'none'; // немедленно снимаем блокировку кликов
      setTimeout(() => { if (frame.parentNode) frame.remove(); }, 550);
    }
    checkMe();
  }

  // Страховка: если closeGiftTicket не вызвался — удаляем iframe через 35 сек
  function _giftFrameGuard() {
    setTimeout(() => {
      const frame = el('gift-overlay-frame');
      if (frame) {
        frame.style.pointerEvents = 'none';
        frame.style.opacity = '0';
        setTimeout(() => { if (frame.parentNode) frame.remove(); }, 300);
      }
    }, 30000);
  }
  function smartSearch() {
    const q = (el('smart-search-inp')?.value||'').toLowerCase().trim();
    if (!q) { renderGrid('smart-grid', []); return; }
    const res = S.games.filter(g =>
      _gname(g).toLowerCase().includes(q) ||
      (g.tags||[]).some(t=>t.includes(q)) ||
      (g.description||'').toLowerCase().includes(q) ||
      (g.provider||'').toLowerCase().includes(q) ||
      (g.provider_tag||'').toLowerCase().includes(q)
    );
    renderGrid('smart-grid', res);
    txt('smart-count', res.length + ' ' + _t('home.search_count_suffix'));
  }

  // ── USER ─────────────────────────────────────────────────────────────────────
  function _renderAvatar(u) {
    const letter = (u.username||'?')[0].toUpperCase();
    ['tb-av','dd-av','set-hero-av'].forEach(id => {
      const e = el(id); if (!e) return;
      if (u.icon_url) {
        e.innerHTML = `<img src="${u.icon_url}" draggable="true" style="width:100%;height:100%;object-fit:cover;border-radius:inherit;display:block" onerror="this.parentNode.textContent='${letter}'">`;
        e.style.padding = '0';
      } else {
        e.textContent = letter;
        e.style.padding = '';
      }
    });
  }

  function setUser(u) {
    S.user=u;
    try { localStorage.setItem('velox_authed', '1'); } catch(e) {}
    const ge2 = el('tb-guest');
    if (ge2) { ge2.style.visibility = ''; ge2.style.opacity = ''; }
    el('tb-guest').classList.add('hidden');
    const ae=el('tb-auth'); ae.classList.remove('hidden'); ae.style.display='flex';

    // Валюта юзера уже правильная с сервера — просто показываем
    const sym = csym(u.currency);
    txt('tb-bal-num', u.balance.toFixed(2)); txt('tb-bal-sym', sym);
    _renderAvatar(u);
    txt('tb-uname',u.username); txt('dd-name',u.username);
    txt('dd-sub',u.email||u.phone||'');
    txt('set-uname',u.username); txt('set-meta',u.email||u.phone||'');
    const unameInp=el('set-uname-inp'); if(unameInp) unameInp.value=u.username;
    txt('set-email',u.email||'—'); txt('set-phone',u.phone||'—');
    const phoneInp = el('set-phone-new'); if (phoneInp) phoneInp.value = u.phone || '';
    const emailInp = el('set-email-new'); if (emailInp) emailInp.value = '';
    const emailCodeInp = el('set-email-code'); if (emailCodeInp) emailCodeInp.value = '';
    const emailHint = el('set-email-hint'); if (emailHint) emailHint.textContent = '';
    profileContactInputChanged();

    // KYC / verification — hide entirely if disabled in site.json
    const kycOn = S.cfg?.kyc_enabled !== false;
    const ve=el('set-ver');
    if(ve){
      if(!kycOn){ ve.style.display='none'; }
      else { ve.style.display=''; ve.textContent=u.verified?_t('settings.verified'):_t('settings.not_verified'); ve.className=u.verified?'badge-ok':'badge-pend'; }
    }
    // Stat card (Верификация в шапке профиля) — try by id, fallback by set-stat-ver parent
    const verStatEl = el('set-stat-ver');
    const verCard = el('set-stat-ver-card') || (verStatEl ? verStatEl.closest('.set-stat') : null);
    if(verCard) verCard.style.display = kycOn ? '' : 'none';
    if(verStatEl && kycOn){verStatEl.textContent=u.verified?'✓ '+_t('settings.verified'):_t('settings.not_verified');verStatEl.style.color=u.verified?'var(--green)':'var(--gold)';}

    const tfaTog=el('tfa-tog'); if(tfaTog) tfaTog.checked=u.two_fa;
    txt('bal-big-num',u.balance.toFixed(2)); txt('bal-big-sym',sym);
    txt('set-stat-bal', u.balance.toFixed(2)+' '+sym);
    const s2=el('set-stat-2fa');
    if(s2){s2.textContent=u.two_fa?'✓ On':'Off';s2.style.color=u.two_fa?'var(--green)':'var(--t2)';}
    txt('set-stat-date', fmtDate(u.created_at));
    const fsChip = el('tb-fs');
    if (fsChip) {
      const fs = u.free_spins || 0;
      if (fs > 0) { txt('tb-fs-num', fs); fsChip.style.display = 'flex'; }
      else { fsChip.style.display = 'none'; }
    }
    const sf=el('sb-fav'); if(sf) sf.style.display='';
    const sbLottery = el('sb-lottery');
    if (sbLottery) sbLottery.style.display = u.ticket_used ? 'none' : '';
    _vipUpdateProfileBadge();
    // ── Feature flags from site.json ─────────────────────────────────────
    _applyFeatureFlags();
  }
  function clearUser() {
    S.user=null;
    try { localStorage.removeItem('velox_authed'); } catch(e) {}
    el('tb-guest').classList.remove('hidden'); el('tb-auth').classList.add('hidden');
    const sf=el('sb-fav'); if(sf) sf.style.display='none';
  }

  function _applyFeatureFlags() {
    const kycEnabled = S.cfg?.kyc_enabled !== false;
    // KYC row in security settings
    let kycRow = el('set-kyc-row');
    if (!kycRow) {
      const nameEl = el('set-doc-ver-name');
      if (nameEl) kycRow = nameEl.closest('.set-feature-row');
    }
    if (kycRow) {
      kycRow.style.display = kycEnabled ? '' : 'none';
      let next = kycRow.nextElementSibling;
      if (next && (next.classList.contains('set-sep') || next.tagName === 'HR')) {
        next.style.display = kycEnabled ? '' : 'none';
      }
    }
    // Stat card in profile header — try id, fallback to set-stat-ver parent
    const verStatEl = el('set-stat-ver');
    const verCard = el('set-stat-ver-card') || (verStatEl ? verStatEl.closest('.set-stat') : null);
    if (verCard) verCard.style.display = kycEnabled ? '' : 'none';
  }
  function csym(c){return{UAH:'₴',RUB:'₽',USD:'$',EUR:'€',USDT:'₮'}[c]||'₴'}
  function fmtDate(s){const loc={RU:'ru-RU',UA:'uk-UA',US:'en-US'}[_lang]||'ru-RU';return s?new Date(s).toLocaleDateString(loc):'—'}
  function validEmail(e){return/^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$/.test(e.trim())}

  // ── NAVIGATION ───────────────────────────────────────────────────────────────
  function go(view, opts = {}) {
    const persist = opts.persist !== false;
    if (!_hasViewNode(view)) return;
    if(['favorites','balance','settings'].includes(view)&&!S.user){modal('auth');return;}
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    el('view-'+view)?.classList.add('active');
    S.view=view; S.activeTag=null;
    if (persist) _persistView(view);
    document.querySelectorAll('.sb-item,.sb-item-child').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
    closeDD();
    if(view==='favorites') renderFavs();
    if(view==='raffles')   loadRaffles();
    if(view==='promotions') loadPromos();
    if(view==='tournaments') loadTournamentLeaderboard();
    if(view==='settings')  { loadFAQ(); _applyFeatureFlags(); }
    if(view==='sport') {
      initMiniBanners();
      loadSportCfg().then(() => {
        renderBukmakingPage();
        buildSportSection();
        buildProvidersRow();
      });
    }
    if(view==='games') {
      renderGrid('all-grid', S.games);
      const t=el('all-sec-title'); if(t) t.textContent=_t('nav.games');
    }
    if(view==='balance'&&S.user){txt('bal-big-num',S.user.balance.toFixed(2));txt('bal-big-sym',csym(S.user.currency));const sym=csym(S.user.currency);const symEl=el('pay-cur-sym');if(symEl)symEl.textContent=sym; S.payConfig=null; _payInit();}
  }

  // ── GAMES GRID ───────────────────────────────────────────────────────────────
  function renderGrid(cid, games) {
    const c=el(cid); if(!c) return;
    if (!games.length) { c.innerHTML=`<div class="empty"><p>${_t('home.games_not_found')}</p></div>`; return; }
    c.innerHTML = games.map(g => {
      const gn = _gname(g);
      const t = S.currentTournament || null;
      const tIds = Array.isArray(t?.game_ids) ? t.game_ids.map(x=>String(x)) : [];
      const hasSeason = !!(t && !t.is_finished && (!tIds.length || tIds.includes(String(g.id))));
      const seasonLive = !!(hasSeason && t.has_started);
      const seasonSoon = !!(hasSeason && !t.has_started);
      const glowColor = hasSeason ? String(t.glow_color || '#f5c451') : '';
      const glowStyle = hasSeason ? ` style="--tour-glow:${glowColor}"` : '';
      const seasonClass = seasonLive ? 'g-card-tour g-card-tour-live' : (seasonSoon ? 'g-card-tour g-card-tour-soon' : '');
      const face = g.logo
        ? `<img class="g-logo" src="${g.logo}" alt="${gn}" onload="this.style.opacity=1" onerror="this.style.display='none';this.nextSibling.style.display='block'" style="opacity:0;transition:opacity .3s"><span class="g-ico" style="display:none">${g.icon||'🎮'}</span>`
        : `<span class="g-ico">${g.icon||'🎮'}</span>`;
      return `<div class="g-card ${!g.active?'g-inactive':''} ${seasonClass}"${glowStyle} onclick="V.openGame('${g.id}')">
        <div class="g-face" style="${g.bg_gradient?'background:'+g.bg_gradient:''}">${face}${!g.active?`<div class="g-soon">${_t('home.soon')}</div>`:''}</div>
        <div class="g-badges">${seasonLive?`<span class="badge badge-tour">${_t('tournaments.badge_live')||'SEASON LIVE'}</span>`:''}${seasonSoon?`<span class="badge badge-tour-soon">${_t('tournaments.badge_soon')||'SEASON SOON'}</span>`:''}${g.hot?'<span class="badge badge-hot">🔥</span>':''}${g.new?'<span class="badge badge-new">NEW</span>':''}</div>
        <div class="g-foot"><div class="g-name">${gn}</div><div class="g-mult">${g.multiplier_display||''}</div></div>
      </div>`;
    }).join('');
  }
  function _gamesByActiveTag(){
    if(!S.activeTag) return S.games;
    const tag = String(S.activeTag);
    return S.games.filter(g => (g.tags||[]).includes(tag) || g.category===tag || g.provider_tag===tag || (g.provider||'').toLowerCase()===tag.toLowerCase());
  }
  function _rerenderVisibleGames(){
    if (S.view === 'home') _renderLobbyGames(false);
    if (S.view === 'games') renderGrid('all-grid', _gamesByActiveTag());
    if (S.view === 'favorites' && S.user) renderFavs();
  }

  // ── GAME MODAL — ИСПРАВЛЕННОЕ ─────────────────────────────────────────────────
  function openGame(id) {
    const g=S.games.find(x=>x.id===id); if(!g) return;
    S._curGame=g;
    const demo=el('gm-demo');
    demo.style.background = g.bg_gradient||'var(--bg3)';
    demo.innerHTML = '';

    if (g.demo && g.demo.trim()) {
      const src = g.demo.trim();
      const ext = src.split('.').pop().split('?')[0].toLowerCase();

      if (ext==='mp4'||ext==='webm') {
        const vid = document.createElement('video');
        vid.src=src; vid.autoplay=true; vid.loop=true; vid.muted=true; vid.playsInline=true;
        vid.style.cssText='width:100%;height:100%;object-fit:cover;border-radius:inherit';
        vid.onerror=()=>{ demo.innerHTML=`<span class="gm-ico-fb">${g.icon||'🎮'}</span>`; };
        demo.appendChild(vid);

      } else if (ext==='gif'||ext==='png'||ext==='jpg'||ext==='jpeg'||ext==='webp'||ext==='avif') {
        const img = document.createElement('img');
        img.src=src; img.alt=_gname(g);
        img.style.cssText='width:100%;height:100%;object-fit:cover;object-position:center;border-radius:inherit;display:block;opacity:0;transition:opacity .3s;image-rendering:-webkit-optimize-contrast;transform:translateZ(0)';
        img.onload  = ()=>{ img.style.opacity='1'; };
        img.onerror = ()=>{ demo.innerHTML=`<span class="gm-ico-fb">${g.icon||'🎮'}</span>`; };
        demo.appendChild(img);

      } else {
        // неизвестное расширение — пробуем как картинку
        const img = document.createElement('img');
        img.src=src; img.alt=_gname(g);
        img.style.cssText='width:100%;height:100%;object-fit:cover;border-radius:inherit;opacity:0;transition:opacity .3s';
        img.onload  = ()=>{ img.style.opacity='1'; };
        img.onerror = ()=>{ demo.innerHTML=`<span class="gm-ico-fb">${g.icon||'🎮'}</span>`; };
        demo.appendChild(img);
      }
    } else {
      demo.innerHTML=`<span class="gm-ico-fb">${g.icon||'🎮'}</span>`;
    }

    txt('gm-name',_gname(g));
    txt('gm-desc',_gdesc(g));
    txt('gm-mult',g.multiplier_display||'');
    txt('gm-bet',`${g.min_bet||0} ${csym(S.user?.currency||'UAH')}`);

    // Теги
    const tagsHtml = (g.tags||[]).map(t=>`<span class="g-tag">${t}</span>`).join('');
    const tagsEl = el('gm-tags'); if(tagsEl) tagsEl.innerHTML = tagsHtml;

    const pb=el('gm-play-btn');
    pb.disabled=!g.active; pb.textContent=g.active?'▶ '+_t('modals.game_play'):'🔒 '+_t('home.soon');
    el('gm-badges').innerHTML=[
      g.hot?`<span class="badge badge-hot">🔥 ${_t('home.hot')}</span>`:'',
      g.new?'<span class="badge badge-new">NEW</span>':'',
      !g.active?`<span class="badge" style="background:var(--bg4);color:var(--t2)">🔒 ${_t('home.soon')}</span>`:'',
      g.provider?`<span class="badge" style="background:rgba(168,85,247,.18);color:#c084fc;border:1px solid rgba(168,85,247,.3);cursor:pointer" onclick="event.stopPropagation();V.closeM();V.filterByTag('${g.provider_tag||g.provider}')">⚡ ${g.provider}</span>`:'',
    ].join('');
    modal('game');
  }

  function playGame(){
    if(!S.user){closeM();modal('auth');return;}
    if(S._curGame?.route){
      try{sessionStorage.setItem('velox_from_game','1');}catch{}
      window.location.href=S._curGame.route;
    }
  }

  // ── FAVORITES ────────────────────────────────────────────────────────────────
  async function renderFavs(){
    if(!S.user) return;
    const gs=S.games.filter(g=>(S.user.favorite_games||[]).includes(g.id));
    el('fav-empty').classList.toggle('hidden',gs.length>0);
    renderGrid('fav-grid',gs);
  }
  function favSearch(){ const q=el('fav-search').value.toLowerCase(); el('fav-list-modal')?.querySelectorAll('.fav-item').forEach(i=>{i.style.display=i.dataset.n.includes(q)?'':'none';}); }
  function buildFavModal(){
    S.favSel=new Set(S.user?.favorite_games||[]);
    el('fav-list-modal').innerHTML=S.games.map(g=>`<div class="fav-item ${S.favSel.has(g.id)?'sel':''}" data-id="${g.id}" data-n="${_gname(g).toLowerCase()}" onclick="V.togFav('${g.id}')"><span style="font-size:18px">${g.icon||'🎮'}</span><span>${_gname(g)}</span></div>`).join('');
    updateFavCount();
  }
  function togFav(id){S.favSel.has(id)?S.favSel.delete(id):S.favSel.add(id);el('fav-list-modal')?.querySelector(`[data-id="${id}"]`)?.classList.toggle('sel',S.favSel.has(id));updateFavCount();}
  function updateFavCount(){txt('fav-cnt',S.favSel.size);el('fav-sel').innerHTML=[...S.favSel].map(id=>{const g=S.games.find(x=>x.id===id);return g?`<div class="fav-sel-item">${g.icon} ${_gname(g)}`:''}).join('');}
  async function saveFavs(){if(!S.user)return;try{await api('/api/favorites','POST',{favorites:[...S.favSel]});S.user.favorite_games=[...S.favSel];closeM();renderFavs();toast(_t('toasts.save_success'),'ok');}catch{toast(_t('toasts.error_generic'),'err');}}

  // ── RAFFLES / PROMOS ─────────────────────────────────────────────────────────
  function campaignImgStyle(item, fallback){
    const src=(item?.image_url||item?.image||'').trim();
    if(!src) return fallback;
    const safe=src.replace(/"/g,'%22');
    return `background:url("${safe}") center/cover no-repeat`;
  }
  function campaignIconHtml(item, emoji='🎁'){
    const src=((item?.image_url||item?.image||'')+'').trim();
    if(!src) return `<span>${emoji}</span>`;
    const safe=src.replace(/"/g,'%22');
    return `<img src="${safe}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit" onerror="this.remove();this.parentElement.innerHTML='${emoji}'">`;
  }
  function renderCampaignRichText(raw){
    const safe=String(raw||'')
      .replace(/&/g,'&amp;')
      .replace(/</g,'&lt;')
      .replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;')
      .replace(/'/g,'&#39;');
    return safe
      .replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>')
      .replace(/\*(.+?)\*/g,'<em>$1</em>')
      .replace(/__(.+?)__/g,'<u>$1</u>')
      .replace(/~~(.+?)~~/g,'<s>$1</s>')
      .replace(/\n/g,'<br>');
  }
  function campaignGameIds(item){
    const arr=Array.isArray(item?.game_links)?item.game_links:[];
    if(item?.game_id && !arr.includes(item.game_id)) arr.unshift(item.game_id);
    return [...new Set(arr)];
  }
  async function loadRaffles(){try{const r=await api('/api/config/raffles');S.raffles=r.raffles||[];el('raffles-feed').innerHTML=S.raffles.map(rf=>`<div class="feed-card" onclick="V.openRaffle(${rf.id})"><div class="feed-icon" style="background:linear-gradient(135deg,#180530,#051530)">${campaignIconHtml(rf,'🎁')}</div><div class="feed-body"><div class="feed-title">${rf.title}</div><div class="feed-desc">${rf.short_desc}</div><div class="feed-date">📅 ${_t('raffles.from')} ${rf.start_date} ${_t('raffles.to')} ${rf.end_date}</div></div><div class="feed-arr">›</div></div>`).join('');}catch{}}
  function openRaffle(id){const rf=S.raffles.find(x=>x.id===id);if(!rf)return;const img=(rf.image_url||rf.image||'').trim();el('raffle-banner').innerHTML=img?`<img src="${img.replace(/"/g,'%22')}" style="width:100%;height:100%;object-fit:cover;border-radius:inherit" onerror="this.remove();this.parentElement.innerHTML='<span style=&quot;font-size:40px&quot;>🎁</span>'">`:'<span style="font-size:40px">🎁</span>';el('raffle-banner').style.background='linear-gradient(135deg,#180530,#051530)';txt('raffle-title',rf.title);el('raffle-desc').innerHTML=renderCampaignRichText(rf.description||'');const gId=campaignGameIds(rf)[0];const g=S.games.find(x=>String(x.id)===String(gId));el('raffle-soc').innerHTML=g?`<button class="btn-p" style="width:100%" onclick="V.openCampaignGame('${String(g.id).replace(/'/g,`\\'`)}')">${_t('promotions.play_now')||'Играть'}</button>`:socialBtns({telegram:rf.telegram,youtube:rf.youtube,viber:rf.viber});modal('raffle');}
  async function refreshTournamentContext(updateTournamentView = true){
    const prevKey = S.currentTournamentKey || '';
    try{
      const r = await api(`/api/tournaments/leaderboard?period=${encodeURIComponent(S.tournamentPeriod || 'week')}&limit=20`);
      const tour = r.tournament || {};
      S.currentTournament = (tour && tour.id) ? tour : null;
    }catch{
      S.currentTournament = null;
    }
    const t = S.currentTournament || {};
    const nextKey = S.currentTournament
      ? [t.id, t.has_started ? 1 : 0, t.is_finished ? 1 : 0, String(t.glow_color||''), (Array.isArray(t.game_ids)?t.game_ids.join('|'):'')].join('::')
      : 'none';
    S.currentTournamentKey = nextKey;
    if (nextKey !== prevKey) _rerenderVisibleGames();
    if (updateTournamentView && S.view === 'tournaments') await loadTournamentLeaderboard(S.tournamentPeriod || 'week');
  }
  function _tourSvg(kind){
    if (kind === 'trophy') return `<svg class="tv2-i tv2-i-trophy" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 4h10v3a5 5 0 0 1-5 5h0a5 5 0 0 1-5-5V4z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 20h6M12 12v8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><path d="M7 6H4a2 2 0 0 0 0 4h3M17 6h3a2 2 0 0 1 0 4h-3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
    if (kind === 'calendar') return `<svg class="tv2-i tv2-i-calendar" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="3.5" y="5.5" width="17" height="15" rx="3" stroke="currentColor" stroke-width="1.7"/><path d="M7.5 3.5v4M16.5 3.5v4M3.5 10.5h17" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>`;
    if (kind === 'gamepad') return `<svg class="tv2-i tv2-i-gamepad" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="8" width="16" height="9" rx="4.5" stroke="currentColor" stroke-width="1.8"/><path d="M8 12h4M10 10v4M15.2 11.2h.01M17.2 13.2h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
    if (kind === 'medal') return `<svg class="tv2-i tv2-i-medal" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="14.5" r="4.5" stroke="currentColor" stroke-width="1.8"/><path d="M9 3h6l-1.5 5h-3L9 3z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
    if (kind === 'warning') return `<svg class="tv2-i tv2-i-warning" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4l8 14H4L12 4z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M12 9.5v4M12 16.5h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
    if (kind === 'rank1') return `<svg class="tv2-i tv2-i-rank rank-1" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 4l1.9 3.9 4.3.6-3.1 3 .8 4.3-3.9-2.1-3.9 2.1.8-4.3-3.1-3 4.3-.6L12 4z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>`;
    if (kind === 'rank2') return `<svg class="tv2-i tv2-i-rank rank-2" viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="6.5" stroke="currentColor" stroke-width="1.8"/><path d="M9.5 16.5L8 20M14.5 16.5l1.5 3.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
    if (kind === 'rank3') return `<svg class="tv2-i tv2-i-rank rank-3" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M8 4h8v5a4 4 0 0 1-4 4h0a4 4 0 0 1-4-4V4z" stroke="currentColor" stroke-width="1.8"/><path d="M10 20h4M12 13v7" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`;
    return '';
  }
  async function loadTournamentLeaderboard(period='week'){
    S.tournamentPeriod = period || S.tournamentPeriod || 'week';
    const feed = el('tournaments-feed');
    const meta = el('tournaments-meta');
    const highWinsEl = el('tournaments-highwins');
    const allTimeEl = el('tournaments-alltime');
    try{
      const r = await api(`/api/tournaments/leaderboard?period=${encodeURIComponent(S.tournamentPeriod)}&limit=10`);
      S.tournaments = Array.isArray(r.leaders) ? r.leaders.slice(0,10) : [];
      const highWins = Array.isArray(r.high_wins) ? r.high_wins : [];
      const allTime = Array.isArray(r.all_time_big_winners) ? r.all_time_big_winners : [];
      const tour = r.tournament || {};
      S.currentTournament = (tour && tour.id) ? tour : null;
      // ── season meta card ────────────────────────────────────────────────────
      if (meta) {
        const fmtDt = (v)=>{
          if(!v) return '—';
          try{return new Date(v).toLocaleString();}catch{return String(v);}
        };
        if (!tour || !tour.id) {
          meta.style.display = '';
          meta.innerHTML = `<div class="tv2-no-season">
            <div class="tv2-no-season-ico">${_tourSvg('trophy')}</div>
            <div class="tv2-no-season-title">${_t('tournaments.no_season')||'No active or upcoming season'}</div>
            <div class="tv2-no-season-sub">${_t('tournaments.season_waiting_hint')||'Season card will auto-update when the next tournament starts.'}</div>
          </div>`;
        } else {
          const title = tour.title || (_t('tournaments.title') || 'Tournament');
          const pool = Number(tour.prize_pool || 0).toFixed(2);
          const glow = tour.glow_color || '#f5c451';
          let stateClass = 'tv2-sc-state-upcoming', stateLabel = '';
          if (tour.is_finished) {
            stateClass = 'tv2-sc-state-done';
            stateLabel = _t('tournaments.state_finished') || 'Finished';
          } else if (tour.has_started) {
            stateClass = 'tv2-sc-state-live';
            stateLabel = `${_t('tournaments.state_active')||'LIVE'}`;
          } else {
            stateLabel = `${_t('tournaments.starts_in')||'Starts in'} ${Math.max(0, Number(tour.seconds_to_start||0))}s`;
          }
          const gameChips = Array.isArray(tour.game_ids) && tour.game_ids.length
            ? tour.game_ids.slice(0,10).map(gid=>`<span class="tv2-chip">${gid}</span>`).join('')
            : `<span class="tv2-chip">${_t('tournaments.all_games')||'All games'}</span>`;
          const participantsEmptyRaw = _t('tournaments.participants_empty');
          const participantsEmpty = (!participantsEmptyRaw || participantsEmptyRaw === 'tournaments.participants_empty') ? 'No season participants yet' : participantsEmptyRaw;
          const miniLeaders = S.tournaments.length
            ? S.tournaments.slice(0,5).map((u,i)=>{
                const av = u.icon_url ? `<img src="${u.icon_url}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">` : `<span>${String(u.username||'?')[0].toUpperCase()}</span>`;
                const rankLabel = i===0?_tourSvg('rank1'):i===1?_tourSvg('rank2'):i===2?_tourSvg('rank3'):`#${i+1}`;
                return `<div class="tv2-sc-mini-row ${i===0?'is-top':''}">
                  <span class="tv2-sc-mini-rank">${rankLabel}</span>
                  <span class="tv2-sc-mini-av">${av}</span>
                  <span class="tv2-sc-mini-name">${u.username||'—'}</span>
                  <span class="tv2-sc-mini-win">${Number(u.total_win||0).toFixed(2)}</span>
                </div>`;
              }).join('')
            : `<div class="tv2-empty-sm">${participantsEmpty}</div>`;
          meta.style.display = '';
          meta.innerHTML = `
            <div class="tv2-sc-glow" style="--sc-glow:${glow}"></div>
            <div class="tv2-sc-top" onclick="V.openTournamentEvent()" style="cursor:pointer">
              <div class="tv2-sc-left">
                <div class="tv2-sc-badge ${stateClass}">${stateLabel}</div>
                <div class="tv2-sc-name">${title}</div>
                <div class="tv2-sc-pool-label">${_t('tournaments.prize_pool')||'Prize pool'}</div>
                <div class="tv2-sc-pool" style="color:${glow}">${pool}</div>
                <div class="tv2-sc-dates">
                  <span>${_tourSvg('calendar')} ${fmtDt(tour.starts_at)}</span>
                  <span class="tv2-sc-dates-sep">→</span>
                  <span>${fmtDt(tour.ends_at)}</span>
                </div>
              </div>
              <div class="tv2-sc-right">
                <div class="tv2-sc-mini-head">${_t('tournaments.participants_title')||'Top participants'}</div>
                <div class="tv2-sc-mini-list">${miniLeaders}</div>
              </div>
            </div>
            <div class="tv2-sc-games-row">
              <span class="tv2-sc-games-label">${_tourSvg('gamepad')} ${_t('tournaments.all_games')||'Games'}:</span>
              <div class="tv2-sc-games">${gameChips}</div>
            </div>`;
        }
      }
      // ── main leaderboard ─────────────────────────────────────────────────────
      if (feed && !S.tournaments.length) {
        feed.innerHTML = `<div class="tv2-empty">
          <div class="tv2-empty-ico">${_tourSvg('medal')}</div>
          <div class="tv2-empty-title">${_t('tournaments.leaders_title')||'Leaders by total winnings'}</div>
          <div class="tv2-empty-sub">${_t('tournaments.empty')||'No leaderboard data yet'}</div>
        </div>`;
      } else if (feed) {
        feed.innerHTML = S.tournaments.map((u,i)=>{
          const rankNum = u.rank || (i+1);
          const medal = rankNum===1?_tourSvg('rank1'):rankNum===2?_tourSvg('rank2'):rankNum===3?_tourSvg('rank3'):null;
          const av = u.icon_url ? `<img src="${u.icon_url}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">` : `<span>${String(u.username||'?')[0].toUpperCase()}</span>`;
          const isTop = rankNum <= 3;
          return `<div class="tv2-leader-row ${isTop?'tv2-leader-top':''}">
            <div class="tv2-leader-rank ${rankNum===1?'rank-1':rankNum===2?'rank-2':rankNum===3?'rank-3':''}">${medal||('#'+rankNum)}</div>
            <div class="tv2-leader-av">${av}</div>
            <div class="tv2-leader-info">
              <div class="tv2-leader-name">${u.username||'—'}</div>
              <div class="tv2-leader-stats">
                <span>${_t('tournaments.sum_bet')||'Bets'}: ${Number(u.total_bet||0).toFixed(2)}</span>
                <span class="tv2-dot">·</span>
                <span>${_t('tournaments.rounds')||'Rounds'}: ${u.rounds||0}</span>
              </div>
            </div>
            <div class="tv2-leader-win">
              <div class="tv2-leader-win-amt">${Number(u.total_win||0).toFixed(2)}</div>
              <div class="tv2-leader-win-lbl">${_t('tournaments.sum_win')||'Winnings'}</div>
            </div>
          </div>`;
        }).join('');
      }
      // ── high wins ────────────────────────────────────────────────────────────
      const _renderWinRow = (w, i, type)=>{
        const av = w.icon_url ? `<img src="${w.icon_url}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">` : `<span>${String(w.username||'?')[0].toUpperCase()}</span>`;
        const byRtp = w.source === 'rtp_fallback';
        const winAmt = type==='alltime' ? Number(w.max_win||0).toFixed(2) : Number(w.win||0).toFixed(2);
        const winLbl = type==='alltime' ? (_t('tournaments.all_time_best_win')||'Best win') : (_t('tournaments.win_amount')||'Win');
        const sub = byRtp ? `${_t('tournaments.rtp_label')||'RTP'}: ${((Number(w.win_rtp)||0)*100).toFixed(2)}%` : (type==='alltime' ? `${_t('tournaments.big_wins_count')||'Big wins'}: ${Number(w.wins_count||0)}` : `${_t('tournaments.game')||'Game'}: ${w.game_id||'—'}`);
        return `<div class="tv2-win-row ${i<3?'tv2-win-top':''}">
          <div class="tv2-win-rank">#${i+1}</div>
          <div class="tv2-win-av">${av}</div>
          <div class="tv2-win-info">
            <div class="tv2-win-name">${w.username||'—'}</div>
            <div class="tv2-win-sub">${sub}</div>
          </div>
          <div class="tv2-win-amt">${winAmt}</div>
        </div>`;
      };
      if (highWinsEl) {
        if (!highWins.length) {
          highWinsEl.innerHTML = `<div class="tv2-empty-sm">${_t('tournaments.high_wins_empty')||'No wins yet'}</div>`;
        } else {
          highWinsEl.innerHTML = highWins.slice(0,200).map((w,i)=>_renderWinRow(w,i,'highwins')).join('');
        }
      }
      if (allTimeEl) {
        if (!allTime.length) {
          allTimeEl.innerHTML = `<div class="tv2-empty-sm">${_t('tournaments.high_wins_empty')||'No wins yet'}</div>`;
        } else {
          allTimeEl.innerHTML = allTime.slice(0,200).map((w,i)=>_renderWinRow(w,i,'alltime')).join('');
        }
      }
      _rerenderVisibleGames();
    }catch{
      if (meta) meta.style.display = 'none';
      S.currentTournament = null;
      if (feed) feed.innerHTML = `<div class="tv2-empty"><div class="tv2-empty-ico">${_tourSvg('warning')}</div><div class="tv2-empty-sub">${_t('validation.connection_error')||'Connection error'}</div></div>`;
      if (highWinsEl) highWinsEl.innerHTML = `<div class="tv2-empty-sm">${_t('validation.connection_error')||'Connection error'}</div>`;
      if (allTimeEl) allTimeEl.innerHTML = `<div class="tv2-empty-sm">${_t('validation.connection_error')||'Connection error'}</div>`;
    }
  }
  function setTournamentPeriod(period, btn){
    const tabs = el('tournaments-tabs');
    if (tabs) tabs.querySelectorAll('.tv2-tab').forEach(b=>b.classList.remove('on'));
    if (btn) btn.classList.add('on');
    loadTournamentLeaderboard(period);
  }

  function _resolveTournamentGame(tour) {
    if (!tour) return null;
    const ids = Array.isArray(tour.game_ids) ? tour.game_ids.map(x=>String(x)) : [];
    if (ids.length) {
      const byId = S.games.find(g => ids.includes(String(g.id)));
      if (byId) return byId;
    }
    return S.games.find(g => String(g.id).toLowerCase().includes('mineslot')) || null;
  }

  function openTournamentEvent() {
    const tour = S.currentTournament || null;
    if (!tour || !tour.id) return;
    const game = _resolveTournamentGame(tour);
    const title = tour.title || (_t('tournaments.title') || 'Tournament');
    const desc = (tour.description || '').trim() || (_t('tournaments.event_desc_fallback') || 'Event details will appear here.');
    txt('tev-title', title);
    txt('tev-subtitle', _t('tournaments.hero_sub') || 'Compete. Win. Take the prize.');
    txt('tev-desc', desc);
    txt('tev-game-label', _t('tournaments.game') || 'Game');
    txt('tev-minbet-label', _t('tournaments.min_bet') || 'MIN BET');
    txt('tev-players-title', _t('tournaments.event_players_title') || 'Event leaderboard');
    txt('tev-open-game-btn', _t('tournaments.open_game') || 'Open game');

    const gName = game?.name || game?.id || (_t('tournaments.all_games') || 'All games');
    txt('tev-game-name', gName);
    txt('tev-name-strip', gName);

    const rtp = Number(game?.rtp || 0);
    txt('tev-rtp', rtp > 0 ? `${(rtp * 100).toFixed(2)}%` : '—');
    txt('tev-min-bet', game ? String(game.min_bet ?? '—') : '—');
    S.tournamentModalGameRoute = game?.route || '';

    const logo = el('tev-game-logo');
    const fallback = el('tev-game-logo-fallback');
    if (logo && fallback) {
      const src = (game?.logo || '').trim();
      if (src) {
        logo.style.display = 'block';
        fallback.style.display = 'none';
        logo.src = src;
        logo.onerror = () => { logo.style.display = 'none'; fallback.style.display = 'block'; };
      } else {
        logo.style.display = 'none';
        fallback.style.display = 'block';
      }
    }

    const players = el('tev-players');
    if (players) {
      if (!S.tournaments.length) {
        players.innerHTML = `<div class="tv2-empty-sm">${_t('tournaments.participants_empty') || 'No season participants yet'}</div>`;
      } else {
        players.innerHTML = S.tournaments.map((u, i) => {
          const av = u.icon_url
            ? `<img src="${u.icon_url}" alt="" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">`
            : `<span>${String(u.username||'?')[0].toUpperCase()}</span>`;
          const rank = i + 1;
          return `<div class="tev-player ${rank<=3?'top':''}">
            <div class="tev-rank">${rank===1?_tourSvg('rank1'):rank===2?_tourSvg('rank2'):rank===3?_tourSvg('rank3'):'#'+rank}</div>
            <div class="tev-av">${av}</div>
            <div class="tev-name">${u.username||'—'}</div>
            <div class="tev-win">${Number(u.total_win||0).toFixed(2)}</div>
          </div>`;
        }).join('');
      }
    }
    modal('tour-event');
  }

  function openTournamentEventGame() {
    if (!S.tournamentModalGameRoute) return;
    closeM();
    try{sessionStorage.setItem('velox_from_game','1');}catch{}
    window.location.href = S.tournamentModalGameRoute;
  }
  async function loadPromos(){try{const r=await api('/api/config/promotions');S.promos=r.promotions||[];el('promos-feed').innerHTML=S.promos.map(p=>`<div class="feed-card" onclick="V.openPromo(${p.id})"><div class="feed-icon" style="background:linear-gradient(135deg,#1a0505,#1a0a00)">${campaignIconHtml(p,'🔥')}</div><div class="feed-body"><div class="feed-title">${p.title}</div><div class="feed-desc">${p.short_desc}</div><div class="feed-date">📅 ${_t('promotions.from')} ${p.start_date} ${_t('promotions.to')} ${p.end_date}</div></div><div class="feed-arr">›</div></div>`).join('');}catch{}}
  function openPromo(id){const p=S.promos.find(x=>x.id===id);if(!p)return;txt('pd-title',p.title);el('pd-sub').innerHTML=renderCampaignRichText(p.description||'');el('pd-stats').innerHTML=`<div class="promo-s"><div class="promo-s-lbl">${_t('promotions.standard_mult')}</div><div class="promo-s-val">${p.standard_multiplier||'—'}</div></div><div class="promo-s"><div class="promo-s-lbl">${_t('promotions.promo_mult')}</div><div class="promo-s-val" style="color:var(--green)">${p.promo_multiplier||'—'}</div></div><div class="promo-s"><div class="promo-s-lbl">${_t('promotions.win_chance')}</div><div class="promo-s-val" style="color:var(--a)">${p.win_chance||'—'}</div></div>`;const g=S.games.find(x=>String(x.id)===String(campaignGameIds(p)[0]||''));if((p.image_url||p.image)){el('pd-demo').style.background='linear-gradient(135deg,#1a0505,#1a0a00)';el('pd-demo').innerHTML=`<img src="${(p.image_url||p.image).replace(/"/g,'%22')}" style="width:100%;height:100%;object-fit:cover;border-radius:inherit" onerror="this.remove()">`;}else if(g){el('pd-demo').style.background=g.bg_gradient||'var(--bg3)';el('pd-demo').innerHTML=`<span class="gm-ico-fb">${g.icon}</span>`;}el('pd-play').onclick=()=>{if(g){closeM();try{sessionStorage.setItem('velox_from_game','1');}catch{}window.location.href=g.route;}};modal('promo-det');}
  function openCampaignGame(gameId){const g=S.games.find(x=>String(x.id)===String(gameId));if(!g)return;closeM();try{sessionStorage.setItem('velox_from_game','1');}catch{}window.location.href=g.route;}

  // ── AUTH ─────────────────────────────────────────────────────────────────────
  function authTab(t){S.authType=t;el('lat-e').classList.toggle('on',t==='email');el('lat-p').classList.toggle('on',t==='phone');el('l-email-w').classList.toggle('hidden',t!=='email');el('l-phone-w').classList.toggle('hidden',t!=='phone');}
  function regTab(t){S.regType=t;el('rat-e').classList.toggle('on',t==='email');el('rat-p').classList.toggle('on',t==='phone');el('r-email-w').classList.toggle('hidden',t!=='email');el('r-phone-w').classList.toggle('hidden',t!=='phone');}
  function authForm(f){txt('l-err','');txt('r-err','');el('auth-login').classList.toggle('hidden',f!=='login');el('auth-reg').classList.toggle('hidden',f!=='reg');}

  async function login(){
    const loginVal=S.authType==='phone'?el('l-pcode').value+el('l-phone').value.trim():el('l-email').value.trim();
    const pw=el('l-pw').value, tfa=el('l-2fa').value.trim(); txt('l-err','');
    if(!loginVal){txt('l-err',_t('validation.enter_email_phone'));return;}
    if(S.authType==='email'&&!validEmail(loginVal)){txt('l-err',_t('validation.invalid_email'));return;}
    if(!pw){txt('l-err',_t('validation.enter_password'));return;}
    try{const r=await api('/api/auth/login','POST',{login:loginVal,password:pw,two_fa:tfa});
      if(r.requires_2fa){el('l-2fa-w').classList.remove('hidden');txt('l-err',_t('validation.enter_2fa'));return;}
      if(r.error){txt('l-err',r.error);return;}
      setUser(r.user);closeM();toast(_t('toasts.login_success')+' '+r.user.username+'!','ok');}
    catch{txt('l-err',_t('validation.connection_error'));}
  }

  // ── REGISTRATION — Step 1: validate → send OTP ─────────────────────────────
  async function register(){
    let email='',phone='';
    if(S.regType==='phone') phone=el('r-pcode').value+el('r-phone').value.trim();
    else email=el('r-email').value.trim();
    const uname=el('r-uname').value.trim(),pw=el('r-pw').value,cpw=el('r-cpw').value;
    txt('r-err','');
    if(!uname||uname.length<3){txt('r-err',_t('validation.username_min'));return;}
    if(!email&&!phone){txt('r-err',_t('validation.enter_email_phone'));return;}
    if(email&&!validEmail(email)){txt('r-err',_t('validation.invalid_email'));return;}
    if(!pw||pw.length<6){txt('r-err',_t('validation.password_min'));return;}
    if(pw!==cpw){txt('r-err',_t('toasts.error_password_match'));return;}
    const regBtn=el('auth-register-btn');
    if(regBtn){regBtn.disabled=true;regBtn.textContent='...';}
    try{
      const r=await api('/api/auth/register/send_otp','POST',{username:uname,email,phone,password:pw,confirm_password:cpw});
      if(r.error){txt('r-err',r.error);if(regBtn){regBtn.disabled=false;regBtn.textContent=_t('auth.register_btn');}return;}
      S.pendingReg={username:uname,email,phone,password:pw,confirm_password:cpw};
      if(r.skip_otp){
        // SMTP disabled — go straight to policy modal with text populated
        closeM();
        _openPolicyModal();
        return;
      }
      closeM();
      _evOpen(r.masked, r.via, r.ttl||60);
    }catch{txt('r-err',_t('validation.connection_error'));if(regBtn){regBtn.disabled=false;regBtn.textContent=_t('auth.register_btn');}}
  }

  // ── OTP Modal helpers ──────────────────────────────────────────────────────
  let _evTimer=null;

  function _evOpen(masked, via, ttl){
    txt('ev-err',''); txt('ev-ok','');
    const codeEl=el('ev-code'); if(codeEl) codeEl.value='';
    const titleEl=el('modal-ev-title'); if(titleEl) titleEl.textContent=_t('auth.email_verify_title');
    const subEl=el('modal-ev-sub');
    if(subEl){
      const key = via==='sms' ? 'auth.phone_verify_sub' : 'auth.email_verify_sub';
      subEl.textContent=_t(key).replace('{masked}',masked);
    }
    const resendLbl=el('ev-resend-lbl'); if(resendLbl) resendLbl.textContent=_t('auth.verify_resend');
    const verBtn=el('ev-verify-btn'); if(verBtn) verBtn.textContent=_t('auth.verify_btn');
    const resendBtn=el('ev-resend-btn'); if(resendBtn) resendBtn.disabled=true;
    _evStartTimer(ttl);
    modal('email-verify');
    setTimeout(()=>{ const c=el('ev-code'); if(c) c.focus(); },300);
  }

  function _evStartTimer(ttl){
    if(_evTimer) clearInterval(_evTimer);
    let rem=ttl;
    const bar=el('ev-timer-bar'), txt_=el('ev-timer-txt'), resendBtn=el('ev-resend-btn');
    const update=()=>{
      if(bar) bar.style.width=(rem/ttl*100)+'%';
      if(txt_) txt_.textContent=rem+'s';
      if(rem<=0){
        clearInterval(_evTimer); _evTimer=null;
        if(bar) bar.style.background='#ef4444';
        if(txt_) txt_.textContent=_t('auth.verify_code_expired');
        if(resendBtn) resendBtn.disabled=false;
        const resendLbl=el('ev-resend-lbl'); if(resendLbl) resendLbl.textContent=_t('auth.verify_resend');
      } else { rem--; }
    };
    update();
    _evTimer=setInterval(update,1000);
  }

  function evCodeInput(inp){
    inp.value=inp.value.replace(/\D/g,'').slice(0,9);
    if(inp.value.length===9) evVerify();
  }

  async function evVerify(){
    const code=(el('ev-code')?.value||'').trim();
    txt('ev-err',''); txt('ev-ok','');
    if(code.length!==9){txt('ev-err',_t('auth.verify_code_invalid'));return;}
    const btn=el('ev-verify-btn'); if(btn){btn.disabled=true;btn.textContent='...';}
    try{
      const body={...S.pendingReg, code};
      const r=await api('/api/auth/register/verify_otp','POST',body);
      if(r.error){txt('ev-err',r.error);if(btn){btn.disabled=false;btn.textContent=_t('auth.verify_btn');}return;}
      if(_evTimer){clearInterval(_evTimer);_evTimer=null;}
      txt('ev-ok',_t('auth.verify_success'));
      S.pendingUserId=r.pending_user_id;
      setTimeout(()=>{
        closeM();
        _openPolicyModal();
      },800);
    }catch{txt('ev-err',_t('validation.connection_error'));if(btn){btn.disabled=false;btn.textContent=_t('auth.verify_btn');}}
  }

  async function evResend(){
    if(!S.pendingReg) return;
    const resendBtn=el('ev-resend-btn'); if(resendBtn) resendBtn.disabled=true;
    txt('ev-err',''); txt('ev-ok','');
    try{
      const r=await api('/api/auth/register/resend_otp','POST',S.pendingReg);
      if(r.error){txt('ev-err',r.error);if(resendBtn) resendBtn.disabled=false;return;}
      const bar=el('ev-timer-bar'); if(bar){bar.style.background='';bar.style.width='100%';}
      _evStartTimer(r.ttl||60);
      const subEl=el('modal-ev-sub');
      if(subEl) subEl.textContent=_t('auth.email_verify_sub').replace('{masked}',r.masked);
    }catch{txt('ev-err',_t('validation.connection_error'));if(resendBtn) resendBtn.disabled=false;}
  }

  // ── Step 3: policy accepted → confirm & open session ──────────────────────
  function _openPolicyModal() {
    // Populate text: i18n key first, fallback to site.json, then hardcoded
    const polText = el('pol-text');
    if (polText) {
      const i18nText = _t('auth.casino_policy_text');
      const cfgText  = S.cfg?.casino_policy_text || '';
      const text     = (i18nText && i18nText !== 'auth.casino_policy_text')
                       ? i18nText : cfgText;
      polText.innerHTML = text
        ? text.replace(/\n/g, '<br>')
        : (_t('auth.policy_loading') || 'Загрузка...');
      polText.scrollTop = 0;
    }
    const polBtn  = el('pol-btn');
    const polHint = el('modal-pol-hint');
    if (polBtn)  polBtn.disabled = true;
    if (polHint) polHint.classList.remove('hidden');
    // Check if text fits without scroll — if so, enable button immediately
    requestAnimationFrame(() => {
      if (polText && polBtn && polText.scrollHeight <= polText.clientHeight + 8) {
        polBtn.disabled = false;
        if (polHint) polHint.classList.add('hidden');
      }
    });
    modal('policy');
  }

  function polScroll(box){
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 8;
    const btn=el('pol-btn'), hint=el('modal-pol-hint');
    if(atBottom){if(btn)btn.disabled=false;if(hint)hint.classList.add('hidden');}
  }
  async function polConfirm(){
    if(!S.pendingUserId){
      // skip_otp path — SMTP disabled, register directly
      if(!S.pendingReg)return;
      try{
        const r=await api('/api/auth/register/direct','POST',S.pendingReg);
        S.pendingReg=null;
        if(r.error){toast(r.error,'err');return;}
        setUser(r.user);closeM();toast(_t('toasts.register_success'),'ok');
        if(!r.user.ticket_used){setTimeout(_openGiftOverlay,800);}
      }catch{toast(_t('toasts.error_generic'),'err');}
      return;
    }
    try{
      const r=await api('/api/auth/register/confirm_policy','POST',{pending_user_id:S.pendingUserId});
      S.pendingUserId=null; S.pendingReg=null;
      if(r.error){toast(r.error,'err');return;}
      setUser(r.user); closeM(); toast(_t('toasts.register_success'),'ok');
      if(!r.user.ticket_used){setTimeout(_openGiftOverlay,800);}
    }catch{toast(_t('toasts.error_generic'),'err');}
  }
  async function logout(){try{await api('/api/auth/logout','POST');}catch{}clearUser();go('home');closeDD();toast(_t('toasts.logout_success'),'inf');}

  // ── FORGOT PASSWORD ──────────────────────────────────────────────────────────
  function openForgot(e){e?.preventDefault();S.forgotNeedsCode=false;txt('forgot-err','');txt('forgot-ok','');el('forgot-contact').value='';el('forgot-2fa').value='';el('forgot-2fa-w').classList.add('hidden');el('forgot-btn').textContent=_t('modals.forgot_send');el('forgot-btn').disabled=false;closeM();modal('forgot');}
  async function forgotPw(){
    const contact=el('forgot-contact').value.trim(),twofa=el('forgot-2fa').value.trim();
    txt('forgot-err','');txt('forgot-ok','');
    if(!contact){txt('forgot-err',_t('validation.enter_email_phone'));return;}
    if(contact.includes('@')&&!validEmail(contact)){txt('forgot-err',_t('validation.invalid_email'));return;}
    const btn=el('forgot-btn'); if(btn) btn.disabled=true;
    try{
      const body={contact};if(S.forgotNeedsCode)body.two_fa=twofa;
      const r=await api('/api/auth/forgot_password','POST',body);
      if(btn) btn.disabled=false;
      if(r.requires_2fa){
        S.forgotNeedsCode=true;
        el('forgot-2fa-w').classList.remove('hidden');
        txt('forgot-err',_t('validation.enter_2fa'));
        if(btn) btn.textContent=_t('validation.forgot_confirm_btn');
        return;
      }
      if(r.error){txt('forgot-err',r.error);return;}
      // Email reset sent
      const msg = r.method==='email'
        ? (_t('reset_password.link_sent')||'Ссылка отправлена на почту')
        : (_t('validation.ticket_created')||'Тикет создан');
      const sub = r.method==='email'
        ? `\n${_t('reset_password.link_sent_sub')||'Проверьте входящие и папку Спам'}`
        : '';
      txt('forgot-ok', msg+sub);
      if(btn) btn.disabled=true;
    }catch{
      if(btn) btn.disabled=false;
      txt('forgot-err',_t('validation.connection_error'));
    }
  }
  function closeModal(name){el('overlay').classList.remove('on');el('modal-'+name)?.classList.remove('on');modal('auth');}

  // ── PROFILE ──────────────────────────────────────────────────────────────────
  function stab(btn,pid){
    document.querySelectorAll('.stab').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');
    document.querySelectorAll('.stab-p').forEach(p=>p.classList.remove('on'));
    el(pid)?.classList.add('on');
    if(pid==='set-pol'&&S.cfg) txt('set-pol-text',S.cfg.casino_policy_text||'');
    if(pid==='set-gen') _loadCurrencyRates();
    if(pid==='set-sec') _applyFeatureFlags();
    if(pid==='set-hist') loadGameHistory();
  }

  // Конвертация суммы между валютами через сохранённые курсы S.rates
  // rates хранятся как { USD: { rate_to_uah: 41.5 }, ... }
  function convertAmount(amount, fromCur, toCur) {
    if (!amount || fromCur === toCur) return amount;
    const rates = S.rates;
    if (!Object.keys(rates).length) return amount;
    const fromRate = fromCur === 'UAH' ? 1 : (rates[fromCur]?.rate_to_uah || 1);
    const toRate   = toCur   === 'UAH' ? 1 : (rates[toCur]?.rate_to_uah   || 1);
    if (!toRate) return amount;
    return Math.round((amount * fromRate / toRate) * 100) / 100;
  }

  async function _loadCurrencyRates(){
    try{
      const r = await api('/api/currency/rates','GET');
      const rates = r.rates || {};

      // Сохраняем в S для использования в истории игр
      S.rates = rates;

      const usd  = rates.USD?.rate_to_uah;
      const eur  = rates.EUR?.rate_to_uah;
      const usdt = rates.USDT?.rate_to_uah;

      if(usd)  txt('rate-usd',  usd.toFixed(2));
      if(eur)  txt('rate-eur',  eur.toFixed(2));
      if(usdt) txt('rate-usdt', usdt.toFixed(2));

      // i18n sublabels and title — динамически от текущей валюты
      const baseSym = csym(S.user?.currency || 'UAH');
      const per     = _t('currency_ui.rates_per') || 'за';
      const usdLbl  = el('rate-usd-lbl');  if(usdLbl)  usdLbl.textContent  = `${baseSym} ${per} $1`;
      const eurLbl  = el('rate-eur-lbl');  if(eurLbl)  eurLbl.textContent  = `${baseSym} ${per} €1`;
      const usdtLbl = el('rate-usdt-lbl'); if(usdtLbl) usdtLbl.textContent = `${baseSym} ${per} ₮1`;
      const rTitle  = el('set-rates-title'); if(rTitle) rTitle.textContent = _t('currency_ui.rates_title') || 'Курсы валют';

      // Freshness badge
      const badge = el('rates-freshness-badge');
      if(badge){
        if(r.fresh){
          badge.textContent = _t('currency_ui.rates_fresh')||'НБУ ✓';
          badge.style.background='rgba(74,222,128,.15)';
          badge.style.color='#4ade80';
        } else {
          badge.textContent = _t('currency_ui.rates_cached')||'кеш';
          badge.style.background='rgba(250,204,21,.15)';
          badge.style.color='#facc15';
        }
      }

      const updEl = el('rates-updated');
      if(updEl && r.updated_at){
        const d = new Date(r.updated_at * 1000);
        const fmt = d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) +
                    ' ' + d.toLocaleDateString([], {day:'2-digit',month:'2-digit'});
        updEl.textContent = `${_t('currency_ui.rates_updated')||'Обновлено'}: ${fmt}`;
      }
    }catch{
      // silently fail
    }
  }


  // ── GAME HISTORY ─────────────────────────────────────────────────────────────

  // Server-side sport bets cache (populated in loadGameHistory)
  let _bukmServerBets = null;

  function _bukmResultBadge(rs) {
    if (rs === 'won')  return '<span style="font-size:10px;font-weight:600;color:#00c97a;margin-left:4px">✓ WIN</span>';
    if (rs === 'lost') return '<span style="font-size:10px;font-weight:600;color:#ff4560;margin-left:4px">✗ LOST</span>';
    if (rs === 'void') return '<span style="font-size:10px;font-weight:600;color:#888;margin-left:4px">↩ VOID</span>';
    return '<span style="font-size:10px;color:#888;margin-left:4px">⏳</span>';
  }

  function _renderBukmHistBet(b, openKey) {
    const sym   = csym(b.currency || 'UAH');
    const dt    = b.created_at ? new Date(b.created_at).toLocaleString() : '';
    const coef  = Number(b.coef || 1).toFixed(2);
    const picks = b.picks || [];
    const fp    = picks[0] || {};
    const home  = fp.home_team || fp.homeTeam || '?';
    const away  = fp.away_team || fp.awayTeam || '?';
    const matchLabel = picks.length > 1
      ? `${picks.length} ${_tv('sport.hist_events', _lang==='UA'?'подій':_lang==='US'?'events':'событий')}`
      : `${home} VS ${away}`;
    const titleLine = `${_tv('sport.hist_modal_title','Спорт')}\n${matchLabel} [×${coef}]`;
    const rs  = b.result_status || 'pending';
    const won = rs === 'won';
    const profitLine = won
      ? '<div class="game-hist-profit win" style="color:#00c97a">+' + sym + Number(b.win || b.potential_win || 0).toFixed(2) + '</div>'
      : '<div class="game-hist-profit" style="color:var(--gold)">→ ' + sym + Number(b.potential_win || 0).toFixed(2) + '</div>';
    const tag0 = (picks[0]?.tag || picks[0]?.market || '').toLowerCase();
    const isBball = tag0.includes('basket') || tag0.includes('nba') || tag0.includes('euroleague');
    const isEsport = tag0.includes('esport') || tag0.includes('cs') || tag0.includes('dota');
    const sportIcoHist = isBball
      ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="M4.93 4.93C6.58 6.58 8 9.2 8 12s-1.42 5.42-3.07 7.07M19.07 4.93C17.42 6.58 16 9.2 16 12s1.42 5.42 3.07 7.07M2 12h20"/></svg>`
      : isEsport
      ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="6" width="20" height="12" rx="3"/><path d="M6 10v4M8 12H4M14 11h2M14 13h2"/></svg>`
      : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10A15.3 15.3 0 0 1 12 2z"/><path d="M2 12h20"/><path d="M4.5 7h15M4.5 17h15"/></svg>`;
    return '<div class="game-hist-row game-hist-row--bukm" style="cursor:pointer" onclick="V._openBukmBetModal(' + JSON.stringify(openKey) + ')">'
      + '<div class="game-hist-icon" style="background:rgba(212,160,23,.12);color:var(--gold)">' + sportIcoHist + '</div>'
      + '<div class="game-hist-info">'
      +   '<div class="game-hist-game" style="white-space:pre-line">' + titleLine.replace(/[<>&"]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])) + _bukmResultBadge(rs) + '</div>'
      +   '<div class="game-hist-date">' + dt + '</div>'
      + '</div>'
      + '<div class="game-hist-amounts">'
      +   '<div class="game-hist-bet">' + _tv('sport.hist_stake', _lang==='UA'?'Ставка':_lang==='US'?'Bet':'Ставка') + ': ' + sym + Number(b.stake || b.amount || 0).toFixed(2) + '</div>'
      +   profitLine
      + '</div>'
      + '</div>';
  }

  function _bukmBetHistoryRows(serverBets) {
    try {
      const bets = (serverBets && serverBets.length)
        ? serverBets
        : JSON.parse(localStorage.getItem('bukm_bet_history') || '[]');
      return bets.map((b, idx) => _renderBukmHistBet(b, b.id || b.bet_id || idx));
    } catch(e) { return []; }
  }

  function _openBukmBetModal(keyOrIdx) {
    try {
      // keyOrIdx is server bet id (number/string) or localStorage index
      const serverBets = _bukmServerBets || [];
      let b = serverBets.find(x => (x.id || x.bet_id) == keyOrIdx);
      if (!b) {
        const local = JSON.parse(localStorage.getItem('bukm_bet_history') || '[]');
        b = typeof keyOrIdx === 'number' && keyOrIdx < local.length
          ? local[keyOrIdx]
          : local.find(x => (x.bet_id || x.id) == keyOrIdx);
      }
      if (!b) return;
      const sym = csym(b.currency || 'UAH');
      const dtLocal = b.created_at ? new Date(b.created_at).toLocaleString() : '';
      const dtUTC   = b.created_at
        ? new Date(b.created_at).toLocaleString('en-GB', { timeZone:'UTC', day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' }) + ' UTC'
        : '';
      const modeLabel = {
        single:  _tv('sport.coupon_single',  'Ординар'),
        express: _tv('sport.coupon_express', 'Екcпрес'),
        system:  _tv('sport.coupon_system',  'Система'),
      }[b.mode] || b.mode || '';

      // SVG иконки
      const _tag0 = (b.picks?.[0]?.tag || b.picks?.[0]?.market || '').toLowerCase();
      const _isB = _tag0.includes('basket') || _tag0.includes('nba') || _tag0.includes('euroleague');
      const _isE = _tag0.includes('esport') || _tag0.includes('cs') || _tag0.includes('dota');
      const icoSport = _isB
        ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="M4.93 4.93C6.58 6.58 8 9.2 8 12s-1.42 5.42-3.07 7.07M19.07 4.93C17.42 6.58 16 9.2 16 12s1.42 5.42 3.07 7.07M2 12h20"/></svg>`
        : _isE
        ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="6" width="20" height="12" rx="3"/><path d="M6 10v4M8 12H4M14 11h2M14 13h2"/></svg>`
        : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10A15.3 15.3 0 0 1 12 2z"/><path d="M2 12h20"/><path d="M4.5 7h15M4.5 17h15"/></svg>`;
      const icoLive   = `<svg width="8" height="8" viewBox="0 0 8 8"><circle cx="4" cy="4" r="4" fill="currentColor"/></svg>`;
      const icoClock  = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>`;
      const icoCoef   = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>`;
      const icoStake  = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/></svg>`;
      const icoWin    = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2z"/></svg>`;
      const icoCheck  = `<svg width="11" height="11" viewBox="0 0 20 20" fill="none"><path d="M3 10l5 5 9-9" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
      const icoVideo  = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>`;

      const picksHtml = (b.picks || []).map(p => {
        const home = p.home_team || p.homeTeam || '?';
        const away = p.away_team || p.awayTeam || '?';
        const startTs = p.matchStartUtc || p.match_td_utc || p.starts_at || '';
        let matchStartStr = '';
        if (startTs) {
          const d = new Date(startTs);
          const local = d.toLocaleString([], { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' });
          const utcStr = d.toLocaleString('en-GB', { timeZone:'UTC', day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' }) + ' UTC';
          matchStartStr = `${local} (${utcStr})`;
        }
        const isLive = p.isLive || p.match_status === 'live';
        const liveBadge = isLive
          ? `<span class="bukm-hm-live">${icoLive} LIVE</span>`
          : '';
        return `<div class="bukm-hm-pick">
          <div class="bukm-hm-pick-top">
            <div class="bukm-hm-pick-teams">${home} <span class="bukm-hm-vs">vs</span> ${away}</div>
            ${liveBadge}
          </div>
          <div class="bukm-hm-pick-outcome">${p.title || p.market || ''}</div>
          <div class="bukm-hm-pick-meta">
            <span class="bukm-hm-pick-coef">${icoCoef} ×${Number(p.odd||1).toFixed(2)}</span>
            ${matchStartStr ? `<span class="bukm-hm-pick-time">${icoClock} ${matchStartStr}</span>` : ''}
          </div>
        </div>`;
      }).join('');

      const anyLive = (b.picks || []).some(p => p.isLive);
      const videoUrl = _normalizeVideoEmbedUrl((b.picks || []).find(p => p.videoUrl)?.videoUrl || '');
      const videoHtml = anyLive
        ? `<div class="bukm-hm-section-title">${icoVideo} ${_tv('sport.hist_broadcast','Трансляция')}</div>` + (videoUrl
            ? `<div class="bukm-hm-video"><iframe src="${videoUrl}" allowfullscreen></iframe></div>`
            : `<div class="bukm-hm-no-video">${_tv('sport.hist_no_broadcast','Трансляция недоступна')}</div>`)
        : '';

      const innerHtml = `
        <button class="m-close" onclick="V.closeM()"><svg width="11" height="11" viewBox="0 0 12 12" fill="none"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>

        <div class="bukm-hm-header">
          <div class="bukm-hm-header-icon">${icoSport}</div>
          <div class="bukm-hm-header-text">
            <div class="bukm-hm-title">${_tv('sport.section_title','Спорт')} <span class="bukm-hm-mode">${modeLabel}</span></div>
            <div class="bukm-hm-date">${dtLocal}</div>
            ${dtUTC ? `<div class="bukm-hm-utc">${dtUTC}</div>` : ''}
          </div>
          <div class="bukm-hm-status-wrap">
            <div class="bukm-hm-status ${b.result_status==='won'?'bukm-hm-status--won':b.result_status==='lost'?'bukm-hm-status--lost':''}">${
              b.result_status==='won' ? icoCheck+' '+_tv('sport.hist_win','Выиграно') :
              b.result_status==='lost'? '✗ '+_tv('sport.hist_lost','Проигрыш') :
              b.result_status==='void'? '↩ '+_tv('sport.hist_void','Возврат') :
              icoCheck+' '+_tv('sport.hist_accepted','Принято')
            }</div>
          </div>
        </div>

        <div class="bukm-hm-section-title">${_tv('sport.hist_picks', b.picks?.length > 1 ? 'Події' : 'Подія')} <span class="bukm-hm-picks-count">${b.picks?.length || 1}</span></div>
        <div class="bukm-hm-picks">${picksHtml}</div>

        <div class="bukm-hm-totals">
          <div class="bukm-hm-total-row">
            <span class="bukm-hm-total-lbl">${icoStake} ${_tv('sport.stake_amount','Сума ставки')}</span>
            <span class="bukm-hm-total-val">${sym}${Number(b.stake||0).toFixed(2)}</span>
          </div>
          <div class="bukm-hm-total-row">
            <span class="bukm-hm-total-lbl">${icoCoef} ${_tv('sport.total_odds','Загальний коефіцієнт')}</span>
            <span class="bukm-hm-total-val accent">×${Number(b.coef||1).toFixed(2)}</span>
          </div>
          <div class="bukm-hm-total-row win-row">
            <span class="bukm-hm-total-lbl">${icoWin} ${_tv('sport.potential_win','Можливий виграш')}</span>
            <span class="bukm-hm-total-val gold">${sym}${Number(b.potential_win||0).toFixed(2)}</span>
          </div>
        </div>

        ${videoHtml}`;

      let m = el('modal-bukm-bet-detail');
      if (!m) {
        m = document.createElement('div');
        m.id = 'modal-bukm-bet-detail';
        m.className = 'modal';
        document.body.appendChild(m);
      }
      m.innerHTML = `<div class="mbox mbox-bukm-bet" onclick="event.stopPropagation()">${innerHtml}</div>`;
      el('overlay')?.classList.add('on');
      m.classList.add('on');
    } catch(e) { console.error('[bukm-bet-modal]', e); }
  }

  async function loadGameHistory() {
    const box = el('game-history-list');
    if (!box) return;
    box.innerHTML = '<div class="game-hist-empty"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin-icon"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></div>';
    let r, rawText;
    try {
      const resp = await _secureFetch('/api/game/history?limit=30', {credentials:'include'});
      rawText = await resp.text();
      r = JSON.parse(rawText);
    } catch(e) {
      console.error('[history] fetch/parse error:', e, 'raw:', rawText);
      box.innerHTML = '<div class="game-hist-empty" style="padding:24px;color:var(--t3);font-size:11px">' + (rawText ? rawText.substring(0,120) : e.message) + '</div>';
      return;
    }
    if (!r || r.error) {
      box.innerHTML = '<div class="game-hist-empty" style="padding:24px;color:var(--t3)">' + (r?.error || _t('settings.history_error') || 'Ошибка') + '</div>';
      return;
    }
    const hist = r.history || [];

    // Спорт-ставки — тянем с сервера, localStorage как fallback
    let bukmRows = [];
    try {
      const bResp = await _secureFetch('/api/sport/bets?limit=50', {credentials:'include'});
      if (bResp.ok) {
        const bData = await bResp.json();
        _bukmServerBets = bData.bets || [];
        // Sync to localStorage for offline modal
        if (_bukmServerBets.length) {
          localStorage.setItem('bukm_bet_history', JSON.stringify(_bukmServerBets.slice(0,50)));
        }
      }
    } catch(_e) { /* fallback to localStorage */ }
    bukmRows = _bukmBetHistoryRows(_bukmServerBets);

    if (!hist.length && !bukmRows.length) {
      box.innerHTML = '<div class="game-hist-empty" style="padding:24px;color:var(--t3)">' + (_t('settings.history_empty') || 'Нет игровых раундов') + '</div>';
      return;
    }
    // Filter out sport_bet — shown separately in bukmRows to avoid duplicates
    const gameRows = hist.filter(h => h.game_id !== 'sport_bet').map(h => {
      const betOrig    = parseFloat(h.bet || 0);
      const winOrig    = parseFloat(h.win || 0);
      const origCur    = h.currency || 'UAH';
      const curCur     = S.user?.currency || 'UAH';

      const bet    = convertAmount(betOrig, origCur, curCur);
      const win    = convertAmount(winOrig, origCur, curCur);
      const profit = win - bet;
      const isWin  = profit >= 0;
      const sym    = csym(curCur);

      const multi  = h.multiplier ? '×'+parseFloat(h.multiplier).toFixed(2) : '';
      const dt     = h.created_at ? new Date(h.created_at.replace(' ','T')).toLocaleString() : '';

      const origHint = (origCur !== curCur)
        ? ` <span style="font-size:10px;opacity:.45;font-weight:400">${csym(origCur)}${betOrig.toFixed(2)}</span>` : '';

      return '<div class="game-hist-row">'
        + '<div class="game-hist-icon ' + (isWin?'win':'loss') + '">' + (isWin?'↑':'↓') + '</div>'
        + '<div class="game-hist-info">'
        +   '<div class="game-hist-game">' + (h.game_id||'—').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c])) + ' <span class="game-hist-multi">' + multi + '</span></div>'
        +   '<div class="game-hist-date">' + dt + '</div>'
        + '</div>'
        + '<div class="game-hist-amounts">'
        +   '<div class="game-hist-bet">' + (_t('settings.history_bet')||'Ставка') + ': ' + sym + bet.toFixed(2) + origHint + '</div>'
        +   '<div class="game-hist-profit ' + (isWin?'win':'loss') + '">' + (isWin?'+':'') + sym + Math.abs(profit).toFixed(2) + '</div>'
        + '</div>'
        + '</div>';
    });

    box.innerHTML = [...bukmRows, ...gameRows].join('');
  }

  // ── TICKET HISTORY ─────────────────────────────────────────────────────────

  const _TICKET_STATUS = {
    open:     { cls:'tkt-panel--yellow', badge:'', color:'rgba(240,180,41,1)' },
    progress: { cls:'tkt-panel--yellow', badge:'', color:'rgba(240,180,41,1)' },
    closed:   { cls:'tkt-panel--green',  badge:'', color:'rgba(0,230,118,1)' },
    rejected: { cls:'tkt-panel--red',    badge:'', color:'rgba(255,69,96,1)' },
  };

  function _tktStatusLabel(s) {
    const k = {open:'ticket_status_open',progress:'ticket_status_progress',closed:'ticket_status_closed',rejected:'ticket_status_rejected'}[s] || 'ticket_status_open';
    return _t('tickets.'+k) || s;
  }
  function _tktStatusEmoji(s) {
    return {open:'🟡',progress:'🟡',closed:'🟢',rejected:'🔴'}[s] || '🟡';
  }
  function _fmtDate(str) {
    if (!str) return '';
    try { return new Date(str.replace(' ','T')).toLocaleString(); } catch { return str; }
  }

  async function loadMyTickets() {
    const wrap = el('tickets-list-wrap');
    if (!wrap) return;
    wrap.innerHTML = '<div class="tickets-loading"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spin-icon"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg></div>';
    let r;
    try {
      const resp = await _secureFetch('/api/support/my_tickets', {credentials:'include'});
      r = await resp.json();
    } catch(e) {
      wrap.innerHTML = '<div class="tkt-empty">' + (_t('toasts.error_generic')||'Ошибка загрузки') + '</div>';
      return;
    }
    if (r.error) { wrap.innerHTML = '<div class="tkt-empty">' + r.error + '</div>'; return; }
    const tickets = r.tickets || [];
    if (!tickets.length) {
      wrap.innerHTML = '<div class="tkt-empty">' + (_t('tickets.tickets_empty')||'Нет тикетов') + '</div>';
      return;
    }
    wrap.innerHTML = tickets.map(t => {
      const st = _TICKET_STATUS[t.status] || _TICKET_STATUS.open;
      const badge = _tktStatusEmoji(t.status) + ' ' + _tktStatusLabel(t.status);
      const accentColor = {open:'rgba(240,180,41,.85)',progress:'rgba(240,180,41,.85)',closed:'rgba(0,230,118,.85)',rejected:'rgba(255,69,96,.85)'}[t.status] || 'rgba(240,180,41,.85)';
      return `<div class="tkt-panel ${st.cls}" style="margin-bottom:8px" onclick="V.openTicket(${t.id})">
        <div class="tkt-panel-accent" style="background:${accentColor}"></div>
        <div class="tkt-panel-body">
          <div class="tkt-panel-top">
            <div class="tkt-panel-subject">${t.subject.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div>
            <div class="tkt-panel-badge" style="color:${accentColor};border-color:${accentColor}">${badge}</div>
          </div>
          <div class="tkt-panel-date">#${t.uuid} &nbsp;·&nbsp; ${_fmtDate(t.created_at)}</div>
        </div>
      </div>`;
    }).join('');
  }

  async function openTicket(ticketId) {
    // Show modal with ticket details and messages
    let r;
    try {
      const resp = await _secureFetch(`/api/support/ticket/${ticketId}/messages`, {credentials:'include'});
      r = await resp.json();
    } catch(e) { toast(_t('toasts.error_generic')||'Ошибка','err'); return; }
    if (r.error) { toast(r.error,'err'); return; }

    const t = r.ticket;
    const msgs = r.messages || [];
    const st = _TICKET_STATUS[t.status] || _TICKET_STATUS.open;
    const accentColor = {open:'rgba(240,180,41,.9)',progress:'rgba(240,180,41,.9)',closed:'rgba(0,230,118,.9)',rejected:'rgba(255,69,96,.9)'}[t.status] || 'rgba(240,180,41,.9)';
    const badge = _tktStatusEmoji(t.status)+' '+_tktStatusLabel(t.status);
    const canClose = t.status !== 'closed' && t.status !== 'rejected';
    const canReply = t.status !== 'closed' && t.status !== 'rejected';

    const msgsHtml = msgs.length
      ? msgs.map(m => {
          if (m.sender === 'system') {
            // Extract admin public_id from message like "Ticket taken by Nick (ADM-XXXXX)"
            const sysText = m.message.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
            const pubIdMatch = m.message.match(/\(([A-Z]{3}-[A-Z0-9]+)\)/);
            const pubId = pubIdMatch ? `<span class="tkt-sys-id">${pubIdMatch[1]}</span>` : '';
            return `<div class="tkt-msg-system">
              <span class="tkt-msg-system-ico">⚡</span>
              <span>${sysText}</span>${pubId}
              <span class="tkt-msg-time" style="margin-left:4px">${_fmtDate(m.created_at)}</span>
            </div>`;
          }
          const isAdmin = m.sender === 'admin';
          const label = isAdmin ? (_t('tickets.ticket_msg_admin')||'Администратор') : (_t('tickets.ticket_msg_you')||'Вы');
          return `<div class="tkt-msg ${isAdmin?'tkt-msg-admin':'tkt-msg-user'}">
            <div class="tkt-msg-sender">${label}</div>
            <div class="tkt-msg-bubble">${m.message.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div>
            <div class="tkt-msg-time">${_fmtDate(m.created_at)}</div>
          </div>`;
        }).join('')
      : `<div class="tkt-no-msg">${_t('tickets.ticket_modal_no_messages')||'Сообщений пока нет'}</div>`;

    const replyForm = canReply ? `
      <div class="tkt-reply-row">
        <textarea class="tkt-reply-inp inp" id="tkt-reply-inp" placeholder="..." rows="2"></textarea>
        <button class="tkt-reply-btn btn-p" id="tkt-reply-btn" onclick="V.replyToTicket(${t.id})">
          <svg width="14" height="14" viewBox="0 0 20 20" fill="none"><path d="M3 10l14-7-5 7 5 7-14-7z" fill="currentColor"/></svg>
        </button>
      </div>` : '';

    const closeBtn = canClose
      ? `<button class="tkt-m-close-btn" onclick="V.closeTicketByUser(${t.id})">
          <svg width="13" height="13" viewBox="0 0 20 20" fill="none"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
          ${_t('tickets.ticket_close_btn')||'Закрыть тикет'}
        </button>` : '';

    const existing = el('tkt-detail-overlay');
    if (existing) existing.remove();

    const ov = document.createElement('div');
    ov.id = 'tkt-detail-overlay';
    ov.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:2000;display:flex;align-items:center;justify-content:center;padding:16px';
    ov.innerHTML = `<div class="tkt-modal-box">
      <button class="m-close" style="position:absolute;top:12px;right:12px" onclick="document.getElementById('tkt-detail-overlay').remove()">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
      </button>
      <div class="tkt-m-title">${t.subject.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div>
      <div class="tkt-m-meta">
        <span class="tkt-m-date-val">#${t.id} &nbsp;·&nbsp; ${_fmtDate(t.created_at)}</span>
        <span class="tkt-m-status-badge" style="color:${accentColor};border-color:${accentColor}">${badge}</span>
      </div>
      <div class="tkt-m-desc-panel" onclick="this.querySelector('.tkt-m-desc-wrap').classList.toggle('open')">
        <div class="tkt-m-desc-header">
          <span>${_t('tickets.ticket_modal_desc')||'Описание'}</span>
          <svg class="tkt-m-desc-arrow" width="13" height="13" viewBox="0 0 20 20" fill="none"><path d="M5 7l5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </div>
        <div class="tkt-m-desc-wrap"><div class="tkt-m-desc-text">${t.description.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div></div>
      </div>
      <div class="tkt-m-chat-section">
        <div class="tkt-m-chat-label">${_t('tickets.ticket_modal_messages')||'Переписка'}</div>
        <div class="tkt-m-chat" id="tkt-chat-box">${msgsHtml}</div>
      </div>
      ${replyForm}
      ${closeBtn}
    </div>`;
    ov.style.position = 'fixed';
    ov.addEventListener('click', e => { if (e.target === ov) ov.remove(); });
    document.body.appendChild(ov);

    // Scroll chat to bottom
    const chatBox = ov.querySelector('#tkt-chat-box');
    if (chatBox) chatBox.scrollTop = chatBox.scrollHeight;

    // Auto-refresh messages every 6s while overlay is open
    let _tktPollId = null;
    const _startPoll = () => {
      _tktPollId = setInterval(async () => {
        if (!document.getElementById('tkt-detail-overlay')) { clearInterval(_tktPollId); return; }
        try {
          const pr = await _secureFetch(`/api/support/ticket/${t.id}/messages`, {credentials:'include'});
          const pd = await pr.json();
          const chat = el('tkt-chat-box');
          if (!chat || !pd.messages) return;
          const newMsgs = pd.messages.filter(m => m.sender !== 'user' || true); // all
          const rendered = newMsgs.map(m => {
            if (m.sender === 'system') {
              const sysText = m.message.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
              const pubIdMatch = m.message.match(/\(([A-Z]{3}-[A-Z0-9]+)\)/);
              const pubId = pubIdMatch ? `<span class="tkt-sys-id">${pubIdMatch[1]}</span>` : '';
              return `<div class="tkt-msg-system"><span class="tkt-msg-system-ico">⚡</span><span>${sysText}</span>${pubId}<span class="tkt-msg-time" style="margin-left:4px">${_fmtDate(m.created_at)}</span></div>`;
            }
            const isAdmin = m.sender === 'admin';
            const lbl = isAdmin ? (_t('tickets.ticket_msg_admin')||'Администратор') : (_t('tickets.ticket_msg_you')||'Вы');
            return `<div class="tkt-msg ${isAdmin?'tkt-msg-admin':'tkt-msg-user'}"><div class="tkt-msg-sender">${lbl}</div><div class="tkt-msg-bubble">${m.message.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div><div class="tkt-msg-time">${_fmtDate(m.created_at)}</div></div>`;
          }).join('');
          const wasBottom = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 10;
          chat.innerHTML = rendered || `<div class="tkt-no-msg">${_t('tickets.ticket_modal_no_messages')||'Сообщений пока нет'}</div>`;
          if (wasBottom) chat.scrollTop = chat.scrollHeight;
        } catch(e) {}
      }, 6000);
    };
    _startPoll();
    ov.addEventListener('click', e => { if (e.target === ov) { clearInterval(_tktPollId); } });

    // Set placeholder properly (avoids _t() returning key when lang not loaded)
    const inp = ov.querySelector('#tkt-reply-inp');
    if (inp) {
      const ph = _t('tickets.ticket_reply_ph');
      inp.placeholder = (ph && ph !== 'tickets.ticket_reply_ph') ? ph : 'Ваш ответ...';
      // Enter = send, Shift+Enter = newline
      inp.addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); V.replyToTicket(t.id); }
      });
    }
  }

  async function replyToTicket(ticketId) {
    const inp = el('tkt-reply-inp');
    const btn = el('tkt-reply-btn');
    if (!inp) return;
    const message = inp.value.trim();
    if (!message) return;
    if (btn) { btn.disabled = true; btn.style.opacity = '.5'; }
    try {
      const resp = await _secureFetch(`/api/support/ticket/${ticketId}/reply`, {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({message}),
      });
      const r = await resp.json();
      if (r.error) { toast(r.error, 'err'); return; }
      const chat = el('tkt-chat-box');
      if (chat) {
        const noMsg = chat.querySelector('.tkt-no-msg');
        if (noMsg) noMsg.remove();
        const you = _t('tickets.ticket_msg_you') || 'Вы';
        const now = new Date().toLocaleString();
        const div = document.createElement('div');
        div.className = 'tkt-msg tkt-msg-user';
        div.innerHTML = `<div class="tkt-msg-sender">${you}</div>
          <div class="tkt-msg-bubble">${message.replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]))}</div>
          <div class="tkt-msg-time">${now}</div>`;
        chat.appendChild(div);
        chat.scrollTop = chat.scrollHeight;
      }
      inp.value = '';
    } catch(e) { toast(_t('toasts.error_generic')||'Ошибка', 'err'); }
    finally { if (btn) { btn.disabled = false; btn.style.opacity = '1'; } }
  }

    async function closeTicketByUser(ticketId) {
    // Custom confirm modal instead of native browser dialog
    await new Promise((resolve) => {
      const existing = el('tkt-confirm-overlay');
      if (existing) existing.remove();

      const co = document.createElement('div');
      co.id = 'tkt-confirm-overlay';
      co.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.72);z-index:3000;display:flex;align-items:center;justify-content:center;padding:16px;backdrop-filter:blur(4px)';
      const confirmText = _t('tickets.ticket_close_confirm') || 'Вы уверены что хотите закрыть тикет?';
      const yesText = _t('tickets.ticket_close_yes') || 'Закрыть';
      const noText  = _t('tickets.ticket_close_no')  || 'Отмена';
      co.innerHTML = `<div class="tkt-confirm-box">
        <div class="tkt-confirm-icon">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="var(--red)" stroke-width="1.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="12" cy="16" r=".5" fill="var(--red)"/></svg>
        </div>
        <div class="tkt-confirm-text">${confirmText}</div>
        <div class="tkt-confirm-btns">
          <button class="tkt-confirm-no btn-ghost" id="tkt-conf-no">${noText}</button>
          <button class="tkt-confirm-yes" id="tkt-conf-yes">${yesText}</button>
        </div>
      </div>`;
      document.body.appendChild(co);

      const cleanup = (confirmed) => {
        co.remove();
        resolve(confirmed);
      };
      co.querySelector('#tkt-conf-no').onclick  = () => cleanup(false);
      co.querySelector('#tkt-conf-yes').onclick = () => cleanup(true);
      co.addEventListener('click', e => { if (e.target === co) cleanup(false); });
    }).then(async (confirmed) => {
      if (!confirmed) return;
      try {
        const resp = await _secureFetch(`/api/support/ticket/${ticketId}/close`, {method:'POST',credentials:'include'});
        const r = await resp.json();
        if (r.error) { toast(r.error,'err'); return; }
        toast(_t('tickets.ticket_closed_ok')||'Тикет закрыт','ok');
        const ov = el('tkt-detail-overlay');
        if (ov) ov.remove();
        loadMyTickets();
      } catch(e) { toast(_t('toasts.error_generic')||'Ошибка','err'); }
    });
  }

  async function saveProfile(){const uname=el('set-uname-inp').value.trim();
    const cur=S.user?.currency||localStorage.getItem('velox_currency')||'UAH';
    try{const r=await api('/api/auth/profile','PUT',{username:uname,currency:cur});if(r.user){setUser(r.user);toast(_t('toasts.save_success'),'ok');}else if(r.error)toast(r.error,'err');}catch{toast(_t('toasts.error_generic'),'err');}}
  function profileContactInputChanged(){
    const email = (el('set-email-new')?.value || '').trim();
    const code = (el('set-email-code')?.value || '').trim();
    const phone = (el('set-phone-new')?.value || '').trim();
    const sendBtn = el('set-email-send-btn');
    const verifyBtn = el('set-email-verify-btn');
    const phoneBtn = el('set-phone-save-btn');
    if (sendBtn) sendBtn.style.display = email ? '' : 'none';
    if (verifyBtn) verifyBtn.style.display = code ? '' : 'none';
    if (phoneBtn) phoneBtn.style.display = phone ? '' : 'none';
  }
  async function sendEmailChangeOtp(){
    const email = (el('set-email-new')?.value || '').trim().toLowerCase();
    const hint = el('set-email-hint');
    if (hint) hint.textContent = '';
    if (!email || !validEmail(email)) { toast(_t('settings.invalid_email') || 'Введите корректный email', 'err'); return; }
    try{
      const r = await api('/api/auth/profile/email/send_otp','POST',{email});
      if(r.error){toast(r.error,'err');return;}
      if (hint) hint.textContent = `${_t('settings.email_code_sent_to') || 'Код отправлен на'} ${r.masked || email}`;
      toast(_t('settings.email_code_sent') || 'Код отправлен','ok');
      profileContactInputChanged();
    }catch{toast(_t('toasts.error_generic'),'err');}
  }
  async function verifyEmailChangeOtp(){
    const code = (el('set-email-code')?.value || '').trim();
    if(!code){toast(_t('settings.email_code_required') || 'Введите код подтверждения','err');return;}
    try{
      const r = await api('/api/auth/profile/email/verify_otp','POST',{code});
      if(r.error){toast(r.error,'err');return;}
      if(r.user){
        setUser(r.user);
        const hint = el('set-email-hint');
        if (hint) hint.textContent = _t('settings.email_verified') || 'Email подтвержден';
        const codeEl = el('set-email-code'); if(codeEl) codeEl.value = '';
        toast(_t('settings.email_updated') || 'Почта обновлена','ok');
        profileContactInputChanged();
      }
    }catch{toast(_t('toasts.error_generic'),'err');}
  }
  async function savePhoneProfile(){
    const phone = (el('set-phone-new')?.value || '').trim();
    if(!phone){toast(_t('settings.phone_required') || 'Введите номер телефона','err');return;}
    try{
      const r = await api('/api/auth/profile/phone','PUT',{phone});
      if(r.error){toast(r.error,'err');return;}
      if(r.user){setUser(r.user);toast(_t('settings.phone_updated') || 'Телефон обновлен','ok');profileContactInputChanged();}
    }catch{toast(_t('toasts.error_generic'),'err');}
  }
  async function changePw(){
    const cur=el('pw-cur').value,nw=el('pw-new').value,cnf=el('pw-cnf').value;
    if(!cur||!nw||!cnf){toast(_t('toasts.error_fill'),'err');return;}
    if(nw!==cnf){toast(_t('toasts.error_password_match'),'err');return;}
    if(nw.length<6){toast(_t('toasts.error_short_password'),'err');return;}
    try{const r=await api('/api/auth/password','PUT',{current_password:cur,new_password:nw,confirm_password:cnf});if(r.error){toast(r.error,'err');return;}['pw-cur','pw-new','pw-cnf'].forEach(id=>el(id).value='');toast(_t('toasts.save_success'),'ok');}catch{toast(_t('toasts.error_generic'),'err');}
  }
  function tfa(chk){chk.checked=!chk.checked;openTFA(chk.checked?'disable':'enable');}
  function openTFA(action,cb){S.tfa_action=action;S.tfa_pending_cb=cb||null;const ie=action==='enable';txt('tfa-title',ie?_tv('auth.totp_enable_title','Enable 2FA TOTP'):_t('auth.tfa_title'));txt('tfa-sub',_t('auth.tfa_desc'));el('tfa-pw').value='';el('tfa-code').value='';el('tfa-code-w').classList.add('hidden');el('tfa-res').classList.add('hidden');txt('tfa-err','');el('tfa-btn').dataset.a=action;el('tfa-btn').textContent=_t('auth.tfa_btn');modal('2fa');}
  async function tfaConfirm(){
    const action=el('tfa-btn').dataset.a;if(action==='close'){closeM();return;}
    const pw=el('tfa-pw').value,code=el('tfa-code').value;txt('tfa-err','');
    try{const r=await api('/api/auth/2fa','PUT',{action,password:pw,two_fa:code});
      if(r.error){txt('tfa-err',r.error);return;}
      if(r.two_fa_setup){
        const setup = r.two_fa_setup || {};
        const box = el('tfa-code-show');
        if (box) {
          const secret = (setup.secret || '').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
          const qr = (setup.qr_url || '').replace(/"/g, '&quot;');
          box.innerHTML = (qr ? `<div style="display:flex;justify-content:center;margin-bottom:8px"><img src="${qr}" alt="TOTP QR" style="width:180px;height:180px;border-radius:10px;border:1px solid rgba(255,255,255,.1)"></div>` : '')
            + `<div style="font-size:11px;color:var(--t2);margin-bottom:4px">${_tv('auth.totp_manual_key','Manual key')}</div><div style="font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace">${secret}</div>`;
        }
        el('tfa-code-w').classList.remove('hidden');
        el('tfa-res').classList.remove('hidden');
        el('tfa-btn').textContent=_tv('auth.totp_verify_enable_btn','Enable after code verify');
        el('tfa-btn').dataset.a='enable';
        txt('tfa-err',_tv('auth.totp_scan_hint','Scan QR, enter a 6-digit code and press again'));
        return;
      }
      toast(action==='enable'?'2FA ON':'2FA OFF','ok');if(S.user)S.user.two_fa=action==='enable';el('tfa-tog').checked=S.user?.two_fa||false;closeM();}
    catch{txt('tfa-err',_t('validation.error'));}
  }
  function verFile(inp){ if(inp.files[0]) txt('ver-fname','📎 '+inp.files[0].name); }

  // ── KYC SYSTEM ─────────────────────────────────────────────────────────────
  // State
  let _kycMode = 'workflow';   // workflow | scan | manual
  let _kycStream = null;       // MediaStream from camera
  let _kycRecorder = null;     // MediaRecorder
  let _kycChunks = [];
  let _kycBlob = null;
  let _kycWordTimer = null;
  let _kycWord = '';

  // Called when modal-verify opens
  async function _kycInit() {
    _kycBlob = null; _kycChunks = [];
    if (_kycStream) { _kycStream.getTracks().forEach(t=>t.stop()); _kycStream=null; }
    txt('ver-err',''); txt('ver-ok','');

    // Load KYC status + config from server
    try {
      const r = await api('/api/kyc/status','GET');
      const status = r.status || 'none';

      // Show status badge if already submitted
      if (status !== 'none') {
        const panel = el('ver-status-panel');
        if (panel) panel.style.display = '';
        const badge = el('kyc-status-badge');
        const labels = {
          pending:  '⏳ ' + (_t('kyc.kyc_status_pending')  || 'На проверке'),
          verified: '✅ ' + (_t('kyc.kyc_status_verified') || 'Верифицирован'),
          rejected: '❌ ' + (_t('kyc.kyc_status_rejected') || 'Отклонено'),
        };
        if (badge) { badge.textContent = labels[status] || status; badge.className = 'kyc-status-badge ' + status; }
        if (status === 'rejected' && r.rejection_reason) {
          const re = el('kyc-reason');
          if (re) { re.textContent = (_t('kyc.kyc_rejection_reason')||'Причина') + ': ' + r.rejection_reason; re.style.display=''; }
        }
        if (status === 'verified') {
          // Already verified — just show badge, hide form
          const formPanels = ['kyc-mode-tabs','ver-dob-wrap','kyc-panel-workflow','kyc-panel-scan','kyc-panel-manual','ver-send-btn'];
          formPanels.forEach(id => { const e=el(id); if(e) e.style.display='none'; });
          return;
        }
      }

      // Determine which mode tabs to show
      const serverMode = r.mode || 'workflow';
      const hasScan    = !!r.active_provider;
      _kycMode = serverMode === 'scan' && hasScan ? 'scan'
               : serverMode === 'both' && hasScan ? 'workflow'
               : serverMode === 'manual' ? 'manual' : 'workflow';

      // Show mode tabs only if multiple modes available
      const tabsEl = el('kyc-mode-tabs');
      const showTabs = serverMode === 'both' || (hasScan && serverMode !== 'manual');
      if (tabsEl) tabsEl.style.display = showTabs ? '' : 'none';

      // Hide scan tab if no provider
      if (!hasScan) {
        const scanTab = el('kyc-tab-scan');
        if (scanTab) scanTab.style.display = 'none';
      }

      kycSetMode(_kycMode);

      // Load liveness word
      if (_kycMode === 'workflow') await _kycLoadWord();

    } catch(e) {
      console.warn('[kycInit]', e);
      kycSetMode('workflow');
    }
  }

  function kycSetMode(mode) {
    _kycMode = mode;
    ['workflow','scan','manual'].forEach(m => {
      const tab   = el(`kyc-tab-${m}`);
      const panel = el(`kyc-panel-${m}`);
      if (tab)   tab.classList.toggle('active', m === mode);
      if (panel) panel.style.display = (m === mode) ? '' : 'none';
    });
    // Update submit button label
    const lblMap = {
      workflow: _t('kyc.kyc_submit_btn') || 'Отправить видео',
      scan:     _t('kyc.kyc_scan_btn')   || 'Начать верификацию',
      manual:   _t('kyc.kyc_submit_btn') || 'Отправить документы',
    };
    const lbl = el('ver-send-lbl'); if (lbl) lbl.textContent = lblMap[mode] || 'Отправить';
    // Load word when switching to workflow
    if (mode === 'workflow' && !_kycWord) _kycLoadWord();
  }

  async function _kycLoadWord() {
    try {
      const r = await api('/api/kyc/word','GET');
      _kycWord = r.word || '';
      const wv = el('kyc-word-value'); if (wv) wv.textContent = _kycWord;
      _kycStartWordTimer(r.remaining_seconds || 0, r.ttl_minutes || 120);
    } catch(e) { console.warn('[kycWord]', e); }
  }

  function _kycStartWordTimer(remaining, ttlMin) {
    if (_kycWordTimer) clearInterval(_kycWordTimer);
    let rem = remaining;
    const update = () => {
      const timerEl = el('kyc-word-timer');
      if (!timerEl) return;
      if (rem <= 0) {
        clearInterval(_kycWordTimer);
        timerEl.textContent = _t('kyc.kyc_word_expired')||'Слово устарело — обновляется...';
        timerEl.style.color = '#ef4444';
        _kycLoadWord();
        return;
      }
      const m = Math.floor(rem/60), s = rem%60;
      // Format with localized time units from i18n
      const _h = _t('kyc._time_h')||'h';
      const _m = _t('kyc._time_m')||'m';
      const _s = _t('kyc._time_s')||'s';
      let timeStr;
      if (rem >= 3600) {
        const h = Math.floor(rem/3600), mm = Math.floor((rem%3600)/60);
        timeStr = `${h}${_h} ${mm}${_m}`;
      } else if (rem >= 60) {
        timeStr = `${m}${_m} ${String(s).padStart(2,'0')}${_s}`;
      } else {
        timeStr = `${rem}${_s}`;
      }
      timerEl.textContent = (_t('kyc.kyc_word_valid')||'Valid for') + ' ' + timeStr;
      timerEl.style.color = rem < 60 ? '#ef4444' : rem < 300 ? '#facc15' : 'var(--t2)';
      rem--;
    };
    update();
    _kycWordTimer = setInterval(update, 1000);
  }

  // ── Camera / Recording ─────────────────────────────────────────────────────

  async function kycStartCam() {
    const btn = el('kyc-btn-cam');
    if (btn) btn.disabled = true;
    try {
      _kycStream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:640,height:480},audio:true});
      const preview = el('kyc-preview');
      if (preview) { preview.srcObject = _kycStream; preview.style.display=''; }
      const recBtn = el('kyc-btn-rec');
      if (recBtn) { recBtn.style.display=''; }
      if (btn) btn.style.display = 'none';
      txt('kyc-vid-info', _t('kyc.kyc_cam_ready')||'Камера готова. Нажмите «Начать запись».');
    } catch(e) {
      if (btn) btn.disabled = false;
      let msg;
      if (e.name === 'NotAllowedError') {
        msg = _t('kyc.kyc_cam_denied') || 'Разрешите доступ к камере в настройках браузера';
      } else if (e.name === 'NotFoundError' || e.message?.includes('allocate') || e.message?.includes('Found')) {
        msg = _t('kyc.kyc_no_cam') || 'Камера не найдена. Используйте загрузку видео-файла.';
        // Show file upload fallback for video
        const vidInfo = el('kyc-vid-info');
        if (vidInfo) vidInfo.innerHTML = '<label style="cursor:pointer;color:var(--a);font-size:12px" onclick="document.getElementById(\'kyc-vid-file\').click()">📎 ' + (_t('kyc.kyc_upload_video')||'Загрузить видео-файл') + '</label><input type="file" id="kyc-vid-file" class="hidden" accept="video/mp4,video/webm,video/mov" onchange="V.kycFileSelected(this)">';
      } else {
        msg = (_t('kyc.kyc_cam_error')||'Ошибка камеры: ') + e.message;
      }
      txt('ver-err', msg);
      if (btn) { btn.disabled = false; btn.style.display = ''; }
    }
  }

  function kycToggleRec() {
    if (!_kycRecorder || _kycRecorder.state === 'inactive') {
      _kycStartRec();
    } else {
      _kycStopRec();
    }
  }

  function _kycStartRec() {
    if (!_kycStream) return;
    _kycChunks = []; _kycBlob = null;


    // Pick best supported format and handle MediaRecorder errors
    const allMimeTypes = ['video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm','video/mp4'];
    allMimeTypes.forEach(m => {
      console.log(m, MediaRecorder.isTypeSupported(m));
    });
    // Check if stream has audio
    const hasAudio = _kycStream.getAudioTracks && _kycStream.getAudioTracks().length > 0;
    // If no audio, only try video mimeTypes
    const mimeTypes = hasAudio ? allMimeTypes : ['video/webm','video/mp4'];
    let recorder = null;
    let mimeType = '';
    let lastError = null;
    for (let mt of [...mimeTypes, '']) {
      try {
        recorder = mt ? new MediaRecorder(_kycStream, {mimeType: mt}) : new MediaRecorder(_kycStream);
        mimeType = mt;
        break;
      } catch (e) {
        lastError = e;
        continue;
      }
    }
    if (!recorder) {
      txt('kyc-vid-info', 'Ошибка: не удалось инициализировать запись видео. Проверьте настройки камеры и браузера.');
      console.error('MediaRecorder init failed', lastError);
      return;
    }
    _kycRecorder = recorder;
    _kycRecorder.ondataavailable = e => { if(e.data.size>0) _kycChunks.push(e.data); };
    _kycRecorder.onstop = () => {
      _kycBlob = new Blob(_kycChunks, {type: mimeType || 'video/webm'});
      const url = URL.createObjectURL(_kycBlob);
      const pb  = el('kyc-playback');
      const pv  = el('kyc-preview');
      if (pb) { pb.src=url; pb.style.display=''; }
      if (pv) pv.style.display='none';
      const retryBtn = el('kyc-btn-retry');
      const recBtn   = el('kyc-btn-rec');
      if (retryBtn) retryBtn.style.display='';
      if (recBtn)   recBtn.style.display='none';
      el('kyc-rec-dot')?.classList.add('hidden');
      txt('kyc-vid-info', _t('kyc.kyc_preview_ready')||'Видео записано. Проверьте и отправьте.');
    };

    try {
      _kycRecorder.start(200);
    } catch (e) {
      txt('kyc-vid-info', 'Ошибка запуска записи: ' + (e.message || e) + '. Попробуйте другой браузер или камеру.');
      console.error('MediaRecorder start failed', e);
      return;
    }
    el('kyc-rec-dot')?.classList.remove('hidden');
    const recBtn = el('kyc-btn-rec');
    if (recBtn) { recBtn.style.background='rgba(239,68,68,.2)'; recBtn.style.borderColor='rgba(239,68,68,.5)'; }
    const recLbl = el('kyc-btn-rec-lbl'); if (recLbl) recLbl.textContent = _t('kyc.kyc_stop_rec')||'Остановить';
    txt('kyc-vid-info', _t('kyc.kyc_recording')||'Запись... Произнесите слово вслух.');
  }

  function _kycStopRec() {
    if (_kycRecorder && _kycRecorder.state !== 'inactive') _kycRecorder.stop();
  }

  function kycRetry() {
    _kycBlob = null; _kycChunks = [];
    const pb = el('kyc-playback'); if(pb){pb.src='';pb.style.display='none';}
    const pv = el('kyc-preview');  if(pv) pv.style.display='';
    const retryBtn = el('kyc-btn-retry'); if(retryBtn) retryBtn.style.display='none';
    const recBtn   = el('kyc-btn-rec');
    if (recBtn) {
      recBtn.style.display=''; recBtn.style.background=''; recBtn.style.borderColor='';
      const lbl = el('kyc-btn-rec-lbl'); if(lbl) lbl.textContent=_t('kyc.kyc_start_rec')||'Начать запись';
    }
    txt('kyc-vid-info',''); txt('ver-err','');
  }

  function kycFileSelected(inp) {
    // Fallback: user selects video file instead of recording
    if (!inp.files[0]) return;
    _kycBlob = inp.files[0];
    const url = URL.createObjectURL(_kycBlob);
    const pb  = el('kyc-playback');
    if (pb) { pb.src=url; pb.style.display=''; }
    txt('ver-err','');
    txt('kyc-vid-info', '📎 ' + inp.files[0].name + ' — ' + (_t('kyc.kyc_preview_ready')||'Готово к отправке'));
    const retryBtn = el('kyc-btn-retry');
    if (retryBtn) retryBtn.style.display='';
  }

  // ── MAIN SUBMIT ─────────────────────────────────────────────────────────────

  async function verify() {
    if(!S.user){modal('auth');return;}
    txt('ver-err',''); txt('ver-ok','');

    // Sync date input lang with current language
    const _dobInp = el('ver-dob');
    if (_dobInp) { const lm={US:'en-GB',RU:'ru',UA:'uk'}; _dobInp.lang = lm[_lang] || 'en-GB'; }
    const dob = (_dobInp?.value||'').trim();
    if(!dob){txt('ver-err',_t('kyc.kyc_dob_label')+' — обязательно');return;}

    const btn=el('ver-send-btn'); if(btn){btn.disabled=true;btn.style.opacity='.65';}
    const _done=()=>{if(btn){btn.disabled=false;btn.style.opacity='';}};

    try {
      if (_kycMode === 'workflow') {
        await _kycSubmitWorkflow(dob, _done);
      } else if (_kycMode === 'scan') {
        await _kycSubmitScan(dob, _done);
      } else {
        await _kycSubmitManual(dob, _done);
      }
    } catch(e) {
      _done(); txt('ver-err', _t('toasts.error_generic'));
    }
  }

  async function _kycSubmitWorkflow(dob, _done) {
    if (!_kycBlob) {
      txt('ver-err', _t('kyc.kyc_video_required')||'Запишите видео перед отправкой');
      _done(); return;
    }
    const fd = new FormData();
    fd.append('dob',   dob);
    fd.append('word',  _kycWord);
    const ext  = _kycBlob.type.includes('mp4') ? 'mp4' : 'webm';
    fd.append('video', _kycBlob, `kyc_video.${ext}`);

    const r = await _secureFetch('/api/kyc/workflow/submit',{method:'POST',body:fd,credentials:'same-origin'});
    const data = await r.json(); _done();
    if(data.error){ txt('ver-err',data.error); return; }
    txt('ver-ok', data.message || _t('kyc.kyc_pending_msg'));
    if(S.user) S.user.kyc_status='pending';
    if(_kycWordTimer) clearInterval(_kycWordTimer);
    if(_kycStream) _kycStream.getTracks().forEach(t=>t.stop());
    setTimeout(()=>closeM(),2500);
  }

  async function _kycSubmitScan(dob, _done) {
    const r = await api('/api/kyc/scan/start','POST',{dob});
    _done();
    if(r.error){
      if(r.fallback==='workflow'){
        kycSetMode('workflow'); _kycLoadWord();
        txt('ver-err',(_t('kyc.kyc_scan_unavailable')||'Скан недоступен')+'. Используйте видео-верификацию.');
      } else { txt('ver-err',r.error); }
      return;
    }
    if(r.url){ window.open(r.url,'_blank','noopener'); closeM(); }
    else if(r.sdk_token){
      // SDK-based (Sumsub/Onfido) — placeholder for JS SDK integration
      txt('ver-ok','Сессия создана. Следуйте инструкциям провайдера.');
      setTimeout(()=>closeM(),2000);
    }
  }

  async function _kycSubmitManual(dob, _done) {
    const file = el('ver-file')?.files?.[0];
    const fd = new FormData();
    fd.append('dob', dob);
    if(file) fd.append('doc', file);
    const r = await _secureFetch('/api/kyc/manual/submit',{method:'POST',body:fd,credentials:'same-origin'});
    const data = await r.json(); _done();
    if(data.error){ txt('ver-err',data.error); return; }
    txt('ver-ok', data.message || _t('kyc.kyc_pending_msg'));
    if(S.user) S.user.kyc_status='pending';
    setTimeout(()=>closeM(),2000);
  }


  function flipCard() {}
  function _syncCvvDots() {}
  function cvvFocused() {}

  // Network logo SVG by card type
  const _NET_LOGOS = {
    VISA: '<span class="net-visa">VISA</span>',
    MC:   '<span class="net-mc"></span>',
    AMEX: '<span class="net-amex">AMEX</span>',
    MIR:  '<span class="net-mir">МИР</span>',
    CARD: '<span class="net-generic">CARD</span>',
  };

  function fmtCard(inp) {
    let v = inp.value.replace(/\D/g,'').slice(0,16);
    v = v.match(/.{1,4}/g)?.join(' ') || v;
    inp.value = v;
    const d = v.replace(/\D/g,'');
    // Update card display
    const numEl = el('pc-display-num');
    if (numEl) {
      const padded = (d + '•'.repeat(16 - Math.min(d.length,16)));
      numEl.innerHTML = [0,4,8,12].map(i => `<span>${padded.slice(i,i+4)}</span>`).join('');
    }
    const backNum = el('pc-back-num');
    if (backNum) backNum.textContent = d.length > 0 ? (d+'•'.repeat(16-d.length)).replace(/(.{4})/g,'$1 ').trim() : '•••• •••• •••• ••••';
    // Network logo
    const type = detectCardType(v);
    const netEl = el('pc-network-logo');
    if (netEl) netEl.innerHTML = _NET_LOGOS[type] || _NET_LOGOS.CARD;
    // Badge
    const badge = el('pc-type-badge');
    if (badge) badge.innerHTML = _NET_LOGOS[type] || _NET_LOGOS.CARD;
    checkCardValid();
  }

  function fmtExp(inp) {
    let v = inp.value.replace(/\D/g,'').slice(0,4);
    if (v.length >= 2) v = v.slice(0,2) + '/' + v.slice(2);
    inp.value = v;
    const expEl = el('pc-display-exp');
    if (expEl) expEl.textContent = v || '••/••';
    checkCardValid();
  }

  function chkCvv() { checkCardValid(); }

  function chkHolder(inp) {
    inp.value = inp.value.toUpperCase();
    const holderEl = el('pc-display-holder');
    if (holderEl) holderEl.textContent = inp.value || 'IVAN PETRENKO';
  }

  function checkCardValid() {
    const num = (el('inp-cnum')?.value||''), exp = (el('inp-cexp')?.value||''), cvv = (el('inp-ccvv')?.value||'');
    const vmsg = el('pay-validation-row'); if (!vmsg) return;
    const rawNum = num.replace(/\D/g,'');
    if (!rawNum) { vmsg.innerHTML = ''; return; }
    const issues = [];
    if (rawNum.length === 16 && !luhn(rawNum)) issues.push(_t('payment.card_invalid_luhn'));
    if (exp.length === 5 && !validExp(exp)) issues.push(_t('payment.check_expiry'));
    if (cvv.length > 0 && !validCvv(cvv)) issues.push('CVV: 3-4');
    if (issues.length) {
      vmsg.innerHTML = `<span class="pay-val-err"><svg width="12" height="12" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="1.5"/><path d="M10 6v4M10 14h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>${issues.join(' · ')}</span>`;
    } else if (rawNum.length === 16 && exp.length === 5 && cvv.length >= 3) {
      vmsg.innerHTML = `<span class="pay-val-ok"><svg width="12" height="12" viewBox="0 0 20 20" fill="none"><circle cx="10" cy="10" r="8" stroke="currentColor" stroke-width="1.5"/><path d="M6 10l3 3 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>${_t('payment.card_valid')}</span>`;
    } else { vmsg.innerHTML = ''; }
  }

  // ── ADDRESS — регионы и ZIP ──────────────────────────────────────────────────

  const _REGIONS = {
    UA: [
      'Вінницька область','Волинська область','Дніпропетровська область',
      'Донецька область','Житомирська область','Закарпатська область',
      'Запорізька область','Івано-Франківська область','Київська область',
      'Кіровоградська область','Луганська область','Львівська область',
      'Миколаївська область','Одеська область','Полтавська область',
      'Рівненська область','Сумська область','Тернопільська область',
      'Харківська область','Херсонська область','Хмельницька область',
      'Черкаська область','Чернівецька область','Чернігівська область',
      'м. Київ','м. Севастополь',
    ],
    RU: [
      'Москва','Санкт-Петербург','Московская область','Ленинградская область',
      'Краснодарский край','Свердловская область','Новосибирская область',
      'Нижегородская область','Татарстан','Башкортостан',
      'Ростовская область','Самарская область','Омская область',
      'Красноярский край','Воронежская область','Волгоградская область',
      'Саратовская область','Тюменская область','Иркутская область',
      'Кемеровская область','Ставропольский край','Алтайский край',
      'Приморский край','Хабаровский край','Пермский край',
      'Оренбургская область','Тульская область','Рязанская область',
      'Липецкая область','Ульяновская область','Ярославская область',
      'Мурманская область','Архангельская область','Вологодская область',
      'Белгородская область','Курская область','Брянская область',
      'Калужская область','Тверская область','Ивановская область',
      'Смоленская область','Тамбовская область','Пензенская область',
      'Орловская область','Псковская область','Новгородская область',
      'Кировская область','Костромская область','Владимирская область',
    ],
    US: [
      'Alabama (AL)','Alaska (AK)','Arizona (AZ)','Arkansas (AR)',
      'California (CA)','Colorado (CO)','Connecticut (CT)','Delaware (DE)',
      'Florida (FL)','Georgia (GA)','Hawaii (HI)','Idaho (ID)',
      'Illinois (IL)','Indiana (IN)','Iowa (IA)','Kansas (KS)',
      'Kentucky (KY)','Louisiana (LA)','Maine (ME)','Maryland (MD)',
      'Massachusetts (MA)','Michigan (MI)','Minnesota (MN)','Mississippi (MS)',
      'Missouri (MO)','Montana (MT)','Nebraska (NE)','Nevada (NV)',
      'New Hampshire (NH)','New Jersey (NJ)','New Mexico (NM)','New York (NY)',
      'North Carolina (NC)','North Dakota (ND)','Ohio (OH)','Oklahoma (OK)',
      'Oregon (OR)','Pennsylvania (PA)','Rhode Island (RI)','South Carolina (SC)',
      'South Dakota (SD)','Tennessee (TN)','Texas (TX)','Utah (UT)',
      'Vermont (VT)','Virginia (VA)','Washington (WA)','West Virginia (WV)',
      'Wisconsin (WI)','Wyoming (WY)','District of Columbia (DC)',
    ],
  };

  // Диапазоны ZIP → регион для каждой страны
  const _UA_ZIP = [
    [1000, 4999,   'м. Київ'],
    [5000, 5999,   'Вінницька область'],
    [6000, 6999,   'Луганська область'],
    [7000, 7999,   'Донецька область'],
    [8000, 8999,   'Дніпропетровська область'],
    [9000, 9999,   'Запорізька область'],
    [10000, 10999, 'Житомирська область'],
    [11000, 11999, 'Кіровоградська область'],
    [14000, 14999, 'Чернігівська область'],
    [15000, 15999, 'Сумська область'],
    [16000, 16999, 'Чернігівська область'],
    [18000, 18999, 'Черкаська область'],
    [20000, 20999, 'Хмельницька область'],
    [21000, 21999, 'Вінницька область'],
    [22000, 22999, 'Рівненська область'],
    [23000, 23999, 'Рівненська область'],
    [24000, 24999, 'Тернопільська область'],
    [25000, 25999, 'Кіровоградська область'],
    [26000, 26999, 'Івано-Франківська область'],
    [27000, 27999, 'Херсонська область'],
    [28000, 28999, 'Херсонська область'],
    [29000, 29999, 'Хмельницька область'],
    [30000, 30999, 'Одеська область'],
    [31000, 31999, 'Миколаївська область'],
    [32000, 32999, 'Київська область'],
    [33000, 33999, 'Рівненська область'],
    [34000, 34999, 'Полтавська область'],
    [36000, 36999, 'Полтавська область'],
    [37000, 37999, 'Полтавська область'],
    [38000, 38999, 'Сумська область'],
    [40000, 40999, 'Харківська область'],
    [43000, 43999, 'Волинська область'],
    [45000, 45999, 'Волинська область'],
    [46000, 46999, 'Тернопільська область'],
    [47000, 47999, 'Тернопільська область'],
    [48000, 48999, 'Черкаська область'],
    [49000, 49999, 'Дніпропетровська область'],
    [50000, 50999, 'Дніпропетровська область'],
    [51000, 51999, 'Дніпропетровська область'],
    [52000, 52999, 'Дніпропетровська область'],
    [53000, 53999, 'Полтавська область'],
    [54000, 54999, 'Миколаївська область'],
    [56000, 56999, 'Миколаївська область'],
    [57000, 57999, 'Одеська область'],
    [58000, 58999, 'Чернівецька область'],
    [59000, 59999, 'Чернівецька область'],
    [61000, 61999, 'Харківська область'],
    [62000, 62999, 'Харківська область'],
    [63000, 63999, 'Харківська область'],
    [64000, 64999, 'Харківська область'],
    [65000, 65999, 'Одеська область'],
    [66000, 66999, 'Одеська область'],
    [67000, 67999, 'Одеська область'],
    [68000, 68999, 'Одеська область'],
    [69000, 69999, 'Запорізька область'],
    [70000, 70999, 'Запорізька область'],
    [71000, 71999, 'Запорізька область'],
    [72000, 72999, 'Херсонська область'],
    [73000, 73999, 'Херсонська область'],
    [74000, 74999, 'Донецька область'],
    [75000, 75999, 'Донецька область'],
    [76000, 76999, 'Івано-Франківська область'],
    [77000, 77999, 'Івано-Франківська область'],
    [78000, 78999, 'Львівська область'],
    [79000, 79999, 'Львівська область'],
    [80000, 80999, 'Львівська область'],
    [81000, 81999, 'Львівська область'],
    [82000, 82999, 'Львівська область'],
    [83000, 83999, 'Донецька область'],
    [84000, 84999, 'Донецька область'],
    [85000, 85999, 'Донецька область'],
    [86000, 86999, 'Донецька область'],
    [87000, 87999, 'Донецька область'],
    [88000, 88999, 'Закарпатська область'],
    [89000, 89999, 'Закарпатська область'],
    [90000, 90999, 'Закарпатська область'],
    [91000, 91999, 'Луганська область'],
    [92000, 92999, 'Луганська область'],
    [93000, 93999, 'Луганська область'],
    [94000, 94999, 'Луганська область'],
    [95000, 95999, 'АР Крим'],
    [96000, 96999, 'АР Крим'],
    [97000, 97999, 'АР Крим'],
    [98000, 98999, 'АР Крим'],
    [99000, 99999, 'м. Севастополь'],
  ];

  // US ZIP первые цифры → штат (приблизительно)
  const _US_ZIP_PREFIX = {
    '0': ['Connecticut (CT)','Maine (ME)','Massachusetts (MA)','New Hampshire (NH)','New Jersey (NJ)','Rhode Island (RI)','Vermont (VT)'],
    '1': ['Delaware (DE)','New York (NY)','Pennsylvania (PA)'],
    '2': ['District of Columbia (DC)','Maryland (MD)','North Carolina (NC)','South Carolina (SC)','Virginia (VA)','West Virginia (WV)'],
    '3': ['Alabama (AL)','Florida (FL)','Georgia (GA)','Mississippi (MS)','Tennessee (TN)'],
    '4': ['Indiana (IN)','Kentucky (KY)','Michigan (MI)','Ohio (OH)'],
    '5': ['Iowa (IA)','Minnesota (MN)','Montana (MT)','Nebraska (NE)','North Dakota (ND)','South Dakota (SD)','Wisconsin (WI)'],
    '6': ['Illinois (IL)','Kansas (KS)','Missouri (MO)'],
    '7': ['Arkansas (AR)','Louisiana (LA)','Oklahoma (OK)','Texas (TX)'],
    '8': ['Arizona (AZ)','Colorado (CO)','Idaho (ID)','Nevada (NV)','New Mexico (NM)','Utah (UT)','Wyoming (WY)'],
    '9': ['Alaska (AK)','California (CA)','Hawaii (HI)','Oregon (OR)','Washington (WA)'],
  };

  function _populateRegions(countryCode, autoSelect) {
    const sel = el('inp-region'); if (!sel) return;
    const regions = _REGIONS[countryCode] || [];
    const noCountryTxt = _t('pay.select_region');
    const selectRegionTxt = '— ' + _t('pay.region') + ' —';
    sel.innerHTML = regions.length
      ? `<option value="">${selectRegionTxt}</option>` + regions.map(r => `<option value="${r}">${r}</option>`).join('')
      : `<option value="">${noCountryTxt}</option>`;
    if (autoSelect) {
      const opt = [...sel.options].find(o => o.value === autoSelect);
      if (opt) sel.value = autoSelect;
    }
  }

  function onCountryChange() {
    const code = el('inp-country')?.value || '';
    _populateRegions(code);
    const zipInp = el('inp-zip');
    if (zipInp) {
      zipInp.placeholder = {UA:'01000', RU:'101000', US:'10001'}[code] || '—';
      zipInp.value = '';
      const zr = el('zip-result'); if (zr) zr.textContent = '';
    }
    // Auto currency by country
    const countryLangMap = {UA:'UA', RU:'RU', US:'EN'};
    const targetLang = countryLangMap[code];
    if (targetLang && _LANGS[targetLang]) {
      const sym = _LANGS[targetLang].sym;
      const paySymEl = el('pay-cur-sym'); if (paySymEl) paySymEl.textContent = sym;
      const feeEl = el('pay-fee-val'); if (feeEl) feeEl.textContent = '0.00 ' + sym;
      const totEl = el('pay-total-val'); if (totEl) totEl.textContent = '— ' + sym;
    }
  }

  function onRegionChange() { /* can extend later */ }

  function lookupZip(inp) {
    const v = inp.value.trim().replace(/\s/g,'');
    const zipResult = el('zip-result'); if (!zipResult) return;
    const countrySel = el('inp-country');
    const countryCode = countrySel?.value || '';

    if (v.length < 4) { zipResult.textContent = ''; return; }

    // Auto-detect country from ZIP format if not set
    let detected = countryCode;
    if (!detected) {
      if (/^\d{5,6}$/.test(v)) {
        const n = parseInt(v);
        if (n >= 100000) detected = 'RU';
        else if (n >= 1000 && n <= 99999) detected = 'UA';
      }
      if (/^\d{5}(-\d{4})?$/.test(v) && v[0] >= '0' && v[0] <= '9') {
        const n = parseInt(v.slice(0,5));
        if (n >= 10000 && n <= 99950) {
          // Could be US or UA — prefer if country set, else ambiguous
          if (!detected) detected = 'US';
        }
      }
      if (detected && countrySel && !countrySel.value) {
        countrySel.value = detected;
        _populateRegions(detected);
      }
    }

    let regionHit = '';

    if (detected === 'UA') {
      if (!/^\d{5}$/.test(v)) { _zipErr(_t('zip.ua_format'), zipResult); return; }
      const n = parseInt(v);
      const row = _UA_ZIP.find(([lo, hi]) => n >= lo && n <= hi);
      if (row) {
        regionHit = row[2];
        _zipOk(`${regionHit}`, zipResult);
        _autoSelectRegion(regionHit);
      } else {
        _zipWarn(_t('zip.region_unknown'), zipResult);
      }

    } else if (detected === 'RU') {
      if (!/^\d{6}$/.test(v)) { _zipErr(_t('zip.ru_format'), zipResult); return; }
      // Rough mapping by first 3 digits
      const prefix3 = parseInt(v.slice(0,3));
      let region = '';
      if (prefix3 >= 101 && prefix3 <= 135) region = 'Москва';
      else if (prefix3 >= 190 && prefix3 <= 199) region = 'Санкт-Петербург';
      else if (prefix3 >= 140 && prefix3 <= 143) region = 'Московская область';
      else if (prefix3 >= 344 && prefix3 <= 347) region = 'Ростовская область';
      else if (prefix3 >= 350 && prefix3 <= 353) region = 'Краснодарский край';
      else if (prefix3 >= 400 && prefix3 <= 404) region = 'Волгоградская область';
      else if (prefix3 >= 410 && prefix3 <= 413) region = 'Саратовская область';
      else if (prefix3 >= 420 && prefix3 <= 423) region = 'Татарстан';
      else if (prefix3 >= 443 && prefix3 <= 446) region = 'Самарская область';
      else if (prefix3 >= 450 && prefix3 <= 453) region = 'Башкортостан';
      else if (prefix3 >= 460 && prefix3 <= 462) region = 'Оренбургская область';
      else if (prefix3 >= 603 && prefix3 <= 607) region = 'Нижегородская область';
      else if (prefix3 >= 614 && prefix3 <= 617) region = 'Пермский край';
      else if (prefix3 >= 620 && prefix3 <= 624) region = 'Свердловская область';
      else if (prefix3 >= 625 && prefix3 <= 627) region = 'Тюменская область';
      else if (prefix3 >= 630 && prefix3 <= 632) region = 'Новосибирская область';
      else if (prefix3 >= 644 && prefix3 <= 646) region = 'Омская область';
      else if (prefix3 >= 660 && prefix3 <= 663) region = 'Красноярский край';
      else if (prefix3 >= 664 && prefix3 <= 666) region = 'Иркутская область';
      else if (prefix3 >= 680 && prefix3 <= 682) region = 'Хабаровский край';
      else if (prefix3 >= 690 && prefix3 <= 693) region = 'Приморский край';
      if (region) { _zipOk(region, zipResult); _autoSelectRegion(region); }
      else _zipWarn(_t('zip.region_unknown'), zipResult);

    } else if (detected === 'US') {
      if (!/^\d{5}(-\d{4})?$/.test(v)) { _zipErr(_t('zip.us_format'), zipResult); return; }
      const candidates = _US_ZIP_PREFIX[v[0]] || [];
      if (candidates.length === 1) {
        _zipOk(candidates[0], zipResult);
        _autoSelectRegion(candidates[0]);
      } else if (candidates.length > 1) {
        _zipWarn(`${_t('zip.possible_states')}: ${candidates.slice(0,3).join(', ')}${candidates.length>3?'...':''}`, zipResult);
      } else {
        _zipWarn(_t('zip.state_unknown'), zipResult);
      }

    } else {
      // No country selected — try to hint
      if (/^\d{5}$/.test(v)) _zipWarn(_t('zip.specify_country'), zipResult);
      else if (/^\d{6}$/.test(v)) { countrySel && (countrySel.value='RU'); _populateRegions('RU'); lookupZip(inp); }
    }
  }

  function _zipOk(msg, el_) { el_.innerHTML = `<span style="color:var(--green)">✓ ${msg}</span>`; }
  function _zipErr(msg, el_) { el_.innerHTML = `<span style="color:var(--red)">✗ ${msg}</span>`; }
  function _zipWarn(msg, el_) { el_.innerHTML = `<span style="color:var(--gold)">${msg}</span>`; }

  function _autoSelectRegion(region) {
    const sel = el('inp-region'); if (!sel || !region) return;
    const opt = [...sel.options].find(o => o.value === region);
    if (opt) sel.value = region;
  }

  // Quick amount buttons
  function setDepAmt(n) {
    const inp = el('inp-dep'); if (!inp) return;
    inp.value = n;
    document.querySelectorAll('.pay-qa').forEach(b => b.classList.remove('on'));
    document.querySelectorAll('.pay-qa').forEach(b => {
      if (parseInt(b.textContent.replace(/\s/g,'')) === n) b.classList.add('on');
    });
    updDepSummary();
  }

  function updDepSummary() {
    const v = parseFloat((el('inp-dep')?.value||'').replace(',','.')) || 0;
    const sym = S.user ? csym(S.user.currency) : '₴';
    const fee = v > 0 ? parseFloat((v * 0.0).toFixed(2)) : 0; // 0% fee
    const total = v + fee;
    const feeEl = el('pay-fee-val'); if (feeEl) feeEl.textContent = fee.toFixed(2) + ' ' + sym;
    const totEl = el('pay-total-val'); if (totEl) totEl.textContent = v > 0 ? total.toFixed(2) + ' ' + sym : '— ' + sym;
    // Update currency symbol
    const symEl = el('pay-cur-sym'); if (symEl) symEl.textContent = sym;
    // Deselect quick amount if typed manually
  }

  function luhn(n){const d=n.replace(/\D/g,'');let s=0,alt=false;for(let i=d.length-1;i>=0;i--){let dg=parseInt(d[i]);if(alt){dg*=2;if(dg>9)dg-=9;}s+=dg;alt=!alt;}return s%10===0&&d.length>=12;}
  function detectCardType(n){const d=n.replace(/\D/g,'');if(/^4/.test(d))return'VISA';if(/^5[1-5]/.test(d)||/^2[2-7]/.test(d))return'MC';if(/^3[47]/.test(d))return'AMEX';if(/^6/.test(d))return'MIR';return'CARD';}
  function validExp(v){const m=v.match(/^(\d{2})\/(\d{2})$/);if(!m)return false;const mn=parseInt(m[1]),yr=2000+parseInt(m[2]),now=new Date(),nm=now.getMonth()+1,ny=now.getFullYear();return mn>=1&&mn<=12&&(yr>ny||(yr===ny&&mn>=nm));}
  function validCvv(v){return/^\d{3,4}$/.test(v);}

  async function deposit(){
    if(!S.user){modal('auth');return;}
    const num=(el('inp-cnum')?.value||'').replace(/\D/g,''),
          exp=(el('inp-cexp')?.value||''),
          cvv=(el('inp-ccvv')?.value||''),
          amount=parseFloat((el('inp-dep')?.value||'').replace(',','.'));
    if(!num||num.length!==16){toast(_t('payment.enter_card'),'err');return;}
    if(!luhn(num)){toast(_t('payment.card_invalid_luhn'),'err');return;}
    if(!validExp(exp)){toast(_t('payment.check_expiry'),'err');return;}
    if(!validCvv(cvv)){toast(_t('payment.enter_cvv'),'err');el('inp-ccvv')?.focus();return;}
    if(!amount||amount<=0){toast(_t('payment.enter_amount'),'err');return;}
    const btn=el('pay-submit-btn');
    if(btn){btn.disabled=true;btn.style.opacity='.65';}
    try{
      const currency=S.user.currency||'UAH';
      // UAH — через LiqPay (реальный провайдер), остальные — прямое зачисление (тест)
      const useProvider=['UAH'].includes(currency);
      const body=useProvider
        ?{amount,currency,provider:'liqpay',meta:{card:num}}
        :{amount,currency};
      const r=await api('/api/balance/deposit','POST',body);
      if(btn){btn.disabled=false;btn.style.opacity='';}
      if(r.error){toast(r.error,'err');return;}
      if(r.payment_url){
        window.open(r.payment_url,'_blank','noopener,noreferrer');
        const redir=_t('payment.redirecting_to')||'→';
        toast(`${redir} LiqPay...`,'inf');
        return;
      }
      if(r.balance!=null){
        S.user.balance=r.balance;const sym=csym(S.user.currency);
        txt('tb-bal-num',r.balance.toFixed(2));txt('tb-bal-sym',sym);
        txt('bal-big-num',r.balance.toFixed(2));txt('bal-big-sym',sym);
        updDepSummary();
      }
      toast(`${_t('payment.deposited')}: ${amount} ${csym(S.user.currency)}`,'ok');
    }catch{
      if(btn){btn.disabled=false;btn.style.opacity='';}
      toast(_t('toasts.error_generic'),'err');
    }
  }

  async function withdraw(){
    if(!S.user){modal('auth');return;}
    const amount=parseFloat((el('inp-dep')?.value||'').replace(',','.'));
    if(!amount||amount<=0){toast(_t('payment.enter_amount'),'err');return;}
    const currency=S.user.currency||'UAH';
    const cardNum=(el('inp-cnum')?.value||'').replace(/\D/g,'');
    if(!cardNum||cardNum.length!==16){
      toast(_t('payment.withdraw_no_card')||'Введите карту для вывода','err');return;
    }
    const btn=el('pay-withdraw-btn');
    if(btn){btn.disabled=true;btn.style.opacity='.6';}
    try{
      const r=await api('/api/payments/withdraw','POST',{
        provider:'liqpay',amount,currency,meta:{card:cardNum}
      });
      if(btn){btn.disabled=false;btn.style.opacity='';}
      if(r.error){toast(r.error,'err');return;}
      if(r.balance!=null){
        S.user.balance=r.balance;const sym=csym(S.user.currency);
        txt('tb-bal-num',r.balance.toFixed(2));txt('tb-bal-sym',sym);
        txt('bal-big-num',r.balance.toFixed(2));txt('bal-big-sym',sym);
        updDepSummary();
      }
      const note=r.note?` — ${r.note}`:'';
      toast(`✓ ${_t('payment.withdrawn')||'Запрос на вывод принят'}: ${amount} ${csym(S.user.currency)}${note}`,'ok');
      if(el('inp-dep'))el('inp-dep').value='';
    }catch{
      if(btn){btn.disabled=false;btn.style.opacity='';}
      toast(_t('toasts.error_generic'),'err');
    }
  }

  async function altPay(provider){
    if(!S.user){modal('auth');return;}
    const amount=parseFloat((el('inp-dep')?.value||'').replace(',','.'));
    if(!amount||amount<=0){toast(_t('payment.enter_amount'),'err');return;}
    const currency=S.user.currency||'UAH';
    const srcBtn=event?.currentTarget;
    if(srcBtn){srcBtn.disabled=true;srcBtn.style.opacity='.7';}
    try{
      const r=await api('/api/payments/create','POST',{
        provider,amount,currency,meta:{},type:'deposit'
      });
      if(srcBtn){srcBtn.disabled=false;srcBtn.style.opacity='';}
      if(r.error){toast(r.error,'err');return;}
      if(r.payment_url){
        window.open(r.payment_url,'_blank','noopener,noreferrer');
        const name=_t('payment.alt_'+provider)||provider;
        const redir=_t('payment.redirecting_to')||'→';
        toast(`${redir} ${name}...`,'inf');
      } else {
        // Провайдер в test_mode или ещё не настроен — показываем ID заявки
        const created=_t('payment.order_created')||'Заявка создана';
        const idLabel=_t('payment.order_id')||'ID';
        const txId=r.provider_tx_id||r.order_id||'—';
        toast(`${created} · ${idLabel}: ${txId}`,'inf');
      }
    }catch{
      if(srcBtn){srcBtn.disabled=false;srcBtn.style.opacity='';}
      toast(_t('toasts.error_generic'),'err');
    }
  }


  // ═══════════════════════════════════════════════════════════════════════════
  //  PAYMENT SYSTEM v3 — grouped provider list, card form, redirect panel
  // ═══════════════════════════════════════════════════════════════════════════

  // ── Provider brand logos (inline SVG) ─────────────────────────────────────
  // ── Built-in brand logos (full-bleed SVG, ratio 16:9) ─────────────────────
  // If provider has logo_url set — that image is used instead (fills card like btm-prov-card)
  const _PAY_LOGOS = {
    liqpay: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="lq" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#FF6600"/><stop offset="1" stop-color="#FF3300"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#lq)"/>
      <circle cx="26" cy="45" r="18" fill="rgba(255,255,255,.12)"/>
      <circle cx="134" cy="45" r="24" fill="rgba(255,255,255,.07)"/>
      <text x="80" y="52" font-size="22" font-weight="900" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif" letter-spacing="1">LiqPay</text>
    </svg>`,
    wayforpay: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="wfp" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0B2E6F"/><stop offset="1" stop-color="#1a4fa8"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#wfp)"/>
      <rect x="0" y="0" width="6" height="90" fill="#F5A623"/>
      <text x="88" y="49" font-size="17" font-weight="800" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif">WayForPay</text>
      <text x="88" y="66" font-size="9" fill="rgba(255,255,255,.5)" text-anchor="middle" font-family="Arial,sans-serif">PAYMENT GATEWAY</text>
    </svg>`,
    fondy: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="fnd" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d0d0d"/><stop offset="1" stop-color="#1a1a2e"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#fnd)"/>
      <circle cx="20" cy="20" r="30" fill="rgba(0,200,100,.08)"/>
      <text x="80" y="52" font-size="26" font-weight="900" fill="#00c864" text-anchor="middle" font-family="Arial,sans-serif">Fondy</text>
    </svg>`,
    paypal: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="pp" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#003087"/><stop offset="1" stop-color="#005ea6"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#pp)"/>
      <circle cx="145" cy="10" r="40" fill="rgba(0,156,222,.18)"/>
      <text x="80" y="44" font-size="19" font-weight="900" fill="#009cde" text-anchor="middle" font-family="Arial,sans-serif">Pay</text>
      <text x="80" y="63" font-size="19" font-weight="900" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif">Pal</text>
    </svg>`,
    stripe: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="str" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#635BFF"/><stop offset="1" stop-color="#7c74ff"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#str)"/>
      <rect x="0" y="55" width="160" height="35" fill="rgba(0,0,0,.2)"/>
      <text x="80" y="50" font-size="28" font-weight="900" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif" letter-spacing="-1">stripe</text>
    </svg>`,
    googlepay: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <rect width="160" height="90" fill="#fff"/>
      <rect width="160" height="90" fill="rgba(0,0,0,.03)"/>
      <text x="47" y="55" font-size="26" font-weight="500" fill="#4285F4" text-anchor="middle" font-family="Arial,sans-serif">G</text>
      <text x="100" y="55" font-size="22" font-weight="500" fill="#5F6368" text-anchor="middle" font-family="Arial,sans-serif">Pay</text>
    </svg>`,
    applepay: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <rect width="160" height="90" fill="#000"/>
      <text x="60" y="58" font-size="30" fill="#fff" text-anchor="middle" font-family="-apple-system,Arial">&#xf8ff;</text>
      <text x="107" y="56" font-size="22" font-weight="400" fill="#fff" text-anchor="middle" font-family="-apple-system,Arial,sans-serif">Pay</text>
    </svg>`,
    crypto: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="btc" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#F7931A"/><stop offset="1" stop-color="#e07b10"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#btc)"/>
      <circle cx="80" cy="45" r="35" fill="rgba(255,255,255,.1)"/>
      <text x="80" y="57" font-size="34" font-weight="900" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif">₿</text>
    </svg>`,
    p2p: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="p2p" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#16a34a"/><stop offset="1" stop-color="#15803d"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#p2p)"/>
      <rect x="20" y="28" width="120" height="34" rx="6" fill="rgba(255,255,255,.15)" stroke="rgba(255,255,255,.3)" stroke-width="1"/>
      <circle cx="40" cy="45" r="9" fill="rgba(255,255,255,.4)"/>
      <rect x="56" y="39" width="50" height="5" rx="2" fill="rgba(255,255,255,.35)"/>
      <rect x="56" y="48" width="30" height="4" rx="2" fill="rgba(255,255,255,.2)"/>
      <text x="80" y="78" font-size="9" font-weight="600" fill="rgba(255,255,255,.6)" text-anchor="middle" font-family="Arial,sans-serif">P2P TRANSFER</text>
    </svg>`,
    bank_transfer: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="bnk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1e3a5f"/><stop offset="1" stop-color="#162d4a"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#bnk)"/>
      <polygon points="80,15 130,35 30,35" fill="rgba(255,255,255,.15)"/>
      <rect x="38" y="35" width="12" height="24" fill="rgba(255,255,255,.12)"/>
      <rect x="58" y="35" width="12" height="24" fill="rgba(255,255,255,.12)"/>
      <rect x="90" y="35" width="12" height="24" fill="rgba(255,255,255,.12)"/>
      <rect x="110" y="35" width="12" height="24" fill="rgba(255,255,255,.12)"/>
      <rect x="30" y="59" width="100" height="5" rx="1" fill="rgba(255,255,255,.2)"/>
      <text x="80" y="78" font-size="9" font-weight="600" fill="rgba(255,255,255,.5)" text-anchor="middle" font-family="Arial,sans-serif">BANK TRANSFER</text>
    </svg>`,
    yookassa: `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg">
      <defs><linearGradient id="yoo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6600cc"/><stop offset="1" stop-color="#8B00FF"/></linearGradient></defs>
      <rect width="160" height="90" fill="url(#yoo)"/>
      <circle cx="130" cy="20" r="40" fill="rgba(255,255,255,.08)"/>
      <text x="80" y="52" font-size="20" font-weight="900" fill="#fff" text-anchor="middle" font-family="Arial,sans-serif">ЮKassa</text>
    </svg>`,
  };

  // ── Category config (icons = inline SVG, no emoji) ─────────────────────────
  function _payCatIconSvg(catId) {
    const ns = 'xmlns="http://www.w3.org/2000/svg"';
    const o = { w: 18, h: 18, vb: '0 0 24 24' };
    const base = `<svg class="pay-cat-svg" width="${o.w}" height="${o.h}" viewBox="${o.vb}" fill="none" ${ns} aria-hidden="true">`;
    const icons = {
      popular: `${base}<path d="M12 3.5l1.9 5.9h6.2l-5 3.6 1.9 5.9L12 15.3 6.9 18.9l1.9-5.9-5-3.6h6.2L12 3.5z" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/></svg>`,
      card: `${base}<rect x="3" y="6" width="18" height="12" rx="2" stroke="currentColor" stroke-width="1.35"/><path d="M3 10.5h18" stroke="currentColor" stroke-width="1.25"/></svg>`,
      ewallet: `${base}<path d="M4 8a2 2 0 012-2h11a2 2 0 012 2v2H4V8z" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/><path d="M4 10h16v6a2 2 0 01-2 2H6a2 2 0 01-2-2v-6z" stroke="currentColor" stroke-width="1.35"/><path d="M8 14h4" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg>`,
      crypto: `${base}<circle cx="9" cy="9" r="3.25" stroke="currentColor" stroke-width="1.35"/><circle cx="15" cy="15" r="3.25" stroke="currentColor" stroke-width="1.35"/><path d="M11.2 11.2l5.6 5.6M16.8 11.2l-5.6 5.6" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>`,
    };
    return icons[catId] || icons.popular;
  }
  const _PAY_ARR_SVG = `<svg class="pay-group-arr-svg" width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  const _PAY_CATS = [
    { id:'popular',  types:null,                     i18n:'payment.cat_popular' },
    { id:'card',     types:['card'],                 i18n:'payment.cat_card' },
    { id:'ewallet',  types:['ewallet','online'],     i18n:'payment.cat_ewallet' },
    { id:'crypto',   types:['crypto','p2p','bank'],  i18n:'payment.cat_other' },
  ];
  function _isPayMobileAccordion(){ return window.innerWidth <= 820; }

  // ── Load config ───────────────────────────────────────────────────────────
  async function _payLoadConfig() {
    if (S.payConfig) return S.payConfig;
    try {
      const r = await api('/api/payments/config', 'GET');
      S.payConfig = r;
      S.payProviders = r.systems || [];
      return r;
    } catch { return null; }
  }

  // ── Init (called when balance view opens) ─────────────────────────────────
  async function _payInit() {
    await _payLoadConfig();
    _payRenderModeTabs();
    _payRenderGroups();
    _payShowPanel('providers');
    _payUpdateRightLabels();
  }

  // ── Mode: Deposit / Withdraw ──────────────────────────────────────────────
  function paySetMode(mode) {
    S.payMode = mode;
    S.payProviderId = null;
    _payRenderModeTabs();
    _payRenderGroups();
    _payShowPanel('providers');
    _payUpdateRightLabels();
  }

  function _payRenderModeTabs() {
    const isDep = S.payMode !== 'withdraw';
    el('pay-tab-dep')?.classList.toggle('active', isDep);
    el('pay-tab-wd')?.classList.toggle('active', !isDep);
    const depLbl = el('pay-tab-dep-lbl'); if (depLbl) depLbl.textContent = _t('payment.deposit_btn') || 'Deposit';
    const wdLbl  = el('pay-tab-wd-lbl');  if (wdLbl)  wdLbl.textContent  = _t('payment.withdraw_btn') || 'Withdraw';
  }

  // ── Render grouped provider list ──────────────────────────────────────────
  function _payRenderGroups() {
    const panel = el('pay-providers-panel');
    if (!panel) return;
    const isDep  = S.payMode !== 'withdraw';
    const allP   = S.payProviders || [];
    const isMobileAccordion = _isPayMobileAccordion();
    const openCat = S.payMobileOpenCat || 'popular';

    let html = '';
    _PAY_CATS.forEach(cat => {
      // Filter providers for this category
      let provs = allP.filter(p => {
        if (cat.types === null) return p.popular; // "popular"
        return cat.types.includes(p.type);
      });
      // For withdraw: hide ewallet types that don't support payout
      if (!isDep) {
        provs = provs.filter(p => ['card','p2p','bank'].includes(p.type) || p.id === 'bank_transfer');
      }
      if (!provs.length) return;

      const catLabel = _t(cat.i18n) || cat.i18n;
      const isOpen = isMobileAccordion && openCat === cat.id;
      html += `<div class="pay-group ${isMobileAccordion ? 'pay-group-mobile' : ''} ${isOpen ? 'is-open' : ''}" data-cat="${cat.id}">
        <button class="pay-group-trigger" type="button" ${isMobileAccordion ? `onclick="V.payToggleGroup('${cat.id}')"` : ''} ${isMobileAccordion ? '' : 'tabindex="-1" aria-hidden="true"'}>
          <span class="pay-group-trigger-left">
            <span class="pay-group-ico">${_payCatIconSvg(cat.id)}</span>
            <span class="pay-group-label">${catLabel}</span>
          </span>
          <span class="pay-group-arr">${_PAY_ARR_SVG}</span>
        </button>
        <div class="pay-group-head">
          <span class="pay-group-label">${catLabel}</span>
          <span class="pay-group-line"></span>
        </div>
        <div class="pay-group-body" style="${isMobileAccordion ? `max-height:${isOpen ? '520px' : '0px'}` : ''}">
          <div class="pay-group-grid">`;

      provs.forEach(p => {
        const avail = p.available;
        const soon  = _t('payment.coming_soon') || 'Soon';

        // Logo: use logo_url image if set, otherwise built-in SVG
        let logoInner;
        if (p.logo_url) {
          logoInner = `<img class="pprov-img" src="${p.logo_url}" alt="${p.label}" onerror="this.style.display='none';this.nextSibling.style.display='flex'">
            <div class="pprov-fallback" style="display:none">${p.label.slice(0,4).toUpperCase()}</div>`;
        } else {
          const svg = _PAY_LOGOS[p.id] || `<svg viewBox="0 0 160 90" xmlns="http://www.w3.org/2000/svg"><rect width="160" height="90" fill="#1a1a2e"/><text x="80" y="52" font-size="16" font-weight="700" fill="#a78bfa" text-anchor="middle" font-family="Arial">${p.label.slice(0,8)}</text></svg>`;
          logoInner = `<div class="pprov-svg">${svg}</div>`;
        }

        html += `<button
          class="pprov-card${!avail ? ' pprov-unavail' : ''}"
          onclick="${avail ? `V.paySelectProvider('${p.id}')` : ''}"
          ${!avail ? `disabled title="${soon}"` : ''}
          aria-label="${p.label}">
          <div class="pprov-media">${logoInner}</div>
          <div class="pprov-overlay"></div>
          <div class="pprov-footer">
            <span class="pprov-name">${p.label}</span>
            ${!avail ? `<span class="pprov-stub">${soon}</span>` : ''}
          </div>
        </button>`;
      });

      html += `</div></div></div>`;
    });

    panel.innerHTML = html || `<div class="pay-empty-msg">${_t('payment.no_providers') || 'No payment methods available'}</div>`;
  }
  function payToggleGroup(catId){
    if (!_isPayMobileAccordion()) return;
    S.payMobileOpenCat = (S.payMobileOpenCat === catId) ? '' : catId;
    document.querySelectorAll('.pay-group-mobile').forEach(gr=>{
      const isOpen = gr.dataset.cat === S.payMobileOpenCat;
      gr.classList.toggle('is-open', isOpen);
      const body = gr.querySelector('.pay-group-body');
      if (body) body.style.maxHeight = isOpen ? '520px' : '0px';
    });
  }

  // ── Select provider ───────────────────────────────────────────────────────
  function paySelectProvider(id) {
    const prov = (S.payProviders || []).find(p => p.id === id);
    if (!prov || !prov.available) return;
    S.payProviderId = id;
    // Mark selected card visually
    document.querySelectorAll('.pprov-card').forEach(b => b.classList.remove('selected'));
    const cards = document.querySelectorAll('.pprov-card');
    cards.forEach(b => {
      const name = b.querySelector('.pprov-name')?.textContent;
      if (name === prov.label) b.classList.add('selected');
    });
    const isCard   = prov.type === 'card';
    const isOnline = prov.type === 'online';

    if (isCard) {
      // Show card entry form
      _payShowChosenBadge(prov);
      _payShowPanel('card');
    } else if (isOnline) {
      // Online payment — amount already in right panel, go straight to checkout
      _payShowPanel('redir');
      _payRenderRedir(prov);
    } else {
      // ewallet / crypto / p2p / bank — show info + redirect panel
      _payShowPanel('redir');
      _payRenderRedir(prov);
    }
  }

  function payBack() {
    S.payProviderId = null;
    document.querySelectorAll('.pprov-card').forEach(b => b.classList.remove('selected'));
    _payShowPanel('providers');
  }

  function _payShowPanel(which) {
    const provPanel = el('pay-providers-panel');
    const cardPanel = el('pay-card-panel');
    const redirPanel = el('pay-redir-panel');
    if (provPanel)  provPanel.style.display = which === 'providers' ? '' : 'none';
    if (cardPanel)  cardPanel.style.display  = which === 'card'      ? '' : 'none';
    if (redirPanel) redirPanel.style.display = which === 'redir'     ? '' : 'none';
  }

  function _payShowChosenBadge(prov) {
    const el_ = el('pay-chosen-prov');
    if (!el_) return;
    const logoHtml = prov.logo_url
      ? `<img src="${prov.logo_url}" style="height:22px;width:auto;object-fit:contain" alt="${prov.label}" onerror="this.style.display='none'">`
      : (_PAY_LOGOS[prov.id] ? `<div style="height:22px;display:flex;align-items:center">${_PAY_LOGOS[prov.id].replace('viewBox="0 0 160 90"','viewBox="0 0 160 90" style="height:22px;width:auto"')}</div>` : '');
    el_.innerHTML = `<div class="pprov-chosen-badge"><div class="pprov-chosen-logo">${logoHtml}</div><span>${prov.label}</span></div>`;
    // i18n card labels
    const lbl = (id, key, fb) => { const e=el(id); if(e) e.textContent=_t(key)||fb; };
    lbl('pay-lbl-cardnum-txt','payment.card_number','Card number');
    lbl('pay-lbl-exp-txt','payment.expiry','Expiry');
    lbl('pay-lbl-holder-txt','payment.holder','Holder');
    lbl('pay-lbl-cvv-txt','payment.cvv','CVV / CVC');
    lbl('pay-back-lbl','payment.back','Back');
  }

  // ── Redirect / instructions panel ─────────────────────────────────────────
  function _payRenderRedir(prov) {
    const isDep = S.payMode !== 'withdraw';
    const cur   = S.user?.currency || 'UAH';
    const logoEl    = el('pay-redir-logo');
    const nameEl    = el('pay-redir-name');
    const descEl    = el('pay-redir-desc');
    const detailsEl = el('pay-redir-details');
    const warnEl    = el('pay-redir-warn');
    const backLbl   = el('pay-redir-back-lbl');

    if (backLbl)  backLbl.textContent  = _t('payment.back') || 'Back';
    if (logoEl) {
      if (prov.logo_url) {
        logoEl.innerHTML = `<img src="${prov.logo_url}" style="height:36px;width:auto;max-width:100px;object-fit:contain" alt="${prov.label}">`;
      } else {
        const svg = _PAY_LOGOS[prov.id] || '';
        logoEl.innerHTML = svg ? `<div style="height:36px;display:flex;align-items:center">${svg.replace('viewBox="0 0 160 90"','viewBox="0 0 160 90" style="height:36px;width:auto"')}</div>` : '';
      }
    }
    if (nameEl)   nameEl.textContent   = prov.label;

    // Description
    const isOnlineType = prov.type === 'online' || prov.type === 'ewallet';
    const descKey = isOnlineType
      ? (isDep ? 'payment.redir_desc_online_dep' : 'payment.redir_desc_online_wd')
      : (isDep ? `payment.redir_desc_${prov.type}_dep` : `payment.redir_desc_${prov.type}_wd`);
    const descFb  = isDep ? _t('payment.redir_desc_online_dep') || 'Enter the amount and click Deposit — you will be redirected to the payment page' : 'Withdrawal request will be processed';
    if (descEl)   descEl.textContent = _t(descKey) || descFb;

    // Details (P2P card / bank IBAN)
    let dHtml = '';
    if (prov.type === 'p2p' && isDep) {
      if (prov.p2p_card) dHtml += _payDetailRow(_t('payment.p2p_card')||'Card', prov.p2p_card);
      if (prov.p2p_holder) dHtml += _payDetailRow(_t('payment.p2p_holder')||'Holder', prov.p2p_holder);
      if (prov.p2p_bank)   dHtml += _payDetailRow(_t('payment.p2p_bank')||'Bank', prov.p2p_bank);
    }
    if (prov.type === 'bank') {
      if (prov.bank_iban)       dHtml += _payDetailRow('IBAN', prov.bank_iban);
      if (prov.bank_swift)      dHtml += _payDetailRow('SWIFT', prov.bank_swift);
      if (prov.bank_beneficiary) dHtml += _payDetailRow(_t('payment.bank_beneficiary')||'Beneficiary', prov.bank_beneficiary);
      if (isDep) dHtml += `<div class="pay-redir-eta">${_t('payment.bank_eta')||'1–3 business days'}</div>`;
    }
    if (detailsEl) detailsEl.innerHTML = dHtml ? `<div class="pay-redir-detail-box">${dHtml}</div>` : '';

    // Currency warning
    const curOk = !prov.supported_currencies?.length || prov.supported_currencies.includes(cur);
    if (warnEl) {
      warnEl.innerHTML = !curOk
        ? `<div class="pay-cur-warn">⚠️ ${_t('payment.currency_not_supported')||'Currency not supported'}: ${cur}. ${_t('payment.supported')||'Available'}: ${prov.supported_currencies.join(', ')}</div>`
        : '';
    }
  }

  function _payDetailRow(label, value) {
    return `<div class="pay-redir-row"><span>${label}</span><strong>${value}</strong></div>`;
  }

  // ── Right panel labels ─────────────────────────────────────────────────────
  function _payUpdateRightLabels() {
    const isDep = S.payMode !== 'withdraw';
    const lbl = (id, key, fb) => { const e=el(id); if(e) e.textContent=_t(key)||fb; };
    lbl('pay-label-balance', 'payment.balance',      'Current Balance');
    lbl('pay-label-amount',  'payment.amount_label', 'Amount');
    lbl('pay-fee-label',     'payment.fee',          'Fee');
    lbl('pay-total-label',   isDep ? 'payment.total' : 'payment.total_withdraw', 'Total');
    lbl('pay-ssl-label',     'payment.ssl',          'SSL');
    lbl('pay-policy-link',   'payment.policy_link',  'Deposit Policy');
    lbl('pay-hist-title',    'payment.history_title','History');
    const submitLbl = el('pay-submit-lbl');
    if (submitLbl) submitLbl.textContent = isDep ? (_t('payment.deposit_btn')||'Deposit') : (_t('payment.withdraw_btn')||'Withdraw');
    const submitBtn = el('pay-submit-btn');
    if (submitBtn) {
      submitBtn.style.background = isDep
        ? 'linear-gradient(135deg,#6c3ff5,#00c8ff)'
        : 'linear-gradient(135deg,#f97316,#ef4444)';
    }
    updDepSummary();
    _payLoadHistory();
  }

  // ── Mini transaction history ───────────────────────────────────────────────
  async function _payLoadHistory() {
    const list = el('pay-history-list');
    if (!list || !S.user) return;
    try {
      const r = await api('/api/transactions', 'GET');
      const txs = (r.transactions || []).slice(0, 5);
      if (!txs.length) { list.innerHTML = `<div class="pay-hist-empty">${_t('payment.no_history')||'No transactions yet'}</div>`; return; }
      list.innerHTML = txs.map(tx => {
        const isIn = tx.type === 'deposit' || tx.type === 'win';
        const sym  = S.user ? csym(S.user.currency) : '';
        const amtFmt = (isIn ? '+' : '–') + parseFloat(tx.amount||0).toFixed(2) + ' ' + sym;
        const d = tx.created_at ? new Date(tx.created_at).toLocaleDateString() : '—';
        return `<div class="pay-hist-row">
          <div class="pay-hist-type${isIn ? ' in' : ' out'}">${isIn ? '↓' : '↑'}</div>
          <div class="pay-hist-meta"><span>${tx.type||'—'}</span><span class="pay-hist-date">${d}</span></div>
          <div class="pay-hist-amt${isIn ? ' in' : ' out'}">${amtFmt}</div>
        </div>`;
      }).join('');
    } catch { /* silent */ }
  }

  // ── paySubmit ─────────────────────────────────────────────────────────────
  async function paySubmit() {
    if (!S.user) { modal('auth'); return; }
    const prov = (S.payProviders||[]).find(p => p.id === S.payProviderId);
    if (!prov) { toast(_t('payment.select_method')||'Select a payment method', 'err'); return; }
    const amount = parseFloat((el('inp-dep')?.value||'').replace(',','.'));
    if (!amount || amount <= 0) { toast(_t('payment.enter_amount'), 'err'); return; }
    const isDep    = S.payMode !== 'withdraw';
    const currency = S.user.currency || 'UAH';
    const btn      = el('pay-submit-btn');
    if (btn) { btn.disabled = true; btn.style.opacity = '.65'; }
    const _done = () => { if (btn) { btn.disabled = false; btn.style.opacity = ''; } };

    if (prov.type === 'card') {
      // Requires full card details
      const num = (el('inp-cnum')?.value||'').replace(/\D/g,'');
      const exp = el('inp-cexp')?.value||'';
      const cvv = el('inp-ccvv')?.value||'';
      if (!num||num.length!==16) { toast(_t('payment.enter_card'),'err'); _done(); return; }
      if (!luhn(num))             { toast(_t('payment.card_invalid_luhn'),'err'); _done(); return; }
      if (!validExp(exp))         { toast(_t('payment.check_expiry'),'err'); _done(); return; }
      if (!validCvv(cvv))         { toast(_t('payment.enter_cvv'),'err'); el('inp-ccvv')?.focus(); _done(); return; }
      isDep
        ? await _payDoDeposit(prov.id, amount, currency, {card:num}, _done)
        : await _payDoWithdraw(prov.id, amount, currency, {card:num}, _done);
    } else if (prov.type === 'online') {
      // Online payment — amount from right panel, redirect to payment URL immediately
      isDep
        ? await _payDoDeposit(prov.id, amount, currency, {}, _done)
        : await _payDoWithdraw(prov.id, amount, currency, {}, _done);
    } else {
      // ewallet / crypto / p2p / bank
      isDep
        ? await _payDoDeposit(prov.id, amount, currency, {}, _done)
        : await _payDoWithdraw(prov.id, amount, currency, {}, _done);
    }
  }

  async function _payDoDeposit(pid, amount, currency, meta, _done) {
    try {
      const r = await api('/api/payments/create','POST',{provider:pid,amount,currency,meta});
      _done();
      if (r.error) { toast(r.error,'err'); return; }
      if (r.payment_url) {
        window.open(r.payment_url,'_blank','noopener,noreferrer');
        toast(`${_t('payment.redirecting_to')||'→'} ${_t('payment.alt_'+pid)||pid}...`,'inf');
        return;
      }
      const txId = r.provider_tx_id || r.order_id || '—';
      toast(`${_t('payment.order_created')||'Order created'} · ID: ${txId}`,'inf');
      if (r.balance != null) _payUpdateBalance(r.balance, currency);
      _payLoadHistory();
    } catch { _done(); toast(_t('toasts.error_generic'),'err'); }
  }

  async function _payDoWithdraw(pid, amount, currency, meta, _done) {
    try {
      const r = await api('/api/payments/withdraw','POST',{provider:pid,amount,currency,meta});
      _done();
      if (r.error) { toast(r.error,'err'); return; }
      if (r.balance != null) _payUpdateBalance(r.balance, currency);
      toast(`✓ ${_t('payment.withdrawn')||'Withdrawal accepted'}: ${amount} ${csym(currency)}${r.note?' — '+r.note:''}`,'ok');
      if (el('inp-dep')) el('inp-dep').value = '';
      updDepSummary(); _payLoadHistory();
    } catch { _done(); toast(_t('toasts.error_generic'),'err'); }
  }

  function _payUpdateBalance(balance, currency) {
    if (!S.user) return;
    S.user.balance = balance;
    const sym = csym(currency);
    txt('tb-bal-num', balance.toFixed(2)); txt('tb-bal-sym', sym);
    txt('bal-big-num', balance.toFixed(2)); txt('bal-big-sym', sym);
    updDepSummary();
  }



  // ── PROMO / SUPPORT ──────────────────────────────────────────────────────────
  async function promo(){
    if(!S.user){closeM();modal('auth');return;}
    const code=el('promo-inp').value.trim().toUpperCase();
    txt('promo-err','');txt('promo-ok','');
    if(!code){txt('promo-err',_t('validation.enter_promo'));return;}
    try{const r=await api('/api/promo/redeem','POST',{code});
      if(r.error){txt('promo-err',r.error);return;}
      S.user.balance=r.balance;const sym=csym(S.user.currency);
      txt('tb-bal-num',r.balance.toFixed(2));txt('tb-bal-sym',sym);
      txt('promo-ok',`🎉 +${r.bonus} ${sym}`);el('promo-inp').value='';}
    catch{txt('promo-err',_t('validation.error'));}
  }
  let _supFiles = [];

  function supFilesChanged(inp) {
    Array.from(inp.files).forEach(f => {
      if (_supFiles.length >= 4) return;
      if (!_supFiles.some(x => x.name === f.name && x.size === f.size)) _supFiles.push(f);
    });
    inp.value = '';
    const list = el('sup-files-list');
    if (!list) return;
    list.innerHTML = '';
    const area = el('sup-upload-area');
    _supFiles.forEach((f, i) => {
      const div = document.createElement('div');
      div.className = 'sup-file-item';
      const nm = document.createElement('span'); nm.className = 'sup-file-name'; nm.textContent = f.name;
      const sz = document.createElement('span'); sz.className = 'sup-file-size'; sz.textContent = (f.size/1024).toFixed(0)+'KB';
      const rm = document.createElement('button'); rm.className = 'sup-file-rm'; rm.textContent = '×';
      rm.onclick = () => { _supFiles.splice(i,1); supFilesChanged({files:[],value:''}); };
      div.appendChild(nm); div.appendChild(sz); div.appendChild(rm);
      list.appendChild(div);
    });
    if (area) area.style.display = _supFiles.length >= 4 ? 'none' : '';
  }

  async function ticket(){
    const c=el('sup-contact').value.trim(),s=el('sup-subj').value.trim(),d=el('sup-desc').value.trim();
    txt('sup-err','');txt('sup-ok','');
    if(!s||!d){txt('sup-err',_t('validation.fill_fields'));return;}
    const btn=el('sup-send-btn');if(btn){btn.disabled=true;btn.textContent='...';}
    try{
      const fd=new FormData();
      fd.append('contact',c);fd.append('subject',s);fd.append('description',d);
      _supFiles.forEach(f=>fd.append('uploads',f));
      const resp=await _secureFetch('/api/support/ticket',{method:'POST',body:fd,credentials:'include'});
      const r=await resp.json();
      if(r.error){txt('sup-err',r.error);return;}
      txt('sup-ok',r.message||_t('support.sent_ok'));
      ['sup-contact','sup-subj','sup-desc'].forEach(id=>{const e=el(id);if(e)e.value='';});
      _supFiles=[];
      const fl=el('sup-files-list');if(fl)fl.innerHTML='';
      const ar=el('sup-upload-area');if(ar)ar.style.display='';
    }catch(e){txt('sup-err',_t('validation.error'));}
    finally{if(btn){btn.disabled=false;btn.textContent=_t('modals.support_send')||'Отправить';}}
  }

  // ── FAQ / LICENSES ───────────────────────────────────────────────────────────
  async function loadFAQ(){try{const r=await api('/api/config/faq');el('faq-list').innerHTML=(r.faq||[]).map((f,i)=>`<div class="faq-item" id="faq-${i}"><div class="faq-q" onclick="V.togFAQ(${i})"><span>${f.question}</span><span class="faq-arr">▸</span></div><div class="faq-a"><div class="faq-a-in">${f.answer}</div></div></div>`).join('');}catch{}}
  function togFAQ(i){el('faq-'+i)?.classList.toggle('on');}
  function fillLicenses(){if(!S.cfg?.licenses_text)return;const l=S.cfg.licenses_text;if(el('lic-games'))el('lic-games').textContent=l.games||'';if(el('lic-dep'))el('lic-dep').textContent=l.deposit||'';if(el('lic-cas'))el('lic-cas').textContent=l.casino||'';}
  function licTab(btn,pid){document.querySelectorAll('.ltab').forEach(b=>b.classList.remove('on'));btn.classList.add('on');document.querySelectorAll('.lic-p').forEach(p=>p.classList.remove('on'));el(pid)?.classList.add('on');}
  function toggleDet(){S.detOpen=!S.detOpen;el('sb-det-sub').classList.toggle('open',S.detOpen);el('sb-det-arr').classList.toggle('open',S.detOpen);}

  // ── MODALS ───────────────────────────────────────────────────────────────────
  function modal(name){
    el('overlay').classList.add('on');
    if(name==='add-fav') buildFavModal();
    if(name==='faq') loadFAQ();
    if(name==='avatar') _openAvatarModal();
    if(name==='support'){txt('sup-err','');txt('sup-ok','');if(S.user)el('sup-contact').value=S.user.email||S.user.phone||'';}
    if(name==='dep-policy'&&S.cfg?.deposit_policy_text) el('dep-pol-text').textContent=S.cfg.deposit_policy_text;
    if(name==='verify') _kycInit();
    if(name==='policy'){requestAnimationFrame(()=>{const box=el('pol-text');if(box&&box.scrollHeight<=box.clientHeight+8){const btn=el('pol-btn');if(btn)btn.disabled=false;const hint=el('pol-hint');if(hint)hint.classList.add('hidden');}});}
    const m=el('modal-'+name);if(m)m.classList.add('on');
  }
  function closeM(){
    el('overlay').classList.remove('on');
    document.querySelectorAll('.modal').forEach(m=>m.classList.remove('on'));
    const v=el('bni-video');if(v){v.pause();}
  }
  function toggleDD(){el('tb-dd')?.classList.toggle('open');}
  function closeDD(){el('tb-dd')?.classList.remove('open');}
  function outsideClick(e){
    // dropdown профиля
    const p=el('tb-profile'); if(p&&!p.contains(e.target)) closeDD();
    // lang menu — ищем КАЖДЫЙ РАЗ свежий элемент (innerHTML пересоздаётся)
    const lw=el('sb-lang-wrap');
    if (lw && !lw.contains(e.target)) {
      // закрываем только если меню реально открыто
      const menu=el('sb-lang-menu');
      if (menu && menu.classList.contains('open')) toggleLangMenu(true);
    }
  }
  function buildPhoneCodes(){(S.cfg?.phone_codes||[]).forEach(c=>{['l-pcode','r-pcode'].forEach(id=>{const s=el(id);if(s)s.innerHTML+=`<option value="${c.code}">${c.flag} ${c.code}</option>`;});});}
  function socialBtns(l){const b=[];if(l?.telegram)b.push(`<a class="soc soc-tg" href="${l.telegram}" target="_blank">✈️ Telegram</a>`);if(l?.youtube)b.push(`<a class="soc soc-yt" href="${l.youtube}" target="_blank">▶️ YouTube</a>`);if(l?.viber)b.push(`<a class="soc soc-vb" href="${l.viber}" target="_blank">📞 Viber</a>`);return b.join('');}
  function toast(msg,type='inf'){const c=el('toasts');const t=document.createElement('div');t.className='t-t '+type;t.innerHTML=`<span>${{ok:'✅',err:'❌',inf:'ℹ️'}[type]}</span><span>${msg}</span>`;c.appendChild(t);setTimeout(()=>{t.style.opacity='0';t.style.transition='opacity .3s';setTimeout(()=>t.remove(),300)},3200);}
  async function api(url,method='GET',body=null){const o={method,headers:{'Content-Type':'application/json'},credentials:'include'};if(body)o.body=JSON.stringify(body);return(await _secureFetch(url,o)).json();}
  function el(id){return document.getElementById(id);}
  function txt(id,v){const e=el(id);if(e)e.textContent=String(v);}

  // ── AVATAR UPLOAD ──────────────────────────────────────────────────────────
  let _avData = null;
  function _openAvatarModal() {
    _avData = null;
    const err = el('av-err'); if(err) err.textContent='';
    const fn  = el('av-fname'); if(fn) fn.textContent='';
    const btn = el('av-save-btn'); if(btn) btn.disabled=true;
    const prev = el('av-modal-prev');
    if (prev && S.user) {
      if (S.user.icon_url) {
        prev.innerHTML=`<img src="${S.user.icon_url}" draggable="true" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">`;
      } else {
        prev.textContent=(S.user.username||'?')[0].toUpperCase();
      }
    }
  }
  function avFile(inp) {
    const file=inp.files[0]; if(!file) return;
    if(file.size>2_100_000){const e=el('av-err');if(e)e.textContent=_t('settings.avatar_error_size');return;}
    const e=el('av-err');if(e)e.textContent='';
    const fn=el('av-fname');if(fn)fn.textContent='📎 '+file.name;
    const reader=new FileReader();
    reader.onload=ev=>{
      _avData=ev.target.result;
      const prev=el('av-modal-prev');
      if(prev)prev.innerHTML=`<img src="${_avData}" draggable="true" style="width:100%;height:100%;object-fit:cover;border-radius:inherit">`;
      const btn=el('av-save-btn');if(btn)btn.disabled=false;
    };
    reader.readAsDataURL(file);
  }
  function avDrop(e) {
    e.preventDefault();
    const zone=el('av-upload-zone');if(zone)zone.style.borderColor='var(--bdrB)';
    const file=e.dataTransfer?.files?.[0];if(!file)return;
    avFile({files:[file]});
  }
  async function saveAvatar() {
    if(!_avData){const e=el('av-err');if(e)e.textContent=_t('settings.avatar_choose');return;}
    const btn=el('av-save-btn');if(btn){btn.disabled=true;btn.textContent=_t('settings.avatar_saving');}
    try{
      const r=await api('/api/auth/avatar','POST',{icon_url:_avData});
      if(r.user){setUser(r.user);closeM();toast(_t('settings.avatar_updated'),'ok');}
      else{const e=el('av-err');if(e)e.textContent=r.error||_t('settings.avatar_error');if(btn)btn.disabled=false;}
    }catch{const e=el('av-err');if(e)e.textContent=_t('settings.avatar_conn_error');if(btn)btn.disabled=false;}
    if(btn)btn.textContent=_t('settings.avatar_save');
  }

  // ── BOTTOM BAR ───────────────────────────────────────────────────────────────
  let _btmCfg = null;
  let _btmPaySelCat = null; // выбранная категория в модалке платёжек

  async function loadBtmCfg() {
    try { _btmCfg = await api('/api/config/btm'); } catch {}
  }

  function buildBtmBar() {
    if (!_btmCfg) return;
    _buildBtmPayments();
    _buildBtmLinks();
  }

  // ── Платёжные системы (ряд иконок) ─────────────────────────────────────────
  function _buildBtmPayments() {
    const wrap = el('btm-payments'); if (!wrap) return;
    const items = (_btmCfg.payment_systems?.items || []).filter(i => i.enabled !== false);
    wrap.innerHTML = items.map(item => {
      const iconHtml = item.icon
        ? `<img class="btm-pay-icon" src="${item.icon}" alt="${item.label}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
          + `<div class="btm-pay-fallback" style="display:none">${item.label.slice(0,4).toUpperCase()}</div>`
        : `<div class="btm-pay-fallback">${item.label.slice(0,4).toUpperCase()}</div>`;
      const showLabel = _btmCfg.appearance?.show_payment_labels !== false;
      return `<div class="btm-pay-item">${iconHtml}${showLabel?`<span class="btm-pay-label">${item.label}</span>`:''}</div>`;
    }).join('');
  }

  // ── Ссылки (3 группы с разделителем) ───────────────────────────────────────
  function _buildBtmLinks() {
    const wrap = el('btm-links'); if (!wrap) return;
    const groups = _btmCfg.links?.groups || [];
    const enabledGroups = groups.map(g => ({
      ...g,
      items: (g.items||[]).filter(i => i.enabled !== false)
    })).filter(g => g.items.length > 0);

    wrap.innerHTML = enabledGroups.map((group, gi) => {
      const linksHtml = group.items.map(item => {
        const onclick = _btmLinkAction(item);
        if (item.action === 'url' && item.url) {
          return `<a class="btm-link" data-btm-id="${item.id}" href="${item.url}" target="_blank" rel="noopener">${item.label}</a>`;
        }
        return `<button class="btm-link" data-btm-id="${item.id}" onclick="${onclick}">${item.label}</button>`;
      }).join('');
      const sep = gi < enabledGroups.length - 1 ? '<div class="btm-link-sep"></div>' : '';
      return `<div class="btm-link-group">${linksHtml}</div>${sep}`;
    }).join('');
  }

  function _btmLinkAction(item) {
    switch(item.action) {
      case 'modal_faq':       return "V.modal('faq')";
      case 'modal_rules':     return "V.btmOpenRules()";
      case 'modal_licenses':  return "V.btmOpenLicenses()";
      case 'modal_providers': return "V.btmOpenProviders()";
      case 'modal_payments':  return "V.btmOpenPayments()";
      case 'modal_contacts':  return "V.btmOpenContacts()";
      case 'modal_support':   return "V.modal('support')";
      case 'filter_tag':      return `V.filterByTag('${item.tag}')`;
      case 'view_all_games':  return "V.go('games')";
      case 'url':             return item.url ? '' : '';
      default: return '';
    }
  }

  // ── MODAL: Правила и условия ───────────────────────────────────────────────
  function btmOpenRules() {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.rules; if (!cfg) return;
    txt('btm-rules-title', _t('modals.rules_title'));
    const tabs = cfg.tabs || [];
    const tabBar = el('btm-rules-tabs');
    const content = el('btm-rules-content');
    if (!tabBar || !content) return;
    const _tabI18n = { general:'modals.tab_general', deposits:'modals.tab_deposits', withdraw:'modals.tab_withdraw', bonuses:'modals.tab_bonuses' };
    tabBar.innerHTML = tabs.map((t,i) => {
      const label = _tabI18n[t.id] ? _t(_tabI18n[t.id]) : t.label;
      return `<button class="btm-tab-btn${i===0?' on':''}" onclick="V.btmRulesTab(this,'${t.id}')">${label}</button>`;
    }).join('');
    _renderBtmTextCards(content, tabs[0]?.text || '');
    modal('btm-rules');
  }
  function btmRulesTab(btn, tabId) {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.rules; if (!cfg) return;
    document.querySelectorAll('#btm-rules-tabs .btm-tab-btn').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    const tab = (cfg.tabs||[]).find(t => t.id === tabId);
    const content = el('btm-rules-content');
    if (content && tab) _renderBtmTextCards(content, tab.text || '');
  }

  // Разбивает текст на секции по заголовкам (строки вида "1. Что-то" или "## Что-то")
  // и рендерит каждую секцию как красивую панель-карточку
  function _renderBtmTextCards(container, rawText) {
    // Разбиваем по строкам начинающимся с цифры+точки или двойного переноса
    const lines = rawText.split('\n');
    const sections = [];
    let cur = null;
    lines.forEach(line => {
      const isHeading = /^\d+[\.\)]\s/.test(line.trim()) || /^#{1,3}\s/.test(line.trim());
      if (isHeading) {
        if (cur) sections.push(cur);
        cur = { title: line.trim().replace(/^#{1,3}\s/, ''), body: [] };
      } else {
        if (!cur) cur = { title: null, body: [] };
        cur.body.push(line);
      }
    });
    if (cur) sections.push(cur);

    if (sections.length === 0 || (sections.length === 1 && !sections[0].title)) {
      // Нет структуры — просто панель с текстом
      container.innerHTML = `<div class="btm-card-block">${rawText.replace(/\n/g,'<br>')}</div>`;
      return;
    }

    container.innerHTML = sections.map(s => {
      const bodyText = s.body.join('\n').replace(/^\n+|\n+$/g,'').replace(/\n/g,'<br>');
      const titleHtml = s.title ? `<div class="btm-card-title">${s.title}</div>` : '';
      const bodyHtml  = bodyText ? `<div class="btm-card-body">${bodyText}</div>` : '';
      return `<div class="btm-card-block">${titleHtml}${bodyHtml}</div>`;
    }).join('');
  }

  // ── MODAL: Лицензии ────────────────────────────────────────────────────────
  function btmOpenLicenses() {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.licenses; if (!cfg) return;
    txt('btm-lic-title', _t('modals.licenses_title'));
    const body = el('btm-lic-text');
    if (body) _renderBtmTextCards(body, cfg.text || '');
    modal('btm-licenses');
  }

  // ── MODAL: Провайдеры ──────────────────────────────────────────────────────
  function btmOpenProviders() {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.providers; if (!cfg) return;
    txt('btm-prov-title', _t('modals.providers_title'));
    const items = (cfg.items||[]).filter(i => i.enabled !== false);
    const grid = el('btm-prov-grid');
    if (!grid) return;
    grid.innerHTML = items.map(p => {
      const logoHtml = p.logo
        ? `<img class="btm-prov-logo" src="${p.logo}" alt="${p.label}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
          + `<div class="btm-prov-fallback" style="display:none">${p.label.slice(0,6)}</div>`
        : `<div class="btm-prov-fallback">${p.label.slice(0,6)}</div>`;
      return `<div class="btm-prov-card" onclick="V.btmProviderClick('${p.tag}')">${logoHtml}<div class="btm-prov-name">${p.label}</div></div>`;
    }).join('');
    modal('btm-providers');
  }
  function btmProviderClick(tag) {
    closeM();
    filterByTag(tag);
  }

  // ── MODAL: Платёжные системы ───────────────────────────────────────────────
  function btmOpenPayments() {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.payments; if (!cfg) return;
    txt('btm-pay-title', _t('modals.payments_title'));
    const cats = (cfg.categories||[]);
    const catBar = el('btm-pay-cats');
    if (!catBar) return;
    const _catI18n = { cards:'modals.pay_cat_cards', ewallet:'modals.pay_cat_ewallet', paymentfaq:'modals.pay_cat_faq' };
    catBar.innerHTML = cats.map((c,i) => {
      const iconHtml = c.icon
        ? `<img class="btm-pay-cat-icon" src="${c.icon}" alt="" onerror="this.style.display='none'">` : '';
      const label = _catI18n[c.id] ? _t(_catI18n[c.id]) : c.label;
      return `<button class="btm-pay-cat-btn${i===0?' on':''}" onclick="V.btmPayCat(this,'${c.id}')">${iconHtml}${label}</button>`;
    }).join('');
    _btmPaySelCat = cats[0]?.id || null;
    _renderBtmPayList(cats[0]);
    modal('btm-payments');
  }
  function btmPayCat(btn, catId) {
    if (!_btmCfg) return;
    document.querySelectorAll('#btm-pay-cats .btm-pay-cat-btn').forEach(b => b.classList.remove('on'));
    btn.classList.add('on');
    _btmPaySelCat = catId;
    const cat = (_btmCfg.modals?.payments?.categories||[]).find(c => c.id === catId);
    _renderBtmPayList(cat);
  }
  function _renderBtmPayList(cat) {
    const list = el('btm-pay-list'); if (!list || !cat) return;
    list.innerHTML = (cat.items||[]).map(item => {
      const iconHtml = item.icon
        ? `<img class="btm-pay-row-icon" src="${item.icon}" alt="${item.label}" onerror="this.style.display='none'">`
        : `<div class="btm-pay-row-icon" style="display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:700;color:var(--t2)">${item.label.slice(0,3)}</div>`;
      return `<div class="btm-pay-row" id="bpr-${item.id}" onclick="V.btmPayToggle('${item.id}')">
        <div class="btm-pay-row-head">${iconHtml}<span class="btm-pay-row-name">${item.label}</span><span class="btm-pay-row-arr">▸</span></div>
        <div class="btm-pay-row-body"><div class="btm-pay-row-body-in">${(item.text||'').replace(/\n/g,'<br>')}</div></div>
      </div>`;
    }).join('');
  }
  function btmPayToggle(id) {
    el('bpr-'+id)?.classList.toggle('on');
  }

  // ── MODAL: Контакты ────────────────────────────────────────────────────────
  const _CT_ICONS = {
    telegram: `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.373 0 0 5.373 0 12s5.373 12 12 12 12-5.373 12-12S18.627 0 12 0zm5.562 8.248-1.97 9.289c-.145.658-.537.818-1.084.508l-3-2.21-1.447 1.394c-.16.16-.295.295-.605.295l.213-3.053 5.56-5.023c.242-.213-.054-.333-.373-.12l-6.871 4.326-2.962-.924c-.643-.204-.657-.643.136-.953l11.57-4.461c.537-.194 1.006.131.833.932z"/></svg>`,
    youtube:  `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31.2 31.2 0 0 0 0 12a31.2 31.2 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31.2 31.2 0 0 0 24 12a31.2 31.2 0 0 0-.5-5.8zM9.7 15.5V8.5l6.3 3.5-6.3 3.5z"/></svg>`,
    whatsapp: `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 0 1-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 0 1-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 0 1 2.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0 0 12.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 0 0 5.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 0 0-3.48-8.413z"/></svg>`,
    viber:    `<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M20.52 3.449C18.245 1.198 15.075 0 11.997 0 5.453 0 .103 5.35.103 11.893c0 2.098.547 4.144 1.588 5.946L0 24l6.305-1.654a11.886 11.886 0 0 0 5.688 1.449h.006c6.543 0 11.89-5.35 11.893-11.893a11.82 11.82 0 0 0-3.371-8.453zM12 21.826a9.857 9.857 0 0 1-5.031-1.378l-.361-.214-3.741.981.999-3.648-.235-.374A9.847 9.847 0 0 1 2.1 11.893C2.1 6.45 6.554 2 12 2c2.636 0 5.112 1.028 6.977 2.893A9.803 9.803 0 0 1 21.9 11.88c-.003 5.444-4.456 9.946-9.9 9.946zm5.44-7.451c-.298-.149-1.758-.867-2.031-.967-.272-.099-.47-.148-.669.15-.198.297-.767.967-.94 1.164-.173.198-.347.223-.645.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.76-1.653-2.059-.173-.297-.018-.458.13-.606.135-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.372-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.372-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.874 1.213 3.073.149.198 2.096 3.2 5.077 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/></svg>`,
    email:    `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m2 7 10 7 10-7"/></svg>`,
  };
  function btmOpenContacts() {
    if (!_btmCfg) return;
    const cfg = _btmCfg.modals?.contacts; if (!cfg) return;
    txt('btm-ct-title', _t('modals.contacts_title'));
    const desc = el('btm-ct-desc');
    const info = el('btm-ct-info');
    const btns = el('btm-ct-btns');
    if (desc) desc.textContent = _t('modals.contacts_desc');
    if (info) info.textContent = _t('modals.contacts_info') || cfg.info_text || '';
    if (btns) {
      const enabled = (cfg.buttons||[]).filter(b => b.enabled !== false);
      const count = enabled.length;
      btns.style.gridTemplateColumns = count === 1 ? '1fr' : count === 3 ? 'repeat(3,1fr)' : 'repeat(2,1fr)';
      btns.innerHTML = enabled.map((b, i) => {
        // Приоритет: icon_svg из конфига → встроенная иконка по id → пусто
        const icon = b.icon_svg
          ? `<img src="${b.icon_svg}" style="width:18px;height:18px;object-fit:contain;filter:brightness(0) invert(1)" onerror="this.style.display='none'">`
          : (_CT_ICONS[b.id] || _CT_ICONS[(b.label||'').toLowerCase()] || '');
        const isLast = i === count - 1;
        const spanStyle = (count % 2 !== 0 && isLast && count !== 1 && count !== 3) ? 'grid-column:1/-1;' : '';
        const href = b.url ? `href="${b.url}" target="_blank" rel="noopener"` : '';
        return `<a class="btm-ct-btn" ${href} style="background:${b.color||'var(--a)'};${spanStyle}">${icon}${b.label}</a>`;
      }).join('');
    }
    modal('btm-contacts');
  }

  // ── RESPONSIBLE GAMBLING ──────────────────────────────────────────────────────

  let _rgExcludeDays = 0;

  async function rgOpen(){
    if(!S.user){modal('auth');return;}
    txt('rg-err','');txt('rg-ok','');
    _rgExcludeDays = 0;
    document.querySelectorAll('.rg-excl-opt').forEach(b=>b.classList.remove('on'));
    const btn=el('rg-excl-btn'); if(btn) btn.disabled=true;
    try{
      const r=await api('/api/rg/status','GET');
      if(r.deposit_daily_limit!=null) {const e=el('rg-daily-inp'); if(e) e.value=r.deposit_daily_limit||'';}
      if(r.deposit_weekly_limit!=null){const e=el('rg-weekly-inp');if(e) e.value=r.deposit_weekly_limit||'';}
      const badge=el('rg-status-badge');
      if(badge){
        if(r.self_excluded){
          badge.style.display='block';
          badge.style.background='rgba(239,68,68,.15)';
          badge.style.color='#ef4444';
          const until=r.excluded_until?r.excluded_until.slice(0,10):'';
          badge.textContent=`${_t('rg.excluded_until')||'Заблокирован до'}: ${until}`;
        } else {
          badge.style.display='none';
        }
      }
    }catch{}
    modal('rg');
  }

  async function rgSaveLimits(){
    const daily  = parseFloat(el('rg-daily-inp')?.value)||0;
    const weekly = parseFloat(el('rg-weekly-inp')?.value)||0;
    txt('rg-err','');txt('rg-ok','');
    try{
      const r=await api('/api/rg/deposit_limit','POST',{daily,weekly});
      if(r.error){txt('rg-err',r.error);return;}
      txt('rg-ok',_t('rg.limits_saved')||'Лимиты сохранены');
    }catch{txt('rg-err',_t('toasts.error_generic'));}
  }

  function rgSelectExclude(btn){
    document.querySelectorAll('.rg-excl-opt').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');
    _rgExcludeDays=parseInt(btn.dataset.days)||0;
    const exclBtn=el('rg-excl-btn'); if(exclBtn) exclBtn.disabled=!_rgExcludeDays;
  }

  async function rgSelfExclude(){
    if(!_rgExcludeDays) return;
    const cm = el('rg-excl-confirm-modal');
    const daysEl = el('rg-excl-confirm-days');
    if (daysEl) {
      daysEl.textContent = _rgExcludeDays >= 36500
        ? (_t('rg.exclude_forever') || 'Permanent')
        : `${_rgExcludeDays} ${_t('rg.exclude_days_suffix') || 'days'}`;
    }
    if (cm) { cm.style.display = 'flex'; return; }
  }
  async function rgConfirmSelfExclude(){
    if(!_rgExcludeDays) return;
    const cm = el('rg-excl-confirm-modal');
    if (cm) cm.style.display = 'none';
    txt('rg-err','');txt('rg-ok','');
    try{
      const r=await api('/api/rg/self_exclude','POST',{days:_rgExcludeDays});
      if(r.error){txt('rg-err',r.error);return;}
      const until=r.excluded_until?r.excluded_until.slice(0,10):'';
      txt('rg-ok',`${_t('rg.excluded_until')||'Заблокирован до'}: ${until}`);
      setTimeout(()=>{closeM();},2000);
    }catch{txt('rg-err',_t('toasts.error_generic'));}
  }
  function rgCancelSelfExclude(){
    const cm = el('rg-excl-confirm-modal');
    if (cm) cm.style.display = 'none';
  }

  // ── AFFILIATE / REFERRAL ──────────────────────────────────────────────────────

  async function affOpen(){
    if(!S.user){modal('auth');return;}
    txt('aff-ok','');
    try{
      const r=await api('/api/affiliate/my_link','GET');
      const inp=el('aff-link-inp'); if(inp) inp.value=r.ref_url||'';
      txt('aff-inv-cnt',r.referred_count||0);
      const sym=csym(S.user.currency);
      txt('aff-earn-amt',`${(r.total_bonus_earned||0).toFixed(2)} ${sym}`);
      txt('aff-bonus-val',`${r.bonus_per_referral||100} ${sym}`);
    }catch{}
    modal('affiliate');
  }

  function affCopyLink(){
    const inp=el('aff-link-inp');
    if(!inp) return;
    navigator.clipboard?.writeText(inp.value).then(()=>{
      toast(_t('affiliate.link_copied')||'Ссылка скопирована!','ok');
    }).catch(()=>{
      inp.select(); document.execCommand('copy');
      toast(_t('affiliate.link_copied')||'Ссылка скопирована!','ok');
    });
  }

  // ── VIP CLUB ──────────────────────────────────────────────────────────────────

  let _vipCfg = null;
  let _vipSelectedTier = null;
  let _vipSelectedMonths = null;
  const _VIP_COLORS = {bronze:'#b0b0b0', silver:'#c0a96e', gold:'#f5c518', platinum:'#00e5ff'};

  async function vipOpen() {
    if (!S.user) { modal('auth'); return; }
    // Clear state
    _vipSelectedTier = null; _vipSelectedMonths = null;
    _vipSetBuyBtn(false);
    el('vip-period-wrap') && (el('vip-period-wrap').style.display = 'none');
    el('vip-benefits-wrap') && (el('vip-benefits-wrap').style.display = 'none');
    el('vip-msg') && (el('vip-msg').style.display = 'none');
    // Load config
    try {
      if (!_vipCfg) {
        const r = await _secureFetch('/api/vip/config', {credentials:'include'});
        _vipCfg = await r.json();
      }
      _vipRenderTiers();
      _vipRenderCurrentStatus();
      _vipRenderLimitsTable();
      _vipRenderLimitsTable();
    } catch(e) { console.warn('[vip]', e); }
    modal('vip');
  }

  function _vipRenderCurrentStatus() {
    const wrap = el('vip-current-status');
    if (!wrap || !S.user) return;
    const tier = S.user.vip_tier;
    const active = S.user.vip_active;
    const exp = S.user.vip_expires_at;
    if (!active || tier === 'none') { wrap.style.display = 'none'; return; }
    const color = _VIP_COLORS[tier] || 'var(--a)';
    const expDate = exp ? new Date(exp).toLocaleDateString() : '';
    wrap.style.display = 'block';
    wrap.style.background = `rgba(${_hexToRgb(color)},.07)`;
    wrap.style.borderColor = `rgba(${_hexToRgb(color)},.3)`;
    wrap.style.color = color;
    const tierLabel = _vipCfg?.tiers?.[tier]?.label || tier;
    wrap.innerHTML = `${_vipCfg?.tiers?.[tier]?.icon || '⭐'} ${_t('vip.vip_your_status')||'Твой статус'}: <strong>${tierLabel}</strong> &nbsp;·&nbsp; ${_t('vip.vip_active_badge')||'Активен до'} ${expDate}`;
  }

  function _vipRenderTiers() {
    const list = el('vip-tiers-list');
    if (!list || !_vipCfg) return;
    const sym = {UAH:'₴',USD:'$',EUR:'€',RUB:'₽',USDT:'₮'}[S.user?.currency||'UAH']||'₴';
    const cur = S.user?.currency||'UAH';
    list.innerHTML = Object.entries(_vipCfg.tiers || {}).map(([id, tier]) => {
      const price1 = _vipConvertPrice(tier.prices['1'], cur);
      const cb = tier.benefits?.cashback_percent || 0;
      const color = _VIP_COLORS[id] || '#aaa';
      const iconHtml = _vipIconHtml(tier, id, 28);
      return `<div class="vip-tier-card" data-tier="${id}" onclick="V._vipSelectTier('${id}')">
        <span class="vip-tier-icon">${iconHtml}</span>
        <div class="vip-tier-info">
          <div class="vip-tier-name" style="color:${color}">${tier.label}</div>
          <div class="vip-tier-desc">${_tv('vip.vip_cashback','Кэшбэк')} ${cb}%</div>
        </div>
        <div class="vip-tier-cashback" style="color:${color}">${_tv('vip.vip_price','от')} ${sym}${price1}</div>
      </div>`;
    }).join('');
  }

  function _vipSelectTier(tierId) {
    _vipSelectedTier = tierId;
    _vipSelectedMonths = null;
    _vipSetBuyBtn(false);
    // Highlight selected
    document.querySelectorAll('.vip-tier-card').forEach(c => {
      c.classList.toggle('selected', c.dataset.tier === tierId);
    });
    // Show periods
    const pw = el('vip-period-wrap');
    if (pw) { pw.style.display = ''; _vipRenderPeriods(tierId); }
    // Show benefits
    const bw = el('vip-benefits-wrap');
    if (bw) { bw.style.display = ''; _vipRenderBenefits(tierId); }
    el('vip-msg') && (el('vip-msg').style.display = 'none');
  }

  function _vipRenderPeriods(tierId) {
    const list = el('vip-periods-list');
    if (!list || !_vipCfg) return;
    const sym = {UAH:'₴',USD:'$',EUR:'€',RUB:'₽',USDT:'₮'}[S.user?.currency||'UAH']||'₴';
    const cur = S.user?.currency||'UAH';
    const tierPrices = _vipCfg.tiers?.[tierId]?.prices || {};
    list.innerHTML = (_vipCfg.durations || []).map(d => {
      const price = _vipConvertPrice(tierPrices[String(d.months)], cur);
      const discLabel = d.discount > 0 ? `−${d.discount}%` : '';
      const label = _tv(`vip.duration_${d.months}m`, `${d.months} мес.`);
      return `<div class="vip-period-btn" data-months="${d.months}" onclick="V._vipSelectPeriod(${d.months})">
        <div class="vip-period-label">${label}</div>
        <div class="vip-period-price">${sym}${price}</div>
        ${discLabel ? `<div class="vip-period-discount">${discLabel}</div>` : ''}
      </div>`;
    }).join('');
  }

  function _vipSelectPeriod(months) {
    _vipSelectedMonths = months;
    document.querySelectorAll('.vip-period-btn').forEach(b => {
      b.classList.toggle('selected', +b.dataset.months === months);
    });
    _vipSetBuyBtn(true);
    el('vip-msg') && (el('vip-msg').style.display = 'none');
  }

  function _vipRenderBenefits(tierId) {
    const list = el('vip-benefits-list');
    if (!list || !_vipCfg) return;
    const b = _vipCfg.tiers?.[tierId]?.benefits || {};
    const cur = S.user?.currency || 'UAH';
    const sym = {UAH:'₴',USD:'$',EUR:'€',RUB:'₽',USDT:'₮'}[cur]||'₴';
    const rows = [
      [_tv('vip.vip_cashback','Кэшбэк'),              `${b.cashback_percent}%`,             true],
      [_tv('vip.vip_deposit_bonus','Бонус к депозиту'), `+${b.deposit_bonus_percent}%`,      true],
      [_tv('vip.vip_win_bonus','Бонус к выигрышу'),    b.win_bonus_percent > 0 ? `+${b.win_bonus_percent}%` : '—', b.win_bonus_percent > 0],
      [_tv('vip.vip_free_spins','Фриспинов/мес'),      `${b.free_spins_monthly}`,            true],
      [_tv('vip.vip_withdraw_lim','Лимит вывода'), b.withdraw_limit_daily > 0 ? `${sym}${_vipConvertPrice(b.withdraw_limit_daily, cur).toLocaleString()}` : _tv('vip.vip_unlimited','Без лимита'), true],
      [_tv('vip.vip_priority','Приоритет. поддержка'), b.priority_support ? '✓' : '—',      b.priority_support],
      [_tv('vip.vip_manager','Персональный менеджер'), b.personal_manager ? '✓' : '—',      b.personal_manager],
      [_tv('vip.vip_promos','Эксклюзивные акции'),     b.exclusive_promos ? '✓' : '—',      b.exclusive_promos],
    ];
    list.innerHTML = rows.map(([label, val, active]) =>
      `<div class="vip-benefit-row${active?' active':''}">
        <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
          <circle cx="6" cy="6" r="5" stroke="${active?'var(--green)':'var(--t3)'}" stroke-width="1.2"/>
          ${active ? '<path d="M3.5 6l2 2 3-3" stroke="var(--green)" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>' : ''}
        </svg>
        ${label}
        <span class="vip-benefit-val" style="color:${active?'var(--a)':'var(--t3)'}">${val}</span>
      </div>`
    ).join('');
  }

  function _vipRenderLimitsTable() {
    const wrap = el('vip-limits-table');
    if (!wrap || !_vipCfg) return;

    const cur  = S.user?.currency || 'UAH';
    const sym  = {UAH:'₴', USD:'$', EUR:'€', RUB:'₽', USDT:'₮'}[cur] || '₴';
    const rates = S.rates || {};

    // If non-UAH currency but rates not loaded yet — load then re-render
    if (cur !== 'UAH' && !rates[cur]?.rate_to_uah) {
      _loadCurrencyRates().then(() => _vipRenderLimitsTable());
      return;
    }

    const user_tier = S.user?.vip_tier || 'none';
    const user_active = S.user?.vip_active || false;

    const fmtAmt = (uah) => {
      if (!uah) return _tv('vip.vip_unlimited', 'Без лимита');
      const rate = cur === 'UAH' ? 1 : (rates[cur]?.rate_to_uah || 1);
      const converted = cur === 'UAH' ? uah : Math.round(uah / rate);
      return `${sym}${converted.toLocaleString()}`;
    };

    // Header
    const colDaily   = _tv('vip.vip_lim_daily',   'Суточный лимит');
    const colMonthly = _tv('vip.vip_lim_monthly',  'Месячный лимит');
    const colPerks   = _tv('vip.vip_lim_perks',    'Преимущества');
    const titleLbl   = _tv('vip.vip_limits_title', 'Рекомендуемые лимиты на вывод средств');

    // Update static label
    const titleEl = el('vip-limits-title');
    if (titleEl) titleEl.textContent = titleLbl;

    // Tier perks descriptions
    const perkDesc = {
      none:     _tv('vip.vip_lim_perk_none',     'Стандартный вывод, базовая поддержка'),
      bronze:   _tv('vip.vip_lim_perk_bronze',   'Приоритетная поддержка'),
      silver:   _tv('vip.vip_lim_perk_silver',   'Быстрый вывод, меньше проверок'),
      gold:     _tv('vip.vip_lim_perk_gold',      'Персональный менеджер, акции'),
      platinum: _tv('vip.vip_lim_perk_platinum',  'Мгновенный вывод, эксклюзивные бонусы'),
    };

    const tiers = _vipCfg.tiers || {};
    const tierOrder = ['bronze', 'silver', 'gold', 'platinum'];

    // "No VIP" row first
    const rows = [{id:'none', label:_tv('vip.vip_no_vip','Нет VIP'), daily:10000, monthly:100000, color:'var(--t3)'}];
    tierOrder.forEach(id => {
      const t = tiers[id];
      if (!t) return;
      rows.push({
        id,
        label:   t.label,
        daily:   t.benefits?.withdraw_limit_daily   || 0,
        monthly: t.benefits?.withdraw_limit_monthly || 0,
        color:   _VIP_COLORS[id] || 'var(--t2)',
      });
    });

    wrap.innerHTML = `
      <table class="vip-lim-table">
        <thead>
          <tr>
            <th>${_tv('vip.vip_lim_status','Статус')}</th>
            <th>${colDaily}</th>
            <th>${colMonthly}</th>
            <th>${colPerks}</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => {
            const isActive = r.id === 'none' ? (!user_active || user_tier === 'none') : (user_active && user_tier === r.id);
            return `<tr class="vip-lim-row${isActive ? ' vip-lim-active' : ''}" style="--tier-color:${r.color}">
              <td class="vip-lim-name" style="color:${r.color};font-weight:${isActive?700:500}">${r.label}</td>
              <td class="vip-lim-val">${fmtAmt(r.daily)}</td>
              <td class="vip-lim-val">${fmtAmt(r.monthly)}</td>
              <td class="vip-lim-perk">${perkDesc[r.id] || ''}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>`;
  }
  function _vipSetBuyBtn(enabled) {
    const btn = el('vip-buy-btn');
    if (!btn) return;
    btn.disabled = !enabled;
    btn.style.opacity = enabled ? '1' : '.4';
    btn.style.cursor  = enabled ? 'pointer' : 'not-allowed';
    const sym = {UAH:'₴',USD:'$',EUR:'€',RUB:'₽',USDT:'₮'}[S.user?.currency||'UAH']||'₴';
    const cur = S.user?.currency||'UAH';
    if (enabled && _vipSelectedTier && _vipSelectedMonths) {
      const price = _vipConvertPrice(
        _vipCfg?.tiers?.[_vipSelectedTier]?.prices?.[String(_vipSelectedMonths)], cur
      );
      btn.textContent = `${_tv('vip.vip_buy_btn','Приобрести')} — ${sym}${price}`;
    } else {
      btn.textContent = _tv('vip.vip_buy_btn','Приобрести');
    }
  }

  // Returns <img> using /static/uploads/vip/${tierId}.png, fallback to SVG on error
  function _vipIconHtml(tier, tierId, size) {
    const s = size || 24;
    const iconUrl = (tier && tier.icon_url) || `/static/uploads/vip/${tierId}.png`;
    return `<img src="${iconUrl}" width="${s}" height="${s}" style="object-fit:contain;border-radius:4px" onerror="this.outerHTML=V._vipDefaultIcon('${tierId}',${s})">`;
  }
  function _vipDefaultIcon(tierId, size) {
    const s = size || 24;
    // Medal-style SVG icons per tier
    const icons = {
      bronze: `<svg width="${s}" height="${s}" viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="20" r="9" fill="rgba(176,130,90,.15)" stroke="#b0825a" stroke-width="1.6"/>
        <circle cx="16" cy="20" r="5.5" fill="rgba(176,130,90,.2)" stroke="#b0825a" stroke-width="1"/>
        <path d="M11 8h10l-2 7H13z" fill="#b0825a" opacity=".7"/>
        <path d="M13 8l-3 4M19 8l3 4" stroke="#b0825a" stroke-width="1.4" stroke-linecap="round"/>
        <text x="16" y="23.5" text-anchor="middle" fill="#b0825a" font-size="7" font-weight="700" font-family="sans-serif">B</text>
      </svg>`,
      silver: `<svg width="${s}" height="${s}" viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="20" r="9" fill="rgba(192,169,110,.12)" stroke="#c0a96e" stroke-width="1.6"/>
        <circle cx="16" cy="20" r="5.5" fill="rgba(192,169,110,.2)" stroke="#c0a96e" stroke-width="1"/>
        <path d="M11 8h10l-2 7H13z" fill="#c0a96e" opacity=".8"/>
        <path d="M13 8l-3 4M19 8l3 4" stroke="#c0a96e" stroke-width="1.4" stroke-linecap="round"/>
        <text x="16" y="23.5" text-anchor="middle" fill="#c0a96e" font-size="7" font-weight="700" font-family="sans-serif">S</text>
      </svg>`,
      gold: `<svg width="${s}" height="${s}" viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="20" r="9" fill="rgba(245,197,24,.12)" stroke="#f5c518" stroke-width="1.6"/>
        <circle cx="16" cy="20" r="5.5" fill="rgba(245,197,24,.22)" stroke="#f5c518" stroke-width="1"/>
        <path d="M11 8h10l-2 7H13z" fill="#f5c518" opacity=".9"/>
        <path d="M13 8l-3 4M19 8l3 4" stroke="#f5c518" stroke-width="1.4" stroke-linecap="round"/>
        <text x="16" y="23.5" text-anchor="middle" fill="#f5c518" font-size="7" font-weight="700" font-family="sans-serif">G</text>
      </svg>`,
      platinum: `<svg width="${s}" height="${s}" viewBox="0 0 32 32" fill="none">
        <circle cx="16" cy="20" r="9" fill="rgba(0,229,255,.08)" stroke="#00e5ff" stroke-width="1.6"/>
        <circle cx="16" cy="20" r="5.5" fill="rgba(0,229,255,.15)" stroke="#00e5ff" stroke-width="1"/>
        <path d="M11 8h10l-2 7H13z" fill="#00e5ff" opacity=".8"/>
        <path d="M13 8l-3 4M19 8l3 4" stroke="#00e5ff" stroke-width="1.4" stroke-linecap="round"/>
        <text x="16" y="23.5" text-anchor="middle" fill="#00e5ff" font-size="6" font-weight="700" font-family="sans-serif">PT</text>
      </svg>`,
    };
    return icons[tierId] || `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="var(--a)" stroke-width="1.5"/></svg>`;
  }

  function _vipConvertPrice(priceUah, currency) {
    if (!priceUah) return 0;
    if (currency === 'UAH') return priceUah;
    // S.rates format: { USD: { rate_to_uah: 41.5 }, ... }
    const rates = S.rates || {};
    const rate = rates[currency]?.rate_to_uah || 0;
    if (!rate) return priceUah; // fallback to UAH if rate missing
    return Math.ceil(priceUah / rate);
  }

  function _hexToRgb(hex) {
    const r = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
    return r ? `${parseInt(r[1],16)},${parseInt(r[2],16)},${parseInt(r[3],16)}` : '100,120,255';
  }

  async function vipPurchase() {
    if (!S.user || !_vipSelectedTier || !_vipSelectedMonths) return;
    const btn = el('vip-buy-btn');
    if (btn) { btn.disabled = true; btn.style.opacity = '.6'; }
    const msgEl = el('vip-msg');
    if (msgEl) msgEl.style.display = 'none';
    try {
      const resp = await _secureFetch('/api/vip/purchase', {
        method: 'POST', credentials: 'include',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({tier: _vipSelectedTier, months: _vipSelectedMonths}),
      });
      const r = await resp.json();
      if (r.error === 'insufficient_funds') {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(255,61,90,.08)';
          msgEl.style.color = 'var(--red)';
          msgEl.style.border = '1px solid rgba(255,61,90,.2)';
          msgEl.innerHTML = `${_t('vip.vip_need_deposit')||'Недостаточно средств.'} <a href="#" onclick="V.closeM();V.go('balance')" style="color:var(--a);text-decoration:underline">${_t('vip.vip_go_deposit')||'Пополнить'}</a>`;
        }
        return;
      }
      if (r.error) {
        if (msgEl) {
          msgEl.style.display = 'block';
          msgEl.style.background = 'rgba(255,61,90,.08)';
          msgEl.style.color = 'var(--red)';
          msgEl.style.border = '1px solid rgba(255,61,90,.2)';
          msgEl.textContent = r.error;
        }
        return;
      }
      // Success — update user state
      if (S.user) {
        S.user.vip_tier       = r.tier;
        S.user.vip_expires_at = r.expires_at;
        S.user.vip_active     = true;
        S.user.balance        = r.balance;
        setUser(S.user);
      }
      toast((_t('vip.vip_success')||'VIP активирован!') + ' ' + (_vipCfg?.tiers?.[r.tier]?.icon||''), 'ok');
      closeM();
      // Обновляем значок VIP в профиле
      _vipUpdateProfileBadge();
    } catch(e) {
      toast(_t('toasts.error_generic')||'Ошибка', 'err');
    } finally {
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
    }
  }

  function _vipUpdateProfileBadge() {
    // Old badge (settings) — keep for compat
    const badge = el('profile-vip-badge');
    if (badge) badge.style.display = 'none';

    // Dropdown VIP row
    const row   = el('dd-vip-row');
    const icon  = el('dd-vip-icon');
    const label = el('dd-vip-label');
    const bdg   = el('dd-vip-badge');

    if (!row) return;
    if (!S.user) { row.style.display = 'none'; return; }

    const tier   = S.user.vip_tier;
    const active = S.user.vip_active;

    if (!active || !tier || tier === 'none') {
      // No active VIP — show "Get VIP" prompt
      row.style.display = '';
      if (icon)  icon.innerHTML = '<svg width="14" height="14" viewBox="0 0 20 20" fill="none"><path d="M10 2l2.4 5H18l-4.2 3.4 1.6 5.6L10 13l-5.4 3 1.6-5.6L2 7h5.6z" stroke="var(--a)" stroke-width="1.4" stroke-linejoin="round"/></svg>';
      if (label) { label.textContent = _tv('vip.vip_modal_title', 'VIP-Клуб'); label.style.color = 'var(--t2)'; }
      if (bdg)   { bdg.textContent = ''; bdg.style.display = 'none'; }
      return;
    }

    const color    = _VIP_COLORS[tier] || '#aaa';
    const tierInfo = _vipCfg?.tiers?.[tier];
    const tierLabel = tierInfo?.label || tier;
    const iconUrl  = tierInfo?.icon_url;

    row.style.display = '';

    // Icon: use /static/uploads/vip/${tier}.png directly (no need to wait for _vipCfg)
    if (icon) {
      const iconUrl = `/static/uploads/vip/${tier}.png`;
      icon.innerHTML = `<img src="${iconUrl}" width="18" height="18" style="object-fit:contain;vertical-align:middle;border-radius:3px" onerror="this.outerHTML=V._vipDefaultIcon('${tier}',18)">`;
    }

    if (label) {
      label.textContent = _tv('vip.vip_modal_title', 'VIP-Клуб');
      label.style.color = 'var(--t2)';
    }

    if (bdg) {
      bdg.textContent = tierLabel;
      bdg.style.display = 'inline-block';
      bdg.style.color = color;
      bdg.style.borderColor = `rgba(${_hexToRgb(color)},.35)`;
      bdg.style.background  = `rgba(${_hexToRgb(color)},.10)`;
    }
  }

  // ── RESET PASSWORD ────────────────────────────────────────────────────────────

  async function _checkResetToken(){
    const params=new URLSearchParams(window.location.search);
    const token=params.get('token');
    if(!token) return;
    // Show reset modal
    const lbl=el('reset-checking-lbl');
    if(lbl) lbl.textContent=_t('reset_password.checking_token')||'Проверяем...';
    modal('reset-pw');
    try{
      const r=await api('/api/auth/reset_password/validate','POST',{token});
      if(r.valid){
        if(lbl) lbl.style.display='none';
        const fw=el('reset-form-wrap'); if(fw) fw.classList.remove('hidden');
        // store token for submit
        window._resetToken=token;
      } else {
        if(lbl) lbl.textContent=_t('reset_password.token_invalid')||'Ссылка недействительна';
        lbl.style.color='var(--err)';
      }
    }catch{
      if(lbl) lbl.textContent=_t('toasts.error_generic');
    }
    // Clean token from URL
    history.replaceState({},'',window.location.pathname);
  }

  async function resetPwSubmit(){
    const pw=el('reset-pw-inp')?.value||'';
    const cpw=el('reset-cpw-inp')?.value||'';
    txt('reset-err','');
    if(!pw||pw.length<6){txt('reset-err',_t('validation.password_min'));return;}
    if(pw!==cpw){txt('reset-err',_t('toasts.error_password_match'));return;}
    const btn=el('reset-submit-btn'); if(btn) btn.disabled=true;
    try{
      const r=await api('/api/auth/reset_password','POST',{
        token:window._resetToken,new_password:pw,confirm_password:cpw
      });
      if(btn) btn.disabled=false;
      if(r.error){txt('reset-err',r.error);return;}
      closeM();
      toast(_t('reset_password.reset_success')||'Пароль изменён','ok');
      setTimeout(()=>modal('auth'),800);
    }catch{
      if(btn) btn.disabled=false;
      txt('reset-err',_t('toasts.error_generic'));
    }
  }

  // ── MINI BANNERS ─────────────────────────────────────────────────────────────
  function initMiniBanners() {
    const banners = (S._pageBanners||[]).filter(b => !b.invisible);
    ['home','games','sport'].forEach(key => {
      const track = el('mini-banners-track-' + key);
      const wrap = el('mini-banners-' + key);
      if (!track) return;
      _enableHoldSwipe(track);
      track.innerHTML = '';
      const list = banners;
      if (wrap) {
        let more = wrap.querySelector('.mini-banners-show-all');
        if (!more) {
          more = document.createElement('button');
          more.className = 'mini-banners-show-all';
          more.type = 'button';
          more.onclick = () => {
            S.bannerShowAll[key] = true;
            initMiniBanners();
          };
          wrap.appendChild(more);
        }
        more.style.display = 'none';
      }
      if (!banners.length) {
        // Single fallback card
        track.innerHTML = `<div class="mini-banner-card mini-banner-fallback" onclick="V.bannerClick()">
          <div class="mini-banner-fallback-text">VELOX Casino</div>
        </div>`;
        return;
      }
      list.forEach((b) => {
        const srcIdx = banners.indexOf(b);
        const card = document.createElement('div');
        card.className = 'mini-banner-card';
        let lastTapTs = 0;
        card.onclick = () => {
          const lastDragAt = Number(track.dataset.lastDragAt || '0');
          if (Date.now() - lastDragAt < 220) return;
          const now = Date.now();
          if (now - lastTapTs < 320) {
            S.bannerIdx = srcIdx < 0 ? 0 : srcIdx;
            V.bannerClick();
          }
          lastTapTs = now;
        };
        card.ondblclick = () => {
          const lastDragAt = Number(track.dataset.lastDragAt || '0');
          if (Date.now() - lastDragAt < 220) return;
          S.bannerIdx = srcIdx < 0 ? 0 : srcIdx;
          V.bannerClick();
        };
        if (b.image_ad?.trim()) {
          const ext = b.image_ad.split('.').pop().toLowerCase();
          let media;
          if (ext === 'mp4' || ext === 'webm') {
            media = document.createElement('video');
            media.autoplay = true; media.loop = true; media.muted = true; media.playsInline = true;
            media.draggable = false;
          } else {
            media = document.createElement('img');
            media.draggable = false;
          }
          media.src = b.image_ad;
          media.alt = b.modal_text || '';
          media.style.opacity = '0';
          media.style.transition = 'opacity .3s';
          const onL = () => { media.style.opacity = '1'; };
          media.tagName === 'VIDEO' ? media.addEventListener('canplay', onL) : (media.onload = onL);
          media.onerror = () => {
            card.innerHTML = `<div class="mini-banner-fallback"><div class="mini-banner-fallback-text">${b.modal_text||''}</div></div>`;
          };
          card.appendChild(media);
        } else {
          card.innerHTML = `<div class="mini-banner-fallback">
            <div style="position:absolute;inset:0;background:radial-gradient(ellipse 60% 80% at 30% 50%,rgba(0,229,255,.08),transparent 70%),radial-gradient(ellipse 50% 60% at 70% 40%,rgba(168,85,247,.07),transparent 70%)"></div>
            <div class="mini-banner-fallback-text">${b.modal_text||''}</div>
          </div>`;
        }
        track.appendChild(card);
      });
    });
  }

  // ── SPORT SECTION ────────────────────────────────────────────────────────────
  let _sportCfg = null;
  async function loadSportCfg() {
    try { _sportCfg = await api('/api/config/bukmaking'); }
    catch {
      try { _sportCfg = await api('/api/config/sports'); } catch { _sportCfg = null; }
    }
  }

  function _normalizeVideoEmbedUrl(url) {
    const raw = String(url || '').trim();
    if (!raw) return '';
    try {
      const u = new URL(raw, window.location.origin);
      const host = u.hostname.replace(/^www\./, '').toLowerCase();
      if (host === 'youtube.com' || host === 'm.youtube.com') {
        const id = u.searchParams.get('v');
        if (id) return `https://www.youtube.com/embed/${id}`;
      }
      if (host === 'youtu.be') {
        const id = u.pathname.split('/').filter(Boolean)[0];
        if (id) return `https://www.youtube.com/embed/${id}`;
      }
      if (host.endsWith('youtube.com') && u.pathname.startsWith('/embed/')) return u.toString();
      return u.toString();
    } catch {
      return raw;
    }
  }

  function buildSportSection() {
    const events = (_sportCfg?.events || []).filter(e => e.enabled);
    const sec = el('sport-section');
    if (!sec) return;
    if (!events.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';
    const row = el('home-sport-row');
    if (!row) return;
    _enableHoldSwipe(row);

    // Build tag map
    const tagMap = {};
    (_sportCfg?.tags || []).forEach(t => { tagMap[t.id] = t; });

    row.innerHTML = events.map(ev => {
      const tag = tagMap[ev.tag];
      const tagIcon = ev.icon_png || tag?.icon_path || '';
      const tagHtml = tagIcon
        ? `<div class="sport-card-tag"><img src="${tagIcon}" alt="${tag?.label||ev.tag||'sport'}" onerror="this.parentNode.innerHTML='<span class=sport-card-tag-emoji>${_sportEmoji(ev.tag)}</span>'"></div>`
        : `<div class="sport-card-tag"><span class="sport-card-tag-emoji">${_sportEmoji(ev.tag)}</span></div>`;

      const bgCover = ev.background || ev.cover;
      const gSafe = _bukmSafeCardGradient(ev);
      const gStable = _bukmStableCardBg(ev);
      const coverHtml = bgCover
        ? `<img class="sport-card-cover" src="${bgCover}" alt="${ev.home_team}" onerror="this.style.display='none';this.nextSibling.style.display='flex'">\n<div class="sport-card-cover-fallback" style="display:none">${_sportEmoji(ev.tag)}</div>`
        : `<div class="sport-card-cover-fallback sport-card-cover-grad" style="background-image:${(gSafe || gStable).replace(/"/g, '&quot;')}"><span class="sport-card-grad-emoji">${_sportEmoji(ev.tag)}</span></div>`;

      const oddsHtml = _buildOddsHtml(ev);
      const whenRow = _bukmFormatMatchWhen(ev);
      const whenTxt = `${whenRow.line1}${whenRow.line2 ? ' · ' + whenRow.line2 : ''}`;
      const hk = _bukmJerseyHtml(ev, 'home');
      const ak = _bukmJerseyHtml(ev, 'away');

      return `<div class="sport-card" onclick="if(this.parentElement.classList.contains('drag-moved'))return;V.go('sport')">
        ${coverHtml}
        ${tagHtml}
        <div class="sport-card-body">
          <div class="sport-card-league">${ev.league||''}</div>
          <div class="sport-card-when">${whenTxt}</div>
          <div class="sport-card-teams">
            <div class="sport-team-pair"><span class="sport-kit-slot">${hk}</span><div class="sport-team-name">${ev.home_team||''}</div></div>
            <div class="sport-vs">vs</div>
            <div class="sport-team-pair away"><span class="sport-kit-slot">${ak}</span><div class="sport-team-name away">${ev.away_team||''}</div></div>
          </div>
          ${oddsHtml}
        </div>
      </div>`;
    }).join('');
  }

  // ── BOOKMAKING PAGE ─────────────────────────────────────────────────────
  let _bukmTag = 'all';
  let _bukmLeague = 'all';
  let _bukmMode = 'all';
  let _bukmType = 'sport';
  let _bukmSearch = '';
  let _bukmCategory = 'all';
  let _bukmCountry = 'all';
  let _bukmSelectedMatchId = null;
  let _bukmCouponMode = 'single';
  let _bukmCoupon = [];
  let _bukmMarketTabs = {}; // { matchId: tabKey } — remembers active market tab per match

  function _jsStr(s) {
    return String(s == null ? '' : s)
      .replace(/\\/g, '\\\\')
      .replace(/'/g, "\\'");
  }

  const _BUKM_DYNAMIC_LEAGUES_MAX = 80;

  function _bukmProviderId(ev) {
    const id = String(ev?.id || '');
    if (id.startsWith('oddsapiio_')) return 'odds_api_io';
    if (id.startsWith('oddsapi_')) return 'the_odds_api';
    return '';
  }

  function _eventMatchesCountryFilter(e) {
    if (_bukmCountry === 'all') return true;
    const want = String(_bukmCountry).toLowerCase().trim();
    const ec = String(e.country || '').trim().toLowerCase();
    if (ec === want) return true;
    const lg = String(e.league || '');
    const idx = lg.indexOf(' - ');
    const head = idx > 0 ? lg.slice(0, idx).trim().toLowerCase() : '';
    if (head === want) return true;
    return false;
  }

  function _bukmIsFinished(ev) {
    const st = String(ev.match_status || '').toLowerCase();
    return st === 'finished' || st === 'cancelled' || st === 'ended' || st === 'settled';
  }

  /** LIVE: статус от API, match_today, либо время начала уже прошло (матч идёт). */
  function _bukmIsEffectivelyLive(ev) {
    if (_bukmIsFinished(ev)) return false;
    if (ev.match_status === 'live' || ev.match_today === true) return true;
    const iso = ev.match_td_utc || ev.starts_at || '';
    if (!iso) return false;
    const t = new Date(iso).getTime();
    if (Number.isNaN(t)) return false;
    return t <= Date.now();
  }

  function _bukmResolveEventType(e) {
    const raw = String(e?.sport_type || '').toLowerCase().trim();
    if (raw === 'sport' || raw === 'esport') return raw;
    const text = `${String(e?.tag || '').toLowerCase()} ${String(e?.category || '').toLowerCase()} ${String(e?.league || '').toLowerCase()}`;
    if (/(esport|e-sport|dota|cs|counter-strike|valorant|league of legends|lol)/i.test(text)) return 'esport';
    return 'sport';
  }

  function _bukmMatchesCoreFilters(e) {
    if (_bukmType !== 'all' && _bukmResolveEventType(e) !== _bukmType) return false;
    if (_bukmTag !== 'all' && e.tag !== _bukmTag) return false;
    if (_bukmMode === 'live' && !_bukmIsEffectivelyLive(e)) return false;
    if (_bukmCategory !== 'all' && String(e.category || '').toLowerCase() !== String(_bukmCategory).toLowerCase()) return false;
    if (!_eventMatchesCountryFilter(e)) return false;
    if (_bukmSearch) {
      const q = _bukmSearch.toLowerCase();
      const hay = `${e.home_team || ''} ${e.away_team || ''} ${e.league || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function _bukmEvents() {
    const events = (_sportCfg?.events || []).filter(e => e.enabled);
    return events.filter(e =>
      _bukmMatchesCoreFilters(e) &&
      (_bukmLeague === 'all' || (e.league || 'Match') === _bukmLeague)
    );
  }

  function bukmSetTag(tag) {
    _bukmTag = tag || 'all';
    _bukmLeague = 'all';
    _bukmCategory = 'all';
    _bukmCountry = 'all';
    renderBukmakingPage();
  }

  function bukmSetLeague(league) {
    _bukmLeague = league || 'all';
    renderBukmakingPage();
  }

  function bukmPick(tag, league) {
    _bukmTag = tag || 'all';
    _bukmLeague = league || 'all';
    renderBukmakingPage();
    el('bukm-live-list')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function bukmSetMode(mode) {
    _bukmMode = mode || 'all';
    renderBukmakingPage();
  }

  function bukmSetType(type) {
    _bukmType = type || 'sport';
    _bukmTag = 'all';
    _bukmCategory = 'all';
    _bukmCountry = 'all';
    renderBukmakingPage();
  }

  function bukmSearchInput(v) {
    _bukmSearch = String(v || '').trim();
    renderBukmakingPage();
  }

  function bukmTogglePanel(id) {
    el(id)?.classList.toggle('open');
  }

  function bukmToggleCat(id) {
    el(id)?.classList.toggle('open');
  }

  function bukmSetCategory(cat, country) {
    _bukmCategory = cat || 'all';
    _bukmCountry = country || 'all';
    renderBukmakingPage();
  }

  function bukmSelectMatch(id) {
    // Toggle: clicking the same card again collapses it
    _bukmSelectedMatchId = (_bukmSelectedMatchId === id) ? null : (id || null);
    renderBukmakingPage();
  }

  function _couponCoef() {
    if (!_bukmCoupon.length) return 1;
    if (_bukmCouponMode === 'single') return Number(_bukmCoupon[0]?.odd || 1);
    if (_bukmCouponMode === 'system') return Number(_bukmCoupon.reduce((a, c) => a * Number(c.odd || 1), 1) * 0.9);
    return Number(_bukmCoupon.reduce((a, c) => a * Number(c.odd || 1), 1));
  }

  function _couponStake() {
    const v = Number(el('bukm-stake-input')?.value || 0);
    return Number.isFinite(v) ? Math.max(0, v) : 0;
  }

  function bukmSetCouponMode(mode) {
    _bukmCouponMode = mode || 'single';
    renderBukmakingPage();
  }

  function bukmStakeInput() {
    const coef = _couponCoef();
    const stake = _couponStake();
    txt('bukm-coupon-win', (stake * coef).toFixed(2));
    // Sync range slider with number input
    const rng = el('bukm-stake-range');
    if (rng && Number(rng.value) !== stake) rng.value = Math.min(5000, Math.max(10, stake));
  }

  async function bukmPlaceBet() {
    if (!S.user) { modal('auth'); return; }
    if (!_bukmCoupon.length) return;

    const stake = _couponStake();
    if (!stake || stake <= 0) {
      toast(_tv('sport.bet_invalid_amount', 'Введите корректную сумму ставки'), 'err');
      return;
    }

    const btn = el('bukm-coupon-btn');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
      const r = await api('/api/sport/bet', 'POST', {
        amount: stake,
        picks:  _bukmCoupon.map(c => ({
          matchId: c.matchId,
          market:  c.market,
          title:   c.title,
          odd:     c.odd,
        })),
        mode: _bukmCouponMode,
      });

      if (r.balance != null) {
        S.user.balance = r.balance;
        const sym = csym(S.user.currency);
        txt('tb-bal-num',  r.balance.toFixed(2)); txt('tb-bal-sym',  sym);
        txt('bal-big-num', r.balance.toFixed(2)); txt('bal-big-sym', sym);
      }

      // Сохраняем ставку в историю
      try {
        const betRecord = {
          type: 'bukm',
          picks: _bukmCoupon.map(c => ({
            matchId: c.matchId,
            market: c.market,
            title: c.title,
            odd: c.odd,
            homeTeam: (_sportCfg?.events || []).find(e => e.id === c.matchId)?.home_team || '',
            awayTeam: (_sportCfg?.events || []).find(e => e.id === c.matchId)?.away_team || '',
            isLive: (_sportCfg?.events || []).find(e => e.id === c.matchId)?.match_status === 'live',
            videoUrl: (_sportCfg?.events || []).find(e => e.id === c.matchId)?.match_center?.video_embed_url || '',
            matchStartUtc: (_sportCfg?.events || []).find(e => e.id === c.matchId)?.match_td_utc
              || (_sportCfg?.events || []).find(e => e.id === c.matchId)?.starts_at || '',
          })),
          stake,
          coef: Number(r.coef || 1),
          potential_win: Number(r.potential_win || 0),
          currency: r.currency || S.user?.currency || 'UAH',
          mode: _bukmCouponMode,
          created_at: new Date().toISOString(),
          betId: r.bet_id || r.id || Date.now(),
        };
        const existing = JSON.parse(localStorage.getItem('bukm_bet_history') || '[]');
        existing.unshift(betRecord);
        localStorage.setItem('bukm_bet_history', JSON.stringify(existing.slice(0, 50)));
      } catch(e) { /* silent */ }

      _bukmCoupon = [];
      const stakeInp = el('bukm-stake-input');
      if (stakeInp) stakeInp.value = '100';

      const coef   = (r.coef || 1).toFixed(2);
      const win    = (r.potential_win || 0).toFixed(2);
      const sym    = csym(r.currency || S.user?.currency || 'UAH');
      toast(`✓ ${_tv('sport.bet_placed', 'Ставка принята')} · ${sym}${stake.toFixed(2)} × ${coef} = ${sym}${win}`, 'ok');
      renderBukmakingPage();
    } catch (e) {
      const msg = e?.message || _tv('sport.bet_error', 'Ошибка при постановке ставки');
      toast(msg, 'err');
      if (btn) { btn.disabled = false; btn.textContent = _tv('sport.place_bet', 'СДЕЛАТЬ СТАВКУ'); }
    }
  }

  function bukmSetTotLine(matchId, val) {
    _bukmMarketTabs[`${matchId}_totline`] = Number(val);
    renderBukmakingPage();
  }

  function bukmSetHcLine(matchId, val) {
    _bukmMarketTabs[`${matchId}_hcline`] = Number(val);
    renderBukmakingPage();
  }

  function bukmSetMarketTab(matchId, tab) {
    _bukmMarketTabs[matchId] = tab;
    renderBukmakingPage();
  }

  function bukmCouponRemove(key) {
    _bukmCoupon = _bukmCoupon.filter(i => i.key !== key);
    renderBukmakingPage();
  }

  function bukmPickMarket(matchId, market, title, odd) {
    const mId = String(matchId || '');
    // Lock betting for live/started matches (bookmaking rules).
    try {
      const ev = (_sportCfg?.events || []).find(e => String(e.id) === mId);
      if (ev && _bukmIsEffectivelyLive(ev)) {
        toast(_tv('sport.bet_locked_live', 'Матч уже начался — ставки недоступны'), 'err');
        return;
      }
    } catch {}
    const key = `${mId}:${market}:${title}`;
    const found = _bukmCoupon.find(i => i.key === key);
    if (found) _bukmCoupon = _bukmCoupon.filter(i => i.key !== key);
    else _bukmCoupon.push({ key, matchId: mId, market, title, odd: Number(odd || 1) });
    renderBukmakingPage();
  }

  function _bukmHasPick(matchId, market, title) {
    const key = `${String(matchId || '')}:${market}:${title}`;
    return _bukmCoupon.some(i => i.key === key);
  }

  /** Безопасный CSS linear-gradient с бэка (card_gradient) для шапки карточки. */
  function _bukmSafeCardGradient(ev) {
    const g = String(ev?.card_gradient || '').trim();
    if (!g || g.length > 480) return '';
    if (!/^linear-gradient\s*\(/i.test(g)) return '';
    if (/[<>'`]|javascript:/i.test(g)) return '';
    return g;
  }

  const _bukmFallbackCardGradients = [
    'linear-gradient(135deg,#071226 0%,#102d58 52%,#2e8fff 100%)',
    'linear-gradient(135deg,#1b0938 0%,#2b1f7a 52%,#6a5cff 100%)',
    'linear-gradient(135deg,#1a1005 0%,#5a2e0d 52%,#f08a24 100%)',
    'linear-gradient(135deg,#062016 0%,#0f4b3a 52%,#1dd1a1 100%)',
    'linear-gradient(135deg,#2a0a16 0%,#5a1231 52%,#ef476f 100%)',
    'linear-gradient(135deg,#10203b 0%,#174d74 52%,#25b0de 100%)',
  ];

  function _bukmStableHash(input) {
    const s = String(input || '0');
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  function _bukmStableCardBg(ev) {
    const key = `${ev?.id || ''}|${ev?.home_team || ''}|${ev?.away_team || ''}|${ev?.league || ''}`;
    const idx = _bukmStableHash(key) % _bukmFallbackCardGradients.length;
    return _bukmFallbackCardGradients[idx];
  }

  function _bukmNumOrNull(v) {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function _bukmExtractScore(ev) {
    const candidates = [
      [ev?.score?.home, ev?.score?.away],
      [ev?.home_score, ev?.away_score],
      [ev?.score_home, ev?.score_away],
      [ev?.match_center?.score?.home, ev?.match_center?.score?.away],
      [ev?.match_center?.home_score, ev?.match_center?.away_score],
    ];
    for (const [hRaw, aRaw] of candidates) {
      const h = _bukmNumOrNull(hRaw);
      const a = _bukmNumOrNull(aRaw);
      if (h !== null || a !== null) return { home: h ?? 0, away: a ?? 0 };
    }
    return null;
  }

  function _bukmSanitizeKitColor(raw, fallback) {
    const x = String(raw || '').trim();
    if (/^#[0-9a-fA-F]{6}$/.test(x)) return x;
    if (/^#[0-9a-fA-F]{3}$/.test(x)) return x;
    if (/^hsla?\([\d\s.,%]+\)$/.test(x)) return x;
    return fallback;
  }

  function _bukmDeriveKitColors(teamName, tag, vivid) {
    const s = String(teamName || 'team');
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    const hue1 = h % 360;
    const hue2 = (hue1 + 38 + (String(tag || '').length * 7)) % 360;
    if (vivid) {
      return {
        primary: `hsl(${hue1}, 86%, 46%)`,
        secondary: `hsl(${hue2}, 42%, 90%)`,
      };
    }
    return {
      primary: `hsl(${hue1}, 72%, 44%)`,
      secondary: `hsl(${hue2}, 48%, 92%)`,
    };
  }

  function _bukmKitColors(ev, side) {
    const team = side === 'home' ? ev.home_team : ev.away_team;
    const d = _bukmDeriveKitColors(team, ev.tag, ev.mode === 'auto');
    const pRaw = side === 'home' ? ev.home_kit_primary : ev.away_kit_primary;
    const sRaw = side === 'home' ? ev.home_kit_secondary : ev.away_kit_secondary;
    return {
      primary: _bukmSanitizeKitColor(pRaw, d.primary),
      secondary: _bukmSanitizeKitColor(sRaw, d.secondary),
    };
  }

  function _bukmJerseyHtml(ev, side) {
    const { primary, secondary } = _bukmKitColors(ev, side);
    const flip = side === 'away' ? 'transform:scaleX(-1)' : '';
    const pEsc = String(primary).replace(/"/g, '&quot;');
    const sEsc = String(secondary).replace(/"/g, '&quot;');
    return `<span class="bukm-kit-jersey" style="${flip}" aria-hidden="true">
      <svg class="bukm-kit-svg" viewBox="0 0 32 40" xmlns="http://www.w3.org/2000/svg">
        <path fill="${pEsc}" d="M8 6 L14 4 L16 8 L18 4 L24 6 L26 14 L24 36 L8 36 L6 14 Z"/>
        <path fill="${sEsc}" opacity=".92" d="M12 10 L20 10 L19 22 L13 22 Z"/>
        <rect x="10" y="28" width="12" height="6" rx="1" fill="${sEsc}"/>
      </svg>
    </span>`;
  }

  /** Дата и время матча для карточки (локаль пользователя). */
  function _bukmFormatMatchWhen(ev) {
    const iso = ev.match_td_utc || ev.starts_at || '';
    if (_bukmIsFinished(ev)) {
      return {
        kind: 'finished',
        line1: _tv('sport.match_finished_badge', 'Завершён'),
        line2: '',
      };
    }
    if (_bukmIsEffectivelyLive(ev)) {
      return {
        kind: 'live',
        line1: _tv('sport.live_badge', 'LIVE'),
        line2: String(ev.live_minutes || '').trim(),
      };
    }
    if (!iso) {
      return { kind: 'unknown', line1: _tv('sport.match_date_unknown', 'Дата уточняется'), line2: '' };
    }
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) {
      return { kind: 'unknown', line1: _tv('sport.match_date_unknown', 'Дата уточняется'), line2: '' };
    }
    const now = new Date();
    const dayStart = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = dayStart(d) - dayStart(now);
    let dayLabel;
    if (diff === 0) dayLabel = _tv('sport.match_day_today', 'Сегодня');
    else if (diff === 86400000) dayLabel = _tv('sport.match_day_tomorrow', 'Завтра');
    else dayLabel = d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return { kind: 'planned', line1: dayLabel, line2: timeStr };
  }

  function _renderBukmEventCard(ev) {
    const when = _bukmFormatMatchWhen(ev);
    const isLive = _bukmIsEffectivelyLive(ev);
    const isExpanded = _bukmSelectedMatchId === ev.id;
    const odds = ev.odds || {};
    const homeIco = ev.team_home_icon || '';
    const awayIco = ev.team_away_icon || '';

    // Cover strip background (фото или букмекерский градиент для auto-линии)
    const bg = ev.background || ev.cover || '';
    const gStrip = _bukmSafeCardGradient(ev);
    const stableBg = _bukmStableCardBg(ev);
    const coverHtml = bg
      ? `<div class="bukm-event-cover-strip" style="background-image:url('${bg.replace(/'/g,"\\'")}')" ></div>`
      : (gStrip
        ? `<div class="bukm-event-cover-strip no-img has-gradient" style="background-image:${gStrip.replace(/"/g, '&quot;')};background-size:cover;background-position:center 20%"></div>`
        : `<div class="bukm-event-cover-strip no-img has-gradient" style="background-image:${stableBg.replace(/"/g, '&quot;')};background-size:cover;background-position:center 20%"></div>`);

    const timeDisplay = `
      <div class="bukm-event-when${isLive ? ' is-live' : ''}" title="${_tv('sport.match_starts_hint', 'Начало матча')}">
        <span class="bukm-event-day">${when.line1}</span>
        ${when.line2 ? `<span class="bukm-event-clock">${when.line2}</span>` : ''}
      </div>`;

    // Score block
    const score = _bukmExtractScore(ev);
    const hasScore = !!score;
    const scoreMid = hasScore
      ? `<div class="bukm-event-score-block">
           <span class="bukm-event-score${isLive ? ' live-score' : ''}">${score.home ?? 0}:${score.away ?? 0}</span>
           ${isLive ? `<span class="bukm-event-badge live" style="font-size:8px">${_tv('sport.live_badge','LIVE')}</span>` : ''}
         </div>`
      : `<div class="bukm-event-score-block"><span class="bukm-event-vs">vs</span></div>`;

    const homeKit = _bukmJerseyHtml(ev, 'home');
    const awayKit = _bukmJerseyHtml(ev, 'away');
    const homeTeam = `<div class="bukm-event-team-block">
      <div class="bukm-event-team-name-row">
        ${homeKit}
        ${homeIco ? `<img class="bukm-event-team-ico" src="${homeIco}" alt="" onerror="this.style.display='none'">` : ''}
        <span class="bukm-event-team-name">${ev.home_team || 'Home'}</span>
      </div>
    </div>`;
    const awayTeam = `<div class="bukm-event-team-block right">
      <div class="bukm-event-team-name-row">
        ${awayKit}
        ${awayIco ? `<img class="bukm-event-team-ico" src="${awayIco}" alt="" onerror="this.style.display='none'">` : ''}
        <span class="bukm-event-team-name">${ev.away_team || 'Away'}</span>
      </div>
    </div>`;

    // Main 1X2 odds buttons
    const makeOddBtn = (market, label, odd, title, disabled) => {
      if (!Number.isFinite(Number(odd))) return '';
      const on = _bukmHasPick(ev.id, market, title) ? ' on' : '';
      const val = Number(odd).toFixed(2);
      const dis = disabled ? ' disabled' : '';
      const onClick = disabled ? '' : `onclick="event.stopPropagation();V.bukmPickMarket('${_jsStr(ev.id)}','${market}','${_jsStr(title)}','${val}')"`; 
      return `<button type="button" class="bukm-live-odd${on}${dis}" ${disabled ? 'disabled' : ''} ${onClick}>
        <span class="bukm-odd-lbl">${label}</span>
        <span class="bukm-odd-num">${val}</span>
      </button>`;
    };

    const h1Title = `${ev.home_team||'Home'} ${_tv('sport.lbl_wins','победит')}`;
    const xTitle = `${_tv('sport.lbl_draw','Ничья')}: ${ev.home_team||'Home'} vs ${ev.away_team||'Away'}`;
    const h2Title = `${ev.away_team||'Away'} ${_tv('sport.lbl_wins','победит')}`;
    const oddsHtml = `<div class="bukm-event-odds${isLive ? ' is-locked' : ''}">
        ${makeOddBtn('home', 'П1', odds.home, h1Title, isLive)}
        ${odds.draw != null && Number.isFinite(odds.draw) ? makeOddBtn('draw', 'X', odds.draw, xTitle, isLive) : ''}
        ${makeOddBtn('away', 'П2', odds.away, h2Title, isLive)}
      </div>`;

    // ── INLINE MARKETS — bookmaking-style tabbed interface ──
    const makeInlineBtn = (market, label, odd, title, sublabel, disabled) => {
      if (!Number.isFinite(Number(odd))) return '';
      const on = _bukmHasPick(ev.id, market, title) ? ' on' : '';
      const val = Number(odd).toFixed(2);
      const dis = disabled ? ' disabled' : '';
      const onClick = disabled ? '' : `onclick="event.stopPropagation();V.bukmPickMarket('${_jsStr(ev.id)}','${market}','${_jsStr(title)}','${val}')"`; 
      return `<button type="button" class="bukm-mkt-btn${on}${dis}" ${disabled ? 'disabled' : ''} ${onClick}>
        <span class="bukm-mkt-lbl">${label}</span>
        ${sublabel ? `<span class="bukm-mkt-sub">${sublabel}</span>` : ''}
        <span class="bukm-mkt-odd">${val}</span>
      </button>`;
    };

    let inlineMarketsHtml = '';
    if (isExpanded) {
      const vUrl = _normalizeVideoEmbedUrl(ev.match_center?.video_embed_url || _sportCfg?.match_center_defaults?.video_embed_url || '');
      const videoPanel = vUrl
        ? `<div class="bukm-event-stream" onclick="event.stopPropagation()">
             <div class="bukm-event-stream-head">📺 ${_tv('sport.hist_broadcast','Трансляция')}</div>
             <div class="bukm-video-wrap" style="margin-top:8px"><iframe src="${vUrl}" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen loading="lazy"></iframe></div>
           </div>`
        : '';
      const m = ev.markets || {};
      const total = m.total || {};
      const handicap = m.handicap || {};
      const matchId = String(ev.id || '');
      const activeTab = _bukmMarketTabs[matchId] || 'result';

      // Double chance (calculated from 1X2)
      const dcHomeX  = (odds.home && odds.draw) ? (1 / (1/Number(odds.home) + 1/Number(odds.draw))).toFixed(2) : null;
      const dcHomAwy = (odds.home && odds.away) ? (1 / (1/Number(odds.home) + 1/Number(odds.away))).toFixed(2) : null;
      const dcXAway  = (odds.draw && odds.away) ? (1 / (1/Number(odds.draw) + 1/Number(odds.away))).toFixed(2) : null;

      const mktTabs = [
        { key: 'result',   label: _tv('sport.mkt_result','Итог') },
        { key: 'total',    label: _tv('sport.mkt_total','Тотал') },
        { key: 'handicap', label: _tv('sport.mkt_handicap','Фора') },
        { key: 'extra',    label: _tv('sport.mkt_extra','Прочее') },
      ];
      const tabsHtml = mktTabs.map(t =>
        `<button type="button" class="bukm-mkt-tab${activeTab === t.key ? ' on' : ''}"
          onclick="event.stopPropagation();V.bukmSetMarketTab('${_jsStr(matchId)}','${t.key}')">${t.label}</button>`
      ).join('');

      let tabContent = '';
      if (activeTab === 'result') {
        const h1Title = `П1 — ${ev.home_team||'Home'}`;
        const xTitle  = _tv('sport.lbl_draw','Ничья');
        const h2Title = `П2 — ${ev.away_team||'Away'}`;
        const winRows = (m.winner || []).map(w => {
          const on = _bukmHasPick(ev.id, 'winner', w.label) ? ' on' : '';
          const val = Number(w.odd||1).toFixed(2);
          return `<button type="button" class="bukm-mkt-btn${on}"
            onclick="event.stopPropagation();V.bukmPickMarket('${_jsStr(ev.id)}','winner','${_jsStr(w.label)}','${val}')">
            <span class="bukm-mkt-lbl">W</span>
            <span class="bukm-mkt-sub">${w.label}</span>
            <span class="bukm-mkt-odd">${val}</span>
          </button>`;
        }).join('');
        tabContent = `
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_match_winner','Победитель матча')}</div>
          <div class="bukm-mkt-row">
            ${makeInlineBtn('home', 'П1', odds.home, h1Title, ev.home_team||'Home', isLive)}
            ${Number.isFinite(odds.draw) ? makeInlineBtn('draw', 'X', odds.draw, xTitle, _tv('sport.lbl_draw','Ничья'), isLive) : ''}
            ${makeInlineBtn('away', 'П2', odds.away, h2Title, ev.away_team||'Away', isLive)}
          </div>
          ${(dcHomeX || dcHomAwy || dcXAway) ? `
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_double_chance','Двойной шанс')}</div>
          <div class="bukm-mkt-row">
            ${dcHomeX  ? makeInlineBtn('dc_1x',  '1X', dcHomeX,  _tv('sport.lbl_dc_1x','Двойной шанс 1X'),  ev.home_team+' '+_tv('sport.lbl_or','или')+' '+_tv('sport.lbl_draw','Ничья'), isLive) : ''}
            ${dcHomAwy ? makeInlineBtn('dc_12',  '12', dcHomAwy, _tv('sport.lbl_dc_12','Двойной шанс 12'),  ev.home_team+' '+_tv('sport.lbl_or','или')+' '+ev.away_team, isLive) : ''}
            ${dcXAway  ? makeInlineBtn('dc_x2',  'X2', dcXAway,  _tv('sport.lbl_dc_x2','Двойной шанс X2'),  _tv('sport.lbl_draw','Ничья')+' '+_tv('sport.lbl_or','или')+' '+ev.away_team, isLive) : ''}
          </div>` : ''}
          ${winRows ? `<div class="bukm-mkt-group-label">${_tv('sport.lbl_tournament_winner','Победитель турнира')}</div><div class="bukm-mkt-row">${winRows}</div>` : ''}`;
      } else if (activeTab === 'total') {
        const line = total.line ?? 2.5;
        const overBase  = Number(total.over  || odds.over  || 1.9);
        const underBase = Number(total.under || odds.under || 1.9);
        // Slider steps: line-1 to line+2 in 0.5 steps
        const sliderMin  = line - 1;
        const sliderMax  = line + 2;
        const sliderStep = 0.5;
        const sliderId   = `tot-slider-${matchId}`;
        const sliderVal  = _bukmMarketTabs[`${matchId}_totline`] ?? line;
        const adj  = (sliderVal - line) * 0.12;
        const ovSl = Math.max(1.01, overBase  + adj).toFixed(2);
        const unSl = Math.max(1.01, underBase - adj).toFixed(2);
        tabContent = `
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_total_line','Тотал — выбери линию')}</div>
          <div class="bukm-slider-wrap" onclick="event.stopPropagation()">
            <div class="bukm-slider-labels">
              <span class="bukm-slider-min">${sliderMin}</span>
              <span class="bukm-slider-val">${sliderVal}</span>
              <span class="bukm-slider-max">${sliderMax}</span>
            </div>
            <input type="range" class="bukm-range" id="${sliderId}"
              min="${sliderMin}" max="${sliderMax}" step="${sliderStep}" value="${sliderVal}"
              oninput="V.bukmSetMarketTab('${_jsStr(matchId)}','total');V.bukmSetTotLine('${_jsStr(matchId)}',this.value);this.previousElementSibling.children[1].textContent=this.value">
          </div>
          <div class="bukm-mkt-row" style="margin-top:10px">
            ${makeInlineBtn(`total_over_${sliderVal}`,  'Б', ovSl, `${_tv('sport.over','Больше')} ${sliderVal}`, `Over ${sliderVal}`, isLive)}
            ${makeInlineBtn(`total_under_${sliderVal}`, 'М', unSl, `${_tv('sport.under','Меньше')} ${sliderVal}`, `Under ${sliderVal}`, isLive)}
          </div>
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_corners_cards','Угловые / Карточки')}</div>
          <div class="bukm-mkt-row">
            ${makeInlineBtn('corners_over',  _tv('sport.lbl_corn_o','УГ Б'), (overBase  * 0.97).toFixed(2), _tv('sport.lbl_corners_over','Угловые Б > 9.5'), '> 9.5', isLive)}
            ${makeInlineBtn('corners_under', _tv('sport.lbl_corn_u','УГ М'), (underBase * 0.97).toFixed(2), _tv('sport.lbl_corners_under','Угловые М < 9.5'), '< 9.5', isLive)}
            ${makeInlineBtn('cards_over',    _tv('sport.lbl_card_o','КР Б'), (overBase  * 1.03).toFixed(2), _tv('sport.lbl_cards_over','Карточки Б > 3.5'), '> 3.5', isLive)}
            ${makeInlineBtn('cards_under',   _tv('sport.lbl_card_u','КР М'), (underBase * 1.03).toFixed(2), _tv('sport.lbl_cards_under','Карточки М < 3.5'), '< 3.5', isLive)}
          </div>`;
      } else if (activeTab === 'handicap') {
        const hLine  = handicap.line ?? -1.5;
        const hBase  = Number(handicap.home || 1.88);
        const aBase  = Number(handicap.away || 1.92);
        const hcSliderId = `hc-slider-${matchId}`;
        const hcMin  = hLine - 1.5;
        const hcMax  = hLine + 1.5;
        const hcVal  = _bukmMarketTabs[`${matchId}_hcline`] ?? hLine;
        const hcAdj  = Math.abs(hcVal - hLine) * 0.08;
        const hcSign = Math.sign(hcVal - hLine) || 0;
        const hcH    = Math.max(1.01, hBase + hcAdj * hcSign).toFixed(2);
        const hcA    = Math.max(1.01, aBase - hcAdj * hcSign).toFixed(2);
        const hcStr  = (hcVal >= 0 ? '+' : '') + hcVal;
        tabContent = `
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_asian_hc','Азиатская фора — выбери линию')}</div>
          <div class="bukm-slider-wrap" onclick="event.stopPropagation()">
            <div class="bukm-slider-labels">
              <span class="bukm-slider-min">${(hcMin >= 0 ? '+' : '') + hcMin}</span>
              <span class="bukm-slider-val">${hcStr}</span>
              <span class="bukm-slider-max">${(hcMax >= 0 ? '+' : '') + hcMax}</span>
            </div>
            <input type="range" class="bukm-range" id="${hcSliderId}"
              min="${hcMin}" max="${hcMax}" step="0.5" value="${hcVal}"
              oninput="V.bukmSetMarketTab('${_jsStr(matchId)}','handicap');V.bukmSetHcLine('${_jsStr(matchId)}',this.value);this.previousElementSibling.children[1].textContent=(this.value>=0?'+':'')+this.value">
          </div>
          <div class="bukm-mkt-row" style="margin-top:10px">
            ${makeInlineBtn(`hc_home_${hcVal}`, _tv('sport.lbl_hc1','Ф1'), hcH, `${_tv('sport.handicap','Фора')} ${ev.home_team||'П1'} (${hcStr})`, ev.home_team||'П1', isLive)}
            ${makeInlineBtn(`hc_away_${hcVal}`, _tv('sport.lbl_hc2','Ф2'), hcA, `${_tv('sport.handicap','Фора')} ${ev.away_team||'П2'} (${((-hcVal) >= 0 ? '+' : '') + (-hcVal)})`, ev.away_team||'П2', isLive)}
          </div>`;
      } else if (activeTab === 'extra') {
        const hasBtts = Number.isFinite(odds.home) && Number.isFinite(odds.away);
        tabContent = `
          ${hasBtts ? `
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_btts','Обе команды забьют')}</div>
          <div class="bukm-mkt-row">
            ${makeInlineBtn('btts_yes', _tv('sport.lbl_yes','Да'),  (1.65).toFixed(2), _tv('sport.lbl_btts_yes','Обе забьют — Да'), '', isLive)}
            ${makeInlineBtn('btts_no',  _tv('sport.lbl_no','Нет'), (2.10).toFixed(2), _tv('sport.lbl_btts_no','Обе забьют — Нет'), '', isLive)}
          </div>` : ''}
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_exact_score','Точный счёт')}</div>
          <div class="bukm-mkt-row">
            ${makeInlineBtn('cs_1_1', '1:1', (3.50).toFixed(2), _tv('sport.lbl_score_1_1','Точный счёт 1:1'), '', isLive)}
            ${makeInlineBtn('cs_1_0', '1:0', (5.20).toFixed(2), _tv('sport.lbl_score_1_0','Точный счёт 1:0'), '', isLive)}
            ${makeInlineBtn('cs_0_1', '0:1', (5.80).toFixed(2), _tv('sport.lbl_score_0_1','Точный счёт 0:1'), '', isLive)}
            ${makeInlineBtn('cs_2_1', '2:1', (7.00).toFixed(2), _tv('sport.lbl_score_2_1','Точный счёт 2:1'), '', isLive)}
          </div>
          <div class="bukm-mkt-group-label">${_tv('sport.lbl_goal_in_match','Гол в матче')}</div>
          <div class="bukm-mkt-row">
            ${makeInlineBtn('goal_yes', _tv('sport.lbl_yes','Да'),    (1.28).toFixed(2), _tv('sport.lbl_goal_yes','Гол в матче — Да'), '', isLive)}
            ${makeInlineBtn('goal_no',  _tv('sport.lbl_no_goals','Без голов'), (3.80).toFixed(2), _tv('sport.lbl_goal_no','Гол в матче — Нет'), '', isLive)}
          </div>`;
      }

      inlineMarketsHtml = `<div class="bukm-event-markets" onclick="event.stopPropagation()">
        ${videoPanel}
        <div class="bukm-mkt-tabs">${tabsHtml}</div>
        <div class="bukm-mkt-content">${tabContent}</div>
      </div>`;
    } else {
      inlineMarketsHtml = `<div class="bukm-event-markets collapsed"></div>`;
    }

    // Expand/collapse toggle button — stopPropagation prevents double-firing with card onclick
    const toggleBtn = `<button type="button" class="bukm-expand-toggle${isExpanded ? ' expanded-btn' : ''}${isLive ? ' live-locked' : ''}" onclick="event.stopPropagation();V.bukmSelectMatch('${_jsStr(ev.id)}')">
      <svg width="11" height="11" viewBox="0 0 20 20" fill="none"><path d="M5 8l5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      ${isLive ? _tv('sport.live_only_info','LIVE') : (isExpanded ? _tv('sport.hide_markets','Скрыть маркеты') : _tv('sport.more_markets','Все маркеты'))}
    </button>`;

    const prov = _bukmProviderId(ev);
    const provLbl = prov === 'odds_api_io' ? _tv('sport.badge_provider_io', 'LINE') : (prov === 'the_odds_api' ? _tv('sport.badge_provider_v4', 'ODDS') : '');
    const provBadge = provLbl ? `<span class="bukm-event-badge provider">${provLbl}</span>` : '';

    // Badges top row
    const badges = `
      ${provBadge}
      <span class="bukm-event-badge ks">${_tv('sport.bet_constructor_badge','КС')}</span>
      ${isLive ? `<span class="bukm-event-badge live">${_tv('sport.live_badge','LIVE')}</span>` : ''}`;

    const expandedClass = isExpanded ? ' expanded' : '';

    return `<div class="bukm-event-card${expandedClass}" data-ev-id="${_jsStr(ev.id)}" onclick="V.bukmSelectMatch('${_jsStr(ev.id)}')">
      ${coverHtml}
      <div class="bukm-event-inner">
        <div class="bukm-event-head">
          ${badges}
          <span class="bukm-event-league">${ev.league || ''}</span>
          ${timeDisplay}
        </div>
        <div class="bukm-event-teams-row">
          ${homeTeam}
          ${scoreMid}
          ${awayTeam}
        </div>
        ${oddsHtml}
        ${toggleBtn}
        ${inlineMarketsHtml}
      </div>
    </div>`;
  }

  function renderBukmakingPage() {
    const tabsEl = el('bukmaking-tabs');
    const modeEl = el('bukm-mode-switch');
    const typeEl = el('bukm-type-switch');
    const topLiveRow = el('bukm-top-live-row');
    const liveListEl = el('bukm-live-list');
    const popularListEl = el('bukm-popular-list');
    const plannedListEl = el('bukm-planned-list');
    const popularTabsEl = el('bukm-popular-tabs');
    const plannedTabsEl = el('bukm-planned-tabs');
    const focusEl = el('bukm-focus-card');
    const marketEl = el('bukm-market-card');
    const matchCenterEl = el('bukm-match-center-card');
    const emptyEl = el('bukmaking-empty');
    const leaguesEl = el('bukm-leagues');
    const catMenuEl = el('bukm-categories-menu');
    if (!emptyEl) return;

    const tags = (_sportCfg?.tags || []);
    const events = (_sportCfg?.events || []).filter(e => e.enabled);
    const filtered = _bukmEvents();

    const allLabel = _tv('common.all', (_lang === 'UA' ? 'Усі' : (_lang === 'US' ? 'All' : 'Все')));

    if (modeEl) {
      const modes = _sportCfg?.ui_filters?.modes || [{ id: 'all', label: 'All' }, { id: 'live', label: 'LIVE' }];
      modeEl.innerHTML = modes.map(m => `<button class="bukm-switch-btn${_bukmMode === m.id ? ' on' : ''}" type="button" onclick="V.bukmSetMode('${_jsStr(m.id)}')">${m.label}</button>`).join('');
    }
    if (typeEl) {
      const types = _sportCfg?.ui_filters?.types || [{ id: 'sport', label: 'Sport' }, { id: 'esport', label: 'E-Sport' }];
      typeEl.innerHTML = types.map(t => `<button class="bukm-switch-btn${_bukmType === t.id ? ' on' : ''}" type="button" onclick="V.bukmSetType('${_jsStr(t.id)}')">${t.label}</button>`).join('');
    }

    const sInp = el('bukm-search-input');
    if (sInp && sInp.value !== _bukmSearch) {
      sInp.value = _bukmSearch;
      if (!sInp.dataset.bukmBind) {
        sInp.dataset.bukmBind = '1';
        sInp.addEventListener('input', (e) => bukmSearchInput(e.target.value));
      }
    }

    const topLiveWrap = topLiveRow?.closest('.bukm-live-strip-wrap');
    if (topLiveWrap) topLiveWrap.style.display = 'none';
    if (topLiveRow) {
      const liveTop = events
        .filter(e => _bukmIsEffectivelyLive(e))
        .sort((a, b) => new Date(a.match_td_utc || a.starts_at || 0).getTime() - new Date(b.match_td_utc || b.starts_at || 0).getTime())
        .slice(0, 10);
      topLiveRow.innerHTML = liveTop.map(ev => {
        const when = _bukmFormatMatchWhen(ev);
        const metaTime = when.kind === 'live'
          ? `${when.line1}${when.line2 ? ' · ' + when.line2 : ''}`
          : [when.line1, when.line2].filter(Boolean).join(' · ');
        const bg = ev.background || ev.cover || '';
        const gChip = _bukmSafeCardGradient(ev);
        const odds = ev.odds || {};
        const h = Number.isFinite(odds.home) ? odds.home.toFixed(2) : '-';
        const d = Number.isFinite(odds.draw) ? odds.draw.toFixed(2) : '-';
        const a = Number.isFinite(odds.away) ? odds.away.toFixed(2) : '-';
        const bgStyle = bg
          ? `style="background-image:url('${bg.replace(/'/g, "\\'")}')"`
          : (gChip ? `style="background-image:${gChip.replace(/"/g, '&quot;')};background-size:cover;background-position:center"` : '');
        return `<div class="provider-chip top-match-chip bukm-live-chip" ${bgStyle} onclick="V.bukmSelectMatch('${_jsStr(ev.id)}')">
          <div class="top-match-head">
            <span class="top-match-league">${ev.league || 'Match'}</span>
            <span class="provider-chip-time">${metaTime}</span>
          </div>
          <div class="bukm-live-pill">LIVE</div>
          <span class="provider-chip-name">${ev.home_team || 'Team 1'} <span class="top-match-vs">vs</span> ${ev.away_team || 'Team 2'}</span>
          <div class="top-match-odds"><span>${h}</span><span>X ${d}</span><span>${a}</span></div>
        </div>`;
      }).join('');
      _enableHoldSwipe(topLiveRow);
    }

    if (catMenuEl) {
      const menu = _sportCfg?.sports_menu || [];
      const navPool = events.filter(e => {
        if (_bukmType !== 'all' && _bukmResolveEventType(e) !== _bukmType) return false;
        if (_bukmTag !== 'all' && e.tag !== _bukmTag) return false;
        if (_bukmMode === 'live' && !_bukmIsEffectivelyLive(e)) return false;
        if (_bukmSearch) {
          const q = _bukmSearch.toLowerCase();
          const hay = `${e.home_team || ''} ${e.away_team || ''} ${e.league || ''}`.toLowerCase();
          if (!hay.includes(q)) return false;
        }
        return true;
      });
      // Track open state of cat-blocks across re-renders
      const prevOpen = {};
      (catMenuEl.querySelectorAll?.('.bukm-cat-block') || []).forEach(b => { prevOpen[b.id] = b.classList.contains('open'); });
      catMenuEl.innerHTML = menu.map((m, idx) => {
        const blockId = `bukm-cat-${idx}`;
        const wasOpen = blockId in prevOpen ? prevOpen[blockId] : m.expanded;
        const blockTag = String(m.tag || m.id || '').replace(/_menu$/, '').toLowerCase();
        const blockIconPath = (tags.find(t => String(t.id || '').toLowerCase() === blockTag)?.icon_path) || '';
        const flagMap = { 'Spain':'🇪🇸','England':'🏴󠁧󠁢󠁥󠁮󠁧󠁿','Germany':'🇩🇪','France':'🇫🇷','Italy':'🇮🇹',
          'Portugal':'🇵🇹','Netherlands':'🇳🇱','USA':'🇺🇸','Ukraine':'🇺🇦','Russia':'🇷🇺',
          'Kazakhstan':'🇰🇿','Brazil':'🇧🇷','Argentina':'🇦🇷','Turkey':'🇹🇷','All':'🌍' };
        const children = (m.children || []).map(c => {
          const countries = c.countries || [];
          if (c.label === 'All' || countries[0] === 'All') {
            const sportCat = String(m.title || '').trim();
            const active = (_bukmCategory === sportCat && _bukmCountry === 'all') ? ' on' : '';
            const count = navPool.filter(ev => String(ev.category || '').toLowerCase() === String(sportCat).toLowerCase()).length;
            return `<button class="bukm-cat-child${active}" type="button" onclick="V.bukmSetCategory('${_jsStr(sportCat)}','all')">
              <span class="bukm-cat-child-name">${blockIconPath ? `<img class="bukm-cat-icon" src="${blockIconPath}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block'">` : ''}<span class="bukm-cat-child-badge"${blockIconPath ? ' style="display:none"' : ''}>${_sportEmoji(String(m.title || '').toLowerCase())}</span>${_tv('sport.whole_sport_worldwide', 'Весь вид спорта')}</span>
              <span class="bukm-cat-child-count">${count}</span>
            </button>`;
          }
          // Each country gets its own button
          return countries.map(country => {
            const targetCat = m.title;
            const active = (_bukmCategory === targetCat && _bukmCountry === country) ? ' on' : '';
            const isEsportBlock = /esport/i.test(String(blockTag));
            const flag = flagMap[country] || '🏳️';
            const cLower = String(country || '').toLowerCase().trim();
            const count = navPool.filter(ev => {
              if (String(ev.category || '').toLowerCase() !== String(targetCat || '').toLowerCase()) return false;
              const ec = String(ev.country || '').toLowerCase().trim();
              if (ec === cLower) return true;
              const lg = String(ev.league || '');
              const idx = lg.indexOf(' - ');
              const head = idx > 0 ? lg.slice(0, idx).trim().toLowerCase() : '';
              return head === cLower;
            }).length;
            return `<button class="bukm-cat-child${active}" type="button" onclick="V.bukmSetCategory('${_jsStr(targetCat)}','${_jsStr(country)}')">
              <span class="bukm-cat-child-name">${isEsportBlock ? '' : `${flag} `}${country}</span>
              ${blockIconPath ? `<img class="bukm-cat-icon" src="${blockIconPath}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block'"><span class="bukm-cat-child-badge" style="display:none">${_sportEmoji(m.title.toLowerCase())}</span>` : `<span class="bukm-cat-child-badge">${_sportEmoji(m.title.toLowerCase())}</span>`}
              <span class="bukm-cat-child-count">${count}</span>
            </button>`;
          }).join('');
        }).join('');
        const titleCount = navPool.filter(ev => String(ev.category || '').toLowerCase() === String(m.title || '').toLowerCase()).length;
        return `<div class="bukm-cat-block${wasOpen ? ' open' : ''}" id="${blockId}">
          <button class="bukm-cat-head" type="button" onclick="V.bukmToggleCat('${blockId}')"><span class="bukm-cat-head-title">${blockIconPath ? `<img class="bukm-cat-icon" src="${blockIconPath}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-block'">` : ''}<span class="bukm-cat-child-badge"${blockIconPath ? ' style="display:none"' : ''}>${_sportEmoji(String(m.title || '').toLowerCase())}</span>${m.title}</span><span class="bukm-cat-head-meta"><span class="bukm-cat-head-count">${titleCount}</span><span class="bukm-cat-arr">▸</span></span></button>
          <div class="bukm-cat-sub">${children}</div>
        </div>`;
      }).join('');
    }

    if (leaguesEl) {
      const tree = _sportCfg?.leagues_tree || [];
      const poolSansLeague = events.filter(e => _bukmMatchesCoreFilters(e));
      const tagIconMap = {};
      tags.forEach(t => { tagIconMap[String(t.id || '').toLowerCase()] = t.icon_path || ''; });
      const leagueCounts = {};
      poolSansLeague.forEach(ev => {
        const l = ev.league || 'Match';
        leagueCounts[l] = (leagueCounts[l] || 0) + 1;
      });
      const topItems = (tree.find(t => t.id === 'top')?.items || []);
      const myItems = (tree.find(t => t.id === 'my')?.items || []);
      const treeSet = new Set([...topItems, ...myItems].map(String));
      const dynFreq = new Map();
      poolSansLeague.forEach(ev => {
        const l = ev.league || 'Match';
        if (!treeSet.has(l)) dynFreq.set(l, (dynFreq.get(l) || 0) + 1);
      });
      const dynamicLeagues = [...dynFreq.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([name]) => name)
        .slice(0, _BUKM_DYNAMIC_LEAGUES_MAX);
      const sepLabel = _tv('sport.provider_leagues', 'Линия провайдера');
      const renderLeagueBtn = (name) => {
        const on = _bukmLeague === name;
        const count = leagueCounts[name] || 0;
        const evSample = poolSansLeague.find(e => (e.league || 'Match') === name);
        const emoji = _sportEmoji(evSample?.tag);
        const iconPath = tagIconMap[String(evSample?.tag || '').toLowerCase()] || '';
        const icon = iconPath
          ? `<img class="bukm-league-icon" src="${iconPath}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><span class="bukm-league-emoji" style="display:none">${emoji}</span>`
          : `<span class="bukm-league-emoji">${emoji}</span>`;
        return `<button type="button" class="bukm-league-item${on ? ' on' : ''}" onclick="V.bukmSetLeague('${_jsStr(name)}')">${icon}<span class="bukm-league-name">${name}</span><span class="bukm-league-count">${count}</span></button>`;
      };
      leaguesEl.innerHTML =
        `<button type="button" class="bukm-league-item${_bukmLeague === 'all' ? ' on' : ''}" onclick="V.bukmSetLeague('all')"><span class="bukm-league-emoji"><svg viewBox="0 0 20 20" width="14" height="14" fill="none"><path d="M10 2l2.5 5 5.5.8-4 3.9 1 5.5L10 14.5 5 17.2l1-5.5L2 7.8l5.5-.8L10 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg></span><span class="bukm-league-name">${_tv('sport.all_leagues', 'Все чемпионаты')}</span><span class="bukm-league-count">${poolSansLeague.length}</span></button>` +
        topItems.map(renderLeagueBtn).join('') +
        myItems.map(renderLeagueBtn).join('') +
        (dynamicLeagues.length
          ? `<div class="bukm-league-subhdr" style="padding:8px 6px 4px;font-size:11px;letter-spacing:.06em;color:var(--t3);text-transform:uppercase">${sepLabel}</div>`
          : '') +
        dynamicLeagues.map(renderLeagueBtn).join('');
      txt('bukm-top-league-label', _tv('sport.top_league', _sportCfg?.ui_filters?.top_league_label || 'Top League'));
      txt('bukm-my-league-label', _tv('sport.my_league', _sportCfg?.ui_filters?.my_league_label || 'My League'));
    }

    filtered.sort((a, b) => new Date(a.starts_at || a.match_td_utc || 0).getTime() - new Date(b.starts_at || b.match_td_utc || 0).getTime());
    const tabsHtml = [
      `<button type="button" class="bukm-tab${_bukmTag === 'all' ? ' on' : ''}" onclick="V.bukmSetTag('all')">${allLabel}</button>`
    ].concat(tags.filter(t => {
      if (_bukmType === 'all') return true;
      const isEs = String(t.id || '').toLowerCase().includes('esport');
      return _bukmType === 'esport' ? isEs : !isEs;
    }).map(t =>
      `<button type="button" class="bukm-tab${_bukmTag === t.id ? ' on' : ''}" onclick="V.bukmSetTag('${t.id}')">${t.label}</button>`
    )).join('');
    if (tabsEl) tabsEl.innerHTML = tabsHtml;

    const sourceForLists = filtered;
    const liveItems = sourceForLists.filter(e => _bukmIsEffectivelyLive(e));
    const liveIds = new Set(liveItems.map(e => e.id));
    const nonLiveItems = sourceForLists.filter(e => !liveIds.has(e.id));
    const nonFin = nonLiveItems.filter(e => !_bukmIsFinished(e));
    const popularItems = nonFin.slice(0, 6);
    const plannedItems = nonFin.filter(e => !_bukmIsEffectivelyLive(e));

    if (liveListEl) liveListEl.innerHTML = liveItems.map(_renderBukmEventCard).join('');
    if (popularListEl) popularListEl.innerHTML = popularItems.map(_renderBukmEventCard).join('');
    if (plannedListEl) plannedListEl.innerHTML = plannedItems.map(_renderBukmEventCard).join('');

    // Update section counts + hide empty sections
    const liveCountEl = el('bukm-live-count');
    if (liveCountEl) liveCountEl.textContent = liveItems.length || '';
    const liveSec = el('bukm-live-section');
    if (liveSec) liveSec.style.display = liveItems.length ? '' : 'none';
    const popularSec = el('bukm-popular-section');
    if (popularSec) popularSec.style.display = popularItems.length ? '' : 'none';
    const plannedSec = el('bukm-planned-section');
    if (plannedSec) plannedSec.style.display = plannedItems.length ? '' : 'none';

    if (popularTabsEl) {
      const cats = [...new Set(filtered.map(e => e.category).filter(Boolean))];
      popularTabsEl.innerHTML = cats.map(c => `<button class="bukm-mini-tab${_bukmCategory === c ? ' on' : ''}" type="button" onclick="V.bukmSetCategory('${_jsStr(c)}','all')">${c}</button>`).join('');
    }
    if (plannedTabsEl) {
      const types = [{ id: 'sport', label: 'Sport' }, { id: 'esport', label: 'E-Sport' }];
      plannedTabsEl.innerHTML = types.map(t => `<button class="bukm-mini-tab${_bukmType === t.id ? ' on' : ''}" type="button" onclick="V.bukmSetType('${t.id}')">${t.label}</button>`).join('');
    }

    if (!sourceForLists.length) {
      emptyEl.classList.remove('hidden');
      return;
    }
    emptyEl.classList.add('hidden');

    // Only change selection if current match is not in the filtered list
    const selectionValid = _bukmSelectedMatchId && sourceForLists.some(e => e.id === _bukmSelectedMatchId);
    if (!selectionValid && _bukmSelectedMatchId !== null) {
      _bukmSelectedMatchId = null;
    }
    const selected = sourceForLists.find(e => e.id === _bukmSelectedMatchId) || null;

    // Правая focusEl — показывает выбранный матч + видео (если задано)
    if (focusEl) {
      if (selected) {
        const isSelLive = selected.match_status === 'live';
        const periods = selected.match_center?.score_by_period || selected.period_scores || [];
        const mainScore = _bukmExtractScore(selected);
        const periodsHtml = periods.length
          ? periods.map(p => `<span>${p.period}: ${p.home}-${p.away}</span>`).join(' · ')
          : null;
        focusEl.style.display = '';
        focusEl.innerHTML = `
          <div class="bukm-focus-card-header">
            <span class="bukm-focus-card-league">${selected.league || ''}</span>
            ${isSelLive ? `<span class="bukm-focus-card-live">&#9679; LIVE ${selected.live_minutes || ''}</span>` : ''}
          </div>
          <div class="bukm-focus-score">
            <div class="bukm-focus-team">${selected.home_team || ''}</div>
            <div class="bukm-focus-mid">${mainScore ? `${mainScore.home}:${mainScore.away}` : 'vs'}</div>
            <div class="bukm-focus-team right">${selected.away_team || ''}</div>
          </div>
          ${periodsHtml ? `<div style="font-size:10px;color:var(--t3);text-align:center;margin-top:8px">${periodsHtml}</div>` : ''}`;
      } else {
        focusEl.style.display = 'none';
      }
    }

    // Правые панели маркетов и матч-центра скрываем — маркеты теперь inline в карточке
    if (marketEl) marketEl.style.display = 'none';
    if (matchCenterEl) matchCenterEl.style.display = 'none';

    const modesWrap = el('bukm-coupon-modes');
    const linesWrap = el('bukm-coupon-lines');
    const emptyCoupon = el('bukm-coupon-empty');
    const stakeInp = el('bukm-stake-input');
    if (modesWrap) {
      const modes = [
        { id: 'single', label: _tv('sport.coupon_single', 'Ординар') },
        { id: 'express', label: _tv('sport.coupon_express', 'Экспресс') },
        { id: 'system', label: _tv('sport.coupon_system', 'Система') },
      ];
      modesWrap.innerHTML = modes.map(m => `<button class="bukm-coupon-mode${_bukmCouponMode === m.id ? ' on' : ''}" type="button" onclick="V.bukmSetCouponMode('${m.id}')">${m.label}</button>`).join('');
    }
    if (stakeInp && !stakeInp.dataset.bound) {
      stakeInp.dataset.bound = '1';
      stakeInp.addEventListener('input', bukmStakeInput);
    }
    if (linesWrap) {
      linesWrap.innerHTML = _bukmCoupon.map(c =>
        `<div class="bukm-coupon-line">
          <div class="bukm-coupon-line-top">
            <div class="bukm-coupon-line-title">${c.title}</div>
            <span class="bukm-coupon-line-odd">${Number(c.odd || 1).toFixed(2)}</span>
            <button type="button" class="bukm-coupon-line-del" title="${_tv('sport.remove','Удалить')}" onclick="V.bukmCouponRemove('${_jsStr(c.key)}')">✕</button>
          </div>
        </div>`
      ).join('');
    }
    if (emptyCoupon) emptyCoupon.style.display = _bukmCoupon.length ? 'none' : '';
    // Update badge count on coupon header
    const badge = el('bukm-coupon-count');
    if (badge) badge.textContent = _bukmCoupon.length;
    const coef = _couponCoef();
    txt('bukm-coupon-total', coef.toFixed(2));
    txt('bukm-coupon-win', (_couponStake() * coef).toFixed(2));
    const submitBtn = el('bukm-coupon-btn');
    if (submitBtn) {
      submitBtn.disabled = !_bukmCoupon.length;
      submitBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 20 20" fill="none"><path d="M3 10l5 5 9-9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> ' + _tv('sport.place_bet', 'СДЕЛАТЬ СТАВКУ');
    }
  }

  function bukmSetStake(amount) {
    const inp = el('bukm-stake-input');
    if (inp) { inp.value = amount; bukmStakeInput({ target: inp }); }
  }

  function _sportEmoji(tag) {
    const map = { football:'⚽', basketball:'🏀', tennis:'🎾', hockey:'🏒', mma:'🥊', esports:'🎮', boxing:'🥊', kickboxing:'🥋', baseball:'⚾', volleyball:'🏐', rugby:'🏉', golf:'⛳', cycling:'🚴', swimming:'🏊', athletics:'🏃' };
    return map[tag] || '🏆';
  }

  function _buildOddsHtml(ev) {
    const o = ev.odds || {};
    const h1 = _t('home.coef_home');
    const hx = _t('home.coef_draw') || 'Х';
    const h2 = _t('home.coef_away');
    let html = `<div class="sport-card-odds">
      <button class="sport-odd-btn"><span class="sport-odd-label">${h1}</span><span class="sport-odd-val">${(o.home||0).toFixed(2)}</span></button>`;
    if (o.draw != null) {
      html += `<button class="sport-odd-btn"><span class="sport-odd-label">${hx}</span><span class="sport-odd-val">${(o.draw||0).toFixed(2)}</span></button>`;
    }
    html += `<button class="sport-odd-btn"><span class="sport-odd-label">${h2}</span><span class="sport-odd-val">${(o.away||0).toFixed(2)}</span></button></div>`;
    return html;
  }

  // ── HIGH RTP PARSER ──────────────────────────────────────────────────────────
  function buildHighRtpSection() {
    // Filter games that have rtp >= 0.95 (95%)
    const RTP_THRESHOLD = 0.95;
    const highRtp = S.games
      .filter(g => g.active && typeof g.rtp === 'number' && g.rtp >= RTP_THRESHOLD)
      .sort((a,b) => (b.rtp||0) - (a.rtp||0))
      .slice(0, 8);
    const sec = el('high-rtp-section');
    if (!sec) return;
    if (!highRtp.length) { sec.style.display = 'none'; return; }
    sec.style.display = '';
    renderGrid('home-rtp-grid', highRtp);
  }

  // ── LOBBY TABS ───────────────────────────────────────────────────────────────
  let _lobbyTab = 'velox';
  let _lobbyPage = 0;
  const LOBBY_PAGE_SIZE = 8;

  function lobbyTab(btn, tab) {
    document.querySelectorAll('.ltab').forEach(b => b.classList.remove('on'));
    if (btn) btn.classList.add('on');
    _lobbyTab = tab;
    _lobbyPage = 0;
    _renderLobbyGames(true);
  }

  function lobbyShowMore() {
    _lobbyPage++;
    _renderLobbyGames(false);
  }

  function _getLobbyGames(tab) {
    const all = S.games.filter(g => g.active !== false);
    switch(tab) {
      case 'velox':    return all.filter(g => (g.provider_tag||'').toLowerCase() === 'velox' || g.featured);
      case 'popular':  return all.filter(g => g.hot || g.featured).concat(all.filter(g => !g.hot && !g.featured));
      case 'new':      return all.filter(g => g.new).concat(all.filter(g => !g.new));
      case 'wager':    return all.filter(g => (g.tags||[]).includes('fs'));
      case 'topmonth': return [...all].reverse();
      case 'bonusbuy': return all.filter(g => (g.tags||[]).includes('buy_bonus') || (g.tags||[]).some(t => t.toLowerCase().includes('bonus')));
      case 'netgame':  return all.filter(g => (g.provider||'').toLowerCase().includes('netgame') || (g.provider_tag||'').toLowerCase().includes('netgame'));
      case 'crash':    return all.filter(g => (g.category||'') === 'crash' || (g.tags||[]).includes('crash'));
      case 'all':      return all;
      default:         return all;
    }
  }

  function _renderLobbyGames(reset) {
    const games = _getLobbyGames(_lobbyTab);
    const start = 0;
    const end = (_lobbyPage + 1) * LOBBY_PAGE_SIZE;
    const slice = games.slice(start, end);
    renderGrid('home-grid', slice);
    const btn = el('home-showmore');
    if (btn) btn.style.display = end < games.length ? '' : 'none';
  }

  // ── PROVIDERS ROW ────────────────────────────────────────────────────────────
  function buildProvidersRow() {
    const row = el('home-top-matches-row'); if (!row) return;
    _enableHoldSwipe(row);
    const sec = el('providers-section');
    const events = (_sportCfg?.events || [])
      .filter(e => e.enabled && e.match_today === true && (e.match_td_utc || e.starts_at))
      .sort((a,b) => new Date(a.match_td_utc || a.starts_at).getTime() - new Date(b.match_td_utc || b.starts_at).getTime())
      .slice(0, 10);
    if (!events.length) {
      if (sec) sec.style.display = 'none';
      return;
    }
    if (sec) sec.style.display = '';
    row.innerHTML = events.map(ev => {
      const dt = ev.match_td_utc || ev.starts_at || '';
      const t = dt ? new Date(dt).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '--:--';
      const bg = ev.background || ev.cover || '';
      const gChip = _bukmSafeCardGradient(ev);
      const odds = ev.odds || {};
      const h = Number.isFinite(odds.home) ? odds.home.toFixed(2) : '-';
      const d = Number.isFinite(odds.draw) ? odds.draw.toFixed(2) : '-';
      const a = Number.isFinite(odds.away) ? odds.away.toFixed(2) : '-';
      const bgStyle = bg
        ? `style="background-image:url('${bg.replace(/'/g, "\\'")}')"`
        : (gChip ? `style="background-image:${gChip.replace(/"/g, '&quot;')};background-size:cover;background-position:center"` : '');
      return `<div class="provider-chip top-match-chip" ${bgStyle} onclick="if(this.parentElement.classList.contains('drag-moved'))return;V.go('sport')">
        <div class="top-match-head">
          <span class="top-match-league">${ev.league || 'Match'}</span>
          <span class="provider-chip-time">${t}</span>
        </div>
        <span class="provider-chip-name">${ev.home_team||'Team 1'} <span class="top-match-vs">vs</span> ${ev.away_team||'Team 2'}</span>
        <div class="top-match-odds">
          <span>${h}</span>
          <span>X ${d}</span>
          <span>${a}</span>
        </div>
      </div>`;
    }).join('');
  }

  function _enableHoldSwipe(node) {
    if (!node || node.dataset.dragReady === '1') return;
    node.dataset.dragReady = '1';
    let isDown = false;
    let startX = 0;
    let startLeft = 0;
    let moved = false;
    let lastX = 0;
    let lastT = 0;
    let vel = 0;
    let raf = 0;
    let glideRaf = 0;
    node.dataset.lastDragAt = node.dataset.lastDragAt || '0';

    const stopGlide = () => {
      if (glideRaf) {
        cancelAnimationFrame(glideRaf);
        glideRaf = 0;
      }
      node.classList.remove('drag-no-snap');
    };

    const down = (e) => {
      stopGlide();
      isDown = true;
      moved = false;
      startX = e.clientX || 0;
      lastX = startX;
      lastT = performance.now();
      vel = 0;
      startLeft = node.scrollLeft;
      node.classList.add('drag-active');
      node.classList.add('drag-no-snap');
    };
    const move = (e) => {
      if (!isDown) return;
      const x = e.clientX || 0;
      const dx = x - startX;
      if (Math.abs(dx) > 6) moved = true;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        node.scrollLeft = startLeft - dx;
      });

      const now = performance.now();
      const dt = Math.max(8, now - lastT);
      vel = (x - lastX) / dt; // px/ms
      lastX = x;
      lastT = now;
      if (moved) {
        node.classList.add('drag-moved');
        e.preventDefault();
      }
    };
    const up = () => {
      if (!isDown) return;
      isDown = false;
      node.classList.remove('drag-active');
      // Inertia for smoother swipe feel
      let v = vel * 26; // tune momentum strength
      const decay = 0.95;
      const step = () => {
        if (Math.abs(v) < 0.15) {
          glideRaf = 0;
          node.classList.remove('drag-no-snap');
          return;
        }
        node.scrollLeft -= v;
        v *= decay;
        glideRaf = requestAnimationFrame(step);
      };
      if (Math.abs(v) > 0.2) glideRaf = requestAnimationFrame(step);
      else node.classList.remove('drag-no-snap');
      if (moved) {
        node.dataset.lastDragAt = String(Date.now());
        setTimeout(() => node.classList.remove('drag-moved'), 120);
      }
    };

    node.addEventListener('pointerdown', down);
    node.addEventListener('pointermove', move, { passive: false });
    node.addEventListener('pointerup', up);
    node.addEventListener('pointercancel', up);
    node.addEventListener('pointerleave', up);
  }

  // ── LOBBY I18N TABS UPDATE ───────────────────────────────────────────────────
  function _applyLobbyI18n() {
    const map = {
      'ltab-velox':     _t('sidebar.velox_choice') || 'Выбор VELOX',
      'ltab-popular':   _t('sidebar.popular')       || 'Популярные',
      'ltab-new':       _t('sidebar.new_games')     || 'Новые',
      'ltab-wager':     _t('sidebar.bonus_wager')   || 'Для отыгрыша',
      'ltab-topmonth':  _t('sidebar.top_month')     || 'Топ месяца',
      'ltab-bonusbuy':  _t('sidebar.bonus_buy')     || 'Bonus Buy',
      'ltab-netgame':   _t('sidebar.netgame')       || 'Netgame',
      'ltab-crash':     _t('sidebar.crash')         || 'Краш',
      'ltab-all':       _t('sidebar.all_games')     || 'Все игры',
    };
    Object.entries(map).forEach(([id, label]) => { const e=el(id); if(e) e.textContent=label; });
    const st = el('home-sport-title'); if(st) st.childNodes[0].textContent = _t('home.sport_section')||'Спорт';
    const rt = el('home-rtp-title');
    if(rt) {
      rt.childNodes[0].textContent = (_t('home.high_rtp_section')||'Высокий RTP') + ' ';
    }
    const pt = el('home-providers-title'); if(pt) pt.textContent=_t('home.top_matches_day')||'Top matches of the day';
    const gt = el('home-games-title'); if(gt) gt.textContent=_t('sidebar.games')||'Игры';
    const spt = el('sport-page-title'); if(spt) spt.textContent = _t('sidebar.sport') || 'Спорт';
    const sInp = el('bukm-search-input'); if (sInp) sInp.placeholder = _t('home.search_placeholder') || 'Поиск команд или чемпионата';
    const topLbl = el('bukm-top-league-label'); if (topLbl) topLbl.textContent = _tv('sport.top_league', 'Top League');
    const myLbl = el('bukm-my-league-label'); if (myLbl) myLbl.textContent = _tv('sport.my_league', 'My League');
    txt('bukm-leagues-title', _tv('sport.leagues', 'Лиги'));
    txt('bukm-categories-title', _tv('sport.categories', 'Категории'));
    txt('bukm-live-now-title', _tv('sport.live_now', 'Live сейчас'));
    txt('bukm-popular-title', _tv('sport.popular_events', 'Популярные ивенты'));
    txt('bukm-planned-title', _tv('sport.planned_games', 'Запланированные игры'));
    txt('bukm-section-title', _tv('sport.section_title', 'Спорт'));
    txt('bukm-coupon-empty-text', _tv('sport.coupon_empty_text', 'Выберите коэффициент'));
    txt('bukm-coupon-title', _tv('sport.coupon', 'Купон'));
    txt('bukm-stake-label', _tv('sport.stake_amount', 'Сумма ставки'));
    txt('bukm-coef-label', _tv('sport.coef_label', 'Коэф.'));
    txt('bukm-win-label', _tv('sport.win_label', 'Выигрыш'));
    txt('bukm-coupon-btn', _tv('sport.place_bet', 'СДЕЛАТЬ СТАВКУ'));
  }

  return{init,go,modal,closeM,closeModal,get _lang(){return _lang;},get _T(){return _T;},toggleDD,toggleDet,toggleSection,toggleLangMenu,
    lobbyTab,lobbyShowMore,
    openGame,playGame,bannerStep,bannerGoto,bannerClick,sidebarBannerClick,
    filterByTag,openSmartSearch,openDailySpin,openLuckyTicket,closeGiftTicket,smartSearch,
    authTab,regTab,authForm,login,register,evCodeInput,evVerify,evResend,polScroll,polConfirm,logout,
    openForgot,forgotPw,stab,loadGameHistory,loadMyTickets,openTicket,closeTicketByUser,replyToTicket,saveProfile,profileContactInputChanged,sendEmailChangeOtp,verifyEmailChangeOtp,savePhoneProfile,changePw,tfa,tfaConfirm,verFile,verify,
    fmtCard,fmtExp,chkCvv,chkHolder,deposit,withdraw,altPay,flipCard,cvvFocused,_syncCvvDots,lookupZip,onCountryChange,onRegionChange,setDepAmt,updDepSummary,
    paySetMode,payToggleGroup,paySelectProvider,paySubmit,payBack,kycSetMode,kycStartCam,kycToggleRec,kycRetry,kycFileSelected,promo,ticket,setLang,supFilesChanged,
    togFAQ,licTab,favSearch,togFav,saveFavs,openRaffle,openPromo,openCampaignGame,setTournamentPeriod,openTournamentEvent,openTournamentEventGame,
    avFile,avDrop,saveAvatar,
    btmOpenRules,btmRulesTab,btmOpenLicenses,btmOpenProviders,btmProviderClick,
    btmOpenPayments,btmPayCat,btmPayToggle,btmOpenContacts,
    rgOpen,rgSaveLimits,rgSelectExclude,rgSelfExclude,rgConfirmSelfExclude,rgCancelSelfExclude,
    affOpen,affCopyLink,
    vipOpen, vipPurchase, _vipSelectTier, _vipSelectPeriod, _vipDefaultIcon,
    resetPwSubmit,_checkResetToken,
    setCurrency,toggleCurMenu,_buildCurPanel,
    bukmSetTag, bukmSetLeague, bukmPick, bukmSetMode, bukmSetType, bukmSearchInput, bukmTogglePanel, bukmToggleCat, bukmSetCategory, bukmSelectMatch, bukmPickMarket, bukmCouponRemove, bukmSetCouponMode, bukmStakeInput, bukmPlaceBet, bukmSetStake, bukmSetMarketTab, bukmSetTotLine, bukmSetHcLine,
    _openBukmBetModal,
};
})();
document.addEventListener('DOMContentLoaded',V.init);