# 🌐 VELOX Casino — Система переводов (i18n)

## Как это работает

- Язык хранится в **`config/site.json`** → поля `default_language` и `site_language`
- Файлы переводов: **`config/languages/ru.json`**, `ua.json`, `us.json`
- Выбор языка сохраняется в **localStorage** (`velox_lang`)
- Переключатель языка — в сайдбаре (раскрывается при наведении)

### Настройка в `config/site.json`
```json
"default_language": "UA",           // язык по умолчанию при первом заходе
"site_language": "RU,UA,US",        // какие языки показывать (через запятую)
"show_language_switcher": true       // показывать переключатель языков в сайдбаре
```

---

## Структура JSON файла перевода

Каждый файл (`ru.json`, `ua.json`, `us.json`) имеет одинаковую структуру.  
Ключи разбиты по секциям. Ниже — полный список всех ключей с пояснениями.

---

## 📌 Секции и ключи

### Мета-информация языка
```json
{
  "lang_label": "Українська",    // Название языка (отображается в переключателе)
  "lang_flag":  "🇺🇦",           // Флаг (emoji)
  "currency":   "UAH",           // Валюта по умолчанию для этого языка
  "currency_sym": "₴"            // Символ валюты
}
```

---

### `topbar` — Верхняя панель
| Ключ | Где используется |
|------|-----------------|
| `topbar.login` | Кнопка "Войти" (не залогинен) |
| `topbar.balance` | Подпись над балансом |
| `topbar.deposit_plus` | Кнопка "+" рядом с балансом |

---

### `sidebar` — Боковое меню
| Ключ | Где используется |
|------|-----------------|
| `sidebar.lang_label` | Заголовок переключателя языков |
| `sidebar.details` | Кнопка "Детали" |
| `sidebar.licenses` | Подпункт "Лицензии" |
| `sidebar.faq` | Подпункт "Вопрос-Ответ" |
| `sidebar.support` | Подпункт "Тех-Поддержка" |
| `sidebar.lucky_ticket` | Кнопка "Лаки-тикет" |
| `sidebar.slots` | Пункт "Слоты" |
| `sidebar.smart_search` | Пункт "Смарт-поиск" |
| `sidebar.daily_spin` | Пункт "Дейли-крут" |
| `sidebar.promotions` | Пункт "Акции" |
| `sidebar.raffles` | Пункт "Розыгрыши" |
| `sidebar.live_games` | Заголовок раздела "Live-игры" |
| `sidebar.blackjack` | Подпункт "Блек-джек" |
| `sidebar.roulette` | Подпункт "Рулетки" |
| `sidebar.crash_games` | Подпункт "Краш игры" |
| `sidebar.game_shows` | Подпункт "Игровые Шоу" |
| `sidebar.more` | Подпункт "Ещё..." |
| `sidebar.favorites` | Пункт "Избранное" (только для авторизованных) |

---

### `nav` — Навигация (заголовки страниц)
| Ключ | Где используется |
|------|-----------------|
| `nav.home` | Заголовок главной страницы |
| `nav.games` | Заголовок страницы "Все игры" |
| `nav.balance` | Заголовок страницы "Баланс" |
| `nav.settings` | Заголовок страницы "Настройки" |
| `nav.raffles` | Заголовок страницы "Розыгрыши" |
| `nav.promotions` | Заголовок страницы "Акции" |
| `nav.favorites` | Заголовок страницы "Избранное" |

---

### `home` — Главная страница
| Ключ | Где используется |
|------|-----------------|
| `home.popular_title` | Заголовок секции "Популярные игры" |
| `home.all_games_title` | Заголовок секции "Все игры" |
| `home.games_not_found` | Текст когда игры не найдены |
| `home.search_placeholder` | Placeholder поля поиска игр |
| `home.search_count_suffix` | Суффикс счётчика найденных игр (напр. "игр", "games") |
| `home.smart_search_title` | Заголовок модалки смарт-поиска |
| `home.daily_spin_toast` | Toast при нажатии "Дейли-крут" |
| `home.lucky_ticket_used` | Toast если билет уже использован |
| `home.soon` | Надпись "Скоро" на недоступных играх |
| `home.hot` | Надпись "Хит" на горячих играх |

