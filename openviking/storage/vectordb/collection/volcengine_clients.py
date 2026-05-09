# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json

import requests  # type: ignore
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

# Default request timeout (seconds)
DEFAULT_TIMEOUT = 30

# VikingDB API Version
VIKING_DB_VERSION = "2025-06-09"


class ClientForConsoleApi:
    _global_host = {
        "cn-beijing": "vikingdb.cn-beijing.volcengineapi.com",
        "cn-shanghai": "vikingdb.cn-shanghai.volcengineapi.com",
        "cn-guangzhou": "vikingdb.cn-guangzhou.volcengineapi.com",
    }

    def __init__(self, ak, sk, region, host=None, session_token=None):
        self.ak = ak
        self.sk = sk
        self.region = region
        self.host = host if host else ClientForConsoleApi._global_host[region]
        self.session_token = session_token or ""

        if not all([self.ak, self.sk, self.host, self.region]):
            raise ValueError("AK, SK, Host, and Region are required for ClientForConsoleApi")

    def prepare_request(self, method, params=None, data=None):
        if Request is None:
            raise ImportError(
                "volcengine package is required. Please install it via 'pip install volcengine'"
            )

        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(DEFAULT_TIMEOUT)
        r.set_socket_timeout(DEFAULT_TIMEOUT)
        mheaders = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
        }
        r.set_headers(mheaders)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path("/")
        if data is not None:
            r.set_body(json.dumps(data))

        credentials = Credentials(
            self.ak,
            self.sk,
            "vikingdb",
            self.region,
            session_token=self.session_token,
        )
        SignerV4.sign(r, credentials)
        return r

    def do_req(self, req_method, req_params=None, req_body=None):
        req = self.prepare_request(method=req_method, params=req_params, data=req_body)
        return requests.request(
            method=req.method,
            url=f"https://{self.host}{req.path}",
            headers=req.headers,
            params=req.query,
            data=req.body,
            timeout=DEFAULT_TIMEOUT,
        )


class ClientForDataApi:
    _global_host = {
        "cn-beijing": "api-vikingdb.vikingdb.cn-beijing.volces.com",
        "cn-shanghai": "api-vikingdb.vikingdb.cn-shanghai.volces.com",
        "cn-guangzhou": "api-vikingdb.vikingdb.cn-guangzhou.volces.com",
    }

    def __init__(self, ak, sk, region, host=None, session_token=None):
        self.ak = ak
        self.sk = sk
        self.region = region
        self.host = host if host else ClientForDataApi._global_host[region]
        self.session_token = session_token or ""

        if not all([self.ak, self.sk, self.host, self.region]):
            raise ValueError("AK, SK, Host, and Region are required for ClientForDataApi")

    def prepare_request(self, method, path, params=None, data=None):
        if Request is None:
            raise ImportError(
                "volcengine package is required. Please install it via 'pip install volcengine'"
            )

        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(DEFAULT_TIMEOUT)
        r.set_socket_timeout(DEFAULT_TIMEOUT)
        mheaders = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
        }
        r.set_headers(mheaders)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path(path)
        if data is not None:
            r.set_body(json.dumps(data))

        credentials = Credentials(
            self.ak,
            self.sk,
            "vikingdb",
            self.region,
            session_token=self.session_token,
        )
        SignerV4.sign(r, credentials)
        return r

    def do_req(self, req_method, req_path, req_params=None, req_body=None):
        req = self.prepare_request(
            method=req_method, path=req_path, params=req_params, data=req_body
        )
        return requests.request(
            method=req.method,
            url=f"https://{self.host}{req.path}",
            headers=req.headers,
            params=req.query,
            data=req.body,
            timeout=DEFAULT_TIMEOUT,
        )


class ClientForDataApiWithApiKey:
    """Data-plane-only client for Volcengine VikingDB using Bearer API key."""

    def __init__(self, api_key: str, host: str):
        self.api_key = api_key
        self.host = host.rstrip("/")

        if not self.api_key or not self.host:
            raise ValueError("api_key and host are required for ClientForDataApiWithApiKey")

    def prepare_request(self, method, path, params=None, data=None):
        if Request is None:
            raise ImportError(
                "volcengine package is required. Please install it via 'pip install volcengine'"
            )

        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(DEFAULT_TIMEOUT)
        r.set_socket_timeout(DEFAULT_TIMEOUT)
        mheaders = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
            "Authorization": f"Bearer {self.api_key}",
        }
        r.set_headers(mheaders)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path(path)
        if data is not None:
            r.set_body(json.dumps(data))
        return r

    def do_req(self, req_method, req_path, req_params=None, req_body=None):
        req = self.prepare_request(
            method=req_method, path=req_path, params=req_params, data=req_body
        )
        return requests.request(
            method=req.method,
            url=f"https://{self.host}{req.path}",
            headers=req.headers,
            params=req.query,
            data=req.body,
            timeout=DEFAULT_TIMEOUT,
        )
