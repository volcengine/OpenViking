---
'@openviking/react': patch
---

## en-US

Keep failed Console questions and client-only errors in the message list, clear accepted drafts immediately, and carry undelivered user inputs into the next chat request until a stream event acknowledges delivery, without duplicating saved messages or replaying inputs after generation starts.

## zh-CN

Console 发送后立即清空输入；会话创建失败时保留提问并在消息列表显示客户端错误。下次发送补建会话，在单次聊天请求中携带未送达的用户输入，原始消息各保存一次，错误不进入存储和模型上下文。恢复队列保留到首个流事件确认送达，送达前失败不会丢失上下文，生成开始后不会重放旧输入。
