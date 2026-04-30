param(
    [string]$PythonVersion = "3.11.9"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ToolsDir = Join-Path $ProjectRoot ".tools"
$PythonDir = Join-Path $ToolsDir "python311"
$InstallerPath = Join-Path $ToolsDir "python-$PythonVersion-amd64.exe"
$VenvDir = Join-Path $ProjectRoot "backend\\.venv-win"
$VenvPython = Join-Path $VenvDir "Scripts\\python.exe"

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "assets\\video_sources") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "backend\\assets\\uploads") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "backend\\assets\\output") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ProjectRoot "backend\\assets\\video_output") | Out-Null

if (-not (Test-Path (Join-Path $PythonDir "python.exe"))) {
    Write-Host "[1/5] Download Python $PythonVersion"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" -OutFile $InstallerPath
    Start-Process -FilePath $InstallerPath -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "Include_pip=1",
        "PrependPath=0",
        "Include_test=0",
        "TargetDir=$PythonDir"
    ) -Wait
} else {
    Write-Host "[1/5] Reuse existing Python at $PythonDir"
}

Write-Host "[2/5] Create Windows venv"
& (Join-Path $PythonDir "python.exe") -m venv $VenvDir

Write-Host "[3/5] Install backend dependencies"
& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "backend\\requirements.txt")

Write-Host "[4/5] Prepare environment files"
$EnvPath = Join-Path $ProjectRoot "backend\\.env"
$EnvExamplePath = Join-Path $ProjectRoot "backend\\.env.example"
if (-not (Test-Path $EnvPath) -and (Test-Path $EnvExamplePath)) {
    Copy-Item $EnvExamplePath $EnvPath
}

Write-Host "[5/5] Smoke test backend runtime"
& $VenvPython -c "import backend.main as m; print('READY', m.app.title)"

Write-Host ""
Write-Host "Run API:"
Write-Host "  backend\\.venv-win\\Scripts\\python.exe -m uvicorn backend.main:app --reload"
