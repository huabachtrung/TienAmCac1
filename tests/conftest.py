"""Shared fixtures for TienAmCac tests."""
import sys
from pathlib import Path

# Ensure backend package is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture
def sample_chapter_text():
    """A small Vietnamese novel chapter snippet for testing."""
    return (
        "Chương 1: Khởi đầu\n\n"
        "Lâm Phong bước vào ngôi làng nhỏ. Bầu trời xanh thẳm, gió nhẹ lay động hàng tre.\n\n"
        "— Ngươi là ai? — giọng nói vang lên từ phía sau.\n\n"
        "Lâm Phong quay lại, thấy một cô gái trẻ mặc áo trắng đang nhìn mình.\n\n"
        "— Ta là Lâm Phong, đến từ Thanh Vân Tông.\n\n"
        "Cô gái mỉm cười nhẹ nhàng. Nàng tên là Tiểu Ngọc, con gái của trưởng làng.\n\n"
        "Chương 2: Thử thách\n\n"
        "Sáng hôm sau, Lâm Phong dậy sớm luyện kiếm. Tiếng kiếm vang vọng khắp nơi.\n\n"
        "— Ngươi muốn gia nhập Thanh Vân Tông sao? — trưởng làng hỏi.\n\n"
        "— Đúng vậy, thưa tiền bối.\n\n"
        "Trưởng làng gật đầu rồi đưa cho Lâm Phong một cuốn sách cổ."
    )


@pytest.fixture
def tmp_upload_dir(tmp_path):
    """Temporary upload directory."""
    d = tmp_path / "uploads"
    d.mkdir()
    return d


@pytest.fixture
def tmp_output_dir(tmp_path):
    """Temporary output directory."""
    d = tmp_path / "output"
    d.mkdir()
    return d


@pytest.fixture
def sample_transcript_segments():
    """Sample transcript segments for video edit agent tests."""
    return [
        {"start": 0.0, "end": 2.5, "text": "Xin chào các bạn hôm nay chúng ta cùng xem video", "words": 9},
        {"start": 2.8, "end": 5.1, "text": "Video này có nội dung rất thú vị và đáng xem", "words": 9},
        {"start": 5.5, "end": 8.2, "text": "Đây là phần đánh nhau rất căng thẳng", "words": 7},
        {"start": 8.5, "end": 10.0, "text": "Kết thúc phần review", "words": 3},
    ]


@pytest.fixture
def sample_source_meta():
    """Sample video source metadata."""
    return {
        "duration_sec": 10.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
    }


@pytest.fixture
def sample_audio_analysis():
    """Sample audio analysis result."""
    return {
        "beats": [{"time": 2.0}, {"time": 5.0}, {"time": 8.0}],
        "energy_peaks": [{"time": 5.5, "dbfs": -18.0}],
        "silences": [],
    }


@pytest.fixture
def app_client():
    """FastAPI test client."""
    from fastapi.testclient import TestClient
    from backend.main import app
    return TestClient(app)
