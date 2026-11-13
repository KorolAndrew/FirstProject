"""Настройки Янки, ключи (зашифрованы средствами Windows), учёт токенов, стоимости и лимиты расходов."""
import base64
import datetime
import json
import os
import sys
import threading
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
_LOCK = threading.RLock()

# Готовые шаблоны провайдеров. Цены - USD за 1 млн токенов (вход, выход); Яна может поправить в настройках.
TEMPLATES = {
    "lmstudio": {"name": "LM Studio (на ПК)", "kind": "openai", "base_url": "http://127.0.0.1:1234/v1",
                 "local": True, "price_in": 0.0, "price_out": 0.0},
    "ollama": {"name": "Ollama (на ПК)", "kind": "openai", "base_url": "http://127.0.0.1:11434/v1",
               "local": True, "price_in": 0.0, "price_out": 0.0},
    "llamacpp": {"name": "llama.cpp server (на ПК)", "kind": "openai", "base_url": "http://127.0.0.1:8080/v1",
                 "local": True, "price_in": 0.0, "price_out": 0.0},
    "deepseek": {"name": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1",
                 "local": False, "price_in": 0.3, "price_out": 1.2},
    "openai": {"name": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1",
               "local": False, "price_in": 2.5, "price_out": 10.0},
    "anthropic": {"name": "Anthropic", "kind": "anthropic", "base_url": "https://api.anthropic.com/v1",
                  "local": False, "price_in": 3.0, "price_out": 15.0},
    # бесплатный уровень Gemini API (ключ из AI Studio): free=True - лимит расходов не нужен, но
    # запросы бесплатного уровня Google может использовать для улучшения сервисов - личное туда не шлём
    "google": {"name": "Google Gemini", "kind": "gemini", "model": "gemini-3.5-flash", "base_url": "https://generativelanguage.googleapis.com/v1beta",
               "local": False, "free": True, "price_in": 0.0, "price_out": 0.0},
    "openrouter": {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1",
                   "local": False, "price_in": 1.0, "price_out": 4.0},
}

DEFAULT = {
    "providers": [
        {"id": "lmstudio", **TEMPLATES["lmstudio"], "enabled": True, "model": "", "key": ""},
    ],
    # лимиты только для платных провайдеров; 0 = платные запрещены
    "budget": {"daily_usd": 0.0, "monthly_usd": 0.0},
    # можно ли отправлять в облако содержимое файлов/переписку без отдельного вопроса
    "cloud_private_ok": False,
}


# ---------- шифрование ключей (Windows DPAPI: расшифровать может только этот пользователь на этом ПК) ----------

def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = BLOB(len(data), buf), BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):
        raise OSError("DPAPI: не получилось " + ("зашифровать" if protect else "расшифровать") + " ключ")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def seal(secret):
    if not secret:
        return ""
    raw = secret.encode("utf-8")
    if IS_WINDOWS:
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")  # не Windows - только для тестов


def unseal(token):
    if not token:
        return ""
    kind, _, b = token.partition(":")
    raw = base64.b64decode(b)
    if kind == "dpapi":
        return _dpapi(raw, False).decode("utf-8")
    if kind == "plain":
        return raw.decode("utf-8")
    raise ValueError("неизвестный формат ключа")


def mask(secret):
    return (secret[:3] + "…" + secret[-4:]) if len(secret) > 10 else ("•••" if secret else "")


# ---------- хранилище ----------

class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg_file = self.dir / "yanka.json"
        self.usage_file = self.dir / "yanka_usage.jsonl"
        self.cfg = self._load()

    def _load(self):
        try:
            cfg = json.loads(self.cfg_file.read_text(encoding="utf-8"))
        except Exception:
            cfg = json.loads(json.dumps(DEFAULT))
        for k, v in DEFAULT.items():
            cfg.setdefault(k, json.loads(json.dumps(v)))
        return cfg

    def save(self):
        with _LOCK:
            tmp = self.cfg_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.cfg_file)

    # --- провайдеры ---
    def providers(self):
        return list(self.cfg["providers"])

    def provider(self, pid):
        for p in self.cfg["providers"]:
            if p["id"] == pid:
                return p
        return None

    def upsert_provider(self, pid, template=None, key=None, **fields):
        with _LOCK:
            p = self.provider(pid)
            if p is None:
                base = TEMPLATES.get(template or pid)
                if not base and not fields.get("base_url"):
                    raise ValueError(f"Неизвестный провайдер {pid}: нужен base_url")
                p = {"id": pid, **(base or {"name": pid, "kind": "openai", "local": False, "price_in": 0.0,
                                            "price_out": 0.0}), "enabled": True, "key": ""}
                p.setdefault("model", "")
                self.cfg["providers"].append(p)
            for k, v in fields.items():
                if k in ("name", "kind", "base_url", "local", "free", "price_in", "price_out", "enabled", "model"):
                    p[k] = v
            if key is not None:
                p["key"] = seal(key.strip())
            self.save()
            return p

    def remove_provider(self, pid):
        with _LOCK:
            self.cfg["providers"] = [p for p in self.cfg["providers"] if p["id"] != pid]
            self.save()

    def key(self, pid):
        p = self.provider(pid)
        return unseal(p.get("key", "")) if p else ""

    def public(self):
        """Настройки без ключей - для окна и журнала."""
        out = json.loads(json.dumps(self.cfg))
        for p in out["providers"]:
            k = p.pop("key", "")
            p["has_key"] = bool(k)
            try:
                p["key_hint"] = mask(unseal(k)) if k else ""
            except Exception:
                p["key_hint"] = "⚠️ не расшифровался"
        return out

    # --- учёт расходов ---
    def record(self, provider, model, tokens_in, tokens_out, cost, ok=True, error="", reason=""):
        row = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "provider": provider, "model": model,
               "in": int(tokens_in or 0), "out": int(tokens_out or 0), "usd": round(float(cost or 0), 6),
               "ok": ok, "error": error[:200], "reason": reason[:200]}
        with _LOCK, open(self.usage_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    def usage(self, since=None):
        rows = []
        try:
            with open(self.usage_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if since is None or r["t"] >= since:
                        rows.append(r)
        except FileNotFoundError:
            pass
        return rows

    def spent(self):
        now = datetime.datetime.now()
        day = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        rows = self.usage(month)
        return {"day": round(sum(r["usd"] for r in rows if r["t"].startswith(day)), 6),
                "month": round(sum(r["usd"] for r in rows), 6),
                "tokens_month": sum(r["in"] + r["out"] for r in rows),
                "calls_month": len(rows)}

    def budget_left(self):
        b, s = self.cfg["budget"], self.spent()
        return {"day": round(float(b.get("daily_usd", 0)) - s["day"], 6),
                "month": round(float(b.get("monthly_usd", 0)) - s["month"], 6)}
