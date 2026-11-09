"""Direct browser authorization, credential handoff and installer routing."""

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from openviking_cli import setup_wizard as wizard
from openviking_cli import volcengine_setup as ark


@pytest.fixture
def account_setup():
    with (
        patch.object(wizard, "_stdin_stdout_tty", return_value=True),
        patch.object(ark, "VolcengineSetup") as factory,
        patch("webbrowser.open") as browser,
        patch.object(wizard, "_prompt_choice", return_value=1),
        patch.object(wizard, "_select_access_plan", return_value=wizard.VOLCENGINE_PLANS[-1]),
        patch.object(wizard, "_prompt_required_input", side_effect=lambda _, default: default),
        patch.object(wizard, "_prompt_confirm", return_value=False) as confirm,
        patch.object(ark, "probe_models") as probe,
        patch.object(
            ark,
            "fetch_models",
            side_effect=lambda base, key: [
                {
                    "id": next(
                        p.default_vlm_model for p in wizard.VOLCENGINE_PLANS if p.api_base == base
                    ),
                    "domain": "VLM",
                },
                {
                    "id": next(
                        p.default_embedding_model
                        for p in wizard.VOLCENGINE_PLANS
                        if p.api_base == base
                    ),
                    "domain": "Embedding",
                },
            ],
        ),
    ):
        account = factory.return_value.__enter__.return_value
        account.start_authorization.return_value = {
            "verification_uri": "https://signin.volcengine.com/device",
            "user_code": "1234",
        }
        account.list_keys.return_value = [{"Id": 17, "Name": "OV", "ProjectName": "default"}]
        account.model_activations.return_value = {
            p.default_vlm_model: "Available" for p in wizard.VOLCENGINE_PLANS
        } | {p.default_embedding_model: "Available" for p in wizard.VOLCENGINE_PLANS}
        account.agent_plan_models.return_value = [
            {"id": wizard.VOLCENGINE_PLANS[0].default_vlm_model, "domain": "VLM"},
            {"id": wizard.VOLCENGINE_PLANS[0].default_embedding_model, "domain": "Embedding"},
        ]
        account.api_key.return_value = "ark-test-secret"
        yield account, browser, confirm, probe


def test_authorized_setup_fills_models_without_cli_or_original_config(
    account_setup, tmp_path, capsys
):
    original = tmp_path / "original.conf"
    original.write_text('{"unchanged": true}')
    with patch("subprocess.run", side_effect=AssertionError("No CLI dependency")):
        config = wizard._wizard_volcengine()
    dense = config["embedding"]["dense"]
    assert dense["api_key"] == config["vlm"]["api_key"] == "ark-test-secret"
    assert dense["dimension"] == wizard.CLOUD_PROVIDERS[0].default_embedding_dim
    target = tmp_path / "test" / "ov.conf"
    assert wizard._write_config(config, target)
    assert json.loads(target.read_text()) == config
    assert target.stat().st_mode & 0o777 == 0o600
    assert original.read_text() == '{"unchanged": true}'
    assert "ark-test-secret" not in capsys.readouterr().out
    account_setup[1].assert_called_once_with("https://signin.volcengine.com/device")
    account_setup[0].wait_for_authorization.assert_called_once()
    account_setup[3].assert_not_called()


def test_login_then_verify_models(account_setup):
    account_setup[2].return_value = True
    account_setup[3].return_value = [("Embedding", True, "ready"), ("VLM", True, "ready")]
    assert isinstance(wizard._wizard_volcengine(), dict)
    account_setup[3].assert_called_once()


def test_login_failure_returns_to_menu(account_setup):
    account_setup[0].wait_for_authorization.side_effect = ark.ArkSetupError("Denied")
    assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[0].api_key.assert_not_called()


def test_probe_failure_does_not_return_saveable_config(account_setup):
    account_setup[2].side_effect = [True, False]
    account_setup[3].return_value = [("Embedding", False, "HTTP 403"), ("VLM", True, "ready")]
    assert wizard._wizard_volcengine() is wizard._GO_BACK


def test_empty_key_list_offers_console_creation_without_copying_key(account_setup):
    account, browser, confirm, _ = account_setup
    account.list_keys.side_effect = [[], [{"Id": 18, "Name": "new"}]]
    confirm.side_effect = [True, False]
    assert isinstance(wizard._wizard_volcengine(), dict)
    assert browser.call_count == 2
    account.api_key.assert_called_once_with({"Id": 18, "Name": "new"})


