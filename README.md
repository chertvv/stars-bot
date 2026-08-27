# Telegram Stars Bot

Бот для приёма оплаты Telegram Stars. Поддержка inline-платежей, создания пользовательских ботов и переводов Stars по ссылке.

## Возможности

- **Создание ботов** — пользователь платит 50 Stars, присылает токен, бот запускается
- **Оплата Stars** — `/pay 100` или inline `@bot 10`
- **Перевод Stars** — `/send 100` генерирует ссылку `t.me/bot?start=chk_XXX`
- **Админ-панель** — бесплатное создание ботов, статистика

## Установка

### 1. Получите токен бота

1. Напишите [@BotFather](https://t.me/BotFather)
2. Отправьте `/newbot`
3. Скопируйте токен

### 2. Запуск через Docker

```bash
# Клонируйте репозиторий
git clone https://github.com/YOUR_USERNAME/stars-bot.git
cd stars-bot

# Запустите
docker build -f Dockerfile.stars_bot -t stars-bot .
docker run -d --name stars-bot --restart unless-stopped \
  -e BOT_TOKEN="ваш_токен" \
  -e ADMIN_ID="ваш_chat_id" \
  -v $(pwd)/user_bots.json:/app/user_bots.json \
  -v $(pwd)/checks.json:/app/checks.json \
  stars-bot
```

### 3. Узнайте свой chat_id

1. Запустите бота с `ADMIN_ID=0`
2. Напишите `/start`
3. В логах увидите свой chat_id
4. Остановите бота, укажите правильный `ADMIN_ID`
5. Запустите заново

## Команды

### Основной бот (@starsbot)

| Команда | Описание |
|---------|----------|
| `/start` | Меню с кнопками |
| `/pay 100` | Создать счёт на Stars |
| `/send 100` | Создать ссылку для перевода |
| `/stats` | Список пользовательских ботов (админ) |
| `/list` | Все токены (админ) |
| `/stop <id>` | Остановить бота (админ) |

### Пользовательские боты

| Команда | Описание |
|---------|----------|
| `/pay 100` | Создать счёт на Stars |
| `@bot 10` | Inline-счёт |
| `/start=chk_XXX` | Оплата перевода |

## Inline-режим

Чтобы включить inline для пользовательского бота:

1. Напишите [@BotFather](https://t.me/BotFather)
2. `/mybots` → выберите бота
3. **Bot Settings** → **Inline Mode** → **Turn on**

## Перевод Stars

1. Отправьте `/send 100` боту
2. Получите ссылку `t.me/bot?start=chk_XXXXXX`
3. Отправьте ссылку получателю
4. Получатель переходит по ссылке и оплачивает
5. Оба получают уведомление

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `BOT_TOKEN` | Токен основного бота | — |
| `ADMIN_ID` | Chat ID администратора | `2022001` |
| `API_BASE` | URL API сервера | `https://dev-angel-7553.dev` |

## Файлы

- `stars_bot.py` — основной код бота
- `user_bots.json` — токены пользовательских ботов (создаётся автоматически)
- `checks.json` — данные о переводах (создаётся автоматически)
- `Dockerfile.stars_bot` — Docker-образ

## Управление контейнером

```bash
# Логи
docker logs -f stars-bot

# Перезапуск
docker restart stars-bot

# Остановка
docker stop stars-bot

# Удаление
docker rm -f stars-bot
```
