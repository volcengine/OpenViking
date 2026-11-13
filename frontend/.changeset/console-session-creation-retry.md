---
'@openviking/react': patch
---

## en-US

Keep failed Console questions and client-only errors in the message list, clear accepted drafts immediately, and carry undelivered user inputs into the next session's single chat request without duplicating saved messages.

## zh-CN

Console 发送后立即清空输入；会话创建失败时保留提问并在消息列表显示客户端错误。下次发送补建会话，在单次聊天请求中携带未送达的用户输入，原始消息各保存一次，错误不进入存储和模型上下文。