def test_multiple_keys_only_exports_selected_one(account_setup):
    account_setup[0].list_keys.return_value = [{"Id": 11}, {"Id": 22}]
    with patch.object(wizard, "_prompt_choice", side_effect=[2, 1, 1]):
        assert isinstance(wizard._wizard_volcengine(), dict)
    account_setup[0].api_key.assert_called_once_with({"Id": 22})


@pytest.mark.parametrize("index,scene", [(0, "RealAgentPlanPersonal"), (1, None), (2, None)])
def test_access_plan_selects_matching_key_pool_endpoint_and_models(account_setup, index, scene):
    plan = wizard.VOLCENGINE_PLANS[index]
    with patch.object(wizard, "_select_access_plan", return_value=plan):
        config = wizard._wizard_volcengine()
    account_setup[0].list_keys.assert_called_once_with("default", scene=scene)
    assert config["vlm"]["api_base"] == config["embedding"]["dense"]["api_base"] == plan.api_base
    assert config["vlm"]["model"] == plan.default_vlm_model
    assert config["embedding"]["dense"]["model"] == plan.default_embedding_model


def test_back_from_plan_menu_does_not_start_authorization(account_setup):
    with patch.object(wizard, "_select_access_plan", return_value=wizard._GO_BACK):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[0].start_authorization.assert_not_called()


def test_noninteractive_does_not_start_login():
    with (
        patch.object(wizard, "_stdin_stdout_tty", return_value=False),
        patch.object(ark, "VolcengineSetup") as factory,
    ):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
        factory.assert_not_called()


def authorization_response(**changes):
    return {
        "device_code": "device-secret",
        "user_code": "1234",
        "verification_uri": "https://signin.volcengine.com/device",
        "verification_uri_complete": "https://signin.volcengine.com/device?code=1234",
        "expires_in": 600,
        "interval": 5,
        **changes,
    }


def token_response():
    return {
        "access_token": json.dumps(
            {
                "access_key_id": "ak-secret",
                "secret_access_key": "sk-secret",
                "session_token": "sts-secret",
            }
        )
    }


@pytest.fixture
def session():
    # All HTTP traffic stays in MockTransport, even the signed control-plane calls.
    def fail_unexpected(request):
        raise AssertionError(f"Unexpected request: {request.method} {request.url.host}")

    client = httpx.Client(transport=httpx.MockTransport(fail_unexpected))
    with patch.object(ark.httpx, "Client", return_value=client), ark.VolcengineSetup() as account:
        yield account, client
    assert not account._credentials
    assert client.is_closed


def test_device_authorization_uses_official_public_client(session):
    account, client = session

    def handler(request):
        assert request.url.path == "/authorize/oauth/device_authorization"
        assert b"device_info=OpenViking" in request.content
        assert b"client_id=trn%3Asignin%3A%3A%3Adevtools%2Fcross-device" in request.content
        return httpx.Response(200, json=authorization_response())

    client._transport = httpx.MockTransport(handler)
    assert account.start_authorization()["user_code"] == "1234"


@pytest.mark.parametrize(
    "changes",
    [
        {"verification_uri": "https://attacker.invalid/"},
        {"verification_uri_complete": "http://signin.volcengine.com/"},
        {"expires_in": -1},
        {"device_code": ""},
    ],
)
def test_invalid_device_response_is_rejected(session, changes):
    account, client = session
    client._transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json=authorization_response(**changes))
    )
    with pytest.raises(ark.ArkSetupError):
        account.start_authorization()


def test_device_poll_honors_slowdown_and_keeps_credentials_in_memory(session, capsys):
    account, client = session
    responses = iter(
        [
            httpx.Response(400, json={"error": "authorization_pending"}),
            httpx.Response(400, json={"error": "slow_down"}),
            httpx.Response(200, json=token_response()),
        ]
    )
    client._transport = httpx.MockTransport(lambda _: next(responses))
    with (
        patch.object(ark.time, "monotonic", return_value=0),
        patch.object(ark.time, "sleep") as sleep,
    ):
        account.wait_for_authorization(
            {"device_code": "device-secret", "interval": 5, "deadline": 600}
        )
    assert [call.args[0] for call in sleep.call_args_list] == [5, 5, 10]
    assert account._credentials["access_key_id"] == "ak-secret"
    assert "secret" not in capsys.readouterr().out


@pytest.mark.parametrize("error", ["access_denied", "expired_token", "invalid_device_code"])
def test_denied_or_expired_authorization_stops_polling(session, error):
    account, client = session
    client._transport = httpx.MockTransport(lambda _: httpx.Response(400, json={"error": error}))
    with patch.object(ark.time, "monotonic", return_value=0), patch.object(ark.time, "sleep"):
        with pytest.raises(ark.ArkSetupError, match="denied or expired"):
            account.wait_for_authorization(
                {"device_code": "device-secret", "interval": 5, "deadline": 600}
            )
    assert not account._credentials


