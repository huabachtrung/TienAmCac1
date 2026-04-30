"""Tien Am Cac FastAPI application."""

from __future__ import annotations

import mimetypes
import shutil
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

try:
    from .agents.video_review_planner import VideoReviewPlanner
    from .config import settings
    from .core.orchestrator import get_job, process_audiobook_task, save_job
    from .core.video_edit_orchestrator import process_video_edit_task
    from .core.video_orchestrator import process_video_review_task
    from .models.schemas import (
        Job,
        JobStatus,
        JobStatusResponse,
        MediaKind,
        UploadResponse,
        VideoAnalyzeRequest,
        VideoAnalyzeResponse,
    )
except ImportError:
    from agents.video_review_planner import VideoReviewPlanner
    from config import settings
    from core.orchestrator import get_job, process_audiobook_task, save_job
    from core.video_edit_orchestrator import process_video_edit_task
    from core.video_orchestrator import process_video_review_task
    from models.schemas import (
        Job,
        JobStatus,
        JobStatusResponse,
        MediaKind,
        UploadResponse,
        VideoAnalyzeRequest,
        VideoAnalyzeResponse,
    )


for directory in (
    settings.UPLOAD_DIR,
    settings.OUTPUT_DIR,
    settings.VIDEO_SOURCE_DIR,
    settings.VIDEO_OUTPUT_DIR,
    settings.VIDEO_TEMP_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


app = FastAPI(
    title="Tien Am Cac API",
    description="AI audiobook and automated review video backend",
    version="1.3.0",
    docs_url="/api/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    start_chapter: int = Form(1),
    end_chapter: int = Form(9999),
    narrator_gender: str = Form(default="female"),
):
    """Upload a PDF, EPUB, or TXT file to start audiobook generation."""
    allowed_types = {".pdf", ".epub", ".txt"}
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {suffix}. Allowed: {sorted(allowed_types)}",
        )

    # Validate narrator_gender
    narrator_gender = narrator_gender.strip().lower()
    if narrator_gender not in {"male", "female"}:
        narrator_gender = "female"

    job_id = str(uuid.uuid4())
    upload_path = settings.UPLOAD_DIR / job_id
    upload_path.mkdir(parents=True, exist_ok=True)
    file_path = upload_path / (file.filename or f"upload{suffix}")

    with open(file_path, "wb") as handle:
        shutil.copyfileobj(file.file, handle)

    job = Job(
        id=job_id,
        filename=file_path.name,
        file_path=str(file_path),
        media_kind=MediaKind.AUDIO,
        meta={"narrator_gender": narrator_gender},
    )
    save_job(job)
    logger.info(f"[API] Audiobook upload stored at {file_path}")

    # Always run in a background thread — no Celery required.
    thread = threading.Thread(
        target=process_audiobook_task,
        args=(job_id, str(file_path), file_path.name, start_chapter, end_chapter, narrator_gender),
        daemon=True,
    )
    thread.start()
    logger.info(f"[API] Audio job {job_id} started in background thread")

    return UploadResponse(
        job_id=job_id,
        message="Processing started. Track progress at /api/jobs/{job_id}",
        filename=file_path.name,
    )


@app.post("/api/video/review", response_model=UploadResponse)
async def create_video_review(
    file: UploadFile | None = File(default=None),
    source_url: str | None = Form(default=None),
    orientation: str = Form(default="vertical"),
    max_duration_sec: int = Form(default=45),
    style: str = Form(default="review_short"),
):
    """Create a review video from an uploaded source file or supported URL."""
    if not file and not (source_url or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Provide either a video file or a supported source URL.",
        )

    source_url = (source_url or "").strip() or None
    try:
        orientation = orientation.strip().lower()
        if orientation not in {"vertical", "horizontal"}:
            raise ValueError
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Orientation must be vertical or horizontal.",
        ) from exc
    max_duration_sec = max(15, min(int(max_duration_sec), 600))

    job_id = str(uuid.uuid4())
    local_file_path = None
    filename = "remote-video"

    if file:
        suffix = Path(file.filename or "").suffix.lower()
        allowed_video_types = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
        if suffix not in allowed_video_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported video type: {suffix}. Allowed: {sorted(allowed_video_types)}",
            )
        upload_path = settings.UPLOAD_DIR / job_id
        upload_path.mkdir(parents=True, exist_ok=True)
        target_path = upload_path / (file.filename or f"source{suffix}")
        with open(target_path, "wb") as handle:
            shutil.copyfileobj(file.file, handle)
        local_file_path = str(target_path)
        filename = target_path.name

    job = Job(
        id=job_id,
        filename=filename,
        file_path=local_file_path or source_url or "",
        media_kind=MediaKind.VIDEO,
        meta={
            "orientation": orientation,
            "style": style,
            "max_duration_sec": max_duration_sec,
            "source_url": source_url,
        },
    )
    save_job(job)
    logger.info(
        f"[API] Video review job created: {job_id} source={local_file_path or source_url}"
    )

    thread = threading.Thread(
        target=process_video_review_task,
        kwargs={
            "job_id": job_id,
            "local_file_path": local_file_path,
            "source_url": source_url,
            "orientation": orientation,
            "max_duration_sec": max_duration_sec,
            "style": style,
        },
        daemon=True,
    )
    thread.start()

    return UploadResponse(
        job_id=job_id,
        message="Video review generation started. Track progress at /api/jobs/{job_id}",
        filename=filename,
    )


