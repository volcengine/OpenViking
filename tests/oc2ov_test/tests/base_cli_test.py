"""
测试基类 - 使用 OpenClaw CLI
增强版：支持 Session ID 自动管理、智能等待、重试机制、测试数据管理
"""

import logging
import time
import unittest

from config.settings import TEST_CONFIG
from utils.assertions import AssertionHelper
from utils.openclaw_cli_client import OpenClawCLIClient, _wait_for_session_lock_release
from utils.test_utils import (
    RetryManager,
    SessionIdManager,
    SmartWaiter,
    TestData,
    TestDataManager,
    get_default_data_manager,
)

MIN_SYNC_WAIT_SECONDS = 5


class BaseOpenClawCLITest(unittest.TestCase):
    """
    OpenClaw CLI 测试基类（增强版）

    新增功能：
    - Session ID 自动管理：每个测试类使用唯一的 session_id
    - 智能等待策略：替代固定等待，支持轮询检查
    - 重试机制：失败时自动重试
    - 测试数据管理：支持数据驱动测试
    """

    session_manager: SessionIdManager = SessionIdManager()
    data_manager: TestDataManager = get_default_data_manager()

    @classmethod
    def setUpClass(cls):
        """
        测试类初始化
        """
        cls._class_session_id = SessionIdManager.generate_test_class_session_id(cls.__name__)
        cls.client = OpenClawCLIClient(session_id=cls._class_session_id)
        cls.logger = logging.getLogger(cls.__name__)
        cls.wait_time = TEST_CONFIG["wait_time"]
        cls.assertion = AssertionHelper()
        cls.smart_waiter = SmartWaiter(
            default_timeout=cls.wait_time * 3,
            default_poll_interval=2.0,
        )
        cls.retry_manager = RetryManager(
            max_retries=3,
            base_delay=1.0,
        )

        cls.session_manager.register_session(
            cls._class_session_id,
            {"test_class": cls.__name__},
        )

        cls.logger.info("=" * 60)
        cls.logger.info(f"测试类 {cls.__name__} 开始")
        cls.logger.info(f"Class Session ID: {cls._class_session_id}")
        cls.logger.info("=" * 60)

    def setUp(self):
        """
        每个测试用例开始前
        """
        self.logger.info("\n" + "-" * 60)
        self.logger.info(f"开始测试: {self._testMethodName}")

    @property
    def current_session_id(self) -> str:
        """
        获取当前测试类的 session_id

        Returns:
            str: 当前 session_id
        """
        return self._class_session_id

    def generate_unique_session_id(self, prefix: str = "test") -> str:
        """
        生成唯一的 session_id

        Args:
            prefix: session_id 前缀

        Returns:
            str: 唯一的 session_id
        """
        return SessionIdManager.generate_session_id(prefix=prefix)

    def wait_for_sync(self, seconds: int = None, session_id: str = None):
        """
        等待记忆同步（锁释放 + 最小等待）

        Args:
            seconds: 等待秒数，默认使用配置的 wait_time，最小 MIN_SYNC_WAIT_SECONDS
            session_id: 等待的 session ID，默认使用当前测试类的 session_id
        """
        target_session_id = session_id or self.current_session_id
        wait_seconds = max(seconds or self.wait_time, MIN_SYNC_WAIT_SECONDS)
        self.logger.info(
            f"等待记忆同步 (锁释放 + {wait_seconds}秒)... [session={target_session_id}]"
        )

        lock_ok = _wait_for_session_lock_release(target_session_id)
        if not lock_ok:
            self.logger.warning("Session lock 未释放，额外等待 5 秒...")
            time.sleep(5)

        time.sleep(wait_seconds)

    def smart_wait_for_sync(
        self,
        check_message: str = None,
        keywords: list = None,
        timeout: float = None,
        poll_interval: float = 3.0,
        session_id: str = None,
    ) -> bool:
        """
        智能等待记忆同步（锁释放 + 轮询检查）

        Args:
            check_message: 用于检查的消息（如不提供则使用固定等待）
            keywords: 期望响应中包含的关键词
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒），最小 3.0
            session_id: 等待和检查的 session ID，默认使用当前测试类的 session_id

        Returns:
            bool: 是否成功同步
        """
        target_session_id = session_id or self.current_session_id

        if not check_message or not keywords:
            self.wait_for_sync(session_id=target_session_id)
            return True

        poll_interval = max(poll_interval, 3.0)
        timeout = timeout or self.wait_time * 3

        lock_ok = _wait_for_session_lock_release(target_session_id)
        if not lock_ok:
            self.logger.warning("Session lock 未释放，额外等待 5 秒...")
            time.sleep(5)

        def check_response() -> bool:
            _wait_for_session_lock_release(target_session_id)
            response = self.client.send_message(check_message, session_id=target_session_id)
            return self.assertion.assert_keywords_in_response(
                response, keywords, require_all=True, case_sensitive=False
            )

        return self.smart_waiter.wait_for_condition(
            check_response,
            timeout=timeout,
            poll_interval=poll_interval,
            message=f"等待记忆同步 (关键词: {keywords}) [session={target_session_id}]",
        )

    def send_and_log(
        self,
        message: str,
        session_id: str = None,
        agent_id: str = None,
        retry_on_failure: bool = False,
    ):
        """
        发送消息并记录日志

        Args:
            message: 消息内容
            session_id: session ID（默认使用当前测试类的 session_id）
            agent_id: agent ID
            retry_on_failure: 是否在失败时重试

        Returns:
            dict: 响应结果
        """
        target_session_id = session_id or self.current_session_id

        self.logger.info("\n" + "▸" * 40)
        self.logger.info("📨 测试步骤 - 发送消息")
        self.logger.info("▸" * 40)
        self.logger.info(f"消息内容: {message}")
        self.logger.info(f"Session ID: {target_session_id}")
        if agent_id:
            self.logger.info(f"Agent ID: {agent_id}")

        if retry_on_failure:

            @self.retry_manager.retry_on_exception(Exception)
            def send_with_retry():
                return self.client.send_message(message, target_session_id, agent_id)

            response = send_with_retry()
        else:
            response = self.client.send_message(message, target_session_id, agent_id)

        self.logger.info("\n" + "◂" * 40)
        self.logger.info("📩 测试步骤 - 响应接收")
        self.logger.info("◂" * 40)

        response_text = self.assertion.extract_response_text(response)
        self.logger.info(f"响应文本: {response_text}")

        self.logger.info("◂" * 40 + "\n")
        return response

    def _is_llm_timeout(self, response) -> bool:
        text = self.assertion.extract_response_text(response)
        timeout_indicators = [
            "idle timeout",
            "did not produce a response",
            "LLM idle timeout",
            "timed out",
            "couldn't generate a response",
            "couldn't generate",
            "please try again",
        ]
        return any(ind.lower() in text.lower() for ind in timeout_indicators)

    def _is_empty_response(self, response) -> bool:
        text = self.assertion.extract_response_text(response)
        return not text.strip()

    def _is_tool_result_only(self, response) -> bool:
        text = self.assertion.extract_response_text(response).strip()
        if not text:
            return False
        tool_result_prefixes = [
            '[{"name"',
            '[{"id"',
            '[{"type"',
            '{"name":',
            '{"id":',
            '{"type":',
        ]
        if any(text.startswith(p) for p in tool_result_prefixes):
            return True
        import re

        tool_result_pattern = r"^\[?\{[^}]*\"name\"\s*:\s*\"none\"[^}]*\}\]?[\s]*$"
        if re.match(tool_result_pattern, text):
            return True
        if text.startswith("[{") and text.endswith("}]"):
            try:
                import json

                parsed = json.loads(text)
                if isinstance(parsed, list) and all(
                    isinstance(item, dict) and "name" in item for item in parsed
                ):
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
        tool_result_patterns = [
            '"name":"none"',
        ]
        return any(p in text for p in tool_result_patterns) and len(text) < 200

    def send_and_retry_on_timeout(
        self,
        message: str,
        session_id: str = None,
        agent_id: str = None,
        max_retries: int = 2,
        retry_delay: float = 5.0,
    ):
        target_session_id = session_id or self.current_session_id
        for attempt in range(max_retries + 1):
            response = self.send_and_log(message, session_id=target_session_id, agent_id=agent_id)
            is_timeout = self._is_llm_timeout(response)
            is_empty = self._is_empty_response(response)
            is_tool_result = self._is_tool_result_only(response)
            if not is_timeout and not is_empty and not is_tool_result:
                return response
            if is_timeout:
                reason = "LLM idle timeout"
            elif is_empty:
                reason = "empty response (no text)"
            else:
                reason = "tool result only (no natural language answer)"
            self.logger.warning(
                f"{reason} (attempt {attempt + 1}/{max_retries + 1}), retrying in {retry_delay}s..."
            )
            if attempt < max_retries:
                time.sleep(retry_delay)
        self.logger.warning(
            f"Retry exhausted after {max_retries + 1} attempts, returning last response"
        )
        return response

    def send_with_retry(
        self,
        message: str,
        session_id: str = None,
        agent_id: str = None,
        max_retries: int = 3,
    ):
        """
        发送消息并在失败时重试

        Args:
            message: 消息内容
            session_id: session ID
            agent_id: agent ID
            max_retries: 最大重试次数

        Returns:
            dict: 响应结果
        """
        retry_manager = RetryManager(max_retries=max_retries)

        @retry_manager.retry_on_exception(Exception)
        def send():
            return self.send_and_log(message, session_id, agent_id)

        return send()

    def assertKeywordsInResponse(
        self, response, keywords, require_all=True, case_sensitive=False, msg=None
    ):
        """
        断言响应中包含指定关键词
        """
        success = self.assertion.assert_keywords_in_response(
            response, keywords, require_all, case_sensitive
        )
        self.assertTrue(success, msg or f"关键词断言失败，期望关键词: {keywords}")

    def assertSimilarity(self, response, expected_text, min_similarity=0.6, msg=None):
        """
        断言响应文本与期望文本的相似度
        """
        success = self.assertion.assert_similarity(response, expected_text, min_similarity)
        self.assertTrue(success, msg or f"相似度断言失败，期望相似度 >= {min_similarity:.0%}")

    def assertAnyKeywordInResponse(self, response, keyword_groups, case_sensitive=False, msg=None):
        """
        断言响应中包含任意一组关键词中的任意一个
        """
        success = self.assertion.assert_any_keyword_in_response(
            response, keyword_groups, case_sensitive
        )
        self.assertTrue(success, msg or "未在任何关键词组中找到匹配")

    def get_test_data(self, name: str) -> TestData:
        """
        获取测试数据

        Args:
            name: 数据名称

        Returns:
            TestData: 测试数据
        """
        return self.data_manager.get_data(name)

    def run_with_test_data(self, data_name: str, query_message: str = None):
        """
        使用测试数据运行测试

        Args:
            data_name: 测试数据名称
            query_message: 查询消息（可选）

        Returns:
            tuple: (写入响应, 查询响应)
        """
        data = self.get_test_data(data_name)
        if not data:
            self.fail(f"测试数据不存在: {data_name}")

        message = data.input_data.get("message", "")
        if not message:
            self.fail(f"测试数据 {data_name} 没有消息内容")

        response1 = self.send_and_log(message)
        self.wait_for_sync()

        query_response = None
        if query_message:
            query_response = self.send_and_log(query_message)

            if data.expected_keywords:
                for keyword_group in data.expected_keywords:
                    self.assertAnyKeywordInResponse(query_response, keyword_group)

        return response1, query_response

    def tearDown(self):
        """
        每个测试用例结束后
        """
        self.logger.info(f"测试完成: {self._testMethodName}")

    @classmethod
    def tearDownClass(cls):
        """
        测试类结束
        """
        cls.session_manager.cleanup_session(cls._class_session_id)
        cls.logger.info("\n" + "=" * 60)
        cls.logger.info(f"测试类 {cls.__name__} 结束")
        cls.logger.info("=" * 60)
