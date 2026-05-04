"""
AGENT #2 — SceneAnalyzer
Sprint 2: Uses Ollama (local LLM, FREE) to analyze each segment:
  - Scene type detection
  - Character NER (name, gender, age, cultivation level)
  - Emotion arc detection
  - Dialogue speaker tagging
"""
import json
import re
from collections import Counter
from typing import List, Dict, Optional, Union
from loguru import logger
import httpx

try:
    from ..config import settings, SCENE_BGM_MAP, VOICE_MAP
    from ..models.schemas import Chapter, TextSegment, Character, SceneType, CharacterType
except ImportError:
    from config import settings, SCENE_BGM_MAP, VOICE_MAP
    from models.schemas import Chapter, TextSegment, Character, SceneType, CharacterType


# ── Prompts ────────────────────────────────────────────────────────────
SCENE_ANALYSIS_PROMPT = """Bạn là AI phân tích tiểu thuyết tiên hiệp. Phân tích đoạn văn sau và trả về JSON.

Đoạn văn:
"{text}"

Trả về JSON với format sau (CHỈ JSON, không thêm gì khác):
{{
  "scene_type": "<một trong: rural_village|immortal_sect|battle|cultivation|romance|mystery|discovery|dialog|narration|death|triumph>",
  "emotion": "<một trong: calm|tense|climax|sad|joyful|fearful|angry|reverent>",
  "emotion_intensity": <số 0.0-1.0>,
  "is_dialog": <true/false>,
  "speaker": "<tên nhân vật nếu là dialog, null nếu không>",
  "speaker_gender": "<male|female|unknown>",
  "speaker_age": "<young|middle|elder|unknown>",
  "speaker_role": "<protagonist|antagonist|elder|disciple|narrator|unknown>",
  "sfx_keywords": ["<các từ khoá trigger SFX trong đoạn, tối đa 3>"],
  "bgm_change": <true/false>
}}"""

NER_PROMPT = """Liệt kê tất cả nhân vật xuất hiện trong văn bản tiểu thuyết tiên hiệp sau.

Văn bản:
"{text}"

Trả về JSON array (CHỈ JSON):
[
  {{
    "name": "<tên nhân vật>",
    "gender": "<male|female|unknown>",
    "age_group": "<young|middle|elder|child|unknown>",
    "role": "<protagonist|antagonist|elder|disciple|minor|unknown>",
    "cultivation_level": "<tên cảnh giới nếu có, null nếu không>"
  }}
]"""


