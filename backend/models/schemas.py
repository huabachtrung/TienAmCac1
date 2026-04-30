"""
TIÊN ÂM CÁC — Data Models
Pydantic schemas for jobs, scripts, characters, audio timeline
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime
import uuid


class JobStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    ANALYZING = "analyzing"
    GENERATING_VOICE = "generating_voice"
    GENERATING_FX = "generating_fx"
    MIXING = "mixing"
    DONE = "done"
    FAILED = "failed"


class SceneType(str, Enum):
    RURAL_VILLAGE = "rural_village"
    IMMORTAL_SECT = "immortal_sect"
    BATTLE = "battle"
    CULTIVATION = "cultivation"
    ROMANCE = "romance"
    MYSTERY = "mystery"
    DISCOVERY = "discovery"
    DIALOG = "dialog"
    NARRATION = "narration"
    DEATH = "death"
    TRIUMPH = "triumph"


class CharacterType(str, Enum):
    NARRATOR = "narrator"
    MALE_YOUNG = "male_young"
    MALE_ELDER = "male_elder"
    MALE_VILLAIN = "male_villain"
    FEMALE_YOUNG = "female_young"
    FEMALE_ELDER = "female_elder"
    CHILD = "child"
    IMMORTAL = "immortal"
    DEMON = "demon"


class VideoOrientation(str, Enum):
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"


class MediaKind(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"


class Character(BaseModel):
    name: str
    character_type: CharacterType = CharacterType.MALE_YOUNG
    description: Optional[str] = None
    voice_config: Optional[Dict[str, str]] = None


class TextSegment(BaseModel):
    """A single chunk of text with analysis annotations"""

    index: int
    text: str
    is_dialog: bool = False
    speaker: Optional[str] = None  # Character name if dialog
    character_type: CharacterType = CharacterType.NARRATOR
    scene_type: SceneType = SceneType.NARRATION
    emotion: str = "neutral"  # calm, tense, climax, sad, joyful
    emotion_intensity: float = 0.5  # 0.0 - 1.0
    sfx_triggers: List[str] = Field(default_factory=list)  # SFX file names
    bgm_change: Optional[str] = None  # BGM file if scene changes
    voice_file: Optional[str] = None  # Generated TTS file path
    voice_profile: Optional[Dict[str, str]] = None
    speaker_key: Optional[str] = None
    duration_sec: float = 0.0


class Chapter(BaseModel):
    """A parsed chapter from PDF/EPUB"""

    index: int
    title: str
    raw_text: str
    segments: List[TextSegment] = Field(default_factory=list)
    characters: List[Character] = Field(default_factory=list)


class AudioTimeline(BaseModel):
    """The final mixing timeline"""

    segments: List[TextSegment]
    total_duration_sec: float = 0.0
    bgm_timeline: List[Dict[str, Any]] = Field(default_factory=list)
    sfx_timeline: List[Dict[str, Any]] = Field(default_factory=list)


class Job(BaseModel):
    """Main job tracking model"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.PENDING
    filename: str
    file_path: str
    media_kind: MediaKind = MediaKind.AUDIO
    total_chapters: int = 0
    processed_chapters: int = 0
    chapters: List[Chapter] = Field(default_factory=list)
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    meta: Dict[str, Any] = Field(default_factory=dict)

    # Progress per agent
    progress: Dict[str, int] = Field(
        default_factory=lambda: {
            "parsing": 0,
            "analyzing": 0,
            "voice": 0,
            "fx": 0,
            "mixing": 0,
        }
    )


class UploadResponse(BaseModel):
    job_id: str
    message: str
    filename: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    media_kind: MediaKind = MediaKind.AUDIO
    progress: Dict[str, int]
    total_chapters: int
    processed_chapters: int
    error: Optional[str] = None
    download_url: Optional[str] = None
    output_path: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class VideoClipSuggestion(BaseModel):
    index: int
    start_sec: float
    end_sec: float
    duration_sec: float
    summary: str
    hook: Optional[str] = None
    subtitles: List[str] = Field(default_factory=list)


class VideoAnalyzeRequest(BaseModel):
    asset_path: str
    orientation: VideoOrientation = VideoOrientation.VERTICAL
    max_clip_seconds: int = 45


class VideoAnalyzeResponse(BaseModel):
    source_path: str
    orientation: VideoOrientation
    duration_sec: float
    width: int
    height: int
    fps: Optional[float] = None
    target_width: int
    target_height: int
    transcript_source: Optional[str] = None
    highlights: List[str] = Field(default_factory=list)
    suggested_clips: List[VideoClipSuggestion] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class VideoReviewRequest(BaseModel):
    source_url: Optional[str] = None
    orientation: VideoOrientation = VideoOrientation.VERTICAL
    max_duration_sec: int = 45
    style: str = "review_short"


class VideoReviewResult(BaseModel):
    title: str
    transcript: str
    review_script: str
    orientation: VideoOrientation
    output_path: str
    subtitles_path: Optional[str] = None
    selected_ranges: List[Dict[str, float]] = Field(default_factory=list)
