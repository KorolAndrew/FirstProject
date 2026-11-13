#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CozyCode - уютное приложение для работы с Клодиком (агентом Notion) и Яночкой.

Что внутри:
  * мост (MCP-сервер), через который Клодик работает с файлами в рабочей папке;
  * чат: сообщения сохраняются на ПК, а в Notion уходит только сигнал "новое сообщение";
  * окно приложения (локальная страница в своём окне pywebview, запасной вариант - окно Edge);
  * запуск туннеля ngrok (постоянный адрес) или Cloudflare (запасной вариант).

Запуск: ярлык CozyCode на рабочем столе (создаётся install.bat).
Отладка:  python cozycode.py --console
"""

import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# При запуске через pythonw консоли нет - пишем всё в журнал.
if sys.stdout is None or sys.stderr is None or "--log" in sys.argv:
    _log = open(DATA_DIR / "cozycode.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _log
    sys.stderr = _log

import collections
import datetime
import json
import mimetypes
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(APP_DIR))
import bridge as br  # noqa: E402
import window as nw  # noqa: E402

YANKA = None  # 1.3.0: Янка - свой ИИ (yanka/), создаётся в main()

VERSION = "1.3.0"
UI_DIR = APP_DIR / "ui"
CHATS_DIR = DATA_DIR / "chats"
INBOX_DIR = DATA_DIR / "inbox"
PROJECTS_FILE = DATA_DIR / "projects.json"
USAGE_FILE = DATA_DIR / "usage.json"
for _d in (CHATS_DIR, INBOX_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CFG = br.CONFIG
UI_KEY = secrets.token_urlsafe(24)
QUIT = threading.Event()
LOCK = threading.RLock()
STALE_WORK_SECONDS = 15 * 60

STATE = {
    "v": 1,
    "ui_seen": 0.0,
    "working": {},          # project_id -> время, когда Клодик забрал сообщение
    "waiting": {},          # project_id -> время отправки сигнала (ещё не забрал)
    "status": {},           # project_id -> промежуточный статус
    "confirms": {},
    "activity": collections.deque(maxlen=150),
    "events": collections.deque(maxlen=50),
    "last_agent": 0.0,
    "bridge": {"ok": True, "on": False, "error": ""},  # 1.3.0: мост включается, когда нужен Клодику
    "tunnel": {"mode": "", "url": "", "state": "off", "error": ""},
    "notion": {"error": "", "last_ok": 0.0},
    "signals": {},          # project_id -> [id страниц-сигналов]
    "diffs": {},            # 1.2.2: id -> {path, diff}
    "shutdown": {"armed": False, "at": None},  # 1.2.2: 🌙 выключить ПК после ответа
}


def bump(event=None, **data):
    with LOCK:
        STATE["v"] += 1
        if event:
            STATE["events"].append({"n": STATE["v"], "type": event, **data})


def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def save_config():
    br.CONFIG_PATH.write_text(json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Проекты и сообщения
# --------------------------------------------------------------------------- #

DEFAULT_PROJECTS = [{"id": "general", "name": "Общий", "emoji": "🐑", "folder": "."}]


def load_projects():
    if PROJECTS_FILE.exists():
        try:
            data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            if data:
                return data
        except Exception:
            pass
    PROJECTS_FILE.write_text(json.dumps(DEFAULT_PROJECTS, ensure_ascii=False, indent=2), encoding="utf-8")
    return list(DEFAULT_PROJECTS)


def save_projects(projects):
    PROJECTS_FILE.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")


def get_project(pid):
    for p in load_projects():
        if p["id"] == pid:
            return p
    return None


def project_dir(project):
    return br.resolve_path(project.get("folder") or ".")


def append_message(pid, msg):
    msg.setdefault("id", uuid.uuid4().hex[:12])
    msg.setdefault("time", now_iso())
    with LOCK:
        with open(CHATS_DIR / f"{pid}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def read_messages(pid, limit=400):
    path = CHATS_DIR / f"{pid}.jsonl"
    if not path.exists():
        return []
    out = []
    with LOCK:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out[-limit:]


def inbox_items():
    items = []
    for f in sorted(INBOX_DIR.glob("*.json")):
        try:
            items.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            pass
    items.sort(key=lambda x: x[1].get("time", ""))
    return items


# --------------------------------------------------------------------------- #
# Скилы, память проекта, папки (1.2)
# --------------------------------------------------------------------------- #

SKILLS_STATE = DATA_DIR / "skills.json"
PROJECT_TEMPLATE = """# {emoji} {name}

> Память проекта. Клодик читает этот файл перед работой и обновляет после каждой задачи.

## О проекте
(что это и зачем)

## Как устроено
(язык, главные файлы, как запускать)

## Что уже сделано
-

## Решения и договорённости
-

