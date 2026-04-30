"""Face-aware vertical reframing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from loguru import logger

try:
    from ..config import settings
    from ..models.schemas import VideoOrientation
except ImportError:
    from config import settings
    from models.schemas import VideoOrientation


class SmartReframer:
    def build_filter(
        self,
        orientation: VideoOrientation,
        source_path: Path,
        start_time: float,
        end_time: float,
    ) -> tuple[str, Dict[str, object]]:
        if orientation == VideoOrientation.HORIZONTAL:
            return (
                "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                "crop=1920:1080,boxblur=18:8[bg];"
                "[0:v]scale=1728:972:force_original_aspect_ratio=decrease[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]",
                {"mode": "horizontal_blur_frame"},
            )

        track = self.detect_crop_track(source_path, start_time, end_time)
        if track.get("x_offset") is not None:
            x_offset = int(track["x_offset"])
            return (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=18:8[bg];"
                f"[0:v]crop=w=ih*9/16:h=ih:x={x_offset}:y=0,"
                "scale=1080:1920[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]",
                track,
            )
        return (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=18:8[bg];"
            "[0:v]scale=900:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[vout]",
            {**track, "mode": "safe_fit_fallback"},
        )

    def detect_crop_track(self, source_path: Path, start_time: float, end_time: float) -> Dict[str, object]:
        if not settings.VIDEO_SMART_CROP_ENABLED:
            return {"mode": "disabled", "x_offset": None, "detections": []}
        try:
            import cv2
        except ImportError:
            logger.warning("[SmartReframer] opencv-python unavailable.")
            return {"mode": "opencv_missing", "x_offset": None, "detections": []}

        cap = cv2.VideoCapture(str(source_path))
        if not cap.isOpened():
            return {"mode": "open_failed", "x_offset": None, "detections": []}
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        crop_width = int(height * 9 / 16)
        if crop_width <= 0 or crop_width >= width:
            cap.release()
            return {"mode": "already_vertical_or_square", "x_offset": None, "detections": []}

        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        mp_detector = self._create_mediapipe_detector()
        start_frame = max(0, int(start_time * fps))
        end_frame = max(start_frame + 1, int(end_time * fps))
        step = max(1, int(fps / max(settings.VIDEO_FACE_TRACK_SAMPLE_FPS, 0.5)))
        detections: List[Dict[str, float]] = []
        centers: List[float] = []
        for frame_no in range(start_frame, end_frame, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
            ok, frame = cap.read()
            if not ok:
                break
            faces = self._detect_faces_mediapipe(mp_detector, frame, width, height)
            if not faces:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = [
                    tuple(int(value) for value in rect)
                    for rect in cascade.detectMultiScale(
                        gray, scaleFactor=1.08, minNeighbors=4, minSize=(48, 48)
                    )
                ]
            if not faces:
                continue
            face = max(faces, key=lambda rect: rect[2] * rect[3])
            x, y, w, h = [int(value) for value in face]
            center = x + w / 2
            centers.append(center)
            detections.append({"time": round(frame_no / fps, 2), "x": x, "y": y, "w": w, "h": h, "center": round(center, 2)})
        cap.release()

        if not centers:
            return {"mode": "no_face_detected", "x_offset": None, "detections": []}
        centers = self._trim_outliers(centers)
        smooth_center = sum(centers) / len(centers)
        padding = crop_width * 0.08
        x_offset = int(smooth_center - crop_width / 2 - padding)
        x_offset = max(0, min(x_offset, width - crop_width))
        return {
            "mode": "mediapipe_face_track" if mp_detector else "opencv_face_track",
            "x_offset": x_offset,
            "source_width": width,
            "source_height": height,
            "crop_width": crop_width,
            "detections": detections[:20],
        }

    def _trim_outliers(self, centers: List[float]) -> List[float]:
        if len(centers) < 5:
            return centers
        ordered = sorted(centers)
        trim = max(1, len(ordered) // 8)
        return ordered[trim:-trim] or centers

    def _create_mediapipe_detector(self):
        try:
            import mediapipe as mp
        except ImportError:
            return None
        try:
            return mp.solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.45,
            )
        except Exception as exc:
            logger.warning(f"[SmartReframer] MediaPipe detector unavailable: {exc}")
            return None

    def _detect_faces_mediapipe(self, detector, frame, width: int, height: int) -> List[tuple[int, int, int, int]]:
        if detector is None:
            return []
        try:
            import cv2
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = detector.process(rgb)
            faces = []
            for detection in result.detections or []:
                box = detection.location_data.relative_bounding_box
                x = max(0, int(box.xmin * width))
                y = max(0, int(box.ymin * height))
                w = min(width - x, int(box.width * width))
                h = min(height - y, int(box.height * height))
                if w > 0 and h > 0:
                    faces.append((x, y, w, h))
            return faces
        except Exception:
            return []
