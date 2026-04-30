from backend.agents.local_voice_engine import LocalVoiceEngine
from pathlib import Path

try:
    engine = LocalVoiceEngine()
    engine.synthesize_text("Đây là câu thử nghiệm", Path("backend/assets/voice_samples/test_out.wav"))
    print("Success!")
except Exception as e:
    import traceback
    traceback.print_exc()
