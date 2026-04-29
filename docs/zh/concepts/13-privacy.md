# 隐私配置与 Skill 隐私提取/加载

本文介绍 OpenViking 的隐私配置能力，以及它与 Skill 写入（提取）和读取（加载还原）的协作机制。

## 目标

隐私配置用于把敏感值（如 `api_key`、`token`、`base_url`）从技能正文中分离出来，避免明文长期存储在 `SKILL.md` 中，同时保留版本化管理与回滚能力。

核心目标：

- 写入时自动抽取敏感值并占位符化
- 读取时按当前生效配置自动还原
- 支持版本查询、切换与审计

---

## 核心对象与存储结构

隐私配置按二级键组织：`category + target_key`。

- `category`：配置类别（当前 Skill 使用 `skill`）
- `target_key`：目标标识（通常是 skill 名）

用户空间下的存储路径：

```
viking://user/{user_space}/privacy/{category}/{target_key}/
├── .meta.json                 # 元信息（active_version/latest_version/labels 等）
├── current.json               # 当前生效版本快照
└── history/
    ├── version_1.json
    ├── version_2.json
    └── ...
```

`current.json` 与 `history/version_x.json` 都是完整快照（`values` 整包）。

---

## 版本语义

### upsert（写入）

- 每次 upsert 都以传入 `values` 作为“新快照候选”
- 如果与当前版本 `values` 完全一致，则不新建版本，直接返回当前版本
- 否则创建新版本并自动设为当前生效版本
- 允许新增 key（不会因“未知 key”报错）

### activate（激活）

- 将历史版本设置为当前生效版本（写回 `current.json`）
- 更新 `.meta.json` 中的 `active_version`

---

## Skill 隐私提取（写入链路）

当通过 `add_skill` 写入 Skill 时，会走隐私提取与占位符化流程。

```
add_skill
  -> SkillProcessor._sanitize_skill_privacy
    -> extract_skill_privacy_values (LLM 抽取 values)
    -> placeholderize_skill_content_with_blocks (明文替换为占位符)
    -> privacy.upsert(category="skill", target_key=skill_name, values=...)
  -> 写入占位符化后的 SKILL.md
```

### 关键点

1. **抽取结果来源**：模型返回 JSON，读取 `values` 字段作为隐私键值。  
2. **内容替换**：原文中命中的敏感片段会替换为占位符：
   - <code v-pre>{{ov_privacy:skill:{skill_name}:{field_name}}}</code>
3. **保留块映射**：会同时记录：
   - `original_content_blocks`
   - `replacement_content_blocks`
4. **写盘结果**：`SKILL.md` 持久化的是占位符内容，不是明文值。

---

## Skill 加载还原（读取链路）

读取 `SKILL.md` 时，`FSService.read` 会尝试自动还原占位符。

```
fs.read(uri)
  -> get_skill_name_from_uri(uri)
  -> privacy.get_current(category="skill", target_key=skill_name)
  -> restore_skill_content(content, skill_name, current.values)
```

### URI 匹配

当前支持通过后缀识别 Skill：`/skills/{name}/SKILL.md`，可兼容：

- `viking://agent/skills/{name}/SKILL.md`
- `viking://agent/{agent_id}/skills/{name}/SKILL.md`

### restore 规则

`restore_skill_content` 的行为如下：

1. **content 中有占位符，且 privacy value 存在且非空**  
   -> 直接替换为对应值。

2. **content 中有占位符，但 privacy value 缺失或为空**  
   -> 保留占位符不替换，并记入 `unresolved_entries`。

3. **privacy 中存在 key 且非空，但 content 中没有对应占位符**  
   -> 记入“额外配置”提示（`Configured but not referenced in content`）。

4. 当存在 `unresolved_entries` 或“额外配置”时，会在内容末尾追加：
   - `[OpenViking Privacy Notice]`
   - `Related configured privacy values: ...`
   - `Not replaced (missing config): ...`（如有）
   - `Configured but not referenced in content: ...`（如有）

> 注意：当前实现中，仅当该 skill 已有 `current` 配置时才会进入 restore。若没有当前配置，不会追加 notice。

---

## 与 CLI/API 的关系

- 管理面：通过 Privacy API/CLI 管理版本（查询、写入、回滚）
- 内容面：通过 `read` 自动恢复占位符
- 两者解耦：内容文件负责占位符，隐私服务负责敏感值与版本

常用命令：

```bash
openviking privacy categories
openviking privacy list skill
openviking privacy skill <target_key>
openviking privacy upsert skill <target_key> --values-json '{"api_key":"..."}'
openviking privacy activate skill <target_key> <version>
openviking read viking://agent/default/skills/<target_key>/SKILL.md
```

---

## 设计收益

- 降低明文敏感信息在技能正文中的暴露风险
- 通过版本化支持密钥轮换与快速回滚
- 对上层调用透明：`read` 即可拿到还原后的可执行技能文本
- 对不完整配置提供可观测提示，便于排障

---

## 相关文档

- [技能](../api/04-skills.md) - Skill 写入与读取 API
- [隐私配置 API](../api/10-privacy.md) - 隐私配置端点说明
- [上下文提取](./06-extraction.md) - 内容提取主流程
- [架构概述](./01-architecture.md) - 系统分层与模块关系
