# Check 1.3.0 next: tests + real screen capture (yanka.vision) -> local model sees it. Screenshot is not saved.
import base64, os, subprocess, sys, time
from pathlib import Path
APP = Path(__file__).resolve().parent.parent / "CozyCode_обновление"
sys.path.insert(0, str(APP))
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=APP, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
print("TESTS:", [l for l in r.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))])
from yanka import vision, local, voice
from yanka.providers import Hub
t = time.time()
png = vision.capture_screen()
print("capture:", len(png), "bytes PNG,", round(time.time() - t, 2), "s")
lm = local.LMStudio()
lm.start_server()
if "qwen/qwen3.5-9b" not in {m.get("identifier") or m.get("modelKey") for m in lm.loaded()}:
    lm.load("qwen/qwen3.5-9b")
import tempfile
h = Hub(tempfile.mkdtemp())
h.store.upsert_provider("lmstudio", model="qwen/qwen3.5-9b")
url = "data:image/png;base64," + base64.b64encode(png).decode()
res = h.chat("lmstudio", [{"role": "user", "content": [{"type": "text", "text": "Что на экране? Одно предложение по-русски."},
                                                        {"type": "image_url", "image_url": {"url": url}}]}],
             max_tokens=150, private=True, timeout=300)
print("vision:", res["seconds"], "s |", res["text"].strip()[:300])
print("voice:", voice.status())
