"""
断言工具模块
提供关键词匹配和文本相似度两种断言方式
"""

import difflib
import logging
from typing import List, Union

logger = logging.getLogger(__name__)


class AssertionHelper:
    """
    断言辅助类
    """

    @staticmethod
    def extract_response_text(response: dict) -> str:
        """
        从响应中提取文本内容
        支持多种响应格式
        """
        if isinstance(response, str):
            return response

        if isinstance(response, dict):
            if "output" in response:
                return str(response["output"])
            if "message" in response:
                return str(response["message"])
            if "content" in response:
                return str(response["content"])
            if "text" in response:
                return str(response["text"])
            if "result" in response and isinstance(response["result"], dict):
                result = response["result"]
                payloads = result.get("payloads")
                if isinstance(payloads, list) and len(payloads) > 0:
                    texts = []
                    for p in payloads:
                        if isinstance(p, dict) and "text" in p:
                            texts.append(str(p["text"]))
                    if texts:
                        return "\n".join(texts)
                for key in ("output", "text", "content", "message", "response"):
                    if key in result and result[key]:
                        return str(result[key])
                messages = result.get("messages")
                if isinstance(messages, list) and len(messages) > 0:
                    for msg in reversed(messages):
                        if isinstance(msg, dict):
                            role = msg.get("role", "")
                            if role == "assistant":
                                c = msg.get("content", "")
                                if c:
                                    return str(c)
            if "choices" in response and len(response["choices"]) > 0:
                choice = response["choices"][0]
                if isinstance(choice, dict):
                    if "message" in choice:
                        msg = choice["message"]
                        if isinstance(msg, dict) and "content" in msg:
                            return str(msg["content"])
                        return str(msg)
                    elif "text" in choice:
                        return str(choice["text"])
                return str(choice)
            if "error" in response:
                return str(response["error"])

        logger.warning("extract_response_text: no text found in response, returning empty string")
        return ""

    @staticmethod
    def assert_keywords_in_response(
        response: Union[dict, str],
        keywords: List[str],
        require_all: bool = True,
        case_sensitive: bool = False,
    ) -> bool:
        """
        断言响应中包含指定关键词

        Args:
            response: 响应内容，可以是字典或字符串
            keywords: 要匹配的关键词列表
            require_all: 是否要求所有关键词都必须出现，默认 True
            case_sensitive: 是否区分大小写，默认 False

        Returns:
            bool: 断言是否通过
        """
        text = AssertionHelper.extract_response_text(response)

        if not case_sensitive:
            text = text.lower()
            keywords = [kw.lower() for kw in keywords]

        found_keywords = []
        missing_keywords = []

        for keyword in keywords:
            if keyword in text:
                found_keywords.append(keyword)
            else:
                missing_keywords.append(keyword)

        logger.info(f"找到的关键词: {found_keywords}")
        if missing_keywords:
            logger.warning(f"缺失的关键词: {missing_keywords}")

        if require_all:
            success = len(missing_keywords) == 0
        else:
            success = len(found_keywords) > 0

        if success:
            logger.info("✅ 关键词断言通过")
        else:
            logger.error("❌ 关键词断言失败")

        return success

    @staticmethod
    def calculate_similarity(text1: str, text2: str) -> float:
        """
        计算两个文本的相似度

        Args:
            text1: 文本1
            text2: 文本2

        Returns:
            float: 相似度，范围 0.0 - 1.0
        """
        return difflib.SequenceMatcher(None, text1, text2).ratio()

    @staticmethod
    def assert_similarity(
        response: Union[dict, str], expected_text: str, min_similarity: float = 0.6
    ) -> bool:
        """
        断言响应文本与期望文本的相似度

        Args:
            response: 响应内容
            expected_text: 期望的文本
            min_similarity: 最小相似度阈值，默认 0.6

        Returns:
            bool: 断言是否通过
        """
        actual_text = AssertionHelper.extract_response_text(response)
        similarity = AssertionHelper.calculate_similarity(actual_text, expected_text)

        logger.info(f"期望文本: {expected_text[:100]}...")
        logger.info(f"实际文本: {actual_text[:100]}...")
        logger.info(f"相似度: {similarity:.2%}")

        success = similarity >= min_similarity

        if success:
            logger.info(f"✅ 相似度断言通过 (>= {min_similarity:.0%})")
        else:
            logger.error(f"❌ 相似度断言失败 (期望 >= {min_similarity:.0%}, 实际 {similarity:.0%})")

        return success

    @staticmethod
    def assert_any_keyword_in_response(
        response: Union[dict, str], keyword_groups: List[List[str]], case_sensitive: bool = False
    ) -> bool:
        """
        断言响应中包含任意一组关键词中的任意一个

        Args:
            response: 响应内容
            keyword_groups: 关键词组列表，每组中任意一个匹配即可
            case_sensitive: 是否区分大小写

        Returns:
            bool: 断言是否通过
        """
        text = AssertionHelper.extract_response_text(response)

        if not case_sensitive:
            text = text.lower()

        for i, group in enumerate(keyword_groups):
            for keyword in group:
                kw = keyword if case_sensitive else keyword.lower()
                if kw in text:
                    logger.info(f"✅ 在第 {i + 1} 组中找到关键词: {keyword}")
                    return True

        logger.error("❌ 未在任何关键词组中找到匹配")
        return False
