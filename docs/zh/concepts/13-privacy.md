# 隐私配置与自动管理

本文介绍 OpenViking 的隐私配置能力，以及它与技能/会话写入（提取）和读取（加载还原）的协作机制。

## 目标

隐私配置用于把敏感值（如 `api_key`、`token`）从内容（如技能、会话）中分离出来。被自动扫描的敏感信息，保存在 OpenViking 用户空间的隐私配置中并保留版本记录，内容则保存占位符，读取时可根据当前生效配置还原。

核心目标：

- 写入时自动提取敏感值并占位符化
- 读取时按内容类型还原
- 支持版本查询、切换与审计

---

## 核心对象与存储结构

隐私配置在当前用户空间内按二级键组织：`category + target_key`。

| 内容 | `category` | `target_key` |
|---|---|---|
| Skill | `skill` | Skill 名称 |
| Session | `session` | Session ID |
| 个人密钥 | `credential` | 如 `github`、`database` |

用户空间下的存储路径：

```
viking://user/{user_id}/privacy/{category}/{target_key}/
├── .meta.json                 # 元信息（active_version/latest_version/labels 等）
├── current.json               # 当前生效版本快照
└── history/
    ├── version_1.json
    ├── version_2.json
    └── ...
```

`current.json` 与 `history/version_x.json` 都是完整的隐私值字典（`values` 整包）。而 Session 或 Skill 中的正文只会保存这些隐私值的引用（即占位符）。

---

## 版本语义

### upsert（写入）

- 每次 upsert 都以传入 `values` 作为“新快照候选”
- 如果与当前版本 `values` 完全一致，则不新建版本，直接返回当前版本
- 否则创建新版本并自动设为当前生效版本
- 允许新增 key（不会因“未知 key”报错）
- `values` 是完整快照，不是部分更新；省略的旧 key 不会保留在新版本中

### activate（激活）

- 将历史版本设置为当前生效版本（写回 `current.json`）
- 更新 `.meta.json` 中的 `active_version`
- 不会轮换或撤销第三方服务中的密钥；这里只切换 OpenViking 使用的值

---

## 技能的隐私提取与读取还原

### 已登记值保护（写入链路）

当通过 `add_skill` 写入 Skill 时，会走隐私提取与占位符化流程。

```
add_skill
  -> SkillProcessor._sanitize_skill_privacy
    -> PrivacySanitizer（保护已登记的个人密钥）
    -> extract_skill_privacy_values (LLM 抽取 values)
    -> placeholderize_skill_content_with_blocks (明文替换为占位符)
    -> privacy.upsert(category="skill", target_key=skill_name, values=...)
  -> 写入占位符化后的 SKILL.md
```

#### 关键点

1. **抽取结果来源**：模型返回 JSON，读取 `values` 字段作为隐私键值。  
2. **内容替换**：原文中命中的敏感片段会替换为占位符：
   - <code>&#123;&#123;ov_privacy:skill:&#123;skill_name&#125;:&#123;field_name&#125;&#125;&#125;</code>
3. **保留块映射**：会同时记录：
   - `original_content_blocks`
   - `replacement_content_blocks`
4. **写盘结果**：匹配成功的片段以占位符保存，未匹配内容保持原样。该流程处理技能正文，不等于扫描并清理所有附件。

---

### 加载还原（读取链路）

读取 `SKILL.md` 时，默认保留占位符；显式指定 `raw=true` 时，`FSService.read` 会尝试还原占位符。

```
fs.read(uri)
  -> get_skill_name_from_uri(uri)
  -> restore_skill_content
    -> privacy.get_current(category, target_key)
    -> 还原正文中的引用
```

#### URI 匹配

当前支持通过后缀识别 Skill：`/skills/{name}/SKILL.md`，可兼容 user-scoped skill 路径，例如：

- `viking://~/skills/{name}/SKILL.md`
- `viking://user/{user_id}/skills/{name}/SKILL.md`

#### restore 规则

`restore_skill_content` 的行为如下：

1. **content 中有占位符，且 privacy value 存在且非空**  
   -> 直接替换为对应值。

2. **content 中有占位符，但 privacy value 缺失或为空**  
   -> 保留占位符不替换，并记入 `unresolved_entries`。

3. **privacy 中存在 key 且非空，但 content 中没有对应占位符**  
   -> 记入“额外配置”提示（`Configured but not referenced in content`）。

4. 当存在 `unresolved_entries` 或“额外配置”时，会在内容末尾追加：
   - `[Privacy Config Notice]`
   - `Missing config: ...`（如有）
   - `Configured but not referenced in content: ...`（如有）

指向当前用户 Privacy 字段的占位符也会按其引用还原；指向其他用户或缺失字段时，保留占位符。

> 仅当该 skill 已有 `current` 配置时，才会追加该技能的配置提示。

---

`read` 返回的是还原后的值；“额外配置”提示也会包含配置值。Privacy API 和历史快照同样可以返回完整值，该功能不等于响应脱敏或存储加密。需要加密存储时，另见[存储加密](./10-encryption.md)。

## 会话的隐私提取与读取还原

### 自动提取（写入链路）

向 Session 写入消息时，会在工具输出外置和消息落盘前，将匹配已登记 Privacy 值的内容替换为占位符。新敏感值的自动探测因误报产生大量无效数据，已在代码中暂时禁用，后续再处理；目前没有运行时配置开关。

