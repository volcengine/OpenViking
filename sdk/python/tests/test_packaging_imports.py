from openviking_sdk import AsyncHTTPClient, OpenVikingError, SyncHTTPClient


def test_sdk_top_level_imports():
    assert AsyncHTTPClient is not None
    assert SyncHTTPClient is not None
    assert OpenVikingError is not None
