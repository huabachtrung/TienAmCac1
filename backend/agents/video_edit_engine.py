"""Automated full-length video editing pipeline.

This engine keeps the raw video's full duration and adds post-production:
animated captions, emphasis text, icon/reaction cues, camera motion hints,
BGM/SFX audio sweetening, and a Remotion-first render path with ffmpeg fallback.
"""

from __future__ import annotations

import json
import math
import re
import shlex
import shutil
import subprocess
from html import escape
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger

try:
    from pydub import AudioSegment, effects
except ImportError:
    AudioSegment = None
    effects = None

try:
    from ..audio_utils import get_ffmpeg_binary
    from ..config import settings
    from ..models.schemas import VideoOrientation
    from .video_review_engine import VideoReviewEngine
except ImportError:
    from audio_utils import get_ffmpeg_binary
    from config import settings
    from models.schemas import VideoOrientation
    from video_review_engine import VideoReviewEngine


class VideoEditEngine:
    """Create a polished full-length edit from one raw source video."""

    def __init__(self):
        self.ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
        self.review_engine = VideoReviewEngine()
        self.http = httpx.Client(timeout=12.0)
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.ollama_model = settings.OLLAMA_MODEL
        self.renderer_dir = Path(__file__).resolve().parents[1] / "video_renderer"
        self.last_render_metadata: Dict[str, object] = {}

    def prepare_source(
        self, job_id: str, source_url: Optional[str], local_file_path: Optional[str]
    ) -> Path:
        return self.review_engine.prepare_source(job_id, source_url, local_file_path)

    def probe_video(self, source_path: Path) -> Dict[str, float]:
        return self.review_engine.probe_video(source_path)

    def _check_ollama(self) -> bool:
        """Quick health check for Ollama — returns False if unreachable."""
        try:
            import httpx as _httpx
            r = _httpx.get(
                f"{settings.OLLAMA_BASE_URL}/api/tags",
                timeout=float(settings.OLLAMA_TIMEOUT),
            )
            return r.status_code == 200
        except Exception:
            return False

    def transcribe(self, source_path: Path) -> Dict[str, object]:
        try:
            model = self.review_engine._get_whisper_model()
            segments, info = model.transcribe(
                str(source_path),
                beam_size=1,
                vad_filter=True,
                word_timestamps=True,
            )
            transcript_segments = []
            lines = []
            for seg in segments:
                text = re.sub(r"\s+", " ", seg.text).strip()
                if not text:
                    continue
                words = []
                for word in getattr(seg, "words", None) or []:
                    token = re.sub(r"\s+", " ", word.word).strip()
                    if token:
                        words.append(
                            {
                                "start": float(word.start),
                                "end": float(word.end),
                                "text": token,
                            }
                        )
                transcript_segments.append(
                    {
                        "start": float(seg.start),
                        "end": float(seg.end),
                        "text": text,
                        "words": words,
                        "word_count": len(text.split()),
                    }
                )
                lines.append(text)
            return {
                "language": getattr(info, "language", None),
                "transcript": " ".join(lines).strip(),
                "segments": transcript_segments,
            }
        except Exception as exc:
            logger.warning(f"[VideoEdit] word timestamp transcription failed: {exc}")
            return self.review_engine.transcribe(source_path)

    def analyze_audio(self, source_path: Path, duration_sec: float) -> Dict[str, object]:
        """Detect energetic moments for impact edits without cloud services."""
        if AudioSegment is None:
            return {"beats": [], "silences": [], "energy_peaks": []}

        temp_dir = settings.VIDEO_TEMP_DIR / "_analysis"
        temp_dir.mkdir(parents=True, exist_ok=True)
        wav_path = temp_dir / f"{source_path.stem[:32]}_analysis.wav"
        try:
            self._run_ffmpeg(
                [
                    self.ffmpeg_bin,
                    "-y",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(wav_path),
                ],
                check=True,
            )
            audio = AudioSegment.from_file(str(wav_path))
        except Exception as exc:
            logger.warning(f"[VideoEdit] audio analysis unavailable: {exc}")
            return {"beats": [], "silences": [], "energy_peaks": []}

        window_ms = 500
        peaks = []
        for start_ms in range(0, len(audio), window_ms):
            chunk = audio[start_ms : start_ms + window_ms]
            if chunk.dBFS > -24:
                peaks.append({"time": round(start_ms / 1000.0, 2), "dbfs": round(chunk.dBFS, 2)})
        peaks = peaks[:80]

        silences = []
        quiet_start = None
        for start_ms in range(0, len(audio), window_ms):
            chunk = audio[start_ms : start_ms + window_ms]
            if chunk.dBFS < -42:
                quiet_start = start_ms if quiet_start is None else quiet_start
            elif quiet_start is not None:
                if start_ms - quiet_start >= 900:
                    silences.append(
                        {"start": round(quiet_start / 1000, 2), "end": round(start_ms / 1000, 2)}
                    )
                quiet_start = None

        beat_step = max(2.0, min(4.0, duration_sec / 12.0))
        beats = [{"time": round(i * beat_step, 2)} for i in range(1, int(duration_sec / beat_step))]
        return {"beats": beats[:40], "silences": silences[:40], "energy_peaks": peaks}

    def build_edit_plan(
        self,
        *,
        transcript: str,
        transcript_segments: List[Dict],
        audio_analysis: Dict[str, object],
        source_meta: Dict[str, float],
        orientation: VideoOrientation,
        style: str = "creator_viral",
    ) -> Dict[str, object]:
        fallback = self._fallback_edit_plan(
            transcript_segments=transcript_segments,
            audio_analysis=audio_analysis,
            source_meta=source_meta,
            orientation=orientation,
            style=style,
        )
        if not transcript.strip():
            return fallback

        # Quick check if Ollama is reachable before expensive call
        if not self._check_ollama():
            logger.warning("[VideoEdit] Ollama not reachable. Using fallback edit plan.")
            return fallback

        prompt = self._edit_plan_prompt(
            transcript=transcript,
            transcript_segments=transcript_segments,
            audio_analysis=audio_analysis,
            source_meta=source_meta,
            orientation=orientation,
            style=style,
        )
        try:
            response = self.http.post(
                self.ollama_url,
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.25, "num_predict": 1400},
                },
                timeout=120.0,
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            plan = self._parse_json(raw)
            if not isinstance(plan, dict):
                raise ValueError("edit plan is not an object")
            return self._sanitize_edit_plan(plan, fallback, source_meta, orientation, style)
        except Exception as exc:
            logger.warning(f"[VideoEdit] Ollama edit plan failed: {exc}. Using fallback.")
            return fallback

    def save_edit_plan(self, job_id: str, edit_plan: Dict[str, object]) -> Path:
        plan_path = settings.VIDEO_TEMP_DIR / job_id / "edit_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(edit_plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan_path

    def render(
        self,
        *,
        job_id: str,
        source_path: Path,
        orientation: VideoOrientation,
        edit_plan_path: Path,
    ) -> tuple[Path, str]:
        output_dir = settings.VIDEO_OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "edited_output.mp4"
        edit_plan = json.loads(edit_plan_path.read_text(encoding="utf-8"))
        renderer_mode = settings.VIDEO_EDIT_RENDERER.lower().strip()

        if renderer_mode not in {"auto", "hyperframes", "ffmpeg", "remotion"}:
            renderer_mode = "auto"

        if renderer_mode in {"auto", "hyperframes"}:
            renderer = self._render_with_hyperframes(
                job_id=job_id,
                source_path=source_path,
                output_path=output_path,
                edit_plan=edit_plan,
            )
            if renderer:
                return output_path, renderer
            if renderer_mode == "hyperframes":
                raise RuntimeError("HyperFrames renderer failed and VIDEO_EDIT_RENDERER=hyperframes")

        if renderer_mode in {"auto", "remotion"}:
            renderer = self._render_with_remotion(
                job_id=job_id,
                source_path=source_path,
                edit_plan_path=edit_plan_path,
                output_path=output_path,
                edit_plan=edit_plan,
            )
            if renderer:
                return output_path, renderer
            if renderer_mode == "remotion":
                raise RuntimeError("Remotion renderer failed and VIDEO_EDIT_RENDERER=remotion")

        fallback_path = self._render_with_ffmpeg(
            job_id=job_id,
            source_path=source_path,
            orientation=orientation,
            edit_plan=edit_plan,
            output_path=output_path,
        )
        return fallback_path, "ffmpeg_fallback"

    def _render_with_hyperframes(
        self,
        *,
        job_id: str,
        source_path: Path,
        output_path: Path,
        edit_plan: Dict[str, object],
    ) -> Optional[str]:
        command = shlex.split(settings.HYPERFRAMES_COMMAND)
        executable = command[0] if command else ""
        resolved = self._resolve_command(executable)
        if not executable or not resolved:
            logger.warning(f"[VideoEdit] HyperFrames command unavailable: {settings.HYPERFRAMES_COMMAND}")
            return None
        command[0] = resolved

        project_dir = settings.VIDEO_TEMP_DIR / job_id / "hyperframes"
        media_dir = project_dir / "media"
        media_dir.mkdir(parents=True, exist_ok=True)
        source_copy = media_dir / f"source{source_path.suffix.lower() or '.mp4'}"
        shutil.copy2(source_path, source_copy)
        audio_mix = self._create_audio_post_mix(job_id, source_path, edit_plan)
        audio_copy = media_dir / "audio_mix.wav"
        shutil.copy2(audio_mix, audio_copy)

        self._write_hyperframes_project(project_dir, source_copy, audio_copy, edit_plan)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        metadata: Dict[str, object] = {
            "hyperframes_project_path": str(project_dir),
            "hyperframes_command": settings.HYPERFRAMES_COMMAND,
            "hyperframes_quality": settings.HYPERFRAMES_QUALITY,
        }
        try:
            lint = self._run_process([*command, "lint"], cwd=project_dir, timeout=120)
            metadata["hyperframes_lint"] = lint["stderr"] or lint["stdout"]
            if lint["returncode"] != 0:
                raise RuntimeError(f"HyperFrames lint failed: {metadata['hyperframes_lint'][:1000]}")

            inspect = self._run_process([*command, "inspect", "--json"], cwd=project_dir, timeout=240)
            metadata["hyperframes_inspect"] = inspect["stderr"] or inspect["stdout"]
            if inspect["returncode"] != 0:
                raise RuntimeError(f"HyperFrames inspect failed: {metadata['hyperframes_inspect'][:1000]}")

            render = self._run_process(
                [
                    *command,
                    "render",
                    "--output",
                    str(output_path),
                    "--quality",
                    settings.HYPERFRAMES_QUALITY,
                ],
                cwd=project_dir,
                timeout=2400,
            )
            metadata["hyperframes_render"] = render["stderr"] or render["stdout"]
            if render["returncode"] != 0:
                raise RuntimeError(f"HyperFrames render failed: {metadata['hyperframes_render'][:1000]}")
            if not output_path.exists() or output_path.stat().st_size < 1024:
                raise FileNotFoundError("HyperFrames did not create a valid output video")
            self.last_render_metadata = metadata
            return "hyperframes"
        except Exception as exc:
            self.last_render_metadata = {**metadata, "hyperframes_error": str(exc)}
            logger.warning(f"[VideoEdit] HyperFrames renderer failed: {exc}")
            return None

    def _write_hyperframes_project(
        self,
        project_dir: Path,
        source_copy: Path,
        audio_copy: Path,
        edit_plan: Dict[str, object],
    ) -> None:
        duration = float(edit_plan.get("duration_sec") or 1.0)
        vertical = str(edit_plan.get("orientation", "vertical")) != "horizontal"
        width, height = (1080, 1920) if vertical else (1920, 1080)
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "DESIGN.md").write_text(
            "\n".join(
                [
                    "## Style Prompt",
                    "Clean creator-video edit with high contrast captions, restrained dark overlays, and fast readable emphasis.",
                    "## Colors",
                    "#05070A background, #FFFFFF primary text, #FACC15 emphasis, #38BDF8 accent, #111827 outline.",
                    "## Typography",
                    "Arial or system sans-serif, bold caption typography.",
                    "## What NOT to Do",
                    "Do not obscure the source subject. Do not use decorative gradients. Do not allow captions to leave frame.",
                ]
            ),
            encoding="utf-8",
        )

        video_rel = source_copy.relative_to(project_dir).as_posix()
        audio_rel = audio_copy.relative_to(project_dir).as_posix()
        caption_nodes = self._hyperframes_caption_nodes(edit_plan)
        popup_nodes = self._hyperframes_popup_nodes(edit_plan)
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tien Am Cac Edit</title>
  <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
