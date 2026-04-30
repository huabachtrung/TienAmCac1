"""
Video review planning utilities.
Builds a clip plan for horizontal or vertical short-form summaries from
videos already stored in project asset folders.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

try:
    from ..audio_utils import get_ffmpeg_binary
    from ..config import settings
    from ..models.schemas import (
        VideoAnalyzeResponse,
        VideoClipSuggestion,
        VideoOrientation,
    )
except ImportError:
    from audio_utils import get_ffmpeg_binary
    from config import settings
    from models.schemas import (
        VideoAnalyzeResponse,
        VideoClipSuggestion,
        VideoOrientation,
    )


class VideoReviewPlanner:
    """Inspects a source video and proposes a short-form summary workflow."""

    def __init__(self):
        self.allowed_roots = [
            settings.VIDEO_SOURCE_DIR.resolve(),
            settings.VIDEO_OUTPUT_DIR.parent.resolve(),
            settings.UPLOAD_DIR.parent.resolve(),
            settings.UPLOAD_DIR.parent.parent.resolve(),
        ]

    def analyze(
        self,
        asset_path: str,
        orientation: VideoOrientation = VideoOrientation.VERTICAL,
        max_clip_seconds: int = 45,
    ) -> VideoAnalyzeResponse:
        source_path = self._resolve_asset_path(asset_path)
        meta = self._probe_video(source_path)
        transcript_path, transcript_lines = self._load_sidecar_transcript(source_path)
        highlights = self._build_highlights(transcript_lines)
        suggested_clips = self._build_clip_plan(
            duration_sec=meta["duration_sec"],
            transcript_lines=transcript_lines,
            highlights=highlights,
            max_clip_seconds=max_clip_seconds,
        )
        target_width, target_height = self._target_size(orientation)
        notes = self._build_notes(source_path, transcript_path, orientation)

        return VideoAnalyzeResponse(
            source_path=str(source_path),
            orientation=orientation,
            duration_sec=meta["duration_sec"],
            width=meta["width"],
            height=meta["height"],
            fps=meta.get("fps"),
            target_width=target_width,
            target_height=target_height,
            transcript_source=str(transcript_path) if transcript_path else None,
            highlights=highlights,
            suggested_clips=suggested_clips,
            notes=notes,
        )

    def _resolve_asset_path(self, asset_path: str) -> Path:
        raw = Path(asset_path)
        project_root = settings.UPLOAD_DIR.parent.parent.resolve()

        candidates: List[Path] = []
        if raw.is_absolute():
            candidates.append(raw.resolve())
        else:
            candidates.extend(
                [
                    (project_root / raw).resolve(),
                    (project_root / "assets" / raw).resolve(),
                    (settings.VIDEO_SOURCE_DIR / raw).resolve(),
                    (settings.VIDEO_SOURCE_DIR / raw.name).resolve(),
                    (settings.UPLOAD_DIR.parent / raw).resolve(),
                ]
            )

        for candidate in candidates:
            if candidate.exists() and self._is_under_allowed_root(candidate):
                return candidate

        raise FileNotFoundError(
            f"Could not find video inside asset folders: {asset_path}"
        )

    def _is_under_allowed_root(self, candidate: Path) -> bool:
        for root in self.allowed_roots:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _probe_video(self, source_path: Path) -> Dict[str, Optional[float]]:
        ffmpeg_bin = get_ffmpeg_binary()
        if not ffmpeg_bin:
            raise RuntimeError("ffmpeg is required to inspect video metadata.")

        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-i", str(source_path)],
            capture_output=True,
            text=True,
        )
        stderr = result.stderr or ""
        logger.info(f"[VideoPlanner] Probed {source_path.name}")

        duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
        size_match = re.search(r"Video: .*? (\d{2,5})x(\d{2,5})", stderr)
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)

        if not duration_match or not size_match:
            raise RuntimeError(f"Could not read metadata from {source_path.name}")

        hours, minutes, seconds = duration_match.groups()
        duration_sec = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        width, height = size_match.groups()

        return {
            "duration_sec": round(duration_sec, 2),
            "width": int(width),
            "height": int(height),
            "fps": float(fps_match.group(1)) if fps_match else None,
        }

    def _load_sidecar_transcript(self, source_path: Path):
        for ext in (".txt", ".srt", ".vtt"):
            candidate = source_path.with_suffix(ext)
            if candidate.exists():
                text = candidate.read_text(encoding="utf-8", errors="ignore")
                lines = self._normalize_transcript_lines(text)
                return candidate, lines
        return None, []

    def _normalize_transcript_lines(self, text: str) -> List[str]:
        cleaned = re.sub(r"\r\n?", "\n", text)
        cleaned = re.sub(r"\d+\n\d{2}:\d{2}:\d{2}.*?\n", "", cleaned)
        lines = [re.sub(r"\s+", " ", line).strip() for line in cleaned.splitlines()]
        return [line for line in lines if len(line) > 20 and not line.isdigit()]

    def _build_highlights(self, transcript_lines: List[str]) -> List[str]:
        if not transcript_lines:
            return []
        highlights: List[str] = []
        for line in transcript_lines:
            normalized = line.strip(" -")
            if normalized and normalized not in highlights:
                highlights.append(normalized[:180])
            if len(highlights) == 5:
                break
        return highlights

    def _build_clip_plan(
        self,
        duration_sec: float,
        transcript_lines: List[str],
        highlights: List[str],
        max_clip_seconds: int,
    ) -> List[VideoClipSuggestion]:
        clip_size = max(15, min(max_clip_seconds, 45))
        clip_count = max(1, math.ceil(duration_sec / clip_size))
        summary_pool = (
            highlights
            or transcript_lines
            or ["No transcript yet. Run ASR before generating review clips."]
        )
        clips: List[VideoClipSuggestion] = []

        for idx in range(clip_count):
            start_sec = round(idx * duration_sec / clip_count, 2)
            end_sec = round(min(duration_sec, (idx + 1) * duration_sec / clip_count), 2)
            duration = round(end_sec - start_sec, 2)
            summary = summary_pool[idx % len(summary_pool)]
            subtitles = (
                transcript_lines[idx * 2 : idx * 2 + 2] if transcript_lines else []
            )
            clips.append(
                VideoClipSuggestion(
                    index=idx,
                    start_sec=start_sec,
                    end_sec=end_sec,
                    duration_sec=duration,
                    summary=summary,
                    hook=self._build_hook(summary, idx),
                    subtitles=subtitles,
                )
            )

        return clips

    def _build_hook(self, summary: str, idx: int) -> str:
        prefixes = [
            "Open with the strongest moment",
            "Keep this beat for the short",
            "Use this as the review pivot",
            "Good closing beat for CTA",
        ]
        return f"{prefixes[idx % len(prefixes)]}: {summary[:90]}"

    def _target_size(self, orientation: VideoOrientation):
        if orientation == VideoOrientation.HORIZONTAL:
            return 1920, 1080
        return 1080, 1920

    def _build_notes(
        self,
        source_path: Path,
        transcript_path: Optional[Path],
        orientation: VideoOrientation,
    ) -> List[str]:
        notes = [
            f"Source video: {source_path.name}",
            f"Preferred output orientation: {orientation.value}",
            "Recommended pipeline: ASR -> summarize -> pick highlights -> crop/reframe -> render subtitles -> export.",
            "Vertical output should be the default for short-form platforms.",
        ]
        if transcript_path:
            notes.append(f"Found transcript sidecar: {transcript_path.name}")
        else:
            notes.append(
                "No transcript sidecar found. A production pipeline should add ASR fallback."
            )
        return notes
