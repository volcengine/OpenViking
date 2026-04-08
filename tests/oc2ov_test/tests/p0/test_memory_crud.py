"""
记忆 CRUD 操作测试
测试目标：验证记忆的增删改查功能
"""

from tests.base_cli_test import BaseOpenClawCLITest


class TestMemoryRead(BaseOpenClawCLITest):
    """
    记忆读取验证测试
    测试目标：验证记忆读取功能是否正常
    测试场景：写入用户信息后，逐项验证各字段的读取
    """

    def test_memory_read_verify(self):
        """测试场景：逐项信息读取验证"""
        self.logger.info("[1/2] 先写入用户信息")
        message = "我叫测试用户-读取验证，今年40岁，住在华南区，职业是前端工程师"
        self.send_and_log(message)

        self.smart_wait_for_sync(
            check_message="我叫什么名字",
            keywords=["测试用户", "读取验证"],
            timeout=30.0,
        )

        self.logger.info("[2/2] 逐项验证记忆读取")
        queries = [
            ("我几岁了？", [["40", "四十"]], "年龄验证"),
            ("我住在哪里？", [["华南"]], "地区验证"),
            ("我的职业是什么？", [["前端", "工程师"]], "职业验证"),
        ]

        for query, expected_keywords, desc in queries:
            self.logger.info(f"  查询: {query} (场景: {desc})")
            resp = self.send_and_log(query)
            self.assertAnyKeywordInResponse(resp, expected_keywords, case_sensitive=False)


class TestMemoryUpdate(BaseOpenClawCLITest):
    """
    记忆更新验证测试
    测试目标：验证记忆更新功能是否正常
    测试场景：先写入初始信息，然后更新年龄、职业和地址，验证更新是否生效
    """

    def test_memory_update_verify(self):
        """测试场景：信息更新与验证"""
        self.logger.info("[1/4] 写入初始信息")
        self.send_and_log("我叫小李，今年28岁，住在西南区，职业是数据分析师")

        self.smart_wait_for_sync(
            check_message="我今年多少岁",
            keywords=["28"],
            timeout=30.0,
        )

        self.logger.info("[2/4] 更新信息：年龄改为29岁，职业改为数据科学家")
        self.send_and_log("我现在29岁了，我的职业从数据分析师变成了数据科学家")

        self.smart_wait_for_sync(
            check_message="我现在多少岁",
            keywords=["29"],
            timeout=30.0,
        )

        self.logger.info("[3/4] 验证更新是否生效")
        resp1 = self.send_and_log("我现在多少岁？我的职业是什么？")
        self.assertAnyKeywordInResponse(
            resp1, [["29", "二十九"], ["数据科学家"]], case_sensitive=False
        )

        self.logger.info("[4/4] 进一步更新地址信息")
        self.send_and_log("我搬到了西北区")

        self.smart_wait_for_sync(
            check_message="我现在住在哪里",
            keywords=["西北"],
            timeout=30.0,
        )


class TestMemoryDelete(BaseOpenClawCLITest):
    """
    记忆删除验证测试
    测试目标：验证记忆删除功能是否正常
    测试场景：写入密码信息，验证存在后请求删除，再验证信息已被删除
    """

    def test_memory_delete_verify(self):
        """测试场景：信息删除与验证"""
        self.logger.info("[1/3] 写入测试密码信息")
        self.send_and_log("我的临时密码是temp12345，请帮我记住")

        self.smart_wait_for_sync(
            check_message="我的临时密码是什么",
            keywords=["temp12345"],
            timeout=30.0,
        )

        self.logger.info("[2/3] 确认信息已存在")
        resp1 = self.send_and_log("我的临时密码是什么？")
        self.assertAnyKeywordInResponse(resp1, [["temp12345"]], case_sensitive=False)

        self.logger.info("[3/3] 请求删除临时密码信息")
        self.send_and_log("我的临时密码已经过期了，请删除这个信息")
        self.wait_for_sync()
        resp2 = self.send_and_log("我的临时密码是什么？")
        self.logger.info("删除验证完成，检查响应是否不包含原密码信息")
        self.assertAnyKeywordInResponse(resp2, [["不知道", "没有", "不存在", "不记得", "过期", "已删除"]], case_sensitive=False)


