"""Quick test for video review and video edit endpoints."""
import requests
import time
import json
import sys
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
import sys

BASE_URL = "http://localhost:8000"


def test_video_review():
    print("=== Testing Video Review ===")
    r = requests.post(f"{BASE_URL}/api/video/review", data={
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "orientation": "vertical",
        "max_duration_sec": "30",
        "style": "review_short"
    })
    print(f"Submit: {r.status_code} {r.json()}")
    if r.status_code != 200:
        print("FAILED to submit")
        return

    job_id = r.json()["job_id"]
    for i in range(30):
        time.sleep(3)
        s = requests.get(f"{BASE_URL}/api/jobs/{job_id}")
        data = s.json()
        status = data["status"]
        progress = data["progress"]
        error = data.get("error")
        print(f"  Poll {i}: status={status}, progress={progress}, error={error}")
        if status in ("done", "failed"):
            if data.get("meta", {}).get("error_detail"):
                detail = json.dumps(data["meta"]["error_detail"], indent=2, ensure_ascii=False)
                print(f"  Error detail: {detail[:2000]}")
            break


def test_video_edit():
    print("\n=== Testing Video Edit ===")
    r = requests.post(f"{BASE_URL}/api/video/edit", data={
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "orientation": "vertical",
        "style": "creator_viral",
        "keep_full_video": "true",
    })
    print(f"Submit: {r.status_code} {r.json()}")
    if r.status_code != 200:
        print("FAILED to submit")
        return

    job_id = r.json()["job_id"]
    for i in range(30):
        time.sleep(3)
        s = requests.get(f"{BASE_URL}/api/jobs/{job_id}")
        data = s.json()
        status = data["status"]
        progress = data["progress"]
        error = data.get("error")
        print(f"  Poll {i}: status={status}, progress={progress}, error={error}")
        if status in ("done", "failed"):
            if data.get("meta", {}).get("error_detail"):
                detail = json.dumps(data["meta"]["error_detail"], indent=2, ensure_ascii=False)
                print(f"  Error detail: {detail[:2000]}")
            break


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "both"
    if mode in ("review", "both"):
        test_video_review()
    if mode in ("edit", "both"):
        test_video_edit()
