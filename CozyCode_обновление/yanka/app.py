"""Этап 8 - Янка внутри CozyCode: отвечает в чате сама (модель на ПК), остальное передаёт Клодику.

cozycode.py вызывает только YankaService: route_message(), answer(), state() и действия менеджеров.
Модель на ПК видит только инструменты чтения (list_dir, read_file, search_files, get_info) и только
через Gate - менять файлы и запускать команды в чате Янка пока не может.
"""
import json
from pathlib import Path
import threading
import time

from . import local
from .memory import KINDS, Memory
from .permissions import Denied, Gate
from .providers import Hub, ProviderError
from .router import EXECUTORS, Router, explain
from .store import TEMPLATES
from .tasks import Tasks, make_verify, plan
from . import vision, voice
from .pulse import MODULE_RU, Pulse
from .team import LIMITS, ROLES, Team

PERSONA = ("Ты — Янка 🐑, тёплая и заботливая помощница Яны в приложении CozyCode на её ПК. "
           "Отвечай по-русски, тепло, коротко и по делу, иногда с лёгкими эмодзи (🐑✨💕). "
           "Ты работаешь на модели прямо на ПК Яны. Код, изменения файлов и большие проекты делает Клодик — "
           "если просьба про это, предложи Яне написать «@клодик …». Не выдумывай факты; если не знаешь — так и скажи. "
           "Если Яна спрашивает про файлы, сама вызови инструмент list_dir или read_file — не проси её вводить команды. "
           "Менять файлы ты не можешь (только смотреть).")
CHAT_TOOLS = ("get_info", "list_dir", "read_file", "search_files")
TAGS = ("@команда", "@team", "@пк", "@local", "@облако", "@cloud", "@gemini", "@гугл", "@google", "@blender", "@блендер", "@roblox", "@роблокс")
PROGRAM_PERSONA = ("\n\nСейчас ты работаешь прямо в программе «{name}» через её инструменты. Делай то, что просит Яна, "
                   "маленькими шагами: сначала посмотри (инструменты get_/list_/info), потом действуй. "
                   "Не больше 6 действий. В конце коротко расскажи, что сделала. Если инструмент вернул ошибку — "
                   "объясни её простыми словами и не повторяй то же самое.")
VISION_PERSONA = ("\n\nЯна показала тебе картинку или снимок экрана. Опиши, что видишь, и ответь на её вопрос. "
                  "Если на экране ошибка — объясни её простыми словами и подскажи, что сделать.")
CLOUD_PERSONA = ("Ты — Янка 🐑, тёплая помощница Яны. Отвечай по-русски, тепло, по делу, с лёгкими эмодзи. "
                 "Сейчас ты отвечаешь через облачную модель.")


def _strip_tag(text):
    t = (text or "").strip()
    low = t.lower()
    for tag in TAGS:
        if low.startswith(tag):
            return t[len(tag):].strip()
    return t


