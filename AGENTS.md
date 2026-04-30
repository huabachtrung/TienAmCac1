# AGENTS

## Mục tiêu hệ thống
- Người dùng tải lên file truyện `.pdf`, `.epub` hoặc `.txt`, chọn chương bắt đầu/kết thúc, hệ thống tạo audiobook có giọng đọc, nhạc nền và hiệu ứng theo pipeline hiện có.
- Người dùng tải lên video hoặc URL video, hệ thống phải hiểu nội dung bằng transcript + keyframe, viết kịch bản review rõ ràng, tạo voice tự nhiên, subtitle khớp voice và xuất video dọc/ngang đúng framing.
- Hệ thống phải bỏ qua phần mở đầu không phải nội dung truyện như `Tác giả:`, `Giới thiệu`, `Nguồn`, `Văn án`, `Mục lục` trước khi vào chương thật.
- Kết quả cuối cùng phải tải về được từ giao diện web.

## Chuẩn chất lượng bắt buộc
- `STRICT_QUALITY_MODE=true` là mặc định. Không được xuất voice silent, giọng robot, review chung chung hoặc crop dọc máy móc chỉ để job “done”.
- Voice chất lượng cao mặc định dùng local Vietnamese F5-TTS qua `LocalVoiceEngine`. `edge-tts` chỉ là provider legacy khi cấu hình rõ `VOICE_PROVIDER=edge`.
- Nếu thiếu checkpoint, vocab, reference audio/text, local TTS command, VLM local hoặc GPU/VRAM không đáp ứng, job high-quality phải fail rõ lý do thay vì tạo output hời hợt.
- Fallback chỉ áp dụng cho nguồn phụ trợ không quyết định chất lượng chính, ví dụ Redis/Celery, asset BGM/SFX online. Fallback không được che lỗi voice/review/crop trong chế độ strict.

## Workflow audiobook
1. Frontend gửi `POST /api/upload` kèm file và khoảng chương.
2. Backend lưu upload vào `backend/assets/uploads/<job_id>/`.
3. Orchestrator chạy pipeline:
   - `DocumentParser`: đọc truyện và lọc đúng khoảng chương.
   - `SceneAnalyzer`: phân tích cảnh, nhân vật, trigger SFX/BGM. Nếu Ollama không sẵn sàng thì fallback rule-based.
   - `VoiceEngine`: dùng `LocalVoiceEngine` khi `VOICE_PROVIDER=local_f5`; tách hội thoại thành lượt riêng và map giọng theo nhân vật.
   - `AudioFXEngine`: chuẩn bị BGM/SFX. Nếu thiếu asset tải ngoài thì tự sinh asset nội bộ để vẫn có nền/hiệu ứng.
   - `AudioMixer`: mix từng chương và ghép file audio cuối vào `backend/assets/output/<job_id>/`.
4. Frontend poll `GET /api/jobs/{job_id}` tới `done`.
5. Người dùng tải output qua `GET /api/jobs/{job_id}/download`.

## Workflow video review
1. Frontend gửi `POST /api/video/review` kèm file hoặc URL, orientation và max duration.
2. Backend lưu nguồn vào `backend/assets/uploads/<job_id>/`.
3. `VideoReviewEngine` chạy:
   - Probe metadata và ASR bằng `faster-whisper` với `word_timestamps=True`.
   - Trích keyframe, gọi VLM local `VIDEO_VISION_MODEL` để phân tích nhân vật/sự kiện/bối cảnh.
   - Viết kịch bản hook -> bối cảnh -> phân tích -> kết luận dựa trên transcript + keyframe; không chấp nhận nội dung chung chung.
   - Sinh voice theo từng câu/segment bằng local F5-TTS, đo duration thật từng segment.
   - Sinh subtitle từ timeline voice thật, không chia đều theo tổng duration.
   - Chọn highlight theo transcript và render video bằng `SmartReframer` để giữ mặt/chủ thể trong khung 9:16.
4. Output video nằm trong `backend/assets/video_output/<job_id>/`.
5. Job metadata phải ghi `voice_provider`, `vision_model`, `speech_cues`, `subtitles_path`, `selected_ranges`, `crop_plan`.

## Cấu hình local AI
- GPU hiện tại đã kiểm tra: RTX 2060 6GB VRAM, CPU i7-9700, RAM 16GB. Phù hợp thử F5-TTS Vietnamese và VLM nhỏ/quantized, không phù hợp Qwen2.5-VL 7B hoặc model video lớn.
- TTS local cần cấu hình:
  - `VOICE_PROVIDER=local_f5`
  - `LOCAL_TTS_COMMAND=f5-tts_infer-cli`
  - `LOCAL_TTS_CKPT_FILE=backend/assets/models/f5-tts-vietnamese/model_last.pt`
  - `LOCAL_TTS_VOCAB_FILE=backend/assets/models/f5-tts-vietnamese/vocab.txt`
  - `LOCAL_TTS_REF_AUDIO=<file wav/mp3 đã có quyền sử dụng>`
  - `LOCAL_TTS_REF_TEXT=<transcript chính xác của reference audio>`
- Vision local mặc định:
  - `VIDEO_VISION_REQUIRED=true`
  - `VIDEO_VISION_MODEL=qwen2.5vl:3b`
  - `VIDEO_KEYFRAME_COUNT=6`

## Cách chạy khuyến nghị
- Chạy API từ thư mục gốc dự án, giữ tương thích `backend.main:app`:
  - `python -m uvicorn backend.main:app --reload`
- Cần có `ffmpeg`.
- Redis/Celery là tùy chọn. Nếu không có Redis, backend fallback sang background thread.
- Ollama là bắt buộc cho video review strict vì VLM local là thành phần chất lượng chính.

## Ghi chú license
- Các checkpoint Vietnamese F5-TTS hiện dùng cho prototype/research có thể có license non-commercial. Trước khi dùng thương mại phải đổi sang model/dataset có quyền sử dụng phù hợp.
