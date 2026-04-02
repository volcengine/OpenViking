"""
测试基类
"""

import logging
import time
import unittest

from config.settings import TEST_CONFIG
from utils.assertions import AssertionHelper
from utils.openclaw_client import OpenClawClient


class BaseOpenClawTest(unittest.TestCase):
    """
    OpenClaw 测试基类
    """

    @classmethod
    def setUpClass(cls):
        """
        测试类初始化
        """
        cls.client = OpenClawClient()
        cls.logger = logging.getLogger(cls.__name__)
        cls.wait_time = TEST_CONFIG["wait_time"]
        cls.assertion = AssertionHelper()
        cls.logger.info("=" * 60)
        cls.logger.info(f"测试类 {cls.__name__} 开始")
        cls.logger.info("=" * 60)

    def setUp(self):
        """
        每个测试用例开始前
        """
        self.logger.info("\n" + "-" * 60)
        self.logger.info(f"开始测试: {self._testMethodName}")

    def wait_for_sync(self, seconds: int = None):
        """
        等待记忆同步
        """
        wait_seconds = seconds or self.wait_time
        self.logger.info(f"等待 {wait_seconds} 秒，确认记忆同步...")
        time.sleep(wait_seconds)

    def send_and_log(self, message: str, agent_id: str = None):
        """
        发送消息并记录日志
        """
        self.logger.info("\n" + "▸" * 40)
        self.logger.info("📨 测试步骤 - 发送消息")
        self.logger.info("▸" * 40)
        self.logger.info(f"消息内容: {message}")
        if agent_id:
            self.logger.info(f"Agent ID: {agent_id}")

        response = self.client.send_message(message, agent_id)

        self.logger.info("\n" + "◂" * 40)
        self.logger.info("📩 测试步骤 - 响应接收")
        self.logger.info("◂" * 40)

        # 提取并显示响应文本
        response_text = self.assertion.extract_response_text(response)
        self.logger.info(f"响应文本: {response_text}")

        self.logger.info("◂" * 40 + "\n")
        return response

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
        cls.logger.info("\n" + "=" * 60)
        cls.logger.info(f"测试类 {cls.__name__} 结束")
        cls.logger.info("=" * 60)