</head>
<body>
  <div data-composition-id="tienamcac-edit" data-start="0" data-duration="{duration:.3f}" data-width="{width}" data-height="{height}">
    <video id="source-video" data-start="0" data-duration="{duration:.3f}" data-track-index="0" src="{escape(video_rel)}" muted playsinline></video>
    <audio id="audio-mix" data-start="0" data-duration="{duration:.3f}" data-track-index="1" src="{escape(audio_rel)}" data-volume="1"></audio>
    <div class="shade" data-layout-ignore></div>
{caption_nodes}
{popup_nodes}
    <style>
      html, body {{ margin: 0; background: #05070A; }}
      [data-composition-id="tienamcac-edit"] {{
        position: relative;
        overflow: hidden;
        background: #05070A;
        font-family: Arial, sans-serif;
      }}
      #source-video {{
        position: absolute;
        inset: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        filter: contrast(1.06) saturate(1.08);
      }}
      .shade {{
        position: absolute;
        inset: 0;
        background: linear-gradient(180deg, rgba(0,0,0,.20), transparent 28%, transparent 66%, rgba(0,0,0,.44));
        z-index: 2;
      }}
      .caption {{
        position: absolute;
        left: 7%;
        right: 7%;
        bottom: 8%;
        z-index: 5;
        text-align: center;
        font-size: {58 if vertical else 46}px;
        line-height: 1.1;
        font-weight: 900;
        color: #FFFFFF;
        text-shadow: 0 5px 0 #000, 0 0 22px rgba(0,0,0,.65);
        -webkit-text-stroke: 3px #111827;
      }}
      .popup {{
        position: absolute;
        left: 50%;
        top: 24%;
        z-index: 6;
        transform: translate(-50%, -50%);
        padding: 14px 24px;
        background: rgba(2, 6, 23, .78);
        border: 3px solid #FACC15;
        color: #FACC15;
        font-size: {66 if vertical else 54}px;
        font-weight: 900;
        text-align: center;
        text-shadow: 0 4px 0 #000;
        max-width: 86%;
      }}
    </style>
    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});
      tl.from(".caption", {{ y: 34, opacity: 0, duration: 0.18, stagger: 0.02, ease: "power2.out" }}, 0);
      tl.from(".popup", {{ scale: 0.86, opacity: 0, duration: 0.16, stagger: 0.02, ease: "back.out(1.8)" }}, 0);
      window.__timelines["tienamcac-edit"] = tl;
    </script>
  </div>
