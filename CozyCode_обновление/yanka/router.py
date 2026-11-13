"""Этап 4 - маршрутизация: кому отдать задачу и почему.

Исполнители:
  local    - модель на ПК (LM Studio): болтовня, вопросы, простые тексты. Бесплатно, данные не уходят.
  cloud    - облачная модель: сложные рассуждения, если Яна дала ключ и лимит (сейчас выключено).
  klodik   - Клодик (Claude Code через Notion): код, файлы, большие проекты, обновления CozyCode.
  blender / roblox - программы через MCP (programs.py), когда задача про 3D-сцену или игру.

Сначала правила (быстро и предсказуемо), локальная модель - только чтобы уточнить спорные случаи.
Ручной выбор: в начале сообщения @пк / @облако / @клодик / @blender / @roblox.
"""
import json
import re
import sys

EXECUTORS = {
    "local": "🐑 Янка на ПК", "cloud": "☁️ облачная модель", "klodik": "✨ Клодик",
    "blender": "🧊 Blender", "roblox": "🎮 Roblox Studio",
}

MANUAL = {"@пк": "local", "@local": "local", "@облако": "cloud", "@cloud": "cloud", "@клодик": "klodik",
          "@klodik": "klodik", "@blender": "blender", "@блендер": "blender", "@roblox": "roblox", "@роблокс": "roblox",
          "@gemini": "cloud", "@гугл": "cloud", "@google": "cloud"}

# (тип задачи, слова-признаки). Порядок важен: более узкое - выше.
RULES = [
    ("blender", r"blender|блендер|3d[- ]?(модел|сцен)|рендер|меш|шейдер"),
    ("roblox", r"roblox|роблокс|luau|studio\b|обби|obby"),
    ("code", r"код|скрипт|программ|функци|баг|ошибк\w* в|traceback|exception|python|питон|пайтон|javascript|\bjs\b|html|css|"
             r"сайт|приложени|компилир|тест\w*|рефактор|git\b|npm|pip\b|\.py\b|\.js\b|cozycode|обновлени\w* cozy"),
    ("files", r"удали|перемест|скопир|переимен|почист|создай (файл|папк)|запиши в файл|сохрани в|разбери (папк|файл)"),
    ("look", r"что (лежит|есть|внутри)|покажи|какие файлы|сколько (файлов|места)|найди (файл|на пк)|прочитай|"
             r"посмотри (файл|папк)|что в (файле|папке)"),
    ("plan", r"план|распиши|по шагам|организуй|расписани|проект\b|задач\w* на|помоги спланировать"),
    ("chat", r"привет|как дела|расскажи|что такое|почему|объясни|придумай|сочини|переведи|посоветуй|спасибо|"
             r"скучно|грустно|давай поговорим"),
]


def _norm(text):
    return (text or "").lower().replace("ё", "е")


def classify(text):
    t = _norm(text)
    hits = [kind for kind, rx in RULES if re.search(rx, t)]
    return hits[0] if hits else "chat", hits


def complexity(text, kind):
    """0..1: длина, количество шагов, «большие» слова."""
    t = _norm(text)
    score = min(len(t) / 1200, 0.4)
    score += 0.1 * min(3, len(re.findall(r"(?m)^\s*(\d+[.)]|[-•*])\s", text or "")))
    if re.search(r"целиком|полностью|архитектур|с нуля|весь проект|много файлов|сложн", t):
        score += 0.3
    if kind in ("code", "files", "blender", "roblox"):
        score += 0.2
    return round(min(score, 1.0), 2)


class Decision(dict):
    """{executor, provider, model, kind, complexity, reason, manual, fallback_from}"""


