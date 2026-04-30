from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_video_edit_tab_and_form_are_exposed_in_static_ui():
    html = (ROOT / "backend" / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "backend" / "static" / "styles.css").read_text(encoding="utf-8")

    required_ids = [
        "modeAudio",
        "modeVideo",
        "modeEdit",
        "audioForm",
        "videoForm",
        "editForm",
        "editUploadZone",
        "editFileInput",
        "btnBrowseEdit",
        "editSourceUrl",
        "editOrientation",
        "editStyle",
        "btnStartEdit",
    ]

    for element_id in required_ids:
        assert f'id="{element_id}"' in html

    assert "Video Edit" in html
    assert "styles.css?v=20260430-video-edit" in html
    assert "app.js?v=20260430-video-edit" in html
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
