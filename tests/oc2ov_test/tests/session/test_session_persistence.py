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
        message = "我喜欢吃樱桃，日常喜欢喝美式咖啡"
        session_a = "persistence_test_a"

        response1 = self.send_and_log(message, session_id=session_a)
        self.wait_for_sync()

        self.logger.info("[2/5] 验证当前会话能读取记忆")
        response2 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_a)
        self.assertAnyKeywordInResponse(
            response2, [["樱桃"], ["美式", "咖啡"]], case_sensitive=False
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        session_b = "persistence_test_b"

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_b)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["樱桃"], ["美式", "咖啡"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")

    def test_memory_persistence_group_b(self):
        """测试组B：我喜欢吃芒果，日常喜欢喝拿铁咖啡"""
        self.logger.info("[1/5] 测试组B - 写入记忆信息")
        message = "我喜欢吃芒果，日常喜欢喝拿铁咖啡"
        session_c = "persistence_test_c"

        response1 = self.send_and_log(message, session_id=session_c)
        self.wait_for_sync()

        self.logger.info("[2/5] 验证当前会话能读取记忆")
        response2 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_c)
        self.assertAnyKeywordInResponse(
            response2, [["芒果"], ["拿铁", "咖啡"]], case_sensitive=False
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        session_d = "persistence_test_d"

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_d)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["芒果"], ["拿铁", "咖啡"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_memory_persistence_group_c(self):
        """测试组C：我喜欢吃草莓，日常喜欢喝抹茶拿铁"""
        self.logger.info("[1/5] 测试组C - 写入记忆信息")
        message = "我喜欢吃草莓，日常喜欢喝抹茶拿铁"
        session_e = "persistence_test_e"

        response1 = self.send_and_log(message, session_id=session_e)
        self.wait_for_sync()

        self.logger.info("[2/5] 验证当前会话能读取记忆")
        response2 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_e)
        self.assertAnyKeywordInResponse(
            response2, [["草莓"], ["抹茶", "拿铁"]], case_sensitive=False
        )

        self.logger.info("[3/5] 使用新的 session-id 模拟新会话")
        session_f = "persistence_test_f"

        self.wait_for_sync()

        self.logger.info("[4/5] 在新会话中查询记忆")
        response3 = self.send_and_log("我喜欢吃什么水果？平时爱喝什么？", session_id=session_f)

        self.logger.info("[5/5] 验证记忆持久化读取")
        self.assertAnyKeywordInResponse(
            response3, [["草莓"], ["抹茶", "拿铁"]], case_sensitive=False
        )

        self.logger.info("测试组C执行完成")