@app.post("/api/video/edit", response_model=UploadResponse)
async def create_video_edit(
    file: UploadFile | None = File(default=None),
    source_url: str | None = Form(default=None),
    orientation: str = Form(default="vertical"),
    style: str = Form(default="creator_viral"),
    keep_full_video: bool = Form(default=True),
):
    """Create a fully post-produced edit from one raw source video."""
    if not file and not (source_url or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Provide either a video file or a supported source URL.",
        )

    source_url = (source_url or "").strip() or None
    orientation = orientation.strip().lower()
    if orientation not in {"vertical", "horizontal"}:
        raise HTTPException(status_code=400, detail="Orientation must be vertical or horizontal.")

    style = (style or "creator_viral").strip().lower()
    if style not in {"creator_viral"}:
        style = "creator_viral"

    job_id = str(uuid.uuid4())
    local_file_path = None
    filename = "remote-video"

    if file:
        suffix = Path(file.filename or "").suffix.lower()
        allowed_video_types = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
        if suffix not in allowed_video_types:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported video type: {suffix}. Allowed: {sorted(allowed_video_types)}",
            )
        upload_path = settings.UPLOAD_DIR / job_id
        upload_path.mkdir(parents=True, exist_ok=True)
        target_path = upload_path / (file.filename or f"source{suffix}")
        with open(target_path, "wb") as handle:
            shutil.copyfileobj(file.file, handle)
        local_file_path = str(target_path)
        filename = target_path.name

    job = Job(
        id=job_id,
        filename=filename,
        file_path=local_file_path or source_url or "",
        media_kind=MediaKind.VIDEO,
        meta={
            "video_task": "edit",
            "orientation": orientation,
            "style": style,
            "keep_full_video": keep_full_video,
            "source_url": source_url,
        },
    )
    save_job(job)
    logger.info(f"[API] Video edit job created: {job_id} source={local_file_path or source_url}")

    thread = threading.Thread(
        target=process_video_edit_task,
        kwargs={
            "job_id": job_id,
            "local_file_path": local_file_path,
            "source_url": source_url,
            "orientation": orientation,
            "style": style,
            "keep_full_video": keep_full_video,
        },
        daemon=True,
    )
    thread.start()

    return UploadResponse(
        job_id=job_id,
        message="Video edit generation started. Track progress at /api/jobs/{job_id}",
        filename=filename,
    )


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Return status for either audiobook or video review jobs."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    download_url = None
    if job.status == JobStatus.DONE and job.output_path:
        output_file = Path(job.output_path)
        if output_file.exists() and output_file.stat().st_size > 0:
            download_url = f"/api/jobs/{job_id}/download"
        else:
            # Output file missing despite DONE status — treat as failed
            logger.error(f"[API] Job {job_id} is DONE but output file missing: {job.output_path}")
            job.status = JobStatus.FAILED
            job.error = f"Output file missing or empty: {job.output_path}"
            save_job(job)

    return JobStatusResponse(
        job_id=job_id,
        status=job.status,
        media_kind=job.media_kind,
        progress=job.progress,
        total_chapters=job.total_chapters,
        processed_chapters=job.processed_chapters,
        error=job.error,
        download_url=download_url,
        output_path=job.output_path,
        meta=job.meta,
    )


@app.get("/api/jobs/{job_id}/download")
async def download_result(job_id: str):
    """Download the generated output file for the job."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.DONE:
        raise HTTPException(
            status_code=400,
            detail=f"Job not ready. Current status: {job.status}",
        )
    if not job.output_path or not Path(job.output_path).exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    if Path(job.output_path).stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Output file is empty")

    media_type, _ = mimetypes.guess_type(job.output_path)
    return FileResponse(
        path=job.output_path,
        media_type=media_type or "application/octet-stream",
        filename=Path(job.output_path).name,
    )


@app.get("/api/voices")
async def list_voices():
    """List available edge-tts Vietnamese voices."""
    try:
        from .agents.voice_engine import VoiceEngine
    except ImportError:
        from agents.voice_engine import VoiceEngine

    engine = VoiceEngine()
    voices = await engine.list_available_voices()
    return {"voices": voices, "voice_map": engine.get_voice_preview_map()}


@app.post("/api/video/analyze", response_model=VideoAnalyzeResponse)
async def analyze_video(request: VideoAnalyzeRequest):
    """Inspect a local asset video and build a review clip plan."""
    try:
        planner = VideoReviewPlanner()
        return planner.analyze(
            asset_path=request.asset_path,
            orientation=request.orientation,
            max_clip_seconds=request.max_clip_seconds,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("[API] Video analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "Tien Am Cac API v1.3.0"}


app.mount(
    "/",
    StaticFiles(directory=Path(__file__).parent / "static", html=True),
    name="static",
)


if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
