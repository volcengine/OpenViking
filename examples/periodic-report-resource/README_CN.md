# 周期报告 Resource

这个示例展示应用如何使用公开的 `AsyncOpenViking` 文件系统 API，将一份已
生成的报告保存为两个项目 Resource：

- `report.md` 保存可阅读的报告正文；
- `manifest.json` 最后写入，并记录正文 URI 与摘要。

该写入顺序是应用侧约定，不是 OpenViking 的事务或 extension ABI。调度、
报告生成、稳定 ID、重试与冲突策略仍由应用负责；OpenViking 提供 Resource
写入和精确回读能力。

示例中的 manifest 字段只是应用元数据示意，并不是 OpenViking 注册的报告
schema。

```bash
python examples/periodic-report-resource/archive_report.py \
  --path ./data \
  --report-id 2026-W29
```

示例使用 `mode="create"`，因此重复使用已有报告 ID 会失败，不会覆盖历史。
生产环境的重试处理器可以读取已有 Resource、比较摘要，并且只接受完全一致
的结果。
