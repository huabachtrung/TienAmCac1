@echo off
title Tien Am Cac - Audiobook Generator

echo.
echo ==================================================
echo   TIEN AM CAC - AI Audiobook Generator
echo ==================================================
echo.

set "ROOT=%~dp0"
set "VENV=%ROOT%backend\.venv-win"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PORT=8000"
set "TOOLS=%ROOT%.tools"
set "OLLAMA_MODELS=%TOOLS%\ollama-models"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"
set "PATH=%TOOLS%\ffmpeg\bin;%TOOLS%\node;%PATH%"

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found at:
    echo         %PYTHON%
    pause
    exit /b 1
)

echo [OK] Python venv: %PYTHON%

REM Set PYTHONPATH so backend package is importable
set "PYTHONPATH=%ROOT%"

where ffmpeg >nul 2>&1
if %errorlevel%==0 (
    echo [OK] ffmpeg: installed
) else (
    echo [WARN] ffmpeg not found in PATH
)

if not exist "%ROOT%backend\assets\uploads"      mkdir "%ROOT%backend\assets\uploads"
if not exist "%ROOT%backend\assets\output"        mkdir "%ROOT%backend\assets\output"
if not exist "%ROOT%backend\assets\bgm"           mkdir "%ROOT%backend\assets\bgm"
if not exist "%ROOT%backend\assets\sfx"           mkdir "%ROOT%backend\assets\sfx"
if not exist "%ROOT%backend\assets\jobs"          mkdir "%ROOT%backend\assets\jobs"
if not exist "%ROOT%backend\assets\video_output"  mkdir "%ROOT%backend\assets\video_output"
if not exist "%ROOT%backend\assets\video_temp"    mkdir "%ROOT%backend\assets\video_temp"
if not exist "%ROOT%backend\assets\voice_samples" mkdir "%ROOT%backend\assets\voice_samples"
if not exist "%ROOT%backend\assets\models\f5-tts-vietnamese" mkdir "%ROOT%backend\assets\models\f5-tts-vietnamese"

echo [OK] Assets folders: ready
echo.
echo ==================================================
echo   Starting server at http://localhost:%PORT%
echo   Press Ctrl+C to stop
echo ==================================================
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:%PORT%"

cd /d "%ROOT%"
"%PYTHON%" -m uvicorn backend.main:app --host 0.0.0.0 --port %PORT% --reload

echo.
echo Server stopped.
pause
