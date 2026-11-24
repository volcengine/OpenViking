"""Select whole CodeQL language databases from changed paths."""

import argparse
import json
import re
import subprocess

LANGUAGES = ["python", "cpp"]
DOCS = re.compile(
    r"^(docs/|bot/docs/)|\.(md|mdx|rst|png|jpg|jpeg|gif|svg)$"
    r"|^(LICENSE|\.gitignore|\.editorconfig)$",
    re.IGNORECASE,
)
SHARED = re.compile(
    r"^\.github/(workflows/|actions/|scripts/codeql_plan\.py$)"
    r"|^(pyproject\.toml|uv\.lock|setup\.py|setup\.cfg|MANIFEST\.in|"
    r"Cargo\.(toml|lock)|\.gitmodules|\.python-version)$"
    r"|(^|/)requirements[^/]*\.txt$"
    r"|^scripts/build_support/"
    r"|(^|/)CMakeLists\.txt$|\.cmake$"
)
CPP = re.compile(
    r"\.(c|cc|cpp|cxx|c\+\+|h|hh|hpp|hxx|h\+\+|inc|inl|ipp|tpp|cu|cuh)(\.in)?$", re.IGNORECASE
)


def plan(paths, full=False):
    if full:
        return LANGUAGES.copy()
    selected = set()
    for path in paths:
        if DOCS.search(path):
            continue
        if SHARED.search(path):
            selected.update(LANGUAGES)
        elif path.endswith((".py", ".pyi")):
            selected.add("python")
        elif CPP.search(path) or path.startswith(("src/", "third_party/")):
            # Native build inputs and generated source templates also matter.
            selected.add("cpp")
    return [language for language in LANGUAGES if language in selected]


def select(event, base, head):
    if event not in ("pull_request", "push") or not base or not base.strip("0"):
        return plan([], full=True)
    # PRs compare from the merge base; pushes must include the entire push,
    # including removed files and rewritten history. Renames include both paths.
    separator = "..." if event == "pull_request" else ".."
    paths = (
        subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}{separator}{head}"]
        )
        .decode()
        .split("\0")
    )
    return plan(paths)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    languages = json.dumps(select(args.event, args.base, args.head), separators=(",", ":"))
    print(f"CodeQL languages: {languages}")
    with open(args.output, "a", encoding="utf-8") as output:
        output.write(f"languages={languages}\n")


if __name__ == "__main__":
    main()
