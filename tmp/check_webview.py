# Проверка: открывается ли своё окно pywebview на этом ПК (Python 3.15). Само закроется через 5 с.
import sys, time, threading
print("python", sys.version, flush=True)
try:
    import webview
    print("pywebview", getattr(webview, "__version__", "?"), flush=True)
except Exception as e:
    print("IMPORT FAIL", repr(e), flush=True)
    sys.exit(1)

HTML = ("<body style='background:#1b1720;color:#f7c6d9;font:20px Segoe UI,sans-serif;display:grid;"
        "place-items:center;height:100vh;margin:0'><div>\U0001F411 Проверка своего окна CozyCode<br>"
        "<small>само закроется через 5 секунд</small></div></body>")
w = webview.create_window("CozyCode - проверка окна", html=HTML, width=560, height=280, background_color="#1b1720")
state = {"closing": 0}

def on_shown():
    print("shown OK", flush=True)

def on_closing():
    state["closing"] += 1
    print("closing event", state["closing"], flush=True)

w.events.shown += on_shown
w.events.closing += on_closing

def later():
    time.sleep(2.5)
    try:
        w.hide(); print("hide OK", flush=True)
        time.sleep(1)
        w.show(); print("show OK", flush=True)
    except Exception as e:
        print("hide/show FAIL", repr(e), flush=True)
    time.sleep(1.5)
    try:
        print("native:", type(getattr(w, "native", None)).__name__, flush=True)
    except Exception as e:
        print("native FAIL", repr(e), flush=True)
    w.destroy()

threading.Thread(target=later, daemon=True).start()
try:
    webview.start()
    print("closed OK, renderer:", getattr(webview, "renderer", "?"), flush=True)
except Exception as e:
    print("START FAIL", repr(e), flush=True)
