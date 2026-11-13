# Voice install for Yanka (approved by Yana): pip vosk + sounddevice, Russian Vosk model (~45 MB).
$ErrorActionPreference = "Continue"
"== pip install vosk sounddevice"
py -3 -m pip install --disable-pip-version-check --user vosk sounddevice 2>&1 | Select-Object -Last 3
$dir = Join-Path $env:LOCALAPPDATA "CozyCode\vosk"
New-Item -ItemType Directory -Force $dir | Out-Null
$name = "vosk-model-small-ru-0.22"
if (Test-Path (Join-Path $dir $name)) { "model already present" } else {
    $zip = Join-Path $dir "$name.zip"
    "== download model"
    curl.exe -L --fail -sS --retry 5 -C - -o $zip "https://alphacephei.com/vosk/models/$name.zip"
    if ($LASTEXITCODE -ne 0) { "DOWNLOAD FAIL $LASTEXITCODE"; exit 1 }
    $len = (Get-Item $zip).Length
    "zip bytes: $len"
    if ($len -ne 46236750) { "SIZE MISMATCH"; exit 2 }
    Expand-Archive -Force $zip $dir
    Remove-Item $zip
}
Get-ChildItem $dir | ForEach-Object { "model dir: " + $_.Name }
"== check"
py -3 -c "import vosk, sounddevice as sd; print('vosk', vosk.__version__ if hasattr(vosk,'__version__') else 'ok'); print('mic:', sd.query_devices(kind='input')['name'])"
"DONE"
