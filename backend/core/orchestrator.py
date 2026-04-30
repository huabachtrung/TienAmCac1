"""
TIÊN ÂM CÁC — Pipeline Orchestrator
Tách pipeline thành hàm thuần Python _run_pipeline() để có thể gọi
từ background thread hoặc Celery worker mà không bị lỗi signature.

Job store dùng file JSON (backend/assets/jobs/) thay vì in-memory dict,
để không bị mất data khi uvicorn --reload restart.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Optional

from loguru import logger

try:
    from ..config import settings
    from ..models.schemas import Job, JobStatus, MediaKind
except ImportError:
    from config import settings
    from models.schemas import Job, JobStatus, MediaKind

# ── Celery (optional) ────────────────────────────────────────────────────
try:
    from celery import Celery

    celery_app = Celery(
        "tien_am_cac",
        broker=settings.REDIS_URL,
        backend=settings.REDIS_URL,
    )
    celery_app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="Asia/Ho_Chi_Minh",
        task_track_started=True,
        worker_prefetch_multiplier=1,
    )
except Exception:
    celery_app = None  # type: ignore[assignment]

# ── File-based Job Store ─────────────────────────────────────────────────
_JOB_DIR = settings.OUTPUT_DIR.parent / "jobs"
_JOB_DIR.mkdir(parents=True, exist_ok=True)
_store_lock = threading.Lock()


def _job_path(job_id: str) -> Path:
    return _JOB_DIR / f"{job_id}.json"


def get_job(job_id: str) -> Optional[Job]:
    """Load job from JSON file. Returns None if not found."""
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        with _store_lock:
            data = json.loads(path.read_text(encoding="utf-8"))
        return Job.model_validate(data)
    except Exception as exc:
        logger.error(f"[JobStore] Failed to load job {job_id}: {exc}")
        return None


def save_job(job: Job) -> None:
    """Persist job to JSON file (thread-safe)."""
    path = _job_path(job.id)
    try:
        payload = job.model_dump(mode="json")
        with _store_lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.error(f"[JobStore] Failed to save job {job.id}: {exc}")


# ── Async helpers ────────────────────────────────────────────────────────

def _run_async(coro):
    """
    Safely run an async coroutine from a sync background thread.
    Creates a fresh event loop so we never conflict with uvicorn's loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Core Pipeline ────────────────────────────────────────────────────────