def test_timed_out_device_session_does_not_poll(session):
    with patch.object(ark.time, "monotonic", return_value=600):
        with pytest.raises(ark.ArkSetupError, match="timed out"):
            session[0].wait_for_authorization(
                {"device_code": "secret", "interval": 5, "deadline": 600}
            )


def test_key_pagination_filters_inactive_and_plan_keys_and_drops_secrets(session):
    with patch.object(
        session[0],
        "_call",
        side_effect=[
            {
                "Items": [{"Id": 1, "Name": "ordinary", "Status": "Active", "Key": "secret"}],
                "TotalCount": 4,
            },
            {
                "Items": [
                    {"Id": 2, "Status": "Disabled"},
                    {"Id": 3, "Status": "Active", "Scene": "RealAgentPlanPersonal"},
                    {"Id": 4, "Status": "Active", "ProjectName": "demo"},
                ],
                "TotalCount": 4,
            },
        ],
    ) as call:
        assert session[0].list_keys() == [
            {"Id": 1, "Name": "ordinary", "ProjectName": "default"},
            {"Id": 4, "Name": None, "ProjectName": "default"},
        ]
        assert call.call_args_list[1].args[1]["PageNumber"] == 2
        assert call.call_args_list[0].args[1]["ProjectName"] == "default"


def test_selected_key_uses_signed_sts_and_exact_id(session):
    account, client = session
    account._credentials = {
        "access_key_id": "ak-secret",
        "secret_access_key": "sk-secret",
        "session_token": "sts-secret",
    }

    def handler(request):
        assert request.url.host == "open.volcengineapi.com"
        assert request.url.params["Action"] == "GetRawApiKey"
        assert request.headers["X-Security-Token"] == "sts-secret"
        assert "Credential=ak-secret/" in request.headers["Authorization"]
        assert json.loads(request.content) == {"Id": 42, "ProjectName": "demo"}
        return httpx.Response(200, json={"Result": {"ApiKey": "ark-test-secret"}})

    client._transport = httpx.MockTransport(handler)
    assert account.api_key({"Id": 42, "ProjectName": "demo"}) == "ark-test-secret"


def test_agent_plan_queries_its_own_scene_and_excludes_other_pools(session):
    with patch.object(
        session[0],
        "_call",
        return_value={
            "Items": [
                {"Id": 1, "Status": "Active", "Scene": "RealAgentPlanPersonal"},
                {"Id": 2, "Status": "Active", "Scene": "Other"},
                {"Id": 3, "Status": "Restricted", "Scene": "RealAgentPlanPersonal"},
            ],
            "TotalCount": 3,
        },
    ) as call:
        keys = session[0].list_keys(scene="RealAgentPlanPersonal")
    assert [row["Id"] for row in keys] == [1]
    assert call.call_args.args[1]["Filter"] == {"AllowAll": True, "Scene": "RealAgentPlanPersonal"}


@pytest.mark.parametrize("key", ["", "***", None, "Error: failure"])
def test_unusable_key_is_rejected(session, key):
    with patch.object(session[0], "_call", return_value={"ApiKey": key}):
        with pytest.raises(ark.ArkSetupError):
            session[0].api_key({"Id": 42})


def test_api_error_does_not_disclose_response(session):
    account, client = session
    account._credentials = {
        "access_key_id": "ak",
        "secret_access_key": "sk",
        "session_token": "sts",
    }
    client._transport = httpx.MockTransport(
        lambda _: httpx.Response(403, json={"secret": "sensitive-value"})
    )
    with pytest.raises(ark.ArkSetupError) as error:
        account.list_keys()
    assert "sensitive-value" not in str(error.value)


def test_api_error_reports_parameter_error_without_blame_or_secrets(session):
    account, client = session
    account._credentials = {
        "access_key_id": "secret-ak",
        "secret_access_key": "secret-sk",
        "session_token": "secret-sts",
    }
    client._transport = httpx.MockTransport(
        lambda _: httpx.Response(
            400,
            json={
                "ResponseMetadata": {
                    "Error": {
                        "Code": "MissingParameter",
                        "Message": "ProjectName required. secret-sts",
                    }
                },
                "Result": {"ApiKey": "must-not-print"},
            },
        )
    )
    with pytest.raises(ark.ArkSetupError) as error:
        account.list_keys()
    assert "MissingParameter: ProjectName required." in str(error.value)
    assert "secret-sts" not in str(error.value)
    assert "must-not-print" not in str(error.value)
    assert "permissions" not in str(error.value)


