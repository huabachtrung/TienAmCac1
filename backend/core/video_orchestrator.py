"""Background orchestration for automated review video generation."""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

try:
    from ..agents.video_review_engine import VideoReviewEngine
    from ..models.schemas import JobStatus, MediaKind, VideoOrientation
    from .orchestrator import get_job, save_job
except ImportError:
    from agents.video_review_engine import VideoReviewEngine
    from models.schemas import JobStatus, MediaKind, VideoOrientation
    from core.orchestrator import get_job, save_job


def process_video_review_task(
    job_id: str,
    *,
    local_file_path: str | None = None,
    source_url: str | None = None,
    orientation: str = "vertical",
    max_duration_sec: int = 45,
    style: str = "review_short",
):
    """Run the video review pipeline and keep job status updated."""

    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    orientation_value = VideoOrientation(orientation)
    job.media_kind = MediaKind.VIDEO
    job.meta.update(
        {
            "source_url": source_url,
            "orientation": orientation_value.value,
            "style": style,
            "max_duration_sec": max_duration_sec,
        }
    )
    save_job(job)

    try:
        _set_stage(job, JobStatus.PARSING, parsing=15)
        engine = VideoReviewEngine()

        source_path = engine.prepare_source(job_id, source_url, local_file_path)
        meta = engine.probe_video(source_path)
        job.filename = source_path.name
        job.file_path = str(source_path)
        job.meta.update({"source_path": str(source_path), "source_meta": meta})
        _set_stage(job, JobStatus.ANALYZING, parsing=100, analyzing=15)

        transcription = engine.transcribe(source_path)
        job.meta.update(
            {
                "transcript_preview": transcription["transcript"][:400],
                "transcript_language": transcription.get("language"),
            }
        )
        _set_stage(job, JobStatus.ANALYZING, parsing=100, analyzing=100)

        visual_analysis = engine.understanding_engine.analyze(
            source_path,
            meta,
            transcription["transcript"],
        )
        job.meta.update(
            {
                "vision_model": engine.understanding_engine.model,
                "visual_analysis": visual_analysis,
            }
        )

        summary = engine.summarize_review_strict(
            transcription["transcript"],
            source_name=source_path.stem,
            max_duration_sec=max_duration_sec,
            visual_analysis=visual_analysis,
        )
        review_script = engine.build_review_script(summary)
        job.meta.update(
            {
                "review_title": summary.get("title") or source_path.stem,
                "review_script": review_script,
            }
        )
        _set_stage(job, JobStatus.GENERATING_VOICE, voice=20)

        narration, speech_cues = asyncio.run(
            engine.synthesize_review_audio_timeline(job_id, review_script)
        )
        mixed_audio_path = engine.create_review_audio_mix(
            job_id, narration, max_duration_sec=max_duration_sec
        )
        narration_duration_sec = engine._audio_duration_sec(mixed_audio_path)
        subtitles_path = engine.create_subtitles_from_cues(
            job_id, speech_cues, narration_duration_sec
        )
        selected_ranges = engine.select_visual_ranges(
            transcript_segments=transcription["segments"],
            source_duration_sec=float(meta["duration_sec"]),
            narration_duration_sec=min(float(max_duration_sec), narration_duration_sec),
        )
        job.meta.update(
            {
                "selected_ranges": selected_ranges,
                "subtitles_path": str(subtitles_path),
                "audio_mix_path": str(mixed_audio_path),
                "voice_provider": engine.voice_engine.provider,
                "speech_cues": [cue.__dict__ for cue in speech_cues],
            }
        )
        _set_stage(job, JobStatus.GENERATING_VOICE, voice=100)
        _set_stage(job, JobStatus.MIXING, voice=100, mixing=30)

        output_path = engine.render_review_video(
            job_id=job_id,
            source_path=source_path,
            source_title=str(summary.get("title") or source_path.stem),
            orientation=orientation_value,
            selected_ranges=selected_ranges,
            mixed_audio_path=mixed_audio_path,
            subtitles_path=subtitles_path,
        )
        if not Path(output_path).exists():
            raise FileNotFoundError("Rendered review video was not created")

        job.output_path = str(output_path)
        job.meta["rendered_title"] = summary.get("title") or source_path.stem
        job.meta["crop_plan"] = engine.last_crop_plan
        _set_stage(job, JobStatus.DONE, mixing=100)
        logger.info(f"[VideoOrchestrator] Job {job_id} complete: {output_path}")
    except Exception as exc:
        logger.exception(f"[VideoOrchestrator] Job {job_id} failed")
        job.status = JobStatus.FAILED
        job.error = str(exc)
        save_job(job)
        raise


def _set_stage(job, status: JobStatus, **progress_updates: int):
    job.status = status
    for key, value in progress_updates.items():
        if key in job.progress:
            job.progress[key] = value
    save_job(job)
