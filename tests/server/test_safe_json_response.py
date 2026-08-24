import json

from openviking.server.responses import SafeJSONResponse, error_response, response_from_result


def test_safe_json_response_preserves_normal_unicode_rendering():
    response = SafeJSONResponse({"message": "hello 世界 😀"})

    assert response.body == b'{"message":"hello \xe4\xb8\x96\xe7\x95\x8c \xf0\x9f\x98\x80"}'


def test_error_response_replaces_lone_surrogates_before_rendering():
    response = error_response(
        "NOT_FOUND",
        "File not found: viking://resources/\ud800bad_name",
        details={"uri": "viking://resources/\ud800bad_name"},
    )

    payload = json.loads(response.body.decode("utf-8"))

    assert payload["error"]["message"] == "File not found: viking://resources/�bad_name"
    assert payload["error"]["details"]["uri"] == "viking://resources/�bad_name"


def test_business_error_response_replaces_lone_surrogates_before_rendering():
    response = response_from_result(
        {
            "status": "error",
            "code": "NOT_FOUND",
            "message": "Missing viking://resources/\ud800bad_name",
            "details": {"uri": "viking://resources/\ud800bad_name"},
        }
    )

    payload = json.loads(response.body.decode("utf-8"))

    assert payload["error"]["message"] == "Missing viking://resources/�bad_name"
    assert payload["error"]["details"]["uri"] == "viking://resources/�bad_name"