def test_retry_after_key_failure_keeps_authorization_session(account_setup):
    account = account_setup[0]
    account.list_keys.side_effect = [ark.ArkSetupError("Temporary error"), [{"Id": 42}]]
    assert isinstance(wizard._wizard_volcengine(), dict)
    account.wait_for_authorization.assert_called_once()
    assert account.list_keys.call_count == 2


def test_change_project_after_error_keeps_authorization_session(account_setup):
    account = account_setup[0]
    account.list_keys.side_effect = [ark.ArkSetupError("Project error"), [{"Id": 42}]]
    with (
        patch.object(wizard, "_prompt_choice", side_effect=[2, 1, 1]),
        patch.object(wizard, "_prompt_required_input", side_effect=["demo", "vlm", "embedding"]),
        patch.object(wizard, "_prompt_required_int", return_value=1024),
    ):
        assert isinstance(wizard._wizard_volcengine(), dict)
    assert [call.args for call in account.list_keys.call_args_list] == [("default",), ("demo",)]
    account.wait_for_authorization.assert_called_once()


@pytest.mark.parametrize("input_type", ["text", "multimodal"])
def test_model_probes_use_expected_protocol_and_do_not_follow_redirects(input_type):
    config = {
        "embedding": {
            "dense": {
                "api_base": "https://ark.cn-beijing.volces.com/api/v3",
                "api_key": "ark-test-secret",
                "model": "embedding",
                "dimension": 2,
                "input": input_type,
            }
        },
        "vlm": {
            "api_base": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key": "ark-test-secret",
            "model": "vlm",
        },
    }
    client = MagicMock()
    client.__enter__.return_value = client
    embedding = {"embedding": [0.1, 0.2, 0.3]}
    client.post.side_effect = [
        httpx.Response(200, json={"data": [embedding] if input_type == "text" else embedding}),
        httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}),
    ]
    with patch("httpx.Client", return_value=client) as factory:
        assert all(ok for _, ok, _ in ark.probe_models(config))
    factory.assert_called_once_with(timeout=30, follow_redirects=False)
    endpoint = "/embeddings" if input_type == "text" else "/embeddings/multimodal"
    assert client.post.call_args_list[0].args[0].endswith(endpoint)
    payload = client.post.call_args_list[0].kwargs["json"]
    assert payload["input"] == (
        "Hello" if input_type == "text" else [{"type": "text", "text": "Hello"}]
    )
    assert client.post.call_args_list[1].args[0].endswith("/chat/completions")
    client.post.side_effect = [
        httpx.Response(403, text="ark-test-secret"),
        httpx.ConnectError("ark-test-secret"),
    ]
    with patch("httpx.Client", return_value=client):
        results = ark.probe_models(config)
    assert all(not ok for _, ok, _ in results)
    assert "ark-test-secret" not in repr(results)


def test_account_setup_is_routed_through_existing_save_flow(tmp_path):
    config = {"embedding": {"dense": {}}, "vlm": {}}
    target = tmp_path / "ov.conf"
    with (
        patch.object(wizard, "_config_path", return_value=target),
        patch.object(wizard, "_prompt_choice", return_value=4),
        patch.object(wizard, "_wizard_volcengine", return_value=config),
        patch.object(wizard, "_wizard_query_planner"),
        patch.object(wizard, "_wizard_server", return_value={"host": "127.0.0.1", "port": 1933}),
        patch.object(wizard, "_wizard_gateway", return_value={"enabled": False}),
        patch.object(wizard, "_prompt_confirm", return_value=True),
        patch.object(wizard, "_post_save_actions"),
    ):
        assert wizard.run_init() == 0
    assert json.loads(target.read_text())["server"]["host"] == "127.0.0.1"


def test_model_catalog_uses_selected_route_and_preserves_returned_ids():
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = httpx.Response(
        200,
        json={
            "data": [
                {"id": "model-from-server", "domain": "VLM"},
                {"id": "embed-from-server", "domain": "Embedding"},
            ]
        },
    )
    with patch("httpx.Client", return_value=client):
        models = ark.fetch_models("https://ark.cn-beijing.volces.com/api/coding/v3", "test-key")
    assert [m["id"] for m in models] == ["model-from-server", "embed-from-server"]
    assert client.get.call_args.args[0].endswith("/api/coding/v3/models")


def test_failed_catalog_does_not_fall_back_to_payg():
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = httpx.Response(404, text="secret-key")
    with patch("httpx.Client", return_value=client), pytest.raises(ark.ArkSetupError) as error:
        ark.fetch_models("https://ark.cn-beijing.volces.com/api/plan/v3", "secret-key")
    assert client.get.call_count == 1
    assert "secret-key" not in str(error.value)


