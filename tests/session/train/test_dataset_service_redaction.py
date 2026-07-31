from openviking.session.train.components.dataset_service import redact_sensitive


def test_redact_sensitive_recursively_masks_openviking_api_key():
    payload = {
        "policy_set": {
            "metadata": {
                "openviking_api_key": "secret-key",
                "openviking_url": "http://127.0.0.1:1933",
                "nested": [{"api_key": "other-secret"}],
            }
        },
        "case": "keep-me",
    }

    redacted = redact_sensitive(payload)

    assert redacted["policy_set"]["metadata"]["openviking_api_key"] == "<redacted>"
    assert redacted["policy_set"]["metadata"]["nested"][0]["api_key"] == "<redacted>"
    assert redacted["policy_set"]["metadata"]["openviking_url"] == "http://127.0.0.1:1933"
    assert payload["policy_set"]["metadata"]["openviking_api_key"] == "secret-key"


def test_redact_sensitive_masks_key_value_pairs_in_strings():
    text = "failed with openviking_api_key='secret-key', api_key=other-secret token: bearer"

    redacted = redact_sensitive(text)

    assert "secret-key" not in redacted
    assert "other-secret" not in redacted
    assert "bearer" not in redacted
    assert "openviking_api_key='<redacted>'" in redacted
    assert "api_key=<redacted>" in redacted
    assert "token: <redacted>" in redacted