---

### `auth` — Авторизация и регистрация
| Ключ | Где используется |
|------|-----------------|
| `auth.login_title` | Заголовок формы входа |
| `auth.register_title` | Заголовок формы регистрации |
| `auth.tab_email` | Таб "Email" |
| `auth.tab_phone` | Таб "Телефон" |
| `auth.email_placeholder` | Placeholder поля email |
| `auth.phone_placeholder` | Placeholder поля телефона |
| `auth.username_placeholder` | Placeholder поля имени пользователя |
| `auth.password_placeholder` | Placeholder поля пароля |
| `auth.confirm_password_placeholder` | Placeholder подтверждения пароля |
| `auth.login_btn` | Кнопка "Войти" |
| `auth.register_btn` | Кнопка "Зарегистрироваться" |
| `auth.no_account` | Ссылка "Нету аккаунта?" |
| `auth.have_account` | Ссылка "Уже есть аккаунт?" |
| `auth.forgot_password` | Ссылка "Забыли пароль?" |
| `auth.policy_agree` | Чекбокс согласия с политикой |
| `auth.restore_title` | Заголовок формы восстановления пароля |
| `auth.restore_sub` | Подзаголовок восстановления пароля |
| `auth.restore_btn` | Кнопка "Отправить" в форме восстановления |
| `auth.restore_confirm_btn` | Кнопка "Подтвердить" в форме восстановления |
| `auth.tfa_title` | Заголовок модалки 2FA |
| `auth.tfa_desc` | Описание под заголовком 2FA |
| `auth.tfa_code_ph` | Placeholder поля кода 2FA |
| `auth.tfa_btn` | Кнопка подтверждения 2FA |
| `auth.tfa_password_ph` | Placeholder поля пароля в 2FA |
| `auth.tfa_save_hint` | Подсказка "Сохраните в надёжном месте" |
| `auth.tfa_your_code` | Метка перед кодом 2FA |

---

### `payment` — Страница пополнения баланса
| Ключ | Где используется |
|------|-----------------|
| `payment.title` | Заголовок секции данных карты |
| `payment.card_number` | Label поля номера карты |
| `payment.expiry` | Label поля срока действия |
| `payment.expiry_ph` | Placeholder срока (ММ / ГГ) |
| `payment.holder` | Label поля держателя карты |
| `payment.cvv` | Label поля CVV |
| `payment.card_holder_label` | Надпись "ДЕРЖАТЕЛЬ" на визуализации карты |
| `payment.card_expiry_label` | Надпись "СРОК" на визуализации карты |
| `payment.address_title` | Заголовок секции "Расчётный адрес" |
| `payment.country` | Label поля "Страна" |
| `payment.select_country` | Placeholder select страны |
| `payment.country_ua` | Название Украины в select |
| `payment.country_ru` | Название России в select |
| `payment.country_us` | Название США в select |
| `payment.zip` | Label поля "Почтовый индекс" |
| `payment.zip_ph` | Placeholder индекса |
| `payment.region` | Label поля "Область / Регион" |
| `payment.select_region` | Placeholder select региона (когда страна выбрана) |
| `payment.no_region` | Placeholder select региона (страна не выбрана) |
| `payment.city` | Label поля "Город" |
| `payment.city_ph` | Placeholder города |
| `payment.street` | Label поля "Улица" |
| `payment.street_ph` | Placeholder улицы |
| `payment.house` | Label поля "Дом" |
| `payment.house_ph` | Placeholder дома |
| `payment.apt` | Label поля "Квартира" |
| `payment.apt_ph` | Placeholder квартиры |
| `payment.optional` | Текст "(необяз.)" рядом с полем |
| `payment.balance` | Label "Текущий баланс" |
| `payment.amount_label` | Label "Сумма пополнения" |
| `payment.deposit_btn` | Кнопка "Пополнить" |
| `payment.alt_methods` | Заголовок "Другие способы" |
| `payment.policy_link` | Ссылка на политику пополнения |
| `payment.fee` | Label "Комиссия" |
| `payment.total` | Label "Итого к оплате" |
| `payment.card_valid` | Сообщение "Карта действительна ✓" |
| `payment.card_invalid` | Ошибка: "Неверный номер карты" |
| `payment.cvv_error` | Ошибка: "Неверный CVV" |
| `payment.holder_error` | Ошибка: "Введите имя держателя" |
| `payment.country_error` | Ошибка: "Выберите страну" |
| `payment.amount_error` | Ошибка: "Введите сумму" |
| `payment.ssl` | Надпись "SSL защита" |
| `payment.enter_card` | Toast: "Введите корректный номер карты" |
| `payment.card_invalid_luhn` | Toast: "Номер карты недействителен" |
| `payment.check_expiry` | Toast: "Проверьте срок действия" |
| `payment.enter_cvv` | Toast: "Введите CVV" |
| `payment.enter_amount` | Toast: "Введите сумму" |
| `payment.deposited` | Toast: "Пополнено: X ₴" |

