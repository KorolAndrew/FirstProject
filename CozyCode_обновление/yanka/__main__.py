"""Проверка ядра Янки из консоли:
    python -m yanka status            провайдеры, расходы, лимиты
    python -m yanka local             LM Studio, ресурсы ПК, что поместится
    python -m yanka start [model]     запустить сервер LM Studio и загрузить модель (лучшую из скачанных)
    python -m yanka chat "текст"      спросить локальную модель
    python -m yanka add <id> [key]    добавить провайдера (deepseek, openai, anthropic, openrouter, ollama…)
    python -m yanka route "текст"     кому отдать задачу и почему
    python -m yanka budget <день$> <месяц$>
"""
import json
import os
import sys
from pathlib import Path

from . import local
from .providers import Hub, ProviderError

APP_DIR = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("YANKA_DATA") or APP_DIR / "data")

SYSTEM = ("Ты — Янка, тёплая и заботливая помощница Яны в приложении CozyCode. "
          "Отвечай по-русски, коротко и по делу, иногда с лёгкими эмодзи 🐑✨.")


def p(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    hub = Hub(DATA)
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        for pr in hub.store.providers():
            hub.check(pr["id"])
        p(hub.status())
    elif cmd == "local":
        p(local.report())
    elif cmd == "start":
        lm = local.LMStudio()
        if not lm.installed:
            sys.exit("LM Studio не найден")
        print("сервер:", lm.start_server())
        key = argv[1] if len(argv) > 1 else local.best_downloaded(local.report())
        if not key:
            sys.exit("нет скачанной модели, которая помещается в видеокарту")
        loaded = [m.get("identifier") or m.get("modelKey") for m in lm.loaded()]
        if key not in loaded:
            p(lm.load(key))
        hub.store.upsert_provider("lmstudio", model=key)
        print("модель для Янки:", key)
    elif cmd == "chat":
        text = " ".join(argv[1:]) or "Привет! Расскажи о себе в двух предложениях."
        try:
            r = hub.chat("lmstudio", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
                         max_tokens=400, reason="проверка из консоли")
        except ProviderError as e:
            sys.exit(f"Ошибка: {e}")
        print(r["text"].strip())
        print(f"\n[{r['provider']} · {r['model']} · {r['in']}+{r['out']} токенов · {r['seconds']} с · ${r['usd']}]")
    elif cmd == "add":
        if len(argv) < 2:
            sys.exit("python -m yanka add <id> [ключ]")
        pr = hub.store.upsert_provider(argv[1], key=argv[2] if len(argv) > 2 else None)
        print("добавлен:", pr["id"], "ключ:", "есть" if pr.get("key") else "нет")
    elif cmd == "route":
        from .router import Router, explain
        text = " ".join(argv[1:])
        d = Router(hub).route(text)
        print(explain(d))
        p(d)
    elif cmd == "budget":
        hub.store.cfg["budget"] = {"daily_usd": float(argv[1]), "monthly_usd": float(argv[2])}
        hub.store.save()
        p(hub.store.cfg["budget"])
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
