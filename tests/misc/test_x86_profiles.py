from build_support.x86_profiles import get_host_engine_build_config


def test_x86_host_uses_sse3_extension_baseline():
    config = get_host_engine_build_config("x86_64")

    assert config.primary_extension == "openviking.storage.vectordb.engine._x86_sse3"
    assert config.cmake_variants == ("sse3", "avx2", "avx512")
    assert config.is_x86 is True


def test_non_x86_host_uses_native_extension_baseline():
    config = get_host_engine_build_config("aarch64")

    assert config.primary_extension == "openviking.storage.vectordb.engine._native"
    assert config.cmake_variants == ()
    assert config.is_x86 is False
