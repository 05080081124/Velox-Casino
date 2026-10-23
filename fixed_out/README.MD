# VELOX Casino - Change Log

## 2026-04-28 (Sport: линия не теряет LIVE)
- **`app/services/sport_odds_feed.py`:** для **Odds-API.io** запрос **`/events`** выполняется для **`pending` и `live`** (раньше в конфиге был только `pending` — после начала матч пропадал из выборки API). Дубликаты по `id` объединяются, приоритет у строки **`live`**. Матч **live** без линии ML остаётся в списке (ставки могут быть закрыты — карточка для счёта всё равно видна). Добавлен разбор **`score`** из объекта события, если API его отдаёт.
- **`config/sport_odds_api.json`:** комментарий **`_note_status`** про поведение статусов.
- **`static/js/app.js`:** **`_bukmIsEffectivelyLive`** — в LIVE попадают матчи со статусом live, **`match_today`**, либо с **`starts_at`/UTC уже в прошлом** (пока не **`finished/cancelled`**). Кнопка **«весь вид спорта»** в левом меню задаёт **`category` = Football/Basketball/…** и **все страны**, а не сброс всего фильтра. Переключение **таба вида спорта** / **типа Sport/E-Sport** сбрасывает привязку к стране, чтобы не ловить пустой список.

## 2026-04-28 (Sport: время/формы + каталог velox_support)
- **`velox_support/app/services/casino_sport_odds.py`:** загрузка модуля казино **`fixed_out/app/services/sport_odds_feed.py`** через `importlib.util.spec_from_file_location`, без `sys.path.insert(fixed_out)` и без конфликта с пакетом **`velox_support.app`** (раньше импорт `app.services.sport_odds_feed` падал с `No module named 'app.services.sport_odds_feed'`).
- **`velox_support/app/api/admin_panel.py`:** инвалидация кэша линии и **`GET .../sport-provider/catalog`** используют этот загрузчик; удалён неиспользуемый **`import sys`**.
- **`fixed_out/static/js/app.js`:** карточки букмекера и блок Sport на главной показывают **день недели / «Сегодня»·«Завтра»** и **локальное время** матча; добавлены мини **«формы»** (SVG-манишка со цветами): цвета из **`home_kit_*` / `away_kit_*`** или стабильный хэш от названия команды.
- **`fixed_out/static/css/style.css`:** стили **`.bukm-event-when`**, **`.bukm-kit-*`**, главная **`.sport-card-when`** / **`.sport-team-pair`**.
- **i18n:** `sport.match_day_today`, `sport.match_day_tomorrow`, `sport.match_date_unknown`, `sport.match_starts_hint` в `ru` / `ua` / `us`, плюс **`config/languages/sport_i18n_keys.json`**.
- **`config/bukmaking.json`:** в **`_readme`** описаны поля формы и логотипов команд.

### Как это работает
- Админка поддержки берёт merged-события из того же Python-файла, что и казино; пути **`config/`** внутри фида резолвятся от **`fixed_out`** (через `__file__` загружаемого модуля).
- На сайте игрок видит понятную дату и время начала; «форма» помогает отличить стороны, пока нет официальных иконок лиг.

## 2026-04-26
- Fixed critical CSRF issue in game frontends: `crash`, `roulette`, and `mineslot` now use `_gfetch()` which automatically adds `X-CSRF-Token` from `velox_csrf` cookie for unsafe HTTP methods.
- Replaced direct `fetch(...)` game API calls with `_gfetch(...)` to keep POST endpoints compatible with enabled CSRF protection (double-submit cookie flow).
- Increased PostgreSQL `users.two_fa_pass` column from `VARCHAR(32)` to `VARCHAR(64)` for safer TOTP secret storage compatibility.
- Added suspicious User-Agent blocking in middleware (`app/security.py`) using configurable patterns from `config/security.json` (`headlesschrome`, `python-requests`, `curl`, known scanners).
- Added GeoIP country blocking support: new `checks.geo_block`, `blocked_countries`, and `geoip.database_paths` in `config/security.json`; middleware now blocks configured ISO country codes when GeoIP DB is available.
- Re-checked screenshot tasks: Redis rate-limit backend is already wired via `REDIS_URL`; BGaming provider config already exists in `config/providers.json`; HTTPS secure cookies already supported via `HTTPS=true` + `security.json https=true` (needs real SSL cert on deploy).
- Fixed app startup crash when `REDIS_URL` is set but Redis Python dependency/server is unavailable: `main.py` now falls back to `memory://` limiter backend instead of crashing.
- Fixed Windows console startup issue (`UnicodeEncodeError`) by replacing non-ASCII arrow symbols in DB startup logs (`app/db.py`) with ASCII `->`.
- Fixed additional Windows console encoding crash in startup banner (`main.py`) by removing emoji symbols from startup logs.
- Added tournaments leaderboard feature: new API `GET /api/tournaments/leaderboard` (periods: day/week/month/all) ranking players by total winnings.
- Added new sidebar tab and page `tournaments` with period filters and leaderboard cards (rank, winnings, bets, profit, rounds).
- Fixed homepage "See all" labels to use i18n (`home.see_all`) instead of static Russian text for multilingual mode.

