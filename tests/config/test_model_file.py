import json
import stat

import pytest

from openviking.config.model_file import read_model_file, save_model_file
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def model_file(tmp_path, monkeypatch):
    path = tmp_path / "custom.conf"
    raw = {
        "vlm": {
            "model": "gpt-4o",
            "provider": "openai",
            "api_key": "${STUDIO_TEST_KEY}",
            "temperature": 0.4,
        },
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimension": 1024,
                "api_key": "${STUDIO_TEST_KEY}",
                "extra_body": {"routing": "keep"},
            }
        },
        "rerank": {"provider": "jev", "api_key": "${STUDIO_TEST_KEY}", "model": "jev-latest"},
        "storage": {"workspace": str(tmp_path / "data")},
    }
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("STUDIO_TEST_KEY", "resolved-secret")
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file", path)
    from openviking.config.model_file import _validate

    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", _validate(raw))
    return path, raw


def test_file_models_preserve_environment_references_and_save_without_publishing(model_file):
    path, raw = model_file
    result = read_model_file()
    assert result["file_path"] == str(path)
    assert result["writable"] and not result["restart_required"]
    assert result["models"]["query_planner"]["source"] == "vlm"
    assert "resolved-secret" not in json.dumps(result)
    assert (
        result["models"]["embedding"]["config"]["dense"]["credentials"][0]["api_key"]
        == "${STUDIO_TEST_KEY}"
    )
    save_model_file({"vlm": {"timeout": 42}}, result["revision"])
    saved = json.loads(path.read_text())
    assert saved["vlm"] == {**raw["vlm"], "timeout": 42}
    assert saved["storage"] == raw["storage"]
    assert saved["embedding"] == raw["embedding"]
    backup = path.with_name(path.name + ".studio.bak")
    assert json.loads(backup.read_text()) == raw
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert OpenVikingConfigSingleton.get_instance().vlm.timeout != 42
    assert read_model_file()["restart_required"]


def test_stale_revision_readonly_invalid_and_non_model_writes_leave_file_unchanged(model_file):
    path, _ = model_file
    revision = read_model_file()["revision"]
    original = path.read_bytes()
    for settings, version in [
        ({"vlm": {"timeout": 30}}, "stale"),
        ({"storage": {}}, revision),
        ({"rerank": {"mode": "invalid"}}, revision),
        ({"vlm": None}, revision),
    ]:
        with pytest.raises(ValueError):
            save_model_file(settings, version)
        assert path.read_bytes() == original
    path.chmod(0o444)
    assert not read_model_file()["writable"]
    with pytest.raises(ValueError, match="read-only"):
        save_model_file({"vlm": {"timeout": 30}}, revision)
    assert path.read_bytes() == original


def test_embedding_binding_rotation_preserves_implicit_input_and_identity(model_file):
    path, raw = model_file
    result = read_model_file()
    save_model_file(
        {"embedding": {"dense": {"credentials": [{"provider": "openai", "api_key": "rotated"}]}}},
        result["revision"],
    )
    dense = json.loads(path.read_text())["embedding"]["dense"]
    assert "input" not in dense
    assert dense["model"] == raw["embedding"]["dense"]["model"]
    assert dense["dimension"] == 1024 and dense["extra_body"] == {"routing": "keep"}


def test_programmatic_configuration_never_guesses_a_file(monkeypatch):
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file", None)
    with pytest.raises(ValueError, match="without a configuration file"):
        read_model_file()


def test_jev_headers_can_be_cleared_and_planner_can_inherit(model_file):
    path, raw = model_file
    raw["query_planner"] = {"provider": "openai", "api_key": "planner-key", "model": "gpt-4o"}
    raw["rerank"]["extra_headers"] = {"obsolete": "secret"}
    path.write_text(json.dumps(raw))
    result = read_model_file()
    save_model_file({"query_planner": None, "rerank": {"extra_headers": {}}}, result["revision"])
    stored = json.loads(path.read_text())
    assert "query_planner" not in stored
    assert stored["rerank"]["extra_headers"] == {}
    assert read_model_file()["models"]["query_planner"]["source"] == "vlm"


