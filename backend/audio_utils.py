import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger


def get_ffmpeg_binary() -> Optional[str]:
    """
    Resolve an ffmpeg executable. Prefer the system binary, then the
    bundled binary from imageio-ffmpeg if available.
    """
    import shutil

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logger.warning(f"[AudioUtils] ffmpeg unavailable: {exc}")
        return None


def get_ffprobe_binary() -> Optional[str]:
    """Resolve an ffprobe executable using the system or imageio bundle."""
    import shutil

    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe

    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        return None

    # Look for a real ffprobe next to the ffmpeg binary
    for name in ("ffprobe.exe", "ffprobe"):
        candidate = Path(ffmpeg_bin).resolve().with_name(name)
        if candidate.exists():
            return str(candidate)

    return None  # No real ffprobe found


def _ffmpeg_mediainfo(filepath: str, ffmpeg_bin: str) -> dict:
    """Extract media info using ffmpeg -i (when ffprobe is unavailable).

    Parses the stderr output of ``ffmpeg -i <file>`` to extract duration,
    codec, sample rate, and channel information that pydub needs.
    """
    import re

    try:
        res = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-i", str(filepath)],
            capture_output=True, text=True, timeout=15,
        )
        stderr = res.stderr or ""
    except Exception:
        return {"streams": [{}], "format": {"filename": str(filepath)}}

    # Parse duration
    dur_match = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", stderr)
    duration = "0"
    if dur_match:
        h, m, s = dur_match.groups()
        duration = str(int(h) * 3600 + int(m) * 60 + float(s))

    # Parse audio stream info
    audio_match = re.search(
        r"Audio:\s*(\w+).*?(\d+)\s*Hz.*?(\w+).*?(\d+)\s*kb/s", stderr
    )
    stream: dict = {
        "codec_type": "audio",
        "sample_fmt": "s16",
        "bits_per_sample": 16,
    }
    if audio_match:
        codec, sr, channels, bitrate = audio_match.groups()
        stream["codec_name"] = codec
        stream["sample_rate"] = sr
        stream["channels"] = 2 if "stereo" in channels else 1
        stream["bit_rate"] = str(int(bitrate) * 1000)
    else:
        # Minimal fallback for any audio
        audio_basic = re.search(r"Audio:\s*(\w+).*?(\d+)\s*Hz", stderr)
        if audio_basic:
            stream["codec_name"] = audio_basic.group(1)
            stream["sample_rate"] = audio_basic.group(2)
            stream["channels"] = 1

    # Parse format
    ext = Path(filepath).suffix.lstrip(".")
    fmt_name = ext if ext else "mp3"

    return {
        "streams": [stream],
        "format": {
            "filename": str(filepath),
            "format_name": fmt_name,
            "duration": duration,
        },
    }


def configure_pydub() -> Optional[str]:
    """Point pydub to a valid ffmpeg/ffprobe binary when possible.

    On Windows with only imageio-ffmpeg installed (no system ffprobe),
    pydub's mediainfo_json would fail because it tries to call ``ffprobe``
    which doesn't exist.  We fix this by:
      1. Setting AudioSegment.converter / .ffmpeg / .ffprobe
      2. Patching get_prober_name() to return real binary path
      3. Patching mediainfo_json() to use ffmpeg -i when ffprobe is missing
    """
    ffmpeg_bin = get_ffmpeg_binary()
    if not ffmpeg_bin:
        return None

    real_ffprobe = get_ffprobe_binary()  # None if only imageio-ffmpeg
    has_real_ffprobe = real_ffprobe is not None

    # Add the directory to PATH so subprocesses also find the binary
    ffmpeg_dir = str(Path(ffmpeg_bin).resolve().parent)
    current_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in current_path.split(os.pathsep):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path

    try:
        from pydub import AudioSegment
        from pydub import utils as pydub_utils

        AudioSegment.converter = ffmpeg_bin
        AudioSegment.ffmpeg = ffmpeg_bin
        if has_real_ffprobe:
            AudioSegment.ffprobe = real_ffprobe

        # Patch get_prober_name
        if has_real_ffprobe:
            def _patched_get_prober_name():
                return real_ffprobe
        else:
            # No real ffprobe → we'll handle it in mediainfo_json
            def _patched_get_prober_name():
                return ffmpeg_bin

        pydub_utils.get_prober_name = _patched_get_prober_name

        # Patch get_encoder_name
        def _patched_get_encoder_name():
            return ffmpeg_bin
        if hasattr(pydub_utils, "get_encoder_name"):
            pydub_utils.get_encoder_name = _patched_get_encoder_name

        # When no real ffprobe exists, we must also patch mediainfo_json
        # because ffmpeg doesn't support -of json -show_format flags.
        if not has_real_ffprobe:
            def _patched_mediainfo_json(filepath, read_ahead_limit=-1):
                return _ffmpeg_mediainfo(filepath, ffmpeg_bin)

            pydub_utils.mediainfo_json = _patched_mediainfo_json
            
            # CRITICAL: audio_segment.py imports mediainfo_json directly, 
            # so we must patch it there too, otherwise from_file() uses the old one!
            import pydub.audio_segment
            pydub.audio_segment.mediainfo_json = _patched_mediainfo_json

    except Exception as exc:
        logger.warning(f"[AudioUtils] Cannot configure pydub ffmpeg: {exc}")

    return ffmpeg_bin
