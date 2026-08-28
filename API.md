# Stars Bot API Documentation

## Базовый URL

```
https://dev-angel-7553.dev/bot<TOKEN>/<METHOD>
```

Все запросы — `POST`, тело — JSON.

---

## 1. Кошельки

### Создание кошелька

Пользователь отправляет `/start` в бота, нажимает «Создать кошелёк», затем вводит токен своего бота.

**Параметры кошелька (wallets.json):**

| Поле       | Тип      | Описание                          |
|------------|----------|-----------------------------------|
| `token`    | string   | Токен бота пользователя            |
| `username` | string   | Username бота (без @)              |
| `base_url` | string   | API base (по умолчанию `https://dev-angel-7553.dev`) |
| `owner`    | int      | Telegram ID владельца              |
| `name`     | string   | Имя кошелька (пустое по умолчанию)  |
| `created`  | int      | Unix timestamp создания            |

**Адрес кошелька** — 8-значный код (`a-z0-9`), генерируется автоматически.

---

### Команды пользователя

| Команда                          | Описание                                      |
|----------------------------------|-----------------------------------------------|
| `/start`                         | Создать кошелёк / показать существующий         |
| `/start pay_XXXXXX`              | Оплата по ссылке (invoice через бота кошелька)  |
| `/start gift_XXXX`               | Активация подарка                               |
| `/my`                            | Показать свой кошелёк                           |
| `/send АДРЕС СУММА`             | Создать ссылку для оплаты                       |
| `/send ИМЯ СУММА`               | Создать ссылку по имени кошелька                 |
| `/send АДРЕС`                    | Запросить сумму                                 |
| `/pay N`                         | Оплата N Stars через основного бота              |
| `/redeem КОД`                    | Активировать промокод                           |
| `@bot N` (inline)               | Inline-счёт на N Stars через бота кошелька      |

---

## 2. Ссылки для оплаты (Checks)

### Создание ссылки

```
/send АДРЕС СУММА
```

Генерирует ссылку вида:
```
https://t.me/{bot_username}?start=pay_XXXXXXXX
```

Бот кошелька активируется на 5 минут для приёма оплаты.

**Структура (checks.json):**

| Поле      | Тип   | Описание                          |
|-----------|-------|-----------------------------------|
| `from`    | int   | ID создателя ссылки               |
| `amount`  | int   | Сумма в Stars                      |
| `wallet`  | string| Адрес кошелька-получателя          |
| `paid`    | bool  | Оплачена ли                        |
| `payer`   | int   | ID плательщика (после оплаты)      |
| `created` | int   | Unix timestamp                     |
| `paid_at` | int   | Unix timestamp оплаты              |

### Процесс оплаты

1. Плательщик открывает `https://t.me/{bot_username}?start=pay_XXXXXX`
2. Бот кошелька отправляет `sendInvoice` плательщику
3. Плательщик оплачивает Stars
4. `pre_checkout_query` → `answerPreCheckoutQuery(ok=true)`
5. `successful_payment` → чек плательщику + уведомление владельцу

---

## 3. Подарки (Gifts)

### Создание ссылки-подарка (только админ)

```
/gift N                    — N активаций (только rename)
/gift bear N               — N медведей (реальный TG Gift)
/gift bear us N            — N медведей + rename
/gift list                 — каталог подарков
```

Ссылка: `https://t.me/kise?start=gift_XXXXXXXX`

**Структура (gifts.json):**

| Поле         | Тип     | Описание                          |
|--------------|---------|-----------------------------------|
| `max`        | int     | Максимум активаций                |
| `used`       | array   | Список ID использовавших          |
| `item`       | string  | Ключ предмета из каталога         |
| `amount`     | int     | Количество                        |
| `rename`     | bool    | Бонус смены имени                 |
| `gift_id`    | string  | ID подарка в TG (для sendGift)    |
| `created`    | int     | Unix timestamp                    |

### Каталог подарков

| Ключ       | Название              | Stars |
|------------|-----------------------|-------|
| `trophy`   | Трофей                | 100   |
| `rose`     | Роза                  | 25    |
| `cake`     | Торт                  | 50    |
| `heart`    | Сердце                | 15    |
| `sock`     | Носок                 | 15    |
| `gift`     | Подарок               | 25    |
| `bouquet`  | Букет                 | 50    |
| `diamond`  | Бриллиант             | 100   |
| `rocket`   | Ракета                | 50    |
| `ring`     | Кольцо                | 100   |
| `ball`     | Мяч                   | 50    |
| `bear`     | Медведь               | 50    |
| `snoop`    | Snoop Dogg            | 200   |
| `cupid`    | Cupid Charm           | 500   |
| `santa`    | Santa Hat             | 50    |
| `sparkler` | Party Sparkler        | 15    |

### Отправка реального TG Gift

```python
sendGift(user_id, gift_id)
```
Вызывается через API основного бота при активации gift-ссылки.

---

## 4. Промокоды

### Создание (только админ)

