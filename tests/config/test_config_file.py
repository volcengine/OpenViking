import hashlib
import json
import os
import stat
import sys

import pytest

from openviking.config.config_file import preview_config_file, read_config_file, save_config_file
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


def _save_form(settings: dict, revision: str) -> dict:
    draft = preview_config_file(read_config_file()["content"], settings)
    return save_config_file(draft["content"], revision)


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "custom.conf"
    raw = {
        "vlm": {
            "model": "gpt-4o",
            "provider": "openai",
            "api_key": "dummy-review-key",
            "temperature": 0.4,
        },
        "embedding": {
            "dense": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "dimension": 1024,
                "api_key": "dummy-review-key",
                "extra_body": {"routing": "keep"},
            }
        },
        "rerank": {"provider": "jev", "api_key": "dummy-review-key", "model": "jev-latest"},
        "storage": {"workspace": str(tmp_path / "data")},
    }
    path.write_text(json.dumps(raw))
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file", path)
    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "_config_file_revision",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    from openviking.config.config_file import _validate

    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", _validate(json.dumps(raw)))
    return path, raw


def test_stale_revision_readonly_invalid_and_non_model_writes_leave_file_unchanged(config_file):
    path, _ = config_file
    revision = read_config_file()["revision"]
    original = path.read_bytes()
    for settings, version in [
        ({"vlm": {"timeout": 30}}, "stale"),
        ({"storage": {}}, revision),
        ({"vlm": {"timeout": "invalid"}}, revision),
        ({"vlm": None}, revision),
    ]:
        with pytest.raises(ValueError):
            _save_form(settings, version)
        assert path.read_bytes() == original
    path.chmod(0o444)
    assert not read_config_file()["writable"]
    with pytest.raises(ValueError, match="read-only"):
        _save_form({"vlm": {"timeout": 30}}, revision)
    assert path.read_bytes() == original


