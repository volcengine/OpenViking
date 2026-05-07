from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read_text(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_console_add_resource_payload_uses_to_field():
    app_js = _read_text("openviking/console/static/app.js")

    assert "function buildAddResourcePayload()" in app_js
    assert "to: elements.addResourceTarget.value.trim()," in app_js
    assert "target: elements.addResourceTarget.value.trim()," not in app_js
