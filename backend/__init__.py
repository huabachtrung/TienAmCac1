# Package marker for backend imports.

# Auto-configure pydub with ffmpeg on Windows (prevents ffprobe missing errors)
try:
    from .audio_utils import configure_pydub as _configure_pydub
    _configure_pydub()
except Exception:
    pass
