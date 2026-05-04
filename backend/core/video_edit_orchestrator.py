"""Background orchestration for automated full-length video editing.

Uses the Agent Team system (DirectorAgent → specialist agents) instead of
calling VideoEditEngine methods directly.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

try:
    from ..agents.video_edit_agents import (
        AgentStatus,
        DirectorAgent,
        EditPipelineContext,
    )
    from ..agents.video_edit_engine import VideoEditEngine
    from ..models.schemas import JobStatus, MediaKind, VideoOrientation
    from .orchestrator import get_job, save_job
except ImportError:
    from agents.video_edit_agents import (
        AgentStatus,
        DirectorAgent,
        EditPipelineContext,
    )
    from agents.video_edit_engine import VideoEditEngine
    from models.schemas import JobStatus, MediaKind, VideoOrientation
    from core.orchestrator import get_job, save_job


def process_video_edit_task(
    job_id: str,
    *,
    local_file_path: str | None = None,
    source_url: str | None = None,
    orientation: str = "vertical",
    style: str = "creator_viral",
    keep_full_video: bool = True,
):
    """Run the AI-assisted video edit pipeline with Agent Team system."""

    job = get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    orientation_value = VideoOrientation(orientation)
    job.media_kind = MediaKind.VIDEO
    job.meta.update(
        {
            "video_task": "edit",
            "source_url": source_url,
            "orientation": orientation_value.value,
            "style": style,
            "keep_full_video": keep_full_video,
        }
    )
    save_job(job)

    try:
        engine = VideoEditEngine()

        # Step 1: Prepare source (before agent pipeline)
        _set_stage(job, JobStatus.PARSING, parsing=10)
        source_path = engine.prepare_source(job_id, source_url, local_file_path)
        meta = engine.probe_video(source_path)
        job.filename = source_path.name
        job.file_path = str(source_path)
        job.meta.update({"source_path": str(source_path), "source_meta": meta})
        _set_stage(job, JobStatus.PARSING, parsing=100)

        # Step 2: Run Agent Team pipeline
        ctx = EditPipelineContext(
            job_id=job_id,
            source_path=source_path,
            source_meta=meta,
            orientation=orientation_value,
            style=style,
        )

        director = DirectorAgent()

        def _progress_callback(pct: int, stage: str = ""):
            """Map agent progress to job stages."""
            if pct <= 20:
                _set_stage(job, JobStatus.ANALYZING, analyzing=pct * 5)
            elif pct <= 70:
                _set_stage(job, JobStatus.ANALYZING, analyzing=100)
            elif pct <= 90:
                _set_stage(job, JobStatus.GENERATING_FX, fx=int((pct - 70) / 20 * 100))
            else:
                _set_stage(job, JobStatus.MIXING, mixing=int((pct - 90) / 10 * 100))

            # Store current agent stage in meta for UI
            job.meta["current_agent_stage"] = stage
            save_job(job)

        result = director.run(ctx, engine, progress_callback=_progress_callback)

        # Store agent log in job meta
        job.meta["agent_log"] = ctx.agent_log
        job.meta["quality_report"] = ctx.quality_report

        if result.status == AgentStatus.FAIL:
            error_detail = "; ".join(result.errors) if result.errors else "Agent pipeline failed"
            # Include quality report in error for UI popup
            job.meta["error_detail"] = {
                "errors": result.errors,
                "quality_report": ctx.quality_report,
                "agent_log": ctx.agent_log[-5:],  # last 5 log entries
            }
            raise RuntimeError(error_detail)

        output_path = result.payload.get("output_path", "")
        renderer = result.payload.get("renderer", "unknown")

        if not Path(output_path).exists() or Path(output_path).stat().st_size < 1024:
            raise FileNotFoundError("Edited video output was not created")

        job.output_path = output_path
        job.meta["renderer"] = renderer
        job.meta["render_metadata"] = engine.last_render_metadata
        job.meta["edit_plan_path"] = result.payload.get("edit_plan_path", "")
        job.meta["edit_plan_summary"] = {
            "captions": len(ctx.edit_plan.get("captions", [])),
            "text_popups": len(ctx.edit_plan.get("text_popups", [])),
            "icons": len(ctx.edit_plan.get("icons", [])),
            "sfx": len((ctx.edit_plan.get("audio") or {}).get("sfx", [])),
        }

        _set_stage(job, JobStatus.DONE, fx=100, mixing=100)
        logger.info(f"[VideoEditOrchestrator] Job {job_id} complete: {output_path}")

    except Exception as exc:
        logger.exception(f"[VideoEditOrchestrator] Job {job_id} failed")
        job.status = JobStatus.FAILED
        job.error = str(exc)
        # Ensure error_detail is available for UI popup
        if "error_detail" not in job.meta:
            job.meta["error_detail"] = {
                "errors": [str(exc)],
                "quality_report": {},
                "agent_log": [],
            }
        save_job(job)
        raise


def run_video_edit_pipeline(
    job_id: str,
    *,
    local_file_path: str | None = None,
    source_url: str | None = None,
    orientation: str = "vertical",
    style: str = "creator_viral",
    keep_full_video: bool = True,
):
    """Backward-compatible entrypoint used by diagnostics and older callers."""
    return process_video_edit_task(
        job_id,
        local_file_path=local_file_path,
        source_url=source_url,
        orientation=orientation,
        style=style,
        keep_full_video=keep_full_video,
    )


def _set_stage(job, status: JobStatus, **progress_updates: int):
    job.status = status
    for key, value in progress_updates.items():
        if key in job.progress:
            job.progress[key] = value
    save_job(job)
