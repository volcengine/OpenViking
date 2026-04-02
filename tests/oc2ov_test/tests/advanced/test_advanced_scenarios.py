"""
高级场景测试
测试目标：验证复杂记忆场景
"""

from tests.base_cli_test import BaseOpenClawCLITest


class TestComplexScenarioMultiUsers(BaseOpenClawCLITest):
    """
    复杂场景1：多用户切换
    测试目标：验证多用户记忆切换
    """

    def test_multi_users_switch(self):
        """多用户记忆切换测试"""
        users = [
            {"name": "用户A", "age": 22, "region": "东北区", "job": "学生"},
            {"name": "用户B", "age": 35, "region": "西北区", "job": "医生"},
            {"name": "用户C", "age": 45, "region": "中南区", "job": "教师"},
        ]

        for user in users:
            self.logger.info(f"写入用户信息: {user}")
            msg = f"我叫{user['name']}，今年{user['age']}岁，住在{user['region']}，职业是{user['job']}"
            self.send_and_log(msg)
            self.wait_for_sync()

            self.logger.info("  验证信息:")
            resp = self.send_and_log("请介绍一下我自己")
            self.assertAnyKeywordInResponse(
                resp,
                [[user["name"]], [str(user["age"])], [user["region"]], [user["job"]]],
                case_sensitive=False,
            )
            self.wait_for_sync(2)


class TestComplexScenarioIncrementalInfo(BaseOpenClawCLITest):
    """
    复杂场景2：增量信息添加
    测试目标：验证增量信息添加
    """

    def test_incremental_info(self):
        """增量信息添加测试"""
        steps = [
            "我叫增量测试用户",
            "我今年33岁",
            "我住在华中区",
            "我的职业是架构师",
            "我喜欢编程和阅读",
            "我擅长Python和Go语言",
            "我有10年工作经验",
        ]

        self.logger.info("分多次添加用户信息...")
        for i, step in enumerate(steps, 1):
            self.logger.info(f"[{i}/{len(steps)}] 添加: {step}")
            self.send_and_log(step)
            self.wait_for_sync()

        self.logger.info("\n[最终验证] 汇总所有信息")
        resp = self.send_and_log(
            "请详细介绍一下我，包括姓名、年龄、地区、职业、兴趣爱好、技能和工作经验"
        )

        self.assertAnyKeywordInResponse(
            resp,
            [
                ["增量测试用户"],
                ["33", "三十三"],
                ["华中"],
                ["架构师"],
                ["编程", "阅读"],
                ["Python", "Go"],
                ["10", "十年"],
            ],
            case_sensitive=False,
        )


class TestComplexScenarioSpecialCharacters(BaseOpenClawCLITest):
    """
    复杂场景3：特殊字符和边界情况
    测试目标：验证特殊字符处理
    """

    def test_special_characters(self):
        """特殊字符和边界情况测试"""
        special_messages = [
            "我叫测试-特殊字符@#$%^&*()",
            "我的备注是：测试'引号\"和\\反斜杠",
            "我的爱好是：🎵音乐、🎨绘画、📚阅读（emoji测试）",
            "我的地址是：测试换行\n第二行\n第三行",
        ]

        for msg in special_messages:
            self.logger.info(f"测试信息: {repr(msg)}")
            self.send_and_log(msg)
            self.wait_for_sync()

        self.logger.info("\n[验证特殊字符记忆]")
        resp = self.send_and_log("请告诉我关于我的所有信息，包括名字、备注、爱好和地址")

        self.assertAnyKeywordInResponse(
            resp, [["测试-特殊字符"], ["音乐", "绘画", "阅读"], ["测试换行"]], case_sensitive=False
        )
