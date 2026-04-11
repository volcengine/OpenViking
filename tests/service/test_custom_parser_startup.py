from types import SimpleNamespace

import pytest

from openviking.service.core import OpenVikingService


@pytest.mark.asyncio
async def test_initialize_registers_custom_parsers_before_other_startup(monkeypatch, tmp_path):
    service = OpenVikingService.__new__(OpenVikingService)
    service._initialized = False
    service._config = SimpleNamespace(
        storage=SimpleNamespace(workspace=str(tmp_path)),
        embedding=SimpleNamespace(max_concurrent=1),
        vlm=SimpleNamespace(max_concurrent=1),
    )
    service._vikingdb_manager = object()
    service._embedder = object()

    sentinel = RuntimeError("custom parser registration failed")

    monkeypatch.setattr(
        "openviking.utils.process_lock.acquire_data_dir_lock",
        lambda _workspace: None,
    )
    monkeypatch.setattr(
        "openviking.service.core.get_openviking_config",
        lambda: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "openviking.parse.custom_loader.register_configured_custom_parsers",
        lambda **_kwargs: (_ for _ in ()).throw(sentinel),
    )

    with pytest.raises(RuntimeError, match="custom parser registration failed"):
        await OpenVikingService.initialize(service)