---

### `settings` — Страница настроек
| Ключ | Где используется |
|------|-----------------|
| `settings.title` | Заголовок страницы настроек |
| `settings.tab_profile` | Таб "Профиль" |
| `settings.tab_security` | Таб "Безопасность" |
| `settings.tab_policy` | Таб "Политика" |
| `settings.edit_profile` | Подзаголовок "Редактировать профиль" |
| `settings.contact_info` | Подзаголовок "Контактная информация" |
| `settings.username_label` | Label "Имя пользователя" |
| `settings.username_ph` | Placeholder поля имени |
| `settings.email_label` | Label "Почта" |
| `settings.phone_label` | Label "Телефон" |
| `settings.verified` | Статус "Подтверждено ✓" |
| `settings.not_verified` | Статус "Ожидание" |
| `settings.currency_label` | Label "Валюта" |
| `settings.tfa_label` | Label "Двухфакторная защита" |
| `settings.tfa_desc` | Описание 2FA |
| `settings.change_password` | Заголовок "Смена пароля" |
| `settings.account_security` | Заголовок "Защита аккаунта" |
| `settings.old_password_label` | Label "Текущий пароль" |
| `settings.old_password_ph` | Placeholder текущего пароля |
| `settings.new_password_label` | Label "Новый пароль" |
| `settings.new_password_ph` | Placeholder нового пароля |
| `settings.confirm_password_label` | Label "Подтвердить" |
| `settings.confirm_password_ph` | Placeholder подтверждения |
| `settings.change_btn` | Кнопка "Сменить пароль" |
| `settings.save_btn` | Кнопка "Сохранить изменения" |
| `settings.promo_title` | Заголовок секции промокода |
| `settings.promo_ph` | Placeholder поля промокода |
| `settings.promo_apply` | Кнопка "Применить" |
| `settings.logout_btn` | Кнопка "Выйти" |
| `settings.stat_balance` | Label "Баланс" в статах |
| `settings.stat_verified` | Label "Верификация" в статах |
| `settings.stat_tfa` | Label "2FA" в статах |
| `settings.stat_date` | Label "Дата регистрации" в статах |
| `settings.policy_title` | Заголовок "Политика казино" |
| `settings.policy_loading` | "Загрузка..." |
| `settings.doc_verification` | Заголовок "Верификация документов" |
| `settings.doc_verification_sub` | Подпись под верификацией |
| `settings.avatar_title` | Заголовок "Аватарка профиля" |
| `settings.avatar_upload` | "Нажмите чтобы выбрать файл" |
| `settings.avatar_hint` | Подсказка форматов (JPG, PNG...) |
| `settings.avatar_save` | Кнопка "Сохранить" |
| `settings.avatar_error_size` | Ошибка: "Макс. 2 МБ" |
| `settings.avatar_choose` | Ошибка: "Выберите файл" |
| `settings.avatar_saving` | "Сохранение..." (кнопка в процессе) |
| `settings.avatar_updated` | Toast: "Аватарка обновлена ✓" |
| `settings.avatar_error` | Ошибка сохранения аватарки |
| `settings.avatar_conn_error` | Ошибка соединения при сохранении |

