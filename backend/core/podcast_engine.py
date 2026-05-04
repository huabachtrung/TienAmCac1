"""End-to-end Podcast Video Engine.

Pipeline:  Article URL → scrape → LLM script → TTS → Whisper align
           → AI image gen → Playwright/GSAP render → MP4 output.

All components are local and FREE (no paid API keys required).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# ── Lazy imports for optional deps ───────────────────────────────────────
try:
    from newspaper import Article as _Article
    HAS_NEWSPAPER = True
except ImportError:
    HAS_NEWSPAPER = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

try:
    from faster_whisper import WhisperModel
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

try:
    from ..config import settings
    from ..agents.local_voice_engine import LocalVoiceEngine
    from .html_video_renderer import HtmlVideoRenderer
except ImportError:
    from config import settings
    from agents.local_voice_engine import LocalVoiceEngine
    from core.html_video_renderer import HtmlVideoRenderer


# ── Reusable icon pool for CapCut-style reactions ────────────────────────
_ICONS_BY_MOOD = {
    "shock":   ["😱", "🤯", "😨"],
    "fire":    ["🔥", "💥", "⚡"],
    "sad":     ["😢", "💔", "😞"],
    "happy":   ["😄", "🎉", "❤️"],
    "think":   ["🤔", "💡", "🧐"],
    "money":   ["💰", "📈", "💸"],
    "default": ["📰", "📢", "🎯"],
}


class PodcastEngine:
    """Converts a news article URL into a professional podcast-style video."""

    def __init__(self):
        self.http = httpx.AsyncClient(timeout=90.0, follow_redirects=True)
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.ollama_model = settings.OLLAMA_MODEL
        self.voice_engine = LocalVoiceEngine()
        self._whisper_model: Optional[Any] = None

    # ── Main pipeline ────────────────────────────────────────────────────

    async def generate_from_url(self, job_id: str, url: str) -> str:
        """Full pipeline: URL → Video MP4.  Returns absolute output path."""
        logger.info(f"[Podcast:{job_id}] ▶ Starting from URL: {url}")

        job_dir = settings.VIDEO_TEMP_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: Scrape ───────────────────────────────────────────────
        logger.info(f"[Podcast:{job_id}] 1/7 Scraping article ...")
        article_text, article_title, article_images = self._scrape_article(url)
        logger.info(f"[Podcast:{job_id}]     Title: {article_title}")
        logger.info(f"[Podcast:{job_id}]     Text length: {len(article_text)} chars, images found: {len(article_images)}")

        # ── Step 2: LLM script ───────────────────────────────────────────
        logger.info(f"[Podcast:{job_id}] 2/7 Generating script via LLM ...")
        script_data = await self._generate_script(article_title, article_text)
        scenes = script_data.get("scenes", [])
        logger.info(f"[Podcast:{job_id}]     Generated {len(scenes)} scenes")

        # ── Step 3: TTS ──────────────────────────────────────────────────
        logger.info(f"[Podcast:{job_id}] 3/7 Synthesizing narration audio ...")
        full_text = " ".join(s["text"] for s in scenes if s.get("text"))
        audio_path = job_dir / "podcast_narration.wav"
        segments = self.voice_engine.split_text(full_text)
        result = await asyncio.to_thread(
            self.voice_engine.synthesize_segments,
            segments,
            audio_path,
            job_dir / "podcast_voice_segments",
        )
        logger.info(f"[Podcast:{job_id}]     Audio: {result.audio_path}")

        # ── Step 4: Whisper word-level timestamps ────────────────────────
        logger.info(f"[Podcast:{job_id}] 4/7 Aligning words with Whisper ...")
        words = await asyncio.to_thread(self._get_word_timestamps, result.audio_path)
        script_data["words"] = words
        logger.info(f"[Podcast:{job_id}]     Aligned {len(words)} words")

        # ── Step 5: Compute durations & scene timing ─────────────────────
        duration = words[-1]["end"] + 1.0 if words else 10.0
        script_data["duration"] = duration
        scene_dur = duration / max(1, len(scenes))
        for i, scene in enumerate(scenes):
            scene["start"] = round(i * scene_dur, 2)
            scene["end"] = round((i + 1) * scene_dur, 2)

        # ── Step 6: Visuals (AI-generated + scraped images) ──────────────
        logger.info(f"[Podcast:{job_id}] 5/7 Generating visuals ...")
        await self._generate_visuals(job_id, script_data, article_images)

        # ── Step 7: Render ───────────────────────────────────────────────
        logger.info(f"[Podcast:{job_id}] 6/7 Rendering video with Playwright ...")
        output_dir = settings.VIDEO_OUTPUT_DIR / job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        final_path = str(output_dir / "podcast_final.mp4")

        # Resolve BGM (pick first file from bgm dir if any)
        bgm_path = self._pick_bgm()

        template_dir = str((Path(settings.UPLOAD_DIR).parent / "templates" / "news_podcast").resolve())
        renderer = HtmlVideoRenderer(template_dir)
        await renderer.render_video(
            script_data,
            str(result.audio_path),
            final_path,
            bgm_path=bgm_path,
        )

        logger.info(f"[Podcast:{job_id}] 7/7 ✓ Complete → {final_path}")
        return final_path

    # ── Scraping ─────────────────────────────────────────────────────────

    def _scrape_article(self, url: str) -> Tuple[str, str, List[str]]:
        """Extract text, title, and image URLs from an article.

        Tries newspaper3k first, falls back to requests + BeautifulSoup,
        then finally to plain text download.
        """
        images: List[str] = []

        if HAS_NEWSPAPER:
            try:
                art = _Article(url, language="vi")
                art.download()
                art.parse()
                if art.text and len(art.text) > 100:
                    images = list(art.images)[:10] if art.images else []
                    return art.text, art.title or "Bài viết", images
            except Exception as e:
                logger.warning(f"[Podcast] newspaper3k failed: {e}")

        # Fallback: requests + BS4
        if HAS_BS4:
            try:
                import requests as _req
                resp = _req.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                # Remove script/style
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                title = soup.title.string if soup.title else "Bài viết"
                body = soup.get_text(separator="\n", strip=True)
                images = [img["src"] for img in soup.find_all("img", src=True)][:10]
                if body and len(body) > 100:
                    return body, title, images
            except Exception as e:
                logger.warning(f"[Podcast] BS4 fallback failed: {e}")

        raise ValueError(f"Could not extract article content from {url}")

    # ── LLM Script Generation ────────────────────────────────────────────

    async def _generate_script(self, title: str, text: str) -> Dict[str, Any]:
        prompt = f"""Bạn là biên tập viên Podcast Video chuyên nghiệp tại một studio sản xuất nội dung số.
