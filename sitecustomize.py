"""Local Windows runtime hooks for command-line tools launched from this repo."""

from __future__ import annotations

import os
from pathlib import Path


def _add_tool_dll_dir() -> None:
    if os.name != "nt":
        return
    root = Path(__file__).resolve().parent
    ffmpeg_bin = root / ".tools" / "ffmpeg" / "bin"
    if not ffmpeg_bin.exists():
        return
    value = str(ffmpeg_bin)
    path = os.environ.get("PATH", "")
    if value not in path.split(os.pathsep):
        os.environ["PATH"] = value + os.pathsep + path
    try:
        os.add_dll_directory(value)
    except (AttributeError, OSError):
        pass


_add_tool_dll_dir()
