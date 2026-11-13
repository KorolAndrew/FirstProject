# CozyCode 1.3.0 - своё окно (pywebview + WebView2) и значок у часов (трей).
# Если pywebview нет или окно не открылось, cozycode.py откроет окно в Edge, как раньше.
import json
import sys
import threading
import time
from pathlib import Path

W = {"win": None, "tray": None, "quitting": False, "running": False, "geom": {}, "hint_shown": False}
IS_WINDOWS = sys.platform == "win32"


def available():
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def running():
    return W["running"] and W["win"] is not None


def _ui(fn):
    """Выполнить fn в потоке окна (WinForms требует этого для трея и иконки)."""
    form = getattr(W["win"], "native", None)
    if form is None:
        return
    from System import Action
    if form.InvokeRequired:
        form.Invoke(Action(fn))
    else:
        fn()


# ---------- место и размер окна ----------

def _virtual_screen():
    try:
        import ctypes
        m = ctypes.windll.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)  # x, y, ширина, высота всех мониторов
    except Exception:
        return None


def _load_geom(path):
    try:
        g = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {"width": max(900, int(g.get("width", 1320))), "height": max(600, int(g.get("height", 860))),
           "maximized": bool(g.get("maximized"))}
    x, y, vs = g.get("x"), g.get("y"), _virtual_screen()
    if isinstance(x, int) and isinstance(y, int) and vs:
        vx, vy, vw, vh = vs
        # окно должно остаться видимым хотя бы краешком (монитор могли отключить)
        if vx - 200 <= x <= vx + vw - 200 and vy <= y <= vy + vh - 100:
            out["x"], out["y"] = x, y
    return out


def _save_geom(path):
    try:
        w = W["win"]
        g = dict(W["geom"])
        if w is not None and not g.get("maximized"):
            g.update(x=int(w.x), y=int(w.y), width=int(w.width), height=int(w.height))
        Path(path).write_text(json.dumps(g), encoding="utf-8")
    except Exception:
        pass


# ---------- трей ----------

def _make_tray(icon_path, on_show, on_quit, log):
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Windows.Forms import NotifyIcon, ContextMenuStrip, ToolStripMenuItem, MouseButtons
    from System.Drawing import Icon, SystemIcons

    def bg(fn):
        return lambda *a: threading.Thread(target=fn, daemon=True).start()

    def build():
        form = W["win"].native
        ico = None
        if icon_path and Path(icon_path).exists():
            try:
                ico = Icon(str(icon_path))
                form.Icon = ico  # значок окна и панели задач
            except Exception as e:
                log(f"Окно: иконка не загрузилась ({e})")
        ni = NotifyIcon()
        ni.Icon = ico or SystemIcons.Application
        ni.Text = "CozyCode - Клодик и Яночка"
        menu = ContextMenuStrip()
        item_open = ToolStripMenuItem("Открыть CozyCode")
        item_open.Click += bg(on_show)
        item_quit = ToolStripMenuItem("Выключить CozyCode")
        item_quit.Click += bg(on_quit)
        menu.Items.Add(item_open)
        menu.Items.Add(item_quit)
        ni.ContextMenuStrip = menu

        def click(sender, e):
            if e.Button == MouseButtons.Left:
                threading.Thread(target=on_show, daemon=True).start()
        ni.MouseClick += click
        ni.Visible = True
        W["tray"] = ni

    _ui(build)


def balloon(text, title="Яночка"):
    ni = W["tray"]
    if ni is None:
        return False
    try:
        from System.Windows.Forms import ToolTipIcon
        _ui(lambda: ni.ShowBalloonTip(5000, title, str(text)[:200], ToolTipIcon.Info))
        return True
    except Exception:
        return False


def _drop_tray():
    ni = W["tray"]
    if ni is None:
        return
    try:
        def gone():
            ni.Visible = False
            ni.Dispose()
        _ui(gone)
    except Exception:
        pass
    W["tray"] = None


# ---------- показать / спрятать ----------

def show():
    """Показать окно и вывести его наверх. True - если своё окно работает."""
    w = W["win"]
    if not running():
        return False
    try:
        w.show()
        if not W["geom"].get("maximized"):
            w.restore()

        def front():
            f = w.native
            f.TopMost = True
            f.Activate()
            f.TopMost = False
        _ui(front)
    except Exception:
        pass
    return True


class Api:
    """То, что окно может попросить у Python (window.pywebview.api.*)."""

    def notify(self, text):
        return balloon(text)

    def native(self):
        return True


# ---------- запуск ----------

def run(url, data_dir, icon_path, quit_event, log):
    """Открывает своё окно и держит его до выхода. Возвращает True, если окно работало,
    False - если открыть не получилось (тогда нужен запасной вариант через Edge)."""
    import webview
    data_dir = Path(data_dir)
    geom_file = data_dir / "window_native.json"
    g = _load_geom(geom_file)
    W["geom"] = {"maximized": g.get("maximized", False)}

    if IS_WINDOWS:
        try:
            import ctypes
            # свой значок на панели задач, а не значок Python
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Yana.CozyCode")
        except Exception:
            pass

    w = webview.create_window(
        "CozyCode", url, width=g.get("width", 1320), height=g.get("height", 860),
        x=g.get("x"), y=g.get("y"), min_size=(900, 600), background_color="#17151b",
        text_select=True, js_api=Api(), maximized=g.get("maximized", False))
    W["win"] = w

    def do_quit():
        quit_event.set()

    def on_shown():
        W["running"] = True
        try:
            _make_tray(icon_path, show, do_quit, log)
        except Exception as e:
            log(f"Окно: трей не получился ({e}) - закрытие окна будет сворачивать его")

    def on_closing():
        if W["quitting"]:
            return True
        _save_geom(geom_file)
        if W["tray"] is not None:
            w.hide()
            if not W["hint_shown"]:
                W["hint_shown"] = True
                balloon("Я тут, у часов 🐑 Клодик работает дальше. Выключить - правой кнопкой по значку.")
        else:
            w.minimize()
        return False  # окно не закрываем: CozyCode продолжает работать

    def on_maximized():
        W["geom"]["maximized"] = True

    def on_restored():
        W["geom"]["maximized"] = False

    w.events.shown += on_shown
    w.events.closing += on_closing
    w.events.maximized += on_maximized
    w.events.restored += on_restored

    def watch_quit():
        quit_event.wait()
        W["quitting"] = True
        _save_geom(geom_file)
        _drop_tray()
        try:
            w.destroy()
        except Exception:
            pass

    threading.Thread(target=watch_quit, daemon=True).start()
    storage = data_dir / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        webview.start(private_mode=False, storage_path=str(storage))
    except Exception as e:
        log(f"Окно: pywebview не запустился ({type(e).__name__}: {e})")
    finally:
        was = W["running"]
        W["running"] = False
        W["win"] = None
    if not was and not quit_event.is_set():
        log(f"Окно: своё окно не открылось за {time.time() - started:.0f} с - открываю в Edge")
        return False
    return True
