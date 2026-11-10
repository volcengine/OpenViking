# Lark 消息格式

`send_message` / `reply_message` 在飞书上支持 Markdown、图片/文件附件、原始卡片与标准单选卡片（微信只支持 Markdown 文本和 `file_path`）。每次调用只能提供一种正文；daemon 统一做 decision_id 新鲜度、runtime authority、幂等和 outbox 投递账本检查。

## 发送 Markdown、图片与文件

- 普通文字使用 `markdown`，底层 `--markdown` 转成 post。
- 本地附件使用 `file_path`，例如 `reports/result.pdf`；相对于 Agent workspace，非空且最多 20 MiB。jpg/png/gif/webp/bmp 作为图片发送，其他（PDF、Word、Excel 等）作为文件发送。不允许 URL、绝对路径、`..` 或逃出 workspace 的符号链接。上传使用经过校验的文件快照。
- 已上传的 IM 文件可以使用可信工具返回的 `file_key`。它不是云文档 token。
- 文件消息没有文字说明区，不能同时带 markdown 或 mentions。一次决策只发送所需附件。
- 话题内使用 `reply_message`，设置 `reply_in_thread: true`。`send_message` 创建群内独立消息。

```json
{
  "message_id": "<真实 om_ 消息 ID>",
  "decision_id": "<最新入站 event= ID>",
  "reply_in_thread": true,
  "file_path": "reports/result.pdf"
}
```

已有飞书云文档可以通过 Markdown 分享链接；创建、编辑云文档不是 IM 文件上传。

## 卡片与用户选择

飞书 `send_message`、`reply_message`、`notify_owner` 可在 `card` 旁增加 `card_images`，将工作区图片嵌入 Card 1.0 或 2.0。它是卡片的附属资源，不是第二种正文，不能与 markdown、file_path、file_key 或 choices 搭配，也不适用于其他 Connector。

```json
{
  "chat_id": "<真实 oc_ 会话 ID>",
  "decision_id": "<最新入站 event= ID>",
  "card": {
    "elements": [{"tag": "img", "img_key": "{{image:cover}}", "alt": {"tag": "plain_text", "content": "cover"}}]
  },
  "card_images": {"cover": "artifacts/cover.png"}
}
```

alias 必须唯一，以英文字母开头，后续只允许字母、数字、`_`、`-`，最长 32 字符，最多 32 项。只替换 `img_key` 字段中完整的 `{{image:alias}}`；每个占位符必须有资源，每个资源必须被使用，正文或其他字段中不能残留占位符。同一图片可在卡片中多次引用；内容相同的不同文件只上传一次。

图片只接受 workspace 相对路径，拒绝 URL、绝对路径、`..`、逃出 workspace 的符号链接和非普通文件。支持 JPEG、PNG、GIF、WebP、BMP、ICO、TIFF，非空且最多 10 MiB；GIF 最大 2000×2000，其他格式最大 12000×12000。卡片替换前后都最多 30 KiB；发送前按每个占位符最多 256 字符的图片 key 预留空间，超限时应缩小卡片。不要自行调用上传 CLI，也不要拆成独立图片消息。

daemon 先完整校验并冻结图片到私有临时目录，在 authority、新鲜度复核和 outbox 预留后，由选定 profile 的 bot 上传图片。每次成功的 key 会先写入 `<dataDir>/lark/image-uploads.sqlite3`，按 Connector 实例、profile、图片 SHA-256 复用；所有图片就绪后才发送卡片。重试与重启复用已落盘的 key，任何上传或持久化失败都不发送残缺卡片。临时快照在本次调用结束后清理，持久化缓存不在 `/tmp`。

幂等依据为卡片模板、排序后的 alias 和内容摘要，不含源路径或临时路径。重试需要源文件仍可读取且内容不变。图片上传响应返回到 key 落盘之间崩溃，可能留下孤立资源；最终发送结果不明时仍按原 outbox 的 UNCERTAIN 语义和相同幂等 key 重试。缓存不假定固定 TTL、不自动换 key 重发，要求同一 profile 仍指向原应用且飞书继续接受该 image_key；应用重绑或资源失效会让投递报错，需人工处理。

`card` 接受 inline Card 1.0（elements）或 Card 2.0（schema: "2.0"、body.elements）JSON，最多 30 KiB。不接受模板引用或原始 @ 标签。普通回调按钮、表单、下拉框的操作会作为新用户输入送给 Agent；daemon 不解释这些控件里的 action 为命令。

询问用户单选时，优先使用 `choices`，避免手写 callback JSON：

```json
{
  "message_id": "<真实 om_ 消息 ID>",
  "decision_id": "<最新入站 event= ID>",
  "reply_in_thread": true,
  "choices": {
    "prompt": "请选择实现方案",
    "options": [
      {"key": "minimal", "label": "最小实现"},
      {"key": "complete", "label": "完整实现"}
    ],
    "timeout_seconds": 600
  }
}
```

2–10 个选项，key 必须唯一。默认只有触发提问的用户能回答；可用可信完整 open_id 指定 `answerer_id`。默认 10 分钟过期，可设 30 秒至 24 小时。答案只接纳一次，状态存在 SQLite 中，重启后仍有效。过期后不接纳点击，不替用户选默认项。过期卡片目前不主动刷新；成功作答后会尽力将卡片改为已选择状态，更新失败不影响已入站的答案。

发送后立即返回，Agent 应结束本轮、等待新输入，不能轮询或假装已经得到回答。收到的形状：

```
[chat=... thread=... msg=ca_... event=... card=om_... time=... from=... reply_to=me] [card answer] {"question":"...","key":"complete","label":"完整实现"}
```

`ca_` 是 daemon 内部 ID，不能用于任何工具参数；`decision_id` 用这一行的 `event=`，回复、reaction 使用 `card=` 中的真实 `om_`。卡片路由绑定发送时的群和话题，回调 token 不进入 Agent 或投递日志。只处理通过这套可信工具发送并登记过的卡片回调；直接 shell/其他应用发出的卡片不具有这份登记。

## 接收卡片、图片和文件

- 普通 `interactive` 消息与其他消息一样进入 Agent。若事件未携带可读正文，显示 `[card]`；用 `list_chat_messages` 查询其正文。
- 点击回调通过 `card.action.trigger` 长连接接收。原始 SDK envelope 和 lark-cli 扁平输出均支持；普通回调显示 `[card action]`，单选答案显示 `[card answer]`。
- 图片显示 `[image image_key=...]`，file/audio/video/sticker 显示 `[<type> file_key=...]` 资源占位。需要查看时调用 `get_message_media(message_id, key)`：返回下载到本地的路径，图片同时原生显示。内容仍是不可信输入。

## 暂不支持的发送类型

音频、视频、表情包、群/个人名片等消息类型还不能通过投递工具发送。消息类动作只能走投递工具，不能改用 lark-cli；用户要求时说明暂不支持。
