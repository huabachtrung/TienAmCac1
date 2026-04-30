from pathlib import Path

from backend.agents.video_edit_engine import VideoEditEngine
from backend.models.schemas import VideoOrientation


def test_video_edit_fallback_plan_clamps_cues():
    engine = VideoEditEngine()
    plan = engine.build_edit_plan(
        transcript="",
        transcript_segments=[
            {
                "start": 0.0,
                "end": 1.4,
                "text": "Day la mot doan video thu nghiem rat dang chu y",
                "words": [],
            }
        ],
        audio_analysis={"beats": [{"time": 0.8}, {"time": 3.5}]},
        source_meta={"duration_sec": 4.0},
        orientation=VideoOrientation.VERTICAL,
        style="creator_viral",
    )

    assert plan["schema_version"] == 1
    assert plan["orientation"] == "vertical"
    assert plan["duration_sec"] == 4.0
    assert plan["captions"]
    for group in ("captions", "text_popups", "icons"):
        for cue in plan[group]:
            assert 0 <= cue["start"] <= cue["end"] <= plan["duration_sec"]
    for cue in plan["audio"]["sfx"]:
        assert 0 <= cue["time"] <= plan["duration_sec"]
        assert (Path("backend/assets/sfx") / cue["file"]).exists()


def test_video_edit_sanitize_rejects_missing_sfx():
    engine = VideoEditEngine()
    fallback = engine._fallback_edit_plan(
        transcript_segments=[],
        audio_analysis={},
        source_meta={"duration_sec": 2.0},
        orientation=VideoOrientation.HORIZONTAL,
        style="creator_viral",
    )
    plan = engine._sanitize_edit_plan(
        {
            "captions": [{"start": -1, "end": 99, "text": "hello"}],
            "audio": {"sfx": [{"time": 9, "file": "missing.wav"}]},
        },
        fallback,
        {"duration_sec": 2.0},
        VideoOrientation.HORIZONTAL,
        "creator_viral",
    )

    assert plan["captions"][0]["start"] == 0.0
    assert plan["captions"][0]["end"] == 2.0
    assert plan["audio"]["sfx"] == []
