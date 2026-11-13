"""Задание 2, этапы 2 и 11 — команда агентов. Янка — координатор.

Роли — отдельные «специалисты» с характером и правилами (своя системная подсказка) поверх той же модели на ПК.
Координатор: понимает задачу → техзадание → выбирает роли → план подзадач с зависимостями →
независимые подзадачи выполняются параллельно → результаты передаются дальше → проверяющий агент
смотрит результат (до 1 доработки) → итог собирает Янка.

Ограничения, чтобы не было бесконечных циклов и лишней нагрузки: не больше 6 подзадач, 2 параллельных
исполнителя (одна видеокарта 8 ГБ), 1 доработка на подзадачу, общий лимит токенов и времени, кнопка «Стоп».
Агенты ничего не меняют на ПК сами: код и файлы — через Клодика (с подтверждениями), программы — через Gate.
"""
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

ROLES = {
    "architect": {"name": "Архитектор", "emoji": "🏛️", "about": "проектирует устройство: части, связи, данные, порядок работ. "
                  "Пишет коротко, схемами из списков, называет риски."},
    "developer": {"name": "Разработчик", "emoji": "💻", "about": "пишет код и точные инструкции по реализации. "
                  "Даёт готовые фрагменты кода с пояснением, куда их положить. Файлы сам не меняет — это сделает Клодик."},
    "tester": {"name": "Тестировщик", "emoji": "🧪", "about": "ищет ошибки и слабые места, придумывает проверки и тесты, "
               "строго говорит, что не так и как проверить."},
    "researcher": {"name": "Исследователь", "emoji": "🔎", "about": "собирает и сравнивает сведения, отделяет факты от догадок, "
                   "честно пишет, чего не знает."},
    "designer": {"name": "Дизайнер", "emoji": "🎨", "about": "продумывает внешний вид и удобство: цвета, расположение, "
                 "ощущение. Стиль CozyCode: тёмная тема, розовые акценты, уютно."},
    "analyst": {"name": "Аналитик", "emoji": "📊", "about": "сравнивает варианты по понятным критериям и принимает решение "
                "с обоснованием."},
    "security": {"name": "Безопасник", "emoji": "🛡️", "about": "оценивает риски: данные, ключи, удаление, доступы, деньги. "
                 "Предлагает, как сделать безопаснее."},
    "pm": {"name": "Менеджер проекта", "emoji": "🗂️", "about": "делит работу на этапы, следит за порядком и сроками, "
           "пишет понятный план действий."},
}
LIMITS = {"max_subtasks": 6, "parallel": 2, "revisions": 1, "max_tokens_total": 40000, "max_seconds": 1500}


def _role_prompt(role):
    r = ROLES[role]
    return (f"Ты — {r['name']} {r['emoji']} в команде помощницы Янки. Ты {r['about']} "
            "Отвечай по-русски, по делу, без воды. Если чего-то не знаешь — так и скажи.")


