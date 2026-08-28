# dev-angel-7553.dev — Telegram Bot API Proxy

## Детальная документация

## Базовый URL

```
https://dev-angel-7553.dev/bot<TOKEN>/<METHOD>
```

Где `<TOKEN>` — токен бота в формате `123456789:AAxxx...`.

Прокси полностью совместим с Telegram Bot API, но имеет ряд отличий.

---

## Форматы запросов

### Поддерживаемые форматы

| Формат | Метод | Примечание |
|--------|-------|------------|
| **Form data** | `application/x-www-form-urlencoded` | Рекомендуется для всех методов |
| **JSON body** | `application/json` | Работает для большинства методов |
| **Multipart** | `multipart/form-data` | Для загрузки файлов (sendPhoto, sendDocument и т.д.) |

### Важно: отличия от официального API

| Параметр | Официальный API | Прокси dev-angel-7553.dev |
|----------|-----------------|---------------------------|
| `getUpdates` + `allowed_updates` | JSON массив | **Не поддерживается** (400 Bad Request) |
| `setMyCommands` + form data | JSON-строка в поле `commands` | **Только JSON body** |
| `sendDocument` / `sendPhoto` | multipart | Multipart (поддерживается) |
| `getFile` + скачивание | `api.telegram.org/file/bot<TOKEN>/<path>` | `dev-angel-7553.dev/file/bot<TOKEN>/<path>` |

---

## Авторизация

Токен передаётся в URL: `https://dev-angel-7553.dev/bot<TOKEN>/<METHOD>`.

Никаких дополнительных заголовков не требуется.

```bash
curl -s "https://dev-angel-7553.dev/bot123:AAxxx/getMe"
```

---

## Формат ответов

Все ответы — JSON. Структура совместима с Telegram Bot API:

```json
{
  "ok": true,
  "result": { ... },
  "error_code": null,
  "description": null
}
```

При ошибке:
```json
{
  "ok": false,
  "result": null,
  "error_code": 400,
  "description": "Bad Request: ..."
}
```

**Особенность прокси:** `error_code` и `description` всегда присутствуют (могут быть `null` при успехе).

---

## Методы API

### Информация о боте

#### getMe

Получить информацию о боте.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getMe"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "id": 600000000132,
    "is_bot": true,
    "first_name": "kise pay",
    "username": "kise",
    "can_join_groups": true,
    "can_read_all_group_messages": false,
    "supports_inline_queries": true,
    "can_connect_to_business": false
  }
}
```

Дополнительные поля прокси:
- `supports_guest_queries` — поддержка guest queries (кастомное поле прокси)

---

#### getMyName / setMyName

```bash
# Получить
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getMyName"

# Установить
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setMyName" -d "name=My Bot"
```

---

#### getMyDescription / setMyDescription

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setMyDescription" -d "description=Bot description"
```

---

#### getMyShortDescription / setMyShortDescription

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setMyShortDescription" -d "short_description=Short bio"
```

---

#### getMyCommands / setMyCommands

**Важно:** `setMyCommands` работает только с JSON body, не с form data.

```bash
# Получить
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getMyCommands"

# Установить (только JSON!)
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setMyCommands" \
  -H "Content-Type: application/json" \
  -d '{"commands":[{"command":"start","description":"Start"},{"command":"help","description":"Help"}]}'
```

---

#### getMyDefaultAdministratorRights

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getMyDefaultAdministratorRights"
```

---

### Получение обновлений (Updates)

#### getUpdates

Long polling — получение обновлений.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getUpdates" -d "offset=0&timeout=5"
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `offset` | int | ID первого обновления (+1 от последнего) |
| `limit` | int | Кол-во обновлений (1-100, по умолчанию 100) |
| `timeout` | int | Long polling таймаут в секундах |

**Ограничение:** `allowed_updates` **не поддерживается** прокси. Бот получает все типы обновлений автоматически.