## 2026-04-27
- Reworked `Tournaments` from a dynamic `game_history` leaderboard into real tournament seasons with dedicated DB tables: added `tournaments` and `tournament_entries` schemas/migrations in `app/db.py`.
- Added new tournament domain logic in `app/models/tournament.py`: auto-create active season per period (`day/week/month/all`), keep season statuses (`active/completed`), store prize pool, and return season leaderboard from `tournament_entries`.
- Connected tournament stats updates to real gameplay flow in `app/models/game.py`: each `GameRound.record(...)` now updates participant totals in active tournaments (`bet`, `win`, `profit`, `rounds`) instead of calculating all standings on every request.
- Updated `GET /api/tournaments/leaderboard` in `app/api/games.py` to return season-aware payload (`tournament` metadata + `leaders`) based on active season data.
- Extended tournaments UI in `templates/partials/views_lobby.html` and `static/js/app.js`: page now shows season card (title, prize pool, season dates) plus leaderboard entries.
- Added i18n keys for the new tournaments UI metadata in `config/languages/ru.json`, `config/languages/ua.json`, and `config/languages/us.json` (`tournaments.prize_pool`, `tournaments.season_dates`).

### Functional explanation
- **What changed in behavior:** tournament standings are now persisted as season entries, not rebuilt ad-hoc from the full `game_history` table each time.
- **Why this is important:** this enables real tournament lifecycle and prize pools, improves scalability, and prepares ground for admin-managed custom seasons.
- **How scoring works now:** when a user plays a round, their cumulative tournament metrics are incremented in `tournament_entries` for all currently active seasons.

## 2026-04-27 (Tournament lifecycle + glow integration)
- Fixed tournament visibility and lifecycle in `fixed_out`:
  - `app/models/tournament.py`: leaderboard no longer auto-creates synthetic seasons; now only returns real active/upcoming seasons from DB, auto-finalizes expired seasons, and parses `game_ids` + `glow_color`.
  - `app/api/games.py`: tournament payload now includes `is_upcoming`, `game_ids`, `glow_color` in addition to runtime timers/flags.
  - `app/models/game.py`: round updates now pass `game_id` into tournament scoring so season stats can be restricted to selected games.
- Added tournament game targeting + glow config storage in DB schema/migrations:
  - `app/db.py`: `tournaments` table now supports `game_ids` and `glow_color` (SQLite + PostgreSQL + migration path).
- Added live tournament hint on game cards:
  - `static/js/app.js`: caches current season, refreshes it periodically, and marks matching game cards with season badge.
  - `static/css/style.css`: added animated season glow (`g-card-tour`) using configurable per-season color.
