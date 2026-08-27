#!/usr/bin/env python3
"""Telegram Stars Bot — создание пользовательских ботов с оплатой Stars.

Поток:
  1. /start -> кнопка «Создать своего бота»
  2. Оплата 100 Stars (админ пропускает оплату)
  3. Пользователь присылает токен -> валидация -> запуск polling в отдельном потоке
  4. Пользовательский бот: /pay N (счёт Stars) + inline @bot N
"""

import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path

import requests

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    "600000000132:bM552OzEgkTC6qbqkhGQpZBQTdYs9V2GcrBxqzlQS54",
)
API_BASE = os.environ.get("API_BASE", "https://dev-angel-7553.dev")
API = f"{API_BASE}/bot{BOT_TOKEN}"

ADMIN_ID = int(os.environ.get("ADMIN_ID", "2022001"))

MIN_AMOUNT = 1
MAX_AMOUNT = 10000
SUBSCRIPTION_PRICE = 50
SUBSCRIPTION_PAYLOAD = "create_bot_subscription"

TOKENS_FILE = Path(__file__).parent / "user_bots.json"
CHECKS_FILE = Path(__file__).parent / "checks.json"

session = requests.Session()

WAITING_FOR_TOKEN: set[int] = set()
WAITING_FOR_SD_TOKEN: set[int] = set()
WAITING_FOR_SD_AMOUNT: set[int] = set()
WAITING_FOR_SEND_AMOUNT: set[int] = set()
WAITING_FOR_SEND_AMOUNT_USER: dict[int, set[int]] = {}
user_bot_threads: dict[int, threading.Thread] = {}
user_bot_stop_flags: dict[int, threading.Event] = {}

BOT_USERNAME = "bot"


# ============================================================
# CHECKS (переводы Stars по ссылке)
# ============================================================

