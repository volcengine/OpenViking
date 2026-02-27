#!/usr/bin/env python3
"""
创建租户和用户，获取 API Key

前置条件:
    1. 按照 GUIDE.md 完成云服务开通和配置
    2. 启动 OpenViking Server:
         export OPENVIKING_CONFIG_FILE=examples/cloud/ov.conf
         openviking-server

获取用户 API Key 的流程:
    1. 在 ov.conf 中设置 server.root_api_key（管理员密钥）
    2. 用 root_api_key 调用 POST /api/v1/admin/accounts 创建租户，返回管理员用户的 API Key
    3. 用管理员 API Key 调用 POST /api/v1/admin/accounts/{id}/users 注册用户，返回用户的 API Key
    4. 每个用户拿到自己的 API Key 后即可独立使用所有数据接口

本脚本自动完成上述流程，创建一个租户 "demo-team"，注册 alice 和 bob 两个用户。

运行:
    uv run setup_users.py
    uv run setup_users.py --url http://localhost:1933 --root-key test
"""

import argparse
import json
import sys

import httpx


def main():
    parser = argparse.ArgumentParser(description="创建租户和用户")
    parser.add_argument("--url", default="http://localhost:1933", help="Server URL")
    parser.add_argument("--root-key", default="test", help="ov.conf 中的 root_api_key")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    headers = {"X-API-Key": args.root_key, "Content-Type": "application/json"}

    # 健康检查
    resp = httpx.get(f"{base}/health")
    if not resp.is_success:
        print(f"Server 不可用: {resp.status_code}")
        sys.exit(1)
    print(f"Server 正常: {resp.json()}")

    # 创建租户，alice 作为管理员
    print("\n== 创建租户 demo-team ==")
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts",
        headers=headers,
        json={"account_id": "demo-team", "admin_user_id": "alice"},
    )
    if not resp.is_success:
        print(f"创建失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    result = resp.json()["result"]
    alice_key = result["user_key"]
    print("  租户: demo-team")
    print("  管理员: alice (admin)")
    print(f"  Alice API Key: {alice_key}")

    # alice 注册 bob
    print("\n== 注册用户 bob ==")
    alice_headers = {"X-API-Key": alice_key, "Content-Type": "application/json"}
    resp = httpx.post(
        f"{base}/api/v1/admin/accounts/demo-team/users",
        headers=alice_headers,
        json={"user_id": "bob", "role": "user"},
    )
    if not resp.is_success:
        print(f"注册失败: {resp.status_code} {resp.text}")
        sys.exit(1)
    result = resp.json()["result"]
    bob_key = result["user_key"]
    print("  用户: bob (user)")
    print(f"  Bob API Key: {bob_key}")

    # 输出汇总
    keys = {
        "url": args.url,
        "account_id": "demo-team",
        "alice_key": alice_key,
        "bob_key": bob_key,
    }
    print("\n== 汇总 ==")
    print(json.dumps(keys, indent=2))

    # 写入文件供后续脚本使用
    keys_file = "examples/cloud/user_keys.json"
    with open(keys_file, "w") as f:
        json.dump(keys, f, indent=2)
    print(f"\n已写入 {keys_file}，后续脚本可直接读取。")


if __name__ == "__main__":
    main()
