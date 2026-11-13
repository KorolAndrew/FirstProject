# -*- coding: utf-8 -*-
"""
CozyCode 1.2.5 - 🧩 Программы: Клодик работает прямо в программах на ПК Яны
(Blender, Roblox Studio и любые другие, у которых есть MCP-сервер).

CozyCode сам запускает MCP-сервер программы (stdio) или подключается к нему по адресу
(Streamable HTTP) и передаёт Клодику её инструменты через инструменты моста:
mcp_list, mcp_tools, mcp_call, mcp_add, mcp_stop. Только стандартная библиотека Python.
"""

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

br = None  # модуль bridge - задаётся в setup()

PROTOCOL = "2025-06-18"
READ_ONLY_RE = re.compile(r"^(get|list|search|read|find|fetch|query|describe|inspect|check|status|ping|help|show|view)(_|$)", re.I)
MAX_IMAGE_B64 = 1_500_000

PRESETS = {
    "blender": {
        "name": "Blender", "emoji": "🧊", "command": "uvx", "args": ["blender-mcp"],
        "help": ("Нужны uv и аддон Blender MCP. Поставить uv: powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\", "
                 "аддон: uvx mcp-for-blender install-addon. Потом в Blender: панель N → вкладка BlenderMCP → «Connect»."),
    },
    "roblox": {
        "name": "Roblox Studio", "emoji": "🧱", "command": "cmd.exe", "args": ["/c", "%LOCALAPPDATA%\\Roblox\\mcp.bat"],
        "help": ("MCP-сервер встроен в Roblox Studio: Assistant → … → Manage MCP Servers → включи "
                 "«Enable Studio as MCP server». Studio должна быть открыта."),
    },
}
ALIASES = {"roblox-studio": "roblox", "robloxstudio": "roblox", "blender-mcp": "blender"}
STATE_TEXT = {"on": "🟢 работает", "starting": "⏳ запускается", "error": "🔴 ошибка", "off": "⚪ не запущена"}

_progs = {}
_plock = threading.RLock()


class SessionLost(Exception):
    """HTTP-сессия программы устарела (программу перезапустили)."""


# --------------------------------------------------------------------------- #
# Настройки программ (config.json → mcp_programs)
# --------------------------------------------------------------------------- #

def _changed():
    if br is not None:
        br._hook("programs")


def configs():
    return br.CONFIG.setdefault("mcp_programs", [])


def save():
    try:
        br.CONFIG_PATH.write_text(json.dumps(br.CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        traceback.print_exc()


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:30]
    return ALIASES.get(s, s)


def find_cfg(pid):
    key = str(pid or "").strip().lower()
    if not key:
        return None
    for c in configs():
        if c.get("id") == key or c.get("id") == slug(key):
            return c
    for c in configs():
        if str(c.get("name") or "").strip().lower() == key:
            return c
    return None


def split_cmdline(s):
    s = str(s or "").strip()
    try:
        parts = shlex.split(s, posix=False)
    except ValueError:
        parts = s.split()
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]


def describe(cfg):
    if cfg.get("url"):
        return cfg["url"]
    parts = [str(cfg.get("command") or "")] + [str(a) for a in cfg.get("args") or []]
    return " ".join(f'"{p}"' if " " in p else p for p in parts).strip()


def make_cfg(name="", command="", args=None, url="", env=None, pid="", emoji="", cmdline=""):
    name = str(name or "").strip()
    key = slug(pid or name)
    if not key:
        key = f"program-{len(configs()) + 1}"
    preset = PRESETS.get(key)
    cmdline = str(cmdline or "").strip()
    if cmdline:
        if re.match(r"^https?://", cmdline, re.I):
            url = cmdline
        else:
            parts = split_cmdline(cmdline)
            command, args = (parts[0], parts[1:]) if parts else ("", [])
    if isinstance(args, str):
        args = split_cmdline(args)
    command = str(command or "").strip()
    if command and not args and " " in command and not Path(os.path.expandvars(command)).exists():
        parts = split_cmdline(command)
        command, args = parts[0], parts[1:]
    cfg = {"id": key, "name": name or (preset or {}).get("name") or key,
           "emoji": emoji or (preset or {}).get("emoji") or "🧩",
           "command": "", "args": [], "url": "", "env": {}, "ask": True, "allowed_tools": [], "approved": False}
    url = str(url or "").strip()
    if url:
        if not re.match(r"^https?://", url, re.I):
            raise br.ToolError("Адрес программы должен начинаться с http:// или https://")
        cfg["url"] = url
    elif command:
        cfg["command"], cfg["args"] = command, [str(a) for a in (args or [])]
    elif preset:
        cfg["command"], cfg["args"] = preset["command"], list(preset["args"])
    else:
        raise br.ToolError("Нужна команда запуска MCP-сервера (command и args) или адрес url.")
    if isinstance(env, dict):
        cfg["env"] = {str(k): str(v) for k, v in env.items()}
    return cfg


