"""Local vision-language analysis for video review planning."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Dict, List

import httpx
from loguru import logger

try:
    from ..audio_utils import get_ffmpeg_binary
    from ..config import settings
except ImportError:
    from audio_utils import get_ffmpeg_binary
    from config import settings


class VideoUnderstandingError(RuntimeError):
    """Raised when strict visual understanding cannot be completed."""


class VideoUnderstandingEngine:
    def __init__(self):
        self.ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.model = settings.VIDEO_VISION_MODEL
        self.http = httpx.Client(timeout=120.0)

    # ── Public API ───────────────────────────────────────────────────────

    def analyze(self, source_path: Path, meta: Dict[str, float], transcript: str) -> Dict[str, object]:
        """Analyze video keyframes with VLM.  Returns empty analysis if VLM unavailable."""
        frame_paths = self.extract_keyframes(source_path, float(meta.get("duration_sec") or 0.0))
        if not frame_paths:
            logger.warning("[VideoUnderstanding] No keyframes extracted — using transcript-only analysis")
            return self._transcript_only_analysis(transcript, source_path.stem)

        # Pre-check Ollama connectivity before expensive VLM call
        if not self._check_ollama_available():
            logger.warning("[VideoUnderstanding] Ollama not reachable — using transcript-only analysis")
            return self._transcript_only_analysis(transcript, source_path.stem)

        try:
            observations = self._analyze_frames(frame_paths, transcript)
            script_outline = self._build_script_outline(observations, transcript)
            return {
                "model": self.model,
                "keyframes": [str(path) for path in frame_paths],
                "observations": observations,
                "script_outline": script_outline,
            }
        except VideoUnderstandingError:
            if settings.VIDEO_VISION_REQUIRED:
                raise
            logger.warning("[VideoUnderstanding] VLM analysis failed — falling back to transcript-only")
            return self._transcript_only_analysis(transcript, source_path.stem)
        except Exception as exc:
            if settings.VIDEO_VISION_REQUIRED:
                raise VideoUnderstandingError(str(exc)) from exc
            logger.warning(f"[VideoUnderstanding] VLM analysis error: {exc} — falling back")
            return self._transcript_only_analysis(transcript, source_path.stem)

    def extract_keyframes(self, source_path: Path, duration_sec: float) -> List[Path]:
        temp_dir = settings.VIDEO_TEMP_DIR / "_keyframes" / source_path.stem[:32]
        temp_dir.mkdir(parents=True, exist_ok=True)
        count = max(3, int(settings.VIDEO_KEYFRAME_COUNT))
        if duration_sec <= 0:
            timestamps = [0.5]
        else:
            timestamps = [max(0.0, (idx + 0.5) * duration_sec / count) for idx in range(count)]
        frames: List[Path] = []
        for idx, timestamp in enumerate(timestamps, start=1):
            frame = temp_dir / f"frame_{idx:02d}.jpg"
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                str(round(timestamp, 2)),
                "-i",
                str(source_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                "-q:v",
                "3",
                str(frame),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0 and frame.exists() and frame.stat().st_size > 1024:
                frames.append(frame)
        return frames

    # ── Private helpers ──────────────────────────────────────────────────

    def _check_ollama_available(self) -> bool:
        """Quick health check for Ollama — returns False if unreachable."""
        try:
            r = httpx.get(
                f"{settings.OLLAMA_BASE_URL}/api/tags",
                timeout=float(settings.OLLAMA_TIMEOUT),
            )
            return r.status_code == 200
        except Exception:
            return False

    def _analyze_frames(self, frame_paths: List[Path], transcript: str) -> Dict[str, object]:
        images = [base64.b64encode(path.read_bytes()).decode("ascii") for path in frame_paths]
        prompt = (
            "Bạn là biên tập viên video nghiêm khắc. Hãy đọc các keyframe và transcript để hiểu nội dung thật. "
            "Trả về JSON thuần với keys: visual_summary, subjects, events, mood, hook_angle, missing_context. "
            "Không nói chung chung; nêu chi tiết nhìn thấy trong hình và liên hệ transcript.\n\n"
            f"Transcript rút gọn:\n{transcript[:2500]}"
        )
        try:
            response = self.http.post(
                self.ollama_url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "images": images,
                    "stream": False,
                    "options": {"temperature": 0.15, "num_predict": 800},
                },
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            payload = self._parse_json(raw)
            if not isinstance(payload, dict):
                raise ValueError("VLM response is not an object")
            return payload
        except Exception as exc:
            if settings.VIDEO_VISION_REQUIRED:
                raise VideoUnderstandingError(
                    f"Local VLM '{self.model}' is required but failed: {exc}"
                ) from exc
            logger.warning(f"[VideoUnderstanding] VLM failed, continuing without strict vision: {exc}")
            return {"visual_summary": "", "subjects": [], "events": [], "mood": "", "hook_angle": ""}

    def _transcript_only_analysis(self, transcript: str, source_name: str) -> Dict[str, object]:
        """Build a useful analysis from transcript alone when VLM is unavailable."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", transcript) if len(s.strip()) > 10]
        subjects = []
        events = []
        mood = "neutral"

        # Extract key topics from transcript
        topic_keywords = {
            "action": ["đánh", "chiến", "nổ", "chém", "chạy", "bay", "lao"],
            "emotion": ["khóc", "cười", "sợ", "vui", "buồn", "tức", "giận"],
            "dialog": ["nói", "hỏi", "đáp", "trả lời", "kể", "bảo"],
        }
        for category, keywords in topic_keywords.items():
            for kw in keywords:
                if kw in transcript.lower():
                    events.append(f"{category}: phát hiện '{kw}' trong transcript")

        # Pick most info-dense sentences as highlights
        best = sorted(sentences, key=len, reverse=True)[:5]

        hook_angle = best[0] if best else f"Video {source_name} cần review"
        visual_summary = " ".join(best[:3]) if best else f"Nội dung từ {source_name}"

        return {
            "model": "transcript_fallback",
            "keyframes": [],
            "observations": {
                "visual_summary": visual_summary,
                "subjects": subjects,
                "events": events,
                "mood": mood,
                "hook_angle": hook_angle[:240],
                "missing_context": "VLM không khả dụng — phân tích dựa hoàn toàn trên transcript",
            },
            "script_outline": {
                "hook": hook_angle[:240],
                "context": visual_summary[:360],
                "insight": " ".join(str(e) for e in events[:3]),
                "closing": "Tóm lại, đây là những chi tiết đáng chú ý nhất từ nội dung.",
                "transcript_hint": re.sub(r"\s+", " ", transcript[:360]).strip(),
            },
        }

    def _build_script_outline(self, observations: Dict[str, object], transcript: str) -> Dict[str, str]:
        return {
            "hook": str(observations.get("hook_angle") or observations.get("visual_summary") or "")[:240],
            "context": str(observations.get("visual_summary") or "")[:360],
            "insight": " ".join(str(item) for item in observations.get("events", [])[:3])
            if isinstance(observations.get("events"), list)
            else str(observations.get("events") or "")[:360],
            "closing": "Tóm lại, điểm đáng xem nằm ở cách các chi tiết hình ảnh và lời thoại cùng đẩy mạch nội dung.",
            "transcript_hint": re.sub(r"\s+", " ", transcript[:360]).strip(),
        }

    def _parse_json(self, raw: str):
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group())
        return json.loads(raw)
