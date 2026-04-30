FROM python:3.11-slim

# Cài đặt các gói hệ thống bắt buộc (đặc biệt là ffmpeg)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy thư viện và cài đặt (để cache lớp này nếu không đổi file requirements)
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ source code backend vào container
COPY backend /app/backend

# Thiết lập PYTHONPATH để module backend được tìm thấy
ENV PYTHONPATH=/app

# Default command để chạy web server
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