def upsert(cfg):
    with _plock:
        progs = configs()
        old = next((c for c in progs if c.get("id") == cfg["id"]), None)
        if old is not None:
            if describe(old) == describe(cfg):
                cfg["allowed_tools"] = list(old.get("allowed_tools") or [])
            cfg["ask"] = old.get("ask", True)
            progs[progs.index(old)] = cfg
        else:
            progs.append(cfg)
        p = _progs.pop(cfg["id"], None)
        save()
    if p is not None:
        p.stop()
    _changed()
    return cfg


def remove(pid):
    with _plock:
        cfg = find_cfg(pid)
        if not cfg:
            return False
        br.CONFIG["mcp_programs"] = [c for c in configs() if c is not cfg]
        p = _progs.pop(cfg["id"], None)
        save()
    if p is not None:
        p.stop()
    _changed()
    return True


def get(pid):
    cfg = find_cfg(pid)
    if not cfg:
        have = ", ".join(c.get("id", "?") for c in configs()) or "пока ни одной"
        raise br.ToolError(f"Программа «{pid}» не подключена (подключены: {have}). "
                           "Добавить: mcp_add (Яна подтвердит) или Яна сама в ⚙️ Настройки → 🧩 Программы.")
    old = None
    with _plock:
        p = _progs.get(cfg["id"])
        if p is None or p.cfg is not cfg:
            old, p = p, Program(cfg)
            _progs[cfg["id"]] = p
    if old is not None:
        old.stop()
    return p


def resolve_exe(cmd):
    cmd = os.path.expandvars(str(cmd or "")).strip()
    if not cmd:
        return None
    if os.path.isabs(cmd) or "\\" in cmd or "/" in cmd:
        return cmd if Path(cmd).is_file() else None
    found = shutil.which(cmd)
    if found:
        return found
    # uv/npm могли поставить уже после запуска CozyCode - тогда PATH у него ещё старый
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".cargo" / "bin", home / "AppData" / "Roaming" / "npm",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"]
    for d in dirs:
        for ext in ("", ".exe", ".cmd", ".bat"):
            f = d / (cmd + ext)
            if f.is_file():
                return str(f)
    return None


# --------------------------------------------------------------------------- #
# Одна программа: MCP-клиент (stdio или Streamable HTTP)
# --------------------------------------------------------------------------- #