```
/promo CODE N                — промокод (только rename)
/promo CODE bear N           — N медведей
/promo CODE bear us N        — N медведей + rename
/promos                      — список промокодов
/delpromo CODE               — удалить промокод
```

### Активация

```
/redeem CODE
```

**Структура (promos.json):**

| Поле         | Тип     | Описание                          |
|--------------|---------|-----------------------------------|
| `max`        | int     | Максимум активаций                |
| `used`       | array   | Список ID использовавших          |
| `item`       | string  | Ключ предмета                     |
| `rename`     | bool    | Бонус смены имени                 |
| `gift_id`    | string  | ID подарка в TG                   |
| `created`    | int     | Unix timestamp                    |

---

## 5. Баны

### Команды админа

```
/ban USER_ID [причина]      — забанить пользователя
/unban USER_ID              — разбанить
/banlist                    — список банов
```

Забаненный пользователь не может:
- Создавать кошельки
- Отправлять команды
- Использовать inline-кнопки

При любой попытке получает: `Вы забанены. Причина: ...`

Нельзя забанить админа.

**Структура (bans.json):**

```json
{
  "123456789": {
    "reason": "scam",
    "banned_at": 1709100000
  }
}
```

---

## 6. Админ-команды

### Управление кошельками

```
/stats                      — статистика бота
/wallets                    — список всех кошельков
/us АДРЕС ИМЯ              — задать имя кошелька
/del АДРЕС                  — удалить кошелёк
/find @username             — найти кошелёк по @username бота
/find 123456789             — найти кошелёк по ID владельца
/user 123456789             — профиль пользователя
/gifts                      — список активных gift-ссылок
```

### Статистика (`/stats`)

Возвращает:
- Количество кошельков
- Количество созданных ссылок
- Количество оплаченных ссылок
- Общая сумма оплат (Stars)
- Количество активных ботов (polling threads)

---

## 7. Telegram Bot API методы

### Основной бот

| Метод                  | Назначение                          |
|------------------------|-------------------------------------|
| `getMe`                | Проверка токена, получение username |
| `getUpdates`           | Long polling                        |
| `sendMessage`          | Отправка сообщения                   |
| `answerCallbackQuery`  | Ответ на inline-кнопку              |
| `sendInvoice`          | Создание счёта (Stars, XTR)          |
| `answerPreCheckoutQuery`| Подтверждение pre-checkout          |
| `sendGift`             | Отправка Telegram Gift              |

### Бот кошелька (user bot)

Каждый кошелёк использует токен пользователя для:
- `getUpdates` — polling (5 мин после `/send`)
- `sendInvoice` — счёт плательщику
- `answerInlineQuery` — inline-счёт
- `answerPreCheckoutQuery` — подтверждение оплаты
- `sendMessage` — чек об оплате + уведомление владельцу

---

## 8. Конфигурация

### Переменные окружения

| Переменная    | По умолчанию                          | Описание                |
|---------------|---------------------------------------|-------------------------|
| `BOT_TOKEN`   | `600000000132:...`                    | Токен основного бота     |
| `API_BASE`    | `https://dev-angel-7553.dev`          | API прокси               |
| `ADMIN_ID`    | `2022001`                             | Telegram ID админа      |

### Лимиты

| Параметр      | Значение |
|---------------|----------|
| `MIN_AMOUNT`  | 1 Star   |
| `MAX_AMOUNT`  | 10000 Stars |
| Gift activations | 1–1000 |
| Polling timeout | 5 минут |

### Файлы данных

| Файл          | Описание                          |
|---------------|-----------------------------------|
| `wallets.json`| Кошельки пользователей            |
| `checks.json` | Ссылки для оплаты                 |
| `gifts.json`  | Gift-ссылки                       |
| `promos.json` | Текстовые промокоды               |
| `bans.json`   | Список банов                      |

---

## 9. Запуск

### Локально

```bash
python3 stars_bot.py
```

### Docker

```bash
docker build -f Dockerfile.stars_bot -t stars-bot .
docker run -d --name stars-bot --restart unless-stopped \
  -v wallets.json:/app/wallets.json \
  -v checks.json:/app/checks.json \
  -v gifts.json:/app/gifts.json \
  -v promos.json:/app/promos.json \
  -v bans.json:/app/bans.json \
  stars-bot
```

---

## 10. Архитектура

```
Пользователь → @starsbot (/send АДРЕС 100)
                    ↓
           Создаёт check (pay_XXXXXX)
           Запускает polling бота кошелька (5 мин)
                    ↓
Плательщик → t.me/{bot_username}?start=pay_XXXXXX
                    ↓
           Бот кошелька sendInvoice → плательщик платит Stars
                    ↓
           successful_payment → чек плательщику
                              → уведомление владельцу
                    ↓
           Check помечается paid=true
```

### Inline-оплата

```
Пользователь → @bot 10 (inline query)
                    ↓
           Бот кошелька answerInlineQuery → статья с кнопкой "Оплатить"
                    ↓
           Плательщик нажимает → sendInvoice → оплата Stars
```
