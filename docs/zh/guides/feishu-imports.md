# 飞书云盘目录集与文件导入

OV 负责枚举云盘文件夹和下载普通文件；Understanding 负责单篇内容解析。Base Server 和 Preprocess 继续使用已有的单篇 Lark URL、Files 和 Responses 接口。

| 入口 | 导入行为 |
| --- | --- |
| `/drive/folder/{token}` | 递归列举目录集；文件夹本身没有正文，云文档和普通文件分别解析 |
| `/file/{token}` | 下载原始文件，根据扩展名选择解析器；不会把文件链接当作 Lark 云文档提交 |

本次变更不增加 Wiki 入口递归能力，已有 Wiki 单篇导入行为保持不变。

## 调用示例

向 `POST /api/v1/resources` 提交：

```json
{
  "path": "https://example.feishu.cn/drive/folder/folder_token",
  "to": "viking://resources/knowledge",
  "wait": false,
  "preserve_structure": true,
  "args": {
    "feishu_access_token": "u-..."
  }
}
```

独立文件使用 `/file/{token}` URL。来源枚举和下载支持 `args.feishu_access_token` 或服务器已有的飞书应用凭证。不传用户 token 时使用应用凭证。`args.lark_file` 仅用于已有的 Understanding 单篇云文档直连，不能作为文件夹枚举或普通文件下载的鉴权参数。

## 解析路由和目录结构

服务器开启 `parser_api.enable` 和 `parser_api.enable_feishu_url` 后，文件夹中的 docx、电子表格和多维表格按各自 URL 提交 Understanding，保留 URL 的 `table`、`view` 参数。云文档不会先转换成 Markdown，也不会生成占位 Markdown 文件。普通文件下载后按 `parser_api.extensions` 配置选择 Understanding 或原生解析器。

关闭飞书 URL 直连时，云文档沿用原生 Markdown 转换；旧版 doc 也使用原生转换。`args.parse_mode=no_split` 沿用原生解析，不支持原生解析的条目记录为失败。

`preserve_structure=true` 时保留来源文件夹层级，不增加额外的 `children/` 层级。Understanding 解析后的单篇文档仍按 OV 现有规则展开为文档目录及分块；最终扩展名和文件数量可能变化。非法路径字符被替换，同名或同 stem 内容按确定的顺序编号，防止解析产物覆盖。

只随导入内容创建其父目录，不单独导入来源空目录；筛选或解析失败后没有内容产物的来源子目录也不会被单独保留。空根目录仍遵循现有“没有可导入内容”的错误行为。

`include`、`exclude`、`ignore_dirs` 和 `.gitignore` 同时作用于本地文件及云文档条目。云文档按其逻辑 `.md` 名称筛选；这些筛选控制解析，不阻止来源阶段的列举和普通文件下载。

## 失败与任务恢复

- 默认保留成功项，枚举、下载和解析失败通过现有目录结果字段报告；根文件夹无法列举时直接报错。
- `strict=true` 时，条目失败或来源遍历触及限制使任务失败。
- 文件夹遍历可通过 `args.feishu_max_nodes`、`args.feishu_max_depth`、`args.feishu_max_download_bytes` 设置正整数上限，默认分别为 5000、20、1 GiB。下载大小在收到内容后检查，不能限制单次 HTTP 响应的峰值内存。下载预算耗尽后跳过后续云文档 URL。
- 目录文件数量仍受 `directory.max_files` 约束；启用 Understanding 目录路由时额外校验 `directory.max_depth`，包括仅包含云文档 URL 的目录。关闭 Understanding、显式选择原生后端或使用 `no_split` 时，深度沿用飞书来源层限制。
- 后台来源任务在获得每篇内容的 response_id 后，先写入现有任务存储再轮询。重新执行同一个来源任务时，相同条目复用已保存的 response_id；新建导入不会复用旧任务。
- 提交成功但 response_id 尚未持久化时发生中断，仍存在重复提交窗口。本改动不承诺严格的 exactly-once，也不新增自动重试整个任务的机制。

飞书凭证使用已有的任务鉴权通道；运行时目录清单和子项检查点不存放凭证。本次改动不新增 watch 的增量移动、删除同步协议。