class TestMemoryUpdateOverwrite(BaseOpenClawCLITest):
    """
    记忆更新覆盖验证
    测试目标：验证用户更新信息后，OpenViking自动覆盖旧记忆，不产生冗余数据
    测试场景：先写入初始信息，再更新信息，验证只保留新信息
    """

    def test_memory_update_overwrite_group_a(self):
        """测试组A：初始信息——我今年30岁；更新信息——我今年31岁，生日在8月"""
        self.logger.info("[1/4] 测试组A - 写入初始信息：我今年30岁")
        session_a = self.generate_unique_session_id(prefix="update_overwrite_a")

        self.send_and_log("我今年30岁", session_id=session_a)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["30"],
            timeout=30.0,
        )

        self.logger.info("[2/4] 写入更新信息：我今年31岁，生日在8月")
        self.send_and_log("我今年31岁，生日在8月", session_id=session_a)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["31"],
            timeout=30.0,
        )

        self.logger.info("[3/4] 查询并验证记忆信息")
        response = self.send_and_log("我今年几岁？生日是什么时候？", session_id=session_a)

        self.logger.info("[4/4] 验证结果：应包含新信息（31岁、8月），不应包含旧信息（30岁）")
        self.assertAnyKeywordInResponse(
            response, [["31", "三十一"], ["8月", "八月"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")

    def test_memory_update_overwrite_group_b(self):
        """测试组B：初始信息——我今年26岁；更新信息——我今年27岁，生日在11月"""
        self.logger.info("[1/4] 测试组B - 写入初始信息：我今年26岁")
        session_b = self.generate_unique_session_id(prefix="update_overwrite_b")

        self.send_and_log("我今年26岁", session_id=session_b)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["26"],
            timeout=30.0,
        )

        self.logger.info("[2/4] 写入更新信息：我今年27岁，生日在11月")
        self.send_and_log("我今年27岁，生日在11月", session_id=session_b)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["27"],
            timeout=30.0,
        )

        self.logger.info("[3/4] 查询并验证记忆信息")
        response = self.send_and_log("我今年几岁？生日是什么时候？", session_id=session_b)

        self.logger.info("[4/4] 验证结果：应包含新信息（27岁、11月），不应包含旧信息（26岁）")
        self.assertAnyKeywordInResponse(
            response, [["27", "二十七"], ["11月", "十一月"]], case_sensitive=False
        )

        self.logger.info("测试组B执行完成")

    def test_memory_update_overwrite_group_c(self):
        """测试组C：初始信息——我今年32岁；更新信息——我今年33岁，生日在5月"""
        self.logger.info("[1/4] 测试组C - 写入初始信息：我今年32岁")
        session_c = self.generate_unique_session_id(prefix="update_overwrite_c")

        self.send_and_log("我今年32岁", session_id=session_c)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["32"],
            timeout=30.0,
        )

        self.logger.info("[2/4] 写入更新信息：我今年33岁，生日在5月")
        self.send_and_log("我今年33岁，生日在5月", session_id=session_c)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["33"],
            timeout=30.0,
        )

        self.logger.info("[3/4] 查询并验证记忆信息")
        response = self.send_and_log("我今年几岁？生日是什么时候？", session_id=session_c)

        self.logger.info("[4/4] 验证结果：应包含新信息（33岁、5月），不应包含旧信息（32岁）")
        self.assertAnyKeywordInResponse(
            response, [["33", "三十三"], ["5月", "五月"]], case_sensitive=False
        )

        self.logger.info("测试组C执行完成")
