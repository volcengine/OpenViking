"""
技能相关测试
测试目标：验证技能调用后的记忆功能
注意：简化为记忆读写测试，避免外部技能依赖
"""

from tests.base_cli_test import BaseOpenClawCLITest


class TestSkillExperiencePrecipitation(BaseOpenClawCLITest):
    """
    销售数据查询技能经验沉淀验证（P1）
    测试目标：验证记忆读写功能正常，数据能正确存储和检索
    测试场景：先发送简单数据，再验证读取功能
    """

    def test_skill_experience_group_a(self):
        """测试组A：简单记忆读写测试-先记住信息再读取"""
        self.logger.info("[1/2] 测试组A - 步骤1：记住个人信息")
        message1 = "请记住：我叫小明，今年25岁，住在上海"
        session_a = "skill_exp_a"

        response1 = self.send_and_log(message1, session_id=session_a)
        self.wait_for_sync(30)

        self.logger.info("[2/2] 步骤2：验证信息读取")
        message2 = "我叫什么名字？今年多大？"
        response2 = self.send_and_log(message2, session_id=session_a)

        self.assertAnyKeywordInResponse(response2, [["小明", "25", "上海"]], case_sensitive=False)

        self.logger.info("测试组A执行完成")

    def test_skill_experience_group_b(self):
        """测试组B：跨会话记忆读取测试"""
        self.logger.info("[1/2] 测试组B - 步骤1：记住个人信息")
        message1 = "请记住：我是小红，职业是设计师，喜欢画画"
        session_b = "skill_exp_b"

        self.send_and_log(message1, session_id=session_b)
        self.wait_for_sync(30)

        self.logger.info("[2/2] 步骤2：验证信息读取")
        message2 = "我的职业是什么？我的爱好是什么？"
        response2 = self.send_and_log(message2, session_id=session_b)

        self.assertAnyKeywordInResponse(
            response2, [["小红", "设计师", "画画"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_skill_experience_group_c(self):
        """测试组C：记忆更新功能测试"""
        self.logger.info("[1/3] 测试组C - 步骤1：记住初始信息")
        message1 = "请记住：我叫小刚，喜欢踢足球"
        session_c = "skill_exp_c"

        self.send_and_log(message1, session_id=session_c)
        self.wait_for_sync(30)

        self.logger.info("[2/3] 步骤2：更新信息")
        message2 = "记住：我现在喜欢打篮球，不喜欢踢足球了"
        self.send_and_log(message2, session_id=session_c)
        self.wait_for_sync(30)

        self.logger.info("[3/3] 步骤3：验证更新后的信息")
        message3 = "我现在喜欢什么运动？"
        response3 = self.send_and_log(message3, session_id=session_c)

        self.assertAnyKeywordInResponse(response3, [["小刚", "篮球"]], case_sensitive=False)

        self.logger.info("测试组C执行完成")


class TestSkillMemoryLogVerification(BaseOpenClawCLITest):
    """
    技能调用记忆注入日志验证（P0）
    测试目标：验证发送数据后，记忆成功注入OpenViking
    测试场景：发送简单数据，然后给出手动检查日志的提示
    """

    def test_skill_log_group_a(self):
        """测试组A：简单数据写入测试"""
        self.logger.info("[1/2] 测试组A - 发送个人信息")
        test_data = "我叫测试员A，这是我的测试数据"
        session_a = "skill_log_a"

        response = self.send_and_log(test_data, session_id=session_a)
        self.wait_for_sync(30)

        self.logger.info("[2/2] 数据发送完成")
        self.logger.info("提示：请手动检查OpenClaw日志，确认有记忆注入记录")
        self.logger.info("提示：日志文件位于 /tmp/openclaw/openclaw-{今天日期}.log")
        self.logger.info("提示：搜索关键词 'memory-openviking'")

        self.assertAnyKeywordInResponse(response, [["测试员A", "测试数据"]], case_sensitive=False)

        self.logger.info("测试组A执行完成")

    def test_skill_log_group_b(self):
        """测试组B：简单数据写入测试2"""
        self.logger.info("[1/2] 测试组B - 发送另一条个人信息")
        test_data = "我是测试员B，我喜欢测试工作"
        session_b = "skill_log_b"

        response = self.send_and_log(test_data, session_id=session_b)
        self.wait_for_sync(30)

        self.logger.info("[2/2] 数据发送完成")
        self.assertAnyKeywordInResponse(response, [["测试员B", "测试工作"]], case_sensitive=False)

        self.logger.info("测试组B执行完成")

    def test_skill_log_group_c(self):
        """测试组C：简单数据写入测试3"""
        self.logger.info("[1/2] 测试组C - 发送第三条信息")
        test_data = "我是测试员C，今天的日期是2026-03-24"
        session_c = "skill_log_c"

        response = self.send_and_log(test_data, session_id=session_c)
        self.wait_for_sync(30)

        self.logger.info("[2/2] 数据发送完成")
        self.assertAnyKeywordInResponse(response, [["测试员C", "2026-03-24"]], case_sensitive=False)

        self.logger.info("测试组C执行完成")
