#!/usr/bin/env python3
"""KAIROS Bot — Telegram напоминалка с Todoist интеграцией
Меню проектов + умные напоминания: 24ч → 6ч → 3ч → 1.5ч → 1ч → 30мин → 15мин"""

import json
import urllib.request
import urllib.parse
import time
import os
import re
from datetime import date, datetime, timedelta, timezone

TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN", "")
TG_BOT = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "5"))  # Челябинск UTC+5
TODOIST_API = "https://api.todoist.com/api/v1"
TG_API = f"https://api.telegram.org/bot{TG_BOT}"

# Интервалы напоминаний (часы до дедлайна)
REMIND_AT_HOURS = [24, 12, 6, 3, 1.5, 1, 0.5, 0.25]

STATE_FILE = os.environ.get("STATE_FILE", "/tmp/reminder_state.json")


def local_now():
    tz = timezone(timedelta(hours=TZ_OFFSET))
    return datetime.now(tz)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE) if os.path.dirname(STATE_FILE) else ".", exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Telegram ---

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


# --- Todoist ---

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


def todoist_get_all_tasks_with_time():
    """Все задачи с расчётом времени до дедлайна"""
    req = urllib.request.Request(f"{TODOIST_API}/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"})
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())

    tasks = data.get("results", [])
    now = local_now()
    result = []

    for t in tasks:
        if t.get("is_completed"):
            continue
        due = t.get("due")
        if not due:
            continue

        due_datetime_str = due.get("datetime", "")
        due_date_str = due.get("date", "")

        if due_datetime_str:
            try:
                dt = datetime.fromisoformat(due_datetime_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=TZ_OFFSET)))
            except:
                continue
        elif due_date_str:
            try:
                d = date.fromisoformat(due_date_str)
                tz = timezone(timedelta(hours=TZ_OFFSET))
                dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
            except:
                continue
        else:
            continue

        hours_left = (dt - now).total_seconds() / 3600
        result.append({
            "id": t["id"],
            "content": t["content"],
            "priority": t.get("priority", 1),
            "project_id": t.get("project_id", ""),
            "hours_left": hours_left,
            "is_overdue": hours_left < 0,
            "due_date": due_date_str,
        })

    return result


def todoist_get_urgent_tasks():
    today = str(date.today())
    all_tasks = todoist_get_tasks()
    result = {"overdue": [], "today": []}
    for t in all_tasks:
        due = t.get("due")
        if due and due.get("date") and not t.get("is_completed"):
            d = due["date"]
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


# --- Helpers ---

def format_time_left(hours):
    if hours <= 0:
        return "просрочено"
    if hours < 1:
        return f"{int(hours * 60)} мин"
    if hours < 24:
        h = int(hours)
        m = int((hours - h) * 60)
        return f"{h}ч {m}мин" if m else f"{h}ч"
    days = int(hours / 24)
    return f"{days}д"


# Кеш проектов
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


# --- Главное меню ---

def send_main_menu(chat_id=TG_CHAT):
    try:
        projects = todoist_get_projects()
        urgent = todoist_get_urgent_tasks()
        urgent_count = len(urgent["overdue"]) + len(urgent["today"])
    except:
        projects = []
        urgent_count = 0

    lines = ["<b>KAIROS</b>\n"]
    if urgent_count > 0:
        lines.append(f"{urgent_count} срочных задач\n")
    else:
        lines.append("Нет срочных задач\n")
    lines.append("Выбери проект:")

    buttons = []
    for p in projects:
        name = f"📥 {p['name']}" if p["name"] == "Inbox" else p["name"]
        buttons.append([{"text": name, "callback_data": f"project:{p['id']}"}])

    buttons.append([
        {"text": "🔥 Срочные", "callback_data": "urgent"},
        {"text": "➕ Новая задача", "callback_data": "new_task"}
    ])

    tg_send("\n".join(lines), buttons, chat_id)