def _run_pipeline(
    job_id: str,
    file_path: str,
    filename: str,
    start_chapter: int = 1,
    end_chapter: int = 9999,
    narrator_gender: str = "female",
) -> None:
    """
    Pure-Python pipeline runner. Safe to call from:
      - background thread  (main.py)
      - Celery worker      (process_audiobook_task wrapper)
    All async agents are driven via _run_async() with isolated event loops.
    """
    try:
        from ..agents.document_parser import DocumentParser
        from ..agents.scene_analyzer import SceneAnalyzer
        from ..agents.voice_engine import VoiceEngine
        from ..agents.audio_fx_engine import AudioFXEngine
        from ..agents.audio_mixer import AudioMixer
    except ImportError:
        from agents.document_parser import DocumentParser
        from agents.scene_analyzer import SceneAnalyzer
        from agents.voice_engine import VoiceEngine
        from agents.audio_fx_engine import AudioFXEngine
        from agents.audio_mixer import AudioMixer

    logger.info(f"[Pipeline] ▶ Starting job {job_id}  chapters {start_chapter}–{end_chapter}")

    # Load job (created by API handler before calling us)
    job = get_job(job_id)
    if job is None:
        from models.schemas import Job, MediaKind
        job = Job(id=job_id, filename=filename, file_path=file_path, media_kind=MediaKind.AUDIO)
        save_job(job)

    try:
        # ── AGENT 1: Document Parser ─────────────────────────────────
        logger.info(f"[Pipeline] Step 1/5 — Parsing document")
        job.status = JobStatus.PARSING
        save_job(job)

        parser = DocumentParser()
        file_suffix = Path(file_path).suffix.lower()
        all_chapters = parser.parse(file_path, start_chapter=start_chapter, end_chapter=end_chapter)

        if file_suffix == ".epub":
            chapters = all_chapters
            if not chapters:
                raise ValueError("Không tìm thấy chương nào trong EPUB (kiểm tra số chương bắt đầu/kết thúc).")
        else:
            start_idx = max(0, start_chapter - 1)
            end_idx = min(len(all_chapters), end_chapter)
            if start_idx >= len(all_chapters) or start_idx >= end_idx:
                raise ValueError(
                    f"Khoảng chương không hợp lệ. File có {len(all_chapters)} chương."
                )
            chapters = all_chapters[start_idx:end_idx]

        if not chapters:
            raise ValueError("Không trích xuất được chương nào từ file.")

        job.total_chapters = len(chapters)
        for ch in chapters:
            parser.split_into_segments(ch)

        job.chapters = chapters
        job.progress["parsing"] = 100
        save_job(job)
        logger.info(f"[Pipeline] ✓ Parsed {len(chapters)} chapters, "
                    f"total segments: {sum(len(c.segments) for c in chapters)}")

        # ── AGENT 2: Scene Analyzer ──────────────────────────────────
        logger.info(f"[Pipeline] Step 2/5 — Scene analysis")
        job.status = JobStatus.ANALYZING
        save_job(job)

        analyzer = SceneAnalyzer()
        for i, chapter in enumerate(chapters):
            chapters[i] = analyzer.analyze_chapter(chapter)
            job.processed_chapters = i + 1
            job.progress["analyzing"] = int((i + 1) / len(chapters) * 100)
            save_job(job)
            logger.info(f"[Pipeline]   Analyzed chapter {i+1}/{len(chapters)}: {chapter.title[:40]}")

        # ── AGENT 3: Voice Engine ────────────────────────────────────
        logger.info(f"[Pipeline] Step 3/5 — TTS voice generation")
        job.status = JobStatus.GENERATING_VOICE
        save_job(job)

        voice_engine = VoiceEngine(narrator_gender=narrator_gender)
        for i, chapter in enumerate(chapters):
            # Create progress callback for this chapter
            def make_progress_callback(chapter_idx, total_segs):
                total_chapter_segs = len(chapters[chapter_idx].segments)
                def update(percent):
                    # Calculate overall voice progress considering chapters done + current chapter
                    chapters_done = chapter_idx
                    current_chapter_progress = percent / 100.0
                    overall = (chapters_done + current_chapter_progress) / len(chapters) * 100
                    job.progress["voice"] = min(100, int(overall))
                    save_job(job)
                return update

            voice_files = voice_engine.generate_chapter_audio(
                chapter,
                job_id,
                chapter_idx=i,
                progress_callback=make_progress_callback(i, len(chapter.segments))
            )

            # Assign generated audio files to segments and calculate durations
            from pydub import AudioSegment
            for seg_idx, audio_path in enumerate(voice_files):
                if seg_idx < len(chapter.segments):
                    chapter.segments[seg_idx].voice_file = str(audio_path)
                    # Calculate actual duration
                    try:
                        audio = AudioSegment.from_file(str(audio_path))
                        chapter.segments[seg_idx].duration_sec = len(audio) / 1000.0
                    except Exception as e:
                        logger.warning(f"[Pipeline] Could not get duration for {audio_path}: {e}")
                        # Estimate: ~3 chars per second as fallback
                        chapter.segments[seg_idx].duration_sec = len(chapter.segments[seg_idx].text) / 3.0

            voice_ok = sum(1 for s in chapter.segments if s.voice_file)
            logger.info(
                f"[Pipeline]   Voice ch {i+1}/{len(chapters)}: "
                f"{voice_ok}/{len(chapter.segments)} segments OK"
            )
            job.progress["voice"] = int((i + 1) / len(chapters) * 100)
            save_job(job)

        total_voice = sum(1 for c in chapters for s in c.segments if s.voice_file)
        total_segs = sum(len(c.segments) for c in chapters)
        logger.info(f"[Pipeline] ✓ Voice done: {total_voice}/{total_segs} segments have audio")

        # ── AGENT 4: Audio FX Engine ─────────────────────────────────
        logger.info(f"[Pipeline] Step 4/5 — BGM/SFX preparation")
        job.status = JobStatus.GENERATING_FX
        save_job(job)

        fx_engine = AudioFXEngine()
        fx_timelines = []
        for i, chapter in enumerate(chapters):
            assets = _run_async(fx_engine.prepare_chapter_assets(chapter))
            timeline = fx_engine.build_fx_timeline(chapter, assets)
            fx_timelines.append(timeline)
            job.progress["fx"] = int((i + 1) / len(chapters) * 100)
            save_job(job)

        _run_async(fx_engine.close())

        # ── AGENT 5: Mixer ────────────────────────────────────────────
        logger.info(f"[Pipeline] Step 5/5 — Mixing & mastering")
        job.status = JobStatus.MIXING
        save_job(job)

        mixer = AudioMixer()
        chapter_files = []
        for i, (chapter, timeline) in enumerate(zip(chapters, fx_timelines)):
            ch_file = mixer.mix_chapter(chapter, timeline, job_id, i)
            chapter_files.append(ch_file)
            job.progress["mixing"] = int((i + 1) / len(chapters) * 100)
            save_job(job)
            logger.info(f"[Pipeline]   Mixed chapter {i+1}/{len(chapters)}: {ch_file}")

        # Concatenate into one final file
        title = Path(filename).stem
        final_path = mixer.concatenate_chapters(chapter_files, job_id, title)

        # ── Verify output actually exists ────────────────────────────
        final = Path(final_path)
        if not final.exists() or final.stat().st_size < 1024:
            raise RuntimeError(
                f"Output file missing or too small: {final_path} "
                f"(size={final.stat().st_size if final.exists() else 'N/A'})"
            )

        job.output_path = final_path
        job.status = JobStatus.DONE
        save_job(job)
        logger.info(f"[Pipeline] ✅ Job {job_id} DONE → {final_path} "
                    f"({final.stat().st_size / 1024:.1f} KB)")

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[Pipeline] ❌ Job {job_id} FAILED: {tb}")
        job = get_job(job_id) or job
        job.status = JobStatus.FAILED
        job.error = f"{exc}\n\nTraceback:\n{tb}"
        save_job(job)


# ── Celery Task (optional wrapper) ───────────────────────────────────────

def process_audiobook_task(
    job_id: str,
    file_path: str,
    filename: str,
    start_chapter: int = 1,
    end_chapter: int = 9999,
    narrator_gender: str = "female",
) -> dict:
    """
    Entry-point called by both:
      - background thread in main.py  (called directly)
      - Celery worker                 (called via .delay() if available)
    """
    try:
        _run_pipeline(job_id, file_path, filename, start_chapter, end_chapter, narrator_gender)
        job = get_job(job_id)
        return {"status": job.status if job else "unknown"}
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"[Pipeline] Job {job_id} failed: {tb}")
        job = get_job(job_id)
        if job:
            job.status = JobStatus.FAILED
            job.error = f"{exc}\n\nTraceback:\n{tb}"
            save_job(job)
        raise
