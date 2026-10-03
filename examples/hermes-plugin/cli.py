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
    local = commands.add_parser("local", help="Control this profile's Quick Local server")
    actions = local.add_subparsers(dest="local_action", required=True)
    for name in ("status", "start", "stop", "restart"):
        actions.add_parser(name).set_defaults(func=_run)
