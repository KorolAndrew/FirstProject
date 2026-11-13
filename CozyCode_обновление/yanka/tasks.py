"""Этап 6 - задачи: план из шагов, выполнение, проверка результата, повторы, продолжение после перерыва.

Задачи лежат в data/yanka_tasks.json и переживают перезапуск: шаг, который выполнялся при выключении,
снова становится «ждёт». Исполнители и проверки подключаются снаружи (router/providers/CozyCode),
поэтому этот модуль ничего не делает с ПК сам.
"""
import datetime
import json
import os
import re
import threading
import uuid
from pathlib import Path

STATUS = {"pending": "⏳ ждёт", "running": "⚙️ делаю", "done": "✅ готово", "failed": "❌ не вышло",
          "waiting": "🙋 жду (Яну или Клодика)", "skipped": "⏭️ пропущен"}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Tasks:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "yanka_tasks.json"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self.items = json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            self.items = []
        # прерванные шаги (ПК выключили посреди работы) - снова в очередь
        for t in self.items:
            for s in t["steps"]:
                if s["status"] == "running":
                    s["status"] = "pending"
                    self._log(t, f"шаг {s['n']} был прерван - продолжу с него")
            if t["status"] == "running":
                t["status"] = "pending"
        self.save()

    def save(self):
        with self._lock:
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.file)

    @staticmethod
    def _log(t, text):
        t.setdefault("log", []).append({"t": now(), "text": text})
        t["log"] = t["log"][-100:]

    # --- создание ---
    def create(self, goal, steps, project="general", title=None):
        """steps: [{"text", "executor"?, "check"?}] или просто строки."""
        norm = []
        for i, s in enumerate(steps, 1):
            s = {"text": s} if isinstance(s, str) else dict(s)
            norm.append({"n": i, "text": str(s.get("text", ""))[:500], "executor": s.get("executor", "local"),
                         "check": s.get("check"), "status": "pending", "result": "", "attempts": 0,
                         "error": ""})
        t = {"id": uuid.uuid4().hex[:8], "title": (title or goal)[:80], "goal": goal, "project": project,
             "status": "pending", "steps": norm, "created": now(), "updated": now(), "log": []}
        self._log(t, f"план из {len(norm)} шагов")
        with self._lock:
            self.items.append(t)
            self.save()
        return t

    def get(self, tid):
        return next((t for t in self.items if t["id"] == tid), None)

    def list(self, project=None, active_only=False):
        out = [t for t in self.items if project is None or t["project"] == project]
        if active_only:
            out = [t for t in out if t["status"] not in ("done", "failed")]
        return sorted(out, key=lambda t: t["updated"], reverse=True)

    def delete(self, tid):
        with self._lock:
            n = len(self.items)
            self.items = [t for t in self.items if t["id"] != tid]
            self.save()
            return len(self.items) < n

    def progress(self, t):
        done = sum(1 for s in t["steps"] if s["status"] in ("done", "skipped"))
        return f"{done}/{len(t['steps'])}"

    # --- выполнение ---
    def run(self, tid, execute, verify=None, max_attempts=2, stop=None):
        """execute(task, step) -> {"ok": bool, "result": str, "waiting": bool?}
        verify(task, step, result) -> (ok: bool, why: str)
        Идёт по шагам до конца, до «жду» или до провала шага после max_attempts попыток."""
        t = self.get(tid)
        if not t:
            raise KeyError(tid)
        t["status"] = "running"
        for s in t["steps"]:
            if stop is not None and stop.is_set():
                t["status"] = "pending"
                self._log(t, "остановлено Яной")
                break
            if s["status"] in ("done", "skipped"):
                continue
            while s["attempts"] < max_attempts:
                s["status"], s["attempts"] = "running", s["attempts"] + 1
                t["updated"] = now()
                self.save()
                try:
                    r = execute(t, s) or {}
                except Exception as e:  # исполнитель упал - это неудачная попытка, а не падение Янки
                    r = {"ok": False, "result": "", "error": f"{type(e).__name__}: {e}"}
                if r.get("waiting"):
                    s.update(status="waiting", result=str(r.get("result", ""))[:4000])
                    t["status"] = "waiting"
                    self._log(t, f"шаг {s['n']}: жду - {s['result'][:120]}")
                    self.save()
                    return t
                ok, why = bool(r.get("ok")), r.get("error", "")
                if ok and verify and s.get("check"):
                    ok, why = verify(t, s, r.get("result", ""))
                s["result"] = str(r.get("result", ""))[:4000]
                if ok:
                    s.update(status="done", error="")
                    self._log(t, f"шаг {s['n']} готов" + (f" (проверка: {why})" if why else ""))
                    break
                s.update(status="pending", error=str(why)[:500])
                self._log(t, f"шаг {s['n']}, попытка {s['attempts']}: {why}")
            else:
                s["status"] = "failed"
                t["status"] = "failed"
                self._log(t, f"шаг {s['n']} не получился за {max_attempts} попытки")
                self.save()
                return t
            self.save()
        else:
            t["status"] = "done"
            self._log(t, "задача готова 🎉")
        t["updated"] = now()
        self.save()
        return t

    def resume(self, tid, step_result=None):
        """Продолжить после «жду»: результат от Яны/Клодика закрывает ожидающий шаг."""
        t = self.get(tid)
        for s in t["steps"]:
            if s["status"] == "waiting":
                s.update(status="done" if step_result is not None else "pending",
                         result=str(step_result or s["result"])[:4000])
        t["status"] = "pending"
        self.save()
        return t


