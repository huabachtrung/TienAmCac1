"""Video Edit Agent Team — coordinated specialist agents for video post-production.

Architecture:
    DirectorAgent orchestrates: Transcriber → SceneAnalyst → Editor →
    SoundDesigner + CaptionWriter → QualityGate → Renderer

Each agent has a single responsibility and communicates via AgentMessage.
QualityGate can reject and trigger retry (max 2).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from ..config import settings, SFX_TRIGGER_MAP
    from ..models.schemas import VideoOrientation
except ImportError:
    from config import settings, SFX_TRIGGER_MAP
    from models.schemas import VideoOrientation


# ── Data structures ──────────────────────────────────────────────────────

class AgentRole(str, Enum):
    DIRECTOR = "DirectorAgent"
    TRANSCRIBER = "TranscriberAgent"
    SCENE_ANALYST = "SceneAnalystAgent"
    EDITOR = "EditorAgent"
    SOUND_DESIGNER = "SoundDesignerAgent"
    CAPTION_WRITER = "CaptionWriterAgent"
    QUALITY_GATE = "QualityGateAgent"
    RENDERER = "RenderAgent"


class AgentStatus(str, Enum):
    OK = "ok"
    FAIL = "fail"
    RETRY = "retry"


@dataclass
class AgentMessage:
    from_agent: AgentRole
    to_agent: AgentRole
    status: AgentStatus = AgentStatus.OK
    payload: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class EditPipelineContext:
    """Shared mutable state passed through the agent pipeline."""
    job_id: str
    source_path: Optional[Path] = None
    source_meta: Dict[str, float] = field(default_factory=dict)
    orientation: VideoOrientation = VideoOrientation.VERTICAL
    style: str = "creator_viral"
    transcript: str = ""
    transcript_segments: List[Dict] = field(default_factory=list)
    transcript_language: Optional[str] = None
    scene_annotations: List[Dict] = field(default_factory=list)
    audio_analysis: Dict[str, Any] = field(default_factory=dict)
    edit_plan: Dict[str, Any] = field(default_factory=dict)
    quality_report: Dict[str, Any] = field(default_factory=dict)
    agent_log: List[Dict[str, str]] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2

    def log(self, agent: AgentRole, message: str, status: str = "ok"):
        entry = {"agent": agent.value, "message": message, "status": status}
        self.agent_log.append(entry)
        log_fn = logger.info if status == "ok" else logger.warning
        log_fn(f"[{agent.value}] {message}")


# ── Agent implementations ────────────────────────────────────────────────

class TranscriberAgent:
    """Transcribe source video with word-level timestamps."""

    role = AgentRole.TRANSCRIBER

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Starting transcription...")
        try:
            result = engine.transcribe(ctx.source_path)
            ctx.transcript = str(result.get("transcript", ""))
            ctx.transcript_segments = list(result.get("segments", []))
            ctx.transcript_language = result.get("language")
            ctx.log(self.role, f"Transcribed {len(ctx.transcript_segments)} segments, "
                               f"lang={ctx.transcript_language}")
            return AgentMessage(self.role, AgentRole.SCENE_ANALYST)
        except Exception as exc:
            ctx.log(self.role, f"Transcription failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])


class SceneAnalystAgent:
    """Analyze audio energy and classify scenes per segment."""

    role = AgentRole.SCENE_ANALYST

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Analyzing audio energy and scene types...")
        try:
            duration = float(ctx.source_meta.get("duration_sec", 1.0))
            ctx.audio_analysis = engine.analyze_audio(ctx.source_path, duration)
            ctx.scene_annotations = self._classify_scenes(ctx.transcript_segments,
                                                           ctx.audio_analysis)
            beats = len(ctx.audio_analysis.get("beats", []))
            peaks = len(ctx.audio_analysis.get("energy_peaks", []))
            ctx.log(self.role, f"Found {beats} beats, {peaks} energy peaks, "
                               f"{len(ctx.scene_annotations)} scene annotations")
            return AgentMessage(self.role, AgentRole.EDITOR)
        except Exception as exc:
            ctx.log(self.role, f"Scene analysis failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])

    def _classify_scenes(self, segments: List[Dict],
                         audio: Dict) -> List[Dict]:
        """Rule-based scene classification per transcript segment."""
        peaks_set = {p.get("time", -1) for p in audio.get("energy_peaks", [])}
        annotations = []
        for seg in segments:
            start = float(seg.get("start", 0))
            text = str(seg.get("text", "")).lower()
            near_peak = any(abs(start - t) < 2.0 for t in peaks_set)
            scene_type = "dialog"
            if near_peak:
                scene_type = "action"
            elif any(k in text for k in ("hỏi", "nói", "đáp", "trả lời")):
                scene_type = "dialog"
            elif any(k in text for k in ("đánh", "chiến", "nổ", "chém")):
                scene_type = "action"
            elif any(k in text for k in ("yên tĩnh", "lặng", "ngủ")):
                scene_type = "quiet"
            annotations.append({
                "start": seg.get("start"),
                "end": seg.get("end"),
                "scene_type": scene_type,
                "near_peak": near_peak,
            })
        return annotations


class EditorAgent:
    """Build the edit decision list (captions, popups, icons, camera, transitions)."""

    role = AgentRole.EDITOR

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Building edit decision list...")
        try:
            plan = engine.build_edit_plan(
                transcript=ctx.transcript,
                transcript_segments=ctx.transcript_segments,
                audio_analysis=ctx.audio_analysis,
                source_meta=ctx.source_meta,
                orientation=ctx.orientation,
                style=ctx.style,
            )
            ctx.edit_plan = plan
            caps = len(plan.get("captions", []))
            pops = len(plan.get("text_popups", []))
            icons = len(plan.get("icons", []))
            ctx.log(self.role, f"Edit plan: {caps} captions, {pops} popups, {icons} icons")
            return AgentMessage(self.role, AgentRole.SOUND_DESIGNER)
        except Exception as exc:
            ctx.log(self.role, f"Edit plan failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])


class SoundDesignerAgent:
    """Design audio post-production: BGM selection and SFX placement."""

    role = AgentRole.SOUND_DESIGNER

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Designing audio post-production...")
        try:
            audio_section = ctx.edit_plan.get("audio", {})
            if not isinstance(audio_section, dict):
                audio_section = {}

            # Validate and enhance BGM
            bgm = audio_section.get("bgm", "bgm_ambient_light.mp3")
            bgm_path = settings.BGM_DIR / str(bgm)
            if not bgm_path.exists():
                bgm = "bgm_ambient_light.mp3"
                ctx.log(self.role, f"BGM '{audio_section.get('bgm')}' not found, using default")

            # Validate SFX — remove missing files
            valid_sfx = []
            removed = 0
            for cue in audio_section.get("sfx", []):
                sfx_file = str(cue.get("file", ""))
                if sfx_file and (settings.SFX_DIR / sfx_file).exists():
                    valid_sfx.append(cue)
                else:
                    removed += 1

            # Add keyword-triggered SFX from transcript
            added_sfx = self._keyword_sfx(ctx.transcript_segments, ctx.source_meta)
            valid_sfx.extend(added_sfx)

            audio_section["bgm"] = bgm
            audio_section["sfx"] = valid_sfx[:80]
            ctx.edit_plan["audio"] = audio_section

            ctx.log(self.role, f"Audio: bgm={bgm}, {len(valid_sfx)} SFX "
                               f"(removed {removed} missing, added {len(added_sfx)} keyword-triggered)")
            return AgentMessage(self.role, AgentRole.CAPTION_WRITER)
        except Exception as exc:
            ctx.log(self.role, f"Sound design failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])

    def _keyword_sfx(self, segments: List[Dict], meta: Dict) -> List[Dict]:
        """Add SFX cues from Vietnamese keyword triggers in transcript."""
        duration = float(meta.get("duration_sec", 1.0))
        cues = []
        for seg in segments[:60]:
            text = str(seg.get("text", "")).lower()
            start = float(seg.get("start", 0))
            if start > duration:
                continue
            for keyword, sfx_file in SFX_TRIGGER_MAP.items():
                if keyword in text and (settings.SFX_DIR / sfx_file).exists():
                    cues.append({
                        "time": round(start, 2),
                        "file": sfx_file,
                        "gain_db": -18,
                    })
                    break  # one SFX per segment
        return cues[:20]


class CaptionWriterAgent:
    """Ensure captions are valid, non-empty, and properly timed."""

    role = AgentRole.CAPTION_WRITER

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Validating and enhancing captions...")
        try:
            captions = ctx.edit_plan.get("captions", [])
            duration = float(ctx.source_meta.get("duration_sec", 1.0))

            # Remove empty captions
            valid = [c for c in captions if str(c.get("text", "")).strip()]

            # Clamp timing
            for cap in valid:
                cap["start"] = max(0.0, min(duration, float(cap.get("start", 0))))
                cap["end"] = max(cap["start"] + 0.2,
                                 min(duration, float(cap.get("end", cap["start"] + 1.0))))

            # Validate popups don't overlap captions excessively
            popups = ctx.edit_plan.get("text_popups", [])
            clean_popups = self._remove_overlapping(popups, valid, duration)

            ctx.edit_plan["captions"] = valid
            ctx.edit_plan["text_popups"] = clean_popups

            ctx.log(self.role, f"Captions: {len(valid)} valid "
                               f"(removed {len(captions) - len(valid)} empty), "
                               f"{len(clean_popups)} popups")
            return AgentMessage(self.role, AgentRole.QUALITY_GATE)
        except Exception as exc:
            ctx.log(self.role, f"Caption writing failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])

    def _remove_overlapping(self, popups: List[Dict], captions: List[Dict],
                            duration: float) -> List[Dict]:
        clean = []
        for p in popups:
            ps = float(p.get("start", 0))
            pe = float(p.get("end", ps + 1.0))
            overlap_count = sum(
                1 for c in captions
                if float(c.get("start", 0)) < pe and float(c.get("end", 0)) > ps
            )
            if overlap_count <= 1:
                p["start"] = max(0.0, min(duration, ps))
                p["end"] = max(p["start"] + 0.2, min(duration, pe))
                clean.append(p)
        return clean


class QualityGateAgent:
    """Critical quality checks before rendering. Can reject and trigger retry."""

    role = AgentRole.QUALITY_GATE

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Running quality checks...")
        errors = []
        warnings = []
        plan = ctx.edit_plan
        duration = float(ctx.source_meta.get("duration_sec", 1.0))

        # Check 1: Captions are required for social vertical content
        captions = plan.get("captions", [])
        if not captions:
            errors.append("QA_NO_CAPTIONS: Edit plan has zero captions — "
                          "video sẽ không có phụ đề")

        # Check 2: All captions must have text
        empty_caps = [i for i, c in enumerate(captions)
                      if not str(c.get("text", "")).strip()]
        if empty_caps:
            errors.append(f"QA_EMPTY_CAPTION: {len(empty_caps)} captions rỗng "
                          f"tại index {empty_caps[:5]}")

        # Check 3: Duration match
        plan_duration = float(plan.get("duration_sec", 0))
        if abs(plan_duration - duration) > 1.0:
            warnings.append(f"QA_DURATION_MISMATCH: plan={plan_duration:.1f}s "
                            f"vs source={duration:.1f}s")

        # Check 4: SFX files must exist
        audio = plan.get("audio", {})
        sfx_list = audio.get("sfx", []) if isinstance(audio, dict) else []
        missing_sfx = [c.get("file") for c in sfx_list
                       if not (settings.SFX_DIR / str(c.get("file", ""))).exists()]
        if missing_sfx:
            errors.append(f"QA_MISSING_SFX: {len(missing_sfx)} SFX files missing: "
                          f"{missing_sfx[:3]}")

        # Check 5: Must have at least BGM or SFX
        has_bgm = bool(audio.get("bgm")) if isinstance(audio, dict) else False
        has_sfx = bool(sfx_list)
        if not has_bgm and not has_sfx:
            warnings.append("QA_NO_AUDIO_POST: No BGM or SFX — video sẽ không có "
                            "nhạc nền hay hiệu ứng")

        # Check 6: Caption timing sanity
        bad_timing = [i for i, c in enumerate(captions)
                      if float(c.get("end", 0)) <= float(c.get("start", 0))]
        if bad_timing:
            errors.append(f"QA_BAD_TIMING: {len(bad_timing)} captions có "
                          f"end <= start tại index {bad_timing[:5]}")

        # Build report
        passed = len(errors) == 0
        ctx.quality_report = {
            "passed": passed,
            "errors": errors,
            "warnings": warnings,
            "checks_run": 6,
            "retry_count": ctx.retry_count,
        }

        if passed:
            ctx.log(self.role, f"✅ PASSED ({len(warnings)} warnings)")
            return AgentMessage(self.role, AgentRole.RENDERER)

        ctx.log(self.role, f"❌ FAILED: {errors}", "fail")
        if ctx.retry_count < ctx.max_retries:
            ctx.retry_count += 1
            ctx.log(self.role, f"Requesting retry {ctx.retry_count}/{ctx.max_retries}")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.RETRY, errors=errors)

        return AgentMessage(
            self.role,
            AgentRole.DIRECTOR,
            AgentStatus.FAIL,
            errors=[
                f"QA failed after {ctx.max_retries} retries: " + "; ".join(errors)
            ],
        )


class RenderAgent:
    """Render the final video using the validated edit plan."""

    role = AgentRole.RENDERER

    def run(self, ctx: EditPipelineContext, engine) -> AgentMessage:
        ctx.log(self.role, "Rendering final video...")
        try:
            edit_plan_path = engine.save_edit_plan(ctx.job_id, ctx.edit_plan)
            output_path, renderer = engine.render(
                job_id=ctx.job_id,
                source_path=ctx.source_path,
                orientation=ctx.orientation,
                edit_plan_path=edit_plan_path,
            )
            if not Path(output_path).exists() or Path(output_path).stat().st_size < 1024:
                raise FileNotFoundError("Rendered video file missing or empty")
            ctx.log(self.role, f"✅ Render complete: {output_path} (renderer={renderer})")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                payload={"output_path": str(output_path),
                                         "renderer": renderer,
                                         "edit_plan_path": str(edit_plan_path)})
        except Exception as exc:
            ctx.log(self.role, f"Render failed: {exc}", "fail")
            return AgentMessage(self.role, AgentRole.DIRECTOR,
                                AgentStatus.FAIL, errors=[str(exc)])


# ── Director Agent ───────────────────────────────────────────────────────

class DirectorAgent:
    """Orchestrates the full video edit pipeline through specialist agents.

    Workflow:
        1. Transcriber → word-level transcript
        2. SceneAnalyst → energy analysis + scene classification
        3. Editor → build edit decision list
        4. SoundDesigner → BGM/SFX post-production
        5. CaptionWriter → validate captions
        6. QualityGate → pass/retry/fail
        7. Renderer → final output

    On QualityGate RETRY, steps 3-6 are re-run (max 2 retries).
    """

    role = AgentRole.DIRECTOR

    def __init__(self):
        self.transcriber = TranscriberAgent()
        self.scene_analyst = SceneAnalystAgent()
        self.editor = EditorAgent()
        self.sound_designer = SoundDesignerAgent()
        self.caption_writer = CaptionWriterAgent()
        self.quality_gate = QualityGateAgent()
        self.renderer = RenderAgent()

    def run(self, ctx: EditPipelineContext, engine,
            progress_callback=None) -> AgentMessage:
        """Execute the full agent pipeline. Returns final message."""
        ctx.log(self.role, f"Starting video edit pipeline for job {ctx.job_id}")

        def _progress(pct: int, stage: str = ""):
            if progress_callback:
                progress_callback(pct, stage)

        # Step 1: Transcribe
        _progress(5, "transcribing")
        msg = self.transcriber.run(ctx, engine)
        if msg.status == AgentStatus.FAIL:
            return self._fail(ctx, msg.errors)

        # Step 2: Scene analysis
        _progress(20, "analyzing_scenes")
        msg = self.scene_analyst.run(ctx, engine)
        if msg.status == AgentStatus.FAIL:
            return self._fail(ctx, msg.errors)

        # Steps 3-6: Edit → Sound → Captions → QA (with retry loop)
        while True:
            _progress(35 + ctx.retry_count * 10, "building_edit_plan")
            msg = self.editor.run(ctx, engine)
            if msg.status == AgentStatus.FAIL:
                return self._fail(ctx, msg.errors)

            _progress(50 + ctx.retry_count * 10, "sound_design")
            msg = self.sound_designer.run(ctx, engine)
            if msg.status == AgentStatus.FAIL:
                return self._fail(ctx, msg.errors)

            _progress(60 + ctx.retry_count * 10, "caption_writing")
            msg = self.caption_writer.run(ctx, engine)
            if msg.status == AgentStatus.FAIL:
                return self._fail(ctx, msg.errors)

            _progress(70 + ctx.retry_count * 5, "quality_check")
            msg = self.quality_gate.run(ctx, engine)

            if msg.status == AgentStatus.RETRY:
                ctx.log(self.role, f"QA retry {ctx.retry_count}/{ctx.max_retries}")
                continue
            elif msg.status == AgentStatus.FAIL:
                return self._fail(ctx, msg.errors)
            else:
                break  # QA passed

        # Step 7: Render
        _progress(80, "rendering")
        msg = self.renderer.run(ctx, engine)
        if msg.status == AgentStatus.FAIL:
            return self._fail(ctx, msg.errors)

        _progress(100, "done")
        ctx.log(self.role, "✅ Pipeline complete!")
        return msg

    def _fail(self, ctx: EditPipelineContext,
              errors: List[str]) -> AgentMessage:
        error_text = "; ".join(errors)
        ctx.log(self.role, f"❌ Pipeline failed: {error_text}", "fail")
        return AgentMessage(
            self.role, AgentRole.DIRECTOR,
            AgentStatus.FAIL,
            errors=errors,
            payload={"quality_report": ctx.quality_report,
                     "agent_log": ctx.agent_log},
        )