def test_embedding_binding_rotation_preserves_implicit_input_and_identity(config_file):
    path, raw = config_file
    result = read_config_file()
    _save_form(
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
        read_config_file()


def test_embedding_credentials_display_parent_fallbacks(config_file):
    path, raw = config_file
    raw["embedding"]["dense"]["credentials"] = [{"id": "primary", "provider": "openai"}]
    path.write_text(json.dumps(raw))
    binding = read_config_file()["models"]["embedding"]["config"]["dense"]["credentials"][0]
    assert binding["model"] == "text-embedding-3-small"
    assert binding["api_key"] == "dummy-review-key"


@pytest.mark.parametrize("kind", ["vlm", "embedding"])
def test_cleared_legacy_binding_fields_do_not_return(config_file, kind):
    path, raw = config_file
    section = raw["vlm"] if kind == "vlm" else raw["embedding"]["dense"]
    section.update(api_base="https://old.example/v1", extra_headers={"X-Old": "obsolete"})
    path.write_text(json.dumps(raw))
    result = read_config_file()
    config = result["models"][kind]["config"]
    binding_section = config if kind == "vlm" else config["dense"]
    binding_section["credentials"][0].update(api_base=None, extra_headers={})
    _save_form({kind: config}, result["revision"])
    stored = json.loads(path.read_text())
    stored_section = stored[kind] if kind == "vlm" else stored[kind]["dense"]
    assert "api_base" not in stored_section and "api_key" not in stored_section
    current = read_config_file()["models"][kind]["config"]
    current_section = current if kind == "vlm" else current["dense"]
    assert not current_section["credentials"][0].get("api_base")
    assert not current_section["credentials"][0].get("extra_headers")
    assert current_section["credentials"][0]["api_key"] == "dummy-review-key"


def test_partial_credentials_preserve_inherited_fields(config_file):
    path, raw = config_file
    raw["vlm"].update(api_base="https://example.com/v1", extra_headers={"X-Keep": "yes"})
    path.write_text(json.dumps(raw))
    result = read_config_file()
    _save_form({"vlm": {"credentials": [{"model": "gpt-4o-mini"}]}}, result["revision"])
    current = read_config_file()["models"]["vlm"]["config"]["credentials"][0]
    assert current["api_base"] == "https://example.com/v1"
    assert current["api_key"] == "dummy-review-key"
    assert current["extra_headers"] == {"X-Keep": "yes"}


def test_first_vlm_connection_does_not_keep_an_empty_preferred_binding(config_file):
    path, raw = config_file
    raw.pop("vlm")
    path.write_text(json.dumps(raw))
    loaded = read_config_file()
    model = loaded["models"]["vlm"]["config"]
    assert model["credentials"] == []
    model["model"] = "gpt-4o"
    model["credentials"].append(
        {"id": "first", "provider": "openai", "api_key": "test-key", "model": "gpt-4o"}
    )
    draft = preview_config_file(loaded["content"], {"vlm": model})
    save_config_file(draft["content"], loaded["revision"])
    config = OpenVikingConfigSingleton._load_from_file(str(path))
    assert len(config.vlm.credentials) == 1
    assert config.vlm.credentials[0].id == "first"
    assert config.vlm.is_available()


@pytest.mark.parametrize("provider_name", ["openai", " OpenAI ", "OpenAI"])
@pytest.mark.parametrize("existing", [False, True])
def test_partial_credentials_use_the_effective_legacy_provider(
    config_file, provider_name, existing
):
    path, raw = config_file
    legacy = {
        "model": "gpt-4o",
        "providers": {
            provider_name: {"api_key": "dummy-review-key", "api_base": "https://example.com/v1"},
            "litellm": {"api_key": "backup-key"},
        },
    }
    if existing:
        raw["vlm"] = legacy
    else:
        raw.pop("vlm")
    path.write_text(json.dumps(raw))
    loaded = read_config_file()
    changes = {"credentials": [{"model": "gpt-4o-mini"}]}
    if not existing:
        changes = {**legacy, **changes}
    _save_form({"vlm": changes}, loaded["revision"])
    stored = json.loads(path.read_text())["vlm"]
    assert "providers" not in stored
    assert stored["credentials"][0]["api_key"] == "dummy-review-key"
    assert stored["credentials"][0]["api_base"] == "https://example.com/v1"
    config = OpenVikingConfigSingleton._load_from_file(str(path))
    assert config.vlm.is_available()
    assert config.vlm.credentials[0].provider == "openai"
    assert config.vlm.credentials[0].model == "gpt-4o-mini"


def test_provider_switch_does_not_reuse_legacy_headers_or_endpoint(config_file):
    path, raw = config_file
    raw["vlm"].update(
        provider="volcengine",
        api_base="https://old.example/api/v3",
        extra_headers={"Authorization": "old"},
    )
    path.write_text(json.dumps(raw))
    result = read_config_file()
    config = result["models"]["vlm"]["config"]
    config["credentials"][0].update(
        provider="openai", api_key="new-key", api_base=None, extra_headers=None
    )
    _save_form({"vlm": config}, result["revision"])
    binding = read_config_file()["models"]["vlm"]["config"]["credentials"][0]
    assert binding["provider"] == "openai" and binding["api_key"] == "new-key"
    assert not binding.get("api_base") and not binding.get("extra_headers")


def test_keyless_embedding_does_not_inherit_legacy_key(config_file):
    path, _ = config_file
    result = read_config_file()
    _save_form(
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
    current = read_config_file()["models"]["embedding"]["config"]["dense"]["credentials"][0]
    assert not current.get("api_key")
    assert current["api_base"] == "http://localhost:8000/v1"


@pytest.mark.parametrize("null_provider", [False, True])
def test_partial_embedding_credentials_preserve_legacy_backend(config_file, null_provider):
    path, raw = config_file
    dense = raw["embedding"]["dense"]
    dense["backend"] = dense.pop("provider")
    if null_provider:
        dense["provider"] = None
    path.write_text(json.dumps(raw))
    result = read_config_file()
    _save_form(
        {"embedding": {"dense": {"credentials": [{"api_key": "rotated"}]}}}, result["revision"]
    )
    current = read_config_file()["models"]["embedding"]["config"]["dense"]
    assert current["credentials"][0]["provider"] == "openai"
    assert current["credentials"][0]["api_key"] == "rotated"


def test_full_file_draft_preserves_other_sections_and_does_not_publish(config_file):
    path, raw = config_file
    raw["server"] = {"port": 1933, "root_api_key": "dummy-review-key"}
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    revision = read_config_file()["revision"]
    raw["server"]["port"] = 1934
    raw["storage"]["workspace"] = "/tmp/new-workspace"
    raw["query_planner"] = {
        "provider": "openai",
        "model": "planner",
        "api_key": "dummy-review-key",
    }
    text = json.dumps(raw, indent=4) + "\n"
    preview = preview_config_file(text, {"vlm": {"timeout": 48}})
    assert path.read_bytes() == before
    draft = json.loads(preview["content"])
    assert draft["server"] == raw["server"]
    assert draft["storage"] == raw["storage"]
    assert draft["query_planner"] == raw["query_planner"]
    assert draft["vlm"]["timeout"] == 48
    save_config_file(preview["content"], revision)
    assert path.read_text() == preview["content"]
    assert path.with_name(path.name + ".studio.bak").read_bytes() == before
    assert read_config_file()["restart_required"]
    assert OpenVikingConfigSingleton.get_instance().vlm.timeout != 48


@pytest.mark.parametrize(
    "content",
    [
        "{",
        "[]",
        '{"server":{"port":"invalid"}}',
        '{"server":[]}',
        '{"server":{"root_api_key":""}}',
        '{"server":{"auth_mode":"api_key"}}',
        '{"server":{"auth_mode":"dev","host":"0.0.0.0"}}',
    ],
)
def test_invalid_full_file_drafts_leave_disk_unchanged(config_file, content):
    path, _ = config_file
    before = path.read_bytes()
    revision = read_config_file()["revision"]
    with pytest.raises(ValueError):
        preview_config_file(content)
    with pytest.raises(ValueError):
        save_config_file(content, revision)
    assert path.read_bytes() == before


def test_full_file_save_obeys_revision_readonly_and_exact_content(config_file):
    path, raw = config_file
    revision = read_config_file()["revision"]
    text = json.dumps({**raw, "server": {"port": 1934}}, indent=4) + "\n"
    with pytest.raises(ValueError, match="changed"):
        save_config_file(text, "stale")
    path.chmod(0o444)
    with pytest.raises(ValueError, match="read-only"):
        save_config_file(text, revision)
    path.chmod(0o600)
    save_config_file(text, revision)
    assert path.read_text() == text
    assert read_config_file()["restart_required"]


def test_startup_revision_tracks_non_model_changes_and_restoration(config_file, monkeypatch):
    path, raw = config_file
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", None)
    monkeypatch.setattr(OpenVikingConfigSingleton, "_config_file_revision", None)
    OpenVikingConfigSingleton.initialize(config_path=str(path))
    initial = path.read_bytes()
    assert not read_config_file()["restart_required"]
    path.write_text(json.dumps({**raw, "server": {"port": 1934}}))
    assert read_config_file()["restart_required"]
    path.write_bytes(initial)
    assert not read_config_file()["restart_required"]


@pytest.mark.parametrize("binding_model", [None, "explicit-model"])
def test_shared_vlm_model_changes_preserve_inheritance_and_explicit_overrides(
    config_file, binding_model
):
    path, raw = config_file
    if binding_model:
        raw["vlm"]["credentials"] = [{"provider": "openai", "model": binding_model}]
    path.write_text(json.dumps(raw))
    loaded = read_config_file()
    model = loaded["models"]["vlm"]["config"]
    model["model"] = "new-model"
    draft = preview_config_file(loaded["content"], {"vlm": model})
    save_config_file(draft["content"], loaded["revision"])
    config = OpenVikingConfigSingleton._load_from_file(str(path))
    assert config.vlm.model == "new-model"
    assert config.vlm.credentials[0].model == binding_model
    assert config.vlm.credentials[0].api_key == "dummy-review-key"
    assert json.loads(path.read_text())["embedding"] == raw["embedding"]


@pytest.mark.parametrize(
    "fragment,environment",
    [
        ('"api_key": "${STUDIO_TEST_KEY}"', {"STUDIO_TEST_KEY": "secret-from-env"}),
        ('"api_key": $STUDIO_TEST_KEY', {"STUDIO_TEST_KEY": '"secret-from-env"'}),
        ('"api_key": "dummy-key", "thinking": ${STUDIO_THINKING}', {"STUDIO_THINKING": "true"}),
        (
            '"credentials": $STUDIO_CREDENTIALS',
            {"STUDIO_CREDENTIALS": '[{"provider":"openai","api_key":"secret-from-env"}]'},
        ),
        (r'"api_key": "\u0024STUDIO_TEST_KEY"', {"STUDIO_TEST_KEY": "secret-from-env"}),
    ],
)
def test_environment_files_are_form_readonly_and_saved_verbatim(
    config_file, monkeypatch, fragment, environment
):
    path, _ = config_file
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    content = '{\n  "vlm": {"model": "gpt-4o", "provider": "openai", ' + fragment + "}\n}\n"
    path.write_text(content)
    loaded = read_config_file()
    assert loaded["content"] == content
    assert loaded["form_readonly"] is True
    assert loaded["models"] == {kind: {"config": {}} for kind in ("vlm", "embedding")}
    assert "secret-from-env" not in json.dumps(loaded)
    with pytest.raises(ValueError, match="requires file editing"):
        preview_config_file(content, {"vlm": {"temperature": 0.7}})
    edited = content.replace('"gpt-4o"', '"gpt-4o-mini"')
    preview = preview_config_file(edited)
    assert preview["content"] == edited and preview["form_readonly"]
    save_config_file(edited, loaded["revision"])
    assert path.read_bytes() == edited.encode()
    runtime = OpenVikingConfigSingleton._load_from_file(str(path))
    if "\\u0024" in content:
        assert runtime.vlm.credentials[0].api_key == "$STUDIO_TEST_KEY"


def test_non_model_environment_reference_blocks_form_edits(config_file, monkeypatch):
    path, raw = config_file
    monkeypatch.setenv("STUDIO_PORT", "1933")
    content = json.dumps(raw)[:-1] + ', "server": {"port": $STUDIO_PORT}}'
    path.write_text(content)
    assert read_config_file()["form_readonly"]
    with pytest.raises(ValueError, match="requires file editing"):
        preview_config_file(content, {"vlm": {"timeout": 42}})


def test_invalid_environment_file_cannot_replace_disk(config_file, monkeypatch):
    path, _ = config_file
    original = path.read_bytes()
    revision = read_config_file()["revision"]
    monkeypatch.setenv("STUDIO_PORT", "not-json")
    with pytest.raises(ValueError):
        save_config_file('{"server":{"port":$STUDIO_PORT}}', revision)
    assert path.read_bytes() == original


def test_form_cannot_introduce_environment_references(config_file):
    path, _ = config_file
    with pytest.raises(ValueError, match="file editor"):
        preview_config_file(path.read_text(), {"vlm": {"api_key": "${NEW_KEY}"}})


def test_file_mode_preserves_bom_and_formatting(config_file):
    path, raw = config_file
    content = "\ufeff" + json.dumps(raw, indent=4) + "\r\n"
    path.write_bytes(content.encode())
    loaded = read_config_file()
    assert loaded["content"] == content
    assert preview_config_file(content)["content"] == content
    save_config_file(content, loaded["revision"])
    assert path.read_bytes() == content.encode()


def test_literal_form_save_preserves_other_sections_and_private_backup(config_file):
    path, raw = config_file
    loaded = read_config_file()
    assert loaded["writable"] and not loaded["restart_required"]
    assert not loaded["form_readonly"]
    _save_form({"vlm": {"timeout": 42}}, loaded["revision"])
    saved = json.loads(path.read_text())
    assert saved == {**raw, "vlm": {**raw["vlm"], "timeout": 42}}
    backup = path.with_name(path.name + ".studio.bak")
    assert json.loads(backup.read_text()) == raw
    if sys.platform != "win32":
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert OpenVikingConfigSingleton.get_instance().vlm.timeout != 42


def test_literal_legacy_backup_bindings_remain_distinct(config_file):
    path, raw = config_file
    raw["vlm"]["backup"] = {"provider": "openai", "model": "gpt-4o-mini", "api_key": "backup-key"}
    content = json.dumps(raw)
    model = preview_config_file(content)["models"]["vlm"]["config"]
    model["timeout"] = 42
    draft = preview_config_file(content, {"vlm": model})
    from openviking.config.config_file import _validate

    runtime = _validate(draft["content"])
    assert [binding.api_key for binding in runtime.vlm.credentials] == [
        "dummy-review-key",
        "backup-key",
    ]


@pytest.mark.parametrize("fragment", ["%STUDIO_WINDOWS_KEY%", r"\u0025STUDIO_WINDOWS_KEY%"])
def test_windows_environment_references_remain_file_only(config_file, monkeypatch, fragment):
    import ntpath

    path, raw = config_file
    monkeypatch.setenv("STUDIO_WINDOWS_KEY", "secret-from-windows-env")
    # Exercise Windows expansion without requiring a Windows host.
    monkeypatch.setattr(os.path, "expandvars", ntpath.expandvars)
    raw["vlm"]["api_key"] = fragment
    content = json.dumps(raw).replace(r"\\u0025", r"\u0025")
    path.write_text(content)
    loaded = read_config_file()
    assert loaded["form_readonly"]
    assert loaded["models"] == {kind: {"config": {}} for kind in ("vlm", "embedding")}
    assert "secret-from-windows-env" not in json.dumps(loaded)
    with pytest.raises(ValueError, match="requires file editing"):
        preview_config_file(content, {"vlm": {"timeout": 42}})
    edited = content.replace('"gpt-4o"', '"gpt-4o-mini"')
    assert preview_config_file(edited)["content"] == edited
    save_config_file(edited, loaded["revision"])
    assert path.read_bytes() == edited.encode()
    runtime = OpenVikingConfigSingleton._load_from_file(str(path))
    assert runtime.vlm.credentials[0].api_key == (
        "%STUDIO_WINDOWS_KEY%" if "\\u0025" in content else "secret-from-windows-env"
    )
