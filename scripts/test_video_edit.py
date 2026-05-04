"""Test video edit endpoint with a YouTube URL."""
import requests
import time
import json
import sys
import os
os.environ["PYTHONIOENCODING"] = "utf-8"

BASE_URL = "http://localhost:8000"

def test_video_edit():
    print("=== Testing Video Edit ===")
    r = requests.post(f"{BASE_URL}/api/video/edit", data={
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "orientation": "vertical",
        "style": "creator_viral",
        "keep_full_video": "true",
    })
    print(f"Submit: {r.status_code}")
    if r.status_code != 200:
        print("FAILED to submit:", r.text[:300])
        return

    job_id = r.json()["job_id"]
    print(f"Job ID: {job_id}")
    for i in range(90):
        time.sleep(5)
        s = requests.get(f"{BASE_URL}/api/jobs/{job_id}")
        data = s.json()
        status = data["status"]
        err = data.get("error", "")
        print(f"  [{i:2d}] {status} | err={err[:80] if err else 'none'}")
        if status in ("done", "failed"):
            if status == "done":
                print(f"  OUTPUT: {data.get('output_path')}")
                print(f"  DOWNLOAD: {data.get('download_url')}")
            else:
                detail = data.get("meta", {}).get("error_detail", {})
                if detail:
                    print(f"  DETAIL: {json.dumps(detail, ensure_ascii=False)[:500]}")
            break

if __name__ == "__main__":
    test_video_edit()
