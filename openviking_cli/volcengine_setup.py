"""Direct VolcEngine device authorization for the installer.

Protocol references:
https://github.com/volcengine/volcengine-cli/blob/master/cmd/consolelogin_oauth_client.go
https://github.com/volcengine/veadk-go/blob/main/auth/veauth/ark_token_provider.go

No CLI configuration or credential store is read or written. OAuth/STS credentials
live only for this setup session; only the selected model API key is handed off.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

_SIGNIN = "https://signin.volcengine.com/authorize/oauth"
_CLIENT_ID = "trn:signin:::devtools/cross-device"
_SCOPE = "Console:All:All"


class ArkSetupError(Exception):
    """Safe, user-facing error without response bodies or credentials."""


def _json_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ArkSetupError("VolcEngine returned an unreadable response. Please retry.") from exc
    if not isinstance(data, dict):
        raise ArkSetupError("VolcEngine returned an unexpected response. Please retry.")
    return data


class VolcengineSetup:
    """One in-memory device login and signed Ark control-plane session."""

    def __init__(self):
        self._client = httpx.Client(timeout=30, follow_redirects=False)
        self._credentials: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._credentials.clear()
        self._client.close()

    def start_authorization(self) -> dict[str, Any]:
        try:
            response = self._client.post(
                f"{_SIGNIN}/device_authorization",
                data={"client_id": _CLIENT_ID, "scope": _SCOPE, "device_info": "OpenViking"},
            )
        except httpx.HTTPError as exc:
            raise ArkSetupError(
                "Cannot reach VolcEngine authorization. Check your network."
            ) from exc
        data = _json_response(response)
        if response.status_code != 200:
            raise ArkSetupError(
                f"Cannot start VolcEngine authorization (HTTP {response.status_code})."
            )
        if not all(
            isinstance(data.get(k), str) and data[k]
            for k in ("device_code", "user_code", "verification_uri")
        ):
            raise ArkSetupError("VolcEngine authorization response is incomplete.")
        for field in ("verification_uri", "verification_uri_complete"):
            if not data.get(field):
                continue
            url = urlsplit(data[field])
            if url.scheme != "https" or url.hostname != "signin.volcengine.com" or url.username:
                raise ArkSetupError("Unexpected VolcEngine authorization address.")
        if not isinstance(data.get("expires_in"), int) or not 0 < data["expires_in"] <= 3600:
            raise ArkSetupError("Invalid device authorization expiry.")
        interval = data.get("interval", 5)
        if not isinstance(interval, int) or interval < 0:
            raise ArkSetupError("Invalid device authorization polling interval.")
        data["interval"] = max(5, interval)
        data["deadline"] = time.monotonic() + data["expires_in"]
        return data

    def wait_for_authorization(self, authorization: dict[str, Any]) -> None:
        interval = authorization["interval"]
        transient_errors = 0
        deadline = authorization["deadline"]
        while time.monotonic() + interval < deadline:
            time.sleep(interval)
            try:
                response = self._client.post(
                    f"{_SIGNIN}/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "client_id": _CLIENT_ID,
                        "scope": _SCOPE,
                        "device_code": authorization["device_code"],
                    },
                )
                data = _json_response(response)
            except (httpx.HTTPError, ArkSetupError):
                transient_errors += 1
                if transient_errors > 5:
                    raise ArkSetupError("Authorization connection failed. Please retry.") from None
                interval = min(max(interval, 5) * 2, max(interval, 30))
                continue
            code = data.get("error")
            if code in ("authorization_pending", "slow_down"):
                transient_errors = 0
                if code == "slow_down":
                    interval += 5
                continue
            if code in ("access_denied", "expired_token", "invalid_device_code"):
                raise ArkSetupError("Authorization was denied or expired. Please start again.")
            if code in ("server_error", "temporarily_unavailable") or response.status_code >= 500:
                transient_errors += 1
                if transient_errors <= 5:
                    continue
            if response.status_code != 200 or code:
                raise ArkSetupError(f"Authorization failed (HTTP {response.status_code}).")
            try:
                credentials = json.loads(data["access_token"])
                fields = ("access_key_id", "secret_access_key", "session_token")
                if not isinstance(credentials, dict) or not all(
                    isinstance(credentials.get(k), str) and credentials[k] for k in fields
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise ArkSetupError(
                    "VolcEngine did not return usable temporary credentials."
                ) from None
            self._credentials = {k: credentials[k] for k in fields}
            return
        raise ArkSetupError("Authorization timed out. Please start again.")

    def _call(self, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        from volcengine.auth.SignerV4 import SignerV4
        from volcengine.base.Request import Request
        from volcengine.Credentials import Credentials

        if not self._credentials:
            raise ArkSetupError("Complete browser authorization first.")
        # Explicit credentials avoid the SDK's environment/CLI credential chain.
        credentials = Credentials(
            self._credentials["access_key_id"],
            self._credentials["secret_access_key"],
            "ark",
            "cn-beijing",
            self._credentials["session_token"],
        )
        request = Request()
        request.set_schema("https")
        request.set_host("open.volcengineapi.com")
        request.set_path("/")
        request.set_method("POST")
        request.set_query({"Action": action, "Version": "2024-01-01"})
        request.set_headers({"Content-Type": "application/json", "Host": request.host})
        request.set_body(json.dumps(payload))
        SignerV4.sign(request, credentials)
        try:
            response = self._client.post(
                request.build(), headers=request.headers, content=request.body.encode("utf-8")
            )
        except httpx.HTTPError as exc:
            raise ArkSetupError(f"VolcEngine {action} request failed. Check your network.") from exc
        body = _json_response(response)
        metadata = body.get("ResponseMetadata")
        error = metadata.get("Error") if isinstance(metadata, dict) else None
        if response.status_code != 200 or error:
            detail = ""
            if isinstance(error, dict):
                # Only the error fields, never response bodies or request headers.
                detail = ": ".join(
                    str(error[field]) for field in ("Code", "Message") if error.get(field)
                )
                for secret in (*self._credentials.values(), request.headers["Authorization"]):
                    detail = detail.replace(secret, "[redacted]")
                detail = " ".join(detail.split())[:500]
            raise ArkSetupError(
                f"VolcEngine {action} failed (HTTP {response.status_code})"
                + (f": {detail}" if detail else ". Please retry or check the Ark console.")
            )
        result = body.get("Result")
        if not isinstance(result, dict):
            raise ArkSetupError(f"VolcEngine {action} returned an incomplete response.")
        return result

    def list_keys(
        self, project: str = "default", *, scene: str | None = None
    ) -> list[dict[str, Any]]:
        key_filter: dict[str, Any] = {"AllowAll": True}
        if scene:
            key_filter["Scene"] = scene
        rows: list[dict[str, Any]] = []
        for page in range(1, 101):
            result = self._call(
                "ListApiKeys",
                {
                    "ProjectName": project,
                    "Filter": key_filter,
                    "PageNumber": page,
                    "PageSize": 100,
                },
            )
            items = result.get("Items")
            if not isinstance(items, list) or not all(isinstance(row, dict) for row in items):
                raise ArkSetupError("VolcEngine returned an invalid API key list.")
            rows.extend(items)
            total = result.get("TotalCount")
            if (isinstance(total, int) and len(rows) >= total) or (
                total is None and len(items) < 100
            ):
                break
            if not items:
                raise ArkSetupError("VolcEngine returned an incomplete API key list.")
        else:
            raise ArkSetupError("Too many API keys to list. Use manual configuration.")
        # Never expose Key fields or cross ordinary/Coding and Agent Plan key pools.
        return [
            {"Id": row["Id"], "Name": row.get("Name"), "ProjectName": project}
            for row in rows
            if row.get("Status") == "Active"
            and type(row.get("Id")) is int
            and (
                row.get("Scene") in (None, scene)
                if scene
                else row.get("Scene") != "RealAgentPlanPersonal"
            )
        ]

    def agent_plan_models(self) -> list[dict[str, Any]]:
        """Read the personal subscription's direct model IDs, not routing targets."""
        result = self._call(
            "ListSubscribeTrade",
            {
                "ResourceTypes": ["AgentPlan"],
                "BizInfos": ["small", "medium", "large", "max"],
                "ResourceNames": ["RealAgentPlanPersonal"],
            },
        )
        subscriptions = result.get("InfoList")
        if not isinstance(subscriptions, list) or not all(
            isinstance(row, dict) for row in subscriptions
        ):
            raise ArkSetupError("VolcEngine returned an invalid Agent Plan subscription list.")
        active = [
            row
            for row in subscriptions
            if row.get("Status") in {"Effective", "Running"}
            and row.get("ResourceName") == "RealAgentPlanPersonal"
        ]
        if (
            len(active) != 1
            or not isinstance(active[0].get("BizInfo"), str)
            or not active[0]["BizInfo"]
        ):
            raise ArkSetupError(
                "Could not identify one active personal Agent Plan subscription and tier. "
                "Check your subscription in the Ark console."
            )
        if active[0].get("NeedCheckAgreement"):
            agreement = self._call(
                "GetAccountAgreement", {"AgreementType": "AgreeAgentPlanService"}
            )
            if agreement.get("IsSigned") is False:
                raise ArkSetupError(
                    "Review the Agent Plan service agreement in the Ark console first."
                )
            if agreement.get("IsSigned") is not True:
                raise ArkSetupError("VolcEngine returned an invalid Agent Plan agreement status.")
        result = self._call(
            "ListAgentPlanVirtualModelID",
            {"Edition": "personal", "BizInfo": active[0]["BizInfo"]},
        )
        rows = result.get("Data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ArkSetupError("VolcEngine returned an invalid Agent Plan model catalog.")
        models = {}
        for row in rows:
            model_id = row.get("VirtualModelID")
            capability = row.get("ModelCapability") or {}
            if not isinstance(model_id, str) or not model_id or not isinstance(capability, dict):
                raise ArkSetupError("VolcEngine returned an incomplete Agent Plan model entry.")
            outputs = capability.get("OutputModalities") or []
            inputs = capability.get("InputModalities") or []
            if not all(
                isinstance(values, list) and all(isinstance(v, str) for v in values)
                for values in (inputs, outputs)
            ):
                raise ArkSetupError("VolcEngine returned invalid Agent Plan model capabilities.")
            domain = None
            if "text" in outputs:
                domain = "VLM" if "image" in inputs else "LLM"
            elif "embedding" in outputs:
                domain = "Embedding"
            model = {"id": model_id, "domain": domain}
            if domain == "Embedding":
                if "image" in inputs or "video" in inputs:
                    model["input"] = "multimodal"
                elif "text" in inputs:
                    model["input"] = "text"
            models[model_id] = model
        if not models:
            raise ArkSetupError("No direct models were returned for your Agent Plan subscription.")
        return list(models.values())

    def model_activations(self) -> dict[str, str]:
        """Read account activation separately from the public model catalog."""
        states: dict[str, str] = {}
        count = 0
        for page in range(1, 101):
            result = self._call(
                "ListModelActivations",
                {
                    "PageNumber": page,
                    "PageSize": 100,
                    "Filter": {"IncludeDeprecatedModels": False},
                },
            )
            items = result.get("Items")
            if not isinstance(items, list) or not all(isinstance(x, dict) for x in items):
                raise ArkSetupError("VolcEngine returned an invalid model activation list.")
            for item in items:
                name, state = item.get("FoundationModelName"), item.get("State")
                if isinstance(name, str) and isinstance(state, str):
                    if name in states and states[name] != state:
                        raise ArkSetupError(
                            "VolcEngine returned conflicting model activation states."
                        )
                    states[name] = state
            count += len(items)
            total = result.get("TotalCount")
            if (isinstance(total, int) and count >= total) or (total is None and len(items) < 100):
                return states
            if not items:
                break
        raise ArkSetupError("VolcEngine returned an incomplete model activation list.")

    def api_key(self, selected: dict[str, Any]) -> str:
        payload = {"Id": selected["Id"]}
        if selected.get("ProjectName"):
            payload["ProjectName"] = selected["ProjectName"]
        result = self._call("GetRawApiKey", payload)
        key = result.get("ApiKey")
        if (
            not isinstance(key, str)
            or not key
            or "*" in key
            or any(c.isspace() for c in key)
            or key.startswith("{")
        ):
            raise ArkSetupError("VolcEngine did not return a usable model API key.")
        return key


def fetch_models(api_base: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch the selected data-plane catalog; never substitute another billing route."""
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get(
                api_base.rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as exc:
        raise ArkSetupError(
            "Could not fetch the model list. Check your connection and retry."
        ) from exc
    if response.status_code != 200:
        raise ArkSetupError(
            f"Model list unavailable on the selected access mode (HTTP {response.status_code})."
        )
    data = _json_response(response).get("data")
    if not isinstance(data, list):
        raise ArkSetupError("Model catalog returned an unexpected response.")
    models = {}
    for row in data:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
            models[row["id"]] = row
    if not models:
        raise ArkSetupError("No models are visible with this API key and access mode.")
    return list(models.values())


def activated_models(models: list[dict[str, Any]], states: dict[str, str]) -> list[dict[str, Any]]:
    """Join exact base names; unknown entitlements must not appear as activated."""
    result = []
    for model in models:
        name = model.get("name") or model["id"]
        state = states.get(name)
        # Some catalog responses use the full version ID as name.
        version = model.get("version")
        if state is None and isinstance(version, str) and name.endswith("-" + version):
            state = states.get(name[: -len(version) - 1])
        if state == "Available":
            result.append(model)
    return result


def probe_models(config: dict[str, Any]) -> list[tuple[str, bool, str]]:
    """Make one small request per model, without printing provider bodies/errors."""
    import httpx

    dense = config["embedding"]["dense"]
    vlm = config["vlm"]
    text_embedding = dense.get("input", "multimodal") == "text"
    probes = [
        (
            "Embedding",
            dense,
            "embeddings" if text_embedding else "embeddings/multimodal",
            {
                "model": dense["model"],
                "input": "Hello" if text_embedding else [{"type": "text", "text": "Hello"}],
            },
        ),
        (
            "VLM",
            vlm,
            "chat/completions",
            {
                "model": vlm["model"],
                "messages": [{"role": "user", "content": "Reply OK."}],
                "max_tokens": 32,
                "thinking": {"type": "disabled"},
            },
        ),
    ]
    results = []
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for label, section, endpoint, payload in probes:
            if label == "Embedding" and (
                type(dense.get("dimension")) is not int or dense["dimension"] <= 0
            ):
                results.append((label, False, "embedding dimension must be positive"))
                continue
            try:
                response = client.post(
                    f"{section['api_base']}/{endpoint}",
                    headers={"Authorization": f"Bearer {section['api_key']}"},
                    json=payload,
                )
                if response.status_code != 200:
                    detail = f"HTTP {response.status_code}"
                    try:
                        error = response.json().get("error", {})
                        if isinstance(error, dict):
                            reason = ": ".join(
                                str(error[field])
                                for field in ("code", "message")
                                if error.get(field)
                            )
                            reason = " ".join(
                                reason.replace(section["api_key"], "[redacted]").split()
                            )[:500]
                            if reason:
                                detail += f": {reason}"
                    except (ValueError, AttributeError):
                        pass
                    results.append((label, False, detail))
                    continue
                body = response.json()
                if label == "Embedding":
                    vector = (
                        body["data"][0]["embedding"]
                        if text_embedding
                        else body["data"]["embedding"]
                    )
                    # The runtime truncates and normalizes the provider vector.
                    valid = isinstance(vector, list) and len(vector) >= dense["dimension"]
                else:
                    message = body["choices"][0]["message"]
                    valid = isinstance(message, dict) and bool(message.get("content"))
                results.append((label, valid, "ready" if valid else "unexpected model output"))
            except httpx.TimeoutException:
                results.append((label, False, "request timed out; check connectivity and retry"))
            except httpx.HTTPError:
                results.append((label, False, "connection failed; check network and API address"))
            except (ValueError, KeyError, IndexError, TypeError):
                results.append((label, False, "unexpected model response"))
    return results
