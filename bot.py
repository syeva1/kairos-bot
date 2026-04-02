#!/usr/bin/env python3
"""KAIROS Bot — Telegram напоминалка с Todoist интеграцией
Умные напоминания: 24ч → 6ч → 3ч → 1.5ч → 1ч → 30мин → 15мин до дедлайна"""

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
REMIND_AT_HOURS = [24, 6, 3, 1.5, 1, 0.5, 0.25]  # 15мин = 0.25ч

# Файл для трекинга отправленных напоминаний
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/reminder_state.json")


def local_now():
    """Текущее время в локальной зоне"""
    tz = timezone(timedelta(hours=TZ_OFFSET))
    return datetime.now(tz)


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def tg_send(text, buttons=None, chat_id=TG_CHAT):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
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
    data = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
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


def todoist_get_all_tasks():
    """Получить все незавершённые задачи с датами"""
    req = urllib.request.Request(
        f"{TODOIST_API}/tasks",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}"}
    )
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())

    tasks = data.get("results", [])
    result = []
    now = local_now()
    today = now.date()

    for t in tasks:
        due = t.get("due")
        if not due or t.get("is_completed"):
            continue
        due_date_str = due.get("date", "")
        due_datetime_str = due.get("datetime", "")

        if due_datetime_str:
            # Задача с точным временем
            try:
                dt = datetime.fromisoformat(due_datetime_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone(timedelta(hours=TZ_OFFSET)))
            except:
                continue
        elif due_date_str:
            # Задача только с датой — дедлайн = конец дня (23:59)
            try:
                d = date.fromisoformat(due_date_str)
                tz = timezone(timedelta(hours=TZ_OFFSET))
                dt = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
            except:
                continue
        else:
            continue

        hours_left = (dt - now).total_seconds() / 3600
        is_overdue = hours_left < 0

        result.append({
            "id": t["id"],
            "content": t["content"],
            "priority": t.get("priority", 1),
            "due_dt": dt,
            "hours_left": hours_left,
            "is_overdue": is_overdue,
            "due_date": due_date_str,
        })

    return result


def todoist_get_tasks():
    """Совместимость — overdue/today"""
    all_tasks = todoist_get_all_tasks()
    today = local_now().date()
    result = {"overdue": [], "today": []}
    for t in all_tasks:
        d = date.fromisoformat(t["due_date"]) if t["due_date"] else None
        if t["is_overdue"] and d and d < today:
            result["overdue"].append(t)
        elif d and d == today:
            result["today"].append(t)
        elif t["is_overdue"]:
            result["overdue"].append(t)
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


def get_remind_label(hours_left):
    """Какой порог сработал"""
    for threshold in REMIND_AT_HOURS:
        if hours_left <= threshold:
            return threshold
    return None


def check_and_send_reminders():
    """Умные напоминания — отправляем по порогам"""
    state = load_state()
    all_tasks = todoist_get_all_tasks()
    now = local_now()
    hour = now.hour

    # Тихие часы: 23:00 — 08:00
    if hour < 8 or hour >= 23:
        return

    sent_any = False
    for t in all_tasks:
        task_id = t["id"]
        hours_left = t["hours_left"]

        if t["is_overdue"] and hours_left < -48:
            continue  # Давно просрочено, не спамим

        threshold = get_remind_label(hours_left)
        if threshold is None and not t["is_overdue"]:
            continue  # Ещё далеко

        # Ключ: task_id + порог
        if t["is_overdue"]:
            state_key = f"{task_id}:overdue"
        else:
            state_key = f"{task_id}:{threshold}"

        # Уже отправляли это напоминание?
        if state_key in state:
            continue

        # Формируем сообщение
        if t["is_overdue"]:
            urgency = "ПРОСРОЧЕНО"
            text = f"<b>KAIROS</b>\n\n{urgency}: <b>{t['content']}</b>"
        else:
            time_str = format_time_left(hours_left)
            text = f"<b>KAIROS</b>\n\nОсталось {time_str}: <b>{t['content']}</b>"

        buttons = [[
            {"text": f"Done", "callback_data": f"done:{task_id}"},
        ]]

        try:
            tg_send(text, buttons)
            state[state_key] = int(now.timestamp())
            sent_any = True
        except Exception as e:
            print(f"Reminder send error: {e}")

    if sent_any:
        save_state(state)

    # Очистка старых записей (>7 дней)
    cutoff = int(now.timestamp()) - 7 * 86400
    state = {k: v for k, v in state.items() if isinstance(v, int) and v > cutoff}
    save_state(state)


