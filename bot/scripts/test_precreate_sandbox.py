#!/usr/bin/env python3
"""测试对话时提前创建sandbox目录"""

import sys
import shutil
import asyncio
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))


async def test_precreate_sandbox():
    """测试提前创建sandbox"""
    print("=" * 60)
    print("🧪 测试对话时提前创建sandbox")
    print("=" * 60)
    
    from vikingbot.config.schema import SandboxConfig, Config
    from vikingbot.sandbox.manager import SandboxManager
    from vikingbot.utils.helpers import get_workspace_path
    
    # 先清理
    workspace = get_workspace_path(ensure_exists=False)
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"已清理workspace: {workspace}")
    
    # 创建配置
    config = Config()
    sandbox_config = SandboxConfig(
        enabled=True,
        backend="srt",
        mode="per-session"
    )
    
    # 创建sandbox manager
    sandbox_manager = SandboxManager(sandbox_config, workspace)
    
    # 测试session key
    test_session_key = "feishu:test:test_chat_123"
    
    print(f"\n测试session key: {test_session_key}")
    
    # 查看workspace目录
    print(f"\n调用get_sandbox之前，workspace目录:")
    if workspace.exists():
        for item in workspace.iterdir():
            print(f"  - {item.name}")
    else:
        print("  (空)")
    
    # 调用get_sandbox（模拟对话时的行为）
    print(f"\n调用 get_sandbox({test_session_key})...")
    sandbox = await sandbox_manager.get_sandbox(test_session_key)
    
    # 查看workspace目录
    print(f"\n调用get_sandbox之后，workspace目录:")
    if workspace.exists():
        for item in workspace.iterdir():
            print(f"  - {item.name}")
    else:
        print("  (空)")
    
    # 验证
    expected_sandbox_dir = test_session_key.replace(":", "_")
    sandbox_path = workspace / expected_sandbox_dir
    
    print(f"\n期望的sandbox目录: {expected_sandbox_dir}")
    print(f"sandbox目录是否存在: {sandbox_path.exists()}")
    
    if sandbox_path.exists() and sandbox_path.is_dir():
        print("✅ 成功！sandbox目录已创建")
        print(f"   路径: {sandbox_path}")
        
        # 查看sandbox目录内容
        print(f"\nsandbox目录内容:")
        for item in sandbox_path.iterdir():
            print(f"  - {item.name}")
    else:
        print("❌ 失败！sandbox目录未创建")
        return False
    
    print("\n" + "=" * 60)
    print("✅ 测试通过！")
    print("=" * 60)
    print("\n🎯 新行为:")
    print("1. 对话开始时 → 立即调用 get_sandbox()")
    print("2. get_sandbox() → 创建会话特定的sandbox目录")
    print("3. 工具执行时 → 已经有sandbox目录可用")
    
    # 清理
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"\n已清理测试workspace: {workspace}")
    
    return True


def main():
    """主测试函数"""
    try:
        success = asyncio.run(test_precreate_sandbox())
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())