def test_activation_pagination_and_unknown_not_treated_as_available():
    with ark.VolcengineSetup() as account, patch.object(account, "_call") as call:
        call.side_effect = [
            {"TotalCount": 2, "Items": [{"FoundationModelName": "vision", "State": "Available"}]},
            {"TotalCount": 2, "Items": [{"FoundationModelName": "embed", "State": "Unavailable"}]},
        ]
        states = account.model_activations()
        assert call.call_args_list[1].args[1]["PageNumber"] == 2
    models = [
        {"id": "vision-123456", "name": "vision", "domain": "VLM"},
        {"id": "embed-123456", "name": "embed", "domain": "Embedding"},
        {"id": "unknown"},
        {"id": "vision-654321", "name": "vision-654321", "version": "654321"},
    ]
    assert ark.activated_models(models, states) == [models[0], models[3]]


def test_no_activated_pair_never_probes_or_returns_config(account_setup, capsys):
    account_setup[0].model_activations.return_value = {}
    assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[3].assert_not_called()
    assert "No confirmed usable" in capsys.readouterr().out


def test_unknown_capabilities_do_not_pollute_embedding_menu(account_setup):
    plan = wizard.VOLCENGINE_PLANS[-1]
    rows = [
        {"id": "hyper3d"},
        {"id": "translation"},
        {"id": plan.default_vlm_model, "domain": "VLM"},
        {"id": plan.default_embedding_model, "domain": "Embedding"},
    ]
    account_setup[0].model_activations.return_value.update(
        {"hyper3d": "Available", "translation": "Available"}
    )
    with (
        patch.object(ark, "fetch_models", return_value=rows),
        patch.object(wizard, "_prompt_choice", return_value=1) as choice,
    ):
        assert isinstance(wizard._wizard_volcengine(), dict)
    assert choice.call_args_list[-1].args[1] == [(plan.default_embedding_model, "Embedding")]


def test_subscription_does_not_use_payg_activation(account_setup):
    with patch.object(wizard, "_select_access_plan", return_value=wizard.VOLCENGINE_PLANS[0]):
        assert isinstance(wizard._wizard_volcengine(), dict)
    account_setup[0].model_activations.assert_not_called()


@pytest.mark.parametrize(
    "state,tier", [("Expired", "small"), ("Pending", "small"), ("Effective", "")]
)
def test_agent_plan_requires_active_subscription_and_tier(session, state, tier):
    account, _ = session
    with patch.object(
        account,
        "_call",
        return_value={
            "InfoList": [
                {"Status": state, "BizInfo": tier, "ResourceName": "RealAgentPlanPersonal"}
            ]
        },
    ) as call:
        with pytest.raises(ark.ArkSetupError, match="active personal Agent Plan"):
            account.agent_plan_models()
        call.assert_called_once()


@pytest.mark.parametrize("subscription_status", ["Effective", "Running"])
def test_agent_plan_uses_scoped_direct_model_catalog(session, subscription_status):
    account, _ = session
    with patch.object(
        account,
        "_call",
        side_effect=[
            {
                "InfoList": [
                    {
                        "Status": subscription_status,
                        "BizInfo": "medium",
                        "ResourceName": "RealAgentPlanPersonal",
                    }
                ]
            },
            {
                "Data": [
                    {
                        "VirtualModelID": "direct-vision",
                        "ModelCapability": {
                            "OutputModalities": ["text"],
                            "InputModalities": ["text", "image"],
                        },
                    },
                    {
                        "VirtualModelID": "direct-embed",
                        "ModelCapability": {"OutputModalities": ["embedding"]},
                    },
                    {
                        "VirtualModelID": "image-generator",
                        "ModelCapability": {"OutputModalities": ["image"]},
                    },
                    {"VirtualModelID": "unknown"},
                ]
            },
        ],
    ) as call:
        models = account.agent_plan_models()
    assert call.call_args_list[0].args == (
        "ListSubscribeTrade",
        {
            "ResourceTypes": ["AgentPlan"],
            "BizInfos": ["small", "medium", "large", "max"],
            "ResourceNames": ["RealAgentPlanPersonal"],
        },
    )
    assert call.call_args_list[1].args == (
        "ListAgentPlanVirtualModelID",
        {"Edition": "personal", "BizInfo": "medium"},
    )
    assert models == [
        {"id": "direct-vision", "domain": "VLM"},
        {"id": "direct-embed", "domain": "Embedding"},
        {"id": "image-generator", "domain": None},
        {"id": "unknown", "domain": None},
    ]


