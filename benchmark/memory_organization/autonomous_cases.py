from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from benchmark.memory_organization.models import fact_lines


@dataclass(frozen=True, slots=True)
class AutonomousCase:
    case_id: str
    category: str
    schemas: tuple[Any, ...]
    initial_files: dict[str, Any]
    expected_groups: dict[str, tuple[frozenset[str], ...]]
    expected_replacements: dict[str, str]
    messages: tuple[Any, ...] = ()
    additional_facts: dict[str, str] | None = None
    maintenance_review_tokens: int | None = None

    @property
    def expected_facts(self) -> dict[str, str]:
        facts = {
            marker: text
            for memory_file in self.initial_files.values()
            for marker, text in fact_lines(memory_file.content)
        }
        facts.update(self.additional_facts or {})
        return facts


def build_cases() -> list[AutonomousCase]:
    from openviking.message import Message, TextPart
    from openviking.session.memory.dataclass import MemoryFile
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
    from openviking.session.memory.utils.template_utils import TemplateUtils

    registry = MemoryTypeRegistry(load_schemas=True)
    directory_schema = registry.get("entities")
    profile_schema = registry.get("profile")
    preferences_schema = registry.get("preferences")
    if directory_schema is None or profile_schema is None or preferences_schema is None:
        raise RuntimeError("Production entities/profile/preferences schemas are required")

    directory_root = TemplateUtils.render(directory_schema.directory, {"user_space": "default"})
    directory_files = {
        f"{directory_root}/Projects/atlas.md": MemoryFile(
            uri=f"{directory_root}/Projects/atlas.md",
            memory_type="entities",
            content=(
                "# Atlas\nAtlas 是一个软件服务。\n\n## 发布\n"
                "- [F01] 使用分阶段发布。\n"
                "- [F02] 有明确记录的回滚流程。"
            ),
            extra_fields={"category": "Projects", "name": "atlas"},
        ),
        f"{directory_root}/projects/atlas.md": MemoryFile(
            uri=f"{directory_root}/projects/atlas.md",
            memory_type="entities",
            content=(
                "# Atlas\nAtlas 是一个软件服务。\n\n## 归属与运维\n"
                "- [F03] 由平台团队负责。\n"
                "- [F04] 提供健康检查接口。"
            ),
            extra_fields={"category": "projects", "name": "atlas"},
        ),
    }

    profile_root = TemplateUtils.render(profile_schema.directory, {"user_space": "default"})
    profile_uri = f"{profile_root}/{profile_schema.filename_template}"
    profile_content = """# 安德鲁
- [F01] 是一名后端工程师 (as of 2026-08-01)
- [F02] 居住在新加坡 (as of 2026-08-01)
- [F03] 拥有计算机科学硕士学位 (as of 2026-08-01)
- [F04] 已婚 (as of 2026-08-01)
- [F05] 生日是 4 月 18 日 (as of 2026-08-01)
- [F06] 会说英语和普通话 (as of 2026-08-01)
- [F07] 偏好简洁的书面状态更新 (as of 2026-08-01)
- [F08] 喜欢包含具体后续行动的评审意见 (as of 2026-08-01)
- [F09] 小型拉取请求使用 squash 合并 (as of 2026-08-01)
- [F10] 会先运行针对性测试，再运行完整测试套件 (as of 2026-08-01)
- [F11] 比起巧妙抽象，更偏好朴素实现 (as of 2026-08-01)
- [F12] 发布前会记录行为变化 (as of 2026-08-01)
- [F13] 喜欢吃辣味面条 (as of 2026-08-01)
- [F14] 不喜欢苦瓜 (as of 2026-08-01)
- [F15] 喝咖啡不加糖 (as of 2026-08-01)
- [F16] 选择提供素食选项的餐厅 (as of 2026-08-01)
- [F17] 偏好直飞航班 (as of 2026-08-01)
- [F18] 避免乘坐红眼航班 (as of 2026-08-01)
- [F19] 喜欢安静的海滨城市 (as of 2026-08-01)
- [F20] 短途旅行时会轻装出行 (as of 2026-08-01)
- [F21] 喜欢回合制策略游戏 (as of 2026-08-01)
- [F22] 喜欢合作解谜游戏 (as of 2026-08-01)
- [F23] 不喜欢竞技射击游戏 (as of 2026-08-01)
- [F24] 偏好剧情丰富的角色扮演游戏 (as of 2026-08-01)"""
    profile_files = {
        profile_uri: MemoryFile(
            uri=profile_uri,
            memory_type="profile",
            content=profile_content,
            extra_fields={},
        )
    }

    preferences_root = TemplateUtils.render(preferences_schema.directory, {"user_space": "default"})
    oversized_preference_uri = f"{preferences_root}/安德鲁/工作偏好.md"
    status_update_facts = {
        "F25": "偏好状态更新第一行直接说明当前结论和整体进展。",
        "F26": "喜欢状态更新用简短项目符号列出本周期完成事项。",
        "F27": "偏好状态更新将已经完成和仍在进行的工作分开呈现。",
        "F28": "喜欢状态更新为每个进行中事项标注明确负责人。",
        "F29": "偏好状态更新为每个行动项写出预计完成日期。",
        "F30": "喜欢状态更新把已经确认的决定单独放在决定区。",
        "F31": "偏好状态更新把尚未解决的问题单独放在开放问题区。",
        "F32": "喜欢状态更新为关键结论附上可量化的进展证据。",
        "F33": "偏好状态更新明确指出相较上一次更新发生了什么变化。",
        "F34": "喜欢状态更新只保留影响当前进展的必要背景信息。",
        "F35": "偏好状态更新使用一致术语描述相同项目和里程碑。",
        "F36": "喜欢状态更新将阻塞事项放在显眼位置并说明影响。",
        "F37": "偏好状态更新为每个阻塞事项写出解除阻塞的下一步。",
        "F38": "喜欢状态更新在结尾汇总下一周期最重要的三项工作。",
        "F39": "偏好状态更新附上相关任务、文档或结果文件的精确链接。",
        "F40": "喜欢状态更新保持简洁，避免重复已经记录的长篇背景。",
        "F57": "偏好状态更新注明数据统计的时间范围和样本口径。",
        "F58": "喜欢状态更新对风险使用高、中、低三级标识。",
        "F59": "偏好状态更新在需要协助时明确写出请求内容和截止时间。",
        "F60": "喜欢状态更新同时提供人类可读摘要和机器可读附件。",
    }
    test_execution_facts = {
        "F41": "偏好先运行与改动模块直接相关的聚焦测试。",
        "F42": "喜欢聚焦测试通过后再运行受影响子系统的测试套件。",
        "F43": "偏好根据改动风险决定是否继续运行完整测试套件。",
        "F44": "喜欢在测试前先执行快速的静态检查和格式检查。",
        "F45": "偏好缺陷修复附带能够稳定复现原问题的回归测试。",
        "F46": "喜欢每个测试只验证一个清晰的行为结果。",
        "F47": "偏好测试名称直接描述输入条件和预期行为。",
        "F48": "喜欢测试使用固定输入和确定性断言，避免随机波动。",
        "F49": "偏好外部服务测试使用记录好的 mock 数据而不是实时调用。",
        "F50": "喜欢为网络和异步测试设置明确且有限的超时时间。",
        "F51": "偏好并发测试限制工作线程数量以保持结果稳定。",
        "F52": "喜欢在隔离临时目录中运行会生成文件的测试。",
        "F53": "偏好测试失败时输出实际值、预期值和关键上下文。",
        "F54": "喜欢测试保留失败样本的随机种子和输入参数。",
        "F55": "偏好昂贵评估前先运行一个最小冒烟测试。",
        "F56": "喜欢测试命令支持机器可读结果和非交互执行。",
        "F61": "偏好按单元测试、集成测试和端到端测试分层运行。",
        "F62": "喜欢只对涉及的测试文件启用详细调试日志。",
        "F63": "偏好测试完成后检查是否残留临时文件和后台进程。",
        "F64": "喜欢在提交前重复运行曾经不稳定的测试以确认稳定性。",
    }
    existing_growth_facts = {
        **{marker: text for marker, text in status_update_facts.items() if marker <= "F40"},
        **{marker: text for marker, text in test_execution_facts.items() if marker <= "F56"},
    }
    additional_facts = {
        **{marker: text for marker, text in status_update_facts.items() if marker >= "F57"},
        **{marker: text for marker, text in test_execution_facts.items() if marker >= "F61"},
    }
    oversized_preference_content = "\n".join(
        f"- [{marker}] {text}" for marker, text in existing_growth_facts.items()
    )
    additional_fact_text = "\n".join(
        f"- [{marker}] {text}" for marker, text in additional_facts.items()
    )
    oversized_preference_files = {
        oversized_preference_uri: MemoryFile(
            uri=oversized_preference_uri,
            memory_type="preferences",
            content=oversized_preference_content,
            extra_fields={"user": "安德鲁", "topic": "工作偏好"},
        )
    }

    return [
        AutonomousCase(
            case_id="case_insensitive_directory_collision",
            category="directory_merge",
            schemas=(directory_schema,),
            initial_files=directory_files,
            expected_groups={"entities": (frozenset({"F01", "F02", "F03", "F04"}),)},
            expected_replacements={
                f"{directory_root}/Projects/atlas.md": f"{directory_root}/projects/atlas.md"
            },
            messages=(
                Message(
                    id="directory-context",
                    role="user",
                    created_at="2026-08-30T10:00:00+08:00",
                    parts=[TextPart("你还记得 Atlas 项目的发布方式、回滚流程和日常运维信息吗？")],
                ),
            ),
        ),
        AutonomousCase(
            case_id="oversized_profile_routes_preferences",
            category="cross_type_split",
            schemas=(profile_schema, preferences_schema),
            initial_files=profile_files,
            expected_groups={
                "profile": (frozenset({"F01", "F02", "F03", "F04", "F05", "F06"}),),
                "preferences": (
                    frozenset({"F07", "F08"}),
                    frozenset({"F09", "F10", "F11", "F12"}),
                    frozenset({"F13", "F14", "F15", "F16"}),
                    frozenset({"F17", "F18", "F19", "F20"}),
                    frozenset({"F21", "F22", "F23", "F24"}),
                ),
            },
            expected_replacements={},
            maintenance_review_tokens=400,
            messages=(
                Message(
                    id="profile-context",
                    role="user",
                    created_at="2026-08-30T10:00:00+08:00",
                    parts=[
                        TextPart(
                            "你还记得我的基本情况，以及我在工作、饮食、旅行和游戏方面的偏好吗？"
                        )
                    ],
                ),
            ),
        ),
        AutonomousCase(
            case_id="oversized_preference_splits",
            category="same_type_split",
            schemas=(preferences_schema,),
            initial_files=oversized_preference_files,
            expected_groups={
                "preferences": (
                    frozenset(status_update_facts),
                    frozenset(test_execution_facts),
                )
            },
            expected_replacements={},
            messages=(
                Message(
                    id="growth-update",
                    role="user",
                    peer_id="安德鲁",
                    created_at="2026-08-29T02:30:00+08:00",
                    parts=[TextPart("请记住我新增的这些偏好：\n" + additional_fact_text)],
                ),
            ),
            additional_facts=additional_facts,
        ),
    ]