# --------------------------------------------------------------------------- #
# Планировщик и стандартные проверки
# --------------------------------------------------------------------------- #

def plan(hub, goal, model=None, max_steps=7):
    """Локальная модель делит цель на шаги. Не вышло - один шаг «сделать целиком»."""
    prompt = (f"Раздели задачу на 2–{max_steps} конкретных шагов. Для каждого укажи исполнителя: "
              "local (ответ или текст), klodik (код и файлы), blender, roblox. "
              "Ответь ТОЛЬКО JSON-массивом: [{\"text\": \"шаг\", \"executor\": \"local\"}].\nЗадача: " + goal[:2000])
    try:
        r = hub.chat("lmstudio", [{"role": "user", "content": prompt}], model=model, max_tokens=600, temperature=0.2,
                     reason="план задачи")
        m = re.search(r"\[.*\]", r["text"], re.S)
        steps = json.loads(m.group(0)) if m else []
        steps = [{"text": str(s.get("text", "")).strip(),
                  "executor": s.get("executor") if s.get("executor") in ("local", "klodik", "blender", "roblox") else "local"}
                 for s in steps if isinstance(s, dict) and str(s.get("text", "")).strip()][:max_steps]
    except Exception:
        steps = []
    return steps or [{"text": goal, "executor": "local"}]


def make_verify(hub=None, model=None, root=None):
    """Проверки шага. check: {"file_exists": путь} | {"contains": текст} | {"model": "что проверить"}"""
    def verify(task, step, result):
        c = step.get("check") or {}
        if "file_exists" in c:
            p = Path(root or ".") / c["file_exists"]
            return p.exists(), ("файл есть" if p.exists() else f"нет файла {c['file_exists']}")
        if "contains" in c:
            ok = c["contains"].lower() in (result or "").lower()
            return ok, ("нужный текст есть" if ok else f"в ответе нет «{c['contains']}»")
        if "model" in c and hub:
            q = (f"Шаг: {step['text']}\nТребование: {c['model']}\nРезультат:\n{(result or '')[:3000]}\n\n"
                 "Результат выполняет требование? Ответь одним словом: ДА или НЕТ, и через тире причину.")
            r = hub.chat("lmstudio", [{"role": "user", "content": q}], model=model, max_tokens=60, temperature=0,
                         reason="проверка шага")
            ans = r["text"].strip()
            return ans.upper().startswith("ДА"), ans[:200]
        return True, ""
    return verify
