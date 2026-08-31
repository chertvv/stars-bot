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
from datetime import datetime, timezone
import threading
import time
from pathlib import Path

import requests

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get(
    "BOT_TOKEN",
    
API_BASE = os.environ.get("API_BASE", "https://dev-angel-7553.dev")
API = f"{API_BASE}/bot{BOT_TOKEN}"

ADMIN_ID = int(os.environ.get("ADMIN_ID", "2022001"))

MIN_AMOUNT = 1
MAX_AMOUNT = 10000

WALLET_PRICE = 50
MAX_WALLETS = 2

PENDING_BUYS_FILE = Path(__file__).parent / "pending_buys.json"
LANG_FILE = Path(__file__).parent / "lang.json"

WALLETS_FILE = Path(__file__).parent / "wallets.json"
CHECKS_FILE = Path(__file__).parent / "checks.json"
GIFTS_FILE = Path(__file__).parent / "gifts.json"
PROMOS_FILE = Path(__file__).parent / "promos.json"
BANS_FILE = Path(__file__).parent / "bans.json"

session = requests.Session()

# Состояния
WAITING_FOR_TOKEN: set[int] = set()
WAITING_SEND_AMOUNT: dict[int, dict] = {}
SEND_ERROR_COUNT: dict[int, int] = {}
WAITING_RENAME: dict[int, str] = {}
WAITING_RECREATE: dict[int, str] = {}
WAITING_WALLET_BUY: set[int] = set()
user_bot_threads: dict[int, threading.Thread] = {}


def load_pending_buys() -> dict:
    if PENDING_BUYS_FILE.exists():
        try:
            return json.loads(PENDING_BUYS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_pending_buys(data: dict) -> None:
    try:
        PENDING_BUYS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_pending_buys error: {e!r}")


def add_pending_buy(user_id: int) -> None:
    data = load_pending_buys()
    data[str(user_id)] = int(time.time())
    save_pending_buys(data)
    WAITING_WALLET_BUY.add(user_id)


def has_pending_buy(user_id: int) -> bool:
    if user_id in WAITING_WALLET_BUY:
        return True
    data = load_pending_buys()
    return str(user_id) in data


def remove_pending_buy(user_id: int) -> None:
    WAITING_WALLET_BUY.discard(user_id)
    data = load_pending_buys()
    data.pop(str(user_id), None)
    save_pending_buys(data)

user_bot_stop_flags: dict[int, threading.Event] = {}

BOT_USERNAME = "bot"


# ============================================================
# I18N / LANGUAGES
# ============================================================

def load_langs() -> dict:
    if LANG_FILE.exists():
        try:
            return json.loads(LANG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_langs(data: dict) -> None:
    try:
        LANG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        print(f"save_langs error: {e!r}")


def get_lang(user_id: int) -> str:
    langs = load_langs()
    return langs.get(str(user_id), "ru")


def set_lang(user_id: int, lang: str) -> None:
    langs = load_langs()
    langs[str(user_id)] = lang
    save_langs(langs)


def t(user_id: int, key: str, **kwargs) -> str:
    lang = get_lang(user_id)
    tr = TRANSLATIONS.get(lang, TRANSLATIONS["ru"])
    text = tr.get(key, TRANSLATIONS["ru"].get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


TRANSLATIONS = {
    "ru": {
        "banned": "Вы забанены.",
        "banned_reason": "Вы забанены.\nПричина: {reason}",
        "no_wallets": "У вас нет кошельков.",
        "no_wallet_create": "У вас нет кошелька. Создайте: /start",
        "wallet_not_found": "Кошелёк {target} не найден.",
        "wallet_not_found_short": "Кошелёк не найден.",
        "link_invalid": "Ссылка недействительна.",
        "link_used": "Эта ссылка уже использована.",
        "token_invalid": "Токен невалиден. Попробуйте снова: /start",
        "token_format": "Неверный формат токена.\nФормат: 123456789:AA...\n\nПопробуйте снова: /start",
        "token_prompt": "Отправьте токен вашего бота.\nФормат: 123456789:AA...\n\nПолучить токен можно у @BotFather -> /newbot",
        "token_looks": "Это похоже на токен, а не имя.\nВведите имя для кошелька (или /cancel):",
        "wallet_created": "Кошелёк создан!\n\nАдрес: {address}\nБот: @{bot}\n\nОтправка средств:\n/send {address} СУММА\n\nНапример:\n/send {address} 100",
        "wallet_created_rename": "Кошелёк создан!\n\nАдрес: {address}\nБот: @{bot}\n\nПодарок: введите имя для кошелька\n(или /cancel для пропуска):",
        "wallet_limit": "Лимит кошельков ({max}) достигнут.\n\nУправление: /wallet",
        "wallet_second_paid": "Первый кошелёк — бесплатно, второй — {price} Stars.\n\nИспользуйте /wallet → Купить",
        "wallet_buy_paid": "✅ Оплата {amount} Stars получена!\n\nТеперь отправьте токен бота для создания кошелька.\nФормат: 123456789:AA...\n\nПолучить токен: @BotFather -> /newbot",
        "wallet_deleted": "🗑 Кошелёк {addr} удалён.",
        "wallet_recreated": "✅ Адрес пересоздан!\n\nСтарый: {old}\nНовый: {new}\nБот: @{bot}\n\nОтправка: /send {new} СУММА",
        "wallet_cant_delete_one": "Нельзя удалить единственный кошелёк.",
        "wallet_select_recreate": "Выберите кошелёк для пересоздания:",
        "wallet_recreate_prompt": "Старый адрес удалён.\n\nОтправьте новый токен бота для создания кошелька.\nФормат: 123456789:AA...\n\nПолучить токен: @BotFather -> /newbot",
        "wallet_select_delete": "Выберите кошелёк для удаления:",
        "wallet_limit_reached": "Лимит кошельков ({max}) достигнут.",
        "invoice_failed": "Не удалось создать счёт.\n\n{desc}",
        "payment_received": "Оплата {amount} {currency} получена!",
        "payment_received_wallet": "Оплата {amount} Stars получена!",
        "send_link_created": "Ссылка для оплаты создана!\n\nКошелёк: {display}\nБот: @{bot}\nСумма: {amount} Stars\n\nСсылка:\n{link}\n\nОтправьте её получателю для оплаты.\nБот активен 5 минут для приёма оплаты.",
        "send_enter_amount": "Введите сумму в Stars (число):",
        "send_amount_invalid": "Введите число — сумму в Stars:",
        "send_too_many_errors": "Слишком много ошибок. Начните заново: /send АДРЕС СУММА",
        "send_amount_range": "Сумма от {min} до {max}. Начните заново: /send АДРЕС СУММА",
        "send_amount_range_retry": "Сумма от {min} до {max} Stars. Попробуйте снова:",
        "send_amount_range_short": "Сумма от {min} до {max} Stars",
        "send_amount_zero": "Сумма должна быть больше 0",
        "send_amount_max": "Максимальная сумма — {max} Stars",
        "rename_cancel": "Ок. Имя не изменено.",
        "rename_done": "Имя кошелька изменено!\n\nАдрес: {address}\nИмя: {name}\n\n/send {name} СУММА",
        "promo_invalid": "Промокод недействителен или уже использован.",
        "gift_invalid": "Ссылка недействительна или уже использована.",
        "gift_activated": "🎁 Подарок активирован!\n",
        "gift_rename_bonus": "✨ Бонус: смена имени кошелька",
        "gift_rename_prompt": "\nВведите новое имя (или /cancel):",
        "gift_no_wallet": "\nСоздайте кошелёк: /start",
        "gift_tg_sent": "✅ Telegram Gift отправлен вам!",
        "gift_tg_failed": "⚠️ Не удалось отправить Gift",
        "gift_item": "Предмет: {title}",
        "promo_activated": "🎟 Промокод {code} активирован!\n",
        "promo_rename_prompt": "\nОтправьте токен бота для создания кошелька.",
        "no_wallets_list": "Нет кошельков.",
        "no_promos": "Нет промокодов.",
        "no_gifts": "Нет подарков.",
        "back": "← Назад",
        "help_title": "Помощь",
        "help_wallet": "Кошелёк:\n  /start — создать кошелёк\n  /wallet — меню кошельков\n  /my — мой кошелёк",
        "help_payment": "Оплата:\n  /send АДРЕС СУММА — ссылка на оплату\n  /pay 100 — оплата через бота\n  @bot 10 — inline-счёт",
        "help_promo": "Промокоды:\n  /redeem КОД — активировать промокод",
        "lang_select": "Выберите язык / Select language",
        "lang_set": "✅ Язык установлен: Русский",
        "start_greeting": "Привет!\n\nСоздайте кошелёк для приёма оплаты Stars.\nНажмите кнопку ниже и введите токен бота.",
        "start_create": "Создать кошелёк",
        "start_help": "Помощь",
        "my_wallet": "Ваш кошелёк: {addr}{name}\nБот: @{bot}\n\nОтправка средств:\n/send {addr} СУММА",
        "wallet_menu_title": "💼 Мои кошельки ({count}/{max})\n",
        "wallet_menu_list": "📋 Мои адреса",
        "wallet_menu_stats": "📊 Статистика",
        "wallet_menu_recreate": "🔄 Пересоздать",
        "wallet_menu_delete": "🗑 Удалить",
        "wallet_menu_buy": "🛒 Купить ({price} Stars)",
        "wallet_menu_limit": "🛕 Лимит достигнут",
        "wallet_menu_help": "❓ Помощь",
        "wallet_list_title": "📋 Ваши кошельки ({count}/{max})\n",
        "wallet_list_item": "Адрес: {addr}{name}\nБот: @{bot}\nОтправка: /send {addr} СУММА\n",
        "wallet_stats_title": "📊 Статистика\n",
        "wallet_stats_item": "{addr}{name}\n  Получено: {received} Stars ({count} оплат)\n",
        "wallet_help_text": "💼 Управление кошельками\n\nПервый кошелёк — бесплатно\nВторой — {price} Stars\nЛимит: {max} кошелька\n\nКоманды:\n  /wallet — это меню\n  /start — создать кошелёк\n  /my — показать кошелёк\n  /send АДРЕС СУММА — оплата",
        "inline_help": "Напишите сумму после имени бота.\n\nПример:\n@kise 10",
        "inline_help_title": "Создать счёт на оплату",
        "inline_help_desc": "Введите сумму, например: 10",
        "inline_invalid_title": "Неверная сумма",
        "inline_invalid_desc": "Например: @kise 10",
        "inline_invalid_text": "Неверная сумма.\n\nПример:\n@kise 10",
        "inline_min_title": "Сумма слишком маленькая",
        "inline_min_desc": "Минимум — {min} Stars",
        "inline_max_title": "Сумма слишком большая",
        "inline_max_desc": "Максимум — {max} Stars",
        "inline_no_wallet_title": "У вас нет кошелька",
        "inline_no_wallet_desc": "Создайте кошелёк: /start у @kise",
        "inline_no_wallet_text": "У вас нет кошелька.\n\nСоздайте: /start у @kise",
        "inline_article_title": "Счёт на {amount} Stars",
        "inline_article_desc": "Оплата на @{bot}",
        "inline_article_text": "💰 Счёт на оплату\n\nСумма: {amount} Stars\nКошелёк: {display}\n\nНажмите кнопку для оплаты:",
        "inline_pay_btn": "Оплатить {amount} Stars",
        "invoice_title": "Оплата {amount} Stars",
        "invoice_desc": "Оплата на сумму {amount} Stars",
        "invoice_buy_title": "Покупка кошелька ({price} Stars)",
        "invoice_buy_desc": "2-й кошелёк. Максимум {max} на пользователя.",
    },
    "en": {
        "banned": "You are banned.",
        "banned_reason": "You are banned.\nReason: {reason}",
        "no_wallets": "You have no wallets.",
        "no_wallet_create": "You have no wallet. Create one: /start",
        "wallet_not_found": "Wallet {target} not found.",
        "wallet_not_found_short": "Wallet not found.",
        "link_invalid": "Link is invalid.",
        "link_used": "This link has already been used.",
        "token_invalid": "Invalid token. Try again: /start",
        "token_format": "Invalid token format.\nFormat: 123456789:AA...\n\nTry again: /start",
        "token_prompt": "Send your bot token.\nFormat: 123456789:AA...\n\nGet token from @BotFather -> /newbot",
        "token_looks": "This looks like a token, not a name.\nEnter a name for the wallet (or /cancel):",
        "wallet_created": "Wallet created!\n\nAddress: {address}\nBot: @{bot}\n\nSending funds:\n/send {address} AMOUNT\n\nExample:\n/send {address} 100",
        "wallet_created_rename": "Wallet created!\n\nAddress: {address}\nBot: @{bot}\n\nGift: enter a name for the wallet\n(or /cancel to skip):",
        "wallet_limit": "Wallet limit ({max}) reached.\n\nManage: /wallet",
        "wallet_second_paid": "First wallet is free, second costs {price} Stars.\n\nUse /wallet → Buy",
        "wallet_buy_paid": "✅ Payment of {amount} Stars received!\n\nNow send the bot token to create a wallet.\nFormat: 123456789:AA...\n\nGet token: @BotFather -> /newbot",
        "wallet_deleted": "🗑 Wallet {addr} deleted.",
        "wallet_recreated": "✅ Address recreated!\n\nOld: {old}\nNew: {new}\nBot: @{bot}\n\nSend: /send {new} AMOUNT",
        "wallet_cant_delete_one": "Cannot delete your only wallet.",
        "wallet_select_recreate": "Select a wallet to recreate:",
        "wallet_recreate_prompt": "Old address deleted.\n\nSend a new bot token to create a wallet.\nFormat: 123456789:AA...\n\nGet token: @BotFather -> /newbot",
        "wallet_select_delete": "Select a wallet to delete:",
        "wallet_limit_reached": "Wallet limit ({max}) reached.",
        "invoice_failed": "Failed to create invoice.\n\n{desc}",
        "payment_received": "Payment of {amount} {currency} received!",
        "payment_received_wallet": "Payment of {amount} Stars received!",
        "send_link_created": "Payment link created!\n\nWallet: {display}\nBot: @{bot}\nAmount: {amount} Stars\n\nLink:\n{link}\n\nSend it to the payer.\nBot is active for 5 minutes to accept payment.",
        "send_enter_amount": "Enter the amount in Stars (number):",
        "send_amount_invalid": "Enter a number — amount in Stars:",
        "send_too_many_errors": "Too many errors. Start over: /send ADDRESS AMOUNT",
        "send_amount_range": "Amount from {min} to {max}. Start over: /send ADDRESS AMOUNT",
        "send_amount_range_retry": "Amount from {min} to {max} Stars. Try again:",
        "send_amount_range_short": "Amount from {min} to {max} Stars",
        "send_amount_zero": "Amount must be greater than 0",
        "send_amount_max": "Maximum amount — {max} Stars",
        "rename_cancel": "Ok. Name not changed.",
        "rename_done": "Wallet name changed!\n\nAddress: {address}\nName: {name}\n\n/send {name} AMOUNT",
        "promo_invalid": "Promo code is invalid or already used.",
        "gift_invalid": "Link is invalid or already used.",
        "gift_activated": "🎁 Gift activated!\n",
        "gift_rename_bonus": "✨ Bonus: wallet name change",
        "gift_rename_prompt": "\nEnter a new name (or /cancel):",
        "gift_no_wallet": "\nCreate a wallet: /start",
        "gift_tg_sent": "✅ Telegram Gift sent to you!",
        "gift_tg_failed": "⚠️ Failed to send Gift",
        "gift_item": "Item: {title}",
        "promo_activated": "🎟 Promo code {code} activated!\n",
        "promo_rename_prompt": "\nSend the bot token to create a wallet.",
        "no_wallets_list": "No wallets.",
        "no_promos": "No promo codes.",
        "no_gifts": "No gifts.",
        "back": "← Back",
        "help_title": "Help",
        "help_wallet": "Wallet:\n  /start — create wallet\n  /wallet — wallet menu\n  /my — my wallet",
        "help_payment": "Payment:\n  /send ADDRESS AMOUNT — payment link\n  /pay 100 — pay via bot\n  @bot 10 — inline invoice",
        "help_promo": "Promo codes:\n  /redeem CODE — activate promo code",
        "lang_select": "Select language / Выберите язык",
        "lang_set": "✅ Language set: English",
        "start_greeting": "Hello!\n\nCreate a wallet to accept Stars payments.\nClick the button below and enter your bot token.",
        "start_create": "Create wallet",
        "start_help": "Help",
        "my_wallet": "Your wallet: {addr}{name}\nBot: @{bot}\n\nSending funds:\n/send {addr} AMOUNT",
        "wallet_menu_title": "💼 My wallets ({count}/{max})\n",
        "wallet_menu_list": "📋 My addresses",
        "wallet_menu_stats": "📊 Statistics",
        "wallet_menu_recreate": "🔄 Recreate",
        "wallet_menu_delete": "🗑 Delete",
        "wallet_menu_buy": "🛒 Buy ({price} Stars)",
        "wallet_menu_limit": "🛕 Limit reached",
        "wallet_menu_help": "❓ Help",
        "wallet_list_title": "📋 Your wallets ({count}/{max})\n",
        "wallet_list_item": "Address: {addr}{name}\nBot: @{bot}\nSend: /send {addr} AMOUNT\n",
        "wallet_stats_title": "📊 Statistics\n",
        "wallet_stats_item": "{addr}{name}\n  Received: {received} Stars ({count} payments)\n",
        "wallet_help_text": "💼 Wallet management\n\nFirst wallet — free\nSecond — {price} Stars\nLimit: {max} wallets\n\nCommands:\n  /wallet — this menu\n  /start — create wallet\n  /my — show wallet\n  /send ADDRESS AMOUNT — payment",
        "inline_help": "Type the amount after the bot name.\n\nExample:\n@kise 10",
        "inline_help_title": "Create payment invoice",
        "inline_help_desc": "Enter amount, e.g.: 10",
        "inline_invalid_title": "Invalid amount",
        "inline_invalid_desc": "Example: @kise 10",
        "inline_invalid_text": "Invalid amount.\n\nExample:\n@kise 10",
        "inline_min_title": "Amount too small",
        "inline_min_desc": "Minimum — {min} Stars",
        "inline_max_title": "Amount too large",
        "inline_max_desc": "Maximum — {max} Stars",
        "inline_no_wallet_title": "You have no wallet",
        "inline_no_wallet_desc": "Create a wallet: /start at @kise",
        "inline_no_wallet_text": "You have no wallet.\n\nCreate one: /start at @kise",
        "inline_article_title": "Invoice for {amount} Stars",
        "inline_article_desc": "Payment to @{bot}",
        "inline_article_text": "💰 Payment invoice\n\nAmount: {amount} Stars\nWallet: {display}\n\nClick the button below to pay:",
        "inline_pay_btn": "Pay {amount} Stars",
        "invoice_title": "Payment {amount} Stars",
        "invoice_desc": "Payment of {amount} Stars",
        "invoice_buy_title": "Buy wallet ({price} Stars)",
        "invoice_buy_desc": "2nd wallet. Maximum {max} per user.",
    },
}


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
    """Генерирует уникальный адрес формата ks-XXXXX (5 символов)."""
    chars = string.ascii_lowercase + string.digits
    while True:
        addr = "ks-" + "".join(secrets.choice(chars) for _ in range(5))
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


def create_gift(activations: int, item: str = "", amount: int = 0, rename: bool = False, gift_id: str = "") -> str:
    code = f"gift_{secrets.token_hex(4)}"
    gifts = load_gifts()
    gifts[code] = {
        "max": activations,
        "used": [],
        "item": item,
        "amount": amount,
        "rename": rename,
        "gift_id": gift_id,
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
# PROMOS (текстовые промокоды)
# ============================================================

PROMOS_FILE = Path(__file__).parent / "promos.json"


def load_promos() -> dict:
    if PROMOS_FILE.exists():
        try:
            return json.loads(PROMOS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_promos(promos: dict) -> None:
    try:
        PROMOS_FILE.write_text(
            json.dumps(promos, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_promos error: {e!r}")


def create_promo(code: str, activations: int, item: str = "", rename: bool = False, gift_id: str = "") -> bool:
    promos = load_promos()
    if code in promos:
        return False
    promos[code] = {
        "max": activations,
        "used": [],
        "item": item,
        "rename": rename,
        "gift_id": gift_id,
        "created": int(time.time()),
    }
    save_promos(promos)
    return True


def use_promo(code: str, user_id: int) -> dict | None:
    """Активирует промокод. Возвращает info или None."""
    promos = load_promos()
    promo = promos.get(code)
    if not promo:
        return None
    if user_id in promo.get("used", []):
        return None
    if len(promo.get("used", [])) >= promo.get("max", 0):
        return None
    promo["used"].append(user_id)
    save_promos(promos)
    return promo


# ============================================================
# BANS (баны пользователей)
# ============================================================

def load_bans() -> dict:
    if BANS_FILE.exists():
        try:
            return json.loads(BANS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_bans(bans: dict) -> None:
    try:
        BANS_FILE.write_text(
            json.dumps(bans, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_bans error: {e!r}")


def is_banned(user_id: int) -> dict | None:
    bans = load_bans()
    key = str(user_id)
    return bans.get(key)


def ban_user(user_id: int, reason: str = "") -> bool:
    bans = load_bans()
    key = str(user_id)
    if key in bans:
        return False
    bans[key] = {
        "reason": reason,
        "banned_at": int(time.time()),
    }
    save_bans(bans)
    return True


def unban_user(user_id: int) -> bool:
    bans = load_bans()
    key = str(user_id)
    if key not in bans:
        return False
    del bans[key]
    save_bans(bans)
    return True


# ============================================================
# TELEGRAM GIFTS CATALOG
# ============================================================

GIFTS_CATALOG = {
    "trophy":       {"id": "5168043875654172773", "title": "🏆 Трофей",           "stars": 100},
    "rose":         {"id": "5168103777563050263", "title": "🌹 Роза",             "stars": 25},
    "cake":         {"id": "5170144170496491616", "title": "🎂 Торт",             "stars": 50},
    "heart":        {"id": "5170145012310081615", "title": "💝 Сердце",           "stars": 15},
    "sock":         {"id": "5170233102089322756", "title": "🧦 Носок",             "stars": 15},
    "gift":         {"id": "5170250947678437525", "title": "🎁 Подарок",          "stars": 25},
    "bouquet":      {"id": "5170314324215857265", "title": "💐 Букет",            "stars": 50},
    "diamond":      {"id": "5170521118301225164", "title": "💎 Бриллиант",       "stars": 100},
    "rocket":       {"id": "5170564780938756245", "title": "🚀 Ракета",          "stars": 50},
    "ring":         {"id": "5170690322832818290", "title": "💍 Кольцо",           "stars": 100},
    "ball":         {"id": "6028601630662853006", "title": "🎾 Мяч",              "stars": 50},
    "bear":         {"id": "6046178578163303744", "title": "🐻 Медведь",         "stars": 50},
    "snoop":        {"id": "6014591077976114307", "title": "🎤 Snoop Dogg",      "stars": 200},
    "cupid":        {"id": "5868561433997870501", "title": "💘 Cupid Charm",     "stars": 500},
    "santa":        {"id": "5983471780763796287", "title": "🎅 Santa Hat",       "stars": 50},
    "sparkler":     {"id": "6003643167683903930", "title": "✨ Party Sparkler",  "stars": 15},
}


def send_tg_gift(token: str, user_id: int, gift_id: str) -> bool:
    """Отправляет Telegram Gift через API."""
    result = user_bot_api(API_BASE, token, "sendGift", {
        "user_id": user_id,
        "gift_id": gift_id,
    }, timeout=15)
    return isinstance(result, dict) and result.get("ok") is True


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

    result = {
        "type": "article",
        "id": secrets.token_hex(8),
        "title": f"Счёт на {amount} Stars",
        "description": f"Оплатить {amount} Stars",
        "input_message_content": {
            "message_text": f"💰 Счёт на оплату\n\nСумма: {amount} Stars\n\nНажмите кнопку ниже для оплаты:",
        },
        "reply_markup": {
            "inline_keyboard": [[
                {"text": f"Оплатить {amount} Stars", "callback_data": f"pay_{amount}"},
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
            charge_id = payment.get("telegram_payment_charge_id", "")
            if check and not check.get("paid"):
                mark_check_paid(code, chat_id)
                wallet_addr = check.get("wallet", "")
                wallets = load_wallets()
                wallet_info = wallets.get(wallet_addr, {})
                owner_id = wallet_info.get("owner", 0)
                # Текст + PDF плательщику через основного бота
                send_message(chat_id, t(chat_id, "payment_received", amount=amount, currency="XTR"))
                send_receipt_pdf(chat_id, amount, wallet_addr=wallet_addr, payer_id=chat_id, charge_id=charge_id, fee=0)
                # Уведомление + PDF владельцу
                if owner_id and owner_id != chat_id:
                    send_message(owner_id, t(owner_id, "payment_received_wallet", amount=amount))
                    send_receipt_pdf(owner_id, amount, wallet_addr=wallet_addr, payer_id=chat_id, charge_id=charge_id, fee=0)
        else:
            user_bot_api(base_url, token, "sendMessage", {
                "chat_id": chat_id,
                "text": t(chat_id, "payment_received_wallet", amount=amount),
            })
            owner_addr, owner_id = find_wallet_owner(token)
            charge_id = payment.get("telegram_payment_charge_id", "")
            send_receipt_pdf(chat_id, amount, wallet_addr=owner_addr or "", payer_id=chat_id, charge_id=charge_id, fee=0)
            if owner_id and owner_id != chat_id:
                user_bot_api(base_url, token, "sendMessage", {
                    "chat_id": owner_id,
                    "text": t(owner_id, "payment_received_wallet", amount=amount),
                })
                send_receipt_pdf(owner_id, amount, wallet_addr=owner_addr or "", payer_id=chat_id, charge_id=charge_id, fee=0)
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


def user_bot_handle_callback(base_url, token, callback):
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

    user_bot_api(base_url, token, "answerCallbackQuery", {
        "callback_query_id": callback_id,
    })

    if data.startswith("pay_"):
        amount_str = data[4:]
        if not amount_str.isdigit():
            return
        amount = int(amount_str)
        if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
            user_bot_api(base_url, token, "sendMessage", {
                "chat_id": chat_id,
                "text": f"Сумма должна быть от {MIN_AMOUNT} до {MAX_AMOUNT} Stars",
            })
            return
        user_bot_send_invoice(base_url, token, chat_id, amount)


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
                "allowed_updates": ["message", "inline_query", "pre_checkout_query", "callback_query"],
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

                callback = update.get("callback_query")
                if isinstance(callback, dict):
                    try:
                        user_bot_handle_callback(base_url, token, callback)
                    except Exception as e:
                        print(f"[user_bot {chat_id}] callback error: {e!r}")
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
# WALLET MENU
# ============================================================

def get_user_wallets(chat_id: int) -> list[tuple[str, dict]]:
    wallets = load_wallets()
    return [(a, w) for a, w in wallets.items() if w.get("owner") == chat_id]


def show_wallet_menu(chat_id: int):
    user_wallets = get_user_wallets(chat_id)
    count = len(user_wallets)

    if count == 0:
        send_message(chat_id, t(chat_id, "no_wallet_create"))
        return

    lines = [t(chat_id, "wallet_menu_title", count=count, max=MAX_WALLETS)]
    for addr, info in user_wallets:
        name = info.get("name", "")
        name_str = f" ({name})" if name else ""
        lines.append(f"  {addr}{name_str} @{info.get('username', '?')}")

    keyboard = {
        "inline_keyboard": [
            [
                {"text": t(chat_id, "wallet_menu_list"), "callback_data": "wallet_list"},
                {"text": t(chat_id, "wallet_menu_stats"), "callback_data": "wallet_stats"},
            ],
            [
                {"text": t(chat_id, "wallet_menu_recreate"), "callback_data": "wallet_recreate"},
                {"text": t(chat_id, "wallet_menu_delete"), "callback_data": "wallet_delete"},
            ],
        ]
    }

    if count < MAX_WALLETS:
        keyboard["inline_keyboard"].append([
            {"text": t(chat_id, "wallet_menu_buy", price=WALLET_PRICE), "callback_data": "wallet_buy"},
        ])
    else:
        keyboard["inline_keyboard"].append([
            {"text": t(chat_id, "wallet_menu_limit"), "callback_data": "wallet_nope"},
        ])
    keyboard["inline_keyboard"].append([
        {"text": t(chat_id, "wallet_menu_help"), "callback_data": "wallet_help"},
    ])

    send_message(chat_id, "\n".join(lines), reply_markup=keyboard)


def handle_wallet_callback(callback_id, chat_id, data):
    answer_callback(callback_id)

    if data == "wallet_menu":
        show_wallet_menu(chat_id)
        return

    if data == "wallet_list":
        user_wallets = get_user_wallets(chat_id)
        if not user_wallets:
            send_message(chat_id, t(chat_id, "no_wallets"))
            return
        lines = [t(chat_id, "wallet_list_title", count=len(user_wallets), max=MAX_WALLETS)]
        for addr, info in user_wallets:
            name = info.get("name", "")
            name_str = f" ({name})" if name else ""
            lines.append(t(chat_id, "wallet_list_item", addr=addr, name=name_str, bot=info.get("username", "?")))
        send_message(chat_id, "\n".join(lines))
        return

    if data == "wallet_stats":
        user_wallets = get_user_wallets(chat_id)
        if not user_wallets:
            send_message(chat_id, t(chat_id, "no_wallets"))
            return
        checks = load_checks()
        user_addrs = {a for a, _ in user_wallets}
        lines = [t(chat_id, "wallet_stats_title")]
        for addr, info in user_wallets:
            name = info.get("name", "")
            name_str = f" ({name})" if name else ""
            as_receiver = [c for c in checks.values() if c.get("wallet") == addr and c.get("paid")]
            received = sum(c.get("amount", 0) for c in as_receiver)
            lines.append(t(chat_id, "wallet_stats_item", addr=addr, name=name_str, received=received, count=len(as_receiver)))
        send_message(chat_id, "\n".join(lines))
        return

    if data == "wallet_recreate":
        user_wallets = get_user_wallets(chat_id)
        if not user_wallets:
            send_message(chat_id, t(chat_id, "no_wallets"))
            return
        if len(user_wallets) == 1:
            addr = user_wallets[0][0]
            wallets = load_wallets()
            del wallets[addr]
            save_wallets(wallets)
            WAITING_RECREATE[chat_id] = "recreate"
            WAITING_FOR_TOKEN.add(chat_id)
            send_message(chat_id, t(chat_id, "wallet_recreate_prompt"))
        else:
            keyboard = {"inline_keyboard": [[
                {"text": f"{a}", "callback_data": f"wallet_recreate_{a}"}
                for a, _ in user_wallets
            ], [{"text": t(chat_id, "back"), "callback_data": "wallet_menu"}]]}
            send_message(chat_id, t(chat_id, "wallet_select_recreate"), reply_markup=keyboard)
        return

    if data.startswith("wallet_recreate_"):
        addr = data[len("wallet_recreate_"):]
        wallets = load_wallets()
        info = wallets.get(addr)
        if not info or info.get("owner") != chat_id:
            send_message(chat_id, t(chat_id, "wallet_not_found_short"))
            return
        del wallets[addr]
        save_wallets(wallets)
        WAITING_RECREATE[chat_id] = "recreate"
        WAITING_FOR_TOKEN.add(chat_id)
        send_message(chat_id, t(chat_id, "wallet_recreate_prompt"))
        return

    if data == "wallet_delete":
        user_wallets = get_user_wallets(chat_id)
        if not user_wallets:
            send_message(chat_id, t(chat_id, "no_wallets"))
            return
        if len(user_wallets) == 1:
            send_message(chat_id, t(chat_id, "wallet_cant_delete_one"))
            return
        keyboard = {"inline_keyboard": [[
            {"text": f"{a}", "callback_data": f"wallet_delete_{a}"}
            for a, _ in user_wallets
        ], [{"text": t(chat_id, "back"), "callback_data": "wallet_menu"}]]}
        send_message(chat_id, t(chat_id, "wallet_select_delete"), reply_markup=keyboard)
        return

    if data.startswith("wallet_delete_"):
        addr = data[len("wallet_delete_"):]
        wallets = load_wallets()
        info = wallets.get(addr)
        if not info or info.get("owner") != chat_id:
            send_message(chat_id, t(chat_id, "wallet_not_found_short"))
            return
        del wallets[addr]
        save_wallets(wallets)
        send_message(chat_id, t(chat_id, "wallet_deleted", addr=addr))
        return

    if data == "wallet_buy":
        user_wallets = get_user_wallets(chat_id)
        if len(user_wallets) >= MAX_WALLETS:
            send_message(chat_id, t(chat_id, "wallet_limit_reached", max=MAX_WALLETS))
            return
        result = telegram("sendInvoice", {
            "chat_id": chat_id,
            "title": t(chat_id, "invoice_buy_title", price=WALLET_PRICE),
            "description": t(chat_id, "invoice_buy_desc", max=MAX_WALLETS),
            "payload": "wallet_buy",
            "currency": "XTR",
            "prices": [{"label": f"{WALLET_PRICE} Stars", "amount": WALLET_PRICE}],
        })
        if not isinstance(result, dict) or result.get("ok") is not True:
            desc = result.get("description", "Error") if isinstance(result, dict) else "No response"
            send_message(chat_id, t(chat_id, "invoice_failed", desc=desc))
        return

    if data == "wallet_nope":
        send_message(chat_id, t(chat_id, "wallet_limit_reached", max=MAX_WALLETS))
        return

    if data == "wallet_help":
        send_message(chat_id, t(chat_id, "wallet_help_text", price=WALLET_PRICE, max=MAX_WALLETS))
        return


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

    ban = is_banned(chat_id)
    if ban:
        reason = ban.get("reason", "")
        if reason:
            send_message(chat_id, t(chat_id, "banned_reason", reason=reason))
        else:
            send_message(chat_id, t(chat_id, "banned"))
        return

    if data.startswith("wallet_"):
        handle_wallet_callback(callback_id, chat_id, data)
        return

    if data == "create_bot":
        answer_callback(callback_id)
        WAITING_FOR_TOKEN.add(chat_id)
        send_message(chat_id, t(chat_id, "token_prompt"))
        return

    if data.startswith("lang_"):
        answer_callback(callback_id)
        if data == "lang_ru":
            set_lang(chat_id, "ru")
            send_message(chat_id, t(chat_id, "lang_set"))
        elif data == "lang_en":
            set_lang(chat_id, "en")
            send_message(chat_id, t(chat_id, "lang_set"))
        elif data == "lang_toggle":
            cur = get_lang(chat_id)
            new = "en" if cur == "ru" else "ru"
            set_lang(chat_id, new)
            send_message(chat_id, t(chat_id, "lang_set"))
        return

    if data == "help":
        answer_callback(callback_id)
        is_admin = chat_id == ADMIN_ID
        msg = t(chat_id, "help_title") + "\n\n" + t(chat_id, "help_wallet") + "\n\n" + t(chat_id, "help_payment") + "\n\n" + t(chat_id, "help_promo")
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
                "  /gift list — каталог подарков\n"
                "  /gift bear 10 — 10 медведей (TG Gift)\n"
                "  /gift bear us 10 — 10 медведей + rename\n"
                "  /gift rose us 5 — 5 роз + rename\n"
                "  /gifts — список подарков\n\n"
                "Промокоды:\n"
                "  /promo START 10 — промокод (rename)\n"
                "  /promo BEAR bear 10 — 10 медведей\n"
                "  /promo BEAR bear us 10 — 10 медведей + rename\n"
                "  /promos — список промокодов\n"
                "  /delpromo CODE — удалить промокод\n\n"
                "Баны:\n"
                "  /ban ID [причина] — забанить\n"
                "  /unban ID — разбанить\n"
                "  /banlist — список банов"
            )
        send_message(chat_id, msg)
        return


# ============================================================
# PDF RECEIPT
# ============================================================

def generate_receipt_pdf(
    user_id: int,
    amount: int,
    wallet_addr: str = "",
    payer_id: int = 0,
    charge_id: str = "",
    fee: int = 0,
) -> bytes:
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A5
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        pdfmetrics.registerFont(TTFont("DejaVu", font_path))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", bold_path))
        base_font = "DejaVu"
        bold_font = "DejaVu-Bold"
    except Exception:
        base_font = "Helvetica"
        bold_font = "Helvetica-Bold"

    lang = get_lang(user_id)
    is_ru = lang == "ru"

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A5, topMargin=15 * mm, bottomMargin=15 * mm,
                           leftMargin=15 * mm, rightMargin=15 * mm)
    title_style = ParagraphStyle("Title", fontName=bold_font, fontSize=18, alignment=TA_CENTER,
                                textColor=colors.HexColor("#2aabee"), spaceAfter=10)
    normal_style = ParagraphStyle("Normal", fontName=base_font, fontSize=11, leading=16)

    elements = []

    if is_ru:
        elements.append(Paragraph("Квитанция об оплате", title_style))
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Перевод зачислен моментально", normal_style))
        elements.append(Spacer(1, 5 * mm))

        rows = [
            ["Дата", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")],
            ["Сумма", f"{amount} Stars"],
            ["Комиссия", f"{fee} Stars"],
            ["К зачислению", f"{amount - fee} Stars"],
            ["Кошелёк", wallet_addr or "—"],
            ["Плательщик", str(payer_id) if payer_id else "—"],
            ["ID транзакции", charge_id or "—"],
            ["Статус", "Зачислено моментально"],
        ]
    else:
        elements.append(Paragraph("Payment Receipt", title_style))
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Transfer credited instantly", normal_style))
        elements.append(Spacer(1, 5 * mm))

        rows = [
            ["Date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")],
            ["Amount", f"{amount} Stars"],
            ["Fee", f"{fee} Stars"],
            ["To credit", f"{amount - fee} Stars"],
            ["Wallet", wallet_addr or "—"],
            ["Payer", str(payer_id) if payer_id else "—"],
            ["Transaction ID", charge_id or "—"],
            ["Status", "Credited instantly"],
        ]

    label_style = ParagraphStyle("Label", fontName=bold_font, fontSize=11,
                                 textColor=colors.HexColor("#666666"), leading=16)
    val_style = ParagraphStyle("Val", fontName=base_font, fontSize=11, leading=16)

    data = [[Paragraph(r[0], label_style), Paragraph(r[1], val_style)] for r in rows]
    table = Table(data, colWidths=[50 * mm, 70 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e0e0e0")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]))
    elements.append(table)
    elements.append(Spacer(1, 8 * mm))

    footer_text = "kise pay — мгновенные переводы" if is_ru else "kise pay — instant transfers"
    footer_style = ParagraphStyle("Footer", parent=normal_style, fontSize=9,
                                   textColor=colors.HexColor("#999999"), alignment=TA_CENTER)
    elements.append(Paragraph(footer_text, footer_style))

    doc.build(elements)
    return buf.getvalue()


def send_document(chat_id, file_bytes, filename, caption=None):
    import io
    url = f"{API}/sendDocument"
    files = {"document": (filename, io.BytesIO(file_bytes), "application/pdf")}
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    try:
        r = session.post(url, files=files, data=data, timeout=30)
        return r.json()
    except Exception as e:
        print(f"sendDocument error: {e!r}")
        return None


def send_receipt_pdf(chat_id, amount, wallet_addr="", payer_id=0, charge_id="", fee=0):
    try:
        pdf = generate_receipt_pdf(chat_id, amount, wallet_addr, payer_id, charge_id, fee)
        lang = get_lang(chat_id)
        caption = "Квитанция об оплате" if lang == "ru" else "Payment receipt"
        send_document(chat_id, pdf, "receipt.pdf", caption=caption)
    except Exception as e:
        print(f"send_receipt_pdf error: {e!r}")


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

    if payload == "wallet_buy":
        add_pending_buy(chat_id)
        WAITING_FOR_TOKEN.add(chat_id)
        send_message(chat_id, t(chat_id, "wallet_buy_paid", amount=amount))
        send_receipt_pdf(chat_id, amount, payer_id=chat_id, charge_id=charge_id, fee=0)
        return

    # pay_ обрабатывается ботом кошелька, основной бот пропускает
    if payload.startswith("pay_"):
        return

    send_message(chat_id, t(chat_id, "payment_received", amount=amount, currency=currency))
    send_receipt_pdf(chat_id, amount, payer_id=chat_id, charge_id=charge_id, fee=0)


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
    name = wallet.get("name", "")
    display = f"{name} ({address})" if name else address
    link = f"https://t.me/{bot_username}?start={code}"
    send_message(chat_id, t(chat_id, "send_link_created", display=display, bot=bot_username, amount=amount, link=link))
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

    ban = is_banned(chat_id)
    if ban:
        reason = ban.get("reason", "")
        if reason:
            send_message(chat_id, t(chat_id, "banned_reason", reason=reason))
        else:
            send_message(chat_id, t(chat_id, "banned"))
        return

    # Ждём новое имя кошелька (после подарка)
    if chat_id in WAITING_RENAME:
        if text == "/cancel":
            WAITING_RENAME.pop(chat_id, None)
            send_message(chat_id, t(chat_id, "rename_cancel"))
            return
        if text == "/my" or text == "/start":
            pass
        elif text.startswith("/"):
            pass
        else:
            address = WAITING_RENAME.pop(chat_id)
            if address == "new":
                WAITING_RENAME.pop(chat_id, None)
                return
            if re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", text):
                send_message(chat_id, t(chat_id, "token_looks"))
                WAITING_RENAME[chat_id] = address
                return
            wallets = load_wallets()
            if address in wallets:
                name = text.strip()
                wallets[address]["name"] = name
                save_wallets(wallets)
                send_message(chat_id, t(chat_id, "rename_done", address=address, name=name))
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
                send_message(chat_id, t(chat_id, "token_format"))
                return
            base_url, result = validate_token(token)
            if base_url is None:
                send_message(chat_id, t(chat_id, "token_invalid"))
                return
            bot_info = result.get("result", {})
            bot_username = bot_info.get("username", "unknown")

            user_wallets = get_user_wallets(chat_id)
            is_buy = has_pending_buy(chat_id)
            is_recreate = chat_id in WAITING_RECREATE
            if len(user_wallets) >= MAX_WALLETS and not is_buy and not is_recreate:
                send_message(chat_id, t(chat_id, "wallet_limit", max=MAX_WALLETS))
                WAITING_FOR_TOKEN.discard(chat_id)
                WAITING_RECREATE.pop(chat_id, None)
                return
            if len(user_wallets) >= 1 and not is_buy and not is_recreate:
                send_message(chat_id, t(chat_id, "wallet_second_paid", price=WALLET_PRICE))
                WAITING_FOR_TOKEN.discard(chat_id)
                return

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
            remove_pending_buy(chat_id)
            WAITING_RECREATE.pop(chat_id, None)

            # Если активирован подарок — просим имя
            if chat_id in WAITING_RENAME:
                WAITING_RENAME[chat_id] = address
                send_message(chat_id, t(chat_id, "wallet_created_rename", address=address, bot=bot_username))
            else:
                send_message(chat_id, t(chat_id, "wallet_created", address=address, bot=bot_username))
            return

    # /start (возможно с параметром gift_XXX, pay_XXX)
    if text.startswith("/start"):
        parts = text.split(" ", 1)
        if len(parts) > 1:
            start_param = parts[1].strip()

            # Оплата по ссылке (через основного бота, invoice через бота кошелька)
            if start_param.startswith("pay_"):
                check = get_check(start_param)
                if check is None:
                    send_message(chat_id, t(chat_id, "link_invalid"))
                    return
                if check.get("paid"):
                    send_message(chat_id, t(chat_id, "link_used"))
                    return
                amount = check["amount"]
                wallet_addr = check.get("wallet", "")
                wallets = load_wallets()
                wallet = wallets.get(wallet_addr)
                if not wallet:
                    send_message(chat_id, t(chat_id, "wallet_not_found_short"))
                    return
                wallet_token = wallet.get("token", "")
                wallet_base = wallet.get("base_url", API_BASE)
                wallet_bot = wallet.get("username", "bot")
                name = wallet.get("name", "")
                display = f"{name} ({wallet_addr})" if name else wallet_addr
                result = user_bot_api(wallet_base, wallet_token, "sendInvoice", {
                    "chat_id": chat_id,
                    "title": t(chat_id, "invoice_title", amount=amount),
                    "description": t(chat_id, "invoice_desc", amount=amount),
                    "payload": start_param,
                    "currency": "XTR",
                    "prices": [{"label": f"{amount} Stars", "amount": amount}],
                })
                if not isinstance(result, dict) or result.get("ok") is not True:
                    desc = result.get("description", "Error") if isinstance(result, dict) else "No response"
                    send_message(chat_id, t(chat_id, "invoice_failed", desc=desc))
                return

            if start_param.startswith("gift_"):
                gift_info = use_gift(start_param, chat_id)
                if not gift_info:
                    send_message(chat_id, t(chat_id, "gift_invalid"))
                    return
                item = gift_info.get("item", "")
                amount = gift_info.get("amount", 0)
                rename = gift_info.get("rename", False)
                tg_gift_id = gift_info.get("gift_id", "")

                gift_sent = False
                if tg_gift_id:
                    gift_sent = send_tg_gift(BOT_TOKEN, chat_id, tg_gift_id)

                gift_lines = [t(chat_id, "gift_activated")]
                if item:
                    cat = GIFTS_CATALOG.get(item, {})
                    title = cat.get("title", item)
                    gift_lines.append(t(chat_id, "gift_item", title=title))
                if gift_sent:
                    gift_lines.append(t(chat_id, "gift_tg_sent"))
                elif tg_gift_id:
                    gift_lines.append(t(chat_id, "gift_tg_failed"))
                if rename:
                    gift_lines.append(t(chat_id, "gift_rename_bonus"))

                wallets = load_wallets()
                user_addr = None
                for addr, info in wallets.items():
                    if info.get("owner") == chat_id:
                        user_addr = addr
                        break

                if rename:
                    if user_addr:
                        WAITING_RENAME[chat_id] = user_addr
                        gift_lines.append(t(chat_id, "gift_rename_prompt"))
                    else:
                        WAITING_FOR_TOKEN.add(chat_id)
                        WAITING_RENAME[chat_id] = "new"
                        gift_lines.append(t(chat_id, "promo_rename_prompt"))
                else:
                    if not user_addr:
                        gift_lines.append(t(chat_id, "gift_no_wallet"))

                send_message(chat_id, "\n".join(gift_lines))
                return

        # Обычный /start без параметра
        wallets = load_wallets()
        for addr, info in wallets.items():
            if info.get("owner") == chat_id:
                name = info.get("name", "")
                name_str = f" ({name})" if name else ""
                send_message(chat_id, t(chat_id, "my_wallet", addr=addr, name=name_str, bot=info.get("username", "?")))
                return

        # Первый запуск — выбор языка
        if get_lang(chat_id) == "ru" and not LANG_FILE.exists():
            keyboard = {
                "inline_keyboard": [[
                    {"text": "🇷🇺 Русский", "callback_data": "lang_ru"},
                    {"text": "🇬🇧 English", "callback_data": "lang_en"},
                ]]
            }
            send_message(chat_id, t(chat_id, "lang_select"), reply_markup=keyboard)
            return

        keyboard = {
            "inline_keyboard": [
                [{"text": t(chat_id, "start_create"), "callback_data": "create_bot"}],
                [{"text": t(chat_id, "start_help"), "callback_data": "help"}],
                [{"text": "🌐 " + ("English" if get_lang(chat_id) == "ru" else "Русский"),
                  "callback_data": "lang_toggle"}],
            ]
        }
        send_message(chat_id, t(chat_id, "start_greeting"), reply_markup=keyboard)
        return

    # /my — показать кошелёк
    if text == "/my":
        wallets = load_wallets()
        for addr, info in wallets.items():
            if info.get("owner") == chat_id:
                name = info.get("name", "")
                name_str = f" ({name})" if name else ""
                send_message(chat_id, t(chat_id, "my_wallet", addr=addr, name=name_str, bot=info.get("username", "?")))
                return
        send_message(chat_id, t(chat_id, "no_wallet_create"))
        return

    # /wallet — меню управления кошельками
    if text == "/wallet":
        show_wallet_menu(chat_id)
        return

    # /lang — сменить язык
    if text == "/lang":
        keyboard = {
            "inline_keyboard": [[
                {"text": "🇷🇺 Русский", "callback_data": "lang_ru"},
                {"text": "🇬🇧 English", "callback_data": "lang_en"},
            ]]
        }
        send_message(chat_id, t(chat_id, "lang_select"), reply_markup=keyboard)
        return

    # /redeem КОД — активировать промокод
    match_redeem = re.fullmatch(r"/redeem(?:@\w+)?\s+(\S+)", text, re.IGNORECASE)
    if match_redeem:
        pcode = match_redeem.group(1).upper()
        promo_info = use_promo(pcode, chat_id)
        if not promo_info:
            send_message(chat_id, t(chat_id, "promo_invalid"))
            return

        item = promo_info.get("item", "")
        rename = promo_info.get("rename", False)
        tg_gift_id = promo_info.get("gift_id", "")

        # Отправляем реальный TG Gift
        gift_sent = False
        if tg_gift_id:
            gift_sent = send_tg_gift(BOT_TOKEN, chat_id, tg_gift_id)

        lines = [f"🎟 Промокод {pcode} активирован!\n"]
        if item:
            cat = GIFTS_CATALOG.get(item, {})
            lines.append(f"🎁 {cat.get('title', item)}")
        if gift_sent:
            lines.append("✅ Telegram Gift отправлен вам!")
        elif tg_gift_id:
            lines.append("⚠️ Не удалось отправить Gift")
        if rename:
            lines.append("✨ Бонус: смена имени кошелька")

        wallets = load_wallets()
        user_addr = None
        for addr, info in wallets.items():
            if info.get("owner") == chat_id:
                user_addr = addr
                break

        if rename:
            if user_addr:
                WAITING_RENAME[chat_id] = user_addr
                lines.append("\nВведите новое имя (или /cancel):")
            else:
                WAITING_FOR_TOKEN.add(chat_id)
                WAITING_RENAME[chat_id] = "new"
                lines.append("\nОтправьте токен бота для создания кошелька.")
        else:
            if not user_addr:
                lines.append("\nСоздайте кошелёк: /start")

        send_message(chat_id, "\n".join(lines))
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
                    send_message(chat_id, t(chat_id, "send_too_many_errors"))
                else:
                    send_message(chat_id, t(chat_id, "send_amount_invalid"))
                return
            amount = int(text)
            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                SEND_ERROR_COUNT[chat_id] = SEND_ERROR_COUNT.get(chat_id, 0) + 1
                if SEND_ERROR_COUNT[chat_id] >= 2:
                    WAITING_SEND_AMOUNT.pop(chat_id, None)
                    SEND_ERROR_COUNT.pop(chat_id, None)
                    send_message(chat_id, t(chat_id, "send_amount_range", min=MIN_AMOUNT, max=MAX_AMOUNT))
                else:
                    send_message(chat_id, t(chat_id, "send_amount_range_retry", min=MIN_AMOUNT, max=MAX_AMOUNT))
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
            send_message(chat_id, t(chat_id, "wallet_not_found", target=target))
            return
        if amount_str:
            amount = int(amount_str)
            if amount < MIN_AMOUNT or amount > MAX_AMOUNT:
                send_message(chat_id, t(chat_id, "send_amount_range_short", min=MIN_AMOUNT, max=MAX_AMOUNT))
                return
            _create_send_link(chat_id, address, amount)
        else:
            WAITING_SEND_AMOUNT[chat_id] = {"address": address}
            SEND_ERROR_COUNT[chat_id] = 0
            send_message(chat_id, t(chat_id, "send_enter_amount"))
        return

    # /pay N (основной бот)
    match = re.fullmatch(r"/pay(?:@\w+)?\s+(\d+)", text, re.IGNORECASE)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            send_message(chat_id, t(chat_id, "send_amount_zero"))
            return
        if amount > MAX_AMOUNT:
            send_message(chat_id, t(chat_id, "send_amount_max", max=MAX_AMOUNT))
            return
        result = telegram("sendInvoice", {
            "chat_id": chat_id,
            "title": t(chat_id, "invoice_title", amount=amount),
            "description": t(chat_id, "invoice_desc", amount=amount),
            "payload": "main_bot_payment",
            "currency": "XTR",
            "prices": [{"label": f"{amount} Stars", "amount": amount}],
        })
        if not isinstance(result, dict) or result.get("ok") is not True:
            desc = result.get("description", "Error") if isinstance(result, dict) else "No response"
            send_message(chat_id, t(chat_id, "invoice_failed", desc=desc))
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
        #   /gift bear 10         — 10 медведей (реальный TG Gift)
        #   /gift bear us 10      — 10 медведей + rename
        #   /gift rose us 5       — 5 роз + rename
        #   /gift list            — каталог подарков
        match_gift_rename = re.fullmatch(r"/gift\s+(\S+)\s+us\s+(\d+)", text, re.IGNORECASE)
        match_gift_item = re.fullmatch(r"/gift\s+(\S+)\s+(\d+)", text, re.IGNORECASE)
        match_gift_plain = re.fullmatch(r"/gift\s+(\d+)", text, re.IGNORECASE)

        # /gift list — показать каталог
        if re.fullmatch(r"/gift\s+list", text, re.IGNORECASE):
            lines = ["Каталог подарков:\n"]
            for key, info in GIFTS_CATALOG.items():
                lines.append(f"  {key} — {info['title']} ({info['stars']} Stars)")
            lines.append("\nИспользование:")
            lines.append("  /gift bear 10 — 10 медведей")
            lines.append("  /gift bear us 10 — 10 медведей + rename")
            send_message(chat_id, "\n".join(lines))
            return

        gift_item = ""
        gift_amount = 0
        gift_rename = False
        gift_activations = 0
        gift_id = ""
        matched = False

        if match_gift_rename:
            gift_item = match_gift_rename.group(1).lower()
            gift_activations = int(match_gift_rename.group(2))
            gift_rename = True
            gift_amount = 1
            matched = True
        elif match_gift_item and not match_gift_item.group(1).isdigit():
            gift_item = match_gift_item.group(1).lower()
            gift_activations = int(match_gift_item.group(2))
            gift_amount = 1
            matched = True
        elif match_gift_plain:
            gift_activations = int(match_gift_plain.group(1))
            gift_rename = True
            matched = True

        if matched:
            # Проверяем предмет по каталогу
            if gift_item:
                cat = GIFTS_CATALOG.get(gift_item)
                if not cat:
                    available = ", ".join(GIFTS_CATALOG.keys())
                    send_message(chat_id, f"Предмет не найден: {gift_item}\n\nДоступные: {available}")
                    return
                gift_id = cat["id"]

            if gift_activations < 1 or gift_activations > 1000:
                send_message(chat_id, "От 1 до 1000 активаций.")
                return

            code = create_gift(gift_activations, gift_item, gift_amount, gift_rename, gift_id)
            link = f"https://t.me/kise?start={code}"
            gifts = load_gifts()
            used_count = len(gifts[code].get("used", []))

            desc_parts = []
            if gift_item:
                cat = GIFTS_CATALOG.get(gift_item, {})
                desc_parts.append(f"🎁 {cat.get('title', gift_item)} ({cat.get('stars', '?')} Stars)")
            if gift_rename:
                desc_parts.append("✨ Rename")
            if not desc_parts:
                desc_parts.append("✨ Rename")
            desc = "\n".join(desc_parts)

            send_message(chat_id, (
                f"Ссылка-подарок создана!\n\n"
                f"{desc}\n"
                f"Активаций: {gift_activations}\n"
                f"Использовано: {used_count}\n\n"
                f"Ссылка:\n{link}"
            ))
            return

        # /gifts — список созданных подарков
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
                    cat = GIFTS_CATALOG.get(item, {})
                    tags.append(cat.get("title", item))
                if rename:
                    tags.append("rename")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"{code}{tag_str} — {used}/{mx}")
            send_message(chat_id, "\n".join(lines))
            return

        # /promo CODE [ITEM] [us] N — создать промокод
        # Форматы:
        #   /promo START 10            — 10 активаций (только rename)
        #   /promo BEAR bear 10        — 10 медведей
        #   /promo BEAR bear us 10     — 10 медведей + rename
        #   /promo ROSE rose us 5       — 5 роз + rename
        match_promo_rename = re.fullmatch(r"/promo\s+(\S+)\s+(\S+)\s+us\s+(\d+)", text, re.IGNORECASE)
        match_promo_item = re.fullmatch(r"/promo\s+(\S+)\s+(\S+)\s+(\d+)", text, re.IGNORECASE)
        match_promo_plain = re.fullmatch(r"/promo\s+(\S+)\s+(\d+)", text, re.IGNORECASE)

        promo_code = ""
        promo_item = ""
        promo_rename = False
        promo_activations = 0
        promo_gift_id = ""
        promo_matched = False

        if match_promo_rename:
            promo_code = match_promo_rename.group(1).upper()
            promo_item = match_promo_rename.group(2).lower()
            promo_activations = int(match_promo_rename.group(3))
            promo_rename = True
            promo_matched = True
        elif match_promo_item and not match_promo_item.group(2).isdigit():
            promo_code = match_promo_item.group(1).upper()
            promo_item = match_promo_item.group(2).lower()
            promo_activations = int(match_promo_item.group(3))
            promo_matched = True
        elif match_promo_plain:
            promo_code = match_promo_plain.group(1).upper()
            promo_activations = int(match_promo_plain.group(2))
            promo_rename = True
            promo_matched = True

        if promo_matched:
            if promo_item:
                cat = GIFTS_CATALOG.get(promo_item)
                if not cat:
                    available = ", ".join(GIFTS_CATALOG.keys())
                    send_message(chat_id, f"Предмет не найден: {promo_item}\n\nДоступные: {available}")
                    return
                promo_gift_id = cat["id"]

            if promo_activations < 1 or promo_activations > 10000:
                send_message(chat_id, "От 1 до 10000 активаций.")
                return

            ok = create_promo(promo_code, promo_activations, promo_item, promo_rename, promo_gift_id)
            if not ok:
                send_message(chat_id, f"Промокод {promo_code} уже существует.")
                return

            desc_parts = []
            if promo_item:
                cat = GIFTS_CATALOG.get(promo_item, {})
                desc_parts.append(f"🎁 {cat.get('title', promo_item)} ({cat.get('stars', '?')} Stars)")
            if promo_rename:
                desc_parts.append("✨ Rename")
            if not desc_parts:
                desc_parts.append("✨ Rename")
            desc = "\n".join(desc_parts)

            send_message(chat_id, (
                f"Промокод создан!\n\n"
                f"Код: {promo_code}\n"
                f"{desc}\n"
                f"Активаций: {promo_activations}\n\n"
                f"Юзерам: /redeem {promo_code}"
            ))
            return

        # /promos — список промокодов
        if text == "/promos":
            promos = load_promos()
            if not promos:
                send_message(chat_id, "Нет промокодов.")
                return
            lines = ["Промокоды:\n"]
            for code, info in promos.items():
                used = len(info.get("used", []))
                mx = info.get("max", 0)
                item = info.get("item", "")
                rename = info.get("rename", False)
                tags = []
                if item:
                    cat = GIFTS_CATALOG.get(item, {})
                    tags.append(cat.get("title", item))
                if rename:
                    tags.append("rename")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"{code}{tag_str} — {used}/{mx}")
            send_message(chat_id, "\n".join(lines))
            return

        # /delpromo CODE — удалить промокод
        match_delpromo = re.fullmatch(r"/delpromo\s+(\S+)", text, re.IGNORECASE)
        if match_delpromo:
            pcode = match_delpromo.group(1).upper()
            promos = load_promos()
            if pcode not in promos:
                send_message(chat_id, f"Промокод {pcode} не найден.")
                return
            del promos[pcode]
            save_promos(promos)
            send_message(chat_id, f"Промокод {pcode} удалён.")
            return

        # /ban ID [причина] — бан пользователя
        match_ban = re.fullmatch(r"/ban\s+(\d+)(?:\s+(.+))?", text, re.IGNORECASE)
        if match_ban:
            ban_target = int(match_ban.group(1))
            ban_reason = match_ban.group(2) or ""
            if ban_target == ADMIN_ID:
                send_message(chat_id, "Нельзя забанить админа.")
                return
            if ban_user(ban_target, ban_reason):
                msg = f"Пользователь {ban_target} забанен."
                if ban_reason:
                    msg += f"\nПричина: {ban_reason}"
                send_message(chat_id, msg)
            else:
                send_message(chat_id, f"Пользователь {ban_target} уже забанен.")
            return

        # /unban ID — разбан
        match_unban = re.fullmatch(r"/unban\s+(\d+)", text, re.IGNORECASE)
        if match_unban:
            unban_target = int(match_unban.group(1))
            if unban_user(unban_target):
                send_message(chat_id, f"Пользователь {unban_target} разбанен.")
            else:
                send_message(chat_id, f"Пользователь {unban_target} не забанен.")
            return

        # /banlist — список банов
        if text == "/banlist":
            bans = load_bans()
            if not bans:
                send_message(chat_id, "Список банов пуст.")
                return
            lines = ["Баны:\n"]
            for uid, info in bans.items():
                reason = info.get("reason", "")
                ts = info.get("banned_at", 0)
                from datetime import datetime
                dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
                r = f" — {reason}" if reason else ""
                lines.append(f"{uid} [{dt}]{r}")
            send_message(chat_id, "\n".join(lines))
            return

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
# ОСНОВНОЙ БОТ: INLINE QUERY
# ============================================================

def handle_inline_query_main(inline_query):
    if not isinstance(inline_query, dict):
        return
    query_id = inline_query.get("id")
    if not query_id:
        return
    query = inline_query.get("query", "")
    if not isinstance(query, str):
        query = ""
    query = query.strip()

    user = inline_query.get("from") or {}
    user_id = user.get("id", 0)
    lang = get_lang(user_id) if user_id else "ru"

    if not query:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "help",
                "title": t(user_id, "inline_help_title"),
                "description": t(user_id, "inline_help_desc"),
                "input_message_content": {
                    "message_text": t(user_id, "inline_help"),
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
                "title": t(user_id, "inline_invalid_title"),
                "description": t(user_id, "inline_invalid_desc"),
                "input_message_content": {
                    "message_text": t(user_id, "inline_invalid_text"),
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
                "title": t(user_id, "inline_min_title"),
                "description": t(user_id, "inline_min_desc", min=MIN_AMOUNT),
                "input_message_content": {"message_text": t(user_id, "inline_min_desc", min=MIN_AMOUNT)},
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
                "title": t(user_id, "inline_max_title"),
                "description": t(user_id, "inline_max_desc", max=MAX_AMOUNT),
                "input_message_content": {"message_text": t(user_id, "inline_max_desc", max=MAX_AMOUNT)},
            }],
            "cache_time": 1,
            "is_personal": True,
        })
        return

    user_wallets = get_user_wallets(user_id) if user_id else []

    if not user_wallets:
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": [{
                "type": "article",
                "id": "no_wallet",
                "title": t(user_id, "inline_no_wallet_title"),
                "description": t(user_id, "inline_no_wallet_desc"),
                "input_message_content": {
                    "message_text": t(user_id, "inline_no_wallet_text"),
                },
            }],
            "cache_time": 30,
            "is_personal": True,
        })
        return

    if len(user_wallets) == 1:
        addr, wallet = user_wallets[0]
    else:
        results = []
        for addr, wallet in user_wallets:
            code = create_check(user_id, amount, addr)
            bot_username = wallet.get("username", "bot")
            name = wallet.get("name", "")
            display = f"{name} ({addr})" if name else addr
            url = f"https://t.me/{bot_username}?start={code}"
            wt = wallet.get("token", "")
            wbu = wallet.get("base_url", API_BASE)
            if wt:
                start_user_bot(address_hash(addr), wt, wbu)
                def _stop_multi(a=addr):
                    stop_user_bot(address_hash(a))
                timer = threading.Timer(300, _stop_multi)
                timer.daemon = True
                timer.start()
            results.append({
                "type": "article",
                "id": f"wallet_{addr}",
                "title": t(user_id, "inline_article_title", amount=amount) + f" → @{bot_username}",
                "description": f"{display}",
                "input_message_content": {
                    "message_text": t(user_id, "inline_article_text", amount=amount, display=display),
                },
                "reply_markup": {
                    "inline_keyboard": [[
                        {"text": t(user_id, "inline_pay_btn", amount=amount), "url": url},
                    ]],
                },
            })
        telegram("answerInlineQuery", {
            "inline_query_id": query_id,
            "results": results,
            "cache_time": 1,
            "is_personal": True,
        })
        return

    addr, wallet = user_wallets[0]
    bot_username = wallet.get("username", "bot")
    name = wallet.get("name", "")
    display = f"{name} ({addr})" if name else addr
    code = create_check(user_id, amount, addr)
    url = f"https://t.me/{bot_username}?start={code}"
    token = wallet.get("token", "")
    base_url = wallet.get("base_url", API_BASE)

    if token:
        start_user_bot(address_hash(addr), token, base_url)
        def _stop_after_timeout():
            stop_user_bot(address_hash(addr))
        timer = threading.Timer(300, _stop_after_timeout)
        timer.daemon = True
        timer.start()

    result = {
        "type": "article",
        "id": secrets.token_hex(8),
        "title": t(user_id, "inline_article_title", amount=amount),
        "description": t(user_id, "inline_article_desc", bot=bot_username),
        "input_message_content": {
            "message_text": t(user_id, "inline_article_text", amount=amount, display=display),
        },
        "reply_markup": {
            "inline_keyboard": [[
                {"text": t(user_id, "inline_pay_btn", amount=amount), "url": url},
            ]],
        },
    }

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

    callback = update.get("callback_query")
    if isinstance(callback, dict):
        try:
            handle_callback_query(callback)
        except Exception as e:
            print(f"Callback error: {e!r}")
        return

    inline_query = update.get("inline_query")
    if isinstance(inline_query, dict):
        try:
            handle_inline_query_main(inline_query)
        except Exception as e:
            print(f"Inline error: {e!r}")
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
