# OpenClaw 插件机制深度分析

> 分析日期：2026-03-03
> 基于 OpenClaw 最新代码库

---

## 目录

1. [插件机制概述](#1-插件机制概述)
2. [插件分类体系](#2-插件分类体系)
3. [注册与加载机制](#3-注册与加载机制)
4. [架构设计详解](#4-架构设计详解)
5. [内置插件清单](#5-内置插件清单)
6. [关于动态修改 SKILL.md 的分析](#6-关于动态修改-skillmd-的分析)

---

## 1. 插件机制概述

OpenClaw 采用**分层、可扩展的插件架构**，支持三种类型的插件：

- **内置插件 (Bundled)** - 随 OpenClaw 一起发布
- **托管插件 (Managed)** - 通过 ClawHub 安装管理
- **工作空间插件 (Workspace)** - 项目特定的本地插件

插件发现层级遵循以下优先级（从高到低）：

```
Config paths → Workspace → Global → Bundled
```

---

## 2. 插件分类体系

OpenClaw 支持以下扩展类型：

| 扩展类型 | 说明 | 示例 |
|---------|------|------|
| **Channel 插件** | 消息通道集成 | Matrix, Zalo, WhatsApp |
| **Tool 插件** | 工具扩展 | 文件操作、网络请求 |
| **Gateway RPC** | RPC 接口扩展 | 自定义 API 端点 |
| **HTTP Handlers** | HTTP 请求处理器 | Webhook 处理 |
| **CLI Commands** | 命令行命令 | 自定义 CLI 指令 |
| **Services** | 后台服务 | 定时任务、监听器 |
| **Hooks** | 事件钩子 | 生命周期钩子 |
| **Provider Auth** | 认证提供者 | OAuth、API Key 管理 |

---

## 3. 注册与加载机制

### 3.1 插件加载器

OpenClaw 使用 **jiti** 作为插件加载器，支持**运行时直接执行 TypeScript**，无需预编译：

```typescript
// 核心加载函数来自 pi-coding-agent 包
import { loadSkillsFromDir } from '@mariozechner/pi-coding-agent';

// 加载技能目录
const skills = await loadSkillsFromDir(skillDir);
```

### 3.2 注册 API

插件通过以下 API 注册到系统：

```typescript
// 注册通道
api.registerChannel(config: ChannelConfig);

// 注册工具
api.registerTool(name: string, handler: ToolHandler);

// 注册 Gateway 方法
api.registerGatewayMethod(method: string, handler: Function);
```

### 3.3 文件监听与热重载

OpenClaw 使用 `chokidar` 监听文件变化，实现热重载：

```typescript
// 来自 src/agents/skills/refresh.ts
const watcher = chokidar.watch(watchTargets, {
  ignoreInitial: true,
  awaitWriteFinish: {
    stabilityThreshold: debounceMs,
    pollInterval: 100,
  },
  ignored: DEFAULT_SKILLS_WATCH_IGNORED,
});
```

---

## 4. 架构设计详解

### 4.1 基于 Hook 的事件驱动架构

OpenClaw 的核心是事件驱动的 Hook 系统，主要事件包括：

| 事件 | 触发时机 |
|------|---------|
| `message:inbound` | 消息流入系统 |
| `message:outbound` | 消息流出系统 |
| `agent:start` | Agent 开始工作 |
| `agent:complete` | Agent 完成工作 |
| `config:reload` | 配置重新加载 |
| `before_prompt_build` | 构建 prompt 之前 |
| `llm_input` | LLM 输入前 |
| `llm_output` | LLM 输出后 |

### 4.2 插件 SDK 能力

插件 SDK 提供以下核心能力：

```typescript
interface PluginSDK {
  // 后台服务
  Background: {
    start(service: BackgroundService): void;
    stop(serviceId: string): void;
  };

  // 生命周期钩子
  Lifecycle: {
    on(event: LifecycleEvent, handler: Function): void;
  };

  // 配置管理
  Config: {
    get<T>(key: string): T;
    set<T>(key: string, value: T): void;
  };

  // 日志
  Logger: {
    info(msg: string): void;
    error(msg: string): void;
    debug(msg: string): void;
  };
}
```

---

## 5. 内置插件清单

### 5.1 内置通道 (Bundled Channels)

| 通道 | 说明 |
|------|------|
| WhatsApp | WhatsApp 商业 API |
| Telegram | Telegram Bot |
| Slack | Slack App |
| Discord | Discord Bot |
| Signal | Signal 集成 |
| iMessage | Apple iMessage |
| Google Chat | Google Workspace Chat |

### 5.2 扩展插件 (位于 `/extensions/`)

| 插件 | 说明 |
|------|------|
| Matrix | 去中心化聊天协议 |
| Microsoft Teams | 微软团队协作 |
| Zalo (User/Business) | 越南社交应用 |
| Nostr | 去中心化社交网络 |
| LINE | 日本即时通讯 |
| Mattermost | 开源团队协作 |
| Nextcloud Talk | 私有云通话 |

---

## 6. 关于动态修改 SKILL.md 的分析

### 6.1 核心结论

**OpenClaw 目前无法直接在 skill 加载时修改 SKILL.md 内容。**

原因：
1. **无生命周期钩子** - Skill 系统没有提供 `onLoad`、`beforeLoad` 等钩子
2. **静态声明式架构** - Skills 通过 `SKILL.md` 静态定义，使用 `pi-coding-agent` 包加载，没有预留修改入口
3. **只读解析** - Frontmatter 解析器只读取不写入
4. **加载后只读** - Skill 加载后被用于构建 system prompt，本身不会被修改

### 6.2 可行替代方案

#### 方案 1: 外部预处理脚本（推荐）

在启动 OpenClaw 之前，运行脚本修改 SKILL.md：

```bash
#!/bin/bash
# preprocess-skills.sh
node scripts/modify-skills.js
openclaw start  # 启动 OpenClaw
```

```javascript
// scripts/modify-skills.js
const fs = require('fs');
const path = require('path');
const yaml = require('yaml');

const skillPath = process.env.SKILL_PATH || './skills/my-skill/SKILL.md';
const content = fs.readFileSync(skillPath, 'utf8');

// 解析 frontmatter
const match = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
if (match) {
  const frontmatter = yaml.parse(match[1]);
  const body = match[2];

  // 动态修改内容
  frontmatter.lastModified = new Date().toISOString();
  frontmatter.dynamicValue = calculateDynamicValue();

  // 写回文件
  const newContent = `---\n${yaml.stringify(frontmatter)}---\n${body}`;
  fs.writeFileSync(skillPath, newContent);
}
```

#### 方案 2: 使用 OpenClaw Hooks 系统

利用 `before_prompt_build` hook 在构建 prompt 时动态修改 skill 内容：

```typescript
// 在你的插件中
import { definePlugin } from 'openclaw';

export default definePlugin({
  name: 'dynamic-skill-modifier',

  hooks: {
    // 在构建 prompt 之前修改 skill 内容
    before_prompt_build: async ({ skills, context }) => {
      // 动态修改 skill 对象（不修改文件，只修改内存中的表示）
      for (const skill of skills) {
        if (skill.name === 'my-dynamic-skill') {
          skill.content = modifySkillContent(skill.content, context);
        }
      }
      return { skills };
    }
  }
});
```

#### 方案 3: 自定义 Skill 加载器（高级）

创建一个自定义的 skill 加载插件，拦截加载过程：

```typescript
// plugins/dynamic-skill-loader.ts
import { loadSkillsFromDir } from 'pi-coding-agent';
import * as fs from 'fs';
import * as path from 'path';

export class DynamicSkillLoader {
  async loadSkills(skillDir: string) {
    // 1. 复制 skill 到临时目录
    const tempDir = this.createTempCopy(skillDir);

    // 2. 修改临时目录中的 SKILL.md
    this.modifySkillMdFiles(tempDir);

    // 3. 从临时目录加载
    return loadSkillsFromDir(tempDir);
  }

  private modifySkillMdFiles(dir: string) {
    const skillMdFiles = this.findSkillMdFiles(dir);
    for (const file of skillMdFiles) {
      let content = fs.readFileSync(file, 'utf8');

      // 动态修改内容
      content = this.applyDynamicModifications(content);

      fs.writeFileSync(file, content);
    }
  }

  private applyDynamicModifications(content: string): string {
    // 添加动态生成的内容
    const dynamicSection = `\n\n<!-- 动态生成于 ${new Date().toISOString()} -->\n`;
    return content + dynamicSection;
  }
}
```

#### 方案 4: 文件监听 + 触发重载（最符合 OpenClaw 设计）

利用 OpenClaw 已有的文件监听机制，在修改 SKILL.md 后自动重载：

```typescript
// 在你的构建脚本中
import * as chokidar from 'chokidar';
import * as fs from 'fs';

// 监听原始 skill 定义文件
const watcher = chokidar.watch('./skill-sources/**/*.md');

watcher.on('change', (filepath) => {
  console.log(`Source changed: ${filepath}`);

  // 重新生成 SKILL.md
  generateSkillMd(filepath);
});

function generateSkillMd(sourcePath: string) {
  const source = fs.readFileSync(sourcePath, 'utf8');

  // 动态生成 frontmatter
  const frontmatter = {
    name: extractName(source),
    version: calculateVersion(),
    lastBuild: new Date().toISOString(),
    dynamicConfig: loadDynamicConfig()
  };

  // 写入 SKILL.md（触发 OpenClaw 重载）
  const skillMd = `---\n${yaml.stringify(frontmatter)}---\n${extractBody(source)}`;
  fs.writeFileSync('./skills/my-skill/SKILL.md', skillMd);
}
```

### 6.3 方案对比

| 方案 | 复杂度 | 侵入性 | 适用场景 | 推荐度 |
|------|--------|--------|----------|--------|
| 预处理脚本 | 低 | 低 | 启动前一次性修改 | ★★★★★ |
| Hooks 系统 | 中 | 中 | 运行时动态修改内存中的 skill | ★★★★ |
| 自定义加载器 | 高 | 高 | 需要完全控制加载过程 | ★★★ |
| 文件监听重载 | 中 | 低 | 需要持续同步外部变更 | ★★★★ |

### 6.4 核心结论

**OpenClaw 的 Skill 系统是静态声明式的**，设计理念是：
- Skill 定义（SKILL.md）是**只读的声明**
- 动态行为通过 **Hooks** 和 **插件** 实现
- 文件变化通过 **监听 + 重载** 机制处理

因此，如果需要"在 skill 加载时修改 SKILL.md"，应该：
1. **在加载前** 通过预处理脚本修改（方案 1）
2. **在加载后** 通过 Hooks 修改内存中的表示（方案 2）
3. **避免** 尝试在加载过程中 hack 内部机制

这种设计与 OpenClaw 的整体架构哲学一致：**声明式配置 + 程序化扩展**。
