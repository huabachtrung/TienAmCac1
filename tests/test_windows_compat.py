"""Windows compatibility tests — pydub config, ffmpeg, path handling."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_configure_pydub_runs_without_error():
    """configure_pydub() should not raise even if ffmpeg is missing."""
    from backend.audio_utils import configure_pydub
    # Should return a string path or None, never raise
    result = configure_pydub()
    assert result is None or isinstance(result, str)


def test_get_ffmpeg_binary():
    """get_ffmpeg_binary() should return a path or None."""
    from backend.audio_utils import get_ffmpeg_binary
    result = get_ffmpeg_binary()
    if result is not None:
        assert isinstance(result, str)
        assert len(result) > 0


def test_settings_load():
    """Settings should load without error on Windows."""
    from backend.config import settings
    assert settings.PORT == 8000
    assert settings.UPLOAD_DIR.is_absolute()
    assert settings.OUTPUT_DIR.is_absolute()


def test_settings_paths_use_absolute():
    """All configured paths should be absolute after resolution."""
    from backend.config import settings
    for attr in ("UPLOAD_DIR", "OUTPUT_DIR", "BGM_DIR", "SFX_DIR",
                 "VIDEO_OUTPUT_DIR", "VIDEO_TEMP_DIR"):
        path = getattr(settings, attr)
        assert path.is_absolute(), f"{attr} is not absolute: {path}"


def test_backend_init_imports():
    """Backend __init__.py should import without crashing."""
    import backend
    # Should have run configure_pydub on import
    assert hasattr(backend, '__file__')


def test_static_files_exist():
    """Static UI files must exist for the web interface."""
    static_dir = ROOT / "backend" / "static"
    assert (static_dir / "index.html").exists()
    assert (static_dir / "styles.css").exists()
    assert (static_dir / "app.js").exists()


def test_error_modal_css_exists():
    """Error modal CSS should be present in styles.css."""
    css = (ROOT / "backend" / "static" / "styles.css").read_text(encoding="utf-8")
    assert "error-modal-overlay" in css
    assert "error-modal" in css
    assert "btn-close-modal" in css


def test_error_modal_js_exists():
    """Error modal JS functions should be present in app.js."""
    js = (ROOT / "backend" / "static" / "app.js").read_text(encoding="utf-8")
    assert "showErrorModal" in js
    assert "closeErrorModal" in js
    assert "error_detail" in js


def test_agents_module_structure():
    """Video edit agents module should be importable."""
    from backend.agents.video_edit_agents import (
        AgentRole,
        AgentStatus,
        DirectorAgent,
        EditPipelineContext,
        QualityGateAgent,
    )
    assert AgentRole.DIRECTOR.value == "DirectorAgent"
    assert AgentRole.QUALITY_GATE.value == "QualityGateAgent"
