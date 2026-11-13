"""Этап 7 - разрешения Янки. Модель НЕ вызывает инструменты сама: только через Gate.call().

Gate:
  - делит действия на виды: чтение, изменение, удаление, команда, установка ПО, настройки системы,
    передача данных наружу, программы (MCP), общение с Яной;
  - решает: можно сразу / спросить Яну / нельзя;
  - если Яна отказала - такое же действие в этой задаче больше не спрашивает и не делает;
  - пишет журнал решений (data/yanka_decisions.jsonl) и запоминает резервные копии, чтобы откатить.
Подтверждения самого CozyCode (команды, удаление) остаются и работают поверх этого.
"""
import datetime
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

CATEGORY = {
    "read": "👀 чтение", "modify": "✏️ изменение файлов", "delete": "🗑️ удаление", "command": "⌨️ команда",
    "install": "📦 установка программ", "system": "⚙️ настройки Windows", "external": "🌐 передача данных наружу",
    "program": "🧩 действие в программе", "talk": "💬 общение с Яной", "unknown": "❓ неизвестное действие",
}

# allow - сразу; ask - спросить Яну; bridge - спросит сам CozyCode (не дублируем); deny - нельзя
DEFAULT_POLICY = {"read": "allow", "talk": "allow", "modify": "allow", "delete": "bridge", "command": "bridge",
                  "program": "bridge", "install": "ask", "external": "ask", "system": "deny", "unknown": "deny"}
# изменение файлов разрешено, потому что CozyCode сам делает резервную копию и есть «↩️ Вернуть»

TOOL_KIND = {
    "get_info": "read", "list_dir": "read", "read_file": "read", "search_files": "read", "list_background": "read",
    "read_background_output": "read", "mcp_list": "read", "mcp_tools": "read",
    "write_file": "modify", "edit_file": "modify", "make_dir": "modify", "copy_path": "modify", "move_path": "modify",
    "delete_path": "delete", "run_command": "command", "start_background": "command", "stop_background": "command",
    "mcp_call": "program", "mcp_add": "install", "mcp_stop": "program",
    "cozy_status": "talk", "cozy_reply": "talk", "cozy_ask": "talk", "cozy_get_inbox": "talk",
}

INSTALL_RX = re.compile(r"\b(pip3?|uv|npm|pnpm|yarn)\s+(install|add|i)\b|\bnpm\s+i\b|\bwinget\s+(install|upgrade)\b|"
                        r"\bchoco\s+(install|upgrade)\b|\bmsiexec\b|\bscoop\s+install\b|\bconda\s+install\b|"
                        r"\bInstall-(Module|Package)\b|\.msi\b|setup\.exe|installer\.exe|\blms\s+get\b", re.I)
SYSTEM_RX = re.compile(r"\b(reg(\.exe)?\s+(add|delete|import)|Set-ItemProperty\s+.*HK(LM|CU)|New-ItemProperty|bcdedit|"
                       r"netsh|sc(\.exe)?\s+(config|create|delete|stop)|Set-Service|Stop-Service|Set-ExecutionPolicy|"
                       r"schtasks\s+/create|Register-ScheduledTask|takeown|icacls|cacls|diskpart|format(-volume)?\b|"
                       r"Disable-|Enable-WindowsOptionalFeature|Set-MpPreference|Add-MpPreference|"
                       r"powercfg|shutdown|Restart-Computer|Stop-Computer|net\s+(user|localgroup|share))", re.I)
EXTERNAL_RX = re.compile(r"\b(curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm|scp|ftp|sftp|Send-MailMessage|"
                         r"git\s+push|Start-BitsTransfer)\b", re.I)
DELETE_RX = re.compile(r"\b(Remove-Item|rm|ri|del|erase|rd|rmdir|Clear-Content)\b", re.I)


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def classify(name, args):
    args = args or {}
    kind = TOOL_KIND.get(name, "unknown")
    if kind == "command":
        cmd = str(args.get("command", ""))
        if SYSTEM_RX.search(cmd):
            return "system"
        if INSTALL_RX.search(cmd):
            return "install"
        if EXTERNAL_RX.search(cmd):
            return "external"
        if DELETE_RX.search(cmd):
            return "delete"
    if kind == "program" and name == "mcp_call":
        tool = str(args.get("tool", ""))
        if re.match(r"^(get|list|search|read|find|fetch|query|describe|inspect|check|status|show|view)(_|$)", tool, re.I):
            return "read"
    return kind