def load_checks() -> dict:
    if CHECKS_FILE.exists():
        try:
            return json.loads(CHECKS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_checks(checks: dict) -> None:
    try:
        CHECKS_FILE.write_text(
            json.dumps(checks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_checks error: {e!r}")


def create_check(from_id: int, amount: int, bot_username: str) -> str:
    code = f"chk_{secrets.token_hex(6)}"
    checks = load_checks()
    checks[code] = {
        "from": from_id,
        "amount": amount,
        "bot": bot_username,
        "paid": False,
        "created": int(time.time()),
    }
    save_checks(checks)
    return code


def get_check(code: str) -> dict | None:
    checks = load_checks()
    return checks.get(code)


def mark_check_paid(code: str, payer_id: int) -> None:
    checks = load_checks()
    if code in checks:
        checks[code]["paid"] = True
        checks[code]["payer"] = payer_id
        checks[code]["paid_at"] = int(time.time())
        save_checks(checks)


# ============================================================
# TOKEN STORAGE
# ============================================================

def load_tokens() -> dict:
    if TOKENS_FILE.exists():
        try:
            return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_tokens(tokens: dict) -> None:
    try:
        TOKENS_FILE.write_text(
            json.dumps(tokens, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_tokens error: {e!r}")


# ============================================================
# TELEGRAM API (основной бот через dev-angel-7553.dev)
# ============================================================

def telegram(method, data=None, timeout=15):
    try:
        r = session.post(f"{API}/{method}", json=data or {}, timeout=timeout)
        r.raise_for_status()
        if not r.text:
            return None
        return r.json()
    except Exception as e:
        print(f"API ERROR [{method}]: {e!r}")
        return None


def send_message(chat_id, text, reply_markup=None):
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram("sendMessage", payload)


def answer_callback(callback_id, text=None):
    data: dict = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    return telegram("answerCallbackQuery", data)


# ============================================================
# USER BOT API (пользовательские боты через api.telegram.org)
# ============================================================

def user_bot_api(base_url, token, method, data=None, timeout=15):
    url = f"{base_url}/bot{token}/{method}"
    try:
        r = session.post(url, json=data or {}, timeout=timeout)
        r.raise_for_status()
        if not r.text:
            return None
        return r.json()
    except Exception as e:
        print(f"USER BOT API [{method}]: {e!r}")
        return None


PROXY_API = API_BASE


def validate_token(token):
    """Проверяет токен через прокси dev-angel-7553.dev."""
    result = user_bot_api(PROXY_API, token, "getMe", timeout=10)
    if isinstance(result, dict) and result.get("ok") is True:
        return PROXY_API, result
    return None, None


# ============================================================
# ОСНОВНОЙ БOT: INVOICE
# ============================================================

def create_subscription_invoice(chat_id):
    return telegram("sendInvoice", {
        "chat_id": chat_id,
        "title": "Создание своего бота",
        "description": f"Подписка на 1 месяц — {SUBSCRIPTION_PRICE} Stars",
        "payload": SUBSCRIPTION_PAYLOAD,
        "currency": "XTR",
        "prices": [{"label": f"{SUBSCRIPTION_PRICE} Stars", "amount": SUBSCRIPTION_PRICE}],
    })


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ БОТ: INVOICE
# ============================================================

def find_bot_owner(token):
    """Находит chat_id владельца бота по токену."""
    tokens = load_tokens()
    for chat_id_str, info in tokens.items():
        if info.get("token") == token:
            return int(chat_id_str)
    return None


def user_bot_send_invoice(base_url, token, chat_id, amount):
    payload = f"stars_{chat_id}_{amount}"
    return user_bot_api(base_url, token, "sendInvoice", {
        "chat_id": chat_id,
        "title": "Оплата Stars",
        "description": f"Оплата на сумму {amount} Stars",
        "payload": payload,
        "currency": "XTR",
        "prices": [{"label": f"{amount} Stars", "amount": amount}],
    })


def user_bot_answer_inline(base_url, token, inline_query, amount):
    if not isinstance(inline_query, dict):
        return None
    query_id = inline_query.get("id")
    if not query_id:
        return None
    user = inline_query.get("from") or {}
    user_id = user.get("id", 0)
    payload = f"inline_{user_id}_{amount}"

    result = {
        "type": "article",
        "id": uuid.uuid4().hex,
        "title": f"Stars: счёт на {amount}",
        "description": f"Создать счёт на {amount} Stars",
        "input_message_content": {
            "message_text": f"Stars: счёт на оплату\n\nСумма: {amount} Stars",
            "parse_mode": "HTML",
        },
        "reply_markup": {
            "inline_keyboard": [[
                {"text": f"Оплатить {amount} Stars", "pay": True},
            ]],
        },
    }

    return user_bot_api(base_url, token, "answerInlineQuery", {
        "inline_query_id": query_id,
        "results": [result],
        "cache_time": 1,
        "is_personal": True,
    })


def user_bot_answer_pre_checkout(base_url, token, query):
    if not isinstance(query, dict):
        return None
    query_id = query.get("id")
    if not query_id:
        return None
    currency = query.get("currency", "XTR")
    amount = query.get("total_amount", 0)

    if currency != "XTR":
        return user_bot_api(base_url, token, "answerPreCheckoutQuery", {
            "pre_checkout_query_id": query_id,
            "ok": False,
            "error_message": "Поддерживается только Telegram Stars.",
        })
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        return user_bot_api(base_url, token, "answerPreCheckoutQuery", {
            "pre_checkout_query_id": query_id,
            "ok": False,
            "error_message": "Некорректная сумма.",
        })
    return user_bot_api(base_url, token, "answerPreCheckoutQuery", {
        "pre_checkout_query_id": query_id,
        "ok": True,
    })


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ БОТ: ОБРАБОТКА СООБЩЕНИЙ
# ============================================================

def user_bot_handle_message(base_url, token, message):
    if not isinstance(message, dict):
        return
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    if not chat_id:
        return

    # successful_payment
    if isinstance(message.get("successful_payment"), dict):
        payment = message["successful_payment"]
        amount = payment.get("total_amount", 0)
        payload = payment.get("invoice_payload", "")

        # Чек (перевод по ссылке)
        if payload.startswith("check_"):
            parts = payload.split("_", 3)
            if len(parts) >= 4:
                code = parts[0] + "_" + parts[1]
                from_id = int(parts[2]) if parts[2].isdigit() else 0
                mark_check_paid(code, chat_id)
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": f"Оплата {amount} Stars получена!",
                })
                if from_id:
                    user_bot_api(base_url, token, "sendMessage", {
                        "chat_id": from_id,
                        "text": (
                            f"Ваш перевод на {amount} Stars оплачен!\n"
                            f"Плательщик: пользователь {chat_id}"
                        ),
                    })
            else:
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": f"Оплата {amount} Stars получена!",
                })
        else:
            user_bot_api(base_url, token, "sendMessage", {
                "chat_id": chat_id,
                "text": (
                    f"Оплата успешно получена!\n\n"
                    f"Сумма: {amount} Stars\n"
                    f"Спасибо за оплату!"
                ),
            })
            # Уведомление владельцу бота
            owner_id = find_bot_owner(token)
            if owner_id and owner_id != chat_id:
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": owner_id,
                    "text": (
                        f"Новая оплата!\n\n"
                        f"Сумма: {amount} Stars\n"
                        f"Пользователь: {chat_id}"
                    ),
                })
        return

    text = message.get("text")
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return

    # /start (только с параметром chk_XXX)
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) > 1:
            start_param = parts[1].strip()
            if start_param.startswith("chk_"):
                check = get_check(start_param)
                if check is None:
                    user_bot_api(base_url, token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": "Ссылка недействительна."
                    })
                    return
                if check.get("paid"):
                    user_bot_api(base_url, token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": "Эта ссылка уже использована."
                    })
                    return
                amount = check["amount"]
                from_id = check["from"]
                payload = f"check_{start_param}_{from_id}_{amount}"
                result = user_bot_api(base_url, token, "sendInvoice", {
                    "chat_id": chat_id,
                    "title": f"Перевод {amount} Stars",
                    "description": f"Оплата перевода на {amount} Stars",
                    "payload": payload,
                    "currency": "XTR",
                    "prices": [{"label": f"{amount} Stars", "amount": amount}],
                })
                if not isinstance(result, dict) or result.get("ok") is not True:
                    desc = result.get("description", "Ошибка") if isinstance(result, dict) else "Нет ответа"
                    user_bot_api(base_url, token, "sendMessage", {
                        "chat_id": chat_id,
                        "text": f"Не удалось создать счёт.\n\n{desc}"
                    })
                return
        # /start без chk_ — игнорируем
        return

    # /send N — перевод Stars по ссылке (только если есть chk_)
    if text.startswith("/send"):
        return

    # /pay N
    match = re.fullmatch(r"/pay(?:@\w+)?\s+(\d+)", text, re.IGNORECASE)
    if not match:
        return

    amount = int(match.group(1))
    if amount <= 0:
        user_bot_api(base_url, token, "sendMessage", {
            "chat_id": chat_id,
            "text": "Сумма должна быть больше 0",
        })
        return
    if amount > MAX_AMOUNT:
        user_bot_api(base_url, token, "sendMessage", {
            "chat_id": chat_id,
            "text": f"Максимальная сумма — {MAX_AMOUNT} Stars",
        })
        return

    result = user_bot_send_invoice(base_url, token, chat_id, amount)
    if not isinstance(result, dict) or result.get("ok") is not True:
        desc = result.get("description", "Неизвестная ошибка") if isinstance(result, dict) else "Нет ответа"
        user_bot_api(base_url, token, "sendMessage", {
            "chat_id": chat_id,
            "text": f"Не удалось создать счёт.\n\n{desc}",
        })


