#!/usr/bin/env python3
"""测试内置skills是否被复制到workspace"""

import sys
import shutil
from pathlib import Path

# 添加项目根目录到Python路径
sys.path.insert(0, str(Path(__file__).parent))

def test_builtin_skills_copy():
    """测试内置skills复制"""
    print("=" * 60)
    print("🧪 测试内置skills复制")
    print("=" * 60)
    
    from vikingbot.utils.helpers import get_workspace_path, ensure_workspace_templates
    from vikingbot.agent.skills import BUILTIN_SKILLS_DIR
    
    print(f"\n内置skills目录: {BUILTIN_SKILLS_DIR}")
    print(f"内置skills目录是否存在: {BUILTIN_SKILLS_DIR.exists()}")
    
    # 列出内置skills
    if BUILTIN_SKILLS_DIR.exists():
        builtin_skills = [d.name for d in BUILTIN_SKILLS_DIR.iterdir() if d.is_dir() and d.name != "README.md"]
        print(f"发现 {len(builtin_skills)} 个内置skills: {builtin_skills}")
    
    # 先删除workspace目录
    workspace = get_workspace_path(ensure_exists=False)
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"\n已删除现有workspace: {workspace}")
    
    # 调用ensure_workspace_templates
    print("\n调用 ensure_workspace_templates()...")
    ensure_workspace_templates(workspace)
    
    # 检查skills目录
    skills_dir = workspace / "skills"
    print(f"\nworkspace/skills目录是否存在: {skills_dir.exists()}")
    
    if skills_dir.exists():
        copied_skills = [d.name for d in skills_dir.iterdir() if d.is_dir()]
        print(f"复制了 {len(copied_skills)} 个skills到workspace: {copied_skills}")
        
        # 检查每个skill是否有SKILL.md
        print(f"\n检查每个skill的SKILL.md文件:")
        for skill_name in copied_skills:
            skill_dir = skills_dir / skill_name
            skill_md = skill_dir / "SKILL.md"
            status = "✅" if skill_md.exists() else "❌"
            print(f"  {status} {skill_name}/SKILL.md")
    
    # 验证
    print("\n" + "-" * 60)
    if skills_dir.exists() and len(list(skills_dir.iterdir())) > 0:
        print("✅ 内置skills复制成功！")
    else:
        print("❌ 内置skills复制失败！")
        return False
    
    print("\n" + "=" * 60)
    print("✅ 测试通过！")
    print("=" * 60)
    
    # 清理
    if workspace.exists():
        shutil.rmtree(workspace)
        print(f"\n已清理测试workspace: {workspace}")
    
    return True


def main():
    """主测试函数"""
    try:
        success = test_builtin_skills_copy()
        return 0 if success else 1
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())