"""
End-to-end video review generation pipeline.

Flow:
1. Optional download from source URL with yt-dlp
2. Probe and transcribe source media
3. Summarize transcript into a short review script
4. Generate narration audio with existing VoiceEngine
5. Select visual highlight ranges
6. Render styled horizontal or vertical review video
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger

try:
    import av
    from faster_whisper import WhisperModel
    from pydub import AudioSegment
    from yt_dlp import YoutubeDL
except ImportError:
    av = None
    WhisperModel = None
    AudioSegment = None
    YoutubeDL = None

try:
    from ..audio_utils import get_ffmpeg_binary
    from ..config import settings, VOICE_MAP
    from ..models.schemas import CharacterType, VideoOrientation, VideoReviewResult
    from .local_voice_engine import LocalVoiceEngine, SpeechCue
    from .smart_reframer import SmartReframer
    from .video_understanding_engine import VideoUnderstandingEngine
    from .voice_engine import VoiceEngine
except ImportError:
    from audio_utils import get_ffmpeg_binary
    from config import settings, VOICE_MAP
    from models.schemas import CharacterType, VideoOrientation, VideoReviewResult
    from local_voice_engine import LocalVoiceEngine, SpeechCue
    from smart_reframer import SmartReframer
    from video_understanding_engine import VideoUnderstandingEngine
    from voice_engine import VoiceEngine


class VideoReviewEngine:
    """Creates an edited review video from a local file or a supported URL."""

    def __init__(self):
        self.ffmpeg_bin = get_ffmpeg_binary() or "ffmpeg"
        self.voice_engine = VoiceEngine()
        self.http = httpx.Client(timeout=20.0)
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.ollama_model = settings.OLLAMA_MODEL
        self._whisper_model = None
        self.local_voice_engine = LocalVoiceEngine()
        self.understanding_engine = VideoUnderstandingEngine()
        self.reframer = SmartReframer()
        self.last_crop_plan: List[Dict[str, object]] = []

    def prepare_source(
        self, job_id: str, source_url: Optional[str], local_file_path: Optional[str]
    ) -> Path:
        if source_url:
            return self._download_source(job_id, source_url)
        if local_file_path:
            return Path(local_file_path)
        raise ValueError("Either source_url or local_file_path is required")

    def _download_source(self, job_id: str, source_url: str) -> Path:
        if YoutubeDL is None:
            raise RuntimeError("yt-dlp is not installed")

        class YTDLLogger:
            def debug(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): logger.error(f"[yt-dlp] {msg}")

        target_dir = settings.UPLOAD_DIR / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(target_dir / "%(title).50s [%(id)s].%(ext)s")

        opts = {
            "outtmpl": outtmpl,
            "format": "mp4/bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "updatetime": False,
            "ffmpeg_location": str(self.ffmpeg_bin),
            "logger": YTDLLogger(),
        }

        logger.info(f"[VideoReview] Downloading source URL: {source_url}")
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source_url, download=True)
            downloaded = Path(ydl.prepare_filename(info))

        if downloaded.suffix.lower() != ".mp4":
            maybe_mp4 = downloaded.with_suffix(".mp4")
            if maybe_mp4.exists():
                downloaded = maybe_mp4

        if not downloaded.exists():
            raise FileNotFoundError("Downloaded source file was not found")

        return downloaded

    def probe_video(self, source_path: Path) -> Dict[str, float]:
        if av is None:
            raise RuntimeError("PyAV is required for probing video metadata")

        with av.open(str(source_path)) as container:
            video_stream = next(
                (stream for stream in container.streams if stream.type == "video"), None
            )
            audio_stream = next(
                (stream for stream in container.streams if stream.type == "audio"), None
            )
            if not video_stream:
                raise ValueError("Source does not contain a video stream")

            width = int(video_stream.width or 0)
            height = int(video_stream.height or 0)
            fps = (
                float(video_stream.average_rate) if video_stream.average_rate else 30.0
            )
            if container.duration:
                duration_sec = float(container.duration / 1_000_000)
            elif video_stream.duration and video_stream.time_base:
                duration_sec = float(video_stream.duration * video_stream.time_base)
            else:
                duration_sec = 0.0

        return {
            "duration_sec": round(duration_sec, 2),
            "width": width,
            "height": height,
            "fps": fps,
            "has_audio": bool(audio_stream),
        }

    def transcribe(self, source_path: Path) -> Dict[str, object]:
        if WhisperModel is None:
            raise RuntimeError("faster-whisper is not installed")

        logger.info(f"[VideoReview] Transcribing {source_path.name}")
        model = self._get_whisper_model()
        segments, info = model.transcribe(
            str(source_path),
            beam_size=1,
            vad_filter=True,
            word_timestamps=True,
        )

        transcript_segments = []
        transcript_lines = []
        for seg in segments:
            text = re.sub(r"\s+", " ", seg.text).strip()
            if not text:
                continue
            transcript_segments.append(
                {
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": text,
                    "words": len(text.split()),
                    "word_timings": [
                        {
                            "start": float(word.start),
                            "end": float(word.end),
                            "text": re.sub(r"\s+", " ", word.word).strip(),
                        }
                        for word in (getattr(seg, "words", None) or [])
                        if re.sub(r"\s+", " ", word.word).strip()
                    ],
                }
            )
            transcript_lines.append(text)

        transcript = " ".join(transcript_lines).strip()
        return {
            "language": getattr(info, "language", None),
            "transcript": transcript,
            "segments": transcript_segments,
        }

    def summarize_review(
        self, transcript: str, source_name: str, max_duration_sec: int
    ) -> Dict[str, object]:
        transcript = transcript.strip()
        if not transcript:
            return self._fallback_summary(source_name)

        # Estimate max words for narration (Vietnamese avg ~2.5 syllables/s, ~1.3 words/s)
        max_words = int(max_duration_sec * 1.3)

        prompt = f"""Bạn là chuyên gia viết kịch bản video review ngắn bằng TIẾNG VIỆT.

