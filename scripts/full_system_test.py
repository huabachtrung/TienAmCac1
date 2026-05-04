"""
TienAmCac - Full System Diagnostic Test
Tests all components: config, models, connections, voice engine, API
"""
import sys, os, time, json, traceback

if __name__ != "__main__":
    import pytest

    pytest.skip("full_system_test is a standalone diagnostic script", allow_module_level=True)

os.environ["PYTHONPATH"] = r"c:\Users\Admin\Desktop\TienAmCac"
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
sys.path.insert(0, r"c:\Users\Admin\Desktop\TienAmCac")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PASS = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"
results = []

def test(name, fn):
    try:
        result = fn()
        if result is True or result is None:
            results.append((name, True, "OK"))
            print(f"  {PASS} {name}")
        else:
            results.append((name, True, str(result)))
            print(f"  {PASS} {name}: {result}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  {FAIL} {name}: {e}")

# ============================================================
print("\n" + "="*60)
print("  TIÊN ÂM CÁC - FULL SYSTEM DIAGNOSTIC")
print("="*60)

# --- 1. CONFIG ---
print("\n[1/7] Configuration & Environment")
print("-" * 40)

def test_env_file():
    from backend.config import settings
    assert settings.VOICE_PROVIDER == "local_f5", f"Expected local_f5, got {settings.VOICE_PROVIDER}"
    return f"VOICE_PROVIDER={settings.VOICE_PROVIDER}"
test("Backend .env loaded", test_env_file)

def test_strict_mode():
    from backend.config import settings
    assert settings.STRICT_QUALITY_MODE is True
test("STRICT_QUALITY_MODE=true", test_strict_mode)

def test_dirs():
    from backend.config import settings
    dirs = {
        "UPLOAD_DIR": settings.UPLOAD_DIR,
        "OUTPUT_DIR": settings.OUTPUT_DIR,
        "BGM_DIR": settings.BGM_DIR,
        "SFX_DIR": settings.SFX_DIR,
        "VOICE_SAMPLES_DIR": settings.VOICE_SAMPLES_DIR,
    }
    for name, d in dirs.items():
        assert d.exists(), f"{name} does not exist: {d}"
    return f"{len(dirs)} directories verified"
test("Asset directories exist", test_dirs)

# --- 2. FFMPEG ---
print("\n[2/7] FFmpeg & Audio")
print("-" * 40)

def test_ffmpeg():
    import shutil
    path = shutil.which("ffmpeg")
    assert path, "ffmpeg not found in PATH"
    return path
test("ffmpeg available", test_ffmpeg)

def test_pydub():
    from pydub import AudioSegment
    seg = AudioSegment.silent(duration=100)
    assert len(seg) == 100
test("pydub functional", test_pydub)

# --- 3. F5-TTS MODEL ---
print("\n[3/7] F5-TTS Model Files")
print("-" * 40)

def test_checkpoint():
    from backend.config import settings
    ckpt = settings.LOCAL_TTS_CKPT_FILE
    assert ckpt.exists(), f"Checkpoint not found: {ckpt}"
    size_mb = ckpt.stat().st_size / 1024 / 1024
    return f"{ckpt.name} ({size_mb:.0f} MB)"
test("Checkpoint file", test_checkpoint)

def test_vocab():
    from backend.config import settings
    vocab = settings.LOCAL_TTS_VOCAB_FILE
    assert vocab.exists(), f"Vocab not found: {vocab}"
    lines = open(vocab, "r", encoding="utf-8").readlines()
    return f"{vocab.name} ({len(lines)} tokens)"
test("Vocab file", test_vocab)

def test_vocab_match():
    from backend.config import settings
    vocab = settings.LOCAL_TTS_VOCAB_FILE
    lines = open(vocab, "r", encoding="utf-8").readlines()
    # Checkpoint expects 2567 = vocab_size(2566) + 1 filler
    assert len(lines) == 2566, f"Vocab has {len(lines)} lines, checkpoint expects 2566"
    return f"2566 tokens -> 2567 embeddings (matched)"
test("Vocab-Checkpoint compatibility", test_vocab_match)

