from build_support import versioning


def test_resolve_openviking_version_prefers_explicit_openviking_version():
    version = versioning.resolve_openviking_version(
        env={
            "OPENVIKING_VERSION": "1.2.3",
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING": "9.9.9",
        }
    )

    assert version == "1.2.3"


def test_resolve_openviking_version_uses_setuptools_scm_pretend_version():
    version = versioning.resolve_openviking_version(
        env={"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_OPENVIKING": "2.3.4.dev5"}
    )

    assert version == "2.3.4.dev5"


def test_resolve_openviking_version_falls_back_to_scm(monkeypatch):
    monkeypatch.setattr(versioning, "_get_scm_version", lambda project_root: "3.4.5.dev6")

    version = versioning.resolve_openviking_version(env={})

    assert version == "3.4.5.dev6"
