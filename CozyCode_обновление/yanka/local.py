"""Локальные модели: LM Studio (основной путь - уже стоит у Яны), Ollama и llama.cpp (если появятся).

Правила: ничего не скачиваем без согласия Яны (download() требует consent=True),
модель грузим, только если она помещается в память (fit()), иначе объясняем почему.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
GB = 1024 ** 3


def _run(args, timeout=60):
    kw = {"creationflags": 0x08000000} if IS_WINDOWS else {}
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)
    return r.returncode, _strip_ansi(r.stdout or ""), _strip_ansi(r.stderr or "")


def _strip_ansi(s):
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


# --------------------------------------------------------------------------- #
# Ресурсы ПК
# --------------------------------------------------------------------------- #

def resources():
    """Видеокарта (NVIDIA), память и свободное место. Ничего не меняет."""
    out = {"gpu": None, "vram_gb": 0.0, "vram_free_gb": 0.0, "ram_gb": 0.0, "ram_free_gb": 0.0}
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            code, s, _ = _run([smi, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"], 15)
            if code == 0 and s.strip():
                name, total, free = [x.strip() for x in s.strip().splitlines()[0].split(",")]
                out.update(gpu=name, vram_gb=round(int(total) / 1024, 2), vram_free_gb=round(int(free) / 1024, 2))
        except Exception:
            pass
    if IS_WINDOWS:
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ax", ctypes.c_ulonglong)]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            out.update(ram_gb=round(m.total / GB, 1), ram_free_gb=round(m.avail / GB, 1))
        except Exception:
            pass
    else:
        try:
            info = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
            out.update(ram_gb=round(int(info["MemTotal"].split()[0]) / 1024 ** 2, 1),
                       ram_free_gb=round(int(info["MemAvailable"].split()[0]) / 1024 ** 2, 1))
        except Exception:
            pass
    return out


# --------------------------------------------------------------------------- #
# Поместится ли модель
# --------------------------------------------------------------------------- #

def _params_b(model):
    s = str(model.get("paramsString") or "")
    m = re.match(r"([\d.]+)\s*([BM])", s, re.I)
    if m:
        return float(m.group(1)) / (1000 if m.group(2).upper() == "M" else 1)
    m = re.search(r"(\d+(?:\.\d+)?)[bB](?![a-z])", str(model.get("modelKey", "")) + " " + str(model.get("path", "")))
    return float(m.group(1)) if m else None


def fit(model, res, context=8192):
    """Оценка: размер файла + KV-кэш для контекста + ~0.6 ГБ на рабочие буферы.
    Возвращает where: gpu (всё в видеокарте - быстро) | split (часть на процессоре - медленнее) | no."""
    size = (model.get("sizeBytes") or 0) / GB
    p = _params_b(model) or max(1.0, size * 2)
    # KV-кэш fp16 для типичной архитектуры с GQA: ~0.11 ГБ на 1B параметров на 8K контекста
    kv = 0.11 * p * (context / 8192)
    need = size + kv + 0.6
    vram, ram = res.get("vram_gb") or 0, res.get("ram_gb") or 0
    bits = ((model.get("quantization") or {}).get("bits")) or None
    if vram and need <= vram * 0.92:
        where = "gpu"
    elif need <= vram * 0.92 + max(0, ram - 6) * 0.8:
        where = "split"
    else:
        where = "no"
    notes = []
    if bits is not None and bits <= 2:
        notes.append(f"сжата до {bits} бит - заметно глупее оригинала, лучше Q4 и выше")
    if where == "split":
        notes.append("часть слоёв пойдёт на процессор - ответы будут медленнее")
    if where == "no":
        notes.append("не поместится в память ПК")
    return {"need_gb": round(need, 2), "where": where, "notes": notes, "params_b": p, "context": context}


# --------------------------------------------------------------------------- #
# LM Studio
# --------------------------------------------------------------------------- #

class LMStudio:
    PORT = 1234

    def __init__(self):
        self.exe = shutil.which("lms") or next(
            (str(p) for p in [Path.home() / ".lmstudio" / "bin" / ("lms.exe" if IS_WINDOWS else "lms")] if p.exists()),
            None)

    @property
    def installed(self):
        return bool(self.exe)

    def _json(self, *args):
        code, out, err = _run([self.exe, *args], 60)
        if code != 0:
            raise RuntimeError((err or out).strip()[:300] or f"lms {' '.join(args)}: код {code}")
        try:
            return json.loads(out)
        except ValueError:
            m = re.search(r"(\[.*\]|\{.*\})", out, re.S)
            return json.loads(m.group(1)) if m else []

    def server_running(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.PORT}/v1/models", timeout=2):
                return True
        except Exception:
            return False

    def downloaded(self):
        return [m for m in self._json("ls", "--json") if m.get("type") == "llm"]

    def loaded(self):
        return self._json("ps", "--json")

    def start_server(self):
        if self.server_running():
            return "уже работает"
        code, out, err = _run([self.exe, "server", "start", "--port", str(self.PORT)], 90)
        if code != 0 or not self.server_running():
            raise RuntimeError("сервер LM Studio не запустился: " + (err or out).strip()[:300])
        return "запущен"

    def load(self, model_key, context=8192, res=None, force=False, idle_minutes=30):
        """Загрузить скачанную модель в память. Без force не грузит то, что не помещается."""
        models = {m["modelKey"]: m for m in self.downloaded()}
        if model_key not in models:
            raise RuntimeError(f"модель {model_key} не скачана")
        f = fit(models[model_key], res or resources(), context)
        if f["where"] == "no" and not force:
            raise RuntimeError(f"{model_key} не поместится: нужно ~{f['need_gb']} ГБ")
        # --gpu принимает только off|max|0..1; без флага LM Studio сам делит модель между видеокартой и процессором
        gpu = ["--gpu=max"] if f["where"] == "gpu" else []
        code, out, err = _run([self.exe, "load", model_key, *gpu, f"--context-length={context}",
                               f"--identifier={model_key}", f"--ttl={int(idle_minutes * 60)}", "-y"], 600)
        if code != 0:
            raise RuntimeError("модель не загрузилась: " + (err or out).strip()[-400:])
        return {"model": model_key, **f}

    def unload(self, model_key=None):
        code, out, err = _run([self.exe, "unload"] + ([model_key] if model_key else ["--all"]), 60)
        if code != 0:
            raise RuntimeError((err or out).strip()[:300])
        return True

    def download(self, query, consent=False):
        """Скачивание - только с явного согласия Яны (большие файлы, место на диске)."""
        if not consent:
            raise PermissionError("Скачивать модель можно только после согласия Яны")
        code, out, err = _run([self.exe, "get", query, "-y"], 7200)
        if code != 0:
            raise RuntimeError("не скачалась: " + (err or out).strip()[-400:])
        return out.strip()[-400:]


def other_runtimes():
    found = {}
    for name, port in (("ollama", 11434), ("llama-server", 8080)):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=1.5):
                running = True
        except Exception:
            running = False
        found[name] = {"installed": bool(shutil.which(name)), "running": running, "port": port}
    return found


# --------------------------------------------------------------------------- #
# Рекомендации
# --------------------------------------------------------------------------- #

# Что искать под 8 ГБ видеопамяти. Это ориентиры для выбора, а не список на автоскачивание.
SUGGEST = [
    {"role": "болтовня и разбор намерений (быстро)", "size": "3–5B, Q4_K_M–Q6_K", "need_gb": "3–5",
     "hint": "компактные модели с хорошим русским: Qwen 3.x 4B, RusMind, Vikhr"},
    {"role": "основной ассистент", "size": "7–9B, Q4_K_M", "need_gb": "5.5–7",
     "hint": "Qwen 3.x 8–9B instruct (поддерживает вызов инструментов)"},
    {"role": "код (запасной, если Клодик недоступен)", "size": "7B, Q4–Q6",
     "need_gb": "5–7", "hint": "Qwen2.5-Coder-7B — уже скачан"},
]


def report(context=8192):
    """Сводка для Яны: ресурсы, что скачано, что поместится, что загружено, что советуем."""
    res = resources()
    lm = LMStudio()
    out = {"resources": res, "lmstudio": {"installed": lm.installed}, "others": other_runtimes(), "suggest": SUGGEST}
    if lm.installed:
        try:
            out["lmstudio"].update(server=lm.server_running(), loaded=[m.get("modelKey") or m.get("identifier")
                                                                        for m in lm.loaded()])
            out["lmstudio"]["models"] = [{"key": m["modelKey"], "name": m.get("displayName"),
                                          "size_gb": round(m.get("sizeBytes", 0) / GB, 2),
                                          "quant": (m.get("quantization") or {}).get("name"),
                                          "tools": m.get("trainedForToolUse", False),
                                          "fit": fit(m, res, context)} for m in lm.downloaded()]
        except Exception as e:
            out["lmstudio"]["error"] = str(e)
    return out


def best_downloaded(rep, prefer_tools=True):
    """Лучшая из скачанных для Янки: не 1–2 бита, помещается (лучше целиком в видеокарту),
    умеет инструменты, побольше. Моя оценка памяти грубая, поэтому «split» тоже подходит."""
    ok = [m for m in rep.get("lmstudio", {}).get("models", []) if m["fit"]["where"] in ("gpu", "split")]
    good = [m for m in ok if not any("бит" in n for n in m["fit"]["notes"])] or ok
    if not good:
        return None
    good.sort(key=lambda m: ((m["tools"] if prefer_tools else 0), m["fit"]["where"] == "gpu", m["fit"]["params_b"]),
              reverse=True)
    return good[0]["key"]
