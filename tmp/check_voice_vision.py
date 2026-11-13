# Read-only checks for voice + vision: (1) can qwen3.5-9b in LM Studio see an image, (2) Windows voices, (3) STT package availability.
import base64, json, subprocess, sys, time, urllib.request, os, tempfile

def ps(cmd, t=60):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=t)
    return (r.stdout or r.stderr).strip()

# 1) screenshot of the primary screen -> PNG (only for this check, deleted after)
shot = os.path.join(tempfile.gettempdir(), "cozy_vision_test.png")
print(ps("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; $b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
         "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height; $g=[System.Drawing.Graphics]::FromImage($bmp); "
         "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size); "
         "$s=New-Object System.Drawing.Bitmap $bmp,([int]($b.Width/2)),([int]($b.Height/2)); "
         f"$s.Save('{shot}',[System.Drawing.Imaging.ImageFormat]::Png); \"$($b.Width)x$($b.Height)\""))
img = base64.b64encode(open(shot, "rb").read()).decode()
os.remove(shot)
body = {"model": "qwen/qwen3.5-9b", "max_tokens": 200, "reasoning_effort": "none",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Что на этом скриншоте? Ответь по-русски в 2 предложениях."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + img}}]}]}
t = time.time()
try:
    req = urllib.request.Request("http://127.0.0.1:1234/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    print("VISION OK", round(time.time() - t, 1), "s:", r["choices"][0]["message"]["content"][:300])
except Exception as e:
    print("VISION FAIL", repr(e)[:300])

# 2) voices
print("SAPI voices:", ps("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | % { $_.VoiceInfo.Name + ' [' + $_.VoiceInfo.Culture + ']' }"))
print("OneCore voices:", ps("Get-ChildItem 'HKLM:\\SOFTWARE\\Microsoft\\Speech_OneCore\\Voices\\Tokens' | % { $_.PSChildName }"))

# 3) STT packages: what pip would install on this Python (dry run, nothing installed)
for pkg in ("vosk", "faster-whisper", "sounddevice"):
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--dry-run", "--quiet", "--report", "-", pkg],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    try:
        rep = json.loads(r.stdout)
        print(pkg, "->", ", ".join(f"{i['metadata']['name']} {i['metadata']['version']}" for i in rep["install"]) or "already installed")
    except Exception:
        print(pkg, "-> FAIL", (r.stderr or r.stdout).strip()[-300:])
print("python", sys.version.split()[0])
