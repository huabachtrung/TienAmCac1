import asyncio
import os
import sys

try:
    import edge_tts
except ImportError:
    print("edge_tts not found")
    sys.exit(1)

TEXT = "Chào mừng các bạn đến với Tiên Âm Các. Hệ thống sẽ tự động xử lý và mang đến cho bạn một câu chuyện thật sinh động và hấp dẫn."
VOICE = "vi-VN-HoaiMyNeural"
OUTPUT = "backend/assets/voice_samples/reference_vi.mp3"

async def main():
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    communicate = edge_tts.Communicate(TEXT, VOICE)
    await communicate.save(OUTPUT)
    print(f"Generated {OUTPUT} successfully.")
    
if __name__ == "__main__":
    asyncio.run(main())