---

### `balance` — Страница баланса
| Ключ | Где используется |
|------|-----------------|
| `balance.title` | Заголовок страницы |
| `balance.deposit_title` | "Пополнить баланс" |
| `balance.method_card` | Таб "Банковская карта" |
| `balance.method_crypto` | Таб "Крипто" |
| `balance.method_p2p` | Таб "P2P перевод" |

---

### `favorites` — Страница избранного
| Ключ | Где используется |
|------|-----------------|
| `favorites.title` | Заголовок страницы |
| `favorites.empty` | "Нет избранных игр" |
| `favorites.empty_sub` | Подсказка "Нажмите ♡ на любую игру" |
| `favorites.add_btn` | Кнопка "Добавить" |
| `favorites.selected` | "Выбрано" (счётчик) |
| `favorites.search_ph` | Placeholder поиска в модалке |

---

### `raffles` — Розыгрыши
| Ключ | Где используется |
|------|-----------------|
| `raffles.title` | Заголовок страницы |
| `raffles.participate` | Кнопка "Участвовать" |
| `raffles.days_left` | "дн. осталось" |
| `raffles.ended` | "Завершён" |
| `raffles.from` | "С" (начало даты) |
| `raffles.to` | "по" (конец даты) |

---

### `promotions` — Акции
| Ключ | Где используется |
|------|-----------------|
| `promotions.title` | Заголовок страницы |
| `promotions.play_now` | Кнопка "Играть" |
| `promotions.standard_mult` | "Стандарт" (множитель) |
| `promotions.promo_mult` | "С промо" (множитель) |
| `promotions.win_chance` | "Шанс выигрыша" |
| `promotions.from` | "С" |
| `promotions.to` | "по" |

---