def user_bot_handle_inline(base_url, token, inline_query):
    if not isinstance(inline_query, dict):
        return
    query_id = inline_query.get("id")
    if not query_id:
        return
    query = inline_query.get("query", "")
    if not isinstance(query, str):
        query = ""
    query = query.strip()

    if not query:
        user_bot_api(base_url, token, "answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "help",
                "title": "Создать счёт",
                "description": "Введите сумму, например 10",
                "input_message_content": {
                    "message_text": "Напишите сумму после имени бота.\n\nПример:\n@bot 10",
                },
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    match = re.fullmatch(r"(\d+)(?:\s*(?:stars?|xtr))?", query, re.IGNORECASE)
    if not match:
        user_bot_api(base_url, token, "answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "invalid",
                "title": "Неверная сумма",
                "description": "Например: @bot 10",
                "input_message_content": {
                    "message_text": "Неверная сумма.\n\nПример:\n@bot 10",
                },
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    amount = int(match.group(1))
    if amount < MIN_AMOUNT:
        user_bot_api(base_url, token, "answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "err_min",
                "title": "Сумма слишком маленькая",
                "description": f"Минимум — {MIN_AMOUNT} Stars",
                "input_message_content": {"message_text": f"Минимум — {MIN_AMOUNT} Stars"},
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return
    if amount > MAX_AMOUNT:
        user_bot_api(base_url, token, "answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "err_max",
                "title": "Сумма слишком большая",
                "description": f"Максимум — {MAX_AMOUNT} Stars",
                "input_message_content": {"message_text": f"Максимум — {MAX_AMOUNT} Stars"},
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    user_bot_answer_inline(base_url, token, inline_query, amount)


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ БОТ: POLLING THREAD
# ============================================================

def run_user_bot(chat_id, token, base_url):
    stop_flag = user_bot_stop_flags.get(chat_id)
    if stop_flag is None:
        stop_flag = threading.Event()
        user_bot_stop_flags[chat_id] = stop_flag

    offset = 0
    print(f"[user_bot {chat_id}] thread started (api={base_url})")

    while not stop_flag.is_set():
        try:
            result = user_bot_api(base_url, token, "getUpdates", {
                "offset": offset,
                "timeout": 5,
                "allowed_updates": ["message", "inline_query", "pre_checkout_query"],
            }, timeout=15)

            if result is None or not isinstance(result, dict) or result.get("ok") is not True:
                time.sleep(2)
                continue

            updates = result.get("result")
            if not isinstance(updates, list):
                time.sleep(1)
                continue

            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1

                inline_query = update.get("inline_query")
                if isinstance(inline_query, dict):
                    try:
                        user_bot_handle_inline(base_url, token, inline_query)
                    except Exception as e:
                        print(f"[user_bot {chat_id}] inline error: {e!r}")
                    continue

                message = update.get("message")
                if isinstance(message, dict):
                    try:
                        user_bot_handle_message(base_url, token, message)
                    except Exception as e:
                        print(f"[user_bot {chat_id}] message error: {e!r}")
                    continue

                pre_checkout = update.get("pre_checkout_query")
                if isinstance(pre_checkout, dict):
                    try:
                        user_bot_answer_pre_checkout(base_url, token, pre_checkout)
                    except Exception as e:
                        print(f"[user_bot {chat_id}] pre_checkout error: {e!r}")
                    continue

        except Exception as e:
            print(f"[user_bot {chat_id}] polling error: {e!r}")
            time.sleep(2)

    print(f"[user_bot {chat_id}] thread stopped")


def start_user_bot(chat_id, token, base_url=PROXY_API):
    if chat_id in user_bot_threads and user_bot_threads[chat_id].is_alive():
        print(f"[user_bot {chat_id}] already running")
        return

    stop_flag = threading.Event()
    user_bot_stop_flags[chat_id] = stop_flag

    t = threading.Thread(target=run_user_bot, args=(chat_id, token, base_url), daemon=True)
    user_bot_threads[chat_id] = t
    t.start()


def stop_user_bot(chat_id):
    stop_flag = user_bot_stop_flags.get(chat_id)
    if stop_flag:
        stop_flag.set()
    user_bot_threads.pop(chat_id, None)
    user_bot_stop_flags.pop(chat_id, None)


# ============================================================
# ВАЛИДАЦИЯ ТОКЕНА
# ============================================================

def validate_and_start_bot(chat_id, token):
    token = token.strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        send_message(chat_id, (
            "Неверный формат токена.\n\n"
            "Формат: 123456789:AA...\n"
            "Попробуйте снова."
        ))
        return

    base_url, result = validate_token(token)
    if base_url is None:
        send_message(chat_id, "Токен невалиден или бот не существует. Попробуйте снова.")
        return

    bot_info = result.get("result", {})
    username = bot_info.get("username", "unknown")

    tokens = load_tokens()
    old_token = tokens.get(str(chat_id), {}).get("token")
    if old_token and old_token != token:
        stop_user_bot(chat_id)

    tokens[str(chat_id)] = {"token": token, "username": username, "base_url": base_url}
    save_tokens(tokens)

    start_user_bot(chat_id, token, base_url)

    send_message(chat_id, (
        f"Ваш бот @{username} активирован!\n\n"
        f"Команды:\n"
        f"  /pay 100 — счёт на Stars\n"
        f"  @{username} 10 — inline-счёт\n\n"
        f"Для inline-режима включите его у @BotFather:\n"
        f"  BotFather -> /mybots -> ваш бот -> Bot Settings -> Inline Mode -> Turn on"
    ))


# ============================================================
# ОСНОВНОЙ БОТ: CALLBACK QUERY
# ============================================================

def handle_callback_query(callback):
    if not isinstance(callback, dict):
        return
    callback_id = callback.get("id")
    if not callback_id:
        return
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat = message.get("chat", {}) if isinstance(message, dict) else {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    if data == "create_bot":
        answer_callback(callback_id)
        WAITING_FOR_TOKEN.add(chat_id)
        send_message(chat_id, (
            "Отправьте токен вашего бота.\n"
            "Формат: 123456789:AA...\n\n"
            "Получить токен можно у @BotFather -> /newbot"
        ))
        return

    if data == "help":
        answer_callback(callback_id)
        send_message(chat_id, (
            "Помощь\n\n"
            "Перевод Stars: /send 100\n"
            "Оплата: /pay 100\n"
            "Inline: @bot 10\n"
            "Создать своего бота — кнопка в /start\n\n"
            "Inline-режим включается у @BotFather:\n"
            "  BotFather -> /mybots -> ваш бот -> Bot Settings -> Inline Mode -> Turn on"
        ))
        return


# ============================================================
# ОСНОВНОЙ БОТ: SUCCESSFUL PAYMENT
# ============================================================

def handle_successful_payment(message):
    if not isinstance(message, dict):
        return
    payment = message.get("successful_payment")
    if not isinstance(payment, dict):
        return
    chat = message.get("chat", {})
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    if not chat_id:
        return

    amount = payment.get("total_amount", 0)
    currency = payment.get("currency", "XTR")
    charge_id = payment.get("telegram_payment_charge_id", "")
    payload = payment.get("invoice_payload", "")

    print(f"PAYMENT: user={chat_id} amount={amount} payload={payload} charge={charge_id}")

    # Чек (перевод по ссылке)
    if payload.startswith("check_"):
        parts = payload.split("_", 3)
        if len(parts) >= 4:
            code = parts[0] + "_" + parts[1]
            from_id = int(parts[2]) if parts[2].isdigit() else 0
            mark_check_paid(code, chat_id)
            send_message(chat_id, f"Оплата {amount} Stars получена!")
            if from_id:
                send_message(from_id, (
                    f"Ваш перевод на {amount} Stars оплачен!\n"
                    f"Плательщик: пользователь {chat_id}"
                ))
        else:
            send_message(chat_id, f"Оплата {amount} {currency} получена!")
    elif payload == SUBSCRIPTION_PAYLOAD:
        WAITING_FOR_TOKEN.add(chat_id)
        send_message(chat_id, (
            "Оплата получена!\n\n"
            "Отправьте токен вашего бота.\n"
            "Формат: 123456789:AA...\n\n"
            "Токен можно получить у @BotFather -> /newbot"
        ))
    else:
        send_message(chat_id, f"Оплата {amount} {currency} получена!")
        # Уведомление админу
        if chat_id != ADMIN_ID:
            send_message(ADMIN_ID, (
                f"Новая оплата!\n\n"
                f"Сумма: {amount} Stars\n"
                f"Пользователь: {chat_id}"
            ))


# ============================================================
# ОСНОВНОЙ БОТ: MESSAGE
# ============================================================

def handle_message(message):
    if not isinstance(message, dict):
        return
    chat = message.get("chat", {})
    if not isinstance(chat, dict):
        return
    chat_id = chat.get("id")
    if not chat_id:
        return

    text = message.get("text")
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return

    # Ждём токен
    if chat_id in WAITING_FOR_TOKEN:
        WAITING_FOR_TOKEN.discard(chat_id)
        validate_and_start_bot(chat_id, text)
        return

    # Ждём сумму для /send
    if chat_id in WAITING_FOR_SEND_AMOUNT:
        WAITING_FOR_SEND_AMOUNT.discard(chat_id)
        match = re.fullmatch(r"(\d+)", text)
        if not match:
            send_message(chat_id, "Нужно число. Используйте: /send 100")
            return
        amount = int(match.group(1))
        if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
            send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT} Stars")
            return
        code = create_check(chat_id, amount, BOT_USERNAME)
        link = f"https://t.me/{BOT_USERNAME}?start={code}"
        send_message(chat_id, (
            f"Перевод на {amount} Stars создан!\n\n"
            f"Отправьте эту ссылку получателю:\n{link}\n\n"
            f"После перехода по ссылке он увидит счёт на оплату."
        ))
        return

    # /start (возможно с параметром chk_XXX)
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) > 1:
            start_param = parts[1].strip()
            if start_param.startswith("chk_"):
                check = get_check(start_param)
                if check is None:
                    send_message(chat_id, "Ссылка недействительна.")
                    return
                if check.get("paid"):
                    send_message(chat_id, "Эта ссылка уже использована.")
                    return
                amount = check["amount"]
                from_id = check["from"]
                payload = f"check_{start_param}_{from_id}_{amount}"
                result = telegram("sendInvoice", {
                    "chat_id": chat_id,
                    "title": f"Перевод {amount} Stars",
                    "description": f"Оплата перевода на {amount} Stars",
                    "payload": payload,
                    "currency": "XTR",
                    "prices": [{"label": f"{amount} Stars", "amount": amount}],
                })
                if not isinstance(result, dict) or result.get("ok") is not True:
                    desc = result.get("description", "Ошибка") if isinstance(result, dict) else "Нет ответа"
                    send_message(chat_id, f"Не удалось создать счёт.\n\n{desc}")
                return
            else:
                send_message(chat_id, "Неизвестная ссылка.")
                return

        keyboard = {
            "inline_keyboard": [
                [{"text": "Создать своего бота", "callback_data": "create_bot"}],
                [{"text": "Помощь", "callback_data": "help"}],
            ]
        }
        send_message(chat_id, (
            "Привет!\n\n"
            "Создать своего бота — кнопка ниже (бесплатно)\n"
            "Оплата по токену: /sd\n"
            "Перевод Stars: /send 100\n"
            "Оплата: /pay 100\n"
            "Inline: @bot 10"
        ), reply_markup=keyboard)
        return

    # Админ-команды
    if chat_id == ADMIN_ID:
        if text == "/stats":
            tokens = load_tokens()
            if not tokens:
                send_message(chat_id, "Нет активных ботов.")
                return
            lines = ["Активные боты:\n"]
            for cid, info in tokens.items():
                uname = info.get("username", "?")
                active = "+" if cid in user_bot_threads and user_bot_threads[cid].is_alive() else "-"
                lines.append(f"{active} {cid} @{uname}")
            send_message(chat_id, "\n".join(lines))
            return

        if text == "/list":
            tokens = load_tokens()
            if not tokens:
                send_message(chat_id, "Файл пустой.")
                return
            lines = ["Токены:\n"]
            for cid, info in tokens.items():
                lines.append(f"{cid} -> {info.get('username', '?')}")
            send_message(chat_id, "\n".join(lines))
            return

        if text.startswith("/stop "):
            target = text.split(" ", 1)[1].strip()
            stop_user_bot(int(target) if target.isdigit() else 0)
            send_message(chat_id, f"Бот {target} остановлен.")
            return

    # /sd — оплата по токену бота
    if text == "/sd":
        WAITING_FOR_SD_TOKEN.add(chat_id)
        send_message(chat_id, (
            "Отправьте токен бота для приёма оплаты.\n"
            "Формат: 123456789:AA...\n\n"
            "После ввода вы получите ссылку для оплаты."
        ))
        return

    # Ждём токен для /sd
    if chat_id in WAITING_FOR_SD_TOKEN:
        WAITING_FOR_SD_TOKEN.discard(chat_id)
        token = text.strip()
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
            send_message(chat_id, (
                "Неверный формат токена.\n"
                "Формат: 123456789:AA..."
            ))
            return
        # Валидируем токен
        me = user_bot_api(API_BASE, token, "getMe", timeout=10)
        if not isinstance(me, dict) or me.get("ok") is not True:
            send_message(chat_id, "Токен невалиден. Проверьте и попробуйте снова.")
            return
        bot_info = me.get("result", {})
        bot_username = bot_info.get("username", "unknown")
        # Сохраняем токен в user_bots.json (без привязки к chat_id)
        tokens = load_tokens()
        tokens[f"sd_{chat_id}"] = {"token": token, "username": bot_username, "base_url": API_BASE, "owner": chat_id}
        save_tokens(tokens)
        # Запускаем polling для этого бота
        start_user_bot(chat_id, token, API_BASE)
        send_message(chat_id, (
            f"Бот @{bot_username} подключён!\n\n"
            f"Отправьте сумму для создания ссылки оплаты.\n"
            f"Например: 100"
        ))
        # Запоминаем что ждём сумму
        WAITING_FOR_SD_AMOUNT.add(chat_id)
        return

    # Ждём сумму для /sd
    if chat_id in WAITING_FOR_SD_AMOUNT:
        WAITING_FOR_SD_AMOUNT.discard(chat_id)
        if not text.isdigit():
            send_message(chat_id, "Нужно число. Попробуйте снова: /sd")
            return
        amount = int(text)
        if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
            send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT} Stars")
            return
        # Получаем токен бота
        tokens = load_tokens()
        sd_info = tokens.get(f"sd_{chat_id}")
        if not sd_info:
            send_message(chat_id, "Ошибка. Попробуйте: /sd")
            return
        sd_token = sd_info["token"]
        sd_username = sd_info["username"]
        # Создаём ссылку
        code = create_check(chat_id, amount, sd_username)
        link = f"https://t.me/{sd_username}?start={code}"
        send_message(chat_id, (
            f"Ссылка для оплаты создана!\n\n"
            f"Бот: @{sd_username}\n"
            f"Сумма: {amount} Stars\n\n"
            f"Ссылка:\n{link}\n\n"
            f"Отправьте её получателю для оплаты."
        ))
        return

    # /pay N (основной бот)
    match = re.fullmatch(r"/pay(?:@\w+)?\s+(\d+)", text, re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            send_message(chat_id, "Сумма должна быть больше 0")
            return
        if amount > MAX_AMOUNT:
            send_message(chat_id, f"Максимальная сумма — {MAX_AMOUNT} Stars")
            return
        result = telegram("sendInvoice", {
            "chat_id": chat_id,
            "title": "Оплата Stars",
            "description": f"Оплата на сумму {amount} Stars",
            "payload": f"stars_{chat_id}_{amount}_{int(time.time())}",
            "currency": "XTR",
            "prices": [{"label": f"{amount} Stars", "amount": amount}],
        })
        if not isinstance(result, dict) or result.get("ok") is not True:
            desc = result.get("description", "Ошибка") if isinstance(result, dict) else "Нет ответа"
            send_message(chat_id, f"Не удалось создать счёт.\n\n{desc}")
        return

    # /send N — перевод Stars по ссылке
    match = re.fullmatch(r"/send(?:@\w+)?\s*(\d*)", text, re.IGNORECASE)
    if match:
        amount_str = match.group(1)
        if amount_str:
            amount = int(amount_str)
            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT} Stars")
                return
            code = create_check(chat_id, amount, BOT_USERNAME)
            link = f"https://t.me/{BOT_USERNAME}?start={code}"
            send_message(chat_id, (
                f"Перевод на {amount} Stars создан!\n\n"
                f"Отправьте эту ссылку получателю:\n{link}\n\n"
                f"После перехода по ссылке он увидит счёт на оплату."
            ))
        else:
            WAITING_FOR_SEND_AMOUNT.add(chat_id)
            send_message(chat_id, "Введите сумму перевода в Stars (число):")
        return


# ============================================================
# ОСНОВНОЙ БОТ: PRE-CHECKOUT
# ============================================================

def answer_pre_checkout(query):
    if not isinstance(query, dict):
        return None
    query_id = query.get("id")
    if not query_id:
        return None
    currency = query.get("currency", "XTR")
    amount = query.get("total_amount", 0)
    payload = query.get("invoice_payload", "")

    print(f"PRE-CHECKOUT: amount={amount} currency={currency} payload={payload}")

    if currency != "XTR":
        return telegram("answerPreCheckoutQuery", {
            "pre_checkout_query_id": query_id,
            "ok": False,
            "error_message": "Поддерживается только Telegram Stars.",
        })
    if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
        return telegram("answerPreCheckoutQuery", {
            "pre_checkout_query_id": query_id,
            "ok": False,
            "error_message": "Некорректная сумма.",
        })
    return telegram("answerPreCheckoutQuery", {
        "pre_checkout_query_id": query_id,
        "ok": True,
    })


# ============================================================
# ОСНОВНОЙ БОТ: INLINE QUERY (@fragmentbot)
# ============================================================

def main_bot_handle_inline(inline_query):
    if not isinstance(inline_query, dict):
        return
    query_id = inline_query.get("id")
    if not query_id:
        return
    query = inline_query.get("query", "")
    if not isinstance(query, str):
        query = ""
    query = query.strip()

    if not query:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "help",
                "title": "Создать счёт на Stars",
                "description": "Введите сумму, например 10",
                "input_message_content": {
                    "message_text": "Напишите сумму после имени бота.\n\nПример:\n@fragmentbot 10"
                },
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    match = re.fullmatch(r"(\d+)(?:\s*(?:stars?|xtr))?", query, re.IGNORECASE)
    if not match:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "invalid",
                "title": "Неверная сумма",
                "description": "Например: @fragmentbot 10",
                "input_message_content": {
                    "message_text": "Неверная сумма.\n\nПример:\n@fragmentbot 10"
                },
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    amount = int(match.group(1))
    if amount < MIN_AMOUNT:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "err_min",
                "title": "Сумма слишком маленькая",
                "description": f"Минимум — {MIN_AMOUNT} Stars",
                "input_message_content": {"message_text": f"Минимум — {MIN_AMOUNT} Stars"},
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return
    if amount > MAX_AMOUNT:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "err_max",
                "title": "Сумма слишком большая",
                "description": f"Максимум — {MAX_AMOUNT} Stars",
                "input_message_content": {"message_text": f"Максимум — {MAX_AMOUNT} Stars"},
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    user = inline_query.get("from") or {}
    user_id = user.get("id", 0)
    payload = f"inline_{user_id}_{amount}"

    result = {
        "type": "article",
        "id": uuid.uuid4().hex,
        "title": f"Счёт на {amount} Stars",
        "description": f"Создать счёт на {amount} Stars",
        "input_message_content": {
            "message_text": f"Счёт на оплату\n\nСумма: {amount} Stars",
            "parse_mode": "HTML",
        },
        "reply_markup": {
            "inline_keyboard": [[
                {"text": f"Оплатить {amount} Stars", "pay": True},
            ]],
        },
    }

    print(f"[inline] user={user_id} amount={amount}")
    telegram("answerInlineQuery", {
        "inline_query_id": query_id,
        "results": [result],
        "cache_time": 1,
        "is_personal": True,
    })


# ============================================================
# ОСНОВНОЙ БОТ: HANDLE UPDATE
# ============================================================

def handle_update(update):
    if not isinstance(update, dict):
        return

    # callback_query
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        try:
            handle_callback_query(callback)
        except Exception as e:
            print(f"Callback error: {e!r}")
        return

    # inline_query
    inline_query = update.get("inline_query")
    if isinstance(inline_query, dict):
        try:
            main_bot_handle_inline(inline_query)
        except Exception as e:
            print(f"Inline error: {e!r}")
        return

    # message
    message = update.get("message")
    if isinstance(message, dict):
        if isinstance(message.get("successful_payment"), dict):
            handle_successful_payment(message)
        else:
            handle_message(message)
        return

    # pre_checkout_query
    pre_checkout = update.get("pre_checkout_query")
    if isinstance(pre_checkout, dict):
        answer_pre_checkout(pre_checkout)
        return


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 40)
    print("TELEGRAM STARS BOT")
    print("CREATE USER BOTS + INLINE PAYMENTS")
    print("=" * 40)

    # Загружаем сохранённые токены и запускаем боты
    tokens = load_tokens()
    if tokens:
        print(f"Found {len(tokens)} saved bots, starting...")
        for chat_id_str, info in tokens.items():
            chat_id = int(chat_id_str)
            token = info.get("token")
            if token:
                base_url = info.get("base_url", PROXY_API)
                start_user_bot(chat_id, token, base_url)
        print()

    # Проверка основного бота
    result = telegram("getMe", timeout=10)
    if not isinstance(result, dict) or result.get("ok") is not True:
        print("ERROR: getMe failed:")
        print(repr(result))
        return

    bot = result.get("result", {})
    username = bot.get("username", "unknown")
    global BOT_USERNAME
    BOT_USERNAME = username
    print(f"Main bot: @{username}")
    print(f"Admin: {ADMIN_ID}")
    print()

    offset = 0

    while True:
        try:
            result = telegram("getUpdates", {
                "offset": offset,
                "timeout": 5,
                "allowed_updates": [
                    "message",
                    "inline_query",
                    "pre_checkout_query",
                    "callback_query",
                ],
            }, timeout=15)

            if result is None:
                time.sleep(2)
                continue
            if not isinstance(result, dict) or result.get("ok") is not True:
                print("getUpdates error:", result)
                time.sleep(2)
                continue

            updates = result.get("result")
            if not isinstance(updates, list):
                time.sleep(1)
                continue

            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"Update error: {e!r}")

        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Main loop error: {e!r}")
            time.sleep(2)


if __name__ == "__main__":
    main()
