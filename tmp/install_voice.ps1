# Voice install for Yanka (approved by Yana). pip hangs on this PC (HTTPS interception), so:
# wheels via curl.exe + sha256 from PyPI, srt (sdist only) as a prebuilt wheel, then offline pip.
$ErrorActionPreference = "Continue"
$w = Join-Path $env:TEMP "cozy_wheels"
New-Item -ItemType Directory -Force $w | Out-Null
$P = "https://files.pythonhosted.org/packages/"
$list = @(
  @("vosk-0.3.45-py3-none-win_amd64.whl", "6994ddc68556c7e5730c3b6f6bad13320e3519b13ce3ed2aa25a86724e7c10ac", "c0/4c/deb0861f7da9696f8a255f1731bb73e9412cca29c4b3888a3fcb2a930a59"),
  @("requests-2.34.2-py3-none-any.whl", "2a0d60c172f83ac6ab31e4554906c0f3b3588d37b5cb939b1c061f4907e278e0", "a0/f4/c67b0b3f1b9245e8d266f0f112c500d50e5b4e83cb6f3b71b6528104182a"),
  @("charset_normalizer-3.5.2-cp315-cp315-win_amd64.whl", "89b53f3cda69831909888e0494f4fa0bcd3537e3e138dabeb620bd6ad946bae8", "98/e8/55d5fd3935b4bce6da4fe0df61898e8c82653e317e677bd58aceb9c60f13"),
  @("idna-3.20-py3-none-any.whl", "ab7ae7122974553370f0bdb919e1a960b2cd1bc1ef0276416d896db81c14582c", "58/a2/bb081bab032533a855d44de1d56f8e8426114ff1ba5d1f07a438a0a654f8"),
  @("urllib3-2.8.0-py3-none-any.whl", "0cf3cae568d36aa9576b28dfb35f11328f1cb974ca7647d9475ebb86c75ac6e3", "92/9d/c4e665119135114480843e7ab388fa94d8480650450e6f8e26b70d323a4c"),
  @("certifi-2026.10.23-py3-none-any.whl", "e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1", "32/d1/a3652fe2c19134d0d15922177821d863cbd7e842d53a21bbe7e8e3a1b5ac"),
  @("tqdm-4.70.1-py3-none-any.whl", "c293e525e6fef9c20e8728fd4612df02a0aa31bb5fe91ecd93e123b1b7bffa73", "a7/03/921a3d3c75785aca9ebfbfcabfbc3a1be12e2ab5265deb026d55a5a3f83e"),
  @("colorama-0.4.6-py2.py3-none-any.whl", "4f1d9991f5acc0ca119f9d443620b77f9d6b33703e51011c16baf57afb285fc6", "d1/d6/3965ed04c63042e047cb6a3e6ed1a63a35087b6a609aa3a15ed8ac56c221"),
  @("websockets-17.4-cp315-cp315-win_amd64.whl", "8b45d40f07081b377f0b837ddbfba4a9ab4a89a7b569d3c095436564e767a27b", "25/a6/818647a9a9b3ee8bf131e9ab4fa1f2db9c6539f08e7084dd57549e0bd49d"),
  @("sounddevice-0.5.6-py3-none-win_amd64.whl", "7f4162f514f007b0bf25a3ccfed3f1705bc2ec311888a90232729eec4f57a4f4", "8d/3e/cbc593c31a5f0d817b3fe97e64aa8461bd0f55cb07b67ce1b776296ae336")
)
foreach ($x in $list) {
    $f = Join-Path $w $x[0]
    $ok = (Test-Path $f) -and ((Get-FileHash $f -Algorithm SHA256).Hash.ToLower() -eq $x[1])
    if (-not $ok) { curl.exe -L --fail -sS --retry 3 -o $f ($P + $x[2] + "/" + $x[0]) }
    if ((Get-FileHash $f -Algorithm SHA256).Hash.ToLower() -ne $x[1]) { "SHA256 MISMATCH " + $x[0]; exit 3 }
    "ok " + $x[0]
}
$srt = Join-Path $w "srt-3.5.3-py3-none-any.whl"
[IO.File]::WriteAllBytes($srt, [Convert]::FromBase64String((Get-Content "$PSScriptRoot\srt-3.5.3-py3-none-any.whl.b64" -Raw)))
if ((Get-FileHash $srt -Algorithm SHA256).Hash.ToLower() -ne "8eefc919adf9d655de316cab306d11a4ae7aa11377acf6352b13c57baa3cebd0") { "SHA256 MISMATCH srt"; exit 3 }
"ok srt-3.5.3 (built from the PyPI sdist)"
"== pip install (offline)"
py -3 -m pip install --disable-pip-version-check --user --no-index --find-links $w vosk sounddevice 2>&1 | Select-Object -Last 3
$dir = Join-Path $env:LOCALAPPDATA "CozyCode\vosk"
New-Item -ItemType Directory -Force $dir | Out-Null
$name = "vosk-model-small-ru-0.22"
if (Test-Path (Join-Path $dir $name)) { "model already present" } else {
    $zip = Join-Path $dir "$name.zip"
    "== download model"
    curl.exe -L --fail -sS --retry 5 -C - -o $zip "https://alphacephei.com/vosk/models/$name.zip"
    if ((Get-Item $zip).Length -ne 46236750) { "MODEL SIZE MISMATCH"; exit 2 }
    Expand-Archive -Force $zip $dir
    Remove-Item $zip
}
Get-ChildItem $dir | ForEach-Object { "model dir: " + $_.Name }
py -3 -c "import vosk, sounddevice as sd; print('vosk ok; mic:', sd.query_devices(kind='input')['name'])"
"DONE"
