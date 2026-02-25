#!/usr/bin/env python3
"""
Bob — 新入职成员的使用流程

操作：浏览团队资源 → 回顾团队记忆 → 添加自己的资源 → 对话 → 沉淀记忆 → 带上下文搜索

获取 API Key:
    API Key 由租户管理员分配，流程如下：

    1. 管理员（如 Alice）用自己的 Key 注册 Bob:
         curl -X POST http://localhost:1933/api/v1/admin/accounts/demo-team/users \
           -H "X-API-Key: <alice_key>" -H "Content-Type: application/json" \
           -d '{"user_id": "bob", "role": "user"}'
       返回中的 user_key 就是 Bob 的 API Key
    2. 或者运行 setup_users.py 自动完成，Key 写入 user_keys.json

运行（建议在 alice.py 之后执行，这样可以看到 Alice 沉淀的团队记忆）:
    uv run examples/cloud/bob.py
    uv run examples/cloud/bob.py --url http://localhost:1933 --api-key <bob_key>
"""

import argparse
import json
import sys
import time

import openviking as ov
from openviking_cli.utils.async_utils import run_async


def load_key_from_file(user="bob"):
    try:
        with open("examples/cloud/user_keys.json") as f:
            keys = json.load(f)
        return keys["url"], keys[f"{user}_key"]
    except (FileNotFoundError, KeyError):
        return None, None


def main():
    parser = argparse.ArgumentParser(description="Bob 的使用流程")
    parser.add_argument("--url", default=None, help="Server URL")
    parser.add_argument("--api-key", default=None, help="Bob 的 API Key")
    args = parser.parse_args()

    url, api_key = args.url, args.api_key
    if not api_key:
        url_from_file, key_from_file = load_key_from_file("bob")
        url = url or url_from_file or "http://localhost:1933"
        api_key = key_from_file
    if not url:
        url = "http://localhost:1933"
    if not api_key:
        print("请通过 --api-key 指定 API Key，或先运行 setup_users.py")
        sys.exit(1)

    print(f"Server: {url}")
    print("User:   bob")
    print(f"Key:    {api_key[:16]}...")

    client = ov.SyncHTTPClient(url=url, api_key=api_key, agent_id="bob-agent")
    client.initialize()

    try:
        # ── 1. 浏览团队已有资源 ──
        print("\n== 1. 浏览团队资源 ==")
        entries = client.ls("viking://")
        if not entries:
            print("  （空，Alice 还没添加资源）")
        for entry in entries:
            if isinstance(entry, dict):
                kind = "dir " if entry.get("isDir") else "file"
                print(f"  [{kind}] {entry.get('name', '?')}")

        # ── 2. 回顾团队记忆（Alice 沉淀的技术决策） ──
        print("\n== 2. 回顾团队记忆: '项目技术选型' ==")
        results = client.find("项目用了什么技术栈和架构选型", limit=5)
        if hasattr(results, "memories") and results.memories:
            print("  团队记忆:")
            for i, m in enumerate(results.memories, 1):
                desc = m.abstract or m.overview or str(m.uri)
                print(f"  {i}. [{m.score:.3f}] {desc[:150]}")
        else:
            print("  未找到团队记忆（Alice 可能还没执行 commit）")
        if hasattr(results, "resources") and results.resources:
            print("  相关资源:")
            for i, r in enumerate(results.resources, 1):
                print(f"  {i}. [{r.score:.3f}] {r.uri}")

        # ── 3. 搜索具体决策 ──
        print("\n== 3. 搜索: '存储方案 TOS 配置' ==")
        results = client.find("文件存储方案 TOS bucket 配置", limit=3)
        if hasattr(results, "memories") and results.memories:
            for i, m in enumerate(results.memories, 1):
                desc = m.abstract or m.overview or str(m.uri)
                print(f"  {i}. [{m.score:.3f}] {desc[:150]}")
        else:
            print("  未找到相关记忆")

        # ── 4. 添加自己的资源 ──
        print("\n== 4. 添加资源: CONTRIBUTING.md ==")
        result = client.add_resource(
            path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/CONTRIBUTING.md",
            reason="贡献指南学习笔记",
        )
        bob_uri = result.get("root_uri", "")
        print(f"  URI: {bob_uri}")
        print("  等待处理...")
        client.wait_processed(timeout=120)
        print("  完成")

        # ── 5. 创建会话，模拟入职学习 ──
        print("\n== 5. 对话: 入职学习 ==")
        session = client.session()
        print(f"  Session: {session.session_id}")

        messages = [
            ("user", "我刚入职，需要了解 OpenViking 的贡献流程"),
            (
                "assistant",
                "欢迎！贡献流程主要是：1) Fork 仓库 2) 创建 feature branch "
                "3) 提交 PR 并通过 CI 4) Code Review 后合并。"
                "代码规范见 CONTRIBUTING.md。",
            ),
            ("user", "本地开发环境怎么搭建？"),
            (
                "assistant",
                "本地开发步骤：1) 安装 Python 3.10+ 和 uv "
                "2) git clone 后执行 uv sync 安装依赖 "
                "3) 复制 examples/ov.conf.example 为 ~/.openviking/ov.conf 填入 API Key "
                "4) 运行 openviking-server 启动开发服务。C++ 扩展需要 cmake 和 pybind11。",
            ),
            ("user", "测试怎么跑？"),
            (
                "assistant",
                "运行测试：1) uv run pytest 跑全量测试 "
                "2) uv run pytest tests/unit -x 只跑单元测试 "
                "3) CI 会自动跑 lint + test，PR 合并前必须全绿。",
            ),
        ]
        for role, content in messages:
            run_async(session.add_message(role=role, content=content))
        print(f"  添加了 {len(messages)} 条消息")

        # ── 6. 沉淀记忆 ──
        print("\n== 6. 沉淀记忆: commit session ==")
        print("  正在提取（开发流程、环境配置等）...")
        client.commit_session(session.session_id)
        print("  commit 完成")
        time.sleep(2)
        client.wait_processed(timeout=120)
        print("  记忆向量化完成")

        # ── 7. 回顾自己的记忆 ──
        print("\n== 7. 回顾记忆: '本地开发环境搭建' ==")
        results = client.find("本地开发环境搭建步骤", limit=3)
        if hasattr(results, "memories") and results.memories:
            print("  记忆:")
            for i, m in enumerate(results.memories, 1):
                desc = m.abstract or m.overview or str(m.uri)
                print(f"  {i}. [{m.score:.3f}] {desc[:150]}")
        if hasattr(results, "resources") and results.resources:
            print("  资源:")
            for i, r in enumerate(results.resources, 1):
                print(f"  {i}. [{r.score:.3f}] {r.uri}")

        # ── 8. 带会话上下文的搜索 ──
        print("\n== 8. 带上下文搜索: '还有什么注意事项' ==")
        results = client.search(
            "还有什么需要注意的事项",
            session_id=session.session_id,
            limit=3,
        )
        if hasattr(results, "resources") and results.resources:
            for i, r in enumerate(results.resources, 1):
                print(f"  {i}. [{r.score:.3f}] {r.uri}")
        if hasattr(results, "memories") and results.memories:
            for i, m in enumerate(results.memories, 1):
                desc = m.abstract or m.overview or str(m.uri)
                print(f"  {i}. [{m.score:.3f}] {desc[:100]}")

        print("\nBob 流程完成")

    finally:
        client.close()


if __name__ == "__main__":
    main()
