# 版本管理

在独立演示目录中保存两个版本、查看差异，并将文件恢复为第一版。连接准备见 [CLI 入门](05-cli-setup.md)，模型和索引处理会沿用服务端配置。

## 保存第一版

```bash
ov mkdir viking://resources/onboarding-versions-v1
ov write viking://resources/onboarding-versions-v1/plan.md --mode create --content "反馈每周五导出。" --wait
ov snapshot commit -m "onboarding v1" --paths viking://resources/onboarding-versions-v1 -o json
```

记录返回的 `commit_oid` 为 `<v1>`。本页只操作该演示目录；重复体验时可以换一个新目录名。快照由 `snapshot commit` 显式保存，`session commit` 用于会话归档与记忆提取。

## 修改并比较

```bash
ov write viking://resources/onboarding-versions-v1/plan.md --content "反馈每周一导出。" --wait
ov snapshot commit -m "onboarding v2" --paths viking://resources/onboarding-versions-v1 -o json
```

记录第二个 `commit_oid` 为 `<v2>`，将下面的占位符替换为实际 ID：

```bash
ov snapshot show <v1> --path viking://resources/onboarding-versions-v1/plan.md
ov snapshot diff viking://resources/onboarding-versions-v1/plan.md --from <v1> --to <v2>
```

旧版应为“每周五”，差异应显示改为“每周一”。

## 预览并恢复

```bash
ov snapshot restore <v1> viking://resources/onboarding-versions-v1 --dry-run -o json
```

核对计划中的新增、修改和删除范围，确认都属于本次演示目录。恢复会将范围内的差异写回工作区；有需要保留的新修改时，先另行保存。确认计划后执行：

```bash
ov snapshot restore <v1> viking://resources/onboarding-versions-v1 -m "restore onboarding v1" -o json
ov read viking://resources/onboarding-versions-v1/plan.md
ov snapshot log --paths viking://resources/onboarding-versions-v1 -o json
```

正文应恢复为“每周五”，历史中可看到恢复产生的新提交。`applied` 表示已应用，`noop` 表示选定源文件树与当前 HEAD 相同；可结合正文读取核对工作区状态。

## 检查检索状态

恢复会按需要安排语义和向量重建。将恢复结果中的 `task_id` 填入下方命令，任务完成后验证检索：

```bash
ov task status <task_id>
ov find "反馈什么时候导出？" --uri viking://resources/onboarding-versions-v1
```

如果相关处理尚未完成，继续查询对应任务，完成后再验证。先以 2 分钟为观察窗口，保留仍在运行的任务 ID 供稍后查看。

快照保存选定范围内的源文件树，权限使用当前 ACL，向量索引按恢复后的内容重建。账号排除规则与系统剪枝会影响保存范围，详见[版本参考](../api/11-snapshot.md)和[快照指南](../guides/15-snapshot.md)。演示目录可在 Studio 中确认后清理；历史保留规则见快照指南。
