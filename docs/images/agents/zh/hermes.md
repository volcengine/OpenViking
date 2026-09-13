## 步骤1：安装

1. 在终端执行如下命令，启动 OpenViking 记忆配置向导：

   ```bash
   hermes memory setup openviking
   ```

2. 执行后将出现配置来源选择界面：

   ```text
   OpenViking config source
     ↑↓ navigate  ENTER/SPACE select  ESC cancel
    → (●) Use existing OpenViking profile - choose from detected ovcli.conf profiles
      (○) Create new OpenViking profile - enter a new URL/API key
   ```

   选项说明：

   - 复用现有 Profile：直接读取本地已有的 `ovcli.conf` 中的 OpenViking 地址和密钥，无需重复填写。
   - 新建 Profile：需手动输入 OpenViking 服务的访问 URL 和 API 密钥，适合首次配置或连接新实例的场景。

3. 若选择「Create new OpenViking profile」，将出现连接方式选择，请选择「OpenViking Service (VolcEngine Cloud)」：

   ```text
   OpenViking connection
     ↑↓ navigate  ENTER/SPACE select  ESC cancel

    → (●) OpenViking Service (VolcEngine Cloud) - use the managed OpenViking endpoint
      (○) Custom - use a local, VPS, or self-hosted OpenViking server
   ```

4. 填入 API KEY：

   ```text
   {{OPENVIKING_API_KEY}}
   ```

5. 填写「Hermes peer ID in OpenViking」：该字段为 Hermes 在 OpenViking 中的 Agent 身份标识，用于区分不同 Agent 产生的记忆。可直接按 Enter 使用默认值「hermes」，也可自定义填写。
6. 选择配置保存方式，建议选择「Mirror to OpenViking store」：

   ```text
   Save OpenViking config
     ↑↓ navigate  ENTER/SPACE select  ESC cancel
      (○) Keep in Hermes only - write values only to Hermes .env
    → (●) Mirror to OpenViking store - write ~/.openviking/ovcli.conf.<name> and link it
   ```

7. 填写「OpenViking profile name」：Hermes 的多租户能力可隔离不同 Profile 的模型、记忆、配置及凭据。建议为每个 Hermes Profile 配置独立的 OpenViking 环境或身份，并在此填写一个便于识别的本地配置名称，以区分对应的 OpenViking 配置。该名称仅用于本地标识，不会创建新用户，也不会改变账号身份或权限。
8. 配置完成后将显示如下确认信息：

   ```text
   OpenViking memory is ready
     Created and linked OpenViking profile.
     Config file: ~/.openviking/ovcli.conf.hermes
     Start a new Hermes session to activate.
   ```

## 步骤2：验证

1. 执行以下命令验证记忆插件状态：

   ```bash
   hermes memory status
   ```

2. 返回如下结果即表示接入成功：

   ```text
   Memory status
   ────────────────────────────────────────
     Built-in (MEMORY.md / USER.md):
       Memory injection:   enabled ✓
       User profile:       enabled ✓
       Memory tool:        enabled ✓
     Provider:  openviking

     openviking config:
       use_ovcli_config: True
       ovcli_config_path: ~/.openviking/ovcli.conf.hermes
       endpoint: `https://api.vikingdb.cn-beijing.volces.com/openviking`
       agent: hermes

     Plugin:    installed ✓
     Status:    available ✓

     Installed plugins:
       • byterover  (API key / local)
       • hindsight  (API key / local)
       • holographic  (local)
       • honcho  (API key / local)
       • mem0  (API key / local)
       • openviking  (API key / local) ← active
       • retaindb  (API key / local)
       • supermemory  (requires API key)
   ```

## 故障排查

| 问题 | 处理 |
|---|---|
| Provider 不是 openviking | 重跑 `hermes memory setup openviking` |
| Status 不是 available | 检查 API Key |

## 参考

- 手动配置文档：[Hermes](https://docs.openviking.net/zh/agent-integrations/05-hermes)
- 原理说明：[OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