Yêu cầu bắt buộc:
- Toàn bộ nội dung phải bằng TIẾNG VIỆT, KHÔNG dùng tiếng Anh hay ngôn ngữ khác.
- Tổng số từ trong hook + bullet_points + closing KHÔNG vượt quá {max_words} từ.
- bullet_points tối đa 3 ý, mỗi ý 1 câu ngắn gọn.
- Không thêm markdown, chỉ trả về JSON thuần.

Chỉ trả về JSON với các key sau (CHỈ JSON, không giải thích):
{{
  "title": "<tên video bằng tiếng Việt>",
  "hook": "<câu mở đầu hấp dẫn>",
  "bullet_points": ["<điểm 1>", "<điểm 2>", "<điểm 3>"],
  "closing": "<câu kết thúc>"
}}

Tên nguồn: {source_name}
Nội dung transcript:
{transcript[:3500]}"""

        try:
            response = self.http.post(
                self.ollama_url,
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 400},
                },
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            summary = self._parse_json(raw)
            if not isinstance(summary, dict):
                raise ValueError("Unexpected summary payload")
            normalized = {
                "title": str(summary.get("title") or source_name),
                "hook": str(summary.get("hook") or ""),
                "bullet_points": list(summary.get("bullet_points") or []),
                "closing": str(
                    summary.get("closing") or "Nếu thấy hay thì đừng quên thả tim nhé!"
                ),
            }
            return self._normalize_vietnamese_summary(
                normalized,
                source_name=source_name,
                transcript=transcript,
                max_words=max_words,
            )
        except Exception as exc:
            logger.warning(
                f"[VideoReview] Ollama summary failed: {exc}. Using fallback summary."
            )
            return self._fallback_summary(source_name, transcript, max_words=max_words)

    def summarize_review_strict(
        self,
        transcript: str,
        source_name: str,
        max_duration_sec: int,
        visual_analysis: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        """Create a concrete review script from transcript plus visual observations."""
        transcript = transcript.strip()
        if not transcript and not visual_analysis:
            if settings.STRICT_QUALITY_MODE:
                raise RuntimeError("Strict review mode requires transcript or visual analysis.")
            return self._fallback_summary(source_name)

        max_words = int(max_duration_sec * 1.3)
        visual_context = json.dumps(visual_analysis or {}, ensure_ascii=False)[:2500]
        prompt = f"""Bạn là biên tập viên trưởng viết kịch bản video review ngắn bằng TIẾNG VIỆT.

Yêu cầu bắt buộc:
- Nội dung phải dựa vào cả transcript và phân tích hình ảnh/keyframe.
- Không viết chung chung kiểu "video này rất thú vị"; phải nêu chi tiết cụ thể nhìn thấy hoặc nghe được.
- Cấu trúc: hook gây tò mò, bối cảnh, phân tích chính, kết luận.
- Tổng số từ trong hook + bullet_points + closing không vượt quá {max_words} từ.
- bullet_points tối đa 3 ý, mỗi ý 1 câu ngắn.
- Chỉ trả về JSON thuần, không markdown.

JSON schema:
{{
  "title": "<tên video bằng tiếng Việt>",
  "hook": "<câu mở đầu hấp dẫn có chi tiết cụ thể>",
  "bullet_points": ["<bối cảnh>", "<phân tích nội dung>", "<điểm đáng xem hoặc đáng bàn>"],
  "closing": "<câu kết thúc>"
}}

Tên nguồn: {source_name}
Phân tích hình ảnh/keyframe:
{visual_context}