**Типы обновлений в ответе:**
- `message` — новое сообщение
- `edited_message` — изменённое сообщение
- `channel_post` — пост в канале
- `edited_channel_post` — изменённый пост
- `deleted_messages` — удалённые сообщения
- `callback_query` — нажатие inline-кнопки
- `inline_query` — inline запрос
- `pre_checkout_query` — pre-checkout для платежей
- `message_reaction` — реакция на сообщение
- `chat_member` — изменение статуса участника
- `my_chat_member` — изменение статуса бота

**Особенность ответа:** все поля присутствуют, но `null` если неактивны:
```json
{
  "update_id": 70,
  "message": null,
  "channel_post": null,
  "callback_query": null,
  "inline_query": null,
  "edited_message": null,
  "deleted_messages": null,
  "message_reaction": null,
  ...
}
```

---

#### getWebhookInfo

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getWebhookInfo"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "url": "",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "last_error_date": null,
    "last_error_message": null
  }
}
```

---

### Отправка сообщений

#### sendMessage

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendMessage" \
  -d "chat_id=123456" \
  -d "text=Hello!"
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `chat_id` | int/string | ID чата |
| `text` | string | Текст (1-4096 символов) |
| `parse_mode` | string | `HTML`, `MarkdownV2`, `Markdown` |
| `reply_markup` | JSON-строка | Inline/Reply клавиатура |
| `reply_to_message_id` | int | Ответ на сообщение |
| `disable_notification` | bool | Без звука |
| `protect_content` | bool | Защита от пересылки |

---

#### sendPhoto

```bash
# Загрузка файла
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendPhoto" \
  -F "chat_id=123456" \
  -F "photo=@photo.jpg" \
  -F "caption=Описание"

# По file_id
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendPhoto" \
  -d "chat_id=123456" \
  -d "photo=AgACAgIAAxkBAAI..."
```

---

#### sendDocument

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendDocument" \
  -F "chat_id=123456" \
  -F "document=@file.pdf" \
  -F "caption=Документ"
```

---

#### sendVideo / sendAudio / sendVoice / sendAnimation / sendSticker

Аналогично `sendPhoto` — multipart для файлов, `file_id` для повторной отправки.

---

#### forwardMessage

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/forwardMessage" \
  -d "chat_id=123456" \
  -d "from_chat_id=789" \
  -d "message_id=42"
```

---

#### copyMessage

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/copyMessage" \
  -d "chat_id=123456" \
  -d "from_chat_id=789" \
  -d "message_id=42"
```

---

#### editMessageText

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/editMessageText" \
  -d "chat_id=123456" \
  -d "message_id=42" \
  -d "text=New text"
```

---

#### deleteMessage

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/deleteMessage" \
  -d "chat_id=123456" \
  -d "message_id=42"
```

---

#### sendChatAction

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendChatAction" \
  -d "chat_id=123456" \
  -d "action=typing"
```

Действия: `typing`, `upload_photo`, `record_video`, `upload_video`, `record_voice`, `upload_voice`, `upload_document`, `find_location`, `record_video_note`, `upload_video_note`.

---

### Inline-кнопки и Callbacks

#### answerCallbackQuery

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/answerCallbackQuery" \
  -d "callback_query_id=QUERY_ID" \
  -d "text=Готово!"
```

---

#### answerInlineQuery

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/answerInlineQuery" \
  -d "inline_query_id=QUERY_ID" \
  -d "results=[{\"type\":\"article\",\"id\":\"1\",\"title\":\"Test\",\"input_message_content\":{\"message_text\":\"Hello\"}}]" \
  -d "cache_time=1"
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `inline_query_id` | string | ID запроса |
| `results` | JSON-строка | Массив результатов |
| `cache_time` | int | Время кэширования (сек) |
| `is_personal` | bool | Персональные результаты |
| `next_offset` | string | Offset для пагинации |
| `switch_pm_text` | string | Текст кнопки перехода в ЛС |
| `switch_pm_parameter` | string | Параметр для /start |

---

### Чаты

#### getChat

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getChat" -d "chat_id=123456"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "id": 123456,
    "type": "private",
    "username": "user123",
    "first_name": "John"
  }
}
```

Типы чатов: `private`, `group`, `supergroup`, `channel`.

---

#### getChatMember

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getChatMember" \
  -d "chat_id=-100123456" \
  -d "user_id=789"
```

