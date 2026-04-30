"""
AGENT #4 — AudioFXEngine
Creates or fetches BGM/SFX assets and prepares a timeline for mixing.
"""
from pathlib import Path
from typing import Optional, Dict

import httpx
from loguru import logger

try:
    from ..audio_utils import configure_pydub
    from ..config import settings
    from ..models.schemas import Chapter
except ImportError:
    from audio_utils import configure_pydub
    from config import settings
    from models.schemas import Chapter


class AudioFXEngine:
    """Ensures audible BGM and SFX even without external API keys."""

    def __init__(self):
        self.bgm_dir = settings.BGM_DIR
        self.sfx_dir = settings.SFX_DIR
        self.bgm_dir.mkdir(parents=True, exist_ok=True)
        self.sfx_dir.mkdir(parents=True, exist_ok=True)
        self.freesound_key = settings.FREESOUND_API_KEY
        self.ffmpeg_bin = configure_pydub()

    async def prepare_chapter_assets(self, chapter: Chapter) -> Dict[str, str]:
        logger.info(f"[FXBot] Preparing assets for chapter: {chapter.title}")
        assets: Dict[str, str] = {}

        bgm_needed = {seg.bgm_change for seg in chapter.segments if seg.bgm_change}
        sfx_needed = {sfx for seg in chapter.segments for sfx in seg.sfx_triggers}

        for bgm_file in bgm_needed:
            path = await self._ensure_bgm(bgm_file)
            if path:
                assets[bgm_file] = str(path)

        for sfx_file in sfx_needed:
            path = await self._ensure_sfx(sfx_file)
            if path:
                assets[sfx_file] = str(path)

        logger.info(f"[FXBot] ✓ {len(assets)} assets ready")
        return assets

    async def _ensure_bgm(self, bgm_filename: str) -> Optional[Path]:
        local_path = self.bgm_dir / bgm_filename
        if local_path.exists():
            return local_path

        logger.info(f"[FXBot] BGM not found locally: {bgm_filename}. Searching...")
        if settings.PIXABAY_API_KEY:
            downloaded = await self._download_pixabay_music(bgm_filename, local_path)
            if downloaded:
                return local_path

        self._create_builtin_bgm(local_path, bgm_filename)
        logger.warning(f"[FXBot] Created built-in BGM for {bgm_filename}")
        return local_path

    async def _download_pixabay_music(self, bgm_filename: str, out_path: Path) -> bool:
        search_map = {
            "bgm_rural_village": "chinese folk countryside peaceful",
            "bgm_immortal_sect": "chinese ancient flute meditation",
            "bgm_epic_battle": "epic chinese battle orchestra",
            "bgm_cultivation": "zen meditation spiritual ambient",
            "bgm_romance": "chinese romantic erhu gentle",
            "bgm_mystery": "mysterious dark ambient",
            "bgm_discovery": "adventure discovery strings",
            "bgm_ambient_light": "soft ambient background quiet",
            "bgm_somber": "sad emotional piano",
            "bgm_triumph": "triumphant victory orchestra",
        }
        stem = bgm_filename.replace(".mp3", "")
        query = search_map.get(stem, "chinese instrumental music")

        try:
            url = f"https://pixabay.com/api/?key={settings.PIXABAY_API_KEY}&q={query}&response_group=high_resolution&category=music&per_page=5"
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
                data = response.json()
            hits = data.get("hits", [])
            if not hits:
                return False

            audio_url = hits[0].get("audio", {}).get("url") or hits[0].get("previewURL")
            if not audio_url:
                return False

            async with httpx.AsyncClient(timeout=20.0) as client:
                audio_response = await client.get(audio_url)
            out_path.write_bytes(audio_response.content)
            logger.info(f"[FXBot] ✓ Downloaded BGM from Pixabay: {bgm_filename}")
            return True
        except Exception as exc:
            logger.warning(f"[FXBot] Pixabay download failed: {exc}")
            return False

    def _create_builtin_bgm(self, out_path: Path, bgm_filename: str, duration_ms: int = 30000):
        from pydub import AudioSegment
        from pydub.generators import Sine, Triangle, WhiteNoise

        scene_profiles = {
            "bgm_rural_village.mp3": {"tones": [262, 330, 392], "noise": -42, "pulse": 1800},
            "bgm_immortal_sect.mp3": {"tones": [220, 294, 440], "noise": -44, "pulse": 2400},
            "bgm_epic_battle.mp3": {"tones": [110, 165, 220], "noise": -34, "pulse": 700},
            "bgm_cultivation.mp3": {"tones": [196, 247, 294], "noise": -46, "pulse": 2600},
            "bgm_romance.mp3": {"tones": [262, 330, 494], "noise": -48, "pulse": 2200},
            "bgm_mystery.mp3": {"tones": [147, 220, 277], "noise": -40, "pulse": 1700},
            "bgm_discovery.mp3": {"tones": [196, 294, 392], "noise": -44, "pulse": 1500},
            "bgm_ambient_light.mp3": {"tones": [220, 330, 440], "noise": -50, "pulse": 2100},
            "bgm_somber.mp3": {"tones": [131, 196, 247], "noise": -44, "pulse": 2500},
            "bgm_triumph.mp3": {"tones": [196, 294, 392], "noise": -38, "pulse": 1000},
        }
        profile = scene_profiles.get(bgm_filename, scene_profiles["bgm_ambient_light.mp3"])

        bed = AudioSegment.silent(duration=duration_ms)
        for index, freq in enumerate(profile["tones"]):
            pad = Sine(freq).to_audio_segment(duration=duration_ms).apply_gain(-35 - index * 2)
            pad = pad.fade_in(1200).fade_out(1500)
            bed = bed.overlay(pad)

        pulse_length = profile["pulse"]
        marker = Triangle(profile["tones"][0] * 2).to_audio_segment(duration=max(250, pulse_length // 3)).apply_gain(-33)
        pulse_track = AudioSegment.silent(duration=duration_ms)
        cursor = 0
        while cursor < duration_ms:
            pulse_track = pulse_track.overlay(marker.fade_out(200), position=cursor)
            cursor += pulse_length
        bed = bed.overlay(pulse_track)

        atmosphere = WhiteNoise().to_audio_segment(duration=duration_ms).low_pass_filter(900).apply_gain(profile["noise"])
        bed = bed.overlay(atmosphere).fade_in(800).fade_out(1200)
        bed.export(str(out_path), format="mp3", bitrate=settings.OUTPUT_BITRATE)

    async def _ensure_sfx(self, sfx_filename: str) -> Optional[Path]:
        local_path = self.sfx_dir / sfx_filename
        if local_path.exists():
            return local_path

        logger.info(f"[FXBot] SFX not found locally: {sfx_filename}. Searching Freesound...")
        if self.freesound_key:
            downloaded = await self._download_freesound_sfx(sfx_filename, local_path)
            if downloaded:
                return local_path

        self._create_builtin_sfx(local_path, sfx_filename)
        logger.warning(f"[FXBot] Created built-in SFX for {sfx_filename}")
        return local_path if local_path.exists() else None

    async def _download_freesound_sfx(self, sfx_filename: str, out_path: Path) -> bool:
        query = sfx_filename.replace("sfx_", "").replace(".wav", "").replace("_", " ")
        try:
            params = {
                "query": query,
                "token": self.freesound_key,
                "format": "json",
                "fields": "id,name,previews",
                "filter": "duration:[0.3 TO 5]",
                "page_size": 5,
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get("https://freesound.org/apiv2/search/text/", params=params)
                data = response.json()
            results = data.get("results", [])
            if not results:
                return False

            preview_url = results[0]["previews"]["preview-hq-mp3"]
            async with httpx.AsyncClient(timeout=20.0) as client:
                audio_response = await client.get(preview_url)
            out_path.write_bytes(audio_response.content)
            logger.info(f"[FXBot] ✓ Downloaded SFX from Freesound: {sfx_filename}")
            return True
        except Exception as exc:
            logger.warning(f"[FXBot] Freesound download failed: {exc}")
            return False

    def _create_builtin_sfx(self, out_path: Path, sfx_filename: str):
        from pydub import AudioSegment
        from pydub.generators import Sine, Square, WhiteNoise

        def burst(freq: int, duration: int, gain: int, fade_out: int = 120):
            return Sine(freq).to_audio_segment(duration=duration).apply_gain(gain).fade_out(fade_out)

        if sfx_filename == "sfx_sword_draw.wav":
            audio = WhiteNoise().to_audio_segment(duration=420).high_pass_filter(1200).apply_gain(-16).fade_in(20).fade_out(250)
        elif sfx_filename == "sfx_sword_clash.wav":
            audio = burst(1200, 140, -9).overlay(burst(860, 220, -11)).overlay(WhiteNoise().to_audio_segment(duration=180).apply_gain(-20))
        elif sfx_filename == "sfx_punch_impact.wav":
            audio = burst(90, 180, -6, 160).overlay(burst(180, 120, -12))
        elif sfx_filename == "sfx_fire_blast.wav":
            audio = WhiteNoise().to_audio_segment(duration=650).low_pass_filter(1400).apply_gain(-18).fade_in(40).fade_out(300)
        elif sfx_filename == "sfx_thunder_strike.wav":
            audio = burst(70, 800, -10, 500).overlay(WhiteNoise().to_audio_segment(duration=500).apply_gain(-18))
        elif sfx_filename == "sfx_ice_shatter.wav":
            audio = burst(1500, 240, -14).overlay(WhiteNoise().to_audio_segment(duration=260).high_pass_filter(1800).apply_gain(-22))
        elif sfx_filename == "sfx_wind_spirit.wav":
            audio = WhiteNoise().to_audio_segment(duration=600).high_pass_filter(700).apply_gain(-24).fade_in(60).fade_out(250)
        elif sfx_filename == "sfx_qi_gathering.wav":
            audio = burst(220, 700, -20, 250).overlay(burst(330, 700, -22, 250)).fade_in(300)
        elif sfx_filename == "sfx_qi_explosion.wav":
            audio = burst(110, 450, -7, 250).overlay(WhiteNoise().to_audio_segment(duration=260).apply_gain(-16))
        elif sfx_filename == "sfx_footsteps_stone.wav":
            step = burst(120, 90, -15, 60).overlay(Square(240).to_audio_segment(duration=40).apply_gain(-24))
            audio = AudioSegment.silent(60).overlay(step, position=0).overlay(step, position=240)
        elif sfx_filename == "sfx_rush_whoosh.wav":
            audio = WhiteNoise().to_audio_segment(duration=400).high_pass_filter(1000).apply_gain(-20).fade_in(20).fade_out(200)
        elif sfx_filename == "sfx_crowd_gasp.wav":
            audio = WhiteNoise().to_audio_segment(duration=700).high_pass_filter(500).low_pass_filter(2200).apply_gain(-25).fade_in(50).fade_out(300)
        elif sfx_filename == "sfx_big_explosion.wav":
            audio = burst(60, 900, -4, 600).overlay(WhiteNoise().to_audio_segment(duration=500).apply_gain(-14))
        elif sfx_filename == "sfx_rumble_collapse.wav":
            audio = burst(55, 1200, -10, 800).overlay(WhiteNoise().to_audio_segment(duration=800).low_pass_filter(500).apply_gain(-20))
        elif sfx_filename == "sfx_water_stream.wav":
            audio = WhiteNoise().to_audio_segment(duration=900).low_pass_filter(1200).high_pass_filter(180).apply_gain(-24).fade_in(120).fade_out(260)
        elif sfx_filename == "sfx_door_creak.wav":
            audio = burst(260, 700, -18, 260).overlay(Square(110).to_audio_segment(duration=420).apply_gain(-24)).fade_in(180).fade_out(260)
        elif sfx_filename == "sfx_bell_chime.wav":
            audio = burst(880, 900, -12, 700).overlay(burst(1320, 700, -18, 500))
        elif sfx_filename == "sfx_horse_gallop.wav":
            hoof = burst(95, 70, -13, 50).overlay(burst(160, 50, -18, 40))
            audio = AudioSegment.silent(duration=900)
            for pos in (0, 130, 340, 470, 680, 810):
                audio = audio.overlay(hoof, position=pos)
        elif sfx_filename == "sfx_arrow_fly.wav":
            audio = WhiteNoise().to_audio_segment(duration=320).high_pass_filter(1600).apply_gain(-22).fade_in(15).fade_out(180)
        elif sfx_filename == "sfx_beast_roar.wav":
            audio = burst(85, 900, -9, 500).overlay(WhiteNoise().to_audio_segment(duration=650).low_pass_filter(700).apply_gain(-20))
        else:
            audio = AudioSegment.silent(duration=250)

        audio = audio.set_frame_rate(settings.SAMPLE_RATE)
        audio.export(str(out_path), format="wav")

    def build_fx_timeline(self, chapter: Chapter, assets: Dict[str, str]) -> Dict:
        timeline = {"bgm_events": [], "sfx_events": []}
        current_time = 0.0
        current_bgm = None

        for seg in chapter.segments:
            if seg.bgm_change and seg.bgm_change != current_bgm and seg.bgm_change in assets:
                timeline["bgm_events"].append(
                    {
                        "time_sec": current_time,
                        "file": assets[seg.bgm_change],
                        "fade_in_sec": 1.4,
                        "fade_out_sec": 1.8,
                        "volume_db": settings.BGM_VOLUME_NORMAL,
                    }
                )
                current_bgm = seg.bgm_change

            for sfx_file in seg.sfx_triggers:
                if sfx_file in assets:
                    timeline["sfx_events"].append(
                        {
                            "time_sec": current_time + 0.15,
                            "file": assets[sfx_file],
                            "volume_db": settings.SFX_VOLUME,
                        }
                    )

            current_time += seg.duration_sec + 0.25

        return timeline

    async def close(self):
        return None
