param(
    [string]$Python = "python",
    [string]$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
)

$ErrorActionPreference = "Stop"

$venv = Join-Path $RepoRoot ".venv-local-ai"
$pythonExe = Join-Path $venv "Scripts\python.exe"
$pipExe = Join-Path $venv "Scripts\pip.exe"

if (!(Test-Path $pythonExe)) {
    & $Python -m venv $venv
}

& $pythonExe -m pip install --upgrade pip wheel setuptools

# RTX 2060 works best with CUDA-enabled torch. Keep this runtime separate from
# backend/.venv-win so the FastAPI app remains stable.
& $pipExe install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128
& $pipExe install -r (Join-Path $RepoRoot "backend\requirements-local-ai.txt")

Write-Host ""
Write-Host "Local AI runtime installed at $venv"
Write-Host "Set LOCAL_TTS_COMMAND to:"
Write-Host "  $pythonExe -m f5_tts.infer.infer_cli"
Write-Host ""
Write-Host "Then download a Vietnamese F5-TTS checkpoint into:"
Write-Host "  backend\assets\models\f5-tts-vietnamese"
Write-Host "and configure LOCAL_TTS_REF_AUDIO / LOCAL_TTS_REF_TEXT in backend\.env."
