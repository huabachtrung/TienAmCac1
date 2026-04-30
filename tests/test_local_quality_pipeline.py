from pathlib import Path

import pytest

from backend.agents.local_voice_engine import SpeechCue, VoiceQualityError
from backend.agents.smart_reframer import SmartReframer
from backend.agents.video_review_engine import VideoReviewEngine
from backend.agents.voice_engine import VoiceEngine
from backend.config import settings
from backend.models.schemas import CharacterType, VideoOrientation


def test_voice_engine_strict_local_does_not_silent_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "VOICE_PROVIDER", "local_f5")

    def fail_synthesis(*args, **kwargs):
        raise VoiceQualityError("missing model")

    engine = VoiceEngine(output_dir=tmp_path)
    monkeypatch.setattr(engine.local_engine, "synthesize_text", fail_synthesis)

    with pytest.raises(VoiceQualityError):
        import asyncio

        asyncio.run(
            engine.generate_text_audio(
                "Xin chào, đây là giọng thử nghiệm.",
                tmp_path / "voice.wav",
                char_type=CharacterType.NARRATOR,
            )
        )

    assert not (tmp_path / "voice.wav").exists()


def test_subtitles_follow_real_speech_cue_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "VIDEO_TEMP_DIR", tmp_path)
    engine = VideoReviewEngine()
    cues = [
        SpeechCue(index=1, text="Câu mở đầu.", start=0.0, end=1.15, path="a.wav"),
        SpeechCue(index=2, text="Câu phân tích dài hơn.", start=1.31, end=3.42, path="b.wav"),
        SpeechCue(index=3, text="Câu kết.", start=3.58, end=4.2, path="c.wav"),
    ]

    subtitle_path = engine.create_subtitles_from_cues("job-sync", cues, 5.0)
    data = subtitle_path.read_text(encoding="utf-8")

    assert "00:00:00,000 --> 00:00:01,150" in data
    assert "00:00:01,310 --> 00:00:03,420" in data
    assert "00:00:03,580 --> 00:00:04,200" in data
    assert "00:00:01,666" not in data


def test_smart_reframer_uses_detected_face_offset(monkeypatch):
    reframer = SmartReframer()
    monkeypatch.setattr(
        reframer,
        "detect_crop_track",
        lambda *args, **kwargs: {
            "mode": "mediapipe_face_track",
            "x_offset": 320,
            "detections": [{"center": 620}],
        },
    )

    filter_complex, crop_info = reframer.build_filter(
        VideoOrientation.VERTICAL,
        Path("source.mp4"),
        0.0,
        4.0,
    )

    assert "x=320" in filter_complex
    assert crop_info["mode"] == "mediapipe_face_track"


def test_strict_review_rejects_generic_no_context(monkeypatch):
    engine = VideoReviewEngine()
    monkeypatch.setattr(settings, "STRICT_QUALITY_MODE", True)

    with pytest.raises(RuntimeError):
        engine.summarize_review_strict("", "video", 45, visual_analysis=None)
