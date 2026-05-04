"""Runtime preflight checks for local Tien Am Cac deployments."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from .audio_utils import get_ffmpeg_binary, get_ffprobe_binary
    from .config import settings
except ImportError:
    from audio_utils import get_ffmpeg_binary, get_ffprobe_binary
    from config import settings


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def run_preflight(
    *,
    deep: bool = False,
    include_hyperframes: bool | None = None,
) -> dict[str, Any]:
    """Run deterministic environment checks.

    ``deep=True`` performs a real F5-TTS synthesis probe, which can take time.
    """

    checks: list[CheckResult] = []
    checks.extend(_check_python_runtime())
    checks.extend(_check_ffmpeg())
    checks.extend(_check_local_tts_files())
    checks.extend(_check_local_ai_stack())
    checks.extend(_check_ollama())

    if deep:
        checks.append(_check_tts_synthesis())

    renderer = settings.VIDEO_EDIT_RENDERER.lower()
    should_check_hyperframes = include_hyperframes
    if should_check_hyperframes is None:
        should_check_hyperframes = renderer in {"auto", "hyperframes"}
    if should_check_hyperframes:
        checks.extend(_check_hyperframes(required=renderer == "hyperframes"))

    failed = [item for item in checks if item.status == "fail"]
    warnings = [item for item in checks if item.status == "warn"]
    return {
        "ok": not failed,
        "status": "fail" if failed else ("warn" if warnings else "ok"),
        "deep": deep,
        "checks": [asdict(item) for item in checks],
    }


def _check_python_runtime() -> list[CheckResult]:
    results = []
    results.append(_ok("python", f"executable={os.sys.executable}"))
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        results.append(_ok("backend_dependencies", "fastapi and uvicorn import ok"))
    except Exception as exc:
        results.append(_fail("backend_dependencies", str(exc)))
    return results


def _check_ffmpeg() -> list[CheckResult]:
    ffmpeg = get_ffmpeg_binary()
    ffprobe = get_ffprobe_binary()
    results = []
    if ffmpeg:
        results.append(_ok("ffmpeg", ffmpeg))
    else:
        results.append(_fail("ffmpeg", "ffmpeg not found"))
    if ffprobe:
        results.append(_ok("ffprobe", ffprobe))
    else:
        results.append(_warn("ffprobe", "ffprobe not found; some probes may fallback to ffmpeg"))
    return results


def _check_local_tts_files() -> list[CheckResult]:
    checks = [
        ("LOCAL_TTS_COMMAND", settings.LOCAL_TTS_COMMAND, _command_exists(settings.LOCAL_TTS_COMMAND)),
        ("LOCAL_TTS_CKPT_FILE", str(settings.LOCAL_TTS_CKPT_FILE), bool(settings.LOCAL_TTS_CKPT_FILE and settings.LOCAL_TTS_CKPT_FILE.exists())),
        ("LOCAL_TTS_VOCAB_FILE", str(settings.LOCAL_TTS_VOCAB_FILE), bool(settings.LOCAL_TTS_VOCAB_FILE and settings.LOCAL_TTS_VOCAB_FILE.exists())),
        ("LOCAL_TTS_REF_AUDIO", str(settings.LOCAL_TTS_REF_AUDIO), bool(settings.LOCAL_TTS_REF_AUDIO and settings.LOCAL_TTS_REF_AUDIO.exists())),
        ("LOCAL_TTS_REF_TEXT", "configured" if settings.LOCAL_TTS_REF_TEXT.strip() else "", bool(settings.LOCAL_TTS_REF_TEXT.strip())),
    ]
    return [_ok(name, value) if ok else _fail(name, f"missing or invalid: {value}") for name, value, ok in checks]


def _check_local_ai_stack() -> list[CheckResult]:
    results = []
    for package in ("torch", "torchaudio", "torchcodec", "f5_tts"):
        try:
            version = _package_version(package)
            if package == "torchcodec":
                _probe_torchcodec_import()
            results.append(_ok(package, version))
        except Exception as exc:
            results.append(_fail(package, str(exc)))
    return results


def _check_ollama() -> list[CheckResult]:
    results = []
    try:
        with urllib.request.urlopen(f"{settings.OLLAMA_BASE_URL}/api/version", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        version = str(payload.get("version") or "")
        if _version_tuple(version) < (0, 7, 0):
            results.append(_fail("ollama_version", f"{version}; qwen2.5vl requires Ollama 0.7.0+"))
        else:
            results.append(_ok("ollama_version", version))
    except Exception as exc:
        results.append(_fail("ollama_version", f"Ollama unavailable: {exc}"))
        return results

    try:
        with urllib.request.urlopen(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = {str(item.get("name")) for item in payload.get("models", [])}
        for model in (settings.OLLAMA_MODEL, settings.VIDEO_VISION_MODEL):
            if model in models:
                results.append(_ok(f"ollama_model:{model}", "available"))
            else:
                results.append(_fail(f"ollama_model:{model}", f"missing; available={sorted(models)}"))
    except Exception as exc:
        results.append(_fail("ollama_models", str(exc)))
    return results


def _check_tts_synthesis() -> CheckResult:
    try:
        try:
            from .agents.local_voice_engine import LocalVoiceEngine
        except ImportError:
            from agents.local_voice_engine import LocalVoiceEngine

        output = Path(tempfile.gettempdir()) / "tienamcac_preflight_tts.wav"
        LocalVoiceEngine().synthesize_text("Xin chao, day la kiem tra he thong ngan.", output)
        size = output.stat().st_size if output.exists() else 0
        if size < 1024:
            return _fail("local_tts_synthesis", f"output too small: {size} bytes")
        return _ok("local_tts_synthesis", f"{output} ({size} bytes)")
    except Exception as exc:
        return _fail("local_tts_synthesis", str(exc))


def _check_hyperframes(required: bool) -> list[CheckResult]:
    results = []
    node = _run_command(["node", "--version"], timeout=10)
    if node[0] == 0:
        version = node[1].strip()
        major = int(version.lstrip("v").split(".")[0]) if version.startswith("v") else 0
        if major >= 22:
            results.append(_ok("node", version))
        else:
            results.append(_fail("node", f"{version}; HyperFrames requires Node.js >= 22"))
    else:
        item = _fail("node", node[2].strip() or "node not available")
        results.append(item if required else _warn(item.name, item.detail))

    npx = _run_command(["npx", "--version"], timeout=10)
    if npx[0] == 0:
        results.append(_ok("npx", npx[1].strip()))
    else:
        item = _fail("npx", npx[2].strip() or "npx not available")
        results.append(item if required else _warn(item.name, item.detail))

    if node[0] == 0 and npx[0] == 0:
        cmd = shlex.split(settings.HYPERFRAMES_COMMAND) + ["doctor"]
        doctor = _run_command(cmd, timeout=120)
        if doctor[0] == 0:
            results.append(_ok("hyperframes_doctor", "ok"))
        else:
            item = _fail("hyperframes_doctor", (doctor[2] or doctor[1]).strip()[:1000])
            results.append(item if required else _warn(item.name, item.detail))
    return results


def _command_exists(command: str) -> bool:
    if not command:
        return False
    executable = command.split()[0]
    return _resolve_command(executable) is not None or Path(executable).exists()


def _package_version(name: str) -> str:
    import importlib.metadata as metadata

    return metadata.version(name)


def _probe_torchcodec_import() -> None:
    _prepare_windows_dll_search_path()
    import torchcodec  # noqa: F401


def _run_command(command: Iterable[str], timeout: int) -> tuple[int, str, str]:
    try:
        cmd = list(command)
        if cmd:
            resolved = _resolve_command(cmd[0])
            if resolved:
                cmd[0] = resolved
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except Exception as exc:
        return 1, "", str(exc)


def _prepare_windows_dll_search_path() -> None:
    if os.name != "nt":
        return
    root = Path(__file__).resolve().parents[1]
    ffmpeg_bin = root / ".tools" / "ffmpeg" / "bin"
    if not ffmpeg_bin.exists():
        return
    value = str(ffmpeg_bin)
    current = os.environ.get("PATH", "")
    if value not in current.split(os.pathsep):
        os.environ["PATH"] = value + os.pathsep + current
    try:
        os.add_dll_directory(value)
    except (AttributeError, OSError):
        pass


def _resolve_command(executable: str) -> str | None:
    if not executable:
        return None
    if Path(executable).exists():
        return executable
    if os.name == "nt":
        stem = executable.lower()
        if stem in {"node", "npm", "npx"}:
            for suffix in (".cmd", ".exe", ".bat"):
                found = shutil.which(executable + suffix)
                if found:
                    return found
    return shutil.which(executable)


def _version_tuple(value: str) -> tuple[int, int, int]:
    numbers = [int(part) for part in re.findall(r"\d+", value)[:3]]
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers[:3])


def _ok(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="ok", detail=detail)


def _warn(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="warn", detail=detail)


def _fail(name: str, detail: str) -> CheckResult:
    return CheckResult(name=name, status="fail", detail=detail)
