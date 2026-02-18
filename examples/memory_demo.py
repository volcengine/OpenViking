"""OpenViking memory dedup demo with REAL LLM decisions (no fake/mock).

This demo focuses on the user preference scenario:
1) "I like apples."                  -> expected create
2) "I like strawberries."            -> expected create
3) "I like Fuji apples."             -> expected merge/none with apple memory
4) "I do not like fruits anymore."   -> expected delete old positive fruit preferences
5) repeat negative preference         -> expected skip/none

The script prints commit and find results after each round so you can inspect
whether memory handling is reasonable.

Usage:
  export OPENVIKING_CONFIG_FILE=ov.conf
  python examples/memory_dedup_cases_demo.py
"""

from __future__ import annotations

import argparse
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from openviking.message.part import TextPart
from openviking.sync_client import SyncOpenViking


@dataclass
class RoundCase:
    title: str
    user_text: str
    expected: str


def _print_section(title: str, body: str = "") -> None:
    print("\n" + "=" * 80)
    print(title)
    if body:
        print("-" * 80)
        print(body)


def _safe_list(items: Iterable[Any]) -> list:
    try:
        return list(items)
    except Exception:
        return []


def _format_find_result(result: Any, max_items: int = 6) -> str:
    memories = _safe_list(getattr(result, "memories", []))
    if not memories:
        return "(no memory hit)"

    lines: List[str] = []
    for i, mem in enumerate(memories[:max_items], 1):
        score = getattr(mem, "score", None)
        score_s = "n/a" if score is None else f"{float(score):.4f}"
        abstract = getattr(mem, "abstract", "") or ""
        uri = getattr(mem, "uri", "")
        lines.append(f"{i}. score={score_s} | {abstract} | {uri}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenViking real-LLM dedup cases demo")
    parser.add_argument(
        "--path",
        default="./ov_data_dedup_cases_demo",
        help="Fixed demo storage path. This script clears it at startup.",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=60.0,
        help="Queue wait timeout in seconds.",
    )
    args = parser.parse_args()

    if not os.environ.get("OPENVIKING_CONFIG_FILE"):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        cfg = os.path.join(repo_root, "ov.conf")
        if os.path.exists(cfg):
            os.environ["OPENVIKING_CONFIG_FILE"] = cfg

    data_path = Path(args.path)
    if data_path.exists():
        shutil.rmtree(data_path)
    data_path.mkdir(parents=True, exist_ok=True)

    client = SyncOpenViking(path=str(data_path))
    client.initialize()

    try:
        client.is_healthy()
        sess_info = client.create_session()
        session_id = sess_info["session_id"]
        sess = client.session(session_id)

        rounds = [
            RoundCase(
                title="Round 1",
                user_text=(
                    "我是一名程序员。"
                    "我爱吃苹果。"
                    "我爱吃草莓。"
                    "我每天早上7点起床。"
                    "我通勤主要骑共享单车。"
                    "我习惯在周末整理书桌。"
                    "我最常用的云盘是Dropbox。"
                    "我对坚果过敏，尤其是腰果。"
                    "我最近在学西班牙语。"
                    "我喜欢在雨天听爵士乐。"
                    "我的常用笔记软件是Obsidian。"
                    "我每周三晚上会去游泳。"
                    "我偏好27英寸的外接显示器。"
                ),
                expected="Expected dedup: create multiple unrelated memories",
            ),
            RoundCase(
                title="Round 2",
                user_text="我爱吃红富士苹果。我是外卖员。",
                expected="Expected dedup: none+merge (细化苹果偏好)",
            ),
            RoundCase(
                title="Round 3",
                user_text="我不爱吃水果了，把之前关于喜欢水果的偏好作废。",
                expected="Expected dedup: delete old positive fruit preferences",
            ),
        ]

        queries = [
            "我喜欢吃什么？",
            "我是做什么工作的？",
        ]

        for r in rounds:
            sess.add_message("user", parts=[TextPart(text=r.user_text)])
            sess.add_message("assistant", parts=[TextPart(text="收到。")])
            commit_result = sess.commit()

            _print_section(
                f"{r.title} commit",
                body=(f"user={r.user_text}\n{r.expected}\ncommit={commit_result}"),
            )

            try:
                client.wait_processed(timeout=args.wait_timeout)
            except Exception:
                pass

            for q in queries:
                try:
                    result = client.find(q, target_uri="viking://user/memories", limit=8)
                    _print_section(f"{r.title} find: {q}", body=_format_find_result(result))
                except Exception as e:
                    _print_section(f"{r.title} find: {q} (failed)", body=str(e))

        _print_section(
            "Done",
            body=(
                "Check whether the later rounds are dominated by negative fruit preference.\n"
                "If old positive fruit preferences disappear or stop ranking high, delete likely worked."
            ),
        )
    finally:
        try:
            client.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
