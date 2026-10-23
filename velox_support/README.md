# VELOX Support Panel

Отдельный сайт панели технической поддержки для казино VELOX.
Работает на отдельном порту (по умолчанию **5001**), подключается к той же БД что и казино.

---

## Структура

```
velox_support/
├── main.py                   # Точка входа Flask
├── requirements.txt
├── start.sh                  # Скрипт запуска
├── .env.example              # Пример конфигурации
├── app/
│   ├── db.py                 # Коннектор к БД казино
│   ├── api/
│   │   ├── auth.py           # Регистрация/вход администратора
│   │   └── tickets.py        # API тикетов, пользователей, статистики
│   ├── models/
│   │   └── admin.py          # Модель администратора
│   └── services/
│       └── email_service.py  # OTP по email
├── config/
│   └── languages/
│       ├── ru.json           # Русский
│       ├── ua.json           # Украинский
│       └── en.json           # Английский
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    ├── base.html
    ├── macros.html
    ├── auth.html             # Главная страница (ключ → регистрация/вход)
    ├── dashboard.html
    ├── tickets.html
    ├── ticket_detail.html
    ├── users.html
    └── user_detail.html
```

---

## Быстрый старт

### 1. Копируем папку рядом с казино

```
project/
├── velox_casino/      ← основной сайт казино (порт 5000)
│   └── database/
│       └── casino.db
└── velox_support/     ← панель поддержки (порт 5001)
```

### 2. Настройка .env

```bash
cd velox_support
cp .env.example .env
```

Обязательно измените:
- `ADMIN_REGISTER_KEY` — уникальный секретный ключ (храните в тайне!)
- `SQLITE_PATH` — путь к `casino.db` казино (по умолчанию `../database/casino.db`)
- `DEFAULT_ADMIN_NICKNAME` — дефолтный никнейм администратора (по умолчанию `VELOX SUPPORT`)

### 3. Установка зависимостей

```bash
pip install -r requirements.txt
```

### 4. Запуск

```bash
bash start.sh
# или напрямую:
python main.py
```

Открыть: **http://localhost:5001**

---

## Поток авторизации

```
Главная страница
    │
    ▼
[Этап 1] Поле ввода ADMIN_REGISTER_KEY
    │ Ключ верный
    ▼
    ├── Нет ни одного admin → [Этап 2] Форма регистрации
    │       Email → OTP по email → Пароль → Никнейм
    │       Снизу: гиперссылка "Войти"
    │
    └── Уже есть admin → [Этап 3] Форма входа
            Email + Пароль
            Снизу: гиперссылка "Регистрация"
```

---

## Возможности панели

| Раздел      | Описание |
|-------------|----------|
| Дашборд     | Статистика: открытые тикеты, игроки, KYC, выплаты |
| Тикеты      | Список всех тикетов с фильтрами по статусу, ответы администратора |
| Пользователи| Поиск игроков, просмотр баланса, транзакций, тикетов |

---

## Таблицы которые создаются

В существующую БД казино добавляются 3 новые таблицы:

- `support_admins` — администраторы панели
- `support_sessions` — сессии (HttpOnly cookie)
- `support_ticket_messages` — ответы в тикетах

Таблицы казино используются только на чтение/запись через существующие поля:
`support_tickets`, `users`, `transactions`, `payment_orders`, `game_history`

---

## i18n

Язык выбирается в интерфейсе (RU / UA / EN) и сохраняется в localStorage.
Файлы переводов: `config/languages/*.json`

Для добавления нового языка:
1. Создайте `config/languages/XX.json`
2. Добавьте кнопку в шаблоны: `<button class="lang-btn" data-lang="XX">🏳 XX</button>`

---

## Настройки никнейма по умолчанию

В `.env`:
```
DEFAULT_ADMIN_NICKNAME=VELOX SUPPORT
```

Администратор при регистрации может указать своё имя — если оставить поле пустым, используется значение из конфига.

---

## Продакшн (nginx)

