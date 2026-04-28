# 更新日志

OpenViking 的所有重要变更都将记录在此文件中。
此更新日志从 [GitHub Releases](https://github.com/volcengine/OpenViking/releases) 自动生成。

## v0.3.12 (2026-04-24)

## What's Changed
* feat(semantic): add output_language_override to pin summary/overview language by @0xble in https://github.com/volcengine/OpenViking/pull/1607
* docs(bot): sync gateway config example with #1640 security defaults by @r266-tech in https://github.com/volcengine/OpenViking/pull/1649
* fix(parser): validate feishu config limits by @duyua9 in https://github.com/volcengine/OpenViking/pull/1645
* fix(code-hosting): recognize SSH repository hosts with userinfo by @officialasishkumar in https://github.com/volcengine/OpenViking/pull/1375
* docs(design): sync §4.6 role_id rules with #1643 passthrough behavior by @r266-tech in https://github.com/volcengine/OpenViking/pull/1657
* feat: 补充百炼记忆库 LoCoMo benchmark 评测脚本 by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1664
* docs(contributing): add maintainer routing map by @yeyitech in https://github.com/volcengine/OpenViking/pull/1519
* Revert "feat: 补充百炼记忆库 LoCoMo benchmark 评测脚本" by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1665
* [fix] Fix/oc2ov test stability by @kaisongli in https://github.com/volcengine/OpenViking/pull/1669
* fix(server): map AGFS URI errors by @euyua9 in https://github.com/volcengine/OpenViking/pull/1671
* feat(git): added azure devops support (#1625) by @Nono-04 in https://github.com/volcengine/OpenViking/pull/1642
* feat: support trusted admin tenant management by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1616
* fix(session): count tool parts in pending tokens by @wlff123 in https://github.com/volcengine/OpenViking/pull/1675
* fix(user_id): support dot by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1679
* Add VitePress docs site and Pages deployment by @yufeng201 in https://github.com/volcengine/OpenViking/pull/1681
* feat(parse): support larkoffice.com Feishu document URLs by @efishliu in https://github.com/volcengine/OpenViking/pull/1684
* feat(ragfs): add s3 key normalization encoding by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1685
* fix: apikey security: API Key 管理重构与安全增强 by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1686
* add Copy Markdown button and llms.txt support    by @yufeng201 in https://github.com/volcengine/OpenViking/pull/1688
* fix(api_keys): proxy get_user_role in NewAPIKeyManager (trusted-mode 500 regression) by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/1691
* Fix docs npm registry for release builds by @yufeng201 in https://github.com/volcengine/OpenViking/pull/1690
* fix: account name security by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1689

## New Contributors
* @Nono-04 made their first contribution in https://github.com/volcengine/OpenViking/pull/1642
* @yufeng201 made their first contribution in https://github.com/volcengine/OpenViking/pull/1681
* @efishliu made their first contribution in https://github.com/volcengine/OpenViking/pull/1684

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.10...v0.3.12


## v0.3.10 (2026-04-23)

# OpenViking v0.3.10 Release Notes / 发布说明

Release date / 发布日期: 2026-04-22

Full Changelog / 完整变更记录: https://github.com/volcengine/OpenViking/compare/v0.3.9...v0.3.10

---

## 中文

### 版本概览

v0.3.10 重点增强了 VLM provider、OpenClaw 插件生态、VikingDB 数据面接入，以及文件写入、QueueFS、Bot/CLI 的稳定性。本次发布包含 46 个提交，覆盖新功能、兼容性修复、安全修复和测试补强。

### 主要更新

- 新增 Codex、Kimi、GLM VLM provider，并支持 `vlm.timeout` 配置。
- 新增 VikingDB `volcengine.api_key` 数据面模式，可通过 API Key 访问已创建好的云上 VikingDB collection/index。
- `write()` 新增 `mode="create"`，支持创建新的文本类 resource 文件，并自动触发语义与向量刷新。
- OpenClaw 插件新增 ClawHub 发布、交互式 setup 向导和 `OPENCLAW_STATE_DIR` 支持。
- QueueFS 新增 SQLite backend，支持持久化队列、ack 和 stale processing 消息恢复。
- Locomo / VikingBot 评测链路新增 preflight 检查和结果校验。

### 新功能用法

#### 使用新的 VLM provider

Codex OAuth 推荐通过初始化向导配置：

```bash
openviking-server init
openviking-server doctor
```

手动配置时，`openai-codex` 在 Codex OAuth 可用时不需要 `api_key`：

```json
{
  "vlm": {
    "provider": "openai-codex",
    "model": "gpt-5.3-codex",
    "api_base": "https://chatgpt.com/backend-api/codex",
    "timeout": 120
  }
}
```

Kimi 和 GLM 使用 OpenAI-compatible 请求格式：

```json
{
  "vlm": {
    "provider": "kimi",
    "model": "kimi-code",
    "api_key": "your-kimi-subscription-api-key",
    "api_base": "https://api.kimi.com/coding"
  }
}
```

```json
{
  "vlm": {
    "provider": "glm",
    "model": "glm-4.6v",
    "api_key": "your-zai-api-key",
    "api_base": "https://api.z.ai/api/coding/paas/v4"
  }
}
```

#### 创建新的 resource 文件

```bash
openviking write viking://resources/notes/release-v0.3.10.md \
  --mode create \
  --content "# v0.3.10

Release notes." \
  --wait
```

`create` 模式只用于新文件；目标已存在时会返回 `409 Conflict`。支持的扩展名包括 `.md`、`.txt`、`.json`、`.yaml`、`.yml`、`.toml`、`.py`、`.js`、`.ts`。

#### 使用 VikingDB API Key 数据面模式

该模式适合连接已提前创建 collection/index/schema 的 VikingDB。OpenViking 会执行数据写入、查询、删除和聚合，不会创建或删除 collection/index。

```json
{
  "storage": {
    "vectordb": {
      "backend": "volcengine",
      "name": "context",
      "project": "default",
      "index_name": "default",
      "volcengine": {
        "api_key": "your-vikingdb-data-api-key",
        "region": "cn-beijing",
        "host": "api-vikingdb.vikingdb.cn-beijing.volces.com"
      }
    }
  }
}
```

#### 安装和配置 OpenClaw 插件

```bash
openclaw plugins install clawhub:@openclaw/openviking
openclaw openviking setup
```

连接已有远端 OpenViking 服务时：

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agentId your-agent-id
```

#### QueueFS SQLite backend

服务端语义/向量任务队列默认可使用持久化 QueueFS。直接挂载 `queuefs` 插件时，可配置 SQLite 参数：

```json
{
  "backend": "sqlite",
  "db_path": "./data/queue.db",
  "recover_stale_sec": 300,
  "busy_timeout_ms": 5000
}
```

### 体验与兼容性改进

- 调整 `recallTokenBudget` 和 `recallMaxContentChars` 默认值，降低 OpenClaw 自动召回注入过长上下文的风险。
- `ov add-memory` 在异步 commit 场景下返回 `OK`，避免误判后台任务仍在执行时的状态。
- `ov chat` 会从 `ovcli.conf` 读取鉴权配置并自动发送必要请求头。
- OpenClaw 插件默认远端连接行为、鉴权、namespace 和 `role_id` 处理更贴合服务端多租户模型。

### 修复

- 修复 Bot API channel 鉴权检查、启动前端口检查和已安装版本上报。
- 修复 OpenClaw 工具调用消息格式不兼容导致的孤儿 `toolResult`。
- 修复 console `add_resource` target 字段、repo target URI、filesystem `mkdir`、reindex maintenance route 等问题。
- 修复 Windows `.bat` 环境读写、shell escaping、`ov.conf` 校验和硬编码路径问题。
- 修复 Gemini + tools 场景下 LiteLLM `cache_control` 导致的 400 错误，并支持 OpenAI reasoning model family。
- 修复 S3FS 目录 mtime 稳定性、Rust native build 环境污染、SQLite 数据库扩展名解析等问题。

### 文档、测试与安全

- 补充 VLM provider、Codex OAuth、Kimi/GLM、`write(mode=create)`、`tools.mcp_servers`、`ov_tools_enable`、Feishu thread 和 VLM timeout 文档。
- 新增资源构建、Context Engine、OpenClaw 插件、内容写入、VLM provider、setup wizard、server bootstrap 和安全相关测试。
- 新增 `SECURITY.md` 并更新 README、多语言文档和社群二维码。
- 修复多项 code scanning 和 runtime 安全告警。
- 增强 Bot gateway、OpenAPI auth、werewolf demo、配置校验和本地命令执行相关安全测试。

---

## English

### Overview

v0.3.10 focuses on VLM providers, the OpenClaw plugin ecosystem, VikingDB data-plane access, and stability improvements across content write, QueueFS, Bot, and CLI workflows. This release includes 46 commits covering new capabilities, compatibility fixes, security fixes, and expanded tests.

### Highlights

- Added Codex, Kimi, and GLM VLM providers, plus `vlm.timeout` for per-request HTTP timeouts.
- Added VikingDB `volcengine.api_key` data-plane mode for accessing pre-created cloud VikingDB collections and indexes with an API key.
- Added `write(mode="create")` for creating new text resource files and automatically refreshing related semantics and vectors.
- Added ClawHub publishing, an interactive setup wizard, and `OPENCLAW_STATE_DIR` support for the OpenClaw plugin.
- Added a SQLite backend for QueueFS with persisted queues, ack support, and stale processing message recovery.
- Added Locomo / VikingBot evaluation preflight checks and result validation.

### New Feature Usage

#### Use the new VLM providers

For Codex OAuth, prefer the setup wizard:

```bash
openviking-server init
openviking-server doctor
```

When configuring manually, `openai-codex` does not require `api_key` if Codex OAuth is available:

```json
{
  "vlm": {
    "provider": "openai-codex",
    "model": "gpt-5.3-codex",
    "api_base": "https://chatgpt.com/backend-api/codex",
    "timeout": 120
  }
}
```

Kimi and GLM use OpenAI-compatible request formats:

```json
{
  "vlm": {
    "provider": "kimi",
    "model": "kimi-code",
    "api_key": "your-kimi-subscription-api-key",
    "api_base": "https://api.kimi.com/coding"
  }
}
```

```json
{
  "vlm": {
    "provider": "glm",
    "model": "glm-4.6v",
    "api_key": "your-zai-api-key",
    "api_base": "https://api.z.ai/api/coding/paas/v4"
  }
}
```

#### Create a new resource file

```bash
openviking write viking://resources/notes/release-v0.3.10.md \
  --mode create \
  --content "# v0.3.10

Release notes." \
  --wait
```

`create` mode only targets new files; an existing path returns `409 Conflict`. Supported extensions include `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, and `.ts`.

#### Use VikingDB API-key data-plane mode

This mode is intended for VikingDB collections, indexes, and schemas that were created out of band. OpenViking can write, search, delete, and aggregate data, but it does not create or delete collections and indexes in this mode.

```json
{
  "storage": {
    "vectordb": {
      "backend": "volcengine",
      "name": "context",
      "project": "default",
      "index_name": "default",
      "volcengine": {
        "api_key": "your-vikingdb-data-api-key",
        "region": "cn-beijing",
        "host": "api-vikingdb.vikingdb.cn-beijing.volces.com"
      }
    }
  }
}
```

#### Install and configure the OpenClaw plugin

```bash
openclaw plugins install clawhub:@openclaw/openviking
openclaw openviking setup
```

To connect to an existing remote OpenViking server:

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agentId your-agent-id
```

#### QueueFS SQLite backend

The server semantic/vector task queues can use persistent QueueFS. When mounting the `queuefs` plugin directly, configure SQLite parameters like this:

```json
{
  "backend": "sqlite",
  "db_path": "./data/queue.db",
  "recover_stale_sec": 300,
  "busy_timeout_ms": 5000
}
```

### Improvements

- Adjusted the default `recallTokenBudget` and `recallMaxContentChars` to reduce the risk of overlong OpenClaw auto-recall context injection.
- `ov add-memory` now returns `OK` for asynchronous commit workflows instead of implying the background task has already finished.
- `ov chat` now reads authentication from `ovcli.conf` and sends the required request headers.
- The OpenClaw plugin now aligns remote connection behavior, auth, namespace, and `role_id` handling with the server multi-tenant model.

### Fixes

- Fixed Bot API channel auth checks, startup port preflight checks, and installed-version reporting.
- Fixed orphan `toolResult` errors caused by incompatible OpenClaw tool-call message formats.
- Fixed console `add_resource` target fields, repo target URIs, filesystem `mkdir`, and the reindex maintenance route.
- Fixed Windows `.bat` environment read/write, shell escaping, `ov.conf` validation, and hardcoded paths.
- Fixed LiteLLM `cache_control` 400 errors for Gemini + tools and added support for OpenAI reasoning model families.
- Fixed S3FS directory mtime stability, Rust native build environment pollution, and SQLite database extension parsing.

### Docs, Tests, and Security

- Documented VLM providers, Codex OAuth, Kimi/GLM, `write(mode=create)`, `tools.mcp_servers`, `ov_tools_enable`, Feishu thread settings, and VLM timeout.
- Added tests for resource builds, Context Engine, the OpenClaw plugin, content write, VLM providers, setup wizard, server bootstrap, and security behavior.
- Added `SECURITY.md` and updated the README files, multilingual docs, and community QR code.
- Addressed multiple code scanning and runtime security findings.
- Expanded security coverage for Bot gateway, OpenAPI auth, the werewolf demo, config validation, and local command execution.


## What's Changed
* docs(channel): document ov_tools_enable config introduced in #1352 by @r266-tech in https://github.com/volcengine/OpenViking/pull/1571
* docs(bot): document tools.mcp_servers config (#1392) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1567
* fix(bot): 端口冲突预检 + /health 上报真实版本 by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/1566
* docs(en): list dashscope in embedding provider table (#1535) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1584
* feat(vlm): expose timeout as a config field and thread it through by @0xble in https://github.com/volcengine/OpenViking/pull/1580
* fix(cli): ov chat auth from config with required headers by @tolatolatop in https://github.com/volcengine/OpenViking/pull/1575
* docs(feishu): document threadRequireMention + botName post #1534 by @r266-tech in https://github.com/volcengine/OpenViking/pull/1573
* fix(vlm): strip cache_control for Gemini + tools to avoid LiteLLM CachedContent 400 by @0xble in https://github.com/volcengine/OpenViking/pull/1569
* fix(vlm): support OpenAI reasoning-model families (gpt-5, o1, o3, o4) by @0xble in https://github.com/volcengine/OpenViking/pull/1568
* fix(oc2ov-test): add Context Engine test case and main branch add cron by @kaisongli in https://github.com/volcengine/OpenViking/pull/1586
* feat(queuefs): Add SQLite backend with ack/recover and unified control-file/config specs by @sponge225 in https://github.com/volcengine/OpenViking/pull/1500
* fix(reindex): repair maintenance route and keep content compatibility by @0xble in https://github.com/volcengine/OpenViking/pull/1583
* feat(demo):werewolf readme by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1590
* fix(security): address targeted code scanning alerts by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1591
* docs(vlm): document timeout config field (#1580) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1593
* fix: allow --sudo for ov admin commands by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1589
* WeChat QR code 更新 by @Lumos088 in https://github.com/volcengine/OpenViking/pull/1597
* fix(session): drop user registry check for message role_id by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1601
* fix(security): clean up code scanning and runtime findings by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1596
* fix(eval): Fix commit emb token calculate, add time cost by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1609
* fix(ragfs): stabilize s3fs directory mod times by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1603
* Create SECURITY.md by @oss-bd in https://github.com/volcengine/OpenViking/pull/1611
* feat: add ClawHub publishing by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1587
* fix(build): sanitize Rust native build env by @myysy in https://github.com/volcengine/OpenViking/pull/1610
* fix(parse): ignore sqlite database extensions by @euyua9 in https://github.com/volcengine/OpenViking/pull/1598
* fix(cli): return OK for add-memory since commit is async by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/1613
* feat(openclaw-plugin): align auth, namespace, and role id handling by @jcp0578 in https://github.com/volcengine/OpenViking/pull/1606
* feat(content-write): 支持 mode=create 创建新文件 by @A0nameless0man in https://github.com/volcengine/OpenViking/pull/1608
* Revert "fix: fall back to prefix filters for volcengine path scope" by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1619
* fix: Windows .bat env read/write, shell escaping, ov.conf validation,… by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1620
* fix(fs): fix mkdir by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1622
* feat(vlm): add Codex, Kimi, and GLM VLM support by @ehz0ah in https://github.com/volcengine/OpenViking/pull/1444
* [feat] Test/add resource ci validation and fix session api case 400 error by @kaisongli in https://github.com/volcengine/OpenViking/pull/1599
* feat(eval):  Locomo bot eval add check by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1629
* docs(skill): sync ov add-memory output example after #1613 by @r266-tech in https://github.com/volcengine/OpenViking/pull/1627
* docs(filesystem): document write() mode=create (#1608) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1623
* adjust default value for recallTokenBudget and recallMaxContentChars. by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1617
* feat(vectordb): add volcengine_api_key data-plane backend for VikingDB by @fengluodb in https://github.com/volcengine/OpenViking/pull/1588
* fix: correct repo target uri by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1633
* fix: handle Wikipedia 403 in CI environment for TC-P05 by @kaisongli in https://github.com/volcengine/OpenViking/pull/1630
* fix: change openclaw mode into remote by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1634
* fix(console): use correct add_resource target field by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1637
* fix(plugin): 修复 OpenClaw 工具调用消息格式不兼容导致的 toolResult 孤儿错误 by @824156793 in https://github.com/volcengine/OpenViking/pull/1632
* Align context-engine assemble test with toolCall output by @wlff123 in https://github.com/volcengine/OpenViking/pull/1641
* fix(bot):Fix bot api-channel auth check by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1640
* fix(session): allow explicit role_id passthrough by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1643

## New Contributors
* @tolatolatop made their first contribution in https://github.com/volcengine/OpenViking/pull/1575
* @Lumos088 made their first contribution in https://github.com/volcengine/OpenViking/pull/1597
* @oss-bd made their first contribution in https://github.com/volcengine/OpenViking/pull/1611
* @euyua9 made their first contribution in https://github.com/volcengine/OpenViking/pull/1598
* @824156793 made their first contribution in https://github.com/volcengine/OpenViking/pull/1632

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.9...v0.3.10


## v0.3.9 (2026-04-18)

## What's Changed
* reorg: remove golang depends by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1339
* Feat/mem opt by @chenjw in https://github.com/volcengine/OpenViking/pull/1349
* fix: openai like embedding models fix, no more matryoshka error by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1350
* feat(bot): Add disable OpenViking config for channels. by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1352
* fix(config): point missing-config help messages to openviking.ai docs by @Gujiassh in https://github.com/volcengine/OpenViking/pull/1370
* fix(embedder): initialize async client state in VolcengineSparseEmbedder by @lRoccoon in https://github.com/volcengine/OpenViking/pull/1362
* feat(examples): add Codex memory plugin example by @0xble in https://github.com/volcengine/OpenViking/pull/1080
* feat(openclaw-plugin): add unified ov_import and ov_search by @jcp0578 in https://github.com/volcengine/OpenViking/pull/1369
* feat(bot): add MCP client support (port from HKUDS/nanobot v0.1.5) by @ponsde in https://github.com/volcengine/OpenViking/pull/1392
* feat(eval):Readme add qa by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1400
* feat(cli): support for default file/dir ignore config in `ovcli.conf` by @sentisso in https://github.com/volcengine/OpenViking/pull/1393
* benchmark: add LoCoMo evaluation for Supermemory by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1401
* fix(embedder): report configured provider in slow-call logs by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1403
* fix(queue): preserve embedding message ids across serialization by @officialasishkumar in https://github.com/volcengine/OpenViking/pull/1380
* test(security): add unit tests for network_guard and zip_safe modules by @sjhddh in https://github.com/volcengine/OpenViking/pull/1395
* fix(semantic): preserve repository hierarchy in overviews by @chethanuk in https://github.com/volcengine/OpenViking/pull/1376
* fix(tests): align pytest coverage docs with required setup (#1259) by @chethanuk in https://github.com/volcengine/OpenViking/pull/1373
* feat: rerank support extra headers by @caisirius in https://github.com/volcengine/OpenViking/pull/1359
* fix: reload legacy session rows by @chethanuk in https://github.com/volcengine/OpenViking/pull/1365
* fix: protect global watch-task control files from non-root access by @Hinotoi-agent in https://github.com/volcengine/OpenViking/pull/1396
* fix(agfs): enable agfs s3 plugin default by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/1408
* fix(claude-code-memory-plugin): improve Windows compatibility by @Castor6 in https://github.com/volcengine/OpenViking/pull/1249
* fix(pdf): resolve bookmark page mapping by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1412
* fix: update observer test to use /models endpoint instead of non-exis… by @kaisongli in https://github.com/volcengine/OpenViking/pull/1407
* fix(openclaw-plugin): extend default Phase 2 commit wait timeout by @yeyitech in https://github.com/volcengine/OpenViking/pull/1415
* pref(retrieve): Optimize the search performance of larger directories by skipping redundant target_directories scope by @sponge225 in https://github.com/volcengine/OpenViking/pull/1426
* Add third_party directory to Dockerfile by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1433
* Fix/openclaw addmsg by @chenjw in https://github.com/volcengine/OpenViking/pull/1391
* feat(bot):Heartbeat fix by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1434
* feat: add `openviking-server init` interactive setup wizard for local Ollama model deployment by @t0saki in https://github.com/volcengine/OpenViking/pull/1353
* fix(volcengine): update default doubao embedding model by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1438
* feat: add Memory V2 full suite test  by @kaisongli in https://github.com/volcengine/OpenViking/pull/1354
* update new wechat group qr code by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/1440
* feat(filesystem): support directory descriptions on mkdir by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1443
* feat(memory): default to memory v2 by @chenjw in https://github.com/volcengine/OpenViking/pull/1445
* fix: resolve OpenClaw session file lock conflicts in oc2ov tests by @kaisongli in https://github.com/volcengine/OpenViking/pull/1441
* fix: isolate temp scope by user within an account by @Hinotoi-agent in https://github.com/volcengine/OpenViking/pull/1398
* fix(docker): raise Rust toolchain for ragfs image builds by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1448
* feat(metric): add metric system by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/1357
* openclaw refactor: assemble context partitioning (Instruction/Archive/Session/… by @wlff123 in https://github.com/volcengine/OpenViking/pull/1446
* Fix/memory v2 by @chenjw in https://github.com/volcengine/OpenViking/pull/1450
* Fix/memory v2 by @chenjw in https://github.com/volcengine/OpenViking/pull/1452
* reorg: split parser layer to 2-layer: accessor and parser, so that we can reuse more code by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1428
* fix: merge error by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1463
* docs: document openviking-server init/doctor in README (#1454) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1455
* [security] fix(pack): block ovpack import writes to forbidden control-plane targets by @Hinotoi-agent in https://github.com/volcengine/OpenViking/pull/1451
* feat(retrieval): add time filters to find and search by @0xble in https://github.com/volcengine/OpenViking/pull/1429
* feat: add local llama-cpp embedding support by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1388
* fix(release): fail wheel builds without ragfs bindings by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1466
* fix: support CI environment in upgrade_openviking.sh by @kaisongli in https://github.com/volcengine/OpenViking/pull/1467
* feat(rust tui): add delete uri funciton with confirmation and refresh behaviour by @xiaobin83 in https://github.com/volcengine/OpenViking/pull/696
* fix(plugin): sanitize prompt fallback in before_prompt_build to preve… by @wlff123 in https://github.com/volcengine/OpenViking/pull/1472
* fix: error handling in CLI tui by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1476
* fix: downgrade embedding metadata check from fatal error to warning f… by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1477
* fix: block CI when OpenViking server fails to start by @kaisongli in https://github.com/volcengine/OpenViking/pull/1478
* feat(bot):Werewolf demo fix, Add one-click startup script by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1473
* fix(code): recognize ssh clone URLs with userinfo by @yeyitech in https://github.com/volcengine/OpenViking/pull/1421
* feat(server): add resources-only WebDAV adapter by @yeyitech in https://github.com/volcengine/OpenViking/pull/1435
* fix(parser): Fix parser config propagation for markdown splitting by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1480
* docs(api): align API reference with current server behavior by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1483
* Feat/memory overview by @chenjw in https://github.com/volcengine/OpenViking/pull/1460
* fix(client): ensure session files exist when creating new session in local mode by @sponge225 in https://github.com/volcengine/OpenViking/pull/1470
* feat(opencode-plugin): add auto recall for automatic memory context injection by @A0nameless0man in https://github.com/volcengine/OpenViking/pull/1484
* fix(plugin): propagate toolCallId and handle user-role tool parts in … by @wlff123 in https://github.com/volcengine/OpenViking/pull/1482
* reorg: collect all envs from everywhere, and defined in consts.py by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1490
* fix(cli): return actionable resource errors for fs commands by @ehz0ah in https://github.com/volcengine/OpenViking/pull/1458
* [security] fix(bot): prevent unauthenticated remote bot control via OpenAPI HTTP routes by @Hinotoi-agent in https://github.com/volcengine/OpenViking/pull/1447
* feat(embedding): surface non-symmetric embedding config for VikingDB provider by @mvanhorn in https://github.com/volcengine/OpenViking/pull/1110
* refactor: organize CLI commands with category tags, add timeout flexibility by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1492
* feat(metric): add token-full-cycle metric by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/1488
* feat: Add Vaka LoCoMo benchmark scripts by @PowerfulLxx in https://github.com/volcengine/OpenViking/pull/1502
* Temporary exemption to avoid compatibility issues. by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1504
* feat: vaka locomo benchmark by @PowerfulLxx in https://github.com/volcengine/OpenViking/pull/1506
* build(deps): update litellm requirement from <1.83.1,>=1.0.0 to >=1.0.0,<1.83.9 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1496
* build(deps): bump actions/github-script from 8 to 9 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1494
* build(deps): bump softprops/action-gh-release from 2 to 3 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1495
* fix(openclaw-plugin): enforce assemble token budgets by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1511
* fix: use abi3 for rust package by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1517
* feat(session): add account namespace policy and shared sessions by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1356
* Add doctor Ollama coverage by @duyua9 in https://github.com/volcengine/OpenViking/pull/1499
* fix(ci): fix ragfs-python native extension build in CI pipelines by @kaisongli in https://github.com/volcengine/OpenViking/pull/1532
* feat(bot):enhance Feishu mentions and name display by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1534
* feat(embedder): add DashScope embedding provider by @A0nameless0man in https://github.com/volcengine/OpenViking/pull/1535
* fix(docker): set OPENVIKING_CLI_CONFIG_FILE so CLI finds /app/ovcli.conf by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/1539
* fix: auth and system cmds by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1545
* fix(openclaw-plugin): clean up ov-healthcheck artifacts by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1540
* fix(context-engine): drop tool-only non-assistant messages instead of emitting empty content by @yeyitech in https://github.com/volcengine/OpenViking/pull/1512
* fix(memory): tolerate Python 3.10 ISO timestamps in extraction paths by @yeyitech in https://github.com/volcengine/OpenViking/pull/1524
* Remove ingestReplyAssist feature and all related config, logic, and t… by @wlff123 in https://github.com/volcengine/OpenViking/pull/1564

## New Contributors
* @Gujiassh made their first contribution in https://github.com/volcengine/OpenViking/pull/1370
* @lRoccoon made their first contribution in https://github.com/volcengine/OpenViking/pull/1362
* @sentisso made their first contribution in https://github.com/volcengine/OpenViking/pull/1393
* @officialasishkumar made their first contribution in https://github.com/volcengine/OpenViking/pull/1380
* @caisirius made their first contribution in https://github.com/volcengine/OpenViking/pull/1359
* @Hinotoi-agent made their first contribution in https://github.com/volcengine/OpenViking/pull/1396
* @yeyitech made their first contribution in https://github.com/volcengine/OpenViking/pull/1415
* @t0saki made their first contribution in https://github.com/volcengine/OpenViking/pull/1353
* @xiaobin83 made their first contribution in https://github.com/volcengine/OpenViking/pull/696
* @A0nameless0man made their first contribution in https://github.com/volcengine/OpenViking/pull/1484
* @ehz0ah made their first contribution in https://github.com/volcengine/OpenViking/pull/1458
* @PowerfulLxx made their first contribution in https://github.com/volcengine/OpenViking/pull/1502
* @duyua9 made their first contribution in https://github.com/volcengine/OpenViking/pull/1499

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.9


## v0.3.8 (2026-04-15)

- Date: 2026-04-15
- Tag: `v0.3.8`
- Compare: https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8

## 中文说明

OpenViking v0.3.8 主要聚焦于 Memory V2、Agent/插件生态增强，以及一批配置、检索和稳定性修复。本次版本共整理了 35 个变更项，覆盖 Memory V2、CLI、OpenClaw、Codex、VikingBot、检索性能和部署体验，并包含 8 位新贡献者。

### Memory V2 专题

Memory V2 是 v0.3.8 的核心主题之一。本节重点介绍其记忆格式设计，以及模板化、结构化更新和可扩展性带来的架构优化。

- 记忆格式：
  - Memory V2 不再把长期记忆限制在 v1 的固定类别里，而是改成基于 YAML 模板定义记忆类型。
  - 每种记忆模板都可以定义 `directory`、`filename_template`、`fields`、`merge_op`，必要时还可以定义 `content_template`。
  - 最终写入的仍然是可读的 Markdown 记忆文件，但文件路径、文件名、字段结构和更新方式都由模板控制，文件名也更语义化，便于导航和检索。
  - 内置模板已经覆盖 `profile`、`preferences`、`entities`、`events`、`cases`、`patterns`、`tools`、`skills`，并支持初始化 `soul.md`、`identity.md` 这类基础记忆文件。
- 重构与优化：
  - v1 的问题是记忆类别、抽取提示和合并逻辑相对固定，新增类型往往需要改核心代码；Memory V2 把这部分能力抽到模板层，新增记忆类型不再需要继续硬编码。
  - 抽取链路从“抽出来再做多轮合并”的思路，演进为基于 ReAct 编排的结构化 `write/edit/delete` 操作，更新路径更统一，也更适合后续扩展。
  - 通过 `memory.custom_templates_dir`，团队可以在不改主干逻辑的情况下扩展自己的业务记忆模板。
  - 本次版本同时补充了 Memory V2 full suite test，并修复了异常响应、越界范围、额外迭代终止等边界问题，使默认开启后的稳定性更可控。
- 对用户的直接价值：
  - 记忆文件更可读，目录结构和文件名更有语义。
  - 记忆类型更容易扩展，不再受限于固定类别。
  - 记忆更新逻辑更统一，后续做定制模板、行业知识卡片、事件索引、工具经验沉淀会更顺。
  - 在 LoCoMo 评测中，Memory V2 路径的准确率达到 80%，说明这套记忆格式和更新机制不仅更灵活，也已经具备实际效果支撑。

### 重点更新

- Memory V2 默认开启：
  - Memory V2 将长期记忆从 v1 的固定类别抽取，升级为基于 YAML 模板的记忆系统，核心链路由模板定义、ReAct 抽取编排和 `write/edit/delete` 结构化操作组成。
  - 记忆类型不再需要写死在核心代码里，内置模板可覆盖 `profile`、`preferences`、`entities`、`events`、`cases`、`patterns`、`tools`、`skills`，并可初始化 `soul.md`、`identity.md` 等基础记忆文件。
  - Memory V2 支持通过 `memory.custom_templates_dir` 扩展自定义模板，便于团队按业务场景定义新的记忆类型，而不必继续修改核心抽取逻辑。
  - 本次版本还补充了 Memory V2 full suite test，并修复了抽取循环中的异常响应、越界范围和额外迭代终止等边界问题，默认开启后更适合直接投入真实对话流量。
- 本地部署与初始化体验：
  - 新增 `openviking-server init` 交互式向导，面向本地 Ollama 模型部署场景，支持自动检测环境、推荐模型、拉取模型并生成可用的 `ov.conf`。
  - `openviking-server doctor` 与服务端健康检查增强了对 Ollama 的识别和联通性检查，降低本地部署排障成本。
- 插件与 Agent 生态增强：
  - VikingBot 新增 MCP client 支持，可连接 `stdio`、`SSE`、`streamable HTTP` 三类 MCP 服务，把第三方工具并入代理运行时。
  - VikingBot 新增可按 channel 关闭 OpenViking 的配置，并修复 heartbeat 消息误入对话、过期 heartbeat 重复检查等问题。
  - 新增 Codex memory plugin 示例，提供 `openviking_recall`、`openviking_store`、`openviking_forget`、`openviking_health` 四个工具，方便在 Codex 中接入 OpenViking 长期记忆。
  - OpenClaw 插件新增统一的 `ov_import` 和 `ov_search`，并补强会话消息捕获、`tool_input` 透传、commit 等待超时和 trace 日志，提升接入稳定性。
- 配置与部署体验改进：
  - `ovcli.conf` 新增 `upload.ignore_dirs`，支持为 `add-resource` 配置默认忽略目录。
  - rerank 配置支持 `extra_headers`，便于对接 OpenAI 兼容 provider、代理层或网关。
  - AGFS S3 插件默认启用；同时在 S3/OSS 兼容场景新增 `disable_batch_delete`，改善与部分 S3 兼容服务的适配。
  - 移除了仓库中的历史 Go 依赖和 AGFS 第三方代码，简化了构建、打包和仓库维护。
- 性能与稳定性：
  - 优化大目录检索，跳过冗余 `target_directories` 作用域过滤，减少不必要的搜索开销。
  - 修复 overview 生成时仓库层级丢失、embedding message ID 序列化丢失、legacy session row 重载、watch task 控制文件保护等问题。
  - 改善 Claude Code memory plugin 的 Windows 兼容性，并修复 PDF 书签页码映射、OpenAI-like embedding 的 Matryoshka 报错、`VolcengineSparseEmbedder` 异步状态初始化，以及默认 Doubao embedding 模型更新等问题。
  - 修复内存提取循环中的异常响应处理、越界范围处理和额外迭代终止逻辑，减少记忆抽取异常；同时补充 Memory V2 全量测试与一批安全/兼容性测试。
- 其他补充：
  - 文件系统新增目录描述支持，可在 `mkdir` 场景下为目录补充语义信息。
  - 仓库同步更新了新的微信交流群二维码。

### 升级提示

- 如果你经常通过 CLI 导入目录资源，建议在 `ovcli.conf` 中配置 `upload.ignore_dirs`，减少无关目录上传。
- 如果你需要保留旧行为，可在 `ov.conf` 中显式设置 `"memory": { "version": "v1" }` 回退到 legacy memory pipeline。
- 如果你之前使用 `ov init` 或 `ov doctor`，请改用 `openviking-server init` 和 `openviking-server doctor`。
- 如果你使用 OpenRouter 或其他 OpenAI 兼容 rerank/VLM 服务，可以通过 `extra_headers` 注入平台要求的 Header。
- 如果你的对象存储是阿里云 OSS 或其他 S3 兼容实现，且批量删除存在兼容问题，可开启 `storage.agfs.s3.disable_batch_delete`。
- 如果你在做 Agent 集成，建议查看 `examples/codex-memory-plugin` 与 `examples/openclaw-plugin` 中的新示例和工具能力。

### 致谢

感谢所有为 v0.3.8 提交特性、修复和文档改进的贡献者。

## English Release Notes

OpenViking v0.3.8 focuses on Memory V2, stronger agent/plugin integrations, and a broad set of configuration, retrieval, and stability improvements. This release rolls up 35 tracked changes across Memory V2, the CLI, OpenClaw, Codex, VikingBot, retrieval performance, and local deployment, and it also welcomes 8 new contributors.

### Memory V2 Spotlight

Memory V2 is one of the central themes of v0.3.8. This section highlights its memory format, along with the architectural improvements brought by templating, structured updates, and extensibility.

- Format:
  - Memory V2 no longer treats long-term memory as a fixed set of hard-coded v1 categories. Instead, memory types are defined through YAML templates.
  - Each memory template can define `directory`, `filename_template`, `fields`, and `merge_op`, and can optionally provide a `content_template`.
  - The final output is still readable Markdown memory files, but the path layout, filenames, field structure, and update behavior are now template-driven. Filenames are also more semantic and easier to navigate.
  - Built-in templates already cover `profile`, `preferences`, `entities`, `events`, `cases`, `patterns`, `tools`, and `skills`, and can initialize baseline files such as `soul.md` and `identity.md`.
- Refactor and optimization:
  - In v1, memory categories, extraction prompts, and merge behavior were relatively fixed, so adding a new memory type usually meant changing core code. Memory V2 moves that flexibility into the template layer.
  - The update path evolves from a mostly extract-then-merge flow into a ReAct-orchestrated structured operation model built around `write/edit/delete`.
  - Through `memory.custom_templates_dir`, teams can extend memory behavior with domain-specific templates without modifying the main extraction pipeline.
  - This release also adds a full Memory V2 test suite and fixes extraction edge cases around unexpected responses, out-of-bounds ranges, and extended-iteration termination, which matters now that the path is default-on.
- What users get from this:
  - More readable memory files with more meaningful directory and filename structure.
  - A memory system that is easier to extend beyond a fixed category list.
  - A more uniform update pipeline for future custom templates, event indexing, knowledge cards, and tool/skill experience capture.
  - In LoCoMo evaluation, the Memory V2 path reached 80% accuracy, which is a useful signal that the new format and update model are not only more flexible but also practically effective.

### Highlights

- Memory V2 by default:
  - Memory V2 moves long-term memory beyond the fixed-category v1 pipeline into a YAML-templated system built around schema-defined memory types, ReAct-style extraction orchestration, and structured `write/edit/delete` operations.
  - Memory types are no longer hard-coded in the core extractor. Built-in templates cover `profile`, `preferences`, `entities`, `events`, `cases`, `patterns`, `tools`, and `skills`, and can initialize baseline files such as `soul.md` and `identity.md`.
  - Teams can extend the system through `memory.custom_templates_dir`, making it practical to define domain-specific memory types without changing core extraction code.
  - This release also adds a full Memory V2 test suite and fixes several extraction edge cases, making the default-on rollout materially safer.
- Local deployment and setup:
  - Added `openviking-server init`, an interactive setup wizard for local Ollama-based deployments that can detect the environment, recommend models, pull them, and generate a valid `ov.conf`.
  - Improved `openviking-server doctor` and server-side readiness checks so Ollama availability is easier to diagnose in local deployments.
- Plugin and agent ecosystem improvements:
  - VikingBot now supports MCP clients and can connect to third-party MCP servers over `stdio`, `SSE`, and `streamable HTTP`.
  - VikingBot also adds per-channel disable controls for OpenViking and fixes heartbeat behavior so health-check traffic no longer pollutes conversations.
  - Added a Codex memory plugin example with `openviking_recall`, `openviking_store`, `openviking_forget`, and `openviking_health` tools for explicit long-term memory operations in Codex.
  - The OpenClaw plugin now exposes unified `ov_import` and `ov_search` flows, and also improves session capture, `tool_input` propagation, commit wait behavior, and trace logging.
- Config and deployment improvements:
  - `ovcli.conf` now supports `upload.ignore_dirs` so `add-resource` can ignore default directories out of the box.
  - Rerank config now supports `extra_headers`, which helps when working with OpenAI-compatible providers, gateways, or custom proxies.
  - AGFS S3 is now enabled by default, and `disable_batch_delete` is available for S3/OSS compatibility scenarios.
  - Removed legacy Go-based dependencies and AGFS third-party code from the repository, simplifying builds, packaging, and maintenance.
- Performance and reliability:
  - Improved retrieval performance for large directories by skipping redundant `target_directories` scope filters when safe.
  - Fixed repository hierarchy loss in semantic overviews, embedding message ID serialization issues, legacy session row reload behavior, and watch-task control file protection.
  - Also improved Windows compatibility for the Claude Code memory plugin, fixed PDF bookmark page mapping, resolved OpenAI-like embedding Matryoshka errors, corrected async client initialization in `VolcengineSparseEmbedder`, and updated the default Doubao embedding model.
  - Fixed several memory extraction edge cases, including unexpected VLM response shapes, out-of-bounds extraction ranges, and extended-iteration termination behavior, and added broader Memory V2 and security test coverage.
- Other additions:
  - The filesystem now supports directory descriptions in `mkdir` flows.
  - The repository also updates the WeChat community QR code.

### Upgrade Notes

- If you frequently upload directories through the CLI, consider setting `upload.ignore_dirs` in `ovcli.conf` to reduce noisy uploads.
- If you need legacy behavior, you can explicitly set `"memory": { "version": "v1" }` in `ov.conf` to fall back to the v1 memory pipeline.
- If you previously used `ov init` or `ov doctor`, switch to `openviking-server init` and `openviking-server doctor`.
- If you use OpenRouter or other OpenAI-compatible rerank/VLM providers, `extra_headers` can now be used to inject required headers.
- If you run against Alibaba Cloud OSS or other S3-compatible services with batch-delete quirks, consider enabling `storage.agfs.s3.disable_batch_delete`.
- If you are building agent integrations, review the updated examples under `examples/codex-memory-plugin` and `examples/openclaw-plugin`.

### Thanks

Thanks to all contributors who shipped features, fixes, and documentation improvements for v0.3.8.

## Detailed Changes / 详细变更

以下补充 v0.3.8 的详细 PR 列表与新贡献者信息，便于直接同步到 GitHub Release。

### What's Changed

* reorg: remove golang depends by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1339
* Feat/mem opt by @chenjw in https://github.com/volcengine/OpenViking/pull/1349
* fix: openai like embedding models fix, no more matryoshka error by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1350
* feat(bot): Add disable OpenViking config for channels. by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1352
* fix(config): point missing-config help messages to openviking.ai docs by @Gujiassh in https://github.com/volcengine/OpenViking/pull/1370
* fix(embedder): initialize async client state in VolcengineSparseEmbedder by @lRoccoon in https://github.com/volcengine/OpenViking/pull/1362
* feat(examples): add Codex memory plugin example by @0xble in https://github.com/volcengine/OpenViking/pull/1080
* feat(openclaw-plugin): add unified ov_import and ov_search by @jcp0578 in https://github.com/volcengine/OpenViking/pull/1369
* feat(bot): add MCP client support (port from HKUDS/nanobot v0.1.5) by @ponsde in https://github.com/volcengine/OpenViking/pull/1392
* feat(eval):Readme add qa by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1400
* feat(cli): support for default file/dir ignore config in `ovcli.conf` by @sentisso in https://github.com/volcengine/OpenViking/pull/1393
* benchmark: add LoCoMo evaluation for Supermemory by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1401
* fix(embedder): report configured provider in slow-call logs by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1403
* fix(queue): preserve embedding message ids across serialization by @officialasishkumar in https://github.com/volcengine/OpenViking/pull/1380
* test(security): add unit tests for network_guard and zip_safe modules by @sjhddh in https://github.com/volcengine/OpenViking/pull/1395
* fix(semantic): preserve repository hierarchy in overviews by @chethanuk in https://github.com/volcengine/OpenViking/pull/1376
* fix(tests): align pytest coverage docs with required setup (#1259) by @chethanuk in https://github.com/volcengine/OpenViking/pull/1373
* feat: rerank support extra headers by @caisirius in https://github.com/volcengine/OpenViking/pull/1359
* fix: reload legacy session rows by @chethanuk in https://github.com/volcengine/OpenViking/pull/1365
* fix: protect global watch-task control files from non-root access by @Hinotoi-agent in https://github.com/volcengine/OpenViking/pull/1396
* fix(agfs): enable agfs s3 plugin default by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/1408
* fix(claude-code-memory-plugin): improve Windows compatibility by @Castor6 in https://github.com/volcengine/OpenViking/pull/1249
* fix(pdf): resolve bookmark page mapping by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1412
* fix: update observer test to use /models endpoint instead of non-existent /vlm by @kaisongli in https://github.com/volcengine/OpenViking/pull/1407
* fix(openclaw-plugin): extend default Phase 2 commit wait timeout by @yeyitech in https://github.com/volcengine/OpenViking/pull/1415
* pref(retrieve): Optimize the search performance of larger directories by skipping redundant target_directories scope by @sponge225 in https://github.com/volcengine/OpenViking/pull/1426
* Add third_party directory to Dockerfile by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1433
* Fix/openclaw addmsg by @chenjw in https://github.com/volcengine/OpenViking/pull/1391
* feat(bot):Heartbeat fix by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1434
* feat: add `openviking-server init` interactive setup wizard for local Ollama model deployment by @t0saki in https://github.com/volcengine/OpenViking/pull/1353
* fix(volcengine): update default doubao embedding model by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1438
* feat: add Memory V2 full suite test by @kaisongli in https://github.com/volcengine/OpenViking/pull/1354
* update new wechat group qr code by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/1440
* feat(filesystem): support directory descriptions on mkdir by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1443
* feat(memory): default to memory v2 by @chenjw in https://github.com/volcengine/OpenViking/pull/1445

### New Contributors

* @Gujiassh made their first contribution in https://github.com/volcengine/OpenViking/pull/1370
* @lRoccoon made their first contribution in https://github.com/volcengine/OpenViking/pull/1362
* @sentisso made their first contribution in https://github.com/volcengine/OpenViking/pull/1393
* @officialasishkumar made their first contribution in https://github.com/volcengine/OpenViking/pull/1380
* @caisirius made their first contribution in https://github.com/volcengine/OpenViking/pull/1359
* @Hinotoi-agent made their first contribution in https://github.com/volcengine/OpenViking/pull/1396
* @yeyitech made their first contribution in https://github.com/volcengine/OpenViking/pull/1415
* @t0saki made their first contribution in https://github.com/volcengine/OpenViking/pull/1353

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.5...v0.3.8


## v0.3.5 (2026-04-10)

## What's Changed
* fix(memory): define config before v2 memory lock retry settings access by @heaoxiang-ai in https://github.com/volcengine/OpenViking/pull/1317
* fix: 优化测试关键词匹配和移除 Release Approval Gate by @kaisongli in https://github.com/volcengine/OpenViking/pull/1313
* fix: sanitize internal error details in bot proxy responses by @sjhddh in https://github.com/volcengine/OpenViking/pull/1310
* feat: add scenario-based API tests  by @kaisongli in https://github.com/volcengine/OpenViking/pull/1303
* fix(security): remove leaked token from settings.py by @kaisongli in https://github.com/volcengine/OpenViking/pull/1319
* Fix/add resource cover by @myysy in https://github.com/volcengine/OpenViking/pull/1321
* Revert "Fix/add resource cover" by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1322
* fix: litellm embedding dimension adapts by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1323
* ci: optimize runner usage with conditional OS matrix and parallel limit by @kaisongli in https://github.com/volcengine/OpenViking/pull/1327
* fix(bot):Response language, Multi user memory commit by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1329
* ci: remove lite and full test workflows by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1331
* docs: fix docker deployment by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1332
* docs(openclaw-plugin): add health check tools guide by @mrj666 in https://github.com/volcengine/OpenViking/pull/1326
* fix(queue): expose re-enqueue counts in queue status by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1337
* feat(s3fs): add disable_batch_delete option for OSS compatibility by @yuan7he in https://github.com/volcengine/OpenViking/pull/1333
* Fix/add resource cover by @myysy in https://github.com/volcengine/OpenViking/pull/1338
* afterTurn: store messages with actual roles and skip heartbeat messages by @wlff123 in https://github.com/volcengine/OpenViking/pull/1340
* fix: fall back to prefix filters for volcengine path scope by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/1342
* fix(session): auto-create missing sessions on first add by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1348
* Fix/api test issues by @kaisongli in https://github.com/volcengine/OpenViking/pull/1341
* fix: derive context_type from URI in index_resource by @yc111233 in https://github.com/volcengine/OpenViking/pull/1346

## New Contributors
* @sjhddh made their first contribution in https://github.com/volcengine/OpenViking/pull/1310
* @mrj666 made their first contribution in https://github.com/volcengine/OpenViking/pull/1326
* @yuan7he made their first contribution in https://github.com/volcengine/OpenViking/pull/1333

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.4...v0.3.5


 ## 更新内容

  - 修复(memory)：在访问 v2 memory 锁重试配置前先定义 config，作者 @heaoxiang-ai，见 https://github.com/volcengine/OpenViking/pull/1317
  - 修复：优化测试关键词匹配，并移除 Release Approval Gate，作者 @kaisongli，见 https://github.com/volcengine/OpenViking/pull/1313
  - 修复：对 bot 代理响应中的内部错误详情进行脱敏处理，作者 @sjhddh，见 https://github.com/volcengine/OpenViking/pull/1310
  - 新增：添加基于场景的 API 测试，作者 @kaisongli，见 https://github.com/volcengine/OpenViking/pull/1303
  - 修复(security)：从 settings.py 中移除泄露的 token，作者 @kaisongli，见 https://github.com/volcengine/OpenViking/pull/1319
  - 修复/新增资源封面，作者 @myysy，见 https://github.com/volcengine/OpenViking/pull/1321
  - 回滚“修复/新增资源封面”，作者 @MaojiaSheng，见 https://github.com/volcengine/OpenViking/pull/1322
  - 修复：LiteLLM 的 embedding 维度自适应，作者 @MaojiaSheng，见 https://github.com/volcengine/OpenViking/pull/1323
  - CI：通过条件式 OS 矩阵和并行数量限制优化 runner 使用，作者 @kaisongli，见 https://github.com/volcengine/OpenViking/pull/1327
  - 修复(bot)：响应语言和多用户记忆提交，作者 @yeshion23333，见 https://github.com/volcengine/OpenViking/pull/1329
  - CI：移除 lite 和 full 测试工作流，作者 @zhoujh01，见 https://github.com/volcengine/OpenViking/pull/1331
  - 文档：修复 Docker 部署说明，作者 @MaojiaSheng，见 https://github.com/volcengine/OpenViking/pull/1332
  - 文档(openclaw-plugin)：新增健康检查工具指南，作者 @mrj666，见 https://github.com/volcengine/OpenViking/pull/1326
  - 修复(queue)：在队列状态中暴露重新入队次数，作者 @qin-ctx，见 https://github.com/volcengine/OpenViking/pull/1337
  - 新增(s3fs)：添加 disable_batch_delete 选项以兼容 OSS，作者 @yuan7he，见 https://github.com/volcengine/OpenViking/pull/1333
  - 修复/新增资源封面，作者 @myysy，见 https://github.com/volcengine/OpenViking/pull/1338
  - afterTurn：按实际角色存储消息，并跳过心跳消息，作者 @wlff123，见 https://github.com/volcengine/OpenViking/pull/1340
  - 修复：为火山引擎路径作用域回退到前缀过滤器，作者 @haosenwang1018，见 https://github.com/volcengine/OpenViking/pull/1342
  - 修复(session)：首次添加时自动创建缺失的会话，作者 @qin-ctx，见 https://github.com/volcengine/OpenViking/pull/1348
  - 修复：解决 API 测试问题，作者 @kaisongli，见 https://github.com/volcengine/OpenViking/pull/1341
  - 修复：在 index_resource 中从 URI 推导 context_type，作者 @yc111233，见 https://github.com/volcengine/OpenViking/pull/1346

  ## 新贡献者

  - @sjhddh 在 https://github.com/volcengine/OpenViking/pull/1310 完成了首次贡献
  - @mrj666 在 https://github.com/volcengine/OpenViking/pull/1326 完成了首次贡献
  - @yuan7he 在 https://github.com/volcengine/OpenViking/pull/1333 完成了首次贡献

  完整更新日志：https://github.com/volcengine/OpenViking/compare/v0.3.4...v0.3.5


## v0.3.4 (2026-04-09)

# OpenViking v0.3.4

本次 `v0.3.4` 版本主要围绕 OpenClaw 插件与评测链路、Memory / 存储与写入稳定性、安全边界与网络控制，以及发布流程、Docker 与 CI 体系做了持续增强。相较 `v0.3.3`，这一版本一方面补齐了 OpenClaw 默认行为、eval 脚本、provider 扩展和多项兼容性问题，另一方面也显著加强了会话写入等待、锁与压缩器重试、HTTP 资源导入 SSRF 防护、trusted mode 限制和整体发布交付链路。

## 版本亮点

- **OpenClaw 插件与评测体验继续完善**：调整 `recallPreferAbstract` 与 `ingestReplyAssist` 的默认值以降低意外行为，[PR #1204](https://github.com/volcengine/OpenViking/pull/1204) [PR #1206](https://github.com/volcengine/OpenViking/pull/1206)；新增 OpenClaw eval shell 脚本并修复评测导入问题，[PR #1287](https://github.com/volcengine/OpenViking/pull/1287) [PR #1305](https://github.com/volcengine/OpenViking/pull/1305)；同时补齐 autoRecall 搜索范围与查询清洗、截断能力，[PR #1225](https://github.com/volcengine/OpenViking/pull/1225) [PR #1297](https://github.com/volcengine/OpenViking/pull/1297)。
- **Memory、会话写入与运行时稳定性明显增强**：写接口引入 request-scoped wait 机制，[PR #1212](https://github.com/volcengine/OpenViking/pull/1212)；补强 PID lock 回收、召回阈值绕过、孤儿 compressor 引用、async contention 和 memory 语义批处理等问题，[PR #1211](https://github.com/volcengine/OpenViking/pull/1211) [PR #1301](https://github.com/volcengine/OpenViking/pull/1301) [PR #1304](https://github.com/volcengine/OpenViking/pull/1304)；并继续优化 memory v2 compressor 锁重试控制，[PR #1275](https://github.com/volcengine/OpenViking/pull/1275)。
- **安全与网络边界进一步收紧**：HTTP 资源导入补齐私网 SSRF 防护，[PR #1133](https://github.com/volcengine/OpenViking/pull/1133)；trusted mode 在无 API key 时被限制为仅允许 localhost，[PR #1279](https://github.com/volcengine/OpenViking/pull/1279)；embedding circuit breaker 与日志抑制也变得可配置，[PR #1277](https://github.com/volcengine/OpenViking/pull/1277)。
- **生态与集成能力继续扩展**：新增 Volcengine Vector DB STS Token 支持，[PR #1268](https://github.com/volcengine/OpenViking/pull/1268)；新增 MiniMax-M2.7 与 MiniMax-M2.7-highspeed provider 支持，[PR #1284](https://github.com/volcengine/OpenViking/pull/1284)；AST 侧补充 Lua parser，[PR #1286](https://github.com/volcengine/OpenViking/pull/1286)；Bot 新增 channel mention 能力，[PR #1272](https://github.com/volcengine/OpenViking/pull/1272)。
- **发布、Docker 与 CI 链路更稳健**：发布时自动更新 `main` 并增加 Docker Hub push，[PR #1229](https://github.com/volcengine/OpenViking/pull/1229)；修复 Docker maturin 路径并将 Gemini optional dependency 纳入镜像，[PR #1295](https://github.com/volcengine/OpenViking/pull/1295) [PR #1254](https://github.com/volcengine/OpenViking/pull/1254)；CI 侧优化 API matrix、补充 timeout/SMTP 通知并修复 reusable workflow / action 问题，[PR #1281](https://github.com/volcengine/OpenViking/pull/1281) [PR #1293](https://github.com/volcengine/OpenViking/pull/1293) [PR #1300](https://github.com/volcengine/OpenViking/pull/1300) [PR #1302](https://github.com/volcengine/OpenViking/pull/1302) [PR #1307](https://github.com/volcengine/OpenViking/pull/1307)。

## 升级说明

- OpenClaw 插件默认配置发生调整：`recallPreferAbstract` 与 `ingestReplyAssist` 现在默认均为 `false`。如果你之前依赖默认开启行为，升级后需要显式配置，见 [PR #1204](https://github.com/volcengine/OpenViking/pull/1204) 和 [PR #1206](https://github.com/volcengine/OpenViking/pull/1206)。
- HTTP 资源导入现在默认更严格地防护私网 SSRF；如果你有合法的内网资源采集场景，升级时建议复核现有接入方式与白名单策略，见 [PR #1133](https://github.com/volcengine/OpenViking/pull/1133)。
- trusted mode 在未提供 API key 时已限制为仅允许 localhost 访问；如果你此前通过非本地地址使用 trusted mode，需要同步调整部署与鉴权配置，见 [PR #1279](https://github.com/volcengine/OpenViking/pull/1279)。
- 写接口现在引入 request-scoped wait 机制，相关调用在并发写入下的等待与返回时机会更一致；如果你有依赖旧时序的外部编排逻辑，建议升级后复核行为，见 [PR #1212](https://github.com/volcengine/OpenViking/pull/1212)。
- server 已支持 `host=none` 以使用双栈网络；如果你在 IPv4/IPv6 混合环境中部署，可考虑调整监听配置，见 [PR #1273](https://github.com/volcengine/OpenViking/pull/1273)。
- Docker 镜像已纳入 Gemini optional dependency，并修复了 maturin 路径问题；如果你维护自定义镜像或发布流水线，建议同步检查构建脚本，见 [PR #1254](https://github.com/volcengine/OpenViking/pull/1254) 和 [PR #1295](https://github.com/volcengine/OpenViking/pull/1295)。

## 详细变更

### OpenClaw 插件、评测与集成

- 默认将 openclaw-plugin 的 `recallPreferAbstract` 设为 `false`，[PR #1204](https://github.com/volcengine/OpenViking/pull/1204) by @wlff123
- 修复 eval 中的 async import 问题，[PR #1203](https://github.com/volcengine/OpenViking/pull/1203) by @yeshion23333
- 默认将 openclaw-plugin 的 `ingestReplyAssist` 设为 `false`，[PR #1206](https://github.com/volcengine/OpenViking/pull/1206) by @wlff123
- 向 OpenAIVLM client 增加 timeout 参数，[PR #1208](https://github.com/volcengine/OpenViking/pull/1208) by @highland0971
- 防止 redo recovery 中阻塞式 VLM 调用导致启动 hang 住，[PR #1226](https://github.com/volcengine/OpenViking/pull/1226) by @mvanhorn
- 为插件 autoRecall 增加 `skills` 搜索范围，[PR #1225](https://github.com/volcengine/OpenViking/pull/1225) by @mvanhorn
- 新增 bot channel mention 能力，[PR #1272](https://github.com/volcengine/OpenViking/pull/1272) by @yeshion23333
- 新增 OpenClaw eval shell 脚本，[PR #1287](https://github.com/volcengine/OpenViking/pull/1287) by @yeshion23333
- 新增 `add_message` 的 create time，[PR #1288](https://github.com/volcengine/OpenViking/pull/1288) by @wlff123
- 对 OpenClaw recall query 做清洗并限制长度，[PR #1297](https://github.com/volcengine/OpenViking/pull/1297) by @qin-ctx
- 修复 OpenClaw eval 导入到 OpenViking 时默认 user 的问题，[PR #1305](https://github.com/volcengine/OpenViking/pull/1305) by @yeshion23333

### Memory、会话、存储与运行时

- 修复明文文件长度小于 4 字节时 decrypt 抛出 `Ciphertext too short` 的问题，[PR #1163](https://github.com/volcengine/OpenViking/pull/1163) by @yc111233
- 为短明文加密补充单元测试，[PR #1217](https://github.com/volcengine/OpenViking/pull/1217) by @baojun-zhang
- 修复 PID lock 回收、召回阈值绕过与 orphaned compressor refs 等问题，[PR #1211](https://github.com/volcengine/OpenViking/pull/1211) by @JasonOA888
- queuefs 去重 memory semantic parent enqueue，[PR #792](https://github.com/volcengine/OpenViking/pull/792) by @Protocol-zero-0
- 为 decrypt 短明文场景补充回归测试，[PR #1223](https://github.com/volcengine/OpenViking/pull/1223) by @yc111233
- 为写接口实现 request-scoped wait 机制，[PR #1212](https://github.com/volcengine/OpenViking/pull/1212) by @zhoujh01
- 将 agfs 重组为 Rust 实现的 ragfs，[PR #1221](https://github.com/volcengine/OpenViking/pull/1221) by @MaojiaSheng
- 修复 session `_wait_for_previous_archive_done` 无限挂起问题，[PR #1235](https://github.com/volcengine/OpenViking/pull/1235) by @yc111233
- 修复 `#1238`、`#1242` 和 `#1232` 涉及的问题，[PR #1243](https://github.com/volcengine/OpenViking/pull/1243) by @MaojiaSheng
- Memory 侧继续做性能优化，[PR #1159](https://github.com/volcengine/OpenViking/pull/1159) by @chenjw
- 回滚 session `_wait_for_previous_archive_done` timeout 改动，[PR #1265](https://github.com/volcengine/OpenViking/pull/1265) by @MaojiaSheng
- memory v2 compressor 改进锁重试控制，[PR #1275](https://github.com/volcengine/OpenViking/pull/1275) by @heaoxiang-ai
- 降低 embedder 在 session 流程中的 async contention，[PR #1301](https://github.com/volcengine/OpenViking/pull/1301) by @qin-ctx
- 在 `_process_memory_directory` 中批量处理 semantic memory，[PR #1304](https://github.com/volcengine/OpenViking/pull/1304) by @chuanbao666

### 安全、网络与 Provider 生态

- 加固 HTTP 资源导入，防止私网 SSRF，[PR #1133](https://github.com/volcengine/OpenViking/pull/1133) by @13ernkastel
- Volcengine Vector DB 支持 STS Token，[PR #1268](https://github.com/volcengine/OpenViking/pull/1268) by @baojun-zhang
- server 支持 `host none` 以启用双栈网络，[PR #1273](https://github.com/volcengine/OpenViking/pull/1273) by @zhoujh01
- 无 API key 时将 trusted mode 限制为 localhost，[PR #1279](https://github.com/volcengine/OpenViking/pull/1279) by @zhoujh01
- embedding circuit breaker 与日志抑制支持配置化，[PR #1277](https://github.com/volcengine/OpenViking/pull/1277) by @baojun-zhang
- 新增 MiniMax-M2.7 与 MiniMax-M2.7-highspeed provider 支持，[PR #1284](https://github.com/volcengine/OpenViking/pull/1284) by @octo-patch
- 为 AST 新增 Lua parser 支持，[PR #1286](https://github.com/volcengine/OpenViking/pull/1286) by @Shawn-cf-o
- 为 Lark 集成补充 `lark-oapi`，[PR #1285](https://github.com/volcengine/OpenViking/pull/1285) by @zhoujh01

### 文档、打包、Docker 与 CI

- 增加 Claude Code Memory Plugin 示例链接与中文文档，[PR #1228](https://github.com/volcengine/OpenViking/pull/1228) by @Castor6
- `add-resource` 处理非 UTF-8 文件名，[PR #1224](https://github.com/volcengine/OpenViking/pull/1224) by @mvanhorn
- 发布时更新 main，并增加 Docker Hub push，[PR #1229](https://github.com/volcengine/OpenViking/pull/1229) by @MaojiaSheng
- 更新微信群二维码，[PR #1282](https://github.com/volcengine/OpenViking/pull/1282) by @yuyaoyoyo-svg
- 将 API 测试矩阵从 5 个 channel 优化为 3 个，[PR #1281](https://github.com/volcengine/OpenViking/pull/1281) by @kaisongli
- 增加中英文 prompt guide 文档，[PR #1292](https://github.com/volcengine/OpenViking/pull/1292) by @zhoujh01
- 增加面向 mem0 的 LoCoMo benchmark 脚本，[PR #1290](https://github.com/volcengine/OpenViking/pull/1290) by @yangxinxin-7
- 修复 Docker 中的 maturin 路径问题，[PR #1295](https://github.com/volcengine/OpenViking/pull/1295) by @zhoujh01
- 在 Docker 镜像中纳入 Gemini optional dependency，[PR #1254](https://github.com/volcengine/OpenViking/pull/1254) by @SeeYangZhi
- CI 增加 timeout 与 SMTP 失败通知，[PR #1293](https://github.com/volcengine/OpenViking/pull/1293) by @kaisongli
- 修复 reusable build workflow 的 YAML block 问题，[PR #1300](https://github.com/volcengine/OpenViking/pull/1300) by @zhoujh01
- 新增 ovpack 递归向量化所有导入文档的能力，[PR #1294](https://github.com/volcengine/OpenViking/pull/1294) by @sponge225
- 改进 oc2ov 自动化测试，[PR #1280](https://github.com/volcengine/OpenViking/pull/1280) by @kaisongli
- 移除不允许的 notify-failure action，[PR #1302](https://github.com/volcengine/OpenViking/pull/1302) by @kaisongli
- 修复 CI，[PR #1307](https://github.com/volcengine/OpenViking/pull/1307) by @zhoujh01

## 新贡献者

- @highland0971 made their first contribution in [PR #1208](https://github.com/volcengine/OpenViking/pull/1208)
- @yc111233 made their first contribution in [PR #1163](https://github.com/volcengine/OpenViking/pull/1163)
- @octo-patch made their first contribution in [PR #1284](https://github.com/volcengine/OpenViking/pull/1284)
- @SeeYangZhi made their first contribution in [PR #1254](https://github.com/volcengine/OpenViking/pull/1254)

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.3...v0.3.4


## What's Changed
* fix(openclaw-plugin): default recallPreferAbstract to false by @wlff123 in https://github.com/volcengine/OpenViking/pull/1204
* fix(eval) Fix import async by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1203
* fix(openclaw-plugin): default ingestReplyAssist to false by @wlff123 in https://github.com/volcengine/OpenViking/pull/1206
* fix: add timeout parameter to OpenAIVLM client by @highland0971 in https://github.com/volcengine/OpenViking/pull/1208
* fix: decrypt raises 'Ciphertext too short' on plaintext files shorter than 4 bytes by @yc111233 in https://github.com/volcengine/OpenViking/pull/1163
* feat(encryption): add encrypt unit test  for plaintext shorter than t… by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/1217
* fix: PID lock recycle, recall threshold bypass, orphaned compressor refs by @JasonOA888 in https://github.com/volcengine/OpenViking/pull/1211
* fix(queuefs): dedupe memory semantic parent enqueues (#769) by @Protocol-zero-0 in https://github.com/volcengine/OpenViking/pull/792
* docs: add Claude Code Memory Plugin example link and Chinese docs by @Castor6 in https://github.com/volcengine/OpenViking/pull/1228
* fix: prevent startup hang from blocking VLM call in redo recovery by @mvanhorn in https://github.com/volcengine/OpenViking/pull/1226
* fix(cli): handle non-UTF-8 filenames in add-resource by @mvanhorn in https://github.com/volcengine/OpenViking/pull/1224
* test(crypto): add regression tests for decrypting short plaintext files by @yc111233 in https://github.com/volcengine/OpenViking/pull/1223
* Implement request-scoped wait for write APIs by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1212
* reorg:  Rewrite agfs to ragfs with rust by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1221
* fix: update main when release, and add docker hub push by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1229
* fix security: feat(resources): harden HTTP resource ingestion against private-network SSRF by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1133
* fix(session): add timeout to _wait_for_previous_archive_done to prevent infinite hang by @yc111233 in https://github.com/volcengine/OpenViking/pull/1235
* fix: #1238 and #1242 and #1232 by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1243
* Feature/memory opt by @chenjw in https://github.com/volcengine/OpenViking/pull/1159
* fix(plugin): add skills to autoRecall search scope by @mvanhorn in https://github.com/volcengine/OpenViking/pull/1225
* Revert "fix(session): add timeout to _wait_for_previous_archive_done to prevent infinite hang" by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1265
* feat(bot): channel mention by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1272
* feat(storage):  volcengine vector db support sts token  by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/1268
* feat(server): support host none to use dual stack network by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1273
* feat(auth): Restrict trusted mode without API key to localhost by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1279
* feature(memory): improve memory v2 lock retry controls in compressor by @heaoxiang-ai in https://github.com/volcengine/OpenViking/pull/1275
* fix(security): configurable embedding circuit breaker & log suppression by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/1277
* update wechat group qrcode by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/1282
* ci: optimize API test matrix from 5 to 3 channels by @kaisongli in https://github.com/volcengine/OpenViking/pull/1281
* feat: add MiniMax-M2.7 and MiniMax-M2.7-highspeed provider support by @octo-patch in https://github.com/volcengine/OpenViking/pull/1284
* feat(eval): add openclaw eval sh by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1287
* add create time in add_message by @wlff123 in https://github.com/volcengine/OpenViking/pull/1288
* fix(lark): add lark-oapi by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1285
* docs(prompt): Docs/prompt guides zh en by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1292
* benchmark: add LoCoMo evaluation scripts for mem0 by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1290
* fix(docker): Fix docker maturin path 1271 by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1295
* Include gemini optional dependency in Docker image by @SeeYangZhi in https://github.com/volcengine/OpenViking/pull/1254
* ci: add timeout and SMTP failure notification by @kaisongli in https://github.com/volcengine/OpenViking/pull/1293
* fix(openclaw): sanitize and cap recall queries by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1297
* fix(ci): repair reusable build workflow yaml blocks by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1300
* feat(ast): add Lua AST parser support by @Shawn-cf-o in https://github.com/volcengine/OpenViking/pull/1286
* fix(ci): remove disallowed notify-failure action by @kaisongli in https://github.com/volcengine/OpenViking/pull/1302
* fix(embedder): reduce async contention in session flows by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1301
* Feat(ovpack): recursively vectorize all imported docs by @sponge225 in https://github.com/volcengine/OpenViking/pull/1294
* feat: add oc2ov auto test improvements by @kaisongli in https://github.com/volcengine/OpenViking/pull/1280
* fix(eval): OpenClaw eval, import to ov use default user by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1305
* fix(memory): batch semantic processing in _process_memory_directory by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/1304
* Fix ci by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1307

## New Contributors
* @highland0971 made their first contribution in https://github.com/volcengine/OpenViking/pull/1208
* @yc111233 made their first contribution in https://github.com/volcengine/OpenViking/pull/1163
* @octo-patch made their first contribution in https://github.com/volcengine/OpenViking/pull/1284
* @SeeYangZhi made their first contribution in https://github.com/volcengine/OpenViking/pull/1254

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.3...v0.3.4


## v0.3.3 (2026-04-03)

# OpenViking v0.3.3

本次 `v0.3.3` 版本主要围绕评测与写入能力、OpenClaw 插件与集成体验、会话与资源导入链路、稳定性与安全性，以及测试与 CI 体系做了集中增强。相较 `v0.3.2`，这一版本一方面补齐了 benchmark / eval / write 等面向落地使用的能力，另一方面也明显加强了 OpenClaw 插件的可观测性、健康检查、容错和测试覆盖，同时修复了若干会直接影响生产使用的锁处理、任务权限、ZIP 编码、资源导入与 embedder 参数问题。

## Highlights

- **评测与写入能力继续扩展**：新增 RAG benchmark 评测框架 [PR #825](https://github.com/volcengine/OpenViking/pull/825)，补充 OpenClaw 的 LoCoMo eval 脚本与说明 [PR #1152](https://github.com/volcengine/OpenViking/pull/1152)，并新增内容写入接口 [PR #1151](https://github.com/volcengine/OpenViking/pull/1151)。
- **OpenClaw 插件可用性显著增强**：补充架构文档与图示 [PR #1145](https://github.com/volcengine/OpenViking/pull/1145)，安装器不再覆盖 `gateway.mode` [PR #1149](https://github.com/volcengine/OpenViking/pull/1149)，新增端到端 healthcheck 工具 [PR #1180](https://github.com/volcengine/OpenViking/pull/1180)，支持 bypass session patterns [PR #1194](https://github.com/volcengine/OpenViking/pull/1194)，并在 OpenViking 故障时避免阻塞 OpenClaw [PR #1158](https://github.com/volcengine/OpenViking/pull/1158)。
- **测试与 CI 覆盖大幅补强**：OpenClaw 插件新增大规模单测套件 [PR #1144](https://github.com/volcengine/OpenViking/pull/1144)，补充 e2e 测试 [PR #1154](https://github.com/volcengine/OpenViking/pull/1154)，新增 OpenClaw2OpenViking 集成测试与 CI 流水线 [PR #1168](https://github.com/volcengine/OpenViking/pull/1168)。
- **会话、解析与导入链路更稳健**：支持创建 session 时指定 `session_id` [PR #1074](https://github.com/volcengine/OpenViking/pull/1074)，CLI 聊天端点优先级与 `grep --exclude-uri/-x` 能力得到增强 [PR #1143](https://github.com/volcengine/OpenViking/pull/1143) [PR #1174](https://github.com/volcengine/OpenViking/pull/1174)，目录导入 UX / 正确性与扫描 warning 契约也进一步改善 [PR #1197](https://github.com/volcengine/OpenViking/pull/1197) [PR #1199](https://github.com/volcengine/OpenViking/pull/1199)。
- **稳定性与安全性继续加固**：修复任务 API ownership 泄露问题 [PR #1182](https://github.com/volcengine/OpenViking/pull/1182)，统一 stale lock 处理并补充 ownership checks [PR #1171](https://github.com/volcengine/OpenViking/pull/1171)，修复 ZIP 乱码 [PR #1173](https://github.com/volcengine/OpenViking/pull/1173)、embedder dimensions 透传 [PR #1183](https://github.com/volcengine/OpenViking/pull/1183)、语义 DAG 增量更新缺失 summary 场景 [PR #1177](https://github.com/volcengine/OpenViking/pull/1177) 等问题。

## Upgrade Notes

- OpenClaw 插件安装器不再写入 `gateway.mode`。如果你之前依赖安装流程自动改写该配置，升级后需要改为显式管理，见 [PR #1149](https://github.com/volcengine/OpenViking/pull/1149)。
- 如果你使用 `--with-bot` 进行安装或启动，失败时现在会直接返回错误码；依赖“失败但继续执行”行为的脚本需要同步调整，见 [PR #1175](https://github.com/volcengine/OpenViking/pull/1175)。
- 如果你接入 OpenAI Dense Embedder，自定义维度参数现在会正确传入 `embed()`；此前依赖默认维度行为的调用方建议复核配置，见 [PR #1183](https://github.com/volcengine/OpenViking/pull/1183)。
- `ov status` 现在会展示 embedding 与 rerank 模型使用情况，便于排障与环境核对，见 [PR #1191](https://github.com/volcengine/OpenViking/pull/1191)。
- 检索侧曾尝试加入基于 tags metadata 的 cross-subtree retrieval [PR #1162](https://github.com/volcengine/OpenViking/pull/1162)，但已在本版本窗口内回滚 [PR #1200](https://github.com/volcengine/OpenViking/pull/1200)，因此不应将其视为 `v0.3.3` 的最终可用能力。
- `litellm` 依赖范围更新为 `>=1.0.0,<1.83.1`，升级时建议同步检查锁文件与兼容性，见 [PR #1179](https://github.com/volcengine/OpenViking/pull/1179)。

## What's Changed

### Benchmark, Eval, CLI, and Writing

- 新增 RAG benchmark 系统评测框架，[PR #825](https://github.com/volcengine/OpenViking/pull/825) by @sponge225
- 优化 CLI chat endpoint 配置优先级，[PR #1143](https://github.com/volcengine/OpenViking/pull/1143) by @ruansheng8
- 新增 OpenClaw 的 LoCoMo eval 脚本与 README，[PR #1152](https://github.com/volcengine/OpenViking/pull/1152) by @yeshion23333
- 新增内容写入接口，[PR #1151](https://github.com/volcengine/OpenViking/pull/1151) by @zhoujh01
- `ov cli grep` 新增 `--exclude-uri` / `-x` 选项，[PR #1174](https://github.com/volcengine/OpenViking/pull/1174) by @heaoxiang-ai
- 更新 rerank 配置文档，[PR #1138](https://github.com/volcengine/OpenViking/pull/1138) by @ousugo

### OpenClaw Plugin, OpenCode Plugin, Bot, and Console

- 刷新 OpenClaw 插件架构文档并补充图示，[PR #1145](https://github.com/volcengine/OpenViking/pull/1145) by @qin-ctx
- 修复 OpenClaw 插件安装器不应写入 `gateway.mode`，[PR #1149](https://github.com/volcengine/OpenViking/pull/1149) by @LinQiang391
- 处理 OpenViking 故障时不再阻塞 OpenClaw，[PR #1158](https://github.com/volcengine/OpenViking/pull/1158) by @wlff123
- 新增 OpenClaw 插件端到端 healthcheck 工具，[PR #1180](https://github.com/volcengine/OpenViking/pull/1180) by @wlff123
- 新增 bypass session patterns 支持，[PR #1194](https://github.com/volcengine/OpenViking/pull/1194) by @Mijamind719
- 修复 OpenCode 插件在当前 main 上恢复 stale commit state 的问题，[PR #1187](https://github.com/volcengine/OpenViking/pull/1187) by @13ernkastel
- 新增 Bot 单通道（BotChannel）集成与 Werewolf demo，[PR #1196](https://github.com/volcengine/OpenViking/pull/1196) by @yeshion23333
- console 支持 account user agentid，[PR #1198](https://github.com/volcengine/OpenViking/pull/1198) by @zhoujh01

### Sessions, Retrieval, Parsing, and Resource Import

- 支持创建 session 时指定 `session_id`，[PR #1074](https://github.com/volcengine/OpenViking/pull/1074) by @likzn
- 修复 reindex 时 memory `context_type` 的 URI 匹配逻辑，[PR #1155](https://github.com/volcengine/OpenViking/pull/1155) by @deepakdevp
- ZIP 下载在可用时使用 `GITHUB_TOKEN`，[PR #1146](https://github.com/volcengine/OpenViking/pull/1146) by @jellespijker
- 统一异步 commit API 文档与示例，[PR #1188](https://github.com/volcengine/OpenViking/pull/1188) by @qin-ctx
- 目录导入时改善用户体验与正确性，[PR #1197](https://github.com/volcengine/OpenViking/pull/1197) by @yangxinxin-7
- 修复扫描 warning 契约与 Python SDK 默认值，[PR #1199](https://github.com/volcengine/OpenViking/pull/1199) by @yangxinxin-7
- 新增 `.inl` 文件扩展名支持，[PR #1176](https://github.com/volcengine/OpenViking/pull/1176) by @myysy
- 修复 semantic DAG 增量更新时 summary 缺失场景，[PR #1177](https://github.com/volcengine/OpenViking/pull/1177) by @myysy
- 引入 tags metadata 的 cross-subtree retrieval 尝试，[PR #1162](https://github.com/volcengine/OpenViking/pull/1162) by @13ernkastel
- 回滚 tags metadata 的 cross-subtree retrieval 变更，[PR #1200](https://github.com/volcengine/OpenViking/pull/1200) by @zhoujh01

### Stability, Runtime, and Security

- 新增 OpenClaw 插件大规模单测套件，[PR #1144](https://github.com/volcengine/OpenViking/pull/1144) by @huangxun375-stack
- 新增 `tests/ut/e2e` 端到端测试，[PR #1154](https://github.com/volcengine/OpenViking/pull/1154) by @huangxun375-stack
- 新增 OpenClaw2OpenViking 集成测试与 CI 流水线，[PR #1168](https://github.com/volcengine/OpenViking/pull/1168) by @kaisongli
- Windows 上因 FUSE 兼容性问题跳过 filesystem tests，[PR #1156](https://github.com/volcengine/OpenViking/pull/1156) by @kaisongli
- 统一 stale lock 处理并增加 ownership checks，[PR #1171](https://github.com/volcengine/OpenViking/pull/1171) by @qin-ctx
- 修复 ZIP 文件乱码问题，[PR #1173](https://github.com/volcengine/OpenViking/pull/1173) by @zhoujh01
- `--with-bot` 失败时改为显式退出并返回错误，[PR #1175](https://github.com/volcengine/OpenViking/pull/1175) by @MaojiaSheng
- 修复 OpenAI Dense Embedder 未透传 dimensions 的问题，[PR #1183](https://github.com/volcengine/OpenViking/pull/1183) by @LinQiang391
- 修复 Task API ownership 泄露安全问题，[PR #1182](https://github.com/volcengine/OpenViking/pull/1182) by @13ernkastel
- `ov status` 现在展示 embedding 与 rerank 模型使用情况，[PR #1191](https://github.com/volcengine/OpenViking/pull/1191) by @MaojiaSheng

### Build and Dependencies

- 更新 `litellm` 依赖范围到 `>=1.0.0,<1.83.1`，[PR #1179](https://github.com/volcengine/OpenViking/pull/1179) by @dependabot[bot]
- 升级 `actions/cache` 从 4 到 5，[PR #1178](https://github.com/volcengine/OpenViking/pull/1178) by @dependabot[bot]

## New Contributors

- @ruansheng8 made their first contribution in [PR #1143](https://github.com/volcengine/OpenViking/pull/1143)
- @jellespijker made their first contribution in [PR #1146](https://github.com/volcengine/OpenViking/pull/1146)
- @heaoxiang-ai made their first contribution in [PR #1174](https://github.com/volcengine/OpenViking/pull/1174)
- @ousugo made their first contribution in [PR #1138](https://github.com/volcengine/OpenViking/pull/1138)

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.2...v0.3.3

## What's Changed
* Feat(benchmark): Add benchmark/RAG : RAG system evaluation framework by @sponge225 in https://github.com/volcengine/OpenViking/pull/825
* docs(openclaw-plugin): refresh architecture guides with diagrams by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1145
* fix(openclaw-plugin): stop writing gateway.mode from installers by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1149
* test(openclaw-plugin): add comprehensive UT suite under tests/ut/ (27… by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1144
* feat(cli): improve chat endpoint configuration priority by @ruansheng8 in https://github.com/volcengine/OpenViking/pull/1143
* feat(eval): add locomo eval scripts for openclaw and readme by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1152
* add e2e test under tests/ut/e2e by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1154
* Handle OpenViking outages without blocking OpenClaw by @wlff123 in https://github.com/volcengine/OpenViking/pull/1158
* fix(ci): skip filesystem tests on Windows due to FUSE compatibility  by @kaisongli in https://github.com/volcengine/OpenViking/pull/1156
* feat(write): add content write interface by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1151
* fix(summarizer): use correct URI matching for memory context_type during reindex by @deepakdevp in https://github.com/volcengine/OpenViking/pull/1155
* fix(parser): use GITHUB_TOKEN for ZIP downloads if available by @jellespijker in https://github.com/volcengine/OpenViking/pull/1146
* feat: Add OpenClaw2OpenViking integration tests with CI pipeline by @kaisongli in https://github.com/volcengine/OpenViking/pull/1168
* fix(transaction): unify stale lock handling with ownership checks by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1171
* fix(zip): Fix garbled characters by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1173
* fix: exit with error if --with-bot fail by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1175
* feat(openclaw-plugin): add end-to-end healthcheck tool for OpenViking… by @wlff123 in https://github.com/volcengine/OpenViking/pull/1180
* feat(parse): add support for .inl file extension by @myysy in https://github.com/volcengine/OpenViking/pull/1176
* fix(semantic_dag): handle missing summary case in incremental update by @myysy in https://github.com/volcengine/OpenViking/pull/1177
* fix(embedder): pass dimensions in OpenAIDenseEmbedder.embed() by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1183
* build(deps): update litellm requirement from <1.82.6,>=1.0.0 to >=1.0.0,<1.83.1 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1179
* build(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1178
* feat(cli): ov cli grep with --exclude-uri/ -x option by @heaoxiang-ai in https://github.com/volcengine/OpenViking/pull/1174
* fix(session): align async commit API docs and examples by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1188
* fix: ov status shows embedding and rerank models usage by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1191
* feat(openclaw-plugin): add bypass session patterns by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1194
* fix: [Security] fix task API ownership leakage by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1182
* feat(bot):Single Channel (BotChannel) Integration, Werewolf demo by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1196
* fix(add-resource): improve directory import UX and correctness by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1197
* docs(config): update rerank configuration by @ousugo in https://github.com/volcengine/OpenViking/pull/1138
* feat(retrieve): use tags metadata for cross-subtree retrieval by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1162
* feat(sessions): support specifying session_id when creating session by @likzn in https://github.com/volcengine/OpenViking/pull/1074
* fix(opencode-plugin): recover stale commit state on current main by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1187
* fix(add-resource): fix scan warnings contract and Python SDK defaults by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1199
* feat(console): support account user agentid by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1198
* Revert "feat(retrieve): use tags metadata for cross-subtree retrieval" by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1200

## New Contributors
* @ruansheng8 made their first contribution in https://github.com/volcengine/OpenViking/pull/1143
* @jellespijker made their first contribution in https://github.com/volcengine/OpenViking/pull/1146
* @heaoxiang-ai made their first contribution in https://github.com/volcengine/OpenViking/pull/1174
* @ousugo made their first contribution in https://github.com/volcengine/OpenViking/pull/1138

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.2...v0.3.3

## What's Changed
* Feat(benchmark): Add benchmark/RAG : RAG system evaluation framework by @sponge225 in https://github.com/volcengine/OpenViking/pull/825
* docs(openclaw-plugin): refresh architecture guides with diagrams by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1145
* fix(openclaw-plugin): stop writing gateway.mode from installers by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1149
* test(openclaw-plugin): add comprehensive UT suite under tests/ut/ (27… by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1144
* feat(cli): improve chat endpoint configuration priority by @ruansheng8 in https://github.com/volcengine/OpenViking/pull/1143
* feat(eval): add locomo eval scripts for openclaw and readme by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1152
* add e2e test under tests/ut/e2e by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1154
* Handle OpenViking outages without blocking OpenClaw by @wlff123 in https://github.com/volcengine/OpenViking/pull/1158
* fix(ci): skip filesystem tests on Windows due to FUSE compatibility  by @kaisongli in https://github.com/volcengine/OpenViking/pull/1156
* feat(write): add content write interface by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1151
* fix(summarizer): use correct URI matching for memory context_type during reindex by @deepakdevp in https://github.com/volcengine/OpenViking/pull/1155
* fix(parser): use GITHUB_TOKEN for ZIP downloads if available by @jellespijker in https://github.com/volcengine/OpenViking/pull/1146
* feat: Add OpenClaw2OpenViking integration tests with CI pipeline by @kaisongli in https://github.com/volcengine/OpenViking/pull/1168
* fix(transaction): unify stale lock handling with ownership checks by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1171
* fix(zip): Fix garbled characters by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1173
* fix: exit with error if --with-bot fail by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1175
* feat(openclaw-plugin): add end-to-end healthcheck tool for OpenViking… by @wlff123 in https://github.com/volcengine/OpenViking/pull/1180
* feat(parse): add support for .inl file extension by @myysy in https://github.com/volcengine/OpenViking/pull/1176
* fix(semantic_dag): handle missing summary case in incremental update by @myysy in https://github.com/volcengine/OpenViking/pull/1177
* fix(embedder): pass dimensions in OpenAIDenseEmbedder.embed() by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1183
* build(deps): update litellm requirement from <1.82.6,>=1.0.0 to >=1.0.0,<1.83.1 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1179
* build(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1178
* feat(cli): ov cli grep with --exclude-uri/ -x option by @heaoxiang-ai in https://github.com/volcengine/OpenViking/pull/1174
* fix(session): align async commit API docs and examples by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1188
* fix: ov status shows embedding and rerank models usage by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/1191
* feat(openclaw-plugin): add bypass session patterns by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1194
* fix: [Security] fix task API ownership leakage by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1182
* feat(bot):Single Channel (BotChannel) Integration, Werewolf demo by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1196
* fix(add-resource): improve directory import UX and correctness by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1197
* docs(config): update rerank configuration by @ousugo in https://github.com/volcengine/OpenViking/pull/1138
* feat(retrieve): use tags metadata for cross-subtree retrieval by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1162
* feat(sessions): support specifying session_id when creating session by @likzn in https://github.com/volcengine/OpenViking/pull/1074
* fix(opencode-plugin): recover stale commit state on current main by @13ernkastel in https://github.com/volcengine/OpenViking/pull/1187
* fix(add-resource): fix scan warnings contract and Python SDK defaults by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1199
* feat(console): support account user agentid by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1198
* Revert "feat(retrieve): use tags metadata for cross-subtree retrieval" by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1200
* fix(storage): recover stale startup locks on Windows by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1201

## New Contributors
* @ruansheng8 made their first contribution in https://github.com/volcengine/OpenViking/pull/1143
* @jellespijker made their first contribution in https://github.com/volcengine/OpenViking/pull/1146
* @heaoxiang-ai made their first contribution in https://github.com/volcengine/OpenViking/pull/1174
* @ousugo made their first contribution in https://github.com/volcengine/OpenViking/pull/1138

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.2...v0.3.3


## v0.3.2 (2026-04-01)

## What's Changed
* Chore/pr agent ark token billing by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1117
* Fix HTTPX recognition issue with SOCKS5 proxy causing OpenViking crash by @wlff123 in https://github.com/volcengine/OpenViking/pull/1118
* refactor(model): unify config-driven retry across VLM and embedding by @qin-ctx in https://github.com/volcengine/OpenViking/pull/926
* fix(bot): import eval session time by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1121
* docs(examples): retire legacy integration examples by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1124
* fix(ci): skip filesystem tests on Windows due to FUSE compatibility by @kaisongli in https://github.com/volcengine/OpenViking/pull/1111
* docs(docker): use latest image tag in examples by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1125
* upload the new wechat group qrcode by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/1127
* Unify test directory in openclaw-plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/1128
* docs(guides):Add concise OVPack guide in Chinese and English by @sponge225 in https://github.com/volcengine/OpenViking/pull/1126
* docs(guides): reorganize observability documentation by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1130
* fix(installer): fall back to official PyPI when mirror lags by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1131
* feat(docker) add vikingbot and console by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1134
* fix(vlm): rollback ResponseAPI to Chat Completions, keep tool calls by @chenjw in https://github.com/volcengine/OpenViking/pull/1137
* feat(openclaw-plugin): add session-pattern guard for ingest reply assist by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/1136


**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.3.1...v0.3.2


## v0.3.1 (2026-03-31)

## What's Changed
* feat(ast): add PHP tree-sitter support by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/1087
* feat(ci): add multi-platform API test support for 5 platforms by @kaisongli in https://github.com/volcengine/OpenViking/pull/1093
* fix(ci): refresh uv.lock for docker release build by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1094
* fix(openclaw-plugin): simplify install flow and harden helpers by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1095
* fix(openclaw-plugin): preserve existing ov.conf on auto install by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1098
* ci: build docker images natively per arch by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1100
* Feature/memory opt by @chenjw in https://github.com/volcengine/OpenViking/pull/1099
* feat(storage): add auto language detection for semantic summary generation by @likzn in https://github.com/volcengine/OpenViking/pull/1076
* feat(prompt) support configurable prompt template directories by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1096
* feat: unify config-driven retry across VLM and embedding by @snemesh in https://github.com/volcengine/OpenViking/pull/1049
* feat(bot): import ov eval script by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1108
* Revert "feat: unify config-driven retry across VLM and embedding" by @qin-ptr in https://github.com/volcengine/OpenViking/pull/1113
* fix(storage) Fix parent_uri compatibility with legacy records by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1107
* fix(session): unify archive context abstracts by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1104

## New Contributors
* @likzn made their first contribution in https://github.com/volcengine/OpenViking/pull/1076

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.15...v0.3.1


## v0.2.14 (2026-03-30)

# OpenViking v0.2.14

本次 `v0.2.14` 版本主要围绕多租户能力、解析导入链路、OpenClaw 插件体验、Bot/Feishu 集成，以及服务端稳定性与安全性做了集中增强。

## Highlights

- 多租户与身份管理进一步完善。CLI 已支持租户身份默认值与覆盖，文档新增多租户使用指南，memory 也支持仅按 agent 维度隔离的 `agent-only` scope。
- 解析与导入链路更完整。图片解析新增 OCR 文本提取，目录导入识别 `.cc` 文件，重复标题导致的文件名冲突得到修复，HTTP 上传链路改为更稳妥的 upload id 流程。
- OpenClaw 插件显著增强。安装器与升级流程统一，默认按最新 Git tag 安装，session API 与 context pipeline 做了统一重构，并补齐了 Windows、compaction、compact result mapping、子进程重拉起等多处兼容性与稳定性问题。
- Bot 与 Feishu 集成可用性继续提升。修复了 bot proxy 未鉴权问题，改进了 Moonshot 请求兼容性，并升级了 Feishu interactive card 的 markdown 展示体验。
- 存储与运行时稳定性持续提升。包括 queuefs embedding tracker 加固、vector store 移除 `parent_uri`、Docker doctor 检查对齐，以及更细粒度的 eval token 指标。

## Upgrade Notes

- Bot proxy 接口 `/bot/v1/chat` 与 `/bot/v1/chat/stream` 已补齐鉴权，依赖未鉴权访问的调用方需要同步调整，见 [#996](https://github.com/volcengine/OpenViking/pull/996)。
- 裸 HTTP 导入本地文件/目录时，推荐按 `temp_upload -> temp_file_id` 的方式接入上传链路，见 [#1012](https://github.com/volcengine/OpenViking/pull/1012)。
- OpenClaw 插件的 compaction delegation 修复要求 `openclaw >= v2026.3.22`，见 [#1000](https://github.com/volcengine/OpenViking/pull/1000)。
- OpenClaw 插件安装器现在默认跟随仓库最新 Git tag 安装，如需固定版本可显式指定，见 [#1050](https://github.com/volcengine/OpenViking/pull/1050)。

## What's Changed

### Multi-tenant, Memory, and Identity

- 支持 CLI 租户身份默认值与覆盖，[#1019](https://github.com/volcengine/OpenViking/pull/1019) by @zhoujh01
- 支持仅按 agent 隔离的 agent memory scope，[#954](https://github.com/volcengine/OpenViking/pull/954) by @liberion1994
- 新增多租户使用指南文档，[#1029](https://github.com/volcengine/OpenViking/pull/1029) by @qin-ctx
- 在 quickstart 和 basic usage 中补充 `user_key` 与 `root_key` 的区别说明，[#1077](https://github.com/volcengine/OpenViking/pull/1077) by @r266-tech
- 示例中支持将 recalled memories 标记为 used，[#1079](https://github.com/volcengine/OpenViking/pull/1079) by @0xble

### Parsing and Resource Import

- 新增图片解析 OCR 文本提取能力，[#942](https://github.com/volcengine/OpenViking/pull/942) by @mvanhorn
- 修复重复标题导致的合并文件名冲突，[#1005](https://github.com/volcengine/OpenViking/pull/1005) by @deepakdevp
- 目录导入时识别 `.cc` 文件，[#1008](https://github.com/volcengine/OpenViking/pull/1008) by @qin-ctx
- 增加对非空 S3 目录 marker 的兼容性，[#997](https://github.com/volcengine/OpenViking/pull/997) by @zhoujh01
- HTTP 上传链路从临时路径切换为 upload id，[#1012](https://github.com/volcengine/OpenViking/pull/1012) by @qin-ctx
- 修复从目录上传时临时归档文件丢失 `.zip` 后缀的问题，[#1021](https://github.com/volcengine/OpenViking/pull/1021) by @Shawn-cf-o

### OpenClaw Plugin and Installer

- 回滚 duplicate registration guard 的加固改动，[#995](https://github.com/volcengine/OpenViking/pull/995) by @qin-ptr
- Windows 下 mapped session ID 做安全清洗，[#998](https://github.com/volcengine/OpenViking/pull/998) by @qin-ctx
- 使用 plugin-sdk exports 实现 compaction delegation，修复 `#833`，要求 `openclaw >= v2026.3.22`，[#1000](https://github.com/volcengine/OpenViking/pull/1000) by @jcp0578
- 统一 installer 升级流程，[#1020](https://github.com/volcengine/OpenViking/pull/1020) by @LinQiang391
- 默认按最新 Git tag 安装 OpenClaw 插件，[#1050](https://github.com/volcengine/OpenViking/pull/1050) by @LinQiang391
- 统一 session APIs，并重构 OpenClaw context pipeline 以提升一致性、可维护性和测试覆盖，[#1040](https://github.com/volcengine/OpenViking/pull/1040) by @wlff123
- 将 openclaw-plugin 的 `DEFAULT_COMMIT_TOKEN_THRESHOLD` 调整为 `20000`，[#1052](https://github.com/volcengine/OpenViking/pull/1052) by @wlff123
- 为 OpenViking 子进程增加防御式重拉起机制，[#1053](https://github.com/volcengine/OpenViking/pull/1053) by @huangxun375-stack
- 对齐 agent routing 并降低默认日志噪音，[#1054](https://github.com/volcengine/OpenViking/pull/1054) by @LinQiang391
- 修复 compact result mapping，[#1058](https://github.com/volcengine/OpenViking/pull/1058) by @jcp0578
- 移除 `ov_archive_expand` 中重复声明的 `const sessionId`，[#1059](https://github.com/volcengine/OpenViking/pull/1059) by @evaldass
- 新增 Claude Code memory plugin 示例，[#903](https://github.com/volcengine/OpenViking/pull/903) by @Castor6

### Bot, Feishu, and Security

- 修复 Bot Proxy 接口 `/bot/v1/chat`、`/bot/v1/chat/stream` 的未鉴权访问问题，[#996](https://github.com/volcengine/OpenViking/pull/996) by @13ernkastel
- 调整 tool content role 为 `user`，并优化 Feishu `on_message`，[#1023](https://github.com/volcengine/OpenViking/pull/1023) by @yeshion23333
- 修复/增强 Moonshot 请求错误处理，[#1026](https://github.com/volcengine/OpenViking/pull/1026) by @Linsiyuan9
- 进一步修复 Moonshot invalid request 问题，[#1028](https://github.com/volcengine/OpenViking/pull/1028) by @Linsiyuan9
- Feishu 消息升级为支持 markdown 的 interactive card v2，[#1015](https://github.com/volcengine/OpenViking/pull/1015) by @r266-tech
- 根据 review feedback 打磨 interactive card 细节，[#1046](https://github.com/volcengine/OpenViking/pull/1046) by @r266-tech

### Storage, Runtime, Examples, and Tooling

- 加固 queuefs embedding tracker 在多 worker loop 下的行为，[#1024](https://github.com/volcengine/OpenViking/pull/1024) by @qin-ctx
- vector store 移除 `parent_uri`，[#1042](https://github.com/volcengine/OpenViking/pull/1042) by @zhoujh01
- 为 eval 增加 embedding token 统计，并让任务结果返回 token 信息，[#1038](https://github.com/volcengine/OpenViking/pull/1038) by @yeshion23333
- 对齐 Docker doctor 中的 AGFS 检查与内置 pyagfs 版本，[#1044](https://github.com/volcengine/OpenViking/pull/1044) by @zhoujh01
- 新增 werewolf game demo，[#1025](https://github.com/volcengine/OpenViking/pull/1025) by @yuyaoyoyo-svg

### Build and Dependencies

- 升级 `actions/checkout` 从 4 到 6，[#1002](https://github.com/volcengine/OpenViking/pull/1002) by @dependabot[bot]
- 升级 `actions/cache` 从 4 到 5，[#1001](https://github.com/volcengine/OpenViking/pull/1001) by @dependabot[bot]

## New Contributors

- @Linsiyuan9 在 [#1026](https://github.com/volcengine/OpenViking/pull/1026) 完成了首次贡献
- @Shawn-cf-o 在 [#1021](https://github.com/volcengine/OpenViking/pull/1021) 完成了首次贡献
- @liberion1994 在 [#954](https://github.com/volcengine/OpenViking/pull/954) 完成了首次贡献
- @Castor6 在 [#903](https://github.com/volcengine/OpenViking/pull/903) 完成了首次贡献
- @huangxun375-stack 在 [#1053](https://github.com/volcengine/OpenViking/pull/1053) 完成了首次贡献
- @evaldass 在 [#1059](https://github.com/volcengine/OpenViking/pull/1059) 完成了首次贡献
- @0xble 在 [#1079](https://github.com/volcengine/OpenViking/pull/1079) 完成了首次贡献

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.13...v0.2.14


## What's Changed
* Revert "fix(openclaw-plugin): harden duplicate registration guard" by @qin-ptr in https://github.com/volcengine/OpenViking/pull/995
* Add non-empty S3 directory marker compatibility by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/997
* fix(openclaw-plugin): sanitize mapped session IDs for Windows by @qin-ctx in https://github.com/volcengine/OpenViking/pull/998
* fix(parse): prevent merged filename collision on duplicate headings by @deepakdevp in https://github.com/volcengine/OpenViking/pull/1005
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1002
* build(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1001
* fix(parse): recognize .cc files during directory import by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1008
* fix(openclaw-plugin): use plugin-sdk exports for compaction delegation (fixes #833) openclaw ≥ v2026.3.22 by @jcp0578 in https://github.com/volcengine/OpenViking/pull/1000
* feat(cli): support tenant identity defaults and overrides by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1019
* fix(bot): Change tool content role to user, opt feishu on_message by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1023
* feat(bot): Error calling LLM: litellm.BadRequestError: MoonshotExcept… by @Linsiyuan9 in https://github.com/volcengine/OpenViking/pull/1026
* feat(bot): use interactive card with markdown for Feishu messages (v2) by @r266-tech in https://github.com/volcengine/OpenViking/pull/1015
* werewolf game demo by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/1025
* fix(queuefs): harden embedding tracker across worker loops by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1024
* feat(installer, openclaw-plugin): unified installer upgrade by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1020
* fix(http): replace temp paths with upload ids by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1012
* Fix Unauthenticated Access to Bot Proxy Endpoints (/bot/v1/chat, /bot/v1/chat/stream) by @13ernkastel in https://github.com/volcengine/OpenViking/pull/996
* Fix/moonshot invalid request by @Linsiyuan9 in https://github.com/volcengine/OpenViking/pull/1028
* feat(parse): implement OCR text extraction for image parser by @mvanhorn in https://github.com/volcengine/OpenViking/pull/942
* fix(ov-cli): preserve .zip suffix for temp archives uploaded from directories by @Shawn-cf-o in https://github.com/volcengine/OpenViking/pull/1021
* feat(memory): support agent-only agent memory scope by @liberion1994 in https://github.com/volcengine/OpenViking/pull/954
* docs(concepts): add multi-tenant usage guide by @qin-ctx in https://github.com/volcengine/OpenViking/pull/1029
* feat(claude-code-plugin): add Claude Code memory plugin example by @Castor6 in https://github.com/volcengine/OpenViking/pull/903
* feat(eval): add emb token, get task add token result by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/1038
* fix(docker): align doctor AGFS check with bundled pyagfs by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1044
* feat(openclaw-plugin): default plugin install to latest Git tag by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1050
* refactor(openclaw-plugin): Unified session APIs and refactored the OpenClaw context pipeline for more consistent behavior, better maintainability, and stronger test coverage. by @wlff123 in https://github.com/volcengine/OpenViking/pull/1040
* set DEFAULT_COMMIT_TOKEN_THRESHOLD to 20000 for openclaw-plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/1052
* fix(openclaw-plugin): add defensive re-spawn for OpenViking subproces… by @huangxun375-stack in https://github.com/volcengine/OpenViking/pull/1053
* feat(openclaw-plugin): align agent routing and reduce default log noi… by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/1054
* fix(openclaw-plugin):  fix compact result mapping by @jcp0578 in https://github.com/volcengine/OpenViking/pull/1058
* Make parent_uri backend-aware in vector store by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/1042
* fix(openclaw-plugin): remove duplicate `const sessionId` declaration in ov_archive_expand by @evaldass in https://github.com/volcengine/OpenViking/pull/1059
* fix(bot): polish interactive card per review feedback by @r266-tech in https://github.com/volcengine/OpenViking/pull/1046
* feat(examples): mark recalled memories as used by @0xble in https://github.com/volcengine/OpenViking/pull/1079
* docs: clarify user_key vs root_key in quickstart and basic usage by @r266-tech in https://github.com/volcengine/OpenViking/pull/1077

## New Contributors
* @Linsiyuan9 made their first contribution in https://github.com/volcengine/OpenViking/pull/1026
* @Shawn-cf-o made their first contribution in https://github.com/volcengine/OpenViking/pull/1021
* @liberion1994 made their first contribution in https://github.com/volcengine/OpenViking/pull/954
* @Castor6 made their first contribution in https://github.com/volcengine/OpenViking/pull/903
* @huangxun375-stack made their first contribution in https://github.com/volcengine/OpenViking/pull/1053
* @evaldass made their first contribution in https://github.com/volcengine/OpenViking/pull/1059
* @0xble made their first contribution in https://github.com/volcengine/OpenViking/pull/1079

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.13...v0.2.14


## v0.2.13 (2026-03-26)

## What's Changed
* test: add comprehensive unit tests for core utilities by @xingzihai in https://github.com/volcengine/OpenViking/pull/990
* fix(vlm): scope LiteLLM thinking param to DashScope providers only by @deepakdevp in https://github.com/volcengine/OpenViking/pull/958
* Api test：improve API test infrastructure with dual-mode CI by @kaisongli in https://github.com/volcengine/OpenViking/pull/950
* docs: Add basic usage example and Chinese documentation for examples by @xingzihai in https://github.com/volcengine/OpenViking/pull/979
* Fix Windows engine wheel runtime packaging by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/993
* fix(openclaw-plugin): harden duplicate registration guard by @qin-ctx in https://github.com/volcengine/OpenViking/pull/974

## New Contributors
* @xingzihai made their first contribution in https://github.com/volcengine/OpenViking/pull/990
* @kaisongli made their first contribution in https://github.com/volcengine/OpenViking/pull/950

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.12...v0.2.13


## v0.2.12 (2026-03-25)

## What's Changed
* Use uv sync --locked in Dockerfile by @mtthidoteu in https://github.com/volcengine/OpenViking/pull/963
* fix(server): handle CancelledError during shutdown paths by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/848
* fix(bot):rollback config by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/973

## New Contributors
* @mtthidoteu made their first contribution in https://github.com/volcengine/OpenViking/pull/963

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.11...v0.2.12


## v0.2.11 (2026-03-25)

# OpenViking v0.2.11

OpenViking v0.2.11 聚焦在四个方向：模型与检索生态扩展、解析与导入能力增强、服务端可观测性与运维能力补齐，以及多租户安全性和稳定性加固。相较 `v0.2.9`，这一版本不仅补上了 Helm 部署、Prometheus 指标、健康统计 API、`ov doctor` 与 `reindex` 等工程能力，也持续扩展了 embedding、rerank、VLM 与 bot 侧的模型接入面。

这次更新的代表性改动包括：新增 MiniMax embedding、Azure OpenAI、GeminiDenseEmbedder、LiteLLM embedding/rerank、OpenAI-compatible rerank 与 Tavily 搜索后端；新增 Whisper 音频转写与飞书/Lark 云文档解析；新增多租户文件加密与文档加密；增加 Prometheus 指标导出、内存健康统计接口、可信租户头鉴权模式，以及面向 Kubernetes 的 Helm Chart。与此同时，版本还集中修复了 Windows 锁文件、会话异步提交、向量检索 NaN/Inf 分数、ZIP 路径穿越、SOCKS5 代理兼容等一批实际使用中的问题。

## 版本亮点

- **模型与检索生态继续扩展**：新增 MiniMax embedding [#624](https://github.com/volcengine/OpenViking/pull/624)、Azure OpenAI embedding/VLM [#808](https://github.com/volcengine/OpenViking/pull/808)、GeminiDenseEmbedder [#751](https://github.com/volcengine/OpenViking/pull/751)、LiteLLM embedding [#853](https://github.com/volcengine/OpenViking/pull/853) 与 rerank [#888](https://github.com/volcengine/OpenViking/pull/888)，并补充 OpenAI-compatible rerank [#785](https://github.com/volcengine/OpenViking/pull/785) 与 Tavily 搜索后端 [#788](https://github.com/volcengine/OpenViking/pull/788)。
- **内容接入链路更完整**：音频资源现在支持 Whisper ASR 解析 [#805](https://github.com/volcengine/OpenViking/pull/805)，云文档场景新增飞书/Lark 解析器 [#831](https://github.com/volcengine/OpenViking/pull/831)，文件向量化策略变为可配置 [#858](https://github.com/volcengine/OpenViking/pull/858)，搜索结果还新增了 provenance 元数据 [#852](https://github.com/volcengine/OpenViking/pull/852)。
- **服务端运维能力明显补齐**：新增 `ov reindex` [#795](https://github.com/volcengine/OpenViking/pull/795)、`ov doctor` [#851](https://github.com/volcengine/OpenViking/pull/851)、Prometheus exporter [#806](https://github.com/volcengine/OpenViking/pull/806)、内存健康统计 API [#706](https://github.com/volcengine/OpenViking/pull/706)、可信租户头模式 [#868](https://github.com/volcengine/OpenViking/pull/868) 与 Helm Chart [#800](https://github.com/volcengine/OpenViking/pull/800)。
- **多租户与安全能力增强**：新增多租户文件加密 [#828](https://github.com/volcengine/OpenViking/pull/828) 和文档加密能力 [#893](https://github.com/volcengine/OpenViking/pull/893)，修复租户上下文在 observer 与 reindex 流程中的透传问题 [#807](https://github.com/volcengine/OpenViking/pull/807) [#820](https://github.com/volcengine/OpenViking/pull/820)，并修复 ZIP Slip 风险 [#879](https://github.com/volcengine/OpenViking/pull/879) 与 trusted auth 模式下 API key 校验缺失 [#924](https://github.com/volcengine/OpenViking/pull/924)。
- **稳定性与工程体验持续加固**：向量检索 NaN/Inf 分数处理在结果端 [#824](https://github.com/volcengine/OpenViking/pull/824) 和源头 [#882](https://github.com/volcengine/OpenViking/pull/882) 双重兜底，会话异步提交与并发提交问题被修复 [#819](https://github.com/volcengine/OpenViking/pull/819) [#783](https://github.com/volcengine/OpenViking/pull/783) [#900](https://github.com/volcengine/OpenViking/pull/900)，Windows stale lock 与 TUI 输入问题持续修复 [#790](https://github.com/volcengine/OpenViking/pull/790) [#798](https://github.com/volcengine/OpenViking/pull/798) [#854](https://github.com/volcengine/OpenViking/pull/854)，同时补上了代理兼容 [#957](https://github.com/volcengine/OpenViking/pull/957) 与 API 重试风暴保护 [#772](https://github.com/volcengine/OpenViking/pull/772)。

## 升级提示

- 如果你使用 `litellm` 集成，请关注这一版本中的安全策略调整：先临时硬禁用 [#937](https://github.com/volcengine/OpenViking/pull/937)，后恢复为仅允许 `<1.82.6` 的版本范围 [#966](https://github.com/volcengine/OpenViking/pull/966)。建议显式锁定依赖版本。
- 如果你启用 trusted auth 模式，需要同时配置服务端 API key，相关校验已在 [#924](https://github.com/volcengine/OpenViking/pull/924) 中强制执行。
- Helm 默认配置已切换为更适合 Volcengine 场景的默认值，并补齐缺失字段 [#822](https://github.com/volcengine/OpenViking/pull/822)。升级 chart 时建议重新审阅 values 配置。
- Windows 用户建议关注 stale PID / RocksDB LOCK 文件处理增强 [#790](https://github.com/volcengine/OpenViking/pull/790) [#798](https://github.com/volcengine/OpenViking/pull/798) [#854](https://github.com/volcengine/OpenViking/pull/854)。

## 详细变更

以下列表已按主题去重整理，保留每一项唯一 PR，并附上对应链接，便于直接用于 GitHub Release。

### 模型、Embedding、Rerank 与检索生态

- 新增 MiniMax embedding provider，支持通过官方 HTTP API 接入 MiniMax 向量模型。[PR #624](https://github.com/volcengine/OpenViking/pull/624)
- 新增 OpenAI-compatible rerank provider。[PR #785](https://github.com/volcengine/OpenViking/pull/785)
- 新增 Azure OpenAI 对 embedding 与 VLM 的支持。[PR #808](https://github.com/volcengine/OpenViking/pull/808)
- 新增 GeminiDenseEmbedder 文本向量模型提供方。[PR #751](https://github.com/volcengine/OpenViking/pull/751)
- 新增 Tavily 作为可配置的 web search backend。[PR #788](https://github.com/volcengine/OpenViking/pull/788)
- 修复 LiteLLM 下 `zai/` 模型前缀处理，避免重复拼接 `zhipu` 前缀。[PR #789](https://github.com/volcengine/OpenViking/pull/789)
- 修复 Gemini embedder 相关问题。[PR #841](https://github.com/volcengine/OpenViking/pull/841)
- 新增 LiteLLM embedding provider。[PR #853](https://github.com/volcengine/OpenViking/pull/853)
- 新增默认向量索引名可配置能力。[PR #861](https://github.com/volcengine/OpenViking/pull/861)
- 新增 LiteLLM rerank provider。[PR #888](https://github.com/volcengine/OpenViking/pull/888)
- 修复 Ollama embedding provider 的维度解析问题。[PR #915](https://github.com/volcengine/OpenViking/pull/915)
- 为 Jina 代码模型补充 code-specific task 默认值。[PR #914](https://github.com/volcengine/OpenViking/pull/914)
- 在 embedder 与 vectordb 维度不匹配时给出警告提示。[PR #930](https://github.com/volcengine/OpenViking/pull/930)
- 为 Jina embedding provider 增加代码模型维度与更可操作的 422 错误提示。[PR #928](https://github.com/volcengine/OpenViking/pull/928)
- 搜索结果新增 provenance 元数据，方便追踪召回来源。[PR #852](https://github.com/volcengine/OpenViking/pull/852)
- 修复 recall limit 逻辑。[PR #821](https://github.com/volcengine/OpenViking/pull/821)
- 在结果序列化前钳制向量检索中的 `inf`/`nan` 分数，避免 JSON 失败。[PR #824](https://github.com/volcengine/OpenViking/pull/824)
- 在相似度分数源头钳制 `inf`/`nan`，进一步避免 JSON crash。[PR #882](https://github.com/volcengine/OpenViking/pull/882)

### 解析、导入与内容处理

- 新增基于 Whisper 的音频解析与 ASR 集成。[PR #805](https://github.com/volcengine/OpenViking/pull/805)
- 修复 PDF 书签目标页为整数索引时的解析问题。[PR #794](https://github.com/volcengine/OpenViking/pull/794)
- 新增飞书/Lark 云文档解析器。[PR #831](https://github.com/volcengine/OpenViking/pull/831)
- 修复 MarkdownParser 的字符数限制处理。[PR #826](https://github.com/volcengine/OpenViking/pull/826)
- 支持可配置的文件向量化策略。[PR #858](https://github.com/volcengine/OpenViking/pull/858)
- 优化语义处理性能，并发执行 batch overview 与 file summary 生成。[PR #840](https://github.com/volcengine/OpenViking/pull/840)
- 修复 `resource add` 时可能出现的 `file exists` 错误。[PR #845](https://github.com/volcengine/OpenViking/pull/845)
- 修复 ZIP 解压中的 Zip Slip 路径穿越风险。[PR #879](https://github.com/volcengine/OpenViking/pull/879)

### 服务端、观测、部署与运维

- 新增 CLI `reindex` 命令，用于主动触发内容重建索引。[PR #795](https://github.com/volcengine/OpenViking/pull/795)
- 修复 observer 中 `RequestContext` 透传，支持 tenant-scoped vector count。[PR #807](https://github.com/volcengine/OpenViking/pull/807)
- 新增基于 observer pattern 的 Prometheus metrics exporter。[PR #806](https://github.com/volcengine/OpenViking/pull/806)
- 新增内存健康统计 API endpoints。[PR #706](https://github.com/volcengine/OpenViking/pull/706)
- 新增 Helm Chart，支持 Kubernetes 部署。[PR #800](https://github.com/volcengine/OpenViking/pull/800)
- 修复 Helm 默认值并补齐缺失配置字段，默认切换为 Volcengine 场景。[PR #822](https://github.com/volcengine/OpenViking/pull/822)
- 新增 `ov doctor` 诊断命令。[PR #851](https://github.com/volcengine/OpenViking/pull/851)
- 将 `ov doctor` 中的 `ov.conf` JSON 加载逻辑集中管理。[PR #913](https://github.com/volcengine/OpenViking/pull/913)
- 新增 trusted auth mode，支持基于 tenant headers 的可信鉴权模式。[PR #868](https://github.com/volcengine/OpenViking/pull/868)
- 修复 trusted auth mode 下必须提供 server API key 的校验。[PR #924](https://github.com/volcengine/OpenViking/pull/924)
- 修复 dockerized localhost server 场景下 CLI 上传本地文件的问题。[PR #961](https://github.com/volcengine/OpenViking/pull/961)
- 将 vectordb engine 迁移到 abi3 packaging，改善构建与分发兼容性。[PR #897](https://github.com/volcengine/OpenViking/pull/897)
- 重构集成测试体系。[PR #910](https://github.com/volcengine/OpenViking/pull/910)

### 多租户、安全、会话与稳定性

- 新增多租户文件加密能力。[PR #828](https://github.com/volcengine/OpenViking/pull/828)
- 新增文档加密能力，并重构加密相关代码。[PR #893](https://github.com/volcengine/OpenViking/pull/893)
- 修复 reindex existence check 未正确传递 tenant context 的问题。[PR #820](https://github.com/volcengine/OpenViking/pull/820)
- 为 `client.Session` 新增 `commit_async`。[PR #819](https://github.com/volcengine/OpenViking/pull/819)
- 修复 session archive 相关问题。[PR #883](https://github.com/volcengine/OpenViking/pull/883)
- 修复 session 并发提交时旧消息可能被重复提交的问题。[PR #783](https://github.com/volcengine/OpenViking/pull/783)
- 新增异步 session commit、session metadata 与 archive continuity threading。[PR #900](https://github.com/volcengine/OpenViking/pull/900)
- 为队列增加 circuit breaker，避免 API retry storm。[PR #772](https://github.com/volcengine/OpenViking/pull/772)
- 修复 Semantic queue worker 使用错误并发限制的问题，改为 `_max_concurrent_semantic`。[PR #905](https://github.com/volcengine/OpenViking/pull/905)
- 修复 HTTPX 在 SOCKS5 代理场景下的识别问题，避免 OpenViking crash。[PR #957](https://github.com/volcengine/OpenViking/pull/957)
- 严格校验 `ov.conf` 与 `ovcli.conf`。[PR #904](https://github.com/volcengine/OpenViking/pull/904)
- 加强 `LogConfig` 未知字段校验，并在 `ParserConfig` 中给出告警。[PR #856](https://github.com/volcengine/OpenViking/pull/856)
- 读取 `ov.conf` JSON 时支持展开环境变量。[PR #908](https://github.com/volcengine/OpenViking/pull/908)
- 临时硬禁用 LiteLLM 集成以规避安全风险。[PR #937](https://github.com/volcengine/OpenViking/pull/937)
- 在安全范围内恢复 LiteLLM 集成，仅允许 `<1.82.6` 版本。[PR #966](https://github.com/volcengine/OpenViking/pull/966)

### Windows 兼容性与底层资源处理

- 修复 Windows 下 stale PID lock 与 TUI console input 处理问题。[PR #790](https://github.com/volcengine/OpenViking/pull/790)
- 启动时清理 Windows 下残留的 RocksDB `LOCK` 文件。[PR #798](https://github.com/volcengine/OpenViking/pull/798)
- 说明文档中补充 `process_lock` 在 Windows 的错误处理说明。[PR #849](https://github.com/volcengine/OpenViking/pull/849)
- 修复 `process_lock._is_pid_alive` 对 `WinError 11` 的处理。[PR #854](https://github.com/volcengine/OpenViking/pull/854)

### Bot、Plugin 与 OpenClaw

- 为 memory-openviking plugin 增加清理脚本。[PR #832](https://github.com/volcengine/OpenViking/pull/832)
- 增加旧版本 plugin 清理说明。[PR #843](https://github.com/volcengine/OpenViking/pull/843)
- 为 openclaw-plugin 增加卸载脚本。[PR #933](https://github.com/volcengine/OpenViking/pull/933)
- openclaw-plugin 上下文引擎重构，增加 token budget 约束并减少上下文膨胀。[PR #891](https://github.com/volcengine/OpenViking/pull/891)
- 新增 archive-aware context assembly 与 async session commit 到 openclaw-plugin。[PR #938](https://github.com/volcengine/OpenViking/pull/938)
- 回滚 openclaw-plugin 的 archive-aware context assembly 与 async session commit 变更。[PR #953](https://github.com/volcengine/OpenViking/pull/953)
- bot 侧新增 multi read tool，优化 loop memory，修复 agent memory search，并更新 README。[PR #895](https://github.com/volcengine/OpenViking/pull/895)
- bot 侧删除无效工具，并更新 search/grep tool 描述。[PR #929](https://github.com/volcengine/OpenViking/pull/929)
- bot 默认 provider 切换为 OpenAI，并修复飞书 `chat_mode` 值。[PR #962](https://github.com/volcengine/OpenViking/pull/962)
- 在 `ov` 脚本中新增 `import locomo`。[PR #965](https://github.com/volcengine/OpenViking/pull/965)
- 重构 memory extract 逻辑。[PR #916](https://github.com/volcengine/OpenViking/pull/916)
- 再次重构 memory extract 逻辑。[PR #952](https://github.com/volcengine/OpenViking/pull/952)

### VLM 与多模态相关修复

- 修复当 API 返回字符串响应时 VLM 抛出 `AttributeError` 的问题。[PR #814](https://github.com/volcengine/OpenViking/pull/814)
- 将 `thinking` 标记透传给 dashscope OpenAI backend。[PR #939](https://github.com/volcengine/OpenViking/pull/939)

### 构建、CI、版本与仓库维护

- 使用 `pull_request_target` 让 Qodo review 能在 fork PR 上运行。[PR #816](https://github.com/volcengine/OpenViking/pull/816)
- 同步 `ov` CLI 版本与 `openviking` 包版本。[PR #869](https://github.com/volcengine/OpenViking/pull/869)
- 修复 CI 构建依赖中缺失 `setuptools-scm` 的问题。[PR #870](https://github.com/volcengine/OpenViking/pull/870)
- 更新 `.pr_agent.toml`。[PR #838](https://github.com/volcengine/OpenViking/pull/838)
- 新增 gitcgr code graph badge。[PR #872](https://github.com/volcengine/OpenViking/pull/872)
- 回滚 gitcgr code graph badge。[PR #884](https://github.com/volcengine/OpenViking/pull/884)

### 文档与社区更新

- README banner 替换为居中 logo。[PR #799](https://github.com/volcengine/OpenViking/pull/799)
- 更新 `INSTALL-ZH.md`。[PR #818](https://github.com/volcengine/OpenViking/pull/818)
- 更新 `INSTALL.md`。[PR #823](https://github.com/volcengine/OpenViking/pull/823)
- 增加 Gemini embedding provider 的使用与安装文档。[PR #830](https://github.com/volcengine/OpenViking/pull/830)
- 新增 OpenCode 与 OpenClaw plugin 的中文 README 翻译。[PR #850](https://github.com/volcengine/OpenViking/pull/850)
- 新增增量更新功能的 API 文档。[PR #886](https://github.com/volcengine/OpenViking/pull/886)
- 新增飞书/Lark 云文档解析文档。[PR #906](https://github.com/volcengine/OpenViking/pull/906)
- 更新 `INSTALL.md`。[PR #917](https://github.com/volcengine/OpenViking/pull/917)
- 更新 `INSTALL-ZH.md`。[PR #918](https://github.com/volcengine/OpenViking/pull/918)
- 更新最新微信交流群二维码。[PR #919](https://github.com/volcengine/OpenViking/pull/919)
- 更新 `README_CN.md`。[PR #920](https://github.com/volcengine/OpenViking/pull/920)
- 更新 `README.md`。[PR #921](https://github.com/volcengine/OpenViking/pull/921)

## New Contributors

- @jackjin1997 首次贡献：[PR #800](https://github.com/volcengine/OpenViking/pull/800)
- @zeng121 首次贡献：[PR #808](https://github.com/volcengine/OpenViking/pull/808)
- @Bortlesboat 首次贡献：[PR #790](https://github.com/volcengine/OpenViking/pull/790)
- @evanYDL 首次贡献：[PR #788](https://github.com/volcengine/OpenViking/pull/788)
- @RobertIndie 首次贡献：[PR #820](https://github.com/volcengine/OpenViking/pull/820)
- @REMvisual 首次贡献：[PR #798](https://github.com/volcengine/OpenViking/pull/798)
- @boyweb 首次贡献：[PR #819](https://github.com/volcengine/OpenViking/pull/819)
- @Protocol-zero-0 首次贡献：[PR #789](https://github.com/volcengine/OpenViking/pull/789)
- @a1461750564 首次贡献：[PR #824](https://github.com/volcengine/OpenViking/pull/824)
- @SCPZ24 首次贡献：[PR #850](https://github.com/volcengine/OpenViking/pull/850)
- @ryzn0518 首次贡献：[PR #831](https://github.com/volcengine/OpenViking/pull/831)
- @stubbi 首次贡献：[PR #853](https://github.com/volcengine/OpenViking/pull/853)
- @vitali87 首次贡献：[PR #872](https://github.com/volcengine/OpenViking/pull/872)
- @snemesh 首次贡献：[PR #882](https://github.com/volcengine/OpenViking/pull/882)
- @vincent067 首次贡献：[PR #905](https://github.com/volcengine/OpenViking/pull/905)
- @ningfeemic-dev 首次贡献：[PR #858](https://github.com/volcengine/OpenViking/pull/858)
- @everforge 首次贡献：[PR #913](https://github.com/volcengine/OpenViking/pull/913)
- @JasonOA888 首次贡献：[PR #915](https://github.com/volcengine/OpenViking/pull/915)
- @itlackey 首次贡献：[PR #908](https://github.com/volcengine/OpenViking/pull/908)
- @3kyou1 首次贡献：[PR #914](https://github.com/volcengine/OpenViking/pull/914)
- @sacloudy 首次贡献：[PR #939](https://github.com/volcengine/OpenViking/pull/939)
- @Ghostknight0 首次贡献：[PR #957](https://github.com/volcengine/OpenViking/pull/957)
## What's Changed
* feat(embedder): minimax embeding by @zhougit86 in https://github.com/volcengine/OpenViking/pull/624
* feat(cli): add reindex command to trigger content re-indexing by @mvanhorn in https://github.com/volcengine/OpenViking/pull/795
* feat: replace banner with centered logo in README files by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/799
* fix(observer): pass RequestContext through vikingdb observer for tenant-scoped vector count by @qin-ctx in https://github.com/volcengine/OpenViking/pull/807
* feat(parse): implement Whisper ASR integration for audio parser by @mvanhorn in https://github.com/volcengine/OpenViking/pull/805
* feat(rerank): add OpenAI-compatible rerank provider by @chenxiaofei-cxf in https://github.com/volcengine/OpenViking/pull/785
* fix(ci): use pull_request_target so Qodo review runs on fork PRs by @qin-ctx in https://github.com/volcengine/OpenViking/pull/816
* feat: add Helm chart for Kubernetes deployment by @jackjin1997 in https://github.com/volcengine/OpenViking/pull/800
* feat: add Azure OpenAI support for embedding and VLM by @zeng121 in https://github.com/volcengine/OpenViking/pull/808
* fix(windows): handle stale PID locks and TUI console input by @Bortlesboat in https://github.com/volcengine/OpenViking/pull/790
* fix(vlm): fix AttributeError when API returns string response (Issue #801) by @sponge225 in https://github.com/volcengine/OpenViking/pull/814
* fix(parser): resolve PDF bookmark destinations for integer page indices by @mvanhorn in https://github.com/volcengine/OpenViking/pull/794
* Update INSTALL-ZH.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/818
* feat: add Tavily as configurable web search backend by @evanYDL in https://github.com/volcengine/OpenViking/pull/788
* fix(content): pass tenant context to reindex existence check by @RobertIndie in https://github.com/volcengine/OpenViking/pull/820
* fix(recall): fix recall limit by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/821
* fix(helm): switch to volcengine defaults and add missing config fields by @qin-ctx in https://github.com/volcengine/OpenViking/pull/822
* fix(windows): clean stale RocksDB LOCK files on startup by @REMvisual in https://github.com/volcengine/OpenViking/pull/798
* Update INSTALL.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/823
* fix(session): add commit_async for client.Session by @boyweb in https://github.com/volcengine/OpenViking/pull/819
* feat(gemini): add GeminiDenseEmbedder text embedding provider by @chethanuk in https://github.com/volcengine/OpenViking/pull/751
* fix(vlm): skip zhipu prefix when model already uses zai/ (LiteLLM) by @Protocol-zero-0 in https://github.com/volcengine/OpenViking/pull/789
* add script to clean up memory-openviking plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/832
* feat: Add multi-tenant file encryption capability by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/828
* fix(parser): MarkdownParser char limit by @chenxiaofei-cxf in https://github.com/volcengine/OpenViking/pull/826
* feat(telemetry): add Prometheus metrics exporter via observer pattern by @mvanhorn in https://github.com/volcengine/OpenViking/pull/806
* fix(search): clamp inf/nan scores from vector search to prevent JSON serialization failure by @a1461750564 in https://github.com/volcengine/OpenViking/pull/824
* Update .pr_agent.toml by @qin-ptr in https://github.com/volcengine/OpenViking/pull/838
* feat(server): add memory health statistics API endpoints by @mvanhorn in https://github.com/volcengine/OpenViking/pull/706
* fix/gemini_embedder by @qin-ptr in https://github.com/volcengine/OpenViking/pull/841
* Add instructions for cleaning up old version plugins. by @wlff123 in https://github.com/volcengine/OpenViking/pull/843
* fix(resource): resolve 'file exists' errors on resource add by @qin-ptr in https://github.com/volcengine/OpenViking/pull/845
* docs: Add Chinese README translations for OpenCode and OpenClaw plugins by @SCPZ24 in https://github.com/volcengine/OpenViking/pull/850
* docs(process_lock): clarify Windows error handling for _is_pid_alive by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/849
* fix(config): validate unknown fields in LogConfig and warn in ParserConfig by @r266-tech in https://github.com/volcengine/OpenViking/pull/856
* docs: add Gemini embedding provider usage and installation guide by @chethanuk in https://github.com/volcengine/OpenViking/pull/830
* fix(utils): handle WinError 11 in process_lock._is_pid_alive by @Bortlesboat in https://github.com/volcengine/OpenViking/pull/854
* feat(parse): add Feishu/Lark cloud document parser by @ryzn0518 in https://github.com/volcengine/OpenViking/pull/831
* feat(vectordb): make default index name configurable by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/861
* feat(embedding): add litellm as embedding provider by @stubbi in https://github.com/volcengine/OpenViking/pull/853
* fix(build): sync ov cli version with openviking by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/869
* fix(scm) Fix missing setuptools-scm in CI build dependencies by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/870
* docs: add gitcgr code graph badge by @vitali87 in https://github.com/volcengine/OpenViking/pull/872
* feat(server): add trusted auth mode for tenant headers by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/868
* Revert "docs: add gitcgr code graph badge" by @qin-ptr in https://github.com/volcengine/OpenViking/pull/884
* fix: clamp inf/nan similarity scores at source to prevent JSON crash by @snemesh in https://github.com/volcengine/OpenViking/pull/882
* docs(api): add documentation for incremental update feature by @myysy in https://github.com/volcengine/OpenViking/pull/886
* Fix/session archive by @myysy in https://github.com/volcengine/OpenViking/pull/883
* feat(openclaw-plugin):context engine refactor design & enforce token budget and reduce context bloat by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/891
* fix(session): prevent concurrent commit re-committing old messages by @deepakdevp in https://github.com/volcengine/OpenViking/pull/783
* fix: prevent Zip Slip path traversal in ZIP extraction by @r266-tech in https://github.com/volcengine/OpenViking/pull/879
* feat(rerank): add litellm as rerank provider by @mvanhorn in https://github.com/volcengine/OpenViking/pull/888
* feat(vectordb)Migrate vectordb engine to abi3 packaging by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/897
* feat: add encrypt doc && refactoring encrypt code by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/893
* feat(bot):add multi read tool, opt loop memory, fix agent memory search, update README by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/895
* feat(cli): add ov doctor diagnostic command by @mvanhorn in https://github.com/volcengine/OpenViking/pull/851
* fix: use _max_concurrent_semantic in Semantic queue worker by @vincent067 in https://github.com/volcengine/OpenViking/pull/905
* docs: add Feishu/Lark cloud document parser documentation by @r266-tech in https://github.com/volcengine/OpenViking/pull/906
* feat: make file vectorization strategy configurable by @ningfeemic-dev in https://github.com/volcengine/OpenViking/pull/858
* feat(retrieve): add provenance metadata to search results by @mvanhorn in https://github.com/volcengine/OpenViking/pull/852
* perf(semantic): run batch overview generation and file summaries concurrently by @ahmedhesham6 in https://github.com/volcengine/OpenViking/pull/840
* fix(queue): add circuit breaker to prevent API retry storms by @deepakdevp in https://github.com/volcengine/OpenViking/pull/772
* feat: refactor integration test by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/910
* Refactor(CLI): Centralize ov.conf JSON loading in ov doctor by @everforge in https://github.com/volcengine/OpenViking/pull/913
* fix(config): validate ov.conf and ovcli.conf strictly by @qin-ctx in https://github.com/volcengine/OpenViking/pull/904
* fix(embedding): add dimension resolution for Ollama embedding provider by @JasonOA888 in https://github.com/volcengine/OpenViking/pull/915
* fix: expand env vars when loading ov.conf JSON config files by @itlackey in https://github.com/volcengine/OpenViking/pull/908
* Update INSTALL.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/917
* Update INSTALL-ZH.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/918
* add latest wechat-group-qrcode by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/919
* Use code-specific Jina task defaults for code embedding models by @3kyou1 in https://github.com/volcengine/OpenViking/pull/914
* Update README_CN.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/920
* Update README.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/921
* Refactor memory extract by @chenjw in https://github.com/volcengine/OpenViking/pull/916
* fix: require server api key in trusted auth mode by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/924
* add script to uninstall openclaw-plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/933
* feat(bot):delete invalid tools, update search\grep tool's description by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/929
* fix(embed): give warning when embeder and vectoerdb are not the same. by @zhougit86 in https://github.com/volcengine/OpenViking/pull/930
* fix(embedder): add code model dimensions and actionable 422 error for Jina by @deepakdevp in https://github.com/volcengine/OpenViking/pull/928
* feat(session): async commit, session metadata, and archive continuity threading by @qin-ctx in https://github.com/volcengine/OpenViking/pull/900
* fix(security): hard-disable litellm integrations by @qin-ctx in https://github.com/volcengine/OpenViking/pull/937
* feat(openclaw-plugin): add archive-aware context assembly and async session commit by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/938
* Revert "feat(openclaw-plugin): add archive-aware context assembly and async session commit" by @qin-ctx in https://github.com/volcengine/OpenViking/pull/953
* fix(vlm): pass thinking flag to dashscope openai backend by @sacloudy in https://github.com/volcengine/OpenViking/pull/939
* Refactor memory extract by @chenjw in https://github.com/volcengine/OpenViking/pull/952
* Fix HTTPX recognition issue with SOCKS5 proxy causing OpenViking crash by @Ghostknight0 in https://github.com/volcengine/OpenViking/pull/957
* fix(cli): upload local files for dockerized localhost servers by @qin-ctx in https://github.com/volcengine/OpenViking/pull/961
* fix(bot):change provider to OpenAI, fix feishu chat_mode value by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/962
* feat(bot): add import locomo to ov script by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/965
* fix(security): restore litellm integrations below 1.82.6 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/966
* Fix/watch manager file not found by @myysy in https://github.com/volcengine/OpenViking/pull/970
* fix release build workflow by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/971

## New Contributors
* @jackjin1997 made their first contribution in https://github.com/volcengine/OpenViking/pull/800
* @zeng121 made their first contribution in https://github.com/volcengine/OpenViking/pull/808
* @Bortlesboat made their first contribution in https://github.com/volcengine/OpenViking/pull/790
* @evanYDL made their first contribution in https://github.com/volcengine/OpenViking/pull/788
* @RobertIndie made their first contribution in https://github.com/volcengine/OpenViking/pull/820
* @REMvisual made their first contribution in https://github.com/volcengine/OpenViking/pull/798
* @boyweb made their first contribution in https://github.com/volcengine/OpenViking/pull/819
* @Protocol-zero-0 made their first contribution in https://github.com/volcengine/OpenViking/pull/789
* @a1461750564 made their first contribution in https://github.com/volcengine/OpenViking/pull/824
* @SCPZ24 made their first contribution in https://github.com/volcengine/OpenViking/pull/850
* @ryzn0518 made their first contribution in https://github.com/volcengine/OpenViking/pull/831
* @stubbi made their first contribution in https://github.com/volcengine/OpenViking/pull/853
* @vitali87 made their first contribution in https://github.com/volcengine/OpenViking/pull/872
* @snemesh made their first contribution in https://github.com/volcengine/OpenViking/pull/882
* @vincent067 made their first contribution in https://github.com/volcengine/OpenViking/pull/905
* @ningfeemic-dev made their first contribution in https://github.com/volcengine/OpenViking/pull/858
* @everforge made their first contribution in https://github.com/volcengine/OpenViking/pull/913
* @JasonOA888 made their first contribution in https://github.com/volcengine/OpenViking/pull/915
* @itlackey made their first contribution in https://github.com/volcengine/OpenViking/pull/908
* @3kyou1 made their first contribution in https://github.com/volcengine/OpenViking/pull/914
* @sacloudy made their first contribution in https://github.com/volcengine/OpenViking/pull/939
* @Ghostknight0 made their first contribution in https://github.com/volcengine/OpenViking/pull/957

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.9...v0.2.11


## v0.2.10 (2026-03-24)

# LiteLLM 安全热修复 Release Note

更新时间：2026-03-24

## 背景

由于上游依赖 `LiteLLM` 出现公开供应链安全事件，OpenViking 在本次热修复中临时禁用所有 LiteLLM 相关入口，以避免继续安装或运行到受影响依赖。

## 变更内容

- 移除根依赖中的 `litellm`
- 移除根 `uv.lock` 中的 `litellm`
- 禁用 LiteLLM 相关的 VLM provider 入口
- 禁用 bot 侧 LiteLLM provider 和图片工具入口
- 增加 LiteLLM 已禁用的回归测试

## 建议操作

建议用户立即执行以下动作：

1. 检查运行环境中是否安装 `litellm`
2. 卸载可疑版本并重建虚拟环境、容器镜像或发布产物
3. 对近期安装过可疑版本的机器轮换 API Key 和相关凭证
4. 升级到本热修复版本

可用命令：

```bash
python -m pip show litellm
python -m pip uninstall -y litellm
```

## 兼容性说明

这是一个以止损为目标的防御性热修复版本。LiteLLM 相关能力会暂时不可用，直到上游给出可信的修复版本和完整事故说明。

## 参考链接

- 上游 issue: <https://github.com/BerriAI/litellm/issues/24512>
- PyPI 项目页: <https://pypi.org/project/litellm/>
- GitHub Security: <https://github.com/BerriAI/litellm/security>
- OpenViking 热修分支: <https://github.com/volcengine/OpenViking/tree/hotfix/v0.2.10-disable-litellm>


## v0.2.9 (2026-03-19)

## What's Changed
* fix(resource): enforce agent-level watch task isolation by @lyfmt in https://github.com/volcengine/OpenViking/pull/762
* feat(embedder): use summary for file embedding in semantic pipeline by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/765
* Fix/bot readme by @chenjw in https://github.com/volcengine/OpenViking/pull/774
* Fix/increment update dir vector store by @myysy in https://github.com/volcengine/OpenViking/pull/773
* fix(plugin): restore bug fixes from #681 and #688 lost during #662 merge by @qin-ctx in https://github.com/volcengine/OpenViking/pull/779
* docs: add docker compose instructions and mac port forwarding tip to … by @fengluodb in https://github.com/volcengine/OpenViking/pull/781
* Update docs by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/782
* feat(ci): add comprehensive Qodo PR-Agent review rules by @chethanuk in https://github.com/volcengine/OpenViking/pull/780
* [feat](bot):Add mode config, add debug mode, add /remember cmd by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/757
* fix(vectordb): share single adapter across account backends to prevent RocksDB lock contention by @ahmedhesham6 in https://github.com/volcengine/OpenViking/pull/777

## New Contributors
* @fengluodb made their first contribution in https://github.com/volcengine/OpenViking/pull/781
* @ahmedhesham6 made their first contribution in https://github.com/volcengine/OpenViking/pull/777

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.8...v0.2.9


## v0.2.8 (2026-03-19)

# OpenViking v0.2.8 发布公告

OpenViking v0.2.8 现已发布。

这是一次围绕 **上下文工程能力、插件生态、检索与记忆链路、可观测性以及工程兼容性** 持续增强的版本更新。  
整体来看，v0.2.8 以功能补强和稳定性修复为主，适合现有用户升级；如果你在使用 OpenClaw / OpenCode 插件、长会话记忆、资源同步或自定义模型接入，本次更新尤其值得关注。

## 本次更新亮点

### 1. 插件生态继续升级，OpenClaw / OpenCode 集成更完整
- `openclaw-plugin` 升级到 **2.0**，从 memory plugin 进一步演进为 **context engine**。
  - 相关 PR：[#662](https://github.com/volcengine/OpenViking/pull/662)
- 新增并完善 **OpenCode memory plugin example**，补充 attribution 与后续插件更新。
  - 相关 PR：[#569](https://github.com/volcengine/OpenViking/pull/569)、[#588](https://github.com/volcengine/OpenViking/pull/588)、[#678](https://github.com/volcengine/OpenViking/pull/678)
- 支持 **多智能体 memory isolation**，可基于 hook context 中的 `agentId` 做记忆隔离。
  - 相关 PR：[#637](https://github.com/volcengine/OpenViking/pull/637)
- 修复插件自动 recall 可能导致 agent 长时间挂起的问题，并补强 legacy commit 兼容。
  - 相关 PR：[#688](https://github.com/volcengine/OpenViking/pull/688)、[#697](https://github.com/volcengine/OpenViking/pull/697)
- MCP 查询服务补充 `api_key` 支持与默认配置能力，降低接入成本。
  - 相关 PR：[#691](https://github.com/volcengine/OpenViking/pull/691)、[#611](https://github.com/volcengine/OpenViking/pull/611)

### 2. Session / Memory 链路增强，长期记忆能力更稳
- 新增 **memory cold-storage archival**，通过 hotness scoring 管理长期记忆冷热分层。
  - 相关 PR：[#620](https://github.com/volcengine/OpenViking/pull/620)
- 新增长记忆 **chunked vectorization**，改善超长内容的向量化处理能力。
  - 相关 PR：[#734](https://github.com/volcengine/OpenViking/pull/734)
- 增加 `used()` 接口，用于上下文 / skill 使用追踪。
  - 相关 PR：[#684](https://github.com/volcengine/OpenViking/pull/684)
- 修复 async commit 过程中的上下文透传、批次内重复写入、非标准 LLM 响应处理等问题。
  - 相关 PR：[#610](https://github.com/volcengine/OpenViking/pull/610)、[#618](https://github.com/volcengine/OpenViking/pull/618)、[#701](https://github.com/volcengine/OpenViking/pull/701)
- Bot 场景下改用 `commit_async()`，减少阻塞风险，提升长会话稳定性。
  - 相关 PR：[#728](https://github.com/volcengine/OpenViking/pull/728)、[#733](https://github.com/volcengine/OpenViking/pull/733)

### 3. 检索与 Embedding 能力持续增强
- 分层检索中正式集成 **rerank**，并修复无 rerank 场景下的检索可用性问题。
  - 相关 PR：[#599](https://github.com/volcengine/OpenViking/pull/599)、[#754](https://github.com/volcengine/OpenViking/pull/754)
- 新增 **RetrievalObserver**，可用于检索质量指标观测，并已接入 `ov observer`。
  - 相关 PR：[#622](https://github.com/volcengine/OpenViking/pull/622)、[#623](https://github.com/volcengine/OpenViking/pull/623)
- Embedding 侧新增：
  - **Ollama** 本地 embedding provider 支持（[#644](https://github.com/volcengine/OpenViking/pull/644)）
  - **Voyage** dense embedding 支持（[#635](https://github.com/volcengine/OpenViking/pull/635)）
  - OpenAI-compatible provider 的 **extra HTTP headers** 支持（[#694](https://github.com/volcengine/OpenViking/pull/694)）
  - query / document **非对称 embedding** 支持（[#608](https://github.com/volcengine/OpenViking/pull/608)、[#702](https://github.com/volcengine/OpenViking/pull/702)）
  - OpenAI embedder 的 `key=value` 参数解析能力（[#711](https://github.com/volcengine/OpenViking/pull/711)）
- 修复 CJK token 估算偏低等问题，提升多语言场景下的稳定性。
  - 相关 PR：[#661](https://github.com/volcengine/OpenViking/pull/661)、[#658](https://github.com/volcengine/OpenViking/pull/658)

### 4. 资源、存储与解析链路更完善
- 新增 **resource watch scheduling** 与状态跟踪能力，资源同步流程更可控。
  - 相关 PR：[#709](https://github.com/volcengine/OpenViking/pull/709)
- 新增 **reindex endpoint**，支持内容手动修改后的重新 embedding。
  - 相关 PR：[#631](https://github.com/volcengine/OpenViking/pull/631)
- 解析能力新增对 **legacy `.doc` / `.xls`** 格式的支持。
  - 相关 PR：[#652](https://github.com/volcengine/OpenViking/pull/652)
- 修复从 URL 导入文件时文件名与扩展名丢失的问题。
  - 相关 PR：[#619](https://github.com/volcengine/OpenViking/pull/619)
- 存储层新增 **path locking** 与选择性 crash recovery，进一步提升写入安全性。
  - 相关 PR：[#431](https://github.com/volcengine/OpenViking/pull/431)
- 修复 tenant API 的隐式 root fallback、文件系统接口路径处理、工作目录 `~` 展开等问题。
  - 相关 PR：[#716](https://github.com/volcengine/OpenViking/pull/716)、[#647](https://github.com/volcengine/OpenViking/pull/647)、[#725](https://github.com/volcengine/OpenViking/pull/725)

### 5. VLM、Trace 与可观测性能力加强
- 新增 **request-level trace metrics** 与对应 API 支持。
  - 相关 PR：[#640](https://github.com/volcengine/OpenViking/pull/640)
- 新增 **memory extract telemetry breakdown**，帮助更细粒度地分析记忆提取过程。
  - 相关 PR：[#735](https://github.com/volcengine/OpenViking/pull/735)
- OpenAI VLM 支持 **streaming response handling**。
  - 相关 PR：[#756](https://github.com/volcengine/OpenViking/pull/756)
- 补充 `max_tokens` 参数以避免 vLLM 拒绝请求，并支持 OpenAI-compatible VLM 的自定义 HTTP headers。
  - 相关 PR：[#689](https://github.com/volcengine/OpenViking/pull/689)、[#723](https://github.com/volcengine/OpenViking/pull/723)
- 自动清理模型输出中的 `<think>` 标签，减少推理内容污染存储结果。
  - 相关 PR：[#690](https://github.com/volcengine/OpenViking/pull/690)

### 6. 工程兼容性与交付体验继续改进
- 修复 Windows zip 路径、代码仓库索引、Rust CLI 版本等跨平台问题。
  - 相关 PR：[#577](https://github.com/volcengine/OpenViking/pull/577)
- `agfs` Makefile 完成跨平台兼容性重构。
  - 相关 PR：[#571](https://github.com/volcengine/OpenViking/pull/571)
- Vectordb engine 支持按 **CPU variant** 拆分。
  - 相关 PR：[#656](https://github.com/volcengine/OpenViking/pull/656)
- Docker 构建链路持续修复，补齐 `build_support/` 拷贝逻辑。
  - 相关 PR：[#699](https://github.com/volcengine/OpenViking/pull/699)、[#705](https://github.com/volcengine/OpenViking/pull/705)
- Release workflow 支持发布 **Python 3.14 wheels**。
  - 相关 PR：[#720](https://github.com/volcengine/OpenViking/pull/720)

### 7. 文档与社区内容持续补强
- README、INSTALL、INSTALL-ZH 等文档持续更新。
  - 相关 PR：[#581](https://github.com/volcengine/OpenViking/pull/581)、[#582](https://github.com/volcengine/OpenViking/pull/582)、[#663](https://github.com/volcengine/OpenViking/pull/663)、[#666](https://github.com/volcengine/OpenViking/pull/666)、[#692](https://github.com/volcengine/OpenViking/pull/692)
- 新增 **日文文档**，进一步提升国际化支持。
  - 相关 PR：[#755](https://github.com/volcengine/OpenViking/pull/755)
- 补充 OpenClaw 插件升级说明与新链接修复。
  - 相关 PR：[#758](https://github.com/volcengine/OpenViking/pull/758)、[#761](https://github.com/volcengine/OpenViking/pull/761)
- 更新社区微信群二维码等资料，方便用户加入交流。
  - 相关 PR：[#649](https://github.com/volcengine/OpenViking/pull/649)

## 升级建议

- 如果你在使用 **OpenClaw 旧版 memory plugin**，建议重点阅读 2.0 升级说明后再升级。https://github.com/volcengine/OpenViking/blob/main/examples/openclaw-plugin/INSTALL-ZH.md
- 如果你依赖 **OpenAI-compatible 模型网关 / 本地模型服务**，建议关注本次新增的 headers、`max_tokens`、`api_key` 与 embedding 参数能力。
- 如果你在生产环境中使用 **长会话记忆、资源自动同步或多智能体场景**，建议升级后重点验证 memory commit、resource watch 和检索观测链路。

## 总结

v0.2.8 是一次面向真实智能体应用场景的持续演进版本。  
这次更新重点集中在：

- 插件生态升级，OpenClaw / OpenCode 集成更成熟
- Session / Memory 长链路能力增强
- 检索、Embedding 与观测能力进一步完善
- 资源、解析、存储链路稳定性提升
- 跨平台、Docker、CI 与发布体验持续优化

欢迎大家升级体验，并继续反馈问题与建议。

## 致谢

感谢所有贡献者参与本次版本更新，也欢迎本版本中的多位新贡献者加入社区。  
从累计变更来看，`v0.2.6 -> v0.2.8` 期间共有 **20 位新贡献者** 完成首次贡献。

## Full Changelog

- `v0.2.7...v0.2.8`  
  https://github.com/volcengine/OpenViking/compare/v0.2.7...v0.2.8

- `v0.2.6...v0.2.8`  
  https://github.com/volcengine/OpenViking/compare/v0.2.6...v0.2.8

## What's Changed
* docs: add MCP integration guide (EN + ZH) by @r266-tech in https://github.com/volcengine/OpenViking/pull/518
* Add Trendshift badge to README by @qin-ctx in https://github.com/volcengine/OpenViking/pull/536
* fix(session): propagate extractor failures to async task error by @dr3243636-ops in https://github.com/volcengine/OpenViking/pull/511
* feat(openclaw-memory-plugin): add default log configuration by @qin-ctx in https://github.com/volcengine/OpenViking/pull/541
* Add files via upload by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/543
* Update install.sh by @qin-ptr in https://github.com/volcengine/OpenViking/pull/545
* Revert "Update install.sh" by @qin-ptr in https://github.com/volcengine/OpenViking/pull/547
* refactor(openclaw-memory-plugin): use openclaw CLI for plugin configuration by @qin-ctx in https://github.com/volcengine/OpenViking/pull/550
* fix: correct Volcengine sparse/hybrid embedder and update sparse model docs by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/561
* feat: CLI sub-command optimization by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/560
* fix(storage): reject traversal segments before VikingFS access checks by @lyfmt in https://github.com/volcengine/OpenViking/pull/557
* feat(resource): implement incremental update with COW pattern by @myysy in https://github.com/volcengine/OpenViking/pull/535
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/556
* build(deps): bump docker/build-push-action from 6 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/555
* build(deps): bump actions/setup-python from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/554
* build(deps): bump docker/metadata-action from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/553
* feat(bot):Feishu channel mention, support PIC conversation, per-channel workspace,  by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/567
* feat: add --sender parameter to chat commands by @chenjw in https://github.com/volcengine/OpenViking/pull/562
* fix(Dockerfile): add rust ov by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/570
* Fix: change Role.ROOT to Role.USER, and mirror the HTTP server behavior     (sessions.py:94-95) by calling initialize_user_directories() and     initialize_agent_directories() at the start of create_session(). by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/572
* fix: use normalized path in ClassifiedFile for cross-platform consistency by @sponge225 in https://github.com/volcengine/OpenViking/pull/574
* fix(session): remove redundant parameters from archive call by @myysy in https://github.com/volcengine/OpenViking/pull/575
* feat:Add OpenCode memory plugin example by @LittleLory in https://github.com/volcengine/OpenViking/pull/569
* Update README.md by @Soda-Wong in https://github.com/volcengine/OpenViking/pull/581
* Update README_CN.md by @Soda-Wong in https://github.com/volcengine/OpenViking/pull/582
* Revert "feat(resource): implement incremental update with COW pattern" by @qin-ctx in https://github.com/volcengine/OpenViking/pull/584
* Limit buffered OpenViking stderr output by @callzhang in https://github.com/volcengine/OpenViking/pull/598
* fix(agfs): refactor Makefile for cross-platform compatibility by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/571
* fix: integrate rerank into hierarchical retriever by @mildred522 in https://github.com/volcengine/OpenViking/pull/599
* feat: generate a framework of config by @zhougit86 in https://github.com/volcengine/OpenViking/pull/600
* Follow up to #569: add attribution notice to OpenCode memory plugin example by @LittleLory in https://github.com/volcengine/OpenViking/pull/588
* fix: windows zip path, code repo indexing, search retrieval, account id, rust cli version... by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/577
* fix(core): add separator to agent_space_name to prevent hash collisions by @mvanhorn in https://github.com/volcengine/OpenViking/pull/609
* fix(session): pass ctx/user/session_id into async commit memory extraction by @mvanhorn in https://github.com/volcengine/OpenViking/pull/610
* fix: simplify embedding rate-limit re-enqueue and clean up tests by @qin-ctx in https://github.com/volcengine/OpenViking/pull/615
* fix(mcp): add early detection for multi-instance stdio contention by @mvanhorn in https://github.com/volcengine/OpenViking/pull/611
* fix(session): skip messages.jsonl in semantic file summary generation by @mvanhorn in https://github.com/volcengine/OpenViking/pull/617
* fix(session): handle non-dict LLM responses in memory extraction by @mvanhorn in https://github.com/volcengine/OpenViking/pull/618
* fix old assert by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/621
* feat(retrieve): add RetrievalObserver for retrieval quality metrics by @mvanhorn in https://github.com/volcengine/OpenViking/pull/622
* fix(parse): preserve original filename and extension when importing from URL by @mvanhorn in https://github.com/volcengine/OpenViking/pull/619
* fix: add observer for retrieval in "ov observer" by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/623
* fix: avoid report error when config is not set by @zhougit86 in https://github.com/volcengine/OpenViking/pull/629
* feat: allow ov tui to show all vector records in vikingdb by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/626
* feat(openclaw-plugin): support multi-agent memory isolation via hook context agentId by @yingriyanlong in https://github.com/volcengine/OpenViking/pull/637
* feat(trace): add request-level trace metrics and API support by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/640
* fix(embed): add text chunking for oversized embedding inputs (#616) by @lgYanami in https://github.com/volcengine/OpenViking/pull/642
* Add new wechat group qrcode image by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/649
* feat(parse): add support for legacy .doc and .xls file formats by @ngoclam9415 in https://github.com/volcengine/OpenViking/pull/652
* fix(language): add threshold for ko/ru/ar detection to avoid misclass… by @KorenKrita in https://github.com/volcengine/OpenViking/pull/658
* fix: file system operation endpoints /ov/fs/ls and /... in app.py by @orbisai0security in https://github.com/volcengine/OpenViking/pull/647
* feat: split vectordb engine by cpu variant by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/656
* feat(embedding): add Ollama provider support for local embedding by @chenxiaofei-cxf in https://github.com/volcengine/OpenViking/pull/644
* Update INSTALL-ZH.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/663
* feat: optimize the feature of ov tui - vector records by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/664
* Fix CJK token underestimation in _estimate_tokens fallback by @jnMetaCode in https://github.com/volcengine/OpenViking/pull/661
* Update INSTALL.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/666
* feat(embedder): add non-symmetric embedding support for query/document by @CHW0n9 in https://github.com/volcengine/OpenViking/pull/608
* fix(bot): when used as bot provider, minimax not support role: system, avoid the role by @zhougit86 in https://github.com/volcengine/OpenViking/pull/628
* feat(content): add reindex endpoint for re-embedding after manual edits by @deepakdevp in https://github.com/volcengine/OpenViking/pull/631
* feat(embedding): add Voyage dense embedding support by @kfiramar in https://github.com/volcengine/OpenViking/pull/635
* Feature/add resource increment by @myysy in https://github.com/volcengine/OpenViking/pull/659
* feat(opencode): update plugin by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/678
* Improve FastAPI service clarity by @itsviseph in https://github.com/volcengine/OpenViking/pull/670
* feat(session): add used() endpoint for context/skill tracking by @qin-ctx in https://github.com/volcengine/OpenViking/pull/684
* fix(memory-openviking): share pending clientPromise across dual-context registrations by @Boshoff93 in https://github.com/volcengine/OpenViking/pull/681
* fix(semantic): add budget guard to overview generation with batched summarization by @deepakdevp in https://github.com/volcengine/OpenViking/pull/683
* Update INSTALL-ZH.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/692
* fix(vlm): strip <think> reasoning tags from model responses by @qin-ctx in https://github.com/volcengine/OpenViking/pull/690
* fix(plugin): wrap auto-recall in withTimeout to prevent indefinite agent hang by @mvanhorn in https://github.com/volcengine/OpenViking/pull/688
* feat(embedder): support extra HTTP headers for OpenAI-compatible providers by @Astro-Han in https://github.com/volcengine/OpenViking/pull/694
* fix(plugin): memcommit session resolution and legacy commit compatibility by @LittleLory in https://github.com/volcengine/OpenViking/pull/697
* fix(docker): copy build_support/ into container image by @qin-ctx in https://github.com/volcengine/OpenViking/pull/699
* fix(session): add batch-internal dedup to prevent duplicates within same commit (#687) by @Astro-Han in https://github.com/volcengine/OpenViking/pull/701
* feat(embedder): Gemini Embedding 2 multimodal support (text + image/video/audio/PDF) by @chethanuk in https://github.com/volcengine/OpenViking/pull/607
* Revert: feat(embedder): Gemini Embedding 2 multimodal support (#607) by @qin-ctx in https://github.com/volcengine/OpenViking/pull/703
* fix: docker by @qin-ctx in https://github.com/volcengine/OpenViking/pull/705
* feat(embedding): combine document embedder and query embedder to avoi… by @zhougit86 in https://github.com/volcengine/OpenViking/pull/702
* feat(session): add memory cold-storage archival via hotness scoring by @mvanhorn in https://github.com/volcengine/OpenViking/pull/620
* fix(ci): publish wheels for Python 3.14 in release workflow by @illusion77 in https://github.com/volcengine/OpenViking/pull/720
* fix: expand tilde (~) in storage workspace paths by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/725
* fix(auth): reject implicit root fallback on tenant APIs by @Astro-Han in https://github.com/volcengine/OpenViking/pull/716
* feat: add key=value parameter parsing to OpenAI embedder by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/711
* fix(mcp): add api_key support and configurable defaults to MCP query server by @mvanhorn in https://github.com/volcengine/OpenViking/pull/691
* fix(vlm): add max_tokens parameter to VLM completion calls to prevent vLLM rejection by @mvanhorn in https://github.com/volcengine/OpenViking/pull/689
* feat(storage): add path locking and selective crash recovery for write operations by @qin-ctx in https://github.com/volcengine/OpenViking/pull/431
* feat(resources): add resource watch scheduling and status tracking by @myysy in https://github.com/volcengine/OpenViking/pull/709
* fix(bot):user memory message role changed to 'user',session add token use by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/733
* Add support for custom HTTP headers in VLM models (OpenAI-compatible) by @KorenKrita in https://github.com/volcengine/OpenViking/pull/723
* feat(vlm): add streaming response handling for OpenAI VLM by @KorenKrita in https://github.com/volcengine/OpenViking/pull/740
* revert-embedding-chunking by @qin-ctx in https://github.com/volcengine/OpenViking/pull/741
* feat(openclaw-plugin 2.0): from memory plugin to context engine by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/662
* Revert "feat(vlm): add streaming response handling for OpenAI VLM" by @KorenKrita in https://github.com/volcengine/OpenViking/pull/745
* feat(session): add chunked vectorization for long memories by @deepakdevp in https://github.com/volcengine/OpenViking/pull/734
* fix(retrieval): allow find without rerank and preserve level-2 rerank scores by @mildred522 in https://github.com/volcengine/OpenViking/pull/754
* docs: add Japanese documents by @eltociear in https://github.com/volcengine/OpenViking/pull/755
* feat(vlm): add streaming response handling for OpenAI VLM by @KorenKrita in https://github.com/volcengine/OpenViking/pull/756
* add openclaw-plugin upgrade description by @wlff123 in https://github.com/volcengine/OpenViking/pull/758
* fix(session): replace blocking commit() with commit_async() in bot by @deepakdevp in https://github.com/volcengine/OpenViking/pull/728
* doc.fix openclaw new plugin link by @KorenKrita in https://github.com/volcengine/OpenViking/pull/761
* feat: add memory extract telemetry breakdown by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/735
* update docs for openclaw-plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/766
* fix(plugin): add timeout protection to getClient() in before_prompt_build hook by @Meskjei in https://github.com/volcengine/OpenViking/pull/749
* feat(client): add account/user params for root key multi-tenant auth by @qin-ctx in https://github.com/volcengine/OpenViking/pull/767
* fix(bot): root key add account ser by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/770
* fix(vectordb): fix croaring avx by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/771

## New Contributors
* @lyfmt made their first contribution in https://github.com/volcengine/OpenViking/pull/557
* @LittleLory made their first contribution in https://github.com/volcengine/OpenViking/pull/569
* @callzhang made their first contribution in https://github.com/volcengine/OpenViking/pull/598
* @mvanhorn made their first contribution in https://github.com/volcengine/OpenViking/pull/609
* @yingriyanlong made their first contribution in https://github.com/volcengine/OpenViking/pull/637
* @lgYanami made their first contribution in https://github.com/volcengine/OpenViking/pull/642
* @ngoclam9415 made their first contribution in https://github.com/volcengine/OpenViking/pull/652
* @KorenKrita made their first contribution in https://github.com/volcengine/OpenViking/pull/658
* @orbisai0security made their first contribution in https://github.com/volcengine/OpenViking/pull/647
* @chenxiaofei-cxf made their first contribution in https://github.com/volcengine/OpenViking/pull/644
* @jnMetaCode made their first contribution in https://github.com/volcengine/OpenViking/pull/661
* @CHW0n9 made their first contribution in https://github.com/volcengine/OpenViking/pull/608
* @deepakdevp made their first contribution in https://github.com/volcengine/OpenViking/pull/631
* @kfiramar made their first contribution in https://github.com/volcengine/OpenViking/pull/635
* @itsviseph made their first contribution in https://github.com/volcengine/OpenViking/pull/670
* @Boshoff93 made their first contribution in https://github.com/volcengine/OpenViking/pull/681
* @Astro-Han made their first contribution in https://github.com/volcengine/OpenViking/pull/694
* @chethanuk made their first contribution in https://github.com/volcengine/OpenViking/pull/607
* @illusion77 made their first contribution in https://github.com/volcengine/OpenViking/pull/720
* @eltociear made their first contribution in https://github.com/volcengine/OpenViking/pull/755
* @Meskjei made their first contribution in https://github.com/volcengine/OpenViking/pull/749

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.6...v0.2.8


## v0.2.6 (2026-03-11)

# OpenViking v0.2.6 发布公告

OpenViking v0.2.6 已发布。

这是一次聚焦体验优化和稳定性增强的小版本更新。相比 v0.2.5，这一版不仅带来了更顺手的命令行交互和全新的 Console，也补齐了会话异步提交、后台任务跟踪、资源导入目录结构保留等关键能力，同时在 openclaw memory plugin、安装流程、跨平台兼容性和 CI 稳定性上做了大量修复与打磨。

## 重点更新

### 1. CLI 与对话体验进一步升级

- `ov chat` 现在基于 `rustyline` 提供更完整的行编辑体验，终端交互更自然，不再出现常见的方向键控制字符问题。
- 新增 Markdown 渲染能力，终端中的回答展示更清晰，代码块、列表等内容可读性更好。
- 支持聊天历史记录，同时提供关闭格式化和关闭历史记录的选项，便于在不同终端环境中按需使用。

### 2. 服务端异步能力增强，长任务不再轻易阻塞

- Session commit 新增异步提交能力，并支持通过 `wait` 参数控制是否同步等待结果。
- 当选择后台执行时，OpenViking 现在会返回可追踪的任务信息，调用方可以通过任务接口查询状态、结果或错误。
- 服务端新增可配置 worker count，进一步缓解单 worker 场景下长任务阻塞请求的问题。

这意味着在记忆提取、归档总结等耗时操作较多的场景下，服务端的可用性和可观测性都有明显提升。

### 3. 新增 Console，并持续增强 Bot 与资源操作能力

- 本版本新增独立的 OpenViking Console，提供更直观的 Web 控制台入口，方便调试、调用和查看接口结果。
- Bot 能力继续增强，新增 eval 能力，开放 `add-resource` 工具，并支持飞书进度通知等扩展能力。
- 资源导入支持 `preserve_structure` 选项，扫描目录时可以保留原始目录层级，适合更复杂的知识组织方式。
- 同时修复了 grep、glob、`add-resource` 等场景下的一些响应问题，提升日常使用稳定性。

### 4. openclaw memory plugin 能力和稳定性大幅完善

- openclaw memory plugin 在这一版中获得了较大幅度增强，补充了更完整的插件能力与相关示例。
- 插件安装链路进一步完善，现已支持通过 npm 包方式安装 setup-helper，部署和升级都更直接。
- 修复了本地运行、端口管理、配置保留、缺失文件下载、配置覆盖等一系列影响安装和使用体验的问题。
- Memory consolidation 与 skill tool memory 相关问题也得到修复，进一步提升记忆链路的稳定性。
- Vikingbot 作为 OpenViking 可选依赖的集成方式也做了完善，降低了插件和 bot 协同使用时的接入门槛。

对于希望将 OpenViking 与 memory / agent 工作流结合使用的开发者来说，这一版的可用性提升会比较明显。

### 5. 安装、跨平台与工程质量继续补强

- 新增 Linux ARM 支持，进一步扩展了 OpenViking 的部署平台范围。
- 修复了 Windows 下 UTF-8 BOM 配置文件的兼容问题，减少配置读取失败的情况。
- 修复了 `install.sh`、`ov.conf.example`、setup 失败退出等问题，并补充了 openclaw memory plugin 的 npm 包安装路径，提升首次安装和配置过程的成功率。
- CI 侧将 GitHub Actions runner 固定到明确 OS 版本，减少环境漂移带来的构建与发布不确定性。

## 总结

v0.2.6 是一个兼顾新能力与稳定性的版本。

如果你主要通过 CLI 或 Bot 使用 OpenViking，这一版会带来更顺手的交互体验；如果你在服务端接入中依赖会话提交、记忆提取和后台任务执行，这一版会带来更好的异步能力与可观测性；如果你在尝试 openclaw memory plugin 或跨平台部署，这一版也补上了不少过去容易踩坑的细节。

欢迎社区继续反馈使用体验、提交 Issue 和 PR，一起把 OpenViking 打磨得更稳、更好用。

## New Contributors

- @markwhen 首次贡献于 https://github.com/volcengine/OpenViking/pull/474
- @dr3243636-ops 首次贡献于 https://github.com/volcengine/OpenViking/pull/472
- @ctudoudou 首次贡献于 https://github.com/volcengine/OpenViking/pull/487
- @lixingjia77 首次贡献于 https://github.com/volcengine/OpenViking/pull/494

感谢所有参与 v0.2.6 开发、修复、文档和工程改进的贡献者。

## Full Changelog

https://github.com/volcengine/OpenViking/compare/v0.2.5...v0.2.6



## What's Changed
* feat: improve ov chat UX with rustyline and markdown rendering by @chenjw in https://github.com/volcengine/OpenViking/pull/466
* fix: grep for binding-client by @markwhen in https://github.com/volcengine/OpenViking/pull/474
* feat(server): add configurable worker count to prevent single-worker blocking by @r266-tech in https://github.com/volcengine/OpenViking/pull/470
* feat(sessions): add async commit support with wait parameter by @dr3243636-ops in https://github.com/volcengine/OpenViking/pull/472
* [feat]: openclaw-memory-plugin: 新增插件功能 by @qin-ptr in https://github.com/volcengine/OpenViking/pull/479
* fix: openclaw plugin local run by @qin-ptr in https://github.com/volcengine/OpenViking/pull/480
* feat: support linux arm by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/482
* fix: fix ov.conf.example by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/488
* fix vikingbot grep/glob/add_resource get response issue by @typeck in https://github.com/volcengine/OpenViking/pull/491
* docs: translate VLM providers section in README to English by @ctudoudou in https://github.com/volcengine/OpenViking/pull/487
* refactor: remove agfs port configuration by @qin-ctx in https://github.com/volcengine/OpenViking/pull/483
* fix: add-resource --to and --parent by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/475
* fix: fix err log by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/495
* fix: remve emtpy merge by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/496
* fix(search): use session summaries in search and cap intent summary l… by @lixingjia77 in https://github.com/volcengine/OpenViking/pull/494
* fix: improve file type detection and C/C++ AST extraction by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/497
* Revert "refactor: remove agfs port configuration" by @Soda-Wong in https://github.com/volcengine/OpenViking/pull/498
* Fix/bot Fix Memory Consolidation Issues and Integrate Vikingbot as   OpenViking Optional Dependency by @chenjw in https://github.com/volcengine/OpenViking/pull/492
* fix(config): handle UTF-8 BOM in config files on Windows (#499) by @r266-tech in https://github.com/volcengine/OpenViking/pull/500
* fix https://github.com/volcengine/OpenViking/issues/477 by @chenjw in https://github.com/volcengine/OpenViking/pull/503
* fix(setup): quit setup when fail by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/504
* feat(tasks): add async task tracking API for background operations by @dr3243636-ops in https://github.com/volcengine/OpenViking/pull/476
* fix(openclaw-memory-plugin): improve port management and preserve existing config by @qin-ctx in https://github.com/volcengine/OpenViking/pull/513
* docs: OpenViking Skills for search, add, operate by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/510
* fix(openclaw-memory-plugin): add missing files and merge config instead of overwriting by @qin-ctx in https://github.com/volcengine/OpenViking/pull/516
* fix install.sh by @qin-ptr in https://github.com/volcengine/OpenViking/pull/517
* feat: Add console by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/383
* feat(bot):Add eval function(support locomo, skillsbench), open add-resource tool, add feishu progress notification capability by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/506
* Fix/skill tool memory by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/514
* feat(resource): add preserve_structure option for directory scanning by @r266-tech in https://github.com/volcengine/OpenViking/pull/509
* ci: pin GitHub Actions runners to explicit OS versions by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/508
* feat(setup-helper): 用 install.js 替换 cli.js，发布为 npm 包 by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/524

## New Contributors
* @markwhen made their first contribution in https://github.com/volcengine/OpenViking/pull/474
* @dr3243636-ops made their first contribution in https://github.com/volcengine/OpenViking/pull/472
* @ctudoudou made their first contribution in https://github.com/volcengine/OpenViking/pull/487
* @lixingjia77 made their first contribution in https://github.com/volcengine/OpenViking/pull/494

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.5...v0.2.6


## v0.2.5 (2026-03-06)

## What's Changed
* docs: use openviking-server to launch server by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/398
* fix: Session.add_message() support parts parameter by @qin-ctx in https://github.com/volcengine/OpenViking/pull/399
* feat: support GitHub tree/<ref> URL for code repository import by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/400
* fix: improve ISO datetime parsing by @zztdandan in https://github.com/volcengine/OpenViking/pull/404
* feat(pdf): extract bookmarks as markdown headings for hierarchical parsing by @r266-tech in https://github.com/volcengine/OpenViking/pull/403
* feat: add index control to add_resource and refactor embedding logic by @Jay-ju in https://github.com/volcengine/OpenViking/pull/401
* fix: support short-format URIs in VikingURI and VikingFS access control by @r266-tech in https://github.com/volcengine/OpenViking/pull/402
* fix: handle None data in skill_processor._parse_skill() by @r266-tech in https://github.com/volcengine/OpenViking/pull/405
* fix: fix add-resource by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/409
* fix: treat only .zip as archive; avoid unzipping ZIP-based container … by @sponge225 in https://github.com/volcengine/OpenViking/pull/410
* fix(cli): support git@ SSH URL format in add-resource by @myysy in https://github.com/volcengine/OpenViking/pull/411
* feat(pdf): add font-based heading detection and refactor PDF/Markdown parsing by @qin-ctx in https://github.com/volcengine/OpenViking/pull/413
* 支持通过curl方式安装部署openclaw+openviking插件 by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/415
* Feature/vikingbot_opt： OpenAPI interface standardization；Feishu multi-user experience;  observability enhancements; configuration system modernization. by @chenjw in https://github.com/volcengine/OpenViking/pull/419
* docs: pip upgrade suggestion by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/418
* feat: define a system path for future deployment by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/423
* chore: downgrade golang version limit, update vlm version to seed 2.0 by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/425
* [fix]: 修复通过curl命令安装场下下ubuntu/debian等系统触发系统保护无法安装的openviking的问题 by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/426
* fix(session): trigger semantic indexing for parent directory after memory extraction by @qin-ctx in https://github.com/volcengine/OpenViking/pull/429
* feat(bot):Refactoring, New Evaluation Module, Feishu channel Opt & Feature Enhancements by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/428
* chore: agfs-client默认切到binding-client模式 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/430
* feat(agfs): add ripgrep-based grep acceleration and fix vimgrep line parser by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/432
* [WIP] ci: add automated PR review workflow using Doubao model by @qin-ctx in https://github.com/volcengine/OpenViking/pull/434
* fix(ci): use OPENAI_KEY instead of OPENAI.KEY in pr-review workflow by @qin-ctx in https://github.com/volcengine/OpenViking/pull/435
* build(deps): bump actions/download-artifact from 7 to 8 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/442
* build(deps): bump actions/upload-artifact from 6 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/441
* build(deps): bump docker/login-action from 3 to 4 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/440
* build(deps): bump docker/setup-qemu-action from 3 to 4 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/438
* build(deps): bump docker/setup-buildx-action from 3 to 4 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/439
* fix(packaging): sdist 排除运行时二进制 by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/447
* ci: enhance PR review with severity classification and checklist by @qin-ctx in https://github.com/volcengine/OpenViking/pull/437
* enable injection for Collection and CollectionAdaptor by @zhougit86 in https://github.com/volcengine/OpenViking/pull/414
* chore: 编译子命令失败报错, golang版本最低要求1.22+ by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/444
* feat(viking_fs) support async grep by @typeck in https://github.com/volcengine/OpenViking/pull/448
* OpenViking Plugin Exception Handling & Fixing by @wlff123 in https://github.com/volcengine/OpenViking/pull/449
* feat(agfs): make binding client optional, add server bootstrap, tune logging and CI by @qin-ctx in https://github.com/volcengine/OpenViking/pull/451
* fix: rust compile in uv pip install -e . by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/452
* [wip]Enhance OpenViking Status Checks in the OpenClaw Plugin by @wlff123 in https://github.com/volcengine/OpenViking/pull/453
* FIX: fixes multiple issues in the OpenViking chat functionality and unifies session ID generation logic between Python and Rust CLI implementations. by @chenjw in https://github.com/volcengine/OpenViking/pull/446
* chore: 增加Makefile,方便build by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/450
* fix(bot): fix Telegram channel init crash and empty content for Claude by @ponsde in https://github.com/volcengine/OpenViking/pull/421
* fix: suport uv pip install openviking[bot] by @chenjw in https://github.com/volcengine/OpenViking/pull/457
* fix: agfs-client默认使用http-client by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/459
* fix(agfs): fix agfs binding-client import error by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/458
* openclaw-memory-plugin: ov.conf backend/agfs, default embedding 25121… by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/460
* vikingbot version to 3.10 by @chenjw in https://github.com/volcengine/OpenViking/pull/461
* update vikingbot version to 0.1.3 by @chenjw in https://github.com/volcengine/OpenViking/pull/462
* fix: github zip download timeout by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/463

## New Contributors
* @zztdandan made their first contribution in https://github.com/volcengine/OpenViking/pull/404
* @Jay-ju made their first contribution in https://github.com/volcengine/OpenViking/pull/401
* @sponge225 made their first contribution in https://github.com/volcengine/OpenViking/pull/410
* @zhougit86 made their first contribution in https://github.com/volcengine/OpenViking/pull/414
* @typeck made their first contribution in https://github.com/volcengine/OpenViking/pull/448

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.3...v0.2.5


## v0.2.3 (2026-03-03)

## Breaking Change
After upgrading, datasets/indexes generated by historical versions are not compatible with the new version and cannot be reused directly. Please rebuild the datasets after upgrading (a full rebuild is recommended) to avoid retrieval anomalies, inconsistent filtering results, or runtime errors.
Stop the service -> rm -rf ./your-openviking-workspace -> restart the service with the openviking-server command.

## What's Changed
* Feat: CLI optimization by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/389
* Update README.md by @Soda-Wong in https://github.com/volcengine/OpenViking/pull/392
* Update README_CN.md by @Soda-Wong in https://github.com/volcengine/OpenViking/pull/391
* feat: support glob -n and cmd echo by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/395
* fix: fix release by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/396

## New Contributors
* @Soda-Wong made their first contribution in https://github.com/volcengine/OpenViking/pull/392

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.2...v0.2.3


## v0.2.2 (2026-03-03)

##  Breaking Change

Warning: This Release includes Breaking Chage! Before upgrading, you should stop VikingDB Server and clear workspace dir first.

## What's Changed
* fix ci by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/363
* 在readme补充千问使用方法 by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/364
* feat(parse): Add C# AST extractor support by @suraciii in https://github.com/volcengine/OpenViking/pull/366
* chore: bump CLI version to 0.2.1 by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/370
* fix: normalize OpenViking memory target paths by @wlff123 in https://github.com/volcengine/OpenViking/pull/373
* fix: fix filter when multi tenants by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/374
* docs/update_wechat by @qin-ctx in https://github.com/volcengine/OpenViking/pull/377
* Fix retriever by @zhoujh01 in https://github.com/volcengine/OpenViking/pull/382
* fix(git): support git@ SSH URLs with regression-safe repo detection (#317) by @r266-tech in https://github.com/volcengine/OpenViking/pull/385
* fix(agfs): 预编译agfs所依赖的lib/bin,无需安装时构建 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/388

## New Contributors
* @suraciii made their first contribution in https://github.com/volcengine/OpenViking/pull/366
* @wlff123 made their first contribution in https://github.com/volcengine/OpenViking/pull/373

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.2.1...v0.2.2


## v0.2.1 (2026-02-28)

This is a **core feature preview release**. Please note that performance and consistency have not been fully optimized, so use with caution.

```
Before you upgrade, please remove old ov.conf and old data directory, and then follow the new README.md to deploy!
```

## 1. Core Capability Upgrades: Multi-tenancy, Cloud-Native & OpenClaw/OpenCode Adaptation
- **Multi-tenancy**: Implemented foundational multi-tenancy support at the API layer (#260, #283), laying the groundwork for isolated usage across multiple users/teams.
- **Cloud-Native Support**: Added support for cloud-native VikingDB (#279), improved cloud deployment documentation and Docker CI workflows (#320), expanding from local deployment to cloud-edge integration.
- **OpenClaw Integration**: Added official installation for the `openclaw-openviking-plugin` (#307), strengthening integration with OpenClaw.
- **OpenCode Support**: Introduced the `opencode` plugin and updated documentation (#351), extending capabilities for code-related scenarios.

## 2. Deep Optimization of the Database Storage Foundation
- **Architecture Refactor**: Refactored the vector database interface (#327) and removed the `with_vector` parameter from query APIs (#338) to simplify the interface design.
- **Performance Optimizations**:
  - Integrated KRL to optimize vector search on ARM Kunpeng architectures (#256).
  - Enabled AVX2 by default and disabled AVX512 for x86 builds (#291), balancing compatibility and performance.
- **BugFixes**: Resolved missing Volcengine URI prefixes for VikingDB, invalidated `is_leaf` filters (#294), and fixed vector storage lock contention during fast restarts (#343).
- **AGFS Enhancements**: Added AGFS binding client (#304), fixed AGFS SDK installation/import issues (#337, #355), and improved filesystem integration.
- **Code Scenario Improvements**: Added AST-based code skeleton extraction mode (#334), supported private GitLab domains for code repositories (#285), and optimized GitHub ZIP download (#267).

## 3. Improved CLI Toolchain (Greatly Enhanced Usability)
Numerous UX improvements for the `ov` CLI to lower barriers to usage:
- Added `ov` command wrapper (#325) and fixed bugs in the CLI wrapper, repo URI handling, and `find` command (#336, #339, #347).
- Enhanced `add-resource` functionality with unit tests (#323) and added ZIP upload support for skills via the `add_skill` API (#312).
- Configuration Extensions: Added timeout support in `ovcli.conf` (#308) and fixed agent_id issues in the Rust CLI (#308).
- Version Support: Added the `--version` flag to `openviking-server` (#358) for easy version validation.

## What's Changed
* docs : update wechat by @qin-ctx in https://github.com/volcengine/OpenViking/pull/264
* feat: 多租户 Phase 1 - API 层多租户能力 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/260
* 增加openviking/eval模块，用于评估测试 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/265
* feat(vectordb): integrate KRL for ARM Kunpeng vector search optimization by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/256
* feat: concurrent embedding, GitHub ZIP download, read offset/limit by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/267
* fix: claude code memory-plugin example:add_message改为写入TextPart列表，避免session解析异常 by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/268
* Feat/add parts support to http api by @SeanZ in https://github.com/volcengine/OpenViking/pull/270
* tests(parsers): add unit tests for office extensions within add_resou… by @shaoeric in https://github.com/volcengine/OpenViking/pull/273
* feat: break change, remove is_leaf scalar and use level instead by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/271
* fix(api): complete parts support in SDK layers and simplify error handling by @SeanZ in https://github.com/volcengine/OpenViking/pull/275
* feat: support cloud vikingDB by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/279
* Fix image_summary bug by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/277
* feat(config): expose embedding.max_concurrent and vlm.max_concurrent … by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/282
* Multi tenant by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/283
* feat: allow private gitlab domain for code repo by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/285
* fix(storage): idempotent rm/mv operations with vector index sync by @SeanZ in https://github.com/volcengine/OpenViking/pull/284
* refactor(filter): replace prefix operator with must by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/292
* fix: VikingDB volcengine URI prefix loss and stale is_leaf filter by @qin-ctx in https://github.com/volcengine/OpenViking/pull/294
* fix(vectordb): default x86 build to AVX2 and disable AVX512 by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/291
* 对齐 docs 和代码里 storage config 的 workspace 属性 by @myysy in https://github.com/volcengine/OpenViking/pull/289
* fix: correct storage.update() call signature in _update_active_counts() by @ponsde in https://github.com/volcengine/OpenViking/pull/280
* feat(memory): add hotness scoring for cold/hot memory lifecycle (#296) by @r266-tech in https://github.com/volcengine/OpenViking/pull/297
* feat(client): ovcli.conf 支持 timeout 配置 + 修复 Rust CLI agent_id by @qin-ctx in https://github.com/volcengine/OpenViking/pull/308
* fix(server): 未配置 root_api_key 时仅允许 localhost 绑定 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/310
* fix: resolve Gemini 404, directory collision, and Unicode decoding er… by @honjiaxuan in https://github.com/volcengine/OpenViking/pull/314
* feat(skill): support zip upload for add_skill API by @SeanZ in https://github.com/volcengine/OpenViking/pull/312
* feat(agfs): agfs新增binding client by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/304
* fix(parser): remove redundant mkdir in DirectoryParser by @honjiaxuan in https://github.com/volcengine/OpenViking/pull/318
* fix: use original directory name for temp URI lookup in TreeBuilder by @ponsde in https://github.com/volcengine/OpenViking/pull/319
* fix: handle is_healthy() AttributeError when not initialized (closes #298) by @r266-tech in https://github.com/volcengine/OpenViking/pull/322
* feat(docs,ci): 完善云上部署文档与 Docker CI 流程 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/320
* fix(agfs): import AGFSBindingClient error不阻塞http client使用 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/324
* feat: Enhance add-resource functionality in cli and add unit tests by @shaoeric in https://github.com/volcengine/OpenViking/pull/323
* feat: expose session(must_exist) and session_exists() on public API by @ponsde in https://github.com/volcengine/OpenViking/pull/321
* feat: wrapper ov command by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/325
* Feat/vectordb interface refactor by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/327
* build(deps): bump docker/build-push-action from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/313
* docs: add storage configuration guide by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/329
* fix: 修复单测以适配 vectordb 接口重构，统一测试数据路径 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/333
* feat(parse): add AST-based code skeleton extraction mode by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/334
* 添加openclaw-openviking-plugin插件安装方式 by @LinQiang391 in https://github.com/volcengine/OpenViking/pull/307
* fix: bugfix ov cli wrapper by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/336
* refactor: remove with_vector from query APIs by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/338
* fix: ov cmd and repo uri by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/339
* fix(agfs): 修复agfs binding-client安装问题, 清理agfs lib文件 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/337
* feat/vikingbot: viking bot mvp based on openviking by @yeshion23333 in https://github.com/volcengine/OpenViking/pull/335
* docs: add uv prerequisites to bot readme by @Tsan1024 in https://github.com/volcengine/OpenViking/pull/341
* fix(storage): 修复快速重启时向量存储锁竞争问题 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/343
* fix: fix cli find by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/347
* (Bug fix) to tool memory merge by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/346
* Fix/reminderbug 修复定时任务的bug by @chenjw in https://github.com/volcengine/OpenViking/pull/349
* Update Python version requirement to 3.10 by @qppq54s in https://github.com/volcengine/OpenViking/pull/348
* Validate ovpack ZIP member paths during import and add tests to reject unsafe entries by @13ernkastel in https://github.com/volcengine/OpenViking/pull/344
* fix(client): 修复单文件在远程server部署时上传失败问题 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/352
* fix(test): 修复测试稳定性问题，清理废弃代码 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/353
* fix(agfs): agfs sdk默认从本地安装 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/355
* eat(opencode): add opencode plugin and update docs by @yangxinxin-7 in https://github.com/volcengine/OpenViking/pull/351
* docs: 修复 openviking serve 命令引用并更新贡献文档 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/357
* feat: update README.md by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/356
* fix: 为 openviking-server 添加 --version 并修复 AGFS 模式判断 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/358
* chore: remove unsupported examples by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/359
* fix: tag by @qin-ctx in https://github.com/volcengine/OpenViking/pull/361

## New Contributors
* @yangxinxin-7 made their first contribution in https://github.com/volcengine/OpenViking/pull/267
* @SeanZ made their first contribution in https://github.com/volcengine/OpenViking/pull/270
* @myysy made their first contribution in https://github.com/volcengine/OpenViking/pull/289
* @ponsde made their first contribution in https://github.com/volcengine/OpenViking/pull/280
* @r266-tech made their first contribution in https://github.com/volcengine/OpenViking/pull/297
* @honjiaxuan made their first contribution in https://github.com/volcengine/OpenViking/pull/314
* @LinQiang391 made their first contribution in https://github.com/volcengine/OpenViking/pull/307
* @yeshion23333 made their first contribution in https://github.com/volcengine/OpenViking/pull/335
* @Tsan1024 made their first contribution in https://github.com/volcengine/OpenViking/pull/341
* @chenjw made their first contribution in https://github.com/volcengine/OpenViking/pull/349
* @qppq54s made their first contribution in https://github.com/volcengine/OpenViking/pull/348
* @13ernkastel made their first contribution in https://github.com/volcengine/OpenViking/pull/344

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.18...v0.2.1


## cli@0.2.0 (2026-02-27)

# OpenViking CLI v0.2.0

## Installation

### Quick Install (macOS/Linux)
```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/cli@0.2.0/crates/ov_cli/install.sh | bash
```

### Manual Installation
Download the appropriate binary for your platform below, extract it, and add it to your PATH.

The CLI command is simply `ov`:
```bash
# After extraction
chmod +x ov  # Unix only
mv ov /usr/local/bin/  # or any directory in your PATH

# Verify installation
ov --version
```

### Checksums
SHA256 checksums are provided for each binary for verification.

## Changes
See the [commit history](https://github.com/volcengine/OpenViking/commits/cli@0.2.0) for details.


## v0.1.18 (2026-02-23)

## What's Changed
* feat: add Rust CLI implementation [very fast] by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/162
* feat: make -o and --json global param by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/172
* feat: provide a test_ov.sh scripts as reference by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/173
* fix: short markdown parse filename by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/176
* Update 03-quickstart-server.md-en version by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/177
* feat: add markitdown-inspired file parsers (Word, PowerPoint, Excel, EPub, ZIP) by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/128
* feat: rename for consistency by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/179
* 上传最新的微信群二维码 by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/183
* Update 03-quickstart-server.md by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/174
* Update 01-about-us.md-微信群二维码地址更新 by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/184
* fix: build target name by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/181
* fix: fix handle github url right by @fatelei in https://github.com/volcengine/OpenViking/pull/180
* Update README_CN.md-新增云端部署跳转链接与说明 by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/186
* Update README.md-新增英文版readme云端部署内容 by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/187
* Update 01-about-us.md-英文版微信群二维码更新 by @yuyaoyoyo-svg in https://github.com/volcengine/OpenViking/pull/185
* fix: fix the difference of python CLI and rust CLI by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/188
* Feat/support multi providers ， OpenViking支持多providers by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/192
* Fix: 修复 rust CLI 命令中与 python CLI ls/tree 命令不一致的部分；add node limit arg by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/193
* feat: add_memory cli with ov-memory SKILL by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/195
* feat: add directory parsing support to OpenViking by @shaoeric in https://github.com/volcengine/OpenViking/pull/194
* fix: auto-rename on duplicate filename conflict by @DevEverything01 in https://github.com/volcengine/OpenViking/pull/197
* fix: guard against None candidate in search_by_id by @DevEverything01 in https://github.com/volcengine/OpenViking/pull/198
* fix bugs 修复provider为openai，但api_key并不是https://api.openai.com/v1而引起的大模型调用失败情况 by @BytedanceFu in https://github.com/volcengine/OpenViking/pull/200
* Update open_viking_config.py by @qin-ptr in https://github.com/volcengine/OpenViking/pull/206
* fix(parser): hash & shorten filenames that exceed filesystem limit by @DevEverything01 in https://github.com/volcengine/OpenViking/pull/205
* test: add comprehensive edge case tests by @aeromomo in https://github.com/volcengine/OpenViking/pull/208
* fix: invalid Go version format in agfs-server go.mod by @aeromomo in https://github.com/volcengine/OpenViking/pull/207
* fix: add input validation to search_by_id method by @aeromomo in https://github.com/volcengine/OpenViking/pull/209
* fix: improve binary content detection and null byte handling by @aeromomo in https://github.com/volcengine/OpenViking/pull/210
* suggestion: align grep and glob options by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/217
* suggestion: fix empty in --simple ls response by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/220
* feat: support basic ov tui for fs navigator, boost version 0.2.0 by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/213
* build(deps): bump actions/download-artifact from 4 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/221
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/222
* build(deps): bump actions/upload-artifact from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/223
* refactor(memory): redesign extraction/dedup flow and add conflict-a… by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/225
* fix: target directories retrieve by @mildred522 in https://github.com/volcengine/OpenViking/pull/227
* fix: skill search ranking - use overview for embedding and fix visited set filtering by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/228
* fix: use frontmatter description for skill vectorization instead of overview by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/229
* build(deps): bump actions/cache from 4 to 5 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/224
* feat: update media parsers by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/196
* fix: update memory by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/231
* fix: resolve -o option conflict between global output and filesystem output-format by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/240
* feat: add memory, resource and search skills by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/214
* fix(memex): add Feishu OAuth support, fix async deadlock, and adapt for local dev by @A11en0 in https://github.com/volcengine/OpenViking/pull/237
* feat(examples): simplify k8s Helm chart to MVP by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/234
* fix: convert Session.load() and get_context_for_search() to async to prevent deadlock by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/235
* feat: add Jina AI embedding provider by @hanxiao in https://github.com/volcengine/OpenViking/pull/245
* feat(docker): init by @simonsmh in https://github.com/volcengine/OpenViking/pull/238
* feat(examples): add Claude memory plugin example for OpenViking by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/246
* fix(fs): ls --simple skips abstract fetching and returns only URIs by @DevEverything01 in https://github.com/volcengine/OpenViking/pull/236
* fix(tui): correct root scopes in filesystem tree by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/250
* fix Invalid isoformat string on Windows due to high-precision timestamps by @kscale in https://github.com/volcengine/OpenViking/pull/252
* feat: support dynamic project_name config in VectorDB / volcengine by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/253
* feat: support tos oss by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/255
* fix: When the AGFS backend is s3, the error "pyagfs.exceptions.AGFSClientError: parent directory does not exist" occurs. by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/254
* fix: handle >6-digit fractional seconds in ISO timestamps on Windows by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/257
* fix: use reason as instruction fallback in resource processing by @haosenwang1018 in https://github.com/volcengine/OpenViking/pull/258
* feat: openviking upload zip and extract on serverside, for add-resource a local dir by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/249
* feat: wrap log configs in LogConfig, add log rotation by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/261

## New Contributors
* @yuyaoyoyo-svg made their first contribution in https://github.com/volcengine/OpenViking/pull/177
* @fatelei made their first contribution in https://github.com/volcengine/OpenViking/pull/180
* @BytedanceFu made their first contribution in https://github.com/volcengine/OpenViking/pull/192
* @DevEverything01 made their first contribution in https://github.com/volcengine/OpenViking/pull/197
* @qin-ptr made their first contribution in https://github.com/volcengine/OpenViking/pull/206
* @aeromomo made their first contribution in https://github.com/volcengine/OpenViking/pull/208
* @haosenwang1018 made their first contribution in https://github.com/volcengine/OpenViking/pull/240
* @hanxiao made their first contribution in https://github.com/volcengine/OpenViking/pull/245
* @simonsmh made their first contribution in https://github.com/volcengine/OpenViking/pull/238
* @kscale made their first contribution in https://github.com/volcengine/OpenViking/pull/252

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.17...v0.1.18


## cli@0.1.0 (2026-02-14)

# OpenViking CLI v0.1.0

## Installation

### Quick Install (macOS/Linux)
```bash
curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/refs/tags/cli@0.1.0/crates/ov_cli/install.sh | bash
```

### Manual Installation
Download the appropriate binary for your platform below, extract it, and add it to your PATH.

The CLI command is simply `ov`:
```bash
# After extraction
chmod +x ov  # Unix only
mv ov /usr/local/bin/  # or any directory in your PATH

# Verify installation
ov --version
```

### Checksums
SHA256 checksums are provided for each binary for verification.

## Changes
See the [commit history](https://github.com/volcengine/OpenViking/commits/cli@0.1.0) for details.


## v0.1.17 (2026-02-14)

## What's Changed
* Revert "feat: support dynamic project_name config  in VectorDB / volcengine" by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/167
* Fix/ci clean workspace by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/170
* fix: tree uri output error, and validate ov.conf before start by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/169


**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.16...v0.1.17


## v0.1.16 (2026-02-13)

## What's Changed
* fix: fix vectordb by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/164
* feat: make temp uri readable, and enlarge timeout of add-resource by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/165
* feat: support dynamic project_name config  in VectorDB / volcengine by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/161
* fix: server uvloop conflicts with nest_asyncio by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/166


**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.15...v0.1.16


## v0.1.15 (2026-02-13)

## What's Changed

Now you can try Server/CLI mode!

* refactor(client): 拆分 HTTP 客户端，分离嵌入模式与 HTTP 模式 by @qin-ctx in https://github.com/volcengine/OpenViking/pull/141
* Transaction store by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/136
* fix CI: correct patch targets in test_quick_start_lite.py by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/140
* fix/lifecycle by @qin-ctx in https://github.com/volcengine/OpenViking/pull/144
* Fix/lifecycle by @qin-ctx in https://github.com/volcengine/OpenViking/pull/146
* refactor: decouple QueueManager from VikingDBManager by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/149
* refactor: to accelerate cli launch speed, refactor openviking_cli dir by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/150
* efactor: to accelerate cli launch speed, refactor openviking_cli dir by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/151
* fix(vectordb): resolve timestamp format and collection creation issues by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/154
* doc: add multi-tenant-design by @qin-ctx in https://github.com/volcengine/OpenViking/pull/155
* fix: fix vectordb sparse by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/160
* [WIP]adapt for openclaw: add memory output language pipeline by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/137
* CLI commands (ls, tree) output optimize by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/156
* fix: replace bare except with Exception in llm utils by @thecaptain789 in https://github.com/volcengine/OpenViking/pull/152
* feat(parser): support repo branch and commit refs by @zeus-cht in https://github.com/volcengine/OpenViking/pull/147
* fix: temp dir check failed by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/163

## New Contributors
* @thecaptain789 made their first contribution in https://github.com/volcengine/OpenViking/pull/152
* @zeus-cht made their first contribution in https://github.com/volcengine/OpenViking/pull/147

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.14...v0.1.15


## v0.1.14 (2026-02-12)

## What's Changed
* build(deps): bump protobuf from 6.33.2 to 6.33.5 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/104
* refactor: cpp bytes rows by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/105
* fix: agfs port by @qin-ctx in https://github.com/volcengine/OpenViking/pull/110
* refactor: refactor agfs s3 backend config by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/113
* fix(depends): 修复pip依赖的安全漏洞 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/107
* feat: add HTTP Server and Python HTTP Client (T2 & T4) by @qin-ctx in https://github.com/volcengine/OpenViking/pull/109
* Add OpenClaw skill for OpenViking MCP integration by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/114
* docs: update docs and github workflows python version, python>=3.10 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/118
* Doc: suggest use ~/.openviking/ov.conf as a default configure path by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/119
* Parallel add by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/121
* feat(directory-scan): add directory pre-scan validation module with f… by @shaoeric in https://github.com/volcengine/OpenViking/pull/102
* fix(docs): fix relative paths in README files to match actual docs st… by @evpeople in https://github.com/volcengine/OpenViking/pull/122
* feat: use a default dir ~/.openviking to store configuration, fix win… by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/120
* feat: dag trigger embedding by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/123
* fix: fix windows by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/126
* fix: release py3.13 by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/127
* fix: remove await asyncio and call agfs directly by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/129
* fix(depends): 修复测试引入的依赖 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/130
* add_message改为写入TextPart列表，避免session消息解析异常; extract_session增加json转换方法 by @Mijamind719 in https://github.com/volcengine/OpenViking/pull/131
* feat: 新增 Bash CLI 基础框架与完整命令实现 (T3 + T5) by @qin-ctx in https://github.com/volcengine/OpenViking/pull/132
* fix: test case by @qin-ctx in https://github.com/volcengine/OpenViking/pull/133
* fix: fix release by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/138

## New Contributors
* @shaoeric made their first contribution in https://github.com/volcengine/OpenViking/pull/102
* @evpeople made their first contribution in https://github.com/volcengine/OpenViking/pull/122
* @Mijamind719 made their first contribution in https://github.com/volcengine/OpenViking/pull/131

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.12...v0.1.14


## v0.1.12 (2026-02-09)

## What's Changed
* feat: add search_with_sparse_logit_alpha by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/71
* refactor: Refactor S3 configuration structure and fix Python 3.9 compatibility issues by @baojun-zhang in https://github.com/volcengine/OpenViking/pull/73
* fix: fix ci by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/74
* refactor: unify async execution utilities into run_async by @qin-ctx in https://github.com/volcengine/OpenViking/pull/75
* docs: update community link by @qin-ctx in https://github.com/volcengine/OpenViking/pull/82
* build(deps): bump actions/download-artifact from 4 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/65
* build(deps): bump actions/github-script from 7 to 8 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/66
* build(deps): bump actions/checkout from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/67
* build(deps): bump actions/setup-go from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/68
* build(deps): bump actions/upload-artifact from 4 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/69
* feat: in chatmem example, add /time and /add_resource command by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/77
* Feature/new demo by @A11en0 in https://github.com/volcengine/OpenViking/pull/78
* Feat:支持原生部署的vikingdb by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/84
* WIP: fix: run memex locally for #78 by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/86
* feat(parse): extract shared upload utilities by @ze-mu-zhou in https://github.com/volcengine/OpenViking/pull/87
* docs: fiix related document links in /openviking/parse/parsers/README.md by @WuMingDao in https://github.com/volcengine/OpenViking/pull/88
* fix(parse): prevent Zip Slip path traversal in _extract_zip (CWE-22) by @ze-mu-zhou in https://github.com/volcengine/OpenViking/pull/89
* Path filter by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/92
* feat: use tabulate for observer by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/94
* fix(parser): fix temporary file leak in HTMLParser download link hand… by @Lettuceleaves in https://github.com/volcengine/OpenViking/pull/95
* perf: reuse query embeddings in hierarchical retriever by @mildred522 in https://github.com/volcengine/OpenViking/pull/93
* fix(agfs): close socket on error path in _check_port_available by @ze-mu-zhou in https://github.com/volcengine/OpenViking/pull/97
* fix(storage): make VikingFS.mkdir() actually create the target directory by @ze-mu-zhou in https://github.com/volcengine/OpenViking/pull/96
* fix: fix sparse by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/100
* feat: support query in mcp, tested with kimi by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/98
* fix: ignore TestVikingDBProject by @qin-ctx in https://github.com/volcengine/OpenViking/pull/103

## New Contributors
* @baojun-zhang made their first contribution in https://github.com/volcengine/OpenViking/pull/73
* @A11en0 made their first contribution in https://github.com/volcengine/OpenViking/pull/78
* @ze-mu-zhou made their first contribution in https://github.com/volcengine/OpenViking/pull/87
* @WuMingDao made their first contribution in https://github.com/volcengine/OpenViking/pull/88
* @Lettuceleaves made their first contribution in https://github.com/volcengine/OpenViking/pull/95
* @mildred522 made their first contribution in https://github.com/volcengine/OpenViking/pull/93

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.11...v0.1.12


## v0.1.11 (2026-02-05)

## What's Changed
* support small github code repos by @MaojiaSheng in https://github.com/volcengine/OpenViking/pull/70

## New Contributors
* @MaojiaSheng made their first contribution in https://github.com/volcengine/OpenViking/pull/70

**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.10...v0.1.11


## v0.1.10 (2026-02-05)

## What's Changed
* Fix compile by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/62
* fix: fix windows release by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/64


**Full Changelog**: https://github.com/volcengine/OpenViking/compare/v0.1.9...v0.1.10


## v0.1.9 (2026-02-05)

## What's Changed
* Bump github/codeql-action from 3 to 4 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/5
* Bump actions/setup-go from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/4
* Bump actions/setup-python from 5 to 6 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/3
* Bump astral-sh/setup-uv from 4 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/2
* Bump actions/download-artifact from 4 to 7 by @dependabot[bot] in https://github.com/volcengine/OpenViking/pull/1
* fix session test by @qin-ctx in https://github.com/volcengine/OpenViking/pull/7
* feat: add GitHub issue and PR templates by @qin-ctx in https://github.com/volcengine/OpenViking/pull/8
* fix: fix_build by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/6
* docs: update readme by @qin-ctx in https://github.com/volcengine/OpenViking/pull/10
* fix: Downgraded a log message from warning to info when the Rerank cl… by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/11
* docs: update_readme by @qin-ctx in https://github.com/volcengine/OpenViking/pull/12
* Remove agfs by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/13
* docs: faq by @qin-ctx in https://github.com/volcengine/OpenViking/pull/14
* fix: fix agfs-server for windows by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/16
* Upgrade go by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/17
* feat: add more visual example `query` by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/19
* fix: support intel mac install by @qin-ctx in https://github.com/volcengine/OpenViking/pull/22
* feat: rename backend to provider for embedding and vlm by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/24
* Lint code by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/25
* refactor: optimized pyproject.toml with optional groups by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/26
* feat: linux compile by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/29
* Change 'provider' to 'backend' in configuration by @coldfire-x in https://github.com/volcengine/OpenViking/pull/27
* update version by @qin-ctx in https://github.com/volcengine/OpenViking/pull/30
* Revert "Change 'provider' to 'backend' in configuration" by @qin-ctx in https://github.com/volcengine/OpenViking/pull/31
* chore: use standard logging pkg by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/40
* feat: chat & chat w/ mem examples by @ZaynJarvis in https://github.com/volcengine/OpenViking/pull/39
* feat:常规环境下，增加python3.13适配 by @Jay-Chou118 in https://github.com/volcengine/OpenViking/pull/45
* docs: add server cli design by @qin-ctx in https://github.com/volcengine/OpenViking/pull/46
* refactor: refactor ci action by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/49
* refactor: extract Service layer from async_client by @qin-ctx in https://github.com/volcengine/OpenViking/pull/50
* fix: fix ci action by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/51
* fix: simplify memory dedup decisions and fix retrieval recursion bug by @qin-ctx in https://github.com/volcengine/OpenViking/pull/53
* Fix ci action by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/57
* fix: 修复s3fs适配 by @chuanbao666 in https://github.com/volcengine/OpenViking/pull/52
* refactor: extract ObserverService from DebugService for cleaner status access API by @qin-ctx in https://github.com/volcengine/OpenViking/pull/59
* fix: fix release action by @kkkwjx07 in https://github.com/volcengine/OpenViking/pull/61

## New Contributors
* @dependabot[bot] made their first contribution in https://github.com/volcengine/OpenViking/pull/5
* @qin-ctx made their first contribution in https://github.com/volcengine/OpenViking/pull/7
* @kkkwjx07 made their first contribution in https://github.com/volcengine/OpenViking/pull/6
* @ZaynJarvis made their first contribution in https://github.com/volcengine/OpenViking/pull/19
* @coldfire-x made their first contribution in https://github.com/volcengine/OpenViking/pull/27
* @Jay-Chou118 made their first contribution in https://github.com/volcengine/OpenViking/pull/45
* @chuanbao666 made their first contribution in https://github.com/volcengine/OpenViking/pull/52

**Full Changelog**: https://github.com/volcengine/OpenViking/commits/v0.1.9


