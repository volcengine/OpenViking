"""Launch the installed OpenSandbox Server with loopback-only Docker port bindings.

OpenSandbox Server 0.1.6 hardcodes 0.0.0.0 for workload and egress port mappings;
its docker.host_ip setting only controls advertised URLs. Apply this restriction
at the Docker SDK boundary in this owned subprocess, never in an external server.
"""

from functools import wraps
from importlib.metadata import distribution


def restrict_published_ports(api_client_class) -> None:
    original = api_client_class.create_host_config

    @wraps(original)
    def create_host_config(self, *args, **kwargs):
        config = original(self, *args, **kwargs)
        for bindings in config.get("PortBindings", {}).values():
            for binding in bindings or []:
                binding["HostIp"] = "127.0.0.1"
        return config

    api_client_class.create_host_config = create_host_config


def main() -> None:
    import docker

    restrict_published_ports(docker.APIClient)
    entrypoint = next(
        entry
        for entry in distribution("opensandbox-server").entry_points
        if entry.group == "console_scripts" and entry.name == "opensandbox-server"
    )
    entrypoint.load()()


if __name__ == "__main__":
    main()
