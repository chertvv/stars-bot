#!/usr/bin/env python3
"""Snitch Bot — пересылает удалённые/изменённые сообщения и одноразовые фото в ЛС админу.

Формат:
  ✏️ Name (@username) отредактировал сообщение.
  Старый текст
  ⇩⇩⇩
  Новый текст

  🗑 Это сообщение было удалено
  Name (@username)
  Текст сообщения
"""

import json
import os
import time
from collections import OrderedDict
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN", "600000000129:PTMn3Ml8rgWPvfwtlzGvPuJWGPy7iOeEkDeGaCEhoX8")
API_BASE = os.environ.get("API_BASE", "https://dev-angel-7553.dev")
API = f"{API_BASE}/bot{BOT_TOKEN}"

ADMIN_ID = int(os.environ.get("ADMIN_ID", "2022001"))

CACHE_FILE = Path(__file__).parent / "snitch_cache.json"
CACHE_MAX = 10000

session = requests.Session()


def telegram(method, data=None, timeout=15):
    try:
        if data:
            r = session.post(f"{API}/{method}", data=data, timeout=timeout)
        else:
            r = session.post(f"{API}/{method}", timeout=timeout)
        r.raise_for_status()
        if not r.text:
            return None
        return r.json()
    except Exception as e:
        print(f"API [{method}]: {e!r}")
        return None


def send_message(chat_id, text):
    return telegram("sendMessage", {"chat_id": chat_id, "text": text})


def forward_message(chat_id, from_chat_id, message_id):
    return telegram("forwardMessage", {
        "chat_id": chat_id,
        "from_chat_id": from_chat_id,
        "message_id": message_id,
    })


# ============================================================
# MESSAGE CACHE (LRU) — храним последние сообщения для edit/delete
# ============================================================

def load_cache() -> OrderedDict:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            return OrderedDict(data)
        except (json.JSONDecodeError, OSError):
            return OrderedDict()
    return OrderedDict()


def save_cache(cache: OrderedDict) -> None:
    try:
        while len(cache) > CACHE_MAX:
            cache.popitem(last=False)
        CACHE_FILE.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"save_cache: {e!r}")


def ckey(chat_id: int, message_id: int) -> str:
    return f"{chat_id}_{message_id}"


def cache_msg(message: dict) -> None:
    if not isinstance(message, dict):
        return
    chat = message.get("chat", {})
    chat_id = chat.get("id", 0)
    msg_id = message.get("message_id", 0)
    if not chat_id or not msg_id:
        return

    from_info = message.get("from", {})
    key = ckey(chat_id, msg_id)
    cache = load_cache()

    entry = {
        "chat_id": chat_id,
        "message_id": msg_id,
        "from_id": from_info.get("id", 0),
        "from_name": from_info.get("first_name") or from_info.get("username", "?"),
        "from_username": from_info.get("username", ""),
        "text": message.get("text", ""),
        "caption": message.get("caption", ""),
        "has_media": bool(
            message.get("photo") or message.get("video") or message.get("voice")
            or message.get("audio") or message.get("document") or message.get("sticker")
            or message.get("animation") or message.get("video_note")
        ),
        "timestamp": int(time.time()),
    }
    cache[key] = entry
    cache.move_to_end(key)
    save_cache(cache)


def get_cached(chat_id: int, message_id: int) -> dict | None:
    return load_cache().get(ckey(chat_id, message_id))


def pop_cached(chat_id: int, message_id: int) -> dict | None:
    cache = load_cache()
    entry = cache.pop(ckey(chat_id, message_id), None)
    save_cache(cache)
    return entry


def update_cached(chat_id: int, message_id: int, new_text: str) -> tuple[str, dict] | None:
    cache = load_cache()
    key = ckey(chat_id, message_id)
    entry = cache.get(key)
    if not entry:
        return None
    old_text = entry.get("text", "")
    entry["text"] = new_text
    cache[key] = entry
    cache.move_to_end(key)
    save_cache(cache)
    return old_text, entry


# ============================================================
# FORMAT
# ============================================================

def user_display(entry: dict) -> str:
    name = entry.get("from_name", "?")
    uname = entry.get("from_username", "")
    if uname:
        return f"{name} (@{uname})"
    return name


# ============================================================
# HANDLERS
# ============================================================

