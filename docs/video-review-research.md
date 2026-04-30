# Video Review Research

## Muc tieu
- Nhan video tu file local hoac URL YouTube, TikTok.
- Tu dong tom tat noi dung, tao voice review, cat highlight, them subtitle va render video output.
- Ho tro `vertical` cho Shorts, Reels, TikTok va `horizontal` cho YouTube, Facebook.

## Ky thuat edit duoc ap dung
- Hook-first pacing: mo bang mot cau hook ngan, sau do dua 2-3 y chinh va dong review ket.
- Highlight selection theo transcript density: uu tien doan co nhieu tu khoa, sau do bo sung cac moc phan bo deu de tranh lap canh.
- Reframing cho video doc: tao lop nen blur full frame va dat khung goc o giua de tranh crop mat chu the qua manh.
- Hard subtitles: render subtitle vao frame de giu kha nang xem khong can audio tren TikTok va Reels.
- Title card overlay: them tieu de ngan o dau khung de nguoi xem hieu ngay clip dang review gi.
- Narration-led structure: dung voice review lam xuong song chinh, visual chi co vai tro minh hoa va duy tri nhip.
- BGM ducking: giu nen nhac thap hon voice de clip nghe day hon nhung van ro loi.

## Stack ky thuat
- `yt-dlp`: tai video tu nhieu nen tang khi nguoi dung dua URL.
- `faster-whisper`: tach transcript nhanh tren CPU de lam summary va subtitle.
- `ffmpeg`: cat canh, scale, crop, blur background, overlay subtitle va render MP4 cuoi.
- `Ollama gemma4:e4b`: tom tat noi dung video thanh script review ngan, co fallback rule-based neu model khong san sang.
- `VoiceEngine`: tao voice review, uu tien `edge-tts`, fallback local tren Windows.

## Flow hien tai
1. Nguoi dung chon file video hoac URL trong giao dien.
2. Chon `vertical` hoac `horizontal` va gioi han thoi luong.
3. Backend tai video neu can, probe metadata, transcribe audio, tom tat script review.
4. He thong tao voice review, subtitle `.srt`, chon highlight va render MP4 output.
5. Frontend poll `/api/jobs/{job_id}` va hien player audio/video dung theo `media_kind`.

## Huong nang cap tiep
- Scene detection theo histogram/shot boundary thay vi transcript-only.
- Face-aware smart crop de giu nhan vat o trung tam khung doc.
- Keyword-to-broll mapping de chen canh minh hoa neu footage goc qua it bien doi.
- Multi-style templates: review nghiem tuc, reaction nhanh, top-points, recap.
- Auto CTA pack: ending frame, logo, watermark, hashtag caption export.
