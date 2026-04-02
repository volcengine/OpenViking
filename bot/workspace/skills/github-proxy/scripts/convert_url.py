#!/usr/bin/env python3
import sys
import re

DEFAULT_PROXY = "https://githubproxy.cc"
BACKUP_PROXY = "https://ghfast.top"

GITHUB_PATTERNS = [
    r"^https?://github\.com/.*",
    r"^https?://raw\.githubusercontent\.com/.*",
    r"^https?://gist\.github\.com/.*",
    r"^https?://gist\.githubusercontent\.com/.*",
]


def is_github_url(url: str) -> bool:
    for pattern in GITHUB_PATTERNS:
        if re.match(pattern, url):
            return True
    return False


def convert_url(url: str, proxy: str = DEFAULT_PROXY) -> str:
    if not url:
        return ""

    if url.startswith(proxy):
        return url

    if not is_github_url(url):
        print(f"警告: 链接 {url} 看起来不是 GitHub 链接", file=sys.stderr)
        return url

    return f"{proxy}/{url}"


def main():
    if len(sys.argv) < 2:
        print("使用方法: python convert_url.py <github_url> [--backup]")
        print("\n示例:")
        print("  python convert_url.py https://github.com/username/repo.git")
        print(
            "  python convert_url.py https://raw.githubusercontent.com/username/repo/main/file.txt"
        )
        print("  python convert_url.py https://github.com/username/repo.git --backup")
        sys.exit(1)

    url = sys.argv[1]
    use_backup = "--backup" in sys.argv or "-b" in sys.argv

    proxy = BACKUP_PROXY if use_backup else DEFAULT_PROXY

    result = convert_url(url, proxy)
    print(result)


if __name__ == "__main__":
    main()
