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

REMINDER_HOURS = list(range(8, 23))
REMINDER_INTERVAL = 3600

PRIORITY_EMOJI = {4: "🔴", 3: "🟠", 2: "🟡", 1: "⚪️"}
PROJECT_EMOJI = {"ИИ": "🤖", "Саморазвитие": "📚", "Работа": "💼", "Inbox": "📥"}


def tg_send(text, buttons=None, chat_id=TG_CHAT):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TG_API}/sendMessage", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def tg_edit(chat_id, message_id, text, buttons=None):
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        data["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TG_API}/editMessageText", data=body,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except:
        pass


def tg_answer_callback(callback_id, text=""):
    data = {"callback_query_id": callback_id, "text": text}
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TG_API}/answerCallbackQuery", data=body,
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
    except:
        pass


def todoist_get_projects():
    req = urllib.request.Request(f"{TODOIST_API}/projects",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    if isinstance(data, dict):
        return data.get("results", [])
    return data


def todoist_get_tasks(project_id=None):
    url = f"{TODOIST_API}/tasks"
    if project_id:
        url += f"?project_id={project_id}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    return data.get("results", [])


def todoist_get_urgent_tasks():
    req = urllib.request.Request(f"{TODOIST_API}/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
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
                item = {"id": t["id"], "content": t["content"],
                        "priority": t.get("priority", 1), "project_id": t.get("project_id", "")}
                if d < today:
                    result["overdue"].append(item)
                elif d == today:
                    result["today"].append(item)
    return result


def todoist_close(task_id):
    req = urllib.request.Request(f"{TODOIST_API}/tasks/{task_id}/close", method="POST",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
    urllib.request.urlopen(req)


def todoist_create(content, due_date=None, priority=1, project_id=None):
    data = {"content": content, "priority": priority}
    if due_date:
        data["due_date"] = due_date
    if project_id:
        data["project_id"] = project_id
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TODOIST_API}/tasks", data=body, method="POST",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# --- Главное меню ---

def send_main_menu(chat_id=TG_CHAT):
    try:
        projects = todoist_get_projects()
        urgent = todoist_get_urgent_tasks()
        urgent_count = len(urgent["overdue"]) + len(urgent["today"])
    except:
        projects = []
        urgent_count = 0

    lines = ["🧠 <b>KAIROS</b>\n"]

    if urgent_count > 0:
        lines.append(f"⚠️ <b>{urgent_count} срочных задач</b>\n")
    else:
        lines.append("✅ Нет срочных задач\n")

    lines.append("Выбери проект:")

    buttons = []
    for p in projects:
        emoji = PROJECT_EMOJI.get(p["name"], "📁")
        buttons.append([{"text": f"{emoji} {p['name']}", "callback_data": f"project:{p['id']}"}])

    buttons.append([
        {"text": "🔥 Срочные", "callback_data": "urgent"},
        {"text": "➕ Новая задача", "callback_data": "new_task"}
    ])

    tg_send("\n".join(lines), buttons, chat_id)


# --- Список задач проекта ---

def send_project_tasks(chat_id, msg_id, project_id, project_name=""):
    try:
        all_tasks = todoist_get_tasks(project_id)
    except Exception as e:
        tg_edit(chat_id, msg_id, f"Ошибка: {e}")
        return

    tasks = [t for t in all_tasks if not t.get("is_completed")]

    emoji = PROJECT_EMOJI.get(project_name, "📁")
    lines = [f"{emoji} <b>{project_name}</b>\n"]
    buttons = []

    if not tasks:
        lines.append("Задач нет 🎉")
    else:
        for t in tasks:
            p_emoji = PRIORITY_EMOJI.get(t.get("priority", 1), "⚪️")
            due = t.get("due")
            date_str = ""
            if due and due.get("date"):
                d = due["date"]
                today = str(date.today())
                if d < today:
                    date_str = f" <i>(просрочено!)</i>"
                elif d == today:
                    date_str = f" <i>(сегодня)</i>"
                else:
                    date_str = f" <i>(до {d})</i>"
            lines.append(f"  {p_emoji} {t['content']}{date_str}")
            buttons.append([
                {"text": f"✅ {t['content'][:30]}", "callback_data": f"done:{t['id']}:{project_id}"}
            ])

    buttons.append([
        {"text": f"➕ Добавить в {project_name}", "callback_data": f"new_in:{project_id}:{project_name}"},
    ])
    buttons.append([
        {"text": "⬅️ Назад", "callback_data": "back_menu"},
        {"text": "🔄 Обновить", "callback_data": f"project:{project_id}"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Срочные задачи ---

def send_urgent(chat_id, msg_id):
    try:
        tasks = todoist_get_urgent_tasks()
        projects = {p["id"]: p["name"] for p in todoist_get_projects()}
    except Exception as e:
        tg_edit(chat_id, msg_id, f"Ошибка: {e}")
        return

    lines = ["🔥 <b>Срочные задачи</b>\n"]
    buttons = []

    if tasks["overdue"]:
        lines.append("🔴 <b>ПРОСРОЧЕНО:</b>\n")
        for t in tasks["overdue"]:
            p_emoji = PRIORITY_EMOJI.get(t["priority"], "⚪️")
            proj = projects.get(t["project_id"], "")
            proj_str = f" [{proj}]" if proj else ""
            lines.append(f"  {p_emoji} {t['content']}{proj_str}")
            buttons.append([{"text": f"✅ {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])
        lines.append("")

    if tasks["today"]:
        lines.append("🟡 <b>СЕГОДНЯ:</b>\n")
        for t in tasks["today"]:
            p_emoji = PRIORITY_EMOJI.get(t["priority"], "⚪️")
            proj = projects.get(t["project_id"], "")
            proj_str = f" [{proj}]" if proj else ""
            lines.append(f"  {p_emoji} {t['content']}{proj_str}")
            buttons.append([{"text": f"✅ {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])

    if not tasks["overdue"] and not tasks["today"]:
        lines.append("✅ Всё выполнено! 💪")

    buttons.append([
        {"text": "⬅️ Назад", "callback_data": "back_menu"},
        {"text": "🔄 Обновить", "callback_data": "urgent"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Напоминание (каждый час) ---

def send_reminder():
    tasks = todoist_get_urgent_tasks()
    if not tasks["overdue"] and not tasks["today"]:
        return

    count = len(tasks["overdue"]) + len(tasks["today"])
    lines = [f"🧠 <b>KAIROS — {time.strftime('%H:%M')}</b>\n"]

    if tasks["overdue"]:
        lines.append("🔴 <b>ПРОСРОЧЕНО:</b>\n")
        for t in tasks["overdue"]:
            lines.append(f"  • {t['content']}")
        lines.append("")

    if tasks["today"]:
        lines.append("🟡 <b>СЕГОДНЯ:</b>\n")
        for t in tasks["today"]:
            lines.append(f"  • {t['content']}")

    buttons = [[
        {"text": "📋 Открыть KAIROS", "callback_data": "open_menu"},
    ]]

    tg_send("\n".join(lines), buttons)


# --- Кеш проектов ---
project_cache = {}

def get_project_name(project_id):
    global project_cache
    if not project_cache:
        try:
            projects = todoist_get_projects()
            project_cache = {p["id"]: p["name"] for p in projects}
        except:
            pass
    return project_cache.get(project_id, "Проект")


# --- Обработка ---

def get_updates(offset=None):
    url = f"{TG_API}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        req = urllib.request.Request(url)
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

        if cb_data.startswith("project:"):
            project_id = cb_data.split(":")[1]
            tg_answer_callback(cb_id)
            name = get_project_name(project_id)
            send_project_tasks(chat_id, msg_id, project_id, name)

        elif cb_data == "urgent":
            tg_answer_callback(cb_id)
            send_urgent(chat_id, msg_id)

        elif cb_data == "back_menu" or cb_data == "open_menu":
            tg_answer_callback(cb_id)
            # Пересобираем меню в текущем сообщении
            try:
                projects = todoist_get_projects()
                urgent = todoist_get_urgent_tasks()
                urgent_count = len(urgent["overdue"]) + len(urgent["today"])
            except:
                projects = []
                urgent_count = 0
            lines = ["🧠 <b>KAIROS</b>\n"]
            if urgent_count > 0:
                lines.append(f"⚠️ <b>{urgent_count} срочных задач</b>\n")
            else:
                lines.append("✅ Нет срочных задач\n")
            lines.append("Выбери проект:")
            buttons = []
            for p in projects:
                emoji = PROJECT_EMOJI.get(p["name"], "📁")
                buttons.append([{"text": f"{emoji} {p['name']}", "callback_data": f"project:{p['id']}"}])
            buttons.append([
                {"text": "🔥 Срочные", "callback_data": "urgent"},
                {"text": "➕ Новая задача", "callback_data": "new_task"}
            ])
            tg_edit(chat_id, msg_id, "\n".join(lines), buttons)

        elif cb_data.startswith("done:"):
            parts = cb_data.split(":")
            task_id = parts[1]
            source = parts[2] if len(parts) > 2 else ""
            try:
                todoist_close(task_id)
                tg_answer_callback(cb_id, "✅ Выполнено!")
                if source == "urgent":
                    send_urgent(chat_id, msg_id)
                elif source:
                    name = get_project_name(source)
                    send_project_tasks(chat_id, msg_id, source, name)
            except Exception as e:
                tg_answer_callback(cb_id, f"Ошибка: {e}")

        elif cb_data == "new_task":
            tg_answer_callback(cb_id)
            # Выбор проекта для новой задачи
            try:
                projects = todoist_get_projects()
            except:
                projects = []
            buttons = []
            for p in projects:
                emoji = PROJECT_EMOJI.get(p["name"], "📁")
                buttons.append([{"text": f"{emoji} {p['name']}", "callback_data": f"new_in:{p['id']}:{p['name']}"}])
            tg_edit(chat_id, msg_id, "📝 <b>В какой проект добавить?</b>", buttons)

        elif cb_data.startswith("new_in:"):
            parts = cb_data.split(":", 2)
            project_id = parts[1]
            project_name = parts[2] if len(parts) > 2 else ""
            tg_answer_callback(cb_id)
            tg_send(f"📝 <b>Напиши задачу для {project_name}:</b>\n\n"
                    f"Можно добавить дату:\n"
                    f"<i>Купить молоко завтра</i>\n"
                    f"<i>Сдать отчёт 2026-04-05</i>", chat_id=chat_id)
            waiting_for_task[chat_id] = {"project_id": project_id, "project_name": project_name}

        elif cb_data.startswith("priority:"):
            parts = cb_data.split(":", 4)
            if len(parts) == 5:
                _, task_encoded, due, level, proj_id = parts
                task_text = urllib.parse.unquote(task_encoded)
                due_date = due if due != "none" else None
                proj = proj_id if proj_id != "none" else None
                try:
                    todoist_create(task_text, due_date, int(level), proj)
                    tg_answer_callback(cb_id, "✅ Задача создана!")
                    tg_edit(chat_id, msg_id, f"✅ <b>Создано:</b> {task_text}")
                except Exception as e:
                    tg_answer_callback(cb_id, f"Ошибка: {e}")

    elif "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        if text == "/start" or text == "/menu":
            send_main_menu(chat_id)

        elif text == "/tasks":
            send_main_menu(chat_id)

        elif text == "/new":
            tg_send("📝 <b>Напиши задачу:</b>", chat_id=chat_id)
            waiting_for_task[chat_id] = {"project_id": None, "project_name": ""}

        elif chat_id in waiting_for_task:
            info = waiting_for_task.pop(chat_id)
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

            encoded = urllib.parse.quote(task_text)[:50]
            d = due_date or "none"
            proj = info.get("project_id") or "none"
            buttons = [[
                {"text": "⚪️ Обычный", "callback_data": f"priority:{encoded}:{d}:1:{proj}"},
                {"text": "🟡 Средний", "callback_data": f"priority:{encoded}:{d}:2:{proj}"},
            ], [
                {"text": "🟠 Высокий", "callback_data": f"priority:{encoded}:{d}:3:{proj}"},
                {"text": "🔴 Срочный", "callback_data": f"priority:{encoded}:{d}:4:{proj}"},
            ]]

            proj_name = info.get("project_name", "")
            proj_str = f" → {proj_name}" if proj_name else ""
            date_text = f" (до {due_date})" if due_date else ""
            tg_send(f"📋 <b>{task_text}</b>{date_text}{proj_str}\n\nВыбери приоритет:",
                    buttons, chat_id=chat_id)


def main():
    print("🧠 KAIROS Bot запущен!")
    offset = None
    last_reminder = 0

    # Отправляем меню при старте
    try:
        send_main_menu()
    except Exception as e:
        print(f"Start error: {e}")

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
                    send_reminder()
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