class SceneAnalyzer:
    """
    AI-powered scene and character analyzer using Ollama (local, free).
    Falls back to Groq API if Ollama is unavailable.
    """

    def __init__(self):
        self.ollama_url = f"{settings.OLLAMA_BASE_URL}/api/generate"
        self.model = settings.OLLAMA_MODEL
        self.client = httpx.Client(timeout=60.0)
        self.ollama_available = True
        self.known_speakers: Dict[str, Dict[str, str]] = {}
        self.protagonist_name: Optional[str] = None
        self.last_dialog_speaker: Optional[str] = None

    def analyze_chapter(self, chapter: Chapter) -> Chapter:
        """Run full analysis on a chapter: NER + segment analysis"""
        logger.info(f"[SceneAI] Analyzing chapter: {chapter.title}")

        # Step 1: Extract all characters from full chapter text
        chapter.characters = self._extract_characters(chapter.raw_text[:3000])
        self._bootstrap_speaker_memory(chapter)

        # Step 2: Analyze each segment
        prev_scene = None
        for i, segment in enumerate(chapter.segments):
            logger.debug(f"[SceneAI] Segment {i+1}/{len(chapter.segments)}")
            analysis = self._analyze_segment(segment.text)
            if segment.is_dialog and not analysis.get("speaker"):
                inferred = self._infer_dialog_attributes(segment.text)
                analysis = {
                    **inferred,
                    **{key: value for key, value in analysis.items() if value not in (None, "", [])},
                }

            # Apply analysis results
            segment.scene_type = SceneType(analysis.get("scene_type", "narration"))
            segment.emotion = analysis.get("emotion", "calm")
            segment.emotion_intensity = float(analysis.get("emotion_intensity", 0.5))
            segment.is_dialog = analysis.get("is_dialog", False)

            if segment.is_dialog:
                segment.speaker = analysis.get("speaker")
                if segment.speaker:
                    remembered = self.known_speakers.get(self._normalize_speaker_name(segment.speaker), {})
                    analysis["speaker_gender"] = remembered.get("speaker_gender", analysis.get("speaker_gender", "male"))
                    analysis["speaker_age"] = remembered.get("speaker_age", analysis.get("speaker_age", "young"))
                    analysis["speaker_role"] = remembered.get("speaker_role", analysis.get("speaker_role", "unknown"))
                segment.character_type = self._map_character_type(
                    analysis.get("speaker_gender", "male"),
                    analysis.get("speaker_age", "young"),
                    analysis.get("speaker_role", "unknown"),
                )
                if segment.speaker:
                    segment.speaker_key = self._normalize_speaker_name(segment.speaker)
                    self._remember_speaker(segment.speaker, analysis)
                    remembered = self.known_speakers.get(segment.speaker_key, {})
                    segment.character_type = self._map_character_type(
                        remembered.get("speaker_gender", analysis.get("speaker_gender", "male")),
                        remembered.get("speaker_age", analysis.get("speaker_age", "young")),
                        remembered.get("speaker_role", analysis.get("speaker_role", "unknown")),
                    )
                    segment.voice_profile = self._voice_profile_for_speaker(segment.speaker_key, segment.character_type)
                    self.last_dialog_speaker = segment.speaker_key
                else:
                    segment.voice_profile = self._voice_profile_for_speaker(None, segment.character_type)
            else:
                segment.character_type = CharacterType.NARRATOR
                segment.voice_profile = None

            # SFX triggers
            segment.sfx_triggers = self._resolve_sfx(
                analysis.get("sfx_keywords", []),
                context_text=segment.text,
            )

            # BGM change detection
            if segment.scene_type != prev_scene or analysis.get("bgm_change"):
                segment.bgm_change = SCENE_BGM_MAP.get(segment.scene_type.value)
                prev_scene = segment.scene_type

        return chapter

    def _bootstrap_speaker_memory(self, chapter: Chapter):
        candidate_names = self._extract_name_candidates(chapter.raw_text)
        if candidate_names:
            self.protagonist_name = self.protagonist_name or candidate_names[0]
        for character in chapter.characters:
            key = self._normalize_speaker_name(character.name)
            self.known_speakers.setdefault(
                key,
                {
                    "speaker": character.name,
                    "speaker_gender": "female" if "female" in character.character_type.value else "male",
                    "speaker_age": "elder" if "elder" in character.character_type.value else "young",
                    "speaker_role": "protagonist" if character.name == self.protagonist_name else "unknown",
                },
            )

    def _analyze_segment(self, text: str) -> Dict:
        """Call Ollama to analyze a single text segment"""
        # Giới hạn độ dài nội dung để không làm RAM tăng vọt
        prompt = SCENE_ANALYSIS_PROMPT.format(text=text[:300]) 

        try:
            raw = self._call_ollama(prompt)
            return self._parse_json(raw)
        except Exception as e:
            logger.warning(f"[SceneAI] Ollama failed: {e}. Using rule-based fallback.")
            return self._rule_based_fallback(text)

    def _extract_characters(self, text: str) -> List[Character]:
        """Extract character list from chapter using LLM"""
        # Giới hạn số lượng token đầu vào (giảm tải RAM)
        prompt = NER_PROMPT.format(text=text[:1500])
        try:
            raw = self._call_ollama(prompt)
            char_list = self._parse_json(raw)
            if not isinstance(char_list, list):
                return []
            return [
                Character(
                    name=c.get("name", "Unknown"),
                    character_type=self._map_character_type(
                        c.get("gender", "male"),
                        c.get("age_group", "young"),
                        c.get("role", "unknown"),
                    )
                )
                for c in char_list
            ]
        except Exception as e:
            logger.warning(f"[SceneAI] NER failed: {e}")
            return self._fallback_extract_characters(text)

    def _call_ollama(self, prompt: str) -> str:
        """Make a request to local Ollama LLM"""
        if not self.ollama_available:
            raise RuntimeError("Ollama unavailable")

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "top_p": 0.9,
                "num_predict": 512,
            }
        }
        try:
            response = self.client.post(self.ollama_url, json=payload)
            response.raise_for_status()
            return response.json()["response"]
        except Exception:
            self.ollama_available = False
            raise

    def _parse_json(self, raw: str) -> Union[Dict, List]:
        """Extract JSON from LLM response"""
        # Try to find JSON block
        match = re.search(r"\{[\s\S]*\}|\[[\s\S]*\]", raw)
        if match:
            return json.loads(match.group())
        return json.loads(raw)

    def _map_character_type(self, gender: str, age: str, role: str) -> CharacterType:
        """Map NER attributes to CharacterType enum"""
        if role in ("narrator",):
            return CharacterType.NARRATOR
        if gender == "female":
            return CharacterType.FEMALE_ELDER if age == "elder" else CharacterType.FEMALE_YOUNG
        if age == "child":
            return CharacterType.CHILD
        if age == "elder" or role == "elder":
            return CharacterType.MALE_ELDER
        if role == "antagonist":
            return CharacterType.MALE_VILLAIN
        if role == "immortal":
            return CharacterType.IMMORTAL
        return CharacterType.MALE_YOUNG

    def _resolve_sfx(self, keywords: List[str], context_text: str = "") -> List[str]:
        """Map LLM-returned keywords to actual SFX filenames"""
        try:
            from ..config import SFX_TRIGGER_MAP
        except ImportError:
            from config import SFX_TRIGGER_MAP
        sfx_files = []
        context_lower = context_text.lower()
        for kw in keywords:
            kw_lower = str(kw).lower().strip()
            if not kw_lower:
                continue
            for trigger, sfx_file in SFX_TRIGGER_MAP.items():
                trigger_lower = trigger.lower()
                has_llm_match = trigger_lower in kw_lower or kw_lower in trigger_lower
                has_text_evidence = trigger_lower in context_lower or kw_lower in context_lower
                if has_llm_match and has_text_evidence:
                    if sfx_file not in sfx_files:
                        sfx_files.append(sfx_file)
                if len(sfx_files) >= 2:
                    return sfx_files
        return sfx_files

    def _rule_based_fallback(self, text: str) -> Dict:
        """Fallback when Ollama is unavailable — keyword-based classification"""
        try:
            from ..config import SFX_TRIGGER_MAP
        except ImportError:
            from config import SFX_TRIGGER_MAP

        text_lower = text.lower()
        scene = "narration"
        emotion = "calm"
        intensity = 0.3

        # Battle detection
        battle_kws = ["đánh", "chiến", "công kích", "kiếm", "sát", "giết", "kết chiến"]
        if any(k in text_lower for k in battle_kws):
            scene = "battle"
            emotion = "tense"
            intensity = 0.8

        # Cultivation
        elif any(k in text_lower for k in ["tu luyện", "tu vi", "đột phá", "linh khí", "thiên đạo"]):
            scene = "cultivation"
            emotion = "reverent"
            intensity = 0.6

        dialog_attrs = self._infer_dialog_attributes(text)
        is_dialog = dialog_attrs["is_dialog"]
        if is_dialog:
            scene = "dialog"
            emotion = "calm"

        # SFX
        sfx_kws = [kw for kw in SFX_TRIGGER_MAP if kw in text_lower]

        return {
            "scene_type": scene,
            "emotion": emotion,
            "emotion_intensity": intensity,
            "is_dialog": is_dialog,
            "speaker": dialog_attrs["speaker"],
            "speaker_gender": dialog_attrs["speaker_gender"],
            "speaker_age": dialog_attrs["speaker_age"],
            "speaker_role": dialog_attrs["speaker_role"],
            "sfx_keywords": sfx_kws[:3],
            "bgm_change": False,
        }

    def _fallback_extract_characters(self, text: str) -> List[Character]:
        candidates = self._extract_name_candidates(text)
        characters = []
        for index, name in enumerate(candidates[:6]):
            role = "protagonist" if index == 0 else "unknown"
            character_type = CharacterType.MALE_YOUNG
            if any(marker in text.lower() for marker in [name.lower() + " nàng", name.lower() + " cô"]):
                character_type = CharacterType.FEMALE_YOUNG
            characters.append(Character(name=name, character_type=character_type))
            self.known_speakers.setdefault(
                self._normalize_speaker_name(name),
                {
                    "speaker": name,
                    "speaker_gender": "female" if character_type == CharacterType.FEMALE_YOUNG else "male",
                    "speaker_age": "young",
                    "speaker_role": role,
                },
            )
        if candidates and not self.protagonist_name:
            self.protagonist_name = candidates[0]
        return characters

    def _extract_name_candidates(self, text: str) -> List[str]:
        matches = re.findall(r"\b([A-ZÀ-Ỵ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỵ][a-zà-ỹ]+){0,2})\b", text)
        blacklist = {"Chương", "Hoàng Phong Cốc", "Thiên Nam", "Loạn Tinh Hải"}
        counts = Counter(name for name in matches if name not in blacklist and len(name) > 2)
        return [name for name, _ in counts.most_common(8)]

    def _infer_dialog_attributes(self, text: str) -> Dict[str, Optional[str]]:
        original = text.strip()
        lowered = original.lower()
        attribution = lowered
        attribution_original = original
        quote_end = max(lowered.rfind('"'), lowered.rfind("”"), lowered.rfind("»"))
        if quote_end != -1 and quote_end + 1 < len(lowered):
            attribution = lowered[quote_end + 1 :]
            attribution_original = original[quote_end + 1 :]

        is_dialog = bool(
            lowered.startswith(("\"", "“", "”", "«", "-", "–", "—"))
            or re.match(r"^[a-zà-ỹ0-9 _]{0,30}:\s", lowered)
        )
        if not is_dialog:
            return {
                "is_dialog": False,
                "speaker": None,
                "speaker_gender": "male",
                "speaker_age": "young",
                "speaker_role": "protagonist",
            }

        female_markers = [" nàng ", " cô ", " muội ", " sư tỷ ", " tỷ tỷ ", " thiếu nữ ", " nữ nhân ", " bà ", " mụ "]
        male_markers = [" hắn ", " y ", " gã ", " đệ ", " sư huynh ", " thiếu niên ", " nam nhân ", " lão ", " ông "]
        elder_markers = ["trưởng lão", "lão", "bà lão", "lão giả", "tiền bối"]
        villain_markers = ["cười gằn", "quát", "rít", "đe dọa", "ma", "tà"]
        immortal_markers = ["tiên", "thượng tiên", "chân nhân", "đạo quân"]

        wrapped = f" {attribution} "
        gender = "female" if any(marker in wrapped for marker in female_markers) else "male"
        if any(marker in wrapped for marker in male_markers):
            gender = "male"
        if any(name in attribution for name in ["lâm phàm", "sư đệ", "thiếu niên", "nam tử"]):
            gender = "male"

        age = "elder" if any(marker in attribution for marker in elder_markers) else "young"
        role = "antagonist" if any(marker in attribution for marker in villain_markers) else "protagonist"
        if any(marker in attribution for marker in immortal_markers):
            role = "immortal"

        speaker_match = re.match(r'^[\-–—"\“\«]*\s*([A-ZÀ-Ỵ][\wÀ-ỹ ]{1,24}):', text.strip())
        speaker = speaker_match.group(1).strip() if speaker_match else None
        if not speaker:
            explicit_name = self._extract_name_from_attribution(attribution_original)
            if explicit_name:
                speaker = explicit_name
            elif role == "protagonist" and self.protagonist_name:
                speaker = self.protagonist_name
            elif self.last_dialog_speaker and self.last_dialog_speaker in self.known_speakers:
                previous = self.known_speakers[self.last_dialog_speaker]
                if previous.get("speaker_gender") != gender:
                    speaker = previous.get("speaker")

        if speaker:
            speaker_lower = speaker.lower()
            if any(marker in speaker_lower for marker in ["tiên tử", "cô", "nương", "tỷ", "muội"]):
                gender = "female"
            if any(marker in speaker_lower for marker in ["lão", "ông", "sư huynh", "đạo hữu"]):
                gender = "male"
            if any(marker in speaker_lower for marker in ["tiên tử", "phu nhân", "sư tỷ", "sư nương"]):
                gender = "female"
            if any(marker in speaker_lower for marker in ["tiên tử", "phu nhân", "sư nương", "bà", "mụ"]):
                age = "elder" if "sư nương" in speaker_lower else age
            if any(marker in speaker_lower for marker in ["tiên tử", "tiên cô"]):
                role = "unknown"

        return {
            "is_dialog": True,
            "speaker": speaker,
            "speaker_gender": gender,
            "speaker_age": age,
            "speaker_role": role,
        }

    def _extract_name_from_attribution(self, attribution: str) -> Optional[str]:
        titled = re.search(
            r"([A-ZÀ-Ỵ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỵa-zà-ỹ][a-zà-ỹ]+){0,2}\s+(?:tiên tử|phu nhân|sư tỷ|sư huynh|đạo hữu|sư nương))",
            attribution,
        )
        if titled:
            return titled.group(1)
        match = re.search(r"([A-ZÀ-Ỵ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỵ][a-zà-ỹ]+){0,2})", attribution)
        return match.group(1) if match else None

    def _normalize_speaker_name(self, name: str) -> str:
        return re.sub(r"\s+", " ", name.strip().lower())

    def _remember_speaker(self, speaker: str, analysis: Dict):
        key = self._normalize_speaker_name(speaker)
        current = self.known_speakers.get(key, {"speaker": speaker})
        current.update(
            {
                "speaker": speaker,
                "speaker_gender": analysis.get("speaker_gender", current.get("speaker_gender", "male")),
                "speaker_age": analysis.get("speaker_age", current.get("speaker_age", "young")),
                "speaker_role": analysis.get("speaker_role", current.get("speaker_role", "unknown")),
            }
        )
        self.known_speakers[key] = current

    def _voice_profile_for_speaker(self, speaker_key: Optional[str], character_type: CharacterType):
        if speaker_key and self.protagonist_name and speaker_key == self._normalize_speaker_name(self.protagonist_name):
            if "female" in character_type.value:
                return VOICE_MAP["protagonist_female"]
            return VOICE_MAP["protagonist_male"]
        return VOICE_MAP.get(character_type.value, VOICE_MAP["narrator"])
