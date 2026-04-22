#!/usr/bin/env python3

import json
import os
import sys
from pathlib import Path

from openviking_cli.utils.config.config_loader import resolve_config_path
from openviking_cli.utils.config.consts import (
    DEFAULT_OVCLI_CONF,
    DEFAULT_OV_CONF,
    OPENVIKING_CLI_CONFIG_ENV,
    OPENVIKING_CONFIG_ENV,
)

_USE_COLOR = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _color(text: str, code: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _prefix() -> str:
    return _color("[preflight]", "36")


def _log_info(message: str) -> None:
    print(f"{_prefix()} {_color('[INFO]', '34')} {message}")


def _log_warn(message: str) -> None:
    print(f"{_prefix()} {_color('[WARN]', '33')} {message}")


def _log_ok(message: str) -> None:
    print(f"{_prefix()} {_color('[OK]', '32')} {message}")


def _log_error(message: str) -> None:
    print(f"{_prefix()} {_color('[ERROR]', '31')} {message}", file=sys.stderr)


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"{_prefix()} {prompt}{suffix}: ").strip()
    if not raw and default is not None:
        return default
    return raw


def _prompt_confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{_prefix()} {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    return json.loads(raw)


def _backup_and_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        with open(path, "r", encoding="utf-8") as src:
            original = src.read()
        with open(backup, "w", encoding="utf-8") as bak:
            bak.write(original)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _resolve_ov_conf_path() -> Path:
    configured_path = os.environ.get(OPENVIKING_CONFIG_ENV, "").strip()
    if configured_path:
        return Path(configured_path).expanduser()

    resolved = resolve_config_path(None, OPENVIKING_CONFIG_ENV, DEFAULT_OV_CONF)
    default_path = str(resolved) if resolved is not None else str(Path.home() / ".openviking" / "ov.conf")

    if _is_interactive():
        _log_info(f"OpenViking 配置默认路径: {default_path}")
        chosen = _prompt_text("直接回车使用默认，或输入新路径", default=default_path)
    else:
        chosen = default_path
    return Path(chosen).expanduser()


def _ensure_server_root_api_key(ov_data: dict) -> tuple[bool, bool]:
    server = ov_data.setdefault("server", {})
    root_key = str(server.get("root_api_key") or "").strip()
    if root_key:
        return True, False

    _log_warn("server.root_api_key 未配置。")
    if not _is_interactive():
        _log_error("非交互模式下无法录入 root_api_key，请先配置后重试。")
        return False, False

    while True:
        key = _prompt_text("请输入 OpenViking root_api_key")
        if key:
            server["root_api_key"] = key
            return True, True
        _log_warn("root_api_key 不能为空，请重新输入。")


def _check_bot_root_key(ov_data: dict) -> tuple[bool, bool]:
    server_root_key = str(ov_data.get("server", {}).get("root_api_key") or "").strip()
    bot = ov_data.setdefault("bot", {})
    ov_server = bot.setdefault("ov_server", {})
    bot_root_key = str(ov_server.get("root_api_key") or "").strip()

    if not bot_root_key or bot_root_key == server_root_key:
        return True, False

    _log_warn("bot.ov_server.root_api_key 与 server.root_api_key 不一致。")
    _log_warn("vikingbot 必须使用 OpenViking 的 root 级 API key。")

    if not _is_interactive():
        _log_error("非交互模式下不会自动修改，请先修复配置后重试。")
        return False, False

    if _prompt_confirm("是否自动调整为 server.root_api_key", default=True):
        ov_server["root_api_key"] = server_root_key
        return True, True

    _log_error("已取消自动调整。")
    return False, False


def _resolve_ovcli_path() -> Path:
    resolved = resolve_config_path(None, OPENVIKING_CLI_CONFIG_ENV, DEFAULT_OVCLI_CONF)
    if resolved is not None:
        return resolved
    return Path.home() / ".openviking" / "ovcli.conf"


def _set_bot_account_id(ov_data: dict, account_id: str) -> bool:
    bot = ov_data.setdefault("bot", {})
    ov_server = bot.setdefault("ov_server", {})
    current = str(ov_server.get("account_id") or "").strip()
    if current == account_id:
        return False
    ov_server["account_id"] = account_id
    return True


def _check_ovcli_keys_and_account(server_root_key: str, ov_data: dict) -> tuple[bool, bool, str]:
    ovcli_path = _resolve_ovcli_path()

    changed_ovcli = False
    changed_ov = False

    if not ovcli_path.exists():
        _log_warn(f"未找到 ovcli.conf: {ovcli_path}")
        if not _is_interactive():
            _log_error("非交互模式下不会创建 ovcli.conf。")
            return False, False, "default"
        if not _prompt_confirm("是否创建并写入 root key", default=True):
            _log_error("已取消创建 ovcli.conf。")
            return False, False, "default"
        ovcli_data = {
            "url": "http://localhost:1933",
            "api_key": server_root_key,
            "root_api_key": server_root_key,
            "account": "",
            "timeout": 60.0,
        }
        changed_ovcli = True
    else:
        try:
            ovcli_data = _load_json(ovcli_path)
        except Exception as exc:
            _log_error(f"读取 ovcli.conf 失败: {exc}")
            return False, False, "default"

    needs_key_update = False
    api_key = str(ovcli_data.get("api_key") or "").strip()
    if api_key != server_root_key:
        needs_key_update = True

    has_root_key_field = "root_api_key" in ovcli_data
    root_api_key = str(ovcli_data.get("root_api_key") or "").strip() if has_root_key_field else ""
    if has_root_key_field and root_api_key != server_root_key:
        needs_key_update = True

    if needs_key_update:
        _log_warn("ovcli.conf 的 key 与 server.root_api_key 不一致。")
        if not _is_interactive():
            _log_error("非交互模式下不会自动修改 ovcli.conf。")
            return False, False, "default"
        if not _prompt_confirm("是否自动更改为 server.root_api_key", default=True):
            _log_error("已取消自动更改 ovcli.conf。")
            return False, False, "default"
        ovcli_data["api_key"] = server_root_key
        if has_root_key_field:
            ovcli_data["root_api_key"] = server_root_key
        changed_ovcli = True
        _log_ok("ovcli.conf key 已同步为 server.root_api_key")

    selected_account = str(ovcli_data.get("account") or "").strip()
    if selected_account:
        if _is_interactive():
            use_current = _prompt_confirm(
                f"检测到 ovcli.conf.account={selected_account}，是否使用该 account",
                default=True,
            )
            if not use_current:
                candidate = _prompt_text("请输入新的 account", default=selected_account).strip()
                if candidate:
                    selected_account = candidate
                ovcli_data["account"] = selected_account
                changed_ovcli = True
                _log_ok(f"ovcli.conf account 已更新为: {selected_account}")
        _log_info(f"导入与评测将使用 account: {selected_account}")
    else:
        _log_warn("ovcli.conf 的 account 为空。")
        if _is_interactive():
            selected_account = _prompt_text(
                "将使用默认 Account=default；回车确认，或输入新 account",
                default="default",
            ).strip()
            if not selected_account:
                selected_account = "default"
        else:
            selected_account = "default"
            _log_info("将使用默认 Account=default 导入 OpenViking 数据和 Vikingbot 评测。")

        if selected_account != "default":
            ovcli_data["account"] = selected_account
            changed_ovcli = True
            _log_ok(f"ovcli.conf account 已更新为: {selected_account}")

    if _set_bot_account_id(ov_data, selected_account):
        changed_ov = True
        _log_ok(f"bot.ov_server.account_id 已同步为: {selected_account}")

    if changed_ovcli:
        _backup_and_write_json(ovcli_path, ovcli_data)
        _log_ok(f"已更新 {ovcli_path}")

    return True, changed_ov, selected_account


def main() -> int:
    try:
        ov_conf_path = _resolve_ov_conf_path()
        if not ov_conf_path.exists():
            _log_error(f"ov.conf 不存在: {ov_conf_path}")
            return 1

        try:
            ov_data = _load_json(ov_conf_path)
        except Exception as exc:
            _log_error(f"读取 ov.conf 失败: {exc}")
            return 1

        ok, changed_server = _ensure_server_root_api_key(ov_data)
        if not ok:
            return 1

        ok, changed_bot = _check_bot_root_key(ov_data)
        if not ok:
            return 1

        if changed_server or changed_bot:
            _backup_and_write_json(ov_conf_path, ov_data)
            _log_ok(f"已更新 {ov_conf_path}")

        if changed_server:
            _log_warn("检测到 server.root_api_key 刚完成配置，请先重启 openviking-server。")
            _log_info("重启完成后请重新执行一键评测脚本。")
            return 2

        server_root_key = str(ov_data.get("server", {}).get("root_api_key") or "").strip()
        ok, changed_ov_account, _ = _check_ovcli_keys_and_account(server_root_key, ov_data)
        if not ok:
            return 1

        if changed_ov_account:
            _backup_and_write_json(ov_conf_path, ov_data)
            _log_ok(f"已更新 {ov_conf_path}")

        _log_ok("配置检查通过。")
        return 0
    except KeyboardInterrupt:
        _log_error("用户取消。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
