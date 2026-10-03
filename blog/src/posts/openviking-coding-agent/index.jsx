import React from 'react';
import { Article, Lead, P, H2, Pre, Table, A } from '../../blog-components';
const Post = ({
  t
}) => <Article>
    <Lead>{t({
      "zh": "换一个对话窗口，Agent 通常还能读取仓库，却未必知道上次为什么放弃某个方案。OpenViking 插件把这类决策和经验保存到服务端，再按任务找回来。它的价值，要看下一次工作能否接着做。",
      "en": "A new conversation can read the repository without knowing why the previous session rejected a design. The OpenViking plugin stores decisions and experience on a server and retrieves them for later tasks. Its value is whether the next session can pick up the work."
    })}</Lead>
    <H2 id="context">{t({
      "zh": "先分清三种上下文",
      "en": "Three kinds of context"
    })}</H2>
    <P>{t({
      "zh": "设想一次修复：团队决定暂时保留旧接口，因为一个下游服务还没迁移。代码记录了实现，AGENTS.md 规定测试命令，对话解释了这个临时决定。下次改同一处代码时，三者都可能有用，但用途不同。",
      "en": "Consider a hypothetical fix: the team keeps an old interface because a downstream service has not migrated. Code records the implementation, AGENTS.md defines test commands, and the conversation explains the temporary decision. A later change may need all three, for different reasons."
    })}</P>
    <Table headers={[{
    "zh": "上下文",
    "en": "Context"
  }, {
    "zh": "适合保存",
    "en": "What belongs here"
  }, {
    "zh": "使用时检查",
    "en": "What to check"
  }].map(t)} rows={[[{
    "zh": "项目指令",
    "en": "Project instructions"
  }, {
    "zh": "测试命令、代码规范、必须遵守的约束",
    "en": "Test commands, code conventions, required constraints"
  }, {
    "zh": "当前仓库中的规则是否仍适用",
    "en": "Whether the current repository rules apply"
  }], [{
    "zh": "会话记录与摘要",
    "en": "Session records and summaries"
  }, {
    "zh": "这次任务做了什么、还剩什么",
    "en": "What happened in this task and what remains"
  }, {
    "zh": "是否已捕获、归档，是否遗漏最后几轮",
    "en": "Whether capture and archival include the last turns"
  }], [{
    "zh": "长期记忆",
    "en": "Long-term memory"
  }, {
    "zh": "可复用的偏好、决策、事件和经验",
    "en": "Reusable preferences, decisions, events, and experience"
  }, {
    "zh": "来源、适用范围，以及是否已被新决定替代",
    "en": "Source, scope, and whether a newer decision supersedes it"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "本地指令文件仍然值得维护。插件补充的是分散在历史交互里的信息；召回的记忆是待核对的背景，不能覆盖用户这次的要求，也不能替代当前代码。",
      "en": "Local instruction files remain useful. The plugin adds information scattered across past interactions. Recalled memory is background to verify; it cannot override the current request or substitute for current code."
    })}</P>
    <H2 id="quickstart">{t({
      "zh": "接入之前，把服务和身份配好",
      "en": "Connect a server and an identity"
    })}</H2>
    <P>{t({
      "zh": "需要一套可访问的 OpenViking Server，以及与插件兼容的版本。服务端负责模型配置、存储和记忆提取；插件安装只解决客户端接入。当前插件使用 viking://~ 家目录别名，旧服务端需要先检查兼容性。",
      "en": "You need a reachable OpenViking Server and compatible server and plugin versions. The server supplies model configuration, storage, and memory extraction; installing a plugin connects the client. Current plugins use the viking://~ home alias, so check compatibility with older servers."
    })}</P>
    <P>{t({
      "zh": "在 macOS 或 Linux 上，统一安装器可以分别配置 Claude Code 和 Codex。安装器会检查依赖、询问服务地址和 API Key，并在修改配置前展示变更。按需要选择对应命令。",
      "en": "On macOS or Linux, the shared installer can configure Claude Code or Codex. It checks dependencies, asks for the server address and API key, and shows configuration changes before applying them. Choose the command for your client."
    })}</P>
    <Pre lang="bash" filename="install-claude.sh">{"curl -fsSL https://openviking.ai/install | bash -s -- --harness claude"}</Pre>
    <Pre lang="bash" filename="install-codex.sh">{"curl -fsSL https://openviking.ai/install | bash -s -- --harness codex"}</Pre>
    <P>{t({
      "zh": "GitHub 访问受限时，可从火山引擎 TOS 镜像运行安装器，再按提示选择客户端和下载源。",
      "en": "If GitHub is hard to reach, run the installer from the Volcengine TOS mirror, then choose the client and download source when prompted."
    })}</P>
    <Pre lang="bash" filename="install-from-mirror.sh">{"bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)"}</Pre>
    <P>{t({
      "zh": "使用 API Key 认证的服务时，客户端应持有绑定用户身份的 User/Admin key。Root key 用于管理。两个客户端要复用同一人的记忆，必须连到同一服务和同一有效用户；相同 URL 本身不代表相同身份。",
      "en": "For an API-key-authenticated server, use a User/Admin key bound to the intended user. Root keys are for administration. Two clients sharing one person’s memory must use the same server and effective user; matching URLs alone do not establish that identity."
    })}</P>
    <P>{t({
      "zh": "安装后重新启动客户端。Codex 还需要启用插件并信任对应 hooks；只看到 MCP 工具可用，不代表自动召回和捕获已经启用。不要叠加安装器和 marketplace 两套安装，以免重复执行 hooks。",
      "en": "Restart the client after installation. In Codex, enable the plugin and trust its hooks. Working MCP tools do not prove that automatic recall and capture are enabled. Choose either the installer or marketplace setup to avoid duplicate hooks."
    })}</P>
    <H2 id="lifecycle">{t({
      "zh": "一次对话怎样变成可复用记忆",
      "en": "From conversation to reusable memory"
    })}</H2>
    <Table headers={[{
    "zh": "时机",
    "en": "When"
  }, {
    "zh": "动作与完成条件",
    "en": "Action and completion boundary"
  }].map(t)} rows={[[{
    "zh": "SessionStart",
    "en": "SessionStart"
  }, {
    "zh": "加载画像、记忆索引和技能清单；恢复场景可补充归档摘要。注入受预算限制，未覆盖全部历史。",
    "en": "Load the profile, memory index, and skill catalog; resume can include an archive summary. Injection has a budget and does not contain all history."
  }], [{
    "zh": "UserPromptSubmit",
    "en": "UserPromptSubmit"
  }, {
    "zh": "开启自动召回时，按当前输入检索并注入背景。网络、检索和可选压缩会增加等待时间。",
    "en": "If auto-recall is enabled, retrieve context for the current input. Network, retrieval, and optional compression add latency."
  }], [{
    "zh": "Stop",
    "en": "Stop"
  }, {
    "zh": "增量捕获本轮记录；达到提交阈值时触发 commit。捕获成功与记忆提取完成是两个状态。",
    "en": "Capture new turns; commit when the configured threshold is reached. Successful capture and completed extraction are separate states."
  }], [{
    "zh": "PreCompact",
    "en": "PreCompact"
  }, {
    "zh": "在宿主压缩上下文前提交待处理记录。可能等待提交，也可能超时或失败。",
    "en": "Commit pending records before the host compacts context. Submission can take time, time out, or fail."
  }], [{
    "zh": "SessionEnd",
    "en": "SessionEnd"
  }, {
    "zh": "处理结束时的提交；Codex 依赖宿主版本和正常退出事件。进程被强杀不能保证触发 hook。",
    "en": "Submit at session end; Codex depends on host version and graceful shutdown. A killed process may never fire the hook."
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "服务端 commit 先归档消息，再异步生成摘要和提取长期记忆。返回 task_id 只表示后续任务可跟踪。任务完成后，归档目录中的 memory_diff.json 才能告诉你实际新增、修改或删除了哪些记忆。一次提交也可能没有值得新增的内容。",
      "en": "The server archives messages before asynchronously generating summaries and extracting memory. A returned task_id makes that work trackable. After completion, memory_diff.json in the archive records actual additions, updates, and deletions. A commit may produce no new memory."
    })}</P>
    <P>{t({
      "zh": "记忆提取会参考已有内容，按启用的类型和策略更新文件。用户画像、偏好、实体和事件服务于后续对话；经验提炼还受 Agent Evolution 等配置控制。类型清单不是“每轮必产出”的承诺。",
      "en": "Extraction consults existing content and updates files according to enabled types and policy. Profiles, preferences, entities, and events support later conversations; experience extraction also depends on Agent Evolution settings. A list of memory types is not a promise to produce each type on every turn."
    })}</P>
    <H2 id="host-differences">{t({
      "zh": "Claude Code 与 Codex 的差别在哪里",
      "en": "Where the hosts differ"
    })}</H2>
    <P>{t({
      "zh": "当前两套插件都通过本地 stdio MCP 代理连接服务端 /mcp，连接配置由环境变量或 ovcli.conf 解析。宿主生命周期不同，写回时机也不同，不宜用固定 hook 数量判断能力。",
      "en": "Both current plugins use a local stdio MCP proxy to reach the server’s /mcp endpoint, resolving connection settings from environment variables or ovcli.conf. Host lifecycles differ, so capture behavior matters more than a fixed hook count."
    })}</P>
    <Table headers={[{
    "zh": "行为",
    "en": "Behavior"
  }, {
    "zh": "Claude Code",
    "en": "Claude Code"
  }, {
    "zh": "Codex",
    "en": "Codex"
  }].map(t)} rows={[[{
    "zh": "提交",
    "en": "Commit"
  }, {
    "zh": "Stop、SessionEnd 等写入路径可用后台 worker；PreCompact 同步提交",
    "en": "Write paths such as Stop and SessionEnd can use a background worker; PreCompact submits synchronously"
  }, {
    "zh": "Stop 增量捕获；PreCompact 提交；支持 SessionEnd 的版本在正常关闭线程时提交",
    "en": "Stop captures incrementally; PreCompact commits; supported versions commit on graceful SessionEnd"
  }], [{
    "zh": "退出兜底",
    "en": "Exit fallback"
  }, {
    "zh": "检查写回日志和待重试记录",
    "en": "Inspect writeback logs and pending retries"
  }, {
    "zh": "启动或 clear 时检查结束标记和闲置状态；resume 不执行同样的清扫",
    "en": "Startup or clear checks end markers and idle state; resume does not perform that sweep"
  }], [{
    "zh": "子 Agent",
    "en": "Subagents"
  }, {
    "zh": "有 SubagentStart/Stop 路径，使用独立会话记录",
    "en": "SubagentStart/Stop paths use separate session records"
  }, {
    "zh": "不要把 Claude Code 的子 Agent 行为套用到 Codex",
    "en": "Do not assume Claude Code subagent behavior applies"
  }]].map(row => row.map(t))} />
    <P>{t({
      "zh": "后台写入减少了阻塞，但没有消除服务延迟和失败。压缩前提交、每轮召回、模型压缩仍可能处在用户等待的路径上。独立 session 也只是记录边界，不等于独立的用户权限边界。",
      "en": "Background writes reduce blocking without removing latency or failure. Pre-compaction submission, per-prompt recall, and model compression can still affect wait time. Separate sessions separate records; they do not create separate user authorization boundaries."
    })}</P>
    <H2 id="sharing">{t({
      "zh": "跨项目共享，要先确定范围",
      "en": "Set the scope before sharing"
    })}</H2>
    <P>{t({
      "zh": "默认工作区 peer 根据 Git origin 派生。同一个仓库的 clone、worktree 和子目录可以对应同一项目记忆；fork 的 origin 不同，默认 peer 也不同。没有仓库或显式工作区配置时，记忆进入用户级空间。",
      "en": "By default, the workspace peer derives from Git origin. Clones, worktrees, and subdirectories of the same repository can share project memory; a fork with another origin gets another peer. Outside a repository or explicitly configured workspace, memory goes to the user-level space."
    })}</P>
    <P>{t({
      "zh": "下面是非 Git 目录的配置示例。它给目录指定项目身份，并把召回范围设为当前 peer 加用户级记忆。peer 仍在 user 边界内；服务多个独立用户时，应先建立用户身份隔离。",
      "en": "This example assigns a project identity to a non-Git directory and scopes recall to that peer plus user-level memory. A peer remains inside a user boundary; a service for separate people needs separate user identities first."
    })}</P>
    <Pre lang="json" filename=".openviking/config.json">{"{\n  \"version\": 1,\n  \"peer\": { \"id\": \"checkout-service\" },\n  \"recall\": { \"peer_scope\": \"actor\" }\n}"}</Pre>
    <P>{t({
      "zh": "Claude Code、Codex 或其他 MCP 客户端能复用服务端上下文，但宿主的进程状态、未上传记录和工具配置不会一起迁移。周报可以从跨会话记录中整理出来，仍要核对时间、任务状态和遗漏。",
      "en": "Claude Code, Codex, and other MCP clients can reuse server-side context. Host process state, uncaptured records, and tool configuration do not migrate with it. A weekly report can draw on sessions, but dates, task status, and omissions still need checking."
    })}</P>
    <H2 id="retrieval">{t({
      "zh": "记忆命中后，读什么、信什么",
      "en": "Read the evidence behind a match"
    })}</H2>
    <P>{t({
      "zh": "search 或 find 返回 URI、摘要和相关性分数。分数用于排序，不是事实正确的概率。一个 0.60 的结果，不能解释成“这条记忆有 60% 可信度”。遇到架构决策或代码约束，应继续 read 对应内容，再核对当前实现。",
      "en": "Search and find return URIs, summaries, and relevance scores. Those scores rank matches; they are not probabilities that a statement is true. A 0.60 result does not mean “60% trustworthy.” Read the underlying content and check current implementation before using an architectural decision or code constraint."
    })}</P>
    <P>{t({
      "zh": "显式工具补充自动 hooks：search/find 找线索，read 取正文，list、grep、glob 帮助定位，remember 记录需要保留的信息，add_resource 导入资料，forget 删除指定记忆。具体可用工具以服务端暴露的 schema 为准。",
      "en": "Explicit tools complement hooks: search/find locate leads, read fetches content, list/grep/glob help navigation, remember records information, add_resource imports material, and forget deletes specified memories. The server’s exposed schema defines the available tools."
    })}</P>
    <P>{t({
      "zh": "自动召回可以关闭。若每轮等待超过节省的查找时间，可先保留捕获和按需查询，再用实际任务比较召回开关前后的耗时、命中和错误引用。L0/L1 帮助筛选内容，但摘要同样可能遗漏或过时。",
      "en": "Auto-recall can be disabled. If waiting on each prompt costs more than the lookup it saves, keep capture and on-demand search, then compare latency, useful matches, and incorrect citations on real tasks. L0/L1 help select content, but summaries can omit details or become stale."
    })}</P>
    <Pre lang="bash" filename="shell-environment.sh">{"export OPENVIKING_AUTO_RECALL=false\nexport OPENVIKING_DEBUG=true"}</Pre>
    <H2 id="safety">{t({
      "zh": "凭证和写回都需要检查",
      "en": "Check credentials and writeback"
    })}</H2>
    <P>{t({
      "zh": "安装器可以把 API Key 写进本地 ovcli.conf。代理避免在项目 .mcp.json 中重复写入密钥，不代表凭证绝不落盘。保护本地配置和运行环境，避免把它们提交到仓库或贴到日志里。",
      "en": "The installer can write an API key to local ovcli.conf. The proxy avoids duplicating secrets in project .mcp.json; it does not promise that credentials never reach disk. Protect local configuration and the runtime environment, and keep them out of repositories and shared logs."
    })}</P>
    <P>{t({
      "zh": "捕获前会清理注入的上下文标签，减少记忆被重复回灌。这不能保证模型回复中的错误不会被提取。发现错误记忆时，应修正或删除它，并追查来源。OPENVIKING_BYPASS_SESSION 用于跳过 hooks；它不会自动撤销模型通过 MCP 主动读写的权限。",
      "en": "Capture strips injected context tags to reduce feedback loops. That does not guarantee that mistakes in an assistant reply will never be extracted. Correct or delete bad memory and inspect its source. OPENVIKING_BYPASS_SESSION bypasses hooks; it does not automatically revoke explicit MCP read/write access."
    })}</P>
    <H2 id="verify">{t({
      "zh": "用一次跨会话任务验收",
      "en": "Verify one cross-session task"
    })}</H2>
    <P>{t({
      "zh": "选一个无敏感信息的测试项目，记录一条之后可核对的决定及原因。正常结束会话，检查服务端任务和 memory_diff.json；然后在新会话中询问这项决定，要求 Agent 给出记忆 URI，并读取来源。最后换到另一个客户端，用相同身份和项目范围重复读取。",
      "en": "Choose a test project without sensitive data and record a decision with its reason. End the session normally, inspect the server task and memory_diff.json, then ask about the decision in a new session. Request the memory URI and read its source. Finally, repeat the lookup from another client using the same identity and project scope."
    })}</P>
    <P>{t({
      "zh": "验收要区分三件事：内容确实写入、检索能找到、Agent 用对了。再补一次更正，检查后续回答是否采用新决定。只看到“我记住了”或安装成功，都不足以证明长期记忆生效。",
      "en": "Verify three separate outcomes: the content was stored, retrieval finds it, and the agent uses it correctly. Then make a correction and check that later answers use the updated decision. An installation success or “I’ll remember that” proves none of these."
    })}</P>
    <P><A href="https://github.com/volcengine/OpenViking/blob/main/examples/claude-code-memory-plugin/README.md">{t({
        "zh": "Claude Code 插件：安装与生命周期",
        "en": "Claude Code plugin: installation and lifecycle"
      })}</A></P>
    <P><A href="https://github.com/volcengine/OpenViking/blob/main/examples/codex-memory-plugin/README.md">{t({
        "zh": "Codex 插件：宿主要求与配置",
        "en": "Codex plugin: host requirements and configuration"
      })}</A></P>
    <P><A href="https://docs.openviking.ai/en/concepts/08-session">{t({
        "zh": "会话提交与记忆变更记录",
        "en": "Session commits and memory change records"
      })}</A></P>
  </Article>;
export default {
  id: "openviking-coding-agent",
  Component: Post,
  meta: {
    "cover": "/assets/covers/openviking-coding-agent.png",
    "publishedAt": "2026-05-20",
    "readingTime": {
      "en": 6,
      "zh": 5
    },
    "category": {
      "zh": "工程",
      "en": "Engineering"
    },
    "tags": ["openviking", "claude-code", "codex", "mcp", "memory"],
    "authors": [{
      "name": "tosaki",
      "github": "t0saki",
      "role": {
        "zh": "工程师",
        "en": "Engineer"
      }
    }],
    "title": {
      "zh": "在 Claude Code / Codex 中接入 OpenViking：让经验跨会话复用",
      "en": "OpenViking for Claude Code and Codex: Reusing Experience Across Sessions"
    },
    "description": {
      "zh": "从插件安装、捕获和提交，到跨项目召回与验收，说明长期记忆能保留什么、何时生效，以及如何核对。",
      "en": "Install the plugin, understand capture and commit, and verify that scoped memory survives a session and helps the next task."
    },
    "updatedAt": "2026-10-03",
    "languages": ["en", "zh"],
    "llmPath": "/post/openviking-coding-agent/llm.txt"
  }
};
