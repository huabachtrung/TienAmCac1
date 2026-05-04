param(
    [switch]$VerifyOnly,
    [switch]$SkipLargeDownloads,
    [switch]$FixLocalAi
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$ToolsDir = Join-Path $Root ".tools"
$BackendDir = Join-Path $Root "backend"
$Python = Join-Path $BackendDir ".venv-win\Scripts\python.exe"
$Requirements = Join-Path $Root "requirements.txt"
$OllamaModels = Join-Path $ToolsDir "ollama-models"
$BundledOllama = Join-Path $ToolsDir "ollama\ollama.exe"
$BundledNodeDir = Join-Path $ToolsDir "node"
$BundledFfmpegDir = Join-Path $ToolsDir "ffmpeg"
$RequiredTextModel = "qwen2.5:1.5b"
$RequiredVisionModel = "qwen2.5vl:3b"

function Write-Step($Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok($Message) {
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Warn($Message) {
    Write-Host "[WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail($Message) {
    Write-Host "[FAIL] $Message" -ForegroundColor Red
}

function Add-PathFront($PathToAdd) {
    if ((Test-Path $PathToAdd) -and (($env:PATH -split [IO.Path]::PathSeparator) -notcontains $PathToAdd)) {
        $env:PATH = "$PathToAdd$([IO.Path]::PathSeparator)$env:PATH"
    }
}

function Add-PythonPathFront($PathToAdd) {
    if ((Test-Path $PathToAdd) -and (($env:PYTHONPATH -split [IO.Path]::PathSeparator) -notcontains $PathToAdd)) {
        $env:PYTHONPATH = "$PathToAdd$([IO.Path]::PathSeparator)$env:PYTHONPATH"
    }
}

function Resolve-WindowsCommand($Name) {
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $null
    }
    if (Test-Path $Name) {
        return (Resolve-Path $Name).Path
    }
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        $base = [IO.Path]::GetFileNameWithoutExtension($Name).ToLowerInvariant()
        if ($base -in @("node", "npm", "npx")) {
            foreach ($suffix in @(".cmd", ".exe", ".bat")) {
                $cmd = Get-Command "$base$suffix" -ErrorAction SilentlyContinue
                if ($cmd) { return $cmd.Source }
            }
        }
    }
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    return $null
}

function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments=$true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
    if ($null -ne $exitCode -and $exitCode -ne 0) {
        throw "Command failed ($exitCode): $FilePath $($Arguments -join ' ')"
    }
}