Nội dung transcript:
{transcript[:3500]}"""
        try:
            response = self.http.post(
                self.ollama_url,
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2, "num_predict": 500},
                },
            )
            response.raise_for_status()
            raw = response.json().get("response", "")
            summary = self._parse_json(raw)
            if not isinstance(summary, dict):
                raise ValueError("Unexpected summary payload")
            normalized = {
                "title": str(summary.get("title") or source_name),
                "hook": str(summary.get("hook") or ""),
                "bullet_points": list(summary.get("bullet_points") or []),
                "closing": str(summary.get("closing") or ""),
            }
            return self._normalize_vietnamese_summary(
                normalized,
                source_name=source_name,
                transcript=transcript,
                max_words=max_words,
            )
        except Exception as exc:
            if settings.STRICT_QUALITY_MODE:
                raise RuntimeError(f"Strict video script generation failed: {exc}") from exc
            logger.warning(f"[VideoReview] Strict summary failed: {exc}. Using fallback summary.")
            return self._fallback_summary(source_name, transcript, max_words=max_words)

    def _fallback_summary(
        self, source_name: str, transcript: str = "", max_words: int = 60
    ) -> Dict[str, object]:
        """Generate a 100% Vietnamese fallback summary from transcript sentences."""
        sentences = [
            sentence.strip(" -")
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", transcript)
            if len(sentence.strip()) > 20
        ]
        sentences = [sentence for sentence in sentences if self._looks_vietnamese(sentence)]

        # Lấy 3 câu dài nhất (nhiều thông tin nhất) theo thứ tự xuất hiện
        best_set = set(sorted(sentences, key=len, reverse=True)[:3])
        ordered_sentences = [s for s in sentences if s in best_set][:3]

        if not ordered_sentences:
            ordered_sentences = [
                "Video này mang đến nội dung vô cùng thú vị và đáng xem.",
                "Mỗi khung hình đều được chăm chút kỹ lưỡng, thu hút người xem từ đầu đến cuối.",
                "Đây là nội dung không nên bỏ lỡ trong tuần này.",
            ]

        summary = {
            "title": source_name,
            "hook": "Chào các bạn! Hôm nay chúng ta cùng nhau review một video cực kỳ thú vị.",
            "bullet_points": ordered_sentences,
            "closing": "Nếu thấy hay thì đừng quên thả tim và chia sẻ cho bạn bè nhé! Hẹn gặp lại ở video sau!",
        }
        return self._normalize_vietnamese_summary(
            summary,
            source_name=source_name,
            transcript=transcript,
            max_words=max_words,
        )

    def _normalize_vietnamese_summary(
        self,
        summary: Dict[str, object],
        source_name: str,
        transcript: str,
        max_words: int,
    ) -> Dict[str, object]:
        """Keep narration and subtitles Vietnamese-only and within target length."""
        title = self._clean_script_text(str(summary.get("title") or source_name))
        hook = self._clean_script_text(str(summary.get("hook") or ""))
        points = [
            self._clean_script_text(str(point))
            for point in list(summary.get("bullet_points") or [])[:3]
        ]
        closing = self._clean_script_text(str(summary.get("closing") or ""))

        if not self._looks_vietnamese(" ".join([hook, *points, closing])):
            hook = "Video này có nhiều chi tiết đáng chú ý và cần được tóm tắt ngắn gọn."
            points = self._generic_vietnamese_points(transcript)
            closing = "Tóm lại, đây là phần review cô đọng để nắm nhanh nội dung chính."

        words_left = max(20, max_words)
        kept: List[str] = []
        for text in [hook, " ".join(points), closing]:
            words = text.split()
            if not words:
                continue
            take = min(len(words), words_left)
            kept.append(" ".join(words[:take]))
            words_left -= take
            if words_left <= 0:
                break

        while len(kept) < 3:
            kept.append("")

        return {
            "title": title or "Video review",
            "hook": kept[0],
            "bullet_points": [kept[1]] if kept[1] else [],
            "closing": kept[2],
        }

    def _clean_script_text(self, text: str) -> str:
        text = re.sub(r"https?://\S+", "", text)
        text = re.sub(r"[#*_`<>[\]{}|~^=]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _looks_vietnamese(self, text: str) -> bool:
        if not text.strip():
            return False
        vietnamese_chars = (
            "ăâđêôơư"
            "áàảãạấầẩẫậắằẳẵặ"
            "éèẻẽẹếềểễệ"
            "íìỉĩị"
            "óòỏõọốồổỗộớờởỡợ"
            "úùủũụứừửữự"
            "ýỳỷỹỵ"
        )
        lowered = text.lower()
        if any(ch in lowered for ch in vietnamese_chars):
            return True
        common_words = {"của", "và", "là", "một", "những", "người", "này", "trong", "được", "không"}
        tokens = set(re.findall(r"\w+", lowered))
        return len(tokens & common_words) >= 2

    def _generic_vietnamese_points(self, transcript: str) -> List[str]:
        if transcript.strip():
            return [
                "Phần mở đầu đặt ra bối cảnh chính và kéo người xem vào câu chuyện.",
                "Các diễn biến quan trọng được chọn lọc để giữ nhịp review rõ ràng.",
                "Phần kết nhấn mạnh điểm đáng nhớ nhất của video gốc.",
            ]
        return [
            "Video gốc chưa có transcript đủ rõ nên bản review tập trung vào nhịp tóm tắt an toàn.",
            "Nội dung được trình bày ngắn gọn để phù hợp thời lượng đã chọn.",
            "Phụ đề chỉ dùng tiếng Việt để giữ trải nghiệm thống nhất.",
        ]

    def build_review_script(self, summary: Dict[str, object]) -> str:
        lines = []
        
        hook = summary.get("hook")
        if hook:
            lines.append(str(hook))
            
        bullet_points = summary.get("bullet_points", [])
        if bullet_points:
            # Gộp các câu thành đoạn văn liền mạch thay vì thêm dấu gạch đầu dòng
            lines.append(" ".join(str(pt).strip() for pt in bullet_points))
            
        closing = summary.get("closing")
        if closing:
            lines.append(str(closing))
            
        return " ".join(lines)

    async def synthesize_review_audio(self, job_id: str, review_script: str) -> Path:
        out_path = settings.VIDEO_TEMP_DIR / job_id / "review_narration.wav"
        if settings.VOICE_PROVIDER.lower() == "local_f5":
            result = await asyncio.to_thread(
                self.local_voice_engine.synthesize_segments,
                self.local_voice_engine.split_text(review_script),
                out_path,
                settings.VIDEO_TEMP_DIR / job_id / "review_voice_segments",
            )
            return result.audio_path
        await self.voice_engine.generate_text_audio(
            review_script,
            out_path,
            char_type=CharacterType.NARRATOR,
            voice_profile=VOICE_MAP["narrator"],
        )
        return out_path

    async def synthesize_review_audio_timeline(self, job_id: str, review_script: str):
        out_path = settings.VIDEO_TEMP_DIR / job_id / "review_narration.wav"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Try local F5-TTS first
        if settings.VOICE_PROVIDER.lower() == "local_f5":
            try:
                result = await asyncio.to_thread(
                    self.local_voice_engine.synthesize_segments,
                    self.local_voice_engine.split_text(review_script),
                    out_path,
                    settings.VIDEO_TEMP_DIR / job_id / "review_voice_segments",
                )
                return result.audio_path, result.cues
            except Exception as exc:
                if not settings.VIDEO_REVIEW_TTS_FALLBACK:
                    raise
                logger.warning(f"[VideoReview] Local F5-TTS failed: {exc}. Falling back to edge-tts.")

        # Fallback to edge-tts
        try:
            narration_path = await self.synthesize_review_audio(job_id, review_script)
        except Exception as exc:
            # If edge-tts also fails, try local F5-TTS one more time or raise
            logger.warning(f"[VideoReview] Edge-TTS also failed: {exc}. Retrying local.")
            result = await asyncio.to_thread(
                self.local_voice_engine.synthesize_segments,
                self.local_voice_engine.split_text(review_script),
                out_path,
                settings.VIDEO_TEMP_DIR / job_id / "review_voice_segments",
            )
            return result.audio_path, result.cues

        duration = self._audio_duration_sec(narration_path)
        return narration_path, self._build_even_cues(review_script, duration)

    def create_review_audio_mix(
        self, job_id: str, narration_path: Path, max_duration_sec: Optional[int] = None
    ) -> Path:
        if AudioSegment is None:
            raise RuntimeError("pydub is required to build review audio")

        output_dir = settings.VIDEO_TEMP_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        mixed_path = output_dir / "review_mix.wav"

        narration = AudioSegment.from_file(str(narration_path))

        # Hard-trim narration to exactly max_duration_sec (no overflow)
        if max_duration_sec and max_duration_sec > 0:
            max_ms = max_duration_sec * 1000
            if len(narration) > max_ms:
                narration = narration[:max_ms].fade_out(500)
            elif len(narration) < max_ms:
                # Pad with silence to fill the target duration cleanly
                narration = narration + AudioSegment.silent(duration=max_ms - len(narration))

        bgm_path = self._pick_bgm()
        if bgm_path and bgm_path.exists():
            try:
                bgm = AudioSegment.from_file(str(bgm_path))
                while len(bgm) < len(narration):
                    bgm += bgm
                bgm = bgm[: len(narration)] - 24
                mixed = bgm.overlay(narration + 2)
            except Exception as exc:
                logger.warning(
                    f"[VideoReview] BGM load failed ({exc}). Continuing with narration only."
                )
                mixed = narration
        else:
            mixed = narration
        mixed.export(str(mixed_path), format="wav")
        return mixed_path

    def create_subtitles(
        self, job_id: str, review_script: str, total_duration_sec: float
    ) -> Path:
        output_dir = settings.VIDEO_TEMP_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = output_dir / "review_subtitles.srt"

        cues = self._split_subtitle_lines(review_script)
        total_duration_sec = max(total_duration_sec, 1.0)
        cue_duration = total_duration_sec / max(len(cues), 1)

        with open(subtitle_path, "w", encoding="utf-8") as handle:
            for idx, cue in enumerate(cues, start=1):
                start = (idx - 1) * cue_duration
                end = min(total_duration_sec, idx * cue_duration)
                handle.write(f"{idx}\n")
                handle.write(
                    f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}\n"
                )
                handle.write(cue + "\n\n")

        return subtitle_path

    def create_subtitles_from_cues(
        self, job_id: str, cues: List[SpeechCue], total_duration_sec: float
    ) -> Path:
        output_dir = settings.VIDEO_TEMP_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = output_dir / "review_subtitles.srt"
        total_duration_sec = max(total_duration_sec, 1.0)
        with open(subtitle_path, "w", encoding="utf-8") as handle:
            for idx, cue in enumerate(cues, start=1):
                start = max(0.0, min(total_duration_sec, float(cue.start)))
                end = max(start + 0.2, min(total_duration_sec, float(cue.end)))
                handle.write(f"{idx}\n")
                handle.write(f"{self._format_srt_time(start)} --> {self._format_srt_time(end)}\n")
                handle.write(str(cue.text).strip() + "\n\n")
        return subtitle_path

    def select_visual_ranges(
        self,
        transcript_segments: List[Dict[str, float]],
        source_duration_sec: float,
        narration_duration_sec: float,
    ) -> List[Dict[str, float]]:
        """CapCut-style smart clip selection.

        Scores each transcript segment by multiple signals:
        - hook_score: first 15% of video gets bonus (opening hook)
        - density_score: word count * duration = info density
        - energy_score: short, punchy segments = viral moments
        - transition_score: topic changes between segments
        Then picks clips with varied lengths (2-10s) ensuring temporal spread.
        """
        target_clip_count = max(3, min(8, math.ceil(narration_duration_sec / 6)))

        if not transcript_segments:
            # No transcript — evenly distribute clips
            clip_len = max(3.0, narration_duration_sec / target_clip_count)
            return [
                {
                    "start": round(i * clip_len, 2),
                    "end": round(min(source_duration_sec, (i + 1) * clip_len), 2),
                    "label": "context",
                }
                for i in range(target_clip_count)
            ]

        # ── Score each segment ───────────────────────────────────
        scored = []
        for idx, seg in enumerate(transcript_segments):
            start = float(seg.get("start", 0))
            end = float(seg.get("end", start + 1))
            duration = max(0.5, end - start)
            word_count = seg.get("words", len(str(seg.get("text", "")).split()))
            if isinstance(word_count, list):
                word_count = len(word_count)
            text = str(seg.get("text", "")).lower()

            # Hook: first 15% of video
            hook_bonus = 2.0 if start < source_duration_sec * 0.15 else 0.0
            # Density: information-rich segments
            density = word_count / max(duration, 0.5)
            # Energy: short punchy = viral
            energy = 1.5 if duration < 4.0 and word_count > 3 else 0.5
            # Viral keywords
            viral_keywords = ["sốc", "bất ngờ", "không thể", "cực", "hot", "đỉnh",
                              "thật sự", "quá", "wow", "amazing", "khó tin"]
            viral_bonus = 2.0 if any(kw in text for kw in viral_keywords) else 0.0
            # Ending bonus — good for conclusion clips
            ending_bonus = 1.0 if start > source_duration_sec * 0.8 else 0.0

            total_score = hook_bonus + density + energy + viral_bonus + ending_bonus

            # Label for clip type
            if hook_bonus > 0:
                label = "hook"
            elif viral_bonus > 0:
                label = "viral"
            elif energy > 1.0:
                label = "highlight"
            elif ending_bonus > 0:
                label = "closing"
            else:
                label = "context"

            scored.append({
                "seg_idx": idx,
                "start": start,
                "end": end,
                "duration": duration,
                "score": total_score,
                "label": label,
            })

        # ── Pick top clips with temporal spread ──────────────────
        scored.sort(key=lambda x: x["score"], reverse=True)
        picked: List[Dict[str, float]] = []
        used_times: List[float] = []
        min_gap = max(2.0, source_duration_sec / (target_clip_count * 2))

        for item in scored:
            if len(picked) >= target_clip_count:
                break
            seg_center = (item["start"] + item["end"]) / 2
            # Check temporal gap from already-picked clips
            too_close = any(abs(seg_center - t) < min_gap for t in used_times)
            if too_close:
                continue

            # Dynamic clip length: hooks are shorter, context longer
            if item["label"] in ("hook", "viral"):
                clip_len = min(item["duration"], max(2.0, narration_duration_sec / target_clip_count * 0.7))
            elif item["label"] == "highlight":
                clip_len = min(item["duration"] + 1.0, max(3.0, narration_duration_sec / target_clip_count))
            else:
                clip_len = min(item["duration"] + 2.0, max(4.0, narration_duration_sec / target_clip_count * 1.3))

            clip_start = max(0.0, min(item["start"], source_duration_sec - clip_len))
            clip_end = min(source_duration_sec, clip_start + clip_len)

            picked.append({
                "start": round(clip_start, 2),
                "end": round(clip_end, 2),
                "label": item["label"],
            })
            used_times.append(seg_center)

        # Fill remaining slots with evenly distributed clips
        if len(picked) < target_clip_count:
            fill_len = max(3.0, narration_duration_sec / target_clip_count)
            for idx in range(target_clip_count - len(picked)):
                ratio = (idx + 1) / (target_clip_count - len(picked) + 1)
                start = ratio * max(source_duration_sec - fill_len, 0.0)
                picked.append({
                    "start": round(start, 2),
                    "end": round(min(source_duration_sec, start + fill_len), 2),
                    "label": "context",
                })

        picked.sort(key=lambda item: item["start"])
        return picked

    def render_review_video(
        self,
        job_id: str,
        source_path: Path,
        source_title: str,
        orientation: VideoOrientation,
        selected_ranges: List[Dict[str, float]],
        mixed_audio_path: Path,
        subtitles_path: Path,
    ) -> Path:
        temp_dir = settings.VIDEO_TEMP_DIR / job_id
        output_dir = settings.VIDEO_OUTPUT_DIR / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_duration = self._audio_duration_sec(mixed_audio_path)
        clip_files = self._build_processed_clips(
            temp_dir=temp_dir,
            source_path=source_path,
            selected_ranges=selected_ranges,
            orientation=orientation,
            target_duration_sec=audio_duration,
        )
        visual_track = self._concat_clips(temp_dir, clip_files)
        output_path = output_dir / "review_output.mp4"

        filter_chain = ",".join(
            [
                self._title_drawtext_filter(source_title, orientation),
                self._subtitle_filter(subtitles_path),
            ]
        )

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(visual_track),
            "-i",
            str(mixed_audio_path),
            "-t",
            str(round(audio_duration, 2)),
            "-vf",
            filter_chain,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(output_path),
        ]
        self._run_ffmpeg(cmd)
        return output_path

    def run(
        self,
        job_id: str,
        source_url: Optional[str],
        local_file_path: Optional[str],
        orientation: VideoOrientation,
        max_duration_sec: int,
    ) -> VideoReviewResult:
        source_path = self.prepare_source(job_id, source_url, local_file_path)
        meta = self.probe_video(source_path)
        source_name = source_path.stem

        transcription = self.transcribe(source_path)
        visual_analysis = self.understanding_engine.analyze(
            source_path,
            meta,
            transcription["transcript"],
        )
        summary = self.summarize_review_strict(
            transcription["transcript"],
            source_name=source_name,
            max_duration_sec=max_duration_sec,
            visual_analysis=visual_analysis,
        )
        review_script = self.build_review_script(summary)

        narration_path, speech_cues = asyncio.run(
            self.synthesize_review_audio_timeline(job_id, review_script)
        )
        mixed_audio_path = self.create_review_audio_mix(
            job_id, narration_path, max_duration_sec=max_duration_sec
        )
        narration_duration_sec = self._audio_duration_sec(mixed_audio_path)
        subtitles_path = self.create_subtitles_from_cues(
            job_id, speech_cues, narration_duration_sec
        )
        selected_ranges = self.select_visual_ranges(
            transcript_segments=transcription["segments"],
            source_duration_sec=meta["duration_sec"],
            narration_duration_sec=min(float(max_duration_sec), narration_duration_sec),
        )
        output_path = self.render_review_video(
            job_id=job_id,
            source_path=source_path,
            source_title=str(summary.get("title") or source_name),
            orientation=orientation,
            selected_ranges=selected_ranges,
            mixed_audio_path=mixed_audio_path,
            subtitles_path=subtitles_path,
        )

        return VideoReviewResult(
            title=str(summary.get("title") or source_name),
            transcript=str(transcription["transcript"]),
            review_script=review_script,
            orientation=orientation,
            output_path=str(output_path),
            subtitles_path=str(subtitles_path),
            selected_ranges=selected_ranges,
        )

    def _get_whisper_model(self):
        if self._whisper_model is None:
            self._whisper_model = WhisperModel(
                settings.VIDEO_ASR_MODEL,
                device="cpu",
                compute_type="int8",
            )
        return self._whisper_model

    def _pick_bgm(self) -> Optional[Path]:
        for filename in (
            "bgm_ambient_light.mp3",
            "bgm_cultivation.mp3",
            "bgm_epic_battle.mp3",
        ):
            candidate = settings.BGM_DIR / filename
            if candidate.exists():
                return candidate
        return None

    def _split_subtitle_lines(self, review_script: str) -> List[str]:
        sentences = [
            re.sub(r"\s+", " ", part).strip()
            for part in re.split(r"(?<=[.!?])\s+|\n+", review_script)
            if part.strip()
        ]
        lines: List[str] = []
        for sentence in sentences:
            words = sentence.split()
            if len(words) <= 12:
                lines.append(sentence)
                continue
            for idx in range(0, len(words), 10):
                lines.append(" ".join(words[idx : idx + 10]))
        return lines or [review_script]

    def _build_even_cues(self, review_script: str, total_duration_sec: float) -> List[SpeechCue]:
        lines = self._split_subtitle_lines(review_script)
        total_duration_sec = max(total_duration_sec, 1.0)
        cue_duration = total_duration_sec / max(len(lines), 1)
        return [
            SpeechCue(
                index=idx,
                text=line,
                start=round((idx - 1) * cue_duration, 3),
                end=round(min(total_duration_sec, idx * cue_duration), 3),
                path="",
            )
            for idx, line in enumerate(lines, start=1)
        ]

    def _build_processed_clips(
        self,
        temp_dir: Path,
        source_path: Path,
        selected_ranges: List[Dict[str, float]],
        orientation: VideoOrientation,
        target_duration_sec: float,
    ) -> List[Path]:
        target_duration_sec = max(target_duration_sec, 1.0)
        clips: List[Path] = []
        self.last_crop_plan = []
        current_duration = 0.0
        idx = 0
        while current_duration < target_duration_sec:
            clip_range = selected_ranges[idx % len(selected_ranges)]
            start = float(clip_range["start"])
            end = float(clip_range["end"])
            clip_duration = max(1.0, end - start)
            remaining = target_duration_sec - current_duration
            clip_duration = min(clip_duration, remaining)
            clip_path = temp_dir / f"clip_{len(clips):03d}.mp4"
            filter_complex, crop_info = self.reframer.build_filter(
                orientation=orientation,
                source_path=source_path,
                start_time=start,
                end_time=end,
            )
            self.last_crop_plan.append(
                {
                    "clip": clip_path.name,
                    "start": round(start, 2),
                    "end": round(end, 2),
                    **crop_info,
                }
            )
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-ss",
                str(start),
                "-t",
                str(round(clip_duration, 2)),
                "-i",
                str(source_path),
                "-an",
                "-filter_complex",
                filter_complex,
                "-map",
                "[vout]",
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "24",
                "-pix_fmt",
                "yuv420p",
                str(clip_path),
            ]
            self._run_ffmpeg(cmd)
            clips.append(clip_path)
            current_duration += clip_duration
            idx += 1
        return clips

    def _concat_clips(self, temp_dir: Path, clip_files: List[Path]) -> Path:
        concat_list = temp_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as handle:
            for clip in clip_files:
                handle.write(f"file '{clip.as_posix()}'\n")

        output_path = temp_dir / "visual_track.mp4"
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_path),
        ]
        self._run_ffmpeg(cmd)
        return output_path

    def _title_drawtext_filter(self, title: str, orientation: VideoOrientation) -> str:
        font = self._font_path().as_posix().replace(":", "\\:").replace("\\\\", "\\\\\\\\") 
        safe_title = title.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        fontsize = "44" if orientation == VideoOrientation.VERTICAL else "38"
        return (
            f"drawtext=fontfile='{font}':text='{safe_title}':"
            f"fontsize={fontsize}:fontcolor=white:box=1:boxcolor=black@0.50:boxborderw=20:"
            "x=(w-text_w)/2:y=64"
        )

    def _subtitle_filter(self, subtitles_path: Path) -> str:
        subtitle_path = subtitles_path.as_posix().replace(":", "\\:")
        font_name = self._subtitle_font_name()
        # Commas in force_style must be escaped with a backslash
        style = (
            f"FontName={font_name}"
            "\\,FontSize=20"
            "\\,Alignment=2"
            "\\,MarginV=80"
            "\\,Outline=2"
            "\\,Shadow=1"
            "\\,BorderStyle=3"
            "\\,Bold=0"
            "\\,PrimaryColour=&H00FFFFFF"
            "\\,OutlineColour=&H00000000"
        )
        return f"subtitles='{subtitle_path}':force_style='{style}'"

    def _detect_subject_x_offset(self, source_path: Path, start_time: float, end_time: float) -> int:
        """Detect the average X center of faces in the clip to calculate crop offset."""
        try:
            import cv2
        except ImportError:
            logger.warning("[VideoReview] opencv-python not installed. Using center crop.")
            return -1

        cap = cv2.VideoCapture(str(source_path))
        if not cap.isOpened():
            return -1
            
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Calculate target crop width based on 9:16 aspect ratio
        target_width = int(height * 9 / 16)
        if target_width >= width:
            cap.release()
            return -1

        start_frame = int(start_time * fps)
        end_frame = int(end_time * fps)
        
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        
        # Load Anime face cascade if available
        # Attempt to resolve from settings.VIDEO_TEMP_DIR which is in assets/
        try:
            from ..config import settings
            assets_dir = settings.VIDEO_TEMP_DIR.parent
        except ImportError:
            # Fallback path if run directly
            assets_dir = Path("C:/Users/Admin/Desktop/TienAmCac/backend/assets")
            
        anime_cascade_path = assets_dir / "models" / "lbpcascade_animeface.xml"
        anime_cascade = None
        if anime_cascade_path.exists():
            anime_cascade = cv2.CascadeClassifier(str(anime_cascade_path))
        
        centers = []
        # Sample max 10 frames from the clip for speed
        step = max(1, (end_frame - start_frame) // 10)
        
        for i in range(start_frame, end_frame, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                break
                
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # detect human faces
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
            
            # fallback to anime face if human face not found
            if len(faces) == 0 and anime_cascade:
                faces = anime_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60))
            
            # If multiple faces, pick the largest one
            if len(faces) > 0:
                largest_face = max(faces, key=lambda rect: rect[2] * rect[3])
                x, y, w, h = largest_face
                centers.append(x + w // 2)
                
        cap.release()
        
        if not centers:
            return -1
            
        # Smooth average
        avg_center = sum(centers) / len(centers)
        x_offset = int(avg_center - target_width / 2)
        
        # Clamp to bounds
        x_offset = max(0, min(x_offset, width - target_width))
        return x_offset

    def _visual_filter(self, orientation: VideoOrientation, source_path: Path = None, start_time: float = 0, end_time: float = 0) -> str:
        if orientation == VideoOrientation.HORIZONTAL:
            return (
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                "crop=1920:1080,boxblur=18:8[bg];"
                "[0:v]scale=1728:972:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]"
            )
            
        # For VERTICAL video, use smart auto-tracking crop with blurred background
        x_offset = -1
        if source_path:
            x_offset = self._detect_subject_x_offset(source_path, start_time, end_time)
            
        if x_offset >= 0:
            # We found a face! Crop directly at the x_offset with blurred background
            # Create blurred background from full frame scaled to target
            # Crop foreground to 9:16 ratio at detected position and scale
            return (
                f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,boxblur=18:8[bg];"
                f"[0:v]crop=w=ih*9/16:h=ih:x={x_offset}:y=0,"
                f"scale=1080:1920[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]"
            )
            
        # Fallback to static center crop with blurred background
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=18:8[bg];"
            "[0:v]crop=w=ih*9/16:h=ih:x=(iw-ow)/2:y=0,"
            "scale=1080:1920[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]"
        )

    def _font_path(self) -> Path:
        """Return a TTF font path that supports Vietnamese Unicode characters."""
        candidates = [
            # Noto Sans — best Unicode coverage, free, install via: winget install Google.NotoFonts
            Path("C:/Windows/Fonts/NotoSans-Regular.ttf"),
            Path("C:/Windows/Fonts/NotoSansVN-Regular.ttf"),
            # Arial Unicode MS — ships with Office
            Path("C:/Windows/Fonts/arialuni.ttf"),
            # Arial — built-in Windows font; partial Vietnamese support
            Path("C:/Windows/Fonts/arial.ttf"),
            # Linux / Docker fallback
            Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for path in candidates:
            if path.exists():
                return path
        return Path("arial.ttf")

    def _subtitle_font_name(self) -> str:
        """Return the font *name* string for libass force_style (not file path).
        Prefers fonts with full Vietnamese diacritic support."""
        if Path("C:/Windows/Fonts/NotoSans-Regular.ttf").exists():
            return "Noto Sans"
        if Path("C:/Windows/Fonts/NotoSansVN-Regular.ttf").exists():
            return "Noto Sans Vietnamese"
        if Path("C:/Windows/Fonts/arialuni.ttf").exists():
            return "Arial Unicode MS"
        return "Arial"

    def _audio_duration_sec(self, audio_path: Path) -> float:
        if AudioSegment is None:
            raise RuntimeError("pydub is required for measuring audio")
        return len(AudioSegment.from_file(str(audio_path))) / 1000.0

    def _format_srt_time(self, seconds: float) -> str:
        millis = int(round(seconds * 1000))
        hours, remainder = divmod(millis, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, remainder = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{remainder:03d}"

    def _run_ffmpeg(self, cmd: List[str]):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg command failed")

    def _parse_json(self, raw: str):
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group())
        return json.loads(raw)