def build_menu_edit(chat_id, msg_id):
    try:
        projects = todoist_get_projects()
        urgent = todoist_get_urgent_tasks()
        urgent_count = len(urgent["overdue"]) + len(urgent["today"])
    except:
        projects = []
        urgent_count = 0

    lines = ["<b>KAIROS</b>\n"]
    if urgent_count > 0:
        lines.append(f"{urgent_count} срочных задач\n")
    else:
        lines.append("Нет срочных задач\n")
    lines.append("Выбери проект:")

    buttons = []
    for p in projects:
        name = f"📥 {p['name']}" if p["name"] == "Inbox" else p["name"]
        buttons.append([{"text": name, "callback_data": f"project:{p['id']}"}])

    buttons.append([
        {"text": "🔥 Срочные", "callback_data": "urgent"},
        {"text": "➕ Новая задача", "callback_data": "new_task"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Задачи проекта ---

def send_project_tasks(chat_id, msg_id, project_id, project_name=""):
    try:
        all_tasks = todoist_get_tasks(project_id)
    except Exception as e:
        tg_edit(chat_id, msg_id, f"Ошибка: {e}")
        return

    tasks = [t for t in all_tasks if not t.get("is_completed")]
    lines = [f"<b>{project_name}</b>\n"]
    buttons = []

    if not tasks:
        lines.append("Задач нет")
    else:
        for t in tasks:
            due = t.get("due")
            date_str = ""
            if due and due.get("date"):
                d = due["date"]
                today = str(date.today())
                if d < today:
                    date_str = " (просрочено)"
                elif d == today:
                    date_str = " (сегодня)"
                else:
                    date_str = f" (до {d})"
            lines.append(f"  — {t['content']}{date_str}")
            buttons.append([
                {"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}:{project_id}"}
            ])

    buttons.append([
        {"text": f"➕ Добавить в {project_name}", "callback_data": f"new_in:{project_id}:{project_name}"},
    ])
    buttons.append([
        {"text": "Назад", "callback_data": "back_menu"},
        {"text": "Обновить", "callback_data": f"project:{project_id}"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Срочные ---

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
        lines.append("<b>Просрочено:</b>\n")
        for t in tasks["overdue"]:
            proj = projects.get(t["project_id"], "")
            proj_str = f" [{proj}]" if proj else ""
            lines.append(f"  — {t['content']}{proj_str}")
            buttons.append([{"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])
        lines.append("")

    if tasks["today"]:
        lines.append("<b>Сегодня:</b>\n")
        for t in tasks["today"]:
            proj = projects.get(t["project_id"], "")
            proj_str = f" [{proj}]" if proj else ""
            lines.append(f"  — {t['content']}{proj_str}")
            buttons.append([{"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])

    if not tasks["overdue"] and not tasks["today"]:
        lines.append("Всё выполнено")

    buttons.append([
        {"text": "Назад", "callback_data": "back_menu"},
        {"text": "Обновить", "callback_data": "urgent"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Умные напоминания ---

def check_and_send_reminders():
    state = load_state()
    now = local_now()
    hour = now.hour

    if hour < 8 or hour >= 23:
        return

    all_tasks = todoist_get_all_tasks_with_time()
    sent_any = False

    for t in all_tasks:
        task_id = t["id"]
        hours_left = t["hours_left"]

        if t["is_overdue"] and hours_left < -48:
            continue

        # Определяем порог
        threshold = None
        if t["is_overdue"]:
            state_key = f"{task_id}:overdue"
        else:
            for th in REMIND_AT_HOURS:
                if hours_left <= th:
                    threshold = th
                    break
            if threshold is None:
                continue
            state_key = f"{task_id}:{threshold}"

        if state_key in state:
            continue

        # Сообщение
        if t["is_overdue"]:
            text = f"<b>KAIROS</b>\n\nПросрочено: <b>{t['content']}</b>"
        else:
            time_str = format_time_left(hours_left)
            text = f"<b>KAIROS</b>\n\nОсталось {time_str}: <b>{t['content']}</b>"

        buttons = [[{"text": "Done", "callback_data": f"done:{task_id}:reminder"}]]

        try:
            tg_send(text, buttons)
            state[state_key] = int(now.timestamp())
            sent_any = True
        except Exception as e:
            print(f"Reminder send error: {e}")

    if sent_any:
        save_state(state)

    # Чистка старых записей
    cutoff = int(now.timestamp()) - 7 * 86400
    cleaned = {k: v for k, v in state.items() if isinstance(v, int) and v > cutoff}
    save_state(cleaned)


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

        elif cb_data in ("back_menu", "open_menu"):
            tg_answer_callback(cb_id)
            build_menu_edit(chat_id, msg_id)

        elif cb_data.startswith("done:"):
            parts = cb_data.split(":")
            task_id = parts[1]
            source = parts[2] if len(parts) > 2 else ""
            try:
                todoist_close(task_id)
                tg_answer_callback(cb_id, "Выполнено")
                # Очистить state напоминаний
                state = load_state()
                state = {k: v for k, v in state.items() if not k.startswith(f"{task_id}:")}
                save_state(state)
                # Обновить UI
                if source == "urgent":
                    send_urgent(chat_id, msg_id)
                elif source == "reminder":
                    tg_edit(chat_id, msg_id, f"Done")
                elif source:
                    name = get_project_name(source)
                    send_project_tasks(chat_id, msg_id, source, name)
            except Exception as e:
                tg_answer_callback(cb_id, f"Ошибка: {e}")

        elif cb_data == "new_task":
            tg_answer_callback(cb_id)
            try:
                projects = todoist_get_projects()
            except:
                projects = []
            buttons = []
            for p in projects:
                buttons.append([{"text": p["name"], "callback_data": f"new_in:{p['id']}:{p['name']}"}])
            tg_edit(chat_id, msg_id, "<b>В какой проект добавить?</b>", buttons)

        elif cb_data.startswith("new_in:"):
            parts = cb_data.split(":", 2)
            project_id = parts[1]
            project_name = parts[2] if len(parts) > 2 else ""
            tg_answer_callback(cb_id)
            tg_send(f"Напиши задачу для <b>{project_name}</b>:\n\n"
                    f"<i>Текст + дата (завтра / сегодня / 2026-04-05)</i>", chat_id=chat_id)
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
                    tg_answer_callback(cb_id, "Задача создана")
                    tg_edit(chat_id, msg_id, f"Создано: <b>{task_text}</b>")
                except Exception as e:
                    tg_answer_callback(cb_id, f"Ошибка: {e}")

    elif "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        if text in ("/start", "/menu", "/tasks"):
            send_main_menu(chat_id)

        elif text == "/urgent":
            # Отправляем как новое сообщение
            tasks = todoist_get_urgent_tasks()
            lines = ["🔥 <b>Срочные задачи</b>\n"]
            buttons = []
            if tasks["overdue"]:
                lines.append("<b>Просрочено:</b>\n")
                for t in tasks["overdue"]:
                    lines.append(f"  — {t['content']}")
                    buttons.append([{"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])
            if tasks["today"]:
                lines.append("<b>Сегодня:</b>\n")
                for t in tasks["today"]:
                    lines.append(f"  — {t['content']}")
                    buttons.append([{"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}:urgent"}])
            if not tasks["overdue"] and not tasks["today"]:
                lines.append("Всё выполнено")
            buttons.append([{"text": "Открыть KAIROS", "callback_data": "open_menu"}])
            tg_send("\n".join(lines), buttons, chat_id)

        elif text == "/new":
            tg_send("Напиши задачу:", chat_id=chat_id)
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
                {"text": "Обычный", "callback_data": f"priority:{encoded}:{d}:1:{proj}"},
                {"text": "Средний", "callback_data": f"priority:{encoded}:{d}:2:{proj}"},
            ], [
                {"text": "Высокий", "callback_data": f"priority:{encoded}:{d}:3:{proj}"},
                {"text": "Срочный", "callback_data": f"priority:{encoded}:{d}:4:{proj}"},
            ]]

            proj_name = info.get("project_name", "")
            proj_str = f" → {proj_name}" if proj_name else ""
            date_text = f" (до {due_date})" if due_date else ""
            tg_send(f"<b>{task_text}</b>{date_text}{proj_str}\n\nПриоритет:",
                    buttons, chat_id=chat_id)


def main():
    print("KAIROS Bot запущен")
    offset = None
    last_check = 0

    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok"):
                for u in updates["result"]:
                    offset = u["update_id"] + 1
                    handle_update(u)

            # Проверка напоминаний каждые 5 минут
            now = time.time()
            if now - last_check >= 300:
                last_check = now
                try:
                    check_and_send_reminders()
                except Exception as e:
                    print(f"Reminder check error: {e}")

        except KeyboardInterrupt:
            print("\nKAIROS остановлен")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
