"""Render the standalone chart and validate its deployment and gateway configuration."""

import json
import subprocess
from pathlib import Path

import yaml

from openviking_gateway.config import StandaloneGatewayConfig

CHART = Path(__file__).resolve().parents[1] / "deploy/helm/openviking-gateway"


def render(values, *overrides):
    result = subprocess.run(
        ["helm", "template", "gateway", str(CHART), "-f", str(CHART / "ci" / values), *overrides],
        check=True,
        capture_output=True,
        text=True,
    )
    resources = [item for item in yaml.safe_load_all(result.stdout) if item]
    configmap = next(item for item in resources if item["kind"] == "ConfigMap")
    config = StandaloneGatewayConfig.model_validate(json.loads(configmap["data"]["gateway.conf"]))
    assert (config.host, config.port, config.storage_path) == (
        "0.0.0.0",
        1935,
        "/app/.openviking/gateway",
    )
    deployment = next(item for item in resources if item["kind"] == "Deployment")
    service = next(item for item in resources if item["kind"] == "Service")
    spec = deployment["spec"]
    pod = spec["template"]["spec"]
    assert service["spec"]["selector"] == spec["selector"]["matchLabels"]
    assert pod["terminationGracePeriodSeconds"] == 660
    for volume in pod["volumes"]:
        if volume["name"] == "root-key":
            assert volume["secret"]["defaultMode"] == 0o600
    assert pod["containers"][0]["lifecycle"]["preStop"]["exec"]["command"] == ["sleep", "15"]
    return resources, spec, config


def main():
    metadata = yaml.safe_load((CHART / "Chart.yaml").read_text())
    default = subprocess.run(
        ["helm", "template", "gateway", str(CHART)], capture_output=True, text=True
    )
    if metadata["appVersion"] == "0.0.0-unreleased":
        assert default.returncode != 0 and "image.tag is required" in default.stderr
    else:
        assert default.returncode == 0, default.stderr
        assert f":v{metadata['appVersion']}" in default.stdout
    for name in ("sqlite.yaml", "redis.yaml"):
        subprocess.run(["helm", "lint", str(CHART), "-f", str(CHART / "ci" / name)], check=True)
    resources, spec, _ = render("sqlite.yaml")
    assert any(item["kind"] == "PersistentVolumeClaim" for item in resources)
    assert spec["strategy"]["type"] == "Recreate"
    resources, spec, config = render("redis.yaml")
    assert not any(item["kind"] == "PersistentVolumeClaim" for item in resources)
    assert spec["replicas"] == 2
    assert spec["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }
    assert any(item["kind"] == "PodDisruptionBudget" for item in resources)
    env = {item["name"] for item in spec["template"]["spec"]["containers"][0]["env"]}
    assert config.storage.redis["password_env"] in env
    assert config.encryption["local"]["key_file"] == "/etc/openviking-gateway/keys/root-key"
    _, _, config = render("redis.yaml", "--set", "config.encryption.provider=volcengine_kms")
    assert (
        config.encryption["volcengine_kms"]["key_file"] == "/etc/openviking-gateway/keys/root-key"
    )
    resources, spec, _ = render("sqlite.yaml", "--set", "persistence.enabled=false")
    assert not any(item["kind"] == "PersistentVolumeClaim" for item in resources)
    assert {"name": "data", "emptyDir": {}} in spec["template"]["spec"]["volumes"]
    for overrides in (
        [],
        ["--set", "config.storage.kernel=redis"],
        ["--set", "config.storage.management=redis"],
    ):
        result = subprocess.run(
            [
                "helm",
                "template",
                "gateway",
                str(CHART),
                "-f",
                str(CHART / "ci/sqlite-invalid.yaml"),
                *overrides,
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and "SQLite storage requires replicaCount: 1" in result.stderr
    print("Gateway chart: deployment contracts and StandaloneGatewayConfig validation passed")


if __name__ == "__main__":
    main()