### `modals` — Модальные окна
| Ключ | Где используется |
|------|-----------------|
| `modals.close` | Кнопка закрытия |
| `modals.loading` | "Загрузка..." |
| `modals.licenses_title` | Заголовок модалки Лицензий |
| `modals.lic_tab_games` | Таб "Игры" в лицензиях |
| `modals.lic_tab_deposits` | Таб "Депозиты" в лицензиях |
| `modals.lic_tab_casino` | Таб "Казино" в лицензиях |
| `modals.faq_title` | Заголовок FAQ |
| `modals.support_title` | Заголовок Тех-Поддержки |
| `modals.support_contact_label` | Label "Контакт" |
| `modals.support_contact_ph` | Placeholder контакта |
| `modals.support_subject_label` | Label "Тема" |
| `modals.support_subject_ph` | Placeholder темы |
| `modals.support_desc_label` | Label "Описание" |
| `modals.support_desc_ph` | Placeholder описания |
| `modals.support_send` | Кнопка "Отправить" |
| `modals.support_desc` | "Свяжитесь с нашей службой..." |
| `modals.rules_title` | Заголовок "Правила и условия" |
| `modals.providers_title` | Заголовок "Провайдеры" |
| `modals.payments_title` | Заголовок "Платёжные системы" |
| `modals.contacts_title` | Заголовок "Контакты" |
| `modals.contacts_desc` | Описание в контактах |
| `modals.contacts_info` | Текст контактной информации (email, часы) |
| `modals.daily_spin_title` | Заголовок "Дейли-крут" |
| `modals.smart_search_title` | Заголовок "Смарт-поиск" |
| `modals.smart_search_ph` | Placeholder поиска |
| `modals.banner_title` | Заголовок баннерной модалки |
| `modals.banner_sub` | Подзаголовок баннерной модалки |
| `modals.tab_general` | Таб "Общие" (правила) |
| `modals.tab_deposits` | Таб "Депозиты" (правила) |
| `modals.tab_withdraw` | Таб "Выводы" (правила) |
| `modals.tab_bonuses` | Таб "Бонусы" (правила) |
| `modals.policy_title` | Заголовок политики казино |
| `modals.policy_scroll_hint` | "Прокрутите вниз чтобы принять" |
| `modals.policy_accept` | Кнопка "Принять и продолжить" |
| `modals.dep_policy_title` | Заголовок политики пополнения |
| `modals.verify_title` | Заголовок "Верификация" |
| `modals.verify_upload` | "Нажмите для загрузки" |
| `modals.verify_hint` | Форматы файлов верификации |
| `modals.verify_send` | Кнопка "Отправить" |
| `modals.promo_title` | Заголовок "Промокод" |
| `modals.promo_ph` | Placeholder промокода |
| `modals.promo_apply` | Кнопка "Применить" |
| `modals.game_play` | Кнопка "Играть" в модалке игры |
| `modals.fav_title` | Заголовок "Избранное" в модалке |
| `modals.fav_selected` | "Выбрано" |
| `modals.fav_add` | Кнопка "Добавить" |
| `modals.fav_search_ph` | Placeholder поиска в избранном |
| `modals.forgot_title` | Заголовок "Восстановление пароля" |
| `modals.forgot_sub` | Подзаголовок восстановления |
| `modals.forgot_contact_label` | Label "Почта или телефон" |
| `modals.forgot_contact_ph` | Placeholder контакта |
| `modals.forgot_2fa_label` | Label "2FA код" |
| `modals.forgot_2fa_hint` | Подсказка о 2FA |
| `modals.forgot_send` | Кнопка "Отправить" |
| `modals.pay_cat_cards` | Категория "Банковские карты" |
| `modals.pay_cat_ewallet` | Категория "Электронные кошельки" |
| `modals.pay_cat_faq` | Категория "Безопасность и FAQ" |

---

### `dropdown` — Выпадающее меню профиля
| Ключ | Где используется |
|------|-----------------|
| `dropdown.profile` | "Профиль" |
| `dropdown.balance` | "Баланс" |
| `dropdown.settings` | "Настройки" |
| `dropdown.promo` | "Промокоды" |
| `dropdown.logout` | "Выйти" |

---

### `footer` — Нижний колонтитул (bottombar)
| Ключ | Где используется |
|------|-----------------|
| `footer.rights` | "Все права защищены" |
| `footer.about` | Ссылка "Про нас" |
| `footer.faq` | Ссылка "FAQ" |
| `footer.rules` | Ссылка "Правила и условия" |
| `footer.licenses` | Ссылка "Лицензии" |
| `footer.providers` | Ссылка "Провайдеры" |
| `footer.payments` | Ссылка "Payment системы" |
| `footer.contacts` | Ссылка "Контакты" |
| `footer.support` | Ссылка "Тех-поддержка" |
| `footer.all_games` | Ссылка "Все игры" |
| `footer.crash` | Ссылка "Краш" |
| `footer.roulette` | Ссылка "Рулетка" |
| `footer.slots` | Ссылка "Слоты" |
| `footer.jump` | Ссылка "Jump" |
| `footer.jackpots` | Ссылка "Джекпоты" |
| `footer.blackjack` | Ссылка "BlackJack" |
| `footer.table` | Ссылка "Настольные игры" |

---