</body>
</html>
"""
        (project_dir / "index.html").write_text(html, encoding="utf-8")

    def _hyperframes_caption_nodes(self, edit_plan: Dict[str, object]) -> str:
        nodes = []
        for idx, cue in enumerate(edit_plan.get("captions", [])[:400], start=1):
            start = max(0.0, float(cue.get("start", 0)))
            end = max(start + 0.2, float(cue.get("end", start + 1.0)))
            text = escape(str(cue.get("text", "")).strip()[:140])
            if not text:
                continue
            nodes.append(
                f'    <div id="caption-{idx}" class="caption" data-start="{start:.3f}" '
                f'data-duration="{end - start:.3f}" data-track-index="{idx + 10}">{text}</div>'
            )
        return "\n".join(nodes)

    def _hyperframes_popup_nodes(self, edit_plan: Dict[str, object]) -> str:
        nodes = []
        for idx, cue in enumerate(edit_plan.get("text_popups", [])[:80], start=1):
            start = max(0.0, float(cue.get("start", 0)))
            end = max(start + 0.2, float(cue.get("end", start + 1.0)))
            text = escape(str(cue.get("text", "")).strip()[:42])
            if not text:
                continue
            nodes.append(
                f'    <div id="popup-{idx}" class="popup" data-start="{start:.3f}" '
                f'data-duration="{end - start:.3f}" data-track-index="{idx + 500}">{text}</div>'
            )
        return "\n".join(nodes)

    def _render_with_remotion(
        self,
        job_id: str,
        source_path: Path,
        edit_plan_path: Path,
        output_path: Path,
        edit_plan: Dict[str, object],
    ) -> Optional[str]:
        render_script = self.renderer_dir / "render.js"
        if not render_script.exists():
            return None
        remotion_visual = output_path.with_name("remotion_visual.mp4")
        try:
            cmd = [
                "node",
                str(render_script),
                "--source",
                str(source_path),
                "--plan",
                str(edit_plan_path),
                "--output",
                str(remotion_visual),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if result.returncode == 0 and remotion_visual.exists() and remotion_visual.stat().st_size > 1024:
                audio_mix = self._create_audio_post_mix(job_id, source_path, edit_plan)
                self._run_ffmpeg(
                    [
                        self.ffmpeg_bin,
                        "-y",
                        "-i",
                        str(remotion_visual),
                        "-i",
                        str(audio_mix),
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                        "-c:v",
                        "copy",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "192k",
                        "-shortest",
                        str(output_path),
                    ],
                    check=True,
                )
                return "remotion"
            logger.warning(
                "[VideoEdit] Remotion renderer unavailable, falling back. "
                f"stderr={result.stderr.strip()[:600]}"
            )
        except Exception as exc:
            logger.warning(f"[VideoEdit] Remotion render failed: {exc}")
        return None

    def _render_with_ffmpeg(
        self,
        *,
        job_id: str,
        source_path: Path,
        orientation: VideoOrientation,
        edit_plan: Dict[str, object],
        output_path: Path,
    ) -> Path:
        temp_dir = settings.VIDEO_TEMP_DIR / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        ass_path = self._create_ass_overlays(temp_dir, edit_plan, orientation)
        audio_mix = self._create_audio_post_mix(job_id, source_path, edit_plan)
        duration = float(edit_plan.get("duration_sec") or 1.0)

        vf = self._fallback_video_filter(orientation, ass_path)
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(source_path),
            "-i",
            str(audio_mix),
            "-t",
            str(round(duration, 2)),
            "-filter_complex",
            vf,
            "-map",
            "[vout]",
            "-map",
            "1:a:0",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_ffmpeg(cmd, check=True)
        return output_path

    def _create_audio_post_mix(
        self, job_id: str, source_path: Path, edit_plan: Dict[str, object]
    ) -> Path:
        if AudioSegment is None:
            raise RuntimeError("pydub is required for video edit audio post")

        temp_dir = settings.VIDEO_TEMP_DIR / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        original_wav = temp_dir / "original_audio.wav"
        mixed_wav = temp_dir / "edit_audio_mix.wav"
        duration_ms = int(float(edit_plan.get("duration_sec") or 1.0) * 1000)

        try:
            self._run_ffmpeg(
                [
                    self.ffmpeg_bin,
                    "-y",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "2",
                    "-ar",
                    str(settings.SAMPLE_RATE),
                    str(original_wav),
                ],
                check=True,
            )
            base = AudioSegment.from_file(str(original_wav))
        except Exception:
            base = AudioSegment.silent(duration=duration_ms, frame_rate=settings.SAMPLE_RATE)

        base = base[:duration_ms]
        if len(base) < duration_ms:
            base += AudioSegment.silent(duration=duration_ms - len(base), frame_rate=settings.SAMPLE_RATE)
        try:
            base = effects.compress_dynamic_range(base, threshold=-18.0, ratio=3.0)
            base = effects.normalize(base, headroom=1.5)
        except Exception:
            pass

        mixed = base
        bgm_file = (edit_plan.get("audio") or {}).get("bgm")
        bgm_path = settings.BGM_DIR / str(bgm_file or "bgm_ambient_light.mp3")
        if bgm_path.exists():
            try:
                bgm = AudioSegment.from_file(str(bgm_path))
                while len(bgm) < duration_ms:
                    bgm += bgm
                bgm = bgm[:duration_ms].apply_gain(-29)
                mixed = bgm.overlay(mixed)
            except Exception as exc:
                logger.warning(f"[VideoEdit] BGM mix skipped: {exc}")

        for cue in (edit_plan.get("audio") or {}).get("sfx", [])[:80]:
            sfx_path = settings.SFX_DIR / str(cue.get("file", ""))
            if not sfx_path.exists():
                continue
            try:
                sfx = AudioSegment.from_file(str(sfx_path)).apply_gain(float(cue.get("gain_db", -12)))
                mixed = mixed.overlay(sfx, position=max(0, int(float(cue.get("time", 0)) * 1000)))
            except Exception:
                continue

        mixed.export(str(mixed_wav), format="wav")
        return mixed_wav

    def _create_ass_overlays(
        self, temp_dir: Path, edit_plan: Dict[str, object], orientation: VideoOrientation
    ) -> Path:
        ass_path = temp_dir / "edit_overlays.ass"
        width, height = (1080, 1920) if orientation == VideoOrientation.VERTICAL else (1920, 1080)
        font = "Arial"
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Caption,{font},{54 if orientation == VideoOrientation.VERTICAL else 46},&H00FFFFFF,&H0000E5FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,5,2,2,80,80,{210 if orientation == VideoOrientation.VERTICAL else 90},1",
            f"Style: Popup,{font},{70 if orientation == VideoOrientation.VERTICAL else 58},&H0000F7FF,&H000000FF,&H0010131A,&HAA000000,1,0,0,0,100,100,0,0,1,6,3,5,60,60,60,1",
            f"Style: Icon,{font},{78 if orientation == VideoOrientation.VERTICAL else 68},&H0038BDF8,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,5,2,5,60,60,60,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        for cue in edit_plan.get("captions", [])[:400]:
            start = self._ass_time(float(cue.get("start", 0)))
            end = self._ass_time(float(cue.get("end", 0)) or float(cue.get("start", 0)) + 1.2)
            text = self._ass_escape(self._karaoke_text(cue))
            lines.append(f"Dialogue: 0,{start},{end},Caption,,0,0,0,,{{\\fad(80,80)}}{text}")

        for cue in edit_plan.get("text_popups", [])[:80]:
            start = self._ass_time(float(cue.get("start", 0)))
            end = self._ass_time(float(cue.get("end", 0)) or float(cue.get("start", 0)) + 1.0)
            x, y = self._overlay_position(str(cue.get("position", "upper")), width, height)
            text = self._ass_escape(str(cue.get("text", ""))[:42])
            effect = str(cue.get("effect", "bounce"))
            tag = self._popup_tag(effect, x, y)
            lines.append(f"Dialogue: 1,{start},{end},Popup,,0,0,0,,{tag}{text}")

        for cue in edit_plan.get("icons", [])[:80]:
            start = self._ass_time(float(cue.get("start", 0)))
            end = self._ass_time(float(cue.get("end", 0)) or float(cue.get("start", 0)) + 0.8)
            x, y = self._overlay_position(str(cue.get("position", "side")), width, height)
            icon = self._ass_escape(str(cue.get("icon", "★"))[:4])
            lines.append(
                f"Dialogue: 2,{start},{end},Icon,,0,0,0,,{{\\pos({x},{y})\\fad(60,160)\\t(0,220,\\fscx135\\fscy135)\\t(220,650,\\fscx100\\fscy100)}}{icon}"
            )

        ass_path.write_text("\n".join(lines), encoding="utf-8")
        return ass_path

    def _fallback_video_filter(self, orientation: VideoOrientation, ass_path: Path) -> str:
        ass = ass_path.as_posix().replace(":", "\\:").replace("'", "\\'")
        grade = "eq=contrast=1.08:saturation=1.12:brightness=0.015,unsharp=5:5:0.7"
        if orientation == VideoOrientation.HORIZONTAL:
            return (
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                f"crop=1920:1080,{grade},ass='{ass}'[vout]"
            )
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=12:6[bg];"
            "[0:v]crop=w=ih*9/16:h=ih:x=(iw-ow)/2:y=0,"
            "scale=1080:1920[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,{grade},ass='{ass}'[vout]"
        )

    def _fallback_edit_plan(
        self,
        *,
        transcript_segments: List[Dict],
        audio_analysis: Dict[str, object],
        source_meta: Dict[str, float],
        orientation: VideoOrientation,
        style: str,
    ) -> Dict[str, object]:
        duration = float(source_meta.get("duration_sec") or 1.0)
        captions = self._caption_cues(transcript_segments, duration)
        popups = []
        icons = []
        sfx = []
        for idx, cue in enumerate(captions[:40]):
            text = self._pick_emphasis(cue.get("text", ""))
            if text and idx % 2 == 0:
                popups.append(
                    {
                        "start": cue["start"],
                        "end": min(cue["end"], cue["start"] + 1.2),
                        "text": text.upper(),
                        "effect": ["bounce", "flash", "slide", "shake"][idx % 4],
                        "position": ["upper", "side", "center"][idx % 3],
                    }
                )
            icon = self._icon_for_text(cue.get("text", ""))
            if icon:
                icons.append(
                    {
                        "start": cue["start"] + 0.05,
                        "end": min(cue["end"], cue["start"] + 0.9),
                        "icon": icon,
                        "effect": "pop",
                        "position": "side",
                    }
                )
                sfx.append(
                    {
                        "time": cue["start"],
                        "file": "sfx_rush_whoosh.wav",
                        "gain_db": -18,
                    }
                )

        for beat in audio_analysis.get("beats", [])[:12]:
            t = float(beat.get("time", 0))
            if 0.5 < t < duration - 0.5:
                sfx.append({"time": t, "file": "sfx_punch_impact.wav", "gain_db": -20})

        return {
            "schema_version": 1,
            "style": style,
            "orientation": orientation.value,
            "duration_sec": round(duration, 2),
            "captions": captions,
            "text_popups": popups[:50],
            "icons": icons[:50],
            "camera": self._camera_cues(duration),
            "transitions": self._transition_cues(duration),
            "audio": {"bgm": "bgm_ambient_light.mp3", "sfx": sfx[:60]},
        }

    def _caption_cues(self, transcript_segments: List[Dict], duration: float) -> List[Dict]:
        cues = []
        for seg in transcript_segments:
            start = max(0.0, float(seg.get("start", 0)))
            end = min(duration, float(seg.get("end", start + 1.5)))
            text = re.sub(r"\s+", " ", str(seg.get("text", ""))).strip()
            if not text or end <= start:
                continue
            words = seg.get("words") or []
            if not words:
                pieces = text.split()
                step = max(0.12, (end - start) / max(len(pieces), 1))
                words = [
                    {"start": start + i * step, "end": min(end, start + (i + 1) * step), "text": word}
                    for i, word in enumerate(pieces)
                ]
            cues.append({"start": round(start, 2), "end": round(end, 2), "text": text, "words": words})
        return cues

    def _sanitize_edit_plan(
        self,
        plan: Dict[str, object],
        fallback: Dict[str, object],
        source_meta: Dict[str, float],
        orientation: VideoOrientation,
        style: str,
    ) -> Dict[str, object]:
        duration = float(source_meta.get("duration_sec") or fallback.get("duration_sec") or 1.0)
        # Merge: only use Ollama values when they're non-empty lists/dicts
        clean = dict(fallback)
        for key, value in plan.items():
            if isinstance(value, list) and len(value) == 0:
                continue  # Don't overwrite fallback with empty lists
            if isinstance(value, dict) and not value:
                continue
            clean[key] = value
        clean["schema_version"] = 1
        clean["style"] = style
        clean["orientation"] = orientation.value
        clean["duration_sec"] = round(duration, 2)
        for key in ("captions", "text_popups", "icons"):
            clean[key] = self._clamp_timed_cues(clean.get(key, []), duration)
        # Ensure captions always exist — use fallback if still empty
        if not clean.get("captions") and fallback.get("captions"):
            clean["captions"] = self._clamp_timed_cues(fallback["captions"], duration)
        audio = clean.get("audio") if isinstance(clean.get("audio"), dict) else {}
        audio["bgm"] = audio.get("bgm") or "bgm_ambient_light.mp3"
        audio["sfx"] = self._clamp_sfx(audio.get("sfx", []), duration)
        clean["audio"] = audio
        return clean

    def _clamp_timed_cues(self, cues: object, duration: float) -> List[Dict]:
        clean = []
        for cue in cues if isinstance(cues, list) else []:
            if not isinstance(cue, dict):
                continue
            start = max(0.0, min(duration, float(cue.get("start", 0))))
            end = max(start + 0.2, min(duration, float(cue.get("end", start + 1.0))))
            item = {**cue, "start": round(start, 2), "end": round(end, 2)}
            clean.append(item)
        return clean

    def _clamp_sfx(self, cues: object, duration: float) -> List[Dict]:
        clean = []
        for cue in cues if isinstance(cues, list) else []:
            if not isinstance(cue, dict):
                continue
            filename = str(cue.get("file", ""))
            if not filename or not (settings.SFX_DIR / filename).exists():
                continue
            clean.append(
                {
                    "time": round(max(0.0, min(duration, float(cue.get("time", 0)))), 2),
                    "file": filename,
                    "gain_db": max(-36, min(0, float(cue.get("gain_db", -16)))),
                }
            )
        return clean

    def _edit_plan_prompt(
        self,
        *,
        transcript: str,
        transcript_segments: List[Dict],
        audio_analysis: Dict[str, object],
        source_meta: Dict[str, float],
        orientation: VideoOrientation,
        style: str,
    ) -> str:
        compact_segments = [
            {"start": s.get("start"), "end": s.get("end"), "text": s.get("text", "")[:160]}
            for s in transcript_segments[:80]
        ]
        return f"""Bạn là creative director dựng video ngắn chuyên nghiệp.
