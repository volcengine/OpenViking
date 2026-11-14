---
'@openviking/react': patch
---

## en-US

Allow a version note when saving an ordinary file. Wait for the file's background write task before creating the snapshot so it does not conflict with the write. If the file is saved but the snapshot is not created, show a warning. Wiki files keep their existing save flow.

## zh-CN

保存普通文件时可填写版本备注。等待该文件的后台写入任务结束后再创建快照，避免与写入冲突。如果正文已保存但未创建快照，则显示提示。Wiki 保持原有保存流程。