def test_legacy_backup_credentials_keep_distinct_keys(model_file):
    path, raw = model_file
    raw["vlm"]["backup"] = {
        "api_key": "backup-secret",
        "model": "gpt-4o-mini",
        "provider": "openai",
    }
    path.write_text(json.dumps(raw))
    bindings = read_model_file()["models"]["vlm"]["config"]["credentials"]
    assert bindings[0]["api_key"] == "${STUDIO_TEST_KEY}"
    assert bindings[1]["api_key"] == "backup-secret"


def test_embedding_credentials_display_parent_fallbacks(model_file):
    path, raw = model_file
    raw["embedding"]["dense"]["credentials"] = [{"id": "primary", "provider": "openai"}]
    path.write_text(json.dumps(raw))
    binding = read_model_file()["models"]["embedding"]["config"]["dense"]["credentials"][0]
    assert binding["model"] == "text-embedding-3-small"
    assert binding["api_key"] == "${STUDIO_TEST_KEY}"


@pytest.mark.parametrize("kind", ["vlm", "embedding"])
def test_cleared_legacy_binding_fields_do_not_return(model_file, kind):
    path, raw = model_file
    section = raw["vlm"] if kind == "vlm" else raw["embedding"]["dense"]
    section.update(api_base="https://old.example/v1", extra_headers={"X-Old": "obsolete"})
    path.write_text(json.dumps(raw))
    result = read_model_file()
    config = result["models"][kind]["config"]
    binding_section = config if kind == "vlm" else config["dense"]
    binding_section["credentials"][0].update(api_base=None, extra_headers={})
    save_model_file({kind: config}, result["revision"])
    stored = json.loads(path.read_text())
    stored_section = stored[kind] if kind == "vlm" else stored[kind]["dense"]
    assert "api_base" not in stored_section and "api_key" not in stored_section
    current = read_model_file()["models"][kind]["config"]
    current_section = current if kind == "vlm" else current["dense"]
    assert not current_section["credentials"][0].get("api_base")
    assert not current_section["credentials"][0].get("extra_headers")
    assert current_section["credentials"][0]["api_key"] == "${STUDIO_TEST_KEY}"


def test_partial_credentials_preserve_inherited_fields_and_environment_references(model_file):
    path, raw = model_file
    raw["vlm"].update(api_base="${STUDIO_TEST_URL}", extra_headers={"X-Keep": "yes"})
    path.write_text(json.dumps(raw))
    result = read_model_file()
    save_model_file({"vlm": {"credentials": [{"model": "gpt-4o-mini"}]}}, result["revision"])
    current = read_model_file()["models"]["vlm"]["config"]["credentials"][0]
    assert current["api_base"] == "${STUDIO_TEST_URL}"
    assert current["api_key"] == "${STUDIO_TEST_KEY}"
    assert current["extra_headers"] == {"X-Keep": "yes"}


def test_provider_switch_does_not_reuse_legacy_headers_or_endpoint(model_file):
    path, raw = model_file
    raw["vlm"].update(
        provider="volcengine",
        api_base="https://old.example/api/v3",
        extra_headers={"Authorization": "old"},
    )
    path.write_text(json.dumps(raw))
    result = read_model_file()
    config = result["models"]["vlm"]["config"]
    config["credentials"][0].update(
        provider="openai", api_key="new-key", api_base=None, extra_headers=None
    )
    save_model_file({"vlm": config}, result["revision"])
    binding = read_model_file()["models"]["vlm"]["config"]["credentials"][0]
    assert binding["provider"] == "openai" and binding["api_key"] == "new-key"
    assert not binding.get("api_base") and not binding.get("extra_headers")


def test_keyless_embedding_does_not_inherit_legacy_key(model_file):
    path, _ = model_file
    result = read_model_file()
    save_model_file(
        {
            "embedding": {
                "dense": {
                    "credentials": [
                        {
                            "provider": "openai",
                            "api_key": None,
                            "api_base": "http://localhost:8000/v1",
                        }
                    ]
                }
            }
        },
        result["revision"],
    )
    current = read_model_file()["models"]["embedding"]["config"]["dense"]["credentials"][0]
    assert not current.get("api_key")
    assert current["api_base"] == "http://localhost:8000/v1"
