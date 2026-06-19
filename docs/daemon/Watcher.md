# Daemon Watcher 排查计划

## 状态：148 tests pass，服务器在 1966 端口运行中，daemon 已处理 3 个文件

## 待完成

### 1. 确认端到端写入
目标：验证知识是否真正写入 `viking://resources/...`

方法：
- 服务器日志当前输出到后台进程 stdout，无法查看。需要改为写文件：在 `ov.conf` 中加 `"log": {"level": "INFO", "output": "D:\\Develop\\ov-test\\server.log"}`
- 杀掉当前服务器，清 cursor DB，重启
- touch JSONL 触发处理，等 ETL 跑完
- 查看 `server.log` 中的写入日志（成功/失败）

预期日志链路：
```
[claude_code] Flushing N events
_enqueue_batch: received N events
ETL loop: dequeued N events
Processing batch with N events → After filtering: X → Reconstructed Y turns → Extracted Z items
Knowledge ingested: viking://resources/...   ← 关键确认点
```

### 2. 根据结果处理
- 写入成功 → 提交 commit 和 PR
- 写入失败 → 根据日志修 `storage_adapter.py`（可能是 `resource_service.add_resource()` 调用参数或 URI 格式问题）

### 3. 提交 PR
- 分支基于 `upstream/main`
- 只包含 daemon 相关文件变更
- PR 目标：`volcengine/OpenViking:main`
