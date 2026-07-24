# Compile to Skill demo

这个 demo 用来手工验证下面这条链路：

1. 用 `ov add-resource` 导入两份虚构的架构双周报。
2. 用 `ov write` 写入三条 trajectory memory。
3. 用 `ov add-skill` 安装一个“生成 Skill 的指导 Skill”。
4. 用 `ov compile --to viking://agent/skills` 生成或更新
   `weekly-report-writer`。

本目录只提供测试数据和命令，不会自动执行任何 OpenViking 操作。

## 文件说明

```text
compile-skill-demo/
├── resources/                         # 周报事实与格式样例
├── memories/                          # 可复用的周报写作 trajectory
└── skills/
    └── build-weekly-report-skill/     # 传给 ov compile --skill 的指导 Skill
```

所有项目名、指标和人员均为虚构测试数据。

## 1. 导入周报 Resource

在仓库根目录执行。`--to` 指定的 URI 首次执行前应不存在。

```bash
ov add-resource \
  examples/compile-skill-demo/resources \
  --to viking://resources/compile-skill-demo/weekly-reports \
  --reason "作为架构双周报的事实组织、表达方式和质量标准样例" \
  --wait
```

## 2. 写入 trajectory Memory

`write --mode create` 会创建文件及缺失的父目录。若重复执行，请改用新的
文件名，或确认内容后将 `--mode create` 改成 `--mode replace`。

```bash
ov write \
  viking://user/memories/trajectories/compile-skill-demo/01-evidence-first.md \
  --from-file examples/compile-skill-demo/memories/01-evidence-first.md \
  --mode create \
  --wait

ov write \
  viking://user/memories/trajectories/compile-skill-demo/02-review-feedback.md \
  --from-file examples/compile-skill-demo/memories/02-review-feedback.md \
  --mode create \
  --wait

ov write \
  viking://user/memories/trajectories/compile-skill-demo/03-final-quality-gate.md \
  --from-file examples/compile-skill-demo/memories/03-final-quality-gate.md \
  --mode create \
  --wait
```

## 3. 安装指导 Skill

这里显式使用 `-p viking://agent/skills`，因此指导 Skill 会安装到共享的
agent skills 根目录。省略 `-p` 时，`ov add-skill` 默认安装到当前用户的
私有 skills 目录。

```bash
ov add-skill \
  examples/compile-skill-demo/skills/build-weekly-report-skill \
  -p viking://agent/skills \
  --wait
```

可以确认实际安装位置：

```bash
ov skills show build-weekly-report-skill --format json
```

## 4. Compile 成新的 Skill

`--from` 可以重复传入，也可以用逗号分隔；不要用分号。这里把 Resource
和 trajectory Memory 两个目录同时作为输入。

```bash
ov compile \
  --from viking://resources/compile-skill-demo/weekly-reports \
  --from viking://user/memories/trajectories/compile-skill-demo \
  --to viking://agent/skills \
  --skill viking://agent/skills/build-weekly-report-skill \
  --reason "创建或更新名为 weekly-report-writer 的 Skill：基于周报样例提炼稳定结构，结合 trajectory 中经过验证的写作流程与质检规则，使后续 Agent 能从零散事实生成清晰、可信、可执行的架构双周报" \
  --wait \
  --timeout 1800
```

这个命令的预期结果是写入一个 Skill 包，而不是 Wiki 页面：

```text
viking://agent/skills/weekly-report-writer/
└── SKILL.md
```

模型也可能按需生成 `references/` 下的少量辅助文件。OpenViking 在安装
Skill 后生成的 `.abstract.md`、`.overview.md` 等系统派生文件不属于
Compile 模型输出的 Wiki pages。

## 5. 检查结果

```bash
ov tree viking://agent/skills/weekly-report-writer
ov read viking://agent/skills/weekly-report-writer/SKILL.md
```

重点检查：

- `SKILL.md` 的 frontmatter 只包含 `name` 和 `description`。
- `name` 为 `weekly-report-writer`。
- 内容描述的是以后如何写周报，而不是复述本 demo 的两份周报。
- 样例中的具体项目名和数字没有被固化成通用规则。
- 输出目录中没有由 Compile 生成的 Wiki 页面。

## 私有目录变体

若要验证用户私有 Skill，把安装和 Compile 命令中的目标改为
`viking://user/skills`：

```bash
ov add-skill \
  examples/compile-skill-demo/skills/build-weekly-report-skill \
  -p viking://user/skills \
  --wait

ov compile \
  --from viking://resources/compile-skill-demo/weekly-reports \
  --from viking://user/memories/trajectories/compile-skill-demo \
  --to viking://user/skills \
  --skill viking://user/skills/build-weekly-report-skill \
  --reason "创建或更新名为 weekly-report-writer 的 Skill：基于样例和 trajectory 提炼可复用的架构双周报写作流程" \
  --wait \
  --timeout 1800
```
