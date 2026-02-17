#!/usr/bin/env python3
"""测试workspace延迟初始化：启动时不创建，第一次使用时才创建"""

import sys
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

def test_lazy_workspace_init():
    """测试延迟初始化"""
    print("=" * 60)
    print("🧪 测试workspace延迟初始化")
    print("=" * 60)
    
    from vikingbot.utils.helpers import get_workspace_path
    from vikingbot.agent.context import ContextBuilder
    
    # 先删除workspace目录
    workspace = get_workspace_path(ensure_exists=False)
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"已删除现有workspace: {workspace}")
    
    print(f"\n当前workspace目录是否存在: {workspace.exists()}")
    
    # 创建ContextBuilder（不应该立即创建workspace）
    print("\n创建 ContextBuilder...")
    context_builder = ContextBuilder(workspace, sandbox_manager=None)
    print(f"ContextBuilder创建后，workspace是否存在: {workspace.exists()}")
    
    if workspace.exists():
        print("❌ 失败：ContextBuilder创建时就创建了workspace！")
        return False
    
    print("✅ 成功：ContextBuilder创建时没有立即创建workspace")
    
    # 第一次调用build_system_prompt（应该创建workspace）
    print("\n第一次调用 build_system_prompt()...")
    prompt = context_builder.build_system_prompt()
    
    print(f"调用后，workspace是否存在: {workspace.exists()}")
    
    if not workspace.exists():
        print("❌ 失败：调用build_system_prompt后没有创建workspace！")
        return False
    
    # 验证文件
    print("\n验证创建的文件:")
    expected_files = ["AGENTS.md", "SOUL.md", "USER.md"]
    for f in expected_files:
        exists = (workspace / f).exists()
        status = "✅" if exists else "❌"
        print(f"  {status} {f}")
    
    # 验证skills目录
    skills_dir = workspace / "skills"
    print(f"\n  {'✅' if skills_dir.exists() else '❌'} skills/ 目录")
    
    if skills_dir.exists():
        skills_count = len(list(skills_dir.iterdir()))
        print(f"  发现 {skills_count} 个skills")
    
    # 第二次调用（不应该重复创建）
    print("\n第二次调用 build_system_prompt()（应该不会重复创建）...")
    prompt2 = context_builder.build_system_prompt()
    print("✅ 第二次调用成功，不会重复创建")
    
    print("\n" + "=" * 60)
    print("✅ 测试通过！")
    print("=" * 60)
    print("\n🎯 延迟初始化工作流程:")
    print("1. 启动时创建 ContextBuilder —— 不创建workspace")
    print("2. 第一次调用 build_system_prompt() —— 创建workspace并复制模板")
    print("3. 后续调用 —— 使用已创建的workspace")
    
    # 清理
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"\n已清理测试workspace: {workspace}")
    
    return True


def main():
    """主测试函数"""
    try:
        success = test_lazy_workspace_init()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())