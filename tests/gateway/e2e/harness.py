"""Kubernetes resources and subprocess clients for the gateway acceptance matrix."""

import base64
import copy
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
# Redis 7.2 checks the disabled default user's ACL while replaying AOF MULTI/EXEC.
ACL = """user default off ~* &* +@all
user admin on >e2e-admin ~* &* +@all
user gateway on >e2e-gateway ~ovgw:* +@read +@write +@scripting +@connection +@transaction +ping +time +scan +unlink +cluster|slots +cluster|shards
"""


def wait_for(check, timeout=90, interval=0.5):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (httpx.HTTPError, subprocess.CalledProcessError) as error:
            last = error
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s: {last!r}")


def merge(target, changes):
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class Harness:
    def __init__(self, kubeconfig, mode, directory):
        self.kubeconfig, self.mode, self.directory = kubeconfig, mode, directory
        self.namespace = "ovgw-e2e-" + uuid.uuid4().hex[:10]
        self.image = os.getenv("OV_TEST_GATEWAY_IMAGE", "ghcr.io/volcengine/openviking:main")
        self.forwards = {}
        repository, tag = self.image.rsplit(":", 1)
        self.values = {
            "fullnameOverride": "gateway",
            "replicaCount": 2,
            "image": {"repository": repository, "tag": tag, "pullPolicy": "IfNotPresent"},
            "existingSecret": "gateway-secrets",
            "service": {"port": 1935},
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "2", "memory": "512Mi"},
            },
            "affinity": {
                "podAntiAffinity": {
                    "preferredDuringSchedulingIgnoredDuringExecution": [
                        {
                            "weight": 100,
                            "podAffinityTerm": {
                                "labelSelector": {
                                    "matchLabels": {"app.kubernetes.io/instance": "gateway"}
                                },
                                "topologyKey": "kubernetes.io/hostname",
                            },
                        }
                    ]
                }
            },
            "config": {
                "openviking_url": "http://mock-ov:1933",
                "openviking_keys": True,
                "tenancy": "key",
                "management_api": False,
                "capture_concurrency": 4,
                "request_log_sink": "stdout",
                "health_interval_seconds": 1,
                "default_profile": {
                    "idle_seconds": 5,
                    "gateway_tools": False,
                    "profile": False,
                    "show_recall": False,
                },
                "default_upstreams": {
                    protocol: {
                        "name": protocol,
                        "protocol": protocol,
                        "base_url": "http://mock-ark:8080"
                        if protocol == "anthropic"
                        else "http://mock-ark:8080/v1",
                        "api_key": "mock-upstream-secret",
                        "models": [
                            "mock",
                            "mock-silent",
                            "mock-long",
                            "mock-tools",
                            "mock-silent-gzip",
                        ],
                    }
                    for protocol in ("chat", "anthropic", "responses")
                },
                "storage": {
                    "kernel": "redis",
                    "management": "redis",
                    "key_prefix": "ovgw:",
                    "redis": {
                        "mode": mode,
                        "endpoints": ["redis://redis-0.redis:6379"],
                        "username": "gateway",
                        # The chart maps the Secret's redis-password entry to this variable.
                        "password_env": "OPENVIKING_GATEWAY_REDIS_PASSWORD",
                        "command_timeout_ms": 1000,
                    },
                },
            },
        }

    def command(self, command, *, input=None, timeout=180, check=True, binary=False):
        result = subprocess.run(
            command, input=input, capture_output=True, text=not binary, timeout=timeout, check=False
        )
        if check and result.returncode:
            raise AssertionError(
                f"command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}"
            )
        return result.stdout

    def kubectl(self, *args, **kwargs):
        return self.command(
            ["kubectl", "--kubeconfig", self.kubeconfig, "-n", self.namespace, *map(str, args)],
            **kwargs,
        )

    def apply(self, *items):
        return self.kubectl(
            "apply",
            "-f",
            "-",
            input=json.dumps({"apiVersion": "v1", "kind": "List", "items": items}),
        )

    def start(self):
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json"))["items"]
        assert len(nodes) == 3, "the suite requires one control-plane and two workers"
        self.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.namespace}})
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "gateway-secrets"},
                "data": {"root-key": base64.b64encode(secrets.token_hex(32).encode()).decode()},
                "stringData": {"redis-password": "e2e-gateway"},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "e2e-mocks"},
                "data": {p.name: p.read_text() for p in (HERE / "mocks").glob("*.py")},
            },
            {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {"name": "redis-config"},
                "data": {"users.acl": ACL},
            },
        )
        for service, port, file in (
            ("mock-ov", 1933, "mock_ov.py"),
            ("mock-ark", 8080, "mock_ark.py"),
        ):
            self.apply(
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": service},
                    "spec": {
                        "replicas": 1,
                        "selector": {"matchLabels": {"app": service}},
                        "template": {
                            "metadata": {"labels": {"app": service}},
                            "spec": {
                                "containers": [
                                    {
                                        "name": "mock",
                                        "image": self.image,
                                        "imagePullPolicy": "IfNotPresent",
                                        "command": ["python", "-u", "/mocks/" + file],
                                        "ports": [{"containerPort": port}],
                                        "readinessProbe": {
                                            "tcpSocket": {"port": port},
                                            "periodSeconds": 1,
                                        },
                                        "volumeMounts": [{"name": "mocks", "mountPath": "/mocks"}],
                                    }
                                ],
                                "volumes": [{"name": "mocks", "configMap": {"name": "e2e-mocks"}}],
                            },
                        },
                    },
                },
                {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {"name": service},
                    "spec": {"selector": {"app": service}, "ports": [{"port": port}]},
                },
            )
        self.start_redis()
        for deployment in ("mock-ov", "mock-ark"):
            self.kubectl("rollout", "status", "deployment/" + deployment, "--timeout=120s")
        self.redeploy({})
        pods = self.pod_objects()
        assert len({pod["spec"]["nodeName"] for pod in pods}) == 2, (
            "gateway replicas must occupy different workers"
        )
        for pod in self.pods():
            self.url(pod)
        self.url("service/mock-ov", 1933)
        self.url("service/mock-ark", 8080)

    def start_redis(self):
        args = [
            "redis-server",
            "--aclfile",
            "/config/users.acl",
            "--appendonly",
            "yes",
            "--appendfsync",
            "always",
            "--save",
            "",
            "--dir",
            "/data",
            "--maxmemory",
            "96mb",
            "--maxmemory-policy",
            "volatile-lru",
        ]
        if self.mode == "cluster":
            args += [
                "--cluster-enabled",
                "yes",
                "--cluster-config-file",
                "nodes.conf",
                "--cluster-node-timeout",
                "3000",
            ]
        self.apply(
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "redis"},
                "spec": {
                    "clusterIP": "None",
                    "publishNotReadyAddresses": True,
                    "selector": {"app": "redis"},
                    "ports": [{"port": 6379}],
                },
            },
            {
                "apiVersion": "apps/v1",
                "kind": "StatefulSet",
                "metadata": {"name": "redis"},
                "spec": {
                    "serviceName": "redis",
                    "replicas": 3 if self.mode == "cluster" else 1,
                    "selector": {"matchLabels": {"app": "redis"}},
                    "template": {
                        "metadata": {"labels": {"app": "redis"}},
                        "spec": {
                            "containers": [
                                {
                                    "name": "redis",
                                    "image": "redis:7.2-alpine",
                                    "args": args,
                                    "readinessProbe": {
                                        "tcpSocket": {"port": 6379},
                                        "periodSeconds": 1,
                                    },
                                    "volumeMounts": [
                                        {"name": "data", "mountPath": "/data"},
                                        {"name": "config", "mountPath": "/config"},
                                    ],
                                }
                            ],
                            "volumes": [{"name": "config", "configMap": {"name": "redis-config"}}],
                        },
                    },
                    "volumeClaimTemplates": [
                        {
                            "metadata": {"name": "data"},
                            "spec": {
                                "accessModes": ["ReadWriteOnce"],
                                "resources": {"requests": {"storage": "256Mi"}},
                            },
                        }
                    ],
                },
            },
        )
        self.kubectl("rollout", "status", "statefulset/redis", "--timeout=150s")
        if self.mode == "cluster":
            script = (
                "nodes=\"\"; for n in 0 1 2; do ip=$(getent hosts redis-$n.redis | awk '{print $1; exit}'); "
                'nodes="$nodes $ip:6379"; done; redis-cli --user admin -a e2e-admin '
                "--cluster create $nodes --cluster-replicas 0 --cluster-yes"
            )
            self.apply(
                {
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "metadata": {"name": "redis-init"},
                    "spec": {
                        "backoffLimit": 0,
                        "template": {
                            "spec": {
                                "restartPolicy": "Never",
                                "containers": [
                                    {
                                        "name": "init",
                                        "image": "redis:7.2-alpine",
                                        "command": ["sh", "-ec", script],
                                    }
                                ],
                            }
                        },
                    },
                }
            )
            self.kubectl("wait", "--for=condition=complete", "job/redis-init", "--timeout=90s")
            wait_for(lambda: "cluster_state:ok" in self.redis("CLUSTER", "INFO"))

    def redeploy(self, config, values=None, wait=True):
        merge(self.values["config"], config)
        merge(self.values, values or {})
        path = self.directory / "values.json"
        path.write_text(json.dumps(self.values))
        chart = os.getenv("OV_TEST_GATEWAY_CHART", str(ROOT / "deploy/helm/openviking-gateway"))
        self.command(
            [
                "helm",
                "--kubeconfig",
                self.kubeconfig,
                "upgrade",
                "--install",
                "gateway",
                chart,
                "-n",
                self.namespace,
                "-f",
                str(path),
            ],
            timeout=120,
        )
        if wait:
            self.kubectl("rollout", "status", "deployment/gateway", "--timeout=180s", timeout=190)
            wait_for(lambda: len(self.pods()) == 2)

    def pod_objects(self):
        return [
            p
            for p in json.loads(
                self.kubectl(
                    "get", "pods", "-l", "app.kubernetes.io/instance=gateway", "-o", "json"
                )
            )["items"]
            if not p["metadata"].get("deletionTimestamp")
            and p["status"].get("phase") == "Running"
            and all(c.get("ready") for c in p["status"].get("containerStatuses", []))
        ]

    def pods(self):
        return sorted(p["metadata"]["name"] for p in self.pod_objects())

    def url(self, resource, port=1935):
        resource = resource if "/" in resource else "pod/" + resource
        cached = self.forwards.get(resource)
        if cached and cached[0].poll() is None:
            return cached[1]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            local = sock.getsockname()[1]
        log = open(self.directory / (resource.replace("/", "-") + ".forward.log"), "w+")
        process = subprocess.Popen(
            [
                "kubectl",
                "--kubeconfig",
                self.kubeconfig,
                "-n",
                self.namespace,
                "port-forward",
                resource,
                f"{local}:{port}",
            ],
            stdout=log,
            stderr=log,
        )
        url = f"http://127.0.0.1:{local}"
        self.forwards[resource] = (process, url, log)

        def ready():
            assert process.poll() is None, f"port-forward exited: {resource}"
            log.flush()
            return "Forwarding from" in Path(log.name).read_text()

        wait_for(ready, timeout=30)
        return url

    def request(self, pod, method, path, key="key-a", **kwargs):
        headers = {"Authorization": "Bearer " + key, **kwargs.pop("headers", {})}
        return httpx.request(method, self.url(pod) + path, headers=headers, timeout=90, **kwargs)

    @contextmanager
    def stream(self, pod, body, key="key-a", headers=None):
        with httpx.stream(
            "POST",
            self.url(pod) + "/gateway/v1/chat/completions",
            json=body,
            headers={"Authorization": "Bearer " + key, **(headers or {})},
            timeout=100,
        ) as response:
            response.raise_for_status()
            yield response

    def mock(self, service, method, path, **kwargs):
        port = 1933 if service == "ov" else 8080
        return httpx.request(
            method, self.url("service/mock-" + service, port) + path, timeout=30, **kwargs
        )

    def redis_bytes(self, *args, pod="redis-0"):
        return self.kubectl(
            "exec",
            pod,
            "--",
            "redis-cli",
            "--no-auth-warning",
            "--raw",
            "-c",
            "--user",
            "admin",
            "-a",
            "e2e-admin",
            *args,
            binary=True,
        ).rstrip(b"\n")

    def redis(self, *args, **kwargs):
        return self.redis_bytes(*args, **kwargs).decode()

    def redis_keys(self, pattern):
        keys = set()
        for n in range(3 if self.mode == "cluster" else 1):
            keys.update(self.redis("--scan", "--pattern", pattern, pod=f"redis-{n}").splitlines())
        return sorted(keys)

    def logs(self):
        return self.kubectl(
            "logs",
            "-l",
            "app.kubernetes.io/instance=gateway",
            "--all-containers=true",
            "--prefix",
            "--tail=-1",
        )

    def collect_logs(self):
        for service in ("ov", "ark"):
            try:
                response = self.mock(service, "GET", "/_records")
                (self.directory / f"mock-{service}.json").write_text(response.text)
            except (AssertionError, httpx.HTTPError):
                pass
        for name, args in {
            "pods": ("get", "pods", "-o", "wide"),
            "events": ("get", "events"),
            "gateway": (
                "logs",
                "-l",
                "app.kubernetes.io/instance=gateway",
                "--prefix",
                "--tail=-1",
            ),
            "redis": ("logs", "statefulset/redis", "--tail=100"),
        }.items():
            try:
                (self.directory / f"{name}.log").write_text(
                    self.kubectl(*args, check=False, timeout=20)
                )
            except subprocess.TimeoutExpired:
                pass

    def close(self):
        for process, _, log in self.forwards.values():
            process.terminate()
            process.wait(timeout=10)
            log.close()
        self.kubectl("delete", "namespace", self.namespace, "--wait=false", check=False, timeout=30)