- Added i18n keys (3 languages) for new tournament UI states:
  - `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
    - `tournaments.no_season`
    - `tournaments.badge_live`

### How it works now
- Seasons created in `velox_support` are visible in `Tournament` tab as real entities (active or upcoming), instead of silently falling back to generated defaults.
- Season start/end is evaluated by current UTC time (`starts_at` / `ends_at`), with expired active seasons auto-marked completed on leaderboard access.
- If season has selected games (`game_ids`), only those games contribute to standings and only those cards receive the glow; if game list is empty, season applies to all games.

### Update (visibility + refresh fixes)
- Improved season visual signaling on game cards in `static/css/style.css` and `static/js/app.js`:
  - stronger live pulse, moving shine stripe, and explicit badges (`SEASON LIVE` / `SEASON SOON`) so users can clearly see tournament state.
  - removed dependence on advanced CSS color mixing for core visibility to avoid weak/no effect on some browsers.
- Fixed tournament auto-refresh side effects in `static/js/app.js`:
  - periodic tournament polling no longer force-renders full game lists every cycle.
  - visible lists are re-rendered only when tournament context actually changes.
  - in `games` view, active category/tag filter is preserved during updates (no fallback to full list).

### Update (tournament page redesign + bugfix)
- Fixed critical date parsing regression in `app/models/tournament.py` (`_parse_dt`) that could break season state detection and make tournament view appear empty.
- Redesigned tournament UI in `templates/partials/views_lobby.html`, `static/js/app.js`, `static/css/style.css`:
  - removed dependency on period tabs in the page UX.
  - added expandable season card with start/end dates, prize pool, current state, and season game scope.
  - added right-side `Top wins` panel (biggest wins list) to keep the page informative even with low tournament participation.
- Extended `GET /api/tournaments/leaderboard` in `app/api/games.py`:
  - now also returns `high_wins` block (top game_history wins with player/game context) for tournament side panel.

### Update (visual polish pass)
- Improved tournament indicators and readability in `static/css/style.css`:
  - `LIVE/ SOON SEASON` badges now use dark translucent background for better text contrast on bright game cards.
  - tournament cards in the `Tournament` tab got cleaner gradients, stronger borders, and tighter spacing for a more premium layout.

- Fixed tournament lifecycle visibility and rollover behavior:
  - `app/models/tournament.py` now finalizes all expired active seasons automatically and selects the most relevant season by time (active now -> upcoming), not only by strict period match.
  - this prevents cases when a season appears "not started" or "not finished" in the lobby due to period mismatch while dates are valid.
- Extended leaderboard season payload in `app/api/games.py`:
  - added `has_started`, `is_finished`, `seconds_to_start`, `seconds_to_end` so frontend can show exact season state.
- Improved tournaments lobby UI in `static/js/app.js`:
  - season dates now render in local readable format instead of raw ISO.
  - added explicit state line (`Active / Finished / Starts in ...`) for current season status clarity.
- Added new i18n keys for season state labels in `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
  - `tournaments.starts_in`, `tournaments.ends_in`, `tournaments.state_active`, `tournaments.state_finished`.

## 2026-04-27 (Tournament expanded card + rankings panels)
- Updated tournament backend payload in `app/api/games.py`:
  - `high_wins` list now supports up to 200 players (sorted by biggest bet win from `game_history`).
  - when there are no big win records, API now returns fallback list by best all-time `Win RTP` (`SUM(win)/SUM(bet)` grouped by user).
- Updated tournament UI behavior in `static/js/app.js`:
  - expanded season card now contains internal `Top 10` participant list (players who already joined season).
  - season dates (`start/end`) are now always visible in the lower-right area of the card.
  - added centered `Play` button in card footer with transition to game route (same flow style as promotions; first selected tournament game, fallback to games page).
  - right-side winners panel now renders up to 200 rows and shows RTP label when fallback mode is active.
- Updated tournament layout styling in `static/css/style.css`:
  - added premium custom scrollbars for tournament participant/winners lists.
  - added new styles for expanded card footer (center button + right-aligned date block) and compact top-10 rows.
- Updated tournaments page markup in `templates/partials/views_lobby.html`:
  - tournament leaderboard and high-wins lists now use reusable custom-scroll style class.
