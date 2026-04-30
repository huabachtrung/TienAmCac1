"""Download Vietnamese F5-TTS checkpoint from HuggingFace.

Usage:
    python scripts/download_f5_model.py

Downloads model_last.pt and vocab.txt from hynt/F5-TTS-Vietnamese-ViVoice
to backend/assets/models/f5-tts-vietnamese/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "backend" / "assets" / "models" / "f5-tts-vietnamese"
REPO_ID = "hynt/F5-TTS-Vietnamese-ViVoice"


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    ckpt_path = MODEL_DIR / "model_last.pt"
    vocab_path = MODEL_DIR / "vocab.txt"

    if ckpt_path.exists() and vocab_path.exists():
        print(f"[OK] Model files already exist at {MODEL_DIR}")
        print(f"     - {ckpt_path.name}: {ckpt_path.stat().st_size / 1024 / 1024:.1f} MB")
        print(f"     - {vocab_path.name}: {vocab_path.stat().st_size} bytes")
        return

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("[ERROR] huggingface_hub not installed. Run:")
        print("        pip install huggingface_hub")
        sys.exit(1)

    print(f"[INFO] Downloading F5-TTS Vietnamese from {REPO_ID}...")
    print(f"       Target: {MODEL_DIR}")
    print()

    # Try to download model checkpoint
    if not ckpt_path.exists():
        try:
            # Different repos use different filenames
            for filename in ("model_last.pt", "model.pt", "model_1200000.pt"):
                try:
                    downloaded = hf_hub_download(
                        repo_id=REPO_ID,
                        filename=filename,
                        local_dir=str(MODEL_DIR),
                        local_dir_use_symlinks=False,
                    )
                    final = MODEL_DIR / "model_last.pt"
                    dl_path = Path(downloaded)
                    if dl_path.name != "model_last.pt" and dl_path.exists():
                        dl_path.rename(final)
                    print(f"  [OK] Downloaded checkpoint: {final.name} ({final.stat().st_size / 1024 / 1024:.1f} MB)")
                    break
                except Exception:
                    continue
            else:
                print(f"  [WARN] Could not find checkpoint file in {REPO_ID}")
                print("         Try a different repo: danhtran2mind/Vi-F5-TTS")
        except Exception as exc:
            print(f"  [ERROR] Checkpoint download failed: {exc}")

    # Try to download vocab
    if not vocab_path.exists():
        try:
            for filename in ("vocab.txt", "vocab_vi.txt"):
                try:
                    downloaded = hf_hub_download(
                        repo_id=REPO_ID,
                        filename=filename,
                        local_dir=str(MODEL_DIR),
                        local_dir_use_symlinks=False,
                    )
                    final = MODEL_DIR / "vocab.txt"
                    dl_path = Path(downloaded)
                    if dl_path.name != "vocab.txt" and dl_path.exists():
                        dl_path.rename(final)
                    print(f"  [OK] Downloaded vocab: {final.name} ({final.stat().st_size} bytes)")
                    break
                except Exception:
                    continue
            else:
                print(f"  [WARN] Could not find vocab file in {REPO_ID}")
        except Exception as exc:
            print(f"  [ERROR] Vocab download failed: {exc}")

    # Summary
    print()
    if ckpt_path.exists() and vocab_path.exists():
        print("[SUCCESS] F5-TTS Vietnamese model ready!")
        print(f"  Checkpoint: {ckpt_path}")
        print(f"  Vocab:      {vocab_path}")
        print()
        print("Next steps:")
        print("  1. Place a Vietnamese reference audio (5-15s) at:")
        print(f"     {ROOT / 'backend' / 'assets' / 'voice_samples' / 'reference_vi.wav'}")
        print("  2. Set LOCAL_TTS_REF_TEXT in .env to the exact transcript of that audio")
        print("  3. Install F5-TTS: pip install f5-tts")
    else:
        print("[INCOMPLETE] Some files are missing. Check output above.")
        print("  Manual download: https://huggingface.co/hynt/F5-TTS-Vietnamese-ViVoice")
        sys.exit(1)


if __name__ == "__main__":
    main()
