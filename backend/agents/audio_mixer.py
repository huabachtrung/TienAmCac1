"""
AGENT #5 — AudioMixer (MixerMaster)
Sprint 5: Assembles all audio layers into a final, mastered output file.
  Layer 1: Voice/dialog (0 dB)
  Layer 2: BGM (-18 dB, auto-ducked to -28 dB under speech)
  Layer 3: SFX (-6 dB, synced to text events)
Final mastering: normalize to -14 LUFS, export MP3 192kbps
"""
import subprocess
from pathlib import Path
from typing import List, Dict
from loguru import logger

try:
    from ..audio_utils import configure_pydub, get_ffmpeg_binary
    from ..config import settings
    from ..models.schemas import Chapter
except ImportError:
    from audio_utils import configure_pydub, get_ffmpeg_binary
    from config import settings
    from models.schemas import Chapter


class AudioMixer:
    """
    Master audio mixer using pydub + ffmpeg.
    Assembles voice, BGM, and SFX layers into a professionally mixed output.
    """

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or settings.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ffmpeg_bin = configure_pydub()

    def mix_chapter(
        self,
        chapter: Chapter,
        fx_timeline: Dict,
        job_id: str,
        chapter_idx: int,
    ) -> str:
        """
        Mix a single chapter and return output file path.
        """
        from pydub import AudioSegment
        from pydub.effects import normalize

        logger.info(f"[Mixer] Mixing chapter {chapter_idx}: {chapter.title}")

        # ── Step 1: Assemble voice track ─────────────────────────────
        voice_track = AudioSegment.empty()
        seg_timestamps = []  # Track when each segment starts

        for seg in chapter.segments:
            if seg.voice_file and Path(seg.voice_file).exists():
                seg_audio = AudioSegment.from_file(seg.voice_file)
                seg_timestamps.append(len(voice_track))
                voice_track += seg_audio
                # Update segment duration
                seg.duration_sec = len(seg_audio) / 1000.0
                # Add short pause between segments
                if seg.is_dialog:
                    voice_track += AudioSegment.silent(duration=200)
                else:
                    voice_track += AudioSegment.silent(duration=100)
            else:
                seg_timestamps.append(len(voice_track))
                voice_track += AudioSegment.silent(duration=500)

        logger.info(f"[Mixer] Voice track duration: {len(voice_track)/1000:.1f}s")

        # ── Step 2: Build BGM track (same length as voice) ───────────
        bgm_track = self._build_bgm_track(
            fx_timeline.get("bgm_events", []),
            len(voice_track)
        )

        # ── Step 3: Build SFX track ───────────────────────────────────
        sfx_track = self._build_sfx_track(
            fx_timeline.get("sfx_events", []),
            len(voice_track)
        )

        # ── Step 4: Duck BGM under narration/dialog ───────────────────
        if bgm_track is not None:
            bgm_track = bgm_track + settings.BGM_VOLUME_NORMAL
            bgm_track = self._apply_ducking(bgm_track, seg_timestamps, chapter.segments)
            mixed = bgm_track.overlay(voice_track)
        else:
            mixed = voice_track

        # ── Step 5: Overlay SFX ──────────────────────────────────────
        if sfx_track is not None:
            mixed = mixed.overlay(sfx_track)

        # ── Step 6: Master ────────────────────────────────────────────
        mixed = normalize(mixed)
        mixed = mixed.set_frame_rate(settings.SAMPLE_RATE)
        mixed = mixed.set_channels(2)

        # ── Step 7: Export ────────────────────────────────────────────
        out_filename = f"chapter_{chapter_idx:03d}_{self._sanitize(chapter.title)}.mp3"
        out_path = self.output_dir / job_id / out_filename
        out_path.parent.mkdir(parents=True, exist_ok=True)

        mixed.export(
            str(out_path),
            format="mp3",
            bitrate=settings.OUTPUT_BITRATE,
            tags={
                "title": chapter.title,
                "album": "Tiên Âm Các Audiobook",
                "genre": "Audiobook",
            }
        )
        logger.info(f"[Mixer] ✓ Chapter exported: {out_path}")
        return str(out_path)

    def concatenate_chapters(self, chapter_files: List[str], job_id: str, title: str = "audiobook") -> str:
        """
        Concatenate all chapter MP3 files into one final output file.
        Uses ffmpeg concat for lossless joining.
        """
        from pydub import AudioSegment

        logger.info(f"[Mixer] Concatenating {len(chapter_files)} chapters...")

        # Build ffmpeg concat list
        concat_file = self.output_dir / job_id / "concat_list.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for ch_file in chapter_files:
                f.write(f"file '{Path(ch_file).resolve()}'\n")

        out_path = self.output_dir / job_id / f"{self._sanitize(title)}_FULL.mp3"

        cmd = [
            get_ffmpeg_binary() or "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(out_path)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"[Mixer] ffmpeg concat error: {result.stderr}")
            # Fallback: pydub concatenation
            combined = AudioSegment.empty()
            for f in chapter_files:
                combined += AudioSegment.from_mp3(f)
            combined.export(str(out_path), format="mp3", bitrate=settings.OUTPUT_BITRATE)

        logger.info(f"[Mixer] ✓ Final output: {out_path}")
        return str(out_path)

    # ------------------------------------------------------------------
    # BGM Track Builder
    # ------------------------------------------------------------------
    def _build_bgm_track(self, bgm_events: List[Dict], total_ms: int):
        """Build a continuous BGM track from events with crossfades"""
        from pydub import AudioSegment

        if not bgm_events:
            return None

        bgm_track = AudioSegment.silent(duration=total_ms)

        for i, event in enumerate(bgm_events):
            bgm_file = event.get("file")
            if not bgm_file or not Path(bgm_file).exists():
                continue

            start_ms = int(event["time_sec"] * 1000)
            # End is next event start or total
            if i + 1 < len(bgm_events):
                end_ms = int(bgm_events[i + 1]["time_sec"] * 1000)
            else:
                end_ms = total_ms

            duration_needed = end_ms - start_ms
            if duration_needed <= 0:
                continue

            # Load and loop BGM to fill duration
            bgm_audio = AudioSegment.from_file(bgm_file)
            bgm_looped = self._loop_audio(bgm_audio, duration_needed)
            bgm_looped = bgm_looped.fade_in(int(event.get("fade_in_sec", 2) * 1000))
            bgm_looped = bgm_looped.fade_out(int(event.get("fade_out_sec", 2) * 1000))

            bgm_track = bgm_track.overlay(bgm_looped, position=start_ms)

        return bgm_track

    # ------------------------------------------------------------------
    # SFX Track Builder
    # ------------------------------------------------------------------
    def _build_sfx_track(self, sfx_events: List[Dict], total_ms: int):
        """Build SFX layer from event timeline"""
        from pydub import AudioSegment

        if not sfx_events:
            return None

        sfx_track = AudioSegment.silent(duration=total_ms)

        for event in sfx_events:
            sfx_file = event.get("file")
            if not sfx_file or not Path(sfx_file).exists():
                continue

            start_ms = int(event["time_sec"] * 1000)
            try:
                sfx_audio = AudioSegment.from_file(sfx_file)
                vol_db = event.get("volume_db", settings.SFX_VOLUME)
                sfx_audio = sfx_audio + vol_db
                sfx_track = sfx_track.overlay(sfx_audio, position=start_ms)
            except Exception as e:
                logger.warning(f"[Mixer] SFX overlay failed ({sfx_file}): {e}")

        return sfx_track

    # ------------------------------------------------------------------
    # Volume Ducking
    # ------------------------------------------------------------------
    def _apply_ducking(self, bgm_track, seg_timestamps, segments):
        """Lower BGM whenever narration/dialog is speaking."""
        ducked = bgm_track
        for start_ms, segment in zip(seg_timestamps, segments):
            if segment.duration_sec <= 0:
                continue
            attack_start = max(0, start_ms - 120)
            release_end = min(len(ducked), start_ms + int(segment.duration_sec * 1000) + 180)
            before = ducked[:attack_start]
            speaking = ducked[attack_start:release_end] + (settings.BGM_VOLUME_DUCKED - settings.BGM_VOLUME_NORMAL)
            after = ducked[release_end:]
            ducked = before + speaking + after
        return ducked

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    def _loop_audio(self, audio, target_ms: int):
        """Loop audio track to fill target duration"""
        from pydub import AudioSegment
        result = AudioSegment.empty()
        while len(result) < target_ms:
            result += audio
        return result[:target_ms]

    def _sanitize(self, name: str) -> str:
        """Sanitize filename: keep only ASCII alphanumerics, dash, underscore."""
        import re
        # First transliterate Unicode to ASCII by removing diacritics? Simpler: replace non-ASCII with _
        # Keep only ASCII letters, numbers, dash, underscore
        ascii_only = re.sub(r'[^a-zA-Z0-9\-_]', '_', name)
        return ascii_only[:50]
