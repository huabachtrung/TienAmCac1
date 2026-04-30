"""
TIÊN ÂM CÁC — Core Configuration
Centralized settings using pydantic-settings
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = True

    # Redis / Celery
    REDIS_URL: str = "redis://localhost:6379/0"

    # AI Models
    OLLAMA_BASE_URL: str = Field(default="http://localhost:11434")
    OLLAMA_MODEL: str = Field(
        default="gemma4:e4b"
    )  # Changed to Google's optimized 4B model for better processing
    GROQ_API_KEY: Optional[str] = Field(default=None)

    # Voice settings
    # High-quality mode uses a local Vietnamese F5-TTS runtime. edge-tts is kept
    # only as an explicit legacy provider.
    VOICE_PROVIDER: str = Field(default="local_f5")
    STRICT_QUALITY_MODE: bool = Field(default=True)
    LOCAL_TTS_COMMAND: str = Field(default="f5-tts_infer-cli")
    LOCAL_TTS_COMMAND_TEMPLATE: Optional[str] = Field(default=None)
    LOCAL_TTS_MODEL_DIR: Path = BASE_DIR / "assets" / "models" / "f5-tts-vietnamese"
    LOCAL_TTS_CKPT_FILE: Optional[Path] = Field(default=None)
    LOCAL_TTS_VOCAB_FILE: Optional[Path] = Field(default=None)
    LOCAL_TTS_REF_AUDIO: Optional[Path] = Field(default=None)
    LOCAL_TTS_REF_TEXT: str = Field(default="")
    LOCAL_TTS_SPEED: float = Field(default=1.0)
    # "female" = vi-VN-HoaiMyNeural narrator in legacy edge mode.
    # "male"   = vi-VN-NamMinhNeural narrator in legacy edge mode.
    NARRATOR_GENDER: str = Field(default="female")

    # Freesound API (free SFX)
    FREESOUND_API_KEY: str = ""

    # Pixabay API (free music)
    PIXABAY_API_KEY: str = ""

    # File paths
    UPLOAD_DIR: Path = BASE_DIR / "assets" / "uploads"
    OUTPUT_DIR: Path = BASE_DIR / "assets" / "output"
    BGM_DIR: Path = BASE_DIR / "assets" / "bgm"
    SFX_DIR: Path = BASE_DIR / "assets" / "sfx"
    VOICE_SAMPLES_DIR: Path = BASE_DIR / "assets" / "voice_samples"
    VIDEO_SOURCE_DIR: Path = BASE_DIR.parent / "assets" / "video_sources"
    VIDEO_OUTPUT_DIR: Path = BASE_DIR / "assets" / "video_output"
    VIDEO_TEMP_DIR: Path = BASE_DIR / "assets" / "video_temp"

    # Audio settings
    OUTPUT_FORMAT: str = "mp3"
    OUTPUT_BITRATE: str = "192k"
    SAMPLE_RATE: int = 44100
    MASTER_LUFS: int = -14
    BGM_VOLUME_NORMAL: int = -24
    BGM_VOLUME_DUCKED: int = -34
    SFX_VOLUME: int = -10

    # Video review settings
    VIDEO_ASR_MODEL: str = "tiny"
    VIDEO_REVIEW_MAX_SECONDS: int = 45
    VIDEO_VERTICAL_SIZE: str = "1080x1920"
    VIDEO_HORIZONTAL_SIZE: str = "1920x1080"
    VIDEO_VISION_REQUIRED: bool = Field(default=True)
    VIDEO_VISION_MODEL: str = Field(default="qwen2.5vl:3b")
    VIDEO_KEYFRAME_COUNT: int = Field(default=6)
    VIDEO_SMART_CROP_ENABLED: bool = Field(default=True)
    VIDEO_FACE_TRACK_SAMPLE_FPS: float = Field(default=2.0)

    class Config:
        env_file = str(BASE_DIR / ".env")
        env_file_encoding = "utf-8"


settings = Settings()
settings.UPLOAD_DIR = (
    (BASE_DIR / settings.UPLOAD_DIR).resolve()
    if not settings.UPLOAD_DIR.is_absolute()
    else settings.UPLOAD_DIR
)
settings.OUTPUT_DIR = (
    (BASE_DIR / settings.OUTPUT_DIR).resolve()
    if not settings.OUTPUT_DIR.is_absolute()
    else settings.OUTPUT_DIR
)
settings.BGM_DIR = (
    (BASE_DIR / settings.BGM_DIR).resolve()
    if not settings.BGM_DIR.is_absolute()
    else settings.BGM_DIR
)
settings.SFX_DIR = (
    (BASE_DIR / settings.SFX_DIR).resolve()
    if not settings.SFX_DIR.is_absolute()
    else settings.SFX_DIR
)
settings.VOICE_SAMPLES_DIR = (
    (BASE_DIR / settings.VOICE_SAMPLES_DIR).resolve()
    if not settings.VOICE_SAMPLES_DIR.is_absolute()
    else settings.VOICE_SAMPLES_DIR
)
settings.LOCAL_TTS_MODEL_DIR = (
    (BASE_DIR / settings.LOCAL_TTS_MODEL_DIR).resolve()
    if not settings.LOCAL_TTS_MODEL_DIR.is_absolute()
    else settings.LOCAL_TTS_MODEL_DIR
)
if settings.LOCAL_TTS_CKPT_FILE is None:
    settings.LOCAL_TTS_CKPT_FILE = settings.LOCAL_TTS_MODEL_DIR / "model_last.pt"
elif not settings.LOCAL_TTS_CKPT_FILE.is_absolute():
    settings.LOCAL_TTS_CKPT_FILE = (BASE_DIR / settings.LOCAL_TTS_CKPT_FILE).resolve()
if settings.LOCAL_TTS_VOCAB_FILE is None:
    settings.LOCAL_TTS_VOCAB_FILE = settings.LOCAL_TTS_MODEL_DIR / "vocab.txt"
elif not settings.LOCAL_TTS_VOCAB_FILE.is_absolute():
    settings.LOCAL_TTS_VOCAB_FILE = (BASE_DIR / settings.LOCAL_TTS_VOCAB_FILE).resolve()
if settings.LOCAL_TTS_REF_AUDIO and not settings.LOCAL_TTS_REF_AUDIO.is_absolute():
    settings.LOCAL_TTS_REF_AUDIO = (BASE_DIR / settings.LOCAL_TTS_REF_AUDIO).resolve()
settings.VIDEO_SOURCE_DIR = (
    (BASE_DIR / settings.VIDEO_SOURCE_DIR).resolve()
    if not settings.VIDEO_SOURCE_DIR.is_absolute()
    else settings.VIDEO_SOURCE_DIR
)
settings.VIDEO_OUTPUT_DIR = (
    (BASE_DIR / settings.VIDEO_OUTPUT_DIR).resolve()
    if not settings.VIDEO_OUTPUT_DIR.is_absolute()
    else settings.VIDEO_OUTPUT_DIR
)
settings.VIDEO_TEMP_DIR = (
    (BASE_DIR / settings.VIDEO_TEMP_DIR).resolve()
    if not settings.VIDEO_TEMP_DIR.is_absolute()
    else settings.VIDEO_TEMP_DIR
)

# Voice map cho các kiểu nhân vật
# QUAN TRỌNG: vi-VN-HoaiMyNeural và vi-VN-NamMinhNeural là Neural voices chuẩn.
# Pitch shift lớn (> ±10Hz) hoặc rate quá âm/dương sẽ tạo giọng robot.
# Giữ pitch gần +0Hz và rate gần +0% để nghe tự nhiên nhất.
VOICE_MAP = {
    # Narrator: tốc độ chậm nhẹ để dễ nghe truyện, pitch chuẩn
    "narrator": {"voice": "vi-VN-HoaiMyNeural", "rate": "-8%", "pitch": "+0Hz"},
    # Nam nhân vật chính trẻ: giọng tự nhiên, không biến đổi
    "male_young": {"voice": "vi-VN-NamMinhNeural", "rate": "+0%", "pitch": "+0Hz"},
    # Nam lão thành: chậm hơn nhẹ, pitch thấp hơn ít
    "male_elder": {"voice": "vi-VN-NamMinhNeural", "rate": "-12%", "pitch": "-6Hz"},
    # Phản diện nam: chậm hơn nhẹ, pitch thấp
    "male_villain": {"voice": "vi-VN-NamMinhNeural", "rate": "-5%", "pitch": "-8Hz"},
    # Nữ nhân vật trẻ: giọng chuẩn, nhẹ nhanh hơn
    "female_young": {"voice": "vi-VN-HoaiMyNeural", "rate": "+2%", "pitch": "+0Hz"},
    # Nữ lão thành: chậm nhẹ
    "female_elder": {"voice": "vi-VN-HoaiMyNeural", "rate": "-8%", "pitch": "-4Hz"},
    # Trẻ em: nhanh hơn nhẹ, pitch cao hơn nhẹ (không quá ±10Hz)
    "child": {"voice": "vi-VN-HoaiMyNeural", "rate": "+8%", "pitch": "+6Hz"},
    # Tiên nhân/thần linh: chậm, trang nghiêm
    "immortal": {"voice": "vi-VN-NamMinhNeural", "rate": "-15%", "pitch": "-4Hz"},
    # Ma quỷ: thấp hơn nhẹ, không quá giả tạo
    "demon": {"voice": "vi-VN-NamMinhNeural", "rate": "-8%", "pitch": "-10Hz"},
    # Chủ nhân công nam
    "protagonist_male": {
        "voice": "vi-VN-NamMinhNeural",
        "rate": "+2%",
        "pitch": "+0Hz",
    },
    # Chủ nhân công nữ
    "protagonist_female": {
        "voice": "vi-VN-HoaiMyNeural",
        "rate": "+2%",
        "pitch": "+0Hz",
    },
}

# Scene → BGM mapping
SCENE_BGM_MAP = {
    "rural_village": "bgm_rural_village.mp3",
    "immortal_sect": "bgm_immortal_sect.mp3",
    "battle": "bgm_epic_battle.mp3",
    "cultivation": "bgm_cultivation.mp3",
    "romance": "bgm_romance.mp3",
    "mystery": "bgm_mystery.mp3",
    "discovery": "bgm_discovery.mp3",
    "dialog": "bgm_ambient_light.mp3",
    "narration": "bgm_ambient_light.mp3",
    "death": "bgm_somber.mp3",
    "triumph": "bgm_triumph.mp3",
}

# Keyword → SFX mapping
SFX_TRIGGER_MAP = {
    # Vũ khí
    "kiếm": "sfx_sword_draw.wav",
    "đao": "sfx_sword_draw.wav",
    "chém": "sfx_sword_clash.wav",
    "đánh": "sfx_punch_impact.wav",
    "va chạm": "sfx_sword_clash.wav",
    # Nguyên tố / Kỹ thuật
    "lửa": "sfx_fire_blast.wav",
    "hỏa": "sfx_fire_blast.wav",
    "sét": "sfx_thunder_strike.wav",
    "lôi": "sfx_thunder_strike.wav",
    "băng": "sfx_ice_shatter.wav",
    "phong": "sfx_wind_spirit.wav",
    "tu luyện": "sfx_qi_gathering.wav",
    "đột phá": "sfx_qi_explosion.wav",
    "linh lực": "sfx_qi_gathering.wav",
    # Di chuyển
    "bước chân": "sfx_footsteps_stone.wav",
    "phi hành": "sfx_wind_spirit.wav",
    "lao tới": "sfx_rush_whoosh.wav",
    # Đám đông / Cảnh lớn
    "đám đông": "sfx_crowd_gasp.wav",
    "nổ": "sfx_big_explosion.wav",
    "sụp đổ": "sfx_rumble_collapse.wav",
    # Moi truong / dao cu. These keys are UTF-8 literals so local analysis can
    # map concrete story descriptions to a fixed offline SFX library.
    "nước chảy": "sfx_water_stream.wav",
    "suối": "sfx_water_stream.wav",
    "mở cửa": "sfx_door_creak.wav",
    "cửa gỗ": "sfx_door_creak.wav",
    "chuông": "sfx_bell_chime.wav",
    "ngựa": "sfx_horse_gallop.wav",
    "mũi tên": "sfx_arrow_fly.wav",
    "yêu thú": "sfx_beast_roar.wav",
    "gầm": "sfx_beast_roar.wav",
}
