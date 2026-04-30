"""Tests for the Video Edit Agent Team system."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.agents.video_edit_agents import (
    AgentRole,
    AgentStatus,
    CaptionWriterAgent,
    DirectorAgent,
    EditPipelineContext,
    EditorAgent,
    QualityGateAgent,
    SceneAnalystAgent,
    SoundDesignerAgent,
    TranscriberAgent,
)
from backend.config import settings
from backend.models.schemas import VideoOrientation


def _make_ctx(job_id="test-job-001", duration=10.0):
    ctx = EditPipelineContext(
        job_id=job_id,
        source_path=Path("fake_source.mp4"),
        source_meta={"duration_sec": duration, "width": 1920, "height": 1080, "fps": 30.0},
        orientation=VideoOrientation.VERTICAL,
        style="creator_viral",
    )
    return ctx


# ── QualityGateAgent Tests ──────────────────────────────────────────────

class TestQualityGateAgent:
    def test_rejects_empty_captions(self):
        gate = QualityGateAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "duration_sec": 10.0,
            "captions": [],
            "text_popups": [],
            "icons": [],
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": []},
        }
        msg = gate.run(ctx, engine=None)
        assert msg.status in (AgentStatus.RETRY, AgentStatus.FAIL)
        assert any("QA_NO_CAPTIONS" in e for e in ctx.quality_report["errors"])

    def test_rejects_empty_caption_text(self):
        gate = QualityGateAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "duration_sec": 10.0,
            "captions": [{"start": 0.0, "end": 1.0, "text": ""}],
            "text_popups": [],
            "icons": [],
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": []},
        }
        msg = gate.run(ctx, engine=None)
        assert any("QA_EMPTY_CAPTION" in e for e in ctx.quality_report["errors"])

    def test_rejects_missing_sfx(self):
        gate = QualityGateAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "duration_sec": 10.0,
            "captions": [{"start": 0.0, "end": 1.0, "text": "Test caption"}],
            "text_popups": [],
            "icons": [],
            "audio": {
                "bgm": "bgm_ambient_light.mp3",
                "sfx": [{"time": 1.0, "file": "totally_missing_file.wav"}],
            },
        }
        msg = gate.run(ctx, engine=None)
        assert any("QA_MISSING_SFX" in e for e in ctx.quality_report["errors"])

    def test_passes_valid_plan(self):
        gate = QualityGateAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "duration_sec": 10.0,
            "captions": [
                {"start": 0.0, "end": 2.0, "text": "Câu mở đầu"},
                {"start": 2.5, "end": 5.0, "text": "Nội dung chính"},
            ],
            "text_popups": [],
            "icons": [],
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": []},
        }
        msg = gate.run(ctx, engine=None)
        assert ctx.quality_report["passed"] is True
        assert msg.to_agent == AgentRole.RENDERER

    def test_rejects_bad_timing(self):
        gate = QualityGateAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "duration_sec": 10.0,
            "captions": [{"start": 5.0, "end": 3.0, "text": "Bad timing"}],
            "text_popups": [],
            "icons": [],
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": []},
        }
        msg = gate.run(ctx, engine=None)
        assert any("QA_BAD_TIMING" in e for e in ctx.quality_report["errors"])


# ── CaptionWriterAgent Tests ────────────────────────────────────────────

class TestCaptionWriterAgent:
    def test_removes_empty_captions(self):
        writer = CaptionWriterAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "captions": [
                {"start": 0.0, "end": 1.0, "text": "Valid"},
                {"start": 1.5, "end": 2.5, "text": ""},
                {"start": 3.0, "end": 4.0, "text": "   "},
                {"start": 4.5, "end": 5.5, "text": "Also valid"},
            ],
            "text_popups": [],
        }
        msg = writer.run(ctx, engine=None)
        assert msg.status == AgentStatus.OK
        assert len(ctx.edit_plan["captions"]) == 2
        assert all(c["text"].strip() for c in ctx.edit_plan["captions"])

    def test_clamps_timing(self):
        writer = CaptionWriterAgent()
        ctx = _make_ctx(duration=5.0)
        ctx.edit_plan = {
            "captions": [{"start": -1.0, "end": 99.0, "text": "Overflow"}],
            "text_popups": [],
        }
        writer.run(ctx, engine=None)
        cap = ctx.edit_plan["captions"][0]
        assert cap["start"] == 0.0
        assert cap["end"] == 5.0


# ── SceneAnalystAgent Tests ─────────────────────────────────────────────

class TestSceneAnalystAgent:
    def test_classifies_action_near_peaks(self):
        analyst = SceneAnalystAgent()
        segments = [
            {"start": 5.0, "end": 6.0, "text": "something happens"},
        ]
        audio = {"energy_peaks": [{"time": 5.5, "dbfs": -18}], "beats": [], "silences": []}
        annotations = analyst._classify_scenes(segments, audio)
        assert annotations[0]["scene_type"] == "action"
        assert annotations[0]["near_peak"] is True

    def test_classifies_dialog(self):
        analyst = SceneAnalystAgent()
        segments = [
            {"start": 0.0, "end": 2.0, "text": "anh ấy hỏi tôi tại sao"},
        ]
        audio = {"energy_peaks": [], "beats": [], "silences": []}
        annotations = analyst._classify_scenes(segments, audio)
        assert annotations[0]["scene_type"] == "dialog"


# ── DirectorAgent Retry Tests ───────────────────────────────────────────

class TestDirectorAgentRetry:
    def test_retry_on_qa_failure(self):
        """Director should retry when QA fails, up to max_retries."""
        ctx = _make_ctx()
        ctx.max_retries = 1

        # Create a mock engine
        mock_engine = MagicMock()
        mock_engine.transcribe.return_value = {
            "transcript": "test transcript",
            "segments": [{"start": 0, "end": 1, "text": "test", "words": 1}],
            "language": "vi",
        }
        mock_engine.analyze_audio.return_value = {"beats": [], "energy_peaks": [], "silences": []}

        # Make build_edit_plan always return empty captions to trigger QA failure
        mock_engine.build_edit_plan.return_value = {
            "schema_version": 1,
            "style": "creator_viral",
            "orientation": "vertical",
            "duration_sec": 10.0,
            "captions": [],  # Empty → QA will reject
            "text_popups": [],
            "icons": [],
            "camera": [],
            "transitions": [],
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": []},
        }

        director = DirectorAgent()
        result = director.run(ctx, mock_engine)

        # Should have failed after retries
        assert result.status == AgentStatus.FAIL
        # Should have retried at least once
        assert ctx.retry_count >= 1
        # Agent log should contain retry messages
        retry_logs = [l for l in ctx.agent_log if "retry" in l["message"].lower()]
        assert len(retry_logs) >= 1


# ── SoundDesignerAgent Tests ────────────────────────────────────────────

class TestSoundDesignerAgent:
    def test_removes_missing_sfx_files(self):
        designer = SoundDesignerAgent()
        ctx = _make_ctx()
        ctx.edit_plan = {
            "audio": {
                "bgm": "bgm_ambient_light.mp3",
                "sfx": [
                    {"time": 1.0, "file": "nonexistent_sfx.wav"},
                ],
            },
            "captions": [],
        }
        ctx.transcript_segments = []
        msg = designer.run(ctx, engine=None)
        assert msg.status == AgentStatus.OK
        # The nonexistent file should be removed
        remaining = ctx.edit_plan["audio"]["sfx"]
        assert all(
            (settings.SFX_DIR / s["file"]).exists()
            for s in remaining
            if s.get("file")
        )