**Важно:** работает только для `supergroup` и `channel` (не для `private` чатов).

---

#### getChatAdministrators

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getChatAdministrators" -d "chat_id=-100123456"
```

**Важно:** только для `supergroup` и `channel`.

---

#### getChatMemberCount

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getChatMemberCount" -d "chat_id=-100123456"
```

---

### Профили пользователей

#### getUserProfilePhotos

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getUserProfilePhotos" \
  -d "user_id=123456" \
  -d "limit=1"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "total_count": 46,
    "photos": [[...]]
  }
}
```

---

### Файлы

#### getFile

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getFile" -d "file_id=FILE_ID"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "file_id": "document_xxx",
    "file_unique_id": "uniq_xxx",
    "file_size": 13351,
    "file_path": "bot-api/..."
  }
}
```

#### Скачивание файла

```
https://dev-angel-7553.dev/file/bot<TOKEN>/<file_path>
```

**Пример:**
```bash
FILE_PATH="bot-api/ZG9jdW1lbnRf..."
curl -o download.pdf "https://dev-angel-7553.dev/file/bot<TOKEN>/$FILE_PATH"
```

---

### Telegram Stars (Платежи)

#### sendInvoice

Отправка счёта для оплаты Telegram Stars.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendInvoice" \
  -d "chat_id=123456" \
  -d "title=Оплата 10 Stars" \
  -d "description=Оплата на сумму 10 Stars" \
  -d "payload=payment_001" \
  -d "currency=XTR" \
  -d 'prices=[{"label":"10 Stars","amount":10}]'
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `chat_id` | int | ID чата |
| `title` | string | Название (1-32 символа) |
| `description` | string | Описание (1-255 символов) |
| `payload` | string | Произвольная строка (бот получит при оплате) |
| `currency` | string | `XTR` (Stars) |
| `prices` | JSON-строка | `[{"label":"...", "amount": N}]` |
| `provider_token` | string | **Не нужен для Stars** |

**Особенность Stars:** `currency` = `XTR`, `provider_token` не требуется.

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "message_id": 507,
    "from": {"id": 600000000132, "is_bot": true, "username": "kise"},
    "chat": {"id": 123456, "type": "private"},
    "invoice": {
      "title": "Оплата 10 Stars",
      "description": "...",
      "currency": "XTR",
      "total_amount": 10
    },
    "reply_markup": {
      "inline_keyboard": [[{"text": "Оплатить 10⭐", "pay": true}]]
    }
  }
}
```

---

#### answerPreCheckoutQuery

Подтверждение pre-checkout перед оплатой.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/answerPreCheckoutQuery" \
  -d "pre_checkout_query_id=QUERY_ID" \
  -d "ok=true"
```

При ошибке:
```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/answerPreCheckoutQuery" \
  -d "pre_checkout_query_id=QUERY_ID" \
  -d "ok=false" \
  -d "error_message=Ошибка оплаты"
```

---

#### successful_payment (входящее обновление)

При успешной оплате бот получает `message` с полем `successful_payment`:

```json
{
  "message": {
    "message_id": 508,
    "chat": {"id": 123456, "type": "private"},
    "from": {"id": 123456, "username": "user"},
    "successful_payment": {
      "currency": "XTR",
      "total_amount": 10,
      "invoice_payload": "payment_001",
      "telegram_payment_charge_id": "abc123..."
    }
  }
}
```

**Поля `successful_payment`:**

| Поле | Тип | Описание |
|------|-----|----------|
| `currency` | string | `XTR` |
| `total_amount` | int | Сумма в Stars |
| `invoice_payload` | string | Payload из sendInvoice |
| `telegram_payment_charge_id` | string | ID транзакции (для возврата) |

---

#### getStarTransactions

