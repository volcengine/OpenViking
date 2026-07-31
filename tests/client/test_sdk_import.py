import sys
from pathlib import Path


def test_import_openviking_sdk_prefers_workspace_sdk(monkeypatch):
    import openviking_cli._sdk_import as sdk_import

    sdk_root = Path(__file__).resolve().parents[2] / "sdk" / "python"
    for name in list(sys.modules):
        if name == "openviking_sdk" or name.startswith("openviking_sdk."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(sdk_root)])

    sdk = sdk_import.import_openviking_sdk()

    assert Path(sdk.__file__).resolve().is_relative_to(sdk_root)
