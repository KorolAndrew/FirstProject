# Live check of Yanka on the real model: chat, code, and the CozyCode answer() path with read-only tools.
import os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "CozyCode_обновление"))
from yanka.providers import Hub
from yanka.router import Router, explain
from yanka.app import YankaService

DATA = Path(__file__).resolve().parent.parent / "CozyCode_обновление" / "data"
hub = Hub(DATA)
for q in ["Привет! Кто ты? Ответь в двух предложениях.", "Напиши на Python функцию, считающую слова в строке. Только код."]:
    r = hub.chat("lmstudio", [{"role": "system", "content": "Ты Янка 🐑, помощница Яны. Отвечай по-русски."},
                              {"role": "user", "content": q}], max_tokens=400, reason="живая проверка")
    print("Q:", q, "\nA:", r["text"].strip()[:500], f"\n[{r['model']} · {r['out']} ток. · {r['seconds']} с]\n")


class FakeBridge:
    TOOLS = [{"name": "list_dir", "description": "Показать содержимое папки", "inputSchema": {"type": "object",
             "properties": {"path": {"type": "string"}}}}]
    def call_tool(self, name, args):
        return {"content": [{"type": "text", "text": "Папка: .\nPROJECT.md\nPLAN.md\nskills/\nблокнот.txt"}], "isError": False}
    def ask_user(self, t, m): return False
    def list_backups(self): return []
    def restore_backup(self, b): return ""


y = YankaService(Path(tempfile.mkdtemp()), FakeBridge(), log=print)
y.hub.store.upsert_provider("lmstudio", model="qwen/qwen3.5-9b")
d = y.router.route("Что лежит в папке проекта?")
print("route:", explain(d))
t = time.time()
text, meta = y.answer("general", ".", "Что лежит в папке проекта?", [], d, status=lambda s: print("  status:", s))
print("A:", text, "\nmeta:", meta, f"{time.time() - t:.1f} s")
print("journal:", [(j["decision"], j["brief"]) for j in y.gate.journal()])
