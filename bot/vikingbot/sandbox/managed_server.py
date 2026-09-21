"""Launch OpenSandbox with loopback ports and workspace-owner execution identities.

OpenSandbox Server 0.1.6 hardcodes 0.0.0.0 for workload and egress port mappings;
its docker.host_ip setting only controls advertised URLs. It also lacks a Docker
execution-user option. Apply these restrictions at the Docker SDK boundary in
this owned subprocess, never in an external server.
"""

from functools import wraps
from importlib.metadata import distribution

WORKSPACE_UID_LABEL = "vikingbot.io/workspace-uid"
WORKSPACE_GID_LABEL = "vikingbot.io/workspace-gid"


def use_workspace_owner(api_client_class) -> None:
    """Set Docker User before execd starts, so commands and file APIs share one UID.

    The trusted Bot supplies the dedicated workspace owner's numeric UID/GID. Do not
    change ownership or permissions of user files, and do not alter egress/cache
    containers, which have different runtime requirements.
    """
    original = api_client_class.create_container

    @wraps(original)
    def create_container(self, *args, **kwargs):
        labels = kwargs.get("labels") or {}
        if "opensandbox.io/id" in labels:
            parts = [labels.get(key, "") for key in (WORKSPACE_UID_LABEL, WORKSPACE_GID_LABEL)]
            if not all(
                isinstance(part, str) and part.isascii() and part.isdecimal() for part in parts
            ):
                raise ValueError("Managed sandbox requires a numeric workspace UID:GID")
            kwargs["user"] = ":".join(parts)
        return original(self, *args, **kwargs)

    api_client_class.create_container = create_container


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
    use_workspace_owner(docker.APIClient)
    entrypoint = next(
        entry
        for entry in distribution("opensandbox-server").entry_points
        if entry.group == "console_scripts" and entry.name == "opensandbox-server"
    )
    entrypoint.load()()


if __name__ == "__main__":
    main()
