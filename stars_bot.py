#!/usr/bin/env python3
"""Telegram Stars Bot — система кошельков для приёма оплаты Stars.

Поток:
  1. /start → бот просит токен
  2. Пользователь вводит токен → бот генерирует 8-значный адрес (кошелёк)
  3. /send АДРЕС СУММА → ссылка для оплаты через бот кошелька
  4. Оплата → чек плательщику + уведомление владельцу кошелька
"""

import json
import os
import re
import secrets
import string
import threading
import time
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

WALLETS_FILE = Path(__file__).parent / "wallets.json"
CHECKS_FILE = Path(__file__).parent / "checks.json"
GIFTS_FILE = Path(__file__).parent / "gifts.json"

session = requests.Session()

# Состояния
WAITING_FOR_TOKEN: set[int] = set()
WAITING_SEND_AMOUNT: dict[int, dict] = {}
SEND_ERROR_COUNT: dict[int, int] = {}
WAITING_RENAME: dict[int, str] = {}
user_bot_threads: dict[int, threading.Thread] = {}
user_bot_stop_flags: dict[int, threading.Event] = {}

BOT_USERNAME = "bot"


# ============================================================
# WALLET STORAGE
# ============================================================

def load_wallets() -> dict:
    if WALLETS_FILE.exists():
        try:
            return json.loads(WALLETS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_wallets(wallets: dict) -> None:
    try:
        WALLETS_FILE.write_text(
            json.dumps(wallets, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_wallets error: {e!r}")


def generate_address() -> str:
    """Генерирует уникальный 8-значный адрес (буквы + цифры)."""
    chars = string.ascii_lowercase + string.digits
    while True:
        addr = "".join(secrets.choice(chars) for _ in range(8))
        wallets = load_wallets()
        if addr not in wallets:
            return addr


def find_wallet_by_token(token: str) -> tuple[str, dict] | None:
    """Находит кошелёк по токену. Возвращает (address, wallet_info)."""
    wallets = load_wallets()
    for addr, info in wallets.items():
        if info.get("token") == token:
            return addr, info
    return None


# ============================================================
# CHECKS (ссылки для оплаты)
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


def create_check(from_id: int, amount: int, wallet_address: str) -> str:
    code = f"pay_{secrets.token_hex(6)}"
    checks = load_checks()
    checks[code] = {
        "from": from_id,
        "amount": amount,
        "wallet": wallet_address,
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
# GIFTS (промокоды-подарки)
# ============================================================

def load_gifts() -> dict:
    if GIFTS_FILE.exists():
        try:
            return json.loads(GIFTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_gifts(gifts: dict) -> None:
    try:
        GIFTS_FILE.write_text(
            json.dumps(gifts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_gifts error: {e!r}")


def create_gift(activations: int, item: str = "", amount: int = 0, rename: bool = False) -> str:
    code = f"gift_{secrets.token_hex(4)}"
    gifts = load_gifts()
    gifts[code] = {
        "max": activations,
        "used": [],
        "item": item,
        "amount": amount,
        "rename": rename,
        "created": int(time.time()),
    }
    save_gifts(gifts)
    return code


def use_gift(code: str, user_id: int) -> dict | None:
    """Активирует подарок. Возвращает info о подарке или None."""
    gifts = load_gifts()
    gift = gifts.get(code)
    if not gift:
        return None
    if user_id in gift.get("used", []):
        return None
    if len(gift.get("used", [])) >= gift.get("max", 0):
        return None
    gift["used"].append(user_id)
    save_gifts(gifts)
    return gift


# ============================================================
# TELEGRAM API (основной бот)
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
# USER BOT API (кошельки через dev-angel-7553.dev)
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


def validate_token(token):
    """Проверяет токен через прокси."""
    result = user_bot_api(API_BASE, token, "getMe", timeout=10)
    if isinstance(result, dict) and result.get("ok") is True:
        return API_BASE, result
    return None, None


# ============================================================
# ПОЛЬЗОВАТЕЛЬСКИЙ БОТ: INVOICE
# ============================================================

def user_bot_send_invoice(base_url, token, chat_id, amount):
    payload = f"wallet_{chat_id}_{amount}"
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
        "id": secrets.token_hex(8),
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
# ПОЛЬЗОВАТЕЛЬСКИЙ БОТ: ПОИСК ВЛАДЕЛЬЦА
# ============================================================

def find_wallet_owner(token):
    """Находит адрес кошелька по токену."""
    wallets = load_wallets()
    for addr, info in wallets.items():
        if info.get("token") == token:
            return addr, info.get("owner", 0)
    return None, 0


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

        # Оплата кошелька (перевод по ссылке)
        if payload.startswith("pay_"):
            code = payload
            check = get_check(code)
            if check and not check.get("paid"):
                mark_check_paid(code, chat_id)
                wallet_addr = check.get("wallet", "")
                from_id = check.get("from", 0)
                # Чек плательщику
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": (
                        f"Чек об оплате\n\n"
                        f"Сумма: {amount} Stars\n"
                        f"Кошелёк: {wallet_addr}\n"
                        f"Статус: оплачено"
                    ),
                })
                # Уведомление владельцу кошелька
                wallets = load_wallets()
                wallet_info = wallets.get(wallet_addr, {})
                owner_id = wallet_info.get("owner", 0)
                if owner_id:
                    user_bot_api(base_url, token, "sendMessage", {
                        "chat_id": owner_id,
                        "text": (
                            f"Поступила оплата!\n\n"
                            f"Сумма: {amount} Stars\n"
                            f"Плательщик: {chat_id}"
                        ),
                    })
            else:
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": chat_id,
                    "text": f"Оплата {amount} Stars получена!",
                })
        else:
            # Обычная оплата /pay
            user_bot_api(base_url, token, "sendMessage", {
                "chat_id": chat_id,
                "text": (
                    f"Оплата получена!\n\n"
                    f"Сумма: {amount} Stars"
                ),
            })
            # Уведомление владельцу кошелька
            owner_addr, owner_id = find_wallet_owner(token)
            if owner_id and owner_id != chat_id:
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": owner_id,
                    "text": (
                        f"Поступила оплата!\n\n"
                        f"Сумма: {amount} Stars\n"
                        f"Плательщик: {chat_id}"
                    ),
                })
        return

    text = message.get("text")
    if not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return

    # /start (только с параметром pay_XXX)
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) > 1:
            start_param = parts[1].strip()
            if start_param.startswith("pay_"):
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
                result = user_bot_api(base_url, token, "sendInvoice", {
                    "chat_id": chat_id,
                    "title": f"Оплата {amount} Stars",
                    "description": f"Оплата перевода на {amount} Stars",
                    "payload": start_param,
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
        desc = result.get("description", "Ошибка") if isinstance(result, dict) else "Нет ответа"
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


def start_user_bot(chat_id, token, base_url=API_BASE):
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


def address_hash(address: str) -> int:
    """Превращает 8-значный адрес в числовой ID для user_bot_threads."""
    h = 0
    for c in address:
        h = (h * 31 + ord(c)) & 0x7FFFFFFF
    return h


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
        is_admin = chat_id == ADMIN_ID
        msg = (
            "Помощь\n\n"
            "Кошелёк:\n"
            "  /start — создать кошелёк\n"
            "  /my — мой кошелёк\n\n"
            "Оплата:\n"
            "  /send АДРЕС СУММА — ссылка на оплату\n"
            "  /pay 100 — оплата через бота\n"
            "  @bot 10 — inline-счёт"
        )
        if is_admin:
            msg += (
                "\n\nАдмин:\n"
                "  /stats — статистика\n"
                "  /wallets — список кошельков\n"
                "  /us АДРЕС ИМЯ — задать имя\n"
                "  /del АДРЕС — удалить\n"
                "  /find @username — найти\n"
                "  /find 123456789 — найти по ID\n"
                "  /user 123456789 — профиль юзера\n"
                "  /gift 10 — подарок (rename) на 10 юзеров\n"
                "  /gift bear 10 — 10 медведей\n"
                "  /gift bear us 10 — 10 медведей + rename\n"
                "  /gifts — список подарков"
            )
        send_message(chat_id, msg)
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

    if payload == "main_bot_payment":
        send_message(chat_id, f"Оплата {amount} {currency} получена!")
    else:
        send_message(chat_id, f"Оплата {amount} {currency} получена!")


# ============================================================
# ОСНОВНОЙ БОТ: MESSAGE
# ============================================================

def _create_send_link(chat_id, address, amount):
    """Создаёт ссылку для оплаты и запускает бота кошелька на 5 мин."""
    wallets = load_wallets()
    wallet = wallets.get(address)
    if not wallet:
        send_message(chat_id, f"Кошелёк {address} не найден.")
        return
    code = create_check(chat_id, amount, address)
    bot_username = wallet.get("username", "bot")
    token = wallet.get("token", "")
    base_url = wallet.get("base_url", API_BASE)
    link = f"https://t.me/{bot_username}?start={code}"
    send_message(chat_id, (
        f"Ссылка для оплаты создана!\n\n"
        f"Кошелёк: {address}\n"
        f"Бот: @{bot_username}\n"
        f"Сумма: {amount} Stars\n\n"
        f"Ссылка:\n{link}\n\n"
        f"Отправьте её получателю для оплаты.\n"
        f"Бот активен 5 минут для приёма оплаты."
    ))
    if token:
        start_user_bot(address_hash(address), token, base_url)
        def _stop_after_timeout():
            stop_user_bot(address_hash(address))
            print(f"[wallet {address}] polling stopped after 5 min")
        timer = threading.Timer(300, _stop_after_timeout)
        timer.daemon = True
        timer.start()


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

    # Ждём новое имя кошелька (после подарка)
    if chat_id in WAITING_RENAME:
        if text == "/cancel":
            WAITING_RENAME.pop(chat_id, None)
            send_message(chat_id, "Ок. Имя не изменено.")
            return
        if text.startswith("/"):
            WAITING_RENAME.pop(chat_id, None)
            # Обрабатываем команду ниже
        else:
            address = WAITING_RENAME.pop(chat_id)
            if address == "new":
                WAITING_RENAME.pop(chat_id, None)
                return
            wallets = load_wallets()
            if address in wallets:
                wallets[address]["name"] = text.strip()
                save_wallets(wallets)
                send_message(chat_id, (
                    f"Имя кошелька изменено!\n\n"
                    f"Адрес: {address}\n"
                    f"Имя: {text.strip()}\n\n"
                    f"/send {text.strip()} СУММА"
                ))
            return

    # Ждём токен (пропускаем команды)
    if chat_id in WAITING_FOR_TOKEN:
        if text.startswith("/"):
            WAITING_FOR_TOKEN.discard(chat_id)
            # Если команда не /start — обрабатываем как обычно
            if not text.startswith("/start"):
                pass  # продолжаем к обработке команды ниже
            else:
                return
        else:
            WAITING_FOR_TOKEN.discard(chat_id)
            token = text.strip()
            if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
                send_message(chat_id, (
                    "Неверный формат токена.\n"
                    "Формат: 123456789:AA...\n\n"
                    "Попробуйте снова: /start"
                ))
                return
            # Валидируем токен
            base_url, result = validate_token(token)
            if base_url is None:
                send_message(chat_id, "Токен невалиден. Попробуйте снова: /start")
                return
            bot_info = result.get("result", {})
            bot_username = bot_info.get("username", "unknown")

            # Проверяем, не занят ли токен другим кошельком
            existing = find_wallet_by_token(token)
            if existing:
                addr, info = existing
                send_message(chat_id, (
                    f"Этот токен уже привязан к кошельку: {addr}\n"
                    f"Бот: @{info.get('username', '?')}"
                ))
                return

            # Генерируем уникальный адрес
            address = generate_address()

            # Сохраняем кошелёк
            wallets = load_wallets()
            wallets[address] = {
                "token": token,
                "username": bot_username,
                "base_url": base_url,
                "owner": chat_id,
                "name": "",
                "created": int(time.time()),
            }
            save_wallets(wallets)

            # Если активирован подарок — просим имя
            if chat_id in WAITING_RENAME:
                WAITING_RENAME[chat_id] = address
                send_message(chat_id, (
                    f"Кошелёк создан!\n\n"
                    f"Адрес: {address}\n"
                    f"Бот: @{bot_username}\n\n"
                    f"Подарок: введите имя для кошелька\n"
                    f"(или /cancel для пропуска):"
                ))
            else:
                send_message(chat_id, (
                    f"Кошелёк создан!\n\n"
                    f"Адрес: {address}\n"
                    f"Бот: @{bot_username}\n\n"
                    f"Отправка средств:\n"
                    f"/send {address} СУММА\n\n"
                    f"Например:\n"
                    f"/send {address} 100"
                ))
            return

    # /start (возможно с параметром gift_XXX или rename_XXX)
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) > 1:
            start_param = parts[1].strip()

            # Подарок — активация промокода
            if start_param.startswith("gift_"):
                gift_info = use_gift(start_param, chat_id)
                if not gift_info:
                    send_message(chat_id, (
                        "Ссылка недействительна или уже использована."
                    ))
                    return
                item = gift_info.get("item", "")
                amount = gift_info.get("amount", 0)
                rename = gift_info.get("rename", False)

                # Формируем сообщение о подарке
                gift_lines = ["Подарок активирован!\n"]
                if item and amount:
                    gift_lines.append(f"Предмет: {item} x{amount}")
                if rename:
                    gift_lines.append("Бонус: смена имени кошелька")

                wallets = load_wallets()
                user_addr = None
                for addr, info in wallets.items():
                    if info.get("owner") == chat_id:
                        user_addr = addr
                        break

                if rename:
                    if user_addr:
                        WAITING_RENAME[chat_id] = user_addr
                        gift_lines.append("\nВведите новое имя (или /cancel):")
                    else:
                        WAITING_FOR_TOKEN.add(chat_id)
                        WAITING_RENAME[chat_id] = "new"
                        gift_lines.append("\nОтправьте токен бота для создания кошелька.\nФормат: 123456789:AA...")
                else:
                    if not user_addr:
                        gift_lines.append("\nСоздайте кошелёк: /start")

                send_message(chat_id, "\n".join(gift_lines))
                return

        # Обычный /start без параметра
        wallets = load_wallets()
        for addr, info in wallets.items():
            if info.get("owner") == chat_id:
                name = info.get("name", "")
                name_str = f" ({name})" if name else ""
                send_message(chat_id, (
                    f"Ваш кошелёк: {addr}{name_str}\n"
                    f"Бот: @{info.get('username', '?')}\n\n"
                    f"Отправка средств:\n"
                    f"/send {addr} СУММА"
                ))
                return

        keyboard = {
            "inline_keyboard": [
                [{"text": "Создать кошелёк", "callback_data": "create_bot"}],
                [{"text": "Помощь", "callback_data": "help"}],
            ]
        }
        send_message(chat_id, (
            "Привет!\n\n"
            "Создайте кошелёк для приёма оплаты Stars.\n"
            "Нажмите кнопку ниже и введите токен бота."
        ), reply_markup=keyboard)
        return

    # /my — показать кошелёк
    if text == "/my":
        wallets = load_wallets()
        for addr, info in wallets.items():
            if info.get("owner") == chat_id:
                name = info.get("name", "")
                name_str = f" ({name})" if name else ""
                send_message(chat_id, (
                    f"Ваш кошелёк: {addr}{name_str}\n"
                    f"Бот: @{info.get('username', '?')}\n\n"
                    f"Отправка средств:\n"
                    f"/send {addr} СУММА"
                ))
                return
        send_message(chat_id, "У вас нет кошелька. Создайте: /start")
        return

    # Ждём сумму для /send (после /send АДРЕС без суммы)
    if chat_id in WAITING_SEND_AMOUNT:
        if text.startswith("/"):
            WAITING_SEND_AMOUNT.pop(chat_id, None)
            SEND_ERROR_COUNT.pop(chat_id, None)
            # Обрабатываем команду ниже
        else:
            if not text.isdigit():
                SEND_ERROR_COUNT[chat_id] = SEND_ERROR_COUNT.get(chat_id, 0) + 1
                if SEND_ERROR_COUNT[chat_id] >= 2:
                    WAITING_SEND_AMOUNT.pop(chat_id, None)
                    SEND_ERROR_COUNT.pop(chat_id, None)
                    send_message(chat_id, "Слишком много ошибок. Начните заново: /send АДРЕС СУММА")
                else:
                    send_message(chat_id, "Введите число — сумму в Stars:")
                return
            amount = int(text)
            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                SEND_ERROR_COUNT[chat_id] = SEND_ERROR_COUNT.get(chat_id, 0) + 1
                if SEND_ERROR_COUNT[chat_id] >= 2:
                    WAITING_SEND_AMOUNT.pop(chat_id, None)
                    SEND_ERROR_COUNT.pop(chat_id, None)
                    send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT}. Начните заново: /send АДРЕС СУММА")
                else:
                    send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT} Stars. Попробуйте снова:")
                return
            # Успех — создаём ссылку
            info = WAITING_SEND_AMOUNT.pop(chat_id)
            SEND_ERROR_COUNT.pop(chat_id, None)
            address = info["address"]
            _create_send_link(chat_id, address, amount)
            return

    # /send АДРЕС_или_ИМЯ [СУММА]
    match = re.fullmatch(r"/send(?:@\w+)?\s+(\S+)(?:\s+(\d+))?", text, re.IGNORECASE)
    if match:
        target = match.group(1).lower()
        amount_str = match.group(2)
        wallets = load_wallets()
        wallet = wallets.get(target)
        address = target
        if not wallet:
            for addr, info in wallets.items():
                if info.get("name", "").lower() == target:
                    wallet = info
                    address = addr
                    break
        if not wallet:
            send_message(chat_id, f"Кошелёк {target} не найден.")
            return
        if amount_str:
            amount = int(amount_str)
            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                send_message(chat_id, f"Сумма от {MIN_AMOUNT} до {MAX_AMOUNT} Stars")
                return
            _create_send_link(chat_id, address, amount)
        else:
            WAITING_SEND_AMOUNT[chat_id] = {"address": address}
            SEND_ERROR_COUNT[chat_id] = 0
            send_message(chat_id, "Введите сумму в Stars (число):")
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
            "payload": "main_bot_payment",
            "currency": "XTR",
            "prices": [{"label": f"{amount} Stars", "amount": amount}],
        })
        if not isinstance(result, dict) or result.get("ok") is not True:
            desc = result.get("description", "Ошибка") if isinstance(result, dict) else "Нет ответа"
            send_message(chat_id, f"Не удалось создать счёт.\n\n{desc}")
        return

    # Админ-команды
    if chat_id == ADMIN_ID:
        # /stats — полная статистика
        if text == "/stats":
            wallets = load_wallets()
            checks = load_checks()
            paid_checks = [c for c in checks.values() if c.get("paid")]
            total_paid = sum(c.get("amount", 0) for c in paid_checks)
            active_bots = sum(1 for t in user_bot_threads.values() if t.is_alive())
            lines = [
                "Статистика\n",
                f"Кошельков: {len(wallets)}",
                f"Ссылок создано: {len(checks)}",
                f"Оплачено: {len(paid_checks)}",
                f"Сумма оплат: {total_paid} Stars",
                f"Активных ботов: {active_bots}",
            ]
            send_message(chat_id, "\n".join(lines))
            return

        # /wallets — список кошельков
        if text == "/wallets":
            wallets = load_wallets()
            if not wallets:
                send_message(chat_id, "Нет кошельков.")
                return
            lines = ["Кошельки:\n"]
            for addr, info in wallets.items():
                uname = info.get("username", "?")
                owner = info.get("owner", "?")
                name = info.get("name", "")
                name_str = f" [{name}]" if name else ""
                lines.append(f"{addr}{name_str} @{uname} (owner: {owner})")
            send_message(chat_id, "\n".join(lines))
            return

        # /us АДРЕС новое_имя — задать кастомное имя кошелька
        match_us = re.fullmatch(r"/us\s+(\S+)\s+(.+)", text, re.IGNORECASE)
        if match_us:
            target = match_us.group(1).lower()
            new_name = match_us.group(2).strip()
            wallets = load_wallets()
            # Ищем по адресу или по имени
            address = target if target in wallets else None
            if not address:
                for addr, info in wallets.items():
                    if info.get("name", "").lower() == target:
                        address = addr
                        break
            if not address:
                send_message(chat_id, f"Кошелёк {target} не найден.")
                return
            wallets[address]["name"] = new_name
            save_wallets(wallets)
            send_message(chat_id, f"Кошелёк {address} переименован в «{new_name}»")
            # Уведомление владельцу
            owner_id = wallets[address].get("owner", 0)
            bot_username = wallets[address].get("username", "")
            if owner_id and owner_id != chat_id:
                send_message(owner_id, (
                    f"Админ выдал имя вашему кошельку\n\n"
                    f"Адрес: {address}\n"
                    f"Имя: {new_name}\n"
                    f"Бот: @{bot_username}\n\n"
                    f"Теперь вы можете принимать оплату:\n"
                    f"/send {new_name} СУММА"
                ))
            return

        # /del АДРЕС_или_ИМЯ — удалить кошелёк
        match_del = re.fullmatch(r"/del\s+(\S+)", text, re.IGNORECASE)
        if match_del:
            target = match_del.group(1).lower()
            wallets = load_wallets()
            address = target if target in wallets else None
            if not address:
                for addr, info in wallets.items():
                    if info.get("name", "").lower() == target:
                        address = addr
                        break
            if not address:
                send_message(chat_id, f"Кошелёк {target} не найден.")
                return
            del wallets[address]
            save_wallets(wallets)
            send_message(chat_id, f"Кошелёк {address} удалён.")
            return

        # /find @username или /find ID — найти кошелёк
        match_find = re.fullmatch(r"/find\s+(@\w+|\d+)", text, re.IGNORECASE)
        if match_find:
            target = match_find.group(1)
            wallets = load_wallets()
            if target.startswith("@"):
                target = target[1:].lower()
                for addr, info in wallets.items():
                    if info.get("username", "").lower() == target:
                        name = info.get("name", "")
                        name_str = f" [{name}]" if name else ""
                        send_message(chat_id, f"@{target} -> {addr}{name_str} (owner: {info.get('owner', '?')})")
                        return
                send_message(chat_id, f"Кошелёк для @{target} не найден.")
            else:
                target_id = int(target)
                found = False
                for addr, info in wallets.items():
                    if info.get("owner") == target_id:
                        name = info.get("name", "")
                        name_str = f" [{name}]" if name else ""
                        send_message(chat_id, f"ID {target_id} -> {addr}{name_str} @{info.get('username', '?')}")
                        found = True
                if not found:
                    send_message(chat_id, f"Кошелёк для ID {target_id} не найден.")
            return

        # /user ID — профиль пользователя
        match_user = re.fullmatch(r"/user\s+(\d+)", text, re.IGNORECASE)
        if match_user:
            target_id = int(match_user.group(1))
            wallets = load_wallets()
            checks = load_checks()

            # Кошельки пользователя
            user_wallets = []
            for addr, info in wallets.items():
                if info.get("owner") == target_id:
                    user_wallets.append((addr, info))

            # Оплаты, где пользователь — плательщик
            as_payer = [c for c in checks.values() if c.get("payer") == target_id]
            # Оплаты, где пользователь — получатель (владелец кошелька)
            user_addrs = {a for a, _ in user_wallets}
            as_receiver = [c for c in checks.values() if c.get("wallet") in user_addrs and c.get("paid")]

            paid_as_payer = sum(c.get("amount", 0) for c in as_payer if c.get("paid"))
            paid_as_receiver = sum(c.get("amount", 0) for c in as_receiver)

            lines = [f"Профиль пользователя {target_id}\n"]

            if user_wallets:
                lines.append("Кошельки:")
                for addr, info in user_wallets:
                    name = info.get("name", "")
                    name_str = f" [{name}]" if name else ""
                    lines.append(f"  {addr}{name_str} @{info.get('username', '?')}")
            else:
                lines.append("Кошельков: 0")

            lines.append(f"\nОтправлено оплат: {len(as_payer)} ({paid_as_payer} Stars)")
            lines.append(f"Получено оплат: {len(as_receiver)} ({paid_as_receiver} Stars)")

            if user_wallets:
                first_created = min(info.get("created", 0) for _, info in user_wallets)
                if first_created:
                    from datetime import datetime
                    lines.append(f"Регистрация: {datetime.fromtimestamp(first_created).strftime('%Y-%m-%d %H:%M')}")

            send_message(chat_id, "\n".join(lines))
            return

        # /gift [ТИП] [us] N — создать ссылку-подарок
        # Форматы:
        #   /gift 10              — 10 активаций (только rename)
        #   /gift bear 10         — 10 "bear" (предмет)
        #   /gift bear us 10      — 10 "bear" + rename
        #   /gift us 10           — 10 rename
        match_gift_rename = re.fullmatch(r"/gift\s+(\S+)\s+us\s+(\d+)", text, re.IGNORECASE)
        match_gift_item = re.fullmatch(r"/gift\s+(\S+)\s+(\d+)", text, re.IGNORECASE)
        match_gift_plain = re.fullmatch(r"/gift\s+(\d+)", text, re.IGNORECASE)

        gift_item = ""
        gift_amount = 0
        gift_rename = False
        gift_activations = 0

        if match_gift_rename:
            gift_item = match_gift_rename.group(1).lower()
            gift_amount = int(match_gift_rename.group(1))  # не используется
            gift_activations = int(match_gift_rename.group(2))
            gift_rename = True
            gift_amount = 1  # 1 предмет
        elif match_gift_item and not match_gift_item.group(1).isdigit():
            gift_item = match_gift_item.group(1).lower()
            gift_activations = int(match_gift_item.group(2))
            gift_amount = 1
        elif match_gift_plain:
            gift_activations = int(match_gift_plain.group(1))
            gift_rename = True

        if match_gift_rename or (match_gift_item and not match_gift_item.group(1).isdigit()) or match_gift_plain:
            if gift_activations < 1 or gift_activations > 1000:
                send_message(chat_id, "От 1 до 1000 активаций.")
                return
            code = create_gift(gift_activations, gift_item, gift_amount, gift_rename)
            link = f"https://t.me/{BOT_USERNAME}?start={code}"
            gifts = load_gifts()
            used_count = len(gifts[code].get("used", []))
            desc_parts = []
            if gift_item:
                desc_parts.append(f"Предмет: {gift_item}")
            if gift_rename:
                desc_parts.append("Бонус: rename")
            if not desc_parts:
                desc_parts.append("Rename")
            desc = "\n".join(desc_parts)
            send_message(chat_id, (
                f"Ссылка-подарок создана!\n\n"
                f"{desc}\n"
                f"Активаций: {gift_activations}\n"
                f"Использовано: {used_count}\n\n"
                f"Ссылка:\n{link}"
            ))
            return

        # /gifts — список подарков
        if text == "/gifts":
            gifts = load_gifts()
            if not gifts:
                send_message(chat_id, "Нет подарков.")
                return
            lines = ["Подарки:\n"]
            for code, info in gifts.items():
                used = len(info.get("used", []))
                mx = info.get("max", 0)
                item = info.get("item", "")
                rename = info.get("rename", False)
                tags = []
                if item:
                    tags.append(item)
                if rename:
                    tags.append("rename")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"{code}{tag_str} — {used}/{mx}")
            send_message(chat_id, "\n".join(lines))
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

    print(f"PRE-CHECKOUT: amount={amount} currency={currency}")

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
# ОСНОВНОЙ БОТ: HANDLE UPDATE
# ============================================================

def handle_update(update):
    if not isinstance(update, dict):
        return

    callback = update.get("callback_query")
    if isinstance(callback, dict):
        try:
            handle_callback_query(callback)
        except Exception as e:
            print(f"Callback error: {e!r}")
        return

    inline_query = update.get("inline_query")
    if isinstance(inline_query, dict):
        return

    message = update.get("message")
    if isinstance(message, dict):
        if isinstance(message.get("successful_payment"), dict):
            handle_successful_payment(message)
        else:
            handle_message(message)
        return

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
    print("WALLET SYSTEM")
    print("=" * 40)

    # Загружаем сохранённые кошельки
    wallets = load_wallets()
    if wallets:
        print(f"Found {len(wallets)} saved wallets")
        print("Bots will start on /send (5 min timeout)")
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