def test_ref_audio():
    from backend.config import settings
    ref = settings.LOCAL_TTS_REF_AUDIO
    assert ref and ref.exists(), f"Reference audio not found: {ref}"
    size_kb = ref.stat().st_size / 1024
    return f"{ref.name} ({size_kb:.0f} KB)"
test("Reference audio", test_ref_audio)

# --- 4. F5-TTS IMPORT ---
print("\n[4/7] F5-TTS Runtime")
print("-" * 40)

def test_f5_import():
    import f5_tts
    return f"f5_tts package found"
test("f5_tts package import", test_f5_import)

def test_f5_cli():
    import shutil
    from backend.config import settings
    cmd = settings.LOCAL_TTS_COMMAND
    path = shutil.which(cmd)
    assert path, f"CLI not found: {cmd}"
    return path
test("f5-tts CLI binary", test_f5_cli)

def test_local_voice_engine():
    from backend.agents.local_voice_engine import LocalVoiceEngine
    engine = LocalVoiceEngine()
    engine.healthcheck()
    return f"provider={engine.provider_name}"
test("LocalVoiceEngine healthcheck", test_local_voice_engine)

# --- 5. OLLAMA ---
print("\n[5/7] Ollama (AI Scene Analysis)")
print("-" * 40)

def test_ollama_running():
    import urllib.request
    try:
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
        data = json.loads(req.read())
        models = [m["name"] for m in data.get("models", [])]
        return f"{len(models)} models: {', '.join(models[:5])}"
    except Exception as e:
        raise RuntimeError(f"Ollama not running: {e}")
test("Ollama server", test_ollama_running)

def test_ollama_model():
    from backend.config import settings
    import urllib.request
    model = settings.OLLAMA_MODEL
    req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=5)
    data = json.loads(req.read())
    models = [m["name"] for m in data.get("models", [])]
    found = any(model in m for m in models)
    if not found:
        raise RuntimeError(f"Model '{model}' not found. Available: {models}")
    return f"{model}"
test("Ollama model available", test_ollama_model)

# --- 6. BACKEND API ---
print("\n[6/7] FastAPI Backend")
print("-" * 40)

def test_fastapi_import():
    from backend.main import app
    routes = [r.path for r in app.routes if hasattr(r, 'path')]
    api_routes = [r for r in routes if r.startswith("/api")]
    return f"{len(api_routes)} API endpoints"
test("FastAPI app import", test_fastapi_import)

def test_agents_import():
    from backend.agents.video_edit_agents import DirectorAgent, QualityGateAgent
    return "DirectorAgent, QualityGateAgent"
test("Agent team import", test_agents_import)

def test_orchestrator_import():
    from backend.core.video_edit_orchestrator import run_video_edit_pipeline
    return "run_video_edit_pipeline"
test("Orchestrator import", test_orchestrator_import)

# --- 7. VOICE SYNTHESIS TEST ---
print("\n[7/7] Voice Synthesis (End-to-End)")
print("-" * 40)

def test_voice_synthesis():
    from backend.agents.local_voice_engine import LocalVoiceEngine
    from pathlib import Path
    import tempfile
    engine = LocalVoiceEngine()
    out = Path("backend/assets/voice_samples/system_test_output.wav")
    try:
        engine.synthesize_text("Xin chào, đây là bài kiểm tra hệ thống", out)
        assert out.exists(), "Output file not created"
        size_kb = out.stat().st_size / 1024
        assert size_kb > 1, f"Output too small: {size_kb:.1f} KB"
        return f"Generated {size_kb:.0f} KB audio"
    finally:
        if out.exists():
            pass  # keep for manual inspection
test("F5-TTS voice generation", test_voice_synthesis)

# ============================================================
print("\n" + "="*60)
print("  SUMMARY")
print("="*60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
print(f"\n  {PASS} Passed: {passed}")
print(f"  {FAIL} Failed: {failed}")
print(f"  Total:  {len(results)}")

if failed > 0:
    print(f"\n  Failed tests:")
    for name, ok, msg in results:
        if not ok:
            print(f"    {FAIL} {name}: {msg}")

print("\n" + "="*60)
sys.exit(0 if failed == 0 else 1)
