import hashlib
import json
import stat

import pytest

from openviking.config.config_file import preview_config_file, read_config_file, save_config_file
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest.fixture
def config_file(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        OpenVikingConfigSingleton,
        "_config_file_revision",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    from openviking.config.config_file import _validate

    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", _validate(raw))
    return path, raw


def test_file_models_preserve_environment_references_and_save_without_publishing(config_file):
    path, raw = config_file
    result = read_config_file()
    assert result["file_path"] == str(path)
    assert result["writable"] and not result["restart_required"]
    assert result["models"]["query_planner"]["source"] == "vlm"
    assert "resolved-secret" not in json.dumps(result)
    assert (
        result["models"]["embedding"]["config"]["dense"]["credentials"][0]["api_key"]
        == "${STUDIO_TEST_KEY}"
    )
    save_config_file({"vlm": {"timeout": 42}}, result["revision"])
    saved = json.loads(path.read_text())
    assert saved["vlm"] == {**raw["vlm"], "timeout": 42}
    assert saved["storage"] == raw["storage"]
    assert saved["embedding"] == raw["embedding"]
    backup = path.with_name(path.name + ".studio.bak")
    assert json.loads(backup.read_text()) == raw
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert OpenVikingConfigSingleton.get_instance().vlm.timeout != 42
    assert read_config_file()["restart_required"]


def test_stale_revision_readonly_invalid_and_non_model_writes_leave_file_unchanged(config_file):
    path, _ = config_file
    revision = read_config_file()["revision"]
    original = path.read_bytes()
    for settings, version in [
        ({"vlm": {"timeout": 30}}, "stale"),
        ({"storage": {}}, revision),
        ({"rerank": {"mode": "invalid"}}, revision),
        ({"vlm": None}, revision),
    ]:
        with pytest.raises(ValueError):
            save_config_file(settings, version)
        assert path.read_bytes() == original
    path.chmod(0o444)
    assert not read_config_file()["writable"]
    with pytest.raises(ValueError, match="read-only"):
        save_config_file({"vlm": {"timeout": 30}}, revision)
    assert path.read_bytes() == original


def test_embedding_binding_rotation_preserves_implicit_input_and_identity(config_file):
    path, raw = config_file
    result = read_config_file()
    save_config_file(
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


def test_jev_headers_can_be_cleared_and_planner_can_inherit(config_file):
    path, raw = config_file
    raw["query_planner"] = {"provider": "openai", "api_key": "planner-key", "model": "gpt-4o"}
    raw["rerank"]["extra_headers"] = {"obsolete": "secret"}
    path.write_text(json.dumps(raw))
    result = read_config_file()
    save_config_file({"query_planner": None, "rerank": {"extra_headers": {}}}, result["revision"])
    stored = json.loads(path.read_text())
    assert "query_planner" not in stored
    assert stored["rerank"]["extra_headers"] == {}
    assert read_config_file()["models"]["query_planner"]["source"] == "vlm"


def test_legacy_backup_credentials_keep_distinct_keys(config_file):
    path, raw = config_file
    raw["vlm"]["backup"] = {
        "api_key": "backup-secret",
        "model": "gpt-4o-mini",
        "provider": "openai",
    }
    path.write_text(json.dumps(raw))
    bindings = read_config_file()["models"]["vlm"]["config"]["credentials"]
    assert bindings[0]["api_key"] == "${STUDIO_TEST_KEY}"
    assert bindings[1]["api_key"] == "backup-secret"


def test_embedding_credentials_display_parent_fallbacks(config_file):
    path, raw = config_file
    raw["embedding"]["dense"]["credentials"] = [{"id": "primary", "provider": "openai"}]
    path.write_text(json.dumps(raw))
    binding = read_config_file()["models"]["embedding"]["config"]["dense"]["credentials"][0]
    assert binding["model"] == "text-embedding-3-small"
    assert binding["api_key"] == "${STUDIO_TEST_KEY}"


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
    save_config_file({kind: config}, result["revision"])
    stored = json.loads(path.read_text())
    stored_section = stored[kind] if kind == "vlm" else stored[kind]["dense"]
    assert "api_base" not in stored_section and "api_key" not in stored_section
    current = read_config_file()["models"][kind]["config"]
    current_section = current if kind == "vlm" else current["dense"]
    assert not current_section["credentials"][0].get("api_base")
    assert not current_section["credentials"][0].get("extra_headers")
    assert current_section["credentials"][0]["api_key"] == "${STUDIO_TEST_KEY}"


def test_partial_credentials_preserve_inherited_fields_and_environment_references(config_file):
    path, raw = config_file
    raw["vlm"].update(api_base="${STUDIO_TEST_URL}", extra_headers={"X-Keep": "yes"})
    path.write_text(json.dumps(raw))
    result = read_config_file()
    save_config_file({"vlm": {"credentials": [{"model": "gpt-4o-mini"}]}}, result["revision"])
    current = read_config_file()["models"]["vlm"]["config"]["credentials"][0]
    assert current["api_base"] == "${STUDIO_TEST_URL}"
    assert current["api_key"] == "${STUDIO_TEST_KEY}"
    assert current["extra_headers"] == {"X-Keep": "yes"}


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
    save_config_file({"vlm": config}, result["revision"])
    binding = read_config_file()["models"]["vlm"]["config"]["credentials"][0]
    assert binding["provider"] == "openai" and binding["api_key"] == "new-key"
    assert not binding.get("api_base") and not binding.get("extra_headers")


def test_keyless_embedding_does_not_inherit_legacy_key(config_file):
    path, _ = config_file
    result = read_config_file()
    save_config_file(
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


@pytest.mark.parametrize("explicit", [False, True])
def test_equal_environment_values_keep_each_binding_source(config_file, monkeypatch, explicit):
    path, raw = config_file
    monkeypatch.setenv("STUDIO_PRIMARY_KEY", "same-key")
    monkeypatch.setenv("STUDIO_BACKUP_KEY", "same-key")
    monkeypatch.setenv("STUDIO_HEADER_A", "same-header")
    monkeypatch.setenv("STUDIO_HEADER_B", "same-header")
    primary = {
        "api_key": "${STUDIO_PRIMARY_KEY}",
        "extra_headers": {"A": "${STUDIO_HEADER_A}", "B": "${STUDIO_HEADER_B}"},
    }
    backup = {"api_key": "${STUDIO_BACKUP_KEY}"}
    raw["vlm"] = {"model": "gpt-4o", "provider": "openai"}
    if explicit:
        raw["vlm"]["credentials"] = [
            {"provider": "openai", **primary},
            {"provider": "openai", **backup},
        ]
    else:
        raw["vlm"]["providers"] = {"openai": primary}
        raw["vlm"]["backup"] = {
            "model": "gpt-4o-mini",
            "provider": "openai",
            "providers": {"openai": backup},
        }
    # A literal equal value elsewhere must not become an environment reference.
    raw["vlm"]["reasoning_effort"] = "same-header"
    path.write_text(json.dumps(raw))
    result = read_config_file()
    bindings = result["models"]["vlm"]["config"]["credentials"]
    assert bindings[0]["api_key"] == "${STUDIO_PRIMARY_KEY}"
    assert bindings[1]["api_key"] == "${STUDIO_BACKUP_KEY}"
    assert bindings[0]["extra_headers"] == primary["extra_headers"]
    assert bindings[0]["reasoning_effort"] == "same-header"
    save_config_file({"vlm": result["models"]["vlm"]["config"]}, result["revision"])
    stored = json.loads(path.read_text())["vlm"]["credentials"]
    assert stored[0]["api_key"] == "${STUDIO_PRIMARY_KEY}"
    assert stored[1]["api_key"] == "${STUDIO_BACKUP_KEY}"
    assert stored[0]["extra_headers"] == primary["extra_headers"]


@pytest.mark.parametrize("null_provider", [False, True])
def test_partial_embedding_credentials_preserve_legacy_backend(config_file, null_provider):
    path, raw = config_file
    dense = raw["embedding"]["dense"]
    dense["backend"] = dense.pop("provider")
    if null_provider:
        dense["provider"] = None
    path.write_text(json.dumps(raw))
    result = read_config_file()
    save_config_file(
        {"embedding": {"dense": {"credentials": [{"api_key": "rotated"}]}}},
        result["revision"],
    )
    current = read_config_file()["models"]["embedding"]["config"]["dense"]
    assert current["credentials"][0]["provider"] == "openai"
    assert current["credentials"][0]["api_key"] == "rotated"


def test_full_file_draft_preserves_other_sections_and_does_not_publish(config_file):
    path, raw = config_file
    raw["server"] = {"port": 1933, "root_api_key": "${STUDIO_TEST_KEY}"}
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    revision = read_config_file()["revision"]
    raw["server"]["port"] = 1934
    raw["storage"]["workspace"] = "/tmp/new-workspace"
    raw["query_planner"] = {
        "provider": "openai",
        "model": "planner",
        "api_key": "${STUDIO_TEST_KEY}",
    }
    text = json.dumps(raw, indent=4) + "\n"
    preview = preview_config_file(text, {"vlm": {"timeout": 48}})
    assert path.read_bytes() == before
    draft = json.loads(preview["content"])
    assert draft["server"] == raw["server"]
    assert draft["storage"] == raw["storage"]
    assert draft["query_planner"] == raw["query_planner"]
    assert draft["vlm"]["timeout"] == 48
    save_config_file({}, revision, preview["content"])
    assert path.read_text() == preview["content"]
    assert path.with_name(path.name + ".studio.bak").read_bytes() == before
    assert read_config_file()["restart_required"]
    assert OpenVikingConfigSingleton.get_instance().vlm.timeout != 48


@pytest.mark.parametrize("content", ["{", "[]", '{"server":{"port":"invalid"}}', '{"server":[]}'])
def test_invalid_full_file_drafts_leave_disk_unchanged(config_file, content):
    path, _ = config_file
    before = path.read_bytes()
    revision = read_config_file()["revision"]
    with pytest.raises(ValueError):
        preview_config_file(content)
    with pytest.raises(ValueError):
        save_config_file({}, revision, content)
    assert path.read_bytes() == before


def test_full_file_save_obeys_revision_readonly_and_exact_content(config_file):
    path, raw = config_file
    revision = read_config_file()["revision"]
    text = json.dumps({**raw, "server": {"port": 1934}}, indent=4) + "\n"
    with pytest.raises(ValueError, match="changed"):
        save_config_file({}, "stale", text)
    with pytest.raises(ValueError, match="either"):
        save_config_file({"vlm": {}}, revision, text)
    path.chmod(0o444)
    with pytest.raises(ValueError, match="read-only"):
        save_config_file({}, revision, text)
    path.chmod(0o600)
    save_config_file({}, revision, text)
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


def test_unquoted_environment_values_survive_form_and_file_saves(config_file, monkeypatch):
    path, raw = config_file
    monkeypatch.setenv("STUDIO_CONCURRENCY", "12")
    monkeypatch.setenv("STUDIO_PORT", "1933")
    monkeypatch.setenv("STUDIO_SERVER", '{"port":1933,"root_api_key":"server-secret"}')
    raw["vlm"]["max_concurrent"] = "${STUDIO_CONCURRENCY}"
    raw["server"] = {"port": "$STUDIO_PORT"}
    text = json.dumps(raw).replace('"${STUDIO_CONCURRENCY}"', "${STUDIO_CONCURRENCY}")
    text = text.replace('"$STUDIO_PORT"', "$STUDIO_PORT")
    path.write_text(text)
    monkeypatch.setattr(OpenVikingConfigSingleton, "_instance", None)
    OpenVikingConfigSingleton.initialize(config_path=str(path))
    assert OpenVikingConfigSingleton.get_instance().vlm.max_concurrent == 12
    loaded = read_config_file()
    assert loaded["content"] == text
    assert "resolved-secret" not in json.dumps(loaded)
    assert not loaded["restart_required"]

    # Form requests cross a JSON transport, which removes Python marker types.
    model = json.loads(json.dumps(loaded["models"]["vlm"]["config"]))
    model["timeout"] = 42
    draft = preview_config_file(text, {"vlm": model})
    assert '"max_concurrent": ${STUDIO_CONCURRENCY}' in draft["content"]
    assert '"port": $STUDIO_PORT' in draft["content"]
    assert "resolved-secret" not in draft["content"]
    save_config_file({}, loaded["revision"], draft["content"])
    assert path.read_text() == draft["content"]
    reloaded = OpenVikingConfigSingleton._load_from_file(str(path))
    assert reloaded.vlm.max_concurrent == 12 and reloaded.vlm.timeout == 42

    # An environment reference may also supply a complete non-model JSON value.
    text = draft["content"].replace('"port": $STUDIO_PORT', '"port": 1934')
    revision = read_config_file()["revision"]
    save_config_file({}, revision, text)
    assert path.read_text() == text
    object_text = json.dumps({**raw, "server": "$STUDIO_SERVER"}).replace(
        '"$STUDIO_SERVER"', "$STUDIO_SERVER"
    )
    projected = preview_config_file(object_text, {"vlm": {"timeout": 45}})
    assert '"server": $STUDIO_SERVER' in projected["content"]
    assert "server-secret" not in json.dumps(projected)


def test_unset_or_invalid_environment_values_cannot_replace_the_file(config_file, monkeypatch):
    path, raw = config_file
    monkeypatch.delenv("STUDIO_UNSET", raising=False)
    monkeypatch.setenv("STUDIO_INVALID", "invalid-json")
    original = path.read_bytes()
    revision = read_config_file()["revision"]
    for reference in ("$STUDIO_UNSET", "${STUDIO_INVALID}"):
        content = json.dumps({**raw, "server": reference}).replace(json.dumps(reference), reference)
        with pytest.raises(ValueError, match="Invalid ov.conf"):
            preview_config_file(content)
        with pytest.raises(ValueError, match="Invalid ov.conf"):
            save_config_file({}, revision, content)
        assert path.read_bytes() == original


def test_unquoted_credential_references_survive_transport_and_reordering(config_file, monkeypatch):
    path, raw = config_file
    monkeypatch.setenv("STUDIO_JSON_KEY", '"resolved-secret"')
    raw["vlm"]["credentials"] = [
        {"id": "first", "provider": "openai", "api_key": "${STUDIO_JSON_KEY}"},
        {"id": "second", "provider": "openai", "api_key": "literal-key"},
    ]
    content = json.dumps(raw).replace('"${STUDIO_JSON_KEY}"', "${STUDIO_JSON_KEY}")
    path.write_text(content)
    loaded = read_config_file()
    assert "resolved-secret" not in json.dumps(loaded)
    model = json.loads(json.dumps(loaded["models"]["vlm"]["config"]))
    model["credentials"].reverse()
    draft = preview_config_file(content, {"vlm": model})
    assert '"api_key": ${STUDIO_JSON_KEY}' in draft["content"]
    save_config_file({}, loaded["revision"], draft["content"])
    config = OpenVikingConfigSingleton._load_from_file(str(path))
    assert config.vlm.credentials[1].id == "first"
    assert config.vlm.credentials[1].api_key == "resolved-secret"


@pytest.mark.parametrize("location", ["vlm", "dense", "credentials", "providers"])
def test_environment_objects_remain_opaque_and_survive_file_edits(
    config_file, monkeypatch, location
):
    path, raw = config_file
    model = {"provider": "openai", "model": "gpt-4o", "api_key": "object-secret"}
    reference = "$STUDIO_MODEL_OBJECT"
    kind = "embedding" if location == "dense" else "vlm"
    if location == "vlm":
        supplied = model
        raw["vlm"] = reference
    elif location == "dense":
        supplied = {**model, "dimension": 1024}
        raw["embedding"]["dense"] = reference
    elif location == "credentials":
        supplied = [model]
        raw["vlm"]["credentials"] = reference
    else:
        supplied = {"openai": {"api_key": "object-secret"}}
        raw["vlm"].pop("api_key")
        raw["vlm"]["providers"] = reference
    monkeypatch.setenv("STUDIO_MODEL_OBJECT", json.dumps(supplied))
    content = json.dumps(raw).replace(json.dumps(reference), reference)
    path.write_text(content)
    OpenVikingConfigSingleton.initialize(config_path=str(path))

    loaded = read_config_file()
    assert loaded["models"][kind]["environment_references"] == [reference]
    assert loaded["models"][kind]["config"] == {}
    assert "object-secret" not in json.dumps(loaded)
    assert not loaded["restart_required"]
    assert preview_config_file(content)["models"] == loaded["models"]
    with pytest.raises(ValueError, match="require file editing"):
        save_config_file({kind: {"timeout": 42}}, loaded["revision"])
    assert path.read_text() == content

    draft = preview_config_file(content, {"rerank": {"timeout": 42}})
    assert f'"{location}": {reference}' in draft["content"]
    assert "object-secret" not in draft["content"]
    save_config_file({}, loaded["revision"], draft["content"])
    assert path.read_text() == draft["content"]
    assert OpenVikingConfigSingleton._load_from_file(str(path)).rerank.timeout == 42
