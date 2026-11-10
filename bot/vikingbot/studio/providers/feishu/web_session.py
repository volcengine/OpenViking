"""Short-lived Feishu console sessions. No cookies or login tokens are persisted.

The console login contract is isolated here because it is not a public OAuth API.
"""

import json
import re
from urllib.parse import urljoin, urlparse

import httpx


class SetupError(Exception):
    """Safe error code suitable for the Studio API; never contains upstream bodies."""


ACCOUNTS = "https://accounts.feishu.cn"
REDIRECT = "https://ask.feishu.cn/"
HEADERS = {
    "x-app-id": "12",
    "x-api-version": "1.0.28",
    "x-device-info": "device_id=0;device_name=Chrome;device_os=Mac;device_model=Chrome;"
    "lark_version=;channel=Release;package_name=feishu;tt_app_id=1658;"
    "is_dpop_support=true;is_iframe=false",
    "x-locale": "zh-CN",
    "x-terminal-type": "2",
}


def allowed_url(url):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.port in (None, 443)
        and not parsed.username
        and not parsed.password
        and any(
            parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain)
            for domain in ("feishu.cn", "larkoffice.com")
        )
    )


def payload(response):
    try:
        response.raise_for_status()
        value = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SetupError("platform_request_failed") from exc
    if not isinstance(value, dict) or value.get("code", 0) != 0:
        raise SetupError("platform_rejected")
    return value


def data(value):
    return value.get("data", value)


def field(value, *names):
    for container in (value, data(value)):
        for name in names:
            if isinstance(container.get(name), str) and container[name]:
                return container[name]
    return None


class FeishuWebSession:
    def __init__(self, client=None):
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        self.origin = "https://open.feishu.cn"
        self.csrf = None
        self.flow_key = None

    async def close(self):
        self.client.cookies.clear()
        self.csrf = self.flow_key = None
        await self.client.aclose()

    async def request(self, method, url, **kwargs):
        # Follow only trusted HTTPS redirects, without forwarding CSRF to another origin.
        for _ in range(10):
            if not allowed_url(url):
                raise SetupError("untrusted_redirect")
            response = await self.client.request(method, url, **kwargs)
            if not response.is_redirect:
                return response
            url = urljoin(str(response.url), response.headers.get("location", ""))
            if method != "GET":
                raise SetupError("session_expired")
        raise SetupError("session_expired")

    async def begin(self):
        response = await self.request(
            "POST",
            ACCOUNTS + "/accounts/qrlogin/init",
            headers=HEADERS,
            json={"biz_type": None, "redirect_uri": REDIRECT},
        )
        token = data(payload(response)).get("step_info", {}).get("token")
        self.flow_key = response.headers.get("x-flow-key")
        if not isinstance(token, str) or not self.flow_key:
            raise SetupError("login_unavailable")
        return json.dumps({"qrlogin": {"token": token}}, separators=(",", ":"))

    async def poll(self):
        response = await self.request(
            "POST",
            ACCOUNTS + "/accounts/qrlogin/polling",
            headers={**HEADERS, "x-flow-key": self.flow_key},
            json={"biz_type": None},
        )
        result = data(payload(response))
        step = result.get("step_info", {})
        if result.get("next_step") == "enter_app":
            if step.get("cross_login_uri"):
                await self.request("GET", step["cross_login_uri"])
            await self.request("GET", REDIRECT)
            return "authorized"
        return {2: "scanned", 5: "expired"}.get(step.get("status"), "waiting_for_scan")

    async def identity(self):
        page = await self.request("GET", "https://open.feishu.cn/app")
        page.raise_for_status()
        match = re.search(r"\b(?:window\.csrfToken\s*=|csrfToken\s*:)\s*['\"]([^'\"]+)", page.text)
        user_match = re.search(r"\bwindow\.user\s*=\s*", page.text)
        if not match or not user_match:
            raise SetupError("session_expired")
        try:
            user, _ = json.JSONDecoder().raw_decode(page.text[user_match.end() :])
        except ValueError as exc:
            raise SetupError("identity_unavailable") from exc
        identity = {
            "user_id": field(user, "id", "userId", "user_id"),
            "tenant_id": field(user, "tenantId", "tenant_id"),
            "user_name": field(user, "name", "userName", "user_name")
            or user.get("displayName", {}).get("value"),
            "tenant_name": field(user, "tenantName", "tenant_name")
            or user.get("tenantDisplayName", {}).get("value"),
        }
        if not all(identity.values()):
            raise SetupError("identity_unavailable")
        self.origin = str(page.url).split("/app")[0]
        if self.origin not in ("https://open.feishu.cn", "https://open.larkoffice.com"):
            raise SetupError("untrusted_redirect")
        self.csrf = match[1]
        return identity

    async def post(self, path, body=None, files=None):
        headers = {
            "origin": self.origin,
            "referer": self.origin + "/app",
            "x-csrf-token": self.csrf,
        }
        kwargs = {"files": files, "data": body} if files else {"json": body or {}}
        return payload(await self.request("POST", self.origin + path, headers=headers, **kwargs))
