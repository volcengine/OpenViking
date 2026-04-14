"""
OpenClaw CLI 客户端封装 - 使用 openclaw agent 命令
"""

import json
import logging
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OpenClawCLIClient:
    """
    OpenClaw CLI 客户端
    """

    def __init__(self, session_id: Optional[str] = None):
        """
        初始化客户端
        """
        self.session_id = session_id or "test_session_default"
        self.timeout = 180

    def send_message(
        self, message: str, session_id: Optional[str] = None, agent_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        使用 openclaw agent 命令发送消息
        """
        target_session_id = session_id or self.session_id

        cmd = [
            "openclaw",
            "agent",
            "--session-id",
            target_session_id,
            "--message",
            message,
            "--json",
        ]

        if agent_id:
            cmd.insert(2, "--agent")
            cmd.insert(3, agent_id)

        try:
            logger.info("=" * 80)
            logger.info("📤 使用 CLI 发送请求到 OpenClaw")
            logger.info("=" * 80)
            logger.info(f"Session ID: {target_session_id}")
            logger.info(f"Agent ID: {agent_id or 'default'}")
            logger.info(f"输入消息: {message}")
            logger.info(f"完整命令: {' '.join(cmd)}")

            logger.info(f"⏳ 等待响应 (超时: {self.timeout}秒)...")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)

            logger.info(f"✅ 命令执行完成 - 返回码: {result.returncode}")

            if result.returncode != 0:
                error_msg = f"命令执行失败: {result.stderr}"
                logger.error("=" * 80)
                logger.error(error_msg)
                logger.error("=" * 80)
                return {"error": error_msg, "success": False}

            if not result.stdout.strip():
                error_msg = "命令返回空输出"
                logger.error("=" * 80)
                logger.error(error_msg)
                logger.error("=" * 80)
                return {"error": error_msg, "success": False}

            try:
                response_data = json.loads(result.stdout)
            except json.JSONDecodeError:
                response_text = result.stdout.strip()
                response_data = {"output": response_text, "success": True}

            logger.info("=" * 80)
            logger.info("📥 OpenClaw 响应内容:")
            logger.info("=" * 80)

            if isinstance(response_data, dict):
                logger.info(json.dumps(response_data, ensure_ascii=False, indent=2))
            else:
                logger.info(str(response_data))

            logger.info("=" * 80)

            return response_data

        except subprocess.TimeoutExpired:
            error_msg = f"命令执行超时 (超时: {self.timeout}秒)"
            logger.error("=" * 80)
            logger.error(error_msg)
            logger.error("=" * 80)
            return {"error": error_msg, "success": False}
        except Exception as e:
            error_msg = f"执行命令异常: {str(e)}"
            logger.error("=" * 80)
            logger.error(error_msg)
            logger.error("=" * 80)
            return {"error": error_msg, "success": False}
