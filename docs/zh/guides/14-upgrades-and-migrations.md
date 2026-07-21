# 升级与迁移

本指南汇总了在实际部署中最常见的升级阻塞问题及其恢复步骤。如果你在拉取
新镜像后容器启动即退出，请先阅读本指南再提 issue。

## 何时阅读本指南

- 你正在升级一套已有的 OpenViking 部署，跨越次要版本。
- 升级后服务启动失败（容器退出，或健康检查长期不通过）。
- 容器日志中出现
  `ModuleNotFoundError: No module named 'openviking.console.bootstrap'`。
- 服务日志中出现 `EmbeddingRebuildRequiredError`。

## 升级前准备

升级前花几分钟做好准备，可以让本指南中后续每一步都是可恢复的。请在拉取
新镜像之前完成以下事项。

- **快照数据目录。** 即挂载到容器 `/app/.openviking` 的目录（在宿主机上
  通常是 `~/.openviking`）。检索相关的两个关键路径是 AGFS 根目录和
  `vectordb/`。停掉服务后用 `cp -a` 或 `tar` 整体打包即可，不需要专门的
  在线备份工具。
- **保存当前的 `ov.conf`。** Embedding 模型、Provider 与维度是版本之间
  最容易漂移、最容易导致启动失败的字段。把当前正常运行的配置文件留一份
  副本，万一升级失败可以快速回滚。
- **优雅停止服务。** 使用 `docker stop <container>`（或
  `docker compose down`）。避免 `docker kill -9` / `SIGKILL`：向量索引
  依赖正常关闭来释放 `vectordb/<collection>/store/LOCK` 下的锁，强制终止
  会留下陈旧的锁文件，阻塞下一次启动。

## 常见的破坏性升级场景

下面两类故障覆盖了 v0.3.15 之后 v0.3.x 系列升级报告里的大多数情况。它们
**可能同时存在** —— 服务可能先因第一个错误退出，修好之后才暴露出第二个
错误 —— 所以请先把两节都读完再动手。

### v0.3.15 → v0.3.19+ ：`openviking.console.bootstrap` 已移除

- **现象。** 容器启动后立即退出。日志中显示
  `ModuleNotFoundError: No module named 'openviking.console.bootstrap'`，
  通常出现在你 `command:` 覆盖里的
  `python -m openviking.console.bootstrap ...` 这一行。
- **原因。** Web Studio 之前是一个独立进程，由
  `python -m openviking.console.bootstrap` 启动。从 v0.3.19 起 Studio
  的资源已被打包进 `openviking-server`，独立的
  `openviking.console.bootstrap` 模块已不复存在（参见 PR #2320）。任何
  仍然启动它的自定义 `command:` 都会报 `ModuleNotFoundError`。
- **修复。** 在 `docker-compose.yml`（或你用来启动容器的任何方式）中，
  删除 `python -m openviking.console.bootstrap` 这一行。镜像默认的入口
  脚本已经会运行 `openviking-server`，它在 `1933` 端口同时提供 API 和
  Studio UI。
- **示例。**

  修改前 —— 两个进程，其中一个已被移除：

  ```yaml
  services:
    openviking:
      image: ghcr.io/volcengine/openviking:latest
      command: |
        openviking-server &
        python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020
  ```

  修改后 —— 单进程，使用默认入口：

  ```yaml
  services:
    openviking:
      image: ghcr.io/volcengine/openviking:latest
      # 不再需要 `command:` 覆盖 —— 镜像入口会运行 openviking-server，
      # 它现在也负责提供 Web Studio。
  ```

  如果你仍然希望显式声明 `command:`，写成
  `command: openviking-server` 并删掉 bootstrap 那一行即可。

### 任何版本出现 `EmbeddingRebuildRequiredError`

- **现象。** 服务日志中出现
  `EmbeddingRebuildRequiredError: Existing collection embedding dimension (...)
  does not match current configuration (...)` 或者
  `EmbeddingRebuildRequiredError: Existing collection embedding metadata does
  not match current configuration`。HTTP 服务还没起来就中止了。