```nginx
server {
    listen 80;
    server_name support.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## Change Log (2026-04-27)

- Добавлена новая вкладка-конструктор сезонов турниров в Support Panel:
  - шаблон `templates/tournament_builder.html`
  - роут `GET /panel/tournament-builder` в `main.py`
  - пункт меню в `templates/macros.html`
  - стили интерфейса конструктора в `static/css/style.css`
  - логика страницы в `static/js/app.js` (`initTournamentBuilder`, CRUD действий).
- Добавлены API для управления турнирными сезонами в `app/api/admin_panel.py`:
  - `GET /api/admin/tournaments/seasons`
  - `POST /api/admin/tournaments/seasons`
  - `POST /api/admin/tournaments/seasons/<id>/status`
  - `DELETE /api/admin/tournaments/seasons/<id>`
  - также добавлен авто-ensure таблиц `tournaments` и `tournament_entries` при работе конструктора.
- Удалён блок `Site mode` из страницы `Analytics` (`templates/analytics.html`), так как управление режимом уже находится во вкладке `Site`.
- Добавлены i18n ключи для новой вкладки и навигации:
  - `config/languages/ru.json`
  - `config/languages/ua.json`
  - `config/languages/en.json`
  - новые ключи: `nav.tournament_builder` и `tournament_builder.*`.
- Обновлены права доступа в `config/perms.json`:
  - добавлена вкладка `tournament_builder` для ролей `admin`, `marketing`, `dealer`, `gm` (CEO получает через `tabs_all`).

### Что это дает по функционалу

- Администратор может создавать и управлять реальными турнирными сезонами из Support Panel без ручного редактирования БД.
- Визуальный конструктор позволяет задать название сезона, период, призовой фонд, даты старта/окончания и связанные игры.
- В списке сезонов доступны быстрые действия: переключение статуса (`active/completed`) и удаление сезона.
- Аналитика становится чище: дублирующее управление режимом сайта убрано из `Analytics`, источник правки остается единым во вкладке `Site`.

### Update (same date)

- Исправлена логика конструктора под реальный смысл дат:
  - поле `Period` теперь используется как пресет длительности (day/week/month/all/custom), а не как единственный фактор жизненного цикла.
  - `starts_at`/`ends_at` остаются главным источником старта и окончания сезона.
- Улучшен UX формы:
  - при смене пресета автоматически подставляется дата окончания от даты старта.
  - добавлен `custom` режим для полностью ручного контроля дат.
- Улучшен выбор игр:
  - вместо обычного `multiple select` добавлен красивый список чипов с поиском.
  - выбранные игры отображаются отдельными визуальными бейджами.
- Backend в `app/api/admin_panel.py` обновлен:
  - создание сезона теперь принимает произвольный `period` (включая `custom`) без жесткой блокировки.

### Update (tournament glow settings)

- Конструктор турниров расширен новыми настройками сезона:
  - `templates/tournament_builder.html`: добавлено поле выбора цвета свечения сезона (`type="color"`).
  - `static/js/app.js`: в payload создания сезона добавлены `glow_color` и сохранение выбранных `game_ids`; в таблице сезонов отображается цветовой chip.
  - `app/api/admin_panel.py`: в таблицу `tournaments` добавлены и поддержаны поля `game_ids` и `glow_color` (с авто-миграцией для существующей БД).
- Добавлены i18n ключи для новых элементов конструктора:
  - `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/en.json`.

### Update (custom glow picker UI)

- Улучшен UX выбора цвета свечения сезона в `Tournament Builder`:
  - `templates/tournament_builder.html`: стандартный `input[type=color]` заменён на кастомный блок в стиле панели (preview, HEX-ввод, палитра swatches, кнопка-палитра).
  - `static/css/style.css`: добавлены стили нового color picker (`tb-color-*`) с неоновыми акцентами в фирменной теме VELOX Support.
  - `static/js/app.js`: добавлена логика синхронизации HEX, preview, swatches и нативного color-dialog fallback.
- Результат: выбор цвета выглядит как часть дизайна админ-панели, а не браузерный дефолтный элемент.

### Update (tournament delete button fix)

- Исправлена надежность удаления сезонов в `Tournament Builder`:
  - `static/js/app.js`: удалены хрупкие inline `onclick` в таблице сезонов, добавлен безопасный event-binding через `data-*` атрибуты.
  - добавлена защита от двойного клика/повторного удаления в процессе запроса.
  - обработка `id` теперь через строковую валидацию (`^\d+$`), чтобы избежать проблем с числовым приведением.

### Update (tournament event description field)

- Расширен `Tournament Builder` новым полем описания события:
  - `templates/tournament_builder.html`: добавлено поле `Description` (`tb-description`) в форме создания сезона.
  - `static/js/app.js`: в payload создания сезона добавлено поле `description`.
  - `app/api/admin_panel.py`:
    - таблица `tournaments` расширена колонкой `description` (PostgreSQL + SQLite + auto-migration via `ALTER TABLE`).
    - API создания сезона (`POST /api/admin/tournaments/seasons`) теперь принимает и сохраняет `description`.
- Добавлены i18n ключи для поля описания:
  - `config/languages/ru.json`, `config/languages/ua.json`, `config/languages/en.json`:
    - `tournament_builder.field_description`
    - `tournament_builder.field_description_ph`

### Что это дает по функционалу

- Маркетинг/админ может задавать отдельный текстовый контент сезона для клиентской event-модалки без ручных правок в БД.
- Описание события становится частью единого сценария: создается в `velox_support` и отображается в `fixed_out` в интерфейсе турниров.

### Update (2026-04-28 — каталог линии провайдера)

- **`app/services/casino_sport_odds.py`:** загрузка **`fixed_out/app/services/sport_odds_feed.py`** через `importlib` по пути файла. Раньше `sys.path.insert(fixed_out)` + `from app.services.sport_odds_feed` резолвился в пакет **`velox_support.app`**, где этого модуля нет → ошибка **`No module named 'app.services.sport_odds_feed'`** на **`GET /api/admin/game-builder/sport-provider/catalog`**.
- **`app/api/admin_panel.py`:** инвалидация кэша линии и каталог используют **`load_casino_sport_odds_feed()`**; импорт **`sys`** удалён как неиспользуемый.
- **`static/js/app.js`**, **`static/css/style.css`:** карточки каталога провайдера — колонка **«Когда»** (дата + время в локали браузера), команды, лига, коэффициенты, статус видимости на сайте.
- **i18n:** `games_builder.provider_when_col`, `games_builder.provider_visible_yes`, `games_builder.provider_visible_no` в `config/languages` (ru / ua / en).
