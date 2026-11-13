# Resumable model download with stall detection and sha256 check.
$ErrorActionPreference = "Continue"
$d = "$env:USERPROFILE\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF"
$part = "$d\Qwen3.5-9B-Q4_K_M.gguf.part"
$lms = "$d\downloading_bionic_Qwen3.5-9B-Q4_K_M.gguf.part"
$url = "https://huggingface.co/lmstudio-community/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf"
$size = 5627044256
$sha = "cd76ec205963b3b33350093e6904d9de16c4e666fd104e1f632d25c7f15f2a13"

# a piece larger than the real file is corrupt - start over
if ((Test-Path $part) -and (Get-Item $part).Length -gt $size) { Remove-Item $part; "removed corrupt partial" }
if (Test-Path $lms) { Remove-Item $lms }
for ($i = 1; $i -le 40; $i++) {
    $have = if (Test-Path $part) { (Get-Item $part).Length } else { 0 }
    if ($have -ge $size) { break }
    "try $i, have $([math]::Round($have/1GB,2)) of 5.24 GB"
    curl.exe -L --fail -sS -w "speed=%{speed_download} B/s\n" -C - --connect-timeout 30 --speed-limit 10240 --speed-time 60 -o $part $url
    if ($LASTEXITCODE -ne 0) { "curl exit $LASTEXITCODE, reconnecting"; Start-Sleep 5 }
}
$have = (Get-Item $part).Length
if ($have -ne $size) { "INCOMPLETE: $have of $size"; exit 1 }
$h = (Get-FileHash $part -Algorithm SHA256).Hash.ToLower()
if ($h -ne $sha) { "SHA256 MISMATCH - file corrupt, removed"; Remove-Item $part; exit 2 }
Move-Item -Force $part "$d\Qwen3.5-9B-Q4_K_M.gguf"
"DONE OK: model downloaded and verified"
