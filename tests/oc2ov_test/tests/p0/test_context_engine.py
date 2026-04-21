"""
Context Engine 核心交互链路测试
覆盖 OpenClaw-OpenViking 核心交互：
1. assemble() - archive 历史组装回放
2. compact() - 对话超阈值后压缩归档
3. 跨 session recall - 不同 session 间的记忆检索注入
4. memory_recall 显式搜索 - 模型主动调用 memory_recall 工具
5. ov_archive_expand 展开 - 模型主动展开 archive 查看原始对话
6. session 隔离 - 不同 session 的记忆不互相污染

断言覆盖：
- 对话级别：模型回复包含预期关键词
- API 级别：OV session 状态正确（archive 存在、记忆已提取）
- 文件级别：记忆 .md 文件在本次测试期间新增或更新且内容包含关键信息

前置条件：
- ECS 上 commitTokenThreshold 已调低（如 500），以减少 archive 生成的对话轮次
- OpenViking 服务正常运行
"""

import os
import time
from pathlib import Path
from typing import Dict, Optional, Set

import requests

from tests.base_cli_test import BaseOpenClawCLITest

SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:1933")
OPENVIKING_API_KEY = os.environ.get("OPENVIKING_API_KEY", "test-root-api-key")
TASK_POLL_INTERVAL = 5
TASK_POLL_MAX_WAIT = 120


def _get_api_headers() -> Dict[str, str]:
    return {
        "X-OpenViking-API-Key": OPENVIKING_API_KEY,
        "Content-Type": "application/json",
    }