function Assert-UnderWorkspace($PathToCheck) {
    $rootPath = [IO.Path]::GetFullPath($Root.Path).TrimEnd('\')
    $fullPath = [IO.Path]::GetFullPath($PathToCheck).TrimEnd('\')
    if (!$fullPath.StartsWith($rootPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside workspace: $fullPath"
    }
}

function Invoke-Download($Url, $OutFile) {
    if ($VerifyOnly) {
        throw "Missing file $OutFile. Run without -VerifyOnly to download: $Url"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $OutFile) | Out-Null
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing
}

function Test-SharedFfmpegBin($BinDir) {
    if (!(Test-Path $BinDir)) {
        return $false
    }
    # TorchCodec on Windows supports FFmpeg 4-7. FFmpeg master currently ships
    # avcodec-62.dll (FFmpeg 8), which is not usable for the local F5-TTS stack.
    $codec = Get-ChildItem -Path $BinDir -Filter "avcodec-*.dll" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
    if (!($codec | Where-Object { $_ -match "^avcodec-(58|59|60|61)\.dll$" })) {
        return $false
    }
    $required = @("avformat*.dll", "avutil*.dll")
    foreach ($pattern in $required) {
        if (!(Get-ChildItem -Path $BinDir -Filter $pattern -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            return $false
        }
    }
    return $true
}

function Ensure-BundledOllama {
    if (Test-Path $BundledOllama) {
        return
    }
    if ($VerifyOnly) {
        throw "Ollama CLI missing and bundled Ollama is not installed. Run bootstrap without -VerifyOnly."
    }
    Write-Warn "System Ollama missing or too old. Installing portable Ollama CLI."
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/ollama/ollama/releases/latest"
    $asset = $release.assets | Where-Object { $_.name -eq "ollama-windows-amd64.zip" } | Select-Object -First 1
    if (!$asset) {
        throw "Could not find ollama-windows-amd64.zip in the latest Ollama release."
    }
    $zip = Join-Path $ToolsDir "ollama-windows-amd64.zip"
    Invoke-Download $asset.browser_download_url $zip
    $extract = Join-Path $ToolsDir "ollama-extract"
    Assert-UnderWorkspace $extract
    if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    Assert-UnderWorkspace (Split-Path $BundledOllama)
    if (Test-Path (Split-Path $BundledOllama)) { Remove-Item -LiteralPath (Split-Path $BundledOllama) -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $BundledOllama) | Out-Null
    Copy-Item -Path (Join-Path $extract "*") -Destination (Split-Path $BundledOllama) -Recurse -Force
    if (!(Test-Path $BundledOllama)) {
        throw "Portable Ollama install did not produce $BundledOllama"
    }
}

function Ensure-Python {
    Write-Step "Python backend runtime"
    if (!(Test-Path $Python)) {
        throw "Backend Python runtime not found: $Python"
    }
    Invoke-Native $Python --version
    if (!$VerifyOnly) {
        Invoke-Native $Python -m pip install --upgrade pip
        Invoke-Native $Python -m pip install -r $Requirements
    }
    Write-Ok "Python runtime ready"
}

function Ensure-Ffmpeg {
    Write-Step "FFmpeg shared build"
    $bundledBin = Join-Path $BundledFfmpegDir "bin"
    Add-PathFront $bundledBin
    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    $hasShared = Test-SharedFfmpegBin $bundledBin
    if ($ffmpeg -and $ffprobe -and (!$FixLocalAi -or $hasShared)) {
        Write-Ok "ffmpeg=$($ffmpeg.Source)"
        Write-Ok "ffprobe=$($ffprobe.Source)"
        if ($hasShared) { Write-Ok "shared FFmpeg DLLs available for TorchCodec" }
        return
    }
    if ($VerifyOnly) {
        throw "ffmpeg/ffprobe or shared FFmpeg DLLs missing. Run bootstrap without -VerifyOnly."
    }
    $zip = Join-Path $ToolsDir "ffmpeg-shared.zip"
    $url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip"
    Invoke-Download $url $zip
    $extract = Join-Path $ToolsDir "ffmpeg-extract"
    Assert-UnderWorkspace $extract
    if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $bin = Get-ChildItem -Path $extract -Recurse -Directory -Filter "bin" | Select-Object -First 1
    if (!$bin) { throw "Could not locate ffmpeg bin directory after extraction." }
    Assert-UnderWorkspace $BundledFfmpegDir
    if (Test-Path $BundledFfmpegDir) { Remove-Item -LiteralPath $BundledFfmpegDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $bundledBin | Out-Null
    Copy-Item -Path (Join-Path $bin.FullName "*") -Destination $bundledBin -Recurse -Force
    Add-PathFront $bundledBin
    if (!(Test-SharedFfmpegBin $bundledBin)) {
        throw "Downloaded FFmpeg build does not contain required shared DLLs."
    }
    Write-Ok "Bundled ffmpeg installed"
}

function Ensure-Node {
    Write-Step "Node.js and npm for HyperFrames"
    Add-PathFront $BundledNodeDir
    $node = Resolve-WindowsCommand "node"
    $npm = Resolve-WindowsCommand "npm"
    $npx = Resolve-WindowsCommand "npx"
    if ($node -and $npm -and $npx) {
        $version = (& $node --version).Trim()
        $major = [int]($version.TrimStart("v").Split(".")[0])
        if ($major -ge 22) {
            Write-Ok "node=$version"
            Write-Ok "npm/npx ready"
            return
        }
        Write-Warn "Node version $version is below 22"
    }
    if ($VerifyOnly) {
        throw "Node.js >= 22 with npm/npx is missing. Run bootstrap without -VerifyOnly."
    }
    $index = Invoke-RestMethod -Uri "https://nodejs.org/dist/index.json"
    $release = $index | Where-Object { $_.version -like "v22.*" -and $_.files -contains "win-x64-zip" } | Select-Object -First 1
    if (!$release) { throw "Could not find Node.js v22 Windows x64 zip in nodejs.org index." }
    $version = $release.version
    $zip = Join-Path $ToolsDir "node-$version-win-x64.zip"
    $url = "https://nodejs.org/dist/$version/node-$version-win-x64.zip"
    Invoke-Download $url $zip
    $extract = Join-Path $ToolsDir "node-extract"
    Assert-UnderWorkspace $extract
    if (Test-Path $extract) { Remove-Item -LiteralPath $extract -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath $extract -Force
    $nodeRoot = Get-ChildItem -Path $extract -Directory | Select-Object -First 1
    if (!$nodeRoot) { throw "Could not locate extracted Node.js directory." }
    Assert-UnderWorkspace $BundledNodeDir
    Assert-UnderWorkspace $nodeRoot.FullName
    if (Test-Path $BundledNodeDir) { Remove-Item -LiteralPath $BundledNodeDir -Recurse -Force }
    Move-Item -Path $nodeRoot.FullName -Destination $BundledNodeDir
    Add-PathFront $BundledNodeDir
    Invoke-Native (Resolve-WindowsCommand "node") --version
    Invoke-Native (Resolve-WindowsCommand "npm") --version
    Invoke-Native (Resolve-WindowsCommand "npx") --version
    Write-Ok "Node.js $version installed"
}

function Ensure-Ollama {
    Write-Step "Ollama and required models"
    New-Item -ItemType Directory -Force -Path $OllamaModels | Out-Null
    $env:OLLAMA_MODELS = $OllamaModels
    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (!$ollama -and (Test-Path $BundledOllama)) {
        $ollama = Get-Item $BundledOllama
    }
    if (!$ollama) {
        Ensure-BundledOllama
        $ollama = Get-Item $BundledOllama
    }
    $ollamaExe = if ($ollama.Source) { $ollama.Source } else { $ollama.FullName }
    Write-Ok "ollama=$ollamaExe"

    try {
        $versionText = (& $ollamaExe --version) -join " "
        if ($versionText -match "(\d+)\.(\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]; $patch = [int]$Matches[3]
            if (($major -lt 0) -or ($major -eq 0 -and $minor -lt 7)) {
                Ensure-BundledOllama
                $ollamaExe = $BundledOllama
                $versionText = (& $ollamaExe --version) -join " "
                if ($versionText -match "(\d+)\.(\d+)\.(\d+)") {
                    $major = [int]$Matches[1]; $minor = [int]$Matches[2]; $patch = [int]$Matches[3]
                    if (($major -lt 0) -or ($major -eq 0 -and $minor -lt 7)) {
                        throw "Bundled Ollama $major.$minor.$patch is too old; qwen2.5vl requires 0.7.0+."
                    }
                }
            }
        }
        Write-Ok $versionText
    } catch {
        throw "Unable to verify Ollama version: $_"
    }

    $models = ""
    try { $models = (& $ollamaExe list) -join "`n" } catch { Write-Warn "Could not list Ollama models: $_" }
    foreach ($model in @($RequiredTextModel, $RequiredVisionModel)) {
        if ($models -match [regex]::Escape($model)) {
            Write-Ok "Ollama model available: $model"
            continue
        }
        if ($VerifyOnly) { throw "Missing Ollama model: $model" }
        if ($SkipLargeDownloads) { Write-Warn "Skipping model pull because -SkipLargeDownloads is set: $model"; continue }
        Invoke-Native $ollamaExe pull $model
    }
}

function Ensure-LocalAiStack {
    Write-Step "F5-TTS local AI stack"
    if (!$FixLocalAi) {
        Write-Warn "Skipping torch/torchaudio/torchcodec reinstall. Use -FixLocalAi to repair local AI stack."
        return
    }
    if ($VerifyOnly) {
        Write-Warn "-FixLocalAi ignored with -VerifyOnly"
        return
    }
    Invoke-Native $Python -m pip uninstall -y torch torchaudio torchcodec
    Invoke-Native $Python -m pip install torch==2.8.0+cu128 torchaudio==2.8.0+cu128 --extra-index-url https://download.pytorch.org/whl/cu128
    Invoke-Native $Python -m pip install torchcodec==0.7.0
    Invoke-Native $Python -m pip install f5-tts==1.1.20
    Invoke-Native $Python -c "import torch, torchaudio, torchcodec; print(torch.__version__, torchaudio.__version__)"
    Write-Ok "Local AI stack repair attempted"
}

function Update-EnvFile {
    Write-Step "Backend .env"
    $envFile = Join-Path $BackendDir ".env"
    if (!(Test-Path $envFile)) { throw "Missing $envFile" }
    if ($VerifyOnly) {
        Write-Ok ".env present"
        return
    }
    $content = Get-Content -Path $envFile -Raw -Encoding UTF8
    $pairs = [ordered]@{
        "OLLAMA_MODEL" = $RequiredTextModel
        "VIDEO_VISION_REQUIRED" = "true"
        "VIDEO_VISION_MODEL" = $RequiredVisionModel
        "VIDEO_EDIT_RENDERER" = "auto"
        "HYPERFRAMES_COMMAND" = "npx hyperframes"
        "HYPERFRAMES_QUALITY" = "standard"
    }
    foreach ($key in $pairs.Keys) {
        $value = $pairs[$key]
        if ($content -match "(?m)^$key=") {
            $content = [regex]::Replace($content, "(?m)^$key=.*$", "$key=$value")
        } else {
            $content += "`n$key=$value"
        }
    }
    Set-Content -Path $envFile -Value $content -Encoding UTF8
    Write-Ok ".env updated"
}

function Run-Preflight {
    Write-Step "Preflight"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    $env:OLLAMA_MODELS = $OllamaModels
    Invoke-Native $Python (Join-Path $Root "scripts\preflight.py") --hyperframes
}

New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
Add-PathFront (Join-Path $BundledFfmpegDir "bin")
Add-PathFront $BundledNodeDir
Add-PythonPathFront $Root.Path

try {
    Ensure-Python
    Ensure-Ffmpeg
    Ensure-Node
    Ensure-Ollama
    Ensure-LocalAiStack
    Update-EnvFile
    Run-Preflight
    Write-Host ""
    Write-Ok "Bootstrap complete"
} catch {
    Write-Host ""
    Write-Fail $_
    exit 1
}
