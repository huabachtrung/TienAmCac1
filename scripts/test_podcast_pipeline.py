"""Full end-to-end test of the Podcast pipeline."""
import sys, os, asyncio, traceback
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.core.podcast_engine import PodcastEngine

URL = "https://vnexpress.net/iran-canh-bao-eo-bien-hormuz-se-la-mo-chon-tau-san-bay-my-5069370.html"
JOB_ID = "test_podcast_001"

async def main():
    engine = PodcastEngine()
    try:
        result = await engine.generate_from_url(JOB_ID, URL)
        print(f"\nSUCCESS: {result}")
    except Exception as e:
        print(f"\nFAILED: {type(e).__name__}: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