Tạo JSON edit_plan cho hậu kỳ toàn bộ video, không cắt ngắn nội dung.

Yêu cầu:
- Chỉ trả JSON, không markdown.
- Style: {style}; orientation: {orientation.value}; duration_sec: {source_meta.get('duration_sec')}.
- Caption phải dựa theo transcript, tiếng Việt nếu transcript là tiếng Việt.
- Text popup ngắn, có lực, không che caption.
- Icon cue dùng một trong: ★, ⚡, !, ✓, 🔥, ?.
- SFX chỉ dùng file có sẵn: sfx_rush_whoosh.wav, sfx_punch_impact.wav, sfx_bell_chime.wav, sfx_big_explosion.wav, sfx_crowd_gasp.wav.
- Không tạo cue ngoài duration.

Schema:
{{
  "captions": [{{"start": 0.0, "end": 1.2, "text": "...", "words": []}}],
  "text_popups": [{{"start": 0.0, "end": 1.0, "text": "...", "effect": "bounce|flash|slide|shake|typewriter", "position": "upper|center|side"}}],
  "icons": [{{"start": 0.0, "end": 0.8, "icon": "⚡", "effect": "pop", "position": "side|upper"}}],
  "camera": [{{"start": 0.0, "end": 2.0, "effect": "punch_zoom|smooth_pan|impact_shake"}}],
  "transitions": [{{"time": 2.0, "effect": "whip|flash|blur|zoom_cut"}}],
  "audio": {{"bgm": "bgm_ambient_light.mp3", "sfx": [{{"time": 1.0, "file": "sfx_rush_whoosh.wav", "gain_db": -16}}]}}
}}