def fingerprint(name, args):
    return hashlib.sha1((name + json.dumps(args or {}, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:16]


def _brief(name, args):
    a = args or {}
    for k in ("command", "path", "destination", "tool", "source"):
        if a.get(k):
            return f"{name}: {str(a[k])[:200]}"
    return name


class Denied(Exception):
    pass


class Gate:
    def __init__(self, data_dir, backend, policy=None):
        """backend: {"call_tool": f(name, args)->MCP-ответ, "ask": f(title, msg)->bool|None,
                     "list_backups": f()->[...], "restore_backup": f(id)->str}"""
        self.b = backend
        self.policy = {**DEFAULT_POLICY, **(policy or {})}
        self.file = Path(data_dir) / "yanka_decisions.jsonl"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.rejected = {}  # task_id -> {fingerprint}
        self._lock = threading.Lock()

    # --- журнал ---
    def _log(self, row):
        row = {"id": uuid.uuid4().hex[:10], "t": now(), **row}
        with self._lock, open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def journal(self, limit=200):
        try:
            rows = [json.loads(l) for l in self.file.read_text(encoding="utf-8").splitlines() if l.strip()]
        except FileNotFoundError:
            rows = []
        return rows[-limit:]

    # --- что видит модель ---
    def tools_for_model(self, tools):
        """Модели показываем только то, что вообще можно (system/unknown запрещены - их не видно)."""
        out = []
        for t in tools:
            kind = TOOL_KIND.get(t["name"], "unknown")
            if self.policy.get(kind) == "deny":
                continue
            out.append({"name": t["name"], "description": t.get("description", ""),
                        "parameters": t.get("inputSchema") or t.get("parameters") or {"type": "object"}})
        return out

    # --- главный вход ---
    def call(self, name, args, task_id="", who="Янка", reason=""):
        args = args or {}
        kind = classify(name, args)
        rule = self.policy.get(kind, "deny")
        fp = fingerprint(name, args)
        base = {"tool": name, "kind": kind, "brief": _brief(name, args), "task": task_id, "who": who,
                "reason": reason[:200]}

        if fp in self.rejected.get(task_id, set()):
            self._log({**base, "decision": "denied", "why": "Яна уже отказала - не повторяю"})
            raise Denied("Яна уже отказала в этом действии. Не повторяй его - спроси Яну, как лучше.")
        if rule == "deny":
            self._log({**base, "decision": "denied", "why": f"{CATEGORY[kind]} Янке запрещено"})
            raise Denied(f"{CATEGORY[kind]}: Янке это делать нельзя. Если очень нужно - попроси Яну или Клодика.")
        if rule == "ask":
            msg = (f"Янка хочет: {CATEGORY[kind]}\n{base['brief']}"
                   + (f"\nЗачем: {reason}" if reason else ""))
            ok = self.b["ask"]("🐑 Янка спрашивает разрешения", msg)
            if not ok:
                self.rejected.setdefault(task_id, set()).add(fp)
                self._log({**base, "decision": "rejected", "why": "Яна отказала"})
                raise Denied("Яна отказала. Не повторяй это действие.")

        track = kind in ("modify", "delete", "command", "install", "program")
        before = {b["id"] for b in self.b["list_backups"]()} if track else set()
        res = self.b["call_tool"](name, args)
        text = "".join(c.get("text", "") for c in (res or {}).get("content", []) if c.get("type") == "text")
        backups = [b["id"] for b in self.b["list_backups"]() if b["id"] not in before] if track else []
        if res and res.get("isError") and "отклонено" in text.lower():
            # отказ в окне подтверждения самого CozyCode - тоже запоминаем
            self.rejected.setdefault(task_id, set()).add(fp)
            self._log({**base, "decision": "rejected", "why": "Яна отклонила в CozyCode"})
            raise Denied("Яна отклонила это действие в CozyCode. Не повторяй его.")
        row = self._log({**base, "decision": "allowed" if rule == "allow" else "approved",
                         "ok": not (res or {}).get("isError"), "backups": backups, "result": text[:200]})
        res = dict(res or {})
        res["decision_id"] = row["id"]
        return res

    # --- откат ---
    def undo(self, decision_id):
        row = next((r for r in self.journal(5000) if r["id"] == decision_id), None)
        if not row:
            raise KeyError(decision_id)
        if not row.get("backups"):
            raise ValueError("Для этого действия нет резервной копии - откатить нельзя "
                             "(новый файл можно просто удалить, команду - только вручную).")
        done = [self.b["restore_backup"](bid) for bid in reversed(row["backups"])]
        self._log({"tool": "undo", "kind": "modify", "brief": f"откат {decision_id}", "decision": "allowed",
                   "who": "Яна", "ok": True, "backups": []})
        return done
