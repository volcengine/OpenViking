"""Profile-scoped Quick Local controls, loaded without the memory provider."""

from pathlib import Path

from hermes_constants import get_hermes_home


def _run(args):
    from .local_server import LocalServer
    from .quick_local import QuickLocalSetupError, _server_accepts_config

    server = LocalServer(Path(get_hermes_home()))
    if not server.paths.server_config.is_file():
        print("Quick Local is not configured for this profile.")
        return
    try:
        if args.local_action == "stop":
            print("Quick Local stopped." if server.stop() else "Quick Local is already stopped.")
            return
        if args.local_action in {"start", "restart"}:
            from hermes_cli.config import load_config_readonly

            from .quick_local import DEPLOYMENT

            memory = load_config_readonly().get("memory", {})
            settings = memory.get("openviking", {}) if isinstance(memory, dict) else {}
            if (
                not isinstance(memory, dict)
                or memory.get("provider") != "openviking"
                or not isinstance(settings, dict)
                or settings.get("deployment") != DEPLOYMENT
            ):
                raise QuickLocalSetupError(
                    "This profile is not using Quick Local. Run "
                    "hermes memory setup openviking to select Quick Local."
                )
            started = server.start(restart=args.local_action == "restart")
            print(f"Starting Quick Local at {started.endpoint}...")
            server.wait_ready(
                started, lambda url: (_server_accepts_config(server._config(), url), "")
            )
        status = server.status()
        print(f"Quick Local: {status['state']} at {status['endpoint']}")
        if status["restart_required"]:
            print("Saved settings require a restart: hermes openviking local restart")
    except QuickLocalSetupError as exc:
        print(str(exc))
        raise SystemExit(1) from exc


def register_cli(subparser):
    commands = subparser.add_subparsers(dest="openviking_command", required=True)
    from .mcp_tools import run as run_mcp

    commands.add_parser("mcp", help="Serve this profile's OpenViking MCP tools over stdio").set_defaults(func=run_mcp)
    local = commands.add_parser("local", help="Control this profile's Quick Local server")
    actions = local.add_subparsers(dest="local_action", required=True)
    for name in ("status", "start", "stop", "restart"):
        actions.add_parser(name).set_defaults(func=_run)
