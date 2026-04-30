# TienAmCac - Audiobook & Video Review AI Agent

TienAmCac là một hệ thống AI mạnh mẽ giúp tạo audiobook và video review từ truyện hoặc video nguồn với chất lượng cao, sử dụng các mô hình local AI (F5-TTS, VLM).

## Tính năng chính

- **Audiobook Generator**: Chuyển đổi `.pdf`, `.epub`, `.txt` thành audiobook với giọng đọc tự nhiên, nhạc nền và hiệu ứng âm thanh.
- **Video Review Agent**: Tự động phân tích video, viết kịch bản review, sinh giọng đọc và tạo video review với phụ đề khớp voice.
- **Local AI Priority**: Sử dụng Vietnamese F5-TTS và Qwen2.5-VL (via Ollama) để đảm bảo chất lượng và quyền riêng tư.
- **Smart Framing**: Tự động crop video (9:16) giữ chủ thể/mặt người trong khung hình.

## Yêu cầu hệ thống

- **OS**: Windows (đã tối ưu hóa)
- **Hardware**: Khuyến nghị GPU NVIDIA (RTX 2060 6GB VRAM trở lên)
- **Dependencies**:
  - Python 3.9+
  - FFmpeg
  - Ollama (cho VLM)
  - Redis (tùy chọn cho background jobs)

## Cài đặt và Chạy

1. **Clone repository**:
   ```bash
   git clone <your-repo-url>
   cd TienAmCac
   ```

2. **Cài đặt dependencies**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Hoặc .\venv\Scripts\activate trên Windows
   pip install -r requirements.txt
   ```

3. **Chạy ứng dụng**:
   Sử dụng file `start.bat` để khởi động cả Backend và Frontend:
   ```bash
   .\start.bat
   ```

## Cấu trúc dự án

- `backend/`: FastAPI server và các AI Agents.
- `frontend/`: Giao diện người dùng (React/Next.js/HTML).
- `assets/`: Thư mục lưu trữ dữ liệu (đã được ignore trong git cho các file lớn).
- `docs/`: Tài liệu nghiên cứu và hướng dẫn.

## Giấy phép

Dự án sử dụng các mô hình mã nguồn mở. Vui lòng kiểm tra license của từng mô hình (F5-TTS Vietnamese, v.v.) trước khi sử dụng thương mại.
