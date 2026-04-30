"""
download_assets.py — TienAmCac Asset Downloader
================================================
Script chạy một lần để tải SFX/BGM vào thư mục assets cố định.

Nguồn:
  SFX — Freesound.org (Creative Commons 0 / CC BY 4.0)
         Cần FREESOUND_API_KEY trong backend/.env
  BGM — Pixabay Music (Royalty-free, thương mại OK)
         Cần PIXABAY_API_KEY trong backend/.env

Nếu không có API key, script sẽ sinh built-in asset bằng pydub.

Cách chạy:
  cd C:\\Users\\Admin\\Desktop\\TienAmCac
  backend\\.venv-win\\Scripts\\python backend/scripts/download_assets.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# ─── Resolve project root ──────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parent.parent   # TienAmCac/backend/
_ROOT = _BACKEND.parent           # TienAmCac/

# Đảm bảo import được backend package
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.config import settings          # noqa: E402  (after sys.path fix)

# ─── Asset lists ───────────────────────────────────────────────────────────────

SFX_ASSETS = {
    "sfx_sword_draw.wav":     {"query": "sword draw metal swoosh",   "duration": "[0.3 TO 3]"},
    "sfx_sword_clash.wav":    {"query": "sword clash metal impact",  "duration": "[0.3 TO 3]"},
    "sfx_punch_impact.wav":   {"query": "punch thud body hit",       "duration": "[0.2 TO 2]"},
    "sfx_fire_blast.wav":     {"query": "fire whoosh blast flame",   "duration": "[0.5 TO 4]"},
    "sfx_thunder_strike.wav": {"query": "thunder lightning strike",   "duration": "[0.5 TO 5]"},
    "sfx_ice_shatter.wav":    {"query": "ice shatter break crack",   "duration": "[0.3 TO 3]"},
    "sfx_wind_spirit.wav":    {"query": "wind whoosh spiritual",     "duration": "[0.5 TO 4]"},
    "sfx_qi_gathering.wav":   {"query": "energy power up charge",    "duration": "[0.5 TO 5]"},
    "sfx_qi_explosion.wav":   {"query": "energy burst explosion orb","duration": "[0.5 TO 4]"},
    "sfx_footsteps_stone.wav":{"query": "footsteps stone walk indoor","duration": "[0.5 TO 5]"},
    "sfx_rush_whoosh.wav":    {"query": "whoosh fast rush movement", "duration": "[0.2 TO 2]"},
    "sfx_crowd_gasp.wav":     {"query": "crowd gasp reaction surprise","duration": "[0.5 TO 5]"},
    "sfx_big_explosion.wav":  {"query": "explosion boom large blast","duration": "[0.5 TO 6]"},
    "sfx_rumble_collapse.wav":{"query": "rumble collapse debris rock","duration": "[1 TO 8]"},
}

BGM_ASSETS = {
    "bgm_rural_village.mp3": "chinese folk countryside peaceful",
    "bgm_immortal_sect.mp3": "chinese ancient flute meditation serene",
    "bgm_epic_battle.mp3":   "epic chinese battle orchestra action",
    "bgm_cultivation.mp3":   "zen meditation spiritual ambient calm",
    "bgm_romance.mp3":       "chinese romantic erhu gentle piano",
    "bgm_mystery.mp3":       "mysterious dark ambient tension",
    "bgm_discovery.mp3":     "adventure discovery strings wonder",
    "bgm_ambient_light.mp3": "soft ambient background peaceful quiet",
    "bgm_somber.mp3":        "sad emotional piano melancholic",
    "bgm_triumph.mp3":       "triumphant victory orchestra uplifting",
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def _log(msg: str):
    # Encode-safe print for Windows CP1252 consoles
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def _save_manifest(directory: Path, manifest: dict):
    manifest_path = directory / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _log(f"  ✓ Manifest saved → {manifest_path}")


# ─── Freesound downloader ──────────────────────────────────────────────────────

def _download_freesound_sfx(api_key: str, sfx_dir: Path) -> dict:
    """Download SFX from Freesound.org. Returns manifest."""
    try:
        import httpx
    except ImportError:
        _log("  ✗ httpx not installed. Run: pip install httpx")
        return {}

    manifest = {}
    headers = {}
    _log(f"\n[SFX] Downloading from Freesound.org → {sfx_dir}")

    for filename, params in SFX_ASSETS.items():
        out_path = sfx_dir / filename
        if out_path.exists() and out_path.stat().st_size > 500:
            _log(f"  ✓ Already exists: {filename}")
            manifest[filename] = {"source": "freesound", "local": str(out_path)}
            continue

        try:
            search_params = {
                "query": params["query"],
                "token": api_key,
                "format": "json",
                "fields": "id,name,previews,duration",
                "filter": f"duration:{params['duration']} license:(\"Creative Commons 0\" OR \"Attribution\")",
                "sort": "score",
                "page_size": 5,
            }
            with httpx.Client(timeout=20.0) as client:
                resp = client.get("https://freesound.org/apiv2/search/text/", params=search_params)
                data = resp.json()

            results = data.get("results", [])
            if not results:
                _log(f"  ✗ No results for: {filename} — using built-in")
                continue

            # Prefer preview-hq-mp3 (no OAuth needed)
            preview_url = results[0]["previews"].get("preview-hq-mp3") or results[0]["previews"].get("preview-lq-mp3")
            if not preview_url:
                _log(f"  ✗ No preview URL for: {filename}")
                continue

            with httpx.Client(timeout=30.0) as client:
                audio_resp = client.get(preview_url)

            # preview is mp3, convert to wav extension anyway (pydub will handle format)
            raw_path = sfx_dir / (filename.replace(".wav", "_raw.mp3"))
            raw_path.write_bytes(audio_resp.content)

            # Convert to WAV with correct sample rate
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(raw_path))
                audio = audio.set_frame_rate(settings.SAMPLE_RATE)
                audio.export(str(out_path), format="wav")
                raw_path.unlink(missing_ok=True)
                _log(f"  ✓ Downloaded + converted: {filename} ({len(audio)/1000:.1f}s)")
                manifest[filename] = {
                    "source": "freesound",
                    "query": params["query"],
                    "local": str(out_path),
                }
            except Exception as conv_err:
                _log(f"  ✗ Conversion failed for {filename}: {conv_err}")
                raw_path.rename(out_path.with_suffix(".mp3"))

            time.sleep(0.4)  # Rate limit courtesy delay

        except Exception as exc:
            _log(f"  ✗ Freesound error for {filename}: {exc}")

    return manifest


# ─── Pixabay downloader ────────────────────────────────────────────────────────

def _download_pixabay_bgm(api_key: str, bgm_dir: Path) -> dict:
    """Download BGM from Pixabay Music API. Returns manifest."""
    try:
        import httpx
    except ImportError:
        _log("  ✗ httpx not installed.")
        return {}

    manifest = {}
    _log(f"\n[BGM] Downloading from Pixabay → {bgm_dir}")

    for filename, query in BGM_ASSETS.items():
        out_path = bgm_dir / filename
        if out_path.exists() and out_path.stat().st_size > 5000:
            _log(f"  ✓ Already exists: {filename}")
            manifest[filename] = {"source": "pixabay", "local": str(out_path)}
            continue

        try:
            url = (
                f"https://pixabay.com/api/videos/music/"
                f"?key={api_key}&q={query.replace(' ', '+')}&per_page=5"
            )
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url)
                data = resp.json()

            hits = data.get("hits", [])
            if not hits:
                _log(f"  ✗ No results for: {filename} — will use built-in")
                continue

            # Pixabay music API returns audio download URLs
            audio_url = hits[0].get("audio", {}).get("url") or hits[0].get("previewURL")
            if not audio_url:
                _log(f"  ✗ No audio URL for: {filename}")
                continue

            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                audio_resp = client.get(audio_url)
            out_path.write_bytes(audio_resp.content)
            _log(f"  ✓ Downloaded: {filename} ({len(audio_resp.content)//1024} KB)")
            manifest[filename] = {
                "source": "pixabay",
                "query": query,
                "local": str(out_path),
            }
            time.sleep(0.3)

        except Exception as exc:
            _log(f"  ✗ Pixabay error for {filename}: {exc}")

    return manifest


# ─── Built-in fallback ─────────────────────────────────────────────────────────

def _generate_builtin_assets(sfx_dir: Path, bgm_dir: Path,
                              sfx_done: set, bgm_done: set):
    """Generate synthetic SFX/BGM for any files not yet downloaded."""
    try:
        from backend.agents.audio_fx_engine import AudioFXEngine
    except ImportError:
        from agents.audio_fx_engine import AudioFXEngine  # type: ignore

    engine = AudioFXEngine()

    # SFX built-in
    missing_sfx = [f for f in SFX_ASSETS if f not in sfx_done and not (sfx_dir / f).exists()]
    if missing_sfx:
        _log(f"\n[SFX] Generating {len(missing_sfx)} built-in SFX...")
        for filename in missing_sfx:
            out_path = sfx_dir / filename
            engine._create_builtin_sfx(out_path, filename)
            size = out_path.stat().st_size if out_path.exists() else 0
            _log(f"  ✓ Built-in SFX: {filename} ({size} bytes)")

    # BGM built-in
    missing_bgm = [f for f in BGM_ASSETS if f not in bgm_done and not (bgm_dir / f).exists()]
    if missing_bgm:
        _log(f"\n[BGM] Generating {len(missing_bgm)} built-in BGMs...")
        for filename in missing_bgm:
            out_path = bgm_dir / filename
            engine._create_builtin_bgm(out_path, filename, duration_ms=60_000)
            size = out_path.stat().st_size if out_path.exists() else 0
            _log(f"  ✓ Built-in BGM: {filename} ({size//1024} KB)")


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    sfx_dir = settings.SFX_DIR
    bgm_dir = settings.BGM_DIR
    sfx_dir.mkdir(parents=True, exist_ok=True)
    bgm_dir.mkdir(parents=True, exist_ok=True)

    freesound_key = settings.FREESOUND_API_KEY.strip()
    pixabay_key   = settings.PIXABAY_API_KEY.strip()

    _log("=" * 60)
    _log("  TienAmCac Asset Downloader")
    _log("=" * 60)
    _log(f"  SFX dir : {sfx_dir}")
    _log(f"  BGM dir : {bgm_dir}")
    _log(f"  Freesound API key : {'✓ set' if freesound_key else '✗ missing (will use built-in)'}")
    _log(f"  Pixabay API key   : {'✓ set' if pixabay_key else '✗ missing (will use built-in)'}")
    _log("=" * 60)

    sfx_manifest: dict = {}
    bgm_manifest: dict = {}

    # Download real assets if API keys are present
    if freesound_key:
        sfx_manifest = _download_freesound_sfx(freesound_key, sfx_dir)
    
    if pixabay_key:
        bgm_manifest = _download_pixabay_bgm(pixabay_key, bgm_dir)

    # Build-in fallback for anything missing
    sfx_done = set(sfx_manifest.keys())
    bgm_done = set(bgm_manifest.keys())
    _generate_builtin_assets(sfx_dir, bgm_dir, sfx_done, bgm_done)

    # Save manifests
    _save_manifest(sfx_dir, sfx_manifest)
    _save_manifest(bgm_dir, bgm_manifest)

    # Final summary
    sfx_count = sum(1 for f in SFX_ASSETS if (sfx_dir / f).exists())
    bgm_count = sum(1 for f in BGM_ASSETS if (bgm_dir / f).exists())
    _log("\n" + "=" * 60)
    _log(f"  ✅ SFX ready: {sfx_count}/{len(SFX_ASSETS)}")
    _log(f"  ✅ BGM ready: {bgm_count}/{len(BGM_ASSETS)}")
    _log("=" * 60)


if __name__ == "__main__":
    main()
