$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot

$ollamaExe = Join-Path $ProjectRoot ".tools\\ollama\\ollama.exe"
$ollamaModels = Join-Path $ProjectRoot ".tools\\ollama-models"
New-Item -ItemType Directory -Force -Path $ollamaModels | Out-Null
$env:OLLAMA_MODELS = $ollamaModels
$env:OLLAMA_HOST = "127.0.0.1:11434"

if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath $ollamaExe -ArgumentList "serve" -WindowStyle Hidden -WorkingDirectory (Split-Path $ollamaExe)
    Start-Sleep -Seconds 4
}

$memuraiDir = Join-Path $ProjectRoot ".tools\\memurai-nuget\\tools"
$memuraiData = Join-Path $ProjectRoot ".tools\\memurai-data"
$memuraiConf = Join-Path $ProjectRoot ".tools\\memurai.conf"
New-Item -ItemType Directory -Force -Path $memuraiData | Out-Null

@"
port 6379
bind 127.0.0.1
protected-mode no
dir $($memuraiData -replace '\\','/')
dbfilename dump.rdb
appendonly no
save ""
logfile "$($ProjectRoot -replace '\\','/')/.tools/memurai.log"
"@ | Set-Content -Encoding ASCII $memuraiConf

if (-not (Get-Process memurai -ErrorAction SilentlyContinue)) {
    Start-Process -FilePath (Join-Path $memuraiDir "memurai.exe") -ArgumentList @($memuraiConf) -WindowStyle Hidden -WorkingDirectory $memuraiDir
    Start-Sleep -Seconds 3
}

Write-Host "Ollama and Redis-compatible local services are running."
