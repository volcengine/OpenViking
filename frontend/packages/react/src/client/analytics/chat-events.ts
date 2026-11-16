import { operation, completed, failed } from './fields.js';

export const chatEvents = {
  viking_bot_message_submit: {
    zh: '开始新回复请求，不包含输入内容；流中追加仅报告发送意图。',
    en: 'A new reply request starts; input is omitted. In-stream follow-ups report send intent only.',
    fields: operation,
  },
  viking_bot_reply_start: {
    zh: '收到首个可呈现的文本、推理或工具内容，不以连接建立计数。',
    en: 'The first renderable text, reasoning, or tool content arrives, not just a connection.',
    fields: completed,
  },
  viking_bot_reply_success: {
    zh: '收到回复的正常完成态。',
    en: 'A normal reply completion is received.',
    fields: completed,
  },
  viking_bot_reply_fail: {
    zh: '回复失败或未收到结束事件即断流。',
    en: 'A reply fails or its stream ends without a terminal event.',
    fields: failed,
  },
  viking_bot_reply_cancel: {
    zh: '用户停止回复或收到明确中断态。',
    en: 'The user stops the reply or an explicit interruption is received.',
    fields: completed,
  },
  viking_bot_session_create_click: {
    zh: '用户请求新建会话，不代表创建成功。',
    en: 'The user requests a new session, not its successful creation.',
    fields: {},
  },
  viking_bot_session_select: {
    zh: '用户选择会话。',
    en: 'The user selects a session.',
    fields: {},
  },
  viking_bot_interaction_submit: {
    zh: '用户提交交互卡片，不包含答案。',
    en: 'The user submits an interaction card; answers are omitted.',
    fields: {},
  },
  viking_bot_interaction_cancel: {
    zh: '用户取消交互卡片。',
    en: 'The user cancels an interaction card.',
    fields: {},
  },
  viking_bot_send_click: {
    zh: '用户发送对话输入，不采集输入内容。',
    en: 'The user sends conversation input; its content is omitted.',
    fields: {},
  },
} as const;