История Stars-транзакций бота.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getStarTransactions" \
  -d "offset=0" \
  -d "limit=10"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "transactions": [
      {
        "id": "bc91d8730f904425bb789531647e5845",
        "amount": -1,
        "nanostar_amount": null,
        "date": 1787936912,
        "source": {
          "type": "other",
          "user_id": 2022001,
          "is_refund": false
        },
        "receiver": null
      },
      {
        "id": "83fd2165e2244ab58116d865c16a3eda",
        "amount": 1,
        "nanostar_amount": null,
        "date": 1787936800,
        "source": {
          "type": "other",
          "user_id": 2022001,
          "is_refund": false
        },
        "receiver": null
      }
    ]
  }
}
```

**Интерпретация:**
- `amount > 0` — Stars **получены** ботом
- `amount < 0` — Stars **потрачены** ботом (например, на sendGift)
- `nanostar_amount` — дробная часть Stars (в наностарах, 1 Star = 10⁹ наностар)

**Поля транзакции:**

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | string | ID транзакции |
| `amount` | int | Сумма (+ получено, - потрачено) |
| `nanostar_amount` | int/null | Дробные Stars |
| `date` | int | Unix timestamp |
| `source` | object | Источник транзакции |
| `source.type` | string | `other`, `user`, `fragment`, `telegram_ad_platform` |
| `source.user_id` | int/null | ID пользователя |
| `source.is_refund` | bool | Возврат |
| `receiver` | object/null | Получатель (для исходящих) |

---

#### refundStarPayment

Возврат Stars пользователю.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/refundStarPayment" \
  -d "user_id=123456" \
  -d "telegram_payment_charge_id=abc123..."
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `user_id` | int | ID пользователя (обязательно) |
| `telegram_payment_charge_id` | string | ID транзакции из `successful_payment` |

---

### Telegram Gifts

#### getAvailableGifts

Список доступных подарков.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/getAvailableGifts"
```

**Ответ:**
```json
{
  "ok": true,
  "result": {
    "gifts": [
      {
        "id": "5168043875654172773",
        "sticker": {
          "file_id": "ogdoc:sticker:...",
          "file_unique_id": "ogdoc:...",
          "type": "regular",
          "width": 512,
          "height": 512,
          "is_animated": true,
          "is_video": false,
          "emoji": "🎁",
          "file_size": 60319
        },
        "star_count": 100,
        "upgrade_star_count": null,
        "total_count": null
      }
    ]
  }
}
```

**Каталог подарков (21 шт):**

| ID | Emoji | Stars |
|----|-------|-------|
| 5168043875654172773 | 🎁 | 100 |
| 5168103777563050263 | 🎁 | 25 |
| 5170144170496491616 | 🎁 | 50 |
| 5170145012310081615 | 🎁 | 15 |
| 5170233102089322756 | 🎁 | 15 |
| 5170250947678437525 | 🎁 | 25 |
| 5170314324215857265 | 🎁 | 50 |
| 5170521118301225164 | 🎁 | 100 |
| 5170564780938756245 | 🎁 | 50 |
| 5170690322832818290 | 🎁 | 100 |
| 5821261908354794038 | 🎁 | 15 |
| 5825801628657124140 | 🎁 | 15 |
| 5837059369300132790 | 🎁 | 25 |
| 5857140566201991735 | 🎁 | 100 |
| 5868561433997870501 | 🎁 | 500 |
| 5960747083030856414 | 🎁 | 100 |
| 5983471780763796287 | 🎁 | 50 |
| 6003643167683903930 | 🎁 | 15 |
| 6014591077976114307 | 🎁 | 200 |
| 6028601630662853006 | 🎁 | 50 |
| 6046178578163303744 | 🎁 | 50 |

---

#### sendGift

Отправка подарка пользователю за Stars со счёта бота.

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendGift" \
  -d "user_id=123456" \
  -d "gift_id=5168043875654172773"
```

**Параметры:**

| Параметр | Тип | Описание |
|----------|-----|----------|
| `user_id` | int | ID получателя |
| `gift_id` | string | ID подарка из `getAvailableGifts` |
| `text` | string | Текст-сопроводление (опц.) |
| `text_parse_mode` | string | `HTML` / `MarkdownV2` (опц.) |

**Важно:** Stars списываются с баланса бота-отправителя. При недостаточном балансе: `Bad Request: not enough Stars on the bot owner's balance`.

---

### Реакции

