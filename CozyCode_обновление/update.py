# CozyCode updater 1.2.0 - копирует новые файлы в папку CozyCode
import os, sys, shutil, socket, time, ctypes, subprocess
from pathlib import Path

VERSION = "1.3.0"
SRC = Path(__file__).resolve().parent
FILES = ["bridge.py", "cozycode.py", "programs.py", "window.py", "README.txt", "ui/index.html", "ui/app.js", "ui/app.css",
         "ui/yanochka.js", "ui/tamagotchi.html",
         "yanka/__init__.py", "yanka/__main__.py", "yanka/store.py", "yanka/providers.py", "yanka/local.py", "yanka/router.py", "yanka/memory.py", "yanka/tasks.py", "yanka/permissions.py", "yanka/app.py",
         "yanka/vision.py", "yanka/voice.py", "yanka/pulse.py", "yanka/team.py",
         "tests/test_yanka.py", "tests/test_router.py", "tests/test_memory.py",
         "tests/test_tasks.py", "tests/test_permissions.py", "tests/test_next.py", "tests/test_team.py"]


def desktop():
    try:
        buf = ctypes.create_unicode_buffer(520)
        ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)
        if buf.value:
            return Path(buf.value)
    except Exception:
        pass
    return Path.home() / "Desktop"


def find_app():
    h = Path.home()
    for d in [desktop(), h / "Desktop", h / "OneDrive" / "Desktop", h / "OneDrive" / "Рабочий стол"]:
        if (d / "CozyCode" / "cozycode.py").exists():
            return d / "CozyCode"
    while True:
        t = input("  Не нашла папку CozyCode. Перетащи её сюда и нажми Enter: ").strip().strip('"')
        if t and (Path(t) / "cozycode.py").exists():
            return Path(t)
        print("  В этой папке нет cozycode.py, попробуй ещё раз.")


def running():
    s = socket.socket()
    s.settimeout(0.5)
    try:
        s.connect(("127.0.0.1", 8766))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main():
    print()
    print("  CozyCode - обновление до " + VERSION)
    print()
    missing = [f for f in FILES if not (SRC / f).exists()]
    if missing:
        print("  Не хватает файлов обновления: " + ", ".join(missing))
        return
    app = find_app()
    print("  Папка CozyCode: " + str(app))
    while running():
        print("  CozyCode ещё включён. Выключи его кнопкой выключения в приложении и нажми Enter.")
        input()
        time.sleep(1)
    old = app / "backups" / ("до-" + VERSION)
    for f in FILES:
        src, dst = SRC / f, app / f
        if dst.exists():
            (old / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, old / f)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        print("  обновлён: " + f)
    keys = SRC / "data" / "import_keys.json"  # 1.3.0: одноразовая передача ключа - CozyCode зашифрует и удалит
    if keys.exists():
        (app / "data").mkdir(parents=True, exist_ok=True)
        shutil.move(str(keys), str(app / "data" / "import_keys.json"))
        print("  ключ провайдера передан Янке (будет зашифрован при запуске)")
    print()
    print("  Готово! Старые файлы сохранены в " + str(old))
    print("  Запускаю CozyCode...")
    try:
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw if pyw.exists() else Path(sys.executable))
        subprocess.Popen([exe, str(app / "cozycode.py")], cwd=str(app), creationflags=0x00000008)
    except Exception as e:
        print("  Не получилось запустить сам (" + repr(e) + "). Запусти CozyCode как обычно.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print()
        print("  Ошибка: " + repr(e))
        print("  Сфотографируй это окно и пришли Клодику.")
