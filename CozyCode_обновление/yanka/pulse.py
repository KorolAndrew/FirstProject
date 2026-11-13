"""Задание 2, этапы 1 и 10 — «Пульс»: что Янка делает прямо сейчас, сколько это стоит по времени и ресурсам,
где ошибки и что можно улучшить. Только наблюдает и предлагает — код и защиту сама не меняет.

Записи (span): модуль, действие, длительность, успех/ошибка, детали. Хранятся в памяти (последние 2000)
и в data/yanka_pulse.jsonl (для анализа за неделю). Активные действия видны, пока идут.
"""
import collections
import datetime
import functools
import json
import os
import threading
import time
import uuid
from pathlib import Path

MODULE_RU = {"model": "🧠 модели", "tool": "🔧 инструменты", "task": "📋 задачи", "agent": "👥 агенты",
             "router": "🧭 маршрут", "memory": "💜 память", "voice": "🎙️ голос", "vision": "👀 зрение",
             "program": "🧩 программы", "chat": "💬 чат"}
SLOW = {"model": 30.0, "tool": 10.0, "task": 600.0, "agent": 300.0, "program": 60.0, "vision": 60.0}


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


class Pulse:
    def __init__(self, data_dir, keep=2000):
        self.file = Path(data_dir) / "yanka_pulse.jsonl"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.recent = collections.deque(maxlen=keep)
        self.active = {}  # id -> span (идёт прямо сейчас)
        self.ratings = collections.deque(maxlen=500)
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
        try:
            with open(self.file, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("t", "") < week_ago:
                        continue
                    (self.ratings if r.get("kind") == "rating" else self.recent).append(r)
        except FileNotFoundError:
            pass

    def _write(self, row):
        try:
            with open(self.file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if self.file.stat().st_size > 5 * 1024 * 1024:  # не даём журналу разрастись
                lines = self.file.read_text(encoding="utf-8").splitlines()[-4000:]
                tmp = self.file.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.replace(tmp, self.file)
        except OSError:
            pass

    # --- запись ---
    def start(self, module, action, who="Янка", detail=""):
        sid = uuid.uuid4().hex[:10]
        with self._lock:
            self.active[sid] = {"id": sid, "module": module, "action": str(action)[:120], "who": who,
                                "detail": str(detail)[:200], "t": now(), "t0": time.time()}
        return sid

    def end(self, sid, ok=True, error="", **extra):
        with self._lock:
            sp = self.active.pop(sid, None)
        if not sp:
            return None
        row = {"t": sp["t"], "module": sp["module"], "action": sp["action"], "who": sp["who"], "detail": sp["detail"],
               "sec": round(time.time() - sp.pop("t0"), 3), "ok": bool(ok), "error": str(error)[:300]}
        row.update({k: v for k, v in extra.items() if isinstance(v, (int, float, str, bool)) or v is None})
        with self._lock:
            self.recent.append(row)
        self._write(row)
        return row

    def span(self, module, action, who="Янка", detail=""):
        pulse = self

        class _Span:
            def __enter__(self):
                self.sid = pulse.start(module, action, who, detail)
                self.extra = {}
                return self

            def __exit__(self, et, ev, tb):
                pulse.end(self.sid, ok=et is None, error=f"{et.__name__}: {ev}" if et else "", **self.extra)
                return False
        return _Span()

    def wrap(self, module, action_of, fn, who="Янка", extra_of=None):
        """Обернуть функцию: каждый вызов - запись в пульсе."""
        @functools.wraps(fn)
        def inner(*a, **k):
            with self.span(module, action_of(*a, **k), who) as s:
                res = fn(*a, **k)
                if extra_of:
                    try:
                        s.extra.update(extra_of(res) or {})
                    except Exception:
                        pass
                return res
        return inner

    def rate(self, message_id, good, model="", kind="", text=""):
        row = {"t": now(), "kind": "rating", "message": message_id, "good": bool(good), "model": model,
               "scenario": kind, "text": str(text)[:200]}
        with self._lock:
            self.ratings.append(row)
        self._write(row)
        return row

    # --- чтение ---
    def snapshot(self, last=40):
        with self._lock:
            active = [dict(v, sec=round(time.time() - v["t0"], 1)) for v in self.active.values()]
            recent = list(self.recent)[-last:][::-1]
        for a in active:
            a.pop("t0", None)
        return {"active": sorted(active, key=lambda a: -a["sec"]), "recent": recent}

    def stats(self, hours=24):
        since = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
        with self._lock:
            rows = [r for r in self.recent if r.get("t", "") >= since]
        by = collections.defaultdict(list)
        for r in rows:
            by[(r["module"], r["action"].split(":")[0])].append(r)
        out = []
        for (m, a), rs in by.items():
            secs = [r["sec"] for r in rs]
            errs = [r for r in rs if not r["ok"]]
            out.append({"module": m, "action": a, "calls": len(rs), "avg": round(sum(secs) / len(secs), 2),
                        "p95": round(_pct(secs, 95), 2), "errors": len(errs),
                        "last_error": errs[-1]["error"] if errs else ""})
        out.sort(key=lambda x: (-x["errors"], -x["p95"]))
        return out

    def insights(self, hours=24):
        """Самоанализ: только выводы и предложения. Ничего не меняет сама."""
        tips = []
        st = self.stats(hours)
        for s in st:
            slow = SLOW.get(s["module"])
            if slow and s["calls"] >= 3 and s["p95"] > slow:
                tips.append({"level": "warn", "module": s["module"],
                             "text": f"«{s['action']}» медленно: 95% вызовов до {s['p95']} с (обычно до {slow:g} с).",
                             "idea": _idea_slow(s)})
            if s["calls"] >= 3 and s["errors"] / s["calls"] >= 0.3:
                tips.append({"level": "err", "module": s["module"],
                             "text": f"«{s['action']}» часто падает: {s['errors']} из {s['calls']}.",
                             "idea": f"Последняя ошибка: {s['last_error'][:160]}"})
        # одинаковые ошибки подряд
        with self._lock:
            errs = [r for r in self.recent if not r["ok"]][-50:]
        common = collections.Counter((r["module"], r["error"][:80]) for r in errs)
        for (m, e), n in common.most_common(3):
            if n >= 3:
                tips.append({"level": "err", "module": m, "text": f"Повторяется ошибка ({n} раз): {e}",
                             "idea": "Скорее всего, одна причина. Напиши «@клодик разберись с ошибкой: …» — он починит."})
        # оценки ответов
        with self._lock:
            rates = list(self.ratings)[-100:]
        bad = [r for r in rates if not r["good"]]
        if len(rates) >= 5 and len(bad) / len(rates) > 0.3:
            worst = collections.Counter(r.get("scenario") or "разговор" for r in bad).most_common(1)[0]
            tips.append({"level": "warn", "module": "chat",
                         "text": f"Яне не понравилось {len(bad)} из {len(rates)} ответов, чаще всего — «{worst[0]}».",
                         "idea": "Можно попробовать другую модель для этого (🐑 Янка → 🧠 Модели) или поправить личность."})
        if not tips:
            tips.append({"level": "ok", "module": "", "text": "Всё работает ровно 💚", "idea": ""})
        return tips


def _idea_slow(s):
    if s["module"] == "model":
        return "Модель не помещается в видеокарту целиком или контекст слишком длинный — выгрузи лишние модели или уменьши историю."
    if s["module"] == "program":
        return "Программа отвечает медленно — проверь, не занята ли она рендером."
    return "Стоит посмотреть, что именно тормозит, — могу передать Клодику."
