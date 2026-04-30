# Video Summary Feature

## Muc tieu

Mo rong he thong tu audiobook sang video review ngan:

- Video nguon da nam trong `assets/video_sources/` hoac mot thu muc asset hop le.
- Backend doc metadata video, transcript sidecar (`.txt`, `.srt`, `.vtt`) neu co.
- He thong de xuat cac clip ngan de dang short-form.
- Co option `vertical` hoac `horizontal`, nhung uu tien `vertical`.

## API da duoc scaffold

- `POST /api/video/analyze`

Payload:

```json
{
  "asset_path": "video_sources/trailer.mp4",
  "orientation": "vertical",
  "max_clip_seconds": 45
}
```

Response tra ve:

- metadata co ban cua video
- kich thuoc canvas muc tieu
- transcript sidecar neu tim thay
- danh sach highlight ban dau
- danh sach clip goi y de render

## Pipeline de xuat cho ban trien khai day du

1. `VideoIngestor`
   - xac thuc file video, trich metadata, snapshot keyframe.
2. `TranscriptEngine`
   - uu tien sidecar transcript.
   - neu khong co, dung ASR nhu Whisper, faster-whisper, hoac Groq Whisper API.
3. `VideoSummaryEngine`
   - tom tat transcript thanh hook, beats, CTA, subtitle line.
   - fallback rule-based neu LLM khong san sang.
4. `ClipSelector`
   - chon moc thoi gian hay nhat.
   - uu tien clip 20-45 giay cho short-form doc.
5. `VideoReframer`
   - vertical: crop/reframe 1080x1920.
   - horizontal: giu 1920x1080.
   - neu co nhan dien chu the thi center vao khuon mat/nhan vat.
6. `SubtitleRenderer`
   - burnt-in subtitle voi font ro, 2 dong, safe-margin cho TikTok/Reels/Shorts.
7. `VideoMixer`
   - mix voiceover, nhac nen, subtitle, intro card, CTA card.
8. `Exporter`
   - ghi output vao `backend/assets/video_output/<job_id>/`.

## Rang buoc can giu

- Khong de ASR/LLM lam fail toan bo job neu co transcript sidecar.
- Vertical la default.
- Video output can co preset rieng cho TikTok/Reels/Shorts va preset ngang cho YouTube/Facebook.
- Neu khong co transcript, he thong van nen phan tich metadata va tao "processing plan" thay vi fail ngay.
