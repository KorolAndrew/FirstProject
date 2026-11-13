"""Единый интерфейс к языковым моделям: локальные (LM Studio, Ollama, llama.cpp) и облачные
(OpenAI, DeepSeek, OpenRouter и другие OpenAI-совместимые, Anthropic).

Hub.chat(...) сам проверяет: провайдер включён, ключ есть, лимит расходов не превышен,
приватные данные не уходят в облако без разрешения. Каждый вызов пишется в журнал расходов.
Только стандартная библиотека - чтобы работало на Python 3.15 без новых пакетов.
"""
import json
import socket
import time
import urllib.error
import urllib.request

from .store import Store


class ProviderError(Exception):
    """kind: disabled | no_key | budget | private | unavailable | auth | rate_limit | bad_request | timeout | server"""

    RU = {
        "disabled": "провайдер выключен", "no_key": "нет API-ключа", "budget": "лимит расходов исчерпан",
        "private": "приватные данные нельзя отправлять в облако без разрешения",
        "unavailable": "сервер модели не отвечает", "auth": "ключ не подошёл", "rate_limit": "слишком много запросов",
        "bad_request": "модель не поняла запрос", "timeout": "модель отвечает слишком долго", "server": "ошибка сервера модели",
    }

    def __init__(self, kind, message="", provider="", retry=False):
        self.kind, self.provider, self.retry = kind, provider, retry
        super().__init__(f"{provider}: {self.RU.get(kind, kind)}" + (f" ({message})" if message else ""))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _http(method, url, headers=None, body=None, timeout=120, provider=""):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        kind = {400: "bad_request", 401: "auth", 403: "auth", 404: "bad_request", 422: "bad_request",
                429: "rate_limit"}.get(e.code, "server" if e.code >= 500 else "bad_request")
        raise ProviderError(kind, f"HTTP {e.code} {detail}", provider, retry=kind in ("rate_limit", "server"))
    except (socket.timeout, TimeoutError):
        raise ProviderError("timeout", "", provider, retry=True)
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            raise ProviderError("timeout", "", provider, retry=True)
        raise ProviderError("unavailable", str(e.reason), provider, retry=False)
    except (ConnectionError, OSError) as e:
        raise ProviderError("unavailable", str(e), provider)
    except ValueError as e:
        raise ProviderError("server", f"ответ не JSON: {e}", provider)


# --------------------------------------------------------------------------- #
# Адаптеры
# --------------------------------------------------------------------------- #

class OpenAIAdapter:
    """OpenAI Chat Completions и всё совместимое: LM Studio, Ollama, llama.cpp, DeepSeek, OpenRouter…"""

    def __init__(self, cfg, key):
        self.cfg, self.key = cfg, key
        self.base = cfg["base_url"].rstrip("/")

    def _h(self):
        return {"Authorization": "Bearer " + self.key} if self.key else {}

    def models(self, timeout=5):
        r = _http("GET", self.base + "/models", self._h(), timeout=timeout, provider=self.cfg["id"])
        return [m.get("id") for m in r.get("data", []) if m.get("id")]

    def chat(self, messages, model, max_tokens=1024, temperature=0.7, tools=None, timeout=180, reasoning=None):
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature,
                "stream": False}
        if reasoning:
            # LM Studio: "none" выключает долгие рассуждения Qwen3.5 (иначе весь лимит уходит на <think>)
            body["reasoning_effort"] = reasoning
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        r = _http("POST", self.base + "/chat/completions", self._h(), body, timeout, self.cfg["id"])
        try:
            choice = r["choices"][0]
            msg = choice.get("message") or {}
        except (KeyError, IndexError, TypeError):
            raise ProviderError("server", "пустой ответ", self.cfg["id"])
        calls = [{"id": c.get("id"), "name": c["function"]["name"],
                  "arguments": _json_or_text(c["function"].get("arguments"))}
                 for c in (msg.get("tool_calls") or []) if c.get("function")]
        u = r.get("usage") or {}
        return {"text": msg.get("content") or "", "tool_calls": calls, "model": r.get("model") or model,
                "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
                "finish": choice.get("finish_reason")}


class AnthropicAdapter:
    def __init__(self, cfg, key):
        self.cfg, self.key = cfg, key
        self.base = cfg["base_url"].rstrip("/")

    def _h(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    def models(self, timeout=5):
        r = _http("GET", self.base + "/models", self._h(), timeout=timeout, provider=self.cfg["id"])
        return [m.get("id") for m in r.get("data", []) if m.get("id")]

    def chat(self, messages, model, max_tokens=1024, temperature=0.7, tools=None, timeout=180, reasoning=None):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        rest = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        body = {"model": model, "messages": rest, "max_tokens": max_tokens, "temperature": temperature}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""),
                              "input_schema": t.get("parameters", {"type": "object"})} for t in tools]
        r = _http("POST", self.base + "/messages", self._h(), body, timeout, self.cfg["id"])
        blocks = r.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [{"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input") or {}}
                 for b in blocks if b.get("type") == "tool_use"]
        u = r.get("usage") or {}
        return {"text": text, "tool_calls": calls, "model": r.get("model") or model,
                "in": u.get("input_tokens"), "out": u.get("output_tokens"), "finish": r.get("stop_reason")}


