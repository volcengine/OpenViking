#!/usr/bin/env python3
"""
Alice — 技术负责人的使用流程

操作：添加项目文档 → 语义搜索 → 多轮对话 → 沉淀记忆 → 回顾记忆

获取 API Key:
    API Key 由管理员通过 Admin API 分配，流程如下：

    1. ov.conf 中配置 server.root_api_key（如 "test"）
    2. 用 root_api_key 创建租户和管理员:
         curl -X POST http://localhost:1933/api/v1/admin/accounts \
           -H "X-API-Key: test" -H "Content-Type: application/json" \
           -d '{"account_id": "demo-team", "admin_user_id": "alice"}'
       返回中的 user_key 就是 Alice 的 API Key
    3. 或者运行 setup_users.py 自动完成上述步骤，Key 写入 user_keys.json

运行:
    uv run examples/cloud/alice.py
    uv run examples/cloud/alice.py --url http://localhost:1933 --api-key <alice_key>
"""

import argparse
import json
import sys
import time

import openviking as ov
from openviking_cli.utils.async_utils import run_async


def load_key_from_file(user="alice"):
    try:
        with open("examples/cloud/user_keys.json") as f:
            keys = json.load(f)
        return keys["url"], keys[f"{user}_key"]
    except (FileNotFoundError, KeyError):
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Alice 的使用流程")
    parser.add_argument("--url", default=None, help="Server URL")
    parser.add_argument("--api-key", default=None, help="Alice 的 API Key")
    args = parser.parse_args()

    url, api_key = args.url, args.api_key
    if not api_key:
        url_from_file, key_from_file = load_key_from_file("alice")
        url = url or url_from_file or "http://localhost:1933"
        api_key = key_from_file
    if not url:
        url = "http://localhost:1933"
    if not api_key:
        print("请通过 --api-key 指定 API Key，或先运行 setup_users.py")
        sys.exit(1)

    print(f"Server: {url}")
    print("User:   alice")
    print(f"Key:    {api_key[:16]}...")

    client = ov.SyncHTTPClient(url=url, api_key=api_key, agent_id="alice-agent")
    client.initialize()

    try:
        # ── 1. 添加资源 ──
        print("\n== 1. 添加资源: OpenViking README ==")
        result = client.add_resource(
            path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
            reason="项目核心文档",
        )
        readme_uri = result.get("root_uri", "")
        print(f"  URI: {readme_uri}")
        print("  等待处理...")
        client.wait_processed()
        print("  完成")

        # ── 2. 查看文件系统 ──
        print("\n== 2. 文件系统 ==")
        entries = client.ls("viking://")
        for entry in entries:
            if isinstance(entry, dict):
                kind = "dir " if entry.get("isDir") else "file"
                print(f"  [{kind}] {entry.get('name', '?')}")

        # ── 3. 读取摘要 ──
        if readme_uri:
            print("\n== 3. 资源摘要 ==")
            abstract = client.abstract(readme_uri)
            print(f"  {abstract[:300]}")

        # ── 4. 语义搜索 ──
        print("\n== 4. 语义搜索: 'context database for AI agent' ==")
        results = client.find("context database for AI agent", limit=3)
        if hasattr(results, "resources") and results.resources:
            for i, r in enumerate(results.resources, 1):
                print(f"  {i}. [{r.score:.3f}] {r.uri}")

        # ── 5. 创建会话，模拟技术讨论 ──
        print("\n== 5. 对话: 技术架构讨论 ==")
        session = client.session()
        print(f"  Session: {session.session_id}")

        messages = [
            ("user", "我们的项目选择用 OpenViking 做 Context Database，主要原因是什么？"),
            (
                "assistant",
                "选择 OpenViking 的核心原因：1) 文件系统范式统一管理上下文 "
                "2) 分层加载（L0/L1/L2）节省 token 3) 目录递归检索比传统 RAG 效果好 "
                "4) 内置 session 管理和 memory 自动沉淀。",
            ),
            ("user", "向量数据库后端我们决定用 VikingDB 还是本地？"),
            (
                "assistant",
                "生产环境决定使用 VikingDB 云服务。原因：1) 不需要自己运维向量库 "
                "2) VikingDB 和方舟模型在同一可用区，网络延迟低 "
                "3) 自动扩缩容，不用担心数据量增长。本地模式只用于开发测试。",
            ),
            ("user", "文件存储用 TOS 对吧？prefix 是 ov？"),
            (
                "assistant",
                "是的。AGFS 后端配置为 S3 模式，对接 TOS。"
                "bucket 是 openvikingdata，prefix 设为 ov，所有文件存在 ov/ 目录下。"
                "AK/SK 使用 IAM 子用户的密钥，权限范围限定在这个 bucket。",
            ),
        ]
        for role, content in messages:
            run_async(session.add_message(role=role, content=content))
        print(f"  添加了 {len(messages)} 条消息")

        # ── 6. 沉淀记忆 ──
        print("\n== 6. 沉淀记忆: commit session ==")
        print("  正在提取（技术决策、架构选型等）...")
        client.commit_session(session.session_id)
        print("  commit 完成")
        time.sleep(2)
        client.wait_processed()
        print("  记忆向量化完成")

        # ── 7. 查看记忆目录 ──
        print("\n== 7. 记忆目录 ==")
        try:
            mem_entries = client.ls("viking://user/alice/memories")
            for entry in mem_entries:
                if isinstance(entry, dict):
                    kind = "dir " if entry.get("isDir") else "file"
                    print(f"  [{kind}] {entry.get('name', '?')}")
        except Exception:
            print("  记忆目录为空（可能无可提取的记忆）")

        # ── 8. 搜索回顾记忆 ──
        print("\n== 8. 回顾记忆: '为什么选择 VikingDB' ==")
        results = client.find("为什么选择 VikingDB 作为向量数据库", limit=3)
        if hasattr(results, "memories") and results.memories:
            print("  记忆:")
            for i, m in enumerate(results.memories, 1):
                desc = m.abstract or m.overview or str(m.uri)
                print(f"  {i}. [{m.score:.3f}] {desc[:150]}")
        if hasattr(results, "resources") and results.resources:
            print("  资源:")
            for i, r in enumerate(results.resources, 1):
                print(f"  {i}. [{r.score:.3f}] {r.uri}")

        print("\nAlice 流程完成")

    finally:
        client.close()


if __name__ == "__main__":
    main()
