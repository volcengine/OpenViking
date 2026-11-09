"""Select PR jobs from a merge-base diff; non-PR events retain full coverage."""

import argparse
import json
import re
import subprocess

FULL_OS = [
    "ubuntu-24.04",
    "ubuntu-24.04-arm",
    "macos-14",
    "macos-15-intel",
    "windows-latest",
]
CI = r"^\.github/(workflows/(pr|api_test|_build|_test_lite|_test_full)\.yml|actions/|scripts/(ci_plan|native_bundle|test_ci_helpers)\.py)"
PACKAGING = r"^(pyproject\.toml|uv\.lock|setup\.py|MANIFEST\.in|Cargo\.(toml|lock))$|^scripts/build_support/"
NATIVE = r"^(src/|third_party/|crates/(?!ov_cli/))"


def plan(paths, full=False):
    def changed(pattern):
        return full or any(re.search(pattern, path) for path in paths)

    cross_platform = changed(f"{NATIVE}|{PACKAGING}|{CI}")
    # CLI-only and Studio-only changes need a packaging smoke test, without
    # occupying all five wheel runners. Native/build configuration changes do.
    wheel = changed(f"{NATIVE}|{PACKAGING}|{CI}|^crates/ov_cli/|^web-studio/")
    plugins = changed(
        r"^(examples/[^/]*(plugin|extension)[^/]*/|examples/memory-plugin-shared/|"
        r"agent-plugins/|\.github/scripts/.*(plugin|marketplace|installer)|"
        r"\.github/workflows/pr\.yml)|"
        r"^(examples/)?(\.claude-plugin|\.agents/plugins)/marketplace\.json$"
    )
    # Keep unfamiliar paths on the safe side: only known independent trees
    # bypass API tests. Added/removed/renamed paths are all included in the diff.
    independent = re.compile(
        r"^(docs/|bot/docs/|sdk/|integrations/frameworks/|agent-plugins/|npm/|"
        r"examples/[^/]*(plugin|extension)[^/]*/|examples/memory-plugin-shared/)"
        r"|^(README(?:\.[^/]+)?|CONTRIBUTING|SECURITY|LICENSE)\.md$"
        r"|(\.png|\.jpg|\.jpeg|\.gif|\.svg)$"
    )
    api = full or any(not independent.search(path) for path in paths)
    return {
        "deps_changed": wheel,
        "wheel_os": FULL_OS if cross_platform else ["ubuntu-24.04"],
        "cuvs_changed": changed(
            f"{NATIVE}|{PACKAGING}|{CI}|"
            r"^openviking/storage/vectordb/|"
            r"^openviking/storage/vectordb_adapters/(factory|local_adapter)\.py$|"
            r"^openviking_cli/utils/config/vectordb_config\.py$|"
            r"^tests/(vectordb/test_cuvs_|benchmark/test_cuvs_|vectordb/test_str_to_uint64)|"
            r"^benchmark/cuvs/|^examples/cuvs_smoke\.py$"
        ),
        "api_changed": api,
        "plugins_changed": plugins,
        "langchain_changed": changed(
            r"^integrations/frameworks/langchain/|^openviking/integrations/|"
            r"^tests/unit/test_langchain_[^/]*\.py$|^tests/integration/langchain_langgraph/|"
            r"^examples/(langchain|langchain-langgraph|agent-framework)/|"
            r"^(pyproject\.toml|uv\.lock)$|"
            r"^\.github/workflows/(pr|python-langchain-release)\.yml$"
        ),
        "openclaw_changed": changed(
            r"^examples/openclaw-plugin/|^examples/memory-plugin-shared/(lib/|sync\.mjs)|"
            r"^\.github/workflows/pr\.yml$"
        ),
        "sdk_python_changed": changed(r"^sdk/python/|^\.github/workflows/pr\.yml$"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not args.full and not args.base:
        parser.error("--base is required unless --full is selected")
    paths = []
    if not args.full:
        paths = (
            subprocess.check_output(
                ["git", "diff", "--name-only", "--no-renames", "-z", f"{args.base}...HEAD"],
            )
            .decode()
            .split("\0")
        )
        paths = [path for path in paths if path]
    result = plan(paths, full=args.full)
    print(json.dumps(result, indent=2))
    with open(args.output, "a", encoding="utf-8") as output:
        for key, value in result.items():
            output.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")


if __name__ == "__main__":
    main()
