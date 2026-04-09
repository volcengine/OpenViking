"""
P0 - 记忆结构化写入验证测试
测试目标：验证OpenViking能正确接收、存储OpenClaw传入的用户信息
"""

from tests.base_cli_test import BaseOpenClawCLITest
from utils.test_utils import TestData


class TestMemoryWriteGroupA(BaseOpenClawCLITest):
    """
    测试组A（小明）：基本记忆结构化写入验证
    测试目标：验证OpenViking能正确接收、存储OpenClaw传入的用户信息，无数据丢失
    测试场景：写入小明的基本信息（姓名、年龄、地区、职业），然后验证信息正确性
    """

    def test_memory_write_basic_info(self):
        """测试场景：基本信息写入与验证"""
        self.logger.info("[1/4] 发送记忆写入指令")

        self.run_with_test_data("user_xiaoming")

        self.logger.info("测试组A执行完成")


class TestMemoryWriteGroupB(BaseOpenClawCLITest):
    """
    测试组B（小红）：更多维度信息写入
    测试目标：多维度丰富信息写入验证
    测试场景：写入小红的详细信息（姓名、年龄、地址、职业、喜好、生日）
    """

    def test_memory_write_rich_info(self):
        """测试场景：丰富信息写入与验证"""
        self.logger.info("[1/3] 发送丰富信息记忆写入")

        self.run_with_test_data("user_xiaohong")

        self.logger.info("测试组B执行完成")


class TestMemoryWriteAutoSession(BaseOpenClawCLITest):
    """
    测试自动 Session ID 管理功能
    测试目标：验证自动生成的 session_id 功能正常工作
    """

    def test_auto_session_basic(self):
        """测试场景：使用自动生成的 session_id 进行基本记忆写入和读取"""
        self.logger.info("测试自动 Session ID 功能")

        message = "我叫自动测试用户，今年28岁"
        self.send_and_log(message)

        self.smart_wait_for_sync(
            check_message="我是谁",
            keywords=["自动测试用户", "28"],
            timeout=30.0,
        )

    def test_custom_session_prefix(self):
        """测试场景：使用自定义前缀的 session_id 进行记忆写入和读取"""
        custom_session = self.generate_unique_session_id(prefix="custom_write")
        self.logger.info(f"使用自定义 session: {custom_session}")

        message = "我叫自定义用户，职业是测试工程师"
        self.send_and_log(message, session_id=custom_session)

        self.smart_wait_for_sync(
            check_message="我的职业是什么",
            keywords=["测试工程师"],
            timeout=30.0,
        )
