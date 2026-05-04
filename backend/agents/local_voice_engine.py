"""Strict local Vietnamese TTS provider.

The runtime is intentionally isolated behind a subprocess boundary so the API
can keep running from ``backend.main:app`` while F5-TTS and its torch stack live
in a separate environment.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import httpx
from loguru import logger

try:
    from pydub import AudioSegment, effects
except ImportError:  # pragma: no cover - healthcheck will fail before synthesis
    AudioSegment = None
    effects = None

try:
    from ..config import settings
except ImportError:
    from config import settings


class VoiceQualityError(RuntimeError):
    """Raised when high-quality local TTS cannot produce a valid result."""


@dataclass
class SpeechCue:
    index: int
    text: str
    start: float
    end: float
    path: str


@dataclass
class SpeechSynthesisResult:
    audio_path: Path
    cues: List[SpeechCue]


class LocalVoiceEngine:
    """F5-TTS subprocess adapter for Vietnamese narration."""

    def __init__(self, provider_name: str = "f5_tts_vietnamese"):
        self.provider_name = provider_name

    def healthcheck(self) -> None:
        command = settings.LOCAL_TTS_COMMAND
        executable = shlex.split(command)[0] if command else ""
        if not executable or shutil.which(executable) is None:
            raise VoiceQualityError(
                f"Local TTS command '{command}' is not available. Install F5-TTS in "
                "a separate runtime or set LOCAL_TTS_COMMAND/LOCAL_TTS_COMMAND_TEMPLATE."
            )
        if not settings.LOCAL_TTS_CKPT_FILE or not settings.LOCAL_TTS_CKPT_FILE.exists():
            raise VoiceQualityError(
                f"Missing local TTS checkpoint: {settings.LOCAL_TTS_CKPT_FILE}. "
                "Download a Vietnamese F5-TTS checkpoint before running high-quality jobs."
            )
        if not settings.LOCAL_TTS_VOCAB_FILE or not settings.LOCAL_TTS_VOCAB_FILE.exists():
            raise VoiceQualityError(f"Missing local TTS vocab: {settings.LOCAL_TTS_VOCAB_FILE}")
        if not settings.LOCAL_TTS_REF_AUDIO or not settings.LOCAL_TTS_REF_AUDIO.exists():
            raise VoiceQualityError(
                "LOCAL_TTS_REF_AUDIO must point to a consented Vietnamese reference voice sample."
            )
        if not settings.LOCAL_TTS_REF_TEXT.strip():
            raise VoiceQualityError("LOCAL_TTS_REF_TEXT must contain the exact reference transcript.")
        if AudioSegment is None:
            raise VoiceQualityError("pydub is required for local TTS validation and concatenation.")

    def free_vram(self) -> None:
        """Unload idle AI models (e.g., Ollama) from VRAM to make room for TTS."""
        try:
            logger.info("[LocalVoice] Checking for idle AI models to free VRAM...")
            url = f"{settings.OLLAMA_BASE_URL}/api/ps"
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                models = response.json().get("models", [])
                for m in models:
                    model_name = m.get("model")
                    if model_name:
                        logger.info(f"[LocalVoice] Unloading Ollama model: {model_name}")
                        httpx.post(
                            f"{settings.OLLAMA_BASE_URL}/api/generate",
                            json={"model": model_name, "keep_alive": 0},
                            timeout=5.0
                        )
        except Exception as e:
            logger.warning(f"[LocalVoice] Failed to free VRAM from Ollama: {e}")

    def synthesize_text(self, text: str, output_path: Path) -> Path:
        self.healthcheck()
        normalized = self.normalize_text(text)
        if not normalized:
            raise VoiceQualityError("Refusing to synthesize empty narration text.")
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.free_vram()
        cmd = self._build_command(normalized, output_path)
        logger.info(f"[LocalVoice] Running {self.provider_name} for {output_path.name}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            env=self._subprocess_env(),
        )
        if result.returncode != 0:
            raise VoiceQualityError(result.stderr.strip() or "Local TTS command failed.")
        self._validate_audio(output_path)
        self._normalize_loudness(output_path)
        return output_path

    def synthesize_segments(
        self,
        texts: Iterable[str],
        output_path: Path,
        segment_dir: Optional[Path] = None,
        pause_ms: int = 160,
    ) -> SpeechSynthesisResult:
        self.healthcheck()
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        segment_dir = segment_dir or output_path.parent / "tts_segments"
        segment_dir.mkdir(parents=True, exist_ok=True)
        cues: List[SpeechCue] = []
        timeline = AudioSegment.silent(duration=0, frame_rate=settings.SAMPLE_RATE)
        pause = AudioSegment.silent(duration=pause_ms, frame_rate=settings.SAMPLE_RATE)

        for idx, raw_text in enumerate(texts, start=1):
            text = self.normalize_text(raw_text)
            if not text:
                continue
            segment_path = segment_dir / f"voice_{idx:03d}.wav"
            self.synthesize_text(text, segment_path)
            segment = AudioSegment.from_file(str(segment_path))
            start = len(timeline) / 1000.0
            timeline += segment
            end = len(timeline) / 1000.0
            cues.append(
                SpeechCue(index=idx, text=text, start=round(start, 3), end=round(end, 3), path=str(segment_path))
            )
            timeline += pause

        if not cues:
            raise VoiceQualityError("No valid narration segments were generated.")
        timeline.export(str(output_path), format="wav")
        self._validate_audio(output_path)
        return SpeechSynthesisResult(audio_path=output_path, cues=cues)

    def split_text(self, text: str) -> List[str]:
        cleaned = self.normalize_text(text)
        parts = [
            part.strip()
            for part in re.split(r"(?<=[.!?;:])\s+|\n+", cleaned)
            if part.strip()
        ]
        units: List[str] = []
        for part in parts:
            words = part.split()
            if len(words) <= 32:
                units.append(part)
                continue
            for start in range(0, len(words), 26):
                units.append(" ".join(words[start : start + 26]))
        return units or ([cleaned] if cleaned else [])

    def normalize_text(self, text: str) -> str:
        text = str(text or "")
        replacements = {
            "“": '"',
            "”": '"',
            "‘": "'",
            "’": "'",
            "—": " - ",
            "…": "...",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _build_command(self, text: str, output_path: Path) -> List[str]:
        values = {
            "command": settings.LOCAL_TTS_COMMAND,
            "ckpt_file": str(settings.LOCAL_TTS_CKPT_FILE),
            "vocab_file": str(settings.LOCAL_TTS_VOCAB_FILE),
            "ref_audio": str(settings.LOCAL_TTS_REF_AUDIO),
            "ref_text": settings.LOCAL_TTS_REF_TEXT,
            "gen_text": text,
            "output_file": str(output_path),
            "speed": str(settings.LOCAL_TTS_SPEED),
        }
        if settings.LOCAL_TTS_COMMAND_TEMPLATE:
            return shlex.split(settings.LOCAL_TTS_COMMAND_TEMPLATE.format(**values))
        return [
            *shlex.split(settings.LOCAL_TTS_COMMAND),
            "--model",
            "F5TTS_Base",
            "--ref_audio",
            values["ref_audio"],
            "--ref_text",
            values["ref_text"],
            "--gen_text",
            values["gen_text"],
            "--speed",
            values["speed"],
            "--vocoder_name",
            "vocos",
            "--vocab_file",
            values["vocab_file"],
            "--ckpt_file",
            values["ckpt_file"],
            "--output_file",
            values["output_file"],
        ]

    def _subprocess_env(self) -> dict:
        env = os.environ.copy()
        root = str(Path(__file__).resolve().parents[2])
        pythonpath = env.get("PYTHONPATH", "")
        parts = [part for part in pythonpath.split(os.pathsep) if part]
        if root not in parts:
            env["PYTHONPATH"] = root + (os.pathsep + pythonpath if pythonpath else "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("WANDB_DISABLED", "true")
        return env

    def _validate_audio(self, path: Path) -> None:
        if not path.exists() or path.stat().st_size < 1024:
            raise VoiceQualityError(f"Local TTS did not create a valid audio file: {path}")
        if AudioSegment is None:
            return
        audio = AudioSegment.from_file(str(path))
        if len(audio) < 250:
            raise VoiceQualityError(f"Generated audio is too short: {path}")
        if audio.dBFS == float("-inf"):
            raise VoiceQualityError(f"Generated audio is silent: {path}")

    def _normalize_loudness(self, path: Path) -> None:
        if AudioSegment is None or effects is None:
            return
        audio = AudioSegment.from_file(str(path))
        try:
            audio = effects.normalize(audio, headroom=1.5)
            audio.export(str(path), format="wav")
        except Exception as exc:
            logger.warning(f"[LocalVoice] Loudness normalization skipped: {exc}")
