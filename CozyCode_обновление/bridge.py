#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Yana PC Bridge - локальный MCP-сервер, через который агент Notion
может работать с файлами в рабочей папке и (с подтверждением) запускать команды.

Запуск:  python server.py   (или двойной клик по start.bat)
Настройки: config.json (создаётся автоматически при первом запуске)
Журнал действий: actions.log

Требуется только Python 3.9+ - никаких дополнительных библиотек.
"""

import atexit
import base64
import datetime
import difflib
import fnmatch
import inspect
import json
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_NAME = "CozyCode"
VERSION = "1.3.0"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
LOG_PATH = BASE_DIR / "actions.log"
BG_LOG_DIR = BASE_DIR / "bg_logs"
CONNECTION_FILE = BASE_DIR / "connection.txt"
IS_WINDOWS = os.name == "nt"

SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", ".next", "dist", "build", ".cache"}
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------- #
# Настройки
# --------------------------------------------------------------------------- #

DEFAULT_CONFIG = {
    "host": "127.0.0.1",
    "port": 8765,
    "workspace": str(Path.home() / "NotionAgentWorkspace"),
    "confirm_commands": True,
    "confirm_deletes": True,
    "confirm_edits": False,
    "confirm_timeout_seconds": 120,
    "default_command_timeout_seconds": 120,
    "max_output_chars": 20000,
    "auto_tunnel": False,
    "ui_port": 8766,
    "tunnel": "ngrok",
    "ngrok_domain": "",
    "notion_token": "",
    "notion_database": "",
    "sound": True,
    "notifications": True,
    "native_window": True,
    "autostart": False,
    "history_messages": 10,
    "yanka_mode": "auto",
    "bridge_autostart": False,
    "extra_folders": [],
    "allowed_commands": [],
    "mcp_programs": [],          # 1.2.5: 🧩 Программы (Blender, Roblox Studio и др.)
    "mcp_autodetected": False,
}

HOOKS = {}


def _hook(name, *args):
    fn = HOOKS.get(name)
    if fn:
        try:
            return fn(*args)
        except Exception:
            traceback.print_exc()
    return None


def _old_bridge_configs():
    home = Path.home()
    names = ["Desktop", "Рабочий стол", "OneDrive/Desktop", "OneDrive/Рабочий стол", "Downloads", "Загрузки"]
    cands = [BASE_DIR.parent / "YanaPCBridge" / "config.json"]
    cands += [home / n / "YanaPCBridge" / "config.json" for n in names]
    out = []
    for c in cands:
        try:
            if c.exists() and c.resolve() != CONFIG_PATH.resolve():
                out.append(c)
        except OSError:
            pass
    return out


def import_old_config(cfg):
    """Берём ключ и папку из старого моста, чтобы подключение в Notion продолжило работать."""
    for path in _old_bridge_configs():
        try:
            old = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if old.get("token"):
            cfg["token"] = old["token"]
            for key in ("workspace", "confirm_commands", "confirm_deletes", "port"):
                if key in old:
                    cfg[key] = old[key]
            cfg["imported_from"] = str(path)
            return True
    return False


def load_config():
    cfg = {}
    changed = False
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    elif import_old_config(cfg):
        changed = True
    for key, value in DEFAULT_CONFIG.items():
        if key not in cfg:
            cfg[key] = value
            changed = True
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(32)
        changed = True
    if changed:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


CONFIG = load_config()
WORKSPACE = Path(os.path.expandvars(os.path.expanduser(CONFIG["workspace"]))).resolve()


# --------------------------------------------------------------------------- #
# Разрешённые папки, защита системных папок, резервные копии (1.2)
# --------------------------------------------------------------------------- #

def _protected_roots():
    roots = []
    if IS_WINDOWS:
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
            v = os.environ.get(var)
            if v:
                roots.append(Path(v))
        drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        roots += [drive / "$Recycle.Bin", drive / "System Volume Information", drive / "Recovery", drive / "Boot"]
    else:
        roots += [Path("/bin"), Path("/etc"), Path("/usr"), Path("/proc"), Path("/sys"), Path("/boot")]
    out = []
    for r in roots:
        try:
            out.append(r.resolve())
        except Exception:
            pass
    return out


PROTECTED = _protected_roots()


def _inside(p, root):
    try:
        Path(p).relative_to(root)
        return True
    except ValueError:
        return False


def is_protected(p):
    return any(_inside(p, r) for r in PROTECTED)


def extra_roots():
    out = []
    for f in CONFIG.get("extra_folders") or []:
        try:
            path = str(f.get("path") or "").strip()
            if path:
                out.append((Path(os.path.expandvars(path)).resolve(), f.get("mode") or "rw"))
        except Exception:
            pass
    return out


def folder_root(p):
    """(корень, режим) для пути: режим 'ws' - рабочая папка, 'rw'/'ro' - разрешённая папка. None - нет доступа."""
    p = Path(p)
    if _inside(p, WORKSPACE):
        return (WORKSPACE, "ws")
    best = None
    for root, mode in extra_roots():
        if _inside(p, root) and (best is None or len(str(root)) > len(str(best[0]))):
            best = (root, mode)
    return best


BACKUP_DIR = BASE_DIR / "backups"
BACKUP_INDEX = BACKUP_DIR / "index.json"
BACKUP_KEEP = 300
BACKUP_MAX_BYTES = 200 * 1024 * 1024
_backup_lock = threading.Lock()


def list_backups():
    try:
        return json.loads(BACKUP_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return []


def _tree_size(p):
    if p.is_file():
        return p.stat().st_size
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def make_backup(p, action, move=False):
    """Сохранить копию перед изменением. move=True - перенести в копии (вместо удаления)."""
    p = Path(p)
    if not p.exists():
        return None
    if not move and _tree_size(p) > BACKUP_MAX_BYTES:
        return None
    bid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    dest = BACKUP_DIR / bid / p.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if move:
        shutil.move(str(p), str(dest))
    elif p.is_dir():
        shutil.copytree(p, dest)
    else:
        shutil.copy2(p, dest)
    with _backup_lock:
        items = list_backups()
        items.append({"id": bid, "time": now(), "path": str(p), "name": p.name, "action": action,
                      "is_dir": dest.is_dir(), "restored": False})
        old, items = items[:-BACKUP_KEEP], items[-BACKUP_KEEP:]
        for o in old:
            shutil.rmtree(BACKUP_DIR / o["id"], ignore_errors=True)
        BACKUP_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return bid


def restore_backup(bid):
    item = next((i for i in list_backups() if i["id"] == bid), None)
    if not item:
        raise ToolError("Копия не найдена.")
    src = BACKUP_DIR / bid / item["name"]
    if not src.exists():
        raise ToolError("Файлы этой копии пропали.")
    target = Path(item["path"])
    if target.exists():
        make_backup(target, "версия перед откатом", move=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, target)
    else:
        shutil.copy2(src, target)
    with _backup_lock:
        items = list_backups()
        for i in items:
            if i["id"] == bid:
                i["restored"] = True
        BACKUP_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"Вернула как было: {item['name']} ✨"


def to_recycle_bin(p):
    p = Path(p)
    if not IS_WINDOWS:
        make_backup(p, "удаление", move=True)
        return
    kind = "DeleteDirectory" if p.is_dir() else "DeleteFile"
    lit = "'" + str(p).replace("'", "''") + "'"
    script = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
              f"[Microsoft.VisualBasic.FileIO.FileSystem]::{kind}({lit}, 'OnlyErrorDialogs', 'SendToRecycleBin')")
    r = subprocess.run(build_command(script, "powershell"), capture_output=True, timeout=900,
                       creationflags=0x08000000)
    if p.exists():
        raise ToolError("Не получилось отправить в Корзину: " + decode_output((r.stdout or b"") + (r.stderr or b""))[:400])



# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #

class ToolError(Exception):
    pass


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def console(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


_log_lock = threading.Lock()


def log_action(tool_name, args, status, detail=""):
    short = {}
    for k, v in (args or {}).items():
        s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
        short[k] = s if len(s) <= 300 else s[:300] + "..."
    line = json.dumps({"time": now(), "tool": tool_name, "args": short, "status": status, "detail": detail[:500]}, ensure_ascii=False)
    with _log_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    _hook("activity", tool_name, short, status, detail)


def _strip_long_prefix(p):
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + s[8:])
    if s.startswith("\\\\?\\"):
        return Path(s[4:])
    return p


def _resolve_hit(candidate):
    """1.2.6: resolve() + проверка папки с повтором. Пока соседний вызов создаёт ту же папку,
    Windows может на миг вернуть путь с префиксом \\\\?\\ или недосозданный - тогда пробуем ещё раз."""
    resolved, hit = candidate, None
    for attempt in range(5):
        try:
            resolved = _strip_long_prefix(Path(candidate).resolve())
        except OSError:
            resolved = _strip_long_prefix(Path(os.path.abspath(candidate)))
        hit = folder_root(resolved)
        if hit:
            break
        time.sleep(0.05 * (attempt + 1))
    return resolved, hit


def resolve_path(p, write=False):
    """Путь внутри рабочей папки или папки, которую Яна разрешила в настройках."""
    p = (p or ".").strip().strip('"')
    candidate = Path(os.path.expandvars(p))
    if not candidate.is_absolute():
        candidate = WORKSPACE / candidate
    candidate = candidate.resolve()
    candidate, hit = _resolve_hit(candidate)
    if not hit:
        raise ToolError(f"Доступ запрещён: путь '{p}' вне рабочей папки {WORKSPACE} и разрешённых папок "
                        "(их Яна добавляет при создании проекта, список - ⚙️ Настройки → 📁 Папки).")
    root, mode = hit
    if mode != "ws" and is_protected(candidate):
        raise ToolError(f"Доступ запрещён: {candidate} - системная папка Windows.")
    if write:
        if mode == "ro":
            raise ToolError(f"Папка {root} открыта только для просмотра. Чтобы что-то менять, Яна должна "
                            "разрешить изменения (⚙️ Настройки → 📁 Папки).")
        if _inside(candidate, BASE_DIR):
            raise ToolError("Файлы самого CozyCode менять нельзя - обновления кладутся в CozyCode_обновление.")
    return candidate


def rel(p):
    p = Path(p)
    try:
        r = str(p.relative_to(WORKSPACE))
    except ValueError:
        return str(p)
    return "." if r in ("", ".") else r


def fmt_size(n):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТБ"


def clip(text, limit=None):
    limit = int(limit or CONFIG["max_output_chars"])
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n... [обрезано {len(text) - limit} символов] ...\n\n{text[-half:]}"


def is_binary(data):
    return b"\x00" in data[:8192]


def decode_output(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        if IS_WINDOWS:
            try:
                return data.decode("cp866")
            except UnicodeDecodeError:
                pass
        return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Подтверждение действий (всплывающее окно Windows)
# --------------------------------------------------------------------------- #

_confirm_lock = threading.Lock()


_ACTOR = threading.local()


def actor():
    """1.3.0: кто сейчас действует - для текста подтверждений."""
    return getattr(_ACTOR, "name", "Клодик")


def call_tool_as(name, args, who):
    _ACTOR.name = who
    try:
        return call_tool(name, args)
    finally:
        _ACTOR.name = "Клодик"


def ask_user(title, message, rule=None, diff=None):
    message = message if len(message) <= 1800 else message[:1800] + "..."
    with _confirm_lock:
        via_app = _hook("confirm", title, message, rule, diff)
        if via_app is not None:
            if via_app == "always" and rule:
                remember_rule(rule)
            return bool(via_app)
        if diff:
            message = (message + "\n\n" + diff)[:1800]
        if IS_WINDOWS:
            import ctypes
            # Да/Нет + вопрос + поверх всех окон; по умолчанию выбрано "Нет"
            flags = 0x4 | 0x20 | 0x100 | 0x1000 | 0x40000
            timeout_ms = int(CONFIG["confirm_timeout_seconds"]) * 1000
            user32 = ctypes.windll.user32
            try:
                fn = user32.MessageBoxTimeoutW
                fn.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                               ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint]
                fn.restype = ctypes.c_int
                result = fn(None, message, title, flags, 0, timeout_ms)
            except AttributeError:
                result = user32.MessageBoxW(None, message, title, flags)
            return result == 6  # IDYES
        auto = os.environ.get("BRIDGE_AUTO_CONFIRM", "").lower()
        if auto in ("yes", "no"):
            return auto == "yes"
        if sys.stdin and sys.stdin.isatty():
            answer = input(f"\n{title}\n{message}\nРазрешить? [y/N]: ")
            return answer.strip().lower() in ("y", "yes", "д", "да")
        return False


def make_diff(old, new, name):
    """1.2.2: изменения в файле (красное/зелёное) для журнала и окна подтверждения."""
    lines = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"было: {name}", f"стало: {name}",
                                      n=2, lineterm=""))
    plus = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
    if len(lines) > 200:
        lines = lines[:200] + [f"… и ещё {len(lines) - 200} строк"]
    text = "\n".join(lines)
    if len(text) > 12000:
        text = text[:12000] + "\n…"
    return text, plus, minus


def confirm_or_fail(title, message, rule=None, diff=None):
    console(f"Жду подтверждения: {title}")
    if not ask_user(title, message, rule, diff):
        raise ToolError("Действие отклонено пользователем (или не было ответа вовремя).")


# 1.2.1: «Всегда разрешать в этой папке» - простые команды без вопросов.
# Удаление, цепочки команд (; & | > и т.п.) и опасные программы так не разрешаются никогда.
_RULE_DENY = {"remove-item", "rm", "ri", "del", "erase", "rd", "rmdir", "format", "format-volume", "diskpart",
              "shutdown", "stop-computer", "restart-computer", "reg", "regedit", "takeown", "icacls", "cacls",
              "cipher", "set-executionpolicy", "invoke-expression", "iex", "invoke-webrequest", "iwr",
              "invoke-restmethod", "irm", "curl", "wget", "start-process", "saps", "start", "powershell", "pwsh",
              "cmd", "wsl", "bash", "sh", "sudo", "runas", "taskkill", "stop-process", "kill", "net", "netsh", "sc",
              "schtasks", "bcdedit", "attrib", "move-item", "mv", "move", "copy-item", "cp", "copy", "xcopy",
              "robocopy", "set-content", "out-file", "clear-content", "new-item", "ren", "rename-item"}
_RULE_SUB = {"git", "npm", "pnpm", "yarn", "pip", "pip3", "winget", "choco", "dotnet", "cargo", "conda", "uv"}


def command_rule(command, wd):
    """Правило «эта программа в этой папке» или None, если команду так разрешать нельзя."""
    cmd = (command or "").strip()
    if not cmd or len(cmd) > 400 or re.search(r"[;&|<>`\r\n]|\$\(|\$\{", cmd):
        return None
    if re.search(r"(^|\s)[-/](c|e|enc|command|encodedcommand)(\s|$)", cmd, re.I):
        return None
    parts = cmd.split()
    first = re.split(r"[\\/]", parts[0].strip("'\""))[-1].lower()
    if first.endswith(".exe"):
        first = first[:-4]
    if not first or first in _RULE_DENY:
        return None
    prefix = first
    if first in _RULE_SUB and len(parts) > 1 and re.fullmatch(r"[\w.-]+", parts[1]):
        prefix += " " + parts[1].lower()
    return {"prefix": prefix, "folder": str(Path(wd).resolve())}


def command_allowed(command, wd):
    rule = command_rule(command, wd)
    if not rule:
        return False
    here = Path(rule["folder"])
    for r in CONFIG.get("allowed_commands") or []:
        try:
            f = Path(r.get("folder", "")).resolve()
        except Exception:
            continue
        if r.get("prefix") == rule["prefix"] and (here == f or f in here.parents):
            console(f"Разрешено правилом «{rule['prefix']}» для {f}")
            return True
    return False


def remember_rule(rule):
    if rule.get("kind") == "mcp":  # 1.2.5: «✅ Всегда» для инструмента программы
        PROGRAMS.remember(rule)
        return
    rules = CONFIG.setdefault("allowed_commands", [])
    if any(r.get("prefix") == rule["prefix"] and r.get("folder") == rule["folder"] for r in rules):
        return
    rules.append({"prefix": rule["prefix"], "folder": rule["folder"], "time": time.strftime("%Y-%m-%d %H:%M")})
    try:
        CONFIG_PATH.write_text(json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        traceback.print_exc()


# --------------------------------------------------------------------------- #
# Запуск процессов
# --------------------------------------------------------------------------- #

def popen_kwargs():
    if IS_WINDOWS:
        return {"creationflags": 0x08000000 | 0x00000200}  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    return {"start_new_session": True}


def build_command(command, shell):
    shell = (shell or "powershell").lower()
    if IS_WINDOWS:
        if shell == "cmd":
            return f'cmd.exe /d /s /c "chcp 65001>nul & {command}"'
        script = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                  "$OutputEncoding=[System.Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
                  + command)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    return ["bash", "-lc", command]


def kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, creationflags=0x08000000)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


BACKGROUND = {}
_bg_lock = threading.Lock()
_bg_counter = [0]


# --------------------------------------------------------------------------- #
# Инструменты
# --------------------------------------------------------------------------- #

TOOLS = []
HANDLERS = {}


def tool(name, description, properties=None, required=(), read_only=False, destructive=False):
    def deco(fn):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": properties or {}, "required": list(required)},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive},
        })
        HANDLERS[name] = fn
        return fn
    return deco


S = lambda d: {"type": "string", "description": d}
I = lambda d: {"type": "integer", "description": d}
B = lambda d: {"type": "boolean", "description": d}
SHELL = {"type": "string", "enum": ["powershell", "cmd"], "description": "Оболочка (по умолчанию powershell)"}


@tool("get_info", "Информация о ПК, рабочей папке, настройках подтверждений и фоновых процессах.", read_only=True)
def get_info():
    with _bg_lock:
        running = sum(1 for b in BACKGROUND.values() if b["proc"].poll() is None)
    return "\n".join([
        f"{APP_NAME} v{VERSION}",
        f"ОС: {platform.system()} {platform.release()} ({platform.machine()})",
        f"Python: {platform.python_version()}",
        f"Рабочая папка: {WORKSPACE}",
        f"Подтверждение команд: {'да' if CONFIG['confirm_commands'] else 'нет'}",
        f"Подтверждение удаления: {'да' if CONFIG['confirm_deletes'] else 'нет'}",
        f"Фоновых процессов запущено: {running}",
        "Пути - относительно рабочей папки (или абсолютные для разрешённых папок).",
        "Разрешённые папки: " + (", ".join(f"{r} ({'только просмотр' if m == 'ro' else 'можно менять'})" for r, m in extra_roots()) or "нет"),
        "Правила работы с приложением CozyCode: прочитай AGENTS.md в корне рабочей папки.",
    ])


@tool("list_dir", "Показать содержимое папки (относительно рабочей папки).",
      {"path": S("Папка, по умолчанию '.'"), "recursive": B("Показать вложенные папки"),
       "max_entries": I("Максимум строк, по умолчанию 500")}, read_only=True)
def list_dir(path=".", recursive=False, max_entries=500):
    p = resolve_path(path)
    if not p.exists():
        raise ToolError(f"Не найдено: {path}")
    if p.is_file():
        return f"{rel(p)} - файл, {fmt_size(p.stat().st_size)}"
    lines = []

    def entry(item, depth):
        indent = "  " * depth
        if item.is_dir():
            return f"{indent}[папка] {item.name}/"
        try:
            size = fmt_size(item.stat().st_size)
        except OSError:
            size = "?"
        return f"{indent}{item.name} ({size})"

    def walk(folder, depth):
        try:
            items = sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            lines.append("  " * depth + "(нет доступа)")
            return
        for item in items:
            if len(lines) >= max_entries:
                return
            lines.append(entry(item, depth))
            if recursive and item.is_dir():
                if item.name in SKIP_DIRS:
                    lines[-1] += " (содержимое пропущено)"
                else:
                    walk(item, depth + 1)

    walk(p, 0)
    header = f"Папка: {rel(p)}"
    if not lines:
        return header + "\n(пусто)"
    if len(lines) >= max_entries:
        lines.append(f"... показано {max_entries} строк, есть ещё")
    return header + "\n" + "\n".join(lines)


@tool("read_file", "Прочитать текстовый файл.",
      {"path": S("Путь к файлу"), "start_line": I("С какой строки (с 1)"),
       "max_lines": I("Сколько строк, по умолчанию 2000")}, required=["path"], read_only=True)
def read_file(path, start_line=1, max_lines=2000):
    p = resolve_path(path)
    if not p.is_file():
        raise ToolError(f"Файл не найден: {path}")
    data = p.read_bytes()
    if is_binary(data):
        return f"{rel(p)} - бинарный файл ({fmt_size(len(data))}), прочитать как текст нельзя."
    lines = data.decode("utf-8", errors="replace").splitlines()
    start = max(1, int(start_line))
    end = min(len(lines), start + int(max_lines) - 1)
    body = "\n".join(lines[start - 1:end])
    return clip(f"Файл: {rel(p)} | строки {start}-{end} из {len(lines)}\n---\n{body}")


@tool("write_file", "Создать или перезаписать текстовый файл (UTF-8). Папки создаются автоматически.",
      {"path": S("Путь к файлу"), "content": S("Содержимое"), "append": B("Дописать в конец вместо перезаписи")},
      required=["path", "content"])
def write_file(path, content, append=False):
    p = resolve_path(path, write=True)
    if p.is_dir():
        raise ToolError(f"Это папка, а не файл: {path}")
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    old = ""
    if existed:
        try:
            old = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            old = ""
    diff, plus, minus = make_diff(old, old + content if append else content, rel(p))
    if existed and CONFIG.get("confirm_edits"):
        confirm_or_fail(f"{APP_NAME}: изменение файла",
                        f"Агент Notion хочет {'дописать' if append else 'перезаписать'} файл {rel(p)} (+{plus} −{minus}).\n\nРазрешить?",
                        diff=diff)
    if existed and not append:
        make_backup(p, "перезапись")
    with open(p, "a" if append else "w", encoding="utf-8", newline="") as f:
        f.write(content)
    _hook("diff", rel(p), diff, plus, minus)
    action = "дописан" if append else ("перезаписан" if existed else "создан")
    return f"Файл {rel(p)} {action} ({fmt_size(p.stat().st_size)})."


@tool("edit_file", "Заменить фрагмент текста в файле. old_text должен встречаться ровно один раз (или replace_all=true).",
      {"path": S("Путь к файлу"), "old_text": S("Что заменить (точное совпадение)"),
       "new_text": S("На что заменить"), "replace_all": B("Заменить все вхождения")},
      required=["path", "old_text", "new_text"])
def edit_file(path, old_text, new_text, replace_all=False):
    p = resolve_path(path, write=True)
    if not p.is_file():
        raise ToolError(f"Файл не найден: {path}")
    with open(p, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    count = text.count(old_text)
    if count == 0:
        raise ToolError("Фрагмент не найден в файле.")
    if count > 1 and not replace_all:
        raise ToolError(f"Фрагмент найден {count} раз. Уточни его или передай replace_all=true.")
    new = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
    diff, plus, minus = make_diff(text, new, rel(p))
    if CONFIG.get("confirm_edits"):
        confirm_or_fail(f"{APP_NAME}: правка файла",
                        f"Агент Notion хочет изменить файл {rel(p)} (+{plus} −{minus}).\n\nРазрешить?", diff=diff)
    make_backup(p, "правка")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    _hook("diff", rel(p), diff, plus, minus)
    return f"Файл {rel(p)} изменён (замен: {count if replace_all else 1})."


@tool("make_dir", "Создать папку (вместе с родительскими).", {"path": S("Путь к папке")}, required=["path"])
def make_dir(path):
    p = resolve_path(path, write=True)
    p.mkdir(parents=True, exist_ok=True)
    return f"Папка {rel(p)} готова."


@tool("copy_path", "Скопировать файл или папку.",
      {"source": S("Откуда"), "destination": S("Куда (полный новый путь)"), "overwrite": B("Перезаписать, если существует")},
      required=["source", "destination"])
def copy_path(source, destination, overwrite=False):
    src, dst = resolve_path(source), resolve_path(destination, write=True)
    if not src.exists():
        raise ToolError(f"Не найдено: {source}")
    if dst.exists() and not overwrite:
        raise ToolError(f"Уже существует: {destination}")
    if dst.exists():
        make_backup(dst, "перезапись при копировании")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=overwrite)
    else:
        shutil.copy2(src, dst)
    return f"Скопировано: {rel(src)} -> {rel(dst)}"


@tool("move_path", "Переместить или переименовать файл/папку.",
      {"source": S("Откуда"), "destination": S("Куда (полный новый путь)"), "overwrite": B("Перезаписать, если существует")},
      required=["source", "destination"])
def move_path(source, destination, overwrite=False):
    src, dst = resolve_path(source, write=True), resolve_path(destination, write=True)
    if src == WORKSPACE or any(src == r for r, _ in extra_roots()):
        raise ToolError("Нельзя перемещать саму рабочую папку.")
    if not src.exists():
        raise ToolError(f"Не найдено: {source}")
    if dst.exists():
        if not overwrite:
            raise ToolError(f"Уже существует: {destination}")
        make_backup(dst, "перезапись при перемещении", move=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return f"Перемещено: {rel(src)} -> {rel(dst)}"


@tool("delete_path", "Удалить файл или папку (может потребоваться подтверждение пользователя).",
      {"path": S("Что удалить"), "recursive": B("Удалить непустую папку целиком")},
      required=["path"], destructive=True)
def delete_path(path, recursive=False):
    p = resolve_path(path, write=True)
    if p == WORKSPACE or any(p == r for r, _ in extra_roots()):
        raise ToolError("Нельзя удалить саму рабочую папку или разрешённую папку целиком - удаляй содержимое по частям.")
    if any(_inside(r, p) for r in PROTECTED) or _inside(BASE_DIR, p):
        raise ToolError("Внутри есть системные папки Windows или файлы CozyCode - такое удалять нельзя.")
    if not p.exists():
        raise ToolError(f"Не найдено: {path}")
    if p.is_dir() and any(p.iterdir()) and not recursive:
        raise ToolError("Папка не пустая. Передай recursive=true, чтобы удалить её целиком.")
    inside_ws = _inside(p, WORKSPACE)
    if CONFIG["confirm_deletes"]:
        kind = "папку со всем содержимым" if p.is_dir() else "файл"
        where = ("Копия сохранится - вернуть можно в ⚙️ Настройки → ↩️ Откат." if inside_ws
                 else "Файлы попадут в Корзину Windows.")
        confirm_or_fail(f"{APP_NAME}: удаление", f"Агент Notion хочет удалить {kind}:\n\n{p}\n\n{where}\n\nРазрешить?")
    if inside_ws:
        make_backup(p, "удаление", move=True)
        return f"Удалено: {rel(p)} (копия сохранена - Яна может вернуть её в ⚙️ Настройки → ↩️ Откат)"
    to_recycle_bin(p)
    return f"Отправлено в Корзину: {p}"


@tool("search_files", "Найти файлы по маске имени и (необязательно) по тексту внутри.",
      {"pattern": S("Маска имени, например *.py (по умолчанию *)"), "text": S("Искать этот текст внутри файлов"),
       "path": S("Где искать, по умолчанию '.'"), "max_results": I("Максимум результатов, по умолчанию 200")},
      read_only=True)
def search_files(pattern="*", text="", path=".", max_results=200):
    root = resolve_path(path)
    results = []
    needle = (text or "").lower()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not fnmatch.fnmatch(name.lower(), (pattern or "*").lower()):
                continue
            fp = Path(dirpath) / name
            if not needle:
                results.append(rel(fp))
            else:
                try:
                    if fp.stat().st_size > 2_000_000:
                        continue
                    data = fp.read_bytes()
                except OSError:
                    continue
                if is_binary(data):
                    continue
                for i, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
                    if needle in line.lower():
                        results.append(f"{rel(fp)}:{i}: {line.strip()[:200]}")
                        if len(results) >= max_results:
                            break
            if len(results) >= max_results:
                return "\n".join(results) + f"\n... достигнут лимит {max_results}"
    return "\n".join(results) if results else "Ничего не найдено."


@tool("run_command", "Выполнить команду на ПК (PowerShell или cmd) и дождаться результата. "
      "Пользователь подтверждает каждую команду во всплывающем окне.",
      {"command": S("Команда"), "cwd": S("Папка запуска, по умолчанию '.'"), "shell": SHELL,
       "timeout_seconds": I("Таймаут в секундах (по умолчанию 120, максимум 1800)")},
      required=["command"], destructive=True)
def run_command(command, cwd=".", shell="powershell", timeout_seconds=None):
    if not command or not command.strip():
        raise ToolError("Пустая команда.")
    wd = resolve_path(cwd)
    if not wd.is_dir():
        raise ToolError(f"Папка не найдена: {cwd}")
    timeout = max(1, min(int(timeout_seconds or CONFIG["default_command_timeout_seconds"]), 1800))
    if CONFIG["confirm_commands"] and not command_allowed(command, wd):
        confirm_or_fail(f"{APP_NAME}: запуск команды",
                        f"Агент Notion хочет выполнить команду:\n\n{command}\n\nПапка: {wd}\nОболочка: {shell}\n\nРазрешить?",
                        command_rule(command, wd))
    console(f"Выполняю: {command[:120]}")
    started = time.time()
    proc = subprocess.Popen(build_command(command, shell), cwd=str(wd), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **popen_kwargs())
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except Exception:
            out = b""
    output = decode_output(out or b"").strip()
    status = "остановлена по таймауту" if timed_out else f"код выхода {proc.returncode}"
    result = f"Команда завершена: {status}, {time.time() - started:.1f} с\n---\n{output or '(нет вывода)'}"
    if timed_out:
        result += "\n\nПодсказка: для долгих процессов (серверы, watch) используй start_background."
    return clip(result)


@tool("start_background", "Запустить долгий процесс в фоне (dev-сервер, сборка и т.п.). Нужно подтверждение пользователя.",
      {"command": S("Команда"), "cwd": S("Папка запуска"), "shell": SHELL, "name": S("Понятное имя процесса")},
      required=["command"], destructive=True)
def start_background(command, cwd=".", shell="powershell", name=""):
    wd = resolve_path(cwd)
    if not wd.is_dir():
        raise ToolError(f"Папка не найдена: {cwd}")
    if CONFIG["confirm_commands"] and not command_allowed(command, wd):
        confirm_or_fail(f"{APP_NAME}: фоновый процесс",
                        f"Агент Notion хочет запустить в фоне:\n\n{command}\n\nПапка: {wd}\n\nРазрешить?",
                        command_rule(command, wd))
    BG_LOG_DIR.mkdir(exist_ok=True)
    with _bg_lock:
        _bg_counter[0] += 1
        bg_id = f"bg{_bg_counter[0]}"
    log_path = BG_LOG_DIR / f"{bg_id}.log"
    logf = open(log_path, "wb")
    proc = subprocess.Popen(build_command(command, shell), cwd=str(wd), stdout=logf,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **popen_kwargs())
    with _bg_lock:
        BACKGROUND[bg_id] = {"proc": proc, "command": command, "name": name or command[:40],
                             "cwd": str(wd), "log": log_path, "logf": logf, "started": now()}
    console(f"Фоновый процесс {bg_id}: {command[:100]}")
    time.sleep(2)
    return f"Запущен фоновый процесс {bg_id} (PID {proc.pid}).\n\n" + read_background_output(bg_id, 40)


@tool("list_background", "Список фоновых процессов и их статус.", read_only=True)
def list_background():
    with _bg_lock:
        items = list(BACKGROUND.items())
    if not items:
        return "Фоновых процессов нет."
    lines = []
    for bg_id, b in items:
        code = b["proc"].poll()
        state = "работает" if code is None else f"завершён (код {code})"
        lines.append(f"{bg_id}: {b['name']} | {state} | PID {b['proc'].pid} | с {b['started']} | {b['cwd']}")
    return "\n".join(lines)


@tool("read_background_output", "Последние строки вывода фонового процесса.",
      {"id": S("ID процесса, например bg1"), "tail_lines": I("Сколько последних строк, по умолчанию 80")},
      required=["id"], read_only=True)
def read_background_output(id, tail_lines=80):
    b = BACKGROUND.get(id)
    if not b:
        raise ToolError(f"Нет процесса {id}")
    try:
        b["logf"].flush()
    except Exception:
        pass
    text = decode_output(b["log"].read_bytes()) if b["log"].exists() else ""
    lines = text.splitlines()[-int(tail_lines):]
    code = b["proc"].poll()
    state = "работает" if code is None else f"завершён (код {code})"
    return clip(f"{id} ({b['name']}): {state}\n---\n" + ("\n".join(lines) or "(пока нет вывода)"))


@tool("stop_background", "Остановить фоновый процесс.", {"id": S("ID процесса, например bg1")}, required=["id"])
def stop_background(id):
    b = BACKGROUND.get(id)
    if not b:
        raise ToolError(f"Нет процесса {id}")
    kill_tree(b["proc"])
    try:
        b["proc"].wait(timeout=10)
    except Exception:
        pass
    try:
        b["logf"].close()
    except Exception:
        pass
    return f"Процесс {id} остановлен."


# --------------------------------------------------------------------------- #
# MCP (JSON-RPC поверх HTTP, транспорт Streamable HTTP)
# --------------------------------------------------------------------------- #

# 1.2.5: 🧩 Программы (Blender, Roblox Studio и др. по MCP) - в отдельном файле programs.py
import programs as PROGRAMS  # noqa: E402
PROGRAMS.setup(sys.modules[__name__])

INSTRUCTIONS = (
    f"{APP_NAME}: доступ к ПК Яны (Windows). Пути - относительно рабочей папки {WORKSPACE}; "
    "кроме неё доступны только папки, которые Яна разрешила (абсолютные пути; папка проекта указана в cozy_get_inbox). "
    "Перед перезаписью и удалением в рабочей папке делается резервная копия, вне её удаление идёт в Корзину. Команды и удаление Яна подтверждает во всплывающем окне - "
    "если действие отклонено, не повторяй его без её согласия. Для серверов и долгих задач используй start_background. "
    "Сообщения из приложения CozyCode забирай через cozy_get_inbox, ход работы показывай через cozy_status, "
    "а итоговый ответ отправляй через cozy_reply. Спросить Яну по ходу работы, не заканчивая её: cozy_ask. "
    "Программы на ПК (Blender, Roblox Studio и др.): mcp_list, mcp_tools, mcp_call. "
    "Если ты здесь впервые (новый аккаунт или новый агент) - сначала прочитай файл AGENTS.md "
    "в корне рабочей папки (read_file AGENTS.md): там все правила работы с CozyCode."
)


def call_tool(name, args):
    fn = HANDLERS.get(name)
    if not fn:
        raise RpcError(-32602, f"Неизвестный инструмент: {name}")
    args = args or {}
    try:
        inspect.signature(fn).bind(**args)
    except TypeError as e:
        log_action(name, args, "bad_args", str(e))
        return {"content": [{"type": "text", "text": f"Неверные аргументы: {e}"}], "isError": True}
    console(f"-> {name} {json.dumps(args, ensure_ascii=False)[:150]}")
    _hook("activity", name, args, "start", "")
    try:
        text = fn(**args)
        log_action(name, args, "ok")
        if isinstance(text, dict) and "content" in text:  # 1.2.5: ответ программы (текст и картинки) как есть
            return text
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except ToolError as e:
        log_action(name, args, "error", str(e))
        console(f"   ! {e}")
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:
        log_action(name, args, "exception", traceback.format_exc())
        console(f"   ! Ошибка: {e}")
        return {"content": [{"type": "text", "text": f"Ошибка: {type(e).__name__}: {e}"}], "isError": True}


def handle_rpc(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    method = msg.get("method")
    if method is None or "id" not in msg:  # ответы клиента и уведомления
        return None
    mid = msg.get("id")
    params = msg.get("params") or {}
    try:
        if method == "initialize":
            requested = params.get("protocolVersion")
            result = {
                "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "yana-pc-bridge", "title": APP_NAME, "version": VERSION},
                "instructions": INSTRUCTIONS,
            }
            console("Агент подключился")
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = call_tool(params.get("name"), params.get("arguments"))
        elif method == "resources/list":
            result = {"resources": []}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        else:
            raise RpcError(-32601, f"Method not found: {method}")
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    except RpcError as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": e.code, "message": e.message}}
    except Exception as e:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}


class Handler(BaseHTTPRequestHandler):
    server_version = "YanaPCBridge/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def log_error(self, fmt, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError, OSError):
            pass

    def do_HEAD(self):
        self.send_response(200 if urlparse(self.path).path in ("/", "/health") else 405)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        token = CONFIG["token"]
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer ") and secrets.compare_digest(auth[7:].strip(), token):
            return True
        api_key = self.headers.get("X-API-Key")
        if api_key and secrets.compare_digest(api_key.strip(), token):
            return True
        key = parse_qs(urlparse(self.path).query).get("key", [None])[0]
        return bool(key) and secrets.compare_digest(key, token)

    def _send(self, code, body=None, extra_headers=None):
        data = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _read_body(self):
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            chunks = []
            while True:
                size = int(self.rfile.readline().strip().split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _unauthorized(self):
        console(f"Отклонён запрос без верного ключа от {self.client_address[0]}")
        self._send(401, {"error": "unauthorized"}, {"WWW-Authenticate": "Bearer"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/health"):
            self._send(200, {"status": "ok", "name": APP_NAME})
        elif path == "/mcp":
            if not self._authorized():
                return self._unauthorized()
            self._send(405, {"error": "use POST"}, {"Allow": "POST, DELETE"})
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        if urlparse(self.path).path != "/mcp":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._unauthorized()
        self._send(200, {})

    def do_POST(self):
        _hook("request")
        if urlparse(self.path).path != "/mcp":
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._unauthorized()
        try:
            msg = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            return self._send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        headers = {}
        if isinstance(msg, list):
            responses = [r for r in (handle_rpc(m) for m in msg) if r is not None]
            if any(isinstance(m, dict) and m.get("method") == "initialize" for m in msg):
                headers["Mcp-Session-Id"] = uuid.uuid4().hex
            return self._send(200, responses, headers) if responses else self._send(202)
        response = handle_rpc(msg)
        if response is None:
            return self._send(202)
        if isinstance(msg, dict) and msg.get("method") == "initialize":
            headers["Mcp-Session-Id"] = uuid.uuid4().hex
        self._send(200, response, headers)


# --------------------------------------------------------------------------- #
# Туннель Cloudflare (чтобы Notion мог достучаться до ПК)
# --------------------------------------------------------------------------- #

TUNNEL = {"proc": None, "url": None}


def find_cloudflared():
    for candidate in (BASE_DIR / "cloudflared.exe", BASE_DIR / "cloudflared"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("cloudflared")


def announce(url):
    token = CONFIG["token"]
    text = (
        "ДАННЫЕ ДЛЯ ПОДКЛЮЧЕНИЯ В NOTION\n"
        f"URL сервера:   {url}/mcp\n"
        "Заголовок:     Authorization\n"
        f"Значение:      Bearer {token}\n"
        "\n"
        "Если заголовок указать негде, используй URL с ключом:\n"
        f"{url}/mcp?key={token}\n"
        "\n"
        "Никому не показывай ключ! Адрес туннеля меняется при каждом перезапуске.\n"
    )
    CONNECTION_FILE.write_text(text, encoding="utf-8")
    print("\n" + "=" * 70 + "\n" + text + "(сохранено в connection.txt)\n" + "=" * 70 + "\n", flush=True)


def start_tunnel(port):
    exe = find_cloudflared()
    if not exe:
        console("cloudflared не найден - туннель не запущен. Запусти install_tunnel.bat.")
        return
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            **popen_kwargs())
    TUNNEL["proc"] = proc
    console("Запускаю туннель Cloudflare...")

    def reader():
        with open(BASE_DIR / "tunnel.log", "w", encoding="utf-8") as log:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace")
                log.write(line)
                log.flush()
                m = re.search(r"https://[-a-z0-9]+\.trycloudflare\.com", line)
                if m and not TUNNEL["url"] and not m.group(0).startswith("https://api."):
                    TUNNEL["url"] = m.group(0)
                    announce(TUNNEL["url"])
        console("Туннель остановился. Перезапусти start.bat.")

    threading.Thread(target=reader, daemon=True).start()


def cleanup():
    PROGRAMS.stop_all()
    with _bg_lock:
        for b in BACKGROUND.values():
            kill_tree(b["proc"])
    if TUNNEL["proc"]:
        kill_tree(TUNNEL["proc"])


def main():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    host, port = CONFIG["host"], int(CONFIG["port"])
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    atexit.register(cleanup)
    print("=" * 70)
    print(f"  {APP_NAME} v{VERSION}")
    print(f"  Рабочая папка:  {WORKSPACE}")
    print(f"  Локальный адрес: http://{host}:{port}/mcp")
    print(f"  Подтверждение команд: {'включено' if CONFIG['confirm_commands'] else 'ВЫКЛЮЧЕНО'}")
    print("  Чтобы остановить - закрой это окно или нажми Ctrl+C")
    print("=" * 70, flush=True)
    if CONFIG.get("auto_tunnel", True):
        start_tunnel(port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console("Остановлено.")
    finally:
        server.server_close()
        cleanup()


if __name__ == "__main__":
    main()