class YankaService:
    def __init__(self, data_dir, bridge, log=print):
        self.br, self.log = bridge, log
        self.hub = Hub(data_dir)
        self.router = Router(self.hub)
        self.memory = Memory(data_dir)
        self.tasks = Tasks(data_dir)
        self.gate = Gate(data_dir, {
            "call_tool": lambda n, a: bridge.call_tool_as(n, a, "Янка 🐑"),
            "ask": lambda t, m: bridge.ask_user(t, m),
            "list_backups": bridge.list_backups,
            "restore_backup": bridge.restore_backup,
        })
        self._local_cache = {"t": 0, "rep": None}
        self._prep_lock = threading.Lock()
        self._since_extract = {}
        self.last = {}  # pid -> последнее решение (для окна: кто отвечал и почему)
        # cozycode.py подставляет: to_klodik(pid, text) - сообщение Клодику; post(pid, text) - ответ Янки в чат
        self.hooks = {"to_klodik": None, "post": None}
        self._running = {}  # task_id -> threading.Event (стоп)
        self.listener = voice.Listener(log)
        # задание 2: «пульс» видит каждый вызов модели и инструмента
        self.pulse = Pulse(data_dir)
        self.hub.chat = self.pulse.wrap(
            "model", lambda pid, *a, **k: f"{pid}: {k.get('reason') or 'запрос'}", self.hub.chat,
            extra_of=lambda r: {"model": r.get("model"), "tokens": (r.get("in") or 0) + (r.get("out") or 0),
                                "usd": r.get("usd", 0)})
        self.gate.call = self.pulse.wrap(
            "tool", lambda name, args=None, **k: (f"{name}: {(args or {}).get('tool')}" if name == "mcp_call" else name),
            self.gate.call, who="Янка")
        self.team = Team(self.hub, self.tasks, self.pulse, log)
        self._import_keys(Path(data_dir) / "import_keys.json")

    # ---------- маршрут ----------
    def _import_keys(self, f):
        """Ключи, которые Клодик положил для Яны: шифруем (DPAPI) и сразу удаляем открытый файл."""
        if not f.exists():
            return
        try:
            for p in json.loads(f.read_text(encoding="utf-8")).get("providers", []):
                self.hub.store.upsert_provider(p["id"], key=p.get("key") or None,
                                               **{k: p[k] for k in ("model", "enabled") if k in p})
                self.log(f"Янка: добавлен провайдер {p['id']}")
        except Exception as e:
            self.log(f"Янка: ключи не импортировались ({e})")
        finally:
            try:
                f.unlink()
            except OSError:
                pass

    def route_message(self, text, mode, images=False):
        """mode: off - всё Клодику (как раньше); auto - Янка решает; local - всегда Янка."""
        if mode == "off" and not text.lower().startswith(("@пк", "@local")):
            return None
        if mode == "local" and not text.startswith("@"):
            text = "@пк " + text
        return self.router.route(text, images=images)

    # ---------- подготовка модели ----------
    def ensure_local(self):
        """Запустить сервер LM Studio и загрузить модель Янки, если ещё не загружена (~10–20 с)."""
        with self._prep_lock:
            lm = local.LMStudio()
            if not lm.installed:
                raise ProviderError("unavailable", "LM Studio не установлен", "lmstudio")
            lm.start_server()
            want = self.router.roles.get("chat") or self.hub.store.provider("lmstudio").get("model")
            if not want:
                want = local.best_downloaded(local.report())
                if not want:
                    raise ProviderError("unavailable", "нет скачанной модели, которая помещается в видеокарту", "lmstudio")
            loaded = {m.get("identifier") or m.get("modelKey") for m in lm.loaded()}
            if want not in loaded:
                self.log(f"Янка: загружаю модель {want}…")
                lm.load(want)
            self.hub.store.upsert_provider("lmstudio", model=want)
            return want

    # ---------- ответ ----------
    def _program_tools(self, program):
        """Инструменты программы (Blender / Roblox Studio) в виде функций для модели."""
        p = self.br.PROGRAMS.get(program)
        out = []
        for t in p.list_tools()[:40]:
            out.append({"name": t["name"], "description": " ".join(str(t.get("description") or "").split())[:300],
                        "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}})
        return p.name, out

    def _answer_cloud(self, pid, text, decision, status, images):
        """Облако: только сам вопрос, без памяти, истории и файлов Яны (личное наружу не уходит)."""
        prov = decision["provider"]
        status(f"☁️ Янка спрашивает {self.hub.store.provider(prov).get('name', prov)}…")
        content = text or "Что на картинке?"
        if images:
            content = [{"type": "text", "text": content}] + [{"type": "image_url", "image_url": {"url": vision.data_url(i)}}
                                                              for i in images]
        r = self.hub.chat(prov, [{"role": "system", "content": CLOUD_PERSONA}, {"role": "user", "content": content}],
                          model=decision.get("model"), max_tokens=1500, private=False, reason="ответ в чате (облако)")
        reply = _strip_think(r["text"]) or "Облачная модель промолчала 🙈"
        meta = {"who": "yanka", "model": r["model"], "provider": prov, "why": explain(decision), "tools": [],
                "tokens": (r.get("in") or 0) + (r.get("out") or 0), "seconds": r.get("seconds", 0),
                "usd": r.get("usd", 0), "cloud": True}
        self.last[pid] = meta
        return reply, meta

    def answer(self, pid, project_folder, text, history, decision, status=None, images=None):
        """Ответ Янки. status(text) - показать статус в окне. images - пути к картинкам. Возвращает (текст, meta)."""
        status = status or (lambda s: None)
        text = _strip_tag(text)
        images = [i for i in (images or []) if vision.is_image(i)]
        executor = decision.get("executor")
        try:
            if executor == "cloud":
                return self._answer_cloud(pid, text, decision, status, images)
            model = decision.get("model") or ""
            if not self.hub.check("lmstudio", timeout=2)["ok"] or not model:
                status("🐑 Янка просыпается (запускаю модель на ПК)…")
                model = self.ensure_local()
                decision["model"] = model
            mem = self.memory.recall(text, project=pid)
            system = PERSONA + (f"\n\nПапка проекта: {project_folder}") + (f"\n\nЧто ты помнишь:\n{mem}" if mem else "")
            used = []
            if decision.get("kind") == "look":
                # модели на 9B часто не зовут инструмент сами - смотрим папку заранее (только чтение, через Gate)
                try:
                    res = self.gate.call("list_dir", {"path": "."}, task_id=f"chat-{pid}", reason="вопрос про файлы")
                    listing = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
                    system += "\n\nСодержимое папки проекта (ты только что посмотрела):\n" + listing[:4000]
                    used.append("list_dir")
                except Denied:
                    pass
            msgs = [{"role": "system", "content": system}]
            for m in history[-10:]:
                if m.get("role") in ("user", "assistant") and m.get("text"):
                    msgs.append({"role": m["role"], "content": str(m["text"])[:1500]})
            program = executor if executor in ("blender", "roblox") else None
            if program:
                status(f"🐑 Янка открывает {EXECUTORS[program]}…")
                pname, tools = self._program_tools(program)
                msgs[0]["content"] += PROGRAM_PERSONA.format(name=pname)
            elif images:
                tools = []
                msgs[0]["content"] += VISION_PERSONA
            else:
                tools = [t for t in self.gate.tools_for_model(self.br.TOOLS) if t["name"] in CHAT_TOOLS]
            if images:
                status("👀 Янка смотрит на картинку…")
                parts = [{"type": "text", "text": text or "Что ты видишь?"}]
                parts += [{"type": "image_url", "image_url": {"url": vision.data_url(i)}} for i in images]
                msgs.append({"role": "user", "content": parts})
                used.append("👀 картинка")
            else:
                msgs.append({"role": "user", "content": text})
            total = {"in": 0, "out": 0, "seconds": 0.0}
            status("🐑 Янка думает…")
            rounds = 7 if program else 5
            for _ in range(rounds):  # ограничение действий за один ответ
                r = self.hub.chat("lmstudio", msgs, model=model, max_tokens=900, tools=tools or None, private=True,
                                  reason="ответ в чате", timeout=300)
                for k in total:
                    total[k] += r.get(k) or 0
                if not r["tool_calls"]:
                    break
                msgs.append({"role": "assistant", "content": r["text"] or "", "tool_calls": [
                    {"id": c["id"] or f"c{i}", "type": "function",
                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                    for i, c in enumerate(r["tool_calls"])]})
                for i, c in enumerate(r["tool_calls"]):
                    used.append(c["name"])
                    try:
                        if program:
                            status(f"🐑 Янка в {EXECUTORS[program]}: {c['name']}")
                            res = self.gate.call("mcp_call", {"program": program, "tool": c["name"],
                                                              "arguments": c["arguments"] or {}},
                                                 task_id=f"chat-{pid}", reason=text[:150])
                        else:
                            status(f"🐑 Янка смотрит: {c['name']}")
                            if c["name"] not in CHAT_TOOLS:
                                raise Denied("в чате Янке доступно только чтение")
                            res = self.gate.call(c["name"], c["arguments"], task_id=f"chat-{pid}", reason="ответ в чате")
                        out = "".join(x.get("text", "") for x in res.get("content", []) if x.get("type") == "text")
                        if any(x.get("type") == "image" for x in res.get("content", [])):
                            out += "\n(программа прислала снимок - он сохранён в mcp/…/images)"
                    except Denied as e:
                        out = f"Нельзя: {e}"
                    except Exception as e:
                        out = f"Ошибка: {type(e).__name__}: {e}"
                    msgs.append({"role": "tool", "tool_call_id": c["id"] or f"c{i}", "content": out[:6000]})
            reply = (r["text"] or "").strip()
            reply = _strip_think(reply) or "Ой, у меня не получилось придумать ответ 🙈 Попробуй сказать по-другому?"
        except (ProviderError, RuntimeError, ValueError) as e:
            self.log(f"Янка: {e}")
            return None, {"error": str(e)}
        except Exception as e:  # программа не запустилась и т.п.
            self.log(f"Янка: {type(e).__name__}: {e}")
            return None, {"error": f"{type(e).__name__}: {e}"}
        meta = {"who": "yanka", "model": model, "why": explain(decision), "tools": used,
                "tokens": total["in"] + total["out"], "seconds": round(total["seconds"], 1)}
        self.last[pid] = meta
        if not images:
            self._maybe_remember(pid, msgs[1:] + [{"role": "assistant", "content": reply}], model)
        return reply, meta

    def _maybe_remember(self, pid, msgs, model):
        n = self._since_extract.get(pid, 0) + 1
        self._since_extract[pid] = n
        if n < 4:
            return
        self._since_extract[pid] = 0
        convo = [m for m in msgs if m["role"] in ("user", "assistant") and isinstance(m.get("content"), str)]

        def run():
            try:
                self.memory.extract(self.hub, convo, project=pid, model=model)
            except Exception as e:
                self.log(f"Янка: память не обновилась ({e})")
        threading.Thread(target=run, daemon=True).start()

    def _model(self):
        """Модель уже работает - берём её; нет - запускаем LM Studio и грузим."""
        m = self.router.roles.get("chat") or self.hub.store.provider("lmstudio").get("model")
        if m and self.hub.check("lmstudio", timeout=2)["ok"]:
            return m
        return self.ensure_local()

    # ---------- задачи (этап 6) ----------
    def task_plan(self, goal, pid):
        model = self._model()
        steps = plan(self.hub, goal, model=model)
        for s in steps:
            if s["executor"] == "local":
                s["check"] = {"model": "результат по существу выполняет этот шаг"}
        return self.tasks.create(goal, steps, project=pid)

    def task_run(self, tid):
        t = self.tasks.get(tid)
        if not t or tid in self._running:
            return
        stop = threading.Event()
        self._running[tid] = stop
        model = self._model()

        def execute(task, step):
            if step["executor"] != "local":
                who = EXECUTORS.get(step["executor"], step["executor"])
                done = "\n".join(f"{s['n']}. {s['text']} — {s['result'][:300]}" for s in task["steps"]
                                 if s["status"] == "done" and s["result"])
                text = (f"📋 Шаг задачи «{task['title']}» ({who}):\n{step['text']}"
                        + (f"\n\nЧто уже сделано:\n{done}" if done else "")
                        + "\n\nКогда сделаешь — ответь в чат; Яна нажмёт «✅ Готово» в задаче.")
                if self.hooks["to_klodik"]:
                    self.hooks["to_klodik"](task["project"], text)
                return {"waiting": True, "result": f"передала {EXECUTORS['klodik']}"}
            prev = "\n".join(f"{s['n']}. {s['text']}: {s['result'][:600]}" for s in task["steps"]
                              if s["status"] == "done" and s["result"])
            msgs = [{"role": "system", "content": PERSONA},
                    {"role": "user", "content": f"Общая цель: {task['goal']}\n"
                                                + (f"Уже сделано:\n{prev}\n" if prev else "")
                                                + f"Сейчас сделай шаг: {step['text']}\n"
                                                + (f"Прошлая попытка не прошла проверку: {step['error']}\n" if step["error"] else "")
                                                + "Ответь результатом шага."}]
            r = self.hub.chat("lmstudio", msgs, model=model, max_tokens=1200, private=True, reason="шаг задачи")
            out = _strip_think(r["text"])
            return {"ok": bool(out), "result": out, "error": "" if out else "пустой ответ"}

        def go():
            try:
                res = self.tasks.run(tid, execute, verify=make_verify(self.hub, model), max_attempts=2, stop=stop)
                if res["status"] == "done" and self.hooks["post"]:
                    last = next((s["result"] for s in reversed(res["steps"]) if s["result"]), "")
                    self.hooks["post"](res["project"], f"📋 Задача «{res['title']}» готова ✅ ({self.tasks.progress(res)})\n\n"
                                       + last[:3000])
                elif res["status"] == "failed" and self.hooks["post"]:
                    bad = next((s for s in res["steps"] if s["status"] == "failed"), None)
                    self.hooks["post"](res["project"], f"📋 Задача «{res['title']}»: шаг {bad['n'] if bad else '?'} не получился 🙈 "
                                       f"({(bad or {}).get('error', '')[:200]}). Посмотри в «🐑 Янка → 📋 Задачи».")
            except Exception as e:
                self.log(f"Янка: задача {tid}: {e}")
            finally:
                self._running.pop(tid, None)

        threading.Thread(target=go, daemon=True).start()

    # ---------- команда агентов (задание 2) ----------
    def team_chat(self, pid, text):
        """@команда …: план → сообщение с планом → выполнение → итог в чат."""
        post = self.hooks.get("post") or (lambda p, s: None)
        goal = _strip_tag(text)
        try:
            model = self._model()
            t = self.team.plan(goal, model)
            t["project"] = pid
            self.tasks.save()
        except Exception as e:
            post(pid, f"👥 Команда не собралась: {e}")
            return
        lines = "\n".join(f"{s['n']}. {ROLES[s['role']]['emoji']} **{ROLES[s['role']]['name']}** — {s['text']}"
                          + (f" _(после {', '.join(map(str, s['deps']))})_" if s.get("deps") else "")
                          + (f" · проверяет {ROLES[s['review']]['emoji']}" if s.get("review") else "")
                          for s in t["steps"])
        post(pid, f"👥 Собрала команду для «{t['title'][2:]}».\n\n**Техзадание**\n{t.get('spec', '')[:1500]}\n\n"
                  f"**План**\n{lines}\n\nРаботаем! Следить можно в «🐑 Янка → 🫀 Пульс», остановить — в «📋 Задачи».")
        self.team.run(t["id"], model, on_done=lambda tt: post(
            pid, f"👥 **Итог команды** ({self.tasks.progress(tt)}, {tt.get('seconds', 0):.0f} с, {tt.get('tokens', 0)} токенов)\n\n"
                 + (tt.get("summary") or "")))

    # ---------- состояние для окна ----------
    def local_report(self, refresh=False):
        if refresh or time.time() - self._local_cache["t"] > 60:
            try:
                self._local_cache = {"t": time.time(), "rep": local.report()}
            except Exception as e:
                self._local_cache = {"t": time.time(), "rep": {"error": str(e)}}
        return self._local_cache["rep"]

    def state(self, pid=None):
        st = self.hub.status()
        return {
            "providers": st["providers"], "spent": st["spent"], "budget": st["budget"], "roles": self.router.roles,
            "local": self.local_report(), "executors": EXECUTORS, "memory_kinds": KINDS,
            "memory_count": len(self.memory.list(limit=10000)),
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"], "progress": self.tasks.progress(t),
                       "updated": t["updated"], "running": t["id"] in self._running or t["id"] in self.team.running,
                       "steps": [{"n": s["n"], "text": s["text"], "executor": s["executor"], "status": s["status"], "role": s.get("role"),
                                  "result": s["result"][:600], "error": s["error"][:200]} for s in t["steps"]]}
                      for t in self.tasks.list()[:30]],
            "templates": {k: v["name"] for k, v in TEMPLATES.items() if not v.get("local")},
            "journal": self.gate.journal(40)[::-1], "last": self.last.get(pid) if pid else None,
            "team_roles": ROLES,
        }

    def pulse_state(self, confirms=None):
        snap = self.pulse.snapshot(40)
        st = self.hub.status()
        tasks = self.tasks.list()
        try:
            res = local.resources()
        except Exception:
            res = {}
        lm = (self.local_report() or {}).get("lmstudio", {})
        return {
            "active": snap["active"], "recent": snap["recent"], "agents": self.team.live(),
            "tasks_running": [{"id": t["id"], "title": t["title"], "progress": self.tasks.progress(t)}
                              for t in tasks if t["status"] in ("running", "waiting") or t["id"] in self._running
                              or t["id"] in self.team.running],
            "tasks_done": [{"id": t["id"], "title": t["title"], "updated": t["updated"]}
                           for t in tasks if t["status"] == "done"][:8],
            "models": {"loaded": lm.get("loaded") or [], "server": lm.get("server"),
                       "used": sorted({r.get("model") for r in snap["recent"] if r.get("module") == "model" and r.get("model")})},
            "tools": [r for r in snap["recent"] if r.get("module") == "tool"][:8],
            "confirms": confirms or [], "resources": res, "spent": st["spent"],
            "errors": [r for r in snap["recent"] if not r.get("ok")][:8],
            "stats": self.pulse.stats(24)[:12], "insights": self.pulse.insights(24),
            "modules": MODULE_RU, "limits": LIMITS,
        }

    def action(self, path, body):
        if path == "start":
            m = body.get("model")
            if m:
                self.router.roles["chat"] = m
                self.hub.store.save()
            model = self.ensure_local()
            self.local_report(refresh=True)
            return {"ok": True, "model": model}
        if path == "unload":
            local.LMStudio().unload()
            self.local_report(refresh=True)
            return {"ok": True}
        if path == "voice/status":
            return voice.status()
        if path == "listen/start":
            st = voice.status()
            if not st["ready"]:
                raise ValueError("Голос ещё не готов: " + (st["error"] or "нет микрофона"))
            return {"ok": self.listener.start(int(body.get("max_seconds") or 30))}
        if path == "listen/stop":
            self.listener.stop()
            return {"ok": True}
        if path == "listen/poll":
            L = self.listener
            return {"active": L.active, "partial": L.partial, "text": L.text, "error": L.error}
        if path == "provider/remove":
            if body["id"] == "lmstudio":
                raise ValueError("LM Studio убирать нельзя - на нём работает Янка")
            self.hub.store.remove_provider(body["id"])
            return {"ok": True}
        if path == "provider":
            if body["id"] not in TEMPLATES and not self.hub.store.provider(body["id"]):
                raise ValueError("неизвестный провайдер")
            p = self.hub.store.upsert_provider(body["id"], enabled=bool(body.get("enabled", True)),
                                               **({"model": body["model"]} if body.get("model") else {}),
                                               key=body.get("key") or None)
            return {"ok": True, "id": p["id"]}
        if path == "budget":
            self.hub.store.cfg["budget"] = {"daily_usd": max(0.0, float(body.get("daily_usd", 0))),
                                            "monthly_usd": max(0.0, float(body.get("monthly_usd", 0)))}
            self.hub.store.save()
            return {"ok": True}
        if path == "memory":
            q = (body.get("q") or "").strip()
            items = self.memory.search(q, limit=100) if q else self.memory.list(limit=300)
            return {"items": items}
        if path == "memory/save":
            if body.get("id"):
                return {"item": self.memory.edit(int(body["id"]), text=body.get("text"), kind=body.get("kind"),
                                                 pinned=body.get("pinned"))}
            mid, st, why = self.memory.add(body.get("kind") or "fact", body.get("text") or "",
                                           project=body.get("project") or "", source="Яна",
                                           pinned=bool(body.get("pinned")))
            if st == "skipped":
                raise ValueError(why)
            return {"id": mid, "status": st}
        if path == "memory/delete":
            return {"ok": self.memory.delete(int(body["id"]))}
        if path == "task/plan":
            goal = (body.get("goal") or "").strip()
            if len(goal) < 5:
                raise ValueError("Опиши задачу чуть подробнее")
            t = self.task_plan(goal, body.get("project") or "general")
            return {"id": t["id"], "steps": len(t["steps"])}
        if path == "task/run":
            t = self.tasks.get(body["id"])
            if t and t.get("kind") == "team":
                post = self.hooks.get("post") or (lambda p, s: None)
                self.team.run(t["id"], self._model(), on_done=lambda tt: post(tt["project"], "👥 **Итог команды**\n\n" + (tt.get("summary") or "")))
            else:
                self.task_run(body["id"])
            return {"ok": True}
        if path == "task/stop":
            ev = self._running.get(body["id"])
            if ev:
                ev.set()
            return {"ok": bool(ev) or self.team.stop(body["id"])}
        if path == "rate":
            return {"ok": bool(self.pulse.rate(body.get("id", ""), bool(body.get("good")), body.get("model", ""),
                                               body.get("kind", ""), body.get("text", "")))}
        if path == "task/done":  # Яна: Клодик сделал ожидающий шаг
            self.tasks.resume(body["id"], body.get("result") or "сделано")
            self.task_run(body["id"])
            return {"ok": True}
        if path == "task/delete":
            return {"ok": self.tasks.delete(body["id"])}
        if path == "task":
            t = self.tasks.get(body["id"])
            return {"task": t}
        if path == "undo":
            return {"result": self.gate.undo(body["id"])}
        raise ValueError("неизвестное действие Янки")


def _strip_think(text):
    """Модели семейства Qwen иногда пишут рассуждения в <think>…</think> - Яне их не показываем."""
    import re
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
