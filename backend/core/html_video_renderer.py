"""Web-based video renderer using Playwright + GSAP.

Renders a GSAP-animated HTML page frame-by-frame with Playwright's
headless Chromium, then pipes frames into FFmpeg for final MP4 output.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _get_ffmpeg() -> str:
    """Resolve the ffmpeg binary path."""
    try:
        from ..audio_utils import get_ffmpeg_binary
        return get_ffmpeg_binary() or "ffmpeg"
    except ImportError:
        return "ffmpeg"


class HtmlVideoRenderer:
    """Renders a video from an HTML/GSAP template + script JSON + audio."""

    def __init__(self, template_dir: str, width: int = 1080, height: int = 1920, fps: int = 30):
        self.template_dir = Path(template_dir)
        self.fps = fps
        self.width = width
        self.height = height

    async def render_video(
        self,
        script_data: dict,
        audio_path: str,
        output_path: str,
        bgm_path: Optional[str] = None,
    ) -> str:
        """Full render pipeline: Playwright screenshot loop → FFmpeg encode.

        Args:
            script_data: The JSON script with scenes, words, duration etc.
            audio_path: Path to the narration WAV/MP3.
            output_path: Destination MP4 path.
            bgm_path: Optional background music file. If provided, it will be
                       mixed underneath the narration with ducking (-20 dB).

        Returns:
            The output_path on success.
        """
        logger.info(f"[HtmlRenderer] Starting render → {output_path}")
        start_time = time.time()

        total_duration = float(script_data.get("duration", 60.0))
        total_frames = int(total_duration * self.fps)

        index_html = self.template_dir / "index.html"
        if not index_html.exists():
            raise FileNotFoundError(f"Template not found: {index_html}")

        ffmpeg_bin = _get_ffmpeg()

        # ── Prepare mixed audio (narration + optional BGM with ducking) ──
        final_audio = audio_path
        if bgm_path and Path(bgm_path).exists():
            final_audio = str(Path(output_path).with_suffix(".mixed.wav"))
            mix_cmd = [
                ffmpeg_bin, "-y",
                "-i", audio_path,
                "-i", bgm_path,
                "-filter_complex",
                # BGM at -20 dB, auto-duck further when narration is loud
                "[1:a]volume=0.1,afade=t=in:d=2,afade=t=out:st=" + str(max(0, total_duration - 3)) + ":d=3[bgm];"
                "[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[out]",
                "-map", "[out]",
                "-ac", "2",
                "-ar", "44100",
                final_audio,
            ]
            logger.info("[HtmlRenderer] Mixing narration + BGM...")
            subprocess.run(mix_cmd, capture_output=True)

        # ── FFmpeg pipe: receive PNG frames on stdin, output MP4 ──────────
        ffmpeg_cmd = [
            ffmpeg_bin, "-y",
            "-loglevel", "warning",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-r", str(self.fps),
            "-i", "-",
            "-i", final_audio,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            output_path,
        ]

        proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        # ── Playwright frame capture ─────────────────────────────────────
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page(
                viewport={"width": self.width, "height": self.height}
            )

            # Windows paths need triple-slash file:/// URLs
            file_url = index_html.absolute().as_uri()
            await page.goto(file_url)

            # Inject script data
            escaped = json.dumps(script_data, ensure_ascii=False)
            await page.evaluate(f"window.loadScript({escaped});")

            # Wait for fonts + images to load
            await page.wait_for_timeout(2000)

            logger.info(f"[HtmlRenderer] Capturing {total_frames} frames @ {self.fps}fps ...")

            for frame_idx in range(total_frames):
                time_sec = frame_idx / self.fps
                await page.evaluate(f"window.goToTime({time_sec});")

                screenshot = await page.screenshot(type="png", animations="disabled")

                try:
                    proc.stdin.write(screenshot)
                except (BrokenPipeError, OSError):
                    logger.error("[HtmlRenderer] FFmpeg pipe broken — aborting render")
                    break

                if frame_idx > 0 and frame_idx % (self.fps * 5) == 0:  # log every 5s
                    elapsed = time.time() - start_time
                    pct = int(frame_idx / total_frames * 100)
                    logger.info(
                        f"[HtmlRenderer] {pct}% ({frame_idx}/{total_frames} frames, {elapsed:.0f}s elapsed)"
                    )

            await browser.close()

        proc.stdin.close()
        proc.wait(timeout=120)

        if proc.returncode != 0:
            stderr = proc.stderr.read().decode(errors="replace")[-500:]
            logger.error(f"[HtmlRenderer] FFmpeg exited {proc.returncode}: {stderr}")
            raise RuntimeError(f"FFmpeg render failed (exit {proc.returncode})")

        # Cleanup temp mixed audio
        if final_audio != audio_path and Path(final_audio).exists():
            Path(final_audio).unlink(missing_ok=True)

        elapsed = time.time() - start_time
        logger.info(f"[HtmlRenderer] ✓ Render complete in {elapsed:.1f}s → {output_path}")
        return output_path
