"""
Pytest 配置文件 - 添加测试报告中文描述和环境信息
"""

import datetime
import platform
import subprocess


def get_openclaw_version():
    """获取 OpenClaw 版本"""
    try:
        result = subprocess.run(
            ["openclaw", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return "Unknown"


def get_openviking_version():
    """获取 OpenViking 版本"""
    # Method 1: Try to import openviking module directly
    try:
        import openviking

        version = getattr(openviking, "__version__", None)
        if version and version != "0.0.0+unknown":
            return version
    except Exception:
        pass

    # Method 2: Try to use ov CLI
    try:
        result = subprocess.run(["ov", "--version"], capture_output=True, text=True, timeout=10)
        version = result.stdout.strip()
        if version and version != "0.0.0+unknown":
            return version
    except Exception:
        pass

    # Method 3: Try to get version from pip show
    try:
        result = subprocess.run(
            ["pip", "show", "openviking"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split("\n"):
            if line.startswith("Version:"):
                version = line.split(":", 1)[1].strip()
                if version and version != "0.0.0+unknown":
                    return version
    except Exception:
        pass

    # Method 4: Try to get version from git
    try:
        import os

        project_dir = os.environ.get("PROJECT_DIR", "/root/project/OpenViking")
        if os.path.exists(os.path.join(project_dir, ".git")):
            result = subprocess.run(
                ["git", "describe", "--tags", "--always"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = result.stdout.strip()
            if version:
                return f"dev-{version}"
    except Exception:
        pass

    return "Unknown"


def pytest_html_report_title(report):
    """自定义报告标题"""
    report.title = "OpenClaw + OpenViking 端到端自动化测试报告"


def pytest_html_results_summary(prefix, summary, postfix):
    """自定义报告摘要 - 添加环境信息和测试说明"""
    openclaw_version = get_openclaw_version()
    openviking_version = get_openviking_version()
    test_date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os_info = f"{platform.system()} {platform.release()}"

    prefix.extend(
        [
            '<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 8px; margin-bottom: 20px;">',
            '<h1 style="color: white; margin: 0; font-size: 28px;">OpenClaw + OpenViking 端到端自动化测试报告</h1>',
            '<p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0; font-size: 16px;">验证记忆读写功能的完整性与可靠性</p>',
            "</div>",
            '<div style="background: #f8f9fa; padding: 15px; border-radius: 6px; margin-bottom: 20px;">',
            '<h3 style="margin-top: 0; color: #333;">📊 环境信息</h3>',
            '<table style="width: 100%; border-collapse: collapse; margin-top: 10px;">',
            '<tr><td style="padding: 8px; border-bottom: 1px solid #ddd; width: 20%;"><strong>📋 项目名称</strong></td><td style="padding: 8px; border-bottom: 1px solid #ddd;">OpenClaw + OpenViking 端到端自动化测试</td></tr>',
            f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>📅 测试日期</strong></td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{test_date}</td></tr>',
            f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>💻 操作系统</strong></td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{os_info}</td></tr>',
            f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>🦞 OpenClaw 版本</strong></td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{openclaw_version}</td></tr>',
            f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd;"><strong>🧠 OpenViking 版本</strong></td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{openviking_version}</td></tr>',
            '<tr><td style="padding: 8px;"><strong>🔗 测试方式</strong></td><td style="padding: 8px;">OpenClaw CLI (--session-id)</td></tr>',
            "</table>",
            "</div>",
            '<h2 style="color: #4a90e2; border-bottom: 2px solid #4a90e2; padding-bottom: 10px;">📖 测试说明</h2>',
            '<div style="background: #f8f9fa; padding: 15px; border-radius: 6px; margin: 15px 0;">',
            '<p style="margin: 0 0 10px 0; font-size: 14px;">本测试验证 OpenClaw 与 OpenViking 之间的核心交互功能，包括：</p>',
            '<ul style="margin: 0; padding-left: 20px; font-size: 14px;">',
            '<li style="margin: 5px 0;">✅ 记忆结构化写入验证</li>',
            '<li style="margin: 5px 0;">✅ 记忆读取/更新/删除验证</li>',
            '<li style="margin: 5px 0;">✅ assemble() 历史组装回放验证</li>',
            '<li style="margin: 5px 0;">✅ compact() 压缩归档验证</li>',
            '<li style="margin: 5px 0;">✅ 跨 session recall 记忆检索注入验证</li>',
            '<li style="margin: 5px 0;">✅ memory_recall 显式搜索验证</li>',
            '<li style="margin: 5px 0;">✅ ov_archive_expand 展开验证</li>',
            '<li style="margin: 5px 0;">✅ session 对话上下文隔离验证</li>',
            "</ul>",
            "</div>",
            '<hr style="margin: 25px 0; border: none; border-top: 1px solid #e0e0e0;">',
        ]
    )


def pytest_html_results_table_header(cells):
    """自定义结果表格表头"""
    cells.insert(2, '<th style="width: 35%;">📝 测试描述</th>')


def pytest_html_results_table_row(report, cells):
    """自定义结果表格行 - 添加中文测试描述"""
    description = "暂无描述"

    test_descriptions = {
        "test_memory_write_basic_info": "测试组A：写入小明的基本信息（姓名、年龄、地区、职业），验证记忆写入完整性与正确性",
        "test_memory_write_rich_info": "测试组B：写入小红的详细信息（姓名、年龄、地址、职业、喜好、生日），验证多维度记忆存储",
        "test_memory_read_verify": "逐项验证记忆读取功能（姓名、年龄、地区、职业），确保信息准确读取",
        "test_memory_update_verify": "验证记忆更新功能（年龄、职业、地址），确保更新正确生效",
        "test_memory_delete_verify": "验证记忆删除功能，写入临时信息后删除并验证",
        "test_memory_persistence_group_a": "跨会话读取验证-组A：我喜欢吃樱桃，日常喜欢喝美式咖啡，验证记忆持久化存储",
        "test_memory_persistence_group_b": "跨会话读取验证-组B：我喜欢吃芒果，日常喜欢喝拿铁咖啡，验证记忆持久化存储",
        "test_memory_persistence_group_c": "跨会话读取验证-组C：我喜欢吃草莓，日常喜欢喝抹茶拿铁，验证记忆持久化存储",
        "test_memory_update_overwrite_group_a": "更新覆盖验证-组A：初始信息30岁→更新为31岁+生日8月，验证旧记忆被覆盖",
        "test_memory_update_overwrite_group_b": "更新覆盖验证-组B：初始信息26岁→更新为27岁+生日11月，验证旧记忆被覆盖",
        "test_memory_update_overwrite_group_c": "更新覆盖验证-组C：初始信息32岁→更新为33岁+生日5月，验证旧记忆被覆盖",
        "test_skill_experience_group_a": "技能经验沉淀-组A：销售数据查询参数格式验证，错误→正确→简化，验证经验记忆",
        "test_skill_experience_group_b": "技能经验沉淀-组B：销售数据查询参数格式验证，错误→正确→简化，验证经验记忆",
        "test_skill_experience_group_c": "技能经验沉淀-组C：销售数据查询参数格式验证，错误→正确→简化，验证经验记忆",
        "test_skill_log_group_a": "技能日志验证-组A：查询销售数据后检查OpenClaw日志，验证memory-openviking inject记录",
        "test_skill_log_group_b": "技能日志验证-组B：查询销售数据后检查OpenClaw日志，验证memory-openviking inject记录",
        "test_skill_log_group_c": "技能日志验证-组C：查询销售数据后检查OpenClaw日志，验证memory-openviking inject记录",
        "test_long_term_target_group_a": "长程记忆目标-组A：插入50轮干扰对话后验证核心目标不丢失（销售数据库优化方案）",
        "test_long_term_target_group_b": "长程记忆目标-组B：插入50轮干扰对话后验证核心目标不丢失（OpenClaw安装适配）",
        "test_long_term_target_group_c": "长程记忆目标-组C：插入50轮干扰对话后验证核心目标不丢失（OpenViking记忆模块）",
        "test_summary_generation_group_a": "长程总结生成-组A：OpenViking自动化测试平台项目，整合背景+讨论+闲聊生成完整总结",
        "test_summary_generation_group_b": "长程总结生成-组B：OpenClaw跨平台适配项目，整合背景+讨论+闲聊生成完整总结",
        "test_summary_generation_group_c": "长程总结生成-组C：OpenViking记忆优化项目，整合背景+讨论+闲聊生成完整总结",
        "test_auto_session_basic": "自动Session ID测试：使用自动生成的session_id进行基本记忆写入和读取，验证Session ID自动管理功能",
        "test_custom_session_prefix": "自定义Session ID测试：使用自定义前缀的session_id进行记忆写入和读取，验证自定义Session功能",
        "test_memory_v2_preferences": "Memory V2偏好设置记忆：对话写入编程语言/编辑器/咖啡偏好→commit→验证preferences目录下新增记忆文件",
        "test_memory_v2_entities": "Memory V2实体信息记忆：对话写入姓名/年龄/职业/公司/住址→commit→验证entities目录下新增记忆文件",
        "test_memory_v2_events": "Memory V2事件记录记忆：对话写入技术分享会事件→commit→验证events目录下新增记忆文件",
        "test_memory_v2_profile": "Memory V2用户画像记忆：对话写入技术负责人画像→commit→验证profile.md文件新增或更新",
        "test_memory_v2_skills": "Memory V2技能记忆(Agent scope)：对话写入Docker/K8s/CI-CD技能→commit→验证agent memories/skills目录下新增记忆文件",
        "test_memory_v2_tools": "Memory V2工具记忆(Agent scope)：对话写入Git/Jenkins/Prometheus工具使用→commit→验证agent memories/tools目录下新增记忆文件",
        "test_assemble_replays_archive_summary": "assemble()历史组装验证：写入信息→commit生成archive→新session→验证archive summary被加载回放",
        "test_compact_produces_archive_on_threshold": "compact()压缩归档验证：持续对话超阈值→验证archive生成+记忆文件生成+上下文被压缩",
        "test_cross_session_recall_works": "跨session recall验证：session A写入→commit+记忆提取→session B查询→验证recall注入+记忆文件存在",
        "test_memory_recall_explicit_search": "memory_recall显式搜索验证：写入独特信息→commit+记忆提取→新session用模糊提示触发显式搜索→验证搜索结果",
        "test_archive_expand_restores_details": "ov_archive_expand展开验证：写入含细节信息→commit生成archive→询问细节触发展开→验证原始细节被还原",
        "test_session_isolation_no_cross_contamination": "session对话上下文隔离验证：session A写入甲信息→session B写入乙信息→分别查询对话上下文→验证互不干扰",
    }

    for test_name, desc in test_descriptions.items():
        if test_name in report.nodeid:
            description = desc
            break

    cells.insert(
        2,
        f'<td style="max-width: 450px; word-wrap: break-word; font-size: 13px; line-height: 1.5;">{description}</td>',
    )
