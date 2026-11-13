# Проверка окон CozyCode 1.2.4 - только смотрит на окна, ничего не меняет и CozyCode не запускает.
# Запуск из рабочей папки: python tmp\check_124.py
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

src = Path("CozyCode_обновление", "cozycode.py").read_text(encoding="utf-8")
start, end = src.index("WINDOWS_FILE = DATA_DIR"), src.index("def open_in_chrome(")
ns = {"json": json, "subprocess": subprocess, "threading": threading, "time": time, "Path": Path,
      "DATA_DIR": Path(tempfile.mkdtemp()), "log": print}
exec(compile(src[start:end], "cozycode.py (1.2.4)", "exec"), ns)

for exe in ("chrome.exe", "msedge.exe"):
    wins = ns["_browser_windows"](exe)
    kinds = ["с вкладками" if ns["_tabbed_title"](t) else "окно-приложение" for _, t in wins]
    print(f"{exe}: окон {len(wins)} -", ", ".join(kinds) or "нет")

ns["_remember_window"]("test", 1, "nothing.exe")
print("Запись windows.json:", "OK" if ns["_saved_windows"]().get("test") else "НЕТ")
print("Закрытое окно не находится:", "OK" if ns["_live_window"]("test") == 0 else "НЕТ")
print("Проверка окон 1.2.4: OK")
