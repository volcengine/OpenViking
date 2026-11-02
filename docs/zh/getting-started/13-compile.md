# 上下文编译

将灯塔项目的四份资料整理成一份小型 Wiki，打开正文核对来源和未知事项。

## 认识 LLM Wiki

LLM Wiki 利用大模型，将分散在文档、会议记录和反馈中的信息，整理成按主题组织、相互链接的 Markdown 知识库。同一项目或概念的相关内容可以汇集到一页，通过导航页浏览，并保留指向原始资料的引用，方便读者和 Agent 查阅、核对与复用。

例如，灯塔项目的目标、反馈流程和准备事项分布在四份资料中。编译后，一篇项目页面就可以串起这些信息，标明尚未确定的日期和预算，并为关键事实附上来源。后续了解项目时，可以先阅读这篇页面，再沿引用查看细节。

OpenViking 通过 LLM Wiki Skill 定义整理方式，由 Compile Runtime 读取来源并生成页面。生成的 Wiki 可继续用于阅读和检索；关键结论仍需结合原始资料核对。下面用灯塔资料完成一次编译。

## 准备来源与技能

本页需要已配置 Compile Runtime 的服务。本机默认使用已安装 `openviking[bot]` 并以 `--with-bot` 启动的 VikingBot，模型和连接配置见[快速开始](02-quickstart.md)。

先完成[资料检索](08-resources.md#准备资料)的资料导入，确认 `viking://resources/onboarding-zh-v1` 可读取。下载 [Wiki Skill v1](/quickstart/llm-wiki-v1.zip)，安装到有写权限的共享技能目录：

```bash
ov add-skill ./llm-wiki-v1.zip -p viking://agent/skills --wait
ov read viking://agent/skills/llm-wiki/SKILL.md
```

这个样例包包含项目自带的 LLM Wiki Skill。编译将调用模型并在下面的新目录中写入产物；重复体验时使用新的目标目录名。

## 提交编译

```bash
ov compile --from viking://resources/onboarding-zh-v1 \
  --to viking://resources/onboarding-wiki-v1 \
  --skill viking://agent/skills/llm-wiki \
  --instruction "请用中文生成一个 index.md 和一篇 entity/lighthouse.md。正文围绕隔离网络中的反馈试点，综合项目、反馈和会议资料，保留完整来源 viking:// URI，明确未知日期。仅在目标目录写入。"
```

记录返回的 `cmp_...` 任务 ID，查询进度：

```bash
ov task status <cmp_task_id>
```

每隔 5 秒查看一次，先观察最多 5 分钟。状态为 `completed` 后读取产物；仍在运行时可稍后再查，或用 `ov task cancel <cmp_task_id>` 请求取消。失败时查看任务错误和 Compile Runtime 配置。

编译完成后，目标目录中应包含导航页和项目正文。你可以参考 [Wiki 正文示例](/quickstart/wiki-output-zh-v1.txt)，了解内容组织和来源引用方式，再按下文核对生成结果。

## 打开正文

```bash
ov tree viking://resources/onboarding-wiki-v1
ov read viking://resources/onboarding-wiki-v1/index.md
ov read viking://resources/onboarding-wiki-v1/entity/lighthouse.md
```

如果实际文件名与指令不同，可从 `tree` 和导航页找到正文 URI。核对以下内容：隔离网络条件、离线安装包与本地目录、每周五导出反馈、模板与演练准备，以及尚未确定的日期和预算。打开正文引用的来源，逐项确认依据。

| 情况 | 检查方向 |
| --- | --- |
| Runtime 不可用 | 服务端是否安装 Bot 依赖并启用编译执行端 |
| 找不到来源或 Skill | URI、读权限与前置处理结果 |
| 正文缺少依据 | 检查实际读取记录和来源引用，补充编译指令后使用新目标重试 |

完成标志是一篇可阅读、可导航且关键事实有来源支持的正文。文件结构、页面类型和可视化见 [LLM Wiki 示例](../context-compilation/02-llm-wiki.md)。清理时确认演示目标目录，保留需要的产物后在 Studio 中删除。