def test_agent_plan_wizard_never_requests_data_plane_models(account_setup):
    with (
        patch.object(wizard, "_select_access_plan", return_value=wizard.VOLCENGINE_PLANS[0]),
        patch.object(ark, "fetch_models", side_effect=AssertionError("No /models for Agent Plan")),
    ):
        assert isinstance(wizard._wizard_volcengine(), dict)
    account_setup[0].agent_plan_models.assert_called_once()


def test_agent_plan_catalog_error_does_not_fall_back_to_payg(account_setup):
    account_setup[0].agent_plan_models.side_effect = ark.ArkSetupError("catalog unavailable")
    with (
        patch.object(wizard, "_select_access_plan", return_value=wizard.VOLCENGINE_PLANS[0]),
        patch.object(ark, "fetch_models") as catalog,
    ):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
    catalog.assert_not_called()
    account_setup[3].assert_not_called()


def test_agent_plan_wrong_pool_never_queries_models(session):
    account, _ = session
    subscription = {"Status": "Running", "BizInfo": "max", "ResourceName": "OtherPlan"}
    with patch.object(account, "_call", return_value={"InfoList": [subscription]}) as call:
        with pytest.raises(ark.ArkSetupError):
            account.agent_plan_models()
    call.assert_called_once()


@pytest.mark.parametrize(
    "catalog",
    [
        {},
        {"Data": []},
        {"Data": [None]},
        {"Data": [{"ModelID": "routing-target"}]},
        {"Data": [{"VirtualModelID": "model", "ModelCapability": {"OutputModalities": "text"}}]},
    ],
)
def test_agent_plan_rejects_incomplete_or_malformed_catalog(session, catalog):
    account, _ = session
    with (
        patch.object(
            account,
            "_call",
            side_effect=[
                {
                    "InfoList": [
                        {
                            "Status": "Running",
                            "BizInfo": "max",
                            "ResourceName": "RealAgentPlanPersonal",
                        }
                    ]
                },
                catalog,
            ],
        ),
        pytest.raises(ark.ArkSetupError),
    ):
        account.agent_plan_models()


def test_empty_key_list_can_switch_projects_without_reauthorizing(account_setup):
    account = account_setup[0]
    account.list_keys.side_effect = [[], [{"Id": 42, "ProjectName": "demo"}]]
    with (
        patch.object(wizard, "_prompt_choice", side_effect=[2, 1, 1]),
        patch.object(wizard, "_prompt_required_input", return_value="demo"),
    ):
        assert isinstance(wizard._wizard_volcengine(), dict)
    assert [call.args for call in account.list_keys.call_args_list] == [("default",), ("demo",)]
    account.wait_for_authorization.assert_called_once()
    account.api_key.assert_called_once_with({"Id": 42, "ProjectName": "demo"})


def test_empty_key_list_can_return_without_creating_key(account_setup):
    account_setup[0].list_keys.return_value = []
    with patch.object(wizard, "_prompt_choice", return_value=3):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[0].api_key.assert_not_called()
    assert account_setup[1].call_count == 1


@pytest.mark.parametrize("raises", [False, True])
def test_key_creation_browser_failure_keeps_authorization(account_setup, capsys, raises):
    import webbrowser

    account, browser, confirm, _ = account_setup
    account.list_keys.side_effect = [[], [{"Id": 42}]]
    browser.side_effect = [True, webbrowser.Error("unavailable") if raises else False]
    confirm.side_effect = [True, False]
    assert isinstance(wizard._wizard_volcengine(), dict)
    account.wait_for_authorization.assert_called_once()
    assert account.list_keys.call_count == 2
    assert "Open this link in your browser:" in capsys.readouterr().out


def test_activation_browser_failure_still_refreshes_models(account_setup, capsys):
    import webbrowser

    account, browser, confirm, _ = account_setup
    active = account.model_activations.return_value
    account.model_activations.side_effect = [{}, active]
    browser.side_effect = [True, webbrowser.Error("unavailable")]
    confirm.side_effect = [True, True, False]
    assert isinstance(wizard._wizard_volcengine(), dict)
    account.wait_for_authorization.assert_called_once()
    assert account.model_activations.call_count == 2
    assert "Open this link in your browser:" in capsys.readouterr().out