```
Session.add_messages_async
  -> extract_and_store_session_privacy
    -> PrivacySanitizer（保护已登记的个人密钥）
    -> 跳过新值探测，不新增 Session Privacy 字段
  -> 工具输出外置
  -> 写入占位符化后的消息
```

#### 关键点

1. **匹配来源**：仅匹配已登记的 Privacy 值；固定规则和敏感字段名不再触发新值提取。
2. **覆盖范围**：处理 TextPart 文本、ContextPart 摘要和 ToolPart 输入、输出。工具输入递归匹配已登记值，不再根据字段名整体保存和遮罩。
3. **内容替换**：原文中的敏感片段会替换为占位符：
   - 当前 Session 的敏感值统一使用以下占位符，以 Session ID 或其哈希映射值标识会话：<code>&#123;&#123;ov_privacy:session:&#123;session_id&#125;:&#123;field_name&#125;&#125;&#125;</code>。
   - 若敏感片段与已登记的个人密钥（credential）匹配，则优先使用唯一匹配字段的占位符，例如 <code>&#123;&#123;ov_privacy:alice:viking://~/privacy/credential/github#/values/pat&#125;&#125;</code>。
4. **写盘结果**：匹配成功的片段以占位符保存，未匹配内容保持原样；不新增 Session Privacy 数据。历史配置和已有占位符的读取还原继续保留。

---

### 加载还原（读取链路）

读取 Session 消息或外置工具输出时，默认保留占位符；显式指定 `raw=true` 时，`FSService.read` 会尝试还原占位符。

```
fs.read(uri)
  -> get_session_id_from_uri(uri, session_root)
  -> restore_session_content
    -> privacy.get_current(category, target_key)
    -> 还原正文中的引用
```

#### URI 匹配

当前支持还原当前用户 Session 目录下的以下文件：

- `sessions/{session_id}/messages.jsonl`
- `sessions/{session_id}/history/archive_*/messages.jsonl`
- `sessions/{session_id}/tool-results/{id}/output.txt`

#### restore 规则

`restore_session_content` 的行为如下：

1. **content 中有占位符，且 privacy value 存在且非空**

   -> 直接替换为对应值；工具输入中的整值占位符按原始 JSON 类型还原。

2. **content 中有占位符，但 privacy value 缺失或为空**

   -> 保留占位符不替换。

3. **content 中的占位符指向其他用户，或使用不属于当前会话的 <code>&#123;&#123;ov_privacy:session:...&#125;&#125;</code>**

   -> 保留占位符不替换。

> 还原只影响读取结果，不修改存储文件。引用跟随当前生效配置，轮换字段值或激活其他版本后，历史消息下次读取时也使用新的当前值。

---

## 与 CLI/API 的关系

- 管理面：通过 Privacy API/CLI 管理版本（查询、写入、回滚）
- 内容面：通过 `read` 自动恢复占位符
- 两者解耦：内容文件负责占位符，隐私服务负责敏感值与版本

PrivacySanitizer 不根据字段名猜测敏感性。当前 active Privacy 中长度不少于 6 个 Unicode 字符的字符串值会参与正文自动扫描与引用替换；更短的值仍可保存、引用和显式读取，但不会参与全文替换，以降低普通正文误命中。Python 负责目录、引用和缓存策略，匹配器构建、最长匹配与正文重写由 `ragfs` 的 Rust Aho-Corasick 实现完成，并通过 PyO3 复用不可变 matcher、扫描时释放 GIL。匹配目录按用户和本进程 revision 缓存，本进程修改会立即失效，另外设有 3600 秒缓存 TTL 以覆盖多 worker/多节点修改。

以保存 GitHub Personal Access Token（PAT）为例：

```bash
# 首次写入或轮换 GitHub PAT；credential/github 是 target，pat 是 field
ov privacy upsert credential github --field-pat 'github_pat_...'

# 盘点当前目标和字段，不暴露值
ov privacy tree --category credential --references

# target 只有 pat 一个字段时可以省略 field
ov privacy reference credential github
ov privacy resolve credential github

# 显式指定 field 的等价形式
ov privacy reference credential github pat
ov privacy resolve credential github pat
ov privacy resolve '{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}'

# 查看/切换历史版本，或删除 pat 字段（最后一个字段删除后 target 也会删除）
ov privacy versions credential github
ov privacy activate credential github 1
ov privacy delete credential github pat --yes

# 扫描一个明确的 URI；默认仅报告，--fix 才会替换明文 PAT
ov privacy scan --uri viking://~/resources/project-a
```

---

## 设计收益

- 降低明文敏感信息在技能正文中的暴露风险
- 为外部密钥轮换后的配置切换保留版本记录
- 对上层调用透明：`read` 即可拿到还原后的可执行技能文本
- 对不完整配置提供可观测提示，便于排障

---

## 相关文档

- [技能](../api/04-skills.md) - Skill 写入与读取 API
- [会话](../api/05-sessions.md) - Session 创建、消息写入
- [隐私配置 API](../api/10-privacy.md) - 隐私配置端点说明
- [上下文提取](./06-extraction.md) - 内容提取主流程
- [架构概述](./01-architecture.md) - 系统分层与模块关系
