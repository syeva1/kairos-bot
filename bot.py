#!/usr/bin/env python3
"""KAIROS Bot — Telegram напоминалка с Todoist интеграцией"""

import json
import urllib.request
import urllib.parse
import time
import os
import re
from datetime import date, timedelta

TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN", "")
TG_BOT = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
TODOIST_API = "https://api.todoist.com/api/v1"
TG_API = f"https://api.telegram.org/bot{TG_BOT}"

REMINDER_HOURS = list(range(8, 23))  # 8:00 — 22:00
REMINDER_INTERVAL = 3600  # каждый час


def tg_send(text, buttons=None, chat_id=TG_CHAT):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{TG_API}/sendMessage", data=body,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def tg_edit(chat_id, message_id, text, buttons=None):
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{TG_API}/editMessageText", data=body,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except:
        pass


def tg_answer_callback(callback_id, text=""):
    data = {"callback_query_id": callback_id, "text": text}
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{TG_API}/answerCallbackQuery", data=body,
        headers={"Content-Type": "application/json"}
    )
    try:
        urllib.request.urlopen(req)
    except:
        pass


def todoist_get_tasks():
    req = urllib.request.Request(
        f"{TODOIST_API}/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())

    today = str(date.today())
    tasks = data.get("results", [])
    result = {"overdue": [], "today": []}

    for t in tasks:
        due = t.get("due")
        if due and due.get("date"):
            d = due["date"]
            if not t.get("is_completed"):
                if d < today:
                    result["overdue"].append({"id": t["id"], "content": t["content"], "priority": t.get("priority", 1)})
                elif d == today:
                    result["today"].append({"id": t["id"], "content": t["content"], "priority": t.get("priority", 1)})
    return result


def todoist_close(task_id):
    req = urllib.request.Request(
        f"{TODOIST_API}/tasks/{task_id}/close", method="POST",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )
    urllib.request.urlopen(req)


def todoist_create(content, due_date=None, priority=1):
    data = {"content": content, "priority": priority}
    if due_date:
        data["due_date"] = due_date
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{TODOIST_API}/tasks", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {TODOIST_TOKEN}",
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪️"}


def build_task_message(tasks):
    lines = ["🧠 <b>KAIROS</b>\n"]
    buttons = []

    if tasks["overdue"]:
        lines.append("🔴 <b>ПРОСРОЧЕНО:</b>\n")
        for t in tasks["overdue"]:
            emoji = PRIORITY_EMOJI.get(t["priority"], "⚪️")
            lines.append(f"  {emoji} {t['content']}")
            buttons.append([
                {"text": f"✅ {t['content'][:30]}", "callback_data": f"done:{t['id']}"}
            ])
        lines.append("")

    if tasks["today"]:
        lines.append("🟡 <b>СЕГОДНЯ:</b>\n")
        for t in tasks["today"]:
            emoji = PRIORITY_EMOJI.get(t["priority"], "⚪️")
            lines.append(f"  {emoji} {t['content']}")
            buttons.append([
                {"text": f"✅ {t['content'][:30]}", "callback_data": f"done:{t['id']}"}
            ])

    buttons.append([
        {"text": "➕ Новая задача", "callback_data": "new_task"},
        {"text": "🔄 Обновить", "callback_data": "refresh"}
    ])

    return "\n".join(lines), buttons


def send_task_list():
    tasks = todoist_get_tasks()
    if not tasks["overdue"] and not tasks["today"]:
        tg_send("✅ <b>Все задачи выполнены!</b>\n\nТак держать 💪")
        return
    text, buttons = build_task_message(tasks)
    tg_send(text, buttons)


def refresh_message(chat_id, msg_id):
    tasks = todoist_get_tasks()
    if not tasks["overdue"] and not tasks["today"]:
        tg_edit(chat_id, msg_id, "✅ <b>Все задачи выполнены!</b>\n\nТак держать 💪")
    else:
        text, buttons = build_task_message(tasks)
        tg_edit(chat_id, msg_id, text, buttons)


def get_updates(offset=None):
    url = f"{TG_API}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=35) as r:
            return json.loads(r.read())
    except:
        return {"ok": False, "result": []}


waiting_for_task = {}


def handle_update(update):
    global waiting_for_task

    if "callback_query" in update:
        cb = update["callback_query"]
        cb_data = cb["data"]
        cb_id = cb["id"]
        chat_id = cb["message"]["chat"]["id"]
        msg_id = cb["message"]["message_id"]

        if cb_data.startswith("done:"):
            task_id = cb_data.split(":")[1]
            try:
                todoist_close(task_id)
                tg_answer_callback(cb_id, "✅ Выполнено!")
                refresh_message(chat_id, msg_id)
            except Exception as e:
                tg_answer_callback(cb_id, f"Ошибка: {e}")

        elif cb_data == "new_task":
            tg_answer_callback(cb_id, "")
            tg_send(
                "📝 <b>Напиши задачу:</b>\n\n"
                "Просто отправь текст. Можно добавить дату:\n"
                "<i>Купить молоко завтра</i>\n"
                "<i>Сдать отчёт 2026-04-05</i>",
                chat_id=chat_id
            )
            waiting_for_task[chat_id] = True

        elif cb_data == "refresh":
            tg_answer_callback(cb_id, "🔄 Обновляю...")
            refresh_message(chat_id, msg_id)

        elif cb_data.startswith("priority:"):
            parts = cb_data.split(":", 3)
            if len(parts) == 4:
                _, task_text_encoded, due, level = parts
                task_text = urllib.parse.unquote(task_text_encoded)
                due_date = due if due != "none" else None
                try:
                    todoist_create(task_text, due_date, int(level))
                    tg_answer_callback(cb_id, "✅ Задача создана!")
                    tg_edit(chat_id, msg_id, f"✅ <b>Создано:</b> {task_text}")
                except Exception as e:
                    tg_answer_callback(cb_id, f"Ошибка: {e}")

    elif "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        if text == "/start":
            tg_send(
                "🧠 <b>KAIROS запущен!</b>\n\n"
                "Я напоминаю о задачах из Todoist каждый час.\n\n"
                "Команды:\n"
                "/tasks — показать задачи\n"
                "/new — создать задачу",
                chat_id=chat_id
            )

        elif text == "/tasks":
            send_task_list()

        elif text == "/new":
            tg_send("📝 <b>Напиши задачу:</b>", chat_id=chat_id)
            waiting_for_task[chat_id] = True

        elif chat_id in waiting_for_task:
            del waiting_for_task[chat_id]
            due_date = None
            task_text = text

            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
            if date_match:
                due_date = date_match.group(1)
                task_text = text.replace(due_date, "").strip()
            elif "завтра" in text.lower():
                due_date = str(date.today() + timedelta(days=1))
                task_text = re.sub(r'(?i)завтра', '', text).strip()
            elif "сегодня" in text.lower():
                due_date = str(date.today())
                task_text = re.sub(r'(?i)сегодня', '', text).strip()
            else:
                due_date = str(date.today())

            if not task_text:
                task_text = text

            encoded = urllib.parse.quote(task_text)[:60]
            d = due_date or "none"
            buttons = [[
                {"text": "⚪️ Обычный", "callback_data": f"priority:{encoded}:{d}:1"},
                {"text": "🟡 Средний", "callback_data": f"priority:{encoded}:{d}:2"},
            ], [
                {"text": "🟠 Высокий", "callback_data": f"priority:{encoded}:{d}:3"},
                {"text": "🔴 Срочный", "callback_data": f"priority:{encoded}:{d}:4"},
            ]]

            date_text = f" (до {due_date})" if due_date else ""
            tg_send(
                f"📋 <b>{task_text}</b>{date_text}\n\nВыбери приоритет:",
                buttons, chat_id=chat_id
            )


def main():
    print("🧠 KAIROS Bot запущен!")
    offset = None
    last_reminder = 0

    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok"):
                for u in updates["result"]:
                    offset = u["update_id"] + 1
                    handle_update(u)

            now = time.time()
            hour = int(time.strftime("%H"))
            if now - last_reminder >= REMINDER_INTERVAL and hour in REMINDER_HOURS:
                last_reminder = now
                try:
                    send_task_list()
                except Exception as e:
                    print(f"Reminder error: {e}")

        except KeyboardInterrupt:
            print("\n👋 KAIROS остановлен")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