@pytest.mark.parametrize("reported_input", [None, "text", "multimodal"])
def test_embedding_protocol_is_saved_and_passed_to_verification(account_setup, reported_input):
    plan = wizard.VOLCENGINE_PLANS[0]
    embedding = {"id": "custom-embedding", "domain": "Embedding"}
    if reported_input:
        embedding["input"] = reported_input
    account, _, confirm, probe = account_setup
    account.agent_plan_models.return_value = [
        {"id": plan.default_vlm_model, "domain": "VLM"},
        embedding,
    ]
    confirm.return_value = True
    probe.return_value = [("Embedding", True, "ready"), ("VLM", True, "ready")]
    choices = [1, 1, 2] if reported_input is None else [1, 1]
    with (
        patch.object(wizard, "_select_access_plan", return_value=plan),
        patch.object(wizard, "_prompt_choice", side_effect=choices),
        patch.object(wizard, "_prompt_required_int", return_value=1024),
    ):
        config = wizard._wizard_volcengine()
    assert config["embedding"]["dense"]["input"] == (reported_input or "text")
    probe.assert_called_once_with(config)


def test_embedding_protocol_can_go_back_without_verifying(account_setup):
    plan = wizard.VOLCENGINE_PLANS[0]
    account_setup[0].agent_plan_models.return_value = [
        {"id": plan.default_vlm_model, "domain": "VLM"},
        {"id": "unknown-protocol", "domain": "Embedding"},
    ]
    with (
        patch.object(wizard, "_select_access_plan", return_value=plan),
        patch.object(wizard, "_prompt_choice", side_effect=[1, 1, 0]),
    ):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[3].assert_not_called()


def test_text_only_llm_cannot_be_saved_as_vlm(account_setup):
    plan = wizard.VOLCENGINE_PLANS[0]
    account_setup[0].agent_plan_models.return_value = [
        {"id": "text-only", "domain": "LLM"},
        {"id": plan.default_embedding_model, "domain": "Embedding"},
    ]
    with patch.object(wizard, "_select_access_plan", return_value=plan):
        assert wizard._wizard_volcengine() is wizard._GO_BACK
    account_setup[3].assert_not_called()


def test_vlm_menu_excludes_llm_when_vision_model_is_available(account_setup):
    plan = wizard.VOLCENGINE_PLANS[0]
    account_setup[0].agent_plan_models.return_value = [
        {"id": "text-only", "domain": "LLM"},
        {"id": plan.default_vlm_model, "domain": "VLM"},
        {"id": plan.default_embedding_model, "domain": "Embedding"},
    ]
    with (
        patch.object(wizard, "_select_access_plan", return_value=plan),
        patch.object(wizard, "_prompt_choice", return_value=1) as choice,
    ):
        config = wizard._wizard_volcengine()
    assert choice.call_args_list[0].args[1] == [(plan.default_vlm_model, "VLM")]
    assert config["vlm"]["model"] == plan.default_vlm_model


@pytest.mark.parametrize("inputs,expected", [(["text"], "text"), (["text", "image"], "multimodal")])
def test_agent_plan_preserves_embedding_input_capability(session, inputs, expected):
    with patch.object(
        session[0],
        "_call",
        side_effect=[
            {
                "InfoList": [
                    {"Status": "Running", "BizInfo": "max", "ResourceName": "RealAgentPlanPersonal"}
                ]
            },
            {
                "Data": [
                    {
                        "VirtualModelID": "embed",
                        "ModelCapability": {
                            "OutputModalities": ["embedding"],
                            "InputModalities": inputs,
                        },
                    }
                ]
            },
        ],
    ):
        assert session[0].agent_plan_models() == [
            {"id": "embed", "domain": "Embedding", "input": expected}
        ]


def test_null_vlm_message_is_a_failed_probe_instead_of_crashing():
    config = {
        "embedding": {
            "dense": {
                "api_base": "https://example.invalid",
                "api_key": "test",
                "model": "embed",
                "dimension": 2,
            }
        },
        "vlm": {"api_base": "https://example.invalid", "api_key": "test", "model": "vlm"},
    }
    client = MagicMock()
    client.__enter__.return_value = client
    client.post.side_effect = [
        httpx.Response(200, json={"data": {"embedding": [0.1, 0.2]}}),
        httpx.Response(200, json={"choices": [{"message": None}]}),
    ]
    with patch("httpx.Client", return_value=client):
        results = ark.probe_models(config)
    assert results[0][1] is True
    assert results[1] == ("VLM", False, "unexpected model output")


@pytest.mark.parametrize("signed", [True, False, None, "true"])
def test_agent_plan_checks_account_agreement_before_catalog(session, signed):
    account, _ = session
    subscription = {
        "Status": "Running",
        "BizInfo": "max",
        "ResourceName": "RealAgentPlanPersonal",
        "NeedCheckAgreement": True,
    }
    with patch.object(
        account,
        "_call",
        side_effect=[
            {"InfoList": [subscription]},
            {"IsSigned": signed},
            {
                "Data": [
                    {
                        "VirtualModelID": "vision",
                        "ModelCapability": {
                            "InputModalities": ["text", "image"],
                            "OutputModalities": ["text"],
                        },
                    }
                ]
            },
        ],
    ) as call:
        if signed is True:
            assert account.agent_plan_models() == [{"id": "vision", "domain": "VLM"}]
            assert call.call_args_list[-1].args[0] == "ListAgentPlanVirtualModelID"
        else:
            with pytest.raises(ark.ArkSetupError):
                account.agent_plan_models()
            assert call.call_count == 2
    assert call.call_args_list[1].args == (
        "GetAccountAgreement",
        {"AgreementType": "AgreeAgentPlanService"},
    )


