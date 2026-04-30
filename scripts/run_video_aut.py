"""End-to-end AUT for automated video review generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.agents.voice_engine import VoiceEngine
from backend.audio_utils import get_ffmpeg_binary

try:
    import av
except ImportError:
    av = None


def main():
    workspace = ROOT / "backend" / "assets" / "video_temp" / "aut"
    workspace.mkdir(parents=True, exist_ok=True)

    source_audio = workspace / "aut_source.wav"
    source_video = workspace / "aut_source.mp4"
    downloaded_output = workspace / "aut_result.mp4"
    log_path = workspace / "aut_server.log"

    create_source_audio(source_audio)
    create_source_video(source_audio, source_video)

    server = start_server(log_path)
    try:
        wait_for_health("http://127.0.0.1:8010/api/health")
        result = run_review_job(source_video, downloaded_output)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        stop_server(server)


def create_source_audio(out_path: Path):
    script = (
        "This clip explains a practical product workflow. "
        "It highlights setup, speed, and the final result. "
        "The review should keep the most useful moments and finish with a short conclusion."
    )
    import asyncio

    asyncio.run(VoiceEngine().generate_text_audio(script, out_path))
    if not out_path.exists():
        raise RuntimeError("Failed to generate AUT source audio")


def create_source_video(audio_path: Path, out_path: Path):
    ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
    font_path = "C\\:/Windows/Fonts/arial.ttf"
    text = "AUT source video for review pipeline".replace(":", "\\:")
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1280x720:rate=30",
        "-i",
        str(audio_path),
        "-vf",
        (
            "drawtext="
            f"fontfile='{font_path}':text='{text}':"
            "fontsize=42:fontcolor=white:box=1:boxcolor=black@0.45:"
            "x=(w-text_w)/2:y=h-120"
        ),
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_path),
    ]
    run_checked(cmd, "Failed to create AUT source video")


def start_server(log_path: Path) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8010",
    ]
    log_handle = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT, cwd=ROOT)


def wait_for_health(url: str, timeout_sec: int = 90):
    deadline = time.time() + timeout_sec
    last_error = None
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"Server did not become healthy: {last_error}")


def run_review_job(source_video: Path, downloaded_output: Path):
    with httpx.Client(base_url="http://127.0.0.1:8010", timeout=60.0) as client:
        with open(source_video, "rb") as handle:
            response = client.post(
                "/api/video/review",
                files={"file": (source_video.name, handle, "video/mp4")},
                data={
                    "orientation": "vertical",
                    "max_duration_sec": "20",
                    "style": "review_short",
                },
            )
        response.raise_for_status()
        payload = response.json()
        job_id = payload["job_id"]

        deadline = time.time() + 360
        last_payload = None
        while time.time() < deadline:
            status_response = client.get(f"/api/jobs/{job_id}")
            status_response.raise_for_status()
            last_payload = status_response.json()
            if last_payload["status"] == "done":
                break
            if last_payload["status"] == "failed":
                raise RuntimeError(f"Video review job failed: {last_payload['error']}")
            time.sleep(2)
        else:
            raise RuntimeError("Timed out waiting for video review job")

        download = client.get(last_payload["download_url"])
        download.raise_for_status()
        downloaded_output.write_bytes(download.content)

    if not downloaded_output.exists() or downloaded_output.stat().st_size < 50_000:
        raise RuntimeError("Downloaded output is missing or too small")

    width, height, duration = probe_video(downloaded_output)
    if height <= width:
        raise RuntimeError(
            f"Expected vertical video output, got {width}x{height} instead"
        )

    return {
        "job_status": last_payload["status"],
        "job_id": job_id,
        "downloaded_output": str(downloaded_output),
        "bytes": downloaded_output.stat().st_size,
        "resolution": f"{width}x{height}",
        "duration_sec": round(duration, 2),
        "meta": last_payload.get("meta", {}),
    }


def probe_video(path: Path):
    if av is None:
        raise RuntimeError("PyAV is required for AUT probe validation")
    with av.open(str(path)) as container:
        stream = next(
            (item for item in container.streams if item.type == "video"), None
        )
        if not stream:
            raise RuntimeError("Output file does not contain a video stream")
        duration = float(container.duration / 1_000_000) if container.duration else 0.0
        return int(stream.width), int(stream.height), duration


def run_checked(cmd: list[str], error_message: str):
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"{error_message}\n{result.stderr}")


def stop_server(server: subprocess.Popen):
    if server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
