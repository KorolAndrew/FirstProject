# Проверка 1.3.0: своё окно + трей + «закрыть = спрятать» + выход. Само закроется примерно через 12 с.
import sys, threading, time, tempfile, py_compile
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

APP = Path(__file__).resolve().parent.parent / "CozyCode_обновление"
for f in ["bridge.py", "cozycode.py", "programs.py", "window.py", "update.py"]:
    py_compile.compile(str(APP / f), doraise=True)
print("py_compile OK", flush=True)
sys.path.insert(0, str(APP))
import window as nw

srv = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: SimpleHTTPRequestHandler(*a, directory=str(APP / "ui")))
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/tamagotchi.html"
data = Path(tempfile.mkdtemp())
quit_ev = threading.Event()
ico = next(iter(Path.home().glob("*Desktop*/CozyCode/ui/img/cozycode.ico")), None) or \
      next(iter(Path.home().glob("OneDrive/*/CozyCode/ui/img/cozycode.ico")), None)
print("icon:", ico, flush=True)


def script():
    for _ in range(100):
        if nw.running() and nw.W["tray"] is not None:
            break
        time.sleep(0.1)
    print("running:", nw.running(), "tray:", nw.W["tray"] is not None, flush=True)
    time.sleep(2)
    print("balloon:", nw.balloon("Проверка 1.3.0 🐑"), flush=True)
    # имитируем крестик
    nw._ui(lambda: nw.W["win"].native.Close())
    time.sleep(1.5)
    print("after close -> visible:", nw.W["win"].native.Visible if nw.W["win"] else None,
          "still running:", nw.running(), flush=True)
    print("show:", nw.show(), flush=True)
    time.sleep(1)
    print("visible after show:", nw.W["win"].native.Visible, flush=True)
    time.sleep(2)
    quit_ev.set()


threading.Thread(target=script, daemon=True).start()
t = time.time()
res = nw.run(url, data, ico, quit_ev, lambda m: print("LOG", m, flush=True))
print("run returned:", res, "in %.1f s" % (time.time() - t), "tray after:", nw.W["tray"], flush=True)
print("geom saved:", (data / "window_native.json").read_text() if (data / "window_native.json").exists() else None, flush=True)