ADAPTERS = {"openai": OpenAIAdapter, "anthropic": AnthropicAdapter}


def _json_or_text(s):
    if isinstance(s, dict):
        return s
    try:
        return json.loads(s or "{}")
    except ValueError:
        return {"_raw": s}


def estimate_tokens(text):
    # грубо: ~3.5 символа на токен для русского/английского вперемешку
    return max(1, int(len(text) / 3.5))


# --------------------------------------------------------------------------- #
# Hub - одна точка входа для всех моделей
# --------------------------------------------------------------------------- #

class Hub:
    def __init__(self, data_dir):
        self.store = Store(data_dir)
        self._health = {}  # pid -> {"ok": bool, "t": time, "error": str, "models": [...]}

    def adapter(self, pid):
        cfg = self.store.provider(pid)
        if not cfg:
            raise ProviderError("bad_request", "нет такого провайдера", pid)
        return cfg, ADAPTERS[cfg.get("kind", "openai")](cfg, self.store.key(pid))

    def check(self, pid, timeout=4):
        """Проверка доступности: отвечает ли сервер и какие модели есть."""
        cfg = self.store.provider(pid)
        h = {"ok": False, "t": time.time(), "error": "", "models": []}
        try:
            if not cfg:
                raise ProviderError("bad_request", "нет такого провайдера", pid)
            if not cfg.get("local") and not cfg.get("key"):
                raise ProviderError("no_key", "", pid)
            h["models"] = self.adapter(pid)[1].models(timeout)
            h["ok"] = True
        except ProviderError as e:
            # Windows на закрытом локальном порту часто отвечает таймаутом, а не отказом
            kind = "unavailable" if e.kind == "timeout" and cfg and cfg.get("local") else e.kind
            h["error"] = str(ProviderError(kind, "", pid)) if kind != e.kind else str(e)
            h["kind"] = kind
        self._health[pid] = h
        return h

    def status(self):
        out = []
        for p in self.store.public()["providers"]:
            out.append({**p, "health": self._health.get(p["id"])})
        return {"providers": out, "spent": self.store.spent(), "budget": self.store.cfg["budget"],
                "budget_left": self.store.budget_left()}

    def _guard(self, cfg, messages, max_tokens, private):
        pid = cfg["id"]
        if not cfg.get("enabled", True):
            raise ProviderError("disabled", "", pid)
        if cfg.get("local"):
            return 0.0
        if not cfg.get("key"):
            raise ProviderError("no_key", "", pid)
        if private and not self.store.cfg.get("cloud_private_ok"):
            raise ProviderError("private", "", pid)
        est_in = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        est = (est_in * cfg.get("price_in", 0) + max_tokens * cfg.get("price_out", 0)) / 1e6
        left = self.store.budget_left()
        if est > 0 and (left["day"] < est or left["month"] < est):
            raise ProviderError("budget", f"нужно ~${est:.4f}, осталось сегодня ${max(0, left['day']):.4f}, "
                                          f"в месяце ${max(0, left['month']):.4f}", pid)
        return est

    def chat(self, pid, messages, model=None, max_tokens=1024, temperature=0.7, tools=None,
             private=False, reason="", retries=2, timeout=180, reasoning=None):
        """private=True - в сообщениях есть файлы/переписка Яны: в облако только с разрешения."""
        cfg = self.store.provider(pid)
        if not cfg:
            raise ProviderError("bad_request", "нет такого провайдера", pid)
        self._guard(cfg, messages, max_tokens, private)
        model = model or cfg.get("model") or ""
        _, ad = self.adapter(pid)
        if not model:
            models = ad.models()
            if not models:
                raise ProviderError("bad_request", "не выбрана модель и сервер не вернул список", pid)
            model = models[0]
        # на ПК по умолчанию без рассуждений (быстро); облако - как настроено у провайдера
        effort = reasoning or cfg.get("reasoning_effort") or ("none" if cfg.get("local") else None)
        t0 = time.time()
        for attempt in range(retries + 1):
            try:
                r = ad.chat(messages, model, max_tokens, temperature, tools, timeout, effort)
                break
            except ProviderError as e:
                if not e.retry or attempt == retries:
                    self.store.record(pid, model, 0, 0, 0, ok=False, error=str(e), reason=reason)
                    raise
                time.sleep(1.5 * (attempt + 1))
        t_in = r["in"] if r["in"] is not None else sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        t_out = r["out"] if r["out"] is not None else estimate_tokens(r["text"])
        cost = 0.0 if cfg.get("local") else (t_in * cfg.get("price_in", 0) + t_out * cfg.get("price_out", 0)) / 1e6
        self.store.record(pid, r["model"], t_in, t_out, cost, reason=reason)
        r.update(provider=pid, local=bool(cfg.get("local")), usd=round(cost, 6), seconds=round(time.time() - t0, 2),
                 **{"in": t_in, "out": t_out})
        return r