class Router:
    def __init__(self, hub, roles=None):
        self.hub = hub
        # какие модели для каких ролей (ключи LM Studio); можно поменять в настройках Янки
        self.roles = roles or hub.store.cfg.setdefault("roles", {"chat": "", "code": "", "fast": ""})

    # --- доступность ---
    def _local_ok(self):
        p = self.hub.store.provider("lmstudio")
        return bool(p and p.get("enabled", True) and self.hub.check("lmstudio", timeout=2)["ok"])

    def _cloud(self, manual=False):
        """Облачный провайдер: включён и с ключом. Платный - только при остатке лимита.
        Бесплатный (free, напр. Gemini) - только когда Яна сама написала @облако/@gemini:
        сам Янка туда ничего не отправляет."""
        left = self.hub.store.budget_left()
        paid_ok = left["day"] > 0 and left["month"] > 0
        for p in self.hub.store.providers():
            if p.get("local") or not p.get("enabled", True) or not p.get("key"):
                continue
            if paid_ok and not p.get("free"):
                return p["id"]
            if manual and p.get("free"):
                return p["id"]
        return None


    def _programs(self):
        # только если Янка работает внутри CozyCode; сами bridge не импортируем (у него побочные эффекты)
        br = sys.modules.get("bridge")
        try:
            return {c.get("id") for c in br.PROGRAMS.configs()} if br else set()
        except Exception:
            return set()

    # --- решение ---
    def route(self, text, private=False, programs=None, images=False):
        t = (text or "").strip()
        first = t.split(maxsplit=1)[0].lower() if t else ""
        manual = MANUAL.get(first)
        kind, hits = classify(t)
        cx = complexity(t, kind)
        programs = self._programs() if programs is None else set(programs)
        local_ok = self._local_ok()
        cloud = self._cloud(manual=manual == "cloud")

        if images and manual not in ("cloud", "klodik"):
            want, why = "local", "картинку посмотрю сама - модель на ПК умеет видеть, и снимок не уходит в интернет"
        elif manual:
            want, why = manual, "ты выбрала сама"
        elif kind in ("blender", "roblox"):
            want, why = kind, f"задача про {EXECUTORS[kind]} - работаю прямо в программе"
        elif kind in ("code", "files"):
            want, why = "klodik", "код и файлы лучше всех делает Клодик, и у него есть все разрешения CozyCode"
        elif kind == "look":
            want, why = "local", "посмотреть файлы могу сама (только чтение)"
        elif kind == "plan" and cx >= 0.5:
            want, why = ("cloud", "большой план - нужна модель посильнее") if cloud else \
                ("klodik", "большой план, облачных моделей нет - зову Клодика")
        elif cx >= 0.6 and cloud and not private:
            want, why = "cloud", "задача сложная, а облачная модель включена"
        else:
            want, why = "local", "простой разговор - отвечаю сама на ПК, бесплатно и без интернета"

        d = Decision(executor=want, kind=kind, complexity=cx, manual=bool(manual), reason=why, provider=None,
                     model=None, fallback_from=None, hits=hits)

        # проверка, что исполнитель доступен, иначе запасной вариант
        if want == "cloud" and (not cloud or (private and not self.hub.store.cfg.get("cloud_private_ok"))):
            reason = "облачные модели выключены" if not cloud else "тут твои личные данные, в облако без разрешения не отправляю"
            d.update(executor="local" if local_ok else "klodik", fallback_from="cloud", reason=reason)
        elif want in ("blender", "roblox") and not local_ok:
            d.update(executor="klodik", fallback_from=want, reason="LM Studio не запущен - с программой поможет Клодик")
        elif want in ("blender", "roblox") and programs and want not in programs:
            d.update(executor="klodik", fallback_from=want, reason=f"{EXECUTORS[want]} не подключён в CozyCode - передаю Клодику")
        elif want == "local" and not local_ok:
            if cloud and not private:
                d.update(executor="cloud", fallback_from="local", reason="LM Studio не запущен - отвечает облачная модель")
            else:
                d.update(executor="klodik", fallback_from="local", reason="LM Studio не запущен - передаю Клодику")

        if d["executor"] in ("local", "blender", "roblox"):  # в программах Янка работает той же моделью на ПК
            role = "code" if kind == "code" else ("fast" if cx < 0.15 and self.roles.get("fast") else "chat")
            d.update(provider="lmstudio", model=self.roles.get(role) or self.hub.store.provider("lmstudio").get("model"))
        elif d["executor"] == "cloud":
            p = self.hub.store.provider(cloud)
            d.update(provider=cloud, model=p.get("model"))
        return d

    def refine(self, text, d):
        """Спорный случай (ничего не нашли по правилам, длинный текст) - спросим локальную модель."""
        if d["manual"] or d["hits"] or d["executor"] != "local" or len(text) < 200:
            return d
        prompt = ("Определи тип просьбы. Ответь ТОЛЬКО JSON: {\"kind\": \"chat|code|files|plan|blender|roblox\"}.\n"
                  "Просьба: " + text[:1500])
        try:
            r = self.hub.chat("lmstudio", [{"role": "user", "content": prompt}], model=d["model"], max_tokens=30,
                              temperature=0, reason="маршрутизация")
            m = re.search(r"\{.*\}", r["text"], re.S)
            kind = json.loads(m.group(0)).get("kind") if m else None
        except Exception:
            return d
        if kind in ("code", "files", "blender", "roblox", "plan") and kind != d["kind"]:
            nd = self.route(("@" + {"code": "клодик", "files": "клодик", "plan": "клодик"}.get(kind, kind)) + " " + text)
            nd.update(manual=False, kind=kind, reason=f"модель на ПК поняла это как «{kind}» → {EXECUTORS[nd['executor']]}")
            return nd
        return d


def explain(d):
    """Короткая строка для окна: кто отвечает и почему."""
    who = EXECUTORS.get(d["executor"], d["executor"])
    model = f" · {d['model']}" if d.get("model") else ""
    return f"{who}{model} — {d['reason']}"