### `toasts` — Уведомления (всплывашки)
| Ключ | Где используется |
|------|-----------------|
| `toasts.login_success` | "Добро пожаловать!" |
| `toasts.logout_success` | "Вы вышли из аккаунта" |
| `toasts.register_success` | "Аккаунт создан!" |
| `toasts.save_success` | "Сохранено" |
| `toasts.promo_success` | "Промокод применён!" |
| `toasts.copy_success` | "Скопировано" |
| `toasts.deposit_success` | "Пополнение успешно!" |
| `toasts.error_generic` | "Произошла ошибка" |
| `toasts.error_fill` | "Заполните все обязательные поля" |
| `toasts.error_password_match` | "Пароли не совпадают" |
| `toasts.error_short_password` | "Пароль слишком короткий (мин. 6)" |

---

### `validation` — Ошибки валидации форм
| Ключ | Где используется |
|------|-----------------|
| `validation.enter_email_phone` | "Введите почту или телефон" |
| `validation.invalid_email` | "Некорректный email" |
| `validation.enter_password` | "Введите пароль" |
| `validation.enter_2fa` | "Введите 2FA код" |
| `validation.connection_error` | "Ошибка соединения" |
| `validation.username_min` | "Имя минимум 3 символа" |
| `validation.password_min` | "Пароль минимум 6 символов" |
| `validation.fill_fields` | "Заполните все поля" |
| `validation.error` | "Ошибка" |
| `validation.enter_promo` | "Введите промокод" |
| `validation.ticket_created` | "Тикет создан!" |
| `validation.forgot_confirm_btn` | "Подтвердить" (кнопка forgot) |

---

### `zip` — Валидация почтовых индексов
| Ключ | Где используется |
|------|-----------------|
| `zip.ua_format` | "Индекс UA: 5 цифр (напр. 49000)" |
| `zip.ru_format` | "Индекс РФ: 6 цифр (напр. 101000)" |
| `zip.us_format` | "ZIP США: 5 цифр (напр. 10001)" |
| `zip.region_unknown` | "Регион не определён" |
| `zip.state_unknown` | "Штат не определён" |
| `zip.possible_states` | "Возможные штаты" (перед списком) |
| `zip.specify_country` | "Укажите страну для определения региона" |

---

### `support` — Форма тех-поддержки
| Ключ | Где используется |
|------|-----------------|
| `support.sent_ok` | "Отправлено!" (успешная отправка тикета) |

---

## 🔧 Пример: добавить новый язык

**1.** Создай файл `config/languages/de.json`:
```json
{
  "lang_label": "Deutsch",
  "lang_flag": "🇩🇪",
  "currency": "EUR",
  "currency_sym": "€",
  "topbar": {
    "login": "ANMELDEN"
  },
  "auth": {
    "login_btn": "Anmelden"
  }
  // ... все остальные ключи
}
```

**2.** В `config/site.json` добавь `DE` в `site_language`:
```json
"site_language": "RU,UA,US,DE",
"default_language": "DE"
```

**3.** Перезапусти сервер — язык появится в переключателе автоматически.

---

## ⚡ Быстрая замена одного слова во всех языках

```bash
# Пример: поменять "Войти" → "Вход" в ru.json
python3 -c "
import json
f = 'config/languages/ru.json'
d = json.load(open(f))
d['auth']['login_btn'] = 'Вход'
json.dump(d, open(f,'w'), ensure_ascii=False, indent=2)
"
```

---

## 📝 Примечания

- **Региональные названия** (области UA, регионы RU, штаты US) **не переводятся** — они технические данные платёжной формы
- **FAQ и Розыгрыши** берут текст из `config/faq.json`, `config/raffles.json` — редактируй там
- **Политика казино** берётся из `config/site.json` → `casino_policy_text`
- Если ключ не найден в JSON — отображается сам ключ (напр. `auth.login_btn`), это сигнал что что-то пропущено
