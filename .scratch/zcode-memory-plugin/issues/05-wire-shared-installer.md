# 05 — Wire shared installer (install.sh)

**What to build:** Users can install the plugin via the documented one-liner: `bash install.sh --harness zcode`. The installer detects ZCode (`~/.zcode/cli/` or `zcode` binary), validates `zcode` as a harness, registers the plugin in ZCode's marketplace, and writes `ovcli.conf` credentials. Without this, the plugin exists but is uninstallable through the standard path.

**Blocked by:** 03 (hook dispatcher), 04 (URI guard)

**Status:** ready-for-agent

- [ ] `validate_selected_harnesses()` in `install.sh` accepts `zcode`
- [ ] `refresh_available_harnesses()` detects ZCode presence
- [ ] TUI includes a zcode checkbox option
- [ ] `install_zcode()` (or variant) registers the plugin via marketplace and configures credentials
- [ ] `bash install.sh --harness zcode --url http://127.0.0.1:1933 --api-key ''` completes without error on a machine with ZCode installed