Transcript segments:
{json.dumps(compact_segments, ensure_ascii=False)}

Audio analysis:
{json.dumps(audio_analysis, ensure_ascii=False)[:2500]}

Transcript:
{transcript[:4000]}"""

    def _camera_cues(self, duration: float) -> List[Dict]:
        cues = []
        step = max(4.0, duration / 8.0)
        t = 0.0
        idx = 0
        while t < duration:
            cues.append(
                {
                    "start": round(t, 2),
                    "end": round(min(duration, t + step), 2),
                    "effect": ["smooth_pan", "punch_zoom", "impact_shake"][idx % 3],
                }
            )
            t += step
            idx += 1
        return cues[:20]

    def _transition_cues(self, duration: float) -> List[Dict]:
        step = max(6.0, duration / 6.0)
        return [
            {"time": round(t, 2), "effect": ["whip", "flash", "blur", "zoom_cut"][i % 4]}
            for i, t in enumerate(self._frange(step, duration, step))
        ][:12]

    def _frange(self, start: float, stop: float, step: float):
        value = start
        while value < stop:
            yield value
            value += step

    def _pick_emphasis(self, text: str) -> str:
        words = [w.strip(".,!?;:()[]\"'").lower() for w in text.split()]
        priority = [w for w in words if len(w) >= 5]
        return " ".join(priority[:3]) if priority else ""

    def _icon_for_text(self, text: str) -> str:
        lowered = text.lower()
        if any(k in lowered for k in ("cháy", "hot", "lửa", "đỉnh", "cực")):
            return "🔥"
        if any(k in lowered for k in ("sốc", "bất ngờ", "nguy", "căng")):
            return "!"
        if any(k in lowered for k in ("đúng", "xong", "thành công")):
            return "✓"
        if any(k in lowered for k in ("nhanh", "mạnh", "đánh", "nổ")):
            return "⚡"
        return "★" if len(text.split()) > 8 else ""

    def _overlay_position(self, position: str, width: int, height: int) -> tuple[int, int]:
        if position == "upper":
            return width // 2, int(height * 0.22)
        if position == "side":
            return int(width * 0.78), int(height * 0.36)
        return width // 2, int(height * 0.42)

    def _popup_tag(self, effect: str, x: int, y: int) -> str:
        base = f"\\pos({x},{y})\\fad(60,160)"
        if effect == "shake":
            return f"{{{base}\\t(0,120,\\frz-4)\\t(120,240,\\frz4)\\t(240,380,\\frz0)}}"
        if effect == "flash":
            return f"{{{base}\\t(0,160,\\fscx145\\fscy145)\\t(160,500,\\fscx100\\fscy100)}}"
        if effect == "slide":
            return f"{{\\move({x-120},{y},{x},{y},0,260)\\fad(60,160)}}"
        return f"{{{base}\\t(0,220,\\fscx130\\fscy130)\\t(220,620,\\fscx100\\fscy100)}}"

    def _karaoke_text(self, cue: Dict) -> str:
        words = cue.get("words") or []
        if not words:
            return str(cue.get("text", ""))
        parts = []
        for word in words[:18]:
            dur_cs = max(8, int((float(word.get("end", 0)) - float(word.get("start", 0))) * 100))
            parts.append(f"{{\\k{dur_cs}}}{word.get('text', '')}")
        return " ".join(parts)

    def _ass_escape(self, text: str) -> str:
        return str(text).replace("\\", "\\\\").replace("{", "").replace("}", "").replace("\n", " ")

    def _ass_time(self, seconds: float) -> str:
        cs = int(round(max(0.0, seconds) * 100))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, c = divmod(rem, 100)
        return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"

    def _parse_json(self, raw: str):
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group())
        return json.loads(raw)

    def _run_ffmpeg(self, cmd: List[str], check: bool = False):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg command failed")
        return result

    def _run_process(self, cmd: List[str], cwd: Path, timeout: int) -> Dict[str, object]:
        if cmd:
            resolved = self._resolve_command(cmd[0])
            if resolved:
                cmd = [resolved, *cmd[1:]]
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def _resolve_command(self, executable: str) -> Optional[str]:
        if not executable:
            return None
        if Path(executable).exists():
            return executable
        if shutil.which(executable):
            if executable.lower() not in {"node", "npm", "npx"}:
                return shutil.which(executable)
        if executable.lower() in {"node", "npm", "npx"}:
            for suffix in (".cmd", ".exe", ".bat"):
                found = shutil.which(executable + suffix)
                if found:
                    return found
        return shutil.which(executable)
