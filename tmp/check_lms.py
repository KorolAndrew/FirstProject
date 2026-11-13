# Только смотрит: состояние LM Studio (CLI lms, сервер, скачанные модели) и llama.cpp/Ollama.
import json, os, shutil, subprocess, urllib.request
from pathlib import Path

home = Path.home()
lms = shutil.which("lms") or next((str(p) for p in [home / ".lmstudio" / "bin" / "lms.exe",
                                                     home / ".cache" / "lm-studio" / "bin" / "lms.exe"] if p.exists()), None)
print("lms:", lms)
if lms:
    for args in (["version"], ["server", "status"], ["ls", "--json"], ["ps", "--json"]):
        try:
            r = subprocess.run([lms] + args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
            print("== lms", *args, "->", r.returncode)
            print((r.stdout or r.stderr)[:3000])
        except Exception as e:
            print("== lms", *args, "FAIL", e)
for port in (1234, 11434, 8080):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=2) as r:
            print(f"port {port}: OK", r.read()[:800].decode("utf-8", "replace"))
    except Exception as e:
        print(f"port {port}: нет ({type(e).__name__})")
for d in [home / ".lmstudio" / "models", home / ".cache" / "lm-studio" / "models"]:
    if d.exists():
        files = [p for p in d.rglob("*.gguf")]
        print("models dir:", d, len(files))
        for p in files[:30]:
            print("  ", p.relative_to(d), round(p.stat().st_size / 2**30, 2), "GB")
cfg = home / ".lmstudio" / "settings.json"
if cfg.exists():
    try:
        s = json.loads(cfg.read_text(encoding="utf-8"))
        print("downloadsFolder:", s.get("downloadsFolder"))
    except Exception as e:
        print("settings:", e)
print("llama-server:", shutil.which("llama-server"), "ollama:", shutil.which("ollama"))
