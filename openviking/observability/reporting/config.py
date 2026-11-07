"""External reporting configuration. No cloud-specific endpoints or credentials."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_configuration(settings) -> dict:
    """Read shared global_configs from a local snapshot or Nacos v1 API."""
    path = settings.config_file or os.getenv("NACOS_CONFIG_FILE")
    if path:
        content = Path(path).read_text(encoding="utf-8")
    else:
        server = settings.nacos_server or os.getenv("NACOS_SERVER_ADDR", "")
        if not server:
            raise ValueError("Reporting requires an external config file or NACOS_SERVER_ADDR")
        server = server.rstrip("/")
        if "://" not in server:
            server = "http://" + server
        if not server.endswith("/nacos"):
            server += "/nacos"
        opener = build_opener(_NoRedirect)
        query = {
            "dataId": settings.nacos_data_id or os.getenv("NACOS_DATA_ID", "global_configs"),
            "group": settings.nacos_group or os.getenv("NACOS_GROUP", "DEFAULT_GROUP"),
        }
        namespace = settings.nacos_namespace or os.getenv("NACOS_NAMESPACE", "public")
        if namespace and namespace != "public":
            query["tenant"] = namespace
        username = os.getenv("NACOS_USERNAME")
        if username:
            form = urlencode({"username": username, "password": os.getenv("NACOS_PASSWORD", "")})
            request = Request(
                server + "/v1/auth/login",
                data=form.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with opener.open(request, timeout=10) as response:
                query["accessToken"] = json.load(response)["accessToken"]
        with opener.open(server + "/v1/cs/configs?" + urlencode(query), timeout=10) as response:
            content = response.read(8 * 1024 * 1024).decode("utf-8")

    def replacement(match):
        name = match.group(1)
        if name not in os.environ:
            raise ValueError("Missing configuration environment variable " + name)
        return os.environ[name]

    config = json.loads(content)
    if not isinstance(config, dict):
        raise ValueError("Reporting configuration must be a JSON object")

    def resolve(value):
        if isinstance(value, str):
            return re.sub(r"\$\{([A-Z][A-Z0-9_]*)\}", replacement, value)
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    # Other services' shared secrets do not become bootstrap requirements for OV.
    return resolve({key: config[key] for key in ("kafka", "telemetry") if key in config})
