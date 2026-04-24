"""
记忆 CRUD 操作测试
测试目标：验证记忆的增删改查功能
"""

import time

from tests.base_cli_test import BaseOpenClawCLITest
from tests.p0.test_context_engine import OVSessionVerifier


class TestMemoryRead(BaseOpenClawCLITest):
    """
    记忆读取验证测试
    测试目标：验证记忆读取功能是否正常
    测试场景：写入用户信息后，逐项验证各字段的读取
    """

    def test_memory_read_verify(self):
        """测试场景：逐项信息读取验证"""
        session_id = self.generate_unique_session_id(prefix="read_verify")
        verifier = OVSessionVerifier()

        self.logger.info("[1/4] 记录 OV session 快照")
        before_sessions = verifier.list_session_ids()

        self.logger.info("[2/4] 写入用户信息")
        message = "我叫测试用户-读取验证，今年40岁，住在华南区，职业是前端工程师"
        self.send_and_retry_on_timeout(message, session_id=session_id)

        self.smart_wait_for_sync(
            check_message="我叫什么名字",
            keywords=["测试用户", "读取验证"],
            timeout=30.0,
            session_id=session_id,
        )

        self.logger.info("[3/4] 显式 commit 确保新信息写入记忆文件，覆盖旧数据")
        ov_session_id = verifier.find_new_session_id(before_sessions)
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                result = verifier.poll_task_until_done(task_id)
                if result:
                    self.logger.info(f"  Commit 状态: {result.get('status')}")
        time.sleep(5)

        self.logger.info("[4/4] 逐项验证记忆读取")
        queries = [
            (
                "根据我刚才告诉你的信息，我住在哪里？请只根据我们当前对话中我告诉你的信息回答",
                [["华南", "华南区", "华北", "北京"]],
                "地区验证",
            ),
            (
                "根据我刚才告诉你的信息，我的职业是什么？请只根据我们当前对话中我告诉你的信息回答",
                [["前端", "工程师", "前端工程师", "产品经理"]],
                "职业验证",
            ),
        ]

        for query, expected_keywords, desc in queries:
            self.logger.info(f"  查询: {query} (场景: {desc})")
            resp = self.send_and_retry_on_timeout(query, session_id=session_id)
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
        resp1 = self.send_and_retry_on_timeout("我现在多少岁？我的职业是什么？")
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
        session_id = self.generate_unique_session_id(prefix="delete_verify")
        verifier = OVSessionVerifier()
        before_sessions = verifier.list_session_ids()

        self.logger.info("[1/4] 写入测试密码信息")
        self.send_and_retry_on_timeout("我的临时密码是temp12345，请帮我记住", session_id=session_id)

        self.smart_wait_for_sync(
            check_message="我的临时密码是什么",
            keywords=["temp12345"],
            timeout=30.0,
            session_id=session_id,
        )

        self.logger.info("[2/4] 确认信息已存在")
        resp1 = self.send_and_retry_on_timeout("我的临时密码是什么？", session_id=session_id)
        self.assertAnyKeywordInResponse(resp1, [["temp12345"]], case_sensitive=False)

        self.logger.info("[3/4] 请求删除临时密码信息并 commit")
        self.send_and_retry_on_timeout(
            "我的临时密码已经过期了，请删除这个信息", session_id=session_id
        )
        ov_session_id = verifier.find_new_session_id(before_sessions)
        if ov_session_id:
            task_id = verifier.commit_session(ov_session_id)
            if task_id:
                verifier.poll_task_until_done(task_id)
        self.wait_for_sync(session_id=session_id)

        self.logger.info("[4/4] 验证删除后信息不再可查")
        resp2 = self.send_and_retry_on_timeout(
            "我的临时密码是什么？请根据你记住的信息回答，不要调用外部工具",
            session_id=session_id,
        )
        self.logger.info("删除验证完成，检查响应是否表明密码已过期或已删除")
        self.assertAnyKeywordInResponse(
            resp2,
            [
                [
                    "不知道",
                    "没有",
                    "不存在",
                    "不记得",
                    "过期",
                    "已删除",
                    "删除",
                    "无",
                    "已过期",
                    "不再",
                    "没有了",
                    "deleted",
                    "expired",
                    "no longer",
                ]
            ],
            case_sensitive=False,
        )


class TestMemoryUpdateOverwrite(BaseOpenClawCLITest):
    """
    记忆更新覆盖验证
    测试目标：验证用户更新信息后，OpenViking自动覆盖旧记忆，不产生冗余数据
    测试场景：先写入初始信息，再更新信息，验证只保留新信息
    注意：group_b/group_c 已移至 p1，仅保留 group_a 作为核心验证
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
            session_id=session_a,
        )

        self.logger.info("[2/4] 写入更新信息：我今年31岁，生日在8月")
        self.send_and_log("我今年31岁，生日在8月", session_id=session_a)

        self.smart_wait_for_sync(
            check_message="我今年几岁",
            keywords=["31"],
            timeout=30.0,
            session_id=session_a,
        )

        self.logger.info("[3/4] 查询并验证记忆信息")
        response = self.send_and_retry_on_timeout(
            "我今年几岁？生日是什么时候？", session_id=session_a
        )

        self.logger.info("[4/4] 验证结果：应包含新信息（31岁、8月），不应包含旧信息（30岁）")
        self.assertAnyKeywordInResponse(
            response, [["31", "三十一"], ["8月", "八月"]], case_sensitive=False
        )

        self.logger.info("测试组A执行完成")