def _json_block(text, opener="["):
    closer = "]" if opener == "[" else "}"
    m = re.search(re.escape(opener) + r".*" + re.escape(closer), text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


class Team:
    def __init__(self, hub, store_tasks, pulse=None, log=print):
        self.hub, self.tasks, self.pulse, self.log = hub, store_tasks, pulse, log
        self.running = {}  # task_id -> {"stop": Event, "agents": {sub_id: {...}}}

    # ---------- общение с моделью ----------
    def _ask(self, run, role, prompt, max_tokens=900, model=None):
        if run["stop"].is_set():
            raise InterruptedError("остановлено")
        if time.time() - run["t0"] > LIMITS["max_seconds"]:
            raise TimeoutError("команда работает слишком долго — останавливаю")
        if run["tokens"] > LIMITS["max_tokens_total"]:
            raise RuntimeError("исчерпан лимит токенов на задачу")
        sp = self.pulse.start("agent", f"{ROLES[role]['name']}", who=ROLES[role]["name"], detail=prompt[:120]) if self.pulse else None
        try:
            r = self.hub.chat("lmstudio", [{"role": "system", "content": _role_prompt(role)},
                                           {"role": "user", "content": prompt}],
                              model=model or run["model"], max_tokens=max_tokens, private=True,
                              reason=f"агент: {ROLES[role]['name']}", timeout=300)
        except Exception as e:
            if sp:
                self.pulse.end(sp, ok=False, error=str(e))
            raise
        run["tokens"] += (r.get("in") or 0) + (r.get("out") or 0)
        if sp:
            self.pulse.end(sp, ok=True, tokens=(r.get("in") or 0) + (r.get("out") or 0))
        text = re.sub(r"<think>.*?</think>", "", r["text"] or "", flags=re.S).strip()
        return text

    # ---------- 1. понять → ТЗ → роли и подзадачи ----------
    def plan(self, goal, model):
        run = {"stop": threading.Event(), "t0": time.time(), "tokens": 0, "model": model}
        spec = self._ask(run, "pm", "Цель Яны:\n" + goal[:3000] + "\n\nСформулируй короткое техническое задание: "
                         "что должно получиться, из каких частей, критерии готовности (3–6 пунктов).", 700)
        roles = ", ".join(f"{k} ({v['name']})" for k, v in ROLES.items())
        raw = self._ask(run, "pm", f"Техзадание:\n{spec}\n\nРаздели работу на 2–{LIMITS['max_subtasks']} подзадач для команды. "
                        f"Роли: {roles}. Для каждой подзадачи укажи роль, зависимости (номера подзадач, без которых нельзя "
                        "начать) и кто проверяет результат (другая роль или пусто). Ответь ТОЛЬКО JSON-массивом: "
                        "[{\"n\": 1, \"role\": \"architect\", \"text\": \"…\", \"deps\": [], \"review\": \"tester\"}]", 900)
        items = _json_block(raw) or []
        subs = []
        for i, it in enumerate(items[:LIMITS["max_subtasks"]], 1):
            if not isinstance(it, dict) or not str(it.get("text", "")).strip():
                continue
            role = it.get("role") if it.get("role") in ROLES else "developer"
            review = it.get("review") if it.get("review") in ROLES and it.get("review") != role else None
            subs.append({"text": str(it["text"]).strip()[:400], "executor": "local", "role": role, "review": review,
                         "deps": [int(d) for d in (it.get("deps") or []) if str(d).isdigit()]})
        if not subs:  # модель не справилась с JSON — один исполнитель
            subs = [{"text": goal[:400], "executor": "local", "role": "developer", "review": "tester", "deps": []}]
        valid = set(range(1, len(subs) + 1))
        for n, s in enumerate(subs, 1):
            s["deps"] = sorted(d for d in s["deps"] if d in valid and d < n)  # только назад — без циклов
        t = self.tasks.create(goal, subs, title="👥 " + goal[:70])
        t["kind"], t["spec"], t["team"] = "team", spec, sorted({s["role"] for s in subs} | {s["review"] for s in subs if s["review"]})
        self.tasks.save()
        return t

    # ---------- 2. выполнение ----------
    def run(self, tid, model, on_done=None):
        t = self.tasks.get(tid)
        if not t or tid in self.running:
            return False
        run = {"stop": threading.Event(), "t0": time.time(), "tokens": 0, "model": model, "agents": {}}
        self.running[tid] = run
        threading.Thread(target=self._go, args=(t, run, on_done), daemon=True).start()
        return True

    def stop(self, tid):
        r = self.running.get(tid)
        if r:
            r["stop"].set()
        return bool(r)

    def _context(self, t, step):
        parts = [f"Общая цель: {t['goal'][:1500]}", f"Техзадание:\n{t.get('spec', '')[:1500]}"]
        for d in step.get("deps") or []:
            prev = t["steps"][d - 1]
            parts.append(f"Результат подзадачи {d} ({ROLES[prev['role']]['name']}):\n{prev['result'][:1800]}")
        return "\n\n".join(parts)

    def _do_step(self, t, step, run):
        agent = {"role": step["role"], "text": step["text"], "state": "работает", "since": time.time()}
        run["agents"][step["n"]] = agent
        try:
            step.update(status="running", attempts=step.get("attempts", 0) + 1)
            self.tasks.save()
            out = self._ask(run, step["role"], self._context(t, step) + f"\n\nТвоя подзадача: {step['text']}\n"
                            "Сделай её и выдай готовый результат.", 1400)
            review = step.get("review")
            for _ in range(LIMITS["revisions"] + 1):
                if not review:
                    break
                agent["state"] = f"проверяет {ROLES[review]['name']}"
                verdict = self._ask(run, review, f"Подзадача: {step['text']}\n\nРезультат коллеги ({ROLES[step['role']]['name']}):\n"
                                    f"{out[:3000]}\n\nПроверь. Первой строкой напиши ОК или ДОРАБОТАТЬ, дальше — коротко почему "
                                    "и что исправить.", 500)
                step["review_note"] = verdict[:600]
                if verdict.strip().upper().startswith(("ОК", "OK")):
                    break
                if step.get("revised"):
                    break  # одна доработка — дальше не крутимся
                step["revised"] = True
                agent["state"] = "дорабатывает"
                out = self._ask(run, step["role"], self._context(t, step) + f"\n\nТвоя подзадача: {step['text']}\n\n"
                                f"Твой прошлый результат:\n{out[:2500]}\n\nЗамечания проверяющего:\n{verdict[:800]}\n\n"
                                "Исправь и выдай итоговый результат.", 1400)
            step.update(status="done", result=out[:6000], error="")
            agent["state"] = "готово"
        except Exception as e:
            step.update(status="failed", error=f"{type(e).__name__}: {e}"[:300])
            agent["state"] = "ошибка"
        finally:
            agent["sec"] = round(time.time() - agent.pop("since"), 1)
            self.tasks.save()

    def _go(self, t, run, on_done):
        t["status"] = "running"
        self.tasks.save()
        try:
            with ThreadPoolExecutor(max_workers=LIMITS["parallel"]) as pool:
                while not run["stop"].is_set():
                    done = {s["n"] for s in t["steps"] if s["status"] == "done"}
                    failed = [s for s in t["steps"] if s["status"] == "failed"]
                    ready = [s for s in t["steps"] if s["status"] == "pending" and set(s.get("deps") or []) <= done]
                    if failed or not ready:
                        break
                    list(pool.map(lambda s: self._do_step(t, s, run), ready[:LIMITS["parallel"] * 2]))
            if run["stop"].is_set():
                t["status"] = "pending"
                for s in t["steps"]:
                    if s["status"] == "running":
                        s["status"] = "pending"
                summary = "⏹ Команда остановлена. Можно продолжить позже."
            elif any(s["status"] == "failed" for s in t["steps"]):
                t["status"] = "failed"
                bad = next(s for s in t["steps"] if s["status"] == "failed")
                summary = f"❌ Подзадача {bad['n']} ({ROLES[bad['role']]['name']}) не получилась: {bad['error']}"
            elif all(s["status"] == "done" for s in t["steps"]):
                summary = self._merge(t, run)
                t["status"] = "done"
            else:
                t["status"] = "pending"
                summary = "Часть подзадач ждёт — не выполнены зависимости."
            t["summary"] = summary
        except Exception as e:
            t["status"], t["summary"] = "failed", f"Ошибка команды: {type(e).__name__}: {e}"
        finally:
            t["tokens"] = run["tokens"]
            t["seconds"] = round(time.time() - run["t0"], 1)
            self.tasks.save()
            self.running.pop(t["id"], None)
            if on_done:
                try:
                    on_done(t)
                except Exception as e:
                    self.log(f"Команда: итог не отправился ({e})")

    def _merge(self, t, run):
        parts = "\n\n".join(f"{s['n']}. {ROLES[s['role']]['emoji']} {ROLES[s['role']]['name']} — {s['text']}\n{s['result'][:1500]}"
                            for s in t["steps"])
        try:
            return self._ask(run, "pm", f"Цель Яны: {t['goal'][:1000]}\n\nРезультаты команды:\n{parts[:9000]}\n\n"
                             "Собери единый итог для Яны: что получилось, главное из каждой части, что делать дальше "
                             "(если нужен код в файлах — так и скажи, это сделает Клодик). Тепло и коротко, по-русски.", 1200)
        except Exception as e:
            return f"Итог не собрался ({e}). Результаты подзадач — в «📋 Задачи»."

    def live(self):
        out = []
        for tid, r in list(self.running.items()):
            for n, a in r["agents"].items():
                if a.get("state") not in ("готово", "ошибка"):
                    out.append({"task": tid, "n": n, "role": a["role"], "name": ROLES[a["role"]]["name"],
                                "emoji": ROLES[a["role"]]["emoji"], "state": a["state"], "text": a["text"][:120],
                                "sec": round(time.time() - a.get("since", time.time()), 1)})
        return out
