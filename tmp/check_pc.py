# Только читает: кодирует новые референсы в base64 (для Клодика) и показывает железо ПК.
import base64, glob, json, os, pathlib, platform, shutil, subprocess

out = pathlib.Path("tmp/refs")
out.mkdir(parents=True, exist_ok=True)
for n in sorted(glob.glob("files/photo_*_2026-11-13_16-44-38.jpg")):
    b = base64.encodebytes(pathlib.Path(n).read_bytes()).decode("ascii")
    (out / (pathlib.Path(n).name + ".b64")).write_text(b, encoding="ascii")
print("refs:", len(list(out.glob("*.b64"))))

ps = ("$c=Get-CimInstance Win32_Processor|Select -First 1 Name,NumberOfCores,NumberOfLogicalProcessors;"
      "$m=[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1);"
      "$g=Get-CimInstance Win32_VideoController|Select Name,AdapterRAM,DriverVersion;"
      "$d=Get-PSDrive -PSProvider FileSystem|Select Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}},@{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}};"
      "[pscustomobject]@{cpu=$c;ram_gb=$m;gpu=$g;disks=$d}|ConvertTo-Json -Depth 4")
r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
print(r.stdout.strip() or r.stderr.strip())
if shutil.which("nvidia-smi"):
    r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                       capture_output=True, text=True, timeout=30)
    print("nvidia-smi:", r.stdout.strip())
else:
    print("nvidia-smi: нет")
print("ollama:", shutil.which("ollama") or "нет")
print("lmstudio:", any(pathlib.Path(p).exists() for p in [os.path.expandvars(r"%LOCALAPPDATA%\Programs\LM Studio"), os.path.expanduser(r"~\.lmstudio")]))
print("windows:", platform.platform())