Hãy đọc bài báo sau và tạo kịch bản video podcast dạng dọc (9:16) hoàn toàn bằng TIẾNG VIỆT.

YÊU CẦU BẮT BUỘC:
1. Cấu trúc phải có ĐÚNG trình tự: Hook → Bối cảnh → Phân tích (2-3 điểm) → Kết luận.
2. Mỗi scene PHẢI có: text (câu nói Tiếng Việt), image_prompt (mô tả ảnh bằng Tiếng Anh), và mood (shock/fire/sad/happy/think/money/default).
3. Scene đầu tiên (Hook) phải giật gân, ngắn gọn (dưới 15 từ).
4. Tổng khoảng 5-7 scenes.
5. Chỉ trả về JSON thuần, không markdown, không giải thích.

JSON schema:
{{
  "theme": "dark",
  "scenes": [
    {{
      "text": "Câu nói Tiếng Việt cho scene này",
      "image_prompt": "English prompt to generate illustration image for this scene, cinematic, detailed",
      "mood": "shock"
    }}
  ]
}}

Tiêu đề bài báo: {title}
Nội dung (rút gọn):
{text[:4000]}"""

        try:
            resp = await self.http.post(
                self.ollama_url,
                json={
                    "model": self.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 1200},
                },
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")

            # Clean markdown fences
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]

            data = json.loads(raw.strip())

            # Inject icons based on mood
            for scene in data.get("scenes", []):
                mood = scene.get("mood", "default")
                pool = _ICONS_BY_MOOD.get(mood, _ICONS_BY_MOOD["default"])
                scene["icon"] = random.choice(pool)

            return data

        except Exception as e:
            logger.error(f"[Podcast] LLM script generation failed: {e} — using fallback")
            return self._fallback_script(title, text)

    def _fallback_script(self, title: str, text: str) -> Dict[str, Any]:
        """Rule-based fallback if LLM is unavailable."""
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 20]
        best = sorted(sentences, key=len, reverse=True)[:4]

        scenes = [
            {"text": f"Tin nóng: {title}", "image_prompt": "Breaking news background, dramatic lighting", "mood": "shock", "icon": "🔥"},
        ]
        for i, sent in enumerate(best):
            moods = ["think", "fire", "happy", "default"]
            m = moods[i % len(moods)]
            scenes.append({
                "text": sent[:200],
                "image_prompt": f"News illustration scene {i+1}, professional journalism photo",
                "mood": m,
                "icon": random.choice(_ICONS_BY_MOOD.get(m, _ICONS_BY_MOOD["default"])),
            })
        scenes.append({
            "text": "Cảm ơn các bạn đã theo dõi. Hẹn gặp lại!",
            "image_prompt": "Thank you screen, modern minimalist design",
            "mood": "happy",
            "icon": "❤️",
        })
        return {"theme": "dark", "scenes": scenes}

    # ── Whisper word alignment ───────────────────────────────────────────

    def _get_whisper_model(self):
        if self._whisper_model is None:
            if not HAS_WHISPER:
                raise RuntimeError("faster-whisper is not installed")
            model_size = getattr(settings, "VIDEO_ASR_MODEL", "tiny")
            self._whisper_model = WhisperModel(
                model_size, device="cpu", compute_type="int8"
            )
        return self._whisper_model

    def _get_word_timestamps(self, audio_path: Path) -> List[Dict[str, Any]]:
        model = self._get_whisper_model()
        segments_iter, _ = model.transcribe(
            str(audio_path),
            beam_size=1,
            word_timestamps=True,
            language="vi",
        )
        words = []
        for seg in segments_iter:
            for w in (seg.words or []):
                word_text = w.word.strip()
                if word_text:
                    words.append({
                        "word": word_text,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                    })
        return words

    # ── Visual generation ────────────────────────────────────────────────

    async def _generate_visuals(
        self, job_id: str, script_data: Dict[str, Any], article_images: List[str]
    ):
        """For each scene, try article images first, then Pollinations AI."""
        job_dir = settings.VIDEO_TEMP_DIR / job_id

        for idx, scene in enumerate(script_data.get("scenes", [])):
            img_path = job_dir / f"scene_{idx}.jpg"

            # Try to use an existing article image first
            if idx < len(article_images) and article_images[idx]:
                try:
                    resp = await self.http.get(article_images[idx], timeout=15.0)
                    if resp.status_code == 200 and len(resp.content) > 1024:
                        with open(img_path, "wb") as f:
                            f.write(resp.content)
                        scene["image_url"] = img_path.absolute().as_uri()
                        logger.info(f"[Podcast:{job_id}]   Scene {idx}: used article image")
                        continue
                except Exception:
                    pass

            # Generate via Pollinations AI (free, no API key)
            prompt = scene.get("image_prompt", "abstract cinematic background")
            prompt += ", vertical 9:16 format, hyper-detailed, cinematic lighting, 4k"
            safe = quote(prompt)
            gen_url = f"https://image.pollinations.ai/prompt/{safe}?width=1080&height=1920&nologo=true"

            success = False
            for attempt in range(3):
                try:
                    resp = await self.http.get(gen_url, timeout=45.0)
                    if resp.status_code == 200 and len(resp.content) > 5000:
                        with open(img_path, "wb") as f:
                            f.write(resp.content)
                        scene["image_url"] = img_path.absolute().as_uri()
                        logger.info(f"[Podcast:{job_id}]   Scene {idx}: generated AI image (attempt {attempt+1})")
                        success = True
                        await asyncio.sleep(1.0) # Rate limit spacing
                        break
                    elif resp.status_code == 429:
                        wait_time = 2.0 * (attempt + 1)
                        logger.warning(f"[Podcast:{job_id}]   Scene {idx}: 429 Too Many Requests. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning(f"[Podcast:{job_id}]   Scene {idx}: image gen returned {resp.status_code}")
                        break
                except Exception as e:
                    logger.warning(f"[Podcast:{job_id}]   Scene {idx}: image gen failed: {e}")
                    await asyncio.sleep(2.0)
            
            if not success:
                scene["image_url"] = ""

    # ── Utilities ────────────────────────────────────────────────────────

    def _pick_bgm(self) -> Optional[str]:
        """Pick a random BGM file from the assets/bgm directory."""
        bgm_dir = settings.BGM_DIR
        if not bgm_dir.exists():
            return None
        candidates = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))
        if not candidates:
            return None
        return str(random.choice(candidates))
