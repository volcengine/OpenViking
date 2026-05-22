# polish-article

调用 Gemini 润色文章。

## 脚本

```bash
python3 blog/scripts/polish-article/polish <file-path>
```

脚本将文件发给 Gemini 润色，结果写入同目录的 `<name>-polished.<ext>`，stdout 输出结果文件路径。

**注意**：Gemini API 调用可能耗时较长（30s–2min），脚本会以 streaming 方式输出进度。调用方 Agent 应使用后台执行或异步等待，不要阻塞。

## Agent 工作流

1. 运行脚本，获得润色结果文件路径
2. 读取润色结果，检查是否被 markdown code fence 包裹（如 ` ```jsx ... ``` `），如有则 strip
3. 检查内容完整性（非空、非报错）
4. 让用户 review 润色结果，确认是否覆盖原文件
5. 用户确认后覆盖原文件，删除润色临时文件
6. 如果项目有构建流程，运行构建验证语法正确性
7. 构建失败则分析并修复（常见：模型丢括号、改坏 JSX）

## 配置

- 凭证：`~/.article-polish.env`（`POLISH_API_URL`、`POLISH_API_KEY`、`POLISH_MODEL`）
- **绝不要读取或输出凭证文件内容**
- 可修改 `POLISH_MODEL` 切换模型，包括`gemini-pro-latest`、`gemini-flash-latest`、`gemini-flash-lite-latest`
