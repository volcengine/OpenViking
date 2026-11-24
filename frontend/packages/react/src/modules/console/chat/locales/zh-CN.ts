import type enUS from './en-US.js';

const messages: typeof enUS = {
  BOT_INVALID_EVENT: 'VikingBot 返回了无效的流事件',
  BOT_INPUT_REJECTED: 'VikingBot 未接受追加输入，可重试',
  BOT_STREAM_TRUNCATED: 'VikingBot 流连接在完成前断开',
  BOT_INVALID_ATTACHMENT:
    '请将图片上传为 HTTPS 或 data 地址后再发送给原生 VikingBot',
};
export default messages;
