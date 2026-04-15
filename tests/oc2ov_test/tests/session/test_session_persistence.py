"""
Session 持久化测试
测试目标：验证跨会话的记忆持久化功能
测试场景：写入用户信息，使用不同 session-id 模拟新会话，验证记忆读取
"""

from tests.base_cli_test import BaseOpenClawCLITest


class TestMemoryPersistence(BaseOpenClawCLITest):
    """
    记忆跨会话读取验证
    测试目标：验证OpenClaw重启后，可从OpenViking正常读取历史记忆，记忆持久化生效
    测试场景：写入用户信息，使用不同session-id模拟新会话，验证记忆读取
    """

    def test_memory_persistence_group_a(self):
        """测试组A：我喜欢吃樱桃，日常喜欢喝美式咖啡"""
        self.logger.info("[1/5] 测试组A - 写入记忆信息")

        self.run_with_test_data(
            data_name="fruit_cherry",
            query_message="我喜欢吃什么水果？平时爱喝什么？",
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        new_session = self.generate_unique_session_id(prefix="persistence_new_a")

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=new_session)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["樱桃"], ["美式", "咖啡"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")

    def test_memory_persistence_group_b(self):
        """测试组B：我喜欢吃芒果，日常喜欢喝拿铁咖啡"""
        self.logger.info("[1/5] 测试组B - 写入记忆信息")

        self.run_with_test_data(
            data_name="fruit_mango",
            query_message="我喜欢吃什么水果？平时爱喝什么？",
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        new_session = self.generate_unique_session_id(prefix="persistence_new_b")

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=new_session)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["芒果"], ["拿铁", "咖啡"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_memory_persistence_group_c(self):
        """测试组C：我喜欢吃草莓，日常喜欢喝抹茶拿铁"""
        self.logger.info("[1/5] 测试组C - 写入记忆信息")

        self.run_with_test_data(
            data_name="fruit_strawberry",
            query_message="我喜欢吃什么水果？平时爱喝什么？",
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        new_session = self.generate_unique_session_id(prefix="persistence_new_c")

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=new_session)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["草莓"], ["抹茶", "拿铁"]], case_sensitive=False
        )

        self.logger.info("测试组C执行完成")


class TestMemoryPersistenceWithRetry(BaseOpenClawCLITest):
    """
    记忆持久化测试（带重试机制）
    """

    def test_persistence_with_retry(self):
        """测试场景：使用重试机制验证持久化"""
        self.logger.info("[1/3] 写入记忆信息")
        message = "我叫重试测试用户，喜欢游泳"

        self.send_with_retry(message, max_retries=3)

        self.smart_wait_for_sync(
            check_message="我喜欢什么运动",
            keywords=["游泳"],
            timeout=30.0,
        )

        self.logger.info("[2/3] 使用新会话查询")
        new_session = self.generate_unique_session_id(prefix="retry_persistence")

        response = self.send_with_retry(
            "我喜欢什么运动",
            session_id=new_session,
            max_retries=3,
        )

        self.logger.info("[3/3] 验证记忆持久化")
        self.assertAnyKeywordInResponse(response, [["游泳"]], case_sensitive=False)