- Added i18n keys in `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
  - `tournaments.participants_title`
  - `tournaments.rtp_label`
  - updated `tournaments.high_wins_title` wording for clarity.

### Functional explanation
- **Expanded tournament card:** now acts as a mini-dashboard — users immediately see season period, can press `Play`, and inspect current top-10 participants directly inside the expanded block.
- **Top-1 logic:** first place in top-10 is the player with maximum accumulated seasonal winnings from bets until season end (`total_win` descending).
- **Right panel behavior:** panel prioritizes largest single wins from bet history; if no such data exists, it automatically switches to all-time high-RTP players so the block is never empty.

## 2026-04-27 (Tournament visual polish pass)
- Improved tournament UI visual quality in `static/css/style.css`:
  - upgraded cards to stronger premium look (depth shadows, refined gradients, gold accent borders on hover).
  - added dedicated styles for top-ranked cards (`top3`) and rank pills with animated shine.
  - improved season card readability (stronger title/labels, elevated CTA button styling, cleaner visual hierarchy).
  - emphasized `Top 1` row inside season top-10 list to make the leader clearly visible.
- Updated tournament card HTML classes in `static/js/app.js`:
  - added semantic classes (`tour-leader-card`, `tour-win-card`, `tour-rank-pill`, `is-top1`) so leaderboard and right-side panel can be styled as final polished components instead of generic feed cards.

### Functional explanation
- **What changed:** no business logic or data source changes; only rendering class hooks and CSS visual treatment were improved.
- **Result:** tournament page now looks production-ready with clearer ranking emphasis and improved perceived quality.

## 2026-04-28 (Sport feed — enable + smoke)
- **`config/sport_odds_api.json`:** `"enabled": true` so merged line from **The Odds API v4** loads when `.env` contains `SPORT_ODDS_API_KEY` / `THE_ODDS_API_KEY` (same keys as `.env.example`). Without a key the server keeps only static events from `bukmaking.json` — no crash, `auto_from_api` stays `0`.
- **`scripts/smoke_bukmaking.py`:** dev helper — run from `fixed_out`: `python scripts/smoke_bukmaking.py`; prints whether a key is set, total merged events, and count of IDs prefixed `oddsapi_*` (events injected by `app/services/sport_odds_feed.py`).
- **How to see The Odds API events:** register at https://the-odds-api.com/, put the key in `.env`, add a `sources[]` entry with `provider: the_odds_api` and `sport_keys`. **Odds-API.io** uses `provider: odds_api_io` and `ODDS_API_IO_KEY` (see newer changelog block below).

## 2026-04-28 (Sport: провайдер + velox_support каталог)
- **`config/sport_provider_visibility.json`:** `mode`: `all` (вся линия на сайте) или `listed` + **`allowed_ids`** — на казино попадают только события провайдера с id из списка; ручные матчи из `bukmaking.json` всегда показываются.
- **`app/services/sport_odds_feed.py`:** после merge файла и API вызывается **`_apply_provider_visibility`**; добавлены **`invalidate_odds_feed_cache`**, **`get_merged_provider_events_preview`** (merge без фильтра — для админки).
- **`velox_support`:** `GET/POST /api/admin/game-builder/sport-provider/*` — каталог линии, переключение режима, **Show/Hide** по одному матчу; после сохранения сбрасывается кэш линии в casino.
- **`static/js/app.js` (Sport):** фильтры страны по префиксу лиги «Страна - …»; боковая колонка лиг дополняется **динамическими** чемпионатами из линии (до 80); бейдж **LINE/ODDS** на карточках провайдера.
- **i18n:** `sport.provider_leagues`, `sport.badge_*` в `config/languages` (ru/ua/us); подписи каталога в `velox_support/config/languages` (ru/ua/en).

## 2026-04-28 (Odds-API.io — фильтры Sport UI)
- **`app/services/sport_odds_feed.py`:** события Odds-API.io получают **`category`** как **Football / Basketball / E-Sport** (как пункты `sports_menu` в `bukmaking.json`), а не сырое имя лиги — иначе `_bukmEvents()` отбрасывала их при включённом фильтре вида спорта. Поле **`country`** заполняется первым сегментом названия лиги до « - » (Spain - La Liga → Spain).
- **`static/js/app.js`:** при переходе на вкладку **Sport** конфиг **`/api/config/bukmaking`** запрашивается снова (merge + кэш сервера), затем перерисовываются блоки Sport на главной.

## 2026-04-28 (Odds-API.io wired)
- **`app/services/sport_odds_feed.py`:** added provider **`odds_api_io`** for **Odds-API.io v3** (`/events` + `/odds/multi`). Event IDs exposed as `oddsapiio_<eventId>`; ML (**Match result**) odds taken from configured `bookmakers` (default Bet365).
- **`config/sport_odds_api.json`:** primary source is **`sources[].provider`** = `odds_api_io` with **`sport_slugs`** (e.g. `football`, `basketball`), **`env_key`** → `ODDS_API_IO_KEY`, **`events_status`** typically `pending` for prematch list.
- **`.env.example`:** documents **`ODDS_API_IO_KEY`** (distinct from **The Odds API** keys).
- **`scripts/smoke_bukmaking.py`:** reports **`auto_odds_api_io`** count (`oddsapiio_*` IDs) separately from **`auto_the_odds_api`** (`oddsapi_*`).
- **Casino UX:** unchanged for players — frontend still loads **`GET /api/config/bukmaking`**; server returns static `bukmaking.json` events plus API rows whenever `enabled` is true and keys exist. Settlement remains **manual admin** (`/api/sport/settle`), not automated by Odds-API.io.

## 2026-04-28
- Improved mobile UX for `Payments` page in `fixed_out`:
  - `static/css/style.css`: added mobile side paddings so payment panels don’t stick to screen edges.
  - `static/css/style.css`: reduced provider cards size (logo + title), made panels visually lighter and more compact on small screens.
  - `static/css/style.css`: added internal vertical scrolling for expanded provider groups so long provider lists don’t consume the whole page.
  - `static/css/style.css`: compacted action button (`Deposit/Withdraw`) and the security badges row (`SSL`, `3D-Secure`, `PCI DSS`) to reduce vertical footprint.
  - `static/js/app.js`: payment category accordion uses **inline SVG** icons (and chevron) instead of emoji.
  - `static/css/style.css`: on mobile, **Current balance** summary card uses `overflow: visible` and a softer, larger radial highlight so the gold “glow” is not clipped by `border-radius` / `overflow: hidden` (matches desktop intent better).

### Functional explanation
- **What changed in behavior:** on mobile, when a payment category is expanded and contains many providers, the provider list now scrolls inside its block instead of growing indefinitely.
- **What it means for users:** the `Payments` screen stays readable and “airy” (with side gaps), while still showing the full design without oversized wide panels.

### Sport / bookmaking — mobile layout (≤980px)
- `static/css/style.css`: on narrow viewports the **Sport** view (`#view-sport`) uses a **single-column stack**: match feed first, then leagues/categories panels, then focus/markets and **bet slip (coupon)** — so the coupon is visible again (older rules hid sidebars/coupon on small screens).
- Header: stacked layout, **full-width search**, mode/type switches scroll horizontally; LIVE strip chips get a soft **edge fade** hint for horizontal scroll.
- Cards: tighter padding, **two-line team names** where needed, **single-column market picks** in the right panel; expanded in-card markets use **horizontal tab scroll**.
- Desktop layout **above 981px** keeps the existing multi-column `sport-page-grid` (unchanged intent).

### Sport / bookmaking — external odds API (The Odds API v4)
- **Behavior:** the platform does **not** “invent” a line from a random hex key. You register with a **concrete provider**, put the **API key in environment only** (`SPORT_ODDS_API_KEY` or `THE_ODDS_API_KEY`), set `"enabled": true` in `config/sport_odds_api.json`, and list desired `sport_keys` (see provider docs). On each `/api/config/bukmaking` request (with cache TTL), the server may **fetch** odds and **merge** them into the JSON your frontend already uses — events then appear on **Sport** like static ones. **Accepting bets** uses the same merged list (`app/api/sport.py` → `get_merged_events_list()`).
- **Two bookmaking providers:** when the vendor only says “pick two providers”, map that to **`sources`** in `sport_odds_api.json` — two objects, each with its own **`env_key`** (variable name in `.env` where that provider’s key lives, e.g. `SPORT_ODDS_API_KEY` and `SPORT_ODDS_API_KEY_2`). Both are queried and events are merged (duplicate `id` dropped). If **`sources`** is empty, the older single-key + top-level `sport_keys` block still works.
- **Files:** `config/sport_odds_api.json` (settings, no secrets), `app/services/sport_odds_feed.py` (fetch + map `h2h` → `odds.home/draw/away`), `.env.example` documents env vars.
- **Note:** a long hex string may belong to **another** API (not The Odds API). If requests return 401, check provider docs or rotate the key after exposing it in chat.

### Deposits (LiqPay) + `deposit_bonus_percent` (RU)
- **Как начисляются деньги:** при старте депозита `WithdrawalService.deposit` создаёт в БД транзакцию типа `deposit` со статусом `pending` и суммой, которую выбрал пользователь. После успешной оплаты LiqPay шлёт callback на `/api/payments/callback/liqpay` (и/или пользователь попадает на `/api/payments/liqpay/return` с подписанным `data`). Сервер вызывает `WithdrawalService.confirm_deposit(order_id, …)`, находит эту `pending` запись по `order_id` / `provider_tx_id` в `meta` и вызывает `WalletService.deposit` с **суммой из этой записи** (не «на глаз» из ответа LiqPay — защита от несовпадения).
- **Придут ли 2500:** да, на баланс уйдёт **как минимум 2500** в валюте депозита, если платёж подтверждён и pending-транзакция найдена. Дополнительно, если у пользователя активен VIP и в `config/vip.json` для его тира задан `deposit_bonus_percent`, `WalletService.deposit` начисляет **ещё процент сверху** (отдельная запись в `transactions` с `meta.type = vip_deposit_bonus`). Итого на балансе: \(2500 + 2500 \times \frac{pct}{100}\) при ненулевом бонусе.
- **Идемпотентность:** повторный callback с тем же заказом не должен задвоить баланс: после первого успеха статус транзакции уже не `pending`, повторный `confirm_deposit` отработает без повторного зачисления.

## 2026-04-27 (Tournament UX refinement: denser layout + all-time panel)
- Updated tournament API payload in `app/api/games.py`:
  - `high_wins` is now focused on top single-round wins (compact upper right panel content).
  - added new `all_time_big_winners` block (up to 200 users) sorted by each player best all-time win (`MAX(game_history.win)`).
  - if no all-time winners by big wins exist, API falls back to all-time `Win RTP` ordering.
- Updated tournaments page markup in `templates/partials/views_lobby.html`:
  - right column now has two logical panels:
    - upper `Big wins`
    - lower `All-time big wins` (as requested).
- Updated tournament rendering logic in `static/js/app.js`:
  - removed `Play` button from season card for cleaner visual consistency.
  - removed early-return empty flow for no-season state, so page no longer feels blank.
  - added richer season empty-state hint card instead of raw duplicate empty labels.
  - added rendering for new `all_time_big_winners` list (with 200 limit and RTP fallback labels).
  - removed duplicate empty message in expanded season participants block by using dedicated `participants_empty` i18n key.
- Updated tournament styling in `static/css/style.css`:
  - right side converted into two separated premium cards (`tour-side-panel`) to reduce visual emptiness.
  - season date block redesigned into highlighted info container (without CTA button).
  - added structured placeholder card for empty leaderboard state and adjusted list heights for denser, balanced layout.
- Added i18n keys in `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
  - `tournaments.all_time_title`
  - `tournaments.all_time_best_win`
  - `tournaments.big_wins_count`
  - `tournaments.season_waiting_hint`
  - `tournaments.participants_empty`

### Functional explanation
- **Right column logic:** top panel shows strongest single wins, bottom panel shows players with highest personal all-time win records (or RTP fallback when needed).
- **Visual density:** tournaments tab now always has meaningful content blocks even without active season participants, so interface looks complete and less empty.

## 2026-04-27 (Tournament UI quality fix: overflow + readability)
- Refined tournament styling in `static/css/style.css` to address rough visual issues:
  - right side list panels are now true container cards with inner framed scroll areas (instead of floating oversized blocks in empty space).
  - fixed rank-badge overflow issues (`tour-rank-pill`) with proper clipping and width constraints.
  - reduced aggressive shine animation speed/strength and added `prefers-reduced-motion` support.
  - improved text contrast for season card and right-side win rows so win values and labels remain readable.
  - improved truncation/overflow behavior for long usernames inside right-side rows.
- Hardened i18n fallback in `static/js/app.js`:
  - `tournaments.participants_empty` now has guarded fallback if translation key is missing or unresolved (`tournaments.participants_empty` literal shown by i18n layer).
- Added/kept required i18n keys in `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json` to avoid untranslated tournament placeholders.

## 2026-04-27 (SPA tabs persistence in fixed_out)
- Updated in-app tab navigation behavior in `static/js/app.js`:
  - added persistent view storage with `localStorage` key `velox_view`.
  - `go(view)` now validates that target view exists and stores selected view.
  - `filterByTag(...)` now also persists current page as `games`.
  - `init()` now restores previously opened page after refresh (`_restoreViewAfterReload()`), so user stays on the same section.

### Functional explanation
- **What changed:** page sections now behave as a single-page app flow and remember the last opened section across browser refreshes.
- **Why it matters:** users no longer get dropped to default page after `F5`, and navigation feels like native in-app page switching instead of reopening context.

## 2026-04-27 (Tournaments redesign by reference layout)
- Applied visual redesign of tournaments page in `fixed_out` using `tournament_redesign_referens.html` as style reference:
  - `templates/partials/views_lobby.html`:
    - wrapped tournament content in new container `tv2-outer` for centered, premium card-like composition.
  - `static/css/style.css`:
    - upgraded hero block (`tv2-hero`) with rounded layout and deeper shadow.
    - aligned spacing and proportions to reference (`tv2-section` paddings, tighter hero typography block).
    - improved season card (`tv2-season-card`) with animated shine effect and stronger depth.
    - polished leaderboard and side panel top rows (`tv2-leader-top`, `tv2-win-top`) for clearer top-3 visual hierarchy.
    - added mobile-safe handling for new wrapper.

### Functional explanation
- **What changed in behavior:** tournament data logic/API stayed the same; only presentation layer was redesigned.
- **What users get now:** tournaments tab looks closer to the provided reference with richer gradients, cleaner card composition, and more visible highlight effects for key rows and active season card.

## 2026-04-27 (Tournament event modal + i18n extension)
- Extended tournament domain payload and schema support for event modal content:
  - `app/db.py`: added tournament `description` column in PostgreSQL schema, SQLite schema, and migration list.
  - `app/models/tournament.py`: normalized `description` in season row output.
  - `app/api/games.py`: `GET /api/tournaments/leaderboard` now returns `tournament.description`.
- Added event modal UI and interactions in lobby:
  - `templates/partials/modals/content.html`: new modal `modal-tour-event` with hero, description, game logo block, RTP/min bet panels, game title strip, and event players list.
  - `static/css/style.css`: new `tev-*` styles for premium event modal look and responsive behavior.
  - `static/js/app.js`:
    - season card in tournaments view is clickable and opens event modal (`openTournamentEvent`).
    - modal resolves target game from season `game_ids`, renders logo/name/RTP/min bet, and opens game route on logo/button click.
    - added i18n bindings for new tournament texts and translated static labels on the tournaments page (hero subtitle, leaderboard title, period tabs).
- Added i18n keys for tournament page and event modal labels:
  - `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
    - `tournaments.hero_sub`
    - `tournaments.leaderboard_title`
    - `tournaments.open_game`
    - `tournaments.min_bet`
    - `tournaments.event_players_title`
    - `tournaments.event_desc_fallback`

### Functional explanation
- **Event card click flow:** clicking the active season card now opens a dedicated event modal with detailed context instead of keeping data only in compact card form.
- **Game context in modal:** modal shows linked tournament game branding (logo from game config), RTP, minimum bet, and provides direct entry into that game.
- **Localization coverage:** new UI strings in tournaments/event modal are now fully externalized into i18n dictionaries for multilingual support.

## 2026-04-27 (Tournaments tab mobile + SVG icon pass)
- Updated `templates/partials/views_lobby.html`:
  - removed the full leaderboard panel block (`Leaderboard` with `7 days / 30 days / all time` tabs) from the `Tournaments` page.
  - left only season card + right-side winners panels, and converted panel header emoji icons to inline SVG.
- Updated `static/js/app.js` tournament rendering:
  - added reusable `_tourSvg(...)` icon helper and replaced tournament emoji markers (no season/empty/error/rank/calendar/game labels) with SVG icons.
  - made leaderboard rendering optional (`#tournaments-feed` may be absent now), so the page keeps working after leaderboard panel removal.
  - removed now-unused page-title i18n bindings for deleted leaderboard/tabs DOM nodes.
  - switched event modal top-players rank markers from emoji medals to SVG rank icons.
- Updated `templates/partials/modals/content.html`:
  - replaced tournament event hero icon and game-logo fallback emoji with SVG icons.
- Updated `static/css/style.css` for responsive polish:
  - changed tournaments right column into adaptive card grid (`2 columns` desktop, `1 column` mobile) to use width more effectively without the removed leaderboard panel.
  - added shared SVG icon sizing/color styles for tournament UI (`tv2-i*`).
  - improved mobile spacing/density for hero, season card, and winners lists for iOS/Android narrow screens.

### Functional explanation
- **What changed in UX:** `Tournaments` now opens directly into season information + wins analytics, without separate leaderboard period switching panel.
- **How it behaves now:** data is still loaded from the same tournament API; only the page composition and icon rendering were changed.
- **Mobile result:** right-side tournament cards now stack cleanly on phones and remain readable with tighter spacing and touch-friendly layout.

## 2026-04-28 (Profile contacts + RG confirmation modal + payments mobile polish)
- Extended profile contact management in `fixed_out`:
  - `app/api/auth.py`: added secure endpoints for contact updates:
    - `PUT /api/auth/profile/phone` to add/update phone number for current authorized user.
    - `POST /api/auth/profile/email/send_otp` to send OTP to new email (with validation + uniqueness checks).
    - `POST /api/auth/profile/email/verify_otp` to confirm OTP and apply email change to current user.
  - `templates/partials/view_settings.html`: added editable contact UI in Profile tab:
    - new email input + send code + verify code fields.
    - phone input + save button.
  - `static/js/app.js`: added frontend handlers `sendEmailChangeOtp`, `verifyEmailChangeOtp`, `savePhoneProfile`, and synchronized profile state refresh after updates.
- Improved responsible gambling self-exclusion confirmation flow:
  - `templates/partials/modals/legal.html`: added dedicated confirmation modal for `Apply self-exclusion`.
  - `static/js/app.js`: replaced native `window.confirm` with modal-based flow (`rgConfirmSelfExclude` / `rgCancelSelfExclude`) and period rendering.
- Polished `payments` mobile UX only (desktop untouched):
  - `static/css/style.css`: added mobile-only refinements (`@media max-width: 820/540`) for sticky mode tabs, tighter spacing, better card/provider grid readability, summary-card ergonomics, and simplified mobile history visibility.
- Added required i18n keys for 3 languages:
  - `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/us.json`:
    - new settings contact-edit labels/placeholders/messages.
    - new RG confirmation modal labels and period suffix.

### Functional explanation
- **Email change safety:** user enters a new email, receives OTP on that exact address, and only after successful code verification the email is updated for the current logged-in account.
- **Phone management:** phone can now be added or changed directly from profile with server-side format and uniqueness validation.
- **RG UX result:** self-exclusion action now has a clearer dedicated confirmation step instead of browser-native confirm.
- **Payments mobile result:** deposit/withdraw page is denser, cleaner, and easier to use on iOS/Android while desktop layout stays unchanged.

## 2026-04-28 (Payments mobile accordion categories)
- Updated mobile provider UX in `payments` while preserving desktop behavior:
  - `static/js/app.js`:
    - enhanced `_payRenderGroups()` with mobile-aware category rendering (`pay-group-mobile`) and single-open accordion behavior.
    - added `payToggleGroup(catId)` to animate open/close and keep focused category expanded.
  - `static/css/style.css`:
    - added mobile-only accordion card styles for 4 categories (`Popular`, `Bank cards`, `E-WALLETS`, `Crypto & other`).
    - added smooth expand/collapse transitions (`max-height`, opacity, arrow rotation) and active card emphasis.
    - desktop group layout remains unchanged (legacy group header/grid stays active on PC).

### Functional explanation
- **Mobile flow now:** users first see 4 compact category cards; tapping one expands payment systems inside that category with animation, reducing clutter and improving touch navigation.
- **Desktop unchanged:** classic grouped grid list stays exactly as before for PC.
