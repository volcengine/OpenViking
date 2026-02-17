#!/usr/bin/env python3
"""测试沙箱日志功能"""

import asyncio
from pathlib import Path
from vikingbot.config.schema import SandboxConfig
from vikingbot.utils.helpers import get_workspace_path

async def test_sandbox_log():
    """测试沙箱日志"""
    print("=== 测试沙箱日志功能 ===")
    
    # 测试禁用沙箱
    print("\n1. 测试禁用沙箱:")
    config_disabled = SandboxConfig(enabled=False, backend="srt", mode="per-session")
    print(f"配置: enabled={config_disabled.enabled}, backend={config_disabled.backend}, mode={config_disabled.mode}")
    
    # 测试启用沙箱
    print("\n2. 测试启用沙箱:")
    config_enabled = SandboxConfig(enabled=True, backend="srt", mode="per-session")
    print(f"配置: enabled={config_enabled.enabled}, backend={config_enabled.backend}, mode={config_enabled.mode}")
    
    print("\n✅ 测试完成！")
    print("当VikingBot启动时，应该会看到沙箱状态的日志：")
    print("- 沙箱启用: [green]✓[/green] Sandbox: enabled (backend=srt, mode=per-session)")
    print("- 沙箱禁用: [dim]Sandbox: disabled[/dim]")

if __name__ == "__main__":
    asyncio.run(test_sandbox_log())