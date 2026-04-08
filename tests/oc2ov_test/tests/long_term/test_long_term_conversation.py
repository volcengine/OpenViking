"""
长程对话相关测试
测试目标：验证长程对话记忆功能
"""

from tests.base_cli_test import BaseOpenClawCLITest


class TestLongTermMemoryTarget(BaseOpenClawCLITest):
    """
    长程对话核心目标记忆验证
    测试目标：验证多轮对话后，核心信息记忆不丢失
    测试场景：先记住核心信息，插入多轮简单对话，然后验证核心信息
    """

    def test_long_term_target_group_a(self):
        """测试组A：记住关键信息，多轮对话后验证"""
        self.logger.info("[1/3] 测试组A - 步骤1：记住关键信息")
        message1 = "请记住：我的名字是张三，我的工号是1001，我的部门是技术部"
        session_a = self.generate_unique_session_id(prefix="long_term_a")

        self.send_and_log(message1, session_id=session_a)

        self.smart_wait_for_sync(
            check_message="我叫什么名字",
            keywords=["张三"],
            timeout=30.0,
        )

        self.logger.info("[2/3] 步骤2：插入多轮简单对话")
        simple_messages = [
            "1 + 1 等于几？",
            "2 + 3 等于几？",
            "5 + 7 等于几？",
            "10 - 4 等于几？",
            "8 + 9 等于几？",
        ]

        for i, msg in enumerate(simple_messages, 1):
            self.logger.info(f"  简单对话 {i}/{len(simple_messages)}: {msg}")
            self.send_and_log(msg, session_id=session_a)
            self.wait_for_sync(2)

        self.logger.info("[3/3] 步骤3：提问并验证核心信息")
        response = self.send_and_log("我叫什么名字？工号是多少？部门是什么？", session_id=session_a)

        self.assertAnyKeywordInResponse(
            response, [["张三", "1001", "技术部"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")

    def test_long_term_target_group_b(self):
        """测试组B：记住关键信息，多轮对话后验证"""
        self.logger.info("[1/3] 测试组B - 步骤1：记住关键信息")
        message1 = "请记住：我的名字是李四，我的工号是1002，我的部门是产品部"
        session_b = self.generate_unique_session_id(prefix="long_term_b")

        self.send_and_log(message1, session_id=session_b)

        self.smart_wait_for_sync(
            check_message="我叫什么名字",
            keywords=["李四"],
            timeout=30.0,
        )

        self.logger.info("[2/3] 步骤2：插入多轮简单对话")
        simple_messages = [
            "请记住：苹果是红色的",
            "请记住：香蕉是黄色的",
            "请记住：葡萄是紫色的",
            "请记住：橙子是橙色的",
            "请记住：西瓜是绿色的",
        ]

        for i, msg in enumerate(simple_messages, 1):
            self.logger.info(f"  简单对话 {i}/{len(simple_messages)}: {msg}")
            self.send_and_log(msg, session_id=session_b)
            self.wait_for_sync(2)

        self.logger.info("[3/3] 步骤3：提问并验证核心信息")
        response = self.send_and_log("我叫什么名字？工号是多少？部门是什么？", session_id=session_b)

        self.assertAnyKeywordInResponse(
            response, [["李四", "1002", "产品部"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_long_term_target_group_c(self):
        """测试组C：记住关键信息，多轮对话后验证"""
        self.logger.info("[1/3] 测试组C - 步骤1：记住关键信息")
        message1 = "请记住：我的名字是王五，我的工号是1003，我的部门是设计部"
        session_c = self.generate_unique_session_id(prefix="long_term_c")

        self.send_and_log(message1, session_id=session_c)

        self.smart_wait_for_sync(
            check_message="我叫什么名字",
            keywords=["王五"],
            timeout=30.0,
        )

        self.logger.info("[2/3] 步骤2：插入多轮简单对话")
        simple_messages = [
            "请重复：今天是2026年3月24日",
            "请重复：今天是星期三",
            "请重复：今天天气晴朗",
            "请重复：现在是测试时间",
            "请重复：正在进行记忆测试",
        ]

        for i, msg in enumerate(simple_messages, 1):
            self.logger.info(f"  简单对话 {i}/{len(simple_messages)}: {msg}")
            self.send_and_log(msg, session_id=session_c)
            self.wait_for_sync(2)

        self.logger.info("[3/3] 步骤3：提问并验证核心信息")
        response = self.send_and_log("我叫什么名字？工号是多少？部门是什么？", session_id=session_c)

        self.assertAnyKeywordInResponse(
            response, [["王五", "1003", "设计部"]], case_sensitive=False
        )

        self.logger.info("测试组C执行完成")


class TestLongTermSummaryGeneration(BaseOpenClawCLITest):
    """
    长程对话总结生成验证
    测试目标：验证多轮信息后，能记住并整合所有信息
    测试场景：先记住多条信息，然后要求复述
    """

    def test_summary_generation_group_a(self):
        """测试组A：记住多条个人信息，然后复述"""
        self.logger.info("[1/4] 测试组A - 步骤1：记住第一条信息")
        session_a = self.generate_unique_session_id(prefix="summary_a")

        self.send_and_log("请记住：我的名字叫测试A，今年28岁", session_id=session_a)
        self.wait_for_sync()

        self.logger.info("[2/4] 步骤2：记住第二条信息")
        self.send_and_log("请记住：我住在北京，职业是工程师", session_id=session_a)
        self.wait_for_sync()

        self.logger.info("[3/4] 步骤3：记住第三条信息")
        self.send_and_log("请记住：我喜欢编程，喜欢阅读", session_id=session_a)
        self.wait_for_sync()

        self.logger.info("[4/4] 步骤4：要求复述所有信息")
        response = self.send_and_log("请复述一下刚才记住的所有关于我的信息", session_id=session_a)

        self.assertAnyKeywordInResponse(
            response, [["测试A", "28", "北京", "工程师", "编程", "阅读"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")

    def test_summary_generation_group_b(self):
        """测试组B：记住多条个人信息，然后复述"""
        self.logger.info("[1/4] 测试组B - 步骤1：记住第一条信息")
        session_b = self.generate_unique_session_id(prefix="summary_b")

        self.send_and_log("请记住：我的名字叫测试B，今年30岁", session_id=session_b)
        self.wait_for_sync()

        self.logger.info("[2/4] 步骤2：记住第二条信息")
        self.send_and_log("请记住：我住在上海，职业是设计师", session_id=session_b)
        self.wait_for_sync()

        self.logger.info("[3/4] 步骤3：记住第三条信息")
        self.send_and_log("请记住：我喜欢画画，喜欢旅行", session_id=session_b)
        self.wait_for_sync()

        self.logger.info("[4/4] 步骤4：要求复述所有信息")
        response = self.send_and_log("请复述一下刚才记住的所有关于我的信息", session_id=session_b)

        self.assertAnyKeywordInResponse(
            response, [["测试B", "30", "上海", "设计师", "画画", "旅行"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_summary_generation_group_c(self):
        """测试组C：记住多条个人信息，然后复述"""
        self.logger.info("[1/4] 测试组C - 步骤1：记住第一条信息")
        session_c = self.generate_unique_session_id(prefix="summary_c")

        self.send_and_log("请记住：我的名字叫测试C，今年32岁", session_id=session_c)
        self.wait_for_sync()

        self.logger.info("[2/4] 步骤2：记住第二条信息")
        self.send_and_log("请记住：我住在广州，职业是产品经理", session_id=session_c)
        self.wait_for_sync()

        self.logger.info("[3/4] 步骤3：记住第三条信息")
        self.send_and_log("请记住：我喜欢音乐，喜欢运动", session_id=session_c)
        self.wait_for_sync()

        self.logger.info("[4/4] 步骤4：要求复述所有信息")
        response = self.send_and_log("请复述一下刚才记住的所有关于我的信息", session_id=session_c)

        self.assertAnyKeywordInResponse(
            response, [["测试C", "32", "广州", "产品经理", "音乐", "运动"]], case_sensitive=False
        )

        self.logger.info("测试组C执行完成")
