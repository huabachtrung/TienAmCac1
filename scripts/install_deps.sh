#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
BACKEND_DIR="$PROJECT_ROOT/backend"

echo "Khoi tao moi truong Tien Am Cac tai $PROJECT_ROOT"

cd "$BACKEND_DIR"

echo "[1/4] Tao virtualenv"
python3 -m venv .venv
source .venv/bin/activate

echo "[2/4] Cai dat dependencies"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

echo "[3/4] Tao thu muc asset"
mkdir -p assets/uploads assets/output assets/bgm assets/sfx assets/video_output
mkdir -p "$PROJECT_ROOT/assets/video_sources"

echo "[4/4] Khoi tao .env neu can"
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
fi

echo
echo "Run API:"
echo "  cd $PROJECT_ROOT"
echo "  backend/.venv/bin/python -m uvicorn backend.main:app --reload"
echo
echo "Redis va Ollama la tuy chon. Neu khong co Redis, backend se fallback sang background thread."