def build_task_message(tasks):
    lines = ["<b>KAIROS</b>\n"]
    buttons = []

    if tasks["overdue"]:
        lines.append("<b>Просрочено:</b>\n")
        for i, t in enumerate(tasks["overdue"], 1):
            lines.append(f"  {i}. {t['content']}")
            buttons.append([
                {"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}"}
            ])
        lines.append("")

    if tasks["today"]:
        lines.append("<b>Сегодня:</b>\n")
        start = len(tasks["overdue"]) + 1
        for i, t in enumerate(tasks["today"], start):
            time_str = format_time_left(t.get("hours_left", 0))
            lines.append(f"  {i}. {t['content']} — {time_str}")
            buttons.append([
                {"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}"}
            ])

    buttons.append([
        {"text": "🔥 Срочные", "callback_data": "urgent"},
        {"text": "➕ Новая задача", "callback_data": "new_task"},
    ])

    return "\n".join(lines), buttons


def send_task_list(chat_id=None):
    tasks = todoist_get_tasks()
    if not tasks["overdue"] and not tasks["today"]:
        tg_send("Все задачи выполнены", chat_id=chat_id or TG_CHAT)
        return
    text, buttons = build_task_message(tasks)
    tg_send(text, buttons, chat_id=chat_id or TG_CHAT)


def send_urgent(chat_id=None):
    all_tasks = todoist_get_all_tasks()
    urgent = [t for t in all_tasks if t["is_overdue"] or t["hours_left"] < 6]
    if not urgent:
        tg_send("Срочных задач нет", chat_id=chat_id or TG_CHAT)
        return

    lines = ["<b>KAIROS</b>\n\n🔥 <b>Срочные:</b>\n"]
    buttons = []
    for i, t in enumerate(urgent, 1):
        time_str = format_time_left(t["hours_left"])
        lines.append(f"  {i}. {t['content']} — {time_str}")
        buttons.append([
            {"text": f"Done: {t['content'][:30]}", "callback_data": f"done:{t['id']}"}
        ])

    tg_send("\n".join(lines), buttons, chat_id=chat_id or TG_CHAT)


def refresh_message(chat_id, msg_id):
    tasks = todoist_get_tasks()
    if not tasks["overdue"] and not tasks["today"]:
        tg_edit(chat_id, msg_id, "Все задачи выполнены")
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
                tg_answer_callback(cb_id, "Выполнено")
                refresh_message(chat_id, msg_id)
                # Очистить напоминания для этой задачи
                state = load_state()
                state = {k: v for k, v in state.items() if not k.startswith(f"{task_id}:")}
                save_state(state)
            except Exception as e:
                tg_answer_callback(cb_id, f"Ошибка: {e}")

        elif cb_data == "new_task":
            tg_answer_callback(cb_id, "")
            tg_send("Напиши задачу:\n\n<i>Текст + дата (завтра / сегодня / 2026-04-05)</i>", chat_id=chat_id)
            waiting_for_task[chat_id] = True

        elif cb_data == "refresh":
            tg_answer_callback(cb_id, "Обновляю...")
            refresh_message(chat_id, msg_id)

        elif cb_data == "urgent":
            tg_answer_callback(cb_id, "")
            send_urgent(chat_id)

        elif cb_data.startswith("priority:"):
            parts = cb_data.split(":", 3)
            if len(parts) == 4:
                _, task_text_encoded, due, level = parts
                task_text = urllib.parse.unquote(task_text_encoded)
                due_date = due if due != "none" else None
                try:
                    todoist_create(task_text, due_date, int(level))
                    tg_answer_callback(cb_id, "Задача создана")
                    tg_edit(chat_id, msg_id, f"Создано: <b>{task_text}</b>")
                except Exception as e:
                    tg_answer_callback(cb_id, f"Ошибка: {e}")

    elif "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg["text"].strip()

        if text == "/start":
            tg_send(
                "<b>KAIROS</b>\n\n"
                "Напоминаю о задачах из Todoist.\n"
                "Уведомления: 24ч, 6ч, 3ч, 1.5ч, 1ч, 30мин, 15мин до дедлайна.\n\n"
                "/tasks — все задачи\n"
                "/urgent — срочные\n"
                "/new — создать задачу",
                chat_id=chat_id
            )

        elif text == "/tasks":
            send_task_list(chat_id)

        elif text == "/urgent":
            send_urgent(chat_id)

        elif text == "/new":
            tg_send("Напиши задачу:", chat_id=chat_id)
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
                {"text": "Обычный", "callback_data": f"priority:{encoded}:{d}:1"},
                {"text": "Средний", "callback_data": f"priority:{encoded}:{d}:2"},
            ], [
                {"text": "Высокий", "callback_data": f"priority:{encoded}:{d}:3"},
                {"text": "Срочный", "callback_data": f"priority:{encoded}:{d}:4"},
            ]]

            date_text = f" (до {due_date})" if due_date else ""
            tg_send(
                f"<b>{task_text}</b>{date_text}\n\nПриоритет:",
                buttons, chat_id=chat_id
            )


def main():
    print("KAIROS Bot запущен")
    offset = None
    last_check = 0

    while True:
        try:
            # Обработка обновлений
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
