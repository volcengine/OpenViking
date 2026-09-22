import pytest
from openviking_sdk import AsyncHTTPClient
from openviking_sdk.errors import (
    AbortedError,
    ConflictError,
    OpenVikingError,
    ResourceExhaustedError,
    UnavailableError,
    UnimplementedError,
)


@pytest.mark.parametrize(
    ("code", "exc_type"),
    (
        ("CONFLICT", ConflictError),
        ("ABORTED", AbortedError),
        ("RESOURCE_EXHAUSTED", ResourceExhaustedError),
        ("UNIMPLEMENTED", UnimplementedError),
    ),
)
def test_client_maps_standard_error_codes(code, exc_type):
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(exc_type) as exc_info:
        client._raise_exception({"code": code, "message": "mapped"})

    assert exc_info.value.code == code


def test_client_preserves_unknown_error_code():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(OpenVikingError) as exc_info:
        client._raise_exception(
            {
                "code": "PROVIDER_SPECIFIC",
                "message": "provider-specific failure",
                "details": {"x": 1},
            }
        )

    assert exc_info.value.code == "PROVIDER_SPECIFIC"
    assert exc_info.value.details == {"x": 1}


def test_client_preserves_unavailable_service_and_reason():
    client = AsyncHTTPClient(url="http://127.0.0.1:1933")

    with pytest.raises(UnavailableError) as exc_info:
        client._raise_exception(
            {
                "code": "UNAVAILABLE",
                "message": "Storage backend unavailable: lock contention",
                "details": {
                    "service": "storage backend",
                    "reason": "failed to read lock token (os error 33)",
                },
            }
        )

    assert str(exc_info.value) == (
        "Storage backend unavailable: failed to read lock token (os error 33)"
    )
    assert exc_info.value.details == {
        "service": "storage backend",
        "reason": "failed to read lock token (os error 33)",
    }
