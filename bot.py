#!/usr/bin/env python3
"""KAIROS Bot — Telegram напоминалка с Todoist интеграцией
Меню проектов + умные напоминания + голосовые + откладывание + подзадачи + брифинги"""

import json
import urllib.request
import urllib.parse
import time
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone

TODOIST_TOKEN = os.environ.get("TODOIST_TOKEN", "")
TG_BOT = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")
TZ_OFFSET = int(os.environ.get("TZ_OFFSET", "5"))
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TODOIST_API = "https://api.todoist.com/api/v1"
TG_API = f"https://api.telegram.org/bot{TG_BOT}"

REMIND_AT_HOURS = [24, 12, 6, 3, 1.5, 1, 0.5, 0.25]
STATE_FILE = os.environ.get("STATE_FILE", "/tmp/reminder_state.json")
DONE_LOG = os.environ.get("DONE_LOG", "/tmp/done_today.json")


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
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def load_done_log():
    try:
        with open(DONE_LOG) as f:
            data = json.load(f)
        # Очистить если не сегодня
        if data.get("date") != str(local_now().date()):
            return {"date": str(local_now().date()), "tasks": []}
        return data
    except:
        return {"date": str(local_now().date()), "tasks": []}


def save_done_log(data):
    with open(DONE_LOG, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def log_done_task(task_name):
    data = load_done_log()
    data["tasks"].append({"name": task_name, "time": local_now().strftime("%H:%M")})
    save_done_log(data)


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


def tg_get_file(file_id):
    """Получить путь к файлу на серверах Telegram"""
    req = urllib.request.Request(f"{TG_API}/getFile?file_id={file_id}")
    with urllib.request.urlopen(req) as r:
        data = json.loads(r.read())
    return data["result"]["file_path"]


def tg_download_file(file_path):
    """Скачать файл с серверов Telegram"""
    url = f"https://api.telegram.org/file/bot{TG_BOT}/{file_path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as r:
        return r.read()


# --- Groq Whisper ---

def transcribe_voice(audio_data, filename="voice.ogg"):
    """Транскрибировать аудио через Groq Whisper API"""
    if not GROQ_API_KEY:
        return None

    import io
    boundary = "----FormBoundary" + str(int(time.time()))
    body = io.BytesIO()

    # file field
    body.write(f"--{boundary}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    body.write(b"Content-Type: audio/ogg\r\n\r\n")
    body.write(audio_data)
    body.write(b"\r\n")

    # model field
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="model"\r\n\r\n')
    body.write(b"whisper-large-v3\r\n")

    # language field
    body.write(f"--{boundary}\r\n".encode())
    body.write(b'Content-Disposition: form-data; name="language"\r\n\r\n')
    body.write(b"ru\r\n")

    body.write(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        data=body.getvalue(),
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            result = json.loads(r.read())
        return result.get("text", "").strip()
    except Exception as e:
        print(f"Transcribe error: {e}")
        return None


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


def todoist_create(content, due_date=None, due_string=None, priority=1, project_id=None, parent_id=None):
    data = {"content": content, "priority": priority}
    if due_string:
        data["due_string"] = due_string
    elif due_date:
        data["due_date"] = due_date
    if project_id:
        data["project_id"] = project_id
    if parent_id:
        data["parent_id"] = parent_id
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TODOIST_API}/tasks", data=body, method="POST",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def todoist_update_due(task_id, due_string=None, due_date=None):
    data = {}
    if due_string:
        data["due_string"] = due_string
    elif due_date:
        data["due_date"] = due_date
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{TODOIST_API}/tasks/{task_id}", data=body, method="POST",
        headers={"Authorization": f"Bearer {TODOIST_TOKEN}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def todoist_get_subtasks(parent_id):
    """Получить подзадачи"""
    all_tasks = todoist_get_tasks()
    return [t for t in all_tasks if t.get("parent_id") == parent_id and not t.get("is_completed")]


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


def parse_recurring(text):
    """Парсим повторяющиеся задачи: 'каждый день', 'каждую пятницу' и т.д."""
    patterns = {
        r'каждый день': 'every day',
        r'каждую неделю': 'every week',
        r'каждый понедельник': 'every monday',
        r'каждый вторник': 'every tuesday',
        r'каждую среду': 'every wednesday',
        r'каждый четверг': 'every thursday',
        r'каждую пятницу': 'every friday',
        r'каждую субботу': 'every saturday',
        r'каждое воскресенье': 'every sunday',
        r'каждый месяц': 'every month',
        r'через день': 'every 2 days',
        r'каждые (\d+) дн': r'every \1 days',
        r'каждые (\d+) час': r'every \1 hours',
    }
    lower = text.lower()
    for pattern, replacement in patterns.items():
        match = re.search(pattern, lower)
        if match:
            due_string = re.sub(pattern, replacement, lower) if '\\1' in replacement else replacement
            if '\\1' in replacement:
                due_string = match.expand(replacement)
            clean_text = re.sub(pattern, '', lower).strip()
            return clean_text, due_string
    return None, None


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

    tasks = [t for t in all_tasks if not t.get("is_completed") and not t.get("parent_id")]
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
            # Проверить подзадачи
            subtasks = [s for s in all_tasks if s.get("parent_id") == t["id"] and not s.get("is_completed")]
            sub_str = f" [{len(subtasks)} подзадач]" if subtasks else ""
            lines.append(f"  — {t['content']}{date_str}{sub_str}")
            buttons.append([
                {"text": f"Done: {t['content'][:25]}", "callback_data": f"done:{t['id']}:{project_id}"},
                {"text": "...", "callback_data": f"task_menu:{t['id']}:{project_id}"}
            ])

    buttons.append([
        {"text": f"➕ Добавить в {project_name}", "callback_data": f"new_in:{project_id}:{project_name}"},
    ])
    buttons.append([
        {"text": "Назад", "callback_data": "back_menu"},
        {"text": "Обновить", "callback_data": f"project:{project_id}"}
    ])

    tg_edit(chat_id, msg_id, "\n".join(lines), buttons)


# --- Меню задачи (подзадачи, откладывание) ---

def send_task_menu(chat_id, msg_id, task_id, project_id):
    try:
        all_tasks = todoist_get_tasks()
        task = None
        for t in all_tasks:
            if t["id"] == task_id:
                task = t
                break
        if not task:
            tg_edit(chat_id, msg_id, "Задача не найдена")
            return

        subtasks = [s for s in all_tasks if s.get("parent_id") == task_id and not s.get("is_completed")]
    except Exception as e:
        tg_edit(chat_id, msg_id, f"Ошибка: {e}")
        return

    lines = [f"<b>{task['content']}</b>\n"]

    due = task.get("due")
    if due and due.get("date"):
        lines.append(f"Дедлайн: {due['date']}")

    if subtasks:
        lines.append(f"\nПодзадачи ({len(subtasks)}):")
        for s in subtasks:
            lines.append(f"  — {s['content']}")

    buttons = []

    # Подзадачи
    if subtasks:
        for s in subtasks:
            buttons.append([
                {"text": f"Done: {s['content'][:25]}", "callback_data": f"done:{s['id']}:task_menu:{task_id}:{project_id}"}
            ])

    buttons.append([
        {"text": "➕ Подзадача", "callback_data": f"new_sub:{task_id}:{project_id}"}
    ])

    # Откладывание
    buttons.append([
        {"text": "+1ч", "callback_data": f"snooze:{task_id}:1h:{project_id}"},
        {"text": "+3ч", "callback_data": f"snooze:{task_id}:3h:{project_id}"},
        {"text": "Завтра", "callback_data": f"snooze:{task_id}:tomorrow:{project_id}"},
    ])

    buttons.append([
        {"text": "Done", "callback_data": f"done:{task_id}:{project_id}"},
        {"text": "Назад", "callback_data": f"project:{project_id}"}
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
            buttons.append([
                {"text": f"Done: {t['content'][:20]}", "callback_data": f"done:{t['id']}:urgent"},
                {"text": "Завтра", "callback_data": f"snooze:{t['id']}:tomorrow:urgent"},
            ])
        lines.append("")

    if tasks["today"]:
        lines.append("<b>Сегодня:</b>\n")
        for t in tasks["today"]:
            proj = projects.get(t["project_id"], "")
            proj_str = f" [{proj}]" if proj else ""
            lines.append(f"  — {t['content']}{proj_str}")
            buttons.append([
                {"text": f"Done: {t['content'][:20]}", "callback_data": f"done:{t['id']}:urgent"},
                {"text": "+1ч", "callback_data": f"snooze:{t['id']}:1h:urgent"},
            ])

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

        if t["is_overdue"]:
            text = f"<b>KAIROS</b>\n\nПросрочено: <b>{t['content']}</b>"
        else:
            time_str = format_time_left(hours_left)
            text = f"<b>KAIROS</b>\n\nОсталось {time_str}: <b>{t['content']}</b>"

        buttons = [
            [{"text": "Done", "callback_data": f"done:{task_id}:reminder"}],
            [
                {"text": "+1ч", "callback_data": f"snooze:{task_id}:1h:reminder"},
                {"text": "+3ч", "callback_data": f"snooze:{task_id}:3h:reminder"},
                {"text": "Завтра", "callback_data": f"snooze:{task_id}:tomorrow:reminder"},
            ]
        ]

        try:
            tg_send(text, buttons)
            state[state_key] = int(now.timestamp())
            sent_any = True
        except Exception as e:
            print(f"Reminder send error: {e}")

    if sent_any:
        save_state(state)

    cutoff = int(now.timestamp()) - 7 * 86400
    cleaned = {k: v for k, v in state.items() if isinstance(v, int) and v > cutoff}
    save_state(cleaned)


# --- Брифинги ---

def send_morning_briefing():
    """Утренний брифинг в 8:00"""
    now = local_now()
    all_tasks = todoist_get_all_tasks_with_time()
    all_raw = todoist_get_tasks()

    overdue = [t for t in all_tasks if t["is_overdue"]]
    today = [t for t in all_tasks if not t["is_overdue"] and t["hours_left"] <= 24]
    upcoming = [t for t in all_tasks if not t["is_overdue"] and 24 < t["hours_left"] <= 72]
    no_date = [t for t in all_raw if not t.get("due") and not t.get("is_completed")]

    lines = [f"<b>KAIROS — Доброе утро</b>\n{now.strftime('%d.%m.%Y, %A')}\n"]

    if overdue:
        lines.append(f"<b>Просрочено ({len(overdue)}):</b>")
        for t in overdue:
            lines.append(f"  — {t['content']}")
        lines.append("")

    if today:
        lines.append(f"<b>Сегодня ({len(today)}):</b>")
        for t in today:
            time_str = format_time_left(t["hours_left"])
            lines.append(f"  — {t['content']} ({time_str})")
        lines.append("")

    if upcoming:
        lines.append(f"<b>Ближайшие 3 дня ({len(upcoming)}):</b>")
        for t in upcoming[:5]:
            lines.append(f"  — {t['content']} (до {t['due_date']})")
        lines.append("")

    total = len(overdue) + len(today) + len(no_date)
    lines.append(f"Всего активных задач: {total}")

    if not overdue and not today:
        lines.append("\nСегодня свободный день")

    buttons = [[{"text": "Открыть KAIROS", "callback_data": "open_menu"}]]
    tg_send("\n".join(lines), buttons)


def send_evening_summary():
    """Вечерний итог в 22:00"""
    now = local_now()
    done = load_done_log()
    all_tasks = todoist_get_all_tasks_with_time()

    overdue = [t for t in all_tasks if t["is_overdue"]]
    tomorrow = [t for t in all_tasks if not t["is_overdue"] and t["hours_left"] <= 24]

    lines = [f"<b>KAIROS — Итоги дня</b>\n{now.strftime('%d.%m.%Y')}\n"]

    done_tasks = done.get("tasks", [])
    if done_tasks:
        lines.append(f"<b>Выполнено сегодня ({len(done_tasks)}):</b>")
        for d in done_tasks:
            lines.append(f"  — {d['name']} ({d['time']})")
        lines.append("")
    else:
        lines.append("Сегодня ничего не выполнено\n")

    if overdue:
        lines.append(f"<b>Осталось просрочено ({len(overdue)}):</b>")
        for t in overdue:
            lines.append(f"  — {t['content']}")
        lines.append("")

    if tomorrow:
        lines.append(f"<b>На завтра ({len(tomorrow)}):</b>")
        for t in tomorrow[:5]:
            lines.append(f"  — {t['content']}")

    if done_tasks and not overdue:
        lines.append("\nОтличный день!")

    buttons = [[{"text": "Открыть KAIROS", "callback_data": "open_menu"}]]
    tg_send("\n".join(lines), buttons)


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

        elif cb_data.startswith("task_menu:"):
            parts = cb_data.split(":")
            task_id = parts[1]
            project_id = parts[2]
            tg_answer_callback(cb_id)
            send_task_menu(chat_id, msg_id, task_id, project_id)

        elif cb_data.startswith("done:"):
            parts = cb_data.split(":")
            task_id = parts[1]
            source = parts[2] if len(parts) > 2 else ""

            # Находим имя задачи для лога
            task_name = task_id
            try:
                all_t = todoist_get_tasks()
                for t in all_t:
                    if t["id"] == task_id:
                        task_name = t["content"]
                        break
            except:
                pass

            try:
                todoist_close(task_id)
                log_done_task(task_name)
                tg_answer_callback(cb_id, "Выполнено")

                state = load_state()
                state = {k: v for k, v in state.items() if not k.startswith(f"{task_id}:")}
                save_state(state)

                if source == "urgent":
                    send_urgent(chat_id, msg_id)
                elif source == "reminder":
                    tg_edit(chat_id, msg_id, f"Done: {task_name}")
                elif source == "task_menu":
                    # Вернуться в меню задачи (parent)
                    parent_id = parts[3] if len(parts) > 3 else ""
                    proj_id = parts[4] if len(parts) > 4 else ""
                    if parent_id:
                        send_task_menu(chat_id, msg_id, parent_id, proj_id)
                elif source:
                    name = get_project_name(source)
                    send_project_tasks(chat_id, msg_id, source, name)
            except Exception as e:
                tg_answer_callback(cb_id, f"Ошибка: {e}")

        elif cb_data.startswith("snooze:"):
            parts = cb_data.split(":")
            task_id = parts[1]
            duration = parts[2]
            source = parts[3] if len(parts) > 3 else ""

            try:
                now = local_now()
                if duration == "1h":
                    new_dt = now + timedelta(hours=1)
                    todoist_update_due(task_id, due_string=new_dt.strftime("%Y-%m-%d в %H:%M"))
                    label = "+1ч"
                elif duration == "3h":
                    new_dt = now + timedelta(hours=3)
                    todoist_update_due(task_id, due_string=new_dt.strftime("%Y-%m-%d в %H:%M"))
                    label = "+3ч"
                elif duration == "tomorrow":
                    tomorrow = now.date() + timedelta(days=1)
                    todoist_update_due(task_id, due_date=str(tomorrow))
                    label = "завтра"

                # Сбросить напоминания для этой задачи
                state = load_state()
                state = {k: v for k, v in state.items() if not k.startswith(f"{task_id}:")}
                save_state(state)

                tg_answer_callback(cb_id, f"Отложено на {label}")

                if source == "urgent":
                    send_urgent(chat_id, msg_id)
                elif source == "reminder":
                    tg_edit(chat_id, msg_id, f"Отложено на {label}")
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
                    f"<i>Текст + дата (завтра / сегодня / 2026-04-05)</i>\n"
                    f"<i>Повтор: каждый день / каждую пятницу</i>", chat_id=chat_id)
            waiting_for_task[chat_id] = {"project_id": project_id, "project_name": project_name}

        elif cb_data.startswith("new_sub:"):
            parts = cb_data.split(":")
            parent_id = parts[1]
            project_id = parts[2] if len(parts) > 2 else ""
            tg_answer_callback(cb_id)
            tg_send("Напиши подзадачу:", chat_id=chat_id)
            waiting_for_task[chat_id] = {
                "project_id": project_id, "project_name": "",
                "parent_id": parent_id
            }

        elif cb_data.startswith("priority:"):
            parts = cb_data.split(":", 4)
            if len(parts) == 5:
                _, task_encoded, due, level, proj_id = parts
                task_text = urllib.parse.unquote(task_encoded)
                due_date = due if due != "none" else None
                proj = proj_id if proj_id != "none" else None

                # Проверить повторяющуюся задачу
                due_string = None
                if due_date and due_date.startswith("every"):
                    due_string = due_date
                    due_date = None

                try:
                    todoist_create(task_text, due_date=due_date, due_string=due_string,
                                   priority=int(level), project_id=proj)
                    tg_answer_callback(cb_id, "Задача создана")
                    tg_edit(chat_id, msg_id, f"Создано: <b>{task_text}</b>")
                except Exception as e:
                    tg_answer_callback(cb_id, f"Ошибка: {e}")

    elif "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]

        # Голосовые сообщения
        if "voice" in msg or "audio" in msg:
            voice = msg.get("voice") or msg.get("audio")
            file_id = voice["file_id"]

            if not GROQ_API_KEY:
                tg_send("Голосовые задачи не настроены (нужен GROQ_API_KEY)", chat_id=chat_id)
                return

            try:
                tg_send("Распознаю...", chat_id=chat_id)
                file_path = tg_get_file(file_id)
                audio_data = tg_download_file(file_path)
                text = transcribe_voice(audio_data)

                if not text:
                    tg_send("Не удалось распознать", chat_id=chat_id)
                    return

                # Подтверждение
                encoded = urllib.parse.quote(text)[:50]
                d = str(date.today())
                buttons = [
                    [{"text": "Создать задачу", "callback_data": f"priority:{encoded}:{d}:1:none"}],
                    [{"text": "Отмена", "callback_data": "back_menu"}]
                ]
                tg_send(f"Распознано: <b>{text}</b>\n\nСоздать задачу?", buttons, chat_id=chat_id)
            except Exception as e:
                tg_send(f"Ошибка: {e}", chat_id=chat_id)
            return

        if "text" not in msg:
            return

        text = msg["text"].strip()

        if text in ("/start", "/menu", "/tasks"):
            send_main_menu(chat_id)

        elif text == "/urgent":
            tasks = todoist_get_urgent_tasks()
            lines = ["🔥 <b>Срочные задачи</b>\n"]
            buttons = []
            if tasks["overdue"]:
                lines.append("<b>Просрочено:</b>\n")
                for t in tasks["overdue"]:
                    lines.append(f"  — {t['content']}")
                    buttons.append([
                        {"text": f"Done: {t['content'][:20]}", "callback_data": f"done:{t['id']}:urgent"},
                        {"text": "Завтра", "callback_data": f"snooze:{t['id']}:tomorrow:urgent"},
                    ])
            if tasks["today"]:
                lines.append("<b>Сегодня:</b>\n")
                for t in tasks["today"]:
                    lines.append(f"  — {t['content']}")
                    buttons.append([
                        {"text": f"Done: {t['content'][:20]}", "callback_data": f"done:{t['id']}:urgent"},
                        {"text": "+1ч", "callback_data": f"snooze:{t['id']}:1h:urgent"},
                    ])
            if not tasks["overdue"] and not tasks["today"]:
                lines.append("Всё выполнено")
            buttons.append([{"text": "Открыть KAIROS", "callback_data": "open_menu"}])
            tg_send("\n".join(lines), buttons, chat_id)

        elif text == "/briefing":
            send_morning_briefing()

        elif text == "/new":
            tg_send("Напиши задачу:", chat_id=chat_id)
            waiting_for_task[chat_id] = {"project_id": None, "project_name": ""}

        elif chat_id in waiting_for_task:
            info = waiting_for_task.pop(chat_id)
            parent_id = info.get("parent_id")

            # Подзадача — создаём сразу
            if parent_id:
                try:
                    todoist_create(text, parent_id=parent_id, project_id=info.get("project_id"))
                    tg_send(f"Подзадача создана: <b>{text}</b>", chat_id=chat_id)
                except Exception as e:
                    tg_send(f"Ошибка: {e}", chat_id=chat_id)
                return

            due_date = None
            due_string = None
            task_text = text

            # Повторяющиеся
            clean, recurring = parse_recurring(text)
            if recurring:
                task_text = clean if clean else text
                due_string = recurring
            else:
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
            d = due_string or due_date or "none"
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
            if due_string:
                date_text = f" ({due_string})"
            elif due_date:
                date_text = f" (до {due_date})"
            else:
                date_text = ""
            tg_send(f"<b>{task_text}</b>{date_text}{proj_str}\n\nПриоритет:",
                    buttons, chat_id=chat_id)


def main():
    print("KAIROS Bot запущен")
    offset = None
    last_check = 0
    last_briefing_morning = ""
    last_briefing_evening = ""

    while True:
        try:
            updates = get_updates(offset)
            if updates.get("ok"):
                for u in updates["result"]:
                    offset = u["update_id"] + 1
                    handle_update(u)

            now_ts = time.time()
            now = local_now()
            today_str = str(now.date())

            # Проверка напоминаний каждые 5 минут
            if now_ts - last_check >= 300:
                last_check = now_ts
                try:
                    check_and_send_reminders()
                except Exception as e:
                    print(f"Reminder check error: {e}")

            # Утренний брифинг в 8:00
            if now.hour == 8 and now.minute < 5 and last_briefing_morning != today_str:
                last_briefing_morning = today_str
                try:
                    send_morning_briefing()
                except Exception as e:
                    print(f"Morning briefing error: {e}")

            # Вечерний итог в 22:00
            if now.hour == 22 and now.minute < 5 and last_briefing_evening != today_str:
                last_briefing_evening = today_str
                try:
                    send_evening_summary()
                except Exception as e:
                    print(f"Evening summary error: {e}")

        except KeyboardInterrupt:
            print("\nKAIROS остановлен")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