class Program:
    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.lock = threading.RLock()   # запуск и остановка
        self.wlock = threading.Lock()   # запись в stdin и номера запросов
        self.pending = {}
        self.seq = 0
        self.tools = None
        self.info = {}
        self.state = "off"              # off | starting | on | error
        self.error = ""
        self.log = deque(maxlen=200)
        self.session = None             # HTTP: Mcp-Session-Id
        self.proto = None
        self.calls = 0

    @property
    def id(self):
        return self.cfg["id"]

    @property
    def name(self):
        return self.cfg.get("name") or self.id

    @property
    def http(self):
        return bool(self.cfg.get("url"))

    def note(self, line):
        self.log.append(time.strftime("%H:%M:%S ") + str(line)[:400])

    def tail(self, n=10):
        lines = list(self.log)[-n:]
        return ("\nПоследние строки программы:\n" + "\n".join(lines)) if lines else ""

    def alive(self):
        if self.http:
            return self.state == "on"
        return self.proc is not None and self.proc.poll() is None

    # ---------- запуск и остановка ----------
    def ensure(self):
        with self.lock:
            if self.state == "on" and self.alive():
                return
            self._stop_locked()
            if not self.cfg.get("approved"):
                br.confirm_or_fail(f"Запустить «{self.name}»?",
                                   f"Клодик хочет запустить MCP-сервер программы «{self.name}»:\n{describe(self.cfg)}\n\n"
                                   "Так он сможет пользоваться её инструментами. Перед действиями (кроме просмотра) он спросит тебя.")
                self.cfg["approved"] = True
                save()
            self.state, self.error = "starting", ""
            _changed()
            try:
                if self.http:
                    self._http_start()
                else:
                    self._spawn()
                    self._handshake()
                self.state = "on"
                self.note("подключено: " + json.dumps({k: v for k, v in self.info.items() if k != "instructions"},
                                                      ensure_ascii=False))
            except Exception as e:
                msg = str(e) if isinstance(e, br.ToolError) else f"{type(e).__name__}: {e}"
                self._stop_locked()
                self.state, self.error = "error", msg[:500]
                _changed()
                hint = PRESETS.get(self.id, {}).get("help") or ""
                raise br.ToolError(f"Не получилось запустить «{self.name}»: {msg}{self.tail()}"
                                   + (f"\n\nПодсказка: {hint}" if hint else ""))
        _changed()

    def _stop_locked(self):
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.stdin.close()
            except Exception:
                pass
            try:
                p.wait(timeout=1.5)
            except Exception:
                pass
            br.kill_tree(p)
        if self.http and self.session:
            self._http_close()
        self.session, self.proto, self.tools = None, None, None
        for slot in list(self.pending.values()):
            slot[0].set()
        self.pending.clear()
        if self.state != "error":
            self.state = "off"

    def stop(self):
        with self.lock:
            self._stop_locked()
            self.state, self.error = "off", ""

    # ---------- stdio ----------
    def _argv(self):
        cmd = str(self.cfg.get("command") or "").strip()
        args = [os.path.expandvars(str(a)) for a in self.cfg.get("args") or []]
        exe = resolve_exe(cmd)
        if not exe:
            hint = ""
            if cmd.lower() in ("uvx", "uv"):
                hint = " Нужен uv: powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"."
            elif cmd.lower() in ("npx", "node", "npm"):
                hint = " Нужен Node.js (nodejs.org)."
            raise br.ToolError(f"не нашла программу «{cmd}» на ПК.{hint}")
        if br.IS_WINDOWS and exe.lower().endswith((".cmd", ".bat")):
            return [resolve_exe("cmd.exe") or "cmd.exe", "/d", "/c", exe] + args
        return [exe] + args

    def _spawn(self):
        argv = self._argv()
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "NO_COLOR": "1"})
        extra = [str(Path.home() / ".local" / "bin")]
        if os.path.isabs(argv[0]):
            extra.insert(0, str(Path(argv[0]).parent))
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        for k, v in (self.cfg.get("env") or {}).items():
            env[str(k)] = os.path.expandvars(str(v))
        cwd = os.path.expandvars(str(self.cfg.get("cwd") or "")) or str(br.WORKSPACE)
        if not Path(cwd).is_dir():
            cwd = None
        self.note("запуск: " + " ".join(argv))
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, env=env, bufsize=0, **br.popen_kwargs())
        self.proc = proc
        threading.Thread(target=self._read_out, args=(proc,), daemon=True).start()
        threading.Thread(target=self._read_err, args=(proc,), daemon=True).start()

    def _read_out(self, proc):
        try:
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self.note("вывод: " + line)
                    continue
                for m in (msg if isinstance(msg, list) else [msg]):
                    self._dispatch(m)
        except Exception as e:
            self.note(f"чтение прервано: {e}")
        code = proc.poll()
        self.note("программа закрылась" + (f" (код {code})" if code is not None else ""))
        if self.proc is proc:
            for slot in list(self.pending.values()):
                slot[0].set()
            if self.state == "on":
                self.state = "off"
                self.tools = None
                _changed()

    def _read_err(self, proc):
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.note("stderr: " + line)
        except Exception:
            pass

    def _dispatch(self, m):
        if not isinstance(m, dict):
            return
        method = m.get("method")
        if method:
            if "id" in m:  # запрос программы к CozyCode
                if method == "ping":
                    reply = {"jsonrpc": "2.0", "id": m["id"], "result": {}}
                elif method == "roots/list":
                    reply = {"jsonrpc": "2.0", "id": m["id"], "result": {"roots": [
                        {"uri": Path(br.WORKSPACE).resolve().as_uri(), "name": "NotionAgentWorkspace"}]}}
                else:
                    reply = {"jsonrpc": "2.0", "id": m["id"],
                             "error": {"code": -32601, "message": f"CozyCode не поддерживает {method}"}}
                try:
                    self._send(reply)
                except Exception as e:
                    self.note(f"не ответила программе на {method}: {e}")
            elif method == "notifications/tools/list_changed":
                self.tools = None
                _changed()
            elif method == "notifications/message":
                p = m.get("params") or {}
                self.note(f"{p.get('level', 'log')}: {json.dumps(p.get('data'), ensure_ascii=False)[:300]}")
            return
        slot = self.pending.get(m.get("id"))
        if slot is not None:
            slot[1] = m
            slot[0].set()

    def _send(self, msg):
        if self.http:
            self._post(msg)
            return
        proc = self.proc
        if proc is None or proc.poll() is not None:
            raise br.ToolError("программа не запущена")
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        with self.wlock:
            proc.stdin.write(data)
            proc.stdin.flush()

    def _next_id(self):
        with self.wlock:
            self.seq += 1
            return self.seq

    def request(self, method, params=None, timeout=60):
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params or {}}
        if self.http:
            return self._result(self._post(msg, timeout=timeout), method)
        proc = self.proc
        if proc is None or proc.poll() is not None:
            raise br.ToolError("программа не запущена")
        slot = [threading.Event(), None]
        self.pending[msg["id"]] = slot
        try:
            try:
                self._send(msg)
            except (OSError, ValueError) as e:
                raise br.ToolError(f"не получилось передать запрос программе: {e}")
            deadline = time.time() + timeout
            while not slot[0].wait(0.5):
                if proc.poll() is not None:
                    slot[0].wait(1.0)  # дочитать последний ответ
                    break
                if time.time() > deadline:
                    try:
                        self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                    "params": {"requestId": msg["id"], "reason": "timeout"}})
                    except Exception:
                        pass
                    raise br.ToolError(f"программа не ответила за {timeout} с")
        finally:
            self.pending.pop(msg["id"], None)
        if slot[1] is None:
            code = proc.poll()
            raise br.ToolError("программа закрылась" + (f" (код {code})" if code is not None else "") + " и не ответила")
        return self._result(slot[1], method)

    @staticmethod
    def _result(m, method):
        if m is None:
            raise br.ToolError(f"программа не ответила на {method}")
        if m.get("error"):
            e = m["error"] if isinstance(m["error"], dict) else {"message": str(m["error"])}
            data = e.get("data")
            raise br.ToolError(f"программа ответила ошибкой: {e.get('message')} (код {e.get('code')})"
                               + (f"\n{json.dumps(data, ensure_ascii=False)[:800]}" if data else ""))
        return m.get("result") or {}

    def _init_params(self):
        return {"protocolVersion": PROTOCOL, "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "cozycode", "title": "CozyCode", "version": br.VERSION}}

    def _remember_info(self, res):
        si = res.get("serverInfo") or {}
        self.proto = res.get("protocolVersion") or PROTOCOL
        self.info = {"server": si.get("title") or si.get("name") or "", "version": si.get("version") or "",
                     "protocol": self.proto, "instructions": str(res.get("instructions") or "")[:2000]}

    def _handshake(self):
        res = self.request("initialize", self._init_params(), timeout=int(self.cfg.get("start_timeout") or 180))
        self._remember_info(res)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # ---------- Streamable HTTP ----------
    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.proto:
            h["MCP-Protocol-Version"] = self.proto
        for k, v in (self.cfg.get("headers") or {}).items():
            h[str(k)] = os.path.expandvars(str(v))
        return h

    def _post(self, msg, timeout=60):
        url = self.cfg["url"]
        req = urllib.request.Request(url, data=json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code == 404 and self.session:
                self.session = None
                self.state = "off"
                raise SessionLost(body)
            raise br.ToolError(f"программа ответила HTTP {e.code}: {body}")
        except (urllib.error.URLError, OSError) as e:
            raise br.ToolError(f"нет связи с {url}: {getattr(e, 'reason', e)} - программа открыта?")
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            if "id" not in msg or "method" not in msg or resp.status == 202:
                return None
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" in ctype:
                return self._read_sse(resp, msg["id"])
            raw = resp.read().decode("utf-8", "replace").strip()
        if not raw:
            return None
        data = json.loads(raw)
        found = None
        for m in (data if isinstance(data, list) else [data]):
            if isinstance(m, dict) and m.get("id") == msg["id"] and "method" not in m:
                found = m
            else:
                self._dispatch(m)
        return found

    def _read_sse(self, resp, rid):
        buf = []

        def flush():
            payload = "\n".join(buf)
            buf.clear()
            try:
                m = json.loads(payload)
            except ValueError:
                return None
            for x in (m if isinstance(m, list) else [m]):
                if isinstance(x, dict) and x.get("id") == rid and "method" not in x:
                    return x
                self._dispatch(x)
            return None

        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("data:"):
                v = line[5:]
                buf.append(v[1:] if v.startswith(" ") else v)
            elif not line and buf:
                found = flush()
                if found is not None:
                    return found
        return flush() if buf else None

    def _http_start(self):
        self.session, self.proto = None, None
        msg = {"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize", "params": self._init_params()}
        res = self._result(self._post(msg, timeout=int(self.cfg.get("start_timeout") or 60)), "initialize")
        self._remember_info(res)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _http_close(self):
        try:
            req = urllib.request.Request(self.cfg["url"], headers=self._headers(), method="DELETE")
            urllib.request.urlopen(req, timeout=3).close()
        except Exception:
            pass

    # ---------- то, чем пользуются инструменты ----------
    def run(self, method, params=None, timeout=60):
        self.ensure()
        try:
            return self.request(method, params, timeout)
        except SessionLost:
            self.note("сессия устарела - подключаюсь заново")
            self.ensure()
            return self.request(method, params, timeout)

    def list_tools(self, refresh=False):
        if refresh or self.tools is None or not (self.state == "on" and self.alive()):
            tools, cursor = [], None
            for _ in range(20):
                res = self.run("tools/list", {"cursor": cursor} if cursor else {}, timeout=60)
                tools += [t for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name")]
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            self.tools = tools
            _changed()
        return self.tools

    def call(self, tool_name, arguments, timeout):
        self.calls += 1
        return self.run("tools/call", {"name": tool_name, "arguments": arguments}, timeout=timeout)


# --------------------------------------------------------------------------- #
# Разрешения, тексты для Клодика, картинки
# --------------------------------------------------------------------------- #

def state_of(p):
    if p is None:
        return "off"
    if p.state == "on" and not p.alive():
        return "off"
    return p.state


def read_only(spec):
    ann = spec.get("annotations") or {}
    if ann.get("readOnlyHint") is True:
        return True
    if ann.get("readOnlyHint") is False or ann.get("destructiveHint") is True:
        return False
    return bool(READ_ONLY_RE.match(spec.get("name") or ""))


def needs_confirm(p, spec):
    if not p.cfg.get("ask", True):
        return False
    if spec.get("name") in (p.cfg.get("allowed_tools") or []):
        return False
    return not read_only(spec)


def remember(rule):
    """«✅ Всегда» в окне подтверждения: больше не спрашивать про этот инструмент программы."""
    cfg = find_cfg(rule.get("program"))
    if not cfg or not rule.get("tool"):
        return
    allowed = cfg.setdefault("allowed_tools", [])
    if rule["tool"] not in allowed:
        allowed.append(rule["tool"])
        save()
    _changed()


def schema_lines(schema):
    schema = schema if isinstance(schema, dict) else {}
    props = schema.get("properties") or {}
    req = set(schema.get("required") or [])
    out = []
    for k, v in list(props.items())[:25]:
        v = v if isinstance(v, dict) else {}
        typ = v.get("type") or ("enum" if v.get("enum") else "")
        if isinstance(typ, list):
            typ = "/".join(map(str, typ))
        extra = ""
        if v.get("enum"):
            extra = " · одно из: " + ", ".join(map(str, v["enum"][:12]))
        if v.get("default") is not None:
            extra += f" · по умолчанию {json.dumps(v['default'], ensure_ascii=False)[:40]}"
        desc = " ".join(str(v.get("description") or "").split())[:160]
        out.append(f"    - {k}{'*' if k in req else ''} ({typ}){': ' + desc if desc else ''}{extra}")
    if len(props) > 25:
        out.append(f"    … и ещё {len(props) - 25}")
    return out


def tools_text(p, tools):
    head = f"{p.cfg.get('emoji') or '🧩'} {p.name} (id: {p.id})"
    if p.info.get("server"):
        head += f" · {p.info['server']} {p.info.get('version', '')}".rstrip()
    lines = [head + f" · инструментов: {len(tools)}"]
    if p.info.get("instructions"):
        lines.append("Подсказка от программы: " + " ".join(p.info["instructions"].split()))
    lines.append("Вызов: mcp_call(program, tool, arguments). ✅ - без вопросов, 🔔 - Яна подтвердит (может нажать «✅ Всегда»). "
                 "* - обязательный параметр.")
    for t in tools:
        mark = "🔔" if needs_confirm(p, t) else "✅"
        desc = " ".join(str(t.get("description") or "").split())
        lines.append(f"\n{mark} {t['name']} — {desc[:400]}")
        lines += schema_lines(t.get("inputSchema"))
    return br.clip("\n".join(lines))


def list_text():
    progs = configs()
    if not progs:
        return ("Пока не подключено ни одной программы. Яна может добавить их в ⚙️ Настройки → 🧩 Программы "
                "(там готовые Blender и Roblox Studio), или вызови mcp_add - Яна подтвердит.")
    lines = [f"🧩 Программы ({len(progs)}):"]
    for c in progs:
        p = _progs.get(c.get("id"))
        st = state_of(p)
        tools = f" · инструментов: {len(p.tools)}" if p and p.tools is not None and st == "on" else ""
        ask = "спрашивает Яну перед действиями" if c.get("ask", True) else "без вопросов"
        allowed = c.get("allowed_tools") or []
        lines.append(f"• {c.get('id')} — {c.get('emoji') or '🧩'} {c.get('name')} · {STATE_TEXT.get(st, st)}{tools}"
                     f" · запуск: {describe(c)} · {ask}" + (f" (всегда можно: {', '.join(allowed)})" if allowed else ""))
        if p and st == "error" and p.error:
            lines.append("  ⚠️ " + p.error)
        if st != "on" and PRESETS.get(c.get("id"), {}).get("help"):
            lines.append("  💡 " + PRESETS[c["id"]]["help"])
    lines.append("\nЗапуск автоматический: mcp_tools(program) покажет инструменты, mcp_call(program, tool, arguments) вызовет. "
                 "Новую программу: mcp_add (Яна подтвердит).")
    return "\n".join(lines)


def save_image(p, c):
    try:
        data = base64.b64decode(c.get("data") or "")
        if not data:
            return ""
        ext = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}.get(c.get("mimeType"), ".png")
        folder = Path(br.WORKSPACE) / "mcp" / p.id / "images"
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{p.calls}{ext}"
        f.write_bytes(data)
        return br.rel(f)
    except Exception:
        return ""


def result_content(p, tool_name, res):
    content = []
    for c in res.get("content") or []:
        if not isinstance(c, dict):
            continue
        kind = c.get("type")
        if kind == "text":
            content.append({"type": "text", "text": br.clip(str(c.get("text") or ""))})
        elif kind == "image":
            path = save_image(p, c)
            if path:
                content.append({"type": "text", "text": f"🖼 Картинка сохранена: {path} (можно приложить к cozy_reply в files)"})
            if 0 < len(c.get("data") or "") <= MAX_IMAGE_B64:
                content.append({"type": "image", "data": c["data"], "mimeType": c.get("mimeType") or "image/png"})
        elif kind == "resource":
            r = c.get("resource") or {}
            if "text" in r:
                content.append({"type": "text", "text": br.clip(f"[{r.get('uri', '')}]\n{r.get('text')}")})
            else:
                content.append({"type": "text", "text": f"[файл {r.get('uri', '')} · {r.get('mimeType', '')}]"})
        elif kind == "resource_link":
            content.append({"type": "text", "text": f"[ссылка: {c.get('name') or ''} {c.get('uri') or ''}]"})
        else:
            content.append({"type": "text", "text": br.clip(json.dumps(c, ensure_ascii=False))})
    if res.get("structuredContent") is not None and not any(x["type"] == "text" for x in content):
        content.append({"type": "text", "text": br.clip(json.dumps(res["structuredContent"], ensure_ascii=False, indent=1))})
    if not content:
        content = [{"type": "text", "text": f"«{tool_name}» выполнен, программа ничего не вернула."}]
    return {"content": content, "isError": bool(res.get("isError"))}


# --------------------------------------------------------------------------- #
# Для приложения CozyCode (⚙️ → 🧩 Программы)
# --------------------------------------------------------------------------- #

def ui_state():
    out = []
    for c in list(configs()):
        p = _progs.get(c.get("id"))
        st = state_of(p)
        out.append({"id": c.get("id"), "name": c.get("name"), "emoji": c.get("emoji") or "🧩", "line": describe(c),
                    "state": st, "error": p.error if p and st == "error" else "",
                    "tools": len(p.tools) if p and p.tools is not None and st == "on" else None,
                    "calls": p.calls if p else 0, "ask": c.get("ask", True),
                    "allowed": list(c.get("allowed_tools") or []),
                    "help": PRESETS.get(c.get("id"), {}).get("help", "")})
    return out


def ui_action(body):
    action = body.get("action")
    note = ""
    if action == "add_preset":
        key = body.get("preset")
        if key not in PRESETS:
            raise ValueError("Нет такой готовой программы")
        cfg = make_cfg(pid=key)
        cfg["approved"] = True
        upsert(cfg)
        note = PRESETS[key]["help"]
    elif action == "save":
        cfg = make_cfg(name=body.get("name"), cmdline=body.get("cmdline"))
        cfg["approved"] = True
        upsert(cfg)
        note = f"Добавила «{cfg['name']}». Нажми «▶️ Проверить»."
    elif action == "remove":
        remove(body.get("id"))
    elif action in ("test", "stop", "ask", "reset"):
        cfg = find_cfg(body.get("id"))
        if not cfg:
            raise ValueError("Программа не найдена")
        if action == "test":
            cfg["approved"] = True
            save()
            p = get(cfg["id"])

            def work():
                try:
                    p.stop()
                    p.list_tools(refresh=True)
                except Exception:
                    pass
                _changed()
            threading.Thread(target=work, daemon=True).start()
            note = "Запускаю… Первый запуск может занять минуту-две."
        elif action == "stop":
            p = _progs.get(cfg["id"])
            if p is not None:
                p.stop()
        elif action == "ask":
            cfg["ask"] = bool(body.get("ask"))
            save()
        else:
            cfg["allowed_tools"] = []
            save()
        _changed()
    else:
        raise ValueError("Неизвестное действие")
    return {"ok": True, "programs": ui_state(), "note": note}


def stop_all():
    for p in list(_progs.values()):
        try:
            p.stop()
        except Exception:
            pass


def autodetect():
    """При первом запуске 1.2.5 сами добавляем Blender и Roblox Studio, если они есть на ПК (ничего не запуская)."""
    if br.CONFIG.get("mcp_autodetected"):
        return
    br.CONFIG["mcp_autodetected"] = True
    found = []
    try:
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        versions = local / "Roblox" / "Versions"
        if (local / "Roblox" / "mcp.bat").exists() or (
                versions.is_dir() and any((d / "RobloxStudioBeta.exe").exists() for d in versions.iterdir() if d.is_dir())):
            found.append("roblox")
        blender_dirs = [Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "Blender Foundation",
                        Path(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)") / "Steam" / "steamapps" / "common" / "Blender"]
        if any(d.exists() for d in blender_dirs) or shutil.which("blender"):
            found.append("blender")
    except Exception:
        traceback.print_exc()
    have = {c.get("id") for c in configs()}
    for key in found:
        if key not in have:
            configs().append(make_cfg(pid=key))
    save()


# --------------------------------------------------------------------------- #
# Инструменты моста для Клодика
# --------------------------------------------------------------------------- #

def setup(bridge):
    global br
    br = bridge
    reg, S, I, B = bridge.tool, bridge.S, bridge.I, bridge.B
    P = S("ID программы из mcp_list (например blender или roblox)")

    @reg("mcp_list", "🧩 Программы на ПК Яны, подключённые к CozyCode по MCP (Blender, Roblox Studio и др.): "
         "статус, команда запуска, подсказки. Начни с него, если Яна просит поработать в программе.", read_only=True)
    def mcp_list():
        return list_text()

    @reg("mcp_tools", "Что умеет программа: её инструменты с описаниями и параметрами. Сам запускает MCP-сервер программы.",
         properties={"program": P, "refresh": B("Перечитать список инструментов заново")},
         required=("program",), read_only=True)
    def mcp_tools(program, refresh=False):
        p = get(program)
        return tools_text(p, p.list_tools(refresh=bool(refresh)))

    @reg("mcp_call", "Вызвать инструмент программы (из mcp_tools). То, что не только смотрит, Яна подтверждает в CozyCode "
         "(может нажать «✅ Всегда»). Картинки (снимки экрана) сохраняются в mcp/<программа>/images.",
         properties={"program": P, "tool": S("Название инструмента из mcp_tools"),
                     "arguments": {"type": "object", "description": "Аргументы инструмента по его схеме из mcp_tools"},
                     "timeout_seconds": I("Сколько ждать ответа: по умолчанию 300, максимум 1800")},
         required=("program", "tool"))
    def mcp_call(program, tool, arguments=None, timeout_seconds=None):
        p = get(program)
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except ValueError:
                raise br.ToolError("arguments должен быть JSON-объектом, например {\"code\": \"...\"}")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise br.ToolError("arguments должен быть объектом, например {\"code\": \"...\"}")
        tools = p.list_tools()
        spec = next((t for t in tools if t.get("name") == tool), None)
        if spec is None:
            raise br.ToolError(f"У «{p.name}» нет инструмента «{tool}». Есть: " + ", ".join(t["name"] for t in tools[:80]))
        if needs_confirm(p, spec):
            shown = json.dumps(arguments, ensure_ascii=False, indent=1)
            desc = " ".join(str(spec.get("description") or "").split())[:300]
            br.confirm_or_fail(f"{p.cfg.get('emoji') or '🧩'} {p.name}: {tool}",
                               f"Клодик хочет выполнить в «{p.name}» действие «{tool}».\n{desc}\n\nПараметры:\n{shown[:1300]}",
                               rule={"kind": "mcp", "prefix": tool, "folder": p.name, "program": p.id, "tool": tool})
        timeout = max(5, min(1800, int(timeout_seconds or p.cfg.get("timeout") or 300)))
        return result_content(p, tool, p.call(tool, arguments, timeout))

    @reg("mcp_add", "Подключить к CozyCode новую программу (её MCP-сервер) - Яна подтверждает. Укажи command и args "
         "(как в инструкции сервера для Claude Desktop/Cursor) или url для HTTP-сервера. "
         "Готовые: name=\"Blender\" или name=\"Roblox Studio\" без команды.",
         properties={"name": S("Название программы, например Blender"), "command": S("Команда запуска, например uvx"),
                     "args": {"type": "array", "items": {"type": "string"}, "description": "Аргументы, например [\"blender-mcp\"]"},
                     "url": S("Адрес HTTP MCP-сервера вместо команды, например http://127.0.0.1:3845/mcp"),
                     "env": {"type": "object", "description": "Необязательно: переменные окружения"},
                     "id": S("Необязательно: короткий ID латиницей")},
         required=("name",))
    def mcp_add(name, command="", args=None, url="", env=None, id=""):
        cfg = make_cfg(name=name, command=command, args=args, url=url, env=env, pid=id)
        if br.CONFIG.get("confirm_commands", True):
            br.confirm_or_fail(f"Подключить «{cfg['name']}»?",
                               f"Клодик хочет подключить программу «{cfg['name']}» к CozyCode.\nЗапуск: {describe(cfg)}\n\n"
                               "CozyCode будет запускать её MCP-сервер и передавать Клодику её инструменты. "
                               "Перед действиями (кроме просмотра) Клодик будет спрашивать тебя.")
        cfg["approved"] = True
        upsert(cfg)
        try:
            tools = get(cfg["id"]).list_tools(refresh=True)
            return f"Подключила «{cfg['name']}» (id: {cfg['id']}): инструментов {len(tools)}. Дальше: mcp_tools(\"{cfg['id']}\")."
        except br.ToolError as e:
            return f"Сохранила «{cfg['name']}» (id: {cfg['id']}), но запустить пока не получилось: {e}"

    @reg("mcp_stop", "Остановить MCP-сервер программы (освободить память). При следующем вызове он запустится заново.",
         properties={"program": P}, required=("program",))
    def mcp_stop(program):
        cfg = find_cfg(program)
        if not cfg:
            raise br.ToolError(f"Программа «{program}» не подключена.")
        p = _progs.get(cfg["id"])
        if p is not None:
            p.stop()
            _changed()
        return f"«{cfg.get('name')}» остановлена."

    try:
        autodetect()
    except Exception:
        traceback.print_exc()