## Планы / чек-лист
- [ ]
"""


def global_skills_dir():
    return br.WORKSPACE / "skills"


def project_skills_dir(project):
    if not project or project.get("id") == "general":
        return None
    try:
        d = project_dir(project) / "skills"
    except Exception:
        return None
    return None if d == global_skills_dir() else d


def parse_skill_text(text, stem="skill"):
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = text[end + 4:].lstrip("\n")
    if not meta.get("description"):
        meta["description"] = next((l.strip("# ").strip() for l in body.splitlines() if l.strip()), "")[:160]
    return {"name": meta.get("name") or stem, "description": meta.get("description", ""), "source": meta.get("source", "")}


def skills_disabled():
    try:
        return set(json.loads(SKILLS_STATE.read_text(encoding="utf-8")).get("disabled", []))
    except Exception:
        return set()


def set_skill_enabled(key, enabled):
    d = skills_disabled()
    if enabled:
        d.discard(key)
    else:
        d.add(key)
    SKILLS_STATE.write_text(json.dumps({"disabled": sorted(d)}, ensure_ascii=False, indent=1), encoding="utf-8")


def list_skills(project):
    out, disabled = [], skills_disabled()
    dirs = [("global", global_skills_dir())]
    pdir = project_skills_dir(project)
    if pdir:
        dirs.append((project["id"], pdir))
    for scope, folder in dirs:
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.md")):
            try:
                info = parse_skill_text(f.read_text(encoding="utf-8", errors="replace"), f.stem)
            except Exception:
                continue
            key = f"{scope}:{f.name}"
            out.append({**info, "key": key, "scope": "global" if scope == "global" else "project",
                        "path": br.rel(f), "enabled": key not in disabled})
    return out


def skill_file(path):
    p = br.resolve_path(path or "")
    if p.suffix.lower() != ".md" or p.parent.name.lower() != "skills" or not p.is_file():
        raise ValueError("Это не файл скила.")
    return p


def memory_path(project):
    return project_dir(project) / "PROJECT.md"


def ensure_memory(project):
    try:
        p = memory_path(project)
        hit = br.folder_root(p)
        if not p.exists() and hit and hit[1] != "ro" and p.parent.is_dir():
            p.write_text(PROJECT_TEMPLATE.format(emoji=project.get("emoji") or "✨", name=project.get("name") or "Проект"),
                         encoding="utf-8")
    except Exception as e:
        log(f"PROJECT.md для {project.get('id')}: {e}")


def folder_label(project):
    folder = project.get("folder") or "."
    try:
        hit = br.folder_root(project_dir(project))
    except Exception:
        return folder + " (НЕТ ДОСТУПА - папку убрали из разрешённых)"
    return folder + (" (ТОЛЬКО ПРОСМОТР - ничего не менять, только читать и советовать)" if hit and hit[1] == "ro" else "")


def project_context(project):
    lines = []
    try:
        mem = memory_path(project)
    except Exception as e:
        return f"--- ⚠️ Папка проекта недоступна: {e}"
    lines.append(f"--- Память проекта: {br.rel(mem)}" + ("" if mem.exists() else " (пока нет - создай по шаблону из AGENTS.md)")
                 + ". Прочитай перед работой, после задачи обнови.")
    skills = [s for s in list_skills(project) if s["enabled"]]
    if skills:
        lines.append("--- Включённые скилы (прочитай подходящие через read_file перед работой, скажи Яне, какие взял):")
        lines += [f"  • {s['name']} - {s['description']} [{s['path']}]" for s in skills]
    else:
        lines.append("--- Включённых скилов нет.")
    return "\n".join(lines)


def projects_with_mode():
    out = []
    for p in load_projects():
        q = dict(p)
        try:
            hit = br.folder_root(project_dir(p))
            q["mode"] = hit[1] if hit else "none"
        except Exception:
            q["mode"] = "none"
        out.append(q)
    return out


def allow_folder(folder, readonly):
    p = Path(os.path.expandvars(folder)).resolve()
    if not p.is_dir():
        raise ValueError(f"Папка не найдена: {p}")
    if br.is_protected(p):
        raise ValueError("Это системная папка Windows - её выбрать нельзя.")
    if br._inside(p, br.BASE_DIR):
        raise ValueError("Это папка самого CozyCode - её выбрать нельзя.")
    mode = "ro" if readonly else "rw"
    folders = CFG.setdefault("extra_folders", [])
    for f in folders:
        if Path(f["path"]).resolve() == p:
            f["mode"] = mode
            break
    else:
        folders.append({"path": str(p), "mode": mode})
    save_config()
    return p


def pick_folder():
    if not br.IS_WINDOWS:
        raise ValueError("Выбор папки работает только на Windows.")
    script = ("Add-Type -AssemblyName System.Windows.Forms; "
              "$o = New-Object System.Windows.Forms.Form -Property @{TopMost=$true; ShowInTaskbar=$false}; "
              "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
              "$f.Description = 'CozyCode: выбери папку для проекта'; $f.ShowNewFolderButton = $true; "
              "if ($f.ShowDialog($o) -eq 'OK') { $f.SelectedPath }")
    cmd = br.build_command(script, "powershell")
    cmd.insert(1, "-STA")
    r = subprocess.run(cmd, capture_output=True, timeout=900, creationflags=0x08000000)
    return br.decode_output(r.stdout or b"").strip()


# --------------------------------------------------------------------------- #
# Инструменты чата для Клодика
# --------------------------------------------------------------------------- #

def _clip(text, n=1500):
    text = text or ""
    return text if len(text) <= n else text[:n] + " …(обрезано)"


@br.tool("cozy_get_inbox",
         "Забрать новые сообщения Яны из приложения CozyCode вместе с недавней историей чата и папкой проекта. "
         "Забранные сообщения удаляются из очереди. Отвечай через cozy_reply.",
         {"history": br.I("Сколько прошлых сообщений показать (по умолчанию из настроек, обычно 10)")})
def cozy_get_inbox(history=None):
    history = int(history if history is not None else CFG.get("history_messages", 10))
    with LOCK:
        items = inbox_items()
        if not items:
            return "Новых сообщений нет."
        by_project = collections.OrderedDict()
        for f, item in items:
            by_project.setdefault(item["project"], []).append(item)
            try:
                f.unlink()
            except OSError:
                pass
        for pid in by_project:
            STATE["working"][pid] = time.time()
            STATE["waiting"].pop(pid, None)
            STATE["status"][pid] = "Клодик читает сообщение…"
    bump("picked", projects=list(by_project))

    parts = []
    for pid, new in by_project.items():
        project = get_project(pid) or {"id": pid, "name": pid, "folder": "."}
        new_ids = {m["id"] for m in new}
        past = [m for m in read_messages(pid) if m["role"] in ("user", "assistant") and m["id"] not in new_ids]
        past = past[-history:] if history > 0 else []
        parts.append(f"=== Проект «{project['name']}» (project_id: {pid}) · папка: {folder_label(project)}")
        parts.append(project_context(project))
        if past:
            parts.append(f"--- Недавняя история ({len(past)}):")
            for m in past:
                who = "Яна" if m["role"] == "user" else ("Янка (модель на ПК)" if m.get("who") == "yanka" else "Клодик")
                parts.append(f"[{m['time'][11:16]}] {who}: {_clip(m['text'])}")
        parts.append("--- Новые сообщения Яны:")
        for m in new:
            parts.append(f"[{m['time'][11:16]}] {m['text']}")
            if m.get("files"):
                parts.append("Приложенные файлы: " + ", ".join(m["files"]))
    parts.append("\nКогда закончишь - отправь ответ через cozy_reply с нужным project_id. "
                 "Долгие шаги можно коротко показывать через cozy_status.")
    return "\n".join(parts)


def schedule_shutdown():
    """1.2.2: 🌙 Клодик закончил - выключаем ПК через минуту (можно отменить)."""
    with LOCK:
        STATE["shutdown"]["armed"] = False
        STATE["shutdown"]["at"] = time.time() + 60
    log("🌙 Клодик закончил - ПК выключится через минуту")
    if os.name == "nt":
        try:
            subprocess.run(["shutdown", "/s", "/t", "60", "/c",
                            "CozyCode: Клодик закончил. ПК выключится через минуту, отменить можно в CozyCode."],
                           creationflags=0x08000000, timeout=15)
        except Exception as e:
            log(f"Не получилось запланировать выключение: {e}")
    bump("shutdown")


def cancel_shutdown():
    with LOCK:
        STATE["shutdown"]["at"] = None
        STATE["shutdown"]["armed"] = False
    if os.name == "nt":
        try:
            subprocess.run(["shutdown", "/a"], creationflags=0x08000000, timeout=15)
        except Exception as e:
            log(f"Не получилось отменить выключение: {e}")
    bump("shutdown_cancel")


@br.tool("cozy_status",
         "Показать Яне в CozyCode короткий статус работы (например «читаю файлы…»).",
         {"project_id": br.S("ID проекта из cozy_get_inbox"), "text": br.S("Короткий статус")},
         required=("project_id", "text"))
def cozy_status(project_id, text):
    with LOCK:
        STATE["status"][project_id] = text[:200]
        STATE["working"][project_id] = time.time()
    bump("status", project=project_id, text=text[:200])
    return "Статус показан."


@br.tool("cozy_ask",
         "Спросить Яну прямо по ходу работы и дождаться ответа, не заканчивая работу (у Яны лимит - запросы в день). "
         "Можно дать варианты-кнопки. Не ответила вовремя - продолжай с самым разумным вариантом и напиши об этом в cozy_reply.",
         {"project_id": br.S("ID проекта из cozy_get_inbox"), "question": br.S("Вопрос - коротко и понятно"),
          "options": {"type": "array", "items": {"type": "string"}, "description": "Необязательно: варианты ответа (до 6 кнопок)"},
          "wait_minutes": br.I("Сколько ждать ответа: по умолчанию 10 минут, максимум 30")},
         required=("project_id", "question"))
def cozy_ask(project_id, question, options=None, wait_minutes=None):
    """1.2.5: вопрос Яне без завершения работы - её ответ не тратит запрос в Notion."""
    if not get_project(project_id):
        raise br.ToolError(f"Нет проекта {project_id}. Используй project_id из cozy_get_inbox.")
    wait = max(1, min(30, int(wait_minutes or 10)))
    opts = [str(o).strip()[:80] for o in (options or []) if str(o).strip()][:6]
    cid = uuid.uuid4().hex[:10]
    entry = {"id": cid, "kind": "question", "title": "Клодик спрашивает", "message": str(question)[:1500],
             "options": opts, "project": project_id, "rule": None, "diff": None,
             "event": threading.Event(), "answer": None}
    with LOCK:
        STATE["confirms"][cid] = entry
        STATE["status"][project_id] = "💭 жду твой ответ на вопрос"
        STATE["working"][project_id] = time.time()
    bump("confirm", id=cid, title="Клодик задал вопрос 💭")
    if time.time() - STATE["ui_seen"] > 6:
        threading.Thread(target=open_window, daemon=True).start()
    entry["event"].wait(wait * 60)
    with LOCK:
        STATE["confirms"].pop(cid, None)
        STATE["status"][project_id] = "работаю дальше…"
    answer = entry["answer"]
    bump("confirm_done", id=cid, allowed=answer is not None)
    if answer is None:
        return f"Яна не ответила за {wait} мин. Продолжай с самым разумным вариантом и напиши об этом в cozy_reply."
    append_message(project_id, {"role": "assistant", "text": "💭 **Вопрос:** " + str(question)[:1500]})
    append_message(project_id, {"role": "user", "text": answer})
    bump()
    return "Ответ Яны: " + answer


@br.tool("cozy_reply",
         "Отправить Яне ответ в чат CozyCode (Markdown). После ответа Яночка позовёт Яну.",
         {"project_id": br.S("ID проекта из cozy_get_inbox"), "text": br.S("Текст ответа (Markdown)"),
          "files": {"type": "array", "items": {"type": "string"}, "description": "Необязательно: пути к созданным файлам"}},
         required=("project_id", "text"))
def cozy_reply(project_id, text, files=None):
    if not get_project(project_id):
        raise br.ToolError(f"Нет проекта {project_id}. Используй project_id из cozy_get_inbox.")
    msg = append_message(project_id, {"role": "assistant", "text": text, "files": list(files or [])})
    with LOCK:
        STATE["working"].pop(project_id, None)
        STATE["status"].pop(project_id, None)
        pending = len(inbox_items())
        signals = STATE["signals"].pop(project_id, [])
        moon = STATE["shutdown"]["armed"] and not pending and not STATE["working"]
    bump("reply", project=project_id, id=msg["id"], preview=text[:120])
    if moon:
        threading.Thread(target=schedule_shutdown, daemon=True).start()
        return "Ответ доставлен. Яна включила 🌙: ПК выключится через минуту. Заканчивай работу."
    if signals:
        threading.Thread(target=archive_signals, args=(signals,), daemon=True).start()
    if pending:
        return (f"Ответ доставлен. Пока ты работал, пришло новых сообщений: {pending}. "
                "Вызови cozy_get_inbox и продолжай.")
    return "Ответ доставлен. Новых сообщений нет - можно заканчивать."


def yanka_to_klodik(pid, text):
    """1.3.0: шаг задачи Янки для Клодика - как обычное сообщение Яны в очередь."""
    msg = append_message(pid, {"role": "user", "text": text, "files": [], "route": "📋 шаг задачи Янки → ✨ Клодик"})
    (INBOX_DIR / f"{msg['time'].replace(':', '-')}_{msg['id']}.json").write_text(
        json.dumps({"id": msg["id"], "project": pid, "text": text, "files": [], "time": msg["time"]},
                   ensure_ascii=False), encoding="utf-8")
    bump("sent", project=pid)
    bridge_on("шаг задачи Янки")
    call_klodik(pid)


def yanka_reply(pid, text, history, decision):
    """1.3.0: ответ Янки в чат. Не вышло (LM Studio, модель) - передаём сообщение Клодику, как раньше."""
    project = get_project(pid) or {"folder": "."}

    def status(s):
        with LOCK:
            STATE["status"][pid] = s[:200]
            STATE["working"][pid] = time.time()
        bump("status", project=pid, text=s[:200])

    try:
        reply, meta = YANKA.answer(pid, str(project_dir(project)), text, history, decision, status)
    except Exception as e:
        log(f"Янка: {type(e).__name__}: {e}")
        reply, meta = None, {"error": str(e)}
    with LOCK:
        STATE["working"].pop(pid, None)
        STATE["status"].pop(pid, None)
    if reply is None:
        note = append_message(pid, {"role": "assistant", "who": "yanka", "text":
                                    f"🐑 Не получилось ответить самой ({meta.get('error', 'ошибка')}). Передала Клодику ✨"})
        (INBOX_DIR / f"{note['time'].replace(':', '-')}_{note['id']}.json").write_text(
            json.dumps({"id": note["id"], "project": pid, "text": text, "files": [], "time": note["time"]},
                       ensure_ascii=False), encoding="utf-8")
        bump("reply", project=pid, id=note["id"], preview=note["text"][:120])
        bridge_on("сообщение Клодику")
        call_klodik(pid)
        return
    msg = append_message(pid, {"role": "assistant", "who": "yanka", "text": reply, "meta": meta})
    bump("reply", project=pid, id=msg["id"], preview=reply[:120], who="yanka")


# --------------------------------------------------------------------------- #
# Подтверждения, журнал, счётчик запросов
# --------------------------------------------------------------------------- #

def confirm_hook(title, message, rule=None, diff=None):
    """Если окно CozyCode открыто - спрашиваем в нём, иначе обычное окно Windows."""
    if time.time() - STATE["ui_seen"] > 6:
        return None
    cid = uuid.uuid4().hex[:10]
    ev = threading.Event()
    entry = {"id": cid, "title": title, "message": message, "rule": rule, "diff": diff, "time": now_iso(), "event": ev, "answer": None}
    with LOCK:
        STATE["confirms"][cid] = entry
    bump("confirm", id=cid, title=title)
    ev.wait(int(CFG.get("confirm_timeout_seconds", 120)))
    with LOCK:
        STATE["confirms"].pop(cid, None)
    bump("confirm_done", id=cid, allowed=bool(entry["answer"]))
    if entry["answer"] == "always" and rule:
        return "always"
    return bool(entry["answer"])


NICE = {
    "get_info": ("ℹ️", "смотрит информацию о ПК"),
    "list_dir": ("📂", "смотрит папку {path}"),
    "read_file": ("👀", "читает {path}"),
    "write_file": ("✏️", "пишет {path}"),
    "edit_file": ("✏️", "правит {path}"),
    "make_dir": ("📁", "создаёт папку {path}"),
    "copy_path": ("📄", "копирует {source}"),
    "move_path": ("📦", "перемещает {source}"),
    "delete_path": ("🗑️", "удаляет {path}"),
    "search_files": ("🔎", "ищет файлы"),
    "run_command": ("⚙️", "команда: {command}"),
    "start_background": ("🚀", "запускает: {command}"),
    "list_background": ("📋", "смотрит фоновые процессы"),
    "read_background_output": ("📜", "читает вывод {id}"),
    "stop_background": ("⏹️", "останавливает {id}"),
    "cozy_get_inbox": ("📬", "читает твоё сообщение"),
    "cozy_status": ("💬", "{text}"),
    "cozy_reply": ("💌", "отправил ответ"),
    "cozy_ask": ("💭", "спрашивает тебя: {question}"),
    "mcp_list": ("🧩", "смотрит подключённые программы"),
    "mcp_tools": ("🧩", "смотрит, что умеет {program}"),
    "mcp_call": ("🎨", "{program}: {tool}"),
    "mcp_add": ("🧩", "подключает программу {name}"),
    "mcp_stop": ("⏹️", "останавливает {program}"),
}


_tl = threading.local()


def diff_hook(path, diff, plus, minus):
    did = uuid.uuid4().hex[:10]
    with LOCK:
        STATE["diffs"][did] = {"path": path, "diff": diff}
        while len(STATE["diffs"]) > 40:
            STATE["diffs"].pop(next(iter(STATE["diffs"])))
    _tl.diff = {"diff_id": did, "plus": plus, "minus": minus}


def activity_hook(tool_name, args, status, detail):
    STATE["last_agent"] = time.time()
    extra = {}
    if tool_name in ("write_file", "edit_file"):
        extra = (getattr(_tl, "diff", None) or {}) if status == "ok" else {}
        _tl.diff = None
    if status == "start" and tool_name not in ("run_command", "start_background", "delete_path"):
        return
    emoji, tpl = NICE.get(tool_name, ("🔧", tool_name))
    safe = collections.defaultdict(str, {k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)) for k, v in (args or {}).items()})
    text = tpl.format_map(safe)
    if len(text) > 90:
        text = text[:90] + "…"
    state = {"start": "wait", "ok": "ok"}.get(status, "error")
    with LOCK:
        STATE["activity"].append({"time": datetime.datetime.now().strftime("%H:%M"), "emoji": emoji,
                                  "text": text, "state": state, "detail": (detail or "")[:200], **extra})
    bump()


_usage_lock = threading.Lock()


def request_hook():
    month = datetime.date.today().strftime("%Y-%m")
    with _usage_lock:
        try:
            usage = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            usage = {}
        usage[month] = usage.get(month, 0) + 1
        USAGE_FILE.write_text(json.dumps(usage), encoding="utf-8")


def usage_this_month():
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8")).get(datetime.date.today().strftime("%Y-%m"), 0)
    except Exception:
        return 0


br.HOOKS.update(confirm=confirm_hook, activity=activity_hook, request=request_hook, diff=diff_hook,
                programs=lambda: bump())  # 1.2.5: статус программ поменялся


# --------------------------------------------------------------------------- #
# Notion: сигнал "новое сообщение"
# --------------------------------------------------------------------------- #

NOTION_VERSION = "2022-06-28"
_title_prop = {}


class NotionError(Exception):
    pass


def notion_db_id():
    raw = (CFG.get("notion_database") or "").strip()
    raw = raw.split("?")[0].replace("-", "")
    m = re.findall(r"[0-9a-fA-F]{32}", raw)
    return m[-1] if m else ""


def notion_request(method, path, body=None):
    token = (CFG.get("notion_token") or "").strip()
    if not token:
        raise NotionError("Не указан ключ Notion (Настройки → Notion).")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("https://api.notion.com/v1" + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read().decode("utf-8"))
            text = info.get("message") or str(info)
        except Exception:
            text = str(e)
        if e.code == 401:
            text = "Notion не принял ключ. Проверь ключ в настройках."
        elif e.code == 404:
            text = "Не вижу базу сигналов. Проверь ссылку и что к базе подключено твоё приложение (••• → Connections)."
        raise NotionError(text)
    except urllib.error.URLError as e:
        raise NotionError(f"Нет связи с Notion: {e.reason}")


def notion_title_prop(db):
    if db not in _title_prop:
        info = notion_request("GET", f"/databases/{db}")
        for name, prop in (info.get("properties") or {}).items():
            if prop.get("type") == "title":
                _title_prop[db] = name
                break
        else:
            raise NotionError("В базе сигналов нет колонки-заголовка.")
    return _title_prop[db]


def notion_check():
    db = notion_db_id()
    if not db:
        raise NotionError("Не указана ссылка на базу сигналов (Настройки → Notion).")
    _title_prop.pop(db, None)
    return notion_title_prop(db)


def send_signal(pid):
    project = get_project(pid) or {"name": pid}
    db = notion_db_id()
    if not db:
        raise NotionError("Не указана ссылка на базу сигналов (Настройки → Notion).")
    title = notion_title_prop(db)
    page = notion_request("POST", "/pages", {
        "parent": {"database_id": db},
        "properties": {title: {"title": [{"text": {"content": f"💌 Новое сообщение · {project['name']}"}}]}},
    })
    with LOCK:
        STATE["signals"].setdefault(pid, []).append(page.get("id"))
        STATE["waiting"][pid] = time.time()
        STATE["notion"] = {"error": "", "last_ok": time.time()}
    bump("signal", project=pid)
    log(f"Сигнал Клодику отправлен ({project['name']})")


def archive_signals(page_ids):
    for pid in page_ids:
        if not pid:
            continue
        try:
            notion_request("PATCH", f"/pages/{pid}", {"archived": True})
        except Exception as e:
            log(f"Не удалось убрать сигнал: {e}")


def agent_busy():
    t = time.time()
    with LOCK:
        return any(t - since < STALE_WORK_SECONDS for since in STATE["working"].values())


def call_klodik(pid, force=False):
    """Позвать Клодика. Если он уже работает - он сам заберёт новое сообщение после ответа."""
    # 1.2.3: интеграция Notion убрана (у фоновых запусков отдельные лимиты).
    # Сообщение просто ждёт, пока Яна пришлёт Клодику «.» в чате Notion.
    with LOCK:
        STATE["waiting"][pid] = time.time()
    bump("queued", project=pid)
    return "queued"


# --------------------------------------------------------------------------- #
# Туннель
# --------------------------------------------------------------------------- #

TUN = {"proc": None}


def find_exe(name):
    exe = name + (".exe" if br.IS_WINDOWS else "")
    cands = [APP_DIR / exe, APP_DIR / "tools" / exe]
    cands += [p.parent / exe for p in br._old_bridge_configs()]
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which(name)


def ngrok_public_url(domain):
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        for t in data.get("tunnels", []):
            if domain and domain in t.get("public_url", ""):
                return t["public_url"]
    except Exception:
        pass
    return ""


def set_tunnel(**kw):
    with LOCK:
        STATE["tunnel"].update(kw)
    bump()


def start_ngrok(domain, port):
    domain = domain.replace("https://", "").replace("http://", "").strip("/ ")
    url = ngrok_public_url(domain)
    if url:
        set_tunnel(mode="ngrok", url=url, state="online", error="", external=True)
        log("ngrok уже запущен в другом окне - использую его")
        return True
    exe = find_exe("ngrok")
    if not exe:
        set_tunnel(mode="ngrok", state="error", error="Не нашла ngrok.exe - положи его в папку CozyCode.")
        return False
    set_tunnel(mode="ngrok", state="starting", url="", error="")
    proc = subprocess.Popen([exe, "http", str(port), f"--url=https://{domain}", "--log=stdout", "--log-format=logfmt"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            **br.popen_kwargs())
    TUN["proc"] = proc
    last_err = [""]

    def reader():
        with open(DATA_DIR / "tunnel.log", "w", encoding="utf-8") as f:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                f.write(line)
                f.flush()
                if "lvl=eror" in line or "ERR_NGROK" in line or "lvl=crit" in line:
                    m = re.search(r'err="?([^"]+)', line)
                    last_err[0] = (m.group(1) if m else line).strip()[:300]

    threading.Thread(target=reader, daemon=True).start()
    for _ in range(40):
        time.sleep(0.5)
        url = ngrok_public_url(domain)
        if url:
            set_tunnel(state="online", url=url, error="", external=False)
            log(f"ngrok запущен: {url}")
            return True
        if proc.poll() is not None:
            break
    br.kill_tree(proc)
    TUN["proc"] = None
    set_tunnel(state="error", error=last_err[0] or "ngrok не запустился (подробности в data/tunnel.log).")
    return False


def start_cloudflare(port):
    if not find_exe("cloudflared"):
        set_tunnel(mode="cloudflare", state="error", error="Нет cloudflared.exe для запасного туннеля.")
        return False
    br.BASE_DIR = DATA_DIR
    br.CONNECTION_FILE = DATA_DIR / "connection.txt"
    br.find_cloudflared = lambda: find_exe("cloudflared")
    set_tunnel(mode="cloudflare", state="starting", url="")
    br.start_tunnel(port)
    for _ in range(60):
        time.sleep(0.5)
        if br.TUNNEL.get("url"):
            set_tunnel(state="online", url=br.TUNNEL["url"], error="Временный адрес! Обнови его в настройках агента.")
            return True
    set_tunnel(state="error", error="Cloudflare не запустился.")
    return False


def tunnel_manager(gen=0):
    port = int(CFG["port"])
    mode = CFG.get("tunnel", "ngrok")
    if mode == "off":
        set_tunnel(mode="off", state="off", url="", error="")
        return
    ok = False
    if mode == "ngrok" and CFG.get("ngrok_domain"):
        ok = start_ngrok(CFG["ngrok_domain"], port)
        if not ok:
            err = STATE["tunnel"]["error"]
            log(f"ngrok: {err}")
            if start_cloudflare(port):
                set_tunnel(error=f"ngrok не заработал ({err}). Включён запасной Cloudflare с временным адресом.")
                ok = True
    else:
        ok = start_cloudflare(port)
    # следим, чтобы туннель не упал
    while ok and not QUIT.wait(15) and BRIDGE["gen"] == gen:
        if STATE["tunnel"]["mode"] == "ngrok":
            alive = bool(ngrok_public_url(CFG["ngrok_domain"]))
            if not alive and STATE["tunnel"]["state"] == "online":
                set_tunnel(state="error", error="Туннель ngrok остановился - перезапускаю…")
                ok = start_ngrok(CFG["ngrok_domain"], port)


# 1.3.0: мост (MCP-сервер для Клодика) и туннель включаются только когда нужны:
# сообщение ушло Клодику, кнопка «🔌» или галочка «включать сразу». Янке они не нужны.
BRIDGE = {"server": None, "gen": 0}
BRIDGE_LOCK = threading.Lock()


def bridge_on(reason=""):
    with BRIDGE_LOCK:
        if BRIDGE["server"]:
            return True
        try:
            server = ThreadingHTTPServer((CFG["host"], int(CFG["port"])), br.Handler)
        except OSError as e:
            STATE["bridge"] = {"ok": False, "on": False,
                               "error": f"Порт {CFG['port']} занят - закрой старое окно Yana PC Bridge и перезапусти CozyCode. ({e})"}
            log(STATE["bridge"]["error"])
            bump()
            return False
        server.daemon_threads = True
        threading.Thread(target=serve, args=(server,), daemon=True).start()
        BRIDGE["server"] = server
        BRIDGE["gen"] += 1
        gen = BRIDGE["gen"]
        STATE["bridge"] = {"ok": True, "on": True, "error": ""}
    log(f"Мост включён{' (' + reason + ')' if reason else ''}: http://{CFG['host']}:{CFG['port']}/mcp · папка {br.WORKSPACE}")
    threading.Thread(target=tunnel_manager, args=(gen,), daemon=True).start()
    bump()
    return True


def bridge_off():
    with BRIDGE_LOCK:
        server, BRIDGE["server"] = BRIDGE["server"], None
        BRIDGE["gen"] += 1
    # только туннель - фоновые процессы и программы не трогаем
    for proc in (TUN["proc"], br.TUNNEL.get("proc")):
        if proc:
            try:
                br.kill_tree(proc)
            except Exception:
                pass
    TUN["proc"] = None
    br.TUNNEL["proc"] = None
    set_tunnel(mode="", state="off", url="", error="")
    if server:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    STATE["bridge"] = {"ok": True, "on": False, "error": ""}
    log("Мост и туннель выключены")
    bump()
    return True


def stop_tunnel():
    if TUN["proc"]:
        br.kill_tree(TUN["proc"])
    br.cleanup()


# --------------------------------------------------------------------------- #
# Windows: ярлыки, автозапуск, окно
# --------------------------------------------------------------------------- #

def pythonw_path():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def powershell(script):
    return subprocess.run(br.build_command(script, "powershell"), capture_output=True, timeout=60,
                          **({"creationflags": 0x08000000} if br.IS_WINDOWS else {}))


def make_shortcut(folder_kind, remove=False):
    """folder_kind: Desktop или Startup."""
    if not br.IS_WINDOWS:
        return False
    def q(s):
        return str(s).replace("'", "''")
    icon = APP_DIR / "ui" / "img" / "cozycode.ico"
    if remove:
        script = f"$p = Join-Path ([Environment]::GetFolderPath('{folder_kind}')) 'CozyCode.lnk'; if (Test-Path $p) {{ Remove-Item $p }}"
    else:
        script = (f"$p = Join-Path ([Environment]::GetFolderPath('{folder_kind}')) 'CozyCode.lnk'; "
                  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); "
                  f"$s.TargetPath = '{q(pythonw_path())}'; "
                  f"$s.Arguments = '\"{q(APP_DIR / 'cozycode.py')}\"'; "
                  f"$s.WorkingDirectory = '{q(APP_DIR)}'; "
                  f"$s.IconLocation = '{q(icon)}'; "
                  "$s.Description = 'CozyCode - Клодик и Яночка'; $s.Save()")
    r = powershell(script)
    return r.returncode == 0


def find_browser():
    env = os.environ
    rel_paths = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
    for base in (env.get("ProgramFiles(x86)"), env.get("ProgramFiles"), env.get("LOCALAPPDATA")):
        for relp in rel_paths:
            if base and Path(base, relp).exists():
                return str(Path(base, relp))
    return shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")


# --------------------------------------------------------------------------- #
# 1.2.4: окно CozyCode как у приложения (запасной путь через Edge, если своё окно не открылось).
# Только стандартный Python (ctypes), ставить ничего не нужно.
# --------------------------------------------------------------------------- #

WINDOWS_FILE = DATA_DIR / "windows.json"
WIN_LOCK = threading.Lock()
_WIN = {}


def _win():
    """Функции Windows с точными типами, чтобы адреса окон не обрезались."""
    if _WIN:
        return _WIN
    import ctypes
    from ctypes import wintypes as wt
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def sig(fn, res, *args):
        fn.restype, fn.argtypes = res, list(args)

    sig(u.EnumWindows, wt.BOOL, enum_proc, wt.LPARAM)
    sig(u.IsWindow, wt.BOOL, wt.HWND)
    sig(u.IsWindowVisible, wt.BOOL, wt.HWND)
    sig(u.IsIconic, wt.BOOL, wt.HWND)
    sig(u.GetWindow, wt.HWND, wt.HWND, wt.UINT)
    sig(u.GetWindowLongW, wt.LONG, wt.HWND, ctypes.c_int)
    sig(u.GetClassNameW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    sig(u.GetWindowTextW, ctypes.c_int, wt.HWND, wt.LPWSTR, ctypes.c_int)
    sig(u.GetWindowThreadProcessId, wt.DWORD, wt.HWND, ctypes.POINTER(wt.DWORD))
    sig(u.GetForegroundWindow, wt.HWND)
    sig(u.SetForegroundWindow, wt.BOOL, wt.HWND)
    sig(u.BringWindowToTop, wt.BOOL, wt.HWND)
    sig(u.ShowWindow, wt.BOOL, wt.HWND, ctypes.c_int)
    sig(u.AttachThreadInput, wt.BOOL, wt.DWORD, wt.DWORD, wt.BOOL)
    sig(u.SwitchToThisWindow, None, wt.HWND, wt.BOOL)
    sig(k.GetCurrentThreadId, wt.DWORD)
    sig(k.OpenProcess, wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
    sig(k.QueryFullProcessImageNameW, wt.BOOL, wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    sig(k.CloseHandle, wt.BOOL, wt.HANDLE)
    _WIN.update(ctypes=ctypes, wt=wt, u=u, k=k, enum_proc=enum_proc)
    return _WIN


def _win_text(fn, hwnd, size=512):
    buf = _win()["ctypes"].create_unicode_buffer(size)
    fn(hwnd, buf, size)
    return buf.value


def _win_exe(hwnd):
    w = _win()
    ctypes, wt = w["ctypes"], w["wt"]
    pid = wt.DWORD()
    w["u"].GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = w["k"].OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, n = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        ok = w["k"].QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
        return Path(buf.value).name.lower() if ok else ""
    finally:
        w["k"].CloseHandle(h)


def _is_browser_window(hwnd, exe_name):
    """Видимое главное окно Chrome или Edge (у обоих класс окна Chrome_WidgetWin_1)."""
    u = _win()["u"]
    if not hwnd or not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, 4):  # 4 = есть окно-хозяин
        return False
    if u.GetWindowLongW(hwnd, -20) & 0x80:  # подсказки и всплывашки (WS_EX_TOOLWINDOW)
        return False
    return _win_text(u.GetClassNameW, hwnd, 64) == "Chrome_WidgetWin_1" and _win_exe(hwnd) == exe_name


def _browser_windows(exe_name):
    """Все видимые главные окна этого браузера: [(handle, заголовок)]."""
    u, found = _win()["u"], []

    def cb(hwnd, _):
        try:
            if _is_browser_window(hwnd, exe_name):
                found.append((hwnd, _win_text(u.GetWindowTextW, hwnd)))
        except Exception:
            pass
        return True

    u.EnumWindows(_win()["enum_proc"](cb), 0)
    return found


def _tabbed_title(title):
    """У обычных окон с вкладками в заголовке есть имя браузера, у окон-«приложений» - нет."""
    t = title.lower()
    return "google chrome" in t or ("microsoft" in t and "edge" in t)


def _catch_new_window(before, exe_name, seconds):
    """Ждём, когда браузер покажет новое окно, и узнаём, какое оно."""
    end, seen = time.time() + seconds, 0.0
    while time.time() < end:
        time.sleep(0.25)
        new = [(h, t) for h, t in _browser_windows(exe_name) if h not in before and t]
        apps = [h for h, t in new if not _tabbed_title(t)]
        if apps:
            return apps[0]
        if new:
            seen = seen or time.time()
            if time.time() - seen > 1.5:
                return new[0][0]
    return 0


def _bring_to_front(hwnd):
    """Показать окно поверх остальных. Windows не даёт окнам выпрыгивать «самим»,
    поэтому на миг подключаемся к окну, в котором Яна только что нажала кнопку."""
    w = _win()
    u, k = w["u"], w["k"]
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE - достать из панели задач
    fg = u.GetForegroundWindow()
    if fg == hwnd:
        return True
    me = k.GetCurrentThreadId()
    other = u.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(other and other != me and u.AttachThreadInput(me, other, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, other, False)
    if u.GetForegroundWindow() != hwnd:
        u.SwitchToThisWindow(hwnd, True)
        time.sleep(0.2)
    return u.GetForegroundWindow() == hwnd


def _saved_windows():
    try:
        data = json.loads(WINDOWS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _remember_window(kind, hwnd, exe_name, **extra):
    with WIN_LOCK:
        data = _saved_windows()
        data[kind] = {"hwnd": int(hwnd), "exe": exe_name, **extra}
        try:
            WINDOWS_FILE.parent.mkdir(parents=True, exist_ok=True)
            WINDOWS_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def _live_window(kind, url=None):
    """Окно, открытое раньше, - если оно всё ещё открыто (и это не обычное окно с вкладками)."""
    s = _saved_windows().get(kind)
    if not isinstance(s, dict) or (url is not None and s.get("url") != url):
        return 0
    try:
        hwnd = int(s.get("hwnd") or 0)
    except (TypeError, ValueError):
        return 0
    if not hwnd or not _is_browser_window(hwnd, str(s.get("exe") or "")):
        return 0
    return 0 if _tabbed_title(_win_text(_win()["u"].GetWindowTextW, hwnd)) else hwnd


def window_size_remembered(profile):
    """1.2.4: Edge сам помнит размер и место окна. Стартовый размер даём, только пока он не запомнил."""
    try:
        prefs = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
        return bool((prefs.get("browser") or {}).get("app_window_placement"))
    except Exception:
        return False


def focus_app_window():
    """1.2.4: CozyCode уже включён - показываем его окно, а не открываем второе."""
    if not br.IS_WINDOWS:
        return False
    try:
        # 1.3.0: своё окно показывает сам запущенный CozyCode
        with urllib.request.urlopen(f"http://127.0.0.1:{CFG['ui_port']}/show", timeout=3) as r:
            if json.loads(r.read().decode("utf-8")).get("native"):
                return True
    except Exception:
        pass
    try:
        hwnd = _live_window("app")
        if hwnd:
            _bring_to_front(hwnd)
            return True
    except Exception as e:
        log(f"Окно CozyCode: {e}")
    return False


def open_window(wait=False):
    if nw.show():
        return
    url = f"http://127.0.0.1:{CFG['ui_port']}/"
    exe = find_browser() if br.IS_WINDOWS else None
    if not exe:
        import webbrowser
        webbrowser.open(url)
        return
    profile = DATA_DIR / "window"
    args = [exe, f"--app={url}", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check", "--disable-features=Translate"]
    if not window_size_remembered(profile):
        args.insert(2, "--window-size=1320,860")
    name = Path(exe).name.lower()
    try:
        before = {h for h, _ in _browser_windows(name)}
    except Exception as e:
        log(f"Окно CozyCode: не получилось проверить окна ({e})")
        before = None
    subprocess.Popen(args, **br.popen_kwargs())
    if before is None:
        return

    def catch():
        try:
            hwnd = _catch_new_window(before, name, 15)
            if hwnd:
                _remember_window("app", hwnd, name)
        except Exception as e:
            log(f"Окно CozyCode: не получилось запомнить окно ({e})")

    if wait:
        catch()
    else:
        threading.Thread(target=catch, daemon=True).start()


# --------------------------------------------------------------------------- #
# Сервер окна приложения (только для этого ПК, в туннель не попадает)
# --------------------------------------------------------------------------- #

PUBLIC_SETTINGS = ("workspace", "confirm_commands", "confirm_deletes", "confirm_timeout_seconds", "tunnel",
                   "ngrok_domain", "notion_database", "sound", "notifications", "autostart", "history_messages",
                   "confirm_edits", "native_window", "yanka_mode", "bridge_autostart")


def public_state():
    t = time.time()
    with LOCK:
        working = {k: int(t - v) for k, v in STATE["working"].items() if t - v < STALE_WORK_SECONDS}
        waiting = {k: int(t - v) for k, v in STATE["waiting"].items()}
        settings = {k: CFG.get(k) for k in PUBLIC_SETTINGS}
        settings["notion_token_set"] = bool(CFG.get("notion_token"))
        settings["mcp_token"] = CFG.get("token") or ""
        domain = (CFG.get("ngrok_domain") or "").strip()
        return {
            "v": STATE["v"],
            "version": VERSION,
            "bridge": dict(STATE["bridge"]),
            "tunnel": dict(STATE["tunnel"]),
            "mcp_url": (STATE["tunnel"].get("url") or ("https://" + domain if domain else "")) + "/mcp",
            "notion": {"configured": bool(CFG.get("notion_token") and notion_db_id()), **STATE["notion"]},
            "agent": {"last_seen": int(t - STATE["last_agent"]) if STATE["last_agent"] else None},
            "projects": projects_with_mode(),
            "folders": CFG.get("extra_folders") or [],
            "cmd_rules": CFG.get("allowed_commands") or [],
            "programs": br.PROGRAMS.ui_state(),
            "shutdown": {"armed": STATE["shutdown"]["armed"],
                         "left": int(STATE["shutdown"]["at"] - t)
                         if STATE["shutdown"]["at"] and STATE["shutdown"]["at"] > t - 5 else None},
            "working": working,
            "waiting": waiting,
            "status": dict(STATE["status"]),
            "confirms": [{k: v for k, v in c.items() if k not in ("event", "answer")} for c in STATE["confirms"].values()],
            "activity": list(STATE["activity"])[-60:],
            "events": list(STATE["events"]),
            "usage": {"requests": usage_this_month(), "limit": 20000},
            "settings": settings,
            "workspace": str(br.WORKSPACE),
        }


def slugify(name):
    s = re.sub(r"[^0-9A-Za-zА-Яа-яЁё_-]+", "-", name.strip()).strip("-")
    return s[:40] or "project"


def api_post(path, body, raw=None, query=None):
    if path == "/api/send":
        pid = body.get("project") or "general"
        if not get_project(pid):
            raise ValueError("Нет такого проекта")
        text = (body.get("text") or "").strip()
        files = [f for f in (body.get("files") or []) if isinstance(f, str)]
        if not text and not files:
            raise ValueError("Пустое сообщение")
        # 1.3.0: Янка отвечает сама (модель на ПК), если это разговор; код и файлы - по-прежнему Клодику
        decision = None
        if YANKA and not files:
            try:
                decision = YANKA.route_message(text, CFG.get("yanka_mode", "auto"))
            except Exception as e:
                log(f"Янка: маршрут не получился ({e})")
        if decision and decision["executor"] == "local":
            history = read_messages(pid, 20)
            msg = append_message(pid, {"role": "user", "text": text, "files": [], "to": "yanka"})
            bump("sent", project=pid)
            threading.Thread(target=yanka_reply, args=(pid, text, history, decision), daemon=True).start()
            return {"ok": True, "message": msg, "yanka": True}
        route = None
        if decision:
            from yanka.router import explain
            route = explain(decision)
        msg = append_message(pid, {"role": "user", "text": text, "files": files, **({"route": route} if route else {})})
        (INBOX_DIR / f"{msg['time'].replace(':', '-')}_{msg['id']}.json").write_text(
            json.dumps({"id": msg["id"], "project": pid, "text": text, "files": files, "time": msg["time"]},
                       ensure_ascii=False), encoding="utf-8")
        bump("sent", project=pid)
        bridge_on("сообщение Клодику")
        result = call_klodik(pid)
        return {"ok": True, "message": msg, "result": result}
    if path == "/api/bridge":  # 1.3.0: 🔌 включить / выключить мост и туннель
        return {"ok": bridge_on("кнопка") if body.get("on") else bridge_off()}
    if path == "/api/recall":
        pid = body.get("project") or "general"
        bridge_on("позвать ещё раз")
        with LOCK:
            STATE["working"].pop(pid, None)
            STATE["status"].pop(pid, None)
        return {"ok": True, "result": call_klodik(pid, force=True)}
    if path == "/api/confirm":
        with LOCK:
            c = STATE["confirms"].get(body.get("id"))
            if c:
                if c.get("kind") == "question":  # 1.2.5: ответ на вопрос Клодика
                    c["answer"] = str(body.get("answer") or "").strip()[:2000] or "(без ответа)"
                else:
                    c["answer"] = "always" if body.get("allow") == "always" and c.get("rule") else bool(body.get("allow"))
                c["event"].set()
        return {"ok": bool(c)}
    if path == "/api/project":
        name = (body.get("name") or "").strip()
        if not name:
            raise ValueError("Нужно название")
        folder = (body.get("folder") or "").strip().strip('"') or f"projects/{slugify(name)}"
        if Path(folder).is_absolute() and not br._inside(Path(folder).resolve(), br.WORKSPACE):
            target = allow_folder(folder, bool(body.get("readonly")))
        else:
            target = br.resolve_path(folder)
            target.mkdir(parents=True, exist_ok=True)
        projects = load_projects()
        pid = slugify(name).lower() + "-" + uuid.uuid4().hex[:4]
        project = {"id": pid, "name": name, "emoji": (body.get("emoji") or "✨")[:4], "folder": br.rel(target)}
        projects.append(project)
        save_projects(projects)
        ensure_memory(project)
        bump()
        return {"ok": True, "project": project}
    if path == "/api/project/update":
        projects = load_projects()
        for p in projects:
            if p["id"] == body.get("id"):
                if body.get("name"):
                    p["name"] = body["name"].strip()[:60]
                if body.get("emoji"):
                    p["emoji"] = body["emoji"][:4]
        save_projects(projects)
        bump()
        return {"ok": True}
    if path == "/api/project/remove":
        projects = [p for p in load_projects() if p["id"] != body.get("id") or p["id"] == "general"]
        save_projects(projects)
        bump()
        return {"ok": True}
    if path == "/api/open_folder":
        project = get_project(body.get("project") or "general")
        folder = project_dir(project) if project else br.WORKSPACE
        if br.IS_WINDOWS:
            os.startfile(str(folder))  # noqa
        return {"ok": True}
    if path == "/api/upload":
        project = get_project((query or {}).get("project", ["general"])[0])
        name = Path(unquote((query or {}).get("name", ["file"])[0])).name or "file"
        if not project:
            raise ValueError("Нет такого проекта")
        folder = project_dir(project) / "files"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        stem, suf, i = target.stem, target.suffix, 1
        while target.exists():
            target = folder / f"{stem} ({i}){suf}"
            i += 1
        target.write_bytes(raw or b"")
        return {"ok": True, "path": br.rel(target)}
    if path == "/api/settings":
        restart_tunnel = False
        for key, value in body.items():
            if key == "notion_token":
                if value:
                    CFG["notion_token"] = value.strip()
                continue
            if key not in PUBLIC_SETTINGS or key == "workspace":
                continue
            if key in ("tunnel", "ngrok_domain") and CFG.get(key) != value:
                restart_tunnel = True
            CFG[key] = value
        if "autostart" in body:
            make_shortcut("Startup", remove=not body["autostart"])
        _title_prop.clear()
        save_config()
        bump()
        return {"ok": True, "restart_needed": restart_tunnel}
    if path == "/api/notion_check":
        try:
            prop = notion_check()
            with LOCK:
                STATE["notion"] = {"error": "", "last_ok": time.time()}
            return {"ok": True, "title": prop}
        except Exception as e:
            with LOCK:
                STATE["notion"]["error"] = str(e)
            return {"ok": False, "error": str(e)}
    if path.startswith("/api/yanka/"):  # 1.3.0: менеджеры Янки
        if not YANKA:
            raise ValueError("Янка не запустилась - посмотри data/cozycode.log")
        what = path[len("/api/yanka/"):]
        if what == "state":
            return YANKA.state(body.get("project"))
        if what == "local":
            return YANKA.local_report(refresh=True)
        return YANKA.action(what, body)
    if path == "/api/quit":
        threading.Thread(target=lambda: (time.sleep(0.4), QUIT.set()), daemon=True).start()
        return {"ok": True}
    if path == "/api/skills":
        project = get_project(body.get("project") or "general")
        pdir = project_skills_dir(project)
        return {"ok": True, "skills": list_skills(project), "project_dir": br.rel(pdir) if pdir else None}
    if path == "/api/skill/get":
        return {"ok": True, "content": skill_file(body.get("path")).read_text(encoding="utf-8", errors="replace")}
    if path == "/api/skill/save":
        content = body.get("content") or ""
        if not content.strip():
            raise ValueError("Скил пустой")
        if body.get("path"):
            p = skill_file(body["path"])
            br.make_backup(p, "правка скила")
        else:
            project = get_project(body.get("project") or "general")
            folder = project_skills_dir(project) if body.get("scope") == "project" else global_skills_dir()
            if folder is None:
                raise ValueError("У этого проекта нет своей папки для скилов")
            folder.mkdir(parents=True, exist_ok=True)
            stem = slugify(parse_skill_text(content)["name"]).lower()
            p, i = folder / f"{stem}.md", 2
            while p.exists():
                p, i = folder / f"{stem}-{i}.md", i + 1
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": br.rel(p)}
    if path == "/api/skill/toggle":
        set_skill_enabled(str(body.get("key") or ""), bool(body.get("enabled")))
        return {"ok": True}
    if path == "/api/skill/delete":
        br.make_backup(skill_file(body.get("path")), "удаление скила", move=True)
        return {"ok": True}
    if path == "/api/memory":
        project = get_project(body.get("project") or "general")
        if not project:
            raise ValueError("Нет такого проекта")
        p = memory_path(project)
        if "content" in body:
            if p.exists():
                br.make_backup(p, "правка PROJECT.md")
            p.write_text(body.get("content") or "", encoding="utf-8")
            return {"ok": True}
        ensure_memory(project)
        return {"ok": True, "path": br.rel(p), "content": p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""}
    if path == "/api/pick_folder":
        return {"ok": True, "path": pick_folder()}
    if path == "/api/diff":
        with LOCK:
            d = STATE["diffs"].get(body.get("id"))
        if not d:
            raise ValueError("Эти изменения уже не хранятся - старые версии есть в ⚙️ Настройки → ↩️ Откат")
        return {"ok": True, **d}
    if path == "/api/shutdown":
        if body.get("action") == "cancel":
            cancel_shutdown()
        else:
            with LOCK:
                STATE["shutdown"]["armed"] = body.get("action") == "arm"
            bump()
        return {"ok": True}
    if path == "/api/cmdrules":
        rules = CFG.setdefault("allowed_commands", [])
        CFG["allowed_commands"] = [r for r in rules if not (r.get("prefix") == body.get("prefix")
                                                            and r.get("folder") == body.get("folder"))]
        save_config()
        bump()
        return {"ok": True}
    if path == "/api/folders":
        target = Path(str(body.get("path") or "")).resolve()
        folders = CFG.setdefault("extra_folders", [])
        if body.get("action") == "mode":
            for f in folders:
                if Path(f["path"]).resolve() == target:
                    f["mode"] = "ro" if body.get("readonly") else "rw"
        elif body.get("action") == "remove":
            CFG["extra_folders"] = [f for f in folders if Path(f["path"]).resolve() != target]
        save_config()
        bump()
        return {"ok": True}
    if path == "/api/programs":  # 1.2.5: 🧩 Программы
        return br.PROGRAMS.ui_action(body)
    if path == "/api/backups":
        return {"ok": True, "items": list(reversed(br.list_backups()))[:150]}
    if path == "/api/backups/restore":
        return {"ok": True, "result": br.restore_backup(body.get("id"))}
    raise KeyError(path)


class UIHandler(BaseHTTPRequestHandler):
    server_version = "CozyCode/" + VERSION

    def log_message(self, fmt, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError, OSError):
            pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{CFG['ui_port']}", f"localhost:{CFG['ui_port']}")

    def _key_ok(self):
        return secrets.compare_digest(self.headers.get("X-Cozy-Key", ""), UI_KEY)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        if url.path == "/ping":
            return self._send(200, {"app": "cozycode"})
        if url.path == "/show":
            return self._send(200, {"ok": True, "native": nw.show()})
        if url.path in ("/", "/index.html"):
            html = (UI_DIR / "index.html").read_text(encoding="utf-8").replace("__COZY_KEY__", UI_KEY)
            return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
        if url.path.startswith("/ui/"):
            target = (UI_DIR / unquote(url.path[4:])).resolve()
            try:
                target.relative_to(UI_DIR.resolve())
            except ValueError:
                return self._send(404, {"error": "not found"})
            if not target.is_file():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            return self._send(200, target.read_bytes(), ctype)
        if url.path.startswith("/api/"):
            if not self._key_ok():
                return self._send(403, {"error": "bad key"})
            q = parse_qs(url.query)
            if url.path == "/api/state":
                STATE["ui_seen"] = time.time()
                return self._send(200, public_state())
            if url.path == "/api/messages":
                pid = q.get("project", ["general"])[0]
                return self._send(200, {"messages": read_messages(pid)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok() or not self._key_ok():
            return self._send(403, {"error": "forbidden"})
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length > 200 * 1024 * 1024:
            return self._send(413, {"error": "Файл слишком большой (максимум 200 МБ)"})
        raw = self.rfile.read(length) if length else b""
        try:
            if url.path == "/api/upload":
                result = api_post(url.path, {}, raw=raw, query=parse_qs(url.query))
            else:
                body = json.loads(raw.decode("utf-8") or "{}")
                result = api_post(url.path, body)
            return self._send(200, result)
        except KeyError:
            return self._send(404, {"error": "not found"})
        except br.ToolError as e:
            return self._send(400, {"error": str(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


# --------------------------------------------------------------------------- #
# Запуск
# --------------------------------------------------------------------------- #

def already_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CFG['ui_port']}/ping", timeout=1.5) as r:
            return b"cozycode" in r.read()
    except Exception:
        return False


def copy_tools_from_old_bridge():
    copied = []
    for name in ("ngrok", "cloudflared"):
        exe = name + ".exe"
        if (APP_DIR / exe).exists():
            continue
        for cfg in br._old_bridge_configs():
            src = cfg.parent / exe
            if src.exists():
                shutil.copy2(src, APP_DIR / exe)
                copied.append(exe)
                break
    return copied


def install():
    print("=" * 60)
    print("  Установка CozyCode")
    print("=" * 60)
    if CFG.get("imported_from"):
        print(f"  ✓ Ключ моста взят из старого Yana PC Bridge - подключение в Notion менять не нужно.")
    else:
        print("  ! Старый мост не найден - создан новый ключ (см. Настройки → Подключение).")
    for exe in copy_tools_from_old_bridge():
        print(f"  ✓ Скопирован {exe}")
    if not find_exe("ngrok"):
        print("  ! ngrok.exe не найден - положи его в папку CozyCode.")
    if make_shortcut("Desktop"):
        print("  ✓ Ярлык CozyCode создан на рабочем столе")
    else:
        print("  ! Не получилось создать ярлык - запускай CozyCode.bat")
    save_config()
    print("\n  Готово! Запускаю CozyCode…")


def serve(server):
    try:
        server.serve_forever(poll_interval=0.5)
    except Exception:
        traceback.print_exc()


def main():
    if "--install" in sys.argv:
        install()
        if br.IS_WINDOWS and not already_running():
            subprocess.Popen([pythonw_path(), str(APP_DIR / "cozycode.py")], cwd=str(APP_DIR),
                             creationflags=0x00000008 | 0x00000200, close_fds=True)
            time.sleep(1.5)
            return
    if already_running():
        # 1.2.4: CozyCode уже включён - показываем его окно, а не открываем второе
        if not focus_app_window():
            open_window(wait=True)
        return
    br.WORKSPACE.mkdir(parents=True, exist_ok=True)
    for p in load_projects():
        ensure_memory(p)
    global YANKA
    try:
        from yanka.app import YankaService
        YANKA = YankaService(DATA_DIR, br, log)
        YANKA.hooks["to_klodik"] = yanka_to_klodik
        YANKA.hooks["post"] = lambda pid, text: bump("reply", project=pid, who="yanka", preview=text[:120],
                                                       id=append_message(pid, {"role": "assistant", "who": "yanka",
                                                                               "text": text})["id"])
        log("Янка: ядро готово (модели на ПК, память, задачи, разрешения)")
    except Exception as e:
        log(f"Янка: не запустилась ({type(e).__name__}: {e}) - всё работает через Клодика")
    ui = ThreadingHTTPServer(("127.0.0.1", int(CFG["ui_port"])), UIHandler)
    ui.daemon_threads = True
    threading.Thread(target=serve, args=(ui,), daemon=True).start()
    log(f"CozyCode {VERSION}: окно http://127.0.0.1:{CFG['ui_port']}/")

    if CFG.get("bridge_autostart") or inbox_items():
        # включён «сразу» или Клодика ждут сообщения с прошлого раза
        bridge_on("при запуске")
    else:
        log("Мост и туннель выключены - включатся, когда понадобится Клодик")

    native = False
    if "--no-window" not in sys.argv and br.IS_WINDOWS and CFG.get("native_window", True) and nw.available():
        # 1.3.0: своё окно держит главный поток, пока CozyCode не выключат
        log("Окно: своё (pywebview)")
        try:
            native = nw.run(f"http://127.0.0.1:{CFG['ui_port']}/", DATA_DIR,
                            APP_DIR / "ui" / "img" / "cozycode.ico", QUIT, log)
        except Exception as e:
            log(f"Окно: {type(e).__name__}: {e}")
    if not native and "--no-window" not in sys.argv:
        open_window()
    try:
        while not QUIT.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    log("Выключаюсь…")
    bridge_off()
    stop_tunnel()
    br.PROGRAMS.stop_all()
    for s in (ui,):
        if s:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