def handle_deleted(chat_id: int, message_ids: list[int]):
    for mid in message_ids:
        entry = pop_cached(chat_id, mid)
        if entry:
            display = user_display(entry)
            text = entry.get("text", "") or entry.get("caption", "")
            msg = f"🗑 Это сообщение было удалено\n{display}\n"
            if text:
                msg += text
            send_message(ADMIN_ID, msg)

            if entry.get("has_media"):
                result = forward_message(ADMIN_ID, chat_id, mid)
                if not result or not result.get("ok"):
                    send_message(ADMIN_ID, f"(медиа недоступно)")
        else:
            send_message(ADMIN_ID, f"🗑 Удалено сообщение {mid} в чате {chat_id} (не в кэше)")


def handle_edited(message: dict):
    chat = message.get("chat", {})
    chat_id = chat.get("id", 0)
    msg_id = message.get("message_id", 0)
    new_text = message.get("text", "") or message.get("caption", "")

    from_info = message.get("from", {})
    name = from_info.get("first_name") or from_info.get("username", "?")
    uname = from_info.get("username", "")

    result = update_cached(chat_id, msg_id, new_text)
    if result:
        old_text, entry = result
        display = user_display(entry)
    else:
        old_text = "(не в кэше)"
        display = f"{name} (@{uname})" if uname else name

    display_new = f"{name} (@{uname})" if uname else name

    msg = f"✏️ {display} отредактировал сообщение.\n{old_text}\n⇩⇩⇩\n{new_text}"
    send_message(ADMIN_ID, msg)


def handle_view_once(message: dict):
    chat = message.get("chat", {})
    chat_id = chat.get("id", 0)
    msg_id = message.get("message_id", 0)
    from_info = message.get("from", {})
    name = from_info.get("first_name") or from_info.get("username", "?")
    uname = from_info.get("username", "")
    display = f"{name} (@{uname})" if uname else name

    photo = message.get("photo", [])
    if not photo:
        return

    biggest = photo[-1] if isinstance(photo, list) else photo
    file_id = biggest.get("file_id", "")

    send_message(ADMIN_ID, f"👁 Одноразовое фото от {display}")

    if file_id:
        file_info = get_file(file_id)
        if file_info and file_info.get("ok"):
            file_path = file_info["result"]["file_path"]
            file_url = f"{API_BASE}/file/bot{BOT_TOKEN}/{file_path}"
            try:
                r = session.get(file_url, timeout=30)
                if r.status_code == 200:
                    import io
                    url = f"{API}/sendPhoto"
                    files = {"photo": ("photo.jpg", io.BytesIO(r.content), "image/jpeg")}
                    data = {"chat_id": ADMIN_ID, "caption": f"👁 от {display}"}
                    session.post(url, files=files, data=data, timeout=30)
                    return
            except Exception as e:
                print(f"download view-once: {e!r}")
        send_photo_by_id(ADMIN_ID, file_id, caption=f"👁 от {display}")


# ============================================================
# MAIN
# ============================================================

def process_update(update: dict):
    message = update.get("message") or update.get("channel_post")
    if isinstance(message, dict):
        cache_msg(message)
        return

    edited = update.get("edited_message") or update.get("edited_channel_post")
    if isinstance(edited, dict):
        handle_edited(edited)
        return

    deleted = update.get("deleted_messages")
    if isinstance(deleted, dict):
        chat_id = deleted.get("chat", {}).get("id", 0)
        msg_ids = deleted.get("message_ids", [])
        handle_deleted(chat_id, msg_ids)
        return


def main():
    print("=" * 40, flush=True)
    print("SNITCH BOT", flush=True)
    print("=" * 40, flush=True)

    result = telegram("getMe", timeout=10)
    if not isinstance(result, dict) or result.get("ok") is not True:
        print("ERROR: getMe failed:", result)
        return

    bot = result.get("result", {})
    username = bot.get("username", "unknown")
    print(f"Bot: @{username}", flush=True)
    print(f"Admin: {ADMIN_ID}", flush=True)
    print(flush=True)

    send_message(ADMIN_ID, f"👁 Snitch Bot @{username} запущен")

    offset = 0

    while True:
        try:
            result = telegram("getUpdates", {
                "offset": offset,
                "timeout": 5,
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
                    process_update(update)
                except Exception as e:
                    print(f"Update error: {e!r}")

        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Main loop: {e!r}")
            time.sleep(2)


if __name__ == "__main__":
    main()