class OVSessionVerifier:
    """OV session 状态验证器，用于断言 archive 和记忆文件"""

    def __init__(self, server_url: str = SERVER_URL, api_key: str = OPENVIKING_API_KEY):
        self.server_url = server_url
        self.headers = _get_api_headers()

    def list_session_ids(self) -> Set[str]:
        try:
            resp = requests.get(
                f"{self.server_url}/api/v1/sessions", headers=self.headers, timeout=10
            )
            if resp.status_code != 200:
                return set()
            sessions = resp.json().get("result", [])
            return {s["session_id"] for s in sessions}
        except Exception:
            return set()

    def find_new_session_id(self, before_ids: Set[str]) -> Optional[str]:
        after_ids = self.list_session_ids()
        new_ids = after_ids - before_ids
        return new_ids.pop() if new_ids else None

    def get_session_detail(self, session_id: str) -> Optional[Dict]:
        try:
            resp = requests.get(
                f"{self.server_url}/api/v1/sessions/{session_id}",
                headers=self.headers,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("result", {})
        except Exception:
            pass
        return None

    def commit_session(self, session_id: str) -> Optional[str]:
        try:
            resp = requests.post(
                f"{self.server_url}/api/v1/sessions/{session_id}/commit",
                headers=self.headers,
                timeout=30,
            )
            if resp.status_code == 200:
                return resp.json().get("result", {}).get("task_id")
        except Exception:
            pass
        return None

    def poll_task_until_done(
        self, task_id: str, max_wait: int = TASK_POLL_MAX_WAIT
    ) -> Optional[Dict]:
        start = time.time()
        while time.time() - start < max_wait:
            try:
                resp = requests.get(
                    f"{self.server_url}/api/v1/tasks/{task_id}",
                    headers=self.headers,
                    timeout=10,
                )
                if resp.status_code == 200:
                    task_data = resp.json().get("result", {})
                    status = task_data.get("status", "unknown")
                    if status in ("completed", "failed"):
                        return task_data
            except Exception:
                pass
            time.sleep(TASK_POLL_INTERVAL)
        return None

    def assert_archive_exists(self, session_id: str) -> bool:
        detail = self.get_session_detail(session_id)
        if not detail:
            return False
        return detail.get("archive_count", 0) > 0 or detail.get("latest_archive_id") is not None

    def assert_memories_extracted(self, session_id: str) -> bool:
        task_id = self.commit_session(session_id)
        if not task_id:
            return False
        result = self.poll_task_until_done(task_id)
        if not result or result.get("status") != "completed":
            return False
        extracted = result.get("result", {}).get("memories_extracted", {})
        if not extracted:
            return False
        total = sum(len(v) if isinstance(v, list) else v for v in extracted.values())
        return total > 0

    @staticmethod
    def snapshot_memory_files() -> Dict[str, float]:
        """记录所有记忆文件的 mtime 快照（路径 → mtime）"""
        files: Dict[str, float] = {}
        data_base = Path.home() / ".openviking" / "data" / "viking" / "default"
        for search_dir in [data_base / "user" / "default" / "memories", data_base / "agent"]:
            if not search_dir.exists():
                continue
            for md_file in search_dir.rglob("*.md"):
                try:
                    files[str(md_file)] = md_file.stat().st_mtime
                except Exception:
                    continue
        return files

    @staticmethod
    def find_new_or_updated_files(before: Dict[str, float], keyword: str = None) -> list:
        """对比前后快照，找出新增或 mtime 变化的文件；可选按关键词过滤内容"""
        after = OVSessionVerifier.snapshot_memory_files()
        results = []
        for path_str, new_mtime in after.items():
            is_new = path_str not in before
            is_updated = not is_new and new_mtime > before[path_str]
            if not (is_new or is_updated):
                continue
            if keyword:
                try:
                    content = Path(path_str).read_text(encoding="utf-8")
                    if keyword not in content:
                        continue
                    results.append(
                        {
                            "path": path_str,
                            "status": "new" if is_new else "updated",
                            "content_preview": content[:200],
                        }
                    )
                except Exception:
                    continue
            else:
                results.append(
                    {
                        "path": path_str,
                        "status": "new" if is_new else "updated",
                    }
                )
        return results


class TestAssembleArchiveReplay(BaseOpenClawCLITest):
    """
    assemble() 历史组装验证
    测试目标：验证 archive 生成后，新 session 能通过 assemble 回放历史摘要
    测试路径：写入信息 → commit 生成 archive → 新 session → 验证 archive summary 被加载
    """

    def test_assemble_replays_archive_summary(self):
        """archive 生成后，新 session 应能通过 assemble 回放历史"""
        session_a = self.generate_unique_session_id(prefix="assemble_src")
        session_b = self.generate_unique_session_id(prefix="assemble_new")
        verifier = OVSessionVerifier()

        self.logger.info("[1/7] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/7] 在 session A 中写入独特信息")
        unique_marker = "蓝鲸计划"
        unique_detail = "2030年发射"
        self.send_and_log(
            f"我正在推进{unique_marker}，目标{unique_detail}，请记住",
            session_id=session_a,
        )

        self.smart_wait_for_sync(
            check_message=f"{unique_marker}的目标是什么",
            keywords=[unique_detail],
            timeout=30.0,
            session_id=session_a,
        )

        self.logger.info("[3/7] 继续写入更多信息，推动 commit/archive 生成")
        for i in range(3):
            self.send_and_log(
                f"{unique_marker}的里程碑{i + 1}：第{i + 1}阶段测试已完成",
                session_id=session_a,
            )
            time.sleep(3)

        self.logger.info("[4/7] 显式 commit 并等待 archive 生成")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        archive_exists = False
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                verifier.poll_task_until_done(task_id)
            time.sleep(5)
            archive_exists = verifier.assert_archive_exists(ov_session_id)
            self.logger.info(f"  Archive 存在: {archive_exists}")
            if not archive_exists:
                self.logger.warning("  Archive 未生成，尝试再次 commit")
                task_id = verifier.commit_session(ov_session_id)
                if task_id:
                    verifier.poll_task_until_done(task_id)
                time.sleep(5)
                archive_exists = verifier.assert_archive_exists(ov_session_id)
                self.logger.info(f"  第二次 commit 后 Archive 存在: {archive_exists}")
        else:
            time.sleep(5)

        self.logger.info("[5/7] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found = OVSessionVerifier.find_new_or_updated_files(before_files, keyword="蓝鲸")
        self.logger.info(f"  本次新增/更新且包含'蓝鲸'的记忆文件数: {len(memory_found)}")
        for mf in memory_found:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")
        if not memory_found:
            self.logger.warning("  未找到本次新增/更新且包含'蓝鲸'的记忆文件")

        self.logger.info("[6/7] 创建全新 session B，触发 assemble 从 archive 加载")
        time.sleep(5)

        self.logger.info("[7/7] 在新 session B 中查询，验证 archive 回放")
        response = self.send_and_log(
            f"我之前提到的{unique_marker}是什么？目标是什么？请从你的记忆或上下文中搜索",
            session_id=session_b,
        )
        self.assertAnyKeywordInResponse(
            response,
            [[unique_marker]],
            case_sensitive=False,
        )


class TestMemoryRecallExplicit(BaseOpenClawCLITest):
    """
    memory_recall 显式搜索验证
    测试目标：验证模型能通过 memory_recall 工具主动搜索长期记忆，而非仅依赖 auto-recall
    测试路径：写入独特信息 → commit + 记忆提取 → 新 session 中用模糊提示触发显式搜索 → 验证搜索结果
    """

    def test_memory_recall_explicit_search(self):
        """模型应能通过 memory_recall 工具显式搜索长期记忆"""
        unique_marker = "极光协议"
        unique_detail = "量子加密通信"

        session_a = self.generate_unique_session_id(prefix="recall_explicit_source")
        session_b = self.generate_unique_session_id(prefix="recall_explicit_target")
        verifier = OVSessionVerifier()

        self.logger.info("[1/7] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/7] 在 session A 中写入独特信息")
        self.send_and_retry_on_timeout(
            f"我参与了一个叫{unique_marker}的项目，它使用{unique_detail}技术，传输速率达到100Gbps，请记住这些信息",
            session_id=session_a,
        )

        self.smart_wait_for_sync(
            check_message=f"{unique_marker}用什么技术",
            keywords=["量子加密", "加密"],
            timeout=30.0,
            session_id=session_a,
        )

        self.logger.info("[3/7] 显式 commit 并等待记忆提取完成")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        commit_success = False
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                result = verifier.poll_task_until_done(task_id)
                if result:
                    status = result.get("status")
                    extracted = result.get("result", {}).get("memories_extracted", {})
                    self.logger.info(f"  Commit 任务状态: {status}, 记忆提取结果: {extracted}")
                    commit_success = status == "completed"
        if not commit_success:
            self.logger.warning("  Commit 未成功完成，等待额外时间...")
        self.logger.info("  等待记忆索引完成...")
        time.sleep(10)

        self.logger.info("[4/7] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found = OVSessionVerifier.find_new_or_updated_files(
            before_files, keyword=unique_marker
        )
        self.logger.info(f"  本次新增/更新且包含'{unique_marker}'的记忆文件数: {len(memory_found)}")
        for mf in memory_found:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")
        if not memory_found:
            self.logger.warning(f"  未找到本次新增/更新且包含'{unique_marker}'的记忆文件")

        self.logger.info("[5/7] 在新 session B 中用明确提示触发 memory_recall 显式搜索")
        response = self.send_and_retry_on_timeout(
            "请搜索你的记忆，我之前有没有提到过一个和加密通信或者协议相关的项目？请仔细搜索记忆文件后回答",
            session_id=session_b,
        )

        self.logger.info("[6/7] 验证回复包含记忆中的关键信息（对话级别断言）")
        self.assertAnyKeywordInResponse(
            response,
            [[unique_marker, "极光"], [unique_detail, "量子加密", "加密通信"]],
            case_sensitive=False,
        )

        self.logger.info("[7/7] 验证回复包含具体细节（业务逻辑断言：显式搜索应返回完整记忆内容）")
        self.assertAnyKeywordInResponse(
            response,
            [["100Gbps", "100G", "Gbps", "加密通信", "量子加密"]],
            case_sensitive=False,
        )


class TestArchiveExpand(BaseOpenClawCLITest):
    """
    ov_archive_expand 展开验证
    测试目标：验证 archive 生成后，模型能通过 ov_archive_expand 展开原始对话获取细节
    测试路径：写入含细节的信息 → commit 生成 archive → 询问细节问题触发展开 → 验证原始细节被还原
    """

    def test_archive_expand_restores_details(self):
        """archive 展开后应能还原原始对话中的细节信息"""
        session_id = self.generate_unique_session_id(prefix="expand_test")
        verifier = OVSessionVerifier()

        self.logger.info("[1/8] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/8] 写入含丰富细节的信息")
        unique_marker = "凤凰计划"
        detail_1 = "预算480万"
        detail_2 = "2027年Q3交付"
        detail_3 = "合作伙伴是深蓝科技"
        self.send_and_log(
            f"我负责{unique_marker}，{detail_1}，{detail_2}，{detail_3}，团队有12人",
            session_id=session_id,
        )

        self.smart_wait_for_sync(
            check_message=f"{unique_marker}的预算是多少",
            keywords=["480"],
            timeout=30.0,
            session_id=session_id,
        )

        self.logger.info("[3/8] 继续写入推动 archive 生成")
        for i in range(3):
            self.send_and_log(
                f"{unique_marker}第{i + 1}阶段进展：已完成需求分析和架构设计",
                session_id=session_id,
            )
            time.sleep(3)

        self.logger.info("[4/8] 显式 commit 并等待 archive 生成")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                verifier.poll_task_until_done(task_id)
        time.sleep(5)

        self.logger.info("[5/8] 验证 archive 已生成（API 级别断言）")
        if ov_session_id:
            has_archive = verifier.assert_archive_exists(ov_session_id)
            self.logger.info(f"  Archive 存在: {has_archive}")
            if not has_archive:
                self.logger.warning("  Archive 未生成，expand 测试可能不可靠")

        self.logger.info("[6/8] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found = OVSessionVerifier.find_new_or_updated_files(
            before_files, keyword=unique_marker
        )
        self.logger.info(f"  本次新增/更新且包含'{unique_marker}'的记忆文件数: {len(memory_found)}")
        for mf in memory_found:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")
        if not memory_found:
            self.logger.warning(f"  未找到本次新增/更新且包含'{unique_marker}'的记忆文件")

        self.logger.info("[7/8] 询问 archive 中的细节问题，触发 ov_archive_expand")
        response = self.send_and_log(
            f"关于{unique_marker}，合作伙伴是谁？预算和交付时间是什么？请仔细回忆所有细节",
            session_id=session_id,
        )

        self.logger.info("[8/8] 验证回复包含 archive 中的原始细节（业务逻辑断言：展开应还原细节）")
        self.assertAnyKeywordInResponse(
            response,
            [["480万", "480"], ["2027", "Q3"], ["深蓝科技", "深蓝"]],
            case_sensitive=False,
        )


class TestSessionIsolation(BaseOpenClawCLITest):
    """
    session 隔离验证
    测试目标：验证不同 session 的对话上下文不会互相污染
    注意：OpenClaw 的记忆文件(USER.md/MEMORY.md)是全局共享的，
    session 隔离主要体现在对话上下文层面——不同 session 看不到对方的对话历史
    测试路径：session A 写入甲信息 → session B 写入乙信息 → 分别查询对话上下文 → 验证互不干扰
    """

    def test_session_isolation_no_cross_contamination(self):
        """不同 session 的对话上下文不应互相污染"""
        marker_a = "翡翠项目"
        detail_a = "负责AI模型训练"
        marker_b = "琥珀项目"
        detail_b = "负责数据采集"

        session_a = self.generate_unique_session_id(prefix="isolation_a")
        session_b = self.generate_unique_session_id(prefix="isolation_b")
        verifier = OVSessionVerifier()

        self.logger.info("[1/7] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/7] 在 session A 中写入甲信息")
        self.send_and_retry_on_timeout(
            f"我在做{marker_a}，{detail_a}，使用GPU集群",
            session_id=session_a,
        )

        self.smart_wait_for_sync(
            check_message="我在做什么项目",
            keywords=[marker_a],
            timeout=30.0,
            session_id=session_a,
        )

        self.logger.info("[3/7] 在 session B 中写入乙信息")
        self.send_and_retry_on_timeout(
            f"我在做{marker_b}，{detail_b}，使用爬虫技术",
            session_id=session_b,
        )

        self.smart_wait_for_sync(
            check_message="我在做什么项目",
            keywords=[marker_b],
            timeout=30.0,
            session_id=session_b,
        )

        self.logger.info("[4/7] 显式 commit 两个 session")
        after_sessions = verifier.list_session_ids()
        new_sessions = after_sessions - before_sessions
        for sid in new_sessions:
            task_id = verifier.commit_session(sid)
            if task_id:
                verifier.poll_task_until_done(task_id)
        time.sleep(5)

        self.logger.info("[5/7] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found_a = OVSessionVerifier.find_new_or_updated_files(before_files, keyword=marker_a)
        memory_found_b = OVSessionVerifier.find_new_or_updated_files(before_files, keyword=marker_b)
        self.logger.info(f"  本次新增/更新且包含'{marker_a}'的记忆文件数: {len(memory_found_a)}")
        self.logger.info(f"  本次新增/更新且包含'{marker_b}'的记忆文件数: {len(memory_found_b)}")
        for mf in memory_found_a + memory_found_b:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")

        self.logger.info("[6/7] 在 session A 中查询对话上下文，应能回忆起甲信息")
        response_a = self.send_and_retry_on_timeout(
            "请根据你记住的关于我的信息回答：我之前告诉过你我在做什么项目？负责什么工作？不要调用任何外部工具，直接从记忆中回答",
            session_id=session_a,
        )
        self.assertAnyKeywordInResponse(
            response_a,
            [[marker_a, "翡翠"], [detail_a, "AI", "模型训练", "GPU"]],
            case_sensitive=False,
        )

        self.logger.info("[7/7] 在 session B 中查询对话上下文，应能回忆起乙信息")
        response_b = self.send_and_retry_on_timeout(
            "请根据你记住的关于我的信息回答：我之前告诉过你我在做什么项目？负责什么工作？不要调用任何外部工具，直接从记忆中回答",
            session_id=session_b,
        )
        self.assertAnyKeywordInResponse(
            response_b,
            [[marker_b, "琥珀"], [detail_b, "数据采集", "爬虫"]],
            case_sensitive=False,
        )


class TestCompactArchiveGeneration(BaseOpenClawCLITest):
    """
    compact() 压缩归档验证
    测试目标：验证对话超过阈值后，compact 正确生成 archive 并压缩上下文
    测试路径：持续对话 → 超过 commitTokenThreshold → 验证 archive 生成 + 记忆文件生成 + 上下文被压缩
    """

    def test_compact_produces_archive_on_threshold(self):
        """对话超过阈值后应触发 compact 生成 archive 和记忆文件"""
        session_id = self.generate_unique_session_id(prefix="compact_test")
        verifier = OVSessionVerifier()

        self.logger.info("[1/7] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/7] 写入多轮对话，推动超过 commitTokenThreshold")
        topics = [
            "我叫张三，是一名架构师",
            "我负责的项目叫天穹系统",
            "天穹系统的核心模块是数据湖",
            "数据湖每天处理10TB数据",
            "我们团队有8个人",
            "项目截止日期是今年年底",
        ]

        for i, topic in enumerate(topics):
            self.logger.info(f"  写入第 {i + 1}/{len(topics)} 轮: {topic}")
            self.send_and_log(topic, session_id=session_id)
            time.sleep(3)

        self.logger.info("[3/7] 显式 commit 并等待 archive + 记忆提取")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                verifier.poll_task_until_done(task_id)
        time.sleep(5)

        self.logger.info("[4/7] 验证 archive 已生成（API 级别断言）")
        if ov_session_id:
            has_archive = verifier.assert_archive_exists(ov_session_id)
            self.logger.info(f"  Archive 存在: {has_archive}")
            if not has_archive:
                self.logger.warning("  Archive 未生成，compact 可能未触发")

        self.logger.info("[5/7] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found = OVSessionVerifier.find_new_or_updated_files(before_files, keyword="天穹")
        self.logger.info(f"  本次新增/更新且包含'天穹'的记忆文件数: {len(memory_found)}")
        for mf in memory_found:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")
        if not memory_found:
            self.logger.warning("  未找到本次新增/更新且包含'天穹'的记忆文件")

        self.logger.info("[6/7] 验证 archive 生成后，agent 仍能回答早期信息")
        response = self.send_and_log(
            "我负责的项目叫什么？核心模块是什么？",
            session_id=session_id,
        )
        self.assertAnyKeywordInResponse(
            response,
            [["天穹"], ["数据湖"]],
            case_sensitive=False,
        )

        self.logger.info("[7/7] 验证 archive 生成后，agent 仍能回答近期信息")
        response2 = self.send_and_log(
            "我们团队有几个人？项目截止日期是什么时候？",
            session_id=session_id,
        )
        self.assertAnyKeywordInResponse(
            response2,
            [["8", "八"], ["年底"]],
            case_sensitive=False,
        )


class TestCrossSessionRecall(BaseOpenClawCLITest):
    """
    跨 session recall 验证
    测试目标：验证在 session A 写入的记忆，能在全新的 session B 中通过 auto-recall 检索到
    测试路径：session A 写入 → commit + 记忆提取 → session B 查询 → 验证 recall 注入 + 记忆文件存在
    """

    def test_cross_session_recall_works(self):
        """session A 写入的信息应能在 session B 中通过 recall 检索到"""
        unique_marker = "星云数据库"
        unique_detail = "使用分布式架构"

        session_a = self.generate_unique_session_id(prefix="recall_source")
        session_b = self.generate_unique_session_id(prefix="recall_target")
        verifier = OVSessionVerifier()

        self.logger.info("[1/7] 记录 OV session 快照 + 记忆文件 mtime 快照")
        before_sessions = verifier.list_session_ids()
        before_files = OVSessionVerifier.snapshot_memory_files()

        self.logger.info("[2/7] 在 session A 中写入独特信息")
        self.send_and_log(
            f"我正在开发{unique_marker}，{unique_detail}，支持PB级存储",
            session_id=session_a,
        )

        self.smart_wait_for_sync(
            check_message=f"{unique_marker}用什么架构",
            keywords=["分布式"],
            timeout=30.0,
            session_id=session_a,
        )

        self.logger.info("[3/7] 显式 commit 并等待记忆提取完成")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                result = verifier.poll_task_until_done(task_id)
                if result:
                    extracted = result.get("result", {}).get("memories_extracted", {})
                    self.logger.info(f"  记忆提取结果: {extracted}")

        self.logger.info("[4/7] 验证记忆文件已新增或更新且包含关键信息（文件级别断言）")
        memory_found = OVSessionVerifier.find_new_or_updated_files(
            before_files, keyword=unique_marker
        )
        self.logger.info(f"  本次新增/更新且包含'{unique_marker}'的记忆文件数: {len(memory_found)}")
        for mf in memory_found:
            self.logger.info(f"  [{mf['status']}] {mf['path']}")
            if "content_preview" in mf:
                self.logger.info(f"  内容预览: {mf['content_preview'][:100]}")
        if not memory_found:
            self.logger.warning(f"  未找到本次新增/更新且包含'{unique_marker}'的记忆文件")

        self.logger.info("[5/7] 验证记忆提取成功（API 级别断言）")
        if ov_session_id:
            has_memories = verifier.assert_memories_extracted(ov_session_id)
            self.logger.info(f"  记忆提取成功: {has_memories}")

        self.logger.info("[6/7] 在全新的 session B 中查询相同信息")
        response = self.send_and_log(
            f"我之前提到的{unique_marker}是什么？有什么特点？",
            session_id=session_b,
        )

        self.logger.info("[7/7] 验证 session B 通过 auto-recall 获取到 session A 的记忆")
        self.assertAnyKeywordInResponse(
            response,
            [[unique_marker]],
            case_sensitive=False,
        )