#### setMessageReaction

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setMessageReaction" \
  -d "chat_id=-100123456" \
  -d "message_id=42" \
  -d 'reaction=[{"type":"emoji","emoji":"👍"}]'
```

**Важно:** работает только для `supergroup` и `channel`.

---

### Webhook

#### setWebhook / deleteWebhook

```bash
# Установить
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/setWebhook" \
  -d "url=https://example.com/webhook"

# Удалить
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/deleteWebhook"
```

---

## Коды ошибок

| Код | Описание | Причина |
|-----|----------|---------|
| 400 | Bad Request | Неверные параметры / неподдерживаемый параметр |
| 401 | Unauthorized | Неверный токен |
| 403 | Forbidden | Нет прав на действие |
| 404 | Not Found | Метод/чат/пользователь не найден |
| 409 | Conflict | Другой getUpdates уже активен |
| 429 | Too Many Requests | Rate limit |

---

## Rate Limits

Прокси наследует лимиты Telegram Bot API:
- ~30 сообщений/сек на разных чатах
- ~20 сообщений/мин в один чат
- 1 активный `getUpdates` на бот

---

## Примеры на Python

### Базовый клиент

```python
import requests

TOKEN = "123:AAxxx"
API = f"https://dev-angel-7553.dev/bot{TOKEN}"

def tg(method, data=None):
    r = requests.post(f"{API}/{method}", data=data or {}, timeout=15)
    return r.json()

# Отправить сообщение
tg("sendMessage", {"chat_id": 123456, "text": "Hello!"})

# Отправить invoice (Stars)
tg("sendInvoice", {
    "chat_id": 123456,
    "title": "Оплата 10 Stars",
    "description": "Test",
    "payload": "order_001",
    "currency": "XTR",
    "prices": '[{"label":"10 Stars","amount":10}]',
})

# Long polling
offset = 0
while True:
    r = tg("getUpdates", {"offset": offset, "timeout": 5})
    for u in r.get("result", []):
        offset = u["update_id"] + 1
        # Обработка...
```

### Загрузка файла

```python
import io

with open("photo.jpg", "rb") as f:
    r = requests.post(f"{API}/sendPhoto",
        files={"photo": ("photo.jpg", f, "image/jpeg")},
        data={"chat_id": 123456, "caption": "Photo"},
    )
print(r.json())
```

### Скачивание файла

```python
file_info = tg("getFile", {"file_id": "FILE_ID"})
file_path = file_info["result"]["file_path"]
download_url = f"https://dev-angel-7553.dev/file/bot{TOKEN}/{file_path}"
r = requests.get(download_url)
with open("download.jpg", "wb") as f:
    f.write(r.content)
```

---

## Примеры на bash/curl

### Отправка сообщения с inline-кнопкой

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendMessage" \
  -d "chat_id=123456" \
  -d "text=Выберите:" \
  -d 'reply_markup={"inline_keyboard":[[{"text":"Да","callback_data":"yes"},{"text":"Нет","callback_data":"no"}]]}'
```

### Stars invoice

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendInvoice" \
  -d "chat_id=123456" \
  -d "title=Покупка" \
  -d "description=Товар" \
  -d "payload=item_001" \
  -d "currency=XTR" \
  -d 'prices=[{"label":"50 Stars","amount":50}]'
```

### Отправка подарка

```bash
curl -s "https://dev-angel-7553.dev/bot<TOKEN>/sendGift" \
  -d "user_id=123456" \
  -d "gift_id=5168103777563050263"
```

---

## Чек-лист отличий от официального API

1. **URL:** `dev-angel-7553.dev` вместо `api.telegram.org`
2. **`allowed_updates`:** не поддерживается в `getUpdates`
3. **`setMyCommands`:** только JSON body (form data не работает для массива `commands`)
4. **File download:** `dev-angel-7553.dev/file/bot<TOKEN>/<path>` вместо `api.telegram.org/file/bot<TOKEN>/<path>`
5. **Response:** `error_code` и `description` всегда присутствуют (`null` при успехе)
6. **Guest queries:** кастомное поле `supports_guest_queries` (нестандартное)
