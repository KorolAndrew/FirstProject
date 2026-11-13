# Task 2 check on the real PC: all tests + a small live team run on the local model (qwen/qwen3.5-9b).
import subprocess, sys, tempfile, time
from pathlib import Path
APP = Path(__file__).resolve().parent.parent / "CozyCode_обновление"
sys.path.insert(0, str(APP))
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=APP, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
print("TESTS:", [l for l in r.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED", "ERROR:", "FAIL:"))], flush=True)
from yanka import local
from yanka.providers import Hub
from yanka.pulse import Pulse
from yanka.tasks import Tasks
from yanka.team import ROLES, Team
lm = local.LMStudio()
lm.start_server()
if "qwen/qwen3.5-9b" not in {m.get("identifier") or m.get("modelKey") for m in lm.loaded()}:
    lm.load("qwen/qwen3.5-9b")
d = tempfile.mkdtemp()
hub = Hub(d)
hub.store.upsert_provider("lmstudio", model="qwen/qwen3.5-9b")
team = Team(hub, Tasks(d), Pulse(d), log=print)
t0 = time.time()
t = team.plan("Придумай маленькую игру-кликер для Roblox: барашек собирает облачка. Нужна идея, устройство и план проверки.",
              "qwen/qwen3.5-9b")
print(f"PLAN {time.time() - t0:.0f}s:", [(s["n"], s["role"], s.get("deps"), s.get("review")) for s in t["steps"]], flush=True)
done = []
team.run(t["id"], "qwen/qwen3.5-9b", on_done=done.append)
while not done and time.time() - t0 < 1400:
    time.sleep(2)
t = done[0] if done else t
print("STATUS:", t["status"], f"{t.get('seconds')}s", t.get("tokens"), "tokens")
for s in t["steps"]:
    print(f"  {s['n']}. {ROLES[s['role']]['name']}: {s['status']} revised={s.get('revised', False)} | {s['result'][:120]!r}")
print("SUMMARY:", (t.get("summary") or "")[:600])