def test_agent_plan_agreement_lookup_failure_does_not_query_catalog(session):
    account, _ = session
    with patch.object(
        account,
        "_call",
        side_effect=[
            {
                "InfoList": [
                    {
                        "Status": "Effective",
                        "BizInfo": "small",
                        "ResourceName": "RealAgentPlanPersonal",
                        "NeedCheckAgreement": True,
                    }
                ]
            },
            ark.ArkSetupError("Agreement lookup unavailable"),
        ],
    ) as call:
        with pytest.raises(ark.ArkSetupError, match="Agreement lookup unavailable"):
            account.agent_plan_models()
    assert call.call_count == 2


@pytest.mark.parametrize(
    "inputs,domain",
    [
        (["text", "video"], "LLM"),
        (["text", "image"], "VLM"),
        (["text", "image", "video"], "VLM"),
    ],
)
def test_agent_plan_vlm_requires_image_input(session, inputs, domain):
    with patch.object(
        session[0],
        "_call",
        side_effect=[
            {
                "InfoList": [
                    {
                        "Status": "Effective",
                        "BizInfo": "small",
                        "ResourceName": "RealAgentPlanPersonal",
                    }
                ]
            },
            {
                "Data": [
                    {
                        "VirtualModelID": "model",
                        "ModelCapability": {
                            "InputModalities": inputs,
                            "OutputModalities": ["text"],
                        },
                    }
                ]
            },
        ],
    ):
        assert session[0].agent_plan_models() == [{"id": "model", "domain": domain}]


@pytest.mark.parametrize("verify", [False, True])
def test_custom_dimension_reprompts_before_saving_or_verifying(account_setup, verify, capsys):
    account, _, confirm, probe = account_setup
    plan = wizard.VOLCENGINE_PLANS[0]
    account.agent_plan_models.return_value = [
        {"id": plan.default_vlm_model, "domain": "VLM"},
        {"id": "custom-embed", "domain": "Embedding", "input": "text"},
    ]
    confirm.return_value = verify
    probe.return_value = [("Embedding", True, "ready"), ("VLM", True, "ready")]
    with (
        patch.object(wizard, "_select_access_plan", return_value=plan),
        patch("builtins.input", side_effect=["0", "-1", "1024"]) as prompt,
    ):
        config = wizard._wizard_volcengine()
    assert config["embedding"]["dense"]["dimension"] == 1024
    assert prompt.call_count == 3
    assert capsys.readouterr().out.count("Embedding dimension must be positive") == 2
    if verify:
        probe.assert_called_once_with(config)
    else:
        probe.assert_not_called()


@pytest.mark.parametrize(
    "failure,detail",
    [
        (httpx.ReadTimeout("secret"), "request timed out; check connectivity and retry"),
        (httpx.ConnectError("secret"), "connection failed; check network and API address"),
        (ValueError("secret"), "unexpected model response"),
    ],
)
def test_probe_preserves_sanitized_failure_categories(failure, detail):
    config = {
        "embedding": {
            "dense": {
                "api_base": "https://example.invalid",
                "api_key": "secret",
                "model": "embed",
                "dimension": 2,
            }
        },
        "vlm": {"api_base": "https://example.invalid", "api_key": "secret", "model": "vlm"},
    }
    with patch("httpx.Client.post", side_effect=failure):
        assert ark.probe_models(config) == [("Embedding", False, detail), ("VLM", False, detail)]


@pytest.mark.parametrize("dimension", [0, -1])
def test_probe_rejects_nonpositive_dimension_before_embedding_request(dimension):
    config = {
        "embedding": {
            "dense": {
                "api_base": "https://example.invalid",
                "api_key": "secret",
                "model": "embed",
                "dimension": dimension,
            }
        },
        "vlm": {"api_base": "https://example.invalid", "api_key": "secret", "model": "vlm"},
    }
    with patch(
        "httpx.Client.post",
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]}),
    ) as post:
        results = ark.probe_models(config)
    assert results[0] == ("Embedding", False, "embedding dimension must be positive")
    assert results[1][1] is True
    post.assert_called_once()
    assert post.call_args.args[0].endswith("/chat/completions")