- **原因。** 磁盘上的向量集合记录了构建它时所使用的 embedding provider、
  模型名和维度。当 `ov.conf` 中 embedding 段发生变化（更换 provider、
  更换模型，特别是更换向量维度）时，已有向量就无法再与新向量进行比较。
  服务为了避免新旧向量混用，宁可拒绝启动。
- **选一条路径。** 两条路径都会保留你的业务数据，区别只在于是否保留旧
  向量。

  **路径 A —— 保留数据，回滚 embedding 配置。** 把 `ov.conf` 的
  embedding 段改回与已有集合一致的取值（即"升级前准备"里你保存的那一
  份）。服务即可恢复启动。后续在维护窗口内通过路径 B 计划性地完成
  embedding 模型变更。如果新旧配置之间只是 provider 或模型名不同、
  **维度完全一致**，也可以在 `ov.conf` 中设置
  `embedding.allow_metadata_override = true`，这样会保留已有向量，仅
  改写记录的 metadata。

  **路径 B —— 在新配置下重建向量。** 这条路径会对所有 resource、
  memory 和 skill 重新计算 embedding。代价是一次完整的 embed 计算，
  对应的费用按你所配置的 embedding provider 计费。

  1. **备份 `vectordb/context/`。** 在数据目录（宿主机
     `~/.openviking`，容器内 `/app/.openviking`）下，把
     `data/vectordb/context/` 改名为 `data/vectordb/context.bak-<日期>/`
     或拷贝到别处。**先不要删除** —— 万一重建中途失败，这是你回退的
     依据。
  2. **只删除 `data/vectordb/context/`。** 不要删除 `data/` 下的其他任
     何目录。AGFS 树（resources、memories、skills、sessions）位于
     `vectordb/` 之外，正是我们要保住的部分。删除其他目录有可能毁掉你
     正打算重建向量的数据本身。
  3. **使用新的 `ov.conf` 启动服务。** 服务会创建一个全新的
     `vectordb/context/` 集合，与新的 embedding 配置匹配。此时服务应
     该能正常启动并通过 `/health`。
  4. **对各 namespace 重建索引。** 使用 CLI 对原本有向量的内容重新
     embed：

     ```bash
     ov reindex viking://resources --mode vectors_only --wait true
     ov reindex viking://user/memories --mode vectors_only --wait true
     ov reindex viking://agent/memories --mode vectors_only --wait true
     ov reindex viking://agent/skills --mode vectors_only --wait true
     ```

     只对你实际使用的 namespace 执行即可。`--mode vectors_only` 会复用
     已有的语义摘要（L0/L1），仅重新计算向量 —— 当变化只发生在
     embedding 配置时，这是正确选择。如果你的语义摘要配置也变了，请
     改用 `--mode semantic_and_vectors`，它会同时重做 L0/L1 摘要，会
     额外产生 VLM 调用费用。
  5. **验证检索可用。** 用你已知答案的查询，在代表性的 URI 下跑一次
     检索：

     ```bash
     ov find "<已知关键词>" --target-uri viking://resources/
     ```

     确认结果符合预期后，再删除 `context.bak-<日期>/` 备份。

## 升级成功后的健全性检查

切换生产流量之前，对升级后的容器跑一遍：

- `curl http://localhost:1933/health` 返回健康响应。
- `ov tree viking://resources -L 1` 列出预期中的资源 —— 验证 AGFS 树
  在升级中未受影响。
- `ov find <已知关键词>` 返回预期命中 —— 验证向量索引已经填充且可
  查询。
- Studio UI 在原来的端口可访问（直连默认 `1933`，经 Caddy 默认
  `1934`）。

## 如果以上都没解决

如果按上述步骤仍无法恢复，请提交 issue 时附上：

- 失败那一次启动的完整服务日志（从容器启动到第一段 stack trace 的全
  部内容）。
- 你的 `ov.conf`，去掉 API key 等敏感字段。
- 升级**之前**和**之后**的具体版本号（镜像 tag 即可）。
- 数据目录下 `ls data/vectordb/` 的输出。

请给 issue 打上 `upgrade` 标签，便于维护者分流。如果你正在跨越
0.3.x → 0.4.0 边界，也请同时阅读相关的迁移说明
[migration/01-user-peer-model.md](../migration/01-user-peer-model.md)。