CHAT = "/gateway/v1/chat/completions"
MODELS = "/gateway/v1/models"
ADMIN = "/gateway/admin"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def account(key):
    return "k_" + digest("openviking\0" + key)


def scope(key="key-a", protocol="chat"):
    return digest(account(key) + "\0default\0" + protocol)


def records(harness, service="ov"):
    response = harness.mock(service, "GET", "/_records")
    response.raise_for_status()
    return response.json()


def control(harness, path, **body):
    response = harness.mock("ov", "POST", path, json=body)
    response.raise_for_status()


def chat(harness, pod, session, messages, key="key-a", model="mock", **policy):
    headers = {"X-OpenViking-Session": session}
    if policy:
        headers["X-OpenViking-Context"] = json.dumps(policy)
    response = harness.request(
        pod,
        "POST",
        CHAT,
        key=key,
        headers=headers,
        json={"model": model, "messages": messages, "stream": False},
    )
    assert response.status_code == 200, response.text
    reply = response.json()["choices"][0]["message"]
    assert reply.get("content"), response.text
    return {"role": "assistant", "content": reply["content"]}


def document(harness, redis_key):
    value = harness.redis("HGET", redis_key, "v").strip()
    return json.loads(value) if value else {}


def credential_keys(harness, key):
    identifier = digest("openviking\0" + key)
    return harness.redis_keys(f"*m:*:o:keys:{digest(identifier)[:32]}")


def session_keys(harness, session, key="key-a", protocol="chat"):
    suffix = digest(digest(session))[:32]
    tenant = scope(key, protocol)
    replay = harness.redis_keys(f"*k:{{{tenant}}}:rp:{suffix}")
    capture = harness.redis_keys(f"*k:{{cap}}:{tenant}:{suffix}")
    assert len(replay) == len(capture) == 1, (replay, capture)
    assert "root:" in harness.redis("HKEYS", replay[0]).splitlines()
    return replay[0], capture[0]


def written_messages(snapshot, library="library-a"):
    return [
        message
        for session in snapshot["libraries"][library]["sessions"].values()
        for message in session["messages"]
    ]


def message_text(message):
    return "".join(part.get("text", "") for part in message["parts"] if part["type"] == "text")
