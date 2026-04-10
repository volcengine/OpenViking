"""
示例测试 - 展示增强版测试基类的用法
演示：Session ID 管理、智能等待、重试机制、测试数据管理
"""

from tests.base_cli_test import BaseOpenClawCLITest
from utils.test_utils import TestData


class TestEnhancedFeatures(BaseOpenClawCLITest):
    """
    增强功能演示测试
    展示如何使用 Session ID 管理、智能等待、重试机制、测试数据管理
    """

    def test_auto_session_id(self):
        """
        演示：自动 Session ID 管理
        - 每个测试方法自动获得唯一的 session_id
        - 通过 self.current_session_id 访问
        """
        self.logger.info(f"当前测试自动生成的 Session ID: {self.current_session_id}")

        message = "我叫测试用户，今年25岁"
        response = self.send_and_log(message)

        self.wait_for_sync()

        response2 = self.send_and_log("我是谁")
        self.assertAnyKeywordInResponse(response2, [["测试用户", "25岁"]])

    def test_custom_session_id(self):
        """
        演示：自定义 Session ID
        - 使用 generate_unique_session_id() 生成自定义 session_id
        - 可以指定前缀
        """
        custom_session = self.generate_unique_session_id(prefix="custom_test")
        self.logger.info(f"自定义 Session ID: {custom_session}")

        message = "我喜欢吃苹果"
        response = self.send_and_log(message, session_id=custom_session)

        self.wait_for_sync()

        response2 = self.send_and_log("我喜欢吃什么", session_id=custom_session)
        self.assertAnyKeywordInResponse(response2, [["苹果"]])

    def test_smart_wait(self):
        """
        演示：智能等待
        - 使用 smart_wait_for_sync() 替代固定等待
        - 轮询检查记忆是否同步完成
        """
        message = "我的爱好是打篮球和游泳"
        self.send_and_log(message)

        success = self.smart_wait_for_sync(
            check_message="我的爱好是什么",
            keywords=["篮球", "游泳"],
            timeout=30.0,
            poll_interval=2.0,
        )

        self.assertTrue(success, "智能等待超时，记忆未同步")

    def test_retry_on_failure(self):
        """
        演示：重试机制
        - 使用 send_with_retry() 在失败时自动重试
        - 使用 send_and_log(retry_on_failure=True) 启用重试
        """
        message = "我在北京工作"

        response = self.send_with_retry(
            message,
            max_retries=3,
        )

        self.wait_for_sync()

        response2 = self.send_and_log("我在哪里工作", retry_on_failure=True)
        self.assertAnyKeywordInResponse(response2, [["北京"]])

    def test_data_driven_with_default_data(self):
        """
        演示：使用默认测试数据
        - 使用 get_test_data() 获取预定义的测试数据
        - 使用 run_with_test_data() 快速运行测试
        """
        _, query_response = self.run_with_test_data(
            data_name="user_xiaoming",
            query_message="我是谁，今年多大",
        )

        self.assertIsNotNone(query_response)

    def test_data_driven_with_custom_data(self):
        """
        演示：使用自定义测试数据
        - 创建 TestData 对象
        - 注册到 data_manager
        """
        custom_data = TestData(
            name="custom_user",
            description="自定义测试用户",
            input_data={
                "message": "我叫自定义用户，职业是数据分析师",
            },
            expected_keywords=[
                ["自定义用户"],
                ["数据分析师"],
            ],
            tags=["custom", "user"],
        )

        self.data_manager.register_data(custom_data)

        _, query_response = self.run_with_test_data(
            data_name="custom_user",
            query_message="我的职业是什么",
        )

        self.assertIsNotNone(query_response)

    def test_combined_features(self):
        """
        演示：组合使用多个增强功能
        - 自动 Session ID
        - 智能等待
        - 重试机制
        - 测试数据
        """
        data = self.get_test_data("fruit_cherry")
        self.assertIsNotNone(data, "测试数据不存在")

        message = data.input_data.get("message")
        self.send_and_log(message, retry_on_failure=True)

        success = self.smart_wait_for_sync(
            check_message="我喜欢吃什么水果",
            keywords=data.expected_keywords[0],
            timeout=30.0,
        )

        self.assertTrue(success, "智能等待超时")


class TestDataDrivenTests(BaseOpenClawCLITest):
    """
    数据驱动测试示例
    使用预定义的测试数据运行多个测试用例
    """

    def test_fruit_cherry(self):
        """测试水果偏好 - 樱桃"""
        _, response = self.run_with_test_data(
            data_name="fruit_cherry",
            query_message="我喜欢吃什么水果，平时爱喝什么",
        )
        self.assertIsNotNone(response)

    def test_fruit_mango(self):
        """测试水果偏好 - 芒果"""
        _, response = self.run_with_test_data(
            data_name="fruit_mango",
            query_message="我喜欢吃什么水果，平时爱喝什么",
        )
        self.assertIsNotNone(response)

    def test_fruit_strawberry(self):
        """测试水果偏好 - 草莓"""
        _, response = self.run_with_test_data(
            data_name="fruit_strawberry",
            query_message="我喜欢吃什么水果，平时爱喝什么",
        )
        self.assertIsNotNone(response)
