"""Tests for FastAPI endpoints — health, upload, job status."""

import io
from pathlib import Path

import pytest


def test_health_endpoint(app_client):
    response = app_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "Tien Am Cac" in data["service"]


def test_upload_rejects_invalid_type(app_client):
    fake_file = io.BytesIO(b"not a real file")
    response = app_client.post(
        "/api/upload",
        files={"file": ("test.jpg", fake_file, "image/jpeg")},
        data={"start_chapter": "1", "end_chapter": "10"},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]


def test_upload_accepts_txt_file(app_client):
    content = "Chương 1: Test\n\nĐây là nội dung thử nghiệm cho hệ thống."
    fake_file = io.BytesIO(content.encode("utf-8"))
    response = app_client.post(
        "/api/upload",
        files={"file": ("test.txt", fake_file, "text/plain")},
        data={"start_chapter": "1", "end_chapter": "10"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "job_id" in data
    assert data["filename"] == "test.txt"


def test_job_status_404_for_unknown_id(app_client):
    response = app_client.get("/api/jobs/nonexistent-id-12345")
    assert response.status_code == 404


def test_video_review_rejects_no_source(app_client):
    response = app_client.post(
        "/api/video/review",
        data={"orientation": "vertical", "max_duration_sec": "30"},
    )
    assert response.status_code == 400
    assert "file" in response.json()["detail"].lower() or "source" in response.json()["detail"].lower()


def test_video_edit_rejects_no_source(app_client):
    response = app_client.post(
        "/api/video/edit",
        data={"orientation": "vertical", "style": "creator_viral"},
    )
    assert response.status_code == 400


def test_video_review_rejects_bad_orientation(app_client):
    fake_file = io.BytesIO(b"\x00" * 100)
    response = app_client.post(
        "/api/video/review",
        files={"file": ("test.mp4", fake_file, "video/mp4")},
        data={"orientation": "diagonal"},
    )
    assert response.status_code == 400
    assert "Orientation" in response.json()["detail"]


def test_upload_rejects_bad_video_type(app_client):
    fake_file = io.BytesIO(b"\x00" * 100)
    response = app_client.post(
        "/api/video/review",
        files={"file": ("test.gif", fake_file, "image/gif")},
        data={"orientation": "vertical"},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]


def test_download_rejects_unknown_job(app_client):
    response = app_client.get("/api/jobs/unknown-id/download")
    assert response.status_code == 404


def test_voices_endpoint(app_client):
    response = app_client.get("/api/voices")
    assert response.status_code == 200
    data = response.json()
    assert "voices" in data
    assert "voice_map" in data
