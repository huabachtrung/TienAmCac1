from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _prepend_path(path: Path) -> None:
    if path.exists():
        current = os.environ.get("PATH", "")
        parts = current.split(os.pathsep) if current else []
        value = str(path)
        if value not in parts:
            os.environ["PATH"] = value + os.pathsep + current


TOOLS = ROOT / ".tools"
_prepend_path(TOOLS / "ffmpeg" / "bin")
_prepend_path(TOOLS / "node")
os.environ.setdefault("OLLAMA_MODELS", str(TOOLS / "ollama-models"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from backend.preflight import run_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Tien Am Cac local preflight checks.")
    parser.add_argument("--deep", action="store_true", help="Run real F5-TTS synthesis probe.")
    parser.add_argument("--hyperframes", action="store_true", help="Check HyperFrames even when renderer is not strict.")
    args = parser.parse_args()

    report = run_preflight(deep=args.deep, include_hyperframes=args.hyperframes or None)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